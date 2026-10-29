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


# --- The writers that put their span end on their own chain (ADR-2286, 2026-10-29) ---
# Each one used to write ``engine.span.end`` without the live signal, so gateway,
# delegate, task-pool and ACS worker runs never reached the public ranking.

def test_task_worker_pool_span_end_sends_the_reported_model(sandbox, tmp_path):
    from core.console.corvin_console import task_worker_pool as twp  # noqa: PLC0415

    result_frame = {
        "type": "result", "result": "done",
        "modelUsage": {"claude-sonnet-5-5": {"outputTokens": 7}},
        "usage": {"input_tokens": 11, "output_tokens": 7,
                  "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2},
    }
    audit = twp._audit_path_for_tenant("_default")
    audit.parent.mkdir(parents=True, exist_ok=True)
    twp._emit_task_engine_span("end", audit, task_id="t-live", status="ok",
                               duration_ms=12, result_frame=result_frame)
    _join_sends()

    assert len(_Recorder.received) == 1
    rec = _Recorder.received[0]
    assert rec["model_id"] == "claude-sonnet-5-5" and rec["role"] == "worker"
    assert (rec["input_tokens"], rec["output_tokens"],
            rec["cache_read_tokens"], rec["cache_write_tokens"]) == (11, 7, 3, 2)
    # The audit record carries the same reported model, not a blank.
    chain = audit.read_text(encoding="utf-8")
    assert "engine.span.end" in chain and "claude-sonnet-5-5" in chain


def test_task_worker_pool_span_without_result_frame_is_not_sent(sandbox):
    from core.console.corvin_console import task_worker_pool as twp  # noqa: PLC0415

    audit = twp._audit_path_for_tenant("_default")
    audit.parent.mkdir(parents=True, exist_ok=True)
    twp._emit_task_engine_span("end", audit, task_id="t-none", status="error",
                               duration_ms=1)
    _join_sends()
    assert _Recorder.received == []


def test_gateway_dispatcher_span_end_sends(sandbox):
    from core.gateway.corvin_gateway import dispatcher as disp  # noqa: PLC0415

    audited: list = []

    class _Writer:
        _usage_split = staticmethod(disp.RunDispatcher._usage_split)

        def _audit_policy(self, event_type, **kw):
            audited.append((event_type, kw))

    disp.RunDispatcher._emit_engine_span(
        _Writer(), "end", tenant_id="_default", run_id="r-live",
        engine_id="claude_code", model_id="claude-opus-5-5", status="ok",
        duration_ms=5, usage={"input_tokens": 4, "output_tokens": 2})
    _join_sends()

    assert [e for e, _ in audited] == ["engine.span.end"]
    assert len(_Recorder.received) == 1
    assert _Recorder.received[0]["model_id"] == "claude-opus-5-5"
    assert _Recorder.received[0]["input_tokens"] == 4


def test_delegate_span_end_sends_the_reported_model_not_the_requested(sandbox, monkeypatch, tmp_path):
    from corvin_delegate import delegation as dlg  # noqa: PLC0415

    sess = tmp_path / "sess"
    sess.mkdir()
    monkeypatch.setenv("CORVIN_SESSION_DIR", str(sess))
    dlg._write_wdat_run_for_delegation(
        engine="claude_code", model="claude-haiku-5-5", duration_ms=9,
        start_wall=1_000_000.0, prompt="p",
        usage={"input_tokens": 6, "output_tokens": 3,
               "cache_read_input_tokens": 1, "cache_creation_input_tokens": 0},
        reported_model="claude-sonnet-5-5",
    )
    _join_sends()

    assert len(_Recorder.received) == 1
    rec = _Recorder.received[0]
    assert rec["model_id"] == "claude-sonnet-5-5"
    assert (rec["input_tokens"], rec["output_tokens"], rec["cache_read_tokens"]) == (6, 3, 1)


def test_delegate_without_reported_model_sends_nothing(sandbox, monkeypatch, tmp_path):
    from corvin_delegate import delegation as dlg  # noqa: PLC0415

    sess = tmp_path / "sess"
    sess.mkdir()
    monkeypatch.setenv("CORVIN_SESSION_DIR", str(sess))
    dlg._write_wdat_run_for_delegation(
        engine="claude_code", model="claude-haiku-5-5", duration_ms=9,
        start_wall=1_000_000.0, prompt="p", usage={},
    )
    _join_sends()
    assert _Recorder.received == []


def test_acs_worker_ok_span_end_hands_the_audited_dict_to_the_sender():
    """Call-site pin: the ACS worker's ok-path end span runs inside a real engine
    spawn, which this suite cannot start. The pin fails if the hand-off is
    dropped or sent from a different dict than the one audited."""
    src = (_SHARED / "acs_runtime.py").read_text(encoding="utf-8")
    write = "_write_audit(ctx.tenant_id, _espan.ENGINE_SPAN_END, _worker_end)"
    send = "_espan.send_live_model_usage(_worker_end)"
    assert write in src and send in src
    assert src.index(write) < src.index(send)
