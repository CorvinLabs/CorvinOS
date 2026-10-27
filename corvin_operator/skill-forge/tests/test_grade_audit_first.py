"""SkillRegistry.grade is audit-first (review c120 s4-store_audit-3).

Driven through the MCP server's real JSON-RPC loop (``serve()`` over stdin/stdout,
``tools/call skill_grade``) — the way a persona grades a skill — with the core
chain writer failing for ``skill.grade``:

* nothing is persisted (no grade on disk, in neither the manifest nor meta.json);
* the caller gets an error response, never ``ok``;
* the hash chain carries no unchained line and still verifies;
* with a working writer the grade lands together with its ``skill.grade`` record;
* ``dedupe_marker`` writes once and refuses the second write without a record.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(REPO_ROOT / "corvin_operator" / "skill-forge"),
           str(REPO_ROOT / "corvin_operator" / "forge")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

NAME = "assistant.audit_first_demo"


@pytest.fixture()
def srv_env(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_PLUGIN_SLOT_DIR", str(tmp_path / "slot"))
    monkeypatch.setenv("CORVIN_PROJECT_ROOT", "")
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.delenv("CORVIN_CALLER_PERSONA", raising=False)
    from skill_forge.multi_registry import MultiSkillRegistry
    reg = MultiSkillRegistry(tenant_id="_default")
    reg.create(scope="user", name=NAME, type="learned-experience", description="d",
               body_md="# x\n\nbody\n", claim={"summary": "s"})
    return reg


def _serve_grade(run_id: str) -> dict:
    from skill_forge.mcp_server import SkillForgeMCPServer
    req = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
           "params": {"name": "skill_grade",
                      "arguments": {"name": NAME, "run_id": run_id, "score": 0.9}}}
    out = io.StringIO()
    SkillForgeMCPServer(stdin=io.StringIO(json.dumps(req) + "\n"), stdout=out,
                        stderr=io.StringIO()).serve()
    return json.loads(out.getvalue().strip().splitlines()[-1])


def _chain(reg) -> Path:
    return Path(reg.audit_path())


def _grade_records(reg, run_id: str) -> list[dict]:
    p = _chain(reg)
    recs = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    return [r for r in recs if r.get("event_type") == "skill.grade"
            and (r.get("details") or {}).get("run_id") == run_id]


@pytest.mark.parametrize("exc", [RuntimeError, OSError])
def test_failed_record_persists_nothing_and_the_caller_sees_it(srv_env, monkeypatch, exc):
    from skill_forge import registry as sreg
    from forge.security_events import verify_chain
    real = sreg._write_event

    def boom(*a, **k):
        if a[1] == "skill.grade":
            raise exc("writer down")
        return real(*a, **k)
    monkeypatch.setattr(sreg, "_write_event", boom)
    resp = _serve_grade("run-fail")
    assert "error" in resp, resp                                   # never reported as ok
    spec = srv_env.get(NAME)
    assert spec.grades == [] or all(g["run_id"] != "run-fail" for g in spec.grades)
    meta = Path(srv_env._root_for("user")) / "skills" / NAME / "meta.json"
    assert "run-fail" not in meta.read_text()
    lines = [json.loads(x) for x in _chain(srv_env).read_text().splitlines()]
    assert all("hash" in r and "prev_hash" in r for r in lines)    # no unchained line
    assert verify_chain(_chain(srv_env)) == (True, [])
    assert _grade_records(srv_env, "run-fail") == []


def test_working_writer_persists_grade_with_its_record(srv_env):
    resp = _serve_grade("run-ok")
    assert resp["result"]["structuredContent"]["data"]["ok"] is True, resp
    assert [g["run_id"] for g in srv_env.get(NAME).grades] == ["run-ok"]
    assert len(_grade_records(srv_env, "run-ok")) == 1


def test_dedupe_marker_writes_once(srv_env):
    from skill_forge.registry import DuplicateGrade
    srv_env.grade(NAME, "r1", 0.9, notes="n", organic=True, dedupe_marker="abc123")
    with pytest.raises(DuplicateGrade):
        srv_env.grade(NAME, "r2", 0.9, notes="n", organic=True, dedupe_marker="abc123")
    assert [g["run_id"] for g in srv_env.get(NAME).grades] == ["r1"]
    assert _grade_records(srv_env, "r2") == []
