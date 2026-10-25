"""ADR-2283 decision 1 — the L5 k=2 approval gate writes every decision to the core chain.

Drives the REAL gateway app (``corvin_gateway.app.app``) over HTTP. The approval gate is
built by the gateway's own wiring (``_init_approval_gate``, the function the lifespan
calls), so the test proves what the gateway passes as ``audit_backend`` — until
2026-10-25 that was the provider MODULE ``corvin_plugins.providers.audit_backend``,
which has no ``write_event``: every decision raised and none was ever recorded.

A pending approval is seeded through the gate itself (nothing in production fills the
queue yet — ADR-2283 leaves the producer open). Then:
  * approve / reject / revoke over ``POST /v1/approvals/...`` each leave exactly ONE
    hash-chained ``skill.approval.*`` record with metadata only (no reason text, no raw
    operator id — a pseudonymous ``operator_ref``);
  * when the chain cannot commit, the route refuses (500) and the approval stays pending.

The chain is redirected with ``VOICE_AUDIT_PATH`` into a per-test sandbox; the live
install is never touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (
    _REPO / "corvin_operator" / "forge",
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "core" / "gateway",
    _REPO,
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi.testclient import TestClient  # noqa: E402

SKILL = "os.delegation_router"
_HASH_A = "a" * 64
_HASH_B = "b" * 64


@pytest.fixture()
def gateway(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    chain = tmp_path / "chain" / "audit.jsonl"
    chain.parent.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))

    from corvin_gateway import app as app_module
    from core.gateway.routes import approval_routes

    app = app_module.app
    previous_gate = getattr(app.state, "approval_gate", None)
    previous_route_gate = approval_routes._approval_gate
    gate = app_module._init_approval_gate(app)
    assert gate is not None, "the gateway did not build its approval gate"
    # Loopback peer, no Bearer token: the gateway's guard admits the local operator.
    client = TestClient(app, client=("127.0.0.1", 50123), raise_server_exceptions=False)
    try:
        yield app_module, gate, client, chain
    finally:
        app.state.approval_gate = previous_gate
        approval_routes.set_approval_gate(previous_route_gate)


def _records(chain: Path, prefix: str = "skill.approval.") -> list[dict]:
    if not chain.exists():
        return []
    out = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    return [r for r in out if str(r.get("event", r.get("event_type", ""))).startswith(prefix)]


def _event(r: dict) -> str:
    return str(r.get("event", r.get("event_type", "")))


def _seed_pending(gate, metric: str = "confidence_threshold"):
    from core.skills.feedback_stability import DriftAlert

    alert = DriftAlert(skill_id=SKILL, metric_name=metric, smoothed_delta=0.3,
                       drift_threshold=0.15, consecutive_high_deltas=2,
                       recent_deltas=[0.3, 0.31])
    record, auto = gate.request_approval(alert, confidence=0.5,
                                         prev_config_hash=_HASH_A, next_config_hash=_HASH_B)
    assert auto is False
    return record.approval_id


def _post(client, approval_id: str, action: str, reason: str):
    return client.post(f"/v1/approvals/{SKILL}/{approval_id}/{action}",
                       params={"tenant_id": "_default"}, json={"reason": reason})


def test_gateway_wires_a_strict_core_chain_backend(gateway):
    _, gate, _, _ = gateway
    from core.skills.skill_registry_phase1 import CoreAuditBackend

    assert isinstance(gate.audit_backend, CoreAuditBackend)
    assert gate.audit_backend.strict is True
    assert gate.audit_backend.tenant_id == "_default"


def test_each_decision_leaves_one_chained_metadata_only_record(gateway):
    _, gate, client, chain = gateway
    approve_id = _seed_pending(gate, "confidence_threshold")
    reject_id = _seed_pending(gate, "retry_budget")
    requested = _records(chain)
    assert [_event(r) for r in requested] == ["skill.approval.requested"] * 2
    # The gate keeps the strict backend's audit_ref as its record's audit_event_id,
    # and that id resolves to exactly the chained request record.
    assert gate.get_approval_status(approve_id).audit_event_id == requested[0]["details"]["audit_ref"]
    assert gate.get_approval_status(reject_id).audit_event_id == requested[1]["details"]["audit_ref"]

    r = _post(client, approve_id, "approve", "approved")
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
    r = _post(client, reject_id, "reject", "denied")
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
    r = _post(client, approve_id, "revoke", "revoked")
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True

    recs = _records(chain)
    events = [_event(r) for r in recs]
    assert events == [
        "skill.approval.requested", "skill.approval.requested",
        "skill.approval.granted", "skill.approval.denied", "skill.approval.revoked",
    ]
    operator_ref = hashlib.sha256(b"local-operator").hexdigest()[:12]
    for rec in recs[2:]:
        details = rec.get("details", {})
        assert details.get("skill_id") == SKILL
        assert details.get("operator_ref") == operator_ref
        assert "operator_id" not in details
        assert "reason" not in details
        assert "local-operator" not in json.dumps(rec)
        assert not details.get("_dropped_fields"), details
    assert recs[3]["details"]["reason_code"] == "denied"
    assert recs[4]["details"]["reason_code"] == "revoked"

    # The whole file is one intact hash chain.
    from forge import security_events

    ok, problems = security_events.verify_chain(chain)
    assert ok, problems


def test_a_decision_is_refused_when_the_chain_cannot_commit(gateway, tmp_path, monkeypatch):
    _, gate, client, chain = gateway
    approval_id = _seed_pending(gate)
    before = len(_records(chain))

    # The writer's parent is a FILE: the core write cannot commit (audit_event -> False).
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(blocker / "audit.jsonl"))

    r = _post(client, approval_id, "approve", "approved")
    assert r.status_code == 500, r.text
    assert len(_records(chain)) == before
    still = gate.get_approval_status(approval_id)
    assert still is not None and still.decision.value == "pending"
