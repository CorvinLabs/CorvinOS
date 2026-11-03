"""Unit tests for the per-tenant memory page (routes/memory.py + memory_store.py, ADR A-10).

Hermetic: every test runs against a fresh temp ``CORVIN_HOME``; nothing reads the
operator's real data dir or ``~/.claude``. The live, real-LLM proof is
``tests/e2e/live/test_console_memory_live.py``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "core" / "gateway",
           _REPO / "corvin_operator" / "forge", _REPO / "corvin_operator" / "bridges" / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "corvin"
    (h / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    (h / "global" / "console" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    yield h
    from corvin_console import memory_store
    mod = memory_store.recall_module()
    if mod is not None:
        mod._close_all_connections()  # Windows: release recall.db before tmp cleanup


def _client(tenant_id: str) -> tuple[TestClient, dict]:
    from corvin_console import auth as session_auth
    from corvin_console.routes import memory as memory_routes

    rec = session_auth.create_session(tenant_id=tenant_id)
    app = FastAPI()
    app.include_router(memory_routes.router, prefix="/v1/console")
    c = TestClient(app)
    c.cookies.set("corvin_console_sid", rec.sid)
    return c, {"X-CSRF-Token": session_auth.derive_csrf_token(rec.csrf_secret, rec.sid)}


def _tenant(home: Path, tid: str = "_default") -> Path:
    return home / "tenants" / tid


def _index_turn(tid: str, user: str, answer: str, sid: str = "s1") -> None:
    from corvin_console import memory_store
    memory_store.note_chat_turn(tenant_id=tid, sid=sid, chat_key=f"web:{sid}", role="user",
                                parts=[{"kind": "text", "text": user}], answered=False)
    r = memory_store.note_chat_turn(tenant_id=tid, sid=sid, chat_key=f"web:{sid}",
                                    role="assistant", parts=[{"kind": "text", "text": answer}],
                                    answered=True)
    assert r and r.get("ok"), r


def test_fresh_tenant_is_an_honest_empty_state_and_creates_nothing(home):
    c, _ = _client("_default")
    r = c.get("/v1/console/memory")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_id"] == "_default"
    assert body["available"] is True and body["present"] is False
    assert body["count"] == 0 and body["files"] == []
    assert ".claude" not in r.text
    assert not (_tenant(home) / "global" / "memory").exists(), "a read must not create the store"


def test_add_read_search_delete_note_round_trip(home):
    c, h = _client("_default")
    r = c.post("/v1/console/memory", headers=h,
               json={"title": "Deploy window", "content": "Deploys only on Tuesdays (Zebrafish rule).",
                     "type": "project"})
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    assert name.startswith("note-") and r.json()["content"].startswith("Deploys only")
    files = list((_tenant(home) / "global" / "memory" / "notes").glob("note-*.json"))
    assert [f.stem for f in files] == [name]
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["title"] == "Deploy window" and "created_by" in stored

    idx = c.get("/v1/console/memory").json()
    assert idx["count"] == 1 and idx["sources"]["note"] == 1
    assert idx["files"][0]["name"] == name and idx["files"][0]["source"] == "note"

    one = c.get(f"/v1/console/memory/{name}")
    assert one.status_code == 200 and "Zebrafish" in one.json()["content"]

    hits = c.get("/v1/console/memory/search", params={"q": "zebrafish"}).json()
    assert [x["name"] for x in hits["results"]] == [name]

    assert c.delete(f"/v1/console/memory/{name}", headers=h).status_code == 200
    assert not files[0].exists()
    assert c.get(f"/v1/console/memory/{name}").status_code == 404
    assert c.delete(f"/v1/console/memory/{name}", headers=h).status_code == 404

    chain = (_tenant(home) / "global" / "forge" / "audit.jsonl").read_text(encoding="utf-8")
    actions = [json.loads(l)["details"].get("action") for l in chain.splitlines() if l.strip()
               and json.loads(l).get("event_type") == "console.action_performed"]
    assert "memory.note_add" in actions and "memory.entry_delete" in actions
    assert "Zebrafish" not in chain, "note content must never reach the audit chain"


def test_mutations_require_csrf(home):
    c, h = _client("_default")
    assert c.post("/v1/console/memory", json={"title": "x", "content": "y"}).status_code == 403
    name = c.post("/v1/console/memory", headers=h, json={"title": "x", "content": "y"}).json()["name"]
    assert c.delete(f"/v1/console/memory/{name}").status_code == 403
    assert c.delete(f"/v1/console/memory/{name}", headers={"X-CSRF-Token": "bogus"}).status_code == 403
    assert c.get(f"/v1/console/memory/{name}").status_code == 200


def test_no_session_is_401(home):
    from corvin_console.routes import memory as memory_routes
    app = FastAPI()
    app.include_router(memory_routes.router, prefix="/v1/console")
    assert TestClient(app).get("/v1/console/memory").status_code == 401


def test_non_owner_tier_is_refused(home):
    from corvin_console.routes import memory as memory_routes
    from corvin_console.deps import require_session
    c, _ = _client("_default")

    class _Viewer:
        tier = "viewer"
        tenant_id = "_default"

    c.app.dependency_overrides[require_session] = lambda: _Viewer()
    assert c.get("/v1/console/memory").status_code == 403
    assert memory_routes  # imported for the override target


@pytest.mark.parametrize("body", [
    {"title": "", "content": "x"},
    {"title": "x", "content": ""},
    {"title": "x", "content": "y", "type": "secret"},
    {"title": "x" * 201, "content": "y"},
    {"title": "x", "content": "y" * 20_001},
])
def test_add_validates(home, body):
    c, h = _client("_default")
    assert c.post("/v1/console/memory", headers=h, json=body).status_code == 422


def test_tenants_are_isolated(home):
    a, ha = _client("_default")
    b, hb = _client("acme")
    name = a.post("/v1/console/memory", headers=ha,
                  json={"title": "A secret", "content": "only tenant A"}).json()["name"]
    _index_turn("_default", "what is the A codeword", "The A codeword is OKAPI.")

    ib = b.get("/v1/console/memory").json()
    assert ib["tenant_id"] == "acme" and ib["count"] == 0 and ib["files"] == []
    assert b.get(f"/v1/console/memory/{name}").status_code == 404
    assert b.get("/v1/console/memory/turn-1").status_code == 404
    assert b.get("/v1/console/memory/search", params={"q": "OKAPI"}).json()["results"] == []
    assert b.delete(f"/v1/console/memory/{name}", headers=hb).status_code == 404
    assert b.delete("/v1/console/memory/turn-1", headers=hb).status_code == 404
    ia = a.get("/v1/console/memory").json()
    assert ia["sources"] == {**ia["sources"], "note": 1, "conversation": 1}


def test_chat_turns_are_indexed_redacted_searchable_and_deletable(home):
    from corvin_console import memory_store
    c, h = _client("_default")
    _index_turn("_default", "mail me at jane.doe@example.com about the Narwhal plan",
                "Will do — the Narwhal plan is on track.")
    # Not conversation: refusals, asides, slash commands, unanswered turns.
    memory_store.note_chat_turn(tenant_id="_default", sid="s2", chat_key="web:s2", role="user",
                                parts=[{"kind": "text", "text": "/help"}], answered=False)
    assert memory_store.note_chat_turn(tenant_id="_default", sid="s2", chat_key="web:s2",
                                       role="assistant", parts=[{"kind": "text", "text": "help"}],
                                       answered=False) is None
    memory_store.note_chat_turn(tenant_id="_default", sid="s3", chat_key="web:s3", role="user",
                                parts=[{"kind": "text", "text": "do something bad"}], answered=False)
    assert memory_store.note_chat_turn(tenant_id="_default", sid="s3", chat_key="web:s3",
                                       role="assistant", parts=[{"kind": "text", "text": "no"}],
                                       answered=True, refused=True) is None

    idx = c.get("/v1/console/memory").json()
    turns = [f for f in idx["files"] if f["source"] == "conversation"]
    assert idx["recall"]["turns"] == 1 and len(turns) == 1
    assert turns[0]["origin"] == "web:s1"
    assert (_tenant(home) / "global" / "memory" / "recall.db").is_file()

    one = c.get(f"/v1/console/memory/{turns[0]['name']}").json()
    assert "jane.doe@example.com" not in json.dumps(one)
    assert "<redacted:email>" in one["user_text"] and "email" in one["redacted_classes"]

    hits = c.get("/v1/console/memory/search", params={"q": "narwhal"}).json()["results"]
    assert [x["name"] for x in hits] == [turns[0]["name"]]

    assert c.delete(f"/v1/console/memory/{turns[0]['name']}", headers=h).status_code == 200
    assert c.get("/v1/console/memory").json()["recall"]["turns"] == 0


def test_forget_chat_erases_its_turns(home):
    from corvin_console import memory_store
    _index_turn("_default", "first question", "first answer", sid="keep")
    _index_turn("_default", "second question", "second answer", sid="gone")
    assert memory_store.forget_chat("_default", "web:gone") == 1
    left = memory_store.recall_module().list_turns(tenant_id="_default")
    assert [t["chat_key"] for t in left] == ["web:keep"]


def test_tenant_files_are_listed_and_session_entries_are_read_only(home):
    t = _tenant(home)
    mem = t / "global" / "memory"
    (mem / "worker_harvest").mkdir(parents=True)
    (mem / "travel.md").write_text("# Travel\nPrefers trains.\n", encoding="utf-8")
    (mem / "worker_harvest" / "harvest_1_00_db-port.md").write_text(
        "---\nname: db-port\ndescription: Worker-harvested entry\nmetadata:\n  type: reference\n---\n\n"
        "The staging DB listens on 5433.\n", encoding="utf-8")
    sess_mem = t / "sessions" / "web_abc" / ".claude" / "memory"
    sess_mem.mkdir(parents=True)
    (sess_mem / "fact.md").write_text("Session fact.\n", encoding="utf-8")
    (t / "forge").mkdir()
    (t / "forge" / "memory.md").write_text("# forge memory\n\n## 2026-11-01 10:00:00\n\nuse uv\n",
                                           encoding="utf-8")
    c, h = _client("_default")
    idx = c.get("/v1/console/memory").json()
    by_source = {f["source"]: f for f in idx["files"]}
    assert set(by_source) == {"topic", "harvest", "session", "forge"}
    assert by_source["harvest"]["type"] == "reference" and by_source["harvest"]["title"] == "db-port"
    assert by_source["topic"]["name"] == "topic-travel"
    assert "5433" in c.get(f"/v1/console/memory/{by_source['harvest']['name']}").json()["content"]
    assert c.delete(f"/v1/console/memory/{by_source['session']['name']}", headers=h).status_code == 403
    assert c.delete("/v1/console/memory/forge-notes", headers=h).status_code == 403
    assert (sess_mem / "fact.md").exists()
    assert c.delete("/v1/console/memory/topic-travel", headers=h).status_code == 200
    assert not (mem / "travel.md").exists()


@pytest.mark.parametrize("name", [
    "..", "topic-..", "topic-../../../secret", "harvest-..%2F..%2Fsecret", "note-../x",
    "turn-0", "turn-abc", "turn--1", "session-zzz", "%2e%2e",
])
def test_names_cannot_traverse(home, name):
    outside = home / "secret.md"
    outside.write_text("TOP-SECRET-MARKER", encoding="utf-8")
    c, h = _client("_default")
    r = c.get(f"/v1/console/memory/{name}")
    assert r.status_code == 404 and "TOP-SECRET-MARKER" not in r.text
    assert c.delete(f"/v1/console/memory/{name}", headers=h).status_code in (404, 405)
    assert outside.exists()


def test_symlink_out_of_the_tenant_is_not_followed(home, tmp_path):
    outside = tmp_path / "host-memory"
    outside.mkdir()
    (outside / "leak.md").write_text("HOST-MARKER", encoding="utf-8")
    mem = _tenant(home) / "global" / "memory"
    mem.mkdir(parents=True)
    try:
        (mem / "worker_harvest").symlink_to(outside, target_is_directory=True)
        (mem / "leak.md").symlink_to(outside / "leak.md")
    except OSError:
        pytest.skip("symlinks not permitted on this host")
    c, _ = _client("_default")
    r = c.get("/v1/console/memory")
    assert r.json()["files"] == [] and "HOST-MARKER" not in r.text
    assert "HOST-MARKER" not in c.get("/v1/console/memory/search", params={"q": "HOST"}).text


def test_console_chat_append_turn_indexes_and_delete_session_forgets(home):
    """The real chat_runtime persistence path feeds the recall store (redacted) and
    deleting the chat erases its turns again."""
    from corvin_console import chat_runtime, memory_store
    sess = chat_runtime.create_session("_default", title="memory hook")
    chat_runtime._append_turn(sess, "user", [{"kind": "text", "text": "call +49 30 1234567 re Quokka"}])
    chat_runtime._append_turn(sess, "assistant", [{"kind": "text", "text": "Noted the Quokka call."}],
                              cli_spawned=True)
    # A gate refusal and a notice are not conversation.
    chat_runtime._append_turn(sess, "user", [{"kind": "text", "text": "refused ask"}])
    chat_runtime._append_turn(sess, "assistant", [{"kind": "text", "text": "refusal"}],
                              gate_refused="pre_spawn")
    chat_runtime._append_turn(sess, "assistant", [{"kind": "text", "text": "notice"}], notice="resume")
    turns = memory_store.recall_module().list_turns(tenant_id="_default")
    assert len(turns) == 1 and turns[0]["chat_key"] == sess.chat_key
    assert "1234567" not in turns[0]["user_text"] and "Quokka" in turns[0]["user_text"]
    assert chat_runtime.delete_session("_default", sess.sid)
    assert memory_store.recall_module().list_turns(tenant_id="_default") == []
