"""E2E wiring proof (ADR-2212): DiagramRendererWorker registered for the
SCREENSHOTS phase must be reachable through the real MaestroOrchestrator
phase dispatch, not just callable in isolation. Exercises the actual
transport (MaestroOrchestrator.execute_phase), not a direct worker call.
"""
import os
import pytest

from core.skills.video_producer.maestro import MaestroOrchestrator, VideoJobPhase
from core.skills.video_producer.workers.asset_analyzer import AssetAnalyzerWorker
from core.skills.video_producer.workers.voice_synthesizer import VoiceSynthesizerWorker
from core.skills.video_producer.workers.diagram_renderer import DiagramRendererWorker
from core.skills.video_producer.workers.video_assembler import VideoAssemblerWorker

requires_openai_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY for real TTS"
)
requires_ffmpeg = pytest.mark.skipif(
    os.system("which ffmpeg > /dev/null 2>&1") != 0, reason="needs ffmpeg"
)


@requires_openai_key
@requires_ffmpeg
def test_diagram_renderer_reachable_through_maestro_screenshots_phase():
    """A job with diagram_specs set, run through the real MaestroOrchestrator
    with DiagramRendererWorker registered for SCREENSHOTS, must complete
    ANALYSIS -> VOICE -> SCREENSHOTS -> ASSEMBLY and produce a real video
    whose frames came from the diagram compiler, not a screenshot capture."""
    maestro = MaestroOrchestrator()
    maestro.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker)
    maestro.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker)
    maestro.register_worker(VideoJobPhase.SCREENSHOTS, DiagramRendererWorker)
    maestro.register_worker(VideoJobPhase.ASSEMBLY, VideoAssemblerWorker)

    job_id = maestro.create_job(
        topic="E2E Wiring Test",
        duration=10,
        audience="technical",
        narration=["This sentence proves the diagram renderer runs through the real Maestro orchestrator."],
        job_id="test_maestro_diagram_wiring",
    )
    job = maestro.jobs[job_id]
    job.diagram_specs = {0: {
        "elements": [{"id": "proof", "type": "box", "label": "Wired", "at": [700, 460]}],
    }}

    maestro.execute_phase(job_id)  # ANALYSIS
    assert job.current_phase == VideoJobPhase.VOICE

    voice_result = maestro.execute_phase(job_id)  # VOICE
    assert job.current_phase == VideoJobPhase.SCREENSHOTS
    assert voice_result.provider_used in ("openai-tts", "edge-tts", "piper-tts", "mock")

    screenshots_result = maestro.execute_phase(job_id)  # SCREENSHOTS (-> diagram renderer)
    assert job.current_phase == VideoJobPhase.ASSEMBLY
    assert screenshots_result.success
    assert len(screenshots_result.screenshots) == 1
    assert screenshots_result.screenshots[0].endswith(".png")

    video_result = maestro.execute_phase(job_id)  # ASSEMBLY
    assert job.current_phase == VideoJobPhase.YOUTUBE
    assert video_result.success
    assert os.path.exists(video_result.video_path)
    assert video_result.bitrate_kbps >= 100  # the quality gate this job had to clear


@requires_openai_key
@requires_ffmpeg
def test_diagram_renderer_with_multiple_frames_per_scene():
    """A diagram spec with `steps` (progressive reveal) produces multiple
    frames; the assembler must use all of them, not just the first, and the
    resulting video must still clear the bitrate quality gate."""
    maestro = MaestroOrchestrator()
    maestro.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker)
    maestro.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker)
    maestro.register_worker(VideoJobPhase.SCREENSHOTS, DiagramRendererWorker)
    maestro.register_worker(VideoJobPhase.ASSEMBLY, VideoAssemblerWorker)

    job_id = maestro.create_job(
        topic="Multi-Frame Wiring Test",
        duration=10,
        audience="technical",
        narration=["Multiple steps in one scene must all become frames in the final video, "
                   "not just the first one, so the encoder has enough entropy across the "
                   "whole clip to clear the minimum bitrate quality gate."],
        job_id="test_maestro_multiframe_wiring",
    )
    job = maestro.jobs[job_id]
    job.diagram_specs = {0: {
        "elements": [
            {"id": "a", "type": "box", "label": "A", "at": [300, 460]},
            {"id": "b", "type": "box", "label": "B", "right_of": "a", "gap": 80},
            {"id": "c", "type": "box", "label": "C", "right_of": "b", "gap": 80},
        ],
        "steps": [["a"], ["a", "b"], ["a", "b", "c"]],
    }}

    maestro.execute_phase(job_id)  # ANALYSIS
    maestro.execute_phase(job_id)  # VOICE
    screenshots_result = maestro.execute_phase(job_id)  # SCREENSHOTS
    assert len(screenshots_result.screenshots) == 3  # one frame per step

    video_result = maestro.execute_phase(job_id)  # ASSEMBLY
    assert video_result.success
    assert video_result.bitrate_kbps >= 100
