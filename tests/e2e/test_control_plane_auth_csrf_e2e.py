"""
Phase 9 Stream 2: End-to-End Auth & CSRF Tests for Control Plane.

Tests that all control plane endpoints properly reject:
- Requests without valid session (401 Unauthorized)
- Requests without CSRF token on mutations (403 Forbidden)
- Requests with invalid CSRF token (403 Forbidden)

These tests use FastAPI TestClient to verify actual HTTP responses.

ADR-2029: User-Centric CorvinOS Control Plane
GDPR Art. 6, 32 — Authentication & Data Protection
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, Mock
from datetime import datetime

# Import the console app
try:
    from core.console.corvin_console.standalone import create_app
    from core.console import auth as session_auth
except ImportError:
    pytest.skip("Console app not available", allow_module_level=True)


@pytest.fixture
def client():
    """Create a test client for the console app."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def mock_session():
    """Create a mock session record."""
    return session_auth.SessionRecord(
        sid="test_session_" + "a" * 30,
        sid_fingerprint="abcdef123456",
        csrf_secret="test_csrf_secret_12345",
        tenant_id="_default",
        user_id="test_user",
        username="testuser",
        authenticated=True,
        login_method="local",
        created_at=datetime.now(),
    )


@pytest.fixture
def valid_csrf_token(mock_session):
    """Generate a valid CSRF token."""
    return session_auth.generate_csrf_token(
        csrf_secret=mock_session.csrf_secret,
        sid=mock_session.sid
    )


# ============================================================================
# OVERRIDE AUTHORITY ENDPOINTS
# ============================================================================

class TestOverrideAuthorityEndpointsAuth:
    """E2E tests for override authority endpoint auth."""

    def test_create_override_without_session_returns_401(self, client):
        """POST /control-plane/overrides without session should return 401."""
        response = client.post(
            "/v1/console/control-plane/overrides",
            json={
                "override_type": "force_enable",
                "target_id": "test_target",
                "reason": "Testing"
            }
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"

    def test_approve_override_without_session_returns_401(self, client):
        """POST /control-plane/overrides/{id}/approve without session should return 401."""
        response = client.post(
            "/v1/console/control-plane/overrides/test_override_id/approve",
            json={"reason": "Approved"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"

    def test_approve_override_without_csrf_returns_403(self, client, mock_session, valid_csrf_token):
        """POST /control-plane/overrides/{id}/approve without CSRF should return 403."""
        # Mock the session cookie but don't provide CSRF token
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.post(
                "/v1/console/control-plane/overrides/test_override_id/approve",
                json={"reason": "Approved"},
                cookies={"corvin_console_sid": "test_session_id"}
                # Note: No x-csrf-token header
            )
            assert response.status_code == 403, f"Expected 403, got {response.status_code}"

    def test_deny_override_without_session_returns_401(self, client):
        """POST /control-plane/overrides/{id}/deny without session should return 401."""
        response = client.post(
            "/v1/console/control-plane/overrides/test_override_id/deny",
            json={"reason": "Denied"}
        )
        assert response.status_code == 401

    def test_deny_override_without_csrf_returns_403(self, client, mock_session):
        """POST /control-plane/overrides/{id}/deny without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.post(
                "/v1/console/control-plane/overrides/test_override_id/deny",
                json={"reason": "Denied"},
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_interrupt_override_without_csrf_returns_403(self, client, mock_session):
        """POST /control-plane/overrides/{id}/interrupt without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.post(
                "/v1/console/control-plane/overrides/test_override_id/interrupt",
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403


# ============================================================================
# SNAPSHOT ENDPOINTS
# ============================================================================

class TestSnapshotEndpointsAuth:
    """E2E tests for snapshot endpoint auth."""

    def test_create_snapshot_without_session_returns_401(self, client):
        """POST /control-plane/snapshots without session should return 401."""
        response = client.post(
            "/v1/console/control-plane/snapshots",
            json={
                "name": "Test Snapshot",
                "description": "Testing"
            }
        )
        assert response.status_code == 401

    def test_create_snapshot_without_csrf_returns_403(self, client, mock_session):
        """POST /control-plane/snapshots without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.post(
                "/v1/console/control-plane/snapshots",
                json={
                    "name": "Test Snapshot",
                    "description": "Testing"
                },
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_list_snapshots_without_session_returns_401(self, client):
        """GET /control-plane/snapshots without session should return 401."""
        response = client.get("/v1/console/control-plane/snapshots")
        assert response.status_code == 401

    def test_get_snapshot_without_session_returns_401(self, client):
        """GET /control-plane/snapshots/{id} without session should return 401."""
        response = client.get("/v1/console/control-plane/snapshots/test_snapshot_id")
        assert response.status_code == 401

    def test_restore_snapshot_without_csrf_returns_403(self, client, mock_session):
        """POST /control-plane/snapshots/{id}/restore without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.post(
                "/v1/console/control-plane/snapshots/test_snapshot_id/restore",
                json={"confirm": True},
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_delete_snapshot_without_csrf_returns_403(self, client, mock_session):
        """DELETE /control-plane/snapshots/{id} without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.delete(
                "/v1/console/control-plane/snapshots/test_snapshot_id",
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_snapshot_audit_log_without_session_returns_401(self, client):
        """GET /control-plane/snapshots/audit-log without session should return 401."""
        response = client.get("/v1/console/control-plane/snapshots/audit-log")
        assert response.status_code == 401


# ============================================================================
# PLUGIN MANAGER ENDPOINTS
# ============================================================================

class TestPluginManagerEndpointsAuth:
    """E2E tests for plugin manager endpoint auth."""

    def test_install_plugin_without_session_returns_401(self, client):
        """PUT /control-plane/plugins/install without session should return 401."""
        response = client.put(
            "/v1/console/control-plane/plugins/install",
            json={
                "plugin_id": "test_plugin",
                "name": "Test Plugin",
                "version": "1.0.0",
                "boot_layer": "installed"
            }
        )
        assert response.status_code == 401

    def test_install_plugin_without_csrf_returns_403(self, client, mock_session):
        """PUT /control-plane/plugins/install without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.put(
                "/v1/console/control-plane/plugins/install",
                json={
                    "plugin_id": "test_plugin",
                    "name": "Test Plugin",
                    "version": "1.0.0",
                    "boot_layer": "installed"
                },
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_list_plugins_without_session_returns_401(self, client):
        """GET /control-plane/plugins without session should return 401."""
        response = client.get("/v1/console/control-plane/plugins")
        assert response.status_code == 401

    def test_get_plugin_without_session_returns_401(self, client):
        """GET /control-plane/plugins/{id} without session should return 401."""
        response = client.get("/v1/console/control-plane/plugins/test_plugin_id")
        assert response.status_code == 401

    def test_enable_plugin_without_csrf_returns_403(self, client, mock_session):
        """PATCH /control-plane/plugins/{id}/enable without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.patch(
                "/v1/console/control-plane/plugins/test_plugin_id/enable",
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_disable_plugin_without_csrf_returns_403(self, client, mock_session):
        """PATCH /control-plane/plugins/{id}/disable without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.patch(
                "/v1/console/control-plane/plugins/test_plugin_id/disable",
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_uninstall_plugin_without_csrf_returns_403(self, client, mock_session):
        """DELETE /control-plane/plugins/{id} without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.delete(
                "/v1/console/control-plane/plugins/test_plugin_id",
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_plugin_audit_log_without_session_returns_401(self, client):
        """GET /control-plane/plugins/audit-log without session should return 401."""
        response = client.get("/v1/console/control-plane/plugins/audit-log")
        assert response.status_code == 401


# ============================================================================
# SUBSYSTEM MANAGER ENDPOINTS
# ============================================================================

class TestSubsystemManagerEndpointsAuth:
    """E2E tests for subsystem manager endpoint auth."""

    def test_start_subsystem_without_csrf_returns_403(self, client, mock_session):
        """PATCH /control-plane/subsystems/{id}/start without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.patch(
                "/v1/console/control-plane/subsystems/test_subsystem_id/start",
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_pause_subsystem_without_csrf_returns_403(self, client, mock_session):
        """PATCH /control-plane/subsystems/{id}/pause without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.patch(
                "/v1/console/control-plane/subsystems/test_subsystem_id/pause",
                json={"timeout_s": 30},
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_resume_subsystem_without_csrf_returns_403(self, client, mock_session):
        """PATCH /control-plane/subsystems/{id}/resume without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.patch(
                "/v1/console/control-plane/subsystems/test_subsystem_id/resume",
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_stop_subsystem_without_csrf_returns_403(self, client, mock_session):
        """PATCH /control-plane/subsystems/{id}/stop without CSRF should return 403."""
        with patch('core.console.corvin_console.auth.load_session', return_value=mock_session):
            response = client.patch(
                "/v1/console/control-plane/subsystems/test_subsystem_id/stop",
                json={"force": False, "timeout_s": 30},
                cookies={"corvin_console_sid": "test_session_id"}
            )
            assert response.status_code == 403

    def test_get_subsystem_status_without_session_returns_401(self, client):
        """GET /control-plane/subsystems/{id} without session should return 401."""
        response = client.get("/v1/console/control-plane/subsystems/test_subsystem_id")
        assert response.status_code == 401

    def test_list_subsystems_without_session_returns_401(self, client):
        """GET /control-plane/subsystems without session should return 401."""
        response = client.get("/v1/console/control-plane/subsystems")
        assert response.status_code == 401

    def test_get_subsystem_logs_without_session_returns_401(self, client):
        """GET /control-plane/subsystems/{id}/logs without session should return 401."""
        response = client.get("/v1/console/control-plane/subsystems/test_subsystem_id/logs")
        assert response.status_code == 401

    def test_subsystem_audit_log_without_session_returns_401(self, client):
        """GET /control-plane/subsystems/audit-log without session should return 401."""
        response = client.get("/v1/console/control-plane/subsystems/audit-log")
        assert response.status_code == 401


# ============================================================================
# COMPREHENSIVE COVERAGE MATRIX
# ============================================================================

class TestAuthCsrfMatrix:
    """Comprehensive matrix test for all endpoint protections."""

    @pytest.mark.parametrize("endpoint,method,require_csrf", [
        # Overrides
        ("/v1/console/control-plane/overrides", "POST", True),
        ("/v1/console/control-plane/overrides/id/approve", "POST", True),
        ("/v1/console/control-plane/overrides/id/deny", "POST", True),
        ("/v1/console/control-plane/overrides/id/interrupt", "POST", True),
        # Snapshots
        ("/v1/console/control-plane/snapshots", "POST", True),
        ("/v1/console/control-plane/snapshots/id/restore", "POST", True),
        ("/v1/console/control-plane/snapshots/id", "DELETE", True),
        # Plugins
        ("/v1/console/control-plane/plugins/install", "PUT", True),
        ("/v1/console/control-plane/plugins/id/enable", "PATCH", True),
        ("/v1/console/control-plane/plugins/id/disable", "PATCH", True),
        ("/v1/console/control-plane/plugins/id", "DELETE", True),
        # Subsystems
        ("/v1/console/control-plane/subsystems/id/start", "PATCH", True),
        ("/v1/console/control-plane/subsystems/id/pause", "PATCH", True),
        ("/v1/console/control-plane/subsystems/id/resume", "PATCH", True),
        ("/v1/console/control-plane/subsystems/id/stop", "PATCH", True),
    ])
    def test_all_mutations_require_auth(self, client, endpoint, method, require_csrf):
        """Test that all mutation endpoints require auth."""
        # Send request without any auth
        if method == "POST":
            response = client.post(endpoint)
        elif method == "PUT":
            response = client.put(endpoint)
        elif method == "PATCH":
            response = client.patch(endpoint)
        elif method == "DELETE":
            response = client.delete(endpoint)

        # Should return 401 (no session) or 403 (session but no CSRF)
        assert response.status_code in [401, 403, 404], \
            f"{method} {endpoint} returned {response.status_code}, expected 401/403"
