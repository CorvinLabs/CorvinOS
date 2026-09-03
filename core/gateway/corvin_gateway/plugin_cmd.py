"""Plugin installation CLI (ADR-0249 Stage 6).

Provides `corvin plugin install <path>` with trust-anchor verification, operator
consent for community plugins, and idempotent writes to tenant.corvin.yaml.

Feature flag: plugin_trust_enforcement (ships dark, defaults to false).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

class _TenantsModule:
    """Lazy façade over ``forge.tenants`` (ADR-0007 resolver).

    Module-level so tests can patch ``plugin_cmd._tenants_module`` and so the
    tenant directory is derived by the canonical ``validate_tenant_id()`` →
    ``tenant_home()`` chain instead of string-joining under ``Path.home()``.
    """

    def _mod(self):
        from forge import tenants  # type: ignore[import-not-found]

        return tenants

    def validate_tenant_id(self, tenant_id: str) -> str:
        return self._mod().validate_tenant_id(tenant_id)

    def tenant_home(self, tenant_id: str) -> Path:
        return Path(self._mod().tenant_home(tenant_id))


_tenants_module = _TenantsModule()


def _tenant_config_path(tenant_id: str) -> Path:
    tid = _tenants_module.validate_tenant_id(tenant_id)
    return _tenants_module.tenant_home(tid) / "global" / "tenant.corvin.yaml"


class _SkipRegistry(Exception):
    """Control flow: runtime registry path disabled by flag (declared path only)."""


def _validate_axes(metadata: "PluginMetadata") -> Optional[str]:
    """Reject manifests that conflate the three plugin axes (CLAUDE.md § plugin registry).

    ``origin`` (builtin|vetted|community) and ``boot_layer`` (bundled|installed)
    are orthogonal; an ``origin: installed`` manifest used to slip past the
    tenant-config write and only blew up (as a swallowed warning) at the
    registry write — leaving the two sources of truth diverged.
    """
    from corvin_plugins.manifest import BootLayer, PluginOrigin

    try:
        PluginOrigin(metadata.origin)
    except ValueError:
        return (
            f"plugin.yaml origin {metadata.origin!r} is not one of "
            f"{[o.value for o in PluginOrigin]} (origin is provenance, not boot layer)"
        )
    try:
        BootLayer(metadata.boot_layer)
    except ValueError:
        return (
            f"plugin.yaml boot_layer {metadata.boot_layer!r} is not one of "
            f"{[b.value for b in BootLayer]}"
        )
    from corvin_plugins.manifest import KNOWN_PLUGIN_TYPES

    if metadata.plugin_type not in KNOWN_PLUGIN_TYPES:
        return (
            f"plugin.yaml plugin_type {metadata.plugin_type!r} is not a known extension point; "
            f"expected one of {sorted(KNOWN_PLUGIN_TYPES)}"
        )
    return None


def _get_corvin_home() -> Path:
    """Canonical runtime root (honours CORVIN_HOME and the repo-local .corvin).

    The two call sites below hardcoded ``Path.home()/.corvin`` and therefore
    wrote tenant config / read trust anchors from the wrong root whenever the
    resolver pointed elsewhere (tests patch this name).
    """
    try:
        from forge.paths import corvin_home  # type: ignore[import-not-found]

        return Path(corvin_home())
    except Exception:  # noqa: BLE001 — forge absent (stripped layout)
        import os

        env = os.environ.get("CORVIN_HOME")
        return Path(os.path.expanduser(env)) if env else Path.home() / ".corvin"



@dataclass
class PluginMetadata:
    """Extracted plugin metadata."""
    plugin_id: str
    name: str
    version: str
    origin: str  # "builtin" | "vetted" | "community"
    boot_layer: str  # "bundled" | "installed"
    class_path: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    plugin_type: str = "generic"


def _load_plugin_yaml(path: Path) -> Optional[Dict[str, Any]]:
    """Load plugin.yaml if it exists."""
    plugin_yaml = path / "plugin.yaml"
    if not plugin_yaml.exists():
        return None

    try:
        import yaml
        return yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"Failed to load plugin.yaml: {e}")
        return None


def _load_setup_py(path: Path) -> Optional[Dict[str, Any]]:
    """Extract metadata from setup.py (fallback)."""
    # Simplified: just look for name and version in setup.py
    setup_py = path / "setup.py"
    if not setup_py.exists():
        return None

    try:
        import re
        content = setup_py.read_text(encoding="utf-8")
        name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
        version_match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)

        if name_match and version_match:
            return {
                "name": name_match.group(1),
                "version": version_match.group(1),
            }
    except Exception as e:
        logger.debug(f"Failed to parse setup.py: {e}")

    return None


def _load_pyproject_toml(path: Path) -> Optional[Dict[str, Any]]:
    """Extract metadata from pyproject.toml (fallback)."""
    pyproject = path / "pyproject.toml"
    if not pyproject.exists():
        return None

    try:
        if sys.version_info >= (3, 11):
            import tomllib
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        else:
            try:
                import toml
                data = toml.loads(pyproject.read_text(encoding="utf-8"))
            except ImportError:
                return None

        project = data.get("project", {})
        if project:
            return {
                "name": project.get("name"),
                "version": project.get("version"),
            }
    except Exception as e:
        logger.debug(f"Failed to parse pyproject.toml: {e}")

    return None


def extract_plugin_metadata(path: str) -> PluginMetadata:
    """Extract plugin metadata from path.

    Tries in order:
    1. plugin.yaml (new ADR-0249 format)
    2. setup.py (legacy)
    3. pyproject.toml (legacy)

    Raises ValueError if no metadata found or validation fails.
    """
    plugin_path = Path(path).resolve()

    if not plugin_path.is_dir():
        raise ValueError(f"Plugin path must be a directory: {path}")

    # Try plugin.yaml first
    plugin_yaml = _load_plugin_yaml(plugin_path)
    if plugin_yaml:
        plugin_id = plugin_yaml.get("id")
        if not plugin_id:
            raise ValueError("plugin.yaml missing required field: id")

        return PluginMetadata(
            plugin_id=plugin_id,
            name=plugin_yaml.get("name", plugin_id),
            version=plugin_yaml.get("version", "0.0.0"),
            origin=plugin_yaml.get("origin", "community"),
            boot_layer=plugin_yaml.get("boot_layer", "installed"),
            class_path=plugin_yaml.get("class_path"),
            plugin_type=str(plugin_yaml.get("plugin_type") or "generic"),
            config=plugin_yaml.get("config"),
        )

    # Fallback to setup.py
    setup_meta = _load_setup_py(plugin_path)
    if setup_meta:
        plugin_id = setup_meta.get("name", "").replace("-", "_").lower()
        if not plugin_id:
            raise ValueError("setup.py: could not extract name")

        return PluginMetadata(
            plugin_id=plugin_id,
            name=setup_meta.get("name", plugin_id),
            version=setup_meta.get("version", "0.0.0"),
            origin="community",  # Legacy plugins are community
            boot_layer="installed",
            class_path=None,
        )

    # Fallback to pyproject.toml
    pyproject_meta = _load_pyproject_toml(plugin_path)
    if pyproject_meta:
        plugin_id = pyproject_meta.get("name", "").replace("-", "_").lower()
        if not plugin_id:
            raise ValueError("pyproject.toml: could not extract name")

        return PluginMetadata(
            plugin_id=plugin_id,
            name=pyproject_meta.get("name", plugin_id),
            version=pyproject_meta.get("version", "0.0.0"),
            origin="community",
            boot_layer="installed",
            class_path=None,
        )

    raise ValueError(
        f"Could not extract plugin metadata from {path}. "
        "Tried: plugin.yaml, setup.py, pyproject.toml"
    )


def verify_plugin_signature(
    plugin_path: Path,
    manifest: Dict[str, Any],
    *,
    trust_anchors: tuple[str, ...] = (),
    require_signature: bool = False,
) -> tuple[bool, str]:
    """Verify Ed25519 signature if present.

    Args:
        plugin_path: Path to plugin directory
        manifest: Plugin manifest dict
        trust_anchors: Trust anchor public keys
        require_signature: If True, fail if no signature present (for vetted plugins)

    Returns (verified, reason_if_false).
    """
    from corvin_plugins.trust import verify_signature

    if "signature" not in manifest:
        if require_signature:
            # Vetted plugin must have a signature
            return False, "vetted plugin missing required signature"
        # Unsigned: allowed for community plugins
        return True, "unsigned"

    if not trust_anchors:
        # No trust anchors configured: cannot verify vetted plugins
        return False, "no trust anchors configured"

    if verify_signature(manifest, trust_anchors=trust_anchors):
        return True, "signature verified"

    return False, "signature verification failed"


def load_tenant_config(tenant_id: str) -> Dict[str, Any]:
    """Load ``tenant.corvin.yaml`` as the raw mapping it is on disk.

    Adversarial review 2026-09-03: this went through ``tenant_config.load()``
    — the gateway's strict pydantic ``TenantConfig`` (``extra="forbid"``, no
    ``spec.plugins`` field, mandatory ``apiVersion``/``kind``/``metadata``).
    Every real tenant file (free-form ``spec`` sections written by the console)
    failed validation, so ``corvinos plugin install`` never worked on a live
    install; and had it loaded, ``model_dump()`` + ``save_tenant_config`` would
    have rewritten the file WITHOUT every section the schema does not know.
    The plugin command only owns ``spec.plugins.installed``; everything else
    must round-trip untouched.
    """
    import yaml

    config_path = _tenant_config_path(tenant_id)
    if not config_path.exists():
        raise FileNotFoundError(f"no config for tenant {tenant_id!r}: {config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_path}: top-level must be a mapping")
    return data


def save_tenant_config(tenant_id: str, config_dict: Dict[str, Any]) -> None:
    """Write ``tenant.corvin.yaml`` atomically, preserving every foreign section.

    Mode: keep the existing file's mode (the console writes 0o644-ish files
    and reads them regardless); a brand-new file is created 0o600.
    """
    import os
    import tempfile
    import yaml

    config_path = _tenant_config_path(tenant_id)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    mode = (config_path.stat().st_mode & 0o777) if config_path.exists() else 0o600

    fd, tmp = tempfile.mkstemp(prefix=".tenant.corvin.", suffix=".yaml", dir=str(config_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(yaml.safe_dump(config_dict, sort_keys=False, allow_unicode=True))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, config_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def plugin_already_installed(
    config: Dict[str, Any],
    plugin_id: str,
) -> bool:
    """Check if plugin is already in installed list."""
    installed = config.get("spec", {}).get("plugins", {}).get("installed", [])
    return any(p.get("id") == plugin_id for p in installed)


def add_plugin_to_config(
    config: Dict[str, Any],
    metadata: PluginMetadata,
) -> None:
    """Add plugin to the tenant config."""
    if "spec" not in config:
        config["spec"] = {}
    if "plugins" not in config["spec"]:
        config["spec"]["plugins"] = {}
    if "installed" not in config["spec"]["plugins"]:
        config["spec"]["plugins"]["installed"] = []

    entry = {
        "id": metadata.plugin_id,
        "name": metadata.name,
        "version": metadata.version,
        "origin": metadata.origin,
        "boot_layer": metadata.boot_layer,
    }

    if metadata.class_path:
        entry["class_path"] = metadata.class_path
    if metadata.config:
        entry["config"] = metadata.config

    config["spec"]["plugins"]["installed"].append(entry)


def prompt_community_confirmation(metadata: PluginMetadata) -> bool:
    """Prompt operator for community plugin confirmation."""
    print(f"\nPlugin requires operator confirmation:")
    print(f"  ID:       {metadata.plugin_id}")
    print(f"  Name:     {metadata.name}")
    print(f"  Version:  {metadata.version}")
    print(f"  Origin:   {metadata.origin}")
    print(f"\nThis plugin is UNREVIEWED. Load it?")

    while True:
        resp = input("(yes/no): ").strip().lower()
        if resp == "yes":
            return True
        elif resp == "no":
            return False
        print("Please enter 'yes' or 'no'")


def emit_audit_event(
    event_type: str,
    plugin_id: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit audit event for plugin installation.

    TODO: Integrate with hash-chained audit trail (ADR-0232).
    For now, log locally.
    """
    event_data = {
        "event_type": event_type,
        "plugin_id": plugin_id,
    }
    if details:
        event_data.update(details)

    logger.info(f"Plugin audit event: {json.dumps(event_data)}")


def install_plugin(
    path: str,
    *,
    tenant_id: str = "_default",
    force: bool = False,
    no_prompt: bool = False,
) -> int:
    """Install a plugin.

    Returns exit code (0 = success, 1 = failure).
    """
    # Step 1: Validate path (fail-closed)
    if path.startswith("http://") or path.startswith("https://"):
        print("Error: URL installation not supported. Provide a local directory path.", file=sys.stderr)
        return 1

    plugin_path = Path(path).resolve()
    if not plugin_path.exists():
        print(f"Error: Plugin path not found: {path}", file=sys.stderr)
        return 1

    if not plugin_path.is_dir():
        print(f"Error: Plugin path must be a directory: {path}", file=sys.stderr)
        return 1

    # Step 2: Extract metadata
    try:
        metadata = extract_plugin_metadata(str(plugin_path))
        axis_error = _validate_axes(metadata) if metadata else None
        if axis_error:
            print(f"Error: {axis_error}", file=sys.stderr)
            return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    logger.info(f"Extracted metadata: {metadata.plugin_id} v{metadata.version}")

    # Step 3: Load tenant config
    try:
        config = load_tenant_config(tenant_id)
    except Exception as e:
        print(f"Error: Failed to load tenant config: {e}", file=sys.stderr)
        return 1

    # Step 4: Check idempotency
    if plugin_already_installed(config, metadata.plugin_id) and not force:
        print(
            f"Plugin {metadata.plugin_id!r} already installed. "
            "Use --force to reinstall.",
            file=sys.stderr,
        )
        emit_audit_event("plugin.already_present", metadata.plugin_id)
        return 0  # Not an error — already done

    # Step 5: Community plugin confirmation (fail-closed)
    if metadata.origin == "community" and not no_prompt and not force:
        if not prompt_community_confirmation(metadata):
            print("Installation cancelled.", file=sys.stderr)
            emit_audit_event("plugin.install_cancelled", metadata.plugin_id)
            return 1

    # Step 6: Signature verification for vetted plugins (fail-closed)
    if metadata.origin == "vetted":
        from corvin_plugins.trust import load_trust_anchors

        corvin_home = _get_corvin_home()
        trust_anchors = load_trust_anchors(corvin_home)

        # Try to load plugin manifest for signature check
        plugin_manifest_path = plugin_path / "plugin.yaml"
        if plugin_manifest_path.exists():
            try:
                import yaml
                manifest = yaml.safe_load(plugin_manifest_path.read_text(encoding="utf-8"))
                verified, reason = verify_plugin_signature(
                    plugin_path,
                    manifest,
                    trust_anchors=trust_anchors,
                    require_signature=True,  # Vetted plugins MUST be signed
                )
                if not verified:
                    print(
                        f"Error: Plugin signature verification failed: {reason}",
                        file=sys.stderr,
                    )
                    emit_audit_event(
                        "plugin.signature_verification_failed",
                        metadata.plugin_id,
                        {"reason": reason},
                    )
                    return 1
            except Exception as e:
                print(f"Error: Could not verify signature: {e}", file=sys.stderr)
                return 1

    # Step 7: Remove old entry if reinstalling
    if force and plugin_already_installed(config, metadata.plugin_id):
        installed = config.get("spec", {}).get("plugins", {}).get("installed", [])
        config["spec"]["plugins"]["installed"] = [
            p for p in installed if p.get("id") != metadata.plugin_id
        ]

    # Step 8: Add plugin to config
    add_plugin_to_config(config, metadata)

    # Step 9: Update registry.yaml FIRST (the runtime source of truth the Console
    # lists from), then the declarative tenant config. Registry failure is an
    # install failure — a warning here left the two sources of truth diverged
    # ("installed" in tenant.corvin.yaml, absent from registry.yaml).
    try:
        from corvin_core.feature_flags import is_enabled as _flag_enabled  # type: ignore[import-not-found]

        runtime_registry_on = bool(_flag_enabled("plugin_runtime_lifecycle", tenant_id))
    except Exception:  # noqa: BLE001 — flags unreadable → registry path off (declared path still honoured)
        runtime_registry_on = False

    try:
        from corvin_plugins.manifest import BootLayer, PluginOrigin, PluginRecord
        from corvin_plugins.state import PluginLifecycle

        if not runtime_registry_on:
            # ADR-0030: the declarative spec.plugins.installed entry is always
            # honoured at boot; registry.yaml (runtime lifecycle) is flag-gated.
            print(
                "Note: plugin_runtime_lifecycle is off — recorded in tenant.corvin.yaml only "
                "(enable the flag for registry.yaml + Console lifecycle control).",
                file=sys.stderr,
            )
            raise _SkipRegistry()

        record = PluginRecord(
            plugin_id=metadata.plugin_id,
            version=metadata.version,
            display_name=metadata.name,
            plugin_type=metadata.plugin_type,
            origin=PluginOrigin(metadata.origin),
            boot_layer=BootLayer(metadata.boot_layer),
        )
        lifecycle = PluginLifecycle(
            tenant_id=tenant_id,
            corvin_home_path=_get_corvin_home(),
            lifecycle_enabled=runtime_registry_on,  # already checked above; the gate is per instance
        )
        if force:
            try:
                lifecycle.uninstall(metadata.plugin_id)
            except Exception:  # noqa: BLE001 — not present in the registry yet
                pass
        lifecycle.install(record, installed_by="cli")
    except _SkipRegistry:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"Error: Failed to update registry.yaml: {e}", file=sys.stderr)
        emit_audit_event("plugin.install_failed", metadata.plugin_id, {"stage": "registry"})
        return 1

    # Step 10: Save tenant config (declarative spec.plugins.installed)
    try:
        save_tenant_config(tenant_id, config)
    except Exception as e:
        print(f"Error: Failed to save tenant config: {e}", file=sys.stderr)
        return 1
        # Don't fail the installation if registry update fails—plugin is still installed
        # in tenant.corvin.yaml, just not visible in Console listing until restart

    # Step 10: Emit audit event
    emit_audit_event(
        "plugin.installed",
        metadata.plugin_id,
        {
            "version": metadata.version,
            "origin": metadata.origin,
            "boot_layer": metadata.boot_layer,
        },
    )

    print(f"✅ Plugin installed: {metadata.plugin_id} v{metadata.version}")
    return 0


# ── CLI integration ──────────────────────────────────────────────────────


def _cmd_plugin_install(args: argparse.Namespace) -> int:
    """Handler for `corvin plugin install`."""
    return install_plugin(
        args.path,
        tenant_id=args.tenant,
        force=args.force,
        no_prompt=args.no_prompt,
    )


def add_plugin_subcommand(sub: argparse._SubParsersAction) -> None:
    """Register plugin subcommand to main parser."""
    plugin_p = sub.add_parser(
        "plugin",
        help="Manage plugins (install, list, enable/disable).",
    )
    plugin_sub = plugin_p.add_subparsers(dest="action", required=True)

    install_p = plugin_sub.add_parser(
        "install",
        help="Install a plugin from a local directory.",
    )
    install_p.add_argument(
        "path",
        help="Local directory path containing plugin.yaml (or setup.py/pyproject.toml)",
    )
    install_p.add_argument(
        "--tenant",
        default="_default",
        help="Tenant ID (default: _default)",
    )
    install_p.add_argument(
        "--force",
        action="store_true",
        help="Reinstall if already present",
    )
    install_p.add_argument(
        "--no-prompt",
        action="store_true",
        help="Skip confirmation prompts (for CI/automation)",
    )
    install_p.set_defaults(func=_cmd_plugin_install)
