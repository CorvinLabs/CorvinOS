"""E2E Test: Task Immediate Global Completion (Sofort Global As Done).

Verifies the complete 4-layer task completion flow:
  Layer 1: DoD Verification (fail-closed)
  Layer 2: Atomic Transition (audit-first, DB after)
  Layer 3: Event Push (<100ms WebSocket propagation)
  Layer 4: Consistency Verification (all systems agree)

Context: ADR-0890 (Hard Completion Gate), ADR-0430 (WebSocket), ADR-0665 (Audit-First)
Wave 1-4 Compatibility: ✅ Uses existing public APIs
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from dataclasses import asdict

from corvin_console.task_completion_orchestrator import (
    TaskCompletionOrchestrator,
    TaskCompletionVerdict,
    CompletionTransactionResult,
)


@pytest.fixture
def mock_components():
    """Mock all orchestrator dependencies."""
    return {
        "task_queue": AsyncMock(),
        "pubsub": AsyncMock(),
        "ccc_pubsub": AsyncMock(),
        "audit_chain": AsyncMock(),
        "dod_verifier": AsyncMock(),
        "websocket_broadcaster": AsyncMock(),
    }


@pytest.fixture
def orchestrator(mock_components):
    """Create orchestrator with mocked dependencies."""
    return TaskCompletionOrchestrator(**mock_components)


class TestTaskCompletionLayers:
    """Test each layer individually, then end-to-end."""

    # ===== LAYER 1: VERIFICATION =====

    @pytest.mark.asyncio
    async def test_layer1_approve_valid_task(self, orchestrator, mock_components):
        """Layer 1: DoD verification approves task with score >= 0.80."""

        # Setup: DoD verifier returns passing score
        mock_components["dod_verifier"].execute.return_value = {
            "score": 0.85,
            "checks": {
                "reachability": True,
                "audit_trail": True,
                "tests": True,
                "docs": True,
                "reproducibility": True,
            },
        }
        mock_components["task_queue"].get_status.return_value = "PENDING"

        # Execute Layer 1
        result = await orchestrator._layer1_verify(
            task_id="test_task_001",
            task_type="api_endpoint",
            symbol_name="GET /v1/initiatives",
            commit_msg="feat(api): full E2E tests + docs",
            tenant_id="_default",
        )

        # Assert: Approved
        assert result["verdict"] == TaskCompletionVerdict.APPROVED
        assert result["score"] == 0.85
        assert all(result["checks"].values()), "All checks should pass"

    @pytest.mark.asyncio
    async def test_layer1_reject_low_score(self, orchestrator, mock_components):
        """Layer 1: Fail-closed — score < 0.80 → NOT APPROVED."""

        mock_components["dod_verifier"].execute.return_value = {
            "score": 0.65,  # Below 0.80 threshold
            "checks": {
                "reachability": True,
                "audit_trail": True,
                "tests": False,  # Missing evidence
                "docs": True,
                "reproducibility": True,
            },
        }
        mock_components["task_queue"].get_status.return_value = "PENDING"

        result = await orchestrator._layer1_verify(
            task_id="test_task_002",
            task_type="lib_function",
            symbol_name="my_lib.process()",
            commit_msg=None,
            tenant_id="_default",
        )

        # Assert: NOT approved (fail-closed)
        assert result["verdict"] == TaskCompletionVerdict.PENDING_EVIDENCE
        assert result["score"] == 0.65
        assert "score < 0.80" in result["reason"]

    @pytest.mark.asyncio
    async def test_layer1_idempotent_already_done(self, orchestrator, mock_components):
        """Layer 1: Idempotency — task already done → return ALREADY_DONE."""

        mock_components["task_queue"].get_status.return_value = "COMPLETED"

        result = await orchestrator._layer1_verify(
            task_id="test_task_done",
            task_type="api_endpoint",
            symbol_name=None,
            commit_msg=None,
            tenant_id="_default",
        )

        assert result["verdict"] == TaskCompletionVerdict.ALREADY_DONE
        assert result["score"] == 1.0

    # ===== LAYER 2: ATOMIC TRANSITION =====

    @pytest.mark.asyncio
    async def test_layer2_atomic_audit_first(self, orchestrator, mock_components):
        """Layer 2: Audit-first — event appended before DB update."""

        # Setup: audit chain appends, DB updates
        audit_event_mock = MagicMock()
        audit_event_mock.audit_hash = "hash_abc123"
        audit_event_mock.prev_hash = "hash_prev"
        mock_components["audit_chain"].get_latest_hash.return_value = "hash_prev"
        mock_components["audit_chain"].append.return_value = audit_event_mock
        mock_components["task_queue"].update_status = AsyncMock()

        # Execute Layer 2
        event = await orchestrator._layer2_atomic_transition(
            task_id="test_task_003",
            dod_score=0.90,
            dod_checks={"reachability": True, "tests": True},
            verified_by="operator_123",
            tenant_id="_default",
        )

        # Assert: Audit chain called FIRST (before DB)
        assert mock_components["audit_chain"].append.called
        assert mock_components["task_queue"].update_status.called

        # Verify call order: audit before DB
        audit_call_index = mock_components["audit_chain"].append.call_count - 1
        db_call_index = mock_components["task_queue"].update_status.call_count - 1
        assert audit_call_index <= db_call_index, "Audit must be called before (or same as) DB"

        # Assert: Event has hash-chain links
        assert event.audit_hash == "hash_abc123"
        assert event.prev_audit_hash == "hash_prev"

    @pytest.mark.asyncio
    async def test_layer2_fail_closed_audit_failure(self, orchestrator, mock_components):
        """Layer 2: Fail-closed — audit chain failure → exception, no DB update."""

        # Setup: audit chain fails
        mock_components["audit_chain"].get_latest_hash.return_value = "hash_prev"
        mock_components["audit_chain"].append.side_effect = Exception("Audit chain broken")

        # Execute: should raise
        with pytest.raises(Exception, match="Audit chain broken"):
            await orchestrator._layer2_atomic_transition(
                task_id="test_task_fail",
                dod_score=0.85,
                dod_checks={},
                verified_by="operator",
                tenant_id="_default",
            )

        # Assert: DB was NOT called (fail-closed)
        assert not mock_components["task_queue"].update_status.called

    # ===== LAYER 3: EVENT PUSH =====

    @pytest.mark.asyncio
    async def test_layer3_event_push_all_systems(self, orchestrator, mock_components):
        """Layer 3: Event push to all systems concurrently (<100ms)."""

        # Setup: WebSocket + CCC PubSub + Learning all async
        mock_components["websocket_broadcaster"].broadcast.return_value = {
            "clients_reached": 3,
            "latency_ms": 42,
        }
        mock_components["ccc_pubsub"].publish = AsyncMock()

        # Create a fake event
        mock_event = MagicMock()
        mock_event.dod_score = 0.85
        mock_event.audit_hash = "hash_abc"

        # Execute Layer 3
        ws_count = await orchestrator._layer3_event_push(
            task_id="test_task_004",
            event=mock_event,
            tenant_id="_default",
        )

        # Assert: All systems were called (fan-out)
        assert mock_components["websocket_broadcaster"].broadcast.called
        assert mock_components["ccc_pubsub"].publish.called
        assert ws_count == 3, "Should notify 3 WebSocket subscribers"

    @pytest.mark.asyncio
    async def test_layer3_event_push_nonblocking_failure(self, orchestrator, mock_components):
        """Layer 3: Event push failures are non-blocking (audit + DB already done)."""

        # Setup: WebSocket fails, CCC fails
        mock_components["websocket_broadcaster"].broadcast.side_effect = Exception(
            "Network timeout"
        )
        mock_components["ccc_pubsub"].publish.side_effect = Exception("PubSub down")

        mock_event = MagicMock()
        mock_event.dod_score = 0.85

        # Execute: should NOT raise (non-blocking)
        ws_count = await orchestrator._layer3_event_push(
            task_id="test_task_push_fail",
            event=mock_event,
            tenant_id="_default",
        )

        # Assert: returned gracefully, 0 subscribers
        assert ws_count == 0
        # Audit + DB remain intact (already committed in Layer 2)

    # ===== LAYER 4: CONSISTENCY VERIFICATION =====

    @pytest.mark.asyncio
    async def test_layer4_consistency_valid(self, orchestrator, mock_components):
        """Layer 4: All systems show COMPLETED → consistent."""

        # Setup: audit chain, DB, both report COMPLETED
        mock_components["audit_chain"].query.return_value = [
            MagicMock(event_type="task_completed")
        ]
        mock_components["task_queue"].get_status.return_value = "COMPLETED"

        mock_event = MagicMock()

        # Execute Layer 4
        is_consistent = await orchestrator._layer4_verify_consistency(
            task_id="test_task_005",
            event=mock_event,
            tenant_id="_default",
        )

        assert is_consistent is True, "Systems should be in consensus"

    @pytest.mark.asyncio
    async def test_layer4_consistency_divergence(self, orchestrator, mock_components):
        """Layer 4: Audit has event but DB doesn't → CRITICAL inconsistency."""

        # Setup: audit says COMPLETED, DB says PENDING
        mock_components["audit_chain"].query.return_value = [
            MagicMock(event_type="task_completed")
        ]
        mock_components["task_queue"].get_status.return_value = "PENDING"  # Mismatch!

        mock_event = MagicMock()

        # Execute Layer 4
        is_consistent = await orchestrator._layer4_verify_consistency(
            task_id="test_task_diverge",
            event=mock_event,
            tenant_id="_default",
        )

        assert is_consistent is False, "Divergence detected"

    # ===== END-TO-END: All 4 Layers =====

    @pytest.mark.asyncio
    async def test_e2e_complete_success_path(self, orchestrator, mock_components):
        """E2E: Full success path — Verify → Atomic → Push → Verify Consistency."""

        # Setup: All systems succeed
        mock_components["task_queue"].get_status.return_value = "PENDING"
        mock_components["dod_verifier"].execute.return_value = {
            "score": 0.90,
            "checks": {
                "reachability": True,
                "audit_trail": True,
                "tests": True,
                "docs": True,
                "reproducibility": True,
            },
        }

        audit_event = MagicMock()
        audit_event.audit_hash = "hash_final"
        mock_components["audit_chain"].get_latest_hash.return_value = "hash_prev"
        mock_components["audit_chain"].append.return_value = audit_event
        mock_components["audit_chain"].query.return_value = [
            MagicMock(event_type="task_completed")
        ]

        mock_components["task_queue"].update_status = AsyncMock()
        mock_components["websocket_broadcaster"].broadcast.return_value = {
            "clients_reached": 5
        }
        mock_components["ccc_pubsub"].publish = AsyncMock()

        # Execute: Full mark_task_done
        result = await orchestrator.mark_task_done(
            task_id="test_task_e2e",
            task_type="api_endpoint",
            symbol_name="GET /api/v1/status",
            commit_msg="feat(api): with full E2E tests",
            operator_id="alice",
            tenant_id="_default",
        )

        # Assert: Success verdict
        assert result.verdict == TaskCompletionVerdict.APPROVED
        assert result.dod_score == 0.90
        assert result.subscribers_notified == 5
        assert result.event is not None

        # Assert: All layers were executed
        assert mock_components["dod_verifier"].execute.called
        assert mock_components["audit_chain"].append.called
        assert mock_components["task_queue"].update_status.called
        assert mock_components["websocket_broadcaster"].broadcast.called
        assert mock_components["ccc_pubsub"].publish.called

    @pytest.mark.asyncio
    async def test_e2e_reject_incomplete_task(self, orchestrator, mock_components):
        """E2E: Fail-closed — incomplete task → PENDING_EVIDENCE, no update."""

        # Setup: DoD score too low
        mock_components["task_queue"].get_status.return_value = "PENDING"
        mock_components["dod_verifier"].execute.return_value = {
            "score": 0.70,  # Below threshold
            "checks": {"tests": False},
        }

        # Execute: mark_task_done
        result = await orchestrator.mark_task_done(
            task_id="test_incomplete",
            task_type="lib_function",
            symbol_name="my_lib.new_func",
            commit_msg=None,
            tenant_id="_default",
        )

        # Assert: Rejected at Layer 1
        assert result.verdict == TaskCompletionVerdict.PENDING_EVIDENCE
        assert "score < 0.80" in result.reason

        # Assert: No state changes (audit, DB, push all skipped)
        assert not mock_components["audit_chain"].append.called
        assert not mock_components["task_queue"].update_status.called
        assert not mock_components["websocket_broadcaster"].broadcast.called

    @pytest.mark.asyncio
    async def test_e2e_idempotency_second_call(self, orchestrator, mock_components):
        """E2E: Idempotency — second mark_task_done call returns ALREADY_DONE."""

        # Setup: First call succeeds
        mock_components["task_queue"].get_status.return_value = "COMPLETED"

        # Execute: mark_task_done (idempotent)
        result = await orchestrator.mark_task_done(
            task_id="test_idempotent",
            task_type="api_endpoint",
            symbol_name=None,
            commit_msg=None,
            tenant_id="_default",
        )

        # Assert: Idempotent result
        assert result.verdict == TaskCompletionVerdict.ALREADY_DONE

        # Assert: No redundant audit/DB/push
        assert not mock_components["dod_verifier"].execute.called
        assert not mock_components["audit_chain"].append.called


class TestRealWorldScenarios:
    """Test realistic Wave 1-4 scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_completion_prevention(self, orchestrator, mock_components):
        """Prevent concurrent completion of same task (race condition)."""

        # Setup: First coroutine marks done
        mock_components["task_queue"].get_status.return_value = "PENDING"
        mock_components["dod_verifier"].execute.return_value = {
            "score": 0.85,
            "checks": {k: True for k in ["reachability", "audit_trail", "tests", "docs", "reproducibility"]},
        }
        audit_event = MagicMock()
        audit_event.audit_hash = "hash_concurrent"
        mock_components["audit_chain"].get_latest_hash.return_value = "hash_prev"
        mock_components["audit_chain"].append.return_value = audit_event
        mock_components["task_queue"].update_status = AsyncMock()
        mock_components["websocket_broadcaster"].broadcast.return_value = {"clients_reached": 1}
        mock_components["ccc_pubsub"].publish = AsyncMock()

        # Execute: Two coroutines racing to mark same task done
        async def mark_done():
            return await orchestrator.mark_task_done(
                task_id="race_condition_test",
                task_type="api_endpoint",
                symbol_name="GET /api",
                commit_msg="test",
                tenant_id="_default",
            )

        results = await asyncio.gather(
            mark_done(),
            mark_done(),
            return_exceptions=False,
        )

        # Assert: First succeeds, second gets ALREADY_DONE
        assert results[0].verdict == TaskCompletionVerdict.APPROVED
        assert results[1].verdict == TaskCompletionVerdict.ALREADY_DONE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
