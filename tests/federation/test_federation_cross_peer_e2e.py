"""Cross-peer federation E2E (ADR-2231/2232, CONCEPT-0097 Phases 2-4).

Two CorvinOS-like instances in one process, each with its own instance_id,
origin and endpoint registries. Instance B runs a REAL ``a2a_http_server`` on
an ephemeral 127.0.0.1 port; instance A talks to it through the REAL
``RemoteTriggerSender`` (HMAC-signed TaskEnvelope, instance pin, signed
ResponseEnvelope). Only the engine at the very end of B's worker path is a
fake — it records the ``model`` it was spawned with.

Covered: catalog discovery (only federable agents, untrusted-input
sanitising, A2A addressing), delegation by capability and by explicit agent,
the agent's model reaching the engine, receiver-side refusals (unknown agent,
hop limit, busy, per-origin opt-out, malformed field), idempotent replay of
the same task, audit-first ordering on the origin, both chain anchors in the
trace, and every federation audit event staying inside its allowlist.
"""
from __future__ import annotations

import json
import secrets
import sys
import unittest.mock as mock
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
for p in (str(SHARED), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

import a2a_federation  # noqa: E402
import a2a_http_server  # noqa: E402
import remote_trigger_receiver as rtr  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402
import spawn_gates  # noqa: E402

TENANT = "_default"


# ── harness ──────────────────────────────────────────────────────────────

@dataclass
class _Ev:
    type: str
    text: str | None = None
    usage: dict | None = None
    error: str | None = None


class _RecordingEngine:
    name = "claude_code"  # stands in for ClaudeCodeEngine at the spawn boundary
    capabilities: dict = {}
    spawns: list[dict] = []
    gate = None  # threading.Event: when set, spawn() blocks until it fires

    def spawn(self, prompt, **kwargs):
        _RecordingEngine.spawns.append(dict(kwargs))
        if _RecordingEngine.gate is not None:
            _RecordingEngine.gate.wait(10)
        out = f"done by {kwargs.get('model') or 'default-model'}"
        return iter([_Ev(type="text_delta", text=out), _Ev(type="turn_completed", text=out)])

    def cancel(self):
        pass


class _CapturingSE:
    def __init__(self):
        self.events: list[dict] = []

    def write_event(self, path, event_type, **kw):
        self.events.append({"event_type": event_type, **kw})
        return {"hash": secrets.token_hex(32)}

    def get_audit_chain_tail(self, path):
        return "a" * 64

    def __getattr__(self, name):  # anything else the receiver probes
        return mock.MagicMock()


@pytest.fixture
def federation_pair(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    (home / "tenants" / TENANT / "global" / "federation").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    monkeypatch.setenv("CORVIN_A2A_FEED_DIR", str(tmp_path / "feed"))
    monkeypatch.setattr(spawn_gates, "check_l44", lambda *a, **kw: None)
    for name in ("license.compute_quota", "license.limits"):
        monkeypatch.setitem(sys.modules, name, None)
    _RecordingEngine.spawns = []
    _RecordingEngine.gate = None
    a2a_federation._SEEN._items.clear()
    a2a_federation._SLOTS._in_use.clear()

    se_b = _CapturingSE()
    se_a = _CapturingSE()
    b_origins = tmp_path / "B" / "origins"
    a_endpoints = tmp_path / "A" / "endpoints"
    b_origins.mkdir(parents=True)
    a_endpoints.mkdir(parents=True)
    iid_a = "iid-A-" + secrets.token_hex(4)
    iid_b = "iid-B-" + secrets.token_hex(4)

    server = a2a_http_server.build_server(
        host="127.0.0.1", port=0, origins_dir=b_origins,
        engine_factory=lambda: _RecordingEngine(), instance_id=iid_b,
        nonce_store=rtr.NonceStore(), forge_se=se_b,
    )
    a2a_http_server.serve_in_thread(server)
    host, port = server.server_address[:2]

    hmac_key, recv_key = secrets.token_hex(32), secrets.token_hex(32)
    origin = {
        "origin_id": "peer-A", "hmac_key": hmac_key, "recv_key": recv_key,
        "enabled": True, "max_ttl_s": 300, "allowed_personas": ["assistant"],
        "spawn_worker": True,
    }
    (b_origins / "peer-A.json").write_text(json.dumps(origin))
    (b_origins / "peer-A.json").chmod(0o600)
    endpoint = {
        "endpoint_id": "peer-B", "url": f"http://{host}:{port}/v1/a2a/receive",
        "hmac_key": hmac_key, "recv_key": recv_key, "instance_id": iid_b,
        "enabled": True, "default_ttl_s": 60, "our_origin_id": "peer-A",
    }
    (a_endpoints / "peer-B.json").write_text(json.dumps(endpoint))
    (a_endpoints / "peer-B.json").chmod(0o600)
    sender = rts.RemoteTriggerSender(endpoints_dir=a_endpoints, instance_id=iid_a, forge_se=se_a)

    from core.federation.local_agent import LocalAgentRegistry
    reg = LocalAgentRegistry(TENANT)
    reg.register(agent_id="opus-code", engine_type="claude_code",
                 capabilities=["code_execution", "analysis"], model="claude-opus-5",
                 max_concurrent=1, cost_per_task_usd=0.50, federable=True)
    reg.register(agent_id="haiku-cheap", engine_type="claude_code",
                 capabilities=["analysis"], model="claude-haiku-4-5", cost_per_task_usd=0.01,
                 max_concurrent=2, federable=True)
    reg.register(agent_id="codex-local", engine_type="codex_cli",
                 capabilities=["code_execution"], model="gpt-5", federable=True)
    reg.register(agent_id="opus-private", engine_type="claude_code",
                 capabilities=["analysis"], model="claude-opus-5", cost_per_task_usd=0.0)

    ctx = {"home": home, "sender": sender, "iid_b": iid_b, "se_b": se_b,
           "origin_path": b_origins / "peer-A.json", "origin": origin}
    try:
        yield ctx
    finally:
        server.shutdown()
        server.server_close()


def _chain_events(home: Path) -> list[dict]:
    from core.paths import tenant_audit_chain
    chain = tenant_audit_chain(TENANT)
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


def _fed_events(se) -> list[dict]:
    return [e for e in se.events if e["event_type"].startswith("federation.")]


# ── Phase 2: discovery ───────────────────────────────────────────────────

def test_catalog_lists_only_federable_agents_with_peer_address(federation_pair):
    from core.federation.peer_catalog import PeerCatalog

    agents = PeerCatalog(TENANT).refresh("peer-B", sender=federation_pair["sender"])
    ids = sorted(a.agent_id for a in agents)
    assert ids == ["haiku-cheap", "opus-code"], \
        "only opted-in claude_code agents are advertised (not codex, not opus-private)"
    iid_b = federation_pair["iid_b"]
    assert all(a.peer_instance_id == iid_b for a in agents)
    assert {a.address for a in agents} == {f"agent://{iid_b}/haiku-cheap", f"agent://{iid_b}/opus-code"}
    assert _RecordingEngine.spawns == [], "a catalog request must never spawn a worker"
    served = [e for e in _fed_events(federation_pair["se_b"]) if e["event_type"] == "federation.catalog_served"]
    assert served and served[0]["details"]["agent_count"] == 2
    fetched = [e for e in _chain_events(federation_pair["home"]) if e.get("event_type") == "federation.catalog_fetched"]
    assert fetched and fetched[-1]["details"]["agent_count"] == 2
    assert PeerCatalog(TENANT).peers()[0]["fresh"] is True


def test_catalog_payload_from_peer_is_sanitised():
    from core.federation.peer_catalog import sanitize_catalog

    raw = [
        {"agent_id": "ok-1", "capabilities": ["analysis"], "model": "m", "max_concurrent": 1, "cost_per_task_usd": 0},
        {"agent_id": "../etc", "capabilities": ["analysis"], "model": "m"},
        {"agent_id": "bad-cap", "capabilities": ["telepathy"], "model": "m"},
        {"agent_id": "nan-cost", "capabilities": ["analysis"], "model": "m", "cost_per_task_usd": float("nan")},
        {"agent_id": "ok-1", "capabilities": ["analysis"], "model": "dup"},
        "not-a-dict",
    ]
    assert [a["agent_id"] for a in sanitize_catalog(raw)] == ["ok-1"]
    assert sanitize_catalog({"agents": []}) == []


def test_origin_opt_out_refuses_catalog(federation_pair):
    from core.federation.peer_catalog import PeerCatalog, PeerCatalogError

    cfg = dict(federation_pair["origin"], allow_federation=False)
    federation_pair["origin_path"].write_text(json.dumps(cfg))
    with pytest.raises(PeerCatalogError):
        PeerCatalog(TENANT).refresh("peer-B", sender=federation_pair["sender"])
    refused = [e for e in _fed_events(federation_pair["se_b"]) if e["event_type"] == "federation.catalog_refused"]
    assert refused and refused[0]["details"]["reason"] == "federation_disabled"


# ── Phase 3: selection + delegation ──────────────────────────────────────

def test_selection_ranks_local_and_peer_agents(federation_pair):
    from core.federation.delegation import rank_candidates
    from core.federation.peer_catalog import PeerCatalog

    PeerCatalog(TENANT).refresh("peer-B", sender=federation_pair["sender"])
    ranked = rank_candidates(TENANT, "analysis")
    assert [c.scope for c in ranked][:2] == ["local", "local"]
    peer_only = rank_candidates(TENANT, "analysis", include_local=False)
    assert [c.agent_id for c in peer_only] == ["haiku-cheap", "opus-code"], "cheapest peer first"
    by_model = rank_candidates(TENANT, "analysis", include_local=False, model="claude-opus-5")
    assert by_model[0].agent_id == "opus-code"


def test_delegate_by_capability_runs_cheapest_peer_agent_on_its_model(federation_pair):
    from core.federation.delegation import delegate
    from core.federation.peer_catalog import PeerCatalog

    PeerCatalog(TENANT).refresh("peer-B", sender=federation_pair["sender"])
    res = delegate(TENANT, instruction="Summarise the attached notes.",
                   capability="analysis", sender=federation_pair["sender"])
    assert res.ok, res
    assert res.agent_id == "haiku-cheap"
    assert res.address == f"agent://{federation_pair['iid_b']}/haiku-cheap"
    assert len(_RecordingEngine.spawns) == 1
    assert _RecordingEngine.spawns[0].get("model") == "claude-haiku-4-5-20251001" or \
        _RecordingEngine.spawns[0].get("model", "").startswith("claude-haiku-4-5")
    assert "done by claude-haiku-4-5" in json.dumps(res.data)

    recv = [e["event_type"] for e in _fed_events(federation_pair["se_b"])]
    assert recv.index("federation.task_received") < recv.index("federation.task_completed")
    origin_chain = [e.get("event_type") for e in _chain_events(federation_pair["home"])]
    assert origin_chain.index("federation.task_delegated") < origin_chain.index("federation.task_result_received")


def test_delegate_to_explicit_agent_and_trace_carries_both_anchors(federation_pair):
    from core.federation.delegation import delegate, trace

    root = delegate(TENANT, instruction="Write a unit test.", endpoint_id="peer-B",
                    agent_id="opus-code", capability="code_execution",
                    sender=federation_pair["sender"])
    assert root.ok, root
    assert _RecordingEngine.spawns[-1].get("model", "").startswith("claude-opus-5")
    child = delegate(TENANT, instruction="Review it.", endpoint_id="peer-B",
                     agent_id="haiku-cheap", parent_task_id=root.task_id, hop=1,
                     sender=federation_pair["sender"])
    assert child.ok, child

    t = trace(TENANT, child.task_id)
    assert t["root_task_id"] == root.task_id
    node = t["tree"]
    assert node["agent_id"] == "opus-code" and node["status"] == "ok"
    assert node["anchors"]["our_chain_tail"] and node["anchors"]["peer_chain_tail"]
    assert [c["task_id"] for c in node["children"]] == [child.task_id]


def test_receiver_refuses_unknown_agent_without_spawning(federation_pair):
    from core.federation.delegation import delegate

    res = delegate(TENANT, instruction="hi", endpoint_id="peer-B", agent_id="ghost",
                   sender=federation_pair["sender"])
    assert not res.ok and res.status == "rejected"
    assert res.error == "federation_unknown_agent"
    assert _RecordingEngine.spawns == []


@pytest.mark.parametrize("agent_id", ["codex-local", "opus-private"])
def test_non_federable_agents_cannot_be_run(federation_pair, agent_id):
    """codex: wrong engine. opus-private: never opted in — and it is the
    cheapest analysis agent, so capability routing must skip it too."""
    from core.federation.delegation import delegate

    res = delegate(TENANT, instruction="hi", endpoint_id="peer-B", agent_id=agent_id,
                   sender=federation_pair["sender"])
    assert res.error == "federation_agent_not_federable"
    assert _RecordingEngine.spawns == []
    res = federation_pair["sender"].send(
        "peer-B", "x", federation={"v": 1, "op": "task", "capability": "analysis", "hop": 0})
    assert res.ok and _RecordingEngine.spawns[-1]["model"].startswith("claude-haiku-4-5")


def test_hop_limit_enforced_on_both_sides(federation_pair):
    from core.federation.delegation import DelegationError, delegate

    with pytest.raises(DelegationError):
        delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="opus-code",
                 hop=a2a_federation.MAX_HOPS + 1, sender=federation_pair["sender"])
    res = federation_pair["sender"].send(
        "peer-B", "x", federation={"v": 1, "op": "task", "target_agent_id": "opus-code",
                                   "hop": a2a_federation.MAX_HOPS + 1})
    assert res.status == "rejected" and res.data.get("reason") == "federation_hop_limit"
    assert _RecordingEngine.spawns == []


def test_busy_agent_is_refused_and_the_same_task_may_retry_later(federation_pair):
    from core.federation.delegation import delegate

    assert a2a_federation._SLOTS.acquire(TENANT, "opus-code", 1)
    try:
        res = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="opus-code",
                       task_id="task-busy-1", sender=federation_pair["sender"])
    finally:
        a2a_federation._SLOTS.release(TENANT, "opus-code")
    assert res.error == "federation_agent_busy"
    assert _RecordingEngine.spawns == []
    again = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="opus-code",
                     task_id="task-busy-1", sender=federation_pair["sender"])
    assert again.ok, "a task that never started must be retryable"


def test_slot_is_released_after_a_task(federation_pair):
    from core.federation.delegation import delegate

    for _ in range(2):  # max_concurrent=1: the second run only succeeds if the first released
        res = delegate(TENANT, instruction=f"run {secrets.token_hex(2)}", endpoint_id="peer-B",
                       agent_id="opus-code", sender=federation_pair["sender"])
        assert res.ok, res
    assert a2a_federation._SLOTS._in_use == {}


def test_finished_task_id_is_never_run_twice(federation_pair):
    """At-most-once (review finding 2): a repeat — same or DIFFERENT agent,
    instruction or schema — is refused, never served a stale answer."""
    from core.federation.delegation import delegate

    first = delegate(TENANT, instruction="once please", endpoint_id="peer-B",
                     agent_id="haiku-cheap", task_id="task-fixed-1", sender=federation_pair["sender"])
    second = delegate(TENANT, instruction="something else", endpoint_id="peer-B",
                      agent_id="opus-code", task_id="task-fixed-1", sender=federation_pair["sender"])
    assert first.ok
    assert not second.ok and second.error == "federation_duplicate_task" and second.data == {}
    assert len(_RecordingEngine.spawns) == 1


def test_retry_while_first_run_is_in_flight_does_not_run_twice(federation_pair):
    """Review finding 1: the retry a timed-out sender makes while the peer is
    still working. haiku-cheap has max_concurrent=2, so the slot would allow it."""
    import threading
    from core.federation.delegation import delegate

    _RecordingEngine.gate = threading.Event()
    results = {}
    t = threading.Thread(target=lambda: results.setdefault("first", delegate(
        TENANT, instruction="slow", endpoint_id="peer-B", agent_id="haiku-cheap",
        task_id="task-inflight-1", sender=federation_pair["sender"])))
    t.start()
    for _ in range(200):
        if _RecordingEngine.spawns:
            break
        threading.Event().wait(0.02)
    assert len(_RecordingEngine.spawns) == 1, "first run never started"
    retry = delegate(TENANT, instruction="slow", endpoint_id="peer-B", agent_id="haiku-cheap",
                     task_id="task-inflight-1", sender=federation_pair["sender"])
    _RecordingEngine.gate.set()
    t.join(15)
    assert retry.error == "federation_duplicate_task"
    assert results["first"].ok
    assert len(_RecordingEngine.spawns) == 1


def test_origin_without_worker_rights_gets_no_federation(federation_pair):
    """Review finding 4: the no-worker path used to answer a signed "ok"."""
    from core.federation.delegation import delegate
    from core.federation.peer_catalog import PeerCatalog, PeerCatalogError

    cfg = dict(federation_pair["origin"], spawn_worker=False)
    federation_pair["origin_path"].write_text(json.dumps(cfg))
    res = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="opus-code",
                   sender=federation_pair["sender"])
    assert not res.ok and res.error == "federation_no_worker"
    with pytest.raises(PeerCatalogError):
        PeerCatalog(TENANT).refresh("peer-B", sender=federation_pair["sender"])
    assert _RecordingEngine.spawns == []


def test_federation_with_group_id_is_refused(federation_pair):
    res = federation_pair["sender"].send(
        "peer-B", "x", group_id="g-1",
        federation={"v": 1, "op": "task", "target_agent_id": "opus-code", "hop": 0})
    assert res.status == "rejected" and res.data.get("reason") == "federation_bad_request"
    assert _RecordingEngine.spawns == []


def test_peer_supplied_status_and_reason_never_pass_verbatim(federation_pair, monkeypatch):
    """Review finding 3: closed vocabulary on the origin side."""
    from types import SimpleNamespace
    from core.federation.delegation import delegate, trace
    from core.federation.peer_catalog import PeerCatalog, PeerCatalogError

    evil = SimpleNamespace(ok=False, status="X" * 5000, instance_id="", data={"reason": "<script>x</script>"},
                           error_category="rejected", duration_ms=1, instance_id_match=False,
                           our_chain_tail="", peer_chain_tail="")
    stub = SimpleNamespace(send=lambda *a, **kw: evil)
    res = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="opus-code", sender=stub)
    assert res.status == "error" and res.error == "peer_refused"
    assert "<script>" not in json.dumps(trace(TENANT, res.task_id))
    chain = json.dumps(_chain_events(federation_pair["home"]))
    assert "<script>" not in chain and "XXXXXXXX" not in chain
    with pytest.raises(PeerCatalogError) as exc:
        PeerCatalog(TENANT).refresh("peer-B", sender=stub)
    assert exc.value.reason == "peer_refused"


def test_local_registry_rejects_unaddressable_ids_and_non_finite_cost(federation_pair):
    from core.federation.local_agent import LocalAgentError, LocalAgentRegistry

    reg = LocalAgentRegistry(TENANT)
    with pytest.raises(LocalAgentError):
        reg.register(agent_id="agént", engine_type="claude_code", capabilities=["analysis"], model="m")
    with pytest.raises(LocalAgentError):
        reg.register(agent_id="nan-cost", engine_type="claude_code", capabilities=["analysis"],
                     model="m", cost_per_task_usd=float("nan"))


def test_malformed_federation_field_is_rejected(federation_pair):
    res = federation_pair["sender"].send("peer-B", "x", federation={"v": 1, "op": "rm -rf"})
    assert res.status == "rejected" and res.data.get("reason") == "federation_bad_request"
    res = federation_pair["sender"].send("peer-B", "x", federation={"v": 99, "op": "catalog"})
    assert res.data.get("reason") == "federation_unsupported_version"
    assert _RecordingEngine.spawns == []


def test_plain_a2a_task_is_unaffected(federation_pair):
    res = federation_pair["sender"].send("peer-B", "plain chat message")
    assert res.ok
    assert "model" not in _RecordingEngine.spawns[-1], "a non-federated task keeps the engine default"
    assert not _fed_events(federation_pair["se_b"])


# ── audit hygiene ────────────────────────────────────────────────────────

def test_every_federation_event_fits_the_central_allowlist(federation_pair):
    from core.federation import audit as fa
    from core.federation.delegation import delegate
    from core.federation.peer_catalog import PeerCatalog
    from forge.security_events import EVENT_SEVERITY, _EVENT_ALLOWLIST

    PeerCatalog(TENANT).refresh("peer-B", sender=federation_pair["sender"])
    delegate(TENANT, instruction="x", capability="analysis", sender=federation_pair["sender"])
    delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="ghost",
             sender=federation_pair["sender"])
    for ev in _fed_events(federation_pair["se_b"]):
        name = ev["event_type"]
        assert name in EVENT_SEVERITY and name in _EVENT_ALLOWLIST, name
        assert set(ev["details"]) <= _EVENT_ALLOWLIST[name], (name, set(ev["details"]) - _EVENT_ALLOWLIST[name])
    for name, fields in fa.ALLOWED_FIELDS.items():
        assert _EVENT_ALLOWLIST[name] == fields, name
        assert EVENT_SEVERITY[name] == fa.SEVERITY[name], name


# ── review round 2 ───────────────────────────────────────────────────────

def test_task_refused_before_its_engine_started_can_be_retried(federation_pair, monkeypatch):
    """Round 2, finding 1: the origin's worker slot was full → the receiver
    refused "busy" inside the task path. That task never ran, so the SAME
    task_id must be accepted on retry — not stuck as a duplicate for an hour."""
    from core.federation.delegation import delegate

    real_acquire = rtr._worker_slot_acquire
    monkeypatch.setattr(rtr, "_worker_slot_acquire", lambda origin_id: False)
    first = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="opus-code",
                     task_id="task-slot-1", sender=federation_pair["sender"])
    assert first.status == "rejected" and first.error == "busy"
    assert _RecordingEngine.spawns == []
    monkeypatch.setattr(rtr, "_worker_slot_acquire", real_acquire)
    retry = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="opus-code",
                     task_id="task-slot-1", sender=federation_pair["sender"])
    assert retry.ok, retry
    assert len(_RecordingEngine.spawns) == 1


def test_failed_strict_audit_in_task_path_releases_the_claim(federation_pair):
    """The envelope_received strict write fails → nonce rolled back AND the
    task_id released, so the sender's retry is not refused as a duplicate."""
    from core.federation.delegation import delegate

    se_b = federation_pair["se_b"]
    real_write = se_b.write_event
    calls = {"n": 0}

    def flaky(path, event_type, **kw):
        if event_type == "A2A.envelope_received" and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("disk full")
        return real_write(path, event_type, **kw)

    se_b.write_event = flaky
    first = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="haiku-cheap",
                     task_id="task-audit-1", sender=federation_pair["sender"])
    assert not first.ok and _RecordingEngine.spawns == []
    retry = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="haiku-cheap",
                     task_id="task-audit-1", sender=federation_pair["sender"])
    assert retry.ok, retry
    assert len(_RecordingEngine.spawns) == 1


def test_task_that_ran_keeps_its_claim(federation_pair):
    from core.federation.delegation import delegate

    a = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="haiku-cheap",
                 task_id="task-ran-1", sender=federation_pair["sender"])
    b = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="haiku-cheap",
                 task_id="task-ran-1", sender=federation_pair["sender"])
    assert a.ok and b.error == "federation_duplicate_task"
    assert len(_RecordingEngine.spawns) == 1


def test_claims_of_one_origin_never_evict_another_origins(monkeypatch):
    monkeypatch.setattr(a2a_federation, "_SEEN_MAX_PER_ORIGIN", 3)
    seen = a2a_federation._SeenTasks()
    assert seen.claim(("t", "victim", "v-1"))
    for i in range(50):
        seen.claim(("t", "flooder", f"f-{i}"))
    assert not seen.claim(("t", "victim", "v-1")), "the victim's claim must survive the flood"


@pytest.mark.parametrize("bad_iid", ["short", "x" * 500, "<script>alert(1)</script>"])
def test_malformed_peer_instance_id_never_recorded(federation_pair, bad_iid):
    """Round 2, finding 2: an unpinned endpoint does not check the peer's
    instance_id; the origin must not record or address with a malformed one."""
    from types import SimpleNamespace
    from core.federation.delegation import delegate, trace
    from core.federation.peer_catalog import PeerCatalog, PeerCatalogError

    reply = SimpleNamespace(ok=True, status="ok", instance_id=bad_iid, data={"agents": []},
                            error_category=None, duration_ms=1, instance_id_match=True,
                            our_chain_tail="", peer_chain_tail="")
    stub = SimpleNamespace(send=lambda *a, **kw: reply)
    res = delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="opus-code", sender=stub)
    assert not res.ok and res.error == "instance_id_invalid"
    assert res.address is None and bad_iid not in json.dumps(trace(TENANT, res.task_id))
    with pytest.raises(PeerCatalogError):
        PeerCatalog(TENANT).refresh("peer-B", sender=stub)
    assert bad_iid not in json.dumps(_chain_events(federation_pair["home"]))
