"""E2E: console ACTIVE pipeline delivers the load-bearing anchor (review R1-B4).

The active pipeline replaces the deterministic CEL brief with an LLM-synthesised
prompt. The anchored facts sat at the head of the deterministic brief only, so on
this path they were persisted every turn and injected never. They are now folded
into the synthesised prompt inside the Gate-2 enforcer (`_gate2_and_bind`).

Real transport: local-login + CSRF, POST /chat/sessions, the stream WebSocket.
Only the paid ``claude`` binary is replaced (``CORVIN_CLAUDE_BIN``); the stand-in
answers the synthesis call (``-p … --output-format json``) with a JSON brief and
the worker turn with stream-json, recording every system prompt it was given.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

FAKE_CLI = r'''
import json, os, sys
argv = sys.argv[1:]
log = os.environ["CORVIN_FAKE_CLI_LOG"]
if "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json":
    # llm_synthesis stage: return a brief that does NOT mention the goal.
    inner = {"brief": "SYNTH-BRIEF-XYZ: answer the operator's question.",
             "needs": {"tools": [], "skills": []}}
    with open(log, "a") as fh:
        fh.write(json.dumps({"kind": "synthesis"}) + "\n")
    print(json.dumps({"result": json.dumps(inner)}))
    sys.exit(0)
sp = ""
if "--append-system-prompt-file" in argv:
    sp = open(argv[argv.index("--append-system-prompt-file") + 1], encoding="utf-8").read()
elif "--append-system-prompt" in argv:
    sp = argv[argv.index("--append-system-prompt") + 1]
try:
    prompt = sys.stdin.read()
except Exception:
    prompt = ""
with open(log, "a") as fh:
    fh.write(json.dumps({"kind": "turn", "system_prompt": sp, "prompt": prompt}) + "\n")
for evt in ({"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
            {"type": "result", "result": "ok",
             "usage": {"input_tokens": 5, "output_tokens": 2,
                       "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}):
    sys.stdout.write(json.dumps(evt) + "\n")
sys.stdout.flush()
'''


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("anchor_active")
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
    home = Path(os.environ["CORVIN_HOME"])
    overlay = home / "tenants" / "_default" / "global" / "features.json"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(json.dumps({"flags": {"vibe_engineering": True,
                                             "vibe_engineering_active": True,
                                             "cel_load_bearing_anchor": True}}))
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from core.console.corvin_console.app import router as console_router

        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")
        with TestClient(app, client=("127.0.0.1", 51236)) as c:
            r = c.get("/v1/console/auth/local-login", follow_redirects=False)
            assert r.status_code in (200, 302, 307), r.text
            csrf = c.get("/v1/console/auth/whoami").json()["csrf_token"]
            sid = c.post("/v1/console/chat/sessions", json={"title": "anchor-active"},
                         headers={"x-csrf-token": csrf}).json()["session"]["sid"]
            for text in ("GOAL-ANCHOR-7731 migrate the billing export to Parquet",
                         "what is the next step?"):
                with c.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
                    assert ws.receive_json()["type"] == "ready"
                    ws.send_json({"type": "user", "text": text})
                    for _ in range(400):
                        if ws.receive_json().get("type") == "done":
                            break
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_active_pipeline_ran(run):
    assert any(c["kind"] == "synthesis" for c in run), "the LLM synthesis stage never ran"
    turns = [c for c in run if c["kind"] == "turn"]
    assert len(turns) >= 2
    assert "SYNTH-BRIEF-XYZ" in turns[-1]["system_prompt"], "synthesised prompt not delivered"


def test_anchor_reaches_the_worker_on_the_synthesised_path(run):
    sp = [c for c in run if c["kind"] == "turn"][-1]["system_prompt"]
    synth_at = sp.index("SYNTH-BRIEF-XYZ")
    assert "GOAL-ANCHOR-7731" in sp[:synth_at], (
        "the anchored goal from turn 1 is not in front of the synthesised prompt")
