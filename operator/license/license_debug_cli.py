"""corvin-license-debug — operator-only OTA diagnostics (ADR-0154).

Runs all four MSLI shard checks (ADR-0154 M6) plus the tamper-response state and
prints a unified diagnosis. ADR-0154 § Consequences earmarks this as the
compensating control for OTA's deliberately-opaque failure modes: legitimate
license issues look like unrelated errors at runtime, so the operator needs one
place that says plainly what is wrong.

Operator-only: never wire this into an end-user-facing surface. It intentionally
reveals the OTA structure (which is documented across ADR-0092/0132/0153/0154).
"""
from __future__ import annotations

import argparse
import json as _json
import sys

_STATUS_GLYPH = {"OK": "✓", "WARN": "▲", "FAIL": "✗"}


def _imp(*names: str):
    """Import the first available module among *names* (prefers live instance)."""
    import sys as _sys
    from importlib import import_module

    for name in names:
        mod = _sys.modules.get(name)
        if mod is not None:
            return mod
    for name in names:
        try:
            return import_module(name)
        except Exception:  # noqa: BLE001
            continue
    return None


def _load_license_quietly() -> None:
    """Re-derive the active license from disk so shard checks see real state."""
    try:
        v = _imp("license.validator", "validator")
        if v is not None:
            v.load_license_from_env()
    except Exception:  # noqa: BLE001
        # Diagnostics must run even when activation fails — that is exactly the
        # case the operator is trying to debug.
        pass


def _gather() -> dict:
    sv = _imp("license.shard_verifier", "shard_verifier")
    report = sv.verify_shards()
    tr = _imp("license.tamper_response", "tamper_response")
    try:
        report["tamper"] = tr.status() if tr is not None else None
    except Exception:  # noqa: BLE001
        report["tamper"] = None
    return report


def _print_human(report: dict) -> None:
    agg = report["aggregate"]
    print(f"CorvinOS license diagnosis (ADR-0154 OTA)")
    print(f"  tier      : {report.get('tier', '?')}")
    print(f"  aggregate : {_STATUS_GLYPH.get(agg, '?')} {agg}")
    print("  shards    :")
    for s in report["shards"]:
        glyph = _STATUS_GLYPH.get(s["status"], "?")
        print(f"    {glyph} Shard {s['shard']} ({s['name']}): {s['status']} — {s['detail']}")
    tamper = report.get("tamper")
    if tamper:
        print(f"  tamper    : ✗ ENGAGED (reason={tamper.get('reason')}, count={tamper.get('count')})")
    else:
        print("  tamper    : ✓ none detected this process")


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corvin-license-debug",
        description="Operator-only OTA license diagnostics (ADR-0154 M6).",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--no-load",
        action="store_true",
        help="do not (re)load the license from disk before checking",
    )
    args = parser.parse_args(argv)

    if not args.no_load:
        _load_license_quietly()

    report = _gather()
    if args.json:
        print(_json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)

    # Exit non-zero on FAIL so operators can gate scripts on it; WARN stays 0
    # (free-tier divergence is informational, never a hard failure).
    return 1 if report["aggregate"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
