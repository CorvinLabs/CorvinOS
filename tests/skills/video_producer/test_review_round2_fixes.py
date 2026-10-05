"""Proofs for the fixes after the 2026-10-05 refutation round (round 2).

Each test replays a break the refutation reviewer demonstrated against the
round-1 fixes. No network, no paid TTS.
"""
import base64
import io
import json
import os
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from core.skills.video_producer.hedges import find_hedge
from core.skills.video_producer.maestro import MaestroOrchestrator, VideoJobPhase
from core.skills.video_producer.workers import job_tmp
from core.skills.video_producer.workers.asset_analyzer import AssetAnalyzerWorker
from core.skills.video_producer.workers.diagram.compiler import SpecError, compile_spec
from core.skills.video_producer.workers.video_assembler import VideoAssemblerWorker
from core.skills.video_producer.workers import voice_synthesizer as vs

GOOD = ["This scene describes the pipeline and is long enough to pass every content gate."]


# ── voice: the provider label is not proof of audio ─────────────────────────

class _Resp:
    status = 200

    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_openai_200_with_json_body_is_not_audio(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(vs.urllib.request, "urlopen", lambda req, timeout=30: _Resp(b'{"error": "nope"}'))
    w = vs.VoiceSynthesizerWorker()
    assert w._synthesize_scene_openai("hello", str(tmp_path / "a.mp3")) is False


def test_non_audio_file_fails_voice_even_with_a_real_provider_label(monkeypatch, tmp_path):
    bogus = tmp_path / "scene.mp3"
    bogus.write_text(json.dumps({"not": "audio"}))
    w = vs.VoiceSynthesizerWorker()
    monkeypatch.setattr(w, "_synthesize_scene_real", lambda text, job_id, scene_index: (str(bogus), "openai-tts"))
    r = w.execute(SimpleNamespace(job_id="rv2_voice", narration=GOOD))
    assert r.success is False and r.loudness_lufs is None


def test_normalising_a_non_audio_file_is_not_normalised(tmp_path):
    bogus = tmp_path / "x.mp3"
    bogus.write_text("{}")
    assert vs.VoiceSynthesizerWorker()._normalize_loudness_ffmpeg([str(bogus)], -23) is False


# ── hedges: whole words, EN + DE, no ordinary technical phrases ─────────────

@pytest.mark.parametrize("text", ["The AI thinks before it acts.", "Your AI guesses less.",
                                  "An improbably large file.", "The model could be swapped.",
                                  "This could become the default."])
def test_legitimate_sentences_are_not_hedges(text):
    assert find_hedge(text) is None


@pytest.mark.parametrize("text", ["I think it works.", "Das ist vielleicht so.", "Es ist vermutlich schneller.",
                                  "It seems fine."])
def test_real_hedges_are_found(text):
    assert find_hedge(text)


# ── Maestro: narration frozen after analysis, job id re-checked ─────────────

class _Voice:
    def __init__(self):
        self.heard = None

    def execute(self, job):
        self.heard = list(job.narration)
        return {"success": True, "audio_files": [], "total_duration_seconds": 1.0}


def test_narration_changed_after_analysis_never_reaches_the_voice():
    voice = _Voice()
    m = MaestroOrchestrator()
    m.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker)
    m.register_worker(VideoJobPhase.VOICE, voice)
    jid = m.create_job("t", 10, "technical", GOOD, job_id="rv2_frozen")
    m.execute_phase(jid)
    m.jobs[jid].narration = ["I think it is probably maybe broken, says the swapped text."]
    with pytest.raises(RuntimeError, match="Phase gate failed for VOICE"):
        m.execute_phase(jid)
    assert voice.heard is None


def test_job_id_mutated_after_creation_is_refused(tmp_path):
    m = MaestroOrchestrator()
    m.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker)
    jid = m.create_job("t", 10, "technical", GOOD, job_id="rv2_mutated")
    m.jobs[jid].job_id = f"../{tmp_path.name}/ESCAPED"
    with pytest.raises(RuntimeError, match="changed or is unsafe"):
        m.execute_phase(jid)


@pytest.mark.parametrize("bad", ["x/../outside/y", "../a", "", None, "a" * 65])
def test_job_tmp_primitive_refuses_unsafe_ids(bad):
    with pytest.raises(ValueError):
        job_tmp.job_scoped_dir(bad)


# ── Assembler ───────────────────────────────────────────────────────────────

def test_zero_narration_is_not_a_video():
    r = VideoAssemblerWorker().execute(SimpleNamespace(
        job_id="rv2_zero", voice_result={"audio_files": ["a.mp3"], "total_duration_seconds": 0.0},
        screenshots_result={"screenshots": ["f.png"]}))
    assert r.success is False


# ── Compiler: captions that need more room than the average estimate ───────

_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"


@pytest.mark.parametrize("citation", [
    "「東京タワーの夜景」 — 山田太郎, CC-BY-SA 4.0, https://commons.wikimedia.org/wiki/File:東京タワー.jpg",
    "WWWWWWWWWW MMMMMMMMMM WWWWWWWWWW MMMMMMMMMM WWWWWWWWWW — CC-BY 4.0, https://commons.wikimedia.org/",
    '"x" — y, CC0, https://commons.wikimedia.org/wiki/File:%E6%9D%B1%E4%BA%AC%E3%82%BF%E3%83%AF%E3%83%BC%E3%81%AE.jpg',
])
def test_caption_is_never_clipped(citation):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    spec = {"elements": [{"id": "i", "type": "image", "src": "research:r", "at": [100, 100], "size": [330, 400]}]}
    try:
        html = compile_spec(spec, images={"r": {"data_uri": _PNG, "citation": citation}}).html_by_step[0]
    except SpecError:
        return  # refusing the layout is also correct; clipping is not
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            page = b.new_page(viewport={"width": 1920, "height": 1080})
            page.set_content(html)
            box = page.evaluate("""() => {
              const im = document.querySelector('.image').getBoundingClientRect();
              const c = document.querySelector('.caption');
              const r = c.getBoundingClientRect();
              return {imBottom: im.bottom, capBottom: r.bottom, overflow: c.scrollHeight - c.clientHeight};
            }""")
        finally:
            b.close()
    assert box["overflow"] <= 0, box
    assert box["capBottom"] <= box["imBottom"] + 0.5, box


def test_overlapping_images_are_refused():
    els = [{"id": "a", "type": "image", "src": "research:r", "at": [100, 100], "size": [600, 400]},
           {"id": "b", "type": "image", "src": "research:r", "at": [300, 430], "size": [600, 400]}]
    with pytest.raises(SpecError, match="overlap"):
        compile_spec({"elements": els}, images={"r": {"data_uri": _PNG, "citation": "c"}})


# ── Renderer: palette line art keeps its grey levels ────────────────────────

def test_palette_line_art_is_resampled_in_colour(tmp_path):
    from core.skills.video_producer.workers.diagram_renderer import _research_images_for
    img = Image.new("P", (3200, 2400), 0)
    img.putpalette([255, 255, 255] + [0, 0, 0] + [0] * 762)
    d = ImageDraw.Draw(img)
    for x in range(0, 3200, 37):
        d.line([(x, 0), (x, 2399)], fill=1, width=1)
    path = tmp_path / "lines.png"
    img.save(path)
    job = SimpleNamespace(research_result={"images": {"r": {"local_path": str(path), "citation": "c"}}})
    uri = _research_images_for(job)["r"]["data_uri"]
    out = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("L")
    levels = {v for v in out.getdata()}
    # Nearest-neighbour on the palette image kept only pure black/white;
    # resampling in colour produces intermediate greys where lines thin out.
    assert any(10 < v < 245 for v in levels), f"no intermediate greys: {sorted(levels)}"


# ── Research: attribution required but no author ────────────────────────────

def test_attribution_required_without_author_is_skipped(monkeypatch):
    from core.skills.video_producer.workers import research_worker as rw
    page = {"index": 1, "title": "File:x.png", "imageinfo": [{
        "url": "https://upload.wikimedia.org/x.png", "descriptionurl": "https://commons.wikimedia.org/wiki/File:x.png",
        "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 3.0"}, "AttributionRequired": {"value": "true"},
                        "Artist": {"value": ""}}}]}
    monkeypatch.setattr(rw, "_http_get_json", lambda url, timeout=15.0: {"query": {"pages": {"1": page}}})
    assert rw.search_wikimedia_commons("x") == []
