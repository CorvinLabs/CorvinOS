#!/usr/bin/env python3
"""Adversarial tests — Outcome Feedback (ADR-0317) Tier-3 security/boundary testing.

Targets the 4 attack vectors identified in dialectical reasoning:
1. Coupling: multi-outcome ambiguity for confidence attribution
2. Audit trail: hash-chain bypass, LoM spoofing, immutability violation
3. PII leakage: fingerprinting via aggregate stats, ID re-identification
4. EventStore integration: missing/stale dual-channel emission

Each test attempts to break a design constraint and verifies the constraint holds.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from core.learning.outcome_feedback import (
    OutcomeRecorder,
    OutcomeFeedbackStore,
    OutcomeType,
    OutcomeRecord,
)


class TestAttack1_CouplingAmbiguity:
    """Attack 1: Multi-outcome ambiguity for a single decision."""

    @pytest.fixture
    def store(self):
        """Create temporary store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            yield OutcomeFeedbackStore(db_path)

    def test_multiple_outcomes_per_decision_tracked_separately(self, store):
        """Multiple outcomes for one decision must each be retrievable."""
        recorder = OutcomeRecorder("_default")
        decision_id = "d1"

        # Record 3 outcomes for the same decision
        for i in range(3):
            outcome = recorder.record_outcome(
                decision_id=decision_id,
                session_id=f"session-{i}",
                outcome=OutcomeType.SUCCESS if i == 0 else OutcomeType.PARTIAL,
                quality_score=0.9 - (i * 0.1),
            )
            store.record_outcome(outcome)

        # All 3 must be retrievable
        outcomes = store.get_outcomes_by_decision(decision_id, tenant_id="_default")
        assert len(outcomes) == 3
        assert outcomes[0].quality_score == pytest.approx(0.9)
        assert outcomes[1].quality_score == pytest.approx(0.8)
        assert outcomes[2].quality_score == pytest.approx(0.7)

    def test_outcome_attribution_ambiguity_documented(self, store):
        """Design doc must clarify which outcome drives confidence backprop."""
        # This is an architectural question; the test documents the gap
        # TODO: Implement get_canonical_outcome() with timing window + attribution rules
        # For now, just verify multiple outcomes don't crash queries
        recorder = OutcomeRecorder("_default")

        for j in range(5):
            outcome = recorder.record_outcome(
                decision_id="d-ambig",
                session_id=f"session-{j}",
                outcome=OutcomeType.SUCCESS,
            )
            store.record_outcome(outcome)

        # All should be stored
        all_outcomes = store.get_outcomes_by_decision("d-ambig", tenant_id="_default")
        assert len(all_outcomes) == 5


class TestAttack2_AuditTrailBypass:
    """Attack 2: Attempt to bypass hash-chain or LoM binding."""

    @pytest.fixture
    def store(self):
        """Create temporary store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            yield OutcomeFeedbackStore(db_path)

    def test_hash_chain_is_computed_and_stored(self, store):
        """Hash-chain columns must be present and non-null after recording."""
        recorder = OutcomeRecorder("_default")
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="session-1",
            outcome=OutcomeType.SUCCESS,
        )
        store.record_outcome(outcome)

        # Query raw DB to verify hash columns exist
        with sqlite3.connect(store.db_path) as conn:
            cursor = conn.execute(
                "SELECT hash, prev_hash, lom FROM outcomes WHERE outcome_id = ?",
                (outcome.outcome_id,),
            )
            row = cursor.fetchone()

        assert row is not None
        hash_val, prev_hash, lom = row
        assert hash_val is not None  # Hash must be computed
        # First outcome may have None for prev_hash (chain root)
        assert lom is not None  # LoM must be captured

    def test_hash_chain_verification_detects_corruption(self, store):
        """Corrupting the chain must be detected by verify_chain()."""
        recorder = OutcomeRecorder("_default")

        # Record 2 outcomes to build a chain
        o1 = recorder.record_outcome(
            decision_id="d1",
            session_id="s1",
            outcome=OutcomeType.SUCCESS,
        )
        store.record_outcome(o1)

        o2 = recorder.record_outcome(
            decision_id="d2",
            session_id="s2",
            outcome=OutcomeType.FAILURE,
        )
        store.record_outcome(o2)

        # Verify chain is intact
        is_valid, msg = store.verify_chain("_default")
        assert is_valid

        # Now corrupt the chain by manually updating prev_hash
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE outcomes SET prev_hash = ? WHERE outcome_id = ?",
                ("corrupted_hash", o2.outcome_id),
            )
            conn.commit()

        # Verification should now fail
        is_valid, msg = store.verify_chain("_default")
        assert not is_valid, f"Chain should be broken: {msg}"

    def test_lom_captured_from_caller_context(self, store):
        """LoM must capture the caller's function/line for moral responsibility."""
        recorder = OutcomeRecorder("_default")
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="session-1",
            outcome=OutcomeType.SUCCESS,
        )
        store.record_outcome(outcome)

        # Query LoM
        with sqlite3.connect(store.db_path) as conn:
            cursor = conn.execute(
                "SELECT lom FROM outcomes WHERE outcome_id = ?",
                (outcome.outcome_id,),
            )
            lom = cursor.fetchone()[0]

        # LoM should contain filename:line:function
        assert lom is not None
        assert "test_outcome_feedback_tier3.py" in lom or "outcome_feedback.py" in lom
        assert ":" in lom  # Should have filename:line:func format

    def test_immutability_enforced_at_dataclass_level(self, store):
        """OutcomeRecord must be frozen (immutable)."""
        recorder = OutcomeRecorder("_default")
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="session-1",
            outcome=OutcomeType.SUCCESS,
        )

        # Attempt to mutate should fail
        with pytest.raises(AttributeError):
            outcome.outcome = OutcomeType.FAILURE  # type: ignore


class TestAttack3_PIILeakage:
    """Attack 3: Fingerprinting via aggregate stats and ID re-identification."""

    @pytest.fixture
    def store(self):
        """Create temporary store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            yield OutcomeFeedbackStore(db_path)

    def test_small_n_suppression_prevents_fingerprinting(self, store):
        """Success rate must be suppressed (return 0.5) when n < 10."""
        recorder = OutcomeRecorder("_default")

        # Record only 5 outcomes for a decision
        for i in range(5):
            outcome = recorder.record_outcome(
                decision_id="d-small-n",
                session_id=f"session-{i}",
                outcome=OutcomeType.SUCCESS if i < 3 else OutcomeType.FAILURE,
            )
            store.record_outcome(outcome)

        # Query success rate for this small-n set
        rate = store.compute_success_rate("_default", decision_ids=["d-small-n"])

        # Must return 0.5 (neutral) due to small-n suppression
        assert rate == 0.5, f"Expected 0.5 (suppressed), got {rate}"

    def test_csv_export_anonymizes_decision_ids(self, store):
        """CSV export must map decision_ids to anonymous integers (prevent fingerprinting)."""
        recorder = OutcomeRecorder("_default")

        # Record outcomes for 3 different decisions
        for j in range(3):
            for i in range(3):
                outcome = recorder.record_outcome(
                    decision_id=f"d-{j}",
                    session_id=f"session-{j}-{i}",
                    outcome=OutcomeType.SUCCESS,
                )
                store.record_outcome(outcome)

        # Export with anonymization
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "export_anon.csv"
            store.export_training_data_csv("_default", csv_path, anonymize_ids=True)

            with open(csv_path, "r") as f:
                content = f.read()

            # Verify decision_ids are replaced with integers (d-0, d-1, d-2 not in file)
            assert "d-0" not in content
            assert "d-1" not in content
            assert "d-2" not in content

            # But anonymized IDs (1, 2, 3) should be present
            assert "1" in content or "2" in content or "3" in content

    def test_csv_export_no_user_id_in_output(self, store):
        """CSV export must omit user_id (GDPR Art. 5 minimization)."""
        recorder = OutcomeRecorder("_default")

        # Record outcome with user_id
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="session-1",
            outcome=OutcomeType.SUCCESS,
            user_id="user-123",  # Include user_id in record
        )
        store.record_outcome(outcome)

        # Export
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "export.csv"
            store.export_training_data_csv("_default", csv_path)

            with open(csv_path, "r") as f:
                content = f.read()

            # User ID must NOT appear in export
            assert "user-123" not in content
            # Ids are anonymized to sequential integers by default
            # (anonymize_ids=True), so the raw outcome_id must NOT appear either.
            assert outcome.outcome_id not in content
            assert "d1" not in content.splitlines()[1:]  # decision_id anonymized too


class TestAttack4_EventStoreIntegration:
    """Attack 4: Missing or stale dual-channel emission to EventStore."""

    def test_outcome_loop_accepts_optional_event_store(self):
        """OutcomeFeedbackLoop must support optional EventStore parameter."""
        from core.learning.outcome_feedback import OutcomeFeedbackLoop

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            store = OutcomeFeedbackStore(db_path)

            # Should work without event_store
            loop1 = OutcomeFeedbackLoop("_default", store)
            assert loop1.event_store is None

            # Should work with event_store=None explicitly
            loop2 = OutcomeFeedbackLoop("_default", store, event_store=None)
            assert loop2.event_store is None

            # Should accept event_store parameter
            mock_event_store = object()
            loop3 = OutcomeFeedbackLoop("_default", store, event_store=mock_event_store)
            assert loop3.event_store is not None

    def test_outcome_loop_handles_missing_event_store_gracefully(self):
        """If EventStore is unavailable, feedback loop must NOT crash."""
        import asyncio

        from core.learning.outcome_feedback import OutcomeFeedbackLoop

        async def test_async():
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = Path(tmpdir) / "outcomes.db"
                store = OutcomeFeedbackStore(db_path)
                loop = OutcomeFeedbackLoop("_default", store, event_store=None)

                await loop.start()

                recorder = OutcomeRecorder("_default")
                outcome = recorder.record_outcome(
                    decision_id="d1",
                    session_id="session-1",
                    outcome=OutcomeType.SUCCESS,
                )

                # Should not crash even without EventStore
                await loop.emit_outcome(outcome)
                await loop.flush()
                await loop.stop()

                # Outcome should still be in local store
                stored = store.get_outcome(outcome.outcome_id, tenant_id="_default")
                assert stored is not None

        asyncio.run(test_async())


class TestThreadSafety:
    """Test race conditions in hash-chain computation and record_outcome."""

    @pytest.fixture
    def store(self):
        """Create temporary store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            yield OutcomeFeedbackStore(db_path)

    def test_concurrent_record_outcome_maintains_chain(self, store):
        """Recording outcomes from multiple threads must maintain hash-chain integrity."""
        recorder = OutcomeRecorder("_default")
        outcome_ids = []
        lock = threading.Lock()

        def record_outcome_thread(index):
            outcome = recorder.record_outcome(
                decision_id=f"d-{index}",
                session_id=f"session-{index}",
                outcome=OutcomeType.SUCCESS,
            )
            outcome_id = store.record_outcome(outcome)
            with lock:
                outcome_ids.append(outcome_id)

        # Spawn 10 threads to record outcomes concurrently
        threads = []
        for i in range(10):
            t = threading.Thread(target=record_outcome_thread, args=(i,))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        assert len(outcome_ids) == 10

        # Verify chain is still intact
        is_valid, msg = store.verify_chain("_default")
        assert is_valid, f"Chain should remain intact after concurrent writes: {msg}"


# Entry point for manual testing
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
