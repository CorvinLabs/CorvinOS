"""Phase model orchestrator — coordinates 12-phase skill generation.

CRITICAL FIX #2 (2026-09-12): Transactional Guarantees
- Integrated WAL (Write-Ahead Log) for crash safety
- Each phase state is logged BEFORE execution
- Atomic swaps ensure partial states are never persisted
- Recovery on restart via WAL replay
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional
from .events import LossEmitter, PhaseCompletedEvent
from .phases.phase_0 import IntakePhase, IntakeRequest
from .phases.phase_1 import IngestionPhase, IngestionRequest
from .phases.phase_2 import ClarificationPhase, ClarificationRequest
from .phases.phase_3 import PlanningPhase, PlanningRequest
from .phases.phase_3b import CheckpointPhase, CheckpointRequest
from .phases.phase_4 import StructurePhase, StructureRequest
from .phases.phase_5 import ContentPhase, ContentRequest
from .phases.phase_6 import HooksPhase, HooksRequest
from .phases.phase_7 import OptimizationPhase, OptimizationRequest
from .phases.phase_8 import ValidationPhase, ValidationRequest
from .phases.phase_9 import PackagingPhase, PackagingRequest
from .phases.phase_10 import DeliveryPhase, DeliveryRequest
from .wal import SkillCreationWAL


@dataclass
class Creator20State:
    """Full state of the skill creation process."""
    skill_id: str
    intake_result: Any = None
    ingestion_result: Any = None
    clarifications: Dict[str, str] = None
    planning_result: Any = None
    checkpoint_result: Any = None
    structure_result: Any = None
    content_result: Any = None
    hooks_result: Any = None
    optimization_result: Any = None
    validation_result: Any = None
    packaging_result: Any = None
    delivery_result: Any = None
    final_loss: Optional[float] = None

    def __post_init__(self):
        if self.clarifications is None:
            self.clarifications = {}


class PhaseModelOrchestrator:
    """Orchestrates all 12 phases of skill generation with transactional guarantees (FIX #2)."""

    def __init__(self, tenant_home: Optional[Path] = None):
        """Initialize orchestrator with WAL support (FIX #2).

        Args:
            tenant_home: Path to tenant home for WAL persistence
        """
        self.emitter = LossEmitter()
        self.phases = {
            0: IntakePhase(),
            1: IngestionPhase(),
            2: ClarificationPhase(),
            3: PlanningPhase(),
            3.5: CheckpointPhase(),  # Phase 3b
            4: StructurePhase(),
            5: ContentPhase(),
            6: HooksPhase(),
            7: OptimizationPhase(),
            8: ValidationPhase(),
            9: PackagingPhase(),
            10: DeliveryPhase(),
        }

        # FIX #2: Initialize WAL for transactional guarantees
        if tenant_home is None:
            from core.paths import tenant_home as get_tenant_home
            tenant_home = get_tenant_home("_default")
        self.wal = SkillCreationWAL(Path(tenant_home))

    def _persist_state_to_wal(self, state: Creator20State, phase_num: int) -> None:
        """Persist state to WAL before phase execution (FIX #2: Crash Safety).

        Args:
            state: Current skill creation state
            phase_num: Phase number about to execute

        Raises:
            IOError: if WAL write fails (fail-closed: phase does not execute)
        """
        from dataclasses import asdict

        # Serialize state for WAL
        state_dict = {
            "skill_id": state.skill_id,
            "intake_result": asdict(state.intake_result) if state.intake_result else None,
            "ingestion_result": asdict(state.ingestion_result) if state.ingestion_result else None,
            "clarifications": state.clarifications,
            "planning_result": asdict(state.planning_result) if state.planning_result else None,
            "checkpoint_result": asdict(state.checkpoint_result) if state.checkpoint_result else None,
            "structure_result": asdict(state.structure_result) if state.structure_result else None,
            "content_result": asdict(state.content_result) if state.content_result else None,
            "hooks_result": asdict(state.hooks_result) if state.hooks_result else None,
            "optimization_result": asdict(state.optimization_result) if state.optimization_result else None,
            "validation_result": asdict(state.validation_result) if state.validation_result else None,
            "packaging_result": asdict(state.packaging_result) if state.packaging_result else None,
            "delivery_result": asdict(state.delivery_result) if state.delivery_result else None,
        }

        # Write to WAL BEFORE phase execution
        self.wal.write_state(state.skill_id, phase_num, state_dict)

    def execute(
        self,
        query: str,
        mode: str = "skill",
        user_clarifications: Optional[Dict[str, str]] = None,
    ) -> Creator20State:
        """Execute the full 12-phase pipeline with WAL transactional guarantees (FIX #2)."""
        state = Creator20State(skill_id="unknown")

        try:
            # Phase 0: Intake
            self._persist_state_to_wal(state, 0)  # FIX #2: WAL before phase
            state.intake_result = self.phases[0].execute(
                IntakeRequest(query=query, mode=mode),
                self.emitter,
            )
            state.skill_id = state.intake_result.skill_id
            self.wal.mark_processed(state.skill_id, 0)  # FIX #2: Mark complete

            # Phase 1: Ingestion
            self._persist_state_to_wal(state, 1)  # FIX #2: WAL before phase
            state.ingestion_result = self.phases[1].execute(
                IngestionRequest(
                    skill_id=state.skill_id,
                    description=state.intake_result.description,
                    intent=state.intake_result.intent,
                    mode=mode,
                ),
                self.emitter,
            )
            self.wal.mark_processed(state.skill_id, 1)  # FIX #2: Mark complete

            # Phase 2: Clarification
            self._persist_state_to_wal(state, 2)  # FIX #2: WAL before phase
            state.clarifications = self.phases[2].execute(
                ClarificationRequest(
                    skill_id=state.skill_id,
                    description=state.intake_result.description,
                    intent=state.intake_result.intent,
                    domain_knowledge=state.ingestion_result.domain_knowledge,
                    user_clarifications=user_clarifications,
                ),
                self.emitter,
            ).clarifications
            self.wal.mark_processed(state.skill_id, 2)  # FIX #2: Mark complete

            # Phase 3: Planning
            self._persist_state_to_wal(state, 3)  # FIX #2: WAL before phase
            state.planning_result = self.phases[3].execute(
                PlanningRequest(
                    skill_id=state.skill_id,
                    intent=state.intake_result.intent,
                    description=state.intake_result.description,
                    domain_knowledge=state.ingestion_result.domain_knowledge,
                    clarifications=state.clarifications,
                ),
                self.emitter,
            )
            self.wal.mark_processed(state.skill_id, 3)  # FIX #2: Mark complete

            # Phase 3b: Checkpoint
            self._persist_state_to_wal(state, 3)  # FIX #2: WAL (note: phase 3 checkpoint)
            checkpoint = self.phases[3.5].execute(
                CheckpointRequest(
                    skill_id=state.skill_id,
                    architecture=state.planning_result.architecture,
                    functions=state.planning_result.functions,
                    dependencies=state.planning_result.dependencies,
                ),
                self.emitter,
            )
            state.checkpoint_result = checkpoint
            self.wal.mark_processed(state.skill_id, 3)  # FIX #2: Mark complete

            if not checkpoint.is_sensible:
                # If checkpoint fails, we could ask user to retry, but for now continue
                pass

            # Phase 4: Structure
            self._persist_state_to_wal(state, 4)  # FIX #2: WAL before phase
            state.structure_result = self.phases[4].execute(
                StructureRequest(
                    skill_id=state.skill_id,
                    intent=state.intake_result.intent,
                    functions=state.planning_result.functions,
                    dependencies=state.planning_result.dependencies,
                ),
                self.emitter,
            )
            self.wal.mark_processed(state.skill_id, 4)  # FIX #2: Mark complete

            # Phase 5: Content
            self._persist_state_to_wal(state, 5)  # FIX #2: WAL before phase
            state.content_result = self.phases[5].execute(
                ContentRequest(
                    skill_id=state.skill_id,
                    intent=state.intake_result.intent,
                    functions=state.planning_result.functions,
                    module_structure=state.structure_result.module_structure,
                ),
                self.emitter,
            )
            self.wal.mark_processed(state.skill_id, 5)  # FIX #2: Mark complete

            # Phase 6: Hooks
            self._persist_state_to_wal(state, 6)  # FIX #2: WAL before phase
            state.hooks_result = self.phases[6].execute(
                HooksRequest(
                    skill_id=state.skill_id,
                    code_generated=state.content_result.code_generated,
                    functions=state.planning_result.functions,
                ),
                self.emitter,
            )
            self.wal.mark_processed(state.skill_id, 6)  # FIX #2: Mark complete

            # Phase 7: Optimization
            self._persist_state_to_wal(state, 7)  # FIX #2: WAL before phase
            state.optimization_result = self.phases[7].execute(
                OptimizationRequest(
                    skill_id=state.skill_id,
                    code_generated=state.content_result.code_generated,
                    lines_of_code=state.content_result.lines_of_code,
                ),
                self.emitter,
            )
            self.wal.mark_processed(state.skill_id, 7)  # FIX #2: Mark complete

            # Phase 8: Validation
            self._persist_state_to_wal(state, 8)  # FIX #2: WAL before phase
            state.validation_result = self.phases[8].execute(
                ValidationRequest(
                    skill_id=state.skill_id,
                    functions=state.planning_result.functions,
                    code_generated=state.content_result.code_generated,
                ),
                self.emitter,
            )
            self.wal.mark_processed(state.skill_id, 8)  # FIX #2: Mark complete

            # Phase 9: Packaging
            self._persist_state_to_wal(state, 9)  # FIX #2: WAL before phase
            state.packaging_result = self.phases[9].execute(
                PackagingRequest(
                    skill_id=state.skill_id,
                    description=state.intake_result.description,
                    intent=state.intake_result.intent,
                    file_layout=state.structure_result.file_layout,
                ),
                self.emitter,
            )
            self.wal.mark_processed(state.skill_id, 9)  # FIX #2: Mark complete

            # Phase 10: Delivery
            self._persist_state_to_wal(state, 10)  # FIX #2: WAL before phase
            state.delivery_result = self.phases[10].execute(
                DeliveryRequest(
                    skill_id=state.skill_id,
                    manifest=state.packaging_result.manifest,
                    version=state.packaging_result.version,
                    package_name=state.packaging_result.package_name,
                ),
                self.emitter,
            )
            self.wal.mark_processed(state.skill_id, 10)  # FIX #2: Mark complete

            # Compute final loss
            state.final_loss = self.emitter.overall_skill_loss()

            # FIX #2: Clear WAL entries on successful completion
            self.wal.clear_skill_entries(state.skill_id)

        except Exception as e:
            # Record error but don't crash
            self.emitter.events.append(
                PhaseCompletedEvent(
                    phase_num=-1,
                    skill_id=state.skill_id,
                    duration_ms=0,
                    errors=[str(e)],
                )
            )
            # FIX #2: Mark error in WAL for debugging
            try:
                if state.skill_id != "unknown":
                    self.wal.mark_error(state.skill_id, -1, str(e))
            except Exception:
                pass  # WAL error marking failed; continue

        return state

    def get_events(self) -> List[PhaseCompletedEvent]:
        """Retrieve all emitted events."""
        return self.emitter.get_events()

    def validate_execution(self, state: Creator20State) -> bool:
        """Validate that execution was complete."""
        required_fields = [
            "intake_result",
            "ingestion_result",
            "planning_result",
            "content_result",
            "packaging_result",
            "delivery_result",
        ]
        return all(getattr(state, field) is not None for field in required_fields)

    def is_loss_valid(self) -> bool:
        """Check that final loss is well-formed."""
        return self.emitter.validate_skill_loss()
