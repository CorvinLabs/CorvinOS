"""corvin tde — Tiered Delegation Engine operations (ADR-0222).

`corvin tde gate` is the LIVE consumer of the ADR-0222 decision gate — the
actuator the gate was built for but deliberately left unwired until a real
consumer existed (WIRING.yaml: decision_gate `status: deferred`). It:

  1. loads the measurement-week log (`<CORVIN_HOME>/measurement-week/measurement.jsonl`),
  2. aggregates measured evidence per complexity band (trivial/moderate/complex),
  3. runs the honesty-gated verdict (`evaluate_tde_verdict`),
  4. prints it, and
  5. with `--arm`, acts on it — but ONLY globally-safely.

Arming is GLOBAL because there is no per-band routing today (the one operator
switch is `worker_engine` native/acs/tde). So `--arm` flips `worker_engine` to
`tde` IFF the verdict was decided on MEASURED data AND TDE wins at least one
band AND NO measured band loses (NO_SAVINGS / TIER_WINS). Otherwise it fails
DARK — reports, writes nothing — honouring the ADR-0222 honesty invariant.

Grenze (documented, not a bug): a verdict where TDE wins `complex` but loses
`trivial` will NOT arm, because global TDE would then harm the losing bands.
Per-band arming (TDE only for the winning band) is a larger routing change
tracked as its own step (needs an armed-band store + a `band` parameter through
`delegation_policy.worker_engine_target`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Repo root: ops/launcher/corvin/tde_cmd.py -> parents[3]
_REPO = Path(__file__).resolve().parents[3]


def add_parser(subparsers) -> None:
    tde_parser = subparsers.add_parser(
        "tde",
        help="Tiered Delegation Engine operations (ADR-0222 measurement gate)",
    )
    tde_sub = tde_parser.add_subparsers(dest="tde_cmd", metavar="subcommand")

    gate = tde_sub.add_parser(
        "gate",
        help="Evaluate the ADR-0222 decision gate against the measurement log; "
             "with --arm, safely flip worker_engine to tde on a measured win.",
    )
    gate.add_argument(
        "--tenant", default="_default",
        help="Tenant whose worker_engine is armed with --arm (default: _default).",
    )
    gate.add_argument(
        "--arm", action="store_true",
        help="Act on the verdict: flip worker_engine to tde IFF the gate decided "
             "on MEASURED data, TDE wins >=1 band, and NO measured band loses. "
             "Without --arm this is a read-only report.",
    )
    gate.add_argument(
        "--json", action="store_true",
        help="Emit the verdict as JSON instead of a human-readable report.",
    )


def _load_tde_modules():
    """Put the orchestration tree on sys.path and import the gate + recorder.

    Returns (MeasurementRecorder, evaluate_tde_verdict) or raises ImportError
    with a clear message on a wheel install without the orchestration tree."""
    orch = _REPO / "operator" / "orchestration"
    if orch.is_dir() and str(orch) not in sys.path:
        sys.path.insert(0, str(orch))
    from tde.tde_measurement import MeasurementRecorder  # noqa: PLC0415
    from tde.decision_gate import evaluate_tde_verdict  # noqa: PLC0415
    return MeasurementRecorder, evaluate_tde_verdict


def _load_feature_flags():
    console = _REPO / "core" / "console"
    if console.is_dir() and str(console) not in sys.path:
        sys.path.insert(0, str(console))
    from corvin_console import feature_flags  # noqa: PLC0415
    return feature_flags


def _arm_decision(result: dict) -> tuple[bool, str]:
    """Decide whether a GLOBAL arm is safe, and why/why not.

    Safe iff: decided on measured data, TDE survives (>=1 measured win), and no
    measured band clearly loses (NO_SAVINGS / TIER_WINS). An INSUFFICIENT_DATA
    measured band is tolerated (no signal yet, but no clear loss either)."""
    if not result.get("decided_on_measured_data"):
        return False, ("no verdict on MEASURED data yet — run a measurement week "
                       "(TDE_MEASUREMENT_ENABLED=1) until each band has samples")
    if not result.get("amplifier_survives"):
        return False, "no band wins on measured data (TDE shows no net advantage)"
    losers = [v.band for v in result["per_band"]
              if v.data_source == "measured"
              and v.verdict in ("NO_SAVINGS", "TIER_WINS")]
    if losers:
        return False, (f"global arming is unsafe: measured band(s) {losers} lose "
                       f"— global TDE would harm them (per-band arming needed)")
    return True, (f"TDE wins {result['winning_bands']} on measured data and no "
                  f"measured band loses")


def _print_report(result: dict, log_path: str, n_samples: int,
                  n_by_band: dict, arm_ok: bool, arm_reason: str,
                  armed: bool | None) -> None:
    print("TDE Decision Gate — ADR-0222 measurement verdict")
    print(f"  Log: {log_path}")
    print(f"  Samples loaded: {n_samples}")
    print("  Bands:")
    for v in result["per_band"]:
        print(f"    {v.band:<9} {v.verdict:<18} "
              f"({v.data_source}, n={n_by_band.get(v.band, 0)})  "
              f"tde_net={v.tde_net_savings:+.3f} tier_net={v.tier_net_savings:+.3f}")
        if v.reason:
            print(f"              ↳ {v.reason}")
    print("  —")
    print(f"  decided on measured data : {result['decided_on_measured_data']}")
    print(f"  amplifier survives       : {result['amplifier_survives']}  "
          f"(>=1 measured TDE win)")
    print(f"  measured winning bands   : {result['winning_bands']}")
    if result["predicted_winning_bands"]:
        print(f"  predicted (assumptions)  : {result['predicted_winning_bands']} "
              f"(NOT actionable — predictions, not measured)")
    print("  —")
    if armed is None:
        verb = "WOULD arm" if arm_ok else "would NOT arm"
        print(f"  arm decision : {verb} — {arm_reason}")
        if arm_ok:
            print("               (re-run with --arm to apply)")
    elif armed:
        print(f"  ARMED ✓ : worker_engine set to 'tde' — {arm_reason}")
    else:
        print(f"  NOT armed ✗ : {arm_reason}")


def _cmd_gate(args: argparse.Namespace) -> int:
    try:
        MeasurementRecorder, evaluate_tde_verdict = _load_tde_modules()
    except ImportError as e:
        print(f"corvin tde gate: TDE orchestration tree unavailable ({e}).\n"
              "This install has no measurement/gate modules — nothing to evaluate.",
              file=sys.stderr)
        return 1

    # Fresh recorder bound to this CORVIN_HOME; load the log (read-only).
    MeasurementRecorder.reset_instance()
    recorder = MeasurementRecorder.get_instance()
    log_path = recorder.log_path
    recorder.load_from_log()
    n_samples = len(recorder.samples)

    evidence = recorder.get_aggregated_evidence()
    n_by_band = {ev.band: ev.n_measured for ev in evidence}
    result = evaluate_tde_verdict(evidence)
    arm_ok, arm_reason = _arm_decision(result)

    armed: bool | None = None
    if args.arm:
        if arm_ok:
            try:
                ff = _load_feature_flags()
                ff.set_worker_engine_mode("tde", args.tenant)
                armed = True
            except Exception as e:  # noqa: BLE001
                print(f"corvin tde gate: arm failed while writing worker_engine "
                      f"({e}).", file=sys.stderr)
                return 1
        else:
            armed = False

    if args.json:
        # BandEvidence/TdeVerdict are dataclasses — render the actionable subset.
        out = {
            "log_path": log_path,
            "samples": n_samples,
            "decided_on_measured_data": result["decided_on_measured_data"],
            "amplifier_survives": result["amplifier_survives"],
            "winning_bands": result["winning_bands"],
            "predicted_winning_bands": result["predicted_winning_bands"],
            "per_band": [
                {"band": v.band, "verdict": v.verdict,
                 "data_source": v.data_source,
                 "tde_net_savings": v.tde_net_savings,
                 "tier_net_savings": v.tier_net_savings,
                 "reason": v.reason}
                for v in result["per_band"]
            ],
            "arm_ok": arm_ok,
            "arm_reason": arm_reason,
            "armed": armed,
            "tenant": args.tenant,
        }
        print(json.dumps(out, indent=2, default=str))
    else:
        _print_report(result, log_path, n_samples, n_by_band,
                      arm_ok, arm_reason, armed)

    return 0


def dispatch(args: argparse.Namespace) -> int:
    if args.tde_cmd == "gate":
        return _cmd_gate(args)
    # No subcommand — argparse prints help via the caller.
    return 2
