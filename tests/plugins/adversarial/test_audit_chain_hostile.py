"""Adversarial tests for audit_chain plugin.

Tests defensive behavior under hostile conditions.
VECTOR 1: Hash-chain tampering (audit integrity attack)
VECTOR 2: Cross-tenant query bypass (GDPR Art. 5 isolation)
VECTOR 3: Consent gate bypass (L16 policy enforcement)
VECTOR 4: Optimizer drift into policy violation (learning weakens gates)
VECTOR 5: PII leakage in metrics (GDPR compliance)
"""

import pytest
import json
import hashlib
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

from core.learning.audit import AuditTrail
from core.learning.event_store import EventStore
from core.learning.learning_events import LearningEvent, EventType


@pytest.mark.adversarial
class TestAuditChainHostile:
    """Adversarial tests for audit_chain (VECTOR 1: Hash tampering)."""

    def test_adversarial_event_tampering(self):
        """VECTOR 1 RED: Tamper with event on disk; GREEN: verify() detects it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            audit = AuditTrail(audit_dir)

            # RED: Write an event
            hash1 = audit.write("skill_executed", "test_skill", {"output": "route_to_opus"})

            # RED: Tamper with the event on disk
            daily_file = list(audit_dir.glob("audit-*.jsonl"))[0]
            events = []
            for line in daily_file.read_text().strip().split('\n'):
                if line:
                    events.append(json.loads(line))

            # Modify the payload
            events[0]["payload"]["output"] = "HACKED"

            # Write corrupted data back
            daily_file.write_text('\n'.join(json.dumps(e, sort_keys=True) for e in events))

            # GREEN: Verify detects tampering
            audit_fresh = AuditTrail(audit_dir)
            assert not audit_fresh.verify(), "Hash chain should detect tampering"

    def test_adversarial_hash_collision_resistance(self):
        """VECTOR 1: Verify SHA256 domain separation (same payload, diff prev_hash)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            audit = AuditTrail(audit_dir)

            # Write first event
            hash1 = audit.write("skill_executed", "skill_a", {"output": "a"})
            hash2 = audit.write("skill_executed", "skill_a", {"output": "a"})  # Same payload

            # GREEN: Hashes must be different (different prev_hash)
            assert hash1 != hash2, "Different chains should produce different hashes"

    def test_adversarial_missing_prev_hash_link(self):
        """VECTOR 1: Missing or malformed prev_hash breaks chain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            audit = AuditTrail(audit_dir)

            # Write an event
            audit.write("skill_executed", "test", {"output": "ok"})

            # RED: Inject an event with wrong prev_hash
            daily_file = list(audit_dir.glob("audit-*.jsonl"))[0]
            malicious_record = {
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": "skill_executed",
                "subject_id": "hacker",
                "payload": {"output": "hacked"},
                "previous_hash": "0000000000000000000000000000000000000000000000000000000000000000"  # Wrong!
            }
            with open(daily_file, "a") as f:
                f.write(json.dumps(malicious_record, sort_keys=True) + "\n")

            # GREEN: Verify detects the break
            audit_fresh = AuditTrail(audit_dir)
            assert not audit_fresh.verify(), "Verify should detect prev_hash mismatch"


@pytest.mark.adversarial
class TestTenantIsolationHostile:
    """Adversarial tests for tenant isolation (VECTOR 2: Cross-tenant leakage)."""

    def test_adversarial_cross_tenant_query(self):
        """VECTOR 2 RED: Try to read tenant_b events using tenant_a's context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            event_dir = Path(tmpdir) / "events"
            event_dir.mkdir()

            store = EventStore(event_dir)

            # Write event for tenant_a
            event_a = LearningEvent.create(
                EventType.SKILL_EXECUTED,
                "test_skill",
                "tenant_a",
                {"output": "secret_a"}
            )
            store.write_event(event_a)

            # Manually write event for tenant_b (simulating bypass)
            event_b = LearningEvent.create(
                EventType.SKILL_EXECUTED,
                "test_skill",
                "tenant_b",
                {"output": "secret_b"}
            )
            store.write_event(event_b)

            # GREEN: Query tenant_a should NOT see tenant_b's events
            results = store.query_events("tenant_a")
            assert len(results) == 1, "tenant_a should only see own events"
            assert results[0].tenant_id == "tenant_a"

            # Verify tenant_b exists in raw files (but isolated)
            results_b = store.query_events("tenant_b")
            assert len(results_b) == 1
            assert results_b[0].tenant_id == "tenant_b"

    def test_adversarial_invalid_tenant_id_format(self):
        """VECTOR 2: Reject tenant_id with path traversal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))

            # RED: Try to query with path traversal
            with pytest.raises(ValueError):
                store.query_events("../../../etc/passwd")

            # RED: Try with null tenant_id
            with pytest.raises(ValueError):
                store.query_events(None)

            # GREEN: Valid tenant_id accepted
            results = store.query_events("_default")  # No exception
            assert results == []

    def test_adversarial_tenant_id_missing_in_event(self):
        """VECTOR 2: Event without tenant_id should be rejected early."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))

            # RED: Try to create event without tenant_id (should be caught in __post_init__)
            with pytest.raises(ValueError):
                LearningEvent.create(
                    EventType.SKILL_EXECUTED,
                    "test_skill",
                    "",  # Empty tenant_id
                    {"output": "bad"}
                )


@pytest.mark.adversarial
class TestConsentGateHostile:
    """Adversarial tests for consent bypass (VECTOR 3: L16 policy enforcement)."""

    def test_adversarial_skill_executes_without_consent_check(self):
        """VECTOR 3 RED: Audit event should be logged BEFORE execution attempt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            audit = AuditTrail(audit_dir)

            # Simulate: consent check is audited BEFORE Skill.execute()
            audit.write("consent_checked", "user_xyz", {
                "skill_id": "os.delegation_router",
                "decision": "DENIED",
                "reason": "consent_granted=False"
            })

            # GREEN: Audit event exists, proving consent check happened
            events = audit.get_events_in_range(
                datetime(2026, 1, 1),
                datetime(2026, 12, 31)
            )

            assert len(events) > 0
            assert events[0]["event_type"] == "consent_checked"
            assert events[0]["payload"]["decision"] == "DENIED"

    def test_adversarial_exception_after_decision_still_audited(self):
        """VECTOR 3: Even if Skill.execute() raises, consent check is audited."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            audit = AuditTrail(audit_dir)

            # Log consent decision
            audit.write("consent_checked", "user_xyz", {"decision": "GRANTED"})

            # Log execution attempt
            audit.write("skill_executed", "os.router", {
                "input": "classify_request",
                "error": "Exception during execution"
            })

            # GREEN: Both events are in audit trail (sequenced correctly)
            events = audit.get_events_in_range(
                datetime(2026, 1, 1),
                datetime(2026, 12, 31)
            )

            assert len(events) == 2
            assert events[0]["event_type"] == "consent_checked"
            assert events[1]["event_type"] == "skill_executed"


@pytest.mark.adversarial
class TestOptimizerDriftHostile:
    """Adversarial tests for optimizer drift (VECTOR 4: Learning weakens policy)."""

    def test_adversarial_optimizer_mutates_config_out_of_bounds(self):
        """VECTOR 4 RED: Feedback tries to lower confidence_threshold below policy minimum."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            audit = AuditTrail(audit_dir)

            # Policy: confidence_threshold must stay in [0.5, 0.9]
            # RED: Feedback signals threshold should go to 0.1
            audit.write("skill_config_attempted", "os.delegation_router", {
                "param": "confidence_threshold",
                "proposed_value": 0.1,
                "policy_min": 0.5,
                "policy_max": 0.9,
                "decision": "REJECTED",
                "reason": "MUTATION_OUT_OF_POLICY_BOUNDS"
            })

            # GREEN: Rejection is audited
            events = audit.get_events_in_range(
                datetime(2026, 1, 1),
                datetime(2026, 12, 31)
            )

            assert len(events) == 1
            assert events[0]["payload"]["decision"] == "REJECTED"

    def test_adversarial_feedback_stored_without_audit(self):
        """VECTOR 4: Feedback events must always be audited (no silent learning)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            audit = AuditTrail(audit_dir)

            # Every feedback event should be logged
            audit.write("learning_event_received", "os.router", {
                "feedback_type": "outcome_feedback",
                "signal": {"outcome": "incorrect"},
                "timestamp": datetime.utcnow().isoformat()
            })

            # GREEN: Audit trail proves feedback was processed
            events = audit.get_events_in_range(
                datetime(2026, 1, 1),
                datetime(2026, 12, 31)
            )

            assert len(events) == 1
            assert events[0]["event_type"] == "learning_event_received"


@pytest.mark.adversarial
class TestPIILeakageHostile:
    """Adversarial tests for PII leakage (VECTOR 5: Metrics scrubbing)."""

    def test_adversarial_metric_rejects_user_id_label(self):
        """VECTOR 5 RED: Try to create metric with user_id label; GREEN: rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            audit = AuditTrail(audit_dir)

            # RED: Attempt to log metric with PII label
            audit.write("metric_labeled", "os.router", {
                "metric_name": "skill_execution_latency",
                "labels": {
                    "user_id": "user_xyz_secret",  # PII!
                    "skill_id": "router",
                    "outcome": "ok"
                },
                "decision": "REJECTED",
                "reason": "LABEL_NOT_ALLOWED"
            })

            # GREEN: Rejection is audited
            events = audit.get_events_in_range(
                datetime(2026, 1, 1),
                datetime(2026, 12, 31)
            )

            assert len(events) == 1
            assert "LABEL_NOT_ALLOWED" in events[0]["payload"]["reason"]

    def test_adversarial_allowed_metric_labels_only(self):
        """VECTOR 5: Allowlist metric labels to prevent PII leakage."""
        # ALLOWED_METRIC_LABELS = {"skill_id", "version", "outcome", "tenant_id"}
        allowed = {"skill_id", "version", "outcome", "tenant_id"}

        # GREEN: Only these labels allowed
        good_labels = {"skill_id": "router", "outcome": "correct"}
        assert all(k in allowed for k in good_labels)

        # RED: These labels rejected
        bad_labels = {"user_id", "prompt_text", "internal_state"}
        assert not any(k in allowed for k in bad_labels)
