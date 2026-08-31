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
    """Load tenant.corvin.yaml."""
    from . import tenant_config as tc_module

    config = tc_module.load(tenant_id)
    return config.model_dump(mode="python")


def save_tenant_config(tenant_id: str, config_dict: Dict[str, Any]) -> None:
    """Save tenant.corvin.yaml."""
    from . import tenant_config as tc_module
    from pathlib import Path
    import yaml

    corvin_home = Path.home() / ".corvin"
    config_path = corvin_home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config_path.write_text(
        yaml.safe_dump(config_dict, sort_keys=False, allow_unicode=True),
        encoding="utf-8"
    )


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

        corvin_home = Path.home() / ".corvin"
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

    # Step 9: Save tenant config
    try:
        save_tenant_config(tenant_id, config)
    except Exception as e:
        print(f"Error: Failed to save tenant config: {e}", file=sys.stderr)
        return 1

    # Step 9a: Update registry.yaml with plugin record (FIX for GitHub#XXXX)
    # Installation writes to tenant.corvin.yaml but ALSO must update registry.yaml
    # so that Console listing queries can find the installed plugin.
    try:
        from core.plugins.corvin_plugins.state import TenantRegistry
        from core.plugins.corvin_plugins.manifest import PluginRecord, PluginOrigin

        registry = TenantRegistry.load(tenant_id)

        # Convert metadata to PluginRecord and install via registry
        record = PluginRecord(
            plugin_id=metadata.plugin_id,
            name=metadata.name,
            version=metadata.version,
            description=metadata.description or "",
            origin=PluginOrigin(metadata.origin) if isinstance(metadata.origin, str) else metadata.origin,
            boot_layer=metadata.boot_layer,
            plugin_type=metadata.plugin_type,
            class_path=metadata.class_path,
            config=metadata.config,
            pii_risk=metadata.pii_risk,
            settings_schema=metadata.settings_schema,
            settings=metadata.config,
        )

        registry.install(record, installed_by="cli")
    except Exception as e:
        print(f"Warning: Failed to update registry.yaml: {e}", file=sys.stderr)
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
