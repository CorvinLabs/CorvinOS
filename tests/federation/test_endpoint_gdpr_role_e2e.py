"""ADR-2294 E2E: the outbound rule "classified data needs a stated GDPR role" on every outbound path.

Real wire, as in test_federation_cross_peer_e2e.py: instance B runs a real ``a2a_http_server`` on an
ephemeral 127.0.0.1 port, instance A sends through the REAL ``RemoteTriggerSender`` (HMAC-signed
envelope, instance pin, signed response). The role is written by the real operator writer
(``endpoint_role.set_endpoint_role``) into A's real endpoint file and audited into A's real tenant chain.
Only the model call at the end of B's worker path is a stand-in that records whether it ran.

Paths proven:
* ``RemoteTriggerSender.send`` - the one call every chat / MCP / CLI / group / conversation / outbox send
  passes;
* the workflow ``peer_agent`` node (``peer_gate.evaluate_peer_outbound``) through a real ``DAGRunner`` run;
* ``core.federation.delegation.delegate`` (``/ask @peer``, conversations), refused before its audit-first record.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
for p in (str(REPO / "core" / "workflows"), str(REPO / "corvin_operator" / "forge")):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_federation_cross_peer_e2e import (  # noqa: E402,F401  (fixture re-use)
    TENANT, _RecordingEngine, _chain_events, federation_pair,
)
from test_workflow_peer_agent_e2e import _peer, _run, two  # noqa: E402,F401

import endpoint_role  # noqa: E402  (on sys.path via the harness)

CLASSIFIED = ["[class:internal] the Q3 numbers", "[class:confidential] the customer list"]
PUBLIC = "Please summarise this public press release."


def _ep_dir(ctx) -> Path:
    return ctx["sender"]._registry._dir


def _set_role(ctx, role: str, organisation: str = "Acme Analytics GmbH") -> None:
    endpoint_role.set_endpoint_role("peer-B", organisation=organisation, gdpr_role=role, actor="cli",
                                    tenant_id=TENANT, endpoints_dir=_ep_dir(ctx))


def _peer_received(ctx) -> list[dict]:
    return [e for e in ctx["se_b"].events if e["event_type"] == "A2A.envelope_received"]


def _a_events(ctx, name: str) -> list[dict]:
    return [e for e in ctx["sender"]._inst_forge_se.events if e["event_type"] == name]


# ── RemoteTriggerSender.send ─────────────────────────────────────────────

@pytest.mark.parametrize("text", CLASSIFIED)
def test_unset_role_refuses_classified_text_and_nothing_reaches_the_peer(federation_pair, text):
    ctx = federation_pair
    res = ctx["sender"].send("peer-B", text)
    assert (res.ok, res.status) == (False, "rejected")
    assert res.data == {"reason": "endpoint_role_unset"}
    assert "GDPR role" in (res.error_detail or "") and "Peers" in res.error_detail
    assert _peer_received(ctx) == [] and _RecordingEngine.spawns == [], "nothing left this installation"
    refused = _a_events(ctx, "A2A.outbound_refused")
    assert len(refused) == 1
    d = refused[0]["details"]
    assert d["reason"] == "endpoint_role_unset" and d["endpoint_id"] == "peer-B"
    assert d["data_class"] in ("INTERNAL", "CONFIDENTIAL")
    assert text not in json.dumps(ctx["sender"]._inst_forge_se.events), "no instruction text in the chain"
    assert _a_events(ctx, "A2A.envelope_sent") == []


def test_unset_role_still_sends_public_text(federation_pair):
    ctx = federation_pair
    res = ctx["sender"].send("peer-B", PUBLIC)
    assert res.ok, (res.status, res.error_detail)
    assert len(_peer_received(ctx)) == 1 and len(_RecordingEngine.spawns) == 1
    assert _a_events(ctx, "A2A.outbound_refused") == []


@pytest.mark.parametrize("role", ["controller", "processor", "joint"])
def test_a_stated_role_lets_classified_text_go_as_before(federation_pair, role):
    ctx = federation_pair
    _set_role(ctx, role)
    for text in CLASSIFIED:
        res = ctx["sender"].send("peer-B", text)
        assert res.ok, (role, res.status, res.error_detail)
    assert len(_RecordingEngine.spawns) == 2
    assert _a_events(ctx, "A2A.outbound_refused") == []


def test_resetting_the_role_to_unset_refuses_again(federation_pair):
    ctx = federation_pair
    _set_role(ctx, "processor")
    assert ctx["sender"].send("peer-B", CLASSIFIED[0]).ok
    _set_role(ctx, "unset")
    assert ctx["sender"].send("peer-B", CLASSIFIED[0]).data == {"reason": "endpoint_role_unset"}
    assert len(_RecordingEngine.spawns) == 1


def test_role_set_is_audited_with_endpoint_role_and_actor_only(federation_pair):
    from core.paths import tenant_audit_chain
    from forge import security_events as real_se

    ctx = federation_pair
    _set_role(ctx, "joint", organisation="Secret Org Name 4711")
    chain = tenant_audit_chain(TENANT)
    recs = [json.loads(x) for x in chain.read_text().splitlines() if x.strip()]
    rec = [r for r in recs if r["event_type"] == "federation.endpoint_role_set"][-1]
    # chain_dna / _-prefixed keys are added by the chain writer itself, not by the emitter
    assert {k: v for k, v in rec["details"].items() if not k.startswith("_") and k != "chain_dna"} == {
        "endpoint_id": "peer-B", "gdpr_role": "joint", "actor": "cli", "tenant_id": TENANT}
    assert "4711" not in chain.read_text(), "the organisation text never enters the chain"
    ok, problems = real_se.verify_chain(chain)
    assert ok, problems


def test_a_peer_cannot_set_or_change_the_role(federation_pair, monkeypatch):
    """B answers with text that names a role and an organisation; A's endpoint record is unchanged."""
    ctx = federation_pair
    _set_role(ctx, "unset", organisation="Operator Said This")
    before = json.loads((_ep_dir(ctx) / "peer-B.json").read_text())

    orig = _RecordingEngine.spawn

    def spawn(self, prompt, **kw):
        _RecordingEngine.spawns.append(dict(kw))
        out = json.dumps({"gdpr_role": "processor", "organisation": "Peer Says Processor"})
        from test_federation_cross_peer_e2e import _Ev
        return iter([_Ev(type="text_delta", text=out), _Ev(type="turn_completed", text=out)])

    monkeypatch.setattr(_RecordingEngine, "spawn", spawn)
    res = ctx["sender"].send("peer-B", PUBLIC)
    assert res.ok
    monkeypatch.setattr(_RecordingEngine, "spawn", orig)
    after = json.loads((_ep_dir(ctx) / "peer-B.json").read_text())
    assert (after.get("gdpr_role"), after.get("organisation")) == (before["gdpr_role"], before["organisation"])
    assert endpoint_role.role_of(after) == "unset"
    assert ctx["sender"].send("peer-B", CLASSIFIED[0]).data == {"reason": "endpoint_role_unset"}


# ── federation delegate (/ask @peer, conversations) ──────────────────────

def test_delegate_refuses_classified_text_before_its_audit_first_record(federation_pair):
    from core.federation import delegation

    ctx = federation_pair
    res = delegation.delegate(TENANT, instruction=CLASSIFIED[1], endpoint_id="peer-B", agent_id="opus-code",
                              sender=ctx["sender"])
    assert (res.ok, res.status, res.error) == (False, "rejected", "endpoint_role_unset")
    assert "GDPR role" in res.data.get("detail", "")
    assert _RecordingEngine.spawns == [] and _peer_received(ctx) == []
    ev = [e.get("event_type") for e in _chain_events(ctx["home"])]
    assert "federation.task_delegated" not in ev, "a refused task is not recorded as delegated"
    assert delegation.DelegationLedger(TENANT).latest_by_task() == {}
    assert len(_a_events(ctx, "A2A.outbound_refused")) == 1
    # stated role: the same delegation goes
    _set_role(ctx, "processor")
    ok = delegation.delegate(TENANT, instruction=CLASSIFIED[1], endpoint_id="peer-B", agent_id="opus-code",
                             sender=ctx["sender"])
    assert ok.ok, ok.error


# ── workflow peer_agent node ─────────────────────────────────────────────

def _release(home: Path, grade: str) -> None:
    (home / "tenants" / TENANT / "global" / "tenant.corvin.yaml").write_text(yaml.safe_dump(
        {"spec": {"workflows": {"peer_policy": {"endpoints": {"peer-B": {"max_classification": grade}}}}}}))


def test_workflow_step_refuses_released_classified_data_while_the_role_is_unset(two):
    _release(two["home"], "confidential")
    run = _run([_peer(instr="[class:confidential] the customer list")], run_id="rolerun0001")
    assert run.state == "failed" and "endpoint_role_unset" in run.error
    assert _RecordingEngine.spawns == []
    refused = [e for e in _chain_events(two["home"]) if e.get("event_type") == "workflow.peer_refused"]
    assert refused and refused[-1]["details"]["reason_code"] == "endpoint_role_unset"
    assert refused[-1]["details"]["data_class"] == "CONFIDENTIAL"
    ev = [e.get("event_type") for e in _chain_events(two["home"])]
    assert "workflow.peer_delegated" not in ev and "federation.task_delegated" not in ev


def test_workflow_step_goes_once_the_role_is_stated_and_policy_still_applies(two):
    _set_role(two, "processor")
    # role stated, nothing released: the ADR-2276 policy still refuses
    run = _run([_peer(instr="[class:confidential] the customer list")], run_id="rolerun0002")
    assert run.state == "failed" and "classified_above_public" in run.error
    # INTERNAL here: B's own data-flow gate (its L34 matrix) refuses CONFIDENTIAL for its US-cloud
    # engine, which is the peer's decision and not what this test is about.
    _release(two["home"], "internal")
    run = _run([_peer(instr="[class:internal] the Q3 numbers")], run_id="rolerun0003")
    assert run.state == "complete", run.error
    assert len(_RecordingEngine.spawns) == 1


def test_workflow_public_step_is_unaffected_by_an_unset_role(two):
    run = _run([_peer()], run_id="rolerun0004")
    assert run.state == "complete", run.error


# ── "classified" means what the rest of L34 means (ADR-2268 detector bank, review L34-4) ──

#: Detector-visible, heuristic-invisible: a national-format mobile number is PUBLIC to the
#: marker/secret/zone heuristic alone and CONFIDENTIAL to the ADR-2268 detector bank.
DETECTOR_ONLY = "Please call the customer back on 0151 2345 6789 today."
DETECTOR_VISIBLE = ["Reply to jane.doe@example.com, IBAN DE89 3704 0044 0532 0130 00", DETECTOR_ONLY]


@pytest.mark.parametrize("text", DETECTOR_VISIBLE)
def test_unset_role_refuses_text_the_detector_bank_grades_above_public(federation_pair, text):
    ctx = federation_pair
    res = ctx["sender"].send("peer-B", text)
    assert (res.ok, res.status) == (False, "rejected"), (res.status, res.error_detail)
    assert res.data == {"reason": "endpoint_role_unset"}
    assert _peer_received(ctx) == [] and _RecordingEngine.spawns == []
    d = _a_events(ctx, "A2A.outbound_refused")[0]["details"]
    assert d["data_class"] == "CONFIDENTIAL"
    assert text not in json.dumps(ctx["sender"]._inst_forge_se.events)


def test_a_stated_role_lets_detector_visible_text_go(federation_pair):
    ctx = federation_pair
    _set_role(ctx, "processor")
    assert ctx["sender"].send("peer-B", DETECTOR_ONLY).ok
