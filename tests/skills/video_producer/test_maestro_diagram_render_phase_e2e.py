"""E2E wiring proof (ADR-2219): a dedicated DIAGRAM_RENDER VideoJobPhase,
with DiagramRendererWorker registered for it, is reachable through the real
MaestroOrchestrator phase dispatch — not just callable in isolation, and not
only via the ADR-2212 pattern of swapping the SCREENSHOTS worker.

Exercises MaestroOrchestrator.execute_phase directly (the real transport),
and separately proves the ADR-2212 pattern (DiagramRendererWorker registered
for SCREENSHOTS, no DIAGRAM_RENDER worker) still behaves exactly as before —
the new phase is additive, not a replacement.
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

_ONE_SPEC = {0: {
    "elements": [{"id": "proof", "type": "box", "label": "Wired via DIAGRAM_RENDER", "at": [700, 460]}],
}}


@requires_openai_key
@requires_ffmpeg
def test_diagram_render_phase_reachable_through_maestro():
    """A job with diagram_specs set, run through the real MaestroOrchestrator
    with DiagramRendererWorker registered for the dedicated DIAGRAM_RENDER
    phase (not SCREENSHOTS), must complete
    ANALYSIS -> VOICE -> DIAGRAM_RENDER -> ASSEMBLY and produce a real video —
    with no SCREENSHOTS worker registered at all, proving the fork actually
    routes away from SCREENSHOTS rather than merely tolerating its absence."""
    maestro = MaestroOrchestrator()
    maestro.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker)
    maestro.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker)
    maestro.register_worker(VideoJobPhase.DIAGRAM_RENDER, DiagramRendererWorker)
    maestro.register_worker(VideoJobPhase.ASSEMBLY, VideoAssemblerWorker)
    # Deliberately NOT registering a SCREENSHOTS worker.

    job_id = maestro.create_job(
        topic="E2E DIAGRAM_RENDER Wiring Test",
        duration=10,
        audience="technical",
        narration=["This sentence proves the dedicated diagram render phase runs through Maestro."],
        job_id="test_maestro_diagram_render_phase",
    )
    job = maestro.jobs[job_id]
    job.diagram_specs = _ONE_SPEC

    maestro.execute_phase(job_id)  # ANALYSIS
    assert job.current_phase == VideoJobPhase.VOICE

    voice_result = maestro.execute_phase(job_id)  # VOICE
    assert job.current_phase == VideoJobPhase.DIAGRAM_RENDER, (
        "VOICE must fork to DIAGRAM_RENDER when diagram_specs is set and a "
        "DIAGRAM_RENDER worker is registered — got "
        f"{job.current_phase.name} instead"
    )
    assert voice_result.provider_used in ("openai-tts", "edge-tts", "piper-tts", "mock")

    render_result = maestro.execute_phase(job_id)  # DIAGRAM_RENDER
    assert job.current_phase == VideoJobPhase.ASSEMBLY
    assert render_result.success
    assert len(render_result.screenshots) == 1
    assert render_result.screenshots[0].endswith(".png")
    # DIAGRAM_RENDER must populate the SAME slot SCREENSHOTS would, so
    # ASSEMBLY needs no branching on which phase actually ran.
    assert job.screenshots_result is render_result

    video_result = maestro.execute_phase(job_id)  # ASSEMBLY
    assert job.current_phase == VideoJobPhase.YOUTUBE
    assert video_result.success
    assert os.path.exists(video_result.video_path)
    assert video_result.bitrate_kbps >= 100  # the quality gate this job had to clear


def test_adr_2212_pattern_unaffected_by_new_phase():
    """Backward compatibility: a job that registers DiagramRendererWorker
    directly for SCREENSHOTS (the pre-existing ADR-2212 pattern) and sets
    diagram_specs, but registers NO worker for DIAGRAM_RENDER, must still
    route VOICE -> SCREENSHOTS exactly as before the fork was introduced.
    No real TTS/ffmpeg needed — fakes are enough to prove the phase routing
    itself, which is the only thing this test is about."""

    class _FakeOk:
        def __init__(self, **extra):
            self._extra = extra

        def execute(self, job, **kwargs):
            result = {"success": True}
            result.update(self._extra)
            return result

    maestro = MaestroOrchestrator()
    maestro.register_worker(VideoJobPhase.ANALYSIS, _FakeOk)
    maestro.register_worker(VideoJobPhase.VOICE, _FakeOk)
    maestro.register_worker(VideoJobPhase.SCREENSHOTS, _FakeOk(screenshots=["diagram.png"]))
    maestro.register_worker(VideoJobPhase.ASSEMBLY, _FakeOk(video_path="/tmp/x.mp4"))
    # No DIAGRAM_RENDER worker registered at all.

    job_id = maestro.create_job(
        topic="ADR-2212 backward-compat check",
        duration=10,
        audience="technical",
        narration=["Narration long enough to clear the content-presence gate."],
        job_id="test_adr_2212_backward_compat",
    )
    job = maestro.jobs[job_id]
    job.diagram_specs = _ONE_SPEC  # set, same as a real ADR-2212 job would

    maestro.execute_phase(job_id)  # ANALYSIS -> VOICE
    maestro.execute_phase(job_id)  # VOICE -> SCREENSHOTS (no DIAGRAM_RENDER worker registered)
    assert job.current_phase == VideoJobPhase.SCREENSHOTS, (
        "a job with no DIAGRAM_RENDER worker registered must keep routing "
        f"through SCREENSHOTS regardless of diagram_specs — got {job.current_phase.name}"
    )
    maestro.execute_phase(job_id)  # SCREENSHOTS -> ASSEMBLY
    assert job.current_phase == VideoJobPhase.ASSEMBLY
