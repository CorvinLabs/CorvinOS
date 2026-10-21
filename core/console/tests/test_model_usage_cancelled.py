"""The Models panel keeps console-cancelled turns apart from failed ones (model_usage, round 2, 2026-10-21).

exit_code -1 on ``os_turn.completed`` / ``status: cancelled`` on the OS span is the console's own
cut-off (operator stop, host shutdown, vanished consumer) — measured on a fresh system: four restarts
and one stop read as 5 "failed" of 36 turns, 86 % success, for an engine that never failed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / p))


def _row(ts, event_type, **details):
    return json.dumps({"ts": ts, "event_type": event_type, "details": details, "tenant_id": "_default"})


class CancelledBucketTests(unittest.TestCase):
    def test_cut_off_turns_are_cancelled_not_failed(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "corvin"
            chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
            chain.parent.mkdir(parents=True)
            now = time.time()
            lines = []
            # three OS turns: one ok, one engine error (rc 1), one cut off (rc -1)
            for i, (rc, status) in enumerate(((0, "ok"), (1, "error"), (-1, "cancelled"))):
                span, turn = f"span-{i}", f"turn-{i}"
                lines.append(_row(now + i, "engine.span.start", span_id=span, turn_id=turn, role="os",
                                  model_id="claude-haiku-5-5", engine_id="claude_code"))
                lines.append(_row(now + i + 0.5, "os_turn.completed", turn_id=turn, model="claude-haiku-5-5",
                                  exit_code=rc, input_tokens=1, output_tokens=2,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))
                lines.append(_row(now + i + 0.6, "engine.span.end", span_id=span, turn_id=turn, role="os",
                                  model_id="claude-haiku-5-5", status=status, duration_ms=10))
            chain.write_text("\n".join(lines) + "\n", encoding="utf-8")
            prev = os.environ.get("CORVIN_HOME")
            os.environ["CORVIN_HOME"] = str(home)
            try:
                from corvin_console import model_usage as mu
                out = mu.model_usage("_default")
            finally:
                if prev is None:
                    os.environ.pop("CORVIN_HOME", None)
                else:
                    os.environ["CORVIN_HOME"] = prev
        totals = out["totals"]
        self.assertEqual((totals["turns"], totals["ok"], totals["failed"], totals["cancelled"], totals["unfinished"]),
                         (3, 1, 1, 1, 0), totals)
        row = [m for m in out["models"] if m["model_id"] == "claude-haiku-5-5"][0]
        self.assertEqual((row["ok"], row["failed"], row["cancelled"]), (1, 1, 1), row)
        # the success rate is over verdicts the ENGINE gave: ok vs failed, never the cut-offs
        self.assertEqual(row["success_pct"], 50.0, row)
        role = [r for r in out["roles"] if r["role"] == "os"][0]
        self.assertEqual((role["ok"], role["failed"], role["cancelled"], role["success_pct"]), (1, 1, 1, 50.0), role)

    def test_a_bare_os_turn_completed_with_rc_minus_one_is_cancelled(self):
        """No span (older records): the exit code alone classifies the turn."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "corvin"
            chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
            chain.parent.mkdir(parents=True)
            now = time.time()
            chain.write_text(_row(now, "os_turn.started", turn_id="t1", model="claude-haiku-5-5") + "\n"
                             + _row(now + 1, "os_turn.completed", turn_id="t1", model="claude-haiku-5-5",
                                    exit_code=-1, input_tokens=0, output_tokens=0) + "\n", encoding="utf-8")
            prev = os.environ.get("CORVIN_HOME")
            os.environ["CORVIN_HOME"] = str(home)
            try:
                from corvin_console import model_usage as mu
                out = mu.model_usage("_default")
            finally:
                if prev is None:
                    os.environ.pop("CORVIN_HOME", None)
                else:
                    os.environ["CORVIN_HOME"] = prev
        self.assertEqual((out["totals"]["cancelled"], out["totals"]["failed"]), (1, 0), out["totals"])


if __name__ == "__main__":
    unittest.main()
