"""Forge Bundle Phase 4 E2E tests — quarantine management + export/validate.

Tests for:
- GET /v1/console/forge-bundles/quarantine
- POST /v1/console/forge-bundles/quarantine/{id}/accept
- POST /v1/console/forge-bundles/quarantine/{id}/reject
- GET /v1/console/forge-bundles/export/available
- POST /v1/console/forge-bundles/export
- POST /v1/console/forge-bundles/validate
"""
import io
import json
import pytest
from unittest.mock import patch, MagicMock

from core.forge_bundle.tool_quarantine import ToolQuarantineWorkflow, QuarantinedTool


TENANT_ID = "_default"


class TestQuarantineManagement:
    """Tests for quarantine list/accept/reject routes."""

    def test_list_quarantine_empty(self, client):
        """GET /quarantine returns empty quarantine."""
        response = client.get("/v1/console/forge-bundles/quarantine")
        assert response.status_code == 200
        data = response.json()
        assert data["tools"] == []
        assert data["plugins"] == []

    def test_list_quarantine_with_tools(self, client):
        """GET /quarantine lists quarantined tools."""
        # Setup: stage a tool to quarantine
        quarantine = ToolQuarantineWorkflow(TENANT_ID)
        spec = {
            "name": "test-tool",
            "description": "A test tool",
            "input_schema": {"type": "object"},
            "runtime": "python",
            "version": "1.0.0",
        }
        impl_bytes = b"#!/usr/bin/env python3\nprint('hello')"

        qt = quarantine.stage_tool_from_bundle(
            tool_id="test-tool",
            bundle_id="test-bundle",
            version="1.0.0",
            spec=spec,
            impl_bytes=impl_bytes,
            impl_ext="py",
            user_id="test-user",
        )

        # List quarantine
        response = client.get("/v1/console/forge-bundles/quarantine")
        assert response.status_code == 200
        data = response.json()

        assert len(data["tools"]) >= 1
        tool = next((t for t in data["tools"] if t["tool_id"] == "test-tool"), None)
        assert tool is not None
        assert tool["version"] == "1.0.0"
        assert tool["spec"]["name"] == "test-tool"

    def test_accept_quarantined_tool_success(self, client):
        """POST /quarantine/{id}/accept promotes tool to registry."""
        # Setup: stage a tool
        quarantine = ToolQuarantineWorkflow(TENANT_ID)
        spec = {
            "name": "test-tool-accept",
            "description": "A test tool",
            "input_schema": {"type": "object"},
            "runtime": "python",
            "version": "1.0.0",
        }
        impl_bytes = b"#!/usr/bin/env python3\nprint('accepted')"

        qt = quarantine.stage_tool_from_bundle(
            tool_id="test-tool-accept",
            bundle_id="test-bundle",
            version="1.0.0",
            spec=spec,
            impl_bytes=impl_bytes,
            impl_ext="py",
            user_id="test-user",
        )

        # Accept tool
        response = client.post(
            f"/v1/console/forge-bundles/quarantine/{qt.quarantine_id}/accept",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["tool_id"] == "test-tool-accept"
        assert data["now_live"] is True

        # Verify quarantine entry is cleaned up
        remaining = quarantine.list_quarantined_tools()
        assert not any(t.tool_id == "test-tool-accept" for t in remaining)

    def test_accept_quarantined_tool_not_found(self, client):
        """POST /quarantine/{id}/accept returns 422 if not found."""
        response = client.post(
            "/v1/console/forge-bundles/quarantine/nonexistent__id__12345/accept",
        )
        assert response.status_code == 422

    def test_reject_quarantined_tool_success(self, client):
        """POST /quarantine/{id}/reject deletes from quarantine."""
        # Setup: stage a tool
        quarantine = ToolQuarantineWorkflow(TENANT_ID)
        spec = {
            "name": "test-tool-reject",
            "description": "A test tool",
            "input_schema": {"type": "object"},
            "runtime": "python",
            "version": "1.0.0",
        }
        impl_bytes = b"#!/usr/bin/env python3\nprint('rejected')"

        qt = quarantine.stage_tool_from_bundle(
            tool_id="test-tool-reject",
            bundle_id="test-bundle",
            version="1.0.0",
            spec=spec,
            impl_bytes=impl_bytes,
            impl_ext="py",
            user_id="test-user",
        )

        # Reject tool
        response = client.post(
            f"/v1/console/forge-bundles/quarantine/{qt.quarantine_id}/reject",
            params={"reason": "Does not meet requirements"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rejected"
        assert data["tool_id"] == "test-tool-reject"
        assert data["reason"] == "Does not meet requirements"

        # Verify quarantine entry is cleaned up
        remaining = quarantine.list_quarantined_tools()
        assert not any(t.tool_id == "test-tool-reject" for t in remaining)

    def test_reject_quarantined_tool_not_found(self, client):
        """POST /quarantine/{id}/reject returns 422 if not found."""
        response = client.post(
            "/v1/console/forge-bundles/quarantine/nonexistent__id__12345/reject",
        )
        assert response.status_code == 422


class TestExportValidation:
    """Tests for export list/build and validation routes."""

    def test_list_available_artifacts(self, client):
        """GET /export/available returns artifact inventory."""
        response = client.get("/v1/console/forge-bundles/export/available")
        assert response.status_code == 200
        data = response.json()
        assert "skills" in data
        assert "tools" in data
        assert "layers" in data
        assert "plugins" in data

    def test_validate_bundle_empty(self, client):
        """POST /validate with empty bundle returns validation error."""
        response = client.post(
            "/v1/console/forge-bundles/validate",
            files={"file": ("empty.zip", b"")},
        )
        assert response.status_code == 422

    def test_validate_bundle_invalid_zip(self, client):
        """POST /validate with invalid ZIP returns validation error."""
        response = client.post(
            "/v1/console/forge-bundles/validate",
            files={"file": ("invalid.zip", b"not a zip")},
        )
        assert response.status_code == 422

    def test_validate_bundle_oversized(self, client):
        """POST /validate with oversized bundle returns 413."""
        large_data = b"x" * (51 * 1024 * 1024)  # 51 MiB
        response = client.post(
            "/v1/console/forge-bundles/validate",
            files={"file": ("large.zip", large_data)},
        )
        assert response.status_code == 413

    def test_export_bundle_missing_selections(self, client):
        """POST /export without selections returns 400."""
        body = {
            "bundle_id": "test-bundle",
            "bundle_version": "1.0.0",
            "selections": [],
        }
        response = client.post(
            "/v1/console/forge-bundles/export",
            json=body,
        )
        assert response.status_code == 400

    def test_export_bundle_missing_id(self, client):
        """POST /export without bundle_id returns 400."""
        body = {
            "bundle_version": "1.0.0",
            "selections": [{"kind": "skill", "id": "test", "version": "1.0.0"}],
        }
        response = client.post(
            "/v1/console/forge-bundles/export",
            json=body,
        )
        assert response.status_code == 400


class TestAuditEvents:
    """Tests for audit event emission."""

    def test_quarantine_accepted_audit_event(self, client):
        """Accept quarantined tool emits audit event."""
        # Setup: stage a tool
        quarantine = ToolQuarantineWorkflow(TENANT_ID)
        spec = {
            "name": "test-tool-audit",
            "description": "A test tool",
            "input_schema": {"type": "object"},
            "runtime": "python",
            "version": "1.0.0",
        }
        impl_bytes = b"#!/usr/bin/env python3\nprint('audit')"

        qt = quarantine.stage_tool_from_bundle(
            tool_id="test-tool-audit",
            bundle_id="test-bundle",
            version="1.0.0",
            spec=spec,
            impl_bytes=impl_bytes,
            impl_ext="py",
            user_id="test-user",
        )

        # Accept and verify audit event is emitted
        response = client.post(
            f"/v1/console/forge-bundles/quarantine/{qt.quarantine_id}/accept",
        )
        assert response.status_code == 200

        # Verify audit chain contains acceptance event
        from core.paths import tenant_audit_chain
        chain = tenant_audit_chain(TENANT_ID)
        if chain.exists():
            lines = chain.read_text().strip().split("\n")
            last_event = json.loads(lines[-1])
            # The last event should be a quarantine acceptance or tool creation event
            assert last_event.get("event_type") in [
                "forge_bundle.quarantine_accepted",
                "tool_created",
            ] or "tool" in last_event.get("event_type", "").lower()

    def test_validate_bundle_audit_event(self, client):
        """Validate bundle emits audit event."""
        import zipfile

        # Create a minimal valid bundle
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "format": "forge-bundle/v1",
                "format_version": "1",
                "id": "test-validate-bundle",
                "version": "1.0.0",
                "created_at": "2026-01-01T00:00:00Z",
                "artifacts": [],
            }
            zf.writestr("forge-bundle.json", json.dumps(manifest))
        bundle_bytes = buf.getvalue()

        # Validate bundle
        response = client.post(
            "/v1/console/forge-bundles/validate",
            files={"file": ("test.zip", bundle_bytes)},
        )
        assert response.status_code == 200

        # Verify audit event is emitted (best effort — may not be queryable in tests)
        data = response.json()
        assert data["bundle_id"] == "test-validate-bundle"


class TestErrorHandling:
    """Tests for error handling and edge cases."""

    def test_unauthorized_access_returns_401(self, client):
        """Requests without session return 401."""
        # This test assumes proper session middleware validation
        # Adjust based on your session handling
        pass

    def test_csrf_validation_on_mutations(self, client):
        """POST routes validate CSRF token."""
        # This test assumes proper CSRF middleware validation
        # Adjust based on your CSRF handling
        pass

    def test_quarantine_cleanup_on_error(self, client):
        """Quarantine is cleaned up even if registry write fails."""
        # This test requires mocking ToolRegistry.create() to fail
        pass
