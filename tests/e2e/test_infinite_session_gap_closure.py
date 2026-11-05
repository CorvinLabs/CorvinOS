"""E2E test for Infinite Session context recovery (ADR-0649, Phase 1 Gap Closure).

Tests that:
1. A session can resume context from a prior session bridge
2. The recovered context is injected into the system prompt
3. The user is NOT prompted "new session?" when context is recovered
4. Audit trail shows context_recovered event
"""

import json
import pytest
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone

from core.console.corvin_console.chat_runtime import (
    WebChatSession,
    _infinite_session_context_block,
)
from core.infinite_session import (
    EventStore,
    SessionBridger,
    SessionBridgeEvent,
    Snapshot,
    CryptoBinding,
)


@pytest.fixture
def tenant_id():
    return "_default"


@pytest.fixture
def task_id():
    return "test_task_001"


@pytest.fixture
def session_id():
    return "test_session_001"


@pytest.fixture
def event_store(tenant_id, tmp_path):
    """Create a tenant-bound EventStore for testing."""
    store = EventStore(tenant_id=tenant_id)
    store.root_dir.parent.mkdir(parents=True, exist_ok=True)
    return store


@pytest.fixture
def crypto_binding(event_store):
    """Create a CryptoBinding for signing bridges."""
    return CryptoBinding(event_store.root_dir.parent)


@pytest.fixture
def session_bridger(event_store, crypto_binding):
    """Create a SessionBridger for creating/resuming bridges."""
    return SessionBridger(event_store, crypto_binding)


def test_context_recovered_in_system_prompt(event_store, crypto_binding, session_bridger, tenant_id, task_id, session_id):
    """PHASE 1 TEST: Verify context is recovered and injected into prompt."""

    # 1. Create a snapshot with prior session context
    prior_context = {
        "goal": "Complete the marketing campaign",
        "progress": "50% done — waiting for approval",
        "blockers": ["Approval from stakeholder", "Design review"],
        "next_step": "Follow up with approver",
    }

    # Real API (core/infinite_session): Snapshot.create() computes the content
    # hash, EventStore.write_snapshot() persists + chain-signs it, and
    # SessionBridger.create_bridge() signs and persists the bridge.
    snapshot = Snapshot.create(
        tenant_id=tenant_id,
        task_id=task_id,
        phase_id="phase_2",
        state_dict=prior_context,
    )

    # 2. Write snapshot to EventStore
    ok, error = event_store.write_snapshot(snapshot)
    assert ok, f"Failed to write snapshot: {error}"

    # 3+4. Create, sign and persist a bridge from prior session to new session
    bridge, error = session_bridger.create_bridge(
        tenant_id=tenant_id,
        task_id=task_id,
        source_session_id="prior_session_id",
        dest_session_id="new_session_id",
        snapshot=snapshot,
        phase_completed="phase_2",
        artifacts=["artifact_1.json", "artifact_2.png"],
        metadata={"reason": "automatic_session_transition"},
    )
    assert bridge is not None, f"Failed to create bridge: {error}"

    # 5. Create a WebChatSession with task_id (THIS IS THE KEY FIX FOR PHASE 1)
    sess = WebChatSession(
        sid="new_session_id",
        tenant_id=tenant_id,
        created_at=datetime.now(timezone.utc).timestamp(),
        last_active_at=datetime.now(timezone.utc).timestamp(),
        task_id=task_id,  # <-- NEW: task_id is now available in WebChatSession
    )

    # 6. Call _infinite_session_context_block() to load context from bridge
    context_block = _infinite_session_context_block(sess)

    # 7. Verify context was recovered and formatted
    assert context_block, "Context block should not be empty (bridge was found and loaded)"
    assert "RECOVERED CONTEXT" in context_block
    assert "marketing campaign" in context_block.lower() or "Complete" in context_block

    # 8. Verify that prompt does NOT contain "neue session?" or "new session?"
    # (This is a negative test — the system should NOT ask for session confirmation)
    assert "neue session" not in context_block.lower()
    assert "new session?" not in context_block.lower()


def test_context_not_recovered_when_no_bridge(event_store, tenant_id, task_id):
    """PHASE 1 TEST: Verify graceful fallback when no bridge exists."""

    # Create a WebChatSession for a task with no prior bridges
    sess = WebChatSession(
        sid="new_session_no_bridge",
        tenant_id=tenant_id,
        created_at=datetime.now(timezone.utc).timestamp(),
        last_active_at=datetime.now(timezone.utc).timestamp(),
        task_id=task_id,  # Task exists, but no bridge for it
    )

    # Call _infinite_session_context_block()
    context_block = _infinite_session_context_block(sess)

    # Should return empty string (fail-safe, not fail-closed)
    assert context_block == "", "Should return empty string when no bridge exists"


def test_context_not_recovered_when_no_task_id(tenant_id):
    """PHASE 1 TEST: Verify that context is not loaded without task_id."""

    # Create a WebChatSession WITHOUT task_id
    sess = WebChatSession(
        sid="session_no_task_id",
        tenant_id=tenant_id,
        created_at=datetime.now(timezone.utc).timestamp(),
        last_active_at=datetime.now(timezone.utc).timestamp(),
        task_id=None,  # <-- No task_id
    )

    # Call _infinite_session_context_block()
    context_block = _infinite_session_context_block(sess)

    # Should return empty string
    assert context_block == "", "Should return empty string when task_id is None"


def test_audit_trail_context_recovered(event_store, crypto_binding, session_bridger, tenant_id, task_id, session_id):
    """PHASE 1 TEST: Verify audit trail shows context_recovered event."""

    # 1. Setup (similar to test_context_recovered_in_system_prompt)
    prior_context = {"goal": "Test audit trail"}
    snapshot = Snapshot.create(
        tenant_id=tenant_id,
        task_id=task_id,
        phase_id="phase_1",
        state_dict=prior_context,
    )

    ok, error = event_store.write_snapshot(snapshot)
    assert ok, error

    bridge, error = session_bridger.create_bridge(
        tenant_id=tenant_id,
        task_id=task_id,
        source_session_id="prior_session",
        dest_session_id="new_session_audit",
        snapshot=snapshot,
        phase_completed="phase_1",
    )
    assert bridge is not None, error

    # 2. Resume and verify audit events
    sess = WebChatSession(
        sid="new_session_audit",
        tenant_id=tenant_id,
        created_at=datetime.now(timezone.utc).timestamp(),
        last_active_at=datetime.now(timezone.utc).timestamp(),
        task_id=task_id,
    )

    # 3. Load context (which should trigger audit events)
    context_block = _infinite_session_context_block(sess)
    assert context_block  # Context was recovered

    # 4. The restore is recorded CONTENT-FREE on the tenant's core chain
    # (chat_runtime._infinite_session_context_block -> paths.core_audit).
    from core.learning.event_persistence import _resolve_core_audit

    chain = Path(_resolve_core_audit().audit_path())  # the path core_audit wrote to
    assert chain.exists(), "core audit chain was never written"
    records = [json.loads(line) for line in chain.read_text(encoding="utf-8").splitlines() if line.strip()]
    restored = [r for r in records if "infinite_session.context_restored" in json.dumps(r)]
    assert restored, "no infinite_session.context_restored record on the core chain"
    last = json.dumps(restored[-1])
    assert snapshot.content_hash in last
    assert "Test audit trail" not in last  # never the snapshot content


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
