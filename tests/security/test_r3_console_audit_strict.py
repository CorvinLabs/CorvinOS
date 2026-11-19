"""R3-NEW-8: a console mutation whose audit record cannot commit is refused.

The chain writer refuses a record whose ``tenant_id`` is not the process
tenant (``AuditTenantMismatch``). The console's emitters were best-effort, so
every mutation made from a session of another tenant ran, answered 200 and
left nothing in any chain but an ERROR log line.

The mutation emitters are now strict by default and ``AuditWriteFailed`` is an
HTTP 503, so a route that calls one without handling the failure answers 503.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
FORGE = REPO / "corvin_operator" / "forge"

_PRELUDE = """
    import json, logging
    from pathlib import Path
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from corvin_console import audit as ca
    from forge import paths as fp

    def chain(tid):
        p = Path(fp.tenant_audit_chain(tid))
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    app = FastAPI()
    state = {"mutated": 0}

    @app.post("/mutate/{tid}")
    def mutate(tid: str):
        state["mutated"] += 1
        ca.action_performed(tenant_id=tid, sid_fingerprint="fp1", action="thing.update",
                            target_kind="thing", target_id="t1")
        return {"ok": True}

    @app.post("/panel/{tid}")
    def panel(tid: str):
        ca.panel_deleted(tenant_id=tid, panel_id="p1", sid_fingerprint="fp1", deleted_by="operator")
        return {"ok": True}

    @app.post("/attachment/{tid}")
    def attachment(tid: str):
        ca.workflow_attachment_deleted(tenant_id=tid, wid="w1", att_id="a1", sid_fingerprint="fp1")
        return {"ok": True}

    @app.post("/denied/{tid}")
    def denied(tid: str):
        ca.action_denied(tenant_id=tid, sid_fingerprint="fp1", action="thing.update",
                         target_kind="thing", target_id="t1", reason="forbidden")
        raise HTTPException(status_code=403, detail="forbidden")

    client = TestClient(app, raise_server_exceptions=False)
"""


def _run(code: str, tmp_path: Path) -> dict:
    home = tmp_path / "home"
    key = tmp_path / "anchor.key"
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    env = {k: v for k, v in os.environ.items()
           if k not in ("FORGE_ROOT", "VOICE_AUDIT_PATH", "CORVIN_TENANT_ID")}
    env.update({
        "CORVIN_HOME": str(home), "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(key), "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "corvin_operator"), str(FORGE),
                                       str(SHARED), str(REPO / "core" / "console")]),
    })
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(_PRELUDE) + textwrap.dedent(code)],
                          env=env, capture_output=True, text=True, timeout=180, cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_action_performed_of_a_non_process_tenant_is_best_effort_but_not_silent(tmp_path):
    """Decision (round 3): ``action_performed`` stays best-effort - most routes call it after
    the change, so a 503 would report failure for an action that already ran. The loss is on
    record: the chain writer's refusal lands in the process tenant's chain (type only)."""
    out = _run("""
        r = client.post("/mutate/acme")
        own = chain("_default")
        print(json.dumps({"status": r.status_code, "body": r.json(),
                          "acme": [e["event_type"] for e in chain("acme")],
                          "own": [[e["event_type"], e["details"].get("dropped_event_type")] for e in own]}))
    """, tmp_path)
    assert out["status"] == 200, out
    assert "console.action_performed" not in out["acme"]
    # The refusal is on record in the process tenant's chain (type only).
    assert ["audit.tenant_mismatch", "console.action_performed"] in out["own"]


def test_every_strict_mutation_emitter_refuses(tmp_path):
    out = _run("""
        print(json.dumps({"panel": client.post("/panel/acme").status_code,
                          "attachment": client.post("/attachment/acme").status_code}))
    """, tmp_path)
    assert out == {"panel": 503, "attachment": 503}


def test_process_tenant_mutation_is_recorded_and_answers_200(tmp_path):
    out = _run("""
        r = client.post("/mutate/_default")
        recs = [e for e in chain("_default") if e["event_type"] == "console.action_performed"]
        print(json.dumps({"status": r.status_code, "n": len(recs),
                          "details": recs[0]["details"] if recs else None}))
    """, tmp_path)
    assert out["status"] == 200 and out["n"] == 1
    assert out["details"]["target_kind"] == "thing" and out["details"]["tenant_id"] == "_default"


def test_informational_record_stays_best_effort(tmp_path):
    """A denial keeps its own status code: raising 503 over a 403 would hide it."""
    out = _run("""
        print(json.dumps({"status": client.post("/denied/acme").status_code}))
    """, tmp_path)
    assert out["status"] == 403


def test_explicit_strict_and_exception_shape(tmp_path):
    out = _run("""
        ca.action_performed(tenant_id="acme", sid_fingerprint="fp1", action="a",
                            target_kind="k", target_id="i")
        try:
            ca.action_performed(tenant_id="acme", sid_fingerprint="fp1", action="a",
                                target_kind="k", target_id="i", strict=True)
            res = None
        except ca.AuditWriteFailed as exc:
            res = {"runtime": isinstance(exc, RuntimeError), "http": isinstance(exc, HTTPException),
                   "status": exc.status_code, "str": str(exc)}
        print(json.dumps({"res": res}))
    """, tmp_path)
    res = out["res"]
    assert res is not None, "a strict mutation record was lost without an exception"
    assert res["runtime"] and res["http"] and res["status"] == 503
    assert "console.action_performed" in res["str"] and "AuditTenantMismatch" in res["str"]
