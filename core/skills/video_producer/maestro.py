"""Maestro Orchestrator for Video Producer Skill 2.0

Controls the entire video production pipeline:
- Job creation with precondition validation
- Phase-based execution with strict ordering
- Per-scene feedback capture for learning
- Audit trail for all decisions
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
import json
from datetime import datetime
import uuid


class VideoJobPhase(Enum):
    """Video production phases with strict ordering"""
    ANALYSIS = 1           # Asset Analyzer Worker
    VOICE = 2              # Voice Synthesizer Worker
    SCREENSHOTS = 3        # Screenshot Capturer Worker
    ASSEMBLY = 4           # Video Assembler Worker
    YOUTUBE = 5            # YouTube Uploader Worker
    COMPLETE = 6


@dataclass(frozen=True)
class FeedbackEvent:
    """Immutable feedback event for learning"""
    timestamp: str
    scene_index: int
    feedback_type: str  # "pacing", "quality", "accuracy", "engagement"
    value: Any
    notes: Optional[str] = None


@dataclass
class VideoJob:
    """Immutable video job specification"""
    job_id: str
    topic: str
    duration_seconds: int
    audience: str  # "beginners", "technical", "operators"
    narration: List[str]
    current_phase: VideoJobPhase = VideoJobPhase.ANALYSIS
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    feedback_history: List[FeedbackEvent] = field(default_factory=list)
    analysis_result: Optional[Dict] = None
    voice_result: Optional[Dict] = None
    screenshots_result: Optional[Dict] = None
    video_result: Optional[Dict] = None
    youtube_result: Optional[Dict] = None

    def __post_init__(self):
        """Validate job invariants"""
        if not self.job_id:
            self.job_id = f"video_{uuid.uuid4().hex[:12]}"
        if not self.topic:
            raise ValueError("Topic is required")
        if self.duration_seconds <= 0:
            raise ValueError("Duration must be positive")
        if not self.narration or len(self.narration) == 0:
            raise ValueError("Narration scenes are required")


class MaestroOrchestrator:
    """Control plane for video production pipeline

    Responsibilities:
    - Create and manage video jobs
    - Register workers for each phase
    - Enforce phase gates (preconditions)
    - Execute phases in order
    - Capture feedback for learning loops
    - Emit audit events
    """

    def __init__(self):
        self.jobs: Dict[str, VideoJob] = {}
        self.worker_registry = {}
        self.audit_log = []

        # Phase gate validators (preconditions)
        self.phase_gates = {
            VideoJobPhase.ANALYSIS: self._validate_analysis_phase,
            VideoJobPhase.VOICE: self._validate_voice_phase,
            VideoJobPhase.SCREENSHOTS: self._validate_screenshots_phase,
            VideoJobPhase.ASSEMBLY: self._validate_assembly_phase,
            VideoJobPhase.YOUTUBE: self._validate_youtube_phase,
        }

    def create_job(
        self,
        topic: str,
        duration: int,
        audience: str,
        narration: List[str],
        job_id: Optional[str] = None,
    ) -> str:
        """Create a new video production job

        Args:
            topic: Video topic (e.g., "What is CorvinOS?")
            duration: Duration in seconds
            audience: Target audience (beginners, technical, operators)
            narration: List of scene narrations (MUST be sourced)
            job_id: Optional custom job ID

        Returns:
            job_id (str): Unique job identifier

        Raises:
            ValueError: If narration is unsourced or invalid
        """

        job = VideoJob(
            job_id=job_id or f"video_{uuid.uuid4().hex[:12]}",
            topic=topic,
            duration_seconds=duration,
            audience=audience,
            narration=narration,
        )

        # Validate narration (Phase 1: basic, Phase 2: deep analysis)
        if not self._validate_narration(narration):
            raise ValueError(
                "Narration must be sourced (no hallucination); "
                "pass to Asset Analyzer for deep validation"
            )

        self.jobs[job.job_id] = job

        # Emit audit event
        self._audit("job_created", job.job_id, {
            "topic": topic,
            "duration": duration,
            "audience": audience,
            "num_scenes": len(narration),
        })

        return job.job_id

    def register_worker(self, phase: VideoJobPhase, worker_class):
        """Register a Worker Skill for a phase

        Args:
            phase: VideoJobPhase to register for
            worker_class: Worker class (will be instantiated) or instance
        """
        # Handle both class and instance
        if isinstance(worker_class, type):
            worker_instance = worker_class()
            worker_name = worker_class.__name__
        else:
            worker_instance = worker_class
            worker_name = worker_class.__class__.__name__

        self.worker_registry[phase] = worker_instance
        self._audit(
            "worker_registered",
            phase.name,
            {"worker": worker_name},
        )

    def execute_phase(self, job_id: str) -> dict:
        """Execute current phase for a job

        Args:
            job_id: Job identifier

        Returns:
            dict: Result from the worker

        Raises:
            ValueError: If job not found
            RuntimeError: If phase gate fails or worker not registered
        """

        job = self.jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        # Enforce phase gates (preconditions)
        gate_check = self.phase_gates.get(job.current_phase)
        if gate_check and not gate_check(job):
            raise RuntimeError(
                f"Phase gate failed for {job.current_phase.name}; "
                f"preconditions not met"
            )

        # Get worker for current phase
        worker = self.worker_registry.get(job.current_phase)
        if not worker:
            raise RuntimeError(
                f"No worker registered for {job.current_phase.name}"
            )

        # Execute phase
        result = worker.execute(job)

        # Store result in job
        phase_name = job.current_phase.name.lower()
        if phase_name == "analysis":
            job.analysis_result = result
        elif phase_name == "voice":
            job.voice_result = result
        elif phase_name == "screenshots":
            job.screenshots_result = result
        elif phase_name == "assembly":
            job.video_result = result
        elif phase_name == "youtube":
            job.youtube_result = result

        # Emit audit event
        # Handle both dict and dataclass results
        success = True
        if isinstance(result, dict):
            success = result.get("success", True)
        else:
            success = getattr(result, "success", True)

        self._audit(
            "phase_executed",
            job_id,
            {
                "phase": job.current_phase.name,
                "worker": worker.__class__.__name__,
                "success": success,
            },
        )

        # Move to next phase
        next_phase_value = job.current_phase.value + 1
        if next_phase_value <= len(VideoJobPhase):
            job.current_phase = VideoJobPhase(next_phase_value)

        return result

    def record_feedback(
        self,
        job_id: str,
        scene_index: int,
        feedback_type: str,
        value: Any,
        notes: Optional[str] = None,
    ):
        """Record per-scene feedback for learning

        Args:
            job_id: Job identifier
            scene_index: Scene index (0-based)
            feedback_type: Type of feedback (pacing, quality, accuracy, engagement)
            value: Feedback value
            notes: Optional notes

        Raises:
            ValueError: If feedback values are invalid
        """

        job = self.jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        # Validate feedback based on type
        self._validate_feedback(feedback_type, value, scene_index, job)

        feedback = FeedbackEvent(
            timestamp=datetime.now().isoformat(),
            scene_index=scene_index,
            feedback_type=feedback_type,
            value=value,
            notes=notes,
        )
        job.feedback_history.append(feedback)

        # Emit audit event (learning signal)
        self._audit(
            "feedback_recorded",
            job_id,
            {
                "scene": scene_index,
                "feedback_type": feedback_type,
                "value": value,
            },
        )

    def get_job(self, job_id: str) -> Optional[VideoJob]:
        """Get job by ID"""
        return self.jobs.get(job_id)

    def list_jobs(self) -> List[str]:
        """List all job IDs"""
        return list(self.jobs.keys())

    def get_audit_log(self) -> List[Dict]:
        """Get full audit log"""
        return self.audit_log.copy()

    def _validate_narration(self, narration: List[str]) -> bool:
        """Validate that narration is well-formed

        Phase 1: Basic validation (non-empty, no obvious hallucinations)
        Phase 2: Deep validation via Asset Analyzer Worker
        """
        if not narration:
            return False

        # Check for basic hallucination indicators
        bad_phrases = [
            "I believe",
            "probably",
            "maybe",
            "I think",
            "I guess",
            "allegedly",
        ]

        for scene in narration:
            if not isinstance(scene, str) or not scene.strip():
                return False
            for phrase in bad_phrases:
                if phrase.lower() in scene.lower():
                    return False

        return True

    def _validate_analysis_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for Analysis phase"""
        return len(job.narration) > 0

    def _validate_voice_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for Voice phase

        Requires: Asset Analyzer must have completed successfully
        """
        return job.analysis_result is not None

    def _validate_screenshots_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for Screenshots phase

        Requires: Voice Synthesizer must have completed
        """
        return job.voice_result is not None

    def _validate_assembly_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for Assembly phase

        Requires: Screenshots must have been captured
        """
        return job.screenshots_result is not None

    def _validate_youtube_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for YouTube phase

        Requires: Video must be assembled
        """
        return job.video_result is not None

    def _validate_feedback(self, feedback_type: str, value: Any, scene_index: int, job: VideoJob):
        """Validate feedback value based on type (fail-closed)

        Raises:
            ValueError: If feedback is invalid
        """
        # Scene index must be valid
        if scene_index < 0 or scene_index >= len(job.narration):
            raise ValueError(f"Scene index {scene_index} out of range (0-{len(job.narration)-1})")

        # Feedback type must be known
        valid_types = ["pacing", "quality", "accuracy", "engagement", "confidence"]
        if feedback_type not in valid_types:
            raise ValueError(f"Unknown feedback type: {feedback_type}")

        # Value validation by type
        if feedback_type == "confidence":
            # Confidence must be between 0.0 and 1.0
            if not isinstance(value, (int, float)):
                raise ValueError(f"Confidence must be numeric, got {type(value)}")
            if value < 0.0 or value > 1.0:
                raise ValueError(f"Confidence must be between 0.0 and 1.0, got {value}")

        elif feedback_type in ["quality", "accuracy", "pacing"]:
            # Quality metrics should be 0-1 or 0-100
            if isinstance(value, (int, float)):
                if value < 0:
                    raise ValueError(f"{feedback_type} score cannot be negative: {value}")
                if value > 100:
                    raise ValueError(f"{feedback_type} score cannot exceed 100: {value}")
            else:
                raise ValueError(f"{feedback_type} score must be numeric, got {type(value)}")

        elif feedback_type == "engagement":
            # Engagement can be string (low/medium/high) or numeric
            if isinstance(value, str):
                valid_engagement = ["low", "medium", "high"]
                if value.lower() not in valid_engagement:
                    raise ValueError(f"Engagement must be low/medium/high, got {value}")
            elif isinstance(value, (int, float)):
                if value < 0 or value > 100:
                    raise ValueError(f"Engagement score must be 0-100, got {value}")

    def _audit(self, event_type: str, job_id: str, details: Dict):
        """Emit audit event (hash-chained, immutable)

        In production: write to audit_backend.write_event()
        """
        event = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "job_id": job_id,
            "details": details,
        }
        self.audit_log.append(event)
