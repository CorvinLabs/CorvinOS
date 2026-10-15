"""E2E: the ``turn_usage`` stream event carries the CONFIRMED model and the RAW usage.

``chat_runtime.stream_turn`` runs against a REAL subprocess (``CORVIN_CLAUDE_BIN``) that
replays a derived claude stream-json file written into the test's tmp dir: a system
``init`` (with or without ``model``), one assistant text, and a ``result`` (with or
without ``usage``). Asserted on the events the browser receives.

Nothing is estimated: the model comes from the ``init`` event only, the token numbers are
the ``result.usage`` fields verbatim, and a missing usage object yields ``usage: None``.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests",
           _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

CONFIRMED = "claude-haiku-4-5-20251001"
USAGE = {
    "input_tokens": 1234,
    "output_tokens": 56,
    "cache_read_input_tokens": 7000,
    "cache_creation_input_tokens": 89,
}


def _stream(*, init_model: str | None, usage: dict | None) -> list[str]:
    init = {"type": "system", "subtype": "init", "session_id": "turn-usage-test"}
    if init_model is not None:
        init["model"] = init_model
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "done", "session_id": "turn-usage-test"}
    if usage is not None:
        result["usage"] = usage
    lines = [
        init,
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "done"}]}, "session_id": "turn-usage-test"},
        result,
    ]
    return [json.dumps(line) for line in lines]


def _sandbox(tmp_path, monkeypatch) -> None:
    """Hermetic env for a turn: sandboxed home, dummy credential, benign house rules."""
    home = tmp_path / "corvin"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_OS_ENGINE", "claude_code")
    # Hermetic auth: no ~/.claude credentials in the sandbox; a dummy key satisfies the probe.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy-not-a-credential")
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)

    import house_rules as _hr  # type: ignore  # noqa: PLC0415
    monkeypatch.setattr(_hr, "_house_rules_classifier",
                        lambda task, rules, auth, **kw: ("", 1.0, "test-benign"))


def _replay(tmp_path, monkeypatch, lines: list[str]) -> None:
    """Point the fake claude CLI at a stream-json fixture replaying ``lines``."""
    fixture = tmp_path / "stream.jsonl"
    fixture.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, value in kit.fake_env(tmp_path, fixture, speedup=20.0).items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def run_turn(tmp_path, monkeypatch):
    """Run one chat turn against a fake claude replaying ``lines``; return its events."""
    _sandbox(tmp_path, monkeypatch)

    from corvin_console import chat_runtime as cr

    def _run(lines: list[str]) -> list[dict]:
        _replay(tmp_path, monkeypatch, lines)
        sess = cr.create_session("_default")

        async def _collect():
            return [ev async for ev in cr.stream_turn(sess, "say done")]

        return asyncio.run(_collect())

    return _run


@pytest.fixture
def ws_turn(tmp_path, monkeypatch):
    """Run one chat turn over the REAL chat WebSocket route; return every wire event.

    Only the session-auth lookup and the license gate are stubbed (no login in a test);
    the route, ``stream_turn`` and the fake claude subprocess are real.
    """
    _sandbox(tmp_path, monkeypatch)

    from unittest.mock import MagicMock, patch  # noqa: PLC0415

    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from corvin_console import chat_runtime as cr  # noqa: PLC0415
    from corvin_console.routes import chat as chat_routes  # noqa: PLC0415

    rec = MagicMock()
    rec.tenant_id = "_default"
    rec.sid_fingerprint = "fp-turn-usage"

    def _run(lines: list[str]) -> list[dict]:
        _replay(tmp_path, monkeypatch, lines)
        sess = cr.create_session("_default")
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=rec),
            patch("corvin_console.routes._compute_license_gate.enforce_chat_turns",
                  lambda *a, **k: None),
        ):
            client = TestClient(app)
            client.cookies.set("corvin_console_sid", "valid-sid")
            wire: list[dict] = []
            with client.websocket_connect(f"/v1/console/chat/sessions/{sess.sid}/stream") as ws:
                assert ws.receive_json()["type"] == "ready"
                ws.send_json({"type": "user", "text": "say done"})
                while True:
                    ev = ws.receive_json()
                    wire.append(ev)
                    if ev.get("type") in ("done", "error"):
                        break
        return wire

    return _run


def _turn_usage_events(events: list[dict]) -> list[dict]:
    return [ev for ev in events if ev.get("type") == "turn_usage"]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process semantics in the harness")
def test_confirmed_model_and_raw_usage_are_emitted_once(run_turn):
    events = run_turn(_stream(init_model=CONFIRMED, usage=USAGE))

    got = _turn_usage_events(events)
    assert len(got) == 1, got
    assert got[0]["model"] == CONFIRMED
    assert got[0]["usage"] == USAGE


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process semantics in the harness")
def test_turn_without_usage_object_reports_none_not_an_estimate(run_turn):
    events = run_turn(_stream(init_model=CONFIRMED, usage=None))

    got = _turn_usage_events(events)
    assert len(got) == 1, got
    assert got[0]["model"] == CONFIRMED
    assert got[0]["usage"] is None


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process semantics in the harness")
def test_partial_usage_reports_missing_fields_as_none_not_zero(run_turn):
    # The CLI reported only output_tokens. A 0 for the missing counts would be a
    # measured-looking zero the UI cannot tell apart from a real one (RT-05).
    events = run_turn(_stream(init_model=CONFIRMED, usage={"output_tokens": 5}))

    got = _turn_usage_events(events)
    assert len(got) == 1, got
    assert got[0]["usage"] == {
        "input_tokens": None,
        "output_tokens": 5,
        "cache_read_input_tokens": None,
        "cache_creation_input_tokens": None,
    }


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process semantics in the harness")
def test_missing_init_model_is_not_estimated(run_turn):
    events = run_turn(_stream(init_model=None, usage=USAGE))

    got = _turn_usage_events(events)
    assert len(got) == 1, got
    # No confirmed model -> nothing is estimated: no model string, the raw usage stays.
    assert got[0]["model"] in (None, ""), got
    assert got[0]["usage"] == USAGE


# --- Over the wire: the same three cases through the REAL chat WebSocket route. ---

@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process semantics in the harness")
def test_ws_confirmed_model_and_raw_usage_reach_the_socket(ws_turn, tmp_path, monkeypatch):
    wire = ws_turn(_stream(init_model=CONFIRMED, usage=USAGE))

    got = _turn_usage_events(wire)
    assert len(got) == 1, wire
    assert got[0]["model"] == CONFIRMED
    assert got[0]["usage"] == USAGE


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process semantics in the harness")
def test_ws_turn_without_usage_object_sends_usage_none(ws_turn, tmp_path, monkeypatch):
    wire = ws_turn(_stream(init_model=CONFIRMED, usage=None))

    got = _turn_usage_events(wire)
    assert len(got) == 1, wire
    assert got[0]["model"] == CONFIRMED
    assert got[0]["usage"] is None


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process semantics in the harness")
def test_ws_missing_init_model_sends_model_none_not_the_requested_one(ws_turn, tmp_path, monkeypatch):
    wire = ws_turn(_stream(init_model=None, usage=USAGE))

    got = _turn_usage_events(wire)
    assert len(got) == 1, wire
    assert got[0]["model"] in (None, ""), wire
    assert got[0]["usage"] == USAGE
