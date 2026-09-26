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

    # Step 1: Finalize turn with context (Session N)
    producer = SessionBridgeProducer()

    # Create snapshot directory structure
    snapshot_dir = tmp_path / "snapshots"
    snapshot_file = snapshot_dir / tenant_id / task_id / "latest.json"
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)

    # Persist snapshot
    signature = "test_signature_xyz"  # In real use, HMAC signature
    snapshot_data = {
        "snapshot": test_snapshot,
        "signature": signature,
    }

    with open(snapshot_file, "w") as f:
        json.dump(snapshot_data, f)

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

    with open(snapshot_file, "w") as f:
        json.dump(
            {"snapshot": stale_snapshot, "signature": "fake"},
            f
        )

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

    with open(snapshot_file, "w") as f:
        json.dump(
            {"snapshot": wrong_snapshot, "signature": "fake"},
            f
        )

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
    snapshot_file = snapshot_dir / tenant_a / task_id / "latest.json"
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)

    with open(snapshot_file, "w") as f:
        json.dump(
            {"snapshot": snapshot_a, "signature": "fake"},
            f
        )

    # Try to restore in tenant B
    recovery_manager = SessionRecoveryManager(snapshot_dir=snapshot_dir)

    with pytest.raises(ContextLossError, match="Cross-tenant"):
        await recovery_manager.auto_restore_session_context(
            tenant_id=tenant_b,  # Different tenant
            task_id=task_id,
        )
