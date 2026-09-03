"""Adversarial tests for Outcome Feedback (ADR-0317) — security & compliance hardening."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.learning.outcome_feedback import (
    OutcomeType,
    OutcomeRecord,
    OutcomeRecorder,
    OutcomeFeedbackStore,
)


class TestPIISafeguards:
    """Adversarial: Test that PII safeguards actually work."""

    @pytest.fixture
    def store(self):
        """Create temporary store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            yield OutcomeFeedbackStore(db_path)

    def test_small_n_suppression_prevents_fingerprinting(self, store):
        """Small-n suppression (N<10) prevents fingerprinting attacks.

        **Attack Scenario:** Attacker observes success_rate() calls with
        different decision_ids and tries to infer user behavior from rate
        precision (e.g., 6/10 = 0.6 vs 5/9 ≈ 0.556).

        **Defense:** Return 0.5 (neutral) when N < 10, making all small
        samples indistinguishable.
        """
        recorder = OutcomeRecorder("tenant-1")

        # Record only 3 outcomes (N < 10)
        for i in range(3):
            outcome = recorder.record_outcome(
                decision_id=f"d{i}",
                session_id="s1",
                outcome=OutcomeType.SUCCESS,
            )
            store.record_outcome(outcome)

        rate = store.compute_success_rate("tenant-1")
        assert rate == 0.5, "N<10 must return suppressed value (0.5)"

    def test_small_n_suppression_with_filter(self, store):
        """Small-n suppression works even with decision_ids filter."""
        recorder = OutcomeRecorder("tenant-1")

        # Record 5 outcomes
        for i in range(5):
            outcome = recorder.record_outcome(
                decision_id=f"d{i}",
                session_id="s1",
                outcome=OutcomeType.SUCCESS,
            )
            store.record_outcome(outcome)

        # Query with filter (should still be suppressed if result N < 10)
        rate = store.compute_success_rate(
            "tenant-1", decision_ids=[f"d{i}" for i in range(3)]
        )
        assert rate == 0.5, "Filtered result N<10 must also be suppressed"

    def test_success_rate_unlocked_at_threshold(self, store):
        """Success rate computed when N >= 10 (suppression lifted)."""
        recorder = OutcomeRecorder("tenant-1")

        # Record exactly 10 outcomes (6 success, 4 failure)
        for i in range(10):
            outcome = recorder.record_outcome(
                decision_id=f"d{i}",
                session_id="s1",
                outcome=OutcomeType.SUCCESS if i < 6 else OutcomeType.FAILURE,
            )
            store.record_outcome(outcome)

        rate = store.compute_success_rate("tenant-1")
        assert rate == 0.6, "N>=10 should return actual rate (0.6), not suppressed"

    def test_csv_export_anonymization_maps_ids(self, store):
        """Decision IDs anonymized to sequential integers in export.

        **Attack Scenario:** Attacker steals CSV export and correlates
        decision_ids with external data to re-identify users.

        **Defense:** Map decision_ids to sequential integers (1, 2, 3...)
        so external correlation is impossible.
        """
        recorder = OutcomeRecorder("tenant-1")

        # Record outcomes for 3 different decisions
        decision_ids = ["d-secret-key-123", "d-password-456", "d-token-789"]
        for decision_id in decision_ids:
            outcome = recorder.record_outcome(
                decision_id=decision_id,
                session_id="s1",
                outcome=OutcomeType.SUCCESS,
            )
            store.record_outcome(outcome)

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "outcomes.csv"
            store.export_training_data_csv("tenant-1", csv_path, anonymize_ids=True)

            with open(csv_path, "r") as f:
                content = f.read()

            # Verify: no original decision_ids in export
            for decision_id in decision_ids:
                assert decision_id not in content, (
                    f"Original decision_id {decision_id} leaked in export!"
                )

            # Verify: anonymized IDs are present
            assert "decision_id_anonymous" in content
            assert "1" in content and "2" in content and "3" in content

    def test_csv_export_no_user_ids(self, store):
        """User IDs never appear in export (GDPR Art. 5 minimization)."""
        recorder = OutcomeRecorder("tenant-1")

        for i in range(3):
            outcome = recorder.record_outcome(
                decision_id=f"d{i}",
                session_id="s1",
                outcome=OutcomeType.SUCCESS,
                user_id="user-secret-123",  # Should be excluded
            )
            store.record_outcome(outcome)

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "outcomes.csv"
            store.export_training_data_csv("tenant-1", csv_path)

            with open(csv_path, "r") as f:
                content = f.read()

            assert "user-secret-123" not in content, "User IDs must not appear in export"
            assert "user_id" not in content.split("\n")[2], "user_id column should not exist"


class TestSecretRedaction:
    """Adversarial: Test that secret redaction actually works."""

    @pytest.fixture
    def recorder(self):
        """Create recorder instance."""
        return OutcomeRecorder("tenant-1")

    def test_redact_api_keys(self, recorder):
        """API keys are redacted from feedback."""
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="s1",
            outcome=OutcomeType.SUCCESS,
            feedback_text="Called with api_key=sk_live_1234567890abcdef",
        )

        assert outcome.feedback_text == "[redacted]"

    def test_redact_bearer_tokens(self, recorder):
        """Bearer tokens are redacted."""
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="s1",
            outcome=OutcomeType.SUCCESS,
            feedback_text="Authenticated: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        )

        assert outcome.feedback_text == "[redacted]"

    def test_redact_hex_blobs(self, recorder):
        """Long hex strings (MD5+ length) are redacted."""
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="s1",
            outcome=OutcomeType.SUCCESS,
            feedback_text="Hash: 5f4dcc3b5aa765d61d8327deb882cf99",  # 32-char hex
        )

        assert outcome.feedback_text == "[redacted]"

    def test_safe_feedback_not_redacted(self, recorder):
        """Safe feedback is not redacted."""
        safe_text = "Decision was fast and accurate, 95% confident"
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="s1",
            outcome=OutcomeType.SUCCESS,
            feedback_text=safe_text,
        )

        assert outcome.feedback_text == safe_text

    def test_redaction_case_insensitive(self, recorder):
        """Secret redaction is case-insensitive."""
        for secret_phrase in ["API_KEY=secret", "Api_Key=secret", "api_key=secret"]:
            outcome = recorder.record_outcome(
                decision_id="d1",
                session_id="s1",
                outcome=OutcomeType.SUCCESS,
                feedback_text=f"Config: {secret_phrase}",
            )
            assert outcome.feedback_text == "[redacted]", f"Failed for: {secret_phrase}"


class TestGDPRErasure:
    """Adversarial: Test GDPR Art. 17 (Right to Erasure) implementation."""

    @pytest.fixture
    def store(self):
        """Create temporary store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            yield OutcomeFeedbackStore(db_path)

    def test_delete_user_outcomes_complete(self, store):
        """Deleting user data removes ALL outcomes for that user."""
        recorder = OutcomeRecorder("tenant-1")

        # Record 10 outcomes for user-1
        for i in range(10):
            outcome = recorder.record_outcome(
                decision_id=f"d{i}",
                session_id=f"s{i}",
                outcome=OutcomeType.SUCCESS if i % 2 == 0 else OutcomeType.FAILURE,
                user_id="user-1",
            )
            store.record_outcome(outcome)

        # Verify outcomes exist
        all_outcomes = store.get_outcomes_by_type("tenant-1", OutcomeType.SUCCESS)
        assert any(o.user_id == "user-1" for o in all_outcomes), "User outcomes should exist"

        # Delete user-1
        deleted = store.delete_user_outcomes("tenant-1", "user-1")
        assert deleted == 10, "All 10 outcomes should be deleted"

        # Verify erasure is complete
        remaining = store.get_outcomes_by_type("tenant-1", OutcomeType.SUCCESS)
        assert not any(
            o.user_id == "user-1" for o in remaining
        ), "No outcomes should remain for user-1"

    def test_delete_user_preserves_others(self, store):
        """Deleting user-1 doesn't affect user-2's data."""
        recorder = OutcomeRecorder("tenant-1")

        # Record outcomes for two users
        for user_id in ["user-1", "user-2"]:
            for i in range(5):
                outcome = recorder.record_outcome(
                    decision_id=f"d-{user_id}-{i}",
                    session_id="s1",
                    outcome=OutcomeType.SUCCESS,
                    user_id=user_id,
                )
                store.record_outcome(outcome)

        # Delete user-1
        store.delete_user_outcomes("tenant-1", "user-1")

        # Verify user-2 data intact
        outcomes = store.get_outcomes_by_type("tenant-1", OutcomeType.SUCCESS)
        assert all(o.user_id == "user-2" for o in outcomes), "user-2 data must be preserved"

    def test_delete_user_wrong_tenant_no_effect(self, store):
        """Deleting user from wrong tenant has no effect (tenant isolation)."""
        recorder = OutcomeRecorder("tenant-1")

        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="s1",
            outcome=OutcomeType.SUCCESS,
            user_id="user-1",
        )
        store.record_outcome(outcome)

        # Try to delete user-1 from wrong tenant (should be no-op)
        deleted = store.delete_user_outcomes("tenant-2", "user-1")
        assert deleted == 0, "Wrong tenant deletion must not affect other tenants"

        # Verify user-1 data still exists in tenant-1
        retrieved = store.get_outcome(outcome.outcome_id)
        assert retrieved is not None


class TestHashChainIntegrity:
    """Adversarial: Test that hash-chain tampering is detected."""

    @pytest.fixture
    def store(self):
        """Create temporary store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            yield OutcomeFeedbackStore(db_path)

    def test_verify_chain_detects_broken_link(self, store):
        """verify_chain() detects tampering (broken hash link)."""
        recorder = OutcomeRecorder("tenant-1")

        # Record 3 outcomes
        for i in range(3):
            outcome = recorder.record_outcome(
                decision_id=f"d{i}",
                session_id="s1",
                outcome=OutcomeType.SUCCESS,
            )
            store.record_outcome(outcome)

        # Verify chain is intact
        is_valid, msg = store.verify_chain("tenant-1")
        assert is_valid, f"Chain should be valid initially: {msg}"

        # Tamper with the middle outcome's prev_hash
        import sqlite3

        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE outcomes SET prev_hash = 'tampered' WHERE outcome_id IN (SELECT outcome_id FROM outcomes ORDER BY timestamp_utc LIMIT 1 OFFSET 1)"
            )
            conn.commit()

        # Verify chain now detects tampering
        is_valid, msg = store.verify_chain("tenant-1")
        assert (
            not is_valid
        ), "verify_chain() should detect tampering and return False"

    def test_first_outcome_must_have_null_prev_hash(self, store):
        """First outcome must have prev_hash=None (chain root)."""
        recorder = OutcomeRecorder("tenant-1")

        outcome = recorder.record_outcome(
            decision_id="d1", session_id="s1", outcome=OutcomeType.SUCCESS
        )
        store.record_outcome(outcome)

        # Manually check the stored prev_hash
        import sqlite3

        with sqlite3.connect(store.db_path) as conn:
            cursor = conn.execute(
                "SELECT prev_hash FROM outcomes WHERE outcome_id = ? ORDER BY timestamp_utc LIMIT 1",
                (outcome.outcome_id,),
            )
            row = cursor.fetchone()

        assert row[0] is None, "First outcome's prev_hash must be None"


class TestTenantIsolation:
    """Adversarial: Test that cross-tenant leakage is impossible."""

    @pytest.fixture
    def store(self):
        """Create temporary store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            yield OutcomeFeedbackStore(db_path)

    def test_tenant_id_required_fail_closed(self, store):
        """Recording outcome without tenant_id must fail (fail-closed)."""
        # Create outcome with empty tenant_id
        with pytest.raises(ValueError, match="tenant_id required"):
            outcome = OutcomeRecord(
                outcome_id="o1",
                decision_id="d1",
                session_id="s1",
                outcome=OutcomeType.SUCCESS,
                timestamp_utc=datetime.utcnow(),
                tenant_id="",  # Empty!
            )
            store.record_outcome(outcome)

    def test_queries_filtered_by_tenant(self, store):
        """Queries automatically filter by tenant_id (no cross-tenant leakage)."""
        recorder1 = OutcomeRecorder("tenant-1")
        recorder2 = OutcomeRecorder("tenant-2")

        # Record outcomes in both tenants
        for i in range(3):
            outcome1 = recorder1.record_outcome(
                decision_id=f"d{i}",
                session_id="s1",
                outcome=OutcomeType.SUCCESS,
            )
            store.record_outcome(outcome1)

            outcome2 = recorder2.record_outcome(
                decision_id=f"d{i}",
                session_id="s1",
                outcome=OutcomeType.FAILURE,
            )
            store.record_outcome(outcome2)

        # Query tenant-1 (should get 3 SUCCESS, no FAILURE)
        outcomes_t1 = store.get_outcomes_by_type("tenant-1", OutcomeType.SUCCESS)
        assert len(outcomes_t1) == 3
        assert all(o.outcome == OutcomeType.SUCCESS for o in outcomes_t1)
        assert all(o.tenant_id == "tenant-1" for o in outcomes_t1)

        # Query tenant-2 (should get 3 FAILURE, no SUCCESS)
        outcomes_t2 = store.get_outcomes_by_type("tenant-2", OutcomeType.FAILURE)
        assert len(outcomes_t2) == 3
        assert all(o.outcome == OutcomeType.FAILURE for o in outcomes_t2)
        assert all(o.tenant_id == "tenant-2" for o in outcomes_t2)

    def test_success_rate_tenant_scoped(self, store):
        """Success rate computed per tenant (no cross-contamination)."""
        recorder1 = OutcomeRecorder("tenant-1")
        recorder2 = OutcomeRecorder("tenant-2")

        # tenant-1: all successes (10)
        for i in range(10):
            outcome = recorder1.record_outcome(
                decision_id=f"d{i}",
                session_id="s1",
                outcome=OutcomeType.SUCCESS,
            )
            store.record_outcome(outcome)

        # tenant-2: all failures (10)
        for i in range(10):
            outcome = recorder2.record_outcome(
                decision_id=f"d{i}",
                session_id="s1",
                outcome=OutcomeType.FAILURE,
            )
            store.record_outcome(outcome)

        # Verify rates are tenant-scoped
        rate_t1 = store.compute_success_rate("tenant-1")
        rate_t2 = store.compute_success_rate("tenant-2")

        assert rate_t1 == 1.0, "tenant-1 should have 100% success"
        assert rate_t2 == 0.0, "tenant-2 should have 0% success"
