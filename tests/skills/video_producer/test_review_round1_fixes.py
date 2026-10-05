"""Proofs for the fixes from the 2026-10-05 adversarial review (round 1).

Each test reproduces a reviewed failure and asserts the fixed behaviour.
No network, no paid TTS: voice is stubbed where speech is not the subject.
"""
import os
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image

from core.skills.video_producer.maestro import MaestroOrchestrator, VideoJobPhase
from core.skills.video_producer.workers.asset_analyzer import AssetAnalyzerWorker
from core.skills.video_producer.workers.diagram.compiler import SpecError, compile_spec
from core.skills.video_producer.workers.diagram.raster import render_compiled
from core.skills.video_producer.workers.video_assembler import VideoAssemblerWorker
from core.skills.video_producer.workers.youtube_uploader import YouTubeUploaderWorker

requires_ffmpeg = pytest.mark.skipif(os.system("which ffmpeg > /dev/null 2>&1") != 0, reason="needs ffmpeg")

GOOD = ["This scene describes the pipeline and is long enough to pass every content gate."]


class _Returns:
    def __init__(self, value):
        self.value = value

    def execute(self, job):
        return self.value


def _maestro(analysis=AssetAnalyzerWorker, voice=None):
    m = MaestroOrchestrator()
    m.register_worker(VideoJobPhase.ANALYSIS, analysis)
    if voice is not None:
        m.register_worker(VideoJobPhase.VOICE, voice)
    return m


# ── Maestro ─────────────────────────────────────────────────────────────────

class _UnsourcedAnalyzer(AssetAnalyzerWorker):
    """The real analyzer with a source check that fails, so status is FAIL."""

    def _verify_sources(self, facts):
        return False


def test_analysis_fail_stops_the_job():
    m = _maestro(analysis=_UnsourcedAnalyzer)
    jid = m.create_job("t", 10, "technical", GOOD, job_id="rv_analysis_fail")
    with pytest.raises(RuntimeError, match="ANALYSIS failed"):
        m.execute_phase(jid)
    assert m.jobs[jid].current_phase == VideoJobPhase.ANALYSIS


@pytest.mark.parametrize("result", [None, {}, {"audio_files": []}, SimpleNamespace(x=1), {"success": "yes"}])
def test_result_without_explicit_success_stops_the_job(result):
    m = _maestro(analysis=_Returns(result))
    jid = m.create_job("t", 10, "technical", GOOD, job_id="rv_no_success")
    with pytest.raises(RuntimeError):
        m.execute_phase(jid)
    assert m.jobs[jid].current_phase == VideoJobPhase.ANALYSIS


@pytest.mark.parametrize("bad", ["../tmp/x", "a/b", "x" * 65, "a b"])
def test_unsafe_job_id_is_refused(bad):
    with pytest.raises(ValueError):
        MaestroOrchestrator().create_job("t", 10, "technical", GOOD, job_id=bad)


def test_duplicate_job_id_is_refused():
    m = MaestroOrchestrator()
    m.create_job("t", 10, "technical", GOOD, job_id="rv_dup")
    with pytest.raises(ValueError, match="already exists"):
        m.create_job("t", 10, "technical", GOOD, job_id="rv_dup")


def test_blanked_narration_after_create_fails_the_analysis_gate():
    m = _maestro()
    jid = m.create_job("t", 10, "technical", GOOD, job_id="rv_blank")
    m.jobs[jid].narration = ["", "   "]
    with pytest.raises(RuntimeError, match="Phase gate failed for ANALYSIS"):
        m.execute_phase(jid)


def test_assembly_gate_needs_frames():
    m = MaestroOrchestrator()
    jid = m.create_job("t", 10, "technical", GOOD, job_id="rv_no_frames")
    job = m.jobs[jid]
    job.current_phase = VideoJobPhase.ASSEMBLY
    job.screenshots_result = {"success": True, "screenshots": []}
    assert m._validate_assembly_phase(job) is False


# ── Assembler ───────────────────────────────────────────────────────────────

def test_assembler_refuses_audio_only():
    r = VideoAssemblerWorker().execute(
        SimpleNamespace(job_id="rv_audio_only", voice_result={"audio_files": ["x.mp3"], "total_duration_seconds": 6},
                        screenshots_result={"screenshots": []}))
    assert r.success is False


@requires_ffmpeg
def test_assembler_times_frames_per_scene(tmp_path):
    audio = []
    for i, dur in enumerate((2.0, 6.0)):
        a = tmp_path / f"s{i}.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=440:d={dur}", str(a)], check=True)
        audio.append(str(a))
    frames = []
    for i, color in enumerate(((220, 30, 30), (30, 30, 220))):
        f = tmp_path / f"f{i}.png"
        Image.new("RGB", (1920, 1080), color).save(f)
        frames.append(str(f))
    job = SimpleNamespace(
        job_id="rv_per_scene",
        voice_result={"audio_files": audio, "total_duration_seconds": 8.0},
        screenshots_result={"screenshots": frames, "frames_by_scene": [[frames[0]], [frames[1]]]},
    )
    r = VideoAssemblerWorker().execute(job)
    assert r.success and abs(r.duration_seconds - 8.0) < 0.6
    probe = tmp_path / "probe.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "2.6", "-i", r.video_path, "-frames:v", "1", str(probe)],
                   check=True)
    # Scene 2's voice starts at 2.0 s; with the old even spread its picture
    # would not appear before 4.0 s.
    r_, g_, b_ = Image.open(probe).convert("RGB").getpixel((960, 540))
    assert b_ > r_, (r_, g_, b_)


# ── YouTube ─────────────────────────────────────────────────────────────────

def test_youtube_upload_fails_honestly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    job = SimpleNamespace(job_id="rv_yt", topic="t", duration_seconds=10, audience="technical",
                          narration=GOOD, video_result={"video_path": "/nonexistent.mp4"})
    r = YouTubeUploaderWorker().execute(job)
    assert r.success is False and r.url == "" and r.published is False


# ── Voice ───────────────────────────────────────────────────────────────────

def test_voice_mock_is_a_failure(monkeypatch):
    from core.skills.video_producer.workers import voice_synthesizer as vs
    w = vs.VoiceSynthesizerWorker()
    monkeypatch.setattr(w, "_synthesize_scene_real", lambda text, job_id, scene_index: ("/tmp/none.mp3", "mock"))
    r = w.execute(SimpleNamespace(job_id="rv_mock_voice", narration=GOOD))
    assert r.success is False and "mock" in r.provider_used


# ── Compiler: the citation cannot be cut off, covered or pushed off-canvas ─

_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
_CIT = '"A deliberately long Commons file title for wrapping" — Some Photographer Name, CC-BY-SA 4.0, https://commons.wikimedia.org/wiki/File:Example_with_a_long_name.jpg'


def _img(**kw):
    el = {"id": "i", "type": "image", "src": "research:r", "at": [100, 100], "size": [800, 600]}
    el.update(kw)
    return el


def test_caption_wraps_and_is_drawn_last():
    spec = {"elements": [{"id": "b", "type": "box", "label": "covering box", "at": [100, 640]}, _img()]}
    html = compile_spec(spec, images={"r": {"data_uri": _PNG, "citation": _CIT}}).html_by_step[0]
    assert "ellipsis" not in html and "nowrap" not in html
    body = html.split("<body>", 1)[1]
    assert body.index('class="image') > body.index('class="box')
    assert "Example_with_a_long_name.jpg" in html


@pytest.mark.parametrize("bad", [dict(at=[1500, 100]), dict(at=[100, 700]), dict(at=[-10, 0]),
                                 dict(size=[200, 600])])
def test_image_off_canvas_or_too_narrow_is_refused(bad):
    with pytest.raises(SpecError):
        compile_spec({"elements": [_img(**bad)]}, images={"r": {"data_uri": _PNG, "citation": _CIT}})


def test_too_many_caption_lines_is_refused():
    with pytest.raises(SpecError, match="too narrow"):
        compile_spec({"elements": [_img(size=[320, 600])]}, images={"r": {"data_uri": _PNG, "citation": _CIT * 3}})


def test_data_uri_appears_once_per_step():
    els = [_img(id=f"i{k}", at=[20 + k * 620, 100], size=[600, 400]) for k in range(3)]
    html = compile_spec({"elements": els}, images={"r": {"data_uri": _PNG, "citation": "c"}}).html_by_step[0]
    assert html.count(_PNG) == 1


def test_image_element_count_is_capped():
    els = [_img(id=f"i{k}", at=[0, 0], size=[400, 200]) for k in range(13)]
    with pytest.raises(SpecError, match="image elements"):
        compile_spec({"elements": els}, images={"r": {"data_uri": _PNG, "citation": "c"}})


def test_rendered_caption_is_visible_over_a_box(tmp_path):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    spec = {"elements": [_img(), {"id": "b", "type": "box", "label": "covering box", "at": [100, 650]}]}
    comp = compile_spec(spec, images={"r": {"data_uri": _PNG, "citation": _CIT}})
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            frame = render_compiled(browser, comp, {"w": 1920, "h": 1080}, tmp_path)[0]
        finally:
            browser.close()
    # Caption strip: bottom of the image box (two lines at 800 px width).
    crop = Image.open(frame).convert("RGB").crop((110, 652, 890, 698))
    muted = sum(1 for px in crop.getdata() if abs(px[0] - 0x9A) < 30 and abs(px[2] - 0xAE) < 30)
    assert muted > 200, f"caption text not visible ({muted} text pixels)"
