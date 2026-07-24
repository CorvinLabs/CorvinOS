"""ADR-0214: send() Integration (Phase 2).

Core L22 hookpoint where engine selection happens:
1. Parse slash commands (/use-engine, /engine-auto, /debug-engine)
2. Pre-gate: L34 data-safety check (engine-agnostic — overrides can't bypass)
3. Cheap-pre-gate: trivial tasks skip detection (but never a user override)
4. RobustEngineDetector: select engine (with uncertainty fallback)
5. Execute with selected engine
6. Record REAL outcome (no fabricated proxy signals)

Decision precedence (highest wins):
  L34 block  >  user override  >  trivial-gate  >  detector

This module provides the integration logic (not the full send() replacement,
which belongs in the L22 layer).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

try:
    from initial_analysis import InitialAnalysisRequest
except ImportError:  # pragma: no cover
    from ..initial_analysis import InitialAnalysisRequest  # type: ignore

from . import tde_audit
from .engine_registry import EngineRegistry, get_registry
from .l34_delegation_gate import L34DelegationGate
from .loss_profile_tracker import get_session_tracker
from .robust_engine_detector import RobustEngineDetector
from .slash_command_parser import SlashCommandParser

_logger = logging.getLogger(__name__)


class SendIntegration:
    """Integration point for send() flow."""

    def __init__(
        self,
        registry: Optional[EngineRegistry] = None,
        l34_classifier: Optional[Any] = None,
        session_key: str = "default",
    ):
        """Initialize integration components.

        Args:
            registry: Engine registry (injectable for tests). Defaults to the
                global singleton.
            l34_classifier: Real L34 Flow Guard classifier when available.
            session_key: ADR-0215 F4 — identifies which (tenant, session)
                this instance's loss evidence belongs to. Console callers
                pass ``f"{tenant_id}:{sid}"``; standalone/CLI/test callers
                may omit it (falls back to the old, single, unkeyed
                behavior under the literal key ``"default"``).
        """
        self.parser = SlashCommandParser()
        self.session_key = session_key
        self.loss_tracker = get_session_tracker(session_key=session_key)
        self.detector = RobustEngineDetector(loss_tracker=self.loss_tracker)
        self.l34_gate = L34DelegationGate(l34_classifier=l34_classifier)
        self.registry = registry or get_registry()

    async def select_engine_and_execute(
        self,
        task: str,
        context: dict[str, Any],
        initial_analysis: InitialAnalysisRequest,
        *,
        run_id: str = "",
    ) -> tuple[str, dict[str, Any]]:
        """
        Core send() logic: select engine and execute.

        Args:
            task: Raw task (may contain /use-engine command)
            context: Task context
            initial_analysis: Classification from Phase 1
            run_id: ADR-0214 audit-graph correlation ID (chat_runtime's
                per-turn ``tde-<epoch>-<hex>``). Threaded into every tde.*
                audit event as ``tde_run_id`` and forwarded to the engine so
                per-step events can be tied back to this turn. Optional —
                "" for callers that don't need graph correlation (tests).

        Returns:
            (engine_name, result). ``result`` always carries an
            ``engine_selection`` block: {engine, confidence, override,
            l34_forced, trivial, signals?}.
        """

        # Step 1: Parse slash commands
        try:
            parsed = self.parser.parse(task)
        except ValueError as exc:
            # Invalid /use-engine target: report instead of crashing the turn.
            return "claude_code", {
                "engine": "claude_code",
                "success": False,
                "error": str(exc),
                "engine_selection": {"engine": "claude_code", "confidence": 0.0,
                                     "override": None, "l34_forced": False,
                                     "trivial": False, "parse_error": True},
            }

        task_text = parsed.task_text
        engine_override = parsed.engine_override
        debug_mode = parsed.debug_mode

        _logger.info(f"Parsed command: engine_override={engine_override}, debug={debug_mode}")

        # Step 2: Pre-gate (L34 data-safety, engine-agnostic — even explicit
        # overrides cannot route unsafe data to a delegating engine).
        prescan = self.l34_gate.prescan(context, max_classification="INTERNAL")
        l34_forced = False
        if not prescan.can_delegate:
            l34_forced = True
            if engine_override and engine_override != "claude_code":
                _logger.warning(
                    "L34 prescan overrides user engine choice %s: %s",
                    engine_override, prescan.reason,
                )
            engine_override = "claude_code"
            tde_audit.emit("l34_blocked", scope="prescan", reason_code="prescan_block",
                           tde_run_id=run_id)
            _logger.warning(f"L34 prescan blocked delegation: {prescan.reason}")

        # Step 3: Selection. Precedence: L34/override > trivial > detector.
        confidence = 1.0
        signals: dict[str, Any] = {}
        trivial = False
        if engine_override:
            engine_name = engine_override
            _logger.info(f"Engine override: {engine_name} (l34_forced={l34_forced})")
        elif self._is_trivial_task(initial_analysis):
            trivial = True
            engine_name = "claude_code"
            _logger.info("Trivial task: using claude_code (cheap)")
        else:
            engine_name, confidence, signals = self.detector.detect_engine(
                task_text,
                context,
                initial_analysis,
            )
            if debug_mode:
                _logger.info(
                    f"Engine detection (debug): {engine_name} ({confidence:.1%}) | signals={signals}"
                )

        tde_audit.emit(
            "engine_selected",
            engine=engine_name,
            confidence=confidence,
            override=bool(parsed.engine_override) and not l34_forced,
            trivial=trivial,
            task_type=initial_analysis.classification.task_type,
            complexity=initial_analysis.classification.complexity,
            tde_run_id=run_id,
        )

        # Step 4: Execute
        _logger.info(f"Executing with {engine_name}")
        result = await self.registry.execute(
            engine_name, initial_analysis, context, task_text=task_text, run_id=run_id,
            session_key=self.session_key,
        )
        if not isinstance(result, dict):
            result = {"engine": engine_name, "success": bool(result), "output": result}

        # Step 5: Record REAL outcome for engine-selection learning.
        # (Per-step delegation losses are recorded inside the TDE executor;
        # this entry tracks whole-task engine outcomes.)
        # A TDE run in which NOTHING was actually delegated must not be
        # booked as tiered_delegation evidence — that would fabricate a
        # "TDE works great" track record out of purely-local execution
        # (round-2 refutation finding). Such runs get a distinct engine tag.
        outcome_engine = engine_name
        if engine_name == "tiered_delegation":
            delegated = (result.get("summary") or {}).get("delegated", 0)
            if not delegated:
                outcome_engine = "tiered_delegation_local"
        self.loss_tracker.record_via_proxy(
            task_type=initial_analysis.classification.task_type,
            engine=outcome_engine,
            schema_valid=bool(result.get("success")),
            downstream_ok=bool(result.get("success")),
            complexity=initial_analysis.classification.complexity,
        )

        selection_info: dict[str, Any] = {
            "engine": engine_name,
            "confidence": round(confidence, 4),
            "override": parsed.engine_override,
            "l34_forced": l34_forced,
            "trivial": trivial,
        }
        if debug_mode:
            selection_info["signals"] = signals
        result.setdefault("engine_selection", selection_info)

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
