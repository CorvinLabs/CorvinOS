"""`corvin plugin install|uninstall|list` — runtime plugin lifecycle.

These commands manage user-installed plugins in the current tenant.
Distinct from plugin_cmd.py (offline scaffolding per ADR-0244).

    corvin plugin install <path>     install a plugin from a directory
    corvin plugin uninstall <id>     remove an installed plugin
    corvin plugin list               list all installed plugins
    corvin plugin enable <id>        enable a plugin
    corvin plugin disable <id>       disable a plugin
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Imported lazily — only when a runtime command is actually invoked.


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


# ── corvin plugin install ────────────────────────────────────────────────────

def cmd_install(args: argparse.Namespace) -> int:
    """Install a plugin from a local directory to the current tenant.

    Handles origin-based trust decisions: community plugins require explicit
    operator confirmation (ADR-0249), vetted plugins must verify against trust
    anchors (fail-closed if key missing). Local paths only; URLs are rejected.
    """
    from corvinOS.shared.paths import _resolve_tenant_id

    try:
        from corvin_plugins.tenant_plugins import get_tenant_registry
        from corvin_plugins.validation import validate_manifest_file
        from corvin_plugins.trust import evaluate, load_trust_anchors, grant_consent
    except ImportError as exc:
        _err(f"plugin system not available: {exc}")
        return 2

    # ── Input validation: reject URLs ───
    path_arg = args.path
    if path_arg.startswith(("http://", "https://", "ftp://", "file://")):
        _err(
            f"URL arguments are not supported: {path_arg}\n"
            f"Plugin install only accepts local directory paths.\n"
            f"Please download the plugin first and provide a local path."
        )
        return 1

    tenant_id = _resolve_tenant_id(args.tenant)
    plugin_dir = Path(path_arg).expanduser().resolve()

    if not plugin_dir.is_dir():
        _err(f"plugin directory not found: {plugin_dir}")
        return 2

    # ── Validate manifest exists ───
    manifest_file = plugin_dir / "plugin.yaml"
    if not manifest_file.is_file():
        _err(f"no plugin.yaml found at {manifest_file}")
        return 2

    # ── Extract plugin metadata (before validation) ───
    try:
        import yaml

        manifest_data = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
        if not isinstance(manifest_data, dict):
            _err("invalid plugin.yaml")
            return 2

        plugin_id = manifest_data.get("plugin_id")
        if not plugin_id:
            _err("plugin_id missing from plugin.yaml")
            return 2

        version = manifest_data.get("version", "0.1.0")
        display_name = manifest_data.get("display_name", plugin_id)
        origin = str(manifest_data.get("origin", "community")).lower()
    except Exception as exc:
        _err(f"failed to parse plugin.yaml: {exc}")
        return 2

    # ── Validate manifest (signature field is stripped for validation) ───
    # The signature is for trust verification, not part of the PluginRecord schema
    validation_data = {k: v for k, v in manifest_data.items() if k != "signature"}
    try:
        import yaml as yaml_module

        # Temporarily write validation data to a temp file for validation
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml_module.dump(validation_data, f)
            temp_manifest = Path(f.name)

        try:
            report = validate_manifest_file(temp_manifest)
        finally:
            temp_manifest.unlink(missing_ok=True)
    except Exception as exc:
        _err(f"failed to validate manifest: {exc}")
        return 2

    if not report.ok:
        _err("manifest validation failed:")
        for finding in report.findings:
            print(f"  {finding}", file=sys.stderr)
        return 1

    # ── Trust evaluation (ADR-0249) ───
    from pathlib import Path as PathlibPath

    try:
        from corvinOS.shared.paths import corvin_home

        ch = corvin_home()
    except ImportError:
        ch = PathlibPath.home() / ".corvin"

    trust_anchors = load_trust_anchors(ch)
    from corvin_plugins.trust import Verdict, enforcement_enabled

    # Evaluate with enforcement=True to get correct verdict
    decision = evaluate(
        manifest_data,
        corvin_home=ch,
        tenant_id=tenant_id,
        enforcement=True,  # Get correct verdict
        trust_anchors=trust_anchors,
    )

    # Check if enforcement is enabled globally
    enforce = enforcement_enabled(tenant_id)

    # ── Handle origin-specific logic ───
    if origin == "vetted" and decision.verdict == Verdict.FORGED:
        # Vetted plugin with invalid signature: always refuse
        _err(
            f"Plugin '{plugin_id}' claims origin=vetted but fails trust verification:\n"
            f"{decision.reason}\n"
            f"Installation refused."
        )
        return 1

    if origin == "vetted" and decision.verdict == Verdict.VETTED:
        print(f"✓ Trust verified: {decision.reason}")

    if origin == "community" and not decision.allowed:
        # Community plugin without consent
        if enforce:
            # Enforcement is ON: refuse without prompting
            _err(
                f"Plugin '{plugin_id}' is unreviewed third-party code.\n"
                f"Installation refused (enforcement enabled).\n"
                f"To approve, use: corvin plugin install --yes"
            )
            return 1

        # Enforcement is OFF: prompt for confirmation
        print(
            f"\nPlugin '{plugin_id}' is unreviewed third-party code.\n"
            f"Installing it will run untested code in this process.\n"
        )
        if not getattr(args, "yes", False):  # Allow --yes flag for automation
            try:
                response = input(f"Approve installation of {plugin_id}? [y/N] ")
                if response.lower() != "y":
                    print("Installation cancelled.")
                    return 1
            except EOFError:
                _err("no TTY available for confirmation (use --yes to skip)")
                return 1
        print(f"✓ Confirmed: installing {plugin_id}")

        # Grant consent in the system
        try:
            grant_consent(
                plugin_id,
                corvin_home=ch,
                tenant_id=tenant_id,
                operator="cli",
                audit_emit=None,  # TODO: wire in audit emitter
            )
        except Exception as exc:
            log.warning(f"could not record consent: {exc}")

    # ── Install ───
    try:
        registry = get_tenant_registry(tenant_id)
        registry.register_plugin(
            plugin_id,
            plugin_dir,
            {
                "version": version,
                "display_name": display_name,
                "boot_layer": manifest_data.get("boot_layer", "installed"),
                "origin": origin,  # Persist origin for audit trail
            },
            installed_by="corvin-cli",
        )
        print(f"✓ Installed {plugin_id}@{version} to tenant {tenant_id}")
        return 0
    except ValueError as exc:
        exc_msg = str(exc)
        # Make idempotent: if already installed, that's not an error
        if "is already installed" in exc_msg:
            print(f"Plugin {plugin_id!r} already installed, skipping.")
            return 0
        _err(exc_msg)
        return 1
    except Exception as exc:
        _err(f"installation failed: {exc}")
        return 2


# ── corvin plugin uninstall ──────────────────────────────────────────────────

def cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove a plugin from the current tenant."""
    from corvinOS.shared.paths import _resolve_tenant_id

    try:
        from corvin_plugins.tenant_plugins import get_tenant_registry
    except ImportError as exc:
        _err(f"plugin system not available: {exc}")
        return 2

    tenant_id = _resolve_tenant_id(args.tenant)

    try:
        registry = get_tenant_registry(tenant_id)
        registry.unregister_plugin(args.plugin_id)
        print(f"✓ Uninstalled {args.plugin_id} from tenant {tenant_id}")
        return 0
    except ValueError as exc:
        _err(str(exc))
        return 1
    except Exception as exc:
        _err(f"uninstall failed: {exc}")
        return 2


# ── corvin plugin list ───────────────────────────────────────────────────────

def cmd_list(args: argparse.Namespace) -> int:
    """List installed plugins in the current tenant."""
    from corvinOS.shared.paths import _resolve_tenant_id

    try:
        from corvin_plugins.tenant_plugins import get_tenant_registry
    except ImportError as exc:
        _err(f"plugin system not available: {exc}")
        return 2

    tenant_id = _resolve_tenant_id(args.tenant)

    try:
        registry = get_tenant_registry(tenant_id)
        plugins = registry.list_plugins()

        if not plugins:
            print("No plugins installed")
            return 0

        if getattr(args, "json", False):
            print(json.dumps([p.to_dict() for p in plugins], indent=2))
            return 0

        print(f"Installed plugins (tenant: {tenant_id}):\n")
        for p in plugins:
            status = "✓" if p.enabled else "✗"
            print(f"  [{status}] {p.plugin_id}@{p.version}")
            if p.display_name and p.display_name != p.plugin_id:
                print(f"      {p.display_name}")
            if p.installed_at:
                print(f"      installed: {p.installed_at}")
        print()
        return 0
    except Exception as exc:
        _err(f"listing failed: {exc}")
        return 2


# ── corvin plugin enable / disable ───────────────────────────────────────────

def cmd_enable(args: argparse.Namespace) -> int:
    """Enable a plugin."""
    from corvinOS.shared.paths import _resolve_tenant_id

    try:
        from corvin_plugins.tenant_plugins import get_tenant_registry
    except ImportError as exc:
        _err(f"plugin system not available: {exc}")
        return 2

    tenant_id = _resolve_tenant_id(args.tenant)

    try:
        registry = get_tenant_registry(tenant_id)
        registry.enable_plugin(args.plugin_id)
        print(f"✓ Enabled {args.plugin_id}")
        return 0
    except ValueError as exc:
        _err(str(exc))
        return 1
    except Exception as exc:
        _err(f"enable failed: {exc}")
        return 2


def cmd_disable(args: argparse.Namespace) -> int:
    """Disable a plugin."""
    from corvinOS.shared.paths import _resolve_tenant_id

    try:
        from corvin_plugins.tenant_plugins import get_tenant_registry
    except ImportError as exc:
        _err(f"plugin system not available: {exc}")
        return 2

    tenant_id = _resolve_tenant_id(args.tenant)

    try:
        registry = get_tenant_registry(tenant_id)
        registry.disable_plugin(args.plugin_id)
        print(f"✓ Disabled {args.plugin_id}")
        return 0
    except ValueError as exc:
        _err(str(exc))
        return 1
    except Exception as exc:
        _err(f"disable failed: {exc}")
        return 2


# ── Parser wiring ────────────────────────────────────────────────────────────

def add_runtime_parser(sub: Any) -> None:
    """Attach runtime plugin subcommands to the plugin subcommand group.

    Called from plugin_cmd.py's add_parser to blend offline and runtime commands.
    """
    # install subcommand
    install_parser = sub.add_parser(
        "install",
        help="Install a plugin from a directory to the current tenant",
    )
    install_parser.add_argument("path", metavar="PATH", help="Local plugin directory (URLs not supported)")
    install_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
    )
    install_parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt for community plugins (for automation)",
    )
    install_parser.set_defaults(plugin_cmd="install", func=cmd_install)

    # uninstall subcommand
    uninstall_parser = sub.add_parser(
        "uninstall",
        help="Uninstall a plugin from the current tenant",
    )
    uninstall_parser.add_argument("plugin_id", metavar="ID", help="Plugin ID")
    uninstall_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
    )
    uninstall_parser.set_defaults(plugin_cmd="uninstall", func=cmd_uninstall)

    # list subcommand
    list_parser = sub.add_parser(
        "list",
        help="List installed plugins in the current tenant",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable output",
    )
    list_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
    )
    list_parser.set_defaults(plugin_cmd="list", func=cmd_list)

    # enable subcommand
    enable_parser = sub.add_parser(
        "enable",
        help="Enable a plugin",
    )
    enable_parser.add_argument("plugin_id", metavar="ID", help="Plugin ID")
    enable_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
    )
    enable_parser.set_defaults(plugin_cmd="enable", func=cmd_enable)

    # disable subcommand
    disable_parser = sub.add_parser(
        "disable",
        help="Disable a plugin",
    )
    disable_parser.add_argument("plugin_id", metavar="ID", help="Plugin ID")
    disable_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
    )
    disable_parser.set_defaults(plugin_cmd="disable", func=cmd_disable)


def dispatch_runtime(args: argparse.Namespace) -> int:
    """Dispatch a runtime plugin command."""
    func = getattr(args, "func", None)
    if func is not None:
        return func(args)
    _err("usage: corvin plugin {install|uninstall|list|enable|disable}")
    return 2


__all__ = [
    "add_runtime_parser",
    "dispatch_runtime",
    "cmd_install",
    "cmd_uninstall",
    "cmd_list",
    "cmd_enable",
    "cmd_disable",
]
