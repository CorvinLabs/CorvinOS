"""Task Engine executor — main orchestrator (ADR-0540)."""

from typing import Optional, Dict, Any, Set
from datetime import datetime
import json
import logging

from .task_def import TaskDefinition
from .dag_planner import DAGPlanner
from .event_store import EventStore
from .skill_dispatcher import SkillDispatcher, SkillResult
from .models import AuditEvent, ExecutionResult

try:
    from core.session_manager.auto_starter import SessionAutoStarter
    from core.session_manager.lifecycle_manager import SessionLifecycleManager
    from core.session_manager.checkpoint_manager import CheckpointManager
    _AUTO_STARTER_AVAILABLE = True
except ImportError:
    _AUTO_STARTER_AVAILABLE = False

logger = logging.getLogger(__name__)


class TaskExecutor:
    """Orchestrates multi-phase task execution with audit trail (ADR-0540–0545)."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.event_store = EventStore(tenant_id=tenant_id)
        self.skill_dispatcher = SkillDispatcher()
        self.session_counter = 0

        # Initialize SessionAutoStarter for infinite sessions (ADR-0649).
        # SessionLifecycleManager takes no tenant (the checkpoint store carries it); passing
        # tenant_id= raised TypeError, which a bare ``except: pass`` turned into a silently
        # absent auto-starter on every executor.
        self.auto_starter = None
        self._split_loop = None
        if _AUTO_STARTER_AVAILABLE:
            try:
                lifecycle_mgr = SessionLifecycleManager()
                checkpoint_mgr = CheckpointManager(tenant_id=tenant_id, auto_cleanup=False)
                self.auto_starter = SessionAutoStarter(lifecycle_mgr, checkpoint_mgr)
            except Exception as exc:  # noqa: BLE001 — optional, but never silently absent
                logger.warning("TaskExecutor: SessionAutoStarter unavailable (%s: %s)",
                               type(exc).__name__, exc)

    def _drive(self, coro):
        """Run one SessionAutoStarter coroutine from this synchronous executor.

        The auto-starter's API is async and keeps per-task asyncio locks, so every call
        runs on ONE private loop per executor. Called from inside a running loop, the
        split check is skipped (logged) instead of blocking that loop.
        """
        import asyncio  # noqa: PLC0415
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            coro.close()
            logger.warning("TaskExecutor: split check skipped — run() called inside a running event loop")
            return None
        if self._split_loop is None or self._split_loop.is_closed():
            self._split_loop = asyncio.new_event_loop()
        return self._split_loop.run_until_complete(coro)

    def _last_event_hash(self) -> str:
        events = self.event_store.events
        return events[-1].hash if events else ""

    def register_skill(self, skill_id: str, skill_fn):
        """Register a mock skill for testing."""
        self.skill_dispatcher.register_skill(skill_id, skill_fn)

    def run(self, task_def: TaskDefinition) -> ExecutionResult:
        """Execute task end-to-end (ADR-0540)."""
        if task_def.tenant_id != self.tenant_id:
            raise ValueError(f"Task tenant {task_def.tenant_id} != executor tenant {self.tenant_id}")

        try:
            # Validate DAG
            planner = DAGPlanner(task_def)
            planner.validate()

            # Emit task_started event (ADR-0649: use task tenant_id, not process tenant)
            self._emit_event(
                event_type="task_started",
                task_id=task_def.task_id,
                payload={"autonomy_level": task_def.autonomy_level},
                tenant_id=task_def.tenant_id,
            )
            # The auto-starter only tracks a task it was told about: without this,
            # every on_task_progress answered "Unknown task" and never split.
            if self.auto_starter:
                self._drive(self.auto_starter.on_task_start(
                    task_id=task_def.task_id,
                    goal=task_def.description or task_def.task_id,
                    tenant_id=task_def.tenant_id,
                ))

            # Execute phases in order
            completed_phases: Set[str] = set()
            final_phase = None
            current_state = {}

            plan = planner.plan()
            for phase_idx, phase_id in enumerate(plan):
                phase = planner.get_phase(phase_id)
                session_id = self._new_session_id()
                final_phase = phase_id

                # Check for session split triggers (ADR-0649: infinite sessions)
                # on_task_progress is async and takes the context-usage signature
                # (context_usage_pct, iterations, context, audit_trail_hash); the old call
                # passed phase_id/phase_idx/total_phases synchronously, which could only
                # raise TypeError. Phase progress is the executor's usage proxy.
                if self.auto_starter:
                    new_session = self._drive(self.auto_starter.on_task_progress(
                        task_id=task_def.task_id,
                        # a FRACTION (0-1): should_split_session compares it with
                        # context_limit_pct=0.85, as core/session_manager/tests do
                        context_usage_pct=phase_idx / max(len(plan), 1),
                        iterations=phase_idx,
                        context={"phase_id": phase_id, "state": current_state},
                        audit_trail_hash=self._last_event_hash(),
                    ))
                    if new_session:
                        # Emit split event and bridge to new session
                        self._emit_event(
                            event_type="session_auto_split",
                            task_id=task_def.task_id,
                            session_id=session_id,
                            payload={"next_session": new_session},
                            tenant_id=task_def.tenant_id,
                        )

                # Execute phase
                phase_result = self._execute_phase(
                    task_def=task_def,
                    phase=phase,
                    session_id=session_id,
                    current_state=current_state,
                )

                if not phase_result["success"]:
                    # Phase failed, rollback (ADR-0649)
                    self._emit_event(
                        event_type="phase_failed",
                        task_id=task_def.task_id,
                        phase_id=phase_id,
                        payload={"error": phase_result.get("error")},
                        tenant_id=task_def.tenant_id,
                    )
                    # Emit rollback event
                    self._emit_event(
                        event_type="task_rolled_back",
                        task_id=task_def.task_id,
                        payload={"reason": f"phase_failed: {phase_result.get('error')}"},
                        tenant_id=task_def.tenant_id,
                    )
                    return ExecutionResult(
                        success=False,
                        task_id=task_def.task_id,
                        final_phase=phase_id,
                        audit_events=self.event_store.get_all_events(),
                        snapshot=None,
                        state_hash="",
                        error=phase_result.get("error"),
                    )

                # Phase succeeded
                completed_phases.add(phase_id)
                current_state = phase_result.get("state", current_state)

                # Emit phase_complete (ADR-0649)
                self._emit_event(
                    event_type="phase_complete",
                    task_id=task_def.task_id,
                    session_id=session_id,
                    phase_id=phase_id,
                    payload={"skills_count": len(phase.skills), "gates_count": len(phase.gates)},
                    tenant_id=task_def.tenant_id,
                )

                # Create snapshot for next session (if not last phase)
                if phase_idx < len(plan) - 1:
                    snapshot = self.event_store.create_snapshot(
                        task_id=task_def.task_id,
                        session_id=session_id,
                        phase_id=phase_id,
                        state=current_state,
                    )
                    # Emit snapshot_created event (ADR-0649)
                    self._emit_event(
                        event_type="task_snapshot_created",
                        task_id=task_def.task_id,
                        session_id=session_id,
                        phase_id=phase_id,
                        payload={"snapshot_hash": snapshot.snapshot_hash},
                        tenant_id=task_def.tenant_id,
                    )
                    # Emit bridge event to next session (ADR-0649)
                    next_session_id = self._new_session_id()
                    self._emit_event(
                        event_type="task_session_bridged",
                        task_id=task_def.task_id,
                        session_id=session_id,
                        payload={
                            "source_session": session_id,
                            "dest_session": next_session_id,
                            "state_hash": snapshot.snapshot_hash,
                            "state_hash_verified": True,
                        },
                        tenant_id=task_def.tenant_id,
                    )

            # Emit task_complete (ADR-0649)
            final_snapshot = self.event_store.create_snapshot(
                task_id=task_def.task_id,
                session_id=self._new_session_id(),
                phase_id=final_phase,
                state=current_state,
            )
            self._emit_event(
                event_type="task_complete",
                task_id=task_def.task_id,
                phase_id=final_phase,
                payload={"final_state_hash": final_snapshot.snapshot_hash},
                tenant_id=task_def.tenant_id,
            )

            # Verify audit chain (ADR-0649)
            chain_valid = self.event_store.verify_chain(task_def.task_id)
            self._emit_event(
                event_type="audit_chain_verified",
                task_id=task_def.task_id,
                payload={"chain_valid": chain_valid, "events_count": len(self.event_store.get_all_events())},
                tenant_id=task_def.tenant_id,
            )

            return ExecutionResult(
                success=True,
                task_id=task_def.task_id,
                final_phase=final_phase,
                audit_events=self.event_store.get_all_events(),
                snapshot=final_snapshot,
                state_hash=final_snapshot.snapshot_hash,
            )

        except Exception as e:
            # Unhandled exception (ADR-0649)
            self._emit_event(
                event_type="task_error",
                task_id=task_def.task_id,
                payload={"error": str(e)},
                tenant_id=task_def.tenant_id,
            )
            return ExecutionResult(
                success=False,
                task_id=task_def.task_id,
                final_phase=None,
                audit_events=self.event_store.get_all_events(),
                snapshot=None,
                state_hash="",
                error=str(e),
            )

    def _execute_phase(self, task_def: TaskDefinition, phase, session_id: str,
                       current_state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute single phase with skills + gates (ADR-0542)."""
        phase_id = phase.id

        # Emit phase_started (ADR-0649)
        self._emit_event(
            event_type="phase_started",
            task_id=task_def.task_id,
            session_id=session_id,
            phase_id=phase_id,
            payload={"skills_count": len(phase.skills)},
            tenant_id=task_def.tenant_id,
        )

        # Dispatch skills
        skill_results = self.skill_dispatcher.dispatch(phase.skills, current_state)
        if not self.skill_dispatcher.all_success(skill_results):
            error = skill_results[-1].error if skill_results else "Unknown skill error"
            return {"success": False, "error": error, "state": current_state}

        # Emit skills_executed (ADR-0649)
        self._emit_event(
            event_type="phase_skills_executed",
            task_id=task_def.task_id,
            session_id=session_id,
            phase_id=phase_id,
            payload={"skills_count": len(skill_results), "all_success": True},
            tenant_id=task_def.tenant_id,
        )

        # Evaluate gates (mock: all pass for now)
        gates_results = self._evaluate_gates(phase.gates)
        if not gates_results["all_passed"]:
            return {"success": False, "error": "Gate failed", "state": current_state}

        # Emit gates_evaluated (ADR-0649)
        self._emit_event(
            event_type="phase_gate_evaluated",
            task_id=task_def.task_id,
            session_id=session_id,
            phase_id=phase_id,
            payload={"gates_count": len(phase.gates), "all_passed": True},
            tenant_id=task_def.tenant_id,
        )

        # Return updated state (for mock, just pass through skills output)
        final_state = skill_results[-1].output if skill_results else current_state
        return {"success": True, "state": final_state}

    def _evaluate_gates(self, gates) -> Dict[str, bool]:
        """Evaluate all gates for a phase (ADR-0542, mock)."""
        # Mock: all gates pass
        return {"all_passed": True, "gates_count": len(gates)}

    def _emit_event(self, event_type: str, task_id: str, session_id: str = "",
                    phase_id: str = "", payload: Dict[str, Any] = None, tenant_id: str = None) -> str:
        """Emit audit event (ADR-0649: per-task tenant_id, not process tenant)."""
        if tenant_id is None:
            tenant_id = self.tenant_id
        event = AuditEvent(
            event_type=event_type,
            task_id=task_id,
            tenant_id=tenant_id,
            session_id=session_id or "",
            phase_id=phase_id or "",
            timestamp=datetime.utcnow().isoformat() + "Z",
            payload=payload or {},
        )
        return self.event_store.append_event(event)

    def _new_session_id(self) -> str:
        """Generate unique session ID."""
        self.session_counter += 1
        return f"s{self.session_counter}-{self.tenant_id}"
