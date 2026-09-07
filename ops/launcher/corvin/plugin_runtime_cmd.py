"""`corvin plugin install|uninstall|list` — runtime plugin lifecycle.

These commands manage user-installed plugins in the current tenant.
Distinct from plugin_cmd.py (offline scaffolding per ADR-0244).

    corvin plugin install <ID|PATH>  install from marketplace ID or local directory
    corvin plugin uninstall <id>     remove an installed plugin
    corvin plugin list               list all installed plugins
    corvin plugin enable <id>        enable a plugin
    corvin plugin disable <id>       disable a plugin
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Imported lazily — only when a runtime command is actually invoked.


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


# ── corvin plugin install ────────────────────────────────────────────────────

def _is_plugin_id(s: str) -> bool:
    """Return True if s looks like a plugin ID, not a filesystem path."""
    # Plugin IDs: no slashes or backslashes (paths contain them)
    # Examples: "auth-saml-2.1", "slack-bridge", "my-plugin"
    return "/" not in s and "\\" not in s


def _marketplace_resolver():
    """The console's marketplace resolver — the ONE index-id → source-dir bridge.

    ``corvin_console.routes.marketplace_resolve`` lives in ``core/console``; the
    launcher runs from a source checkout or a wheel that vendors the console, so
    the package is tried first and the checkout path appended as a fallback
    (append, never insert(0) — the same rule routes/plugins.py follows).
    """
    try:
        from corvin_console.routes import marketplace_resolve  # type: ignore[import-not-found]
        return marketplace_resolve
    except ImportError:
        pass
    console = Path(__file__).resolve().parents[3] / "core" / "console"
    if (console / "corvin_console").is_dir() and str(console) not in sys.path:
        sys.path.append(str(console))
    from corvin_console.routes import marketplace_resolve  # type: ignore[import-not-found]
    return marketplace_resolve


def _cmd_install_marketplace(plugin_id: str, args: argparse.Namespace, tenant_id: str) -> int:
    """Install a plugin by marketplace id — resolved to LOCAL builtin source.

    Accepts the console index-id form (``plugin:buildin-<category>-<name>``) or
    a bare directory name (``semantic_context_retriever``), resolves it under
    the trusted roots (the Corvin-Marketplace checkout, then the in-wheel
    ``core/plugins/buildin``) via the same ``marketplace_resolve`` module the
    console install route uses, and then installs it exactly like a local path.

    Until 2026-09-07 this called ``PluginMarketplace.get_default()`` /
    ``.get_index()`` / ``.install_plugin()`` — none of which existed — and the
    resulting AttributeError was swallowed into ``exit 2: marketplace install
    failed``. ``corvin plugin install <id>`` never worked once.

    Out of scope (stated, not hidden): remote download of community plugins.
    There is no signed-artifact fetch path yet, so a non-builtin id is refused
    with a clear message instead of being pretended.
    """
    try:
        resolver = _marketplace_resolver()
    except ImportError as exc:
        _err(f"marketplace resolver not available: {exc}")
        return 2
    if not resolver.available():
        _err("plugin subsystem unavailable in this installation")
        return 2

    candidates = [plugin_id]
    if not plugin_id.startswith("plugin:"):
        # Bare name: try it under every category the marketplace ships.
        try:
            from corvin_plugins import bootstrap as _bootstrap
            roots = [_bootstrap._marketplace_root(), _bootstrap._BUILTIN_ROOT]
        except ImportError:
            roots = []
        for root in roots:
            for cat in sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
                candidates.append(f"plugin:buildin-{cat}-{plugin_id}")

    plugin_dir: Path | None = None
    last_error = ""
    for index_id in candidates:
        try:
            plugin_dir = resolver.resolve_builtin_dir(index_id)
            break
        except resolver.MarketplaceResolveError as exc:
            last_error = str(exc)
    if plugin_dir is None:
        _err(f"plugin not found in marketplace: {plugin_id} ({last_error})")
        return 1

    print(f"resolved {plugin_id} → {plugin_dir}")
    return _cmd_install_local(str(plugin_dir), args, tenant_id)


def _cmd_install_local(path_str: str, args: argparse.Namespace, tenant_id: str) -> int:
    """Install a plugin from a local directory."""
    try:
        from corvin_plugins.tenant_plugins import get_tenant_registry
        from corvin_plugins.validation import validate_manifest_file
    except ImportError as exc:
        _err(f"plugin system not available: {exc}")
        return 2

    plugin_dir = Path(path_str).expanduser().resolve()

    if not plugin_dir.is_dir():
        _err(f"plugin directory not found: {plugin_dir}")
        return 2

    # Validate manifest exists
    manifest_file = plugin_dir / "plugin.yaml"
    if not manifest_file.is_file():
        _err(f"no plugin.yaml found at {manifest_file}")
        return 2

    # Validate manifest
    report = validate_manifest_file(manifest_file)
    if not report.ok:
        _err("manifest validation failed:")
        for finding in report.findings:
            print(f"  {finding}", file=sys.stderr)
        return 1

    # Extract plugin metadata
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
    except Exception as exc:
        _err(f"failed to parse plugin.yaml: {exc}")
        return 2

    # Install
    try:
        registry = get_tenant_registry(tenant_id)
        registry.register_plugin(
            plugin_id,
            plugin_dir,
            {
                "version": version,
                "display_name": display_name,
                "boot_layer": manifest_data.get("boot_layer", "installed"),
            },
            installed_by="corvin-cli",
        )
        print(f"✓ Installed {plugin_id}@{version} to tenant {tenant_id}")
        return 0
    except ValueError as exc:
        _err(str(exc))
        return 1
    except Exception as exc:
        _err(f"installation failed: {exc}")
        return 2


def cmd_install(args: argparse.Namespace) -> int:
    """Install a plugin from marketplace (ID) or local directory (PATH)."""
    from corvinOS.shared.paths import _resolve_tenant_id

    tenant_id = _resolve_tenant_id(args.tenant)
    input_arg = args.plugin_id_or_path

    # Detect whether input is a plugin ID or a filesystem path
    if _is_plugin_id(input_arg):
        # Install from marketplace by ID
        return _cmd_install_marketplace(input_arg, args, tenant_id)
    else:
        # Install from local directory
        return _cmd_install_local(input_arg, args, tenant_id)


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
        help="Install a plugin from marketplace ID or local directory",
    )
    install_parser.add_argument(
        "plugin_id_or_path",
        metavar="ID_OR_PATH",
        help="Plugin ID (e.g., auth-saml-2.1) or local path (/path/to/plugin-dir)",
    )
    install_parser.add_argument(
        "--tenant",
        metavar="ID",
        default=None,
        help="Tenant ID (default: _default)",
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
