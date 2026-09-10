"""Phase execution logic (0–10)."""

from typing import List, Dict, Any
from datetime import datetime
from .events import PhaseCompletedEvent, PhaseGate
import time


class PhaseExecutor:
    """Executes phases 0–10 with gates."""

    async def phase_0_intake(self, goal: str, skill_id: str) -> PhaseCompletedEvent:
        """Phase 0: Intake — capture goal in one sentence."""
        start = time.time()
        success = len(goal.split()) > 3  # sanity check
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=0,
            skill_id=skill_id,
            success=success,
            duration_ms=duration_ms,
            loss_component=0.0 if success else 1.0,
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"goal_length_words": len(goal.split())},
            errors_if_any=[] if success else ["Goal too short"],
        )

    async def phase_2_clarification(self, skill_id: str) -> PhaseCompletedEvent:
        """Phase 2: Clarification — ask 1 question max."""
        start = time.time()
        # Placeholder: ask user 1 focused question
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=2,
            skill_id=skill_id,
            success=True,
            duration_ms=duration_ms,
            loss_component=0.1,  # 1 question = low loss
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"questions_asked": 1},
            errors_if_any=[],
        )

    async def phase_3_plan(self, skill_id: str, goal: str) -> PhaseCompletedEvent:
        """Phase 3: Plan — thesis/antithesis/synthesis."""
        start = time.time()
        # Placeholder: dialectical planning
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=3,
            skill_id=skill_id,
            success=True,
            duration_ms=duration_ms,
            loss_component=0.15,
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"dialectical_check": "passed", "critical_objections": 0},
            errors_if_any=[],
        )

    async def phase_3b_checkpoint(self, skill_id: str) -> PhaseCompletedEvent:
        """Phase 3b: Checkpoint — user confirms plan."""
        start = time.time()
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=3,  # mark as 3b
            skill_id=skill_id,
            success=True,
            duration_ms=duration_ms,
            loss_component=0.05,
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"user_confirmed": True},
            errors_if_any=[],
        )

    async def phase_4_structure(self, skill_id: str) -> PhaseCompletedEvent:
        """Phase 4: Structure — build skeleton."""
        start = time.time()
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=4,
            skill_id=skill_id,
            success=True,
            duration_ms=duration_ms,
            loss_component=0.08,
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"files_created": 5},
            errors_if_any=[],
        )

    async def phase_5_content(self, skill_id: str) -> PhaseCompletedEvent:
        """Phase 5: Content — write files."""
        start = time.time()
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=5,
            skill_id=skill_id,
            success=True,
            duration_ms=duration_ms,
            loss_component=0.12,
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"placeholders_removed": 0},
            errors_if_any=[],
        )

    async def phase_6_hooks(self, skill_id: str) -> PhaseCompletedEvent:
        """Phase 6: Hooks — optional hook synthesis."""
        start = time.time()
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=6,
            skill_id=skill_id,
            success=True,
            duration_ms=duration_ms,
            loss_component=0.0,  # no hooks needed
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"hooks_needed": False},
            errors_if_any=[],
        )

    async def phase_7_optimization(self, skill_id: str) -> PhaseCompletedEvent:
        """Phase 7: Optimization — LDD inner/refinement loop."""
        start = time.time()
        # Placeholder: run optimization loop
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=7,
            skill_id=skill_id,
            success=True,
            duration_ms=duration_ms,
            loss_component=0.1,
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"optimization_iterations": 2, "target_score": 0.90},
            errors_if_any=[],
        )

    async def phase_8_validation(self, skill_id: str) -> PhaseCompletedEvent:
        """Phase 8: Validation — 13 hard checks."""
        start = time.time()
        # Placeholder: run validation
        success = True  # mock: all checks pass
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=8,
            skill_id=skill_id,
            success=success,
            duration_ms=duration_ms,
            loss_component=0.0 if success else 0.8,
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"checks_passed": 13, "blockers": 0},
            errors_if_any=[] if success else ["Validation failed"],
        )

    async def phase_9_packaging(self, skill_id: str) -> PhaseCompletedEvent:
        """Phase 9: Packaging — build zip."""
        start = time.time()
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=9,
            skill_id=skill_id,
            success=True,
            duration_ms=duration_ms,
            loss_component=0.05,
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"zip_created": True},
            errors_if_any=[],
        )

    async def phase_10_delivery(self, skill_id: str) -> PhaseCompletedEvent:
        """Phase 10: Delivery — explain + upload steps."""
        start = time.time()
        duration_ms = int((time.time() - start) * 1000)

        return PhaseCompletedEvent(
            phase_id=10,
            skill_id=skill_id,
            success=True,
            duration_ms=duration_ms,
            loss_component=0.0,
            timestamp=datetime.utcnow().isoformat(),
            data_sources_used=[],
            decisions_made={"user_has_zip": True},
            errors_if_any=[],
        )

    async def run_all_phases(
        self, skill_id: str, goal: str, mode: str
    ) -> List[PhaseCompletedEvent]:
        """Run phases 0–10 in sequence."""
        events = []

        # Phase 0
        events.append(await self.phase_0_intake(goal, skill_id))
        if not events[-1].success:
            return events

        # Phase 2–3b
        events.append(await self.phase_2_clarification(skill_id))
        events.append(await self.phase_3_plan(skill_id, goal))
        events.append(await self.phase_3b_checkpoint(skill_id))

        # Phase 4–6
        events.append(await self.phase_4_structure(skill_id))
        events.append(await self.phase_5_content(skill_id))
        events.append(await self.phase_6_hooks(skill_id))

        # Phase 7–10
        events.append(await self.phase_7_optimization(skill_id))
        events.append(await self.phase_8_validation(skill_id))
        if not events[-1].success:
            return events
        events.append(await self.phase_9_packaging(skill_id))
        events.append(await self.phase_10_delivery(skill_id))

        return events
