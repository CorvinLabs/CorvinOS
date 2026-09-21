"""Quality Scoring & Feedback Integration — Phase 3, ADR-0314

Integration with learning loop for video quality optimization.
"""

import logging
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class VideoQualityScore:
    """Immutable quality score (ADR-0314 integration)"""
    video_id: str
    spec_hash: str
    output_file_hash: str
    duration_seconds: int
    fps: int
    resolution: tuple
    file_size_mb: float

    # Quality metrics (0.0 to 1.0)
    technical_quality: float = 0.85  # Codec, bitrate, frame count
    visual_quality: float = 0.75     # Color, text readability, animations
    audio_quality: float = 0.80      # TTS clarity, sync
    overall_score: float = field(default=0.0)

    # Feedback
    user_feedback: Optional[str] = None
    feedback_timestamp: Optional[str] = None

    def calculate_overall(self) -> float:
        """Calculate weighted overall score"""
        return (
            self.technical_quality * 0.4 +
            self.visual_quality * 0.35 +
            self.audio_quality * 0.25
        )

class QualityScorer:
    """Scores video quality for learning loop (ADR-0314)"""

    def __init__(self):
        self.scores = {}

    def score_video(
        self,
        video_file: str,
        spec_hash: str,
        duration_seconds: int,
        fps: int,
        resolution: tuple,
    ) -> VideoQualityScore:
        """Score a generated video

        Args:
            video_file: Path to MP4 file
            spec_hash: SHA256 of spec
            duration_seconds: Target duration
            fps: Frames per second
            resolution: (width, height)

        Returns:
            VideoQualityScore (immutable)
        """
        import os

        video_id = spec_hash[:8]
        file_size_mb = os.path.getsize(video_file) / (1024*1024) if os.path.exists(video_file) else 0.0

        # Calculate output hash
        try:
            with open(video_file, "rb") as f:
                output_hash = hashlib.sha256(f.read()).hexdigest()
        except:
            output_hash = "unknown"

        # Score components
        technical_quality = self._score_technical(duration_seconds, fps, resolution, file_size_mb)
        visual_quality = 0.75  # Fixed for now (would use ML model in future)
        audio_quality = 0.80   # Fixed for now

        score = VideoQualityScore(
            video_id=video_id,
            spec_hash=spec_hash,
            output_file_hash=output_hash,
            duration_seconds=duration_seconds,
            fps=fps,
            resolution=resolution,
            file_size_mb=file_size_mb,
            technical_quality=technical_quality,
            visual_quality=visual_quality,
            audio_quality=audio_quality,
        )

        # Finalize with overall calculation
        object.__setattr__(score, "overall_score", score.calculate_overall())

        self.scores[video_id] = score
        logger.info(f"📊 Video scored: {video_id} → {score.overall_score:.2f}")

        return score

    def _score_technical(self, duration: int, fps: int, resolution: tuple, size_mb: float) -> float:
        """Score technical quality (codec, bitrate, frame count)"""
        score = 0.85  # Baseline: H.264 + AAC

        # Bitrate efficiency (size vs duration)
        bitrate_kbps = (size_mb * 1024 * 8) / duration if duration > 0 else 0
        if bitrate_kbps < 1000:  # Too low
            score -= 0.1
        elif bitrate_kbps > 2000:  # Too high
            score -= 0.05

        # Resolution check
        if resolution == (1920, 1080):
            score += 0.05  # Full HD bonus
        elif resolution[0] < 1280:
            score -= 0.1   # Low res penalty

        # FPS check
        if fps == 30:
            pass  # Standard
        elif fps == 60:
            score += 0.05  # High FPS bonus

        return min(1.0, max(0.0, score))

    def record_feedback(self, video_id: str, feedback: str) -> bool:
        """Record user feedback for learning loop (ADR-0314)"""

        if video_id not in self.scores:
            logger.warning(f"Video {video_id} not found for feedback")
            return False

        score = self.scores[video_id]

        # Create feedback event (immutable, append-only)
        feedback_event = {
            "video_id": video_id,
            "feedback": feedback,
            "timestamp": datetime.utcnow().isoformat(),
            "score_before": score.overall_score,
        }

        logger.info(f"📝 Feedback recorded: {video_id} → {feedback[:50]}...")

        # Emit to learning event store (ADR-0314)
        self._emit_feedback_event(feedback_event)

        return True

    def _emit_feedback_event(self, event: dict):
        """Emit feedback to learning loop (ADR-0314)"""
        # Integration point: emit to event store
        # (actual implementation would write to audit trail)
        logger.debug(f"📊 Feedback event: {event}")

# Singleton
_scorer = QualityScorer()

def score_video(
    video_file: str,
    spec_hash: str,
    duration_seconds: int,
    fps: int,
    resolution: tuple,
) -> VideoQualityScore:
    """Score a video (convenience function)"""
    return _scorer.score_video(video_file, spec_hash, duration_seconds, fps, resolution)

def record_feedback(video_id: str, feedback: str) -> bool:
    """Record user feedback (convenience function)"""
    return _scorer.record_feedback(video_id, feedback)
