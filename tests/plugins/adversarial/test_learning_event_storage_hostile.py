"""Adversarial tests for learning_event_storage plugin.

Tests defensive behavior under hostile conditions.
- Event mutation (tampering)
- Concurrent writes (race conditions)
- Tenant isolation (cross-tenant access)
- Event integrity (field validation)
"""

import pytest
import threading
import tempfile
import dataclasses
from pathlib import Path
from datetime import datetime

from core.learning.event_store import EventStore
from core.learning.learning_events import LearningEvent, EventType


@pytest.mark.adversarial
class TestLearningEventStorageHostile:
    """Adversarial tests for learning_event_storage."""

    def test_adversarial_event_mutation_attempt(self):
        """RED: Try to modify a LearningEvent after creation; GREEN: frozen dataclass prevents attribute reassignment."""
        event = LearningEvent.create(
            EventType.SKILL_EXECUTED,
            "test_skill",
            "tenant_a",
            {"output": "data"}
        )

        # RED: Try to reassign the signal attribute (should fail on frozen)
        with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
            event.signal = {"output": "hacked"}  # Will fail because dataclass is frozen

        # GREEN: Event fields are still original
        assert event.signal["output"] == "data"

    def test_adversarial_concurrent_writes_race_condition(self):
        """RED: Multiple threads write events simultaneously; GREEN: no data loss."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            errors = []

            def write_many(tenant_id, count):
                for i in range(count):
                    try:
                        event = LearningEvent.create(
                            EventType.SKILL_EXECUTED,
                            f"skill_{i}",
                            tenant_id,
                            {"iteration": i}
                        )
                        store.write_event(event)
                    except Exception as e:
                        errors.append(e)

            # Launch 5 threads, each writing 20 events
            threads = [
                threading.Thread(target=write_many, args=(f"tenant_{j}", 20))
                for j in range(5)
            ]

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # GREEN: No exceptions + all events persisted
            assert len(errors) == 0, f"Concurrent writes should not error: {errors}"

            # Verify all events written (5 tenants * 20 events = 100 total)
            all_events = []
            for tenant_id in [f"tenant_{j}" for j in range(5)]:
                results = store.query_events(tenant_id)
                all_events.extend(results)

            assert len(all_events) == 100, "All concurrent writes should persist"

    def test_adversarial_malformed_event_fields(self):
        """RED: Create event with missing required fields; GREEN: __post_init__ validation catches it."""
        # Missing tenant_id
        with pytest.raises(ValueError):
            LearningEvent.create(
                EventType.SKILL_EXECUTED,
                "test_skill",
                "",  # Empty tenant_id
            )

        # Missing skill_id
        with pytest.raises(ValueError):
            LearningEvent(
                event_id="test",
                event_type=EventType.SKILL_EXECUTED,
                skill_id="",  # Empty
                tenant_id="tenant_a",
                timestamp=datetime.utcnow().isoformat() + "Z"
            )

    def test_adversarial_event_immutability(self):
        """VECTOR: LearningEvent is frozen, preventing post-creation tampering."""
        event = LearningEvent.create(
            EventType.SKILL_EXECUTED,
            "test",
            "tenant_a",
            {"original": "data"}
        )

        # GREEN: Cannot reassign fields (frozen dataclass)
        with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
            event.timestamp = "hacked_timestamp"

        with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
            event.signal = {"tampered": "value"}
