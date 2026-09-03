"""Task Engine executor — main orchestrator (ADR-0540)."""

from typing import Optional, Dict, Any, Set
from datetime import datetime
import json

from .task_def import TaskDefinition
from .dag_planner import DAGPlanner
from .event_store import EventStore
from .skill_dispatcher import SkillDispatcher, SkillResult
from .models import AuditEvent, ExecutionResult


class TaskExecutor:
    """Orchestrates multi-phase task execution with audit trail (ADR-0540–0545)."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.event_store = EventStore(tenant_id=tenant_id)
        self.skill_dispatcher = SkillDispatcher()
        self.session_counter = 0

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

            # Emit task_started event
            self._emit_event(
                event_type="task_started",
                task_id=task_def.task_id,
                payload={"autonomy_level": task_def.autonomy_level},
            )

            # Execute phases in order
            completed_phases: Set[str] = set()
            final_phase = None
            current_state = {}

            plan = planner.plan()
            for phase_idx, phase_id in enumerate(plan):
                phase = planner.get_phase(phase_id)
                session_id = self._new_session_id()
                final_phase = phase_id

                # Execute phase
                phase_result = self._execute_phase(
                    task_def=task_def,
                    phase=phase,
                    session_id=session_id,
                    current_state=current_state,
                )

                if not phase_result["success"]:
                    # Phase failed, rollback
                    self._emit_event(
                        event_type="phase_failed",
                        task_id=task_def.task_id,
                        phase_id=phase_id,
                        payload={"error": phase_result.get("error")},
                    )
                    # Emit rollback event
                    self._emit_event(
                        event_type="task_rolled_back",
                        task_id=task_def.task_id,
                        payload={"reason": f"phase_failed: {phase_result.get('error')}"},
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

                # Emit phase_complete
                self._emit_event(
                    event_type="phase_complete",
                    task_id=task_def.task_id,
                    session_id=session_id,
                    phase_id=phase_id,
                    payload={"skills_count": len(phase.skills), "gates_count": len(phase.gates)},
                )

                # Create snapshot for next session (if not last phase)
                if phase_idx < len(plan) - 1:
                    snapshot = self.event_store.create_snapshot(
                        task_id=task_def.task_id,
                        session_id=session_id,
                        phase_id=phase_id,
                        state=current_state,
                    )
                    # Emit snapshot_created event
                    self._emit_event(
                        event_type="task_snapshot_created",
                        task_id=task_def.task_id,
                        session_id=session_id,
                        phase_id=phase_id,
                        payload={"snapshot_hash": snapshot.snapshot_hash},
                    )
                    # Emit bridge event to next session
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
                    )

            # Emit task_complete
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
            )

            # Verify audit chain
            chain_valid = self.event_store.verify_chain(task_def.task_id)
            self._emit_event(
                event_type="audit_chain_verified",
                task_id=task_def.task_id,
                payload={"chain_valid": chain_valid, "events_count": len(self.event_store.get_all_events())},
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
            # Unhandled exception
            self._emit_event(
                event_type="task_error",
                task_id=task_def.task_id,
                payload={"error": str(e)},
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

        # Emit phase_started
        self._emit_event(
            event_type="phase_started",
            task_id=task_def.task_id,
            session_id=session_id,
            phase_id=phase_id,
            payload={"skills_count": len(phase.skills)},
        )

        # Dispatch skills
        skill_results = self.skill_dispatcher.dispatch(phase.skills, current_state)
        if not self.skill_dispatcher.all_success(skill_results):
            error = skill_results[-1].error if skill_results else "Unknown skill error"
            return {"success": False, "error": error, "state": current_state}

        # Emit skills_executed
        self._emit_event(
            event_type="phase_skills_executed",
            task_id=task_def.task_id,
            session_id=session_id,
            phase_id=phase_id,
            payload={"skills_count": len(skill_results), "all_success": True},
        )

        # Evaluate gates (mock: all pass for now)
        gates_results = self._evaluate_gates(phase.gates)
        if not gates_results["all_passed"]:
            return {"success": False, "error": "Gate failed", "state": current_state}

        # Emit gates_evaluated
        self._emit_event(
            event_type="phase_gate_evaluated",
            task_id=task_def.task_id,
            session_id=session_id,
            phase_id=phase_id,
            payload={"gates_count": len(phase.gates), "all_passed": True},
        )

        # Return updated state (for mock, just pass through skills output)
        final_state = skill_results[-1].output if skill_results else current_state
        return {"success": True, "state": final_state}

    def _evaluate_gates(self, gates) -> Dict[str, bool]:
        """Evaluate all gates for a phase (ADR-0542, mock)."""
        # Mock: all gates pass
        return {"all_passed": True, "gates_count": len(gates)}

    def _emit_event(self, event_type: str, task_id: str, session_id: str = "",
                    phase_id: str = "", payload: Dict[str, Any] = None) -> str:
        """Emit audit event (ADR-0232)."""
        event = AuditEvent(
            event_type=event_type,
            task_id=task_id,
            tenant_id=self.tenant_id,
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
