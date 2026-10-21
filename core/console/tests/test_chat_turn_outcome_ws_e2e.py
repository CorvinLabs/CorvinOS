"""E2E (T-0250): the ``outcome`` of a turn reaches the browser on the ``result`` event.

``chat_runtime.stream_turn`` tags the scope-end result ``ok`` / ``cut`` / ``process_died``
(ADR-2236, P1). This drives two turns through the REAL chat WebSocket route
(``/v1/console/chat/sessions/{sid}/stream``) with the fake claude CLI replaying captured
streams (``bgscope_testkit``) and asserts the field as the wire carries it — the web client
(``chat-registry.ts``) renders it on the turn, so a consumer now exists on both ends:

  * a clean turn: the final result says ``outcome: "ok"``
  * the CLI exiting non-zero with a background child still open: the final result says
    ``outcome: "process_died"`` and its text is the failure notice, not an answer
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests",
           _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402  # fake claude CLI + captured fixtures (ADR-2236)


def _launcher(tmp: Path) -> Path:
    if sys.platform.startswith("win"):
        pytest.skip("POSIX launcher; the Windows shim is covered by the bridge suites")
    bin_dir = tmp / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / "claude"
    launcher.write_text("#!/bin/sh\n" f"exec {sys.executable!r} {str(kit.FAKE)!r} \"$@\"\n", encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return launcher


def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "corvin"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_OS_ENGINE", "claude_code")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy-not-a-credential")
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    import house_rules as _hr  # type: ignore  # noqa: PLC0415
    monkeypatch.setattr(_hr, "_house_rules_classifier",
                        lambda task, rules, auth, **kw: ("", 1.0, "test-benign"))


def _clean_fixture(tmp_path: Path) -> Path:
    lines = [
        {"type": "system", "subtype": "init", "session_id": "oc-ws-e2e"},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "done"}]}, "session_id": "oc-ws-e2e"},
        {"type": "result", "subtype": "success", "is_error": False,
         "result": "done", "session_id": "oc-ws-e2e"},
    ]
    return kit.write_fixture(tmp_path / "fixture-clean", "oc-ws-e2e", lines)


def _process_died_fixture(tmp_path: Path) -> Path:
    """The captured background-Bash stream, cut at its first result with a non-zero EOF:
    the CLI only exits after its children end, so this is a kill or a crash."""
    events = kit.load_fixture("bash_bg_ok")
    cut = next(i for i, e in enumerate(events) if e.get("type") == "result")
    t = events[cut]["_t"]
    events = events[:cut + 1] + [{"type": "_eof", "_t": t + 0.6, "rc": 1}]
    return kit.write_fixture(tmp_path / "fixture-died", "crash_open_child", events)


@pytest.fixture
def ws_turn(tmp_path, monkeypatch):
    """Run one chat turn over the real WebSocket route against *fixture*; return the wire events."""
    _sandbox(tmp_path, monkeypatch)
    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from corvin_console import chat_runtime as cr  # noqa: PLC0415
    from corvin_console.routes import chat as chat_routes  # noqa: PLC0415

    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(_launcher(tmp_path)))
    monkeypatch.setenv("FAKE_CLAUDE_SPEEDUP", "4.0")
    monkeypatch.setenv("FAKE_CLAUDE_SILENT_S", "0.0")
    monkeypatch.setenv("FAKE_CLAUDE_PIDFILE", str(tmp_path / "fake.pid"))
    monkeypatch.setenv("FAKE_CLAUDE_SPAWNLOG", str(tmp_path / "spawns.log"))

    rec = MagicMock()
    rec.tenant_id = "_default"
    rec.sid_fingerprint = "fp-oc-ws-e2e"

    def _run(fixture: Path) -> list[dict]:
        monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(fixture))
        sess = cr.create_session("_default")
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        wire: list[dict] = []
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=rec),
            patch("corvin_console.routes._compute_license_gate.enforce_chat_turns",
                  lambda *a, **k: None),
        ):
            client = TestClient(app)
            client.cookies.set("corvin_console_sid", "valid-sid")
            with client.websocket_connect(f"/v1/console/chat/sessions/{sess.sid}/stream") as ws:
                assert ws.receive_json()["type"] == "ready"
                ws.send_json({"type": "user", "text": "please do it"})
                while True:
                    ev = ws.receive_json()
                    wire.append(ev)
                    if ev.get("type") in ("done", "error"):
                        break
        return wire

    return _run, tmp_path


def _final_results(wire: list[dict]) -> list[dict]:
    return [e for e in wire if e.get("type") == "result" and not e.get("interim")]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process semantics in the harness")
def test_a_clean_turn_carries_outcome_ok_over_the_wire(ws_turn) -> None:
    run, tmp_path = ws_turn
    wire = run(_clean_fixture(tmp_path))
    assert wire[-1]["type"] == "done", wire[-1]
    finals = _final_results(wire)
    assert finals, wire
    assert all(e.get("outcome") == "ok" for e in finals), finals


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process semantics in the harness")
def test_a_killed_turn_carries_outcome_process_died_over_the_wire(ws_turn) -> None:
    run, tmp_path = ws_turn
    wire = run(_process_died_fixture(tmp_path))
    assert wire[-1]["type"] == "done", wire[-1]
    finals = [e for e in _final_results(wire) if e.get("final")]
    assert len(finals) == 1, _final_results(wire)
    assert finals[0]["outcome"] == "process_died", finals[0]
    # Positive control: the text is the failure notice, so a client rendering the outcome
    # has something to explain, and the field is a closed enum on every result.
    assert "background task" in finals[0]["text"]
    assert {e.get("outcome") for e in wire if e.get("type") == "result"} <= {"ok", "cut", "process_died", None}
