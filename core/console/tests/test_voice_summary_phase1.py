"""
Voice Summary Phase 1 Tests
Tests: Start/Stop Recording, Summary Retrieval, Health Check, Graceful Fallback
"""

import pytest
from fastapi.testclient import TestClient
from core.console.corvin_console.app import app


@pytest.fixture
def client():
    return TestClient(app)


class TestVoiceSummaryPhase1:
    """Phase 1: Simple Voice Recording + Summary"""

    def test_voice_health_check(self, client):
        """Test: Voice Summary health check (STT availability)"""
        response = client.get("/v1/console/chat/voice/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "stt_available" in data
        assert data["stt_available"] is True  # Phase 1: Mock availability

    def test_start_voice_recording(self, client):
        """Test: Start voice recording (Opt-In)"""
        session_id = "test-session-001"
        response = client.post(
            f"/v1/console/chat/{session_id}/voice/start",
            json={"tenant_id": "_default"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "recording_started"
        assert data["session_id"] == session_id
        assert data["stt_available"] is True

    def test_stop_voice_recording(self, client):
        """Test: Stop recording and generate summary"""
        session_id = "test-session-002"

        # Start recording
        client.post(
            f"/v1/console/chat/{session_id}/voice/start",
            json={"tenant_id": "_default"}
        )

        # Stop recording
        transcript = "User asked: how do I route requests? Agent answered: use os-router."
        response = client.post(
            f"/v1/console/chat/{session_id}/voice/stop",
            json={"tenant_id": "_default", "transcript": transcript}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "recording_stopped"
        assert data["session_id"] == session_id
        assert len(data["transcript"]) > 0
        assert data["summary"] is not None

    def test_get_voice_summary_not_started(self, client):
        """Test: Get summary for session that never recorded"""
        session_id = "never-recorded-001"
        response = client.get(f"/v1/console/chat/{session_id}/voice/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["status"] == "not_started"

    def test_get_voice_summary_recording(self, client):
        """Test: Get summary for active recording session"""
        session_id = "test-session-003"

        # Start recording
        client.post(
            f"/v1/console/chat/{session_id}/voice/start",
            json={"tenant_id": "_default"}
        )

        # Fetch current state
        response = client.get(f"/v1/console/chat/{session_id}/voice/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "recording"
        assert data["started_at"] is not None

    def test_get_voice_summary_stopped(self, client):
        """Test: Get summary for completed session"""
        session_id = "test-session-004"

        # Start and stop
        client.post(
            f"/v1/console/chat/{session_id}/voice/start",
            json={"tenant_id": "_default"}
        )

        transcript = "Test conversation"
        client.post(
            f"/v1/console/chat/{session_id}/voice/stop",
            json={"tenant_id": "_default", "transcript": transcript}
        )

        # Fetch final state
        response = client.get(f"/v1/console/chat/{session_id}/voice/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"
        assert data["transcript"] == transcript
        assert data["summary"] is not None
        assert data["stopped_at"] is not None

    def test_stop_nonexistent_session(self, client):
        """Test: Error when stopping recording that doesn't exist"""
        response = client.post(
            "/v1/console/chat/nonexistent-session/voice/stop",
            json={"tenant_id": "_default", "transcript": ""}
        )
        assert response.status_code == 404

    def test_session_isolation(self, client):
        """Test: Multiple sessions don't interfere"""
        session1 = "session-001"
        session2 = "session-002"

        # Start both
        client.post(f"/v1/console/chat/{session1}/voice/start", json={"tenant_id": "_default"})
        client.post(f"/v1/console/chat/{session2}/voice/start", json={"tenant_id": "_default"})

        # Stop only session 1
        client.post(
            f"/v1/console/chat/{session1}/voice/stop",
            json={"tenant_id": "_default", "transcript": "Session 1"}
        )

        # Verify states
        s1 = client.get(f"/v1/console/chat/{session1}/voice/summary").json()
        s2 = client.get(f"/v1/console/chat/{session2}/voice/summary").json()

        assert s1["status"] == "stopped"
        assert s2["status"] == "recording"

    def test_tenant_scoping(self, client):
        """Test: Audit events include tenant_id"""
        session_id = "test-tenant-scope"
        tenant_id = "tenant-custom-001"

        response = client.post(
            f"/v1/console/chat/{session_id}/voice/start",
            json={"tenant_id": tenant_id}
        )

        assert response.status_code == 200
        # In Phase 2: verify audit event has correct tenant_id


class TestGracefulDegradation:
    """Phase 1: Chat works even when STT is unavailable"""

    def test_stt_unavailable_fallback(self, client):
        """Test: Chat continues even if STT unavailable"""
        # Phase 2: Mock STT unavailability
        # For now: verify health endpoint shows STT status
        response = client.get("/v1/console/chat/voice/health")
        data = response.json()

        # If stt_available=False, chat should still work
        # (This is enforced in React component: Button disabled but doesn't block chat)
        assert "stt_available" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
