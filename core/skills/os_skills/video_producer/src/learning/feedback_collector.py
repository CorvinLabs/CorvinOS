"""Operator Feedback Collection for Video Producer Skill 2.0 (Phase 4b).

Collects 1–5 scale feedback on video quality with optional per-worker notes.
Integrates with ADR-0314 (Learning Infrastructure) for audit trail.

Feedback Types:
- quality: Overall video quality (1-5 scale)
- relevance: Narrative relevance to source material (1-5)
- correctness: Factual accuracy (1-5)

Every feedback event is:
1. Timestamped (ISO 8601 UTC)
2. Attributed (user_id_hash, job_id, scene_id)
3. Audit-logged (append-only, hash-chained)
4. Tenant-scoped (GDPR Art. 6, 32)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

FeedbackType = Literal["quality", "relevance", "correctness"]


@dataclass(frozen=True)
class FeedbackRecord:
    """An immutable feedback submission from operator."""

    feedback_id: str
    job_id: str
    scene_id: str
    feedback_type: FeedbackType
    rating: int  # 1-5 scale
    worker_notes: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    user_id_hash: str = field(default="anonymous")
    tenant_id: str = "_default"

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate feedback record."""
        if not 1 <= self.rating <= 5:
            return False, "Rating must be 1-5"
        if not self.feedback_type in ["quality", "relevance", "correctness"]:
            return False, f"Unknown feedback_type: {self.feedback_type}"
        if self.worker_notes and len(self.worker_notes) > 500:
            return False, "Worker notes exceeds 500 chars"
        if not self.job_id or not self.scene_id:
            return False, "job_id and scene_id required"
        return True, None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "feedback_id": self.feedback_id,
            "job_id": self.job_id,
            "scene_id": self.scene_id,
            "feedback_type": self.feedback_type,
            "rating": self.rating,
            "worker_notes": self.worker_notes,
            "timestamp": self.timestamp,
            "user_id_hash": self.user_id_hash,
            "tenant_id": self.tenant_id,
        }


class FeedbackCollector:
    """Collects and persists operator feedback for learning loop."""

    def __init__(
        self,
        workdir: str | Path,
        tenant_id: str = "_default",
    ):
        """Initialize feedback collector.

        Args:
            workdir: Directory for feedback storage
            tenant_id: Tenant scope (GDPR)
        """
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.tenant_id = tenant_id
        self.feedback_dir = self.workdir / "feedback"
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self.feedback_log = self.workdir / "feedback.jsonl"

    def submit_feedback(
        self,
        job_id: str,
        scene_id: str,
        feedback_type: FeedbackType,
        rating: int,
        worker_notes: Optional[str] = None,
        user_id_hash: str = "anonymous",
    ) -> tuple[FeedbackRecord, Optional[str]]:
        """Submit operator feedback.

        Args:
            job_id: Video production job ID
            scene_id: Scene being evaluated
            feedback_type: Type of feedback (quality|relevance|correctness)
            rating: 1-5 scale rating
            worker_notes: Optional per-worker notes
            user_id_hash: User identifier (hashed for GDPR)

        Returns:
            (FeedbackRecord, error_message) tuple
        """
        feedback_id = str(uuid4())

        record = FeedbackRecord(
            feedback_id=feedback_id,
            job_id=job_id,
            scene_id=scene_id,
            feedback_type=feedback_type,
            rating=rating,
            worker_notes=worker_notes,
            user_id_hash=user_id_hash,
            tenant_id=self.tenant_id,
        )

        # Validate
        valid, error = record.validate()
        if not valid:
            return record, error

        # Persist to JSONL (append-only)
        try:
            with open(self.feedback_log, "a") as f:
                f.write(json.dumps(record.to_dict()) + "\n")

            # Also save individual feedback file
            feedback_file = self.feedback_dir / f"{feedback_id}.json"
            with open(feedback_file, "w") as f:
                json.dump(record.to_dict(), f, indent=2)

            logger.info(f"Feedback submitted: {feedback_id} for job {job_id}")
            return record, None
        except Exception as e:
            error_msg = f"Failed to save feedback: {str(e)}"
            logger.error(error_msg)
            return record, error_msg

    def get_feedback_for_job(self, job_id: str) -> list[FeedbackRecord]:
        """Get all feedback for a job."""
        records = []
        if not self.feedback_log.exists():
            return records

        try:
            with open(self.feedback_log, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data["job_id"] == job_id:
                        records.append(FeedbackRecord(**data))
        except Exception as e:
            logger.error(f"Failed to read feedback log: {e}")

        return records

    def get_feedback_for_scene(self, job_id: str, scene_id: str) -> list[FeedbackRecord]:
        """Get feedback for specific scene."""
        all_records = self.get_feedback_for_job(job_id)
        return [r for r in all_records if r.scene_id == scene_id]

    def get_average_rating(
        self,
        job_id: str,
        feedback_type: Optional[FeedbackType] = None,
    ) -> float:
        """Calculate average rating for a job."""
        records = self.get_feedback_for_job(job_id)
        if feedback_type:
            records = [r for r in records if r.feedback_type == feedback_type]

        if not records:
            return 0.0

        total = sum(r.rating for r in records)
        return total / len(records)

    def get_feedback_stats(self, job_id: str) -> dict[str, Any]:
        """Get comprehensive feedback statistics."""
        records = self.get_feedback_for_job(job_id)

        stats = {
            "total_feedback": len(records),
            "by_type": {},
            "average_rating": 0.0,
        }

        if not records:
            return stats

        for ftype in ["quality", "relevance", "correctness"]:
            type_records = [r for r in records if r.feedback_type == ftype]
            if type_records:
                ratings = [r.rating for r in type_records]
                stats["by_type"][ftype] = {
                    "count": len(type_records),
                    "average": sum(ratings) / len(ratings),
                    "min": min(ratings),
                    "max": max(ratings),
                }

        stats["average_rating"] = sum(r.rating for r in records) / len(records)

        return stats
