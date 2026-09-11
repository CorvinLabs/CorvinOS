"""Phase model orchestrator — coordinates 12-phase skill generation."""

from dataclasses import dataclass
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
    """Orchestrates all 12 phases of skill generation."""

    def __init__(self):
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

    def execute(
        self,
        query: str,
        mode: str = "skill",
        user_clarifications: Optional[Dict[str, str]] = None,
    ) -> Creator20State:
        """Execute the full 12-phase pipeline."""
        state = Creator20State(skill_id="unknown")

        try:
            # Phase 0: Intake
            state.intake_result = self.phases[0].execute(
                IntakeRequest(query=query, mode=mode),
                self.emitter,
            )
            state.skill_id = state.intake_result.skill_id

            # Phase 1: Ingestion
            state.ingestion_result = self.phases[1].execute(
                IngestionRequest(
                    skill_id=state.skill_id,
                    description=state.intake_result.description,
                    intent=state.intake_result.intent,
                    mode=mode,
                ),
                self.emitter,
            )

            # Phase 2: Clarification
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

            # Phase 3: Planning
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

            # Phase 3b: Checkpoint
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

            if not checkpoint.is_sensible:
                # If checkpoint fails, we could ask user to retry, but for now continue
                pass

            # Phase 4: Structure
            state.structure_result = self.phases[4].execute(
                StructureRequest(
                    skill_id=state.skill_id,
                    intent=state.intake_result.intent,
                    functions=state.planning_result.functions,
                    dependencies=state.planning_result.dependencies,
                ),
                self.emitter,
            )

            # Phase 5: Content
            state.content_result = self.phases[5].execute(
                ContentRequest(
                    skill_id=state.skill_id,
                    intent=state.intake_result.intent,
                    functions=state.planning_result.functions,
                    module_structure=state.structure_result.module_structure,
                ),
                self.emitter,
            )

            # Phase 6: Hooks
            state.hooks_result = self.phases[6].execute(
                HooksRequest(
                    skill_id=state.skill_id,
                    code_generated=state.content_result.code_generated,
                    functions=state.planning_result.functions,
                ),
                self.emitter,
            )

            # Phase 7: Optimization
            state.optimization_result = self.phases[7].execute(
                OptimizationRequest(
                    skill_id=state.skill_id,
                    code_generated=state.content_result.code_generated,
                    lines_of_code=state.content_result.lines_of_code,
                ),
                self.emitter,
            )

            # Phase 8: Validation
            state.validation_result = self.phases[8].execute(
                ValidationRequest(
                    skill_id=state.skill_id,
                    functions=state.planning_result.functions,
                    code_generated=state.content_result.code_generated,
                ),
                self.emitter,
            )

            # Phase 9: Packaging
            state.packaging_result = self.phases[9].execute(
                PackagingRequest(
                    skill_id=state.skill_id,
                    description=state.intake_result.description,
                    intent=state.intake_result.intent,
                    file_layout=state.structure_result.file_layout,
                ),
                self.emitter,
            )

            # Phase 10: Delivery
            state.delivery_result = self.phases[10].execute(
                DeliveryRequest(
                    skill_id=state.skill_id,
                    manifest=state.packaging_result.manifest,
                    version=state.packaging_result.version,
                    package_name=state.packaging_result.package_name,
                ),
                self.emitter,
            )

            # Compute final loss
            state.final_loss = self.emitter.overall_skill_loss()

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
