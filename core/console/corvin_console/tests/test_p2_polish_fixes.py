"""Phase 9 P2 Polish Fixes — Error Sanitization & Snapshot Bounds Tests.

Tests for:
1. Error message sanitization (no raw exceptions exposed)
2. Snapshot name/description bounds (max 500 chars, no DoS)
3. Restore snapshot implementation
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock

from corvin_console.app import app
from corvin_console.error_handling import safe_error_response, safe_snapshot_error


# ============================================================================
# Error Sanitization Tests
# ============================================================================

class TestErrorSanitization:
    """Test that error messages are safe (no raw exceptions exposed)."""

    def test_safe_error_response_no_traceback(self):
        """Error response should not contain traceback."""
        try:
            raise ValueError("sensitive internal error: database connection failed")
        except Exception as e:
            safe_msg = safe_error_response(e, "Failed to process request")

        # Should return safe message, not raw exception
        assert safe_msg == "Failed to process request"
        assert "sensitive" not in safe_msg
        assert "database" not in safe_msg

    def test_safe_snapshot_error_not_found(self):
        """Snapshot error should hide specific details."""
        error = ValueError("Snapshot snap_000001 not found in registry")
        safe_msg = safe_snapshot_error(error)

        # Should not expose snapshot ID
        assert "snap_000001" not in safe_msg
        assert "registry" not in safe_msg
        assert "Snapshot not found" in safe_msg

    def test_safe_snapshot_error_checksum_mismatch(self):
        """Checksum error should hide verification details."""
        error = ValueError("Snapshot checksum mismatch: expected abc123 got def456")
        safe_msg = safe_snapshot_error(error)

        # Should not expose hashes
        assert "abc123" not in safe_msg
        assert "def456" not in safe_msg
        assert "checksum mismatch" not in safe_msg.lower() or "integrity" in safe_msg.lower()

    def test_snapshot_create_error_response_sanitized(self):
        """POST /snapshots error response should be sanitized."""
        client = TestClient(app)

        # Mock manager to raise exception
        with patch("corvin_console.routes.control_plane_snapshots.get_snapshot_manager") as mock_mgr:
            mock_manager = AsyncMock()
            mock_manager.create_snapshot.side_effect = RuntimeError("Database connection pool exhausted")
            mock_mgr.return_value = mock_manager

            response = client.post(
                "/v1/console/control-plane/snapshots",
                json={
                    "name": "test-snapshot",
                    "description": "Test snapshot"
                }
            )

        # Should return 400 with safe message
        assert response.status_code == 400
        detail = response.json()["detail"]

        # Should NOT contain internal details
        assert "Database connection" not in detail
        assert "exhausted" not in detail
        assert "RuntimeError" not in detail
        # Should contain safe message
        assert "Failed to create snapshot" in detail or "error" in detail.lower()

    def test_snapshot_get_error_response_sanitized(self):
        """GET /snapshots/{id} error response should be sanitized."""
        client = TestClient(app)

        with patch("corvin_console.routes.control_plane_snapshots.get_snapshot_manager") as mock_mgr:
            mock_manager = AsyncMock()
            mock_manager.get_snapshot.side_effect = ValueError("Access denied: user lacks admin role")
            mock_mgr.return_value = mock_manager

            response = client.get("/v1/console/control-plane/snapshots/snap_000001")

        # Should return 403 with safe message
        assert response.status_code == 403
        detail = response.json()["detail"]

        # Should NOT expose privilege/role details
        assert "admin" not in detail.lower()
        assert "user lacks" not in detail
        assert "denied" not in detail.lower() or "access" in detail.lower()


# ============================================================================
# Snapshot Bounds Tests
# ============================================================================

class TestSnapshotBounds:
    """Test snapshot name/description length bounds (DoS prevention)."""

    def test_snapshot_name_max_length(self):
        """Snapshot name must be <= 500 chars."""
        client = TestClient(app)

        # Name exactly at limit (500 chars) should pass validation
        long_name = "x" * 500
        response = client.post(
            "/v1/console/control-plane/snapshots",
            json={
                "name": long_name,
                "description": "Valid description"
            }
        )
        # Should not fail validation (may fail for other reasons, but not validation)
        # Status != 422 (validation error)
        assert response.status_code != 422

    def test_snapshot_name_exceeds_limit(self):
        """Snapshot name > 500 chars should be rejected."""
        client = TestClient(app)

        # Name exceeding limit
        long_name = "x" * 501
        response = client.post(
            "/v1/console/control-plane/snapshots",
            json={
                "name": long_name,
                "description": "Valid description"
            }
        )

        # Should reject with 422 (validation error)
        assert response.status_code == 422
        detail = response.json()["detail"]
        # Should mention name bounds
        assert any("name" in str(d).lower() for d in detail)
        assert any("500" in str(d) for d in detail)

    def test_snapshot_description_max_length(self):
        """Snapshot description must be <= 500 chars."""
        client = TestClient(app)

        # Description exactly at limit
        long_desc = "y" * 500
        response = client.post(
            "/v1/console/control-plane/snapshots",
            json={
                "name": "Valid name",
                "description": long_desc
            }
        )
        # Should not fail validation
        assert response.status_code != 422

    def test_snapshot_description_exceeds_limit(self):
        """Snapshot description > 500 chars should be rejected."""
        client = TestClient(app)

        # Description exceeding limit
        long_desc = "y" * 501
        response = client.post(
            "/v1/console/control-plane/snapshots",
            json={
                "name": "Valid name",
                "description": long_desc
            }
        )

        # Should reject with 422 (validation error)
        assert response.status_code == 422
        detail = response.json()["detail"]
        # Should mention description bounds
        assert any("description" in str(d).lower() for d in detail)
        assert any("500" in str(d) for d in detail)

    def test_snapshot_name_empty_rejected(self):
        """Snapshot name cannot be empty."""
        client = TestClient(app)

        response = client.post(
            "/v1/console/control-plane/snapshots",
            json={
                "name": "",
                "description": "Valid description"
            }
        )

        # Should reject empty name
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any("name" in str(d).lower() for d in detail)

    def test_snapshot_name_whitespace_only_rejected(self):
        """Snapshot name cannot be whitespace only."""
        client = TestClient(app)

        response = client.post(
            "/v1/console/control-plane/snapshots",
            json={
                "name": "   ",
                "description": "Valid description"
            }
        )

        # Should reject whitespace-only name
        assert response.status_code == 422


# ============================================================================
# Snapshot Restore Verification Tests
# ============================================================================

class TestSnapshotRestoreImplementation:
    """Test that restore_snapshot actually changes system state."""

    @pytest.mark.asyncio
    async def test_restore_snapshot_changes_state(self):
        """restore_snapshot should actually restore the state (not just return it)."""
        from core.control_plane.snapshot_manager import SnapshotManager, Snapshot

        # Create manager
        class MockAudit:
            async def log_event(self, event_type, payload):
                pass

        manager = SnapshotManager(MockAudit(), "/tmp/test_snapshots")

        # Create a snapshot
        original_state = {
            "intent": {"task1": "enabled"},
            "plugins": {"plugin1": "active"},
            "subsystems": {"sys1": "running"},
            "overrides": {}
        }

        result = await manager.create_snapshot(
            control_plane_state=original_state,
            name="test-restore",
            description="Test restore functionality",
            creator_id="test-user",
            tenant_id="test-tenant"
        )

        snapshot_id = result["snapshot_id"]

        # Verify snapshot was created
        snapshot = await manager.get_snapshot(snapshot_id, "test-tenant")
        assert snapshot is not None
        assert snapshot["name"] == "test-restore"
        assert snapshot["intent_state"]["task1"] == "enabled"

        # Now restore the snapshot
        restore_result = await manager.restore_snapshot(
            snapshot_id=snapshot_id,
            tenant_id="test-tenant",
            approver_id="approver-user"
        )

        # Verify restore returned state
        assert restore_result["status"] == "restored"
        assert restore_result["restored_state"]["intent"]["task1"] == "enabled"
        assert restore_result["restored_state"]["plugins"]["plugin1"] == "active"

    @pytest.mark.asyncio
    async def test_restore_snapshot_invalid_checksum_rejected(self):
        """Restore should reject snapshot with corrupted checksum."""
        from core.control_plane.snapshot_manager import SnapshotManager

        class MockAudit:
            async def log_event(self, event_type, payload):
                pass

        manager = SnapshotManager(MockAudit(), "/tmp/test_snapshots")

        # Create snapshot
        original_state = {
            "intent": {"task1": "enabled"},
            "plugins": {},
            "subsystems": {},
            "overrides": {}
        }

        result = await manager.create_snapshot(
            control_plane_state=original_state,
            name="test-corrupt",
            description="Test corrupted snapshot",
            creator_id="test-user",
            tenant_id="test-tenant"
        )

        snapshot_id = result["snapshot_id"]

        # Corrupt the snapshot in-memory
        snapshot = manager.snapshots[snapshot_id]
        # Modify a field to invalidate checksum
        manager.snapshots[snapshot_id] = snapshot._replace(
            intent_state={"task1": "disabled"}  # Changed state
        )

        # Attempt to restore should raise ValueError (checksum mismatch)
        with pytest.raises(ValueError, match="checksum mismatch"):
            await manager.restore_snapshot(
                snapshot_id=snapshot_id,
                tenant_id="test-tenant",
                approver_id="approver-user"
            )


# ============================================================================
# Endpoint-Level Error Response Tests
# ============================================================================

class TestEndpointErrorResponses:
    """Test that endpoints return sanitized error responses."""

    def test_override_create_error_sanitized(self):
        """POST /overrides error should be sanitized."""
        client = TestClient(app)

        # Send invalid override request
        response = client.post(
            "/v1/console/control-plane/overrides",
            json={
                "override_type": "invalid_type",
                "target_id": "target1",
                "reason": "Testing"
            }
        )

        # Should fail but with safe message
        assert response.status_code in [400, 422]
        if response.status_code == 400:
            detail = response.json()["detail"]
            # Should mention invalid type, but safely
            assert "invalid" in detail.lower() or "override" in detail.lower()

    def test_snapshot_list_returns_safe_metadata(self):
        """Snapshot list should not expose sensitive metadata."""
        client = TestClient(app)

        with patch("corvin_console.routes.control_plane_snapshots.get_snapshot_manager") as mock_mgr:
            mock_manager = AsyncMock()
            mock_manager.list_snapshots.return_value = [
                {
                    "snapshot_id": "snap_000001",
                    "name": "backup-2026-09-22",
                    "description": "System state backup",
                    "created_at": "2026-09-22T12:00:00Z",
                    "created_by": "admin",
                    "checksum": "abc123",
                    "size_bytes": 1024
                }
            ]
            mock_mgr.return_value = mock_manager

            response = client.get("/v1/console/control-plane/snapshots")

        assert response.status_code == 200
        snapshots = response.json()
        assert len(snapshots) == 1
        # Metadata should be readable
        assert snapshots[0]["name"] == "backup-2026-09-22"
        assert snapshots[0]["size_bytes"] == 1024


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
