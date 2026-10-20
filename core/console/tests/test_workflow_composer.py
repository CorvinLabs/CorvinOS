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


# ── Attachments, gates, snapshot (CONCEPT-0115 P3) ─────────────────────────

def _upload(client, wid, files):
    return client.post(f"/v1/console/workflows/{wid}/attachments", files=files)


def _att_dir(tid, wid):
    from forge import paths as _fp
    return _fp.tenant_home(tid) / "workflows" / wid / "attachments"


def _record_gates(monkeypatch, refuse_when=None):
    """Wrap the shared pre-spawn gate: record (channel, prompt) of EVERY call, refuse on demand."""
    import corvin_console.routes.workflows as wfroutes
    calls: list[tuple[str, str]] = []
    real = wfroutes._spawn_gates.check_console_spawn_or_refusal

    def _gate(prompt, *, tenant_id, channel, **kw):
        calls.append((channel, prompt))
        if refuse_when and refuse_when in prompt:
            return "Refused by the test gate."
        return None
    monkeypatch.setattr(wfroutes._spawn_gates, "check_console_spawn_or_refusal", _gate)
    return calls, real


def test_upload_stores_under_tenant_home_hides_paths_and_audits_without_the_name(tmp_path):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        make_workflow(tid, "wf1")
        c = new_client()
        r = _upload(c, "wf1", [("files", ("../../evil name.csv", b"a,b\n1,2\n", "text/csv"))])
        assert r.status_code == 200, r.text
        att = r.json()["attachments"][0]
        assert set(att) == {"id", "name", "size", "mime"}  # no filesystem path out
        assert "/" not in att["name"] and att["size"] == 8
        stored = _att_dir(tid, "wf1") / att["id"]
        assert (stored / "data" / att["name"]).read_bytes() == b"a,b\n1,2\n"
        assert json.loads((stored / "meta.json").read_text())["sha256"]
        ev = [x for x in audit_records(tid) if x.get("event_type", x.get("event")) == "workflow.attachment_uploaded"
              or x.get("type") == "workflow.attachment_uploaded"]
        assert ev, "upload must be audited with its own event"
        blob = json.dumps(ev)
        assert "evil" not in blob and att["id"] in blob and "sha256_prefix" in blob
        # download round-trips, delete removes the files
        g = c.get(f"/v1/console/workflows/wf1/attachments/{att['id']}")
        assert g.status_code == 200 and g.content == b"a,b\n1,2\n"
        assert c.delete(f"/v1/console/workflows/wf1/attachments/{att['id']}").status_code == 200
        assert not stored.exists()
        assert c.get(f"/v1/console/workflows/wf1/attachments/{att['id']}").status_code == 404


def test_upload_needs_csrf_and_a_session_and_caps_the_batch(tmp_path):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        make_workflow(tid, "wf1")
        from fastapi.testclient import TestClient
        c = new_client()
        no_csrf = TestClient(app, raise_server_exceptions=False)
        no_csrf.cookies.set("corvin_console_sid", c.cookies.get("corvin_console_sid"))
        assert _upload(no_csrf, "wf1", [("files", ("a.txt", b"x", "text/plain"))]).status_code == 403
        anon = TestClient(app, raise_server_exceptions=False)
        assert _upload(anon, "wf1", [("files", ("a.txt", b"x", "text/plain"))]).status_code == 401
        eleven = [("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(11)]
        assert _upload(c, "wf1", eleven).status_code == 400
        assert not _att_dir(tid, "wf1").exists() or not any(_att_dir(tid, "wf1").iterdir())
        assert _upload(c, "../x", [("files", ("a.txt", b"x", "text/plain"))]).status_code in (400, 404, 422)


def test_cross_tenant_attachment_is_invisible_everywhere(tmp_path):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        for t in ("tenant_b",):
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True, exist_ok=True)
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True, exist_ok=True)
            (home / "tenants" / t / "global" / "console" / "sessions").mkdir(parents=True, exist_ok=True)
        make_workflow(tid, "wf1")
        make_workflow("tenant_b", "wf1")  # same wid in the other tenant
        a, b = new_client(tid), new_client("tenant_b")
        att = _upload(a, "wf1", [("files", ("a.txt", b"secret-a", "text/plain"))]).json()["attachments"][0]
        # B reads/deletes A's id: the path is built from B's session -> not found.
        assert b.get(f"/v1/console/workflows/wf1/attachments/{att['id']}").status_code == 404
        assert b.delete(f"/v1/console/workflows/wf1/attachments/{att['id']}").status_code == 404
        # B references it on its own design socket: refused, audited, nothing spawned.
        with b.websocket_connect("/v1/console/workflows/wf1/chat") as ws:
            ws.receive_json(); ws.receive_json()
            ws.send_json({"type": "user", "text": "use it", "attachments": [att["id"]]})
            err = ws.receive_json()
            assert err["type"] == "error" and err["code"] == 404
        assert (_att_dir(tid, "wf1") / att["id"]).exists()  # A's file untouched
        # (The audit writer drops events whose tenant is not the PROCESS tenant — AuditTenantMismatch —
        # so a second tenant's refusal cannot be asserted in a chain here; the refusal itself is.)


def test_design_message_with_attachment_passes_every_gate_and_the_engine_sees_the_text(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        make_workflow(tid, "wf1", phase="detailing")  # detailing -> chat prompt AND yaml prompt spawn
        import corvin_console.routes.workflows as wfroutes
        calls, _ = _record_gates(monkeypatch)
        spawned: list[str] = []

        def _fake_spawn(prompt):
            spawned.append(prompt)
            return "Understood."
        monkeypatch.setattr(wfroutes, "_run_claude_for_design", _fake_spawn)
        c = new_client()
        att = _upload(c, "wf1", [("files", ("brief.md", b"Quarterly KPI brief: revenue 7", "text/markdown"))]).json()["attachments"][0]
        with c.websocket_connect("/v1/console/workflows/wf1/chat") as ws:
            ws.receive_json(); ws.receive_json()
            ws.send_json({"type": "user", "text": "build it from the brief", "attachments": [att["id"]]})
            frames = []
            while True:
                f = ws.receive_json()
                frames.append(f)
                if f["type"] == "message" and f["role"] == "assistant":
                    break
        user_frame = next(f for f in frames if f["type"] == "message" and f["role"] == "user")
        assert user_frame["attachments"][0]["id"] == att["id"]
        channels = [ch for ch, _ in calls]
        # input gate, chat-prompt gate AND the separately built yaml prompt gate all ran
        assert {"workflow-design-input", "workflow-design", "workflow-design-yaml"} <= set(channels)
        assert all("Quarterly KPI brief" in p for ch, p in calls if ch in ("workflow-design-input", "workflow-design"))
        assert spawned and all("Quarterly KPI brief" in p for p in spawned[:1])


def test_non_text_attachment_is_refused_before_any_spawn(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        make_workflow(tid, "wf1")
        import corvin_console.routes.workflows as wfroutes
        monkeypatch.setattr(wfroutes, "_run_claude_for_design",
                            lambda p: (_ for _ in ()).throw(AssertionError("must not spawn")))
        c = new_client()
        att = _upload(c, "wf1", [("files", ("scan.pdf", b"%PDF-1.4 binary", "application/pdf"))]).json()["attachments"][0]
        with c.websocket_connect("/v1/console/workflows/wf1/chat") as ws:
            ws.receive_json(); ws.receive_json()
            ws.send_json({"type": "user", "text": "read it", "attachments": [att["id"]]})
            err = ws.receive_json()
            assert err["type"] == "error" and err["code"] == 415 and "text-based" in err["message"]


def test_attachment_changed_on_disk_after_upload_is_refused(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        make_workflow(tid, "wf1")
        import corvin_console.routes.workflows as wfroutes
        monkeypatch.setattr(wfroutes, "_run_claude_for_design",
                            lambda p: (_ for _ in ()).throw(AssertionError("must not spawn")))
        c = new_client()
        att = _upload(c, "wf1", [("files", ("a.txt", b"original", "text/plain"))]).json()["attachments"][0]
        (_att_dir(tid, "wf1") / att["id"] / "data" / att["name"]).write_bytes(b"tampered")
        with c.websocket_connect("/v1/console/workflows/wf1/chat") as ws:
            ws.receive_json(); ws.receive_json()
            ws.send_json({"type": "user", "text": "go", "attachments": [att["id"]]})
            err = ws.receive_json()
            assert err["type"] == "error" and err["code"] == 409


def test_gate_refused_line_is_stored_as_hash_and_never_replayed(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        wf_dir = make_workflow(tid, "wf1", phase="detailing")
        import corvin_console.routes.workflows as wfroutes
        calls, _ = _record_gates(monkeypatch, refuse_when="FORBIDDEN-MARKER")
        spawned: list[str] = []
        monkeypatch.setattr(wfroutes, "_run_claude_for_design", lambda p: spawned.append(p) or "ok")
        c = new_client()
        with c.websocket_connect("/v1/console/workflows/wf1/chat") as ws:
            ws.receive_json(); ws.receive_json()
            ws.send_json({"type": "user", "text": "please FORBIDDEN-MARKER now"})
            u, a = ws.receive_json(), ws.receive_json()
            assert u["refused"] is True and "FORBIDDEN-MARKER" not in json.dumps(u)
            assert "Refused by the test gate" in a["content"]
            assert spawned == []
            # a later, allowed turn: its prompts (chat + 12-line yaml history) never contain the refused text
            ws.send_json({"type": "user", "text": "a harmless follow-up"})
            while True:
                f = ws.receive_json()
                if f["type"] == "message" and f["role"] == "assistant":
                    break
        raw = (wf_dir / "wf1.chat.jsonl").read_text()
        assert "FORBIDDEN-MARKER" not in raw
        line = next(json.loads(x) for x in raw.splitlines() if '"refused"' in x)
        assert len(line["user_sha256"]) == 64 and line["user_len"] == len("please FORBIDDEN-MARKER now")
        assert spawned and all("FORBIDDEN-MARKER" not in p for p in spawned)
        assert all("FORBIDDEN-MARKER" not in p for ch, p in calls if ch != "workflow-design-input")


def test_yaml_generation_is_gated_on_its_own_prompt(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        import corvin_console.routes.workflows as wfroutes
        calls, _ = _record_gates(monkeypatch, refuse_when="Based on this conversation")
        monkeypatch.setattr(wfroutes, "_run_claude_for_design",
                            lambda p: (_ for _ in ()).throw(AssertionError("must not spawn")))
        assert wfroutes._generate_awp_yaml([], "hi", "wf1", tid) is None
        assert calls and calls[0][0] == "workflow-design-yaml"
        # no tenant => refused, never an ungated spawn
        assert wfroutes._generate_awp_yaml([], "hi", "wf1") is None


def test_run_snapshots_attachments_and_refuses_a_changed_one(tmp_path):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        # one `code` node: the console runner refuses it AFTER the snapshot step, so no engine spawns
        yaml_text = _TWO_STEP.replace("type: agent\n      agent: assistant\n      depends_on: []",
                                      "type: code\n      depends_on: []", 1)
        wf_dir = make_workflow(tid, "wf1", yaml_text, phase="ready")
        c = new_client()
        att = _upload(c, "wf1", [("files", ("data.csv", b"x,y\n1,2\n", "text/csv"))]).json()["attachments"][0]
        r = c.post("/v1/console/workflows/wf1/runs", json={"inputs": {}, "dry_run": False})
        assert r.status_code == 200, r.text
        rid = r.headers["X-Run-Id"]
        run_meta = json.loads((wf_dir / "wf1" / "runs" / f"{rid}.meta.json").read_text())
        assert run_meta["attachments"][0]["id"] == att["id"]
        snap = wf_dir / "wf1" / "runs" / rid / "attachments" / att["id"] / "data" / att["name"]
        assert snap.read_bytes() == b"x,y\n1,2\n"
        assert any("workflow.attachment_snapshot" in json.dumps(x) for x in audit_records(tid))
        # a changed attachment refuses the next run
        (_att_dir(tid, "wf1") / att["id"] / "data" / att["name"]).write_bytes(b"changed")
        r2 = c.post("/v1/console/workflows/wf1/runs", json={"inputs": {}, "dry_run": False})
        assert "Attachment check failed" in r2.text
        meta2 = json.loads((wf_dir / "wf1" / "runs" / f"{r2.headers['X-Run-Id']}.meta.json").read_text())
        assert meta2["status"] == "failed" and meta2["error"] == "attachment changed"


def test_resume_reply_passes_the_spawn_gate_and_a_refusal_leaves_the_run_paused(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        from forge import paths as _fp
        wf_dir = _fp.tenant_home(tid) / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        (wf_dir / "wf1.meta.json").write_text(json.dumps({"id": "wf1", "phase": "ready"}))
        yaml_p = wf_dir / "wf1.awp.yaml"
        yaml_p.write_text('''awp: "1.1.0"
workflow:
  name: resume_gate_test
  description: x
inputs: {}
orchestration:
  engine: dag
  graph:
    - id: confirm
      type: ask_human
      depends_on: []
      channel: discord
      chat_id: "555"
      prompt: "Confirm?"
      expect:
        field: confirmed
        type: boolean
''')
        from corvin_workflows import DAGRunner, StubEngine
        from corvin_workflows.storage import load_workflow
        res = DAGRunner(load_workflow(str(yaml_p)), engine=StubEngine(), tenant_id=tid).run(inputs={})
        assert res.state == "paused"
        calls, _ = _record_gates(monkeypatch, refuse_when="NOPE-MARKER")
        c = new_client()
        r = c.post(f"/v1/console/workflows/wf1/runs/{res.run_id}/resume", json={"reply": "NOPE-MARKER ja"})
        assert r.status_code == 403 and r.json()["detail"]["error"] == "refused"
        assert [ch for ch, _ in calls] == ["workflow-resume"]
        ok = c.post(f"/v1/console/workflows/wf1/runs/{res.run_id}/resume", json={"reply": "ja"})
        assert ok.status_code == 200, ok.text  # still paused, so the clean reply resumes it


# ── Art. 17 (CONCEPT-0115 P5) ──────────────────────────────────────────────

def test_art17_erasure_removes_the_subjects_attachment_files_and_run_snapshots_only(tmp_path):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        yaml_text = _TWO_STEP.replace("type: agent\n      agent: assistant\n      depends_on: []",
                                      "type: code\n      depends_on: []", 1)
        wf_dir = make_workflow(tid, "wf1", yaml_text, phase="ready")
        a, b = new_client(), new_client()  # two sessions = two uploader identities
        att_a = _upload(a, "wf1", [("files", ("mine.txt", b"subject data", "text/plain"))]).json()["attachments"][0]
        att_b = _upload(b, "wf1", [("files", ("other.txt", b"someone else", "text/plain"))]).json()["attachments"][0]
        a.post("/v1/console/workflows/wf1/runs", json={"inputs": {}, "dry_run": False})  # snapshots both

        root = _att_dir(tid, "wf1")
        subject = json.loads((root / att_a["id"] / "meta.json").read_text())["uploaded_by"]
        other = json.loads((root / att_b["id"] / "meta.json").read_text())["uploaded_by"]
        assert subject != other
        snaps = list((wf_dir / "wf1" / "runs").glob(f"*/attachments/{att_a['id']}/data/mine.txt"))
        assert snaps and root.joinpath(att_a["id"], "data", "mine.txt").exists()

        from erasure_handlers import WorkflowChatHandler
        res = WorkflowChatHandler(tenant_id=tid).purge(subject, "er-att")
        assert res.status.value == "applied", res

        # file proof: the subject's bytes are gone from the store AND from every run snapshot ...
        assert not (root / att_a["id"]).exists()
        assert not list((wf_dir / "wf1" / "runs").glob(f"*/attachments/{att_a['id']}"))
        assert not [p for p in home.rglob("*") if p.is_file() and p.read_bytes() == b"subject data"]
        # ... and the other uploader's attachment, record and snapshot are untouched.
        assert (root / att_b["id"] / "data" / "other.txt").read_bytes() == b"someone else"
        assert (root / att_b["id"] / "meta.json").exists()
        assert list((wf_dir / "wf1" / "runs").glob(f"*/attachments/{att_b['id']}/data/other.txt"))
