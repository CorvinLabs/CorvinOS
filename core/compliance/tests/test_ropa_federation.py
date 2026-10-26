"""The Art. 30 record lists cross-organisation delegation (PLAN-0965 F-11).

Real audit writer, real hash chain, real generator: events are written with ``seed_chain`` (the production
``write_event``) and read back by ``gdpr_ropa.generate``. Nothing is mocked.
"""
from __future__ import annotations

import time

from corvin_compliance_reports import audit_query, gdpr_ropa

# Registered federation.* / workflow.peer_* events that are NOT a cross-system flow, with the reason.
# A new event must either be listed in gdpr_ropa._FEDERATION_EVENTS or appear here with a reason.
_NOT_A_FLOW = {
    "federation.local_agent_registered": "local agent registry, no peer involved",
    "federation.local_agent_deregistered": "local agent registry, no peer involved",
    "federation.local_agent_registration_rejected": "local agent registry, no peer involved",
    "federation.local_ask": "one-shot LOCAL agent turn inside a peer thread, nothing is sent",
    "federation.conversation_operator_message": "operator interjection, stored in the local transcript",
    "federation.conversation_paused": "local operator control of a running conversation",
    "federation.conversation_resumed": "local operator control of a running conversation",
    "federation.conversation_settings_changed": "local operator control of a running conversation",
    "federation.endpoint_role_set": "operator states a paired endpoint's GDPR role (ADR-2294), no data flow; "
                                    "the stated role is shown per peer from the endpoint record",
}


def test_every_registered_federation_event_is_in_the_record_or_exempted_with_a_reason():
    from forge import security_events as se

    registered = {n for n in se.EVENT_SEVERITY if n.startswith(("federation.", "workflow.peer_"))}
    assert registered, "positive control: the registry must contain federation events"
    listed = set(gdpr_ropa._FEDERATION_EVENTS)
    missing = sorted(registered - listed - set(_NOT_A_FLOW))
    assert not missing, f"registered but neither in the RoPA nor exempted: {missing}"
    stale = sorted(listed - registered)
    assert not stale, f"listed in the RoPA but not registered (typo or removed event): {stale}"
    assert not (set(_NOT_A_FLOW) - registered), "exemption names an event that is not registered"


def test_ropa_counts_cross_organisation_delegation_per_peer(sandbox_home, seed_chain, tmp_path):
    base = dict(task_id="task_a", endpoint_id="peer-a", peer_instance_id="iid-a", agent_id="analyst", tenant_id="_default")
    seed_chain("federation.task_delegated", capability="analysis", hop=0, parent_task_id="", **base)
    seed_chain("federation.task_result_received", status="ok", duration_ms=120,
               our_chain_tail="a" * 16, peer_chain_tail="b" * 16, **base)
    seed_chain("workflow.peer_delegated", wid="wf1", rid="run1", node_id="n1", endpoint_id="peer-a",
               peer_instance_id="iid-a", agent_id="analyst")
    seed_chain("workflow.peer_refused", wid="wf1", rid="run1", node_id="n2", endpoint_id="peer-b",
               agent_id="analyst", data_class="CONFIDENTIAL", reason_code="classified_above_public")
    seed_chain("federation.task_received", task_id="task_in", origin_id="origin-c", agent_id="analyst",
               capability="analysis", hop=1, parent_task_id="", tenant_id="_default")
    # a local-only event must not be counted as a flow
    seed_chain("federation.local_agent_registered", agent_id="analyst", tenant_id="_default")

    out = tmp_path / "ropa.pdf"
    md = gdpr_ropa.generate(tenant_id="_default", start_ts=0, end_ts=int(time.time()) + 1000, output_path=out)
    assert md["federation_events"] == 5, md
    assert out.read_bytes()[:4] == b"%PDF"

    from forge import paths as _forge_paths

    chain = _forge_paths.tenant_global_dir("_default") / "forge" / "audit.jsonl"
    events = audit_query.collect_events(tenant_id="_default", start_ts=0, end_ts=int(time.time()) + 1000,
                                        event_types=gdpr_ropa._FEDERATION_EVENTS, chain_path=chain)
    rows = {r[0]: r[3:] for r in gdpr_ropa._aggregate_federation(events)[1:]}
    # columns after peer, organisation, role: out, back, workflow steps, served in, refused, outcome unknown
    assert rows["peer-a"] == ["1", "1", "1", "0", "0", "0"]
    assert rows["peer-b"] == ["0", "0", "0", "0", "1", "0"]
    assert rows["origin-c"] == ["0", "0", "0", "1", "0", "0"]


def test_empty_period_says_so_instead_of_an_empty_table():
    rows = gdpr_ropa._aggregate_federation([])
    assert rows[1] == ["—", "", "", "0", "0", "0", "0", "0", "0"]


def _endpoint(d, eid, **extra):
    import json

    cfg = {"endpoint_id": eid, "url": "https://x.example/v1/a2a/receive", "hmac_key": "a" * 64,
           "recv_key": "b" * 64, "enabled": True, **extra}
    (d / f"{eid}.json").write_text(json.dumps(cfg))
    (d / f"{eid}.json").chmod(0o600)


def test_ropa_lists_every_paired_endpoint_with_organisation_and_stated_role(sandbox_home, seed_chain, tmp_path):
    """ADR-2294: organisation and role per peer next to the flow counts; ``unset`` is never guessed."""
    eps = tmp_path / "endpoints"
    eps.mkdir()
    _endpoint(eps, "peer-a", organisation="Acme Analytics GmbH", gdpr_role="processor")
    # a label and URL that SAY "processor" are not a stated role
    _endpoint(eps, "peer-b", label="Processor Inc", url="https://processor.example/v1/a2a/receive")
    _endpoint(eps, "peer-c", organisation="Quiet Partner AG", gdpr_role="joint")  # paired, no flow this period
    seed_chain("federation.task_delegated", task_id="t1", endpoint_id="peer-a", peer_instance_id="iid-a",
               agent_id="analyst", capability="analysis", hop=0, parent_task_id="", tenant_id="_default")
    seed_chain("workflow.peer_refused", wid="wf1", rid="run1", node_id="n2", endpoint_id="peer-b",
               agent_id="analyst", data_class="CONFIDENTIAL", reason_code="endpoint_role_unset")

    from forge import paths as _forge_paths

    chain = _forge_paths.tenant_global_dir("_default") / "forge" / "audit.jsonl"
    events = audit_query.collect_events(tenant_id="_default", start_ts=0, end_ts=int(time.time()) + 1000,
                                        event_types=gdpr_ropa._FEDERATION_EVENTS, chain_path=chain)
    table = gdpr_ropa._aggregate_federation(events, roles=gdpr_ropa._endpoint_roles(eps))
    assert table[0][:3] == ["Peer (endpoint/origin)", "Organisation", "GDPR role"]
    rows = {r[0]: r for r in table[1:]}
    assert rows["peer-a"][1:4] == ["Acme Analytics GmbH", "processor", "1"]
    assert rows["peer-b"][1:3] == ["—", "unset - operator decision pending"]
    assert rows["peer-b"][7] == "1"  # refused
    assert rows["peer-c"][1:3] == ["Quiet Partner AG", "joint"]
    assert rows["peer-c"][3:] == ["0", "0", "0", "0", "0", "0"]

    out = tmp_path / "ropa.pdf"
    md = gdpr_ropa.generate(tenant_id="_default", start_ts=0, end_ts=int(time.time()) + 1000, output_path=out,
                            endpoints_dir=eps)
    assert md["federation_peers"] == 3 and out.read_bytes()[:4] == b"%PDF"


def test_unset_text_is_the_shared_constant():
    gdpr_ropa._endpoint_roles(None)  # puts the shared dir on sys.path
    import endpoint_role

    assert gdpr_ropa._UNSET_ROLE_TEXT == endpoint_role.UNSET_RECORD_TEXT == "unset - operator decision pending"


def test_an_origin_that_is_not_one_of_our_endpoints_has_no_stated_role():
    events = [{"event_type": "federation.task_received", "details": {"origin_id": "origin-x"}}]
    rows = gdpr_ropa._aggregate_federation(events, roles={})
    assert rows[1][:3] == ["origin-x", "—", "unset - operator decision pending"]
