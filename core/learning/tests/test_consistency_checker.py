"""R3-B2 / R3-B3: the "recent loss" window must be RECENT, and one bad line
must not blind the whole query.

Both defects live in ``core.learning.event_store.EventStore.query_events`` and
surface in :class:`core.learning.consistency_checker.FeedbackConsistencyValidator`,
which is what decides whether operator feedback is downweighted:

* R3-B2 — the store globbed its date files ASCENDING and returned as soon as
  ``offset+limit`` events were collected, so it handed back the OLDEST N. The
  validator's "recent" trend was therefore computed from ancient history and
  the window never advanced: a system whose loss had been falling for weeks
  still looked like whatever it looked like on day one.
* R3-B3 — ``EventType(data["event_type"])`` sat inside a ``try`` whose only
  handler was ``json.JSONDecodeError``. A single record with an unknown enum
  value (a newer schema, or corruption) raised ``ValueError`` OUT of
  ``query_events``, discarding every later good event; the validator's bare
  ``except`` then saw an empty history and inferred "neutral" — exactly the
  always-neutral behaviour round 2 removed.

The store is driven through its real write/read API on a real on-disk tree;
only the two hostile lines (unknown enum, corrupt JSON) are appended directly,
because ``write_event`` correctly refuses to produce them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.learning.consistency_checker import FeedbackConsistencyValidator, FeedbackSignal
from core.learning.event_store import EventStore
from core.learning.learning_events import EventType, LearningEvent

TENANT = "_default"
SKILL = "os.delegation_router"


@pytest.fixture
def store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "tenants" / TENANT, tenant_id=TENANT)


def _write(store: EventStore, date: str, seq: int, total_loss: float) -> None:
    """Append one real learning event dated ``date`` (date-partitioned file)."""
    event = LearningEvent(
        event_id=f"ev_{date}_{seq}",
        event_type=EventType.METRIC,
        skill_id=SKILL,
        tenant_id=TENANT,
        timestamp=f"{date}T00:{seq:02d}:00Z",
        signal={"total_loss": total_loss},
        lom="core/learning/tests/test_consistency_checker.py:_write",
    )
    store.write_event(event)


class TestNewestFirstSelection:
    def test_limit_always_windows_from_the_newest_end(self, store):
        """``limit=N`` means "the N most recent", in EVERY order (round-4, F3).

        This used to assert the opposite for the default — that ``limit=2``
        returned the two OLDEST events — which is the contract that froze
        ``recent_outcomes`` on outcomes #4991–#5000 of all time. Selection is
        now always the newest window; ``newest_first`` only orders the result.
        """
        _write(store, "2026-01-01", 1, 0.90)
        _write(store, "2026-01-01", 2, 0.88)
        _write(store, "2026-09-05", 1, 0.20)
        _write(store, "2026-09-06", 1, 0.10)

        chronological = store.query_events(TENANT, limit=2)
        assert [e.signal["total_loss"] for e in chronological] == [0.20, 0.10]

        newest = store.query_events(TENANT, limit=2, newest_first=True)
        assert [e.signal["total_loss"] for e in newest] == [0.10, 0.20]

    def test_offset_skips_the_most_recent_events(self, store):
        for seq, loss in enumerate([0.5, 0.4, 0.3, 0.2, 0.1], start=1):
            _write(store, "2026-09-06", seq, loss)

        page2 = store.query_events(TENANT, limit=2, offset=2)
        assert [e.signal["total_loss"] for e in page2] == [0.4, 0.3]

    def test_newest_first_reverses_within_a_single_date_file(self, store):
        for seq, loss in enumerate([0.5, 0.4, 0.3, 0.2, 0.1], start=1):
            _write(store, "2026-09-06", seq, loss)

        newest = store.query_events(TENANT, limit=2, newest_first=True)
        assert [e.signal["total_loss"] for e in newest] == [0.1, 0.2]

    def test_the_window_advances_as_new_events_arrive(self, store):
        for seq, loss in enumerate([0.9, 0.9, 0.9], start=1):
            _write(store, "2026-01-01", seq, loss)
        before = store.query_events(TENANT, limit=3, newest_first=True)
        assert [e.signal["total_loss"] for e in before] == [0.9, 0.9, 0.9]

        _write(store, "2026-09-06", 1, 0.1)
        after = store.query_events(TENANT, limit=3, newest_first=True)
        assert after[0].signal["total_loss"] == 0.1, "the newest event must be in the window"


class TestValidatorSeesTheRecentTrend:
    def test_a_long_improving_history_is_read_as_improving(self, store):
        # Ancient history (more than one full window): loss CLIMBING.
        window = FeedbackConsistencyValidator.LOSS_WINDOW_SAMPLES
        for seq in range(1, window + 6):
            _write(store, "2026-01-01", seq, 0.30 + 0.02 * seq)
        # Recent history (exactly one window): loss FALLING.
        for seq in range(1, window + 1):
            _write(store, "2026-09-06", seq, 0.70 - 0.02 * seq)

        validator = FeedbackConsistencyValidator(event_store=store)
        result = validator.validate_consistency(
            feedback_id="fb_recent",
            skill_id=SKILL,
            task_id="task_recent",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id=TENANT,
        )
        # GOOD feedback while loss is FALLING is consistent. Reading the oldest
        # window instead (the pre-fix behaviour) reports "increasing" and rates
        # this same feedback as a contradiction.
        assert result.loss_trend == "decreasing", result
        assert result.is_consistent, result
        assert result.consistency_score > 0.5


class TestOneBadLineDoesNotBlindTheQuery:
    def _corrupt(self, store: EventStore, date: str, line: str) -> None:
        with open(store.events_dir / f"{date}.jsonl", "a") as fh:
            fh.write(line + "\n")

    def test_unknown_event_type_is_skipped_not_raised(self, store):
        _write(store, "2026-09-06", 1, 0.5)
        self._corrupt(store, "2026-09-06", json.dumps({
            "event_id": "ev_future",
            "event_type": "a_type_from_a_newer_schema",
            "skill_id": SKILL,
            "tenant_id": TENANT,
            "timestamp": "2026-09-06T02:00:00Z",
            "signal": {"total_loss": 0.4},
        }))
        _write(store, "2026-09-06", 3, 0.3)

        events = store.query_events(TENANT)
        assert [e.signal["total_loss"] for e in events] == [0.5, 0.3], (
            "the good event AFTER the unknown-enum line must survive"
        )

    def test_corrupt_json_line_is_skipped_not_fatal_for_the_file(self, store):
        _write(store, "2026-09-06", 1, 0.5)
        self._corrupt(store, "2026-09-06", "{not json at all")
        _write(store, "2026-09-06", 3, 0.3)

        events = store.query_events(TENANT)
        assert [e.signal["total_loss"] for e in events] == [0.5, 0.3]

    def test_validator_still_sees_a_history_through_a_bad_line(self, store):
        for seq in range(1, 4):
            _write(store, "2026-09-06", seq, 0.70 - 0.05 * seq)
        self._corrupt(store, "2026-09-06", json.dumps({
            "event_id": "ev_future",
            "event_type": "a_type_from_a_newer_schema",
            "skill_id": SKILL,
            "tenant_id": TENANT,
            "timestamp": "2026-09-06T04:00:00Z",
        }))
        for seq in range(5, 8):
            _write(store, "2026-09-06", seq, 0.70 - 0.05 * seq)

        validator = FeedbackConsistencyValidator(event_store=store)
        result = validator.validate_consistency(
            feedback_id="fb_bad_line",
            skill_id=SKILL,
            task_id="task_bad_line",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id=TENANT,
        )
        assert result.loss_trend == "decreasing", result
        assert result.loss_trend != "unknown"


# ── round-4 F8: the "chronological" sort was a plain string sort ─────────────
#
# Python's sort is stable, so equal or empty keys preserved the INPUT order —
# and the input is newest-first. A monotonically DECREASING loss was therefore
# reported as INCREASING whenever the timestamps were missing, identical, or
# lexically incomparable (mixed UTC offsets).


class _NewestFirstStore:
    """Minimal store stub with the REAL store's ordering contract."""

    def __init__(self, losses, timestamps):
        # losses/timestamps are given oldest→newest; the store answers reversed.
        self._events = [
            LearningEvent(
                event_id=f"ev{i}",
                event_type=EventType.METRIC,
                skill_id=SKILL,
                tenant_id=TENANT,
                timestamp=ts,
                signal={"total_loss": loss},
            )
            for i, (loss, ts) in enumerate(zip(losses, timestamps))
        ][::-1]

    def query_events(self, *args, **kwargs):
        return list(self._events)


_FALLING = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]


def _trend(timestamps):
    validator = FeedbackConsistencyValidator(
        event_store=_NewestFirstStore(_FALLING, timestamps)
    )
    result = validator.validate_consistency(
        feedback_id="fb1", skill_id=SKILL, task_id="t1",
        feedback_signal=FeedbackSignal.GOOD, tenant_id=TENANT,
    )
    return result.loss_trend


def test_well_formed_utc_timestamps_read_as_decreasing():
    stamps = [f"2026-09-06T00:{i:02d}:00Z" for i in range(10)]
    assert _trend(stamps) == "decreasing"


def test_missing_timestamps_do_not_invert_the_trend():
    assert _trend([None] * 10) == "decreasing"


def test_identical_timestamps_do_not_invert_the_trend():
    assert _trend(["2026-09-06T00:00:00Z"] * 10) == "decreasing"


def test_mixed_utc_offsets_are_ordered_by_instant_not_by_string():
    # Alternating Z / +02:00 stamps whose real instants are still increasing.
    stamps = []
    for i in range(10):
        if i % 2 == 0:
            stamps.append(f"2026-09-06T02:{i:02d}:00Z")
        else:
            stamps.append(f"2026-09-06T04:{i:02d}:00+02:00")  # == 02:0i UTC
    assert _trend(stamps) == "decreasing"
