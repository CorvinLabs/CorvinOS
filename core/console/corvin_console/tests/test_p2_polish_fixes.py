"""Phase 9 P2 Polish Fixes — Error Sanitization & Snapshot Bounds Tests.

Tests for:
1. Error message sanitization (no raw exceptions exposed)
2. Snapshot name/description bounds (max 500 chars, no DoS)
3. Restore snapshot implementation
"""

import sys
import tempfile
from pathlib import Path

import pytest

from corvin_console.error_handling import safe_error_response, safe_snapshot_error

# The console router is mounted under /v1/console by its hosts; the old tests
# posted to /v1/console/... on the bare ``corvin_console.app.app`` (which
# serves at root) and so only ever saw 404. Use the shared sandbox: real
# router at the real prefix, a real session, a scratch CORVIN_HOME.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_admin_route import _sandbox  # noqa: E402

SNAP = "/v1/console/control-plane/snapshots"


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

    # (The snapshot create/get sanitization tests patched a
    # ``get_snapshot_manager`` the defused router no longer has: every
    # snapshot route answers 501 before any manager could raise — see
    # TestSnapshotRoutesDefused.)


# ============================================================================
# Snapshot routes: defused, fail closed (adversarial review 2026-09-27)
# ============================================================================

class TestSnapshotRoutesDefused:
    """Snapshot capture recorded a hard-coded EMPTY state and restore reported
    success while restoring nothing, so every route now answers 501
    ``not_implemented`` behind a session. The name/description bounds tests
    that used to live here have no subject any more: the request model was
    removed with the handlers, and no input reaches validation."""

    ROUTES = [
        ("post", SNAP, {"name": "x" * 501, "description": "y"}),
        ("get", SNAP, None),
        ("get", SNAP + "/audit-log", None),
        ("get", SNAP + "/snap_000001", None),
        ("post", SNAP + "/snap_000001/restore", {}),
        ("post", SNAP + "/snap_000001/diff", {}),
        ("delete", SNAP + "/snap_000001", None),
    ]

    def test_every_route_is_501_not_implemented_with_a_session(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _):
            for method, path, body in self.ROUTES:
                kw = {"headers": {"X-CSRF-Token": csrf}}
                if body is not None:
                    kw["json"] = body
                r = getattr(client, method)(path, **kw)
                assert r.status_code == 501, (method, path, r.status_code, r.text)
                assert r.json()["detail"]["status"] == "not_implemented"

    def test_every_route_requires_a_session(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            client.cookies.clear()
            for method, path, body in self.ROUTES:
                kw = {"json": body} if body is not None else {}
                r = getattr(client, method)(path, **kw)
                assert r.status_code == 401, (method, path, r.status_code)

    def test_mutations_require_csrf(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            r = client.post(SNAP, json={"name": "n", "description": "d"})
            assert r.status_code == 403, r.text


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

        import tempfile
        manager = SnapshotManager(MockAudit(), tempfile.mkdtemp())

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

        # A restore must never be reported done: nothing applies snapshot
        # state, so it fails closed (adversarial review 2026-09-27).
        from core.control_plane.snapshot_manager import SnapshotRestoreNotImplemented
        with pytest.raises(SnapshotRestoreNotImplemented):
            await manager.restore_snapshot(
                snapshot_id=snapshot_id,
                tenant_id="test-tenant",
                approver_id="approver-user"
            )

    @pytest.mark.asyncio
    async def test_restore_snapshot_invalid_checksum_rejected(self):
        """Restore should reject snapshot with corrupted checksum."""
        from core.control_plane.snapshot_manager import SnapshotManager

        class MockAudit:
            async def log_event(self, event_type, payload):
                pass

        import tempfile
        manager = SnapshotManager(MockAudit(), tempfile.mkdtemp())

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
        import dataclasses
        manager.snapshots[snapshot_id] = dataclasses.replace(
            snapshot, intent_state={"task1": "disabled"}  # Changed state
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
        """POST /overrides with an unknown type → 400/422, no internals."""
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            from core.compliance import consent_store
            from corvin_console import auth as _auth
            from corvin_console.routes import control_plane_overrides as ov

            consent_store._stores.clear()
            ov._authority = None
            sid = client.cookies.get("corvin_console_sid")
            consent_store.get_consent_store("_default", corvin_home=home).grant_consent(
                user_id=_auth.load_session(sid).sid_fingerprint,
                scope="control_plane_override_operations")
            response = client.post(
                "/v1/console/control-plane/overrides",
                headers={"X-CSRF-Token": csrf},
                json={"override_type": "invalid_type", "target_id": "target1",
                      "reason": "Testing"},
            )
            ov._authority = None

        assert response.status_code in (400, 422), response.text
        body = response.text
        assert "Traceback" not in body and "File \"" not in body
        if response.status_code == 400:
            detail = response.json()["detail"]
            assert "invalid" in detail.lower() or "override" in detail.lower()

    def test_snapshot_list_is_not_implemented_never_sample_data(self):
        """The list route must not serve fabricated snapshot metadata."""
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            response = client.get(SNAP)
        assert response.status_code == 501
        assert "snapshots" not in response.json()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
