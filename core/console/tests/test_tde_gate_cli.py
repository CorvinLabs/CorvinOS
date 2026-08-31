"""E2E — `corvin tde gate` decision-gate consumer (TDE_ROBUST_USABLE_PLAN Step 2).

Drives the CLI through a REAL subprocess (argparse → dispatch → _cmd_gate), the
same boundary the `corvin` entry point uses, against a real measurement.jsonl on
a temp CORVIN_HOME (e2e-wiring-proof). Verifies the honesty + robustness gates:

  - no measured data           → INSUFFICIENT_DATA, --arm writes nothing
  - all bands win on measured   → --arm flips worker_engine to tde
  - a measured band LOSES       → --arm refuses (global TDE would harm it)

The last case is the load-bearing robustness invariant: arming is global today,
so a mixed verdict must NOT arm.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]


def _sample_dict(band: str, i: int, *, win: bool) -> dict:
    """One measurement.jsonl line, built DIRECTLY (no MeasurementSample import)
    so this test is immune to sys.modules pollution from neighbouring TDE tests
    that inject fake `tde` modules. Fields match the MeasurementSample
    constructor that load_from_log() reconstructs (it drops *_chars keys).

    win=True → clear TDE_WINS shape (90% token savings, tiny loss, big margin
    over tier); win=False → the tier beats TDE (negative savings) → a 'loser'."""
    if win:
        direct, tde, tier = 1000.0, 100.0, 900.0
        tde_loss, tier_loss = 0.01, 0.02
    else:
        direct, tde, tier = 1000.0, 1300.0, 900.0
        tde_loss, tier_loss = 0.03, 0.02
    return {
        "task_id": f"{band}-{i}", "task_band": band, "timestamp": float(i),
        "direct_tokens": direct, "direct_output": "x",
        "tier_tokens": tier, "tier_output": "x", "tier_loss": tier_loss,
        "tde_tokens": tde, "tde_output": "x", "tde_loss": tde_loss,
        "quality_judge_model": "haiku", "data_source": "measured",
    }


def _write_log(home: str, spec: dict[str, bool], n_per_band: int = 32) -> None:
    """spec: band -> win?. Writes n_per_band samples for each named band."""
    log_dir = Path(home) / "measurement-week"
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "measurement.jsonl", "w") as f:
        for band, win in spec.items():
            for i in range(n_per_band):
                f.write(json.dumps(_sample_dict(band, i, win=win),
                                   default=str) + "\n")


def _run_gate(home: str, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CORVIN_HOME"] = home
    env.pop("TDE_MEASUREMENT_ENABLED", None)
    code = ("import sys; sys.argv=['corvin','tde','gate',*%r]; "
            "from ops.launcher.corvin.cli import main; main()" % list(extra))
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO), env=env, capture_output=True, text=True, timeout=120,
    )


def _features_worker_engine(home: str, tenant: str = "_default") -> str | None:
    """Read worker_engine out of the tenant overlay the arm actuator writes."""
    for p in Path(home).rglob("features.json"):
        try:
            data = json.loads(p.read_text())
            if "worker_engine" in data:
                return data["worker_engine"]
        except Exception:  # noqa: BLE001
            pass
    return None


class TdeGateCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_data_reports_insufficient_and_does_not_arm(self) -> None:
        r = _run_gate(self.home, "--arm")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("decided on measured data : False", r.stdout)
        self.assertIn("NOT armed", r.stdout)
        self.assertIsNone(_features_worker_engine(self.home),
                          "no-data --arm must write nothing (fail-dark)")

    def test_all_bands_win_arms_globally(self) -> None:
        _write_log(self.home, {"trivial": True, "moderate": True, "complex": True})
        # Report first (no --arm) must not write.
        r0 = _run_gate(self.home)
        self.assertEqual(r0.returncode, 0, r0.stderr)
        self.assertIn("amplifier survives       : True", r0.stdout)
        self.assertIsNone(_features_worker_engine(self.home),
                          "a report without --arm must never write")
        # Now arm.
        r = _run_gate(self.home, "--arm")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ARMED", r.stdout)
        self.assertEqual(_features_worker_engine(self.home), "tde",
                         "all-win + --arm must flip worker_engine to tde")

    def test_mixed_verdict_refuses_to_arm(self) -> None:
        # complex wins, trivial loses → global TDE would harm trivial → no arm.
        _write_log(self.home, {"trivial": False, "complex": True})
        r = _run_gate(self.home, "--arm")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NOT armed", r.stdout)
        self.assertIn("lose", r.stdout.lower())
        self.assertIsNone(_features_worker_engine(self.home),
                          "a losing measured band must block the global arm")

    def test_json_output_is_valid(self) -> None:
        _write_log(self.home, {"complex": True})
        r = _run_gate(self.home, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertIn("per_band", payload)
        self.assertIn("arm_ok", payload)
        self.assertFalse(payload["armed"] is True)  # no --arm → not armed


if __name__ == "__main__":
    unittest.main()
