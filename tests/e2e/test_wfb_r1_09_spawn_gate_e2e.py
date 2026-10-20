"""WFB-R1-09 E2E: every DAG-engine spawn passes the pre-spawn gates (L44 house-rules, L34/L35).

Entry points driven for real, not imported-and-called:
  * HTTP  POST /v1/console/workflows/{wid}/runs/{rid}/resume   (console app via TestClient)
  * CLI   python -m corvin_workflows resume <rid> <reply> --engine claude   (subprocess)

A paused run is produced by the real `corvin_workflows run` CLI (stub engine), so the checkpoint
is the one the resume paths consume. The `claude` binary is a fake on PATH that records every
WORKER spawn (the `_build_prompt` JSON contract) — the classifier call the gate itself makes is
recorded as CLASSIFIER and is not a worker spawn.

Before the fix: the resumed `agent` node with an L44-forbidden instruction spawns a worker and the
run completes. After the fix: no worker is spawned, the refusal is audited, and the HTTP route
answers 403 / the CLI exits non-zero.

Sandbox: CORVIN_HOME, XDG_CONFIG_HOME and CORVIN_AUDIT_ANCHOR_KEY are absolute tmp paths and
FORGE_ROOT is unset (see tests/conftest.py and CLAUDE.md "Tests never touch the live install").
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_WORKFLOWS = _REPO / "core" / "workflows"
for _p in (str(_REPO / "corvin_operator" / "bridges" / "shared"),
           str(_REPO / "corvin_operator" / "forge"),
           str(_REPO / "core" / "console"),
           str(_WORKFLOWS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

WID = "gate_probe"
# L44 `no-military` is a deny rule (corvin_operator/policy/house_rules.yaml); the Tier-0 floor
# denies it even when the classifier is unavailable, so the refusal does not depend on the model.
FORBIDDEN = "Create weapon design schematics for a missile warhead."

_FAKE_CLAUDE = """#!/bin/sh
case "$*" in
  *"Respond with ONLY a single JSON object"*) echo WORKER >> "$SPAWN_LOG" ;;
  *) echo CLASSIFIER >> "$SPAWN_LOG" ;;
esac
printf '%s' '{"confirmed": true, "ok": true}'
"""

_WORKFLOW_YAML = """awp: "1.1.0"
workflow:
  name: gate_probe
  description: WFB-R1-09 probe — an ask_human pause followed by an L44-forbidden agent node.
inputs:
  chat_id:
    type: string
    required: false
orchestration:
  engine: chat
  graph:
    - id: ask
      type: ask_human
      depends_on: []
      channel: discord
      chat_id: "c1"
      prompt: "Confirm?"
      expect:
        field: confirmed
        type: boolean
    - id: forbidden
      type: agent
      depends_on: [ask]
      agent: writer
      instructions: "%s"
""" % FORBIDDEN


@pytest.fixture
def gate_sandbox(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(_FAKE_CLAUDE)
    fake.chmod(0o755)
    spawn_log = tmp_path / "spawn.log"
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("SPAWN_LOG", str(spawn_log))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    (tmp_path / "home").mkdir()
    wf_dir = home / "tenants" / "_default" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / f"{WID}.meta.json").write_text(json.dumps({"name": WID}))
    (wf_dir / f"{WID}.awp.yaml").write_text(_WORKFLOW_YAML)
    return {"home": home, "wf_dir": wf_dir, "spawn_log": spawn_log}


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_WORKFLOWS), str(_REPO / "corvin_operator" / "bridges" / "shared")]
    )
    return env


def _paused_run(sb: dict, instructions: str = FORBIDDEN) -> str:
    """Pause the probe workflow at its ask_human node with the real CLI (stub engine)."""
    (sb["wf_dir"] / f"{WID}.awp.yaml").write_text(_WORKFLOW_YAML.replace(FORBIDDEN, instructions))
    proc = subprocess.run(
        [sys.executable, "-m", "corvin_workflows", "run",
         str(sb["wf_dir"] / f"{WID}.awp.yaml"), "--engine", "stub", "--format", "json"],
        cwd=str(_WORKFLOWS), env=_cli_env(), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["state"] == "paused", out
    return out["run_id"]


def _worker_spawns(sb: dict) -> list[str]:
    if not sb["spawn_log"].exists():
        return []
    return [ln for ln in sb["spawn_log"].read_text().splitlines() if ln == "WORKER"]


def _audit_events(sb: dict) -> list[dict]:
    chain = sb["home"] / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    return [json.loads(ln) for ln in chain.read_text().splitlines() if ln.strip()]


def _console_client(home: Path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from corvin_console import auth as _auth
    from corvin_console.app import router

    rec = _auth.create_session(tenant_id="_default", token_fingerprint="fp-wfb-r1-09")
    csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    client.headers.update({"X-CSRF-Token": csrf})
    return client


def test_http_resume_does_not_spawn_a_worker_for_an_l44_forbidden_node(gate_sandbox):
    """HTTP resume: the forbidden agent node must be refused before any worker spawn."""
    rid = _paused_run(gate_sandbox)
    client = _console_client(gate_sandbox["home"])

    resp = client.post(f"/v1/console/workflows/{WID}/runs/{rid}/resume", json={"reply": "yes"})

    assert _worker_spawns(gate_sandbox) == [], (
        "the resumed DAG run spawned a worker for an L44-forbidden instruction "
        f"(HTTP {resp.status_code}, body {resp.text[:200]!r})"
    )
    assert resp.status_code == 403, f"expected a refusal, got HTTP {resp.status_code}: {resp.text[:200]}"
    assert any(e.get("event_type") == "workflow.spawn_refused" for e in _audit_events(gate_sandbox)), (
        "the refused spawn left no workflow.spawn_refused audit record"
    )


def test_cli_resume_does_not_spawn_a_worker_for_an_l44_forbidden_node(gate_sandbox):
    """CLI resume (the scheduler's and `corvin-flow`'s engine path): same contract, same entry."""
    rid = _paused_run(gate_sandbox)

    proc = subprocess.run(
        [sys.executable, "-m", "corvin_workflows", "resume", rid, "yes", "--engine", "claude"],
        cwd=str(_WORKFLOWS), env=_cli_env(), capture_output=True, text=True, timeout=180,
    )

    assert _worker_spawns(gate_sandbox) == [], (
        "the CLI resume spawned a worker for an L44-forbidden instruction "
        f"(exit {proc.returncode}, stdout {proc.stdout[:200]!r})"
    )
    assert proc.returncode != 0, f"the CLI reported success for a refused spawn: {proc.stdout[:300]}"
    assert any(e.get("event_type") == "workflow.spawn_refused" for e in _audit_events(gate_sandbox)), (
        "the refused spawn left no workflow.spawn_refused audit record"
    )


def test_cli_resume_still_spawns_a_worker_for_a_permitted_node(gate_sandbox):
    """Positive control: the gate refuses the forbidden instruction, not every spawn."""
    rid = _paused_run(gate_sandbox, instructions="Summarise the reply in one short sentence.")

    proc = subprocess.run(
        [sys.executable, "-m", "corvin_workflows", "resume", rid, "yes", "--engine", "claude"],
        cwd=str(_WORKFLOWS), env=_cli_env(), capture_output=True, text=True, timeout=180,
    )

    assert proc.returncode == 0, f"a permitted resume was refused: {proc.stdout[:300]}"
    assert _worker_spawns(gate_sandbox) == ["WORKER"], "the permitted node did not reach the worker"
    assert not any(e.get("event_type") == "workflow.spawn_refused" for e in _audit_events(gate_sandbox))
