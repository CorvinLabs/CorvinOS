#!/usr/bin/env python3
"""Re-print the token-savings receipt from a tenant's audit chain (T-0246).

The figure README.md and docs/overview/token-savings.md quote ("-60 % vs Opus 5: $344 actual vs
$863, 222 priced turns, 2026-09-28 -> 10-04") was read from the maintainer's chain by hand on
2026-10-04. This script reproduces that reading from any chain, for any window and reference model,
with the SAME fold the console uses: ``core.learning.model_selection_learner.compute_cost_efficiency``
(same ``os_turn.started``/``os_turn.completed`` join, same rate card, same cache multipliers, same
"unpriced turn is counted, never estimated" rule). Nothing is copied from it.

    scripts/token_savings_receipt.py --since 2026-09-28 --reference claude-opus-5
    scripts/token_savings_receipt.py --since 2026-09-28 --until 2026-10-05 --tenant _default --json
    scripts/token_savings_receipt.py --chain /path/to/audit.jsonl --since 2026-09-28

* The chain is the tenant's canonical one (``core.paths.tenant.tenant_audit_chain``, never a
  hand-composed path) unless ``--chain`` names a file. It is opened READ-ONLY; the script writes nothing.
* ``--since`` / ``--until`` are ISO dates or datetimes (a bare date is UTC midnight; ``since <= ts <
  until``). With ``--since`` omitted the tenant's counting epoch (``usage_epoch``, ADR-0760) applies,
  exactly as in the console; an explicit ``--since`` replaces it.
* The reference model must be in the rate card (``claude-opus-5`` by default; ``claude-opus-5-5``,
  ``claude-sonnet-5`` ... are accepted). An unknown id is refused - a guessed reference is a fabricated saving.
* The saving is ``1 - actual / reference`` of the summed dollars, NOT an average of percentages, and it
  is printed unclamped (a window of Fable turns shows a negative saving against Opus 5).
* Delegated worker runs (ACS completions, gateway worker spans) are a second row, read from the
  tenant's chain files only; with ``--chain`` that row is omitted.

Exit codes: 0 printed; 2 the chain cannot be read, or an argument is invalid.
Python 3.11; safe under ``python -I`` (the repo root is put on ``sys.path`` from this file's location).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_DEFAULT_REFERENCE = "claude-opus-5"


def _parse_when(raw: Optional[str], flag: str) -> Optional[datetime]:
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"error: {flag} {raw!r} is not an ISO date or datetime "
                         "(e.g. 2026-09-28 or 2026-09-28T12:00:00+00:00)")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def _chain_path(args: argparse.Namespace) -> Path:
    if args.chain:
        return Path(args.chain).expanduser()
    from core.paths.tenant import tenant_audit_chain  # the one resolver every chain reader uses

    try:
        return tenant_audit_chain(args.tenant)
    except ValueError as e:
        raise SystemExit(f"error: --tenant {args.tenant!r}: {e}")


def _check_readable(path: Path) -> None:
    """Exit 2 unless *path* is a regular file this process can open for reading. Read-only by construction:
    the only open is ``rb``."""
    if not path.is_file():
        raise SystemExit(f"error: audit chain {path} is not a readable file")
    try:
        with path.open("rb") as fh:
            fh.read(1)
    except OSError as e:
        raise SystemExit(f"error: audit chain {path} cannot be read: {e}")


def _series(turns: int, priced: int, actual: float, reference: float, mix: Dict[str, int]) -> Dict[str, Any]:
    saving = round((1 - actual / reference) * 100, 1) if reference > 0 else None
    return {"turns": turns, "priced_turns": priced, "actual_usd": round(actual, 4),
            "reference_usd": round(reference, 4), "saving_percent": saving, "model_mix": dict(mix)}


def build_receipt(args: argparse.Namespace) -> Dict[str, Any]:
    from core.learning import model_selection_learner as msl

    chain = _chain_path(args)
    _check_readable(chain)
    since = _parse_when(args.since, "--since")
    until = _parse_when(args.until, "--until")
    if since and until and until <= since:
        raise SystemExit("error: --until must be after --since")
    if msl.model_price_per_1k(args.reference) is None:
        raise SystemExit(f"error: reference model {args.reference!r} is not in the rate card "
                         f"({', '.join(sorted(msl._MODEL_PRICING_USD_PER_1K))})")
    try:
        result = msl.compute_cost_efficiency(
            args.tenant, chain_path=chain,
            since=since.timestamp() if since else None,
            until=until.timestamp() if until else None,
            reference=args.reference,
        )
    except ValueError as e:
        raise SystemExit(f"error: {e}")

    if since is not None:
        window = {"since": since.isoformat(), "until": until.isoformat() if until else None, "source": "cli"}
    else:
        from core.console.corvin_console import usage_epoch  # the console's window, read from one place

        epoch = usage_epoch.epoch_ts(args.tenant)
        window = {"since": _iso(epoch), "until": until.isoformat() if until else None,
                  "source": "usage_epoch" if epoch else "all"}

    os_turns = _series(sum(d.total_turns for d in result.daily), sum(d.counted_turns for d in result.daily),
                       result.total_actual_usd, result.total_baseline_usd, result.model_mix)
    workers = None
    if not args.chain:
        workers = _series(sum(d.total_turns for d in result.acs_daily), sum(d.counted_turns for d in result.acs_daily),
                          result.acs_total_actual_usd, result.acs_total_baseline_usd, result.acs_model_mix)
    return {"chain": str(chain), "tenant": args.tenant, "reference_model": result.reference_model,
            "window": window, "os_turns": os_turns, "worker_runs": workers}


def _fmt_pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.1f} %"


def render_table(receipt: Dict[str, Any]) -> str:
    w = receipt["window"]
    lines = [
        f"Token-savings receipt - chain {receipt['chain']}",
        f"window: {w['since'] or 'chain start'} -> {w['until'] or 'now'} (from {w['source']})"
        f" - reference model {receipt['reference_model']}",
        "",
        f"{'series':<22}{'turns':>8}{'priced':>8}{'actual':>12}{'reference':>12}{'saving':>10}",
    ]
    rows = [("OS turns", receipt["os_turns"])]
    if receipt["worker_runs"] is not None:
        rows.append(("delegated worker runs", receipt["worker_runs"]))
    for name, s in rows:
        lines.append(f"{name:<22}{s['turns']:>8}{s['priced_turns']:>8}{'$' + format(s['actual_usd'], '.2f'):>12}"
                     f"{'$' + format(s['reference_usd'], '.2f'):>12}{_fmt_pct(s['saving_percent']):>10}")
    mix = receipt["os_turns"]["model_mix"]
    if mix:
        lines += ["", "priced OS turns by model:"]
        for model, n in sorted(mix.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {model:<40}{n:>6}")
    lines += ["", "saving = 1 - actual / reference, over the summed dollars of the priced turns; an unpriced turn",
              "(unknown model or no token data) is counted in 'turns' and never estimated."]
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="Re-print the token-savings receipt from the audit chain (read-only).")
    p.add_argument("--since", help="ISO date/datetime; the window start (UTC). Omitted: the tenant's counting epoch.")
    p.add_argument("--until", help="ISO date/datetime; the window end, exclusive (UTC).")
    p.add_argument("--reference", default=_DEFAULT_REFERENCE, help=f"reference model id (default {_DEFAULT_REFERENCE})")
    p.add_argument("--tenant", default="_default", help="tenant id (default _default)")
    p.add_argument("--chain", help="a chain file to read instead of the tenant's canonical chain")
    p.add_argument("--json", action="store_true", help="print the receipt as JSON")
    args = p.parse_args(argv)
    try:
        receipt = build_receipt(args)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 2
        raise
    print(json.dumps(receipt, indent=2, sort_keys=True) if args.json else render_table(receipt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
