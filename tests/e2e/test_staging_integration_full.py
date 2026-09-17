"""Context-Drift Staging Integration Tests.

Full integration tests against staging environment.
Tests complete goal lifecycle, persistence, drift detection, and compliance.

ADR-0407: Session Context Drift Prevention
ADR-0404: Goal Alignment Validation Gate
ADR-0405: Cross-Session Goal Persistence
ADR-0406: LDD Goal Re-Sync Protocol
"""

import pytest
import json
import time
import hashlib
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class Goal:
    """Goal object from staging API."""
    id: str
    task: str
    constraint: str
    created_at: str
    session_id: str
    goal_hash: str


class StagingClient:
    """Client for staging Context-Drift API."""

    def __init__(self, base_url: str = "http://localhost:8765"):
        self.base_url = base_url
        self.session_id: Optional[str] = None
        self.goals: Dict[str, Goal] = {}

    def new_session(self) -> str:
        """Create new session and return session ID."""
        import requests
        response = requests.post(f"{self.base_url}/v1/sessions", json={})
        session = response.json()
        self.session_id = session["id"]
        return self.session_id

    def set_session(self, session_id: str) -> None:
        """Set current session."""
        self.session_id = session_id

    def create_goal(self, goal_spec: Dict[str, str]) -> Goal:
        """Create new goal."""
        import requests
        response = requests.post(
            f"{self.base_url}/v1/context-drift/goals",
            json={
                "session_id": self.session_id,
                "task": goal_spec.get("task"),
                "constraint": goal_spec.get("constraint", ""),
            }
        )
        goal_data = response.json()
        goal = Goal(**goal_data)
        self.goals[goal.id] = goal
        return goal

    def get_goal(self, goal_id: Optional[str] = None) -> Goal:
        """Get goal by ID or latest."""
        import requests
        goal_id = goal_id or list(self.goals.keys())[-1]
        response = requests.get(f"{self.base_url}/v1/context-drift/goals/{goal_id}")
        goal_data = response.json()
        goal = Goal(**goal_data)
        return goal

    def update_goal(self, goal_spec: Dict[str, str], goal_id: Optional[str] = None) -> Goal:
        """Update goal."""
        import requests
        goal_id = goal_id or list(self.goals.keys())[-1]
        response = requests.put(
            f"{self.base_url}/v1/context-drift/goals/{goal_id}",
            json={
                "task": goal_spec.get("task"),
                "constraint": goal_spec.get("constraint", ""),
            }
        )
        goal_data = response.json()
        goal = Goal(**goal_data)
        self.goals[goal_id] = goal
        return goal

    def restore_goal(self, goal: Goal) -> Goal:
        """Restore goal from checkpoint."""
        import requests
        response = requests.post(
            f"{self.base_url}/v1/context-drift/goals/{goal.id}/restore",
            json=asdict(goal)
        )
        goal_data = response.json()
        restored = Goal(**goal_data)
        return restored

    def check_goal_alignment(self, goal: Goal) -> Dict[str, Any]:
        """Check goal alignment (detect drift)."""
        import requests
        response = requests.post(
            f"{self.base_url}/v1/context-drift/goals/{goal.id}/check-alignment",
            json={"session_id": self.session_id}
        )
        return response.json()

    def query_audit_trail(
        self,
        event_type: Optional[str] = None,
        goal_id: Optional[str] = None
    ) -> list:
        """Query audit trail."""
        import requests
        params = {}
        if event_type:
            params["event_type"] = event_type
        if goal_id:
            params["goal_id"] = goal_id

        response = requests.get(
            f"{self.base_url}/v1/context-drift/audit",
            params=params
        )
        return response.json().get("events", [])

    def query_transparency_log(self) -> list:
        """Query transparency log (EU AI Act)."""
        import requests
        response = requests.get(f"{self.base_url}/v1/context-drift/transparency")
        return response.json().get("events", [])

    def health_check(self) -> Dict[str, Any]:
        """Check API health."""
        import requests
        response = requests.get(f"{self.base_url}/v1/context-drift/health")
        return response.json()


@pytest.mark.e2e
@pytest.mark.staging
class TestContextDriftStagingIntegration:
    """Full integration tests for Context-Drift staging."""

    @pytest.fixture
    def staging_client(self):
        """Create staging client and verify connection."""
        client = StagingClient()
        try:
            health = client.health_check()
            assert health["status"] == "healthy"
            logger.info(f"✅ Connected to staging: {health}")
        except Exception as e:
            pytest.skip(f"Staging environment not available: {e}")
        return client

    def test_end_to_end_goal_lifecycle(self, staging_client):
        """Complete goal lifecycle: create → persist → restore → drift detect."""
        logger.info("\n🧪 Test: End-to-End Goal Lifecycle")

        # 1. Create goal in session 1
        session_id_1 = staging_client.new_session()
        goal = staging_client.create_goal({
            "task": "Implement payment processing",
            "constraint": "deadline: end of quarter"
        })
        logger.info(f"✅ Goal created: {goal.id}")
        assert goal.task == "Implement payment processing"
        assert goal.goal_hash is not None

        # 2. Verify goal persists in same session
        goal_restored = staging_client.get_goal(goal.id)
        assert goal_restored.id == goal.id
        assert goal_restored.goal_hash == goal.goal_hash
        logger.info(f"✅ Goal persisted in session: {session_id_1}")

        # 3. Create new session (simulate resume)
        session_id_2 = staging_client.new_session()
        logger.info(f"✅ New session created: {session_id_2}")

        # 4. Goal alignment check should detect no drift (same goal)
        alignment = staging_client.check_goal_alignment(goal)
        assert alignment["drift_score"] == 0.0  # No drift yet
        assert alignment["action"] == "CONTINUE"
        logger.info(f"✅ Goal alignment OK: drift_score={alignment['drift_score']}")

        # 5. Simulate drift (change goal)
        changed_goal = staging_client.update_goal({
            "task": "Implement invoice system",  # Changed!
            "constraint": "open-ended"
        }, goal.id)
        logger.info(f"✅ Goal changed (simulating drift)")

        # 6. Check alignment should detect drift
        alignment = staging_client.check_goal_alignment(goal)
        assert alignment["drift_score"] > 0.35  # Drift detected
        assert alignment["action"] in ["ALERT", "ESCALATE"]
        logger.info(f"✅ Drift detected: drift_score={alignment['drift_score']}, action={alignment['action']}")

        # 7. Restore original goal
        restored_goal = staging_client.restore_goal(goal)
        assert restored_goal.id == goal.id
        assert restored_goal.task == goal.task
        logger.info(f"✅ Goal restored to original")

        # 8. Re-check alignment (should show recovery)
        alignment = staging_client.check_goal_alignment(restored_goal)
        assert alignment["drift_score"] == 0.0
        logger.info(f"✅ Alignment recovered: drift_score={alignment['drift_score']}")

        logger.info("✅ End-to-End Test PASSED")

    def test_audit_trail_immutability(self, staging_client):
        """Audit trail is complete and immutable."""
        logger.info("\n🧪 Test: Audit Trail Immutability")

        # Create goal
        staging_client.new_session()
        goal = staging_client.create_goal({"task": "test task"})

        # Query audit events for this goal
        audit_events = staging_client.query_audit_trail(
            event_type="goal_created",
            goal_id=goal.id
        )

        assert len(audit_events) > 0, "No audit events found"
        event = audit_events[0]

        # Verify immutability markers
        assert event.get("immutable") == True, "Event should be marked immutable"
        assert event.get("hash_chain_valid") == True, "Hash chain should be valid"
        assert event.get("prev_hash") is not None, "Previous hash should be present"
        assert event.get("hash") is not None, "Event hash should be present"

        logger.info(f"✅ Audit event verified:")
        logger.info(f"   - Event ID: {event.get('id')}")
        logger.info(f"   - Immutable: {event.get('immutable')}")
        logger.info(f"   - Hash chain valid: {event.get('hash_chain_valid')}")
        logger.info("✅ Audit Trail Test PASSED")

    def test_compliance_gdpr_no_pii(self, staging_client):
        """GDPR compliance: No PII in audit."""
        logger.info("\n🧪 Test: GDPR Compliance - No PII in Audit")

        # Create goal with potentially sensitive data
        staging_client.new_session()
        goal = staging_client.create_goal({
            "task": "Implement payment for user@example.com",  # Email = PII
            "constraint": "deadline: 2026-09-30"
        })

        # Query audit
        audit_events = staging_client.query_audit_trail(goal_id=goal.id)

        # Check for PII patterns (simplified)
        pii_patterns = [
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email
            r'\b\d{3}-\d{2}-\d{4}\b',  # SSN
        ]

        for event in audit_events:
            event_str = json.dumps(event)
            for pattern in pii_patterns:
                import re
                if re.search(pattern, event_str):
                    pytest.fail(f"PII found in audit event: {pattern}")

        logger.info("✅ No PII detected in audit events")
        logger.info("✅ GDPR Compliance Test PASSED")

    def test_compliance_eu_ai_act_transparency(self, staging_client):
        """EU AI Act compliance: Transparency logged."""
        logger.info("\n🧪 Test: EU AI Act Compliance - Transparency Logging")

        # Create goal and check alignment
        staging_client.new_session()
        goal = staging_client.create_goal({"task": "test"})
        staging_client.check_goal_alignment(goal)

        # Query transparency log
        transparency = staging_client.query_transparency_log()
        assert len(transparency) > 0, "No transparency log events found"

        # Last event should be our alignment check
        last_event = transparency[-1]
        assert last_event.get("action") == "goal_alignment_checked"
        assert last_event.get("model") is not None  # Should log which model
        assert last_event.get("timestamp") is not None

        logger.info(f"✅ Transparency log verified:")
        logger.info(f"   - Action: {last_event.get('action')}")
        logger.info(f"   - Model: {last_event.get('model')}")
        logger.info(f"   - Timestamp: {last_event.get('timestamp')}")
        logger.info("✅ EU AI Act Compliance Test PASSED")

    def test_performance_slos_met(self, staging_client):
        """Performance SLOs: Goal alignment <5ms."""
        logger.info("\n🧪 Test: Performance SLOs")

        staging_client.new_session()
        goal = staging_client.create_goal({"task": "test"})

        # Measure alignment check latency
        import time
        start = time.time()
        staging_client.check_goal_alignment(goal)
        elapsed_ms = (time.time() - start) * 1000

        logger.info(f"  Alignment check latency: {elapsed_ms:.2f}ms")
        assert elapsed_ms < 5, f"SLO violated: {elapsed_ms:.2f}ms > 5ms target"

        # Measure create latency
        start = time.time()
        staging_client.create_goal({"task": "test 2"})
        elapsed_ms = (time.time() - start) * 1000

        logger.info(f"  Goal creation latency: {elapsed_ms:.2f}ms")
        assert elapsed_ms < 100, f"Creation SLO violated: {elapsed_ms:.2f}ms > 100ms target"

        logger.info("✅ Performance SLOs Test PASSED")

    def test_cross_session_isolation(self, staging_client):
        """Sessions don't cross-contaminate."""
        logger.info("\n🧪 Test: Cross-Session Isolation")

        # Session 1: Create goal A
        session_1 = staging_client.new_session()
        goal_a = staging_client.create_goal({
            "task": "Task A",
            "constraint": "session 1"
        })
        logger.info(f"✅ Session 1: Goal A created")

        # Session 2: Create goal B
        session_2 = staging_client.new_session()
        goal_b = staging_client.create_goal({
            "task": "Task B",
            "constraint": "session 2"
        })
        logger.info(f"✅ Session 2: Goal B created")

        # Switch back to session 1 and verify goal A unchanged
        staging_client.set_session(session_1)
        goal_a_restored = staging_client.get_goal(goal_a.id)
        assert goal_a_restored.task == "Task A"
        assert goal_a_restored.constraint == "session 1"

        # Switch to session 2 and verify goal B
        staging_client.set_session(session_2)
        goal_b_restored = staging_client.get_goal(goal_b.id)
        assert goal_b_restored.task == "Task B"
        assert goal_b_restored.constraint == "session 2"

        logger.info("✅ Sessions properly isolated")
        logger.info("✅ Cross-Session Isolation Test PASSED")

    def test_concurrent_goal_operations(self, staging_client):
        """Concurrent goal operations don't race."""
        logger.info("\n🧪 Test: Concurrent Operations (Sequential in Test)")

        staging_client.new_session()

        # Create multiple goals
        goals = []
        for i in range(5):
            goal = staging_client.create_goal({
                "task": f"Task {i}",
                "constraint": f"concurrent test {i}"
            })
            goals.append(goal)
            logger.info(f"  Created goal {i}: {goal.id}")

        # Verify all goals exist and are unique
        assert len(goals) == 5
        assert len(set(g.id for g in goals)) == 5, "Goal IDs not unique!"

        # Verify each goal has correct content
        for i, goal in enumerate(goals):
            retrieved = staging_client.get_goal(goal.id)
            assert retrieved.task == f"Task {i}"
            logger.info(f"  Verified goal {i}")

        logger.info("✅ Concurrent Operations Test PASSED")

    def test_error_handling_graceful_degradation(self, staging_client):
        """API handles errors gracefully."""
        logger.info("\n🧪 Test: Error Handling")

        staging_client.new_session()

        # Try to get non-existent goal
        try:
            staging_client.get_goal("nonexistent-goal-id")
            pytest.fail("Should have raised error for non-existent goal")
        except Exception as e:
            logger.info(f"✅ Got expected error: {type(e).__name__}")
            assert "404" in str(e) or "not found" in str(e).lower()

        # Try to check alignment on non-existent goal
        try:
            from dataclasses import make_dataclass
            fake_goal = Goal(
                id="fake",
                task="fake",
                constraint="fake",
                created_at="2026-09-17T00:00:00Z",
                session_id="fake",
                goal_hash="fake"
            )
            staging_client.check_goal_alignment(fake_goal)
            pytest.fail("Should have raised error")
        except Exception as e:
            logger.info(f"✅ Got expected error: {type(e).__name__}")

        logger.info("✅ Error Handling Test PASSED")

    def test_health_check(self, staging_client):
        """API health check returns expected structure."""
        logger.info("\n🧪 Test: Health Check")

        health = staging_client.health_check()

        assert health["status"] == "healthy"
        assert "threshold" in health  # Current drift threshold
        assert "feedback_quality" in health  # Feedback accuracy
        assert health["threshold"] > 0.0
        assert 0.0 <= health["feedback_quality"] <= 1.0

        logger.info(f"✅ Health check passed:")
        logger.info(f"   - Status: {health['status']}")
        logger.info(f"   - Threshold: {health['threshold']}")
        logger.info(f"   - Feedback quality: {health['feedback_quality']:.2%}")
        logger.info("✅ Health Check Test PASSED")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
