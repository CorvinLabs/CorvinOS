"""ADR-0214: send() Integration (Phase 2).

Core L22 hookpoint where engine selection happens:
1. Parse slash commands
2. Pre-gate: L34 data-safety check
3. InitialAnalysis (ADR-0210 Phase 1)
4. Cheap-Pre-Gate: trivial tasks skip full analysis
5. RobustEngineDetector: select engine
6. Execute with selected engine

This module provides the integration logic (not the full send() replacement,
which belongs in the L22 layer).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

try:
    from operator.orchestration.initial_analysis import InitialAnalysisRequest
    from operator.orchestration.tde.engine_registry import get_registry
    from operator.orchestration.tde.l34_delegation_gate import L34DelegationGate
    from operator.orchestration.tde.loss_profile_tracker import LossProfileTracker
    from operator.orchestration.tde.robust_engine_detector import RobustEngineDetector
    from operator.orchestration.tde.slash_command_parser import SlashCommandParser
except ImportError:
    # Fallback for testing
    from initial_analysis import InitialAnalysisRequest  # type: ignore
    from engine_registry import get_registry  # type: ignore
    from l34_delegation_gate import L34DelegationGate  # type: ignore
    from loss_profile_tracker import LossProfileTracker  # type: ignore
    from robust_engine_detector import RobustEngineDetector  # type: ignore
    from slash_command_parser import SlashCommandParser  # type: ignore

_logger = logging.getLogger(__name__)


class SendIntegration:
    """Integration point for send() flow."""

    def __init__(self):
        """Initialize integration components."""
        self.parser = SlashCommandParser()
        self.loss_tracker = LossProfileTracker()
        self.detector = RobustEngineDetector(loss_tracker=self.loss_tracker)
        self.l34_gate = L34DelegationGate()
        self.registry = get_registry()

    async def select_engine_and_execute(
        self,
        task: str,
        context: dict[str, Any],
        initial_analysis: InitialAnalysisRequest,
    ) -> tuple[str, dict[str, Any]]:
        """
        Core send() logic: select engine and execute.

        Steps:
        1. Parse slash commands
        2. Pre-gate: L34 data-safety
        3. Cheap-pre-gate: trivial tasks
        4. Engine detection
        5. Execute

        Args:
            task: Raw task (may contain /use-engine command)
            context: Task context
            initial_analysis: Classification from Phase 1

        Returns:
            (engine_name, result)
        """

        # Step 1: Parse slash commands
        parsed = self.parser.parse(task)
        task_text = parsed.task_text
        engine_override = parsed.engine_override
        debug_mode = parsed.debug_mode

        _logger.info(f"Parsed command: engine_override={engine_override}, debug={debug_mode}")

        # Step 2: Pre-gate (L34 data-safety, engine-agnostic)
        prescan = self.l34_gate.can_delegate_step(
            None,  # No specific step, just overall check
            context,
            max_classification="INTERNAL",
        )

        if not prescan.can_delegate:
            # Force claude_code (only safe option)
            engine_override = "claude_code"
            _logger.warning(f"L34 prescan blocked delegation: {prescan.reason}")

        # Step 3: Cheap-pre-gate (trivial tasks)
        if self._is_trivial_task(initial_analysis):
            engine_name = "claude_code"
            _logger.info("Trivial task: using claude_code (cheap)")
        elif engine_override:
            # User forced an engine
            engine_name = engine_override
            _logger.info(f"User override: {engine_name}")
        else:
            # Step 4: Engine detection
            engine_name, confidence, signals = self.detector.detect_engine(
                task_text,
                context,
                initial_analysis,
            )

            if debug_mode:
                # Log debug info for user visibility
                _logger.info(
                    f"Engine detection (debug): {engine_name} ({confidence:.1%}) | signals={signals}"
                )

        # Step 5: Execute
        _logger.info(f"Executing with {engine_name}")
        result = await self.registry.execute(engine_name, initial_analysis, context)

        # Record outcome (for loss-tracking)
        # Note: real loss measurement happens in Phase 2
        self.loss_tracker.record_via_proxy(
            task_type=initial_analysis.classification.task_type,
            engine=engine_name,
            schema_valid=True,  # Placeholder
            downstream_ok=True,  # Placeholder
        )

        return engine_name, result

    def _is_trivial_task(self, analysis: InitialAnalysisRequest) -> bool:
        """Heuristic: is this a trivial task?"""
        # Trivial if:
        # - Complexity is "simple"
        # - Estimated tokens < 500
        # - Only 1 step

        return (
            analysis.classification.complexity == "simple"
            and analysis.global_plan.estimated_tokens < 500
            and len(analysis.global_plan.steps) == 1
        )
