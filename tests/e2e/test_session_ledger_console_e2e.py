"""E2E: console web-chat turns survive a CLI compaction (session ledger).

Real transport: ``/auth/local-login`` + CSRF, ``POST /chat/sessions``, the
``/chat/sessions/{sid}/stream`` WebSocket. Only the paid ``claude`` binary is
replaced (``CORVIN_CLAUDE_BIN`` seam). The stand-in behaves like the CLI where
it matters here: it appends each user message to a transcript in the real
Claude Code JSONL layout under ``CLAUDE_CONFIG_DIR/projects/<escaped cwd>/``,
and when the message contains ``COMPACT-NOW`` it appends a ``compact_boundary``
record — after which, exactly as with the real CLI, the earlier messages are
no longer verbatim in the model's context.

Asserted: before the compaction no history block is injected (the transcript
holds every turn); on the turn after it, every earlier turn is re-supplied
verbatim from the chat's append-only ``turns.jsonl`` via the session ledger.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HEADER = "## Chat history this session does not hold"

FAKE_CLI = r'''
import json, os, re, sys
log = os.environ["CORVIN_FAKE_CLI_LOG"]
argv = sys.argv[1:]
sp = ""
if "--append-system-prompt-file" in argv:
    sp = open(argv[argv.index("--append-system-prompt-file") + 1], encoding="utf-8").read()
elif "--append-system-prompt" in argv:
    sp = argv[argv.index("--append-system-prompt") + 1]
try:
    prompt = sys.stdin.read()
except Exception:
    prompt = ""
if not prompt and argv and not argv[-1].startswith("-"):
    prompt = argv[-1]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": argv, "system_prompt": sp, "prompt": prompt}) + "\n")
proj = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects",
                    re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(os.getcwd())))
os.makedirs(proj, exist_ok=True)
with open(os.path.join(proj, "11111111-2222-4333-8444-555555555555.jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
    if "COMPACT-NOW" in prompt:
        fh.write(json.dumps({"type": "system", "subtype": "compact_boundary", "uuid": "cb-console-1",
                             "compactMetadata": {"trigger": "auto", "preTokens": 190000,
                                                 "postTokens": 12000}}) + "\n")
m = re.search(r"[A-Z]+-\d{4}", prompt)
word = m.group(0) if m else "ok"
for evt in ({"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "ack " + word}]}},
            {"type": "result", "result": "ack " + word,
             "usage": {"input_tokens": 5, "output_tokens": 2,
                       "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}):
    sys.stdout.write(json.dumps(evt) + "\n")
sys.stdout.flush()
'''

MSGS = [
    "ALPHA-5521 my favourite colour is teal",
    "BRAVO-6632 my project is called Heron",
    "CHARLIE-7743 the deadline is Friday COMPACT-NOW",
    "DELTA-8854 summarise what you know about me",
]

#: A console slash command: the dispatcher answers it, no engine runs. It must
#: still reach later turns through the ledger (review R4-10).
SLASH = "/engine ECHO-9965"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("ledger_console")
    script = d / "fake_cli.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    shim = d / "claude.sh"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    log = d / "calls.jsonl"
    saved = {k: os.environ.get(k) for k in
             ("CORVIN_CLAUDE_BIN", "CORVIN_FAKE_CLI_LOG", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY")}
    os.environ.update({
        "CORVIN_CLAUDE_BIN": str(shim), "CORVIN_FAKE_CLI_LOG": str(log),
        "CLAUDE_CONFIG_DIR": str(d / "claude-cfg"),
        "ANTHROPIC_API_KEY": "sk-ant-e2e-dummy-never-sent",
    })
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from core.console.corvin_console.app import router as console_router

        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")
        with TestClient(app, client=("127.0.0.1", 51235)) as c:
            r = c.get("/v1/console/auth/local-login", follow_redirects=False)
            assert r.status_code in (200, 302, 307), r.text
            csrf = c.get("/v1/console/auth/whoami").json()["csrf_token"]
            r = c.post("/v1/console/chat/sessions", json={"title": "ledger-e2e"},
                       headers={"x-csrf-token": csrf})
            assert r.status_code == 200, r.text
            sid = r.json()["session"]["sid"]
            for text in MSGS[:3] + [SLASH] + MSGS[3:]:
                with c.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
                    assert ws.receive_json()["type"] == "ready"
                    ws.send_json({"type": "user", "text": text})
                    for _ in range(400):
                        if ws.receive_json().get("type") == "done":
                            break
        calls = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        os_calls = [c for c in calls if any(m.split()[0] in c["prompt"] for m in MSGS)]
        return {"calls": os_calls}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _call_for(run, key: str) -> dict:
    hits = [c for c in run["calls"] if key in c["prompt"]]
    assert hits, f"no engine spawn carried {key}; spawns: {[c['prompt'][:40] for c in run['calls']]}"
    return hits[-1]


def test_every_turn_reached_the_engine(run):
    for m in MSGS:
        _call_for(run, m.split()[0])


def test_no_history_block_while_the_transcript_holds_every_turn(run):
    for key in ("BRAVO-6632", "CHARLIE-7743"):
        assert HEADER not in _call_for(run, key)["system_prompt"], key


def test_turns_lost_to_compaction_are_resupplied_verbatim(run):
    sp = _call_for(run, "DELTA-8854")["system_prompt"]
    assert HEADER in sp
    for m in MSGS[:3]:
        assert m in sp, f"{m!r} missing after compaction"
    assert "ack ALPHA-5521" in sp, "the assistant side is re-supplied too"
    assert "DELTA-8854" not in sp.split(HEADER, 1)[1], "the in-flight turn is not history"


def test_slash_command_turn_is_recorded_and_resupplied(run):
    assert not any("ECHO-9965" in c["prompt"] for c in run["calls"]), "a slash command spawned the engine"
    sp = _call_for(run, "DELTA-8854")["system_prompt"]
    assert SLASH in sp and "configured engine for this tenant" in sp
