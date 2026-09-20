"""Video Producer Skill 2.0 — Phase 1 Foundation (Maestro + Workers)."""

from .maestro import VideoProducerMaestro
# Re-exported: orchestrator.py shipped without an entry here, so
# `from ...video_producer import VideoProducerOrchestrator` — the import
# two test suites use — raised ImportError and both were collection errors.
from .orchestrator import VideoProducerOrchestrator
from .storyboard_generator import StoryboardGenerator
from .exceptions import (
    AnalysisGateFailedError,
    AnalysisIncompleteError,
    AssetIngestionError,
    PreconditionNotMetError,
    StoryboardValidationError,
    VideoProducerError,
)
from .worker_base import WorkerSkillBase, WorkerManifest, WorkerRegistry, WorkerResult
from .workers.audio_synthesis import AudioSynthesisWorker
from .workers.screenshot_capture import ScreenshotCaptureWorker
from .workers.video_assembler import VideoAssemblerWorker
from .types import Scene, Storyboard, AssetAnalysisResult, FactualClaim, Contradiction

__all__ = [
    "VideoProducerMaestro",
    "VideoProducerOrchestrator",
    "StoryboardGenerator",
    "VideoProducerError",
    "AssetIngestionError",
    "AnalysisIncompleteError",
    "AnalysisGateFailedError",
    "PreconditionNotMetError",
    "StoryboardValidationError",
    "WorkerSkillBase",
    "WorkerManifest",
    "WorkerRegistry",
    "WorkerResult",
    "AudioSynthesisWorker",
    "ScreenshotCaptureWorker",
    "VideoAssemblerWorker",
    "Scene",
    "Storyboard",
    "AssetAnalysisResult",
    "FactualClaim",
    "Contradiction",
]
