"""
Phase 3 Complexity Metrics — True Complexity Accuracy Measurement

Defines metrics for evaluating the ComplexityJudge's ability to classify
task true complexity, with specific focus on Phase 3's "kurz aber komplex"
(short but complex) challenge.

Metrics:
1. Classification Accuracy (overall, by tier)
2. Edge Case Detection (kurz aber komplex TP rate, lang aber simpel TP rate)
3. Confidence Calibration (does judge confidence match actual accuracy?)
4. Signal Strength Distribution
5. Failure Analysis (false positives/negatives)

Success Criteria:
- Overall accuracy: ≥92%
- Complex detection rate (true positives): ≥90%
- Edge case detection: ≥85% for "kurz aber komplex", ≥85% for "lang aber simpel"
- Confidence calibration: correlation(judge_confidence, actual_correctness) ≥ 0.75
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import json
from datetime import datetime


class MetricTier(Enum):
    """Complexity tier."""
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


@dataclass
class TaskResult:
    """Result of judging a single task."""
    task_id: str
    prompt: str
    true_complexity: str  # "simple", "medium", "complex" (ground truth)
    true_category: str    # "Mathematical", "DomainExpertise", "NuancedReasoning", "FalseComplexity"
    predicted_complexity: str  # Judge's prediction
    predicted_score: float  # 0-100 composite score
    predicted_confidence: float  # 0-1.0 confidence
    signal_q1: float  # Domain knowledge signal
    signal_q2: float  # Reasoning depth signal
    signal_q3: float  # Problem novelty signal
    correct: bool = field(init=False)
    prompt_length: int = field(init=False)
    is_edge_case: bool = field(init=False)  # True if "kurz aber komplex" or "lang aber simpel"

    def __post_init__(self):
        """Compute derived fields."""
        self.correct = (self.predicted_complexity == self.true_complexity)
        self.prompt_length = len(self.prompt.split())
        # Edge case: short (< 20 tokens) but complex, or long (> 100 tokens) but simple
        self.is_edge_case = (
            (self.prompt_length < 20 and self.true_complexity == "complex") or
            (self.prompt_length > 100 and self.true_complexity == "simple")
        )


@dataclass
class Phase3Metrics:
    """Phase 3 benchmarking metrics."""

    # Task results
    results: List[TaskResult] = field(default_factory=list)

    # Metadata
    timestamp_utc: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    num_tasks: int = 0
    judge_version: str = "phase_3_v1"

    # Success criteria
    SUCCESS_CRITERIA = {
        "overall_accuracy": 0.92,  # ≥92%
        "complex_recall": 0.90,    # ≥90% of COMPLEX tasks detected
        "simple_specificity": 0.90,  # ≥90% of SIMPLE tasks correctly classified
        "medium_accuracy": 0.85,   # ≥85% (harder tier to classify)
        "edge_case_accuracy": 0.85,  # ≥85% for "kurz aber komplex"
        "confidence_calibration": 0.75,  # Pearson correlation ≥ 0.75
    }

    def add_result(self, result: TaskResult) -> None:
        """Add a task result."""
        self.results.append(result)
        self.num_tasks = len(self.results)

    def accuracy_overall(self) -> float:
        """Overall classification accuracy (0-1)."""
        if not self.results:
            return 0.0
        correct = sum(1 for r in self.results if r.correct)
        return correct / len(self.results)

    def accuracy_by_tier(self) -> Dict[str, float]:
        """Accuracy broken down by predicted tier."""
        by_tier = {}
        for tier in ["simple", "medium", "complex"]:
            tier_results = [r for r in self.results if r.true_complexity == tier]
            if not tier_results:
                by_tier[tier] = 0.0
            else:
                correct = sum(1 for r in tier_results if r.correct)
                by_tier[tier] = correct / len(tier_results)
        return by_tier

    def recall_by_tier(self) -> Dict[str, float]:
        """Recall (true positive rate) by tier."""
        by_tier = {}
        for tier in ["simple", "medium", "complex"]:
            tier_results = [r for r in self.results if r.predicted_complexity == tier]
            if not tier_results:
                by_tier[tier] = 0.0
            else:
                correct = sum(1 for r in tier_results if r.true_complexity == tier)
                by_tier[tier] = correct / len(tier_results)
        return by_tier

    def edge_case_accuracy(self) -> float:
        """Accuracy on "kurz aber komplex" and "lang aber simpel" tasks."""
        edge_cases = [r for r in self.results if r.is_edge_case]
        if not edge_cases:
            return 0.0
        correct = sum(1 for r in edge_cases if r.correct)
        return correct / len(edge_cases)

    def edge_case_breakdown(self) -> Dict[str, float]:
        """Accuracy breakdown for different edge case types."""
        breakdown = {}

        # Kurz aber komplex: short (< 20 tokens) and truly complex
        kurz_aber_komplex = [
            r for r in self.results
            if r.prompt_length < 20 and r.true_complexity == "complex"
        ]
        if kurz_aber_komplex:
            correct = sum(1 for r in kurz_aber_komplex if r.correct)
            breakdown["kurz_aber_komplex"] = correct / len(kurz_aber_komplex)
        else:
            breakdown["kurz_aber_komplex"] = 0.0

        # Lang aber simpel: long (> 100 tokens) and truly simple
        lang_aber_simpel = [
            r for r in self.results
            if r.prompt_length > 100 and r.true_complexity == "simple"
        ]
        if lang_aber_simpel:
            correct = sum(1 for r in lang_aber_simpel if r.correct)
            breakdown["lang_aber_simpel"] = correct / len(lang_aber_simpel)
        else:
            breakdown["lang_aber_simpel"] = 0.0

        return breakdown

    def accuracy_by_category(self) -> Dict[str, float]:
        """Accuracy by task category (Mathematical, DomainExpertise, etc.)."""
        by_cat = {}
        categories = set(r.true_category for r in self.results)
        for cat in sorted(categories):
            cat_results = [r for r in self.results if r.true_category == cat]
            if not cat_results:
                by_cat[cat] = 0.0
            else:
                correct = sum(1 for r in cat_results if r.correct)
                by_cat[cat] = correct / len(cat_results)
        return by_cat

    def confidence_calibration_pearson(self) -> float:
        """Pearson correlation between judge confidence and actual correctness."""
        if len(self.results) < 3:
            return 0.0

        confidences = [r.predicted_confidence for r in self.results]
        correctness = [float(r.correct) for r in self.results]

        # Compute Pearson correlation
        mean_conf = sum(confidences) / len(confidences)
        mean_corr = sum(correctness) / len(correctness)

        numerator = sum(
            (confidences[i] - mean_conf) * (correctness[i] - mean_corr)
            for i in range(len(confidences))
        )
        denom_conf = sum((c - mean_conf) ** 2 for c in confidences) ** 0.5
        denom_corr = sum((c - mean_corr) ** 2 for c in correctness) ** 0.5

        if denom_conf == 0 or denom_corr == 0:
            return 0.0

        return numerator / (denom_conf * denom_corr)

    def signal_strength_by_complexity(self) -> Dict[str, Dict[str, float]]:
        """Average signal strength (Q1, Q2, Q3) by true complexity."""
        by_complexity = {}
        for tier in ["simple", "medium", "complex"]:
            tier_results = [r for r in self.results if r.true_complexity == tier]
            if not tier_results:
                by_complexity[tier] = {"q1_avg": 0, "q2_avg": 0, "q3_avg": 0}
            else:
                by_complexity[tier] = {
                    "q1_avg": sum(r.signal_q1 for r in tier_results) / len(tier_results),
                    "q2_avg": sum(r.signal_q2 for r in tier_results) / len(tier_results),
                    "q3_avg": sum(r.signal_q3 for r in tier_results) / len(tier_results),
                }
        return by_complexity

    def confusion_matrix(self) -> Dict[str, Dict[str, int]]:
        """Confusion matrix: [true_label][predicted_label] = count."""
        tiers = ["simple", "medium", "complex"]
        matrix = {tier: {pred: 0 for pred in tiers} for tier in tiers}
        for r in self.results:
            matrix[r.true_complexity][r.predicted_complexity] += 1
        return matrix

    def production_ready_verdict(self) -> Dict[str, bool]:
        """Evaluate if metrics meet production readiness criteria."""
        return {
            "overall_accuracy_ok": self.accuracy_overall() >= self.SUCCESS_CRITERIA["overall_accuracy"],
            "complex_recall_ok": self.recall_by_tier().get("complex", 0) >= self.SUCCESS_CRITERIA["complex_recall"],
            "simple_specificity_ok": self.recall_by_tier().get("simple", 0) >= self.SUCCESS_CRITERIA["simple_specificity"],
            "edge_case_ok": self.edge_case_accuracy() >= self.SUCCESS_CRITERIA["edge_case_accuracy"],
            "confidence_calibration_ok": self.confidence_calibration_pearson() >= self.SUCCESS_CRITERIA["confidence_calibration"],
        }

    def report(self) -> str:
        """Generate human-readable report."""
        report = f"""
===== PHASE 3 COMPLEXITY JUDGE METRICS REPORT =====
Timestamp: {self.timestamp_utc}
Judge Version: {self.judge_version}
Total Tasks: {self.num_tasks}

OVERALL ACCURACY: {self.accuracy_overall():.1%}
SUCCESS CRITERION: {self.SUCCESS_CRITERIA['overall_accuracy']:.1%}
STATUS: {'✓ PASS' if self.accuracy_overall() >= self.SUCCESS_CRITERIA['overall_accuracy'] else '✗ FAIL'}

ACCURACY BY TIER:
{json.dumps(self.accuracy_by_tier(), indent=2)}

RECALL BY TIER (True Positive Rate):
{json.dumps(self.recall_by_tier(), indent=2)}

EDGE CASE DETECTION:
  Overall: {self.edge_case_accuracy():.1%}
  Breakdown:
{json.dumps(self.edge_case_breakdown(), indent=4)}

ACCURACY BY CATEGORY:
{json.dumps(self.accuracy_by_category(), indent=2)}

CONFUSION MATRIX (True vs Predicted):
{json.dumps(self.confusion_matrix(), indent=2)}

SIGNAL STRENGTH BY COMPLEXITY:
{json.dumps(self.signal_strength_by_complexity(), indent=2)}

CONFIDENCE CALIBRATION:
  Pearson Correlation: {self.confidence_calibration_pearson():.3f}
  SUCCESS CRITERION: {self.SUCCESS_CRITERIA['confidence_calibration']:.3f}
  STATUS: {'✓ PASS' if self.confidence_calibration_pearson() >= self.SUCCESS_CRITERIA['confidence_calibration'] else '✗ FAIL'}

PRODUCTION READINESS VERDICT:
{json.dumps(self.production_ready_verdict(), indent=2)}

OVERALL PRODUCTION STATUS:
  {'✓ PRODUCTION-READY' if all(self.production_ready_verdict().values()) else '⚠ CONDITIONAL / ✗ NOT READY'}
"""
        return report

    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return {
            "timestamp_utc": self.timestamp_utc,
            "judge_version": self.judge_version,
            "num_tasks": self.num_tasks,
            "overall_accuracy": self.accuracy_overall(),
            "accuracy_by_tier": self.accuracy_by_tier(),
            "recall_by_tier": self.recall_by_tier(),
            "edge_case_accuracy": self.edge_case_accuracy(),
            "edge_case_breakdown": self.edge_case_breakdown(),
            "accuracy_by_category": self.accuracy_by_category(),
            "confusion_matrix": self.confusion_matrix(),
            "signal_strength_by_complexity": self.signal_strength_by_complexity(),
            "confidence_calibration_pearson": self.confidence_calibration_pearson(),
            "production_ready_verdict": self.production_ready_verdict(),
        }

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), indent=2)


# Success criteria constants (exported for use in tests)
PHASE3_SUCCESS_CRITERIA = Phase3Metrics.SUCCESS_CRITERIA
