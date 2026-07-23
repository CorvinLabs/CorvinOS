"""ADR-0214: Robust Engine Detector with Multi-Signal Ensemble.

Detects which Agentic Compute Engine (TDE, ACS, Claude-Code) to use
based on 5 independent signals (parallelization, historical loss, data/complexity,
task type, context availability).

Uses softmax ensemble with logit-scaling for real probability outputs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Optional

try:
    from operator.orchestration.initial_analysis import InitialAnalysisRequest
except ImportError:
    from initial_analysis import InitialAnalysisRequest  # type: ignore

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


class RobustEngineDetector:
    """Multi-signal ensemble for engine selection."""

    # Weights (sum to 1.0 by design)
    WEIGHTS = {
        "signal_parallelization": 0.30,
        "signal_data_complexity": 0.20,
        "signal_task_type": 0.20,
        "signal_historical": 0.25,
        "signal_context": 0.05,
    }

    # Logit scaling factor (to prevent Softmax saturation at ~33%)
    LOGIT_SCALE = 5.0

    # Quality threshold for loss-gate (independent of tokens)
    QUALITY_THRESHOLD = 0.05  # 5% max acceptable loss

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

        if task_type in ["code_generation", "code_analysis", "refactoring", "debugging"]:
            signal_task_type = 0.75 * llm_confidence  # Coding = TDE, weighted by confidence
        elif task_type in ["reasoning", "decomposition", "hierarchical_planning"]:
            signal_task_type = -0.6  # Reasoning = ACS
        else:
            signal_task_type = 0.0  # Neutral

        # === Signal 4: Historical Loss Profile ===
        if self.loss_tracker and hasattr(self.loss_tracker, 'history') and self.loss_tracker.history:
            avg_loss = self.loss_tracker.estimate_loss_for_task_type(task_type, complexity)
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
        )

    def _ensemble_scores(self, signals: DetectionSignals) -> dict[str, float]:
        """Combine signals into engine scores via softmax."""

        # Compute per-engine logits
        tde_logit = (
            signals.signal_parallelization * self.WEIGHTS["signal_parallelization"]
            + signals.signal_data_complexity * self.WEIGHTS["signal_data_complexity"]
            + signals.signal_task_type * self.WEIGHTS["signal_task_type"]
            + signals.signal_historical * self.WEIGHTS["signal_historical"]
            + signals.signal_context * self.WEIGHTS["signal_context"]
        )

        acs_logit = (
            -signals.signal_data_complexity * 0.5  # ACS for large data
            + (0.75 if signals.signal_task_type < 0 else 0.0)  # ACS for reasoning
            + 0.3  # ACS baseline
        )

        claude_code_logit = 0.2  # Claude-Code baseline

        # Softmax with logit-scaling (fix saturation)
        logits = {
            "tiered_delegation": max(0, tde_logit) * self.LOGIT_SCALE,
            "acs": max(0, acs_logit) * self.LOGIT_SCALE,
            "claude_code": max(0, claude_code_logit) * self.LOGIT_SCALE,
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
