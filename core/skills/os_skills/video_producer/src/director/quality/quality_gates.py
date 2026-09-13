"""Quality enforcement gates for video production.

Enforces professional baseline standards to prevent low-quality content
from being published or featured.
"""

from typing import Dict, Any, List
from dataclasses import dataclass
from enum import Enum


class QualityStatus(Enum):
    """Quality assessment status"""
    REJECTED = "rejected"
    DRAFT = "draft"
    PUBLISHABLE = "publishable"
    FEATURED = "featured"


@dataclass
class QualityAssessment:
    """Result of quality assessment"""
    status: QualityStatus
    score: float
    reason: str
    action: str
    detailed_scores: Dict[str, float]


class QualityGates:
    """Enforce professional baseline standards"""

    # Quality thresholds
    MINIMUM_QUALITY = {
        "0.70": {"publish": False, "reason": "draft_quality", "status": QualityStatus.REJECTED},
        "0.85": {"publish": True, "review_required": False, "status": QualityStatus.DRAFT},
        "0.90": {"publish": True, "review_required": False, "status": QualityStatus.PUBLISHABLE, "featured": True},
        "1.00": {"publish": True, "status": QualityStatus.FEATURED}
    }

    # Quality criteria weights
    QUALITY_WEIGHTS = {
        "narrative_flow": 0.25,
        "visual_quality": 0.25,
        "audio_quality": 0.15,
        "pacing": 0.15,
        "engagement": 0.15,
        "technical_excellence": 0.05
    }

    def __init__(self):
        self.assessment_history: List[QualityAssessment] = []

    def validate_quality(self, video: Dict[str, Any],
                        score: float) -> QualityAssessment:
        """Validate video meets quality gates.

        Args:
            video: Video metadata/content
            score: Overall quality score (0.0 to 1.0)

        Returns:
            QualityAssessment with status and recommendation
        """
        # Compute detailed scores
        detailed_scores = self._compute_detailed_scores(video)

        # Determine status based on score
        if score < 0.70:
            assessment = QualityAssessment(
                status=QualityStatus.REJECTED,
                score=score,
                reason="below_minimum_quality",
                action="requires_remake",
                detailed_scores=detailed_scores
            )
        elif score < 0.85:
            assessment = QualityAssessment(
                status=QualityStatus.DRAFT,
                score=score,
                reason="below_publication_threshold",
                action="needs_improvement",
                detailed_scores=detailed_scores
            )
        elif score < 0.90:
            assessment = QualityAssessment(
                status=QualityStatus.PUBLISHABLE,
                score=score,
                reason="meets_minimum_publication",
                action="publish_as_is",
                detailed_scores=detailed_scores
            )
        else:
            assessment = QualityAssessment(
                status=QualityStatus.FEATURED,
                score=score,
                reason="exceeds_quality_threshold",
                action="feature_prominently",
                detailed_scores=detailed_scores
            )

        self.assessment_history.append(assessment)
        return assessment

    def _compute_detailed_scores(self, video: Dict[str, Any]) -> Dict[str, float]:
        """Compute detailed quality scores by category.

        Args:
            video: Video metadata

        Returns:
            Dictionary of category scores
        """
        scores = {}

        # Narrative flow (structure, coherence, engagement)
        scores["narrative_flow"] = self._score_narrative_flow(video)

        # Visual quality (composition, editing, effects)
        scores["visual_quality"] = self._score_visual_quality(video)

        # Audio quality (clarity, mixing, music)
        scores["audio_quality"] = self._score_audio_quality(video)

        # Pacing (timing, transitions, rhythm)
        scores["pacing"] = self._score_pacing(video)

        # Engagement (hooks, emotional arc, calls to action)
        scores["engagement"] = self._score_engagement(video)

        # Technical excellence (resolution, codec, metadata)
        scores["technical_excellence"] = self._score_technical_excellence(video)

        return scores

    def _score_narrative_flow(self, video: Dict[str, Any]) -> float:
        """Score narrative flow and structure"""
        score = 0.5

        # Check for narrative structure
        if video.get("has_narrative_structure", False):
            score += 0.2

        # Check for coherent transitions
        if video.get("has_smooth_transitions", False):
            score += 0.15

        # Check for clear message
        if video.get("has_clear_message", False):
            score += 0.15

        return min(score, 1.0)

    def _score_visual_quality(self, video: Dict[str, Any]) -> float:
        """Score visual composition and quality"""
        score = 0.5

        # Check resolution
        resolution = video.get("resolution", "")
        if resolution in ["4K", "1080p"]:
            score += 0.2
        elif resolution == "720p":
            score += 0.1

        # Check for professional editing
        if video.get("has_professional_editing", False):
            score += 0.2

        # Check color grading
        if video.get("has_color_grading", False):
            score += 0.1

        return min(score, 1.0)

    def _score_audio_quality(self, video: Dict[str, Any]) -> float:
        """Score audio clarity and mixing"""
        score = 0.5

        # Check audio clarity
        if video.get("clear_audio", False):
            score += 0.25

        # Check audio mixing
        if video.get("balanced_audio_mix", False):
            score += 0.15

        # Check for background music
        if video.get("appropriate_music", False):
            score += 0.1

        return min(score, 1.0)

    def _score_pacing(self, video: Dict[str, Any]) -> float:
        """Score pacing and timing"""
        score = 0.5

        # Check duration appropriateness
        duration = video.get("duration_seconds", 0)
        if 60 <= duration <= 600:  # 1-10 minutes
            score += 0.2

        # Check for varied pacing
        if video.get("varies_pacing", False):
            score += 0.15

        # Check transition smoothness
        if video.get("smooth_transitions", False):
            score += 0.15

        return min(score, 1.0)

    def _score_engagement(self, video: Dict[str, Any]) -> float:
        """Score viewer engagement potential"""
        score = 0.5

        # Check for hook
        if video.get("has_attention_hook", False):
            score += 0.2

        # Check for emotional arc
        if video.get("has_emotional_arc", False):
            score += 0.15

        # Check for call to action
        if video.get("has_call_to_action", False):
            score += 0.15

        return min(score, 1.0)

    def _score_technical_excellence(self, video: Dict[str, Any]) -> float:
        """Score technical specifications"""
        score = 0.5

        # Check frame rate
        frame_rate = video.get("frame_rate", 0)
        if frame_rate >= 24:
            score += 0.2

        # Check codec quality
        if video.get("codec", "") in ["h264", "h265"]:
            score += 0.2

        # Check metadata completeness
        if video.get("has_complete_metadata", False):
            score += 0.1

        return min(score, 1.0)

    def get_recommendations(self, assessment: QualityAssessment) -> List[str]:
        """Get specific recommendations to improve quality.

        Args:
            assessment: Quality assessment result

        Returns:
            List of specific recommendations
        """
        recommendations = []

        # Find lowest scoring categories
        low_scores = [
            (category, score) for category, score in assessment.detailed_scores.items()
            if score < 0.6
        ]

        low_scores.sort(key=lambda x: x[1])

        for category, score in low_scores[:3]:  # Top 3 improvements needed
            if category == "narrative_flow":
                recommendations.append("Improve narrative structure and transitions")
            elif category == "visual_quality":
                recommendations.append("Enhance visual composition and color grading")
            elif category == "audio_quality":
                recommendations.append("Improve audio clarity and balance")
            elif category == "pacing":
                recommendations.append("Adjust pacing for better engagement")
            elif category == "engagement":
                recommendations.append("Add stronger hooks and emotional arc")
            elif category == "technical_excellence":
                recommendations.append("Upgrade to higher resolution or better codec")

        return recommendations

    def get_assessment_history_summary(self) -> Dict[str, Any]:
        """Get summary of all assessments"""
        if not self.assessment_history:
            return {
                "total_assessments": 0,
                "average_score": 0.0,
                "status_distribution": {}
            }

        status_counts = {}
        total_score = 0

        for assessment in self.assessment_history:
            status = assessment.status.value
            status_counts[status] = status_counts.get(status, 0) + 1
            total_score += assessment.score

        return {
            "total_assessments": len(self.assessment_history),
            "average_score": total_score / len(self.assessment_history),
            "status_distribution": status_counts,
            "pass_rate": status_counts.get("publishable", 0) + status_counts.get("featured", 0) / len(self.assessment_history) if self.assessment_history else 0
        }
