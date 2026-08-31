"""k=3: Topic Drift Detection — Detect unexpected topic shifts."""

import pytest
from core.context import PipelineAddition, QualityTier
from core.context.topic_drift_detection import (
    TopicDriftDetector,
    DriftClassification,
    create_topic_drift_detector,
    should_include_addition,
)


def test_hard_blocker_detection():
    """Test: Blocking prerequisites detected (audit, safety)."""
    detector = TopicDriftDetector("Refactor module X")
    
    blocker = PipelineAddition(
        scope="session",
        source="memory:audit",
        relevance="Audit verification is a prerequisite",
        tier=QualityTier.TIER_1_ALWAYS,
        content="Must verify audit trail before changes",
    )
    
    analysis = detector.analyze_addition(blocker)
    assert analysis.classification == DriftClassification.HARD_BLOCKER
    assert analysis.recommended_action == "include"


def test_precedent_detection():
    """Test: Architectural precedent detected (ADR, pattern)."""
    detector = TopicDriftDetector("Refactor authentication")
    
    precedent = PipelineAddition(
        scope="session",
        source="adr:0278",
        relevance="ADR defines pattern",
        tier=QualityTier.TIER_2_FLAG,
        content="Follow ADR-0278 design pattern",
    )
    
    analysis = detector.analyze_addition(precedent)
    assert analysis.classification == DriftClassification.ORDER_SUGGESTION
    assert analysis.recommended_action == "flag"


def test_topic_shift_detection():
    """Test: Topic shift detected (instead, redirect, abandon)."""
    detector = TopicDriftDetector("Refactor authentication module")
    
    shift = PipelineAddition(
        scope="session",
        source="memory:idea",
        relevance="Consider instead",
        tier=QualityTier.TIER_3_ASK,
        content="Forget authentication, rebuild entire architecture",
    )
    
    analysis = detector.analyze_addition(shift)
    assert analysis.classification == DriftClassification.TOPIC_SHIFT
    assert analysis.recommended_action == "ask_user"


def test_tangential_detection():
    """Test: Tangential info detected (related, optional)."""
    detector = TopicDriftDetector("Implement feature X")
    
    tangent = PipelineAddition(
        scope="session",
        source="memory:related",
        relevance="Related module",
        tier=QualityTier.TIER_3_ASK,
        content="By the way, module Y also uses this pattern",
    )
    
    analysis = detector.analyze_addition(tangent)
    assert analysis.classification == DriftClassification.TANGENTIAL
    assert analysis.recommended_action == "skip"


def test_same_family_detection():
    """Test: Same topic family detected (keyword overlap)."""
    detector = TopicDriftDetector("Refactor authentication module")
    
    same_family = PipelineAddition(
        scope="session",
        source="memory:pattern",
        relevance="Module authentication best practices",
        tier=QualityTier.TIER_2_FLAG,
        content="Authentication should use role-based access",
    )
    
    analysis = detector.analyze_addition(same_family)
    assert analysis.classification == DriftClassification.SAME_FAMILY


def test_drift_detection_accuracy():
    """Test: Drift detection accuracy >95%."""
    detector = TopicDriftDetector("Implement user authentication")
    
    test_cases = [
        ("audit verification required", DriftClassification.HARD_BLOCKER),
        ("follow adr pattern", DriftClassification.ORDER_SUGGESTION),
        ("forget this, rebuild", DriftClassification.TOPIC_SHIFT),
        ("related module info", DriftClassification.TANGENTIAL),
        ("authentication best practices", DriftClassification.SAME_FAMILY),
    ]
    
    correct = 0
    for text, expected in test_cases:
        add = PipelineAddition(
            scope="session",
            source="test",
            relevance=text,
            tier=QualityTier.TIER_2_FLAG,
            content=text,
        )
        analysis = detector.analyze_addition(add)
        if analysis.classification == expected:
            correct += 1
    
    accuracy = (correct / len(test_cases)) * 100
    assert accuracy >= 95.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
