"""E2E Integration Test: Session N → N+1 continuity (FIX #8).

Tests real session finalization, persistence, and recovery without mocks.
Verifies that context snapshots are properly created, stored, and restored
across session boundaries.

Based on Phase 9 production deployment requirements.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any

# Import the real session recovery + producer modules
from core.infinite_session.session_recovery import (
    SessionRecoveryManager,
    SnapshotVerificationError,
    SnapshotExpiredError,
    ContextLossError,
)
from core.infinite_session.session_bridge_producer import SessionBridgeProducer
from core.infinite_session.session_recovery import sign_snapshot


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Scratch CORVIN_HOME + a configured snapshot key (fail-closed without one)."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("CORVIN_SNAPSHOT_KEY", "test-snapshot-key-not-default")


def _write_signed(snapshot_file: Path, snapshot: Dict[str, Any]) -> None:
    """Persist a snapshot the way SessionBridgeProducer does: HMAC-signed."""
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot_file.write_text(json.dumps({"snapshot": snapshot, "signature": sign_snapshot(snapshot)}))


@pytest.mark.asyncio
async def test_session_n_to_n_plus_1_flow(tmp_path):
    """Real integration: finalize, persist, restore (Session N → N+1).

    This test:
    1. Creates a session context in "Session N"
    2. Finalizes and persists snapshot
    3. Verifies snapshot file exists
    4. Starts "Session N+1"
    5. Restores context from snapshot
    6. Verifies all context variables are restored
    """

    # Setup: Session N context
    tenant_id = "_default"
    task_id = "test_task_001"
    session_id = "session_n"

    # Create test snapshot data (what Session N would produce)
    test_snapshot = {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "session_id": session_id,
        "worktree_path": "/home/user/work",
        "base_commit": "abc123def456",
        "phase_name": "Phase 3",
        "conversation_turn_count": 5,
        "timestamp": datetime.utcnow().isoformat(),
        "dest_session_id": task_id,  # For validation in FIX #6
        "content_hash": "sha256_xyz123",
    }

    # Create snapshot directory structure
    snapshot_dir = tmp_path / "snapshots"
    snapshot_file = snapshot_dir / tenant_id / task_id / "latest.json"
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)

    # Persist snapshot (HMAC-signed, as the producer does)
    _write_signed(snapshot_file, test_snapshot)

    # Verify snapshot file created
    assert snapshot_file.exists()
    assert snapshot_file.stat().st_size > 0

    # Step 2: Start Session N+1
    recovery_manager = SessionRecoveryManager(
        snapshot_dir=snapshot_dir
    )

    # Step 3: Restore context from snapshot
    restored_context = await recovery_manager.auto_restore_session_context(
        tenant_id=tenant_id,
        task_id=task_id,
    )

    # Step 4: Verify restoration
    assert restored_context is not None
    assert restored_context["task_id"] == task_id
    assert restored_context["tenant_id"] == tenant_id
    assert restored_context["phase_name"] == "Phase 3"
    assert restored_context["conversation_turn_count"] == 5


@pytest.mark.asyncio
async def test_snapshot_timestamp_validation(tmp_path):
    """FIX #6: Reject snapshots older than 24 hours."""

    tenant_id = "_default"
    task_id = "test_stale_snapshot"

    # Create stale snapshot (25 hours old)
    stale_timestamp = (
        datetime.utcnow() - timedelta(hours=25)
    ).isoformat()

    stale_snapshot = {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "session_id": "old_session",
        "timestamp": stale_timestamp,
        "dest_session_id": task_id,
        "content_hash": "sha256_old",
    }

    # Write to disk
    snapshot_dir = tmp_path / "snapshots"
    snapshot_file = snapshot_dir / tenant_id / task_id / "latest.json"
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)

    _write_signed(snapshot_file, stale_snapshot)

    # Attempt recovery
    recovery_manager = SessionRecoveryManager(snapshot_dir=snapshot_dir)

    # Should raise SnapshotExpiredError (FIX #6)
    with pytest.raises(SnapshotExpiredError):
        await recovery_manager.auto_restore_session_context(
            tenant_id=tenant_id,
            task_id=task_id,
        )


@pytest.mark.asyncio
async def test_snapshot_destination_validation(tmp_path):
    """FIX #6: Reject snapshots with mismatched destination session."""

    tenant_id = "_default"
    task_id = "test_task_wrong_dest"

    # Create snapshot with wrong destination
    wrong_snapshot = {
        "tenant_id": tenant_id,
        "task_id": "different_task_id",
        "session_id": "session_x",
        "timestamp": datetime.utcnow().isoformat(),
        "dest_session_id": "totally_different_task",  # Won't match task_id
        "content_hash": "sha256_wrong",
    }

    snapshot_dir = tmp_path / "snapshots"
    snapshot_file = snapshot_dir / tenant_id / task_id / "latest.json"
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)

    _write_signed(snapshot_file, wrong_snapshot)

    recovery_manager = SessionRecoveryManager(snapshot_dir=snapshot_dir)

    # Should raise ContextLossError due to destination mismatch (FIX #6)
    with pytest.raises(ContextLossError, match="destination mismatch"):
        await recovery_manager.auto_restore_session_context(
            tenant_id=tenant_id,
            task_id=task_id,
        )


@pytest.mark.asyncio
async def test_snapshot_no_prior_context(tmp_path):
    """Graceful handling: no snapshot for a task returns None (not error)."""

    tenant_id = "_default"
    task_id = "brand_new_task_no_prior_session"

    snapshot_dir = tmp_path / "snapshots"

    recovery_manager = SessionRecoveryManager(snapshot_dir=snapshot_dir)

    # No snapshot exists
    restored = await recovery_manager.auto_restore_session_context(
        tenant_id=tenant_id,
        task_id=task_id,
    )

    # Should return None gracefully (new session, no prior context)
    assert restored is None


@pytest.mark.asyncio
async def test_cross_tenant_snapshot_rejected(tmp_path):
    """Verify FIX #6: Cross-tenant snapshots are rejected."""

    tenant_a = "tenant_a"
    tenant_b = "tenant_b"
    task_id = "test_cross_tenant"

    # Create snapshot in tenant A
    snapshot_a = {
        "tenant_id": tenant_a,  # Created in tenant A
        "task_id": task_id,
        "timestamp": datetime.utcnow().isoformat(),
        "dest_session_id": task_id,
        "content_hash": "sha256_a",
    }

    snapshot_dir = tmp_path / "snapshots"
    recovery_manager = SessionRecoveryManager(snapshot_dir=snapshot_dir)

    # (1) Path scoping: tenant A's snapshot is invisible to tenant B.
    _write_signed(snapshot_dir / tenant_a / task_id / "latest.json", snapshot_a)
    assert await recovery_manager.auto_restore_session_context(
        tenant_id=tenant_b, task_id=task_id,
    ) is None

    # (2) A validly signed tenant-A snapshot planted under tenant B's path is
    # rejected by the tenant check, not restored into tenant B.
    _write_signed(snapshot_dir / tenant_b / task_id / "latest.json", snapshot_a)
    with pytest.raises(ContextLossError, match="Cross-tenant"):
        await recovery_manager.auto_restore_session_context(
            tenant_id=tenant_b,  # Different tenant
            task_id=task_id,
        )


@pytest.mark.asyncio
async def test_forged_signature_rejected(tmp_path):
    """A snapshot whose signature was not produced with the key is rejected."""
    tenant_id, task_id = "_default", "forged"
    snap = {
        "tenant_id": tenant_id, "task_id": task_id,
        "timestamp": datetime.utcnow().isoformat(), "content_hash": "x",
    }
    snapshot_dir = tmp_path / "snapshots"
    f = snapshot_dir / tenant_id / task_id / "latest.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"snapshot": snap, "signature": "fake"}))
    with pytest.raises(SnapshotVerificationError):
        await SessionRecoveryManager(snapshot_dir=snapshot_dir).auto_restore_session_context(
            tenant_id=tenant_id, task_id=task_id,
        )


@pytest.mark.asyncio
async def test_restore_fails_closed_without_key(tmp_path, monkeypatch):
    """No snapshot key configured → restore refuses (never verifies with a default)."""
    tenant_id, task_id = "_default", "nokey"
    snap = {"tenant_id": tenant_id, "task_id": task_id, "timestamp": datetime.utcnow().isoformat()}
    snapshot_dir = tmp_path / "snapshots"
    _write_signed(snapshot_dir / tenant_id / task_id / "latest.json", snap)
    monkeypatch.delenv("CORVIN_SNAPSHOT_KEY")
    with pytest.raises(ValueError, match="CORVIN_SNAPSHOT_KEY"):
        await SessionRecoveryManager(snapshot_dir=snapshot_dir).auto_restore_session_context(
            tenant_id=tenant_id, task_id=task_id,
        )
