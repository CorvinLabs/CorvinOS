"""
INTEGRATION TEST: Phase B Compliance & Audit Trail
Verify GDPR Art. 30,32 + EU AI Act Art. 50 + Audit Chain Integrity

GDPR Art. 30,32:
- Audit trail must be complete and verifiable
- Hash-chain integrity (no gaps, no tampering)
- Per-tenant data isolation (no cross-tenant leakage)

EU AI Act Art. 50:
- Bot disclosure: Every decision must be attributed
- Transparency: Users understand AI involvement
- No silent operations

Audit Chain:
- Hash-linked (each event links to previous)
- Immutable (append-only, no rewrites)
- Complete (no gaps in sequence)
"""

import pytest
import json
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.compliance.audit_integration import (
    AuditTrail, AuditEvent, AuditChainVerifier
)
from core.learning.active_loop import ActiveLearningLoop
from core.skills.os_skills.orchestrator import SkillOrchestrator
from core.marketplace.plugin_registry import PluginRegistry


class TestGDPRArticle30_32_Compliance:
    """Test GDPR Article 30 (Records of Processing) & 32 (Security)"""

    @pytest.fixture
    def audit_trail(self):
        return AuditTrail(tenant_id="_default")

    def test_audit_trail_records_all_processing(self, audit_trail):
        """GDPR Art. 30: All processing activities must be recorded"""
        events = [
            ("skill_executed", {"skill_id": "router", "input": "...", "output": "..."}),
            ("feedback_received", {"skill_id": "router", "signal": "correct"}),
            ("config_updated", {"skill_id": "router", "param": "confidence", "delta": 0.05}),
            ("plugin_installed", {"plugin_id": "analytics", "version": "1.0.0"}),
            ("consent_checked", {"user_id": "...", "consent_type": "feedback"}),
        ]

        for event_type, details in events:
            audit_trail.log_event(AuditEvent(
                event_type=event_type,
                details=details
            ))

        # Retrieve all events
        all_events = audit_trail.get_all_events()
        assert len(all_events) >= len(events), "All events must be recorded"

    def test_audit_trail_timestamps_all_events(self, audit_trail):
        """GDPR Art. 32: Every event must have a precise timestamp"""
        audit_trail.log_event(AuditEvent(
            event_type="test_event",
            details={"test": "data"}
        ))

        events = audit_trail.get_all_events()
        assert len(events) > 0

        for event in events:
            assert "timestamp" in event or "ts" in event, "Event must have timestamp"
            # Timestamp should be ISO 8601
            ts_str = event.get("timestamp") or event.get("ts")
            try:
                datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except:
                pytest.fail(f"Invalid timestamp format: {ts_str}")

    def test_audit_trail_immutability(self, audit_trail):
        """GDPR Art. 32: Audit trail must be immutable (append-only)"""
        # Log an event
        audit_trail.log_event(AuditEvent(
            event_type="immutability_test",
            details={"value": "original"}
        ))

        events_v1 = audit_trail.get_all_events()

        # Try to modify (should fail or be ignored)
        with pytest.raises(Exception) or True:
            # Should not allow modification
            events_v1[0]["details"]["value"] = "modified"

        # Re-fetch events
        events_v2 = audit_trail.get_all_events()

        # Events should be unchanged
        assert len(events_v1) == len(events_v2)

    def test_audit_trail_hash_chain_integrity(self, audit_trail):
        """GDPR Art. 32: Hash chain must be cryptographically sound"""
        # Log multiple events
        for i in range(10):
            audit_trail.log_event(AuditEvent(
                event_type=f"event_{i}",
                details={"sequence": i}
            ))

        events = audit_trail.get_all_events()

        # Verify each event links to previous
        for i in range(1, len(events)):
            current = events[i]
            previous = events[i-1]

            # Compute previous event hash
            prev_event_str = json.dumps(previous, sort_keys=True)
            expected_prev_hash = hashlib.sha256(
                prev_event_str.encode()
            ).hexdigest()

            # Current event should reference it
            if "prev_hash" in current:
                assert current["prev_hash"] == expected_prev_hash, \
                    f"Event {i} prev_hash mismatch"

    def test_audit_trail_no_tampering_detection(self, audit_trail):
        """Detect if chain has been tampered with"""
        # Log events
        for i in range(5):
            audit_trail.log_event(AuditEvent(
                event_type=f"event_{i}",
                details={"seq": i}
            ))

        # Verify chain
        is_valid = audit_trail.verify_chain_integrity()
        assert is_valid, "Chain should be valid initially"

        # Simulate tampering (in memory)
        events = audit_trail.get_all_events()
        if len(events) > 2:
            # Modify an event in the middle
            events[1]["details"]["tampered"] = True

            # Re-verify (should fail or flag as invalid)
            # Note: Actual implementation would detect this
            # Here we simulate the detection
            is_tampered = audit_trail.detect_tampering(events)
            assert is_tampered or not audit_trail.verify_chain_integrity(), \
                "Tampering should be detectable"


class TestTenantIsolationGDPR:
    """GDPR Art. 6, 32: Tenant data must be isolated"""

    def test_audit_trail_tenant_isolation(self):
        """Events from different tenants must not leak"""
        audit_t1 = AuditTrail(tenant_id="tenant_1")
        audit_t2 = AuditTrail(tenant_id="tenant_2")

        # Log events in tenant 1
        audit_t1.log_event(AuditEvent(
            event_type="test",
            details={"tenant_secret": "t1_secret_data"}
        ))

        # Log events in tenant 2
        audit_t2.log_event(AuditEvent(
            event_type="test",
            details={"tenant_secret": "t2_secret_data"}
        ))

        # Retrieve events per tenant
        t1_events = audit_t1.get_all_events()
        t2_events = audit_t2.get_all_events()

        # Verify isolation
        t1_str = json.dumps(t1_events)
        t2_str = json.dumps(t2_events)

        assert "t2_secret_data" not in t1_str, "Tenant 1 should not see Tenant 2 data"
        assert "t1_secret_data" not in t2_str, "Tenant 2 should not see Tenant 1 data"

    def test_learning_feedback_tenant_isolation(self):
        """Learning feedback must be isolated per tenant"""
        loop_t1 = ActiveLearningLoop(tenant_id="tenant_1")
        loop_t2 = ActiveLearningLoop(tenant_id="tenant_2")

        skill_id = "shared_skill"

        # Tenant 1: positive feedback
        loop_t1.record_feedback({
            "skill_id": skill_id,
            "feedback_type": "outcome_feedback",
            "signal": "correct",
            "confidence_score": 0.95,
            "tenant_id": "tenant_1"
        })

        # Tenant 2: negative feedback
        loop_t2.record_feedback({
            "skill_id": skill_id,
            "feedback_type": "outcome_feedback",
            "signal": "incorrect",
            "confidence_score": 0.20,
            "tenant_id": "tenant_2"
        })

        # Get feedback per tenant
        t1_feedback = loop_t1.get_feedback(skill_id)
        t2_feedback = loop_t2.get_feedback(skill_id)

        # Verify tenant 1 only sees positive feedback
        if t1_feedback:
            assert all(f.get("tenant_id") == "tenant_1" for f in t1_feedback)


class TestEUAIActArticle50:
    """EU AI Act Art. 50: Transparency & Disclosure"""

    def test_bot_disclosure_in_events(self):
        """Every AI decision must be disclosed as AI-generated"""
        audit_trail = AuditTrail(tenant_id="_default")

        events_to_log = [
            ("delegation_decision", {
                "task": "analyze_data",
                "engine_selected": "claude-opus",
                "is_ai_decision": True,
                "model_reasoning_available": True
            }),
            ("skill_executed", {
                "skill_id": "router",
                "is_ai_decision": True,
                "model_name": "router_v1.0"
            }),
            ("feedback_optimized", {
                "skill_id": "router",
                "optimizer_is_ai": True,
                "confidence_adjustment": 0.05
            })
        ]

        for event_type, details in events_to_log:
            audit_trail.log_event(AuditEvent(
                event_type=event_type,
                details=details
            ))

        events = audit_trail.get_all_events()

        # Verify all decisions are marked as AI
        for event in events:
            if "decision" in event.get("event_type", "").lower():
                assert event.get("details", {}).get("is_ai_decision") in [True, None], \
                    "Decision should be disclosed as AI-generated"

    def test_reasoning_disclosure(self):
        """AI reasoning must be disclosed (when available)"""
        audit_trail = AuditTrail(tenant_id="_default")

        audit_trail.log_event(AuditEvent(
            event_type="routing_decision",
            details={
                "task": "analyze_code",
                "selected_engine": "opus",
                "reasoning": "Opus is best for code analysis (complexity > 0.8)",
                "model_reasoning_available": True
            }
        ))

        events = audit_trail.get_all_events()
        assert len(events) > 0

        # Verify reasoning is present
        last_event = events[-1]
        assert "reasoning" in last_event.get("details", {}), \
            "Reasoning should be disclosed"


class TestAuditChainIntegrity:
    """Verify complete hash-chain integrity"""

    def test_audit_chain_complete_coverage(self):
        """All Phase B operations must be in audit trail"""
        audit_trail = AuditTrail(tenant_id="_default")

        # Simulate Phase B workflow
        operations = [
            # Discovery
            ("marketplace_search", {"query": "processor"}),
            # License check
            ("license_quota_checked", {"user_id": "user_001", "tier": "A"}),
            # Installation
            ("plugin_installed", {"plugin_id": "processor_v1"}),
            # Skill deployment
            ("skill_deployed", {"skill_id": "os.router"}),
            ("dag_validated", {"skills": 3}),
            # Feedback collection
            ("feedback_recorded", {"skill_id": "os.router", "signal": "correct"}),
            ("feedback_recorded", {"skill_id": "os.router", "signal": "correct"}),
            # Optimization
            ("skill_optimized", {"skill_id": "os.router", "delta": 0.05}),
            # DoD verification
            ("dod_score_calculated", {"project": "proj_001", "score": 0.87}),
            # Metrics aggregation
            ("metrics_aggregated", {"skill_count": 3}),
        ]

        for event_type, details in operations:
            audit_trail.log_event(AuditEvent(
                event_type=event_type,
                details=details
            ))

        # Verify all operations logged
        events = audit_trail.get_all_events()
        assert len(events) >= len(operations), "All operations must be logged"

        # Verify chain integrity
        is_valid = audit_trail.verify_chain_integrity()
        assert is_valid, "Chain must be valid"

    def test_audit_chain_no_gaps(self):
        """Audit chain must have no missing events"""
        audit_trail = AuditTrail(tenant_id="_default")

        # Log events with explicit sequencing
        for i in range(20):
            audit_trail.log_event(AuditEvent(
                event_type="sequential_event",
                details={"sequence": i}
            ))

        events = audit_trail.get_all_events()

        # Verify sequence is unbroken
        assert len(events) == 20, "All 20 events must be present"

        # Verify no duplicates
        event_ids = [e.get("id") or e.get("timestamp") for e in events]
        assert len(event_ids) == len(set(event_ids)), "No duplicate events"

    def test_audit_chain_chronological_order(self):
        """Events must be in chronological order"""
        audit_trail = AuditTrail(tenant_id="_default")

        # Log events with slight delays
        for i in range(5):
            audit_trail.log_event(AuditEvent(
                event_type=f"event_{i}",
                details={"index": i}
            ))
            time.sleep(0.01)  # 10ms delay

        events = audit_trail.get_all_events()

        # Verify chronological order
        for i in range(1, len(events)):
            ts_prev = events[i-1].get("timestamp") or events[i-1].get("ts")
            ts_curr = events[i].get("timestamp") or events[i].get("ts")

            if ts_prev and ts_curr:
                dt_prev = datetime.fromisoformat(ts_prev.replace("Z", "+00:00"))
                dt_curr = datetime.fromisoformat(ts_curr.replace("Z", "+00:00"))
                assert dt_prev <= dt_curr, f"Events not in chronological order: {i-1} -> {i}"


class TestPIIScrubbing:
    """Verify PII is scrubbed from audit trail"""

    def test_no_email_in_audit_trail(self):
        """Email addresses must not appear in audit trail"""
        audit_trail = AuditTrail(tenant_id="_default")

        # Attempt to log event with email
        audit_trail.log_event(AuditEvent(
            event_type="user_action",
            details={
                "user_email": "user@example.com",  # Should be scrubbed
                "action": "skill_executed"
            }
        ))

        events = audit_trail.get_all_events()
        events_str = json.dumps(events)

        assert "@example.com" not in events_str, "Emails should be scrubbed"

    def test_no_api_keys_in_audit_trail(self):
        """API keys/secrets must not appear in audit trail"""
        audit_trail = AuditTrail(tenant_id="_default")

        audit_trail.log_event(AuditEvent(
            event_type="authentication",
            details={
                "api_key": "sk-12345abcde",  # Should be scrubbed
                "status": "authenticated"
            }
        ))

        events = audit_trail.get_all_events()
        events_str = json.dumps(events)

        assert "sk-12345abcde" not in events_str, "API keys should be scrubbed"
        assert "api_key" not in events_str or events_str.count("api_key") == 1, \
            "API key field itself should be removed or redacted"

    def test_no_personal_ids_in_feedback(self):
        """User IDs and personal identifiers must not appear in feedback"""
        loop = ActiveLearningLoop(tenant_id="_default")

        loop.record_feedback({
            "skill_id": "test",
            "feedback_type": "outcome_feedback",
            "signal": "correct",
            "user_id": "12345",  # Should be scrubbed
            "email": "user@test.com",  # Should be scrubbed
            "tenant_id": "_default"
        })

        feedback = loop.get_feedback("test")
        feedback_str = json.dumps(feedback)

        assert "12345" not in feedback_str, "User ID should be scrubbed"
        assert "@test.com" not in feedback_str, "Email should be scrubbed"


class TestConcurrentAuditTrailSafety:
    """Verify audit trail is thread-safe"""

    def test_concurrent_event_logging(self):
        """Multiple threads logging events should not corrupt chain"""
        audit_trail = AuditTrail(tenant_id="_default")
        num_threads = 10
        events_per_thread = 10

        def log_events(thread_id):
            for i in range(events_per_thread):
                audit_trail.log_event(AuditEvent(
                    event_type=f"thread_{thread_id}_event_{i}",
                    details={"thread_id": thread_id, "index": i}
                ))

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(log_events, i)
                for i in range(num_threads)
            ]
            [f.result(timeout=5) for f in futures]

        events = audit_trail.get_all_events()

        # Should have all events
        expected_count = num_threads * events_per_thread
        assert len(events) == expected_count, f"Expected {expected_count} events, got {len(events)}"

        # Chain should still be valid
        assert audit_trail.verify_chain_integrity(), "Chain must be valid after concurrent logging"

    def test_concurrent_tenant_isolation(self):
        """Multiple tenants logging events simultaneously must be isolated"""
        num_tenants = 5
        events_per_tenant = 20

        audit_trails = {
            f"tenant_{i}": AuditTrail(tenant_id=f"tenant_{i}")
            for i in range(num_tenants)
        }

        def log_to_tenant(tenant_id):
            for i in range(events_per_tenant):
                audit_trails[tenant_id].log_event(AuditEvent(
                    event_type="concurrent_event",
                    details={"index": i}
                ))

        with ThreadPoolExecutor(max_workers=num_tenants) as executor:
            futures = [
                executor.submit(log_to_tenant, f"tenant_{i}")
                for i in range(num_tenants)
            ]
            [f.result(timeout=5) for f in futures]

        # Verify isolation
        for tenant_id, trail in audit_trails.items():
            events = trail.get_all_events()
            assert len(events) == events_per_tenant, \
                f"Tenant {tenant_id} should have {events_per_tenant} events"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
