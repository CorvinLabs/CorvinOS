"""Workflow design chat composer, server side (CONCEPT-0115 / ADR-2270).

Everything goes through the real transport: an HTTP client and a WebSocket
against the console router, in a sandboxed CORVIN_HOME (never the live install).

Run:  core/console/.venv/bin/python -m pytest core/console/tests/test_workflow_composer.py
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "corvin_operator"
_CONSOLE = _REPO / "core" / "console"
_WORKFLOWS = _REPO / "core" / "workflows"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_WORKFLOWS)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def sandbox(tmp_path: Path, tenant_id: str = "_default"):
    """A console app on a sandboxed CORVIN_HOME; yields (app, home, tenant, new_client)."""
    home = tmp_path / "corvin_home"
    for sub in ("auth", "forge", "console/sessions"):
        (home / "tenants" / tenant_id / "global" / sub).mkdir(parents=True, exist_ok=True)
    keep = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", "XDG_CONFIG_HOME",
                                           "CORVIN_AUDIT_ANCHOR_KEY")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    os.environ.setdefault("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")

        def new_client(tid: str = tenant_id):
            rec = _auth.create_session(tenant_id=tid, token_fingerprint=f"fp-{tid}")
            csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
            c = TestClient(app, raise_server_exceptions=False)
            c.cookies.set("corvin_console_sid", rec.sid)
            c.headers.update({"X-CSRF-Token": csrf})
            return c

        yield app, home, tenant_id, new_client
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def make_workflow(tenant_id: str, wid: str, yaml_text: str = "", phase: str = "discovering") -> Path:
    from forge import paths as _fp
    wf_dir = _fp.tenant_home(tenant_id) / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / f"{wid}.meta.json").write_text(
        json.dumps({"id": wid, "title": wid, "phase": phase}), encoding="utf-8")
    if yaml_text:
        (wf_dir / f"{wid}.awp.yaml").write_text(yaml_text, encoding="utf-8")
    return wf_dir


def audit_records(tenant_id: str) -> list[dict]:
    from forge import paths as _fp
    chain = _fp.tenant_home(tenant_id) / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    return [json.loads(x) for x in chain.read_text().splitlines() if x.strip()]


_TWO_STEP = """awp: "1.0.0"
workflow:
  name: two_step
  description: x
orchestration:
  engine: dag
  graph:
    - id: fetch
      type: agent
      agent: assistant
      depends_on: []
      instructions: "fetch"
    - id: summarise
      type: agent
      agent: assistant
      depends_on: [fetch]
      instructions: "summarise"
"""


def test_commands_table_is_served_and_needs_a_session(tmp_path):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        from fastapi.testclient import TestClient
        anon = TestClient(app, raise_server_exceptions=False)
        assert anon.get("/v1/console/workflow-design/commands").status_code == 401
        r = new_client().get("/v1/console/workflow-design/commands")
        assert r.status_code == 200, r.text
        cmds = [c["cmd"] for c in r.json()["commands"]]
        assert {"/help", "/phase", "/nodes", "/voice on", "/voice off"} <= set(cmds)


def test_ws_commands_answer_without_spawn_and_stay_out_of_the_transcript(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        wf_dir = make_workflow(tid, "wf1", _TWO_STEP, phase="structuring")
        import corvin_console.routes.workflows as wfroutes

        def _boom(*a, **k):  # any engine spawn during a command is a bug
            raise AssertionError("a slash command must never spawn an engine")
        monkeypatch.setattr(wfroutes, "_run_claude_for_design", _boom)
        monkeypatch.setattr(wfroutes, "_design_turn", _boom)

        client = new_client()
        with client.websocket_connect("/v1/console/workflows/wf1/chat") as ws:
            init = ws.receive_json()
            assert init["type"] == "init"
            opening = ws.receive_json()  # empty transcript -> the opening question
            assert opening["type"] == "message"

            ws.send_json({"type": "user", "text": "/phase"})
            echo, reply = ws.receive_json(), ws.receive_json()
            assert echo["role"] == "user" and echo["command"] is True
            assert reply["role"] == "assistant" and "structuring" in reply["content"]

            ws.send_json({"type": "user", "text": "/nodes"})
            ws.receive_json()
            assert "fetch -> summarise" in ws.receive_json()["content"]

            ws.send_json({"type": "user", "text": "/voice on"})
            ack = ws.receive_json()
            assert ack == {"type": "voice_ack", "enabled": True, "lang": "en"}
            ws.receive_json(); ws.receive_json()

            ws.send_json({"type": "user", "text": "/nonsense"})
            ws.receive_json()
            assert "Unknown command /nonsense" in ws.receive_json()["content"]

        # Command lines never reach the design transcript (only the opening line is there).
        chat = [json.loads(x) for x in (wf_dir / "wf1.chat.jsonl").read_text().splitlines() if x.strip()]
        assert all(m["role"] == "assistant" for m in chat) and len(chat) == 1

        cmd_events = [r for r in audit_records(tid)
                      if r.get("details", {}).get("action") == "workflow.design_command"]
        assert {e["details"].get("trigger") for e in cmd_events} >= {"phase", "nodes", "voice", "nonsense"}


def test_path_like_text_is_not_a_command():
    from corvin_console.routes import workflow_composer as wc
    assert wc.parse_design_command("/home/user/file.csv please read") is None
    assert wc.parse_design_command("Describe a workflow") is None
    assert wc.parse_design_command("  /phase ") == ("/phase", "")
    assert wc.parse_design_command("/voice off") == ("/voice", "off")
