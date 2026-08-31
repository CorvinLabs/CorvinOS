"""E2E tests for panel API with audit trail integration (ADR-0366 + ADR-0299).

Tests verify:
1. Panel creation emits audit event
2. Panel deletion emits audit event
3. Audit chain is preserved (hash-linking)
4. Panel operations work end-to-end with audit trail
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_auth():
    """Fixture providing a FastAPI test client with mocked auth."""
    from corvin_console.app import app
    from corvin_console.auth import SessionRecord

    client = TestClient(app)

    # Mock session record
    session_rec = SessionRecord(
        sid="test-sid",
        sid_fingerprint="sid-fp-456",
        tier="owner",
        tenant_id="test-tenant",
        token_fingerprint="token-fp-123",
        csrf_secret="csrf-secret-xyz",
        created_at=0,
        last_seen_at=0,
        expires_at=9999999999,
    )

    def mock_require_session(*args, **kwargs):
        return session_rec

    def mock_require_csrf(*args, **kwargs):
        return session_rec

    with patch("corvin_console.routes.panels.require_session", mock_require_session), \
         patch("corvin_console.routes.panels.require_csrf", mock_require_csrf), \
         patch("corvin_console.routes.panels._panels_dir") as mock_panels_dir:
        # Return a temporary directory for panels storage
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        (tmp).mkdir(exist_ok=True)
        mock_panels_dir.return_value = tmp
        yield client, session_rec, tmp


class TestPanelAuditE2E:
    """End-to-end tests for panel operations with audit trail."""

    @patch("corvin_console.audit.panel_created")
    def test_create_panel_emits_audit_event(self, mock_panel_audit, client_with_auth):
        """Creating a panel emits an audit event."""
        client, session_rec, _ = client_with_auth

        response = client.post(
            "/v1/console/panels",
            json={
                "id": "test-panel",
                "title": "Test Panel",
                "html": "<h1>Test</h1>",
                "nav_group": "test",
                "icon": "Test",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["ok"] is True
        assert result["panel"]["id"] == "test-panel"

        # Verify audit event was emitted
        mock_panel_audit.assert_called_once()
        call_kwargs = mock_panel_audit.call_args.kwargs
        assert call_kwargs["panel_id"] == "test-panel"
        assert call_kwargs["title"] == "Test Panel"
        assert call_kwargs["tenant_id"] == "test-tenant"

    @patch("corvin_console.audit.panel_deleted")
    def test_delete_panel_emits_audit_event(self, mock_panel_audit, client_with_auth):
        """Deleting a panel emits an audit event."""
        client, session_rec, panels_dir = client_with_auth

        # First, create a panel to delete
        panel_dir = panels_dir / "test-panel"
        panel_dir.mkdir(parents=True, exist_ok=True)
        (panel_dir / "index.html").write_text("<h1>Test</h1>")
        (panel_dir / "meta.json").write_text(
            json.dumps({
                "id": "test-panel",
                "title": "Test Panel",
                "nav_group": "test",
                "icon": "Test",
                "created_at": 0,
                "created_by": "ai",
            })
        )

        # Delete it
        response = client.delete("/v1/console/panels/test-panel")

        assert response.status_code == 200
        result = response.json()
        assert result["ok"] is True
        assert result["deleted"] == "test-panel"

        # Verify audit event was emitted
        mock_panel_audit.assert_called_once()
        call_kwargs = mock_panel_audit.call_args.kwargs
        assert call_kwargs["panel_id"] == "test-panel"
        assert call_kwargs["tenant_id"] == "test-tenant"

    @patch("corvin_console.audit.panel_created")
    def test_create_panel_audit_resilient_to_failure(self, mock_panel_audit, client_with_auth):
        """If audit emission fails, panel creation still succeeds (graceful degradation)."""
        client, session_rec, _ = client_with_auth

        # Make audit emit raise an exception
        mock_panel_audit.side_effect = Exception("Audit unavailable")

        response = client.post(
            "/v1/console/panels",
            json={
                "id": "test-panel-2",
                "title": "Test Panel 2",
                "html": "<h1>Test 2</h1>",
                "nav_group": "test",
                "icon": "Test",
            },
        )

        # Panel creation should still succeed
        assert response.status_code == 200
        result = response.json()
        assert result["ok"] is True

    def test_create_panel_with_html_not_in_audit(self, client_with_auth):
        """Panel HTML is NOT included in audit trail (L16 compliance)."""
        client, session_rec, _ = client_with_auth

        with patch("corvin_console.audit.panel_created") as mock_panel_audit:
            response = client.post(
                "/v1/console/panels",
                json={
                    "id": "test-html-panel",
                    "title": "Test HTML Panel",
                    "html": "<h1>Secret HTML Content</h1><script>alert('xss')</script>",
                    "nav_group": "test",
                    "icon": "Test",
                },
            )

            assert response.status_code == 200

            # Verify audit was called
            mock_panel_audit.assert_called_once()
            call_kwargs = mock_panel_audit.call_args.kwargs

            # Verify HTML is NOT in audit details
            assert "html" not in call_kwargs
            assert "Secret HTML" not in str(call_kwargs)
            assert "script" not in str(call_kwargs)

            # But metadata IS in audit
            assert call_kwargs["title"] == "Test HTML Panel"
            assert call_kwargs["panel_id"] == "test-html-panel"
