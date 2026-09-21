"""Core learning loops are discovered from real stores, and never invented.

``core.learning.loop_discovery`` is what makes the Learning Loops panel show the
loops CorvinOS runs itself. The rules it must not break:

* a health score exists only where something measured one — a loop with
  executions but no outcome and no grade reports ``None``, not 0.5;
* a status that asserts an age is only produced where an age is known;
* a day with no recorded outcome is a gap in the trend, not a zero;
* only allowlisted scalar fields of an event signal are ever rendered.

Runnable with pytest or
``python3 -m unittest tests.unit.test_loop_discovery``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _event(skill_id: str, when: datetime, event_type: str = "skill_executed", **signal) -> dict:
    return {
        "event_id": f"{skill_id}-{when.timestamp()}-{event_type}",
        "event_type": event_type,
        "skill_id": skill_id,
        "tenant_id": "_default",
        "timestamp": _iso(when),
        "version": "1.0",
        "signal": signal or {"status": "success"},
    }


class _Fixture:
    """A throwaway CORVIN_HOME holding a tenant's learning events."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.events_dir = self.root / "tenants" / "_default" / "learning" / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self._prev = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(self.root)

    def write(self, events: list[dict]) -> None:
        by_day: dict[str, list[dict]] = {}
        for ev in events:
            by_day.setdefault(ev["timestamp"][:10], []).append(ev)
        for day, rows in by_day.items():
            with (self.events_dir / f"{day}.jsonl").open("a") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")

    def close(self) -> None:
        if self._prev is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev
        self._tmp.cleanup()


class TestCoreLoopDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()
        self.addCleanup(self.fx.close)
        self.now = datetime.now(timezone.utc)

    def _discover(self):
        from core.learning import loop_discovery

        return loop_discovery.discover_core_loops("_default")

    def test_a_loop_with_no_outcome_has_no_health_score(self) -> None:
        """Executions are not outcomes. A neutral 0.5 here would be a fabricated
        measurement in the same field a real one uses."""
        self.fx.write([_event("os.only_executes", self.now - timedelta(hours=2)) for _ in range(40)])

        loop = next(l for l in self._discover().loops if l.owner == "os.only_executes")
        self.assertIsNone(loop.health_score)
        self.assertEqual(loop.health_basis, "")
        self.assertEqual(loop.event_count_total, 40)

    def test_health_score_is_the_measured_outcome_rate(self) -> None:
        recent = self.now - timedelta(hours=1)
        events = [_event("os.router", recent, "outcome", success=True) for _ in range(3)]
        events.append(_event("os.router", recent, "outcome", success=False))
        self.fx.write(events)

        loop = next(l for l in self._discover().loops if l.owner == "os.router")
        self.assertAlmostEqual(loop.health_score, 0.75)
        self.assertIn("3 of 4", loop.health_basis)
        self.assertEqual(loop.outcome_total, 4)
        self.assertEqual(loop.outcome_success, 3)

    def test_status_follows_real_age(self) -> None:
        self.fx.write([
            _event("os.fresh", self.now - timedelta(hours=1)),
            _event("os.quiet", self.now - timedelta(days=3)),
            _event("os.gone", self.now - timedelta(days=30)),
        ])
        by_owner = {l.owner: l for l in self._discover().loops}
        self.assertEqual(by_owner["os.fresh"].status, "active")
        self.assertEqual(by_owner["os.quiet"].status, "dormant")
        self.assertEqual(by_owner["os.gone"].status, "stale")

    def test_a_measured_failure_is_degrading_regardless_of_age(self) -> None:
        old = self.now - timedelta(days=40)
        self.fx.write([
            _event("os.broken", old, "outcome", success=False),
            _event("os.broken", old, "outcome", success=False),
        ])
        loop = next(l for l in self._discover().loops if l.owner == "os.broken")
        self.assertEqual(loop.status, "degrading")

    def test_seven_day_count_excludes_older_events(self) -> None:
        self.fx.write(
            [_event("os.mixed", self.now - timedelta(days=1)) for _ in range(2)]
            + [_event("os.mixed", self.now - timedelta(days=20)) for _ in range(5)]
        )
        loop = next(l for l in self._discover().loops if l.owner == "os.mixed")
        self.assertEqual(loop.event_count_total, 7)
        self.assertEqual(loop.event_count_7d, 2)

    def test_scanned_window_travels_with_the_totals(self) -> None:
        self.fx.write([_event("os.a", self.now - timedelta(hours=1)) for _ in range(5)])
        result = self._discover()
        self.assertEqual(result.scanned, 5)
        self.assertFalse(result.truncated)


class TestCoreLoopTrend(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()
        self.addCleanup(self.fx.close)
        self.now = datetime.now(timezone.utc)

    def test_a_day_without_an_outcome_has_no_score(self) -> None:
        """A quiet day did not fail. Carrying a value forward or writing 0.0
        would both draw a line through data that does not exist."""
        from core.learning import loop_discovery

        self.fx.write([
            _event("os.router", self.now, "outcome", success=True),
            _event("os.router", self.now - timedelta(days=2)),  # execution only
        ])
        points = loop_discovery.core_loop_trend("_default", "core:os.router", days=5)
        self.assertEqual(len(points), 5)
        today = points[-1]
        self.assertEqual(today.health_score, 1.0)
        two_days_ago = points[-3]
        self.assertEqual(two_days_ago.event_count, 1)
        self.assertIsNone(two_days_ago.health_score)

    def test_undated_sources_yield_no_trend_and_no_events(self) -> None:
        from core.learning import loop_discovery

        self.assertEqual(loop_discovery.core_loop_trend("_default", "core:cel.memory"), [])
        self.assertEqual(loop_discovery.core_loop_events("_default", "core:cel.memory"), [])

    def test_non_core_ids_are_not_claimed(self) -> None:
        from core.learning import loop_discovery

        self.assertIsNone(loop_discovery.core_loop_owner("plugin:foo:bar"))
        self.assertEqual(loop_discovery.core_loop_owner("core:os.x"), "os.x")


class TestSignalRendering(unittest.TestCase):
    def test_only_allowlisted_scalars_are_rendered(self) -> None:
        """The signal dict is written by many emitters and is not guaranteed
        content-free, so rendering it whole would leak whatever an emitter put
        there."""
        from core.learning.loop_discovery import _signal_summary

        out = _signal_summary({
            "status": "completed",
            "engine": "claude",
            "duration_ms": 12,
            "prompt": "secret user text",
            "user_email": "someone@example.com",
            "nested": {"also": "secret"},
        })
        self.assertIn("status=completed", out)
        self.assertIn("engine=claude", out)
        self.assertNotIn("secret", out)
        self.assertNotIn("@", out)

    def test_a_non_dict_signal_renders_nothing(self) -> None:
        from core.learning.loop_discovery import _signal_summary

        self.assertIsNone(_signal_summary(None))
        self.assertIsNone(_signal_summary("just a string"))


if __name__ == "__main__":
    unittest.main()
