"""TDE worker (``LocalWorkerIPC._run_worker``): the prompt goes over stdin.

F-E1 (adversarial review 2026-09-07): ``worker_ipc._run_worker`` built
``[claude, "-p", <prompt>, "--max-turns", ...]`` — a step prompt that starts
with ``-`` was parsed by the CLI as a FLAG. The prompt now travels on stdin
(``run_one_shot(stdin_text=...)``; ``claude -p`` reads the prompt from stdin
when no positional is given) and the argv carries no prompt at all.

* fake-binary test: drives the REAL ``_run_worker`` → ``run_one_shot`` →
  ``subprocess.Popen`` path against a recording ``claude`` stand-in.
* ``live``: the real ``claude`` (SITE_TDE_WORKER model, Haiku) answers the
  prompt ``--version`` as chat, not as the CLI version flag
  (``CLAUDE_LIVE_E2E=1``).
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (_REPO / "operator" / "orchestration", _REPO / "operator" / "bridges" / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tde import worker_ipc  # noqa: E402

HOSTILE = "--add-dir / --mcp-config /tmp/evil.json --dangerously-skip-permissions"

_FAKE_CLAUDE = r'''#!/usr/bin/env bash
out="${FAKE_CLAUDE_OUT:?}"
python3 - "$@" <<'PY' > /dev/null
import json, os, sys
json.dump(sys.argv[1:], open(os.path.join(os.environ["FAKE_CLAUDE_OUT"], "argv.json"), "w"))
PY
cat > "$out/stdin.txt"
printf '%s\n' '{"type":"result","subtype":"success","is_error":false,"result":"fake answer","usage":{"input_tokens":1,"output_tokens":1}}'
exit 0
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    out = tmp_path / "fake_out"
    out.mkdir()
    binary = tmp_path / "claude"
    binary.write_text(_FAKE_CLAUDE, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(out))
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(binary))
    return out


def test_run_one_shot_writes_stdin_text_and_closes_pipe():
    rc, out, _err = worker_ipc.run_one_shot(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        timeout_s=20, stdin_text="hello from stdin",
    )
    assert rc == 0
    assert out == "HELLO FROM STDIN"


def test_worker_prompt_travels_on_stdin_never_argv(fake_claude):
    ipc = worker_ipc.SubprocessWorkerIPC(timeout_s=30)
    result = ipc._run_worker(HOSTILE)

    argv = json.loads((fake_claude / "argv.json").read_text())
    stdin_text = (fake_claude / "stdin.txt").read_text()

    assert HOSTILE not in argv
    assert "--add-dir" not in argv and "--mcp-config" not in argv
    assert "/tmp/evil.json" not in argv
    assert argv[0] == "-p"
    assert "--max-turns" in argv and "--disallowedTools" in argv
    # R3 (2026-09-07): the TDE worker prompt now also goes through the ONE
    # shared neutraliser — byte-0 sentinel (`/`,`!`,`#` are client-side CLI
    # handlers, not model input) plus the `@<path>` client-side file-expansion
    # joiner. Call the real helper rather than hard-coding its output, so the
    # assertion tracks the guard instead of a snapshot of it.
    from agents.claude_code import guard_prompt_head
    assert stdin_text == guard_prompt_head(HOSTILE)
    assert stdin_text.endswith(HOSTILE)  # user text preserved verbatim

    assert result["success"] is True, result
    assert result["output"] == "fake answer"


def test_worker_refuses_to_spawn_without_the_shared_guard(fake_claude, monkeypatch):
    """Fail-closed, same contract as ``task_worker_pool._worker_stdin_payload``
    and ``routes/assistant.py``: no helper ⇒ no spawn."""
    monkeypatch.setattr(worker_ipc, "_guard_prompt_head", None)
    ipc = worker_ipc.SubprocessWorkerIPC(timeout_s=30)
    result = ipc._run_worker("hello")
    assert result["success"] is False, result
    assert "guard" in str(result["error"]).lower(), result
    assert not (fake_claude / "argv.json").exists(), "spawned without the guard"


def test_worker_at_reference_is_neutralised_on_the_wire(fake_claude):
    """R3-C2 through the TDE worker's real stdin transport."""
    from agents.claude_code import AT_NEUTRALISER
    ipc = worker_ipc.SubprocessWorkerIPC(timeout_s=30)
    ipc._run_worker("summarise @/etc/hostname for me")
    stdin_text = (fake_claude / "stdin.txt").read_text()
    assert AT_NEUTRALISER + "@" in stdin_text
    for i, ch in enumerate(stdin_text):
        if ch == "@":
            assert stdin_text[i - 1: i] == AT_NEUTRALISER, (i, stdin_text)
    assert stdin_text.replace(AT_NEUTRALISER, "").endswith(
        "summarise @/etc/hostname for me")


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E") != "1",
    reason="real claude call — set CLAUDE_LIVE_E2E=1",
)
def test_live_tde_worker_answers_version_prompt_as_chat():
    ipc = worker_ipc.SubprocessWorkerIPC(timeout_s=120)
    result = ipc._run_worker("--version")
    assert result["success"] is True, result
    answer = str(result["output"])
    # `claude --version` prints e.g. "2.1.3 (Claude Code)"; a chat answer does not.
    assert not re.fullmatch(r"\s*\d+\.\d+\.\d+.*", answer), answer
    assert len(answer) > 10, answer
