"""Quality Gates for Director Mode Phase 4

70/85/90 scoring thresholds with detailed component scoring.
Video-type-specific rules (Tutorial ≠ Marketing).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class QualityStatus(str, Enum):
    """Quality status based on score thresholds."""
    REJECTED = "rejected"          # < 0.70
    DRAFT = "draft"                # 0.70–0.85
    PUBLISHABLE = "publishable"    # 0.85–0.90
    FEATURED = "featured"          # >= 0.90


@dataclass
class QualityAssessment:
    """Single quality assessment result."""
    video_id: str
    timestamp: str  # ISO format
    overall_score: float  # [0.0, 1.0]
    status: QualityStatus

    # Component scores [0.0, 1.0]
    narrative_score: float = 0.5
    visual_score: float = 0.5
    audio_score: float = 0.5
    pacing_score: float = 0.5
    engagement_score: float = 0.5
    technical_score: float = 0.5

    # Recommendations for improvement
    recommendations: List[str] = None

    # Audit trail
    video_type: Optional[str] = None  # "tutorial", "marketing", "explainer"
    assessed_by: str = "QualityGates"

    def __post_init__(self):
        if self.recommendations is None:
            self.recommendations = []

    def to_dict(self) -> Dict:
        """Convert to dict (for audit logging)."""
        return {
            'video_id': self.video_id,
            'timestamp': self.timestamp,
            'overall_score': self.overall_score,
            'status': self.status.value,
            'narrative_score': self.narrative_score,
            'visual_score': self.visual_score,
            'audio_score': self.audio_score,
            'pacing_score': self.pacing_score,
            'engagement_score': self.engagement_score,
            'technical_score': self.technical_score,
            'recommendations': self.recommendations,
            'video_type': self.video_type,
            'assessed_by': self.assessed_by,
        }


class QualityGates:
    """Quality Gates Skill (Phase 4)

    Deterministic scoring with video-type-specific rules.
    """

    # Weights for overall score calculation
    DEFAULT_WEIGHTS = {
        'narrative': 0.25,
        'visual': 0.25,
        'audio': 0.15,
        'pacing': 0.15,
        'engagement': 0.15,
        'technical': 0.05,
    }

    # Video-type-specific weights
    WEIGHTS_BY_TYPE = {
        'tutorial': {
            'narrative': 0.30,  # Clear explanation critical
            'visual': 0.20,
            'audio': 0.15,
            'pacing': 0.10,
            'engagement': 0.10,
            'technical': 0.15,  # Code/screenshots matter
        },
        'marketing': {
            'narrative': 0.15,
            'visual': 0.35,  # Visual impact critical
            'audio': 0.15,
            'pacing': 0.15,
            'engagement': 0.15,  # Emotional engagement matters
            'technical': 0.05,
        },
        'explainer': {
            'narrative': 0.30,
            'visual': 0.25,
            'audio': 0.15,
            'pacing': 0.15,
            'engagement': 0.10,
            'technical': 0.05,
        },
    }

    def __init__(self):
        """Initialize QualityGates."""
        pass

    def assess_video(
        self,
        video_id: str,
        narrative_score: float,
        visual_score: float,
        audio_score: float,
        pacing_score: float,
        engagement_score: float,
        technical_score: float,
        video_type: Optional[str] = None,
    ) -> QualityAssessment:
        """Assess video quality and return assessment.

        All component scores should be in [0.0, 1.0].
        Returns QualityAssessment with overall_score and status.
        """
        # Validate component scores
        for score in [narrative_score, visual_score, audio_score, pacing_score, engagement_score, technical_score]:
            if not (0.0 <= score <= 1.0):
                raise ValueError(f"Score must be in [0.0, 1.0], got {score}")

        # Get weights for video type
        weights = self._get_weights(video_type)

        # Compute overall score
        overall_score = (
            narrative_score * weights['narrative'] +
            visual_score * weights['visual'] +
            audio_score * weights['audio'] +
            pacing_score * weights['pacing'] +
            engagement_score * weights['engagement'] +
            technical_score * weights['technical']
        )

        # Determine status
        status = self._score_to_status(overall_score)

        # Generate recommendations
        recommendations = self._generate_recommendations(
            narrative_score, visual_score, audio_score, pacing_score, engagement_score, technical_score
        )

        assessment = QualityAssessment(
            video_id=video_id,
            timestamp=datetime.utcnow().isoformat(),
            overall_score=overall_score,
            status=status,
            narrative_score=narrative_score,
            visual_score=visual_score,
            audio_score=audio_score,
            pacing_score=pacing_score,
            engagement_score=engagement_score,
            technical_score=technical_score,
            recommendations=recommendations,
            video_type=video_type,
        )

        logger.info(
            f"Quality assessment for {video_id} (type={video_type}): "
            f"overall={overall_score:.2f} ({status.value})"
        )

        return assessment

    def _get_weights(self, video_type: Optional[str]) -> Dict[str, float]:
        """Get weights for video type."""
        if video_type and video_type in self.WEIGHTS_BY_TYPE:
            return self.WEIGHTS_BY_TYPE[video_type]
        return self.DEFAULT_WEIGHTS

    def _score_to_status(self, score: float) -> QualityStatus:
        """Map score to status."""
        if score >= 0.90:
            return QualityStatus.FEATURED
        elif score >= 0.85:
            return QualityStatus.PUBLISHABLE
        elif score >= 0.70:
            return QualityStatus.DRAFT
        else:
            return QualityStatus.REJECTED

    def _generate_recommendations(
        self,
        narrative_score: float,
        visual_score: float,
        audio_score: float,
        pacing_score: float,
        engagement_score: float,
        technical_score: float,
    ) -> List[str]:
        """Generate top 3 recommendations for improvement."""
        # Score each component for recommendation
        component_scores = [
            ('narrative', narrative_score, 'Improve narrative clarity and structure'),
            ('visual', visual_score, 'Enhance visual design and graphics'),
            ('audio', audio_score, 'Improve audio quality and narration'),
            ('pacing', pacing_score, 'Adjust scene pacing and timing'),
            ('engagement', engagement_score, 'Increase viewer engagement'),
            ('technical', technical_score, 'Fix technical issues and artifacts'),
        ]

        # Sort by score (lowest first = most needed improvement)
        component_scores.sort(key=lambda x: x[1])

        # Return top 3 recommendations
        recommendations = [rec for _, _, rec in component_scores[:3]]
        return recommendations
