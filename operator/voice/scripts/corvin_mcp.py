"""corvin-mcp — MCP Plugin Manager CLI (ADR-0096 M1/M4).

Usage::

    corvin-mcp install npm:@modelcontextprotocol/server-brave-search@0.6.2
    corvin-mcp install local:./my-tool/
    corvin-mcp install docker:ghcr.io/foo/bar:1.2.3
    corvin-mcp activate brave-search [--scope user|session|project]
    corvin-mcp deactivate brave-search [--scope user]
    corvin-mcp list [--scope user]
    corvin-mcp show <id>
    corvin-mcp remove <id>
    corvin-mcp update [<id>] [--all]
    corvin-mcp search <query>

Exit codes: 0 success, 1 runtime error, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running from repo root without installation.
_HERE = Path(__file__).resolve()
_SHARED = _HERE.parents[2] / "bridges" / "shared"
_MCP_ROOT = _HERE.parents[2] / "mcp_manager"
for _p in (_SHARED, _MCP_ROOT):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mcp_manager import activate as _act  # type: ignore[import-not-found]
from mcp_manager import catalog as _cat  # type: ignore[import-not-found]
from mcp_manager import installer as _ins  # type: ignore[import-not-found]


def _tid() -> str:
    return os.environ.get("CORVIN_TENANT_ID") or "_default"


def _audit_path() -> Path | None:
    try:
        from forge.paths import corvin_home  # type: ignore[import-not-found]
        _forge = _HERE.parents[2] / "forge"
        if str(_forge) not in sys.path:
            sys.path.insert(0, str(_forge))
        from forge.paths import corvin_home  # noqa: F811  # type: ignore
        home = Path(corvin_home())
        return home / "tenants" / _tid() / "global" / "audit.jsonl"
    except Exception:
        return None


def _emit(event_type: str, details: dict) -> None:
    apath = _audit_path()
    if apath is None:
        return
    try:
        _forge = _HERE.parents[2] / "forge"
        if str(_forge) not in sys.path:
            sys.path.insert(0, str(_forge))
        from forge.security_events import write_event  # type: ignore[import-not-found]
        write_event(apath, event_type, details=details)
    except Exception:
        pass


# ── sub-commands ─────────────────────────────────────────────────────────────


def cmd_install(args: argparse.Namespace) -> int:
    tid = _tid()
    try:
        entry = _ins.install(args.source, tid, allow_unpin=getattr(args, "allow_unpin", False))
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _emit("mcp_plugin.installed", {
        "tool_id": entry["id"],
        "source": entry["source"],
        "tenant_id": tid,
    })
    print(f"Installed {entry['id']!r} from {entry['source']!r}")
    secrets = entry.get("secrets") or []
    if secrets:
        names = ", ".join(s["name"] for s in secrets if s.get("name"))
        print(f"  Secrets needed: {names}")
        print(f"  Configure via: corvin-mcp secrets {entry['id']}")
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    tid = _tid()
    scope = args.scope or "user"
    try:
        _act.activate(tid, args.id, scope)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _emit("mcp_plugin.activated", {
        "tool_id": args.id,
        "scope": scope,
        "tenant_id": tid,
    })
    print(f"Activated {args.id!r} (scope={scope})")
    return 0


def cmd_deactivate(args: argparse.Namespace) -> int:
    tid = _tid()
    scope = args.scope or "user"
    try:
        removed = _act.deactivate(tid, args.id, scope)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not removed:
        print(f"{args.id!r} was not active in scope={scope}")
        return 0
    _emit("mcp_plugin.deactivated", {
        "tool_id": args.id,
        "scope": scope,
        "tenant_id": tid,
    })
    print(f"Deactivated {args.id!r} (scope={scope})")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    tid = _tid()
    tools = _cat.list_tools(tid)
    if not tools:
        print("No tools installed.")
        return 0

    active = _act.load_active(tid)
    for tool in tools:
        tool_id = tool["id"]
        scopes = [s for s in _act.VALID_SCOPES if tool_id in active.get(s, [])]
        scope_tag = f" [active:{','.join(scopes)}]" if scopes else " [inactive]"
        print(f"  {tool_id:30s}  {tool['source']}{scope_tag}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    tid = _tid()
    entry = _cat.get_tool(tid, args.id)
    if entry is None:
        print(f"error: tool {args.id!r} not found", file=sys.stderr)
        return 1
    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    tid = _tid()
    # Deactivate from global scopes (user + tenant). Session and project
    # scopes require a session_key / project_dir; the CLI does not have
    # those context values, so it only clears the persistent scopes.
    for scope in ("user", "tenant"):
        _act.deactivate(tid, args.id, scope)
    removed = _ins.uninstall(args.id, tid)
    if not removed:
        print(f"error: tool {args.id!r} not found", file=sys.stderr)
        return 1
    _emit("mcp_plugin.removed", {
        "tool_id": args.id,
        "tenant_id": tid,
    })
    print(f"Removed {args.id!r}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Re-pin one tool (or all tools) to the latest available version."""
    tid = _tid()

    if getattr(args, "all", False):
        # Update all installed tools.
        tools = _cat.list_tools(tid)
        if not tools:
            print("No tools installed.")
            return 0
        exit_code = 0
        for tool in tools:
            tool_id = tool["id"]
            try:
                _ins.update(tool_id, tid)
                print(f"Updated {tool_id!r}")
                _emit("mcp_plugin.updated", {"tool_id": tool_id, "tenant_id": tid})
            except ValueError as exc:
                print(f"  skip {tool_id!r}: {exc}", file=sys.stderr)
        return exit_code

    tool_id = getattr(args, "id", None)
    if not tool_id:
        print("error: specify a tool id or --all", file=sys.stderr)
        return 2
    try:
        entry = _ins.update(tool_id, tid)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _emit("mcp_plugin.updated", {"tool_id": entry["id"], "tenant_id": tid})
    print(f"Updated {entry['id']!r} (source={entry['source']!r})")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Search the bundled manifests for matching MCP tools."""
    query = " ".join(args.query).lower().strip()
    manifests_dir = Path(__file__).resolve().parents[2] / "mcp_manager" / "mcp_manager" / "builtin_manifests"
    if not manifests_dir.is_dir():
        print("No bundled manifests found.", file=sys.stderr)
        return 0

    results = []
    for manifest_file in sorted(manifests_dir.glob("*.json")):
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(manifest, dict):
            continue
        # Search in name, description, id, and tags.
        searchable = " ".join([
            manifest.get("id") or "",
            manifest.get("name") or "",
            manifest.get("description") or "",
            " ".join(manifest.get("tags") or []),
        ]).lower()
        if not query or query in searchable:
            results.append(manifest)

    if not results:
        print(f"No bundled tools found matching {args.query!r}")
        return 0

    for m in results:
        print(f"\n  {m.get('id'):20s}  {m.get('name')}")
        print(f"    {m.get('description')}")
        tags = ", ".join(m.get("tags") or [])
        if tags:
            print(f"    Tags: {tags}")
        hint = m.get("install_hint")
        if hint:
            print(f"    Install: {hint}")
    print()
    return 0


def cmd_secrets(args: argparse.Namespace) -> int:
    tid = _tid()
    entry = _cat.get_tool(tid, args.id)
    if entry is None:
        print(f"error: tool {args.id!r} not found", file=sys.stderr)
        return 1
    secrets = entry.get("secrets") or []
    if not secrets:
        print(f"Tool {args.id!r} declares no secrets.")
        return 0

    print(f"Secrets for {args.id!r}:")
    # Canonical vault resolver (CORVIN_SECRET_VAULT → XDG_CONFIG_HOME →
    # ~/.config/corvin-voice/secrets.json) — was hardcoded, ignoring both the env
    # override and XDG (path-audit 2026-06-25 #MED11).
    try:
        from forge.secret_vault import default_vault_path as _dvp  # type: ignore
        vault_path = _dvp()
    except Exception:  # noqa: BLE001
        _env = os.environ.get("CORVIN_SECRET_VAULT")
        if _env:
            vault_path = Path(os.path.expanduser(os.path.expandvars(_env)))
        else:
            _cfg = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
            vault_path = Path(os.path.expanduser(_cfg)) / "corvin-voice" / "secrets.json"
    vault: dict = {}
    if vault_path.is_file():
        try:
            vault = json.loads(vault_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    for secret in secrets:
        name = secret.get("name", "?")
        vault_key = secret.get("vault_key", name.lower())
        configured = vault_key in vault
        status = "configured" if configured else "MISSING"
        print(f"  {name} (vault_key={vault_key!r}): {status}")

    if any(s.get("vault_key", s.get("name", "").lower()) not in vault
           for s in secrets if s.get("required")):
        print()
        print("To configure missing secrets, add them to:")
        print(f"  {vault_path}")
        print('Example: {"brave_api_key": "BSA_..."} (mode 0600)')
    return 0


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="corvin-mcp",
        description="MCP Plugin Manager (ADR-0096 M1)",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p_install = sub.add_parser("install", help="Install a tool")
    p_install.add_argument(
        "source",
        help="Source: npm:<pkg>[@ver], local:<path>, "
             "github:<owner>/<repo>[@tag|@sha], pip:<pkg>[@ver], docker:<image>[:<tag>]",
    )
    p_install.add_argument(
        "--allow-unpin", action="store_true", default=False,
        help="Allow GitHub installs from branch head (not recommended)",
    )

    p_activate = sub.add_parser("activate", help="Activate a tool")
    p_activate.add_argument("id", help="Tool ID")
    p_activate.add_argument("--scope", choices=list(_act.VALID_SCOPES),
                            default="user", help="Activation scope (default: user)")

    p_deactivate = sub.add_parser("deactivate", help="Deactivate a tool")
    p_deactivate.add_argument("id", help="Tool ID")
    p_deactivate.add_argument("--scope", choices=list(_act.VALID_SCOPES),
                              default="user")

    p_list = sub.add_parser("list", help="List installed tools")
    p_list.add_argument("--scope", choices=list(_act.VALID_SCOPES), default=None,
                        help="Filter by scope")

    p_show = sub.add_parser("show", help="Show tool details")
    p_show.add_argument("id", help="Tool ID")

    p_remove = sub.add_parser("remove", help="Remove a tool")
    p_remove.add_argument("id", help="Tool ID")

    p_secrets = sub.add_parser("secrets", help="Show secret configuration status")
    p_secrets.add_argument("id", help="Tool ID")

    p_update = sub.add_parser("update", help="Update a tool to the latest version")
    p_update.add_argument("id", nargs="?", default=None, help="Tool ID (omit with --all)")
    p_update.add_argument("--all", action="store_true", default=False,
                          help="Update all installed tools")

    p_search = sub.add_parser("search", help="Search bundled tool manifests")
    p_search.add_argument("query", nargs="*", help="Search terms (empty = list all)")

    args = parser.parse_args()

    handlers = {
        "install": cmd_install,
        "activate": cmd_activate,
        "deactivate": cmd_deactivate,
        "list": cmd_list,
        "show": cmd_show,
        "remove": cmd_remove,
        "secrets": cmd_secrets,
        "update": cmd_update,
        "search": cmd_search,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
