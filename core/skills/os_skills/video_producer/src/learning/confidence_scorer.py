"""Confidence Scoring Engine for Video Producer Skill 2.0 (Phase 4b).

Computes confidence scores per worker based on feedback history using:
1. Exponential smoothing (recent feedback weighted more)
2. Sample size adjustment (more samples = higher confidence)
3. Variance detection (consistent = more confident)

Per-Worker Confidence:
- slide_quality: How good are rendered slides (0.0-1.0)
- audio_quality: How good is voice narration (0.0-1.0)
- narrative_flow: How well does narration match visuals (0.0-1.0)

Model Confidence (for model selection):
- GPT-4: Quality on 1-min, 5-min, 15-min videos
- Claude-Opus: Quality on each duration
- Claude-Sonnet: Quality on each duration

Exponential smoothing: S_t = alpha * X_t + (1 - alpha) * S_{t-1}
where alpha = 0.3 (recent feedback weighted 30%)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class WorkerType(str, Enum):
    """Types of workers in video producer."""
    SLIDE_RENDERER = "slide_renderer"
    VOICE_SYNTHESIZER = "voice_synthesizer"
    SCREENSHOT_CAPTURER = "screenshot_capturer"
    VIDEO_ASSEMBLER = "video_assembler"


@dataclass(frozen=True)
class ConfidenceMetric:
    """A single confidence measurement for a worker."""
    worker_id: str
    metric_name: str  # "slide_quality", "audio_quality", etc.
    confidence_score: float  # 0.0-1.0
    sample_count: int
    variance: float  # 0.0 (consistent) to 1.0 (inconsistent)
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def __post_init__(self):
        """Validate confidence score."""
        if not 0.0 <= self.confidence_score <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {self.confidence_score}")


@dataclass
class WorkerConfidenceProfile:
    """Complete confidence profile for a worker."""
    worker_id: str
    metrics: dict[str, ConfidenceMetric] = field(default_factory=dict)
    overall_score: float = 0.5
    is_converged: bool = False
    convergence_rate: float = 0.0


class ConfidenceScorer:
    """Computes and tracks confidence scores for workers."""

    # Exponential smoothing factor (recent feedback = 30%)
    ALPHA = 0.3

    # Convergence threshold
    CONVERGENCE_THRESHOLD = 0.85
    MIN_SAMPLES_FOR_CONVERGENCE = 5

    # Variance thresholds for consistency
    VARIANCE_CONSISTENT = 0.1  # Low variance = consistent
    VARIANCE_INCONSISTENT = 0.4  # High variance = inconsistent

    def __init__(
        self,
        workdir: str | Path,
        tenant_id: str = "_default",
    ):
        """Initialize confidence scorer.

        Args:
            workdir: Directory for state storage
            tenant_id: Tenant scope
        """
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.tenant_id = tenant_id
        self.state_file = self.workdir / "confidence_scores.json"
        self.worker_profiles = self._load_profiles()

    def _load_profiles(self) -> dict[str, WorkerConfidenceProfile]:
        """Load worker confidence profiles from disk."""
        profiles = {}
        if not self.state_file.exists():
            # Initialize defaults
            for worker in WorkerType:
                profiles[worker.value] = WorkerConfidenceProfile(worker_id=worker.value)
            return profiles

        try:
            data = json.loads(self.state_file.read_text())
            for worker_id, profile_data in data.items():
                profile = WorkerConfidenceProfile(worker_id=worker_id)
                for metric_name, metric_data in profile_data.get("metrics", {}).items():
                    profile.metrics[metric_name] = ConfidenceMetric(**metric_data)
                profile.overall_score = profile_data.get("overall_score", 0.5)
                profile.is_converged = profile_data.get("is_converged", False)
                profiles[worker_id] = profile
        except Exception as e:
            logger.warning(f"Failed to load confidence profiles: {e}, using defaults")
            for worker in WorkerType:
                profiles[worker.value] = WorkerConfidenceProfile(worker_id=worker.value)

        return profiles

    def _save_profiles(self) -> None:
        """Persist worker profiles to disk."""
        data = {}
        for worker_id, profile in self.worker_profiles.items():
            data[worker_id] = {
                "worker_id": profile.worker_id,
                "metrics": {
                    name: {
                        "worker_id": metric.worker_id,
                        "metric_name": metric.metric_name,
                        "confidence_score": metric.confidence_score,
                        "sample_count": metric.sample_count,
                        "variance": metric.variance,
                        "last_updated": metric.last_updated,
                    }
                    for name, metric in profile.metrics.items()
                },
                "overall_score": profile.overall_score,
                "is_converged": profile.is_converged,
                "convergence_rate": profile.convergence_rate,
            }

        self.state_file.write_text(json.dumps(data, indent=2))

    def update_confidence(
        self,
        worker_id: str,
        metric_name: str,
        new_rating: float,  # 0.0-1.0
    ) -> ConfidenceMetric:
        """Update confidence for a worker metric using exponential smoothing.

        Args:
            worker_id: Worker identifier
            metric_name: Metric name (e.g., "slide_quality")
            new_rating: New rating to incorporate (0.0-1.0)

        Returns:
            Updated ConfidenceMetric
        """
        if worker_id not in self.worker_profiles:
            self.worker_profiles[worker_id] = WorkerConfidenceProfile(worker_id=worker_id)

        profile = self.worker_profiles[worker_id]

        # Get existing metric or create new
        if metric_name in profile.metrics:
            old_metric = profile.metrics[metric_name]
            old_score = old_metric.confidence_score
            old_samples = old_metric.sample_count
            old_variance = old_metric.variance
        else:
            old_score = 0.5  # Start neutral
            old_samples = 0
            old_variance = 0.0

        # Apply exponential smoothing
        new_score = self.ALPHA * new_rating + (1 - self.ALPHA) * old_score
        new_samples = old_samples + 1

        # Update variance (simplified: track if ratings are consistent)
        if old_samples > 0:
            # Variance is distance from smooth average
            deviation = abs(new_rating - new_score)
            new_variance = self.ALPHA * deviation + (1 - self.ALPHA) * old_variance
        else:
            new_variance = 0.0

        # Create updated metric
        metric = ConfidenceMetric(
            worker_id=worker_id,
            metric_name=metric_name,
            confidence_score=new_score,
            sample_count=new_samples,
            variance=new_variance,
        )

        profile.metrics[metric_name] = metric

        # Update overall score (average of all metrics)
        if profile.metrics:
            profile.overall_score = sum(
                m.confidence_score for m in profile.metrics.values()
            ) / len(profile.metrics)

        # Check convergence
        self._update_convergence(profile)

        # Persist
        self._save_profiles()

        logger.info(
            f"Updated confidence for {worker_id}.{metric_name}: "
            f"{new_score:.2f} (samples: {new_samples}, variance: {new_variance:.3f})"
        )

        return metric

    def _update_convergence(self, profile: WorkerConfidenceProfile) -> None:
        """Check if worker has converged."""
        if not profile.metrics:
            profile.is_converged = False
            return

        # Need minimum samples
        min_samples = min(m.sample_count for m in profile.metrics.values())
        if min_samples < self.MIN_SAMPLES_FOR_CONVERGENCE:
            profile.is_converged = False
            return

        # Need high score + low variance
        avg_score = sum(m.confidence_score for m in profile.metrics.values()) / len(
            profile.metrics
        )
        max_variance = max(m.variance for m in profile.metrics.values())

        profile.is_converged = (
            avg_score >= self.CONVERGENCE_THRESHOLD
            and max_variance <= self.VARIANCE_CONSISTENT
        )

        # Compute convergence rate (0.0 = diverging, 1.0 = fully converged)
        profile.convergence_rate = min(1.0, (avg_score - 0.5) * 2)  # Normalize to 0-1

    def get_worker_confidence(self, worker_id: str) -> dict[str, Any]:
        """Get all confidence metrics for a worker."""
        if worker_id not in self.worker_profiles:
            return {}

        profile = self.worker_profiles[worker_id]
        return {
            "worker_id": worker_id,
            "overall_score": profile.overall_score,
            "is_converged": profile.is_converged,
            "convergence_rate": profile.convergence_rate,
            "metrics": {
                name: {
                    "confidence": metric.confidence_score,
                    "samples": metric.sample_count,
                    "variance": metric.variance,
                    "last_updated": metric.last_updated,
                }
                for name, metric in profile.metrics.items()
            },
        }

    def get_all_confidence_metrics(self) -> dict[str, Any]:
        """Get all confidence metrics for dashboard."""
        return {
            worker_id: self.get_worker_confidence(worker_id)
            for worker_id in self.worker_profiles.keys()
        }
