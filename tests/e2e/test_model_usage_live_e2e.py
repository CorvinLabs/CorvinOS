"""E2E — live model-usage signal (ADR-0851) through its real call site.

Drives ``engine_span.emit_end`` (the emitter the OS, console, AWP and A2A paths
share) and checks what a local stats endpoint actually receives: one POST per
finished span that reported a model, with the summed four-way token split.

The transport is a local plain-HTTP server. The production opener refuses
``http://`` on purpose (credentials are never sent in plaintext), so the test
swaps only ``_open_no_redirect`` for ``urlopen``; the guard itself is covered by
``test_production_opener_refuses_plaintext``.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SHARED = _REPO / "corvin_operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


class _Recorder(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        _Recorder.received.append(json.loads(self.rfile.read(length).decode("utf-8")))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):  # silence the test server
        pass


@pytest.fixture
def stats_server():
    _Recorder.received = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/api/telemetry/model-usage"
    server.shutdown()
    server.server_close()


@pytest.fixture
def sandbox(monkeypatch, tmp_path, stats_server):
    home = tmp_path / "corvin_home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_MODEL_USAGE_LIVE", "1")
    import model_usage_live  # type: ignore  # noqa: PLC0415
    import core.console.corvin_core.aco.htrace_consent as consent  # noqa: PLC0415

    monkeypatch.setattr(model_usage_live, "_endpoint", lambda: stats_server)
    monkeypatch.setattr(
        consent, "_open_no_redirect",
        lambda req, timeout: urllib.request.urlopen(req, timeout=timeout),
    )
    return home


def _join_sends() -> None:
    for t in list(threading.enumerate()):
        if t.name == "model-usage-live":
            t.join(timeout=10)


def _emit(**over):
    import engine_span  # type: ignore  # noqa: PLC0415

    calls: list = []
    kw = {
        "span_id": "spn_test", "role": "os", "engine_id": "claude_code",
        "model_id": "claude-haiku-5-5", "status": "ok",
        "input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 2, "cache_write_tokens": 1,
    }
    kw.update(over)
    engine_span.emit_end(lambda *a, **k: calls.append((a, k)), **kw)
    return calls


def test_each_finished_span_is_posted_as_it_closes_and_sums_match(sandbox):
    _emit(span_id="spn_1")
    _emit(span_id="spn_2", model_id="claude-opus-5-5", input_tokens=100, output_tokens=40)
    _join_sends()

    received = _Recorder.received
    assert len(received) == 2
    assert all(r["event_type"] == "model_usage" for r in received)
    assert {r["model_id"] for r in received} == {"claude-haiku-5-5", "claude-opus-5-5"}

    total = sum(r["input_tokens"] + r["output_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"]
                for r in received)
    assert total == (10 + 5 + 2 + 1) + (100 + 40 + 2 + 1)


def test_state_file_counts_sends_and_never_holds_the_payload(sandbox):
    import model_usage_live  # type: ignore  # noqa: PLC0415

    _emit(span_id="spn_a")
    _emit(span_id="spn_b", model_id="claude-opus-5-5")
    _join_sends()

    state = model_usage_live.read_state(sandbox)
    assert state["attempts"] == 2 and state["successes"] == 2
    assert state["consecutive_failures"] == 0 and state["last_detail"] == "http 200"
    raw = model_usage_live.state_path(sandbox).read_text(encoding="utf-8")
    assert "claude-" not in raw and "tokens" not in raw


def test_span_without_reported_model_is_not_sent(sandbox):
    _emit(span_id="spn_nomodel", model_id="")
    _join_sends()
    assert _Recorder.received == []


def test_opt_out_in_tenant_config_sends_nothing(sandbox):
    cfg_dir = sandbox / "tenants" / "_default" / "global"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "tenant.corvin.yaml").write_text(
        "spec:\n  telemetry:\n    ping_enabled: false\n", encoding="utf-8"
    )
    _emit(span_id="spn_optout")
    _join_sends()
    assert _Recorder.received == []


def test_emit_still_writes_audit_when_send_fails(sandbox, monkeypatch, stats_server):
    import model_usage_live  # type: ignore  # noqa: PLC0415
    monkeypatch.setattr(model_usage_live, "_endpoint", lambda: "http://127.0.0.1:1/unreachable")
    calls = _emit(span_id="spn_down")
    _join_sends()
    assert len(calls) == 1
    assert calls[0][0][0] == "engine.span.end"


def test_production_opener_refuses_plaintext(monkeypatch, tmp_path, stats_server):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_MODEL_USAGE_LIVE", "1")
    import model_usage_live  # type: ignore  # noqa: PLC0415
    monkeypatch.setattr(model_usage_live, "_endpoint", lambda: stats_server)
    _emit(span_id="spn_plain")
    _join_sends()
    assert _Recorder.received == []
