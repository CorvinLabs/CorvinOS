"""Tests for scheduler.py — cron parser, materialise, recurring + one-shot.

Run: python3 operator/bridges/shared/test_scheduler.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Sandbox the schedule file before importing the module.
_SANDBOX = tempfile.mkdtemp(prefix="sched_test_")
os.environ["XDG_CACHE_HOME"] = _SANDBOX

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scheduler  # noqa: E402


class CronParserTests(unittest.TestCase):
    def test_every_minute(self):
        cron = "* * * * *"
        # Any datetime should match.
        self.assertTrue(scheduler._matches_cron(cron, datetime(2026, 5, 6, 8, 0)))

    def test_specific_time(self):
        cron = "30 8 * * *"
        self.assertTrue(scheduler._matches_cron(cron, datetime(2026, 5, 6, 8, 30)))
        self.assertFalse(scheduler._matches_cron(cron, datetime(2026, 5, 6, 8, 31)))
        self.assertFalse(scheduler._matches_cron(cron, datetime(2026, 5, 6, 9, 30)))

    def test_weekday_range(self):
        # Mon-Fri at 09:00.
        cron = "0 9 * * 1-5"
        # 2026-05-04 is a Monday.
        self.assertTrue(scheduler._matches_cron(cron, datetime(2026, 5, 4, 9, 0)))
        # 2026-05-09 is a Saturday → should NOT match.
        self.assertFalse(scheduler._matches_cron(cron, datetime(2026, 5, 9, 9, 0)))

    def test_advance_cron_finds_future(self):
        cron = "0 8 * * *"  # daily 08:00
        base = datetime(2026, 5, 6, 7, 30).timestamp()
        nxt = scheduler._advance_cron(cron, base)
        nxt_dt = datetime.fromtimestamp(nxt)
        self.assertEqual(nxt_dt.hour, 8)
        self.assertEqual(nxt_dt.minute, 0)
        # Same day if before 08:00, otherwise next day.
        self.assertEqual(nxt_dt.date(), datetime(2026, 5, 6).date())


class ParseWhenTests(unittest.TestCase):
    def test_relative(self):
        ts, cron = scheduler.parse_when("in 5m")
        self.assertIsNone(cron)
        self.assertAlmostEqual(ts, time.time() + 300, delta=2)

    def test_iso(self):
        ts, cron = scheduler.parse_when("2030-01-01T12:00")
        self.assertIsNone(cron)
        # Local-time interpretation; just sanity-check it's in the future.
        self.assertGreater(ts, time.time())

    def test_cron_recurring(self):
        ts, cron = scheduler.parse_when("0 8 * * 1-5")
        self.assertEqual(cron, "0 8 * * 1-5")
        self.assertGreater(ts, time.time())

    def test_garbage_returns_none(self):
        ts, cron = scheduler.parse_when("not a real time")
        self.assertIsNone(ts)
        self.assertIsNone(cron)


class TaskLifecycleTests(unittest.TestCase):
    def setUp(self):
        # Fresh schedule file per test.
        if scheduler.SCHEDULE_FILE.exists():
            scheduler.SCHEDULE_FILE.unlink()

    def test_add_list_remove(self):
        item = scheduler.add_task(
            channel="discord", chat_id="ch1", sender="u1",
            text="hello", when="in 60s",
        )
        self.assertEqual(item["channel"], "discord")
        self.assertIsNone(item["cron"])
        listed = scheduler.list_tasks()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], item["id"])
        self.assertTrue(scheduler.remove_task(item["id"]))
        self.assertEqual(len(scheduler.list_tasks()), 0)

    def test_remove_nonexistent(self):
        self.assertFalse(scheduler.remove_task("nope"))

    def test_filter_by_channel(self):
        scheduler.add_task(channel="a", chat_id="x", sender="x", text="t", when="in 1h")
        scheduler.add_task(channel="b", chat_id="y", sender="y", text="t", when="in 1h")
        self.assertEqual(len(scheduler.list_tasks(channel="a")), 1)
        self.assertEqual(len(scheduler.list_tasks(channel="b")), 1)


class MaterialiseTests(unittest.TestCase):
    def setUp(self):
        if scheduler.SCHEDULE_FILE.exists():
            scheduler.SCHEDULE_FILE.unlink()
        self.inbox = Path(tempfile.mkdtemp(prefix="sched_inbox_"))

    def test_one_shot_fires_and_disappears(self):
        # Schedule for 1 second ago → due immediately.
        item = scheduler.add_task(
            channel="telegram", chat_id="42", sender="42",
            text="ping", when="in 0s",
        )
        # Force its next_run into the past so we don't have to sleep.
        items = scheduler.load()
        items[0]["next_run"] = time.time() - 5
        scheduler.save(items)

        fired = scheduler.materialize_due(self.inbox)
        self.assertEqual(len(fired), 1)
        # Task is gone (one-shot).
        self.assertEqual(len(scheduler.list_tasks()), 0)
        # Inbox file exists with the right shape.
        files = list(self.inbox.glob("*.json"))
        self.assertEqual(len(files), 1)
        env = json.loads(files[0].read_text())
        self.assertEqual(env["channel"], "telegram")
        self.assertEqual(env["text"], "ping")
        self.assertTrue(env["_scheduled"])

    def test_recurring_advances(self):
        # Cron with a fired-due slot, expect it to advance.
        item = scheduler.add_task(
            channel="discord", chat_id="ch", sender="u",
            text="standup", when="* * * * *",  # every minute
        )
        # Backdate next_run.
        items = scheduler.load()
        items[0]["next_run"] = time.time() - 5
        scheduler.save(items)

        fired = scheduler.materialize_due(self.inbox)
        self.assertEqual(len(fired), 1)
        # Recurring → still in schedule, with next_run in the future.
        kept = scheduler.list_tasks()
        self.assertEqual(len(kept), 1)
        self.assertGreater(kept[0]["next_run"], time.time())

    def test_dedupe_within_30s(self):
        # Add a one-shot, fire it, then immediately try to fire again.
        scheduler.add_task(channel="a", chat_id="c", sender="s",
                           text="x", when="in 0s")
        items = scheduler.load()
        items[0]["next_run"] = time.time() - 1
        items[0]["last_fire"] = time.time() - 5
        scheduler.save(items)
        # The task is "due" by next_run but too recently fired (last_fire 5s ago)
        # → de-dupe should skip it.
        fired = scheduler.materialize_due(self.inbox)
        self.assertEqual(len(fired), 0)


class WorkflowOutboxTargetTests(unittest.TestCase):
    """Regression: scheduled workflow reports must land in the SHARED outbox
    the daemons poll, not an orphan per-channel dir."""

    def test_report_lands_in_shared_outbox_not_per_channel(self):
        with tempfile.TemporaryDirectory() as td:
            shared = Path(td) / "shared_outbox"
            per_channel = Path(td) / "discord" / "outbox"
            per_channel.mkdir(parents=True, exist_ok=True)
            os.environ["ADAPTER_OUTBOX"] = str(shared)
            try:
                item = {
                    "id": "wf1", "channel": "discord", "chat_id": "123456",
                    "workflow_name": "nonexistent_wf", "workflow_inputs": {},
                }
                # The workflow subprocess will fail (unknown workflow), but the
                # function still writes the report envelope with an error body.
                ok = scheduler._run_workflow_to_outbox(item, now=1000.0)
                self.assertTrue(ok)
                reports = list(shared.glob("sched_wf_wf1_*.json"))
                self.assertEqual(len(reports), 1,
                                 f"report not in shared outbox: {list(shared.iterdir())}")
                env = json.loads(reports[0].read_text())
                self.assertEqual(env["channel"], "discord")
                self.assertEqual(env["chat_id"], "123456")
                # EU AI Act Art. 50 §4 — autonomous AI notification must be marked.
                self.assertTrue(env.get("_final"))
                self.assertTrue(env.get("provenance", {}).get("ai_generated"))
                self.assertEqual(env["provenance"]["generator_id"], "corvin_os")
                # Nothing must land in the orphan per-channel dir.
                self.assertEqual(list(per_channel.glob("*.json")), [])
            finally:
                os.environ.pop("ADAPTER_OUTBOX", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
