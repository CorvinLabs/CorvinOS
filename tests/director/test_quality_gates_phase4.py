"""Phase 4: Quality Gates Tests

Tests for:
- Quality assessment and gating
- Detailed quality scoring
- Improvement recommendations
"""

import pytest
from core.skills.os_skills.video_producer.src.director.quality.quality_gates import (
    QualityGates, QualityStatus, QualityAssessment
)


class TestQualityGates:
    """Test quality enforcement gates"""

    def test_quality_gate_reject_below_70(self):
        """Videos below 70% are rejected"""
        gates = QualityGates()
        assessment = gates.validate_quality({}, 0.65)

        assert assessment.status == QualityStatus.REJECTED
        assert assessment.action == "requires_remake"

    def test_quality_gate_draft_at_85(self):
        """Videos at 85% are publishable but marked as draft"""
        gates = QualityGates()
        assessment = gates.validate_quality({}, 0.85)

        assert assessment.status == QualityStatus.PUBLISHABLE
        assert assessment.action == "publish_as_is"

    def test_quality_gate_publishable_at_85_to_90(self):
        """Videos between 85-90% are publishable"""
        gates = QualityGates()
        assessment = gates.validate_quality({}, 0.87)

        assert assessment.status == QualityStatus.PUBLISHABLE

    def test_quality_gate_featured_at_90(self):
        """Videos at 90% and above are featured"""
        gates = QualityGates()
        assessment = gates.validate_quality({}, 0.92)

        assert assessment.status == QualityStatus.FEATURED
        assert assessment.action == "feature_prominently"

    def test_quality_assessment_has_detailed_scores(self):
        """Assessment includes detailed scores by category"""
        gates = QualityGates()
        video = {
            "has_narrative_structure": True,
            "has_professional_editing": True,
            "clear_audio": True,
            "duration_seconds": 300,
            "has_emotional_arc": True
        }
        assessment = gates.validate_quality(video, 0.85)

        assert "detailed_scores" in assessment.__dict__
        assert len(assessment.detailed_scores) > 0
        assert "narrative_flow" in assessment.detailed_scores


class TestDetailedScoring:
    """Test detailed quality scoring by category"""

    def test_narrative_flow_scoring(self):
        """Score narrative flow quality"""
        gates = QualityGates()
        video_good = {
            "has_narrative_structure": True,
            "has_smooth_transitions": True,
            "has_clear_message": True
        }
        assessment = gates.validate_quality(video_good, 0.8)
        assert assessment.detailed_scores["narrative_flow"] > 0.7

    def test_visual_quality_scoring(self):
        """Score visual quality"""
        gates = QualityGates()
        video_hd = {
            "resolution": "1080p",
            "has_professional_editing": True,
            "has_color_grading": True
        }
        assessment = gates.validate_quality(video_hd, 0.8)
        assert assessment.detailed_scores["visual_quality"] > 0.7

    def test_audio_quality_scoring(self):
        """Score audio quality"""
        gates = QualityGates()
        video_audio = {
            "clear_audio": True,
            "balanced_audio_mix": True,
            "appropriate_music": True
        }
        assessment = gates.validate_quality(video_audio, 0.8)
        assert assessment.detailed_scores["audio_quality"] > 0.7

    def test_pacing_scoring(self):
        """Score pacing and timing"""
        gates = QualityGates()
        video_pacing = {
            "duration_seconds": 180,  # 3 minutes - good length
            "varies_pacing": True,
            "smooth_transitions": True
        }
        assessment = gates.validate_quality(video_pacing, 0.8)
        assert assessment.detailed_scores["pacing"] > 0.5

    def test_engagement_scoring(self):
        """Score viewer engagement potential"""
        gates = QualityGates()
        video_engaging = {
            "has_attention_hook": True,
            "has_emotional_arc": True,
            "has_call_to_action": True
        }
        assessment = gates.validate_quality(video_engaging, 0.8)
        assert assessment.detailed_scores["engagement"] > 0.7

    def test_technical_excellence_scoring(self):
        """Score technical specifications"""
        gates = QualityGates()
        video_tech = {
            "frame_rate": 30,
            "codec": "h264",
            "has_complete_metadata": True
        }
        assessment = gates.validate_quality(video_tech, 0.8)
        assert assessment.detailed_scores["technical_excellence"] > 0.5


class TestRecommendations:
    """Test improvement recommendations"""

    def test_recommendations_for_low_quality(self):
        """Get recommendations for videos needing improvement"""
        gates = QualityGates()
        assessment = gates.validate_quality(
            {
                "has_narrative_structure": False,
                "clear_audio": False,
                "has_attention_hook": False
            },
            0.60
        )

        recommendations = gates.get_recommendations(assessment)
        assert len(recommendations) > 0
        # Should recommend improvements in low-scoring areas

    def test_recommendations_target_low_scores(self):
        """Recommendations focus on lowest-scoring categories"""
        gates = QualityGates()
        video_bad_audio = {
            "has_narrative_structure": True,
            "clear_audio": False,
            "balanced_audio_mix": False
        }
        assessment = gates.validate_quality(video_bad_audio, 0.70)
        recommendations = gates.get_recommendations(assessment)

        # Should recommend audio improvements
        assert any("audio" in r.lower() for r in recommendations)

    def test_top_3_recommendations(self):
        """Get top 3 highest-priority recommendations"""
        gates = QualityGates()
        assessment = gates.validate_quality(
            {
                "has_narrative_structure": False,
                "clear_audio": False,
                "has_attention_hook": False,
                "resolution": "480p"
            },
            0.50
        )

        recommendations = gates.get_recommendations(assessment)
        assert len(recommendations) <= 3  # Should be limited to 3


class TestAssessmentHistory:
    """Test assessment tracking and history"""

    def test_assessment_history_tracking(self):
        """Assessments are tracked"""
        gates = QualityGates()
        gates.validate_quality({}, 0.75)
        gates.validate_quality({}, 0.85)
        gates.validate_quality({}, 0.95)

        assert len(gates.assessment_history) == 3

    def test_assessment_summary(self):
        """Get assessment summary"""
        gates = QualityGates()
        for score in [0.65, 0.75, 0.85, 0.92]:
            gates.validate_quality({}, score)

        summary = gates.get_assessment_history_summary()
        assert summary["total_assessments"] == 4
        assert summary["average_score"] > 0.7
        assert "status_distribution" in summary

    def test_status_distribution_in_summary(self):
        """Summary shows distribution of statuses"""
        gates = QualityGates()
        gates.validate_quality({}, 0.65)  # REJECTED
        gates.validate_quality({}, 0.87)  # PUBLISHABLE
        gates.validate_quality({}, 0.92)  # FEATURED

        summary = gates.get_assessment_history_summary()
        assert "rejected" in summary["status_distribution"]
        assert "publishable" in summary["status_distribution"]
        assert "featured" in summary["status_distribution"]


class TestQualityWeights:
    """Test quality scoring weights"""

    def test_quality_weights_sum_to_one(self):
        """Quality criteria weights sum to 1.0"""
        gates = QualityGates()
        total_weight = sum(gates.QUALITY_WEIGHTS.values())
        assert abs(total_weight - 1.0) < 0.001

    def test_narrative_flow_weighted_heavily(self):
        """Narrative flow is a primary criteria"""
        gates = QualityGates()
        assert gates.QUALITY_WEIGHTS["narrative_flow"] == 0.25
        assert gates.QUALITY_WEIGHTS["visual_quality"] == 0.25

    def test_technical_weighted_low(self):
        """Technical excellence is secondary"""
        gates = QualityGates()
        assert gates.QUALITY_WEIGHTS["technical_excellence"] == 0.05


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
