"""Phase 4 Tests: Feedback WAL + Daemon Integration (ADR-0661/0662)

Test Plan:
1. FeedbackWAL persistence (append, read, mark processed, retention)
2. Daemon WAL consumer (read unprocessed, apply learning, crash recovery)
3. End-to-end: feedback → weight update → audit trail → dashboard visibility
4. Crash scenarios: daemon dies mid-learning, resumes from WAL
"""

import asyncio
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any
from unittest import mock

import pytest

from core.background.learning_daemon import DataHubLearningDaemon, DaemonEvent
from core.learning.feedback_wal import FeedbackWAL, FeedbackWALEntry


class TestFeedbackWAL:
    """Test FeedbackWAL persistence layer."""

    @pytest.fixture
    def wal(self):
        """Create temporary WAL for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield FeedbackWAL(Path(tmpdir), tenant_id="_default")

    @pytest.mark.asyncio
    async def test_wal_append(self, wal):
        """Append feedback to WAL."""
        feedback = {
            "feedback_id": "fb-001",
            "skill_id": "test_skill",
            "task_id": "task-001",
            "tenant_id": "_default",
            "signal": 0.8,
        }

        entry = await wal.append(feedback)

        assert entry.feedback_id == "fb-001"
        assert entry.skill_id == "test_skill"
        assert entry.signal == 0.8
        assert not entry.processed
        assert entry.entry_id is not None

    @pytest.mark.asyncio
    async def test_wal_persistence(self, wal):
        """Feedback persists to disk."""
        feedback = {
            "feedback_id": "fb-002",
            "skill_id": "skill2",
            "task_id": "task-002",
            "tenant_id": "_default",
            "signal": -0.5,
        }

        entry1 = await wal.append(feedback)
        assert wal.wal_file.exists()

        # Read back from disk
        with open(wal.wal_file, "r") as f:
            line = f.readline()
            entry_dict = json.loads(line)

        assert entry_dict["feedback_id"] == "fb-002"
        assert entry_dict["signal"] == -0.5
        assert entry_dict["processed"] is False

    @pytest.mark.asyncio
    async def test_wal_get_unprocessed(self, wal):
        """Retrieve unprocessed entries from WAL."""
        # Append 3 entries
        for i in range(3):
            await wal.append(
                {
                    "feedback_id": f"fb-{i}",
                    "skill_id": f"skill-{i}",
                    "task_id": f"task-{i}",
                    "tenant_id": "_default",
                    "signal": float(i),
                }
            )

        # Retrieve unprocessed
        unprocessed = await wal.get_unprocessed()
        assert len(unprocessed) == 3

    @pytest.mark.asyncio
    async def test_wal_mark_processed(self, wal):
        """Mark entry as processed in WAL."""
        entry1 = await wal.append(
            {
                "feedback_id": "fb-mark",
                "skill_id": "skill",
                "task_id": "task",
                "tenant_id": "_default",
                "signal": 0.5,
            }
        )

        # Mark processed
        success = await wal.mark_processed(entry1.entry_id)
        assert success

        # Verify not in unprocessed list
        unprocessed = await wal.get_unprocessed()
        assert len(unprocessed) == 0

    @pytest.mark.asyncio
    async def test_wal_mark_processed_with_error(self, wal):
        """Mark entry with error reason."""
        entry1 = await wal.append(
            {
                "feedback_id": "fb-error",
                "skill_id": "skill",
                "task_id": "task",
                "tenant_id": "_default",
                "signal": 0.5,
            }
        )

        # Mark with error
        success = await wal.mark_processed(entry1.entry_id, error="stale_feedback")
        assert success

        # Verify error is persisted
        with open(wal.wal_file, "r") as f:
            line = f.readline()
            entry_dict = json.loads(line)

        assert entry_dict["processed"] is True
        assert entry_dict["process_error"] == "stale_feedback"

    @pytest.mark.asyncio
    async def test_wal_crash_recovery(self, wal):
        """WAL recovers processing state after crash."""
        # Append entries
        entry1 = await wal.append(
            {
                "feedback_id": "fb-rec1",
                "skill_id": "skill",
                "task_id": "task",
                "tenant_id": "_default",
                "signal": 0.5,
            }
        )
        entry2 = await wal.append(
            {
                "feedback_id": "fb-rec2",
                "skill_id": "skill",
                "task_id": "task",
                "tenant_id": "_default",
                "signal": 0.3,
            }
        )

        # Mark first as processed
        await wal.mark_processed(entry1.entry_id)

        # Simulate crash: create new WAL instance (reloads from disk)
        wal2 = FeedbackWAL(wal.wal_dir, tenant_id="_default")

        # Should recover last processed marker
        assert wal2.last_processed_entry_id == entry1.entry_id

        # Should only return unprocessed
        unprocessed = await wal2.get_unprocessed()
        assert len(unprocessed) == 1
        assert unprocessed[0].feedback_id == "fb-rec2"

    @pytest.mark.asyncio
    async def test_wal_retention_policy(self, wal):
        """Old processed entries are archived by retention policy."""
        # Append and process old entry
        old_entry = await wal.append(
            {
                "feedback_id": "fb-old",
                "skill_id": "skill",
                "task_id": "task",
                "tenant_id": "_default",
                "signal": 0.5,
                "timestamp": (datetime.utcnow() - timedelta(days=95)).isoformat(),
            }
        )
        await wal.mark_processed(old_entry.entry_id)

        # Append recent entry
        new_entry = await wal.append(
            {
                "feedback_id": "fb-new",
                "skill_id": "skill",
                "task_id": "task",
                "tenant_id": "_default",
                "signal": 0.3,
            }
        )

        # Run retention policy
        archived = await wal.retention_cleanup()

        # Old entry should be archived
        assert archived == 1

        # Recent entry should remain
        unprocessed = await wal.get_unprocessed()
        assert len(unprocessed) == 1
        assert unprocessed[0].feedback_id == "fb-new"


class TestDaemonWALIntegration:
    """Test daemon integration with FeedbackWAL."""

    @pytest.fixture
    def daemon_with_wal(self):
        """Create daemon with WAL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wal = FeedbackWAL(Path(tmpdir), tenant_id="_default")
            daemon = DataHubLearningDaemon(tenant_id="_default", feedback_wal=wal)
            yield daemon, wal

    @pytest.mark.asyncio
    async def test_daemon_processes_wal_feedback(self, daemon_with_wal):
        """Daemon reads and processes feedback from WAL."""
        daemon, wal = daemon_with_wal

        # Append feedback to WAL
        entry = await wal.append(
            {
                "feedback_id": "fb-daemon",
                "skill_id": "os.test_skill",
                "task_id": "task-daemon",
                "tenant_id": "_default",
                "signal": 0.9,
            }
        )

        # Process feedback from WAL
        await daemon._process_feedback_from_wal()

        # Entry should be marked processed
        unprocessed = await wal.get_unprocessed()
        assert len(unprocessed) == 0

        # Weight learner should have processed feedback
        assert daemon.weight_learner.weight_history  # History grew

    @pytest.mark.asyncio
    async def test_daemon_rejects_stale_feedback(self, daemon_with_wal):
        """Daemon rejects feedback older than 24 hours."""
        daemon, wal = daemon_with_wal

        # Create stale feedback (>24h old)
        stale_time = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        entry = await wal.append(
            {
                "feedback_id": "fb-stale",
                "skill_id": "skill",
                "task_id": "task",
                "tenant_id": "_default",
                "signal": 0.5,
                "timestamp": stale_time,
            }
        )

        # Process
        await daemon._process_feedback_from_wal()

        # Should be marked with "stale_feedback" error
        with open(wal.wal_file, "r") as f:
            line = f.readline()
            entry_dict = json.loads(line)

        assert entry_dict["processed"] is True
        assert entry_dict["process_error"] == "stale_feedback"

    @pytest.mark.asyncio
    async def test_daemon_wal_crash_recovery(self, daemon_with_wal):
        """Daemon resumes learning from WAL after crash."""
        daemon1, wal = daemon_with_wal

        # Add feedback
        entries = []
        for i in range(3):
            entry = await wal.append(
                {
                    "feedback_id": f"fb-crash-{i}",
                    "skill_id": "skill",
                    "task_id": "task",
                    "tenant_id": "_default",
                    "signal": 0.5,
                }
            )
            entries.append(entry)

        # Process first two
        await daemon1._process_feedback_from_wal()
        await daemon1._process_feedback_from_wal()  # Process batch

        unprocessed = await wal.get_unprocessed()
        initial_count = len(unprocessed)

        # Simulate daemon1 crash: create new daemon2
        daemon2 = DataHubLearningDaemon(tenant_id="_default", feedback_wal=wal)

        # daemon2 should resume from where daemon1 left off
        await daemon2._process_feedback_from_wal()

        # All should be processed now
        unprocessed = await wal.get_unprocessed()
        assert len(unprocessed) == 0


class TestPhase4E2E:
    """End-to-end Phase 4 tests: feedback → learning → audit."""

    @pytest.mark.asyncio
    async def test_e2e_feedback_to_weight_update(self):
        """E2E: feedback flows through WAL → daemon → weight update."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wal = FeedbackWAL(Path(tmpdir), tenant_id="_default")
            daemon = DataHubLearningDaemon(tenant_id="_default", feedback_wal=wal)

            # Record initial weights
            initial_weights = daemon.weight_learner.weights.copy()

            # Add positive feedback for a data source
            await wal.append(
                {
                    "feedback_id": "fb-e2e",
                    "skill_id": "test_skill",
                    "task_id": "task-e2e",
                    "tenant_id": "_default",
                    "signal": 0.8,
                    "data_sources": ["memory:tier2", "rag:embeddings"],
                }
            )

            # Process through daemon
            await daemon._process_feedback_from_wal()

            # Weights should have changed (improved for positive sources)
            updated_weights = daemon.weight_learner.weights
            assert updated_weights != initial_weights

            # Verify audit trail was emitted
            assert daemon.audit_trail  # Has events

    @pytest.mark.asyncio
    async def test_e2e_convergence_on_feedback(self):
        """E2E: multiple feedback signals drive convergence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wal = FeedbackWAL(Path(tmpdir), tenant_id="_default")
            daemon = DataHubLearningDaemon(tenant_id="_default", feedback_wal=wal)

            # Simulate 100 consistent positive feedbacks
            for i in range(100):
                await wal.append(
                    {
                        "feedback_id": f"fb-conv-{i}",
                        "skill_id": "test_skill",
                        "task_id": f"task-{i}",
                        "tenant_id": "_default",
                        "signal": 0.8,  # Consistently positive
                        "data_sources": ["memory:tier2"],
                    }
                )

            # Process all feedback
            for _ in range(5):  # Run multiple batches
                await daemon._process_feedback_from_wal()

            # Check convergence
            converged = daemon.weight_learner.check_convergence()

            # Should converge toward positive weight for memory:tier2
            memory_weight = daemon.weight_learner.weights.get("memory:tier2", 0.5)
            assert memory_weight > 0.6  # Should have increased


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
