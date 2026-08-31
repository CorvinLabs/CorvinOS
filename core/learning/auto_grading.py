"""Auto-grading module: Bayesian skill grade calculation from task results.

ADR-0360: SkillForgeSubsystem learns from task outcomes via Bayesian updates.
This module extracts features from task results and computes confidence scores.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
from datetime import datetime
import math
import logging

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceGrade:
    """Result of auto-grading a skill execution."""
    score: float  # 0.0-1.0 confidence score
    explanation: str  # Human-readable reasoning
    features: Dict[str, Any]  # Extracted features for auditing
    timestamp: str = ""  # ISO 8601

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()
        # Clamp score to valid range
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"Score must be 0.0-1.0, got {self.score}")


def auto_grade(
    task_result: Dict[str, Any],
    prior_confidence: float = 0.5,
    feedback: Optional[str] = None,
) -> ConfidenceGrade:
    """Auto-grade a skill's task result using Bayesian confidence update.

    Extracts features from task_result and applies Bayesian update to prior confidence:
    new_confidence = (1 - alpha) * prior + alpha * (prior + delta)

    where:
    - alpha = 0.3 (learning rate)
    - delta = feature_score - 0.5 (improvement signal)

    Args:
        task_result: Task execution result dict with keys:
            - success: bool (did the task complete?)
            - latency_ms: int (execution time)
            - error: Optional[str] (error message if failed)
            - output_quality: Optional[float] (0-1, if available)
            - user_feedback: Optional[str] (operator feedback)
            - tokens_used: Optional[int] (token count)
        prior_confidence: Prior confidence (0.0-1.0, default 0.5)
        feedback: Optional operator feedback string

    Returns:
        ConfidenceGrade with score and explanation

    Raises:
        ValueError: If task_result is malformed or prior_confidence invalid
    """
    # Validate inputs
    if not isinstance(task_result, dict):
        raise ValueError(f"task_result must be dict, got {type(task_result)}")
    if not 0.0 <= prior_confidence <= 1.0:
        raise ValueError(f"prior_confidence must be 0.0-1.0, got {prior_confidence}")

    # Extract features
    features = _extract_features(task_result, feedback)

    # Calculate feature score (0.0-1.0)
    feature_score = _calculate_feature_score(features)

    # Bayesian update
    alpha = 0.3  # Learning rate (30% new evidence, 70% prior)
    delta = feature_score - 0.5  # Improvement signal
    new_confidence = (1 - alpha) * prior_confidence + alpha * (prior_confidence + delta)

    # Clamp to valid range
    new_confidence = max(0.0, min(1.0, new_confidence))

    # Generate explanation
    explanation = _generate_explanation(features, prior_confidence, new_confidence)

    return ConfidenceGrade(
        score=new_confidence,
        explanation=explanation,
        features=features,
    )


def _extract_features(task_result: Dict[str, Any], feedback: Optional[str]) -> Dict[str, Any]:
    """Extract grading features from task result.

    Args:
        task_result: Task execution result
        feedback: Optional operator feedback

    Returns:
        Dict with extracted features and their values
    """
    features = {}

    # Success signal (strong indicator)
    success = task_result.get("success", False)
    features["success"] = success
    if success:
        features["success_weight"] = 1.0
    else:
        features["success_weight"] = 0.0

    # Latency signal (lower is better, but avoid premature optimization)
    latency_ms = task_result.get("latency_ms", 1000)
    features["latency_ms"] = latency_ms
    # Latency score: normalize to 0-1 (< 100ms = 1.0, > 5000ms = 0.0)
    if latency_ms < 100:
        features["latency_score"] = 1.0
    elif latency_ms > 5000:
        features["latency_score"] = 0.0
    else:
        features["latency_score"] = 1.0 - (latency_ms - 100) / 4900

    # Output quality (if available)
    output_quality = task_result.get("output_quality")
    if output_quality is not None:
        if not 0.0 <= output_quality <= 1.0:
            output_quality = max(0.0, min(1.0, output_quality))
        features["output_quality"] = output_quality
    else:
        features["output_quality"] = 0.5 if success else 0.0

    # Error classification
    error = task_result.get("error")
    features["error"] = error
    features["error_severity"] = _classify_error_severity(error)

    # Token usage (if available, used for cost awareness)
    tokens_used = task_result.get("tokens_used")
    if tokens_used:
        features["tokens_used"] = tokens_used
        # Token efficiency score: prefer < 1000 tokens
        features["token_efficiency_score"] = max(0.0, 1.0 - (tokens_used / 2000))
    else:
        features["token_efficiency_score"] = 0.5

    # User feedback signal
    if feedback:
        feedback_lower = feedback.lower()
        if any(p in feedback_lower for p in ["excellent", "perfect", "great", "love"]):
            features["user_feedback_score"] = 1.0
            features["user_feedback_text"] = "positive"
        elif any(p in feedback_lower for p in ["bad", "poor", "terrible", "hate", "fail"]):
            features["user_feedback_score"] = 0.0
            features["user_feedback_text"] = "negative"
        else:
            features["user_feedback_score"] = 0.5
            features["user_feedback_text"] = "neutral"
    else:
        features["user_feedback_score"] = 0.5
        features["user_feedback_text"] = "not provided"

    return features


def _calculate_feature_score(features: Dict[str, Any]) -> float:
    """Calculate composite feature score (0.0-1.0).

    Combines all features using weighted average:
    - Success: 40% weight (most important)
    - Output quality: 30% weight
    - Latency: 15% weight
    - Token efficiency: 10% weight
    - User feedback: 5% weight

    Args:
        features: Feature dict from _extract_features

    Returns:
        Composite score 0.0-1.0
    """
    score = 0.0

    # Success (40%)
    success_score = features.get("success_weight", 0.5)
    score += success_score * 0.40

    # Output quality (30%)
    output_quality = features.get("output_quality", 0.5)
    score += output_quality * 0.30

    # Latency (15%)
    latency_score = features.get("latency_score", 0.5)
    score += latency_score * 0.15

    # Token efficiency (10%)
    token_score = features.get("token_efficiency_score", 0.5)
    score += token_score * 0.10

    # User feedback (5%)
    feedback_score = features.get("user_feedback_score", 0.5)
    score += feedback_score * 0.05

    # Apply error penalty if present
    error_severity = features.get("error_severity", 0.0)
    if error_severity > 0:
        score = score * (1 - error_severity * 0.5)  # Max 50% penalty

    return max(0.0, min(1.0, score))


def _classify_error_severity(error: Optional[str]) -> float:
    """Classify error severity (0.0 = no error, 1.0 = critical).

    Args:
        error: Error message or None

    Returns:
        Severity score 0.0-1.0
    """
    if not error:
        return 0.0

    error_lower = error.lower()

    # Critical errors (timeout, crash, security)
    if any(p in error_lower for p in ["timeout", "crash", "security", "forbidden", "unauthorized"]):
        return 1.0

    # High-severity errors (validation, constraint)
    if any(p in error_lower for p in ["validation", "constraint", "invalid", "malformed"]):
        return 0.7

    # Medium-severity errors (retry, degraded)
    if any(p in error_lower for p in ["retry", "degraded", "partial", "incomplete"]):
        return 0.4

    # Low-severity errors (warning, deprecated)
    if any(p in error_lower for p in ["warning", "deprecated", "fallback"]):
        return 0.1

    # Unknown error (assume medium)
    return 0.5


def _generate_explanation(
    features: Dict[str, Any],
    prior_confidence: float,
    new_confidence: float,
) -> str:
    """Generate human-readable explanation of grade decision.

    Args:
        features: Feature dict
        prior_confidence: Prior confidence
        new_confidence: New confidence after update

    Returns:
        Explanation string
    """
    parts = []

    # Outcome
    success = features.get("success", False)
    if success:
        parts.append("✓ Task succeeded")
    else:
        error = features.get("error")
        if error:
            parts.append(f"✗ Task failed: {error[:50]}...")
        else:
            parts.append("✗ Task failed")

    # Quality
    output_quality = features.get("output_quality", 0.5)
    if output_quality >= 0.8:
        parts.append("High output quality")
    elif output_quality <= 0.2:
        parts.append("Low output quality")

    # Performance
    latency_ms = features.get("latency_ms", 0)
    if latency_ms < 100:
        parts.append("Fast execution")
    elif latency_ms > 3000:
        parts.append("Slow execution")

    # Feedback
    feedback_text = features.get("user_feedback_text", "")
    if feedback_text == "positive":
        parts.append("Positive user feedback")
    elif feedback_text == "negative":
        parts.append("Negative user feedback")

    # Confidence change
    confidence_delta = new_confidence - prior_confidence
    if confidence_delta > 0.1:
        parts.append(f"Confidence improved +{confidence_delta:.2f}")
    elif confidence_delta < -0.1:
        parts.append(f"Confidence decreased {confidence_delta:.2f}")

    explanation = " | ".join(parts)
    if not explanation:
        explanation = "Task completed with neutral signals"

    return explanation


if __name__ == "__main__":
    # Example usage
    result = {
        "success": True,
        "latency_ms": 150,
        "output_quality": 0.9,
        "tokens_used": 500,
        "error": None,
    }

    grade = auto_grade(result, prior_confidence=0.5)
    print(f"Score: {grade.score:.3f}")
    print(f"Explanation: {grade.explanation}")
