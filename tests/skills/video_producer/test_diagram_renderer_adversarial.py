"""Adversarial regression proofs for the diagram renderer (review 2026-10-03).

Each test reproduces a finding with the input the reviewer used and asserts
the fixed behaviour. Real Chromium, a real local HTTP listener counting hits,
real MaestroOrchestrator — no mocks of the component under test.
"""
from __future__ import annotations

import http.server
import threading
import time
from pathlib import Path

import pytest

from core.skills.video_producer.maestro import MaestroOrchestrator, VideoJobPhase
from core.skills.video_producer.workers.diagram.compiler import Compiled, SpecError, compile_spec
from core.skills.video_producer.workers.diagram_renderer import DiagramRendererWorker

BOX = {"id": "x", "type": "box", "label": "X", "at": [100, 100]}


def _spec(*extra, **top):
    return {"elements": [BOX, *extra], **top}


class _Counter(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def do_GET(self):  # noqa: N802
        _Counter.hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def listener():
    _Counter.hits = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Counter)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


# ── C1: injection ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"id": "h", "type": "highlight", "target": "x",
     "color": 'red;"></div><script>fetch("http://127.0.0.1:1/x")</script><div style="'},
    {"id": "h", "type": "highlight", "target": "x", "color": "javascript:alert(1)"},
])
def test_highlight_color_injection_is_refused(bad):
    with pytest.raises(SpecError, match="#RRGGBB"):
        compile_spec(_spec(bad))


def test_grid_opacity_injection_is_refused():
    bad = {"id": "g", "type": "grid", "opacity": '1"/><foreignObject><img src="http://127.0.0.1:1/g"/></foreignObject><g x="'}
    with pytest.raises(SpecError, match="opacity"):
        compile_spec(_spec(bad))


def test_browser_runs_no_script_and_makes_no_request_even_for_hostile_html(listener, tmp_path):
    """Defence in depth: if hostile markup ever reached the page, Chromium must
    neither execute it nor contact any host."""
    from playwright.sync_api import sync_playwright

    from core.skills.video_producer.workers.diagram.raster import render_compiled

    hostile = (
        "<html><body style='background:#123456'>"
        f"<script>fetch('{listener}/script')</script>"
        f"<img src='{listener}/img'><link rel='stylesheet' href='{listener}/css'>"
        "<div style='width:400px;height:300px;background:linear-gradient(red,blue)'></div>"
        "</body></html>"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            # positive control: an UNHARDENED page with the same markup does
            # reach the listener — otherwise "0 hits" below would prove nothing
            plain = browser.new_page()
            plain.set_content(hostile, wait_until="load")
            plain.wait_for_timeout(300)
            plain.close()
            control_hits = list(_Counter.hits)
            _Counter.hits.clear()
            render_compiled(browser, Compiled(html_by_step=[hostile]), {"w": 640, "h": 360}, tmp_path, 1)
        finally:
            browser.close()
    time.sleep(0.3)
    assert {"/script", "/img"} <= set(control_hits), f"positive control failed: {control_hits}"
    assert _Counter.hits == [], f"page reached the network: {_Counter.hits}"


# ── C2: resource exhaustion ──────────────────────────────────────────────────

@pytest.mark.parametrize("spacing", [0, -5, 1e-9])
def test_grid_spacing_cannot_hang(spacing):
    t0 = time.monotonic()
    with pytest.raises(SpecError, match="spacing"):
        compile_spec(_spec({"id": "g", "type": "grid", "spacing": spacing}))
    assert time.monotonic() - t0 < 1.0


def test_unbounded_sizes_are_refused():
    with pytest.raises(SpecError):
        compile_spec(_spec({"id": "g", "type": "grid", "size": [1e6, 1e6], "spacing": 8}))
    with pytest.raises(SpecError, match="canvas"):
        compile_spec(_spec(canvas={"w": 10**6, "h": 1080}))
    with pytest.raises(SpecError, match="steps"):
        compile_spec(_spec(steps=[["x"]] * 2000))


# ── C6 / C5: structural validation instead of raw exceptions ─────────────────

@pytest.mark.parametrize("spec, match", [
    (_spec({"id": "x", "type": "box", "label": "dup", "at": [0, 0]}), "duplicate"),
    (_spec({"id": "a", "type": "arrow", "from": "x.s", "to": "zz.n"}, steps=[["x"]]), "unknown box ids"),
    (_spec(steps=["xy"]), "every step must be a list"),
    (_spec({"id": "i", "type": "box", "label": "I", "at": [0, 0], "icon": "nope"}), "icon"),
    (_spec(canvas={"w": 1920}), "canvas"),
    (_spec({"id": "a", "type": "arrow", "to": "x.n"}), "from"),
    (_spec({"id": "b", "type": "brace", "spans": ["x"], "label": 7}), "label"),
    ({"elements": "abc"}, "elements"),
    (_spec({"id": "y", "type": "box", "label": "Y", "at": ["a", "b"]}), "number"),
])
def test_malformed_specs_raise_specerror(spec, match):
    with pytest.raises(SpecError, match=match):
        compile_spec(spec)


# ── C3 / C4 / C7 through the real worker + maestro ───────────────────────────

def _maestro_at_screenshots(worker, job_id=None):
    class _Ok:
        def __init__(self, name):
            self.name, self.version = name, "noop"

        def execute(self, job):
            return {"success": True}

    m = MaestroOrchestrator()
    m.register_worker(VideoJobPhase.ANALYSIS, _Ok("asset_analyzer"))
    m.register_worker(VideoJobPhase.VOICE, _Ok("voice_synthesizer"))
    m.register_worker(VideoJobPhase.SCREENSHOTS, worker)
    jid = m.create_job(topic="T", duration=10, audience="test",
                       narration=["Enough narration text to pass the content-presence gate."], job_id=job_id)
    m.execute_phase(jid)
    m.execute_phase(jid)
    return m, jid


def test_invalid_spec_stops_the_pipeline_and_writes_nothing(tmp_path):
    w = DiagramRendererWorker(diagram_specs={0: _spec(steps=[["x"], ["nope"]])}, out_dir=str(tmp_path))
    m, jid = _maestro_at_screenshots(w)
    with pytest.raises(RuntimeError, match="SCREENSHOTS failed"):
        m.execute_phase(jid)
    assert m.get_job(jid).current_phase == VideoJobPhase.SCREENSHOTS
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad_id", ["../escaped_dir", "/tmp/abs_escape", "a/b"])
def test_job_id_cannot_escape_out_dir(tmp_path, bad_id):
    out = tmp_path / "out"
    w = DiagramRendererWorker(diagram_specs={0: _spec()}, out_dir=str(out))
    # Maestro now refuses the id when the job is built (VideoJob.JOB_ID_RE);
    # before that the renderer refused it at execute time. Either is fine —
    # what matters is that nothing is written outside out_dir.
    with pytest.raises((ValueError, RuntimeError)):
        m, jid = _maestro_at_screenshots(w, job_id=bad_id)
        m.execute_phase(jid)
    assert not (tmp_path / "escaped_dir").exists()
    assert not Path("/tmp/abs_escape").exists()


def test_default_out_dir_is_private_not_shared_tmp():
    w = DiagramRendererWorker(diagram_specs={0: _spec()})
    assert w.out_dir.name.startswith("diagram_renderer_")
    assert (w.out_dir.stat().st_mode & 0o077) == 0
    w.out_dir.rmdir()


def test_rerender_with_fewer_steps_leaves_no_stale_frames(tmp_path):
    two = _spec({"id": "y", "type": "box", "label": "Y", "right_of": "x"}, steps=[["x"], ["x", "y"]])
    w = DiagramRendererWorker(diagram_specs={0: two}, out_dir=str(tmp_path))

    class _J:
        job_id = "job_rerender"

    assert w.execute(_J()).num_captured == 2
    w.diagram_specs = {0: _spec()}
    r = w.execute(_J())
    assert r.success and r.num_captured == 1
    assert sorted(p.name for p in (tmp_path / "job_rerender" / "scene_00").iterdir()) == ["frame_01.png"]
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")], "staging dir left behind"


def test_json_style_string_scene_keys_are_accepted(tmp_path):
    w = DiagramRendererWorker(diagram_specs={"0": _spec()}, out_dir=str(tmp_path))

    class _J:
        job_id = "job_strkeys"

    r = w.execute(_J())
    assert r.success, r.error
    assert r.num_captured == 1


@pytest.mark.parametrize("bad_id", ["../escaped_dir", "/tmp/abs_escape2", "a/b"])
def test_renderer_itself_refuses_unsafe_job_id(tmp_path, bad_id):
    """The worker keeps its own guard for callers that bypass Maestro."""
    from types import SimpleNamespace
    w = DiagramRendererWorker(diagram_specs={0: _spec()}, out_dir=str(tmp_path / "out"))
    result = w.execute(SimpleNamespace(job_id=bad_id, diagram_specs=None))
    assert not result.success and "safe directory name" in result.error
    assert not (tmp_path / "escaped_dir").exists()
