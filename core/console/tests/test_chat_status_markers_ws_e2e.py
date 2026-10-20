"""E2E: the console web chat hands the status-marker rule to the Claude CLI (ADR-2269).

The turn goes through the REAL chat WebSocket route (``/v1/console/chat/sessions/{sid}/stream``)
and ``chat_runtime.stream_turn``; only the session-auth lookup and the license gate are
stubbed. The claude binary is a launcher that records its full argv and then replays a
stream-json fixture. The test reads the file behind ``--append-system-prompt-file`` as the
CLI received it and asserts the rule is in it, and that the rule is the one from
``status_markers`` (not a copy). A second turn with the same session proves the rule is
present on every turn, not only the first.
"""
from __future__ import annotations

import json
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

import bgscope_testkit as kit  # noqa: E402  # fake claude CLI + fixtures (ADR-2236)
import status_markers  # noqa: E402

RULE = status_markers.status_marker_rule()
FAKE_ARGS_LOG = "fake_argv.jsonl"


def _launcher(tmp: Path) -> Path:
    """A claude stand-in: append its argv (JSON line) to a log, then run the fake CLI."""
    if sys.platform.startswith("win"):
        pytest.skip("POSIX launcher; the Windows shim is covered by the bridge suites")
    bin_dir = tmp / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    recorder = bin_dir / "record_argv.py"
    recorder.write_text(
        "import json, sys\n"
        f"open({str(tmp / FAKE_ARGS_LOG)!r}, 'a', encoding='utf-8')"
        ".write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    launcher = bin_dir / "claude"
    launcher.write_text(
        "#!/bin/sh\n"
        f"{sys.executable!r} {str(recorder)!r} \"$@\" || exit 1\n"
        f"exec {sys.executable!r} {str(kit.FAKE)!r} \"$@\"\n",
        encoding="utf-8",
    )
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


def _fixture(tmp_path: Path) -> Path:
    lines = [
        {"type": "system", "subtype": "init", "session_id": "sm-ws-e2e"},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "done"}]}, "session_id": "sm-ws-e2e"},
        {"type": "result", "subtype": "success", "is_error": False,
         "result": "done", "session_id": "sm-ws-e2e"},
    ]
    return kit.write_fixture(tmp_path / "fixture", "sm-ws-e2e", lines)


def _system_prompt_files(argv_log: Path) -> list[str]:
    """The text of every --append-system-prompt-file the CLI was started with, in order."""
    texts = []
    for line in argv_log.read_text(encoding="utf-8").splitlines():
        argv = json.loads(line)
        if "--append-system-prompt-file" in argv:
            path = Path(argv[argv.index("--append-system-prompt-file") + 1])
            texts.append(path.read_text(encoding="utf-8"))
    return texts


@pytest.fixture
def ws_turns(tmp_path, monkeypatch):
    """Run chat turns over the real WebSocket route; return the wire events per turn."""
    _sandbox(tmp_path, monkeypatch)
    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from corvin_console import chat_runtime as cr  # noqa: PLC0415
    from corvin_console.routes import chat as chat_routes  # noqa: PLC0415

    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(_launcher(tmp_path)))
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(_fixture(tmp_path)))
    monkeypatch.setenv("FAKE_CLAUDE_SPEEDUP", "20.0")
    monkeypatch.setenv("FAKE_CLAUDE_SILENT_S", "0.0")
    monkeypatch.setenv("FAKE_CLAUDE_PIDFILE", str(tmp_path / "fake.pid"))
    monkeypatch.setenv("FAKE_CLAUDE_SPAWNLOG", str(tmp_path / "spawns.log"))

    rec = MagicMock()
    rec.tenant_id = "_default"
    rec.sid_fingerprint = "fp-sm-ws-e2e"

    def _run(texts: list[str]) -> list[list[dict]]:
        sess = cr.create_session("_default")
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        turns: list[list[dict]] = []
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=rec),
            patch("corvin_console.routes._compute_license_gate.enforce_chat_turns",
                  lambda *a, **k: None),
        ):
            client = TestClient(app)
            client.cookies.set("corvin_console_sid", "valid-sid")
            with client.websocket_connect(f"/v1/console/chat/sessions/{sess.sid}/stream") as ws:
                assert ws.receive_json()["type"] == "ready"
                for text in texts:
                    ws.send_json({"type": "user", "text": text})
                    wire: list[dict] = []
                    while True:
                        ev = ws.receive_json()
                        wire.append(ev)
                        if ev.get("type") in ("done", "error"):
                            break
                    turns.append(wire)
        return turns

    return _run, tmp_path


def test_every_console_turn_carries_the_status_marker_rule(ws_turns) -> None:
    run, tmp_path = ws_turns
    turns = run(["first question", "second question"])
    for wire in turns:
        assert wire[-1]["type"] == "done", wire[-1]
    prompts = _system_prompt_files(tmp_path / FAKE_ARGS_LOG)
    # One turn = one CLI start with a system-prompt file; the rule must be in each.
    assert len(prompts) >= 2, prompts
    for text in prompts:
        assert RULE in text
        assert "Status markers" in text


def test_positive_control_the_rule_is_not_vacuous(ws_turns) -> None:
    # The file the CLI reads is the turn prompt, not an empty stub: it carries the
    # console's own base prompt as well as the rule.
    run, tmp_path = ws_turns
    run(["control"])
    text = _system_prompt_files(tmp_path / FAKE_ARGS_LOG)[0]
    assert len(text) > len(RULE) + 200
