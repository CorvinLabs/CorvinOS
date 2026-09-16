"""QualityOrchestrator Skill — Specification Loss Signal Architecture (ADR-0731).

Navigates 3-layer loss landscape with convergence optimizer.
Spec-as-Loss: quality gates become continuous, learnable loss fields.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

_log = logging.getLogger(__name__)


class TaskSize(Enum):
    """Auto-classified task size (from token budget)."""
    MICRO = "micro"       # <2k tokens
    MEDIUM = "medium"     # 2k–10k tokens
    MACRO = "macro"       # >10k tokens


@dataclass(frozen=True)
class QualityProfile:
    """Task-size-dependent quality profile (hyperparameters)."""
    size: TaskSize
    max_iterations: int
    loss_budget_ms: int
    dod_checks: list
    hallucin_checks: list
    weight_dod: float
    weight_hallucin: float
    threshold: float
    spec_version: str = "1.0"


@dataclass
class FailureAnalysis:
    """Analysis of a failed quality check."""
    primary_cause: str  # "hallucination" | "dod"
    detections: list = field(default_factory=list)
    failed_checks: list = field(default_factory=list)
    remediation_hint: str = ""


@dataclass
class QualityResult:
    """Result of quality orchestration (pass or fallback)."""
    status: str  # "converged" | "exhausted" | "error"
    output: Optional[str]
    score: float
    iteration: int
    spec: Optional[Dict] = None
    failure_analysis: Optional[FailureAnalysis] = None
    audit_events: list = field(default_factory=list)


# Quality Profiles (task-size dependent)
QUALITY_PROFILES = {
    TaskSize.MICRO: QualityProfile(
        size=TaskSize.MICRO,
        max_iterations=1,
        loss_budget_ms=5,
        dod_checks=[],  # Skip expensive DoD checks
        hallucin_checks=["confidence_scorer"],
        weight_dod=0.2,
        weight_hallucin=0.8,
        threshold=0.75,
    ),
    TaskSize.MEDIUM: QualityProfile(
        size=TaskSize.MEDIUM,
        max_iterations=3,
        loss_budget_ms=100,
        dod_checks=["reachability", "test_evidence"],
        hallucin_checks=["fact_check", "confidence", "contradiction"],
        weight_dod=0.4,
        weight_hallucin=0.6,
        threshold=0.85,
    ),
    TaskSize.MACRO: QualityProfile(
        size=TaskSize.MACRO,
        max_iterations=5,
        loss_budget_ms=500,
        dod_checks=["reachability", "audit_trail", "test_evidence", "docs_sync", "reproducibility"],
        hallucin_checks=["fact_check", "confidence", "contradiction", "consistency_check"],
        weight_dod=0.5,
        weight_hallucin=0.5,
        threshold=0.9,
    ),
}


def loss_landscape(
    output: str,
    task: Dict,
    spec: Dict,
    task_size: TaskSize,
    dod_verifier_score: float = 0.0,
    hallucin_detector_score: float = 0.0,
) -> Tuple[float, Dict]:
    """Compute continuous loss landscape.

    Args:
        output: LLM-generated output to score
        task: Task metadata
        spec: Current specification
        task_size: Auto-classified task size (MICRO/MEDIUM/MACRO)
        dod_verifier_score: DoD verification score ∈ [0.0, 1.0]
        hallucin_detector_score: Hallucination detection score ∈ [0.0, 1.0]

    Returns:
        (loss, components) where loss ∈ [0.0, 1.0], 0=perfect
    """
    profile = QUALITY_PROFILES[task_size]

    # Weighted loss combination
    loss_dod = 1.0 - dod_verifier_score
    loss_hallucin = 1.0 - hallucin_detector_score
    loss_combined = (
        profile.weight_dod * loss_dod +
        profile.weight_hallucin * loss_hallucin
    )

    return loss_combined, {
        "loss_dod": loss_dod,
        "loss_hallucin": loss_hallucin,
        "loss_combined": loss_combined,
        "score_dod": dod_verifier_score,
        "score_hallucin": hallucin_detector_score,
        "task_size": task_size.value,
    }


class SpecConvergenceOptimizer:
    """Learns spec updates from quality failures (ADR-0731 layer 3)."""

    def __init__(self):
        self.convergence_history = []

    def learn(
        self,
        current_spec: Dict,
        failures: FailureAnalysis,
        iteration: int,
        history: list = None,
    ) -> Dict:
        """Analyze failures + synthesize spec delta.

        Args:
            current_spec: Current specification (immutable ref)
            failures: Failure analysis (cause + detections)
            iteration: Current iteration number
            history: Convergence history (for pattern detection)

        Returns:
            delta_spec (additive only, never removes prior constraints)
        """
        delta = {}

        if failures.primary_cause == "hallucination":
            # Hallucination-driven: tighten fact checks
            delta["domain_facts_tightening"] = self._tighten_facts(failures.detections)
            delta["confidence_threshold"] = min(0.95, (current_spec.get("confidence_threshold", 0.7) + 0.05))
            _log.info(f"Iter {iteration}: Tighten hallucination guard (threshold→{delta['confidence_threshold']})")

        elif failures.primary_cause == "dod":
            # DoD-driven: add constraint
            remediation = failures.remediation_hint or ""
            if "reachability" in remediation:
                delta["reachability_constraint"] = True
            if "audit" in remediation:
                delta["require_audit_trail"] = True
            if "test" in remediation:
                delta["require_test_evidence"] = True
            _log.info(f"Iter {iteration}: Add DoD constraints ({list(delta.keys())})")

        # Track learning
        self.convergence_history.append({
            "iteration": iteration,
            "primary_cause": failures.primary_cause,
            "delta_spec": delta,
        })

        return delta

    def _tighten_facts(self, detections: list) -> Dict:
        """Synthesize fact-tightening rules from hallucination detections."""
        return {
            "detection_count": len(detections),
            "patterns": [d.get("pattern", "") for d in detections[:3]],
        }


class QualityOrchestrator:
    """Main Quality Orchestrator Skill (ADR-0731).

    Navigates loss landscape via spec-as-loss framework.
    Convergence loop: Generate → Measure → Analyze → Learn → Iterate.
    """

    def __init__(self):
        self.profiles = QUALITY_PROFILES
        self.optimizer = SpecConvergenceOptimizer()
        self.audit_events = []

    def orchestrate(
        self,
        task: Dict,
        spec: Dict,
        llm_generate_fn,
        dod_score_fn,
        hallucin_score_fn,
        audit_write_fn = None,
    ) -> QualityResult:
        """Execute quality landscape navigation (convergence loop).

        Args:
            task: Task dict with 'id', 'type', 'size'
            spec: Current specification
            llm_generate_fn: Callable(task, spec) → str (generated output)
            dod_score_fn: Callable(task, output, checks) → float
            hallucin_score_fn: Callable(output, spec) → float
            audit_write_fn: Optional audit backend writer

        Returns:
            QualityResult (converged, exhausted, or error)
        """
        task_size = task.get("size", TaskSize.MEDIUM)
        if isinstance(task_size, str):
            task_size = TaskSize(task_size)

        profile = self.profiles[task_size]
        score_quality = 0.0
        final_iteration = 0

        try:
            for iteration in range(profile.max_iterations):
                final_iteration = iteration

                # 1. Generate
                output = llm_generate_fn(task, spec)
                if not output:
                    _log.error(f"Iter {iteration}: LLM generation returned empty")
                    continue

                # 2. Measure quality
                score_dod = dod_score_fn(task, output, profile.dod_checks) if profile.dod_checks else 1.0
                score_hallucin = hallucin_score_fn(output, spec)
                score_quality = (
                    profile.weight_dod * score_dod +
                    profile.weight_hallucin * score_hallucin
                )
                loss_k, components = loss_landscape(
                    output, task, spec, task_size,
                    dod_verifier_score=score_dod,
                    hallucin_detector_score=score_hallucin,
                )

                # 3. Audit quality measurement
                event = {
                    "event_type": "quality_measured",
                    "iteration": iteration,
                    "task_id": task.get("id", "unknown"),
                    "score_dod": score_dod,
                    "score_hallucin": score_hallucin,
                    "score_quality": score_quality,
                    "loss": loss_k,
                    "components": components,
                }
                self._emit_audit(event, audit_write_fn)

                # 4. Check convergence
                if score_quality >= profile.threshold:
                    _log.info(f"Iter {iteration}: CONVERGED (score={score_quality:.3f} >= {profile.threshold})")
                    return QualityResult(
                        status="converged",
                        output=output,
                        score=score_quality,
                        iteration=iteration,
                        spec=spec,
                        audit_events=self.audit_events,
                    )

                _log.info(f"Iter {iteration}: score={score_quality:.3f}, loss={loss_k:.3f} (need {profile.threshold})")

                # 5. Analyze failure + learn
                failures = self._analyze_failures(output, spec, score_dod, score_hallucin)
                delta_spec = self.optimizer.learn(
                    current_spec=spec,
                    failures=failures,
                    iteration=iteration,
                    history=self.optimizer.convergence_history,
                )
                spec = self._merge_spec(spec, delta_spec)

                # 6. Audit spec update
                event = {
                    "event_type": "spec_updated",
                    "iteration": iteration,
                    "task_id": task.get("id", "unknown"),
                    "delta_spec": delta_spec,
                    "spec_hash": hash(str(spec)),
                    "primary_cause": failures.primary_cause,
                }
                self._emit_audit(event, audit_write_fn)

            # Exhausted iterations
            _log.warning(f"Max iterations ({profile.max_iterations}) reached, quality={score_quality:.3f}")
            event = {
                "event_type": "quality_exhausted",
                "task_id": task.get("id", "unknown"),
                "max_iterations": profile.max_iterations,
                "final_score": score_quality,
            }
            self._emit_audit(event, audit_write_fn)

            return QualityResult(
                status="exhausted",
                output=None,
                score=score_quality,
                iteration=final_iteration,
                spec=spec,
                audit_events=self.audit_events,
            )

        except Exception as e:
            _log.error(f"Quality orchestration error: {e}")
            event = {
                "event_type": "quality_error",
                "task_id": task.get("id", "unknown"),
                "error": str(e),
            }
            self._emit_audit(event, audit_write_fn)

            return QualityResult(
                status="error",
                output=None,
                score=0.0,
                iteration=final_iteration,
                audit_events=self.audit_events,
            )

    def _analyze_failures(
        self,
        output: str,
        spec: Dict,
        score_dod: float,
        score_hallucin: float,
    ) -> FailureAnalysis:
        """Identify primary failure cause."""
        if score_hallucin < score_dod:
            return FailureAnalysis(
                primary_cause="hallucination",
                detections=[{"pattern": "fact_check_failed", "confidence": 1.0 - score_hallucin}],
                remediation_hint="tighten_fact_check | expand_domain_facts",
            )
        else:
            return FailureAnalysis(
                primary_cause="dod",
                failed_checks=["reachability", "test_evidence"][:int((1.0 - score_dod) * 5)],
                remediation_hint="add_reachability_constraint | require_test_evidence",
            )

    def _merge_spec(self, current_spec: Dict, delta_spec: Dict) -> Dict:
        """Merge delta into spec (additive only, never removes)."""
        merged = dict(current_spec)
        merged.update(delta_spec)
        return merged

    def _emit_audit(self, event: Dict, write_fn = None) -> None:
        """Emit audit event."""
        self.audit_events.append(event)
        if write_fn:
            try:
                write_fn(event)
            except Exception as e:
                _log.warning(f"Audit write failed: {e}")
