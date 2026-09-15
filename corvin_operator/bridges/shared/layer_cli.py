"""ADR-0142 M4 — ``corvin-layer`` CLI.

Subcommands:
    add <path>        Install an extension from a directory (disabled by default)
    remove <name>     Remove a non-core extension
    enable <name>     Enable a disabled extension
    disable <name>    Disable without removing
    list              List CORE (immutable) + EXTENSIONS sections
    info <name>       Show manifest, hooks, capabilities, scope
    validate <path>   Lint a layer.yaml before installing
    upgrade <name>    Upgrade to latest version (re-runs add from source)
    export <name>     Export installed extension as a tarball

    custom install <path>    Install a custom layer (ADR-0156)
    custom list              List installed custom layers
    custom enable <name>     Enable a custom layer
    custom disable <name>    Disable a custom layer
    custom remove <name>     Remove a custom layer
    custom export <name>     Export a custom layer as .tar.gz

Core layers (``corvin.*``) cannot be removed or disabled — that errors with the
ADR-0142 message. Every lifecycle action emits an ext.* audit event.

NOTE on ``add`` source: M4 supports a local directory (the most common case +
the CI-testable one). Tarball/GitHub-URL ingestion is noted in the ADR but is a
download-mechanism detail layered on top of the same install path; this CLI
installs from an unpacked directory.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
from pathlib import Path

try:
    from . import extension_registry as _reg  # type: ignore
    from . import extension_api as _ext_api  # type: ignore
    from . import custom_layer_registry as _clr  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import extension_registry as _reg  # type: ignore
    import extension_api as _ext_api  # type: ignore
    import custom_layer_registry as _clr  # type: ignore

ExtensionRegistry = _reg.ExtensionRegistry
ExtensionError = _reg.ExtensionError
ExtensionNamespaceError = _reg.ExtensionNamespaceError

_CORE_REMOVE_MSG = (
    "Error: '{name}' is a core layer protected by ADR-0141.\n"
    "Core layers cannot be removed. If you believe this is an error, see:\n"
    "  docs/claude-ref/layer-integrity-protocol.md"
)


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _is_core_name(name: str) -> bool:
    return name.startswith(_reg._CORE_PREFIX)


def _make_registry(args: argparse.Namespace) -> ExtensionRegistry:
    return ExtensionRegistry(
        tenant_id=getattr(args, "tenant", None),
        session_id=getattr(args, "session", "") or "",
        project_root=Path(getattr(args, "project_root", None)) if getattr(args, "project_root", None) else None,
    )


# ── validate ─────────────────────────────────────────────────────────────────
def cmd_validate(args: argparse.Namespace) -> int:
    src = Path(args.path)
    manifest_path = src / "layer.yaml" if src.is_dir() else src
    if not manifest_path.is_file():
        _eprint(f"Error: no layer.yaml found at {manifest_path}")
        return 1
    import yaml
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest = _reg.parse_manifest(raw, root=manifest_path.parent)
        _reg.validate_name(manifest.name)
        reg = _make_registry(args)
        _reg.check_requires(manifest, reg.core_capabilities)
    except ExtensionNamespaceError as exc:
        _eprint(f"INVALID (namespace): {exc}")
        return 1
    except ExtensionError as exc:
        _eprint(f"INVALID: {exc}")
        return 1
    except Exception as exc:
        _eprint(f"INVALID: {exc}")
        return 1
    print(f"OK: {manifest.name} v{manifest.version} (scope={manifest.scope}, "
          f"hooks={len(manifest.hooks)}, requires={len(manifest.requires)})")
    return 0


# ── add / upgrade ────────────────────────────────────────────────────────────
def _install_from_dir(args: argparse.Namespace, *, upgrade: bool) -> int:
    src = Path(args.path)
    if not src.is_dir():
        _eprint(f"Error: '{src}' is not a directory")
        return 1
    manifest_path = src / "layer.yaml"
    if not manifest_path.is_file():
        _eprint(f"Error: no layer.yaml in {src}")
        return 1
    reg = _make_registry(args)
    # Validate + namespace-gate (also emits ext.core_namespace_rejected on a
    # corvin.* name attempt).
    try:
        manifest = reg.load_manifest_file(manifest_path)
    except ExtensionNamespaceError as exc:
        _eprint(f"Error: {exc}")
        return 1
    except ExtensionError as exc:
        _eprint(f"Error: failed to load extension: {exc}")
        return 1

    scope_dir = reg.scope_dir(manifest.scope)
    if scope_dir is None:
        _eprint(f"Error: scope '{manifest.scope}' is not persistable via the CLI")
        return 1
    dest = scope_dir / manifest.name
    if dest.exists():
        if not upgrade:
            _eprint(f"Error: '{manifest.name}' already installed at {dest} (use 'upgrade')")
            return 1
        # Preserve the enabled marker across an upgrade.
        was_enabled = (dest / ".enabled").exists()
        shutil.rmtree(dest)
    else:
        was_enabled = False

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    # Default-off after add; upgrade preserves prior state.
    enabled_marker = dest / ".enabled"
    if upgrade and was_enabled:
        enabled_marker.touch()
    else:
        if enabled_marker.exists():
            enabled_marker.unlink()

    reg._audit("ext.installed", name=manifest.name, version=manifest.version,
               scope=manifest.scope, reason="upgrade" if upgrade else "add")
    state = "enabled" if (enabled_marker.exists()) else "disabled"
    print(f"Installed {manifest.name} v{manifest.version} (scope={manifest.scope}) — {state}.")
    if state == "disabled":
        print(f"Run 'corvin-layer enable {manifest.name}' to activate it.")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    return _install_from_dir(args, upgrade=False)


def cmd_upgrade(args: argparse.Namespace) -> int:
    return _install_from_dir(args, upgrade=True)


# ── remove ───────────────────────────────────────────────────────────────────
def cmd_remove(args: argparse.Namespace) -> int:
    name = args.name
    if _is_core_name(name):
        _eprint(_CORE_REMOVE_MSG.format(name=name))
        return 1
    reg = _make_registry(args)
    reg.discover()
    manifest = reg.get(name)
    if manifest is None or manifest.root is None:
        _eprint(f"Error: extension '{name}' is not installed")
        return 1
    shutil.rmtree(manifest.root)
    reg._audit("ext.removed", name=name, version=manifest.version,
               scope=manifest.scope, reason="cli")
    print(f"Removed {name}.")
    return 0


# ── enable / disable ─────────────────────────────────────────────────────────
def _set_enabled(args: argparse.Namespace, *, enable: bool) -> int:
    name = args.name
    if _is_core_name(name):
        if enable:
            _eprint(f"Error: '{name}' is a core layer and is always active.")
        else:
            _eprint(_CORE_REMOVE_MSG.format(name=name))
        return 1
    reg = _make_registry(args)
    reg.discover()
    manifest = reg.get(name)
    if manifest is None or manifest.root is None:
        _eprint(f"Error: extension '{name}' is not installed")
        return 1
    marker = manifest.root / ".enabled"
    if enable:
        marker.touch()
        reg._audit("ext.enabled", name=name, version=manifest.version,
                   scope=manifest.scope, reason="cli")
        print(f"Enabled {name}.")
    else:
        if marker.exists():
            marker.unlink()
        reg._audit("ext.disabled", name=name, version=manifest.version,
                   scope=manifest.scope, reason="cli")
        print(f"Disabled {name}.")
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    return _set_enabled(args, enable=True)


def cmd_disable(args: argparse.Namespace) -> int:
    return _set_enabled(args, enable=False)


# ── REST-friendly wrappers (ADR-0142 M5 — console route reuse) ──────────────
# These mirror the CLI command bodies but return structured dicts and raise
# typed exceptions instead of printing + returning an exit code, so the console
# route can map them to HTTP status codes. The deny-of-core rule is enforced
# identically (a name starting with ``corvin.`` errors).


def install_dir(
    path: str | Path,
    *,
    scope_override: str | None = None,
    tenant_id: str | None = None,
    session_id: str = "",
    project_root: str | Path | None = None,
    enable: bool = False,
    upgrade: bool = False,
) -> dict:
    """Install an extension from an unpacked directory and return a summary dict.

    The manifest's declared ``scope`` decides the storage location (the same as
    the CLI). ``scope_override`` is accepted for API symmetry but is currently
    advisory only — the manifest scope is authoritative, matching ADR-0142's
    storage table. Raises ExtensionError / ExtensionNamespaceError on failure
    and ValueError for an already-installed name (without ``upgrade``).
    """
    src = Path(path)
    if not src.is_dir():
        raise ValueError(f"'{src}' is not a directory")
    manifest_path = src / "layer.yaml"
    if not manifest_path.is_file():
        raise ValueError(f"no layer.yaml in {src}")

    reg = ExtensionRegistry(
        tenant_id=tenant_id,
        session_id=session_id or "",
        project_root=Path(project_root) if project_root else None,
    )
    # load_manifest_file runs the namespace gate (emits
    # ext.core_namespace_rejected) and the requires check.
    manifest = reg.load_manifest_file(manifest_path)

    scope_dir = reg.scope_dir(manifest.scope)
    if scope_dir is None:
        raise ValueError(f"scope '{manifest.scope}' is not persistable")
    dest = scope_dir / manifest.name
    if dest.exists():
        if not upgrade:
            raise ValueError(f"'{manifest.name}' is already installed (use upgrade)")
        was_enabled = (dest / ".enabled").exists()
        shutil.rmtree(dest)
    else:
        was_enabled = False

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)

    enabled_marker = dest / ".enabled"
    want_enabled = enable or (upgrade and was_enabled)
    if want_enabled:
        enabled_marker.touch()
    elif enabled_marker.exists():
        enabled_marker.unlink()

    reg._audit("ext.installed", name=manifest.name, version=manifest.version,
               scope=manifest.scope, reason="upgrade" if upgrade else "add")

    return {
        "name": manifest.name,
        "version": manifest.version,
        "scope": manifest.scope,
        "enabled": enabled_marker.exists(),
    }


def set_enabled(
    name: str,
    *,
    enable: bool,
    tenant_id: str | None = None,
    session_id: str = "",
    project_root: str | Path | None = None,
) -> dict:
    """Enable/disable an installed extension. Raises PermissionError for a core
    layer (``corvin.*``) with the ADR-0142 wording, KeyError when the extension
    is not installed."""
    if _is_core_name(name):
        raise PermissionError(_CORE_REMOVE_MSG.format(name=name))
    reg = ExtensionRegistry(
        tenant_id=tenant_id,
        session_id=session_id or "",
        project_root=Path(project_root) if project_root else None,
    )
    reg.discover()
    manifest = reg.get(name)
    if manifest is None or manifest.root is None:
        raise KeyError(f"extension '{name}' is not installed")
    marker = manifest.root / ".enabled"
    if enable:
        marker.touch()
        reg._audit("ext.enabled", name=name, version=manifest.version,
                   scope=manifest.scope, reason="console")
    else:
        if marker.exists():
            marker.unlink()
        reg._audit("ext.disabled", name=name, version=manifest.version,
                   scope=manifest.scope, reason="console")
    return {"name": name, "version": manifest.version,
            "scope": manifest.scope, "enabled": enable}


def remove(
    name: str,
    *,
    tenant_id: str | None = None,
    session_id: str = "",
    project_root: str | Path | None = None,
) -> dict:
    """Remove an installed non-core extension. Raises PermissionError for a core
    layer (``corvin.*``), KeyError when the extension is not installed."""
    if _is_core_name(name):
        raise PermissionError(_CORE_REMOVE_MSG.format(name=name))
    reg = ExtensionRegistry(
        tenant_id=tenant_id,
        session_id=session_id or "",
        project_root=Path(project_root) if project_root else None,
    )
    reg.discover()
    manifest = reg.get(name)
    if manifest is None or manifest.root is None:
        raise KeyError(f"extension '{name}' is not installed")
    version, scope = manifest.version, manifest.scope
    shutil.rmtree(manifest.root)
    reg._audit("ext.removed", name=name, version=version,
               scope=scope, reason="console")
    return {"name": name, "version": version, "scope": scope}


# ── list ─────────────────────────────────────────────────────────────────────
def cmd_list(args: argparse.Namespace) -> int:
    reg = _make_registry(args)
    reg.discover()
    print("CORE LAYERS (immutable — protected by ADR-0141 Layer Integrity Protocol)")
    for c in reg.list_core():
        print(f"  {c['name']:<22} v{c['version']:<5} active   {c['description']}")
    print()
    print("EXTENSIONS (user-managed)")
    exts = reg.list_extensions()
    if not exts:
        print("  (none installed)")
    for m in sorted(exts, key=lambda e: e.name):
        status = "active" if m.enabled else "disabled"
        events = ", ".join(sorted({h.event for h in m.hooks})) or "—"
        print(f"  {m.name:<22} v{m.version:<5} {status:<8} scope={m.scope}   hooks: {events}")
    return 0


# ── info ─────────────────────────────────────────────────────────────────────
def cmd_info(args: argparse.Namespace) -> int:
    name = args.name
    reg = _make_registry(args)
    if _is_core_name(name):
        for c in reg.list_core():
            if c["name"] == name:
                print(f"{name} v{c['version']} — CORE LAYER (immutable, ADR-0141)")
                print(f"  {c['description']}")
                return 0
        _eprint(f"Error: unknown core layer '{name}'")
        return 1
    reg.discover()
    m = reg.get(name)
    if m is None:
        _eprint(f"Error: extension '{name}' is not installed")
        return 1
    print(f"{m.name} v{m.version}")
    print(f"  description : {m.description}")
    print(f"  author      : {m.author}")
    print(f"  license     : {m.license}")
    print(f"  scope       : {m.scope}")
    print(f"  status      : {'enabled' if m.enabled else 'disabled'}")
    print(f"  hooks       :")
    for h in m.hooks:
        print(f"    - {h.event} (priority={h.priority}) → {h.script}")
    if m.provides:
        print(f"  provides    : {', '.join(p['name'] + ' ' + str(p.get('version','')) for p in m.provides)}")
    if m.requires:
        print(f"  requires    : {', '.join(m.requires)}")
    if m.mcp_tools:
        print(f"  mcp_tools   : {len(m.mcp_tools)}")
    return 0


# ── export ───────────────────────────────────────────────────────────────────
def cmd_export(args: argparse.Namespace) -> int:
    name = args.name
    reg = _make_registry(args)
    reg.discover()
    m = reg.get(name)
    if m is None or m.root is None:
        _eprint(f"Error: extension '{name}' is not installed")
        return 1
    out = Path(args.out) if args.out else Path.cwd() / f"{name}-{m.version}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        # Exclude the runtime .enabled marker — exports are portable, scope-neutral.
        for child in sorted(m.root.iterdir()):
            if child.name == ".enabled":
                continue
            tar.add(child, arcname=f"{name}/{child.name}")
    print(f"Exported {name} v{m.version} → {out}")
    return 0


# ── custom layer subcommands (ADR-0156 M1) ────────────────────────────────────

def cmd_custom_install(args: argparse.Namespace) -> int:
    """Install a custom layer from a directory or .tar.gz."""
    try:
        rec = _clr.install_layer(
            args.path,
            tenant_id=getattr(args, "tenant", None),
            upgrade=getattr(args, "upgrade", False),
        )
        state = "active" if rec.active else "disabled"
        print(f"Installed custom layer {rec.name} v{rec.version} (tier={rec.tier}) — {state}.")
        if not rec.active:
            print(f"Run 'corvin-layer custom enable {rec.name}' to activate it.")
        return 0
    except _clr.CustomLayerNameError as exc:
        _eprint(f"Error (namespace): {exc}")
        return 1
    except _clr.CustomLayerManifestError as exc:
        _eprint(f"Error (manifest): {exc}")
        return 1
    except ValueError as exc:
        _eprint(f"Error: {exc}")
        return 1
    except _clr.CustomLayerError as exc:
        _eprint(f"Error: {exc}")
        return 1


def cmd_custom_upgrade(args: argparse.Namespace) -> int:
    """Upgrade an installed custom layer from a directory or .tar.gz."""
    args.upgrade = True
    return cmd_custom_install(args)


def cmd_custom_list(args: argparse.Namespace) -> int:
    """List installed custom layers for the current tenant."""
    layers = _clr.list_layers(tenant_id=getattr(args, "tenant", None))
    if not layers:
        print("No custom layers installed.")
        return 0
    print("CUSTOM LAYERS (ADR-0156)")
    for rec in layers:
        status = "active  " if rec.active else "disabled"
        print(f"  {rec.name:<30} v{rec.version:<8} tier={rec.tier}  {status}  {rec.display_name}")
    return 0


def cmd_custom_enable(args: argparse.Namespace) -> int:
    """Enable a custom layer."""
    try:
        rec = _clr.enable_layer(args.name, tenant_id=getattr(args, "tenant", None))
        print(f"Enabled custom layer {rec.name}.")
        return 0
    except _clr.CustomLayerNotFoundError as exc:
        _eprint(f"Error: {exc}")
        return 1


def cmd_custom_disable(args: argparse.Namespace) -> int:
    """Disable a custom layer without removing it."""
    try:
        rec = _clr.disable_layer(args.name, tenant_id=getattr(args, "tenant", None))
        print(f"Disabled custom layer {rec.name}.")
        return 0
    except _clr.CustomLayerNotFoundError as exc:
        _eprint(f"Error: {exc}")
        return 1


def cmd_custom_remove(args: argparse.Namespace) -> int:
    """Remove a custom layer (disable + delete files + registry entry)."""
    try:
        rec = _clr.remove_layer(args.name, tenant_id=getattr(args, "tenant", None))
        print(f"Removed custom layer {rec.name}.")
        return 0
    except _clr.CustomLayerNotFoundError as exc:
        _eprint(f"Error: {exc}")
        return 1


def cmd_custom_export(args: argparse.Namespace) -> int:
    """Export a custom layer as a .tar.gz archive."""
    try:
        out = _clr.export_layer(
            args.name,
            dest_path=getattr(args, "out", None) or None,
            tenant_id=getattr(args, "tenant", None),
        )
        print(f"Exported {args.name} → {out}")
        return 0
    except _clr.CustomLayerNotFoundError as exc:
        _eprint(f"Error: {exc}")
        return 1
    except _clr.CustomLayerError as exc:
        _eprint(f"Error: {exc}")
        return 1


def _build_custom_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the ``custom`` subcommand group with its own sub-subparsers."""
    custom_p = sub.add_parser(
        "custom",
        help="manage custom layers (ADR-0156)",
    )
    custom_sub = custom_p.add_subparsers(dest="custom_command", required=True)

    sp = custom_sub.add_parser("install", help="install a custom layer from a directory or .tar.gz")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_custom_install)

    sp = custom_sub.add_parser("upgrade", help="re-install a custom layer (preserves active state)")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_custom_upgrade)

    sp = custom_sub.add_parser("list", help="list installed custom layers")
    sp.set_defaults(func=cmd_custom_list)

    sp = custom_sub.add_parser("enable", help="enable an installed custom layer")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_custom_enable)

    sp = custom_sub.add_parser("disable", help="disable a custom layer without removing it")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_custom_disable)

    sp = custom_sub.add_parser("remove", help="remove a custom layer")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_custom_remove)

    sp = custom_sub.add_parser("export", help="export a custom layer as a .tar.gz archive")
    sp.add_argument("name")
    sp.add_argument("--out", default=None, help="output tarball path")
    sp.set_defaults(func=cmd_custom_export)


# ── argparse wiring ──────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="corvin-layer",
                                description="Manage CorvinOS layer extensions (ADR-0142).")
    p.add_argument("--tenant", default=None, help="tenant id (default: CORVIN_TENANT_ID or _default)")
    p.add_argument("--session", default="", help="session id (for session-scoped extensions)")
    p.add_argument("--project-root", default=None, help="project root for project-scoped extensions (default: cwd)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("add", help="install an extension from a directory (disabled by default)")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("upgrade", help="re-install an extension from its source directory")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_upgrade)

    sp = sub.add_parser("remove", help="remove a non-core extension")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("enable", help="enable a disabled extension")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_enable)

    sp = sub.add_parser("disable", help="disable an extension without removing it")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_disable)

    sp = sub.add_parser("list", help="list core layers + extensions")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("info", help="show extension manifest detail")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("validate", help="lint a layer.yaml before installing")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("export", help="export an installed extension as a tarball")
    sp.add_argument("name")
    sp.add_argument("--out", default=None, help="output tarball path")
    sp.set_defaults(func=cmd_export)

    # ADR-0156 M1 — custom layer subcommand group
    _build_custom_subparser(sub)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
