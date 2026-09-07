"""R3-C1 (adversarial review round 3, 2026-09-07) — the console assistant route.

``POST /v1/console/assistant/message`` spawns ``claude -p`` on the operator's
message. It was the ONE spawn site rounds 1–2 missed: it imported neither
``guard_prompt_head`` nor ``PROMPT_HEAD_SENTINEL``, and passed the prompt as a
POSITIONAL argv element with no ``--`` separator. With the route's own defaults
(``context={}``, ``history=[]``) the argv element WAS the user's message
byte-for-byte, so:

  * ``--version`` was parsed by the CLI as a FLAG (it printed its version and
    never ran a model turn);
  * ``/pwn`` expanded a project slash command from the spawn cwd;
  * ``@/etc/hostname`` anywhere in the message expanded into that file's
    content (R3-C2), which no tool policy restricts.

Driven through the REAL boundary: the real FastAPI router mounted on a real
app, a real HTTP request, and a recording ``claude`` stand-in on PATH that the
route actually execs. Only the session/CSRF dependency is overridden.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (str(_REPO / "operator"), str(_REPO / "operator" / "forge"),
           str(_REPO / "operator" / "license"), str(_REPO / "core" / "console"),
           str(_REPO / "operator" / "bridges" / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agents.claude_code import (  # noqa: E402
    AT_NEUTRALISER,
    PROMPT_HEAD_SENTINEL,
    guard_prompt_head,
)

HEAD = PROMPT_HEAD_SENTINEL + "\n"

_RECORDER = r"""#!/usr/bin/env python3
import json, os, sys
out = os.environ["FAKE_CLAUDE_OUT"]
json.dump(sys.argv[1:], open(os.path.join(out, "argv.json"), "w"))
open(os.path.join(out, "stdin.txt"), "w").write(sys.stdin.read())
sys.stdout.write("recorded")
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")

    out = tmp_path / "fake_out"
    out.mkdir()
    binroot = tmp_path / "bin"
    binroot.mkdir()
    fake = binroot / "claude"
    fake.write_text(_RECORDER, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(out))
    monkeypatch.setenv("PATH", f"{binroot}{os.pathsep}{os.environ['PATH']}")

    for key in [k for k in sys.modules if k.startswith(("corvin_console", "forge"))]:
        del sys.modules[key]

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console import deps as _deps
    from corvin_console.routes import assistant as route

    class _Rec:
        tenant_id = "_default"
        sid_fingerprint = "test-fp"
        csrf_token = "t"

    app = FastAPI()
    app.include_router(route.router, prefix="/v1/console")
    app.dependency_overrides[_deps.require_csrf] = lambda: _Rec()
    app.dependency_overrides[_deps.require_session] = lambda: _Rec()
    return TestClient(app, raise_server_exceptions=False), out, route


def _post(client, message: str):
    resp = client.post("/v1/console/assistant/message", json={"message": message})
    assert resp.status_code == 200, resp.text
    return resp


def _recorded(out: Path):
    argv = json.loads((out / "argv.json").read_text(encoding="utf-8"))
    stdin = (out / "stdin.txt").read_text(encoding="utf-8")
    return argv, stdin


def test_prompt_is_not_on_argv_and_is_sentinel_guarded(client):
    """The message must not appear in argv at all, and byte 0 of what the CLI
    reads must be the shared sentinel — so `--version` / `/pwn` are text."""
    c, out, _route = client
    hostile = "--version"
    _post(c, hostile)
    argv, stdin = _recorded(out)
    assert hostile not in argv, argv
    assert "--" not in argv, argv          # no positional-prompt separator either
    assert argv[0] == "-p", argv
    assert stdin.startswith(HEAD), stdin[:60]
    assert stdin.endswith(hostile), stdin[-40:]
    assert not stdin.startswith("/")


def test_slash_command_reaches_the_cli_as_text(client):
    c, out, _route = client
    _post(c, "/pwn")
    _argv, stdin = _recorded(out)
    assert stdin == guard_prompt_head("/pwn")
    assert stdin[0].isalpha(), stdin[:20]


def test_at_path_reference_is_neutralised(client):
    """R3-C2 through this route: the `@` may not sit at a token start when the
    CLI scans the message, and nothing may be deleted from the user's text."""
    c, out, _route = client
    msg = "please read @/etc/hostname and @~/.corvin/audit.jsonl"
    _post(c, msg)
    _argv, stdin = _recorded(out)
    assert AT_NEUTRALISER + "@" in stdin
    for i, ch in enumerate(stdin):
        if ch == "@":
            assert stdin[i - 1: i] == AT_NEUTRALISER, (i, stdin)
    # user content preserved verbatim once the zero-width joiners are dropped
    assert stdin.replace(AT_NEUTRALISER, "") == HEAD + msg


def test_email_address_in_the_message_survives(client):
    """The stated trade-off boundary: an `@` inside an e-mail local part is not
    an expansion vector and must not be touched."""
    c, out, _route = client
    msg = "forward it to silvio.jurk@googlemail.com"
    _post(c, msg)
    _argv, stdin = _recorded(out)
    assert stdin == HEAD + msg
    assert AT_NEUTRALISER not in stdin


def test_route_refuses_to_spawn_when_the_guard_is_missing(client, monkeypatch):
    """Fail-closed, the same contract as ``task_worker_pool._worker_stdin_payload``:
    no helper ⇒ no payload ⇒ no spawn."""
    c, out, route = client
    monkeypatch.setattr(route, "_guard_prompt_head", None)
    for stale in out.glob("*"):
        stale.unlink()
    resp = c.post("/v1/console/assistant/message", json={"message": "hello"})
    assert resp.status_code == 200, resp.text
    assert "unavailable" in resp.json()["response"].lower()
    # The L44 pre-spawn gate runs its OWN classifier through the same recorder,
    # so a recording alone is not proof. The assistant's spawn is the only one
    # that feeds a payload starting with the sentinel line — that must be absent.
    recorded = (out / "stdin.txt").read_text(encoding="utf-8") if (out / "stdin.txt").exists() else ""
    assert not recorded.startswith(HEAD), (
        "the assistant turn spawned anyway without the guard"
    )
