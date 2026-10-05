"""E2E wiring proof (ADR-2221): IMAGE_RESEARCH is reachable through the real
MaestroOrchestrator dispatch, and a researched image actually lands in a
rendered frame — with its citation drawn by the compiler, not the spec.

The full-chain test hits the real Wikimedia Commons API, real OpenAI TTS and
real ffmpeg. The routing / fail-closed tests need no network: they use a
stand-in VOICE worker, because what they prove is Maestro's phase routing
and the compiler's refusal paths, not speech synthesis.
"""
import os
import socket
from types import SimpleNamespace

import pytest

from core.skills.video_producer.maestro import MaestroOrchestrator, VideoJobPhase
from core.skills.video_producer.workers.asset_analyzer import AssetAnalyzerWorker
from core.skills.video_producer.workers.diagram.compiler import SpecError, compile_spec
from core.skills.video_producer.workers.diagram_renderer import DiagramRendererWorker
from core.skills.video_producer.workers.research_worker import ImageResearchWorker
from core.skills.video_producer.workers.video_assembler import VideoAssemblerWorker
from core.skills.video_producer.workers.voice_synthesizer import VoiceSynthesizerWorker


def _network() -> bool:
    try:
        socket.create_connection(("commons.wikimedia.org", 443), timeout=3).close()
        return True
    except OSError:
        return False


requires_network = pytest.mark.skipif(not _network(), reason="needs commons.wikimedia.org")
requires_openai_key = pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
requires_ffmpeg = pytest.mark.skipif(os.system("which ffmpeg > /dev/null 2>&1") != 0, reason="needs ffmpeg")

_SPEC = {0: {
    "elements": [
        {"id": "title", "type": "text", "at": [120, 90], "style": "title",
         "lines": ["Researched image", "embedded through IMAGE_RESEARCH"]},
        {"id": "photo", "type": "image", "src": "research:graph", "at": [1060, 260], "size": [720, 560]},
        {"id": "facts", "type": "text", "at": [120, 330], "style": "bullets", "width": 860,
         "lines": ["Licensed source only", "Citation drawn by the compiler"]},
    ],
}}


class _StubVoice:
    def execute(self, job):
        return {"success": True, "audio_files": [], "total_duration_seconds": 1.0}


def _maestro(with_research: bool, voice=VoiceSynthesizerWorker, out_dir=None) -> MaestroOrchestrator:
    m = MaestroOrchestrator()
    m.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker)
    m.register_worker(VideoJobPhase.VOICE, voice)
    if with_research:
        m.register_worker(VideoJobPhase.IMAGE_RESEARCH, ImageResearchWorker(out_dir=out_dir))
    m.register_worker(VideoJobPhase.DIAGRAM_RENDER, DiagramRendererWorker(out_dir=out_dir))
    m.register_worker(VideoJobPhase.ASSEMBLY, VideoAssemblerWorker)
    return m


@requires_network
@requires_openai_key
@requires_ffmpeg
def test_image_research_phase_reaches_a_rendered_frame(tmp_path):
    m = _maestro(with_research=True, out_dir=str(tmp_path))
    job_id = m.create_job(
        topic="E2E IMAGE_RESEARCH Wiring Test", duration=10, audience="technical",
        narration=["This scene shows a licensed image that the research phase found on Wikimedia Commons, downloaded, checked for its license, and embedded into the frame together with its citation."],
        job_id="test_maestro_image_research_phase",
    )
    job = m.jobs[job_id]
    job.diagram_specs = _SPEC
    job.research_queries = {"graph": {"query": "graph theory network diagram", "sources": ["commons"]}}

    m.execute_phase(job_id)  # ANALYSIS
    m.execute_phase(job_id)  # VOICE
    assert job.current_phase == VideoJobPhase.IMAGE_RESEARCH, job.current_phase

    research = m.execute_phase(job_id)  # IMAGE_RESEARCH
    assert research.success, research.error
    assert job.research_result is research
    rec = research.images["graph"]
    assert os.path.exists(rec["local_path"])
    assert rec["license"] and rec["citation"]
    assert job.current_phase == VideoJobPhase.DIAGRAM_RENDER

    render = m.execute_phase(job_id)  # DIAGRAM_RENDER
    assert render.success, render.error
    frame = render.screenshots[0]

    from PIL import Image
    with Image.open(frame) as im:
        crop = im.convert("RGB").crop((1060, 260, 1780, 786)).resize((180, 130))
        colors = crop.getcolors(maxcolors=180 * 130)
    # A photo/diagram embedded in the image box carries far more colours than
    # the flat surface fill the box would show if the image were missing.
    assert colors is None or len(colors) > 40, f"image area looks empty ({len(colors)} colours)"

    m.execute_phase(job_id)  # ASSEMBLY
    assert os.path.getsize(job.video_result.video_path) > 10_000


def test_research_queries_without_worker_skip_phase_and_fail_closed(tmp_path):
    """No IMAGE_RESEARCH worker registered: VOICE routes straight to
    DIAGRAM_RENDER, and the spec's research:graph is unresolved, so the render
    fails — the frame is never shipped with a hole where the image belongs."""
    m = _maestro(with_research=False, voice=_StubVoice, out_dir=str(tmp_path))
    job_id = m.create_job(
        topic="E2E IMAGE_RESEARCH skip", duration=10, audience="technical",
        narration=["This scene references an image that was never researched."],
        job_id="test_maestro_image_research_skip",
    )
    job = m.jobs[job_id]
    job.diagram_specs = _SPEC
    job.research_queries = {"graph": "graph theory"}

    m.execute_phase(job_id)  # ANALYSIS
    m.execute_phase(job_id)  # VOICE
    assert job.current_phase == VideoJobPhase.DIAGRAM_RENDER
    with pytest.raises(RuntimeError, match="DIAGRAM_RENDER failed"):
        m.execute_phase(job_id)
    assert job.screenshots_result is not None and not job.screenshots_result.success
    assert "research:graph" in job.screenshots_result.error


def test_job_without_research_queries_never_enters_the_phase(tmp_path):
    m = _maestro(with_research=True, voice=_StubVoice, out_dir=str(tmp_path))
    job_id = m.create_job(
        topic="E2E no research", duration=10, audience="technical",
        narration=["This scene needs no researched image at all."],
        job_id="test_maestro_no_research",
    )
    m.jobs[job_id].diagram_specs = {0: {"elements": [{"id": "b", "type": "box", "label": "x", "at": [100, 100]}]}}
    m.execute_phase(job_id)
    m.execute_phase(job_id)
    assert m.jobs[job_id].current_phase == VideoJobPhase.DIAGRAM_RENDER


@pytest.mark.parametrize("src", [
    "/etc/passwd", "file:///etc/passwd", "https://example.com/x.png",
    "research:../../etc", "data:image/png;base64,AAAA",
])
def test_image_src_accepts_only_research_refs(src):
    spec = {"elements": [{"id": "i", "type": "image", "src": src, "at": [0, 0], "size": [200, 200]}]}
    with pytest.raises(SpecError):
        compile_spec(spec, images={"graph": {"data_uri": "data:image/png;base64,AA", "citation": "c"}})


def test_citation_comes_from_research_not_spec():
    spec = {"elements": [{"id": "i", "type": "image", "src": "research:graph", "at": [0, 0], "size": [400, 300],
                          "label": "spec-supplied text"}]}
    compiled = compile_spec(spec, images={"graph": {"data_uri": "data:image/png;base64,AA",
                                                    "citation": '"Graph" — Jane Doe, CC-BY-SA 4.0, https://x'}})
    html = compiled.html_by_step[0]
    assert "Jane Doe, CC-BY-SA 4.0" in html


def test_image_without_citation_is_refused():
    spec = {"elements": [{"id": "i", "type": "image", "src": "research:graph", "at": [0, 0], "size": [400, 300]}]}
    with pytest.raises(SpecError, match="citation"):
        compile_spec(spec, images={"graph": {"data_uri": "data:image/png;base64,AA", "citation": ""}})


def test_research_result_pointing_at_non_image_fails(tmp_path):
    bogus = tmp_path / "notes.txt"
    bogus.write_text("not an image")
    job = SimpleNamespace(
        job_id="test_bogus_research", diagram_specs=_SPEC,
        research_result={"images": {"graph": {"local_path": str(bogus), "citation": "c"}}},
    )
    result = DiagramRendererWorker(out_dir=str(tmp_path)).execute(job)
    assert not result.success
    assert "not a decodable image" in result.error
