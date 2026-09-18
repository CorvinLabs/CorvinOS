"""Video Producer Skill 2.0 — Phase 1 Foundation (Maestro + Workers)."""

from .maestro import VideoProducerMaestro
from .worker_base import WorkerSkillBase, WorkerManifest, WorkerRegistry, WorkerResult
from .workers.audio_synthesis import AudioSynthesisWorker
from .workers.screenshot_capture import ScreenshotCaptureWorker
from .workers.video_assembler import VideoAssemblerWorker

# LDD k=2 E2E Wiring Proof (Video Producer 2.0 Enhanced)
from .orchestrator_v2_enhanced import VideoProducerOrchestratorV2
from .types import VideoGenerationRequest, VideoGenerationResult, PhaseResult

__all__ = [
    "VideoProducerMaestro",
    "WorkerSkillBase",
    "WorkerManifest",
    "WorkerRegistry",
    "WorkerResult",
    "AudioSynthesisWorker",
    "ScreenshotCaptureWorker",
    "VideoAssemblerWorker",
    "VideoProducerOrchestratorV2",
    "VideoGenerationRequest",
    "VideoGenerationResult",
    "PhaseResult",
]
