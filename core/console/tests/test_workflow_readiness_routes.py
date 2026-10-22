"""HTTP E2E for the workflow readiness routes (ADR-2275 / PLAN-0962 S2).

Real FastAPI router + TestClient, sandboxed CORVIN_HOME (same sandbox as the resume tests).
Covers 200 / 401 / 403 (CSRF) / 404 / 413 / 400, cross-tenant isolation of the acceptance case,
the audited override, and that the audit chain still verifies after the new events.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_workflow_resume_hardening import _sandbox  # noqa: E402  (same sandbox, same dir)

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "core/workflows/corvin_workflows/examples/all_node_types.awp.yaml"
ACCEPTANCE = {
    "inputs": {"topic": "x"},
    "stub_responses": {"researcher": {"items": ["a", "b", "c"], "tone": "urgent"}},
    "expect": {"state": "complete", "nodes_reached": ["research", "reply"]},
}
BASE = "/v1/console/workflows"


@pytest.fixture
def env(tmp_path):
    with _sandbox(tmp_path) as (client, home, tid):
        yield client, home, tid


def _make(tid, wid, text, phase="detailing", **meta):
    from forge import paths as _fp
    d = _fp.tenant_home(tid) / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{wid}.meta.json").write_text(json.dumps({"id": wid, "name": wid, "phase": phase, **meta}))
    (d / f"{wid}.awp.yaml").write_text(text)


def _chain_records(tid):
    from forge import paths as _fp
    chain = _fp.tenant_home(tid) / "global" / "forge" / "audit.jsonl"
    return chain, [json.loads(x) for x in chain.read_text().splitlines() if x.strip()]


def test_readiness_report_roundtrip_and_audit(env):
    client, _, tid = env
    _make(tid, "wf1", EXAMPLE.read_text())
    r = client.get(f"{BASE}/wf1/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["synthetic"] is True and "not real execution" in body["notice"]
    sigs = {s["name"]: s["status"] for s in body["signals"]}
    assert sigs["validator"] == "pass" and sigs["reachability"] == "pass" and sigs["acceptance"] == "unmeasured"
    assert body["ready"] is False and body["phase"] == "detailing" and body["acceptance"] is None

    assert client.put(f"{BASE}/wf1/acceptance", json=ACCEPTANCE).status_code == 200
    body = client.get(f"{BASE}/wf1/readiness").json()
    assert body["ready"] is True and body["acceptance"]["expect"]["state"] == "complete"

    import forge.security_events as se
    chain, recs = _chain_records(tid)
    checked = [r for r in recs if r["event_type"] == "workflow.readiness_checked"]
    assert len(checked) == 2
    d = checked[-1]["details"]
    assert d["ready"] is True and d["failing"] == 0 and d["unmeasured"] == 0 and d["synthetic"] is True
    assert d["wid"] == "wf1" and d["tenant_id"] == tid and "elapsed_ms" in d and d["sid_fingerprint"]
    assert "_dropped_fields" not in d
    ok, problems = se.verify_chain(chain)
    assert ok, problems


def test_auth_csrf_and_missing_workflow(env):
    client, _, tid = env
    _make(tid, "wf1", EXAMPLE.read_text())
    # no cookie -> 401 on every route
    saved = dict(client.cookies)
    client.cookies.clear()
    assert client.get(f"{BASE}/wf1/readiness").status_code == 401
    assert client.put(f"{BASE}/wf1/acceptance", json=ACCEPTANCE).status_code == 401
    assert client.post(f"{BASE}/wf1/ready-override", json={"reason": "x"}).status_code == 401
    client.cookies.set("corvin_console_sid", saved["corvin_console_sid"])
    # cookie but no CSRF header -> 403 on writes
    tok = client.headers.pop("X-CSRF-Token")
    assert client.put(f"{BASE}/wf1/acceptance", json=ACCEPTANCE).status_code == 403
    assert client.post(f"{BASE}/wf1/ready-override", json={"reason": "x"}).status_code == 403
    client.headers["X-CSRF-Token"] = tok
    # unknown / malformed wid
    assert client.get(f"{BASE}/nope/readiness").status_code == 404
    assert client.put(f"{BASE}/nope/acceptance", json=ACCEPTANCE).status_code == 404
    assert client.post(f"{BASE}/nope/ready-override", json={"reason": "x"}).status_code == 404
    assert client.get(f"{BASE}/Bad..Id/readiness").status_code in (400, 404)


def test_acceptance_size_and_schema(env):
    client, _, tid = env
    _make(tid, "wf1", EXAMPLE.read_text())
    big = {"inputs": {"k": "x" * (70 * 1024)}, "expect": {"state": "complete"}}
    assert client.put(f"{BASE}/wf1/acceptance", json=big).status_code == 413
    assert client.put(f"{BASE}/wf1/acceptance", json={"expect": {"state": "weird"}}).status_code == 400
    assert client.put(f"{BASE}/wf1/acceptance", json={"unknown": 1}).status_code == 400
    assert client.put(f"{BASE}/wf1/acceptance", content=b"not json",
                      headers={"content-type": "application/json"}).status_code == 400
    meta = json.loads((_meta(tid, "wf1")).read_text())
    assert "acceptance" not in meta  # nothing was stored by a refused request


def _meta(tid, wid):
    from forge import paths as _fp
    return _fp.tenant_home(tid) / "workflows" / f"{wid}.meta.json"


def test_cross_tenant_cannot_read_or_write_the_acceptance_case(env, tmp_path):
    client, home, tid = env
    _make(tid, "wf1", EXAMPLE.read_text())
    assert client.put(f"{BASE}/wf1/acceptance", json=ACCEPTANCE).status_code == 200
    # a second tenant's session
    from corvin_console import auth as _auth
    (home / "tenants" / "tenantb" / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / "tenantb" / "global" / "console" / "sessions").mkdir(parents=True)
    (home / "tenants" / "tenantb" / "global" / "auth").mkdir(parents=True)
    rec = _auth.create_session(tenant_id="tenantb", token_fingerprint="fp-b")
    other = type(client)(client.app, raise_server_exceptions=False)
    other.cookies.set("corvin_console_sid", rec.sid)
    other.headers.update({"X-CSRF-Token": _auth.derive_csrf_token(rec.csrf_secret, rec.sid)})
    assert other.get(f"{BASE}/wf1/readiness").status_code == 404
    assert other.put(f"{BASE}/wf1/acceptance", json={"expect": {"state": "failed"}}).status_code == 404
    assert other.post(f"{BASE}/wf1/ready-override", json={"reason": "x"}).status_code == 404
    kept = json.loads(_meta(tid, "wf1").read_text())["acceptance"]
    assert kept["expect"]["state"] == "complete"
    # tenant b has its own workflow of the same name: it sees no acceptance from tenant a
    from forge import paths as _fp
    d = _fp.tenant_home("tenantb") / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / "wf1.meta.json").write_text(json.dumps({"id": "wf1", "phase": "detailing"}))
    (d / "wf1.awp.yaml").write_text(EXAMPLE.read_text())
    got = other.get(f"{BASE}/wf1/readiness").json()
    assert got["acceptance"] is None and got["ready"] is False


def test_override_needs_reason_is_audited_without_the_text(env):
    client, _, tid = env
    _make(tid, "wf1", EXAMPLE.read_text())
    assert client.post(f"{BASE}/wf1/ready-override", json={}).status_code == 400
    assert client.post(f"{BASE}/wf1/ready-override", json={"reason": "   "}).status_code == 400
    assert client.post(f"{BASE}/wf1/ready-override", json={"reason": "x" * 301}).status_code == 400
    assert json.loads(_meta(tid, "wf1").read_text())["phase"] == "detailing"
    secret = "operator-private-reason-text"
    r = client.post(f"{BASE}/wf1/ready-override", json={"reason": secret})
    assert r.status_code == 200 and r.json() == {"ok": True, "phase": "ready"}
    meta = json.loads(_meta(tid, "wf1").read_text())
    assert meta["phase"] == "ready" and meta["ready_override"]["failing"] == 1
    chain, recs = _chain_records(tid)
    ov = [x for x in recs if x["event_type"] == "workflow.ready_overridden"]
    assert len(ov) == 1 and ov[0]["details"]["reason_len"] == len(secret) and ov[0]["details"]["failing"] == 1
    assert secret not in chain.read_text()
    assert ov[0]["severity"] == "WARNING"
    import forge.security_events as se
    ok, problems = se.verify_chain(chain)
    assert ok, problems


def test_override_cannot_pass_a_draft_that_does_not_validate(env):
    client, _, tid = env
    _make(tid, "wf2", "awp: '1.0.0'\nworkflow: {name: wf2, description: d}\norchestration: {engine: dag, graph: []}\n")
    r = client.post(f"{BASE}/wf2/ready-override", json={"reason": "just do it"})
    assert r.status_code == 409
    assert json.loads(_meta(tid, "wf2").read_text())["phase"] == "detailing"
