#!/usr/bin/env python3
"""forge — runtime tool generation CLI for Claude Code (MVP).

Usage:
  forge.py create   --name N --desc D --schema FILE --impl FILE [--runtime python|bash]
  forge.py list
  forge.py show     --name N
  forge.py call     --name N --input JSON
  forge.py delete   --name N
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from forge.multi_registry import MultiRegistry
from forge.permissions import PermissionStore
from forge.registry import Registry
from forge.runner import (
    PermissionDenied,
    SchemaError,
    TamperError,
    ToolError,
    run_tool,
)


def _default_root() -> Path:
    """Resolve the default workspace root for CLI invocations.

    Priority:
      1. ``FORGE_ROOT`` (explicit env override; tests + ops tooling)
      2. Workspace-scope detection via ``forge.scope.detect_scope()``
         which honours CORVIN_CHANNEL_ID (-> session),
         git-repo (-> project), or fallback (-> user).
      3. Tenant resolution: CORVIN_TENANT_ID env or _default
    """
    import os
    env = os.environ.get("FORGE_ROOT")
    if env:
        return Path(env).expanduser()
    from forge.scope import detect_scope, scope_root
    from forge.paths import _resolve_tenant_id
    tenant_id = _resolve_tenant_id(os.environ.get("CORVIN_TENANT_ID"))
    return scope_root(detect_scope(), tenant_id=tenant_id)


DEFAULT_ROOT = _default_root()


def _registry(args) -> Registry:
    return Registry(Path(args.root))


def _root_was_overridden(args) -> bool:
    """Did the user pass --root explicitly (vs. relying on default)?"""
    return str(Path(args.root)) != str(DEFAULT_ROOT)


def _multi(args) -> MultiRegistry:
    """Build a MultiRegistry — legacy single-root if --root was overridden."""
    return MultiRegistry()


def cmd_create(args) -> int:
    schema = json.loads(Path(args.schema).read_text())
    impl = Path(args.impl).read_text()
    if _root_was_overridden(args):
        # Legacy: explicit --root means single-root registry. The CLI
        # --scope flag is reused for the (legacy) permission-scope here,
        # mirroring pre-J.1.2 behaviour.
        spec = _registry(args).create(
            name=args.name,
            description=args.desc,
            input_schema=schema,
            impl=impl,
            runtime=args.runtime,
            scope=args.scope if args.scope in ("session", "project", "user") else "session",
            overwrite=args.overwrite,
        )
    else:
        spec = _multi(args).create(
            scope=args.scope,
            name=args.name,
            description=args.desc,
            input_schema=schema,
            impl=impl,
            runtime=args.runtime,
            overwrite=args.overwrite,
        )
    print(f"forged {spec.name}  sha={spec.sha256}  runtime={spec.runtime}")
    return 0


def cmd_list(args) -> int:
    if _root_was_overridden(args):
        tools = [(None, t) for t in _registry(args).list()]
    else:
        tools = _multi(args).list_with_scope()
    if not tools:
        print("(no tools registered)")
        return 0
    for ws_scope, t in tools:
        scope_tag = f"[{ws_scope}]" if ws_scope else ""
        print(
            f"  {t.name:24s}  {scope_tag:11s}  "
            f"calls={t.call_count:<4d}  {t.description}"
        )
    return 0


def cmd_show(args) -> int:
    spec = (
        _registry(args).get(args.name)
        if _root_was_overridden(args)
        else _multi(args).get(args.name)
    )
    if not spec:
        print(f"unknown tool: {args.name}", file=sys.stderr)
        return 2
    print(json.dumps(
        {
            "name": spec.name,
            "description": spec.description,
            "runtime": spec.runtime,
            "input_schema": spec.input_schema,
            "scope": spec.scope,
            "sha256": spec.sha256,
            "call_count": spec.call_count,
            "impl_path": spec.impl_path,
        },
        indent=2,
    ))
    return 0


def cmd_call(args) -> int:
    payload = json.loads(args.input)
    mode = "yes" if args.yes else ("deny" if args.deny else "ask")
    # Resolve the registry that holds the named tool (lookup with shadowing
    # in multi-mode; single-root in legacy mode).
    if _root_was_overridden(args):
        reg = _registry(args)
    else:
        mr = _multi(args)
        ws_scope = mr.find_scope(args.name)
        if ws_scope is None:
            print(f"unknown tool: {args.name}", file=sys.stderr)
            return 2
        reg = mr._registry(ws_scope)
    try:
        result = run_tool(
            reg,
            args.name,
            payload,
            timeout=args.timeout,
            permission_mode=mode,
            use_sandbox=not args.no_sandbox,
        )
    except SchemaError as e:
        print(f"schema error: {e}", file=sys.stderr)
        return 3
    except PermissionDenied as e:
        print(f"permission denied: {e}", file=sys.stderr)
        return 5
    except TamperError as e:
        print(f"tamper detected: {e}", file=sys.stderr)
        return 6
    except ToolError as e:
        print(f"tool error: {e}", file=sys.stderr)
        return 4
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def cmd_approve(args) -> int:
    reg = _registry(args)
    spec = reg.get(args.name)
    if not spec:
        print(f"unknown tool: {args.name}", file=sys.stderr)
        return 2
    PermissionStore(reg.root).record(spec.name, spec.sha256, mode="yes")
    print(f"approved {spec.name} (sha={spec.sha256})")
    return 0


def cmd_revoke(args) -> int:
    PermissionStore(_registry(args).root).revoke(args.name)
    print(f"revoked {args.name}")
    return 0


def cmd_mcp(args) -> int:
    from forge.mcp_server import MCPServer
    return MCPServer(Path(args.root), permission_mode=args.permission_mode).serve()


def cmd_cleanup(args) -> int:
    from forge.runs import cleanup_runs
    from forge import cache as _cache
    reg = _registry(args)
    result = cleanup_runs(reg.root, keep=args.keep)
    print(json.dumps(result, indent=2))
    if args.purge_cache:
        n = _cache.invalidate(reg.root, key=None)
        print(f"cache: purged {n} entries")
    return 0


def cmd_run_show(args) -> int:
    from forge.runs import show_run
    reg = _registry(args)
    try:
        rec = show_run(reg.root, run_id=args.id)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(rec, indent=2, default=str))
    else:
        print(rec.get("summary_md", f"# Run {rec.get('run_id')}\n(no summary)"))
    return 0


def cmd_run_list(args) -> int:
    from forge.runs import list_runs
    runs = list_runs(_registry(args).root)
    for r in runs[: args.limit]:
        print(f"  {r.get('run_id'):40s}  {r.get('tool', '?'):20s}  "
              f"status={r.get('status', '?'):8s}  "
              f"dur={r.get('duration_s', 0):>6.3f}s  "
              f"sandbox={r.get('sandbox', '?')}")
    return 0


def cmd_sync(args) -> int:
    from forge.sync import sync
    target = Path(args.target).expanduser() if args.target else None
    records = sync(_registry(args).root,
                   target_root=target, dry_run=args.dry_run)
    for r in records:
        print(f"  [{r.action:8s}]  {r.name:24s}  "
              f"{r.src} -> {r.dst}"
              f"{('  (' + r.reason + ')') if r.reason else ''}")
    if not records:
        print("  (no skills to sync)")
    return 0


def cmd_audit_verify(args) -> int:
    from forge.security_events import verify_chain
    reg = _registry(args)
    audit_path = reg.root / reg.AUDIT_NAME
    ok, problems = verify_chain(audit_path)
    if ok:
        print(f"audit OK  ({audit_path})")
        return 0
    print(f"audit INTEGRITY VIOLATION  ({audit_path})", file=sys.stderr)
    for p in problems:
        print(f"  line {p['line']}: {p['issue']}  {p}", file=sys.stderr)
    return 1


def cmd_delete(args) -> int:
    if _root_was_overridden(args):
        _registry(args).delete(args.name)
        print(f"deleted {args.name}")
        return 0
    ok = _multi(args).delete(args.name, scope=args.scope)
    if not ok:
        print(f"unknown tool: {args.name}", file=sys.stderr)
        return 2
    print(f"deleted {args.name}")
    return 0


def cmd_promote(args) -> int:
    skill_dir = _registry(args).promote(args.name)
    print(f"promoted {args.name} -> {skill_dir}")
    return 0


def cmd_note(args) -> int:
    text = args.text or sys.stdin.read()
    _registry(args).note(text)
    print(f"noted ({len(text)} chars)")
    return 0


def cmd_stats(args) -> int:
    print(json.dumps(_registry(args).stats(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="claude-tool-forge CLI")
    p.add_argument("--root", default=str(DEFAULT_ROOT), help="registry root dir")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create")
    pc.add_argument("--name", required=True)
    pc.add_argument("--desc", required=True)
    pc.add_argument("--schema", required=True, help="path to JSONSchema file")
    pc.add_argument("--impl", required=True, help="path to implementation file")
    pc.add_argument("--runtime", choices=["python", "bash"], default="python")
    pc.add_argument(
        "--scope",
        choices=["task", "session", "project", "user"],
        default=None,
        help="workspace scope (default: detect_scope())",
    )
    pc.add_argument("--overwrite", action="store_true")
    pc.set_defaults(fn=cmd_create)

    pl = sub.add_parser("list")
    pl.set_defaults(fn=cmd_list)

    ps = sub.add_parser("show")
    ps.add_argument("--name", required=True)
    ps.set_defaults(fn=cmd_show)

    pa = sub.add_parser("call")
    pa.add_argument("--name", required=True)
    pa.add_argument("--input", required=True, help="JSON payload")
    pa.add_argument("--timeout", type=float, default=30.0)
    pa.add_argument("--yes", action="store_true",
                    help="auto-approve permission prompt")
    pa.add_argument("--deny", action="store_true",
                    help="fail closed if not pre-approved")
    pa.add_argument("--no-sandbox", action="store_true",
                    help="skip bubblewrap (rlimits still apply)")
    pa.set_defaults(fn=cmd_call)

    pd = sub.add_parser("delete")
    pd.add_argument("--name", required=True)
    pd.add_argument(
        "--scope",
        choices=["task", "session", "project", "user"],
        default=None,
        help="restrict delete to a single workspace scope",
    )
    pd.set_defaults(fn=cmd_delete)

    pp = sub.add_parser("promote", help="materialize a forged tool as a Skill")
    pp.add_argument("--name", required=True)
    pp.set_defaults(fn=cmd_promote)

    pn = sub.add_parser("note", help="append a note to .forge/memory.md")
    pn.add_argument("text", nargs="?", help="note text (or read from stdin)")
    pn.set_defaults(fn=cmd_note)

    pt = sub.add_parser("stats", help="aggregate stats from registry + audit")
    pt.set_defaults(fn=cmd_stats)

    pap = sub.add_parser("approve",
                         help="pre-approve a tool (skip permission prompt)")
    pap.add_argument("--name", required=True)
    pap.set_defaults(fn=cmd_approve)

    pr = sub.add_parser("revoke", help="revoke a previously granted approval")
    pr.add_argument("--name", required=True)
    pr.set_defaults(fn=cmd_revoke)

    pm = sub.add_parser("mcp", help="run the MCP server (stdio transport)")
    pm.add_argument(
        "--permission-mode",
        choices=["yes", "ask", "deny"],
        default="yes",
        help="permission mode for forged tool calls",
    )
    pm.set_defaults(fn=cmd_mcp)

    pcl = sub.add_parser("cleanup", help="prune old runs (keep most recent N)")
    pcl.add_argument("--keep", type=int, default=50)
    pcl.add_argument("--purge-cache", action="store_true",
                     help="also wipe cache/")
    pcl.set_defaults(fn=cmd_cleanup)

    pruns = sub.add_parser("run-list", help="list recent runs")
    pruns.add_argument("--limit", type=int, default=20)
    pruns.set_defaults(fn=cmd_run_list)

    prs = sub.add_parser("run-show", help="show one run (default: most recent)")
    prs.add_argument("--id", default=None,
                     help="run id; default = most recent")
    prs.add_argument("--format", choices=["md", "json"], default="md")
    prs.set_defaults(fn=cmd_run_show)

    psy = sub.add_parser("sync",
                         help="copy promoted skills to ~/.claude/skills/")
    psy.add_argument("--target", default=None,
                     help="override target directory (default: ~/.claude/skills)")
    psy.add_argument("--dry-run", action="store_true")
    psy.set_defaults(fn=cmd_sync)

    pav = sub.add_parser("audit-verify",
                         help="verify hash-chain integrity of audit.jsonl")
    pav.set_defaults(fn=cmd_audit_verify)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
