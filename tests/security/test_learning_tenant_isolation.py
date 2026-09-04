"""Multi-tenant isolation tests for learning plugins (HIGH-2 remediation).

Verifies that learning events are isolated by tenant_id and never leak
across tenant boundaries. Tests concurrent access from multiple tenants.

GDPR Art. 5/6/32 compliance: each tenant sees only own data.
"""

import asyncio
import pytest
import tempfile
import threading
from pathlib import Path

# Import the plugins
import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS/core')

from plugins.builtin_plugins.learning_event_storage import LearningEventStorage
from plugins.builtin_plugins.brain_learning_tracker import BrainLearningTracker


class TestLearningTenantIsolation:
    """Tests for tenant isolation in learning plugins."""

    @pytest.mark.asyncio
    async def test_event_storage_tenant_filtering(self):
        """Test that retrieve_events filters by tenant_id."""
        storage = LearningEventStorage()
        await storage.initialize(None)

        # Setup: 3 tenants, 3 events each
        events_config = [
            ("tenant_a", [
                {"tenant_id": "tenant_a", "type": "feedback", "skill_id": "skill_1", "score": 0.8},
                {"tenant_id": "tenant_a", "type": "outcome", "skill_id": "skill_2", "score": 0.9},
                {"tenant_id": "tenant_a", "type": "preference", "skill_id": "skill_1", "score": 0.7},
            ]),
            ("tenant_b", [
                {"tenant_id": "tenant_b", "type": "feedback", "skill_id": "skill_1", "score": 0.5},
                {"tenant_id": "tenant_b", "type": "outcome", "skill_id": "skill_3", "score": 0.4},
                {"tenant_id": "tenant_b", "type": "preference", "skill_id": "skill_2", "score": 0.6},
            ]),
            ("tenant_c", [
                {"tenant_id": "tenant_c", "type": "feedback", "skill_id": "skill_2", "score": 0.9},
                {"tenant_id": "tenant_c", "type": "outcome", "skill_id": "skill_1", "score": 0.8},
                {"tenant_id": "tenant_c", "type": "preference", "skill_id": "skill_3", "score": 0.7},
            ]),
        ]

        # Store all events
        for tenant_id, events in events_config:
            for event in events:
                await storage.execute(
                    "store_event",
                    event=event
                )

        # Test 1: Each tenant retrieves only their own events
        for tenant_id, expected_events in events_config:
            result = await storage.execute(
                "retrieve_events",
                tenant_id=tenant_id
            )
            assert result["success"], f"Failed to retrieve events for {tenant_id}"
            assert result["count"] == 3, f"Expected 3 events for {tenant_id}, got {result['count']}"

            # Verify all retrieved events belong to this tenant
            for event in result["events"]:
                assert event["tenant_id"] == tenant_id, \
                    f"Event from wrong tenant: {event['tenant_id']} != {tenant_id}"

        # Test 2: Verify no cross-tenant leakage
        for tenant_id, _ in events_config:
            result = await storage.execute(
                "retrieve_events",
                tenant_id=tenant_id
            )
            for event in result["events"]:
                for other_tenant_id, _ in events_config:
                    if other_tenant_id != tenant_id:
                        assert event["tenant_id"] != other_tenant_id, \
                            f"Cross-tenant leakage detected: {tenant_id} can see {other_tenant_id}"

    @pytest.mark.asyncio
    async def test_query_by_type_tenant_isolation(self):
        """Test query_by_type respects tenant isolation."""
        storage = LearningEventStorage()
        await storage.initialize(None)

        # Setup: 2 tenants with overlapping event types
        await storage.execute("store_event", event={
            "tenant_id": "tenant_a",
            "type": "feedback",
            "skill_id": "skill_1",
            "score": 0.8
        })
        await storage.execute("store_event", event={
            "tenant_id": "tenant_b",
            "type": "feedback",
            "skill_id": "skill_1",
            "score": 0.5
        })

        # Query by type for each tenant
        result_a = storage.query_by_type("feedback", "tenant_a")
        result_b = storage.query_by_type("feedback", "tenant_b")

        # Each should have 1 event
        assert len(result_a) == 1
        assert len(result_b) == 1

        # Verify event ownership
        assert result_a[0]["tenant_id"] == "tenant_a"
        assert result_b[0]["tenant_id"] == "tenant_b"
        assert result_a[0]["score"] == 0.8
        assert result_b[0]["score"] == 0.5

    @pytest.mark.asyncio
    async def test_query_by_skill_tenant_isolation(self):
        """Test query_by_skill respects tenant isolation."""
        storage = LearningEventStorage()
        await storage.initialize(None)

        # Setup: 2 tenants, same skill
        for tenant_id in ["tenant_a", "tenant_b"]:
            for i in range(3):
                await storage.execute("store_event", event={
                    "tenant_id": tenant_id,
                    "type": "feedback",
                    "skill_id": "skill_1",
                    "score": 0.5 + i * 0.1
                })

        # Query skill for each tenant
        result_a = storage.query_by_skill("skill_1", "tenant_a")
        result_b = storage.query_by_skill("skill_1", "tenant_b")

        # Each should have 3 events
        assert len(result_a) == 3
        assert len(result_b) == 3

        # Verify no cross-tenant leakage
        for event in result_a:
            assert event["tenant_id"] == "tenant_a"
        for event in result_b:
            assert event["tenant_id"] == "tenant_b"

    @pytest.mark.asyncio
    async def test_brain_tracker_tenant_isolation(self):
        """Test BrainLearningTracker respects tenant isolation."""
        tracker = BrainLearningTracker()
        await tracker.initialize(None)

        # Setup: 3 tenants, track confidence scores
        tenants = ["tenant_a", "tenant_b", "tenant_c"]
        for tenant_id in tenants:
            for i in range(5):
                await tracker.execute(
                    "track_confidence",
                    task_id=f"task-{i}",
                    score=0.5 + i * 0.1,
                    timestamp=f"2026-09-04T12:{i:02d}:00Z",
                    tenant_id=tenant_id
                )

        # Test: Each tenant sees only own learning curve
        for tenant_id in tenants:
            result = await tracker.execute(
                "get_learning_curve",
                tenant_id=tenant_id
            )
            assert result["success"]
            assert len(result["curve"]) == 5, f"Expected 5 scores for {tenant_id}"

        # Test: Verify no cross-tenant leakage
        result_a = await tracker.execute(
            "get_learning_curve",
            tenant_id="tenant_a"
        )
        result_b = await tracker.execute(
            "get_learning_curve",
            tenant_id="tenant_b"
        )

        # Curves should be different (same task IDs but different internal state)
        assert result_a["success"] and result_b["success"]
        # Tenant A and B should have isolated data
        assert len(result_a["curve"]) == 5
        assert len(result_b["curve"]) == 5

    def test_record_grade_tenant_isolation(self):
        """Test record_grade includes tenant_id and maintains isolation."""
        tracker = BrainLearningTracker()

        # Record grades from different tenants
        grade_a = tracker.record_grade(
            skill_id="skill_1",
            decision_id="dec-1",
            score=0.8,
            feedback="Good",
            tenant_id="tenant_a"
        )

        grade_b = tracker.record_grade(
            skill_id="skill_1",
            decision_id="dec-2",
            score=0.5,
            feedback="Average",
            tenant_id="tenant_b"
        )

        # Both should succeed
        assert grade_a["success"]
        assert grade_b["success"]
        assert grade_a["tenant_id"] == "tenant_a"
        assert grade_b["tenant_id"] == "tenant_b"

        # Verify internal state is isolated
        with tracker._lock:
            # Tenant A's scores
            scores_a = [s for s in tracker._confidence_scores if s.tenant_id == "tenant_a"]
            # Tenant B's scores
            scores_b = [s for s in tracker._confidence_scores if s.tenant_id == "tenant_b"]

        assert len(scores_a) == 1
        assert len(scores_b) == 1
        assert scores_a[0].score == 0.8
        assert scores_b[0].score == 0.5

    def test_concurrent_tenant_access(self):
        """Test concurrent access from multiple tenants doesn't leak data."""
        storage = LearningEventStorage()
        errors = []
        results_by_tenant = {}
        lock = threading.Lock()

        def tenant_write_and_read(tenant_id: str, event_count: int):
            try:
                # Write events
                for i in range(event_count):
                    storage.execute("store_event", event={
                        "tenant_id": tenant_id,
                        "type": f"type-{i}",
                        "skill_id": f"skill-{i}",
                        "score": 0.5
                    })

                # Read back (should see only own events)
                result = storage.execute(
                    "retrieve_events",
                    tenant_id=tenant_id
                )

                with lock:
                    results_by_tenant[tenant_id] = result
                    # Verify all retrieved events belong to this tenant
                    if result["success"]:
                        for event in result["events"]:
                            if event.get("tenant_id") != tenant_id:
                                errors.append(
                                    f"Tenant {tenant_id} sees event from {event.get('tenant_id')}"
                                )
            except Exception as e:
                errors.append(f"Tenant {tenant_id} error: {str(e)}")

        # Concurrent: 5 threads, 3 tenants × (5, 3, 4) events
        tenants = [
            ("tenant_a", 5),
            ("tenant_b", 3),
            ("tenant_c", 4),
        ]

        threads = []
        for tenant_id, event_count in tenants:
            for _ in range(2):  # 2 concurrent operations per tenant
                t = threading.Thread(
                    target=tenant_write_and_read,
                    args=(tenant_id, event_count)
                )
                threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify: no errors, no cross-tenant leakage
        assert len(errors) == 0, f"Concurrent access errors: {errors}"
        assert len(results_by_tenant) == 3

        # Each tenant should see only own events
        for tenant_id in ["tenant_a", "tenant_b", "tenant_c"]:
            assert tenant_id in results_by_tenant
            result = results_by_tenant[tenant_id]
            assert result["success"]
            # All events should belong to this tenant
            for event in result["events"]:
                assert event["tenant_id"] == tenant_id

    @pytest.mark.asyncio
    async def test_concurrent_multitenent_stress(self):
        """Stress test: all operations on multiple tenants concurrently."""
        storage = LearningEventStorage()
        tracker = BrainLearningTracker()

        await storage.initialize(None)
        await tracker.initialize(None)

        errors = []
        counter = threading.Lock()
        stats = {"writes": 0, "reads": 0, "grades": 0}

        async def storage_ops(tenant_id: str):
            try:
                # Write 10 events
                for i in range(10):
                    await storage.execute(
                        "store_event",
                        event={
                            "tenant_id": tenant_id,
                            "type": "feedback",
                            "skill_id": f"skill-{i}",
                            "score": 0.5 + i * 0.05
                        }
                    )
                with counter:
                    stats["writes"] += 10

                # Read back
                result = await storage.execute(
                    "retrieve_events",
                    tenant_id=tenant_id
                )
                with counter:
                    stats["reads"] += 1
                    if result["count"] != 10:
                        errors.append(
                            f"Tenant {tenant_id}: expected 10 events, got {result['count']}"
                        )
            except Exception as e:
                errors.append(f"Storage op error for {tenant_id}: {str(e)}")

        async def tracker_ops(tenant_id: str):
            try:
                # Track 5 confidence scores
                for i in range(5):
                    await tracker.execute(
                        "track_confidence",
                        task_id=f"task-{i}",
                        score=0.6,
                        timestamp="2026-09-04T12:00:00Z",
                        tenant_id=tenant_id
                    )
                with counter:
                    stats["writes"] += 5

                # Get learning curve
                result = await tracker.execute(
                    "get_learning_curve",
                    tenant_id=tenant_id
                )
                with counter:
                    stats["reads"] += 1
                    if len(result.get("curve", [])) != 5:
                        errors.append(
                            f"Tenant {tenant_id}: expected 5 scores, got {len(result.get('curve', []))}"
                        )
            except Exception as e:
                errors.append(f"Tracker op error for {tenant_id}: {str(e)}")

        def tracker_grade_ops(tenant_id: str):
            try:
                for i in range(5):
                    tracker.record_grade(
                        skill_id=f"skill-{i}",
                        decision_id=f"dec-{i}",
                        score=0.7,
                        feedback="Good",
                        tenant_id=tenant_id
                    )
                with counter:
                    stats["grades"] += 5
            except Exception as e:
                errors.append(f"Grade op error for {tenant_id}: {str(e)}")

        # Run concurrently
        tasks = []
        for tenant_id in ["tenant_a", "tenant_b", "tenant_c"]:
            tasks.append(storage_ops(tenant_id))
            tasks.append(tracker_ops(tenant_id))

        await asyncio.gather(*tasks)

        # Also run sync grade ops in threads
        threads = []
        for tenant_id in ["tenant_a", "tenant_b", "tenant_c"]:
            t = threading.Thread(target=tracker_grade_ops, args=(tenant_id,))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify: no errors
        assert len(errors) == 0, f"Stress test errors: {errors}"
        assert stats["writes"] > 0
        assert stats["reads"] > 0
        assert stats["grades"] > 0

    @pytest.mark.asyncio
    async def test_no_default_tenant_leakage(self):
        """Test that "_default" tenant doesn't receive data from others."""
        storage = LearningEventStorage()
        await storage.initialize(None)

        # Store events in specific tenants
        await storage.execute("store_event", event={
            "tenant_id": "tenant_prod",
            "type": "feedback",
            "skill_id": "skill_1",
            "score": 0.9
        })

        # Try to read from _default (should get nothing)
        result_default = await storage.execute(
            "retrieve_events",
            tenant_id="_default"
        )

        result_prod = await storage.execute(
            "retrieve_events",
            tenant_id="tenant_prod"
        )

        # _default should be empty
        assert result_default["count"] == 0
        # tenant_prod should have 1
        assert result_prod["count"] == 1
