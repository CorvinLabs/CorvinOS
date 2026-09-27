"""Worker Skills for Video Producer Skill 2.0

Each worker is an independent Skill that handles one phase of video production:
- AssetAnalyzerWorker: Validate narration and assets
- VoiceSynthesizerWorker: Generate voice narration
- ScreenshotCapturerWorker: Capture screenshots
- VideoAssemblerWorker: Assemble video from components
- YouTubeUploaderWorker: Upload to YouTube
"""

from .asset_analyzer import AssetAnalyzerWorker, AnalysisStatus, AnalysisResult
from .voice_synthesizer import VoiceSynthesizerWorker, VoiceResult
from .screenshot_capturer import ScreenshotCapturerWorker, ScreenshotResult
from .video_assembler import VideoAssemblerWorker, VideoResult
from .youtube_uploader import YouTubeUploaderWorker, UploadResult

# Phase 6b generic workers (formerly the shadowed ``video_producer/workers.py``).
# NOT IMPLEMENTED — each fails closed with a ``not_implemented:`` error.
from .phase6b import (
    NOT_IMPLEMENTED,
    WORKERS,
    BaseWorker,
    FFmpegWorker,
    ScreenshotWorker,
    TTSWorker,
    WorkerResult,
    YouTubeWorker,
    get_worker,
)

__all__ = [
    "AssetAnalyzerWorker",
    "AnalysisStatus",
    "AnalysisResult",
    "VoiceSynthesizerWorker",
    "VoiceResult",
    "ScreenshotCapturerWorker",
    "ScreenshotResult",
    "VideoAssemblerWorker",
    "VideoResult",
    "YouTubeUploaderWorker",
    "UploadResult",
    "NOT_IMPLEMENTED",
    "WORKERS",
    "BaseWorker",
    "FFmpegWorker",
    "ScreenshotWorker",
    "TTSWorker",
    "WorkerResult",
    "YouTubeWorker",
    "get_worker",
]
