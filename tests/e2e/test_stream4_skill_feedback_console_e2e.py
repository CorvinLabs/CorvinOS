"""Stream 4 skill-feedback route — over HTTP against the real mounted router.

POST /feedback/skill → GET /feedback/skill/history → GET /feedback/skill/status,
session + CSRF overridden, against a TEMP ``CORVIN_HOME``. Proves the route that
``app.py`` mounts accepts feedback for each Stream skill, rejects malformed input
at the boundary and reads the submitted event back.
"""
from __future__ import annotations

import dataclasses

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.console.corvin_console import auth as session_auth
from core.console.corvin_console import deps as console_deps

BASE = "/v1/console/feedback/skill"


def _fake_session_record(tenant_id: str) -> session_auth.SessionRecord:
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = 1_000_000.0 + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


@pytest.fixture
def client(tmp_path, monkeypatch):
    h = tmp_path / "corvin-home"
    (h / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(h))
    from core.console.corvin_console.routes import stream4_skill_feedback as route

    app = FastAPI()
    app.include_router(route.router, prefix="/v1/console")
    rec = _fake_session_record("_default")
    app.dependency_overrides[console_deps.require_session] = lambda: rec
    app.dependency_overrides[console_deps.require_csrf] = lambda: rec
    app.dependency_overrides[route.require_session] = lambda: rec
    app.dependency_overrides[route.require_session_csrf_on_mutation] = lambda: rec
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _body(skill_id: str, subject_id: str = "task-1", rating: int = 2) -> dict:
    return {"skill_id": skill_id, "subject_id": subject_id, "rating": rating, "category": "accuracy"}


@pytest.mark.parametrize(
    "skill_id", ["os.delegation_router", "os.workflow_optimizer", "os.security_orchestrator", "os.flow_guard"]
)
def test_feedback_is_accepted_for_every_stream_skill(client, skill_id):
    r = client.post(BASE, json=_body(skill_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "received"
    assert body["skill_id"] == skill_id and body["subject_id"] == "task-1"
    assert body["feedback_id"]


def test_submitted_feedback_is_read_back_from_history(client):
    sent = client.post(BASE, json={**_body("os.flow_guard", "flow-42", rating=-1), "reasoning": "secret reason"}).json()
    hist = client.get(f"{BASE}/history", params={"skill_id": "os.flow_guard"})
    assert hist.status_code == 200, hist.text
    body = hist.json()
    assert body["total_count"] == 1 and body["avg_rating"] == -1.0
    ev = body["feedback_events"][0]
    assert ev["feedback_id"] == sent["feedback_id"]
    assert (ev["subject_id"], ev["rating"], ev["category"]) == ("flow-42", -1, "accuracy")
    assert ev["reasoning"] is None  # free text is never stored


def test_history_is_per_skill_and_newest_first(client):
    client.post(BASE, json=_body("os.flow_guard", "a", rating=1))
    client.post(BASE, json=_body("os.workflow_optimizer", "b", rating=2))
    last = client.post(BASE, json=_body("os.flow_guard", "c", rating=0)).json()
    body = client.get(f"{BASE}/history", params={"skill_id": "os.flow_guard", "limit": 1}).json()
    assert body["total_count"] == 2
    assert [e["feedback_id"] for e in body["feedback_events"]] == [last["feedback_id"]]


@pytest.mark.parametrize(
    "patch",
    [
        {"skill_id": "os.unknown"},
        {"rating": 3},
        {"category": "nonsense"},
        {"subject_id": "bad\nid"},
    ],
)
def test_malformed_feedback_is_rejected_at_the_boundary(client, patch):
    r = client.post(BASE, json={**_body("os.flow_guard"), **patch})
    assert r.status_code == 422, r.text
    assert client.get(f"{BASE}/status").json()["total_received"] == 0  # nothing stored


def test_status_counts_what_was_stored(client):
    s0 = client.get(f"{BASE}/status").json()
    assert s0["total_received"] == 0 and s0["last_feedback_timestamp"] is None
    sent = client.post(BASE, json=_body("os.workflow_optimizer")).json()
    s1 = client.get(f"{BASE}/status").json()
    assert s1["total_received"] == 1 and s1["last_feedback_timestamp"] == sent["timestamp"]
    assert "total_processed" not in s1  # nothing unmeasured is reported


def test_feedback_is_chained_before_it_is_stored(client, tmp_path):
    import json

    from core.paths.tenant import corvin_home  # noqa: F401  (sandbox sanity: CORVIN_HOME is the tmp home)
    from corvin_operator.forge.forge.paths import tenant_audit_chain

    r = client.post(BASE, json={**_body("os.flow_guard", "flow-7", rating=-2), "reasoning": "do-not-chain-me"})
    assert r.status_code == 200, r.text
    chain = tenant_audit_chain("_default")
    assert str(chain).startswith(str(tmp_path)), "audit write escaped the sandbox"
    text = chain.read_text()
    recs = [json.loads(line) for line in text.splitlines() if '"skill_feedback"' in line]
    assert recs, "no skill_feedback record was chained"
    d = recs[-1].get("details", recs[-1])
    assert d.get("signal") == "no" and d.get("skill_id") == "os.flow_guard"
    assert "do-not-chain-me" not in text


def test_feedback_closes_the_loop_for_a_skill_that_reads_its_config(client):
    from core.skills.os_skills.skill_adapter import load_skill_config

    assert load_skill_config("os.delegation_router", "_default")[1] is None  # nothing learned yet
    up = client.post(BASE, json=_body("os.delegation_router", "t-1", rating=2)).json()
    assert up["config_applied"] is True and up["config_version"] == "v1"
    cfg, version = load_skill_config("os.delegation_router", "_default")
    assert version == "v1" and round(cfg.confidence_threshold, 2) == 0.75  # 0.70 + 0.05

    down = client.post(BASE, json=_body("os.delegation_router", "t-2", rating=-1)).json()
    assert down["config_applied"] is True and down["config_version"] == "v2"
    assert round(load_skill_config("os.delegation_router", "_default")[0].confidence_threshold, 2) == 0.70

    neutral = client.post(BASE, json=_body("os.delegation_router", "t-3", rating=0)).json()
    assert neutral["config_applied"] is False  # "other" carries no direction
    assert load_skill_config("os.delegation_router", "_default")[1] == "v2"


def test_the_learned_threshold_reaches_the_skill_that_consumes_it(client):
    from core.skills.os_skills.delegation_router import _learned_threshold

    assert _learned_threshold("_default") is None
    client.post(BASE, json=_body("os.delegation_router", "t-1", rating=1))
    value, version = _learned_threshold("_default")
    assert version == "v1" and round(value, 2) == 0.75


def test_feedback_for_a_skill_without_a_config_consumer_changes_nothing(client):
    from core.skills.os_skills.skill_adapter import load_skill_config

    r = client.post(BASE, json=_body("os.flow_guard", "f-1", rating=2)).json()
    assert r["config_applied"] is False and "reads no learned config" in r["message"]
    assert load_skill_config("os.flow_guard", "_default")[1] is None


def test_config_change_is_chained_before_it_is_persisted(client, tmp_path):
    import json

    from core.learning.event_persistence import _resolve_core_audit
    from core.paths.tenant import tenant_home

    client.post(BASE, json=_body("os.delegation_router", "t-9", rating=2))
    events = list((tenant_home("_default") / "learning" / "events").glob("*.jsonl"))
    recs = [json.loads(line) for f in events for line in f.read_text().splitlines()]
    cfg = [r for r in recs if r.get("event_type") == "config_updated" and r.get("skill_id") == "os.delegation_router"]
    assert len(cfg) == 1, recs
    ref = cfg[0]["audit_ref"]
    chain = _resolve_core_audit().audit_path()
    assert str(chain).startswith(str(tmp_path)), "audit write escaped the sandbox"
    assert ref in open(chain).read()  # the chain record exists, and it carries no payload
