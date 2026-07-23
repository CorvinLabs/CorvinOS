"""ADR-0214: Robust Engine Detector with Multi-Signal Ensemble.

Detects which Agentic Compute Engine (TDE, ACS, Claude-Code) to use
based on 5 independent signals (parallelization, historical loss, data/complexity,
task type, context availability).

Uses softmax ensemble with logit-scaling for real probability outputs.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Optional

try:
    from initial_analysis import InitialAnalysisRequest
except ImportError:  # pragma: no cover - orchestration dir not on sys.path
    from ..initial_analysis import InitialAnalysisRequest  # type: ignore

_logger = logging.getLogger(__name__)


@dataclass
class DetectionSignals:
    """Intermediate signal scores (before ensemble)."""
    parallelization_ratio: float
    signal_parallelization: float

    data_mb: float
    complexity: str
    signal_data_complexity: float

    task_type: str
    llm_confidence: float
    signal_task_type: float

    historical_loss_pct: float
    signal_historical: float

    has_full_context: bool
    signal_context: float

    # ADR-0214 refined: ACS vs. TDE routing based on iteration + context, not volume
    iteration_loops: int  # Count of steps that re-read prior outputs (default: 1)
    signal_iteration: float  # 0-1: penalty for ACS (hates iteration); reward for TDE

    user_interactive: bool  # Is user present for real-time steering? (default: False)
    signal_interaction: float  # 0-1: TDE for interactive, ACS for batch

    sensitivity_level: str  # PUBLIC | INTERNAL | CONFIDENTIAL | RESTRICTED (default: PUBLIC)
    # Note: RESTRICTED blocks ACS entirely (L34 hard-gate), not here


class RobustEngineDetector:
    """Multi-signal ensemble for engine selection."""

    # Weights (sum to 1.0 by design)
    # ADR-0214 refined: increased parallelization weight, added iteration penalty
    WEIGHTS = {
        "signal_parallelization": 0.35,  # Increased: most important for ACS/TDE split
        "signal_iteration": 0.20,  # NEW: ACS hates iteration; TDE loves it
        "signal_task_type": 0.15,  # Reduced (was 0.20)
        "signal_historical": 0.20,  # Reduced (was 0.25)
        "signal_context": 0.05,  # Unchanged
        "signal_data_complexity": 0.03,  # Reduced to minimal (was 0.20; volume alone insufficient)
        "signal_interaction": 0.02,  # Minimal: tiebreaker only
    }

    # Logit scaling factor (to prevent Softmax saturation at ~33%)
    LOGIT_SCALE = 5.0

    # Quality threshold for loss-gate (independent of tokens)
    QUALITY_THRESHOLD = 0.05  # 5% max acceptable loss

    # Below this softmax confidence the detector refuses to pick a specialist
    # engine and falls back to claude_code (ADR-0214: safe default).
    # MUST be > 1/3: a 3-way softmax maximum is always >= 1/3, so a 0.3
    # threshold could never fire (round-2 refutation: dead code).
    CONFIDENCE_THRESHOLD = 0.45
    FALLBACK_ENGINE = "claude_code"

    def __init__(self, loss_tracker: Optional[Any] = None):
        """Initialize detector with optional loss-tracker for historical signals."""
        self.loss_tracker = loss_tracker

    def detect_engine(
        self,
        task: str,
        context: dict[str, Any],
        initial_analysis: InitialAnalysisRequest
    ) -> tuple[str, float, dict[str, Any]]:
        """
        Detect which engine to use.

        Args:
            task: Task description
            context: Task context (files, statement, etc)
            initial_analysis: Classification + plan from Phase 1

        Returns:
            (engine_name, confidence, decision_signals)
            engine_name: "tiered_delegation" | "acs" | "claude_code"
            confidence: 0.0-1.0 (real probability from softmax)
            decision_signals: dict with all signal values for transparency
        """

        # Compute 5 signals
        signals = self._compute_signals(task, context, initial_analysis)

        # Ensemble + softmax
        scores = self._ensemble_scores(signals)

        # Select best engine
        best_engine = max(scores, key=scores.get)
        confidence = scores[best_engine]

        # Uncertainty fallback: when no engine clearly wins, use the safe
        # sequential default instead of gambling on a specialist engine.
        if confidence < self.CONFIDENCE_THRESHOLD and best_engine != self.FALLBACK_ENGINE:
            _logger.info(
                "Engine detection uncertain (%.1f%% < %.0f%%) — falling back to %s",
                confidence * 100, self.CONFIDENCE_THRESHOLD * 100, self.FALLBACK_ENGINE,
            )
            best_engine = self.FALLBACK_ENGINE
            confidence = scores.get(self.FALLBACK_ENGINE, confidence)

        _logger.info(
            f"Engine detection: {best_engine} ({confidence:.1%} confidence) | "
            f"signals={vars(signals)}"
        )

        return best_engine, confidence, vars(signals)

    def _compute_signals(
        self,
        task: str,
        context: dict[str, Any],
        analysis: InitialAnalysisRequest
    ) -> DetectionSignals:
        """Compute 5 independent signals."""

        # === Signal 1: Parallelization Potential ===
        parallelizable = sum(
            1 for step in analysis.global_plan.steps
            if step.can_parallelize
        )
        total_steps = len(analysis.global_plan.steps)

        parallelization_ratio = (parallelizable / total_steps) if total_steps > 0 else 0.0
        signal_parallelization = 0.8 if parallelization_ratio >= 0.3 else 0.2

        # === Signal 2: Data Volume + Complexity ===
        data_mb = self._estimate_context_size_mb(context)
        complexity = analysis.classification.complexity

        if data_mb < 10 and complexity in ["moderate", "complex"]:
            signal_data_complexity = 0.85  # TDE sweet spot
        elif data_mb > 500:
            signal_data_complexity = -0.5  # Large data → ACS needed
        else:
            signal_data_complexity = 0.5  # Neutral

        # === Signal 3: Task Type + LLM Confidence ===
        task_type = analysis.classification.task_type
        llm_confidence = analysis.classification.confidence

        # Vocabulary MUST match the ADR-0210 analysis prompt (initial_analysis.py):
        # code_generation | data_analysis | tool_call | reasoning | transformation
        # | retrieval | delegation. "transformation" is what the classifier
        # emits for refactorings (verified against the live prompt 2026-07-23).
        # Legacy aliases kept as harmless supersets.
        if task_type in ("code_generation", "transformation",
                         "code_analysis", "refactoring", "debugging"):
            signal_task_type = 0.75 * llm_confidence  # Coding = TDE, weighted by confidence
        elif task_type in ("reasoning", "delegation",
                           "decomposition", "hierarchical_planning"):
            signal_task_type = -0.6  # Deep reasoning / recursive decomposition = ACS
        else:
            signal_task_type = 0.0  # Neutral (data_analysis, tool_call, retrieval, …)

        # === Signal 4: Historical Loss Profile ===
        # Only a LEARNED loss moves this signal off neutral. Without enough
        # evidence the tracker returns its conservative default (10%), which
        # must NOT be misread as "TDE is losing 10%" — that would penalize
        # TDE before a single delegation ever ran.
        has_evidence = False
        if self.loss_tracker is not None:
            try:
                min_samples = getattr(self.loss_tracker, "MIN_SAMPLES", 5)
                has_evidence = self.loss_tracker.evidence_for(
                    task_type, complexity, engine="tiered_delegation"
                ) >= min_samples
            except AttributeError:
                has_evidence = bool(getattr(self.loss_tracker, "history", None))

        if has_evidence:
            avg_loss = self.loss_tracker.estimate_loss_for_task_type(
                task_type, complexity, engine="tiered_delegation"
            )
            historical_loss_pct = avg_loss * 100

            if avg_loss < 0.03:  # <3% loss
                signal_historical = 0.85
            elif avg_loss > 0.08:  # >8% loss
                signal_historical = 0.2
            else:
                signal_historical = 0.5
        else:
            # No history: conservative
            historical_loss_pct = 10.0  # Default 10% until we have data
            signal_historical = 0.5

        # === Signal 5: Context Availability ===
        has_full_context = "statement" in context and context.get("statement") is not None
        signal_context = 0.8 if has_full_context else 0.1

        # === NEW Signal 6: Iteration Loops (ADR-0214 refined) ===
        # Count how many steps re-read prior outputs (indicates iterative refinement).
        # Heuristic: if plan is long + sequential (low parallelization), assume iterative.
        iteration_loops = 1  # Conservative default: assume one-pass
        if parallelization_ratio < 0.3 and total_steps > 3:
            iteration_loops = min(total_steps, 3)  # Heuristic: sequential = 2-3 iterations
        signal_iteration = (
            0.2 if iteration_loops <= 2 else 0.8  # ACS hates iteration; TDE loves it
        )

        # === NEW Signal 7: User Interaction ===
        # Is user present for real-time steering? Heuristic: interactive task types.
        user_interactive = task_type in ("reasoning", "debugging", "code_analysis")
        signal_interaction = 0.8 if user_interactive else 0.2  # TDE for interactive

        # === NEW Signal 8: Data Sensitivity ===
        # Extract sensitivity from context if available (set by L34PreGate).
        # Default: PUBLIC (safe assumption; L34 gate will override for sensitive data).
        sensitivity_level = context.get("sensitivity_level", "PUBLIC")

        return DetectionSignals(
            parallelization_ratio=parallelization_ratio,
            signal_parallelization=signal_parallelization,
            data_mb=data_mb,
            complexity=complexity,
            signal_data_complexity=signal_data_complexity,
            task_type=task_type,
            llm_confidence=llm_confidence,
            signal_task_type=signal_task_type,
            historical_loss_pct=historical_loss_pct,
            signal_historical=signal_historical,
            has_full_context=has_full_context,
            signal_context=signal_context,
            iteration_loops=iteration_loops,
            signal_iteration=signal_iteration,
            user_interactive=user_interactive,
            signal_interaction=signal_interaction,
            sensitivity_level=sensitivity_level,
        )

    def _ensemble_scores(self, signals: DetectionSignals) -> dict[str, float]:
        """Combine signals into engine scores via softmax.

        ADR-0214 refined routing:
        - ACS: high parallelization + low iteration + stateless (PUBLIC data)
        - TDE: iterative refinement + context-carrying + user interaction
        """

        # === TDE Logit: Context-carrying, iterative, interactive ===
        tde_logit = (
            # Parallelization: TDE OK with <40% parallel (vs ACS needs >60%)
            (1.0 - signals.signal_parallelization) * self.WEIGHTS["signal_parallelization"]
            # Iteration: TDE LOVES iteration (ACS hates it)
            + signals.signal_iteration * self.WEIGHTS["signal_iteration"]
            # Task type: TDE for coding, user interaction
            + signals.signal_task_type * self.WEIGHTS["signal_task_type"]
            # Historical loss: use empirical data
            + signals.signal_historical * self.WEIGHTS["signal_historical"]
            # Context: TDE needs full context
            + signals.signal_context * self.WEIGHTS["signal_context"]
            # Interaction: TDE for real-time steering
            + signals.signal_interaction * self.WEIGHTS["signal_interaction"]
            # Data complexity: TDE OK with complex (iterative refinement)
            + (0.5 if signals.complexity in ["moderate", "complex"] else 0.0) * 0.01
        )

        # === ACS Logit: Parallelizable, stateless, batch ===
        acs_logit = (
            # Parallelization: ACS LOVES high parallelization
            signals.signal_parallelization * self.WEIGHTS["signal_parallelization"]
            # Iteration: ACS HATES iteration (penalty)
            - signals.signal_iteration * self.WEIGHTS["signal_iteration"]
            # Task type: ACS for reasoning / decomposition (if task_type < 0)
            + (0.6 if signals.signal_task_type < 0 else -0.2) * self.WEIGHTS["signal_task_type"]
            # Data: large data helps ACS only if parallelizable
            + (0.3 if signals.data_mb > 500 and signals.parallelization_ratio > 0.5 else -0.1)
                * self.WEIGHTS["signal_data_complexity"]
            # Interaction: ACS hates interactive (batch-oriented)
            - signals.signal_interaction * self.WEIGHTS["signal_interaction"]
            + 0.2  # ACS baseline
        )

        # === Claude-Code Logit: Safe default, sequential, interactive ===
        claude_code_logit = (
            # Is this truly simple? (low parallelization + low data)
            (0.3 if signals.parallelization_ratio < 0.2 and signals.data_mb < 50 else 0.0)
            # Context availability helps CC (direct access to files/state)
            + signals.signal_context * self.WEIGHTS["signal_context"] * 0.5
            + 0.15  # CC baseline (safe default for uncertainty)
        )

        # Softmax with logit-scaling (fix saturation). NO clamping: softmax
        # handles negative logits natively.
        logits = {
            "tiered_delegation": tde_logit * self.LOGIT_SCALE,
            "acs": acs_logit * self.LOGIT_SCALE,
            "claude_code": claude_code_logit * self.LOGIT_SCALE,
        }

        return self._softmax(logits)

    def _softmax(self, scores: dict[str, float]) -> dict[str, float]:
        """Normalize scores to probabilities via softmax."""
        if not scores:
            return {}

        # Numerical stability: subtract max
        max_score = max(scores.values())
        exp_scores = {k: math.exp(v - max_score) for k, v in scores.items()}
        total = sum(exp_scores.values())

        return {k: v / total for k, v in exp_scores.items()}

    def _estimate_context_size_mb(self, context: dict[str, Any]) -> float:
        """Rough estimate of context size in MB."""
        try:
            context_json = json.dumps(context, default=str)
            return len(context_json) / (1024 * 1024)
        except Exception:
            return 0.0
