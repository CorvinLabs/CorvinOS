"""End-to-end CLI tests for user_style.

Drives ``python operator/bridges/shared/user_style.py <subcmd>``
via subprocess against a tempdir sandbox. Verifies:

  - status returns valid JSON with all expected keys
  - sweep --no-judge processes a synthetic chain and reports counts
  - sweep emits audit events into the chain (correct event_types)
  - list-live / list-shadow / list-cooldown render JSON arrays
  - reset-cooldown removes entries
  - exit codes are correct for unknown clusters / args
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_USER_STYLE = _HERE / "user_style.py"
_FORGE_TOP = _HERE.parent.parent / "forge"


def _run(args: list[str], home: Path, *, expect_ok: bool = True) -> dict:
    """Run the CLI, return parsed stdout (JSON) + exit code."""
    cmd = [sys.executable, str(_USER_STYLE), "--corvin-home", str(home)] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if expect_ok and r.returncode != 0:
        raise AssertionError(
            f"unexpected exit={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
        )
    try:
        body = json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        body = None
    return {"code": r.returncode, "body": body,
            "stderr": r.stderr, "stdout": r.stdout}


def _emit_outcome(audit_path: Path, *, signal: str, skill: str,
                  prev_run_id: str, ts: float) -> None:
    """Use security_events.write_event to seed the chain."""
    sys.path.insert(0, str(_FORGE_TOP))
    from forge.security_events import write_event  # noqa: E402
    write_event(
        audit_path, "skill.outcome_graded",
        tool="adapter", ts=ts,
        details={
            "signal": signal, "prev_run_id": prev_run_id,
            "skills": [skill], "score": 0.1,
        },
    )


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="us-cli-"))
        self.audit = self.tmp / "global" / "forge" / "audit.jsonl"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class StatusTests(_Base):
    def test_status_on_empty_home(self) -> None:
        out = _run(["status"], self.tmp)
        self.assertEqual(out["code"], 0)
        self.assertEqual(set(out["body"].keys()),
                         {"live", "shadow", "cooldown", "hard_cap"})
        self.assertEqual(out["body"]["live"], 0)
        self.assertEqual(out["body"]["shadow"], 0)
        self.assertEqual(out["body"]["cooldown"], 0)


class SweepTests(_Base):
    def test_sweep_with_no_signals_is_no_op(self) -> None:
        out = _run(["sweep", "--no-judge"], self.tmp)
        self.assertEqual(out["code"], 0)
        self.assertEqual(out["body"]["candidates_proposed"], 0)
        self.assertEqual(out["body"]["candidates_promoted"], 0)
        self.assertEqual(out["body"]["bullets_rolled_back"], 0)

    def test_sweep_with_negative_signals_proposes_candidate(self) -> None:
        skill = "skill_CLI"
        ts = time.time()
        for i in range(8):
            _emit_outcome(self.audit, signal="rejection", skill=skill,
                          prev_run_id=f"r{i}", ts=ts - i)
        out = _run(["sweep", "--no-judge"], self.tmp)
        self.assertEqual(out["code"], 0, msg=out["stderr"])
        self.assertEqual(out["body"]["candidates_proposed"], 1)
        self.assertGreaterEqual(out["body"]["clusters"], 1)

        # status now reports 1 shadow
        st = _run(["status"], self.tmp)
        self.assertEqual(st["body"]["shadow"], 1)

    def test_sweep_emits_audit_event_for_proposal(self) -> None:
        skill = "skill_AUD"
        ts = time.time()
        for i in range(8):
            _emit_outcome(self.audit, signal="rejection", skill=skill,
                          prev_run_id=f"a{i}", ts=ts - i)
        _run(["sweep", "--no-judge"], self.tmp)

        # Audit event must be present
        events = []
        with self.audit.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("event_type", "").startswith("user_style."):
                    events.append(rec)
        types = [e["event_type"] for e in events]
        self.assertIn("user_style.candidate_proposed", types)


class ListTests(_Base):
    def test_list_shadow_after_propose(self) -> None:
        skill = "skill_LSH"
        ts = time.time()
        for i in range(8):
            _emit_outcome(self.audit, signal="rejection", skill=skill,
                          prev_run_id=f"l{i}", ts=ts - i)
        _run(["sweep", "--no-judge"], self.tmp)

        out = _run(["list-shadow"], self.tmp)
        self.assertEqual(out["code"], 0)
        self.assertIsInstance(out["body"], list)
        self.assertEqual(len(out["body"]), 1)
        self.assertEqual(out["body"][0]["skill_name"], skill)
        self.assertIn("shadow_age_h", out["body"][0])

    def test_list_live_starts_empty(self) -> None:
        out = _run(["list-live"], self.tmp)
        self.assertEqual(out["body"], [])

    def test_list_cooldown_starts_empty(self) -> None:
        out = _run(["list-cooldown"], self.tmp)
        self.assertEqual(out["body"], [])


class CooldownTests(_Base):
    def test_reset_cooldown_round_trip(self) -> None:
        # First, get a cluster into cooldown via judge=overfit.
        skill = "skill_CD"
        ts = time.time()
        for i in range(8):
            _emit_outcome(self.audit, signal="rejection", skill=skill,
                          prev_run_id=f"c{i}", ts=ts - i)

        # Use the python module directly to register cooldown (since the
        # CLI's --no-judge always returns FAITHFUL — to test cooldown we
        # need OVERFIT). Tap into the API.
        sys.path.insert(0, str(_HERE))
        sys.path.insert(0, str(_FORGE_TOP))
        # Re-import in isolation to avoid stale module state.
        for mod in ("user_style",):
            sys.modules.pop(mod, None)
        import user_style as us
        us.run_daily_sweep(
            audit_path=self.audit, corvin_home=self.tmp,
            judge_fn=lambda c, d: False,  # OVERFIT
        )
        cluster_id = us._cluster_id(skill)

        # CLI sees cooldown
        out = _run(["list-cooldown"], self.tmp)
        ids = {row["cluster_id"] for row in out["body"]}
        self.assertIn(cluster_id, ids)

        # Reset removes it
        out2 = _run(["reset-cooldown", cluster_id], self.tmp)
        self.assertEqual(out2["code"], 0)
        self.assertTrue(out2["body"]["ok"])

        # And the cooldown is gone
        out3 = _run(["list-cooldown"], self.tmp)
        ids3 = {row["cluster_id"] for row in out3["body"]}
        self.assertNotIn(cluster_id, ids3)

    def test_reset_unknown_cluster_returns_1(self) -> None:
        out = _run(["reset-cooldown", "cl_does_not_exist"],
                   self.tmp, expect_ok=False)
        self.assertEqual(out["code"], 1)
        self.assertFalse(out["body"]["ok"])


class ArgvTests(_Base):
    def test_no_subcommand_returns_2(self) -> None:
        out = _run([], self.tmp, expect_ok=False)
        self.assertEqual(out["code"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
