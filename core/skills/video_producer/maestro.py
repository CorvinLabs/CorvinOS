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
import re

from .hedges import find_hedge
from datetime import datetime
import uuid


class VideoJobPhase(Enum):
    """Video production phases.

    SCREENSHOTS and DIAGRAM_RENDER are alternatives for the same slot (both
    populate job.screenshots_result with the visual frames ASSEMBLY consumes)
    — never both for one job. IMAGE_RESEARCH is optional and runs between
    VOICE and that visual slot. _next_phase() picks the route; see its
    docstring for the selection rule. Every other transition is strictly
    sequential.
    """
    ANALYSIS = 1           # Asset Analyzer Worker
    VOICE = 2              # Voice Synthesizer Worker
    SCREENSHOTS = 3        # Screenshot Capturer Worker (real browser screenshots)
    DIAGRAM_RENDER = 4     # Diagram Renderer Worker (declarative diagram specs -> PNG)
    ASSEMBLY = 5           # Video Assembler Worker
    YOUTUBE = 6            # YouTube Uploader Worker
    COMPLETE = 7
    # ADR-2221. Value 8, not inserted between VOICE and the visual phases:
    # renumbering would change every persisted/serialized phase value, and
    # _next_phase() routes it explicitly, so its position in the value order
    # carries no meaning.
    IMAGE_RESEARCH = 8     # Image Research Worker (licensed image fetch)


@dataclass(frozen=True)
class FeedbackEvent:
    """Immutable feedback event for learning"""
    timestamp: str
    scene_index: int
    feedback_type: str  # "pacing", "quality", "accuracy", "engagement"
    value: Any
    notes: Optional[str] = None


# A job id becomes part of file paths in several workers (/tmp/<id>_final.mp4,
# per-job directories), so it is restricted at the one place every job is
# built rather than at each call site: "../tmp/x" escaped /tmp before this.
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class VideoJob:
    """Video job specification.

    Mutable on purpose: callers attach diagram_specs / research_queries after
    create_job(). Anything a gate relies on is therefore re-checked when the
    gate runs (see _validate_analysis_phase), not only at creation.
    """
    job_id: str
    topic: str
    duration_seconds: int
    audience: str  # "beginners", "technical", "operators"
    narration: List[str]
    current_phase: VideoJobPhase = VideoJobPhase.ANALYSIS
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    feedback_history: List[FeedbackEvent] = field(default_factory=list)
    # ADR-2212 / ADR-2214: when set, the pipeline renders these diagram specs
    # (via DiagramRendererWorker) instead of capturing real screenshots. Takes
    # effect two ways depending on what the caller registered: a
    # DIAGRAM_RENDER worker routes this job through that dedicated phase
    # (ADR-2214, the normal Maestro job path); with no DIAGRAM_RENDER worker
    # registered, a DiagramRendererWorker registered directly for SCREENSHOTS
    # still works exactly as before (ADR-2212, unchanged for backward compat).
    # {scene_index: spec}, same vocabulary as diagram/compiler.py.
    diagram_specs: Optional[Dict[int, dict]] = None
    # ADR-2221: {ref: query} or {ref: {"query": str, "sources": [...]}}.
    # A diagram spec embeds the result as an image element with
    # src "research:<ref>"; the citation is drawn from research_result.
    research_queries: Optional[Dict[str, Any]] = None
    research_result: Optional[Any] = None
    analysis_result: Optional[Dict] = None
    # The narration ANALYSIS approved. Every later gate compares against it:
    # replacing job.narration after analysis must not reach the voice.
    analyzed_narration: Optional[tuple] = None
    voice_result: Optional[Dict] = None
    screenshots_result: Optional[Dict] = None
    video_result: Optional[Dict] = None
    youtube_result: Optional[Dict] = None

    def __post_init__(self):
        """Validate job invariants"""
        if not self.job_id:
            self.job_id = f"video_{uuid.uuid4().hex[:12]}"
        if not isinstance(self.job_id, str) or not JOB_ID_RE.match(self.job_id):
            raise ValueError("job_id must match [A-Za-z0-9_-]{1,64}")
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
            VideoJobPhase.DIAGRAM_RENDER: self._validate_diagram_render_phase,
            VideoJobPhase.IMAGE_RESEARCH: self._validate_image_research_phase,
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

        # ======== GATE 1: Content-Presence (Fail-Closed) ========
        # Must pass BEFORE job creation
        self.validate_content_presence(narration)

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

        if job.job_id in self.jobs:
            raise ValueError(f"job {job.job_id} already exists; pick a new job_id")
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
        # Workers build paths from job.job_id; the field is mutable, so the
        # check made at creation is repeated before every dispatch.
        if not isinstance(job.job_id, str) or not JOB_ID_RE.match(job.job_id) or job.job_id != job_id:
            raise RuntimeError(f"job id of {job_id!r} was changed or is unsafe; refusing to dispatch")

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

        # Store result in job. SCREENSHOTS and DIAGRAM_RENDER are
        # alternatives for the same slot (see VideoJobPhase docstring) — both
        # populate screenshots_result, so ASSEMBLY's gate and VideoAssemblerWorker
        # need no branching on which one ran.
        phase_name = job.current_phase.name.lower()
        if phase_name == "analysis":
            job.analysis_result = result
            job.analyzed_narration = tuple(job.narration)
        elif phase_name == "voice":
            job.voice_result = result
        elif phase_name == "image_research":
            job.research_result = result
        elif phase_name in ("screenshots", "diagram_render"):
            job.screenshots_result = result
        elif phase_name == "assembly":
            job.video_result = result
        elif phase_name == "youtube":
            job.youtube_result = result

        # A result has to SAY it succeeded. None, a dict without "success" or
        # an object without the attribute used to count as success — and an
        # AnalysisResult with status FAIL advanced the job that way.
        if isinstance(result, dict):
            success = result.get("success") is True
        else:
            success = getattr(result, "success", None) is True

        self._audit(
            "phase_executed",
            job_id,
            {
                "phase": job.current_phase.name,
                "worker": worker.__class__.__name__,
                "success": success,
            },
        )

        # A failed phase stops the job where it is (fail-closed): advancing
        # would hand the next worker a result that says "nothing was produced"
        # and let the next gate (which only checks "result is not None") pass.
        if not success:
            raise RuntimeError(
                f"Phase {job.current_phase.name} failed for job {job_id}; "
                f"the job stays in {job.current_phase.name}"
            )

        # Move to next phase
        next_phase = self._next_phase(job.current_phase, job)
        if next_phase is not None:
            job.current_phase = next_phase

        return result

    def _next_phase(self, current: "VideoJobPhase", job: VideoJob) -> Optional["VideoJobPhase"]:
        """Determine the phase after `current` for this job.

        IMAGE_RESEARCH (ADR-2221) runs right after VOICE when the job carries
        research_queries AND an IMAGE_RESEARCH worker is registered; it then
        continues into the same visual fork VOICE would have taken. A job
        with research_queries but no registered worker skips the phase —
        any diagram spec that embeds research:<ref> then fails closed in
        DIAGRAM_RENDER (unresolved image), it is never rendered with a hole.

        Linear for every phase except the VOICE -> {SCREENSHOTS, DIAGRAM_RENDER}
        fork: DIAGRAM_RENDER runs instead of SCREENSHOTS only when the job
        carries diagram_specs AND a DIAGRAM_RENDER worker is registered —
        otherwise SCREENSHOTS runs exactly as before (ADR-2212 jobs that
        register DiagramRendererWorker directly for SCREENSHOTS, with no
        DIAGRAM_RENDER registration, are unaffected). Both forks converge on
        ASSEMBLY, which only ever reads job.screenshots_result.
        """
        if current == VideoJobPhase.VOICE:
            if job.research_queries and VideoJobPhase.IMAGE_RESEARCH in self.worker_registry:
                return VideoJobPhase.IMAGE_RESEARCH
            return self._visual_phase(job)
        if current == VideoJobPhase.IMAGE_RESEARCH:
            return self._visual_phase(job)
        if current in (VideoJobPhase.SCREENSHOTS, VideoJobPhase.DIAGRAM_RENDER):
            return VideoJobPhase.ASSEMBLY
        next_value = current.value + 1
        for phase in VideoJobPhase:
            if phase.value == next_value:
                return phase
        return None

    def _visual_phase(self, job: VideoJob) -> "VideoJobPhase":
        if job.diagram_specs and VideoJobPhase.DIAGRAM_RENDER in self.worker_registry:
            return VideoJobPhase.DIAGRAM_RENDER
        return VideoJobPhase.SCREENSHOTS

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

        for scene in narration:
            if not isinstance(scene, str) or not scene.strip():
                return False
            if find_hedge(scene):
                return False

        return True

    def validate_content_presence(self, narration: List[str], emit: bool = True) -> None:
        """GATE 1: Content-Presence Gate — Fail-Closed Validation (ADR-0720)

        Rejects jobs with empty or insufficient narration BEFORE any worker dispatch.
        This is a fail-closed gate: if content is inadequate, raise immediately.

        Args:
            narration: List of narration texts for all scenes

        Raises:
            ValueError: If narration is empty, None, or all blank
        """
        # Check 1: Narration must exist
        if not narration:
            raise ValueError(
                "Content-Presence Gate FAILED: Narration is empty. "
                "At least one non-empty scene is required."
            )

        # Check 2: All scenes must have content
        non_empty_scenes = [scene.strip() for scene in narration if scene.strip()]
        if len(non_empty_scenes) != len(narration):
            raise ValueError(
                f"Content-Presence Gate FAILED: {len(narration) - len(non_empty_scenes)} "
                f"scene(s) are empty out of {len(narration)} total. "
                "All scenes must have non-empty narration."
            )

        # Check 3: Total content length must be meaningful (at least 20 chars)
        total_content_length = sum(len(scene.strip()) for scene in narration)
        if total_content_length < 20:
            raise ValueError(
                f"Content-Presence Gate FAILED: Total narration too short ({total_content_length} chars). "
                "Minimum 20 characters required for meaningful content."
            )

        # Emit audit event: content validation passed
        if emit:
            self._audit("content_presence_validated", "pre-job-creation", {
                "num_scenes": len(narration),
                "total_length_chars": total_content_length,
                "status": "passed",
            })

    def _validate_analysis_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for Analysis phase.

        Re-runs the content-presence gate: narration can be replaced on the
        job after create_job(), and blank scenes must not reach analysis.
        """
        try:
            self.validate_content_presence(job.narration, emit=False)
        except (ValueError, AttributeError, TypeError):
            return False
        return True

    def _validate_voice_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for Voice phase

        Requires: Asset Analyzer completed, and the narration is still the
        text it approved (the job is mutable; an edit after analysis would
        otherwise be spoken unchecked).
        """
        return (job.analysis_result is not None
                and job.analyzed_narration is not None
                and tuple(job.narration) == job.analyzed_narration)

    def _validate_screenshots_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for Screenshots phase

        Requires: Voice Synthesizer must have completed
        """
        return job.voice_result is not None

    def _validate_diagram_render_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for Diagram Render phase

        Requires: Voice Synthesizer must have completed, and the job must
        actually carry diagram specs (same precondition DiagramRendererWorker
        itself enforces — this gate just fails closed before dispatch
        instead of after).
        """
        return job.voice_result is not None and bool(job.diagram_specs)

    def _validate_image_research_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for Image Research phase (ADR-2221)

        Requires: Voice Synthesizer completed and the job names queries.
        """
        return job.voice_result is not None and bool(job.research_queries)

    def _validate_assembly_phase(self, job: VideoJob) -> bool:
        """Validate preconditions for Assembly phase

        Requires: at least one visual frame. With no frames the assembler
        would produce an audio-only .mp4 and call it a video.
        """
        r = job.screenshots_result
        if r is None:
            return False
        frames = r.get("screenshots") if isinstance(r, dict) else getattr(r, "screenshots", None)
        return bool(frames)

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
        """Record a pipeline event in this orchestrator's IN-MEMORY log.

        Not hash-chained and not on the tenant audit chain — nothing here
        reaches security_events. Do not cite it as the audit trail.
        """
        event = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "job_id": job_id,
            "details": details,
        }
        self.audit_log.append(event)
