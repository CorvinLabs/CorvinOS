#!/usr/bin/env python3
"""corvin-incident — L39 Incident Tracker CLI (ADR-0057).

Subcommands:
  open         Open a new incident record
  list         List incidents (optionally filtered by status)
  show         Show full incident record including description
  update       Update incident status
  close        Close an incident, recording duration
  scan         Scan audit chain for latent consent/disclosure failures
  notify-draft Generate Art. 73 §2 notification draft
  export       Export all incidents as JSON (for audit packages)

Must NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure shared module path
_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parent.parent / "bridges" / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


def _load_tracker():
    try:
        import incident_tracker
        return incident_tracker
    except ImportError as exc:
        sys.exit(f"Error: cannot import incident_tracker: {exc}")


def cmd_open(args):
    t = _load_tracker()
    record = t.open_incident(
        category=args.category,
        trigger_event=args.trigger_event,
        trigger_chain_hash=args.trigger_chain_hash or "0" * 16,
        description=args.description,
        severity=args.severity,
        tenant_id=args.tenant,
    )
    print(f"Opened incident {record.incident_id}")
    print(f"  category:  {record.category}")
    print(f"  severity:  {record.severity}")
    print(f"  status:    {record.status}")
    print(f"  detected:  {record.detected_at}")


def cmd_list(args):
    t = _load_tracker()
    records = t.list_incidents(
        tenant_id=args.tenant,
        status=args.status if args.status != "all" else None,
    )
    if not records:
        print("No incidents found.")
        return
    fmt = "{:<38} {:<14} {:<12} {:<12} {}"
    print(fmt.format("INCIDENT_ID", "CATEGORY", "SEVERITY", "STATUS", "DETECTED"))
    print("-" * 110)
    for r in records:
        cat_short = r.category[:13]
        print(fmt.format(r.incident_id, cat_short, r.severity, r.status, r.detected_at[:19]))


def cmd_show(args):
    t = _load_tracker()
    record = t.load_incident(args.incident_id, tenant_id=args.tenant)
    if record is None:
        sys.exit(f"Incident not found: {args.incident_id}")
    d = record.to_dict()
    for k, v in d.items():
        if v is not None:
            print(f"{k:<22}: {v}")


def cmd_update(args):
    t = _load_tracker()
    notified_at = args.notified_at or None
    record = t.update_incident(
        args.incident_id,
        args.status,
        tenant_id=args.tenant,
        notified_at=notified_at,
    )
    print(f"Updated {record.incident_id}: status → {record.status}")


def cmd_close(args):
    t = _load_tracker()
    record = t.close_incident(args.incident_id, tenant_id=args.tenant)
    print(f"Closed {record.incident_id}  (closed_at: {record.closed_at})")


def cmd_scan(args):
    t = _load_tracker()
    findings = t.scan_audit_chain(since_days=args.since, tenant_id=args.tenant)
    if not findings:
        print(f"No potential issues found in the last {args.since} days.")
        return
    print(f"Found {len(findings)} potential issue(s) — review and open incidents as needed:\n")
    for i, f in enumerate(findings, 1):
        print(f"  [{i}] {f.get('potential_category')} — uid: {f.get('uid_hash')} "
              f"event: {f.get('event')}")
    print("\nUse 'corvin-incident open --category <category> ...' to create records.")


def cmd_notify_draft(args):
    t = _load_tracker()
    draft = t.notify_draft(
        args.incident_id,
        tenant_id=args.tenant,
        authority=args.authority,
        operator_name=args.operator_name,
    )
    if args.output:
        Path(args.output).write_text(draft, encoding="utf-8")
        print(f"Notification draft written to {args.output}")
    else:
        print(draft)


def cmd_export(args):
    t = _load_tracker()
    data = t.export_incidents(tenant_id=args.tenant)
    out = json.dumps(data, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Exported {len(data)} incident(s) to {args.output}")
    else:
        print(out)


def main():
    parser = argparse.ArgumentParser(
        prog="corvin-incident",
        description="Corvin L39 Incident Tracker — EU AI Act Art. 73 (ADR-0057)",
    )
    parser.add_argument(
        "--tenant",
        default=os.environ.get("CORVIN_TENANT_ID", "_default"),
        help="Tenant ID (default: _default)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # open
    p_open = sub.add_parser("open", help="Open a new incident record")
    p_open.add_argument("--category", required=True,
                        choices=["chain_integrity", "consent_bypass",
                                 "engine_policy_violation", "pii_in_audit_chain",
                                 "secret_exposure", "disclosure_failure"])
    p_open.add_argument("--trigger-event", default="manual",
                        help="Audit event type that triggered detection")
    p_open.add_argument("--trigger-chain-hash", default="0" * 16,
                        help="First 16 hex chars of triggering audit entry hash")
    p_open.add_argument("--description", required=True,
                        help="Operator-authored description (not in audit chain)")
    p_open.add_argument("--severity", default="serious",
                        choices=["serious", "warning", "informational"])
    p_open.set_defaults(func=cmd_open)

    # list
    p_list = sub.add_parser("list", help="List incidents")
    p_list.add_argument("--status", default="all",
                        choices=["all", "open", "contained", "notified", "closed"])
    p_list.set_defaults(func=cmd_list)

    # show
    p_show = sub.add_parser("show", help="Show full incident record")
    p_show.add_argument("incident_id")
    p_show.set_defaults(func=cmd_show)

    # update
    p_update = sub.add_parser("update", help="Update incident status")
    p_update.add_argument("incident_id")
    p_update.add_argument("--status", required=True,
                          choices=["open", "contained", "notified", "closed"])
    p_update.add_argument("--notified-at",
                          help="ISO-8601 UTC timestamp of authority notification")
    p_update.set_defaults(func=cmd_update)

    # close
    p_close = sub.add_parser("close", help="Close an incident")
    p_close.add_argument("incident_id")
    p_close.set_defaults(func=cmd_close)

    # scan
    p_scan = sub.add_parser("scan", help="Scan audit chain for latent issues")
    p_scan.add_argument("--since", type=int, default=30,
                        help="Look back N days (default: 30)")
    p_scan.set_defaults(func=cmd_scan)

    # notify-draft
    p_nd = sub.add_parser("notify-draft",
                           help="Generate Art. 73 §2 notification draft")
    p_nd.add_argument("incident_id")
    p_nd.add_argument("--authority", default="BSI",
                      help="Supervisory authority name")
    p_nd.add_argument("--operator-name",
                      default="[OPERATOR: FILL IN]",
                      help="Operator legal name")
    p_nd.add_argument("--output", help="Output file path (default: stdout)")
    p_nd.set_defaults(func=cmd_notify_draft)

    # export
    p_export = sub.add_parser("export", help="Export all incidents as JSON")
    p_export.add_argument("--output", help="Output file path (default: stdout)")
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args()
    try:
        args.func(args)
    except FileNotFoundError as exc:
        sys.exit(f"Error: {exc}")
    except ValueError as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
