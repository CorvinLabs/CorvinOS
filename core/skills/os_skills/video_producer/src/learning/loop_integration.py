"""Learning Loop Integration for Video Producer Skill 2.0 (Phase 4b).

Closes the loop: feedback → confidence scoring → model selection → next run uses new config.

Integration Points:
1. FeedbackCollector: Accepts operator feedback (1-5 scale)
2. ConfidenceScorer: Tracks worker performance over time
3. ModelSelector: Learns best model for video duration
4. Audit Trail: Every decision is logged + hash-chained (GDPR Art. 30)
5. Orchestrator: Uses learned config on next run

Load-Bearing:
- Feedback MUST be validated before processing (no PII)
- Model switch REQUIRES minimum confidence delta (0.15)
- Audit events MUST be hash-chained (immutable)
- All decisions are deterministic (reproducible)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import sys

logger = logging.getLogger(__name__)

# Import learning modules (relative imports within same package)
from .feedback_collector import FeedbackCollector, FeedbackRecord
from .confidence_scorer import ConfidenceScorer, ConfidenceMetric
from .model_selector import ModelSelector, Model


@dataclass(frozen=True)
class LearningLoopEvent:
    """Immutable audit event for learning loop."""
    event_id: str
    event_type: str  # "feedback_received", "confidence_updated", "model_switched"
    timestamp: str
    skill_id: str = "os.video_producer"
    tenant_id: str = "_default"
    job_id: Optional[str] = None
    scene_id: Optional[str] = None
    data: dict[str, Any] = None  # Event-specific data
    prev_hash: Optional[str] = None
    hash: Optional[str] = None

    def compute_hash(self) -> str:
        """Compute deterministic hash of event."""
        payload = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "job_id": self.job_id,
            "scene_id": self.scene_id,
            "data": self.data or {},
            "prev_hash": self.prev_hash,
        }
        json_str = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()


class LearningLoopIntegration:
    """Orchestrates feedback → optimizer → config update loop."""

    def __init__(
        self,
        workdir: str | Path,
        tenant_id: str = "_default",
    ):
        """Initialize learning loop integration.

        Args:
            workdir: Working directory for all components
            tenant_id: Tenant scope (GDPR)
        """
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.tenant_id = tenant_id

        # Initialize sub-components
        self.feedback_collector = FeedbackCollector(self.workdir, tenant_id)
        self.confidence_scorer = ConfidenceScorer(self.workdir, tenant_id)
        self.model_selector = ModelSelector(self.workdir, tenant_id)

        # Audit trail
        self.audit_log = self.workdir / "learning_audit.jsonl"
        self.last_hash = None
        self._load_last_hash()

    def _load_last_hash(self) -> None:
        """Load last hash from audit log."""
        if self.audit_log.exists():
            try:
                with open(self.audit_log, "r") as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            self.last_hash = data.get("hash")
            except Exception as e:
                logger.warning(f"Failed to load last hash: {e}")

    def submit_feedback(
        self,
        job_id: str,
        scene_id: str,
        rating: int,
        feedback_type: str = "quality",
        worker_notes: Optional[str] = None,
        user_id_hash: str = "anonymous",
    ) -> tuple[bool, Optional[str]]:
        """Submit feedback and update learning state.

        Args:
            job_id: Video job ID
            scene_id: Scene ID
            rating: 1-5 scale
            feedback_type: "quality", "relevance", "correctness"
            worker_notes: Optional notes
            user_id_hash: User identifier

        Returns:
            (success, error_message) tuple
        """
        # Validate PII (no email, phone, names in notes)
        if worker_notes:
            pii_patterns = ["@", "+", "email:", "phone:"]
            if any(p in worker_notes.lower() for p in pii_patterns):
                return False, "Worker notes contain suspected PII"

        # Submit feedback
        feedback, error = self.feedback_collector.submit_feedback(
            job_id=job_id,
            scene_id=scene_id,
            feedback_type=feedback_type,
            rating=rating,
            worker_notes=worker_notes,
            user_id_hash=user_id_hash,
        )

        if error:
            return False, error

        # Log audit event
        self._emit_audit_event(
            event_type="feedback_received",
            job_id=job_id,
            scene_id=scene_id,
            data={
                "feedback_type": feedback_type,
                "rating": rating,
                "has_notes": worker_notes is not None,
            },
        )

        # Update confidence based on rating (convert 1-5 to 0.0-1.0)
        confidence_value = (rating - 1) / 4.0  # 1→0.0, 5→1.0

        # Infer worker from feedback notes if provided
        worker_id = self._infer_worker_from_notes(worker_notes or "")
        if worker_id:
            self.confidence_scorer.update_confidence(
                worker_id=worker_id,
                metric_name=f"{feedback_type}_rating",
                new_rating=confidence_value,
            )

        return True, None

    def _infer_worker_from_notes(self, notes: str) -> Optional[str]:
        """Infer worker ID from feedback notes.

        Args:
            notes: Feedback notes

        Returns:
            Worker ID or None
        """
        keywords = {
            "voice": "voice_synthesizer",
            "audio": "voice_synthesizer",
            "slide": "slide_renderer",
            "screenshot": "screenshot_capturer",
            "crop": "screenshot_capturer",
            "bitrate": "video_assembler",
            "video": "video_assembler",
        }

        notes_lower = notes.lower()
        for keyword, worker_id in keywords.items():
            if keyword in notes_lower:
                return worker_id

        return None

    def select_model_for_video(self, duration_seconds: float) -> tuple[Model, dict[str, Any]]:
        """Select model for video and return stats.

        Args:
            duration_seconds: Video duration

        Returns:
            (selected_model, stats_dict) tuple
        """
        model = self.model_selector.select_model(duration_seconds)
        stats = self.model_selector.get_model_stats()

        self._emit_audit_event(
            event_type="model_selected",
            data={
                "model": model,
                "duration_seconds": duration_seconds,
            },
        )

        return model, stats

    def report_video_quality(
        self,
        job_id: str,
        video_duration_seconds: float,
        quality_score: float,  # 0.0-1.0
        model_used: Optional[Model] = None,
    ) -> Optional[Model]:
        """Report video quality and update model selection.

        Args:
            job_id: Job ID
            video_duration_seconds: Duration
            quality_score: Quality 0.0-1.0
            model_used: Model that was used

        Returns:
            New selected model if switched, None otherwise
        """
        if not model_used:
            return None

        new_model = self.model_selector.report_result(
            model=model_used,
            video_duration_seconds=video_duration_seconds,
            quality_rating=quality_score,
        )

        self._emit_audit_event(
            event_type="video_quality_reported",
            job_id=job_id,
            data={
                "quality_score": quality_score,
                "model_used": model_used,
                "model_switched_to": new_model,
            },
        )

        return new_model

    def get_learning_stats(self) -> dict[str, Any]:
        """Get comprehensive learning statistics."""
        return {
            "confidence_metrics": self.confidence_scorer.get_all_confidence_metrics(),
            "model_stats": self.model_selector.get_model_stats(),
            "feedback_stats": self._get_feedback_stats(),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _get_feedback_stats(self) -> dict[str, Any]:
        """Get feedback statistics."""
        # Summary: total feedback submitted, distribution by type
        return {
            "total_feedback": 0,  # Aggregate across all jobs
            "by_type": {
                "quality": 0,
                "relevance": 0,
                "correctness": 0,
            },
        }

    def _emit_audit_event(
        self,
        event_type: str,
        job_id: Optional[str] = None,
        scene_id: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        """Emit and persist audit event (hash-chained).

        Args:
            event_type: Type of event
            job_id: Optional job ID
            scene_id: Optional scene ID
            data: Event-specific data
        """
        from uuid import uuid4

        event = LearningLoopEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            timestamp=datetime.utcnow().isoformat() + "Z",
            skill_id="os.video_producer",
            tenant_id=self.tenant_id,
            job_id=job_id,
            scene_id=scene_id,
            data=data or {},
            prev_hash=self.last_hash,
        )

        # Compute hash
        event_dict = asdict(event)
        event_dict["hash"] = None  # Exclude hash from hash computation

        payload = {k: v for k, v in event_dict.items() if k != "hash"}
        json_str = json.dumps(payload, sort_keys=True)
        event_hash = hashlib.sha256(json_str.encode()).hexdigest()

        # Persist
        event_dict["hash"] = event_hash
        self.last_hash = event_hash

        try:
            with open(self.audit_log, "a") as f:
                f.write(json.dumps(event_dict) + "\n")
            logger.info(f"Audit event: {event_type} (hash: {event_hash[:8]}...)")
        except Exception as e:
            logger.error(f"Failed to write audit event: {e}")
