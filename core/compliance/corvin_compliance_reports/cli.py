"""Operator CLI for the three baseline compliance reports.

Usage:
  python -m corvin_compliance_reports.cli generate <type> [--tenant ID] [--since DUR] [--until DUR] [--output PATH] [--quiet]
  python -m corvin_compliance_reports.cli list

Report types:
  ai-act-50          EU AI Act Art. 50 — Active-Disclosure Evidence
  gdpr-30            GDPR Art. 30 — Records of Processing
  audit-attestation  Audit-Chain Integrity Attestation

Examples:
  # Last 30 days of AI Act evidence for the default tenant
  python -m corvin_compliance_reports.cli generate ai-act-50 --since 30d

  # Full GDPR RoPA for tenant 'acme' last quarter
  python -m corvin_compliance_reports.cli generate gdpr-30 \\
      --tenant acme --since 90d --output /tmp/acme-ropa.pdf
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from . import (
    __version__,
    ai_act_evidence,
    audit_attestation,
    gdpr_ropa,
    audit as _audit,
)


_GENERATORS = {
    "ai-act-50": ("ai_act_art_50", ai_act_evidence.generate),
    "gdpr-30": ("gdpr_art_30_ropa", gdpr_ropa.generate),
    "audit-attestation": ("audit_chain_attestation", audit_attestation.generate),
}


_DURATION_RE = re.compile(r"^(\d+)([smhdw])$")


def _parse_duration(s: str) -> int:
    """Parse short duration tokens into seconds. '30d' → 30*86400."""
    m = _DURATION_RE.match(s.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid duration: {s!r} (expected e.g. '7d', '30d', '12h', '90d')"
        )
    n, unit = int(m.group(1)), m.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}[unit]
    return n * mult


def _cmd_list(args: argparse.Namespace) -> int:
    print("Available report types:")
    for key, (rtype, _) in _GENERATORS.items():
        print(f"  {key:<22}  ({rtype})")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    if args.report_type not in _GENERATORS:
        print(f"error: unknown report type {args.report_type!r}",
              file=sys.stderr)
        print("       run 'list' to see available types", file=sys.stderr)
        return 2

    rtype_canon, generator = _GENERATORS[args.report_type]
    now = int(time.time())
    start_ts = now - args.since
    end_ts = now if args.until == 0 else now - args.until
    if start_ts >= end_ts:
        print("error: --since must produce a time window before --until",
              file=sys.stderr)
        return 2

    output = Path(args.output) if args.output else Path(
        f"{args.tenant}-{rtype_canon}-{now}.pdf"
    )

    try:
        md = generator(
            tenant_id=args.tenant,
            start_ts=start_ts,
            end_ts=end_ts,
            output_path=output,
        )
    except Exception as exc:
        # Best-effort failure emit (chain may not exist either).
        try:
            _audit.report_failed(
                report_type=rtype_canon,
                tenant_id=args.tenant,
                reason="render-error",
                period_start_ts=start_ts,
                period_end_ts=end_ts,
            )
        except Exception:
            pass
        print(f"error: report generation failed: {exc}", file=sys.stderr)
        return 3

    # Emit audit (best-effort; never block report delivery).
    try:
        _audit.report_generated(
            report_type=rtype_canon,
            tenant_id=args.tenant,
            period_start_ts=start_ts,
            period_end_ts=end_ts,
            total_events=md.get("total_chain_events") or md.get("total_events") or 0,
            chain_intact=bool(md.get("chain_intact", True)),
            anchor_hash=md.get("anchor_hash") or "",
            page_count_estimate=md.get("page_count_estimate"),
        )
    except Exception as exc:
        if not args.quiet:
            print(f"warning: audit emit failed: {exc}", file=sys.stderr)

    if args.quiet:
        print(str(output))
    else:
        print(f"wrote {output}")
        print(f"  tenant      : {args.tenant}")
        print(f"  type        : {rtype_canon}")
        period_dur_d = (end_ts - start_ts) // 86400
        print(f"  period      : last {period_dur_d} day(s)")
        if md.get("chain_intact"):
            print(f"  chain       : intact ✓")
        else:
            print(f"  chain       : FAILED ✗")
        if md.get("anchor_hash"):
            print(f"  anchor      : {md['anchor_hash']}")
        print(f"  output size : {output.stat().st_size:,} bytes")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corvin-compliance-reports",
        description=f"Corvin compliance-report generator (v{__version__})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List available report types")
    p_list.set_defaults(func=_cmd_list)

    p_gen = sub.add_parser("generate", help="Generate a baseline compliance report")
    p_gen.add_argument(
        "report_type",
        help=f"One of: {', '.join(_GENERATORS)}",
    )
    p_gen.add_argument(
        "--tenant", default="_default",
        help="Tenant ID (default: _default)",
    )
    p_gen.add_argument(
        "--since", type=_parse_duration, default=30 * 86400,
        help="Time window start, before now (e.g. 7d, 30d, 90d). Default 30d.",
    )
    p_gen.add_argument(
        "--until", type=_parse_duration, default=0,
        help="Time window end, before now (default 0 = now)",
    )
    p_gen.add_argument(
        "--output", default=None,
        help="Output PDF path (default: <tenant>-<type>-<epoch>.pdf in CWD)",
    )
    p_gen.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output; only print the resulting path.",
    )
    p_gen.set_defaults(func=_cmd_generate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
