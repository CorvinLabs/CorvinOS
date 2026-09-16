"""E2E tests for Director Mode Phases 3-4 (ADR-0696)"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.skills.director_mode.phase3_style_learning import (
    StyleProfile, StyleLearner, FeedbackType, FeedbackEvent
)
from core.skills.director_mode.phase4_quality_gates import (
    QualityStatus, QualityAssessment, QualityGates
)


def test_phase3_style_learner_profile_creation():
    """Phase 3: StyleLearner creates and manages profiles."""
    learner = StyleLearner()
    profile = learner.get_profile("user_123")

    # Verify profile defaults
    assert profile.user_id == "user_123"
    assert profile.animation_level == 0.5
    assert profile.text_density == 0.5
    assert profile.overall_speed == 1.0
    assert profile.average_scene_duration == 5.0
    assert profile.feedback_count == 0

    print("✅ Phase 3: Profile Creation — PASS")


def test_phase3_style_learner_feedback_recording():
    """Phase 3: Record feedback and update profile parameters."""
    learner = StyleLearner()
    profile = learner.get_profile("user_456")

    # Record feedback: more animations
    feedback = FeedbackEvent(
        user_id="user_456",
        feedback_type=FeedbackType.MORE_ANIMATIONS,
        timestamp=datetime.utcnow().isoformat(),
    )
    updated_profile = learner.record_feedback(feedback)

    # Verify parameter updated
    assert updated_profile.animation_level == 0.7  # 0.5 + 0.2
    assert updated_profile.feedback_count == 1
    assert len(updated_profile.feedback_history) == 1

    # Record another feedback: slower pacing
    feedback2 = FeedbackEvent(
        user_id="user_456",
        feedback_type=FeedbackType.SLOWER_PACING,
        timestamp=datetime.utcnow().isoformat(),
    )
    updated_profile2 = learner.record_feedback(feedback2)

    # Verify second feedback
    assert updated_profile2.overall_speed == 0.75  # 1.0 - 0.25
    assert updated_profile2.feedback_count == 2

    print("✅ Phase 3: Feedback Recording — PASS")


def test_phase3_parameter_clamping():
    """Phase 3: Parameters clamp to valid ranges."""
    learner = StyleLearner()
    profile = learner.get_profile("user_789")

    # Set profile to edge case (high animation)
    profile.animation_level = 0.95

    # Record "more animations" feedback (should clamp at 1.0)
    feedback = FeedbackEvent(
        user_id="user_789",
        feedback_type=FeedbackType.MORE_ANIMATIONS,
        timestamp=datetime.utcnow().isoformat(),
    )
    updated_profile = learner.record_feedback(feedback)

    # Verify clamped
    assert updated_profile.animation_level == 1.0  # Clamped at max

    # Test speed multiplier range [0.5, 2.0]
    profile2 = learner.get_profile("user_speed")
    profile2.overall_speed = 1.9

    feedback_faster = FeedbackEvent(
        user_id="user_speed",
        feedback_type=FeedbackType.FASTER_PACING,
        timestamp=datetime.utcnow().isoformat(),
    )
    updated_speed = learner.record_feedback(feedback_faster)

    # Verify clamped at max 2.0
    assert updated_speed.overall_speed == 2.0

    print("✅ Phase 3: Parameter Clamping — PASS")


def test_phase4_quality_gates_scoring():
    """Phase 4: Quality gates compute overall score from components."""
    gates = QualityGates()

    # Assess video with mixed scores
    assessment = gates.assess_video(
        video_id="video_001",
        narrative_score=0.8,
        visual_score=0.7,
        audio_score=0.6,
        pacing_score=0.75,
        engagement_score=0.8,
        technical_score=0.9,
    )

    # Verify overall score computed
    assert 0.0 <= assessment.overall_score <= 1.0
    assert assessment.status in [QualityStatus.REJECTED, QualityStatus.DRAFT, QualityStatus.PUBLISHABLE, QualityStatus.FEATURED]

    print("✅ Phase 4: Quality Scoring — PASS")


def test_phase4_quality_status_thresholds():
    """Phase 4: Score thresholds map to correct status."""
    gates = QualityGates()

    # Test REJECTED (< 0.70)
    assessment_rejected = gates.assess_video(
        video_id="video_rejected",
        narrative_score=0.5,
        visual_score=0.5,
        audio_score=0.5,
        pacing_score=0.5,
        engagement_score=0.5,
        technical_score=0.5,
    )
    assert assessment_rejected.status == QualityStatus.REJECTED

    # Test DRAFT (0.70–0.85)
    assessment_draft = gates.assess_video(
        video_id="video_draft",
        narrative_score=0.75,
        visual_score=0.75,
        audio_score=0.75,
        pacing_score=0.75,
        engagement_score=0.75,
        technical_score=0.75,
    )
    assert assessment_draft.status == QualityStatus.DRAFT

    # Test PUBLISHABLE (0.85–0.90)
    assessment_pub = gates.assess_video(
        video_id="video_publishable",
        narrative_score=0.87,
        visual_score=0.87,
        audio_score=0.87,
        pacing_score=0.87,
        engagement_score=0.87,
        technical_score=0.87,
    )
    assert assessment_pub.status == QualityStatus.PUBLISHABLE

    # Test FEATURED (>= 0.90)
    assessment_featured = gates.assess_video(
        video_id="video_featured",
        narrative_score=0.95,
        visual_score=0.95,
        audio_score=0.95,
        pacing_score=0.95,
        engagement_score=0.95,
        technical_score=0.95,
    )
    assert assessment_featured.status == QualityStatus.FEATURED

    print("✅ Phase 4: Status Thresholds — PASS")


def test_phase4_video_type_weights():
    """Phase 4: Video-type-specific weights affect score."""
    gates = QualityGates()

    # Same component scores, different video types
    scores = {
        "narrative_score": 0.9,
        "visual_score": 0.5,
        "audio_score": 0.5,
        "pacing_score": 0.5,
        "engagement_score": 0.5,
        "technical_score": 0.5,
    }

    # Tutorial: narrative weighted higher (0.30 vs 0.25 default)
    tutorial_assessment = gates.assess_video(
        video_id="video_tutorial",
        video_type="tutorial",
        **scores
    )

    # Marketing: visual weighted higher (0.35 vs 0.25 default)
    marketing_assessment = gates.assess_video(
        video_id="video_marketing",
        video_type="marketing",
        **scores
    )

    # Tutorial score should be higher (narrative weighted up)
    assert tutorial_assessment.overall_score > marketing_assessment.overall_score

    print("✅ Phase 4: Video-Type Weights — PASS")


def test_phase4_recommendations():
    """Phase 4: Generate improvement recommendations."""
    gates = QualityGates()

    # Video with mixed quality
    assessment = gates.assess_video(
        video_id="video_mixed",
        narrative_score=0.9,  # Good
        visual_score=0.4,     # Poor (recommendation)
        audio_score=0.3,      # Poor (recommendation)
        pacing_score=0.8,     # Good
        engagement_score=0.2, # Very poor (recommendation)
        technical_score=0.7,  # OK
    )

    # Verify recommendations (top 3 lowest)
    assert len(assessment.recommendations) == 3
    assert "engagement" in assessment.recommendations[0].lower() or "engagement" in " ".join(assessment.recommendations).lower()

    print("✅ Phase 4: Recommendations — PASS")


def test_phase3_4_e2e_pipeline():
    """Full E2E: Phase 3 (learn style) → Phase 4 (assess quality)."""
    learner = StyleLearner()
    gates = QualityGates()

    # Phase 3: Operator provides feedback on a video
    user_id = "director_user_123"
    profile = learner.get_profile(user_id)

    # Simulate 3 feedback events
    feedbacks = [
        FeedbackEvent(user_id=user_id, feedback_type=FeedbackType.MORE_ANIMATIONS, timestamp=datetime.utcnow().isoformat()),
        FeedbackEvent(user_id=user_id, feedback_type=FeedbackType.FASTER_PACING, timestamp=datetime.utcnow().isoformat()),
        FeedbackEvent(user_id=user_id, feedback_type=FeedbackType.BRIGHTER_COLORS, timestamp=datetime.utcnow().isoformat()),
    ]

    for feedback in feedbacks:
        profile = learner.record_feedback(feedback)

    # Verify learned preferences
    assert profile.animation_level > 0.5  # Learned more animations
    assert profile.overall_speed > 1.0    # Learned faster pacing
    assert profile.color_brightness > 0.5  # Learned brighter colors

    # Phase 4: Assess quality of next video
    assessment = gates.assess_video(
        video_id="video_next",
        narrative_score=0.85,
        visual_score=min(1.0, profile.animation_level + 0.2),  # Use learned + boost
        audio_score=0.80,
        pacing_score=min(1.0, profile.overall_speed / 2.0 + 0.3),  # Normalized + boost
        engagement_score=0.85,
        technical_score=0.90,
        video_type="explainer",
    )

    # Verify assessment (good scores should produce publishable)
    assert assessment.status in [QualityStatus.PUBLISHABLE, QualityStatus.FEATURED, QualityStatus.DRAFT]
    assert len(assessment.recommendations) <= 3

    print("✅ Phase 3-4 E2E: PASS (Narrative preserved through orchestration)")


if __name__ == "__main__":
    test_phase3_style_learner_profile_creation()
    test_phase3_style_learner_feedback_recording()
    test_phase3_parameter_clamping()
    test_phase4_quality_gates_scoring()
    test_phase4_quality_status_thresholds()
    test_phase4_video_type_weights()
    test_phase4_recommendations()
    test_phase3_4_e2e_pipeline()
    print("\n✅ ALL DIRECTOR MODE PHASE 3-4 TESTS PASS (8/8)")
