"""E2E test for DiagramRendererWorker, same pattern as
tests/skills/test_video_producer_phase4_real_apis.py::TestMaestroPhase4 —
a real MaestroOrchestrator, a real worker registration, a real
maestro.execute_phase() call (not a direct method import), so the phase-gate
and result-storage wiring is exercised exactly as production code hits it.
"""
from pathlib import Path

from core.skills.video_producer.maestro import MaestroOrchestrator, VideoJobPhase
from core.skills.video_producer.workers.diagram_renderer import (
    DiagramRendererWorker,
    DiagramRenderResult,
)

MAESTRO_WORKERS_SPEC = {
    "theme": "corvin-dark",
    "canvas": {"w": 1920, "h": 1080},
    "elements": [
        {"id": "maestro", "type": "box", "label": "os.video_producer", "at": [760, 90], "icon": "layers", "variant": "accent"},
        {"id": "w1", "type": "box", "label": "worker", "below": "maestro", "gap": 100},
        {"id": "a1", "type": "arrow", "from": "maestro.s", "to": "w1.n"},
    ],
    "steps": [["maestro"], ["maestro", "w1", "a1"]],
}


def test_worker_initialization():
    worker = DiagramRendererWorker()
    assert worker.name == "diagram_renderer"
    assert worker.version == "1.1.0"


def test_no_specs_fails_closed_and_the_job_does_not_advance(tmp_path):
    """No specs = no visuals. The phase fails and maestro keeps the job in
    SCREENSHOTS instead of handing ASSEMBLY an empty result."""
    import pytest

    worker = DiagramRendererWorker(diagram_specs={}, out_dir=str(tmp_path))
    maestro = MaestroOrchestrator()
    maestro.register_worker(VideoJobPhase.ANALYSIS, _NoopAnalysis())
    maestro.register_worker(VideoJobPhase.VOICE, _NoopVoice())
    maestro.register_worker(VideoJobPhase.SCREENSHOTS, worker)

    job_id = maestro.create_job(
        topic="Test", duration=10, audience="test",
        narration=["Scene one has enough narration text to pass the content gate."],
    )
    maestro.execute_phase(job_id)  # ANALYSIS
    maestro.execute_phase(job_id)  # VOICE
    with pytest.raises(RuntimeError, match="SCREENSHOTS failed"):
        maestro.execute_phase(job_id)
    job = maestro.get_job(job_id)
    assert job.current_phase == VideoJobPhase.SCREENSHOTS
    assert job.screenshots_result.success is False
    assert job.screenshots_result.screenshots == []


def test_execute_via_real_maestro_phase_e2e(tmp_path):
    """Real MaestroOrchestrator -> register_worker -> execute_phase, exactly
    the production call shape (tests/skills/test_video_producer_phase4_real_apis.py
    TestMaestroPhase4 pattern) — not a bare class import + direct call."""
    out_dir = tmp_path / "diagram_out"
    worker = DiagramRendererWorker(diagram_specs={0: MAESTRO_WORKERS_SPEC}, out_dir=str(out_dir))

    maestro = MaestroOrchestrator()
    maestro.register_worker(VideoJobPhase.ANALYSIS, _NoopAnalysis())
    maestro.register_worker(VideoJobPhase.VOICE, _NoopVoice())
    maestro.register_worker(VideoJobPhase.SCREENSHOTS, worker)

    job_id = maestro.create_job(
        topic="Video Producer Architecture", duration=20, audience="technical",
        narration=["os.video_producer orchestrates its worker skills."],
    )

    maestro.execute_phase(job_id)  # ANALYSIS
    maestro.execute_phase(job_id)  # VOICE
    result = maestro.execute_phase(job_id)  # SCREENSHOTS -> diagram_renderer.execute(job)

    job = maestro.get_job(job_id)
    assert job.current_phase == VideoJobPhase.ASSEMBLY  # phase advanced -> execute_phase succeeded
    assert job.screenshots_result is result  # same slot video_assembler reads from

    assert isinstance(result, DiagramRenderResult)
    assert result.success is True
    assert result.num_captured == 2  # two steps in MAESTRO_WORKERS_SPEC
    assert len(result.screenshots) == 2

    import hashlib

    digests = []
    for frame_path_str in result.screenshots:
        frame_path = Path(frame_path_str)
        assert frame_path.exists(), f"{frame_path} was reported but not written"
        assert frame_path.stat().st_size > 1000, f"{frame_path} suspiciously small"
        digests.append(hashlib.sha256(frame_path.read_bytes()).hexdigest())
    assert len(set(digests)) == len(digests), "step 2 rendered identical to step 1 — reveal did nothing"


class _NoopAnalysis:
    def __init__(self):
        self.name = "asset_analyzer"
        self.version = "noop"

    def execute(self, job):
        return {"success": True, "ready_for_narration": True}


class _NoopVoice:
    def __init__(self):
        self.name = "voice_synthesizer"
        self.version = "noop"

    def execute(self, job):
        return {"success": True}
