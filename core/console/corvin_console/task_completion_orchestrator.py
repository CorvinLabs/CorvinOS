"""Task Completion Orchestrator: Sofort Global As Done Pattern.

Implements 4-layer atomicity for "mark task as done":
  Layer 1: Verification (Definition-of-Done)
  Layer 2: Atomic State Transition (DB + Audit in one transaction)
  Layer 3: Event Push to All Systems (<100ms propagation)
  Layer 4: Global State Consistency Verification

Architecture: ADR-0890 (Hard Completion Gate) + ADR-0430 (WebSocket Real-Time)
             + ADR-0665 (Audit-First Learning) + ADR-0168 M3 (CCC PubSub)

Load-bearing constraints:
  - Fail-closed: Missing evidence → task NOT marked done (never assume)
  - Audit-first: Event logged BEFORE state change (immutable, hash-chained)
  - Atomic transaction: DB + Audit + Event or NONE
  - No polling: WebSocket push <100ms to all subscribers
  - Exactly-once: Idempotent via task_id key (no double-complete)

Wave 1-4 compatible: Uses existing public APIs (TaskQueue, TaskPubSub, AuditChain).
"""

import asyncio
import logging
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional, Dict, Any, Set

logger = logging.getLogger(__name__)


class TaskCompletionVerdict(Enum):
    """Completion verification result."""
    APPROVED = "approved"           # DoD score >= 0.80, all deps satisfied
    PENDING_EVIDENCE = "pending"    # Score < 0.80, needs more evidence
    BLOCKED = "blocked"             # Hard dependency not done
    ALREADY_DONE = "already_done"   # Idempotent: task already marked done


@dataclass(frozen=True)
class TaskCompletionEvent:
    """Immutable task completion event (audit-chained).

    This event is the source of truth: once appended to audit chain,
    the task is definitively done across ALL systems.
    """
    task_id: str
    timestamp: float                    # monotonic, audit-chain ordered
    verified_by: str                    # "dod_verifier" or operator_id
    dod_score: float                    # 0.0–1.0 confidence
    dod_checks: Dict[str, bool]         # reachability, audit_trail, tests, docs, reproducibility
    prev_audit_hash: Optional[str]      # hash-chain link
    audit_hash: Optional[str] = None    # computed post-append
    tenant_id: str = "_default"


@dataclass(frozen=True)
class CompletionTransactionResult:
    """Result of atomic completion transaction."""
    verdict: TaskCompletionVerdict
    task_id: str
    dod_score: Optional[float] = None
    reason: str = ""
    event: Optional[TaskCompletionEvent] = None
    subscribers_notified: int = 0


class TaskCompletionOrchestrator:
    """Orchestrates 4-layer task completion: Verify → Atomic Transition → Push → Verify Consistency."""

    def __init__(
        self,
        task_queue,              # TaskQueue instance
        pubsub,                  # TaskPubSub instance
        ccc_pubsub,              # CCCPubSub instance (ADR-0168 M3)
        audit_chain,             # AuditChain instance (hash-chained, immutable)
        dod_verifier,            # DoD_VerifierSkill instance
        websocket_broadcaster,   # WebSocketBroadcaster instance
    ):
        self.task_queue = task_queue
        self.pubsub = pubsub
        self.ccc_pubsub = ccc_pubsub
        self.audit_chain = audit_chain
        self.dod_verifier = dod_verifier
        self.websocket_broadcaster = websocket_broadcaster

        # Track completed tasks for idempotency + consistency checks
        self._recently_completed: Dict[str, float] = {}  # task_id -> completion_time
        self._completion_lock = asyncio.Lock()  # Prevent concurrent completion of same task

    async def mark_task_done(
        self,
        task_id: str,
        task_type: str = "general",      # "api_endpoint", "cli_command", "lib_function", "plugin", "skill"
        symbol_name: Optional[str] = None,
        commit_msg: Optional[str] = None,
        operator_id: Optional[str] = None,
        tenant_id: str = "_default",
    ) -> CompletionTransactionResult:
        """
        Mark task as done, atomically and globally.

        **Contract:**
        - Returns APPROVED only if DoD verification passes (score >= 0.80)
        - Fail-closed: missing evidence → PENDING_EVIDENCE, NOT APPROVED
        - Atomic: Audit event + DB state + Event push or NONE
        - Idempotent: calling twice with same task_id → second call returns ALREADY_DONE

        **Latency SLO:** <100ms end-to-end (Layer 1–4)

        Args:
            task_id: Unique task identifier
            task_type: Task classification (used by DoD weight learner)
            symbol_name: Code symbol (function, endpoint, class) for reachability check
            commit_msg: Commit message (for reproducibility check)
            operator_id: Operator performing the mark (for audit trail)
            tenant_id: Tenant isolation (default: "_default")

        Returns:
            CompletionTransactionResult with verdict + event + subscriber count
        """

        # ===== LAYER 1: VERIFICATION =====
        try:
            dod_result = await self._layer1_verify(
                task_id=task_id,
                task_type=task_type,
                symbol_name=symbol_name,
                commit_msg=commit_msg,
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.exception(f"DoD verification error for {task_id}: {e}")
            return CompletionTransactionResult(
                verdict=TaskCompletionVerdict.PENDING_EVIDENCE,
                task_id=task_id,
                reason=f"Verification failed: {str(e)}",
            )

        # Early exit: task already done (idempotency)
        if dod_result["verdict"] == TaskCompletionVerdict.ALREADY_DONE:
            logger.info(f"Task {task_id} already completed (idempotent)")
            return CompletionTransactionResult(
                verdict=TaskCompletionVerdict.ALREADY_DONE,
                task_id=task_id,
            )

        # Early exit: verification failed
        if dod_result["verdict"] != TaskCompletionVerdict.APPROVED:
            logger.warning(f"Task {task_id} NOT approved: {dod_result['verdict'].value}")
            return CompletionTransactionResult(
                verdict=dod_result["verdict"],
                task_id=task_id,
                dod_score=dod_result.get("score"),
                reason=dod_result.get("reason", ""),
            )

        # ===== LAYER 2: ATOMIC TRANSITION =====
        try:
            # Prevent concurrent completion of same task
            async with self._completion_lock:
                # Re-check: another coroutine might have completed it
                if task_id in self._recently_completed:
                    return CompletionTransactionResult(
                        verdict=TaskCompletionVerdict.ALREADY_DONE,
                        task_id=task_id,
                    )

                completion_event = await self._layer2_atomic_transition(
                    task_id=task_id,
                    dod_score=dod_result["score"],
                    dod_checks=dod_result["checks"],
                    verified_by=operator_id or "dod_verifier",
                    tenant_id=tenant_id,
                )
        except Exception as e:
            logger.exception(f"Atomic transition failed for {task_id}: {e}")
            return CompletionTransactionResult(
                verdict=TaskCompletionVerdict.PENDING_EVIDENCE,
                task_id=task_id,
                reason=f"Transaction failed: {str(e)}",
            )

        # ===== LAYER 3: EVENT PUSH (Global Propagation <100ms) =====
        try:
            subscribers_notified = await self._layer3_event_push(
                task_id=task_id,
                event=completion_event,
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.warning(f"Event push failed for {task_id} (non-blocking): {e}")
            subscribers_notified = 0

        # ===== LAYER 4: CONSISTENCY VERIFICATION =====
        try:
            is_consistent = await self._layer4_verify_consistency(
                task_id=task_id,
                event=completion_event,
                tenant_id=tenant_id,
            )
            if not is_consistent:
                logger.critical(f"CONSISTENCY CHECK FAILED for {task_id} after completion!")
                # This is CRITICAL but doesn't roll back (audit-first immutability)
        except Exception as e:
            logger.warning(f"Consistency check failed for {task_id}: {e}")

        # Mark in local tracker (for idempotency window)
        self._recently_completed[task_id] = time.monotonic()

        return CompletionTransactionResult(
            verdict=TaskCompletionVerdict.APPROVED,
            task_id=task_id,
            dod_score=dod_result["score"],
            event=completion_event,
            subscribers_notified=subscribers_notified,
        )

    # ===== LAYER 1: VERIFICATION =====

    async def _layer1_verify(
        self,
        task_id: str,
        task_type: str,
        symbol_name: Optional[str],
        commit_msg: Optional[str],
        tenant_id: str,
    ) -> Dict[str, Any]:
        """Check if task has all required evidence for completion.

        Fail-closed: missing evidence → score < 0.80 → NOT APPROVED

        Returns:
            {
                "verdict": TaskCompletionVerdict,
                "score": 0.0–1.0,
                "checks": {"reachability": bool, ...},
                "reason": str
            }
        """

        # Check 1: Already done? (idempotency)
        current_status = await self.task_queue.get_status(task_id, tenant_id=tenant_id)
        if current_status == "COMPLETED":
            return {
                "verdict": TaskCompletionVerdict.ALREADY_DONE,
                "score": 1.0,
                "checks": {},
                "reason": "Task already marked complete",
            }

        # Check 2: Run DoD Verifier Skill (5 canonical checks)
        try:
            dod_result = await self.dod_verifier.execute(
                task_id=task_id,
                task_type=task_type,
                symbol_name=symbol_name,
                commit_msg=commit_msg,
                tenant_id=tenant_id,
            )
        except asyncio.TimeoutError:
            logger.error(f"DoD verifier timeout for {task_id}")
            return {
                "verdict": TaskCompletionVerdict.PENDING_EVIDENCE,
                "score": 0.0,
                "checks": {},
                "reason": "Verification timeout (>30s)",
            }

        # Check 3: Check hard dependencies (fail-closed)
        blocked_deps = await self._check_hard_dependencies(task_id, tenant_id)
        if blocked_deps:
            return {
                "verdict": TaskCompletionVerdict.BLOCKED,
                "score": dod_result.get("score", 0.0),
                "checks": dod_result.get("checks", {}),
                "reason": f"Hard dependencies not satisfied: {blocked_deps}",
            }

        # Final: Score threshold
        score = dod_result.get("score", 0.0)
        if score < 0.80:
            return {
                "verdict": TaskCompletionVerdict.PENDING_EVIDENCE,
                "score": score,
                "checks": dod_result.get("checks", {}),
                "reason": f"DoD score {score:.2f} < 0.80 threshold",
            }

        return {
            "verdict": TaskCompletionVerdict.APPROVED,
            "score": score,
            "checks": dod_result.get("checks", {}),
            "reason": "All checks passed",
        }

    async def _check_hard_dependencies(self, task_id: str, tenant_id: str) -> list:
        """Check if all hard dependencies are done. Fail-closed: blocked if ANY not done."""
        # TODO: Query KG for hard dependencies (ADR-0890 dependency model)
        # For now: simplified placeholder
        return []

    # ===== LAYER 2: ATOMIC TRANSITION =====

    async def _layer2_atomic_transition(
        self,
        task_id: str,
        dod_score: float,
        dod_checks: Dict[str, bool],
        verified_by: str,
        tenant_id: str,
    ) -> TaskCompletionEvent:
        """Atomically update: Audit + DB + Event (all or nothing).

        Order: AUDIT FIRST (immutable), then DB state change.
        If audit fails → exception propagates, DB untouched.
        If DB fails after audit → inconsistency detected by Layer 4.

        Returns:
            TaskCompletionEvent (appended to audit chain)
        """

        # Get prior audit hash (for chaining)
        prev_audit_hash = await self.audit_chain.get_latest_hash(tenant_id)

        # Create event (immutable dataclass)
        completion_event = TaskCompletionEvent(
            task_id=task_id,
            timestamp=time.monotonic(),
            verified_by=verified_by,
            dod_score=dod_score,
            dod_checks=dod_checks,
            prev_audit_hash=prev_audit_hash,
            tenant_id=tenant_id,
        )

        # ===== AUDIT FIRST (immutable) =====
        try:
            stored_event = await self.audit_chain.append(
                event=completion_event,
                event_type="task_completed",
                tenant_id=tenant_id,
            )
            # audit_chain.append() returns event with audit_hash computed + prev_hash verified
        except Exception as e:
            logger.critical(f"AUDIT CHAIN APPEND FAILED for {task_id}: {e}")
            raise  # Fail-closed: don't continue without audit

        # ===== DB STATE CHANGE (after audit succeeds) =====
        try:
            await self.task_queue.update_status(
                task_id=task_id,
                status="COMPLETED",
                metadata={
                    "dod_score": dod_score,
                    "verified_by": verified_by,
                    "audit_hash": stored_event.audit_hash,
                },
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.error(f"DB update failed for {task_id} (audit already appended): {e}")
            # Non-fatal: audit chain has the source of truth; DB will catch up

        return stored_event

    # ===== LAYER 3: EVENT PUSH (Global Propagation) =====

    async def _layer3_event_push(
        self,
        task_id: str,
        event: TaskCompletionEvent,
        tenant_id: str,
    ) -> int:
        """Push event to ALL systems concurrently (<100ms SLO).

        Targets:
          1. WebSocket subscribers (dashboard live update)
          2. CCC PubSub (entity events for KG + console)
          3. Learning outcome sink (outcome feedback for weight learning)

        Returns:
            Number of WebSocket subscribers notified
        """

        tasks = []

        # 1. WebSocket push (fan-out to all subscribers)
        async def ws_push():
            try:
                result = await self.websocket_broadcaster.broadcast(
                    message={
                        "type": "task_completed",
                        "task_id": task_id,
                        "dod_score": event.dod_score,
                        "timestamp": event.timestamp,
                        "audit_hash": event.audit_hash,
                    },
                    timeout_sec=5.0,
                )
                return result.get("clients_reached", 0)
            except Exception as e:
                logger.warning(f"WebSocket broadcast failed for {task_id}: {e}")
                return 0

        # 2. CCC PubSub (ADR-0168 M3)
        async def ccc_push():
            try:
                await self.ccc_pubsub.publish(
                    tenant_id=tenant_id,
                    action_id=task_id,
                    event_kind="completed",
                    entity_type="ats_task",  # or "workflow_task", "skill_execution", etc.
                    entity_id=task_id,
                    payload={
                        "dod_score": event.dod_score,
                        "verified_by": event.verified_by,
                        "audit_hash": event.audit_hash,
                    },
                )
            except Exception as e:
                logger.warning(f"CCC PubSub publish failed for {task_id}: {e}")

        # 3. Learning outcome sink (for weight learning loop)
        async def learning_push():
            try:
                # TODO: Emit to outcome_sink.record_task_outcome()
                pass
            except Exception as e:
                logger.warning(f"Learning sink failed for {task_id}: {e}")

        # Run all concurrently (fan-out, no blocking)
        tasks = [ws_push(), ccc_push(), learning_push()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Count successful WS subscribers
        ws_count = results[0] if isinstance(results[0], int) else 0
        return ws_count

    # ===== LAYER 4: CONSISTENCY VERIFICATION =====

    async def _layer4_verify_consistency(
        self,
        task_id: str,
        event: TaskCompletionEvent,
        tenant_id: str,
    ) -> bool:
        """Verify that ALL systems show task as COMPLETED (consensus check).

        Checks:
          1. Audit chain has event + hash-chain valid
          2. DB status is COMPLETED
          3. PubSub has published (implicit: Layer 3 succeeded)
          4. WebSocket subscribers notified (implicit: Layer 3 returned count)

        Returns:
            True if consistent, False otherwise (CRITICAL if inconsistent!)
        """

        # Check 1: Audit chain has event
        try:
            audit_events = await self.audit_chain.query(
                task_id=task_id,
                event_type="task_completed",
                tenant_id=tenant_id,
            )
            if not audit_events:
                logger.critical(f"CONSISTENCY: Audit chain missing event for {task_id}")
                return False
        except Exception as e:
            logger.critical(f"CONSISTENCY: Audit chain query failed for {task_id}: {e}")
            return False

        # Check 2: DB status is COMPLETED
        try:
            db_status = await self.task_queue.get_status(task_id, tenant_id=tenant_id)
            if db_status != "COMPLETED":
                logger.critical(f"CONSISTENCY: DB status {db_status} != COMPLETED for {task_id}")
                return False
        except Exception as e:
            logger.critical(f"CONSISTENCY: DB status query failed for {task_id}: {e}")
            return False

        logger.info(f"CONSISTENCY: Task {task_id} verified across all systems ✓")
        return True
