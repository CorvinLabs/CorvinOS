#!/usr/bin/env python3
"""corvin-erasure — DPO-facing CLI for GDPR Art. 17 erasure (M4.5).

Thin wrapper around :class:`erasure_orchestrator.ErasureOrchestrator`.
Loads the tenant config, registers handlers (built-in stub chain plus
operator-installed real handlers via the registration entry-point),
runs erasure, prints a structured result, and exits with a code that
reflects the overall status.

Subcommands::

    corvin-erasure run <subject_id> --requester DPO [options]
    corvin-erasure list                  # past requests under this tenant
    corvin-erasure show <request_id>     # show a previous trail file

Exit codes for ``run``:

  0  overall_status == COMPLETED
  2  overall_status == PARTIAL
  3  overall_status == FAILED
  1  fatal infrastructure problem (no tenant config, orchestrator
     construction failed, etc.) — reserved for unexpected errors

Handler registration:

  The DPO usually wants every registered ErasureHandler in the
  process. By default the CLI registers ``builtin_stub_chain()`` so
  five SKIPPED entries land in the audit chain plus the trail file —
  giving the operator visibility into which real handlers are still
  missing.

  Real handlers ship in follow-up commits per layer (L7 / L24 / L28
  / L33 + identity-mapping). When those land they call the
  ``register_corvin_erasure_handler`` entry-point — currently a
  stub list, populated by side-effecting imports in this CLI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    new = Path.home() / ".corvin"
    legacy = Path.home() / ".corvinOS"
    if new.is_dir():
        return new
    if legacy.is_dir():
        return legacy
    return new


def _tenant_id() -> str:
    return os.environ.get("CORVIN_TENANT_ID") or "_default"


def _tenant_root(home: Path, tenant_id: str) -> Path:
    new = home / "tenants" / tenant_id
    if new.is_dir():
        return new
    # Legacy single-tenant layout
    return home


def _load_yaml(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        return yaml.safe_load(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return None


# Registration entry-point. Real per-layer handler modules append to
# this list at import time (side-effecting registration). The CLI
# imports the layer modules — if any aren't available, the
# corresponding handler is missing and the builtin stub covers it.
_REGISTERED_HANDLERS: list[Any] = []


def register_handler(handler: Any) -> None:
    """Entry point for real per-layer handler modules.

    Usage from a follow-up handler module (M4.6 / per-layer commits)::

        from corvin_erasure import register_handler
        register_handler(L28RecallHandler(...))
    """
    _REGISTERED_HANDLERS.append(handler)


def _maybe_register_real_handlers(tenant_id: str) -> None:
    """Best-effort: import erasure_handlers.real_handler_chain and
    register each. Silently no-op when the module isn't installed
    (Apache-only deployments that don't ship per-layer handlers stay
    on the builtin stub chain).
    """
    try:
        from erasure_handlers import real_handler_chain  # type: ignore
    except ImportError:
        return
    # Only register if no handler exists for that layer_id yet — keeps
    # the CLI idempotent across repeat invocations and respects an
    # operator's custom registration from another import.
    existing = {h.layer_id for h in _REGISTERED_HANDLERS}
    for h in real_handler_chain(tenant_id=tenant_id):
        if h.layer_id not in existing:
            register_handler(h)


def _build_orchestrator(home: Path, tenant_id: str) -> Any:
    from erasure_orchestrator import (  # type: ignore
        ErasureOrchestrator,
        builtin_stub_chain,
        make_forge_audit_writer,
    )

    tenant_root = _tenant_root(home, tenant_id)
    trail_dir = tenant_root / "global" / "erasure"
    audit_path = tenant_root / "global" / "forge" / "audit.jsonl"

    orch = ErasureOrchestrator(
        tenant_id=tenant_id,
        trail_dir=trail_dir,
        audit_writer=make_forge_audit_writer(audit_path),
    )

    # Real handlers registered via register_handler() take precedence;
    # any layer that hasn't registered falls back to a stub from the
    # builtin chain so the gap is visible in audit + trail file.
    registered_ids = {h.layer_id for h in _REGISTERED_HANDLERS}
    for h in _REGISTERED_HANDLERS:
        orch.register_handler(h)
    for stub in builtin_stub_chain():
        if stub.layer_id not in registered_ids:
            orch.register_handler(stub)

    return orch


# ── subcommands ──────────────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> int:
    from erasure_orchestrator import ErasureRequest, OverallStatus  # type: ignore

    home = Path(args.home).expanduser() if args.home else _corvin_home()
    if not home.is_dir():
        print(f"FATAL: corvin_home not found at {home}", file=sys.stderr)
        return 2

    tenant_id = args.tenant or _tenant_id()
    try:
        req = ErasureRequest(
            subject_id=args.subject_id,
            requester=args.requester,
            scope=args.scope,
            notes=args.notes or "",
        )
    except ValueError as e:
        print(f"erasure request validation: {e}", file=sys.stderr)
        return 2

    # Real per-layer handlers (L28 / L33 / L7 / L24 / L16-identity)
    # registered by default when erasure_handlers module is available.
    # Disable via --use-stubs to fall back to the M4-shipped stub chain.
    if not args.use_stubs:
        _maybe_register_real_handlers(tenant_id)

    try:
        orch = _build_orchestrator(home, tenant_id)
    except Exception as e:  # noqa: BLE001
        print(f"orchestrator construction failed: {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        layers = orch.list_layers()
        out = {
            "dry_run": True,
            "request_id": req.request_id,
            "subject_id": req.subject_id,
            "tenant_id": tenant_id,
            "handlers_registered": layers,
            "note": "no handlers were invoked — drop --dry-run to execute",
        }
        print(json.dumps(out, indent=2 if args.format == "json" else None))
        return 0

    # Purge the bg-monitor's personal-data store (bg_watch.json) before the
    # orchestrator runs — this file is outside the session dir and not covered
    # by the handler chain's path-based sweep.  Best-effort, never blocks erasure.
    try:
        from bg_monitor import purge_user as _bg_purge  # type: ignore
        removed = _bg_purge(req.subject_id)
        if removed:
            print(f"bg_monitor: purged {removed} wakeup entry/entries for {req.subject_id}")
    except Exception:
        pass

    # Purge the durable completion-notification queue (pending_notifications/)
    # for the same reason: its records live at the CORVIN_HOME root, outside the
    # session dir and per-tenant sweep, and carry routing PII (sender uid +
    # chat_id). Best-effort, never blocks erasure.
    try:
        from completion_notify import purge_user as _cn_purge  # type: ignore
        cn_removed = _cn_purge(req.subject_id)
        if cn_removed:
            print(f"completion_notify: purged {cn_removed} record(s) for {req.subject_id}")
    except Exception:
        pass

    result = orch.execute(req)

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"Erasure request {result.request.request_id}")
        print(f"  subject_id      : {result.request.subject_id}")
        print(f"  requester       : {result.request.requester}")
        print(f"  scope           : {result.request.scope}")
        print(f"  overall_status  : {result.overall_status.value}")
        print(f"  applied/skipped/failed : "
              f"{result.applied_count}/"
              f"{sum(1 for r in result.per_layer if r.status.value == 'skipped')}/"
              f"{result.failed_count}")
        print()
        print(f"{'layer_id':22s}  {'status':9s}  {'count':>6s}  reason")
        print("  ".join(["─" * 22, "─" * 9, "─" * 6, "─" * 40]))
        for r in result.per_layer:
            reason = (r.reason or "")[:60]
            print(f"{r.layer_id:22s}  {r.status.value:9s}  {r.count:6d}  {reason}")
        trail = home / "tenants" / tenant_id / "global" / "erasure" / f"{result.request.request_id}.json"
        if trail.is_file():
            print()
            print(f"trail: {trail}")

    trail_path = (home / "tenants" / tenant_id / "global" / "erasure"
                  / f"{result.request.request_id}.json")

    if result.overall_status == OverallStatus.COMPLETED:
        return 0
    if result.overall_status == OverallStatus.PARTIAL:
        print(
            f"WARNING: erasure PARTIAL — {result.failed_count} handler(s) failed. "
            f"See {trail_path} for details.",
            file=sys.stderr,
        )
        return 2
    # FAILED
    print(
        f"ERROR: erasure FAILED. See {trail_path} for details.",
        file=sys.stderr,
    )
    return 3


def cmd_list(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser() if args.home else _corvin_home()
    tenant_id = args.tenant or _tenant_id()
    trail_dir = _tenant_root(home, tenant_id) / "global" / "erasure"
    if not trail_dir.is_dir():
        print(f"no trail directory at {trail_dir}")
        return 0
    trails = sorted(trail_dir.glob("er-*.json"))
    if not trails:
        print(f"no past erasure requests under {trail_dir}")
        return 0
    if args.format == "json":
        rows = []
        for t in trails:
            try:
                data = json.loads(t.read_text())
                rows.append({
                    "request_id": data["request"]["request_id"],
                    "subject_id": data["request"]["subject_id"],
                    "requester": data["request"]["requester"],
                    "started_at": data["started_at"],
                    "completed_at": data["completed_at"],
                    "overall_status": data["overall_status"],
                })
            except (OSError, json.JSONDecodeError, KeyError) as e:
                rows.append({"file": t.name, "error": str(e)})
        print(json.dumps(rows, indent=2))
    else:
        print(f"{'request_id':18s}  {'started':10s}  {'subject_id':16s}  status")
        print("  ".join(["─" * 18, "─" * 10, "─" * 16, "─" * 10]))
        for t in trails:
            try:
                data = json.loads(t.read_text())
                ts = time.strftime("%Y-%m-%d",
                                   time.localtime(data["started_at"]))
                print(f"{data['request']['request_id']:18s}  "
                      f"{ts:10s}  "
                      f"{data['request']['subject_id']:16s}  "
                      f"{data['overall_status']}")
            except (OSError, json.JSONDecodeError, KeyError):
                print(f"{t.name}  (malformed)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser() if args.home else _corvin_home()
    tenant_id = args.tenant or _tenant_id()
    trail_dir = _tenant_root(home, tenant_id) / "global" / "erasure"
    trail = trail_dir / f"{args.request_id}.json"
    if not trail.is_file():
        print(f"no trail file at {trail}", file=sys.stderr)
        return 2
    print(trail.read_text())
    return 0


# ── parser + entry point ─────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="corvin-erasure",
        description="GDPR Art. 17 erasure orchestrator (Layer 36 / ADR-0045).",
    )
    p.add_argument("--home", default=None,
                   help="override corvin_home (default: $CORVIN_HOME or ~/.corvin)")
    p.add_argument("--tenant", default=None,
                   help="tenant id (default: $CORVIN_TENANT_ID or _default)")
    p.add_argument("--format", choices=("text", "json"), default="text",
                   help="output format (default: text)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run",
        help="execute erasure for a subject_id")
    pr.add_argument("subject_id",
        help="pseudonymous subject identifier (operator-chosen, "
             "regex ^[A-Za-z0-9._\\-]{1,128}$; reject raw email / name)")
    pr.add_argument("--requester", required=True,
        help="who initiated the request (e.g. dpo@example.com)")
    pr.add_argument("--scope", default="all",
        help="free-form operator hint; passed to handlers (default: all)")
    pr.add_argument("--notes", default="",
        help="free-form notes preserved on trail file (NOT in audit chain)")
    pr.add_argument("--dry-run", action="store_true",
        help="list registered handlers without invoking them")
    pr.add_argument("--use-stubs", action="store_true",
        help="skip auto-registration of real per-layer handlers; use "
             "the M4-shipped stub chain instead (useful for tests / when "
             "you only want the audit-trail without actual purges)")
    pr.set_defaults(fn=cmd_run)

    pl = sub.add_parser("list",
        help="show past erasure requests under this tenant")
    pl.set_defaults(fn=cmd_list)

    ps = sub.add_parser("show",
        help="print the trail file for a request_id")
    ps.add_argument("request_id",
        help="erasure request id, e.g. er-abc123def456")
    ps.set_defaults(fn=cmd_show)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
