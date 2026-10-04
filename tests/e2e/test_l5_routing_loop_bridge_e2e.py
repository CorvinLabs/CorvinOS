"""ADR-2092 E2E — the L5 routing loop through the real bridge entry point.

Every scenario pushes a real inbox envelope through ``adapter.process_one`` with the
Skill registry booted by the bridge's own ``_boot_acp_skills`` (the call ``main()``
makes), writes to a sandboxed hash-chained audit file, and verifies that chain.

Boundaries replaced, and why:
* ``ADAPTER_FAKE_CLAUDE=1`` — the OS engine subprocess (no API spend); the routing
  code under test runs before it. ``ADAPTER_DISABLE_VOICE=1`` — the voice summary,
  which would otherwise spawn a real ``claude -p`` per turn.
* ``adapter._run_acs_delegation`` — the ACS worker fan-out, a separate subsystem
  with its own suites; replaced by a recorder so the test can prove whether the
  route reached it.
* feature flags ``bridge_worker_engine_parity`` / ``bridge_tde_execution`` — set ON,
  as on the live install, so the full shared route runs.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
for p in (str(SHARED), str(REPO / "corvin_operator" / "forge"), str(REPO / "core" / "console")):
    if p not in sys.path:
        sys.path.insert(0, p)

TENANT = "_default"
SENDER = "u-e2e-router"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    for name in ("inbox", "outbox", "processed", "bridges"):
        (tmp_path / name).mkdir()
    env = {
        "CORVIN_HOME": str(tmp_path / "home"),
        "ADAPTER_INBOX": str(tmp_path / "inbox"),
        "ADAPTER_OUTBOX": str(tmp_path / "outbox"),
        "ADAPTER_PROCESSED": str(tmp_path / "processed"),
        "ADAPTER_BRIDGES_DIR": str(tmp_path / "bridges"),
        "ADAPTER_FAKE_CLAUDE": "1",
        "ADAPTER_FAKE_DELAY": "0",
        "ADAPTER_ROUTING_MODE": "off",
        "ADAPTER_DISABLE_VOICE": "1",
        "VOICE_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CORVIN_ACP_PHASE", raising=False)
    sys.modules.pop("adapter", None)
    adapter = importlib.import_module("adapter")
    booted = adapter._boot_acp_skills()
    assert "os.delegation_router" in booted, booted

    import core.skills.os_skills.model_selector  # noqa: F401 — warm, as the bridge does
    from corvin_core import feature_flags

    real = feature_flags.is_enabled
    monkeypatch.setattr(
        feature_flags, "is_enabled",
        lambda flag, tid=TENANT, *a, **k: True
        if flag in ("bridge_worker_engine_parity", "bridge_tde_execution") else real(flag, tid, *a, **k),
    )
    monkeypatch.setattr(feature_flags, "worker_engine_mode", lambda tid=TENANT: "native")
    acs_calls: list[str] = []

    def _fake_acs(prompt, **_kw):
        acs_calls.append(prompt)
        return "ACS-ANSWER"

    monkeypatch.setattr(adapter, "_run_acs_delegation", _fake_acs)
    from core.skills.os_skills.monitoring import dual_write

    dual_write.reset_for_tests()
    yield adapter, tmp_path, acs_calls
    dual_write.reset_for_tests()


def _send(adapter, base: Path, text: str) -> list[dict]:
    msg_id = f"e2e-{time.time_ns()}"
    envelope = {"id": msg_id, "channel": "discord", "from": SENDER,
                "chat_id": "chat-e2e", "text": text, "ts": time.time()}
    f = base / "inbox" / f"{msg_id}.json"
    f.write_text(json.dumps(envelope))
    adapter.process_one(f, settings={"whitelist": [SENDER], "voice_summary_mode": "never"})
    return [json.loads(p.read_text()) for p in sorted((base / "outbox").glob(f"{msg_id}*.json"))]


def _chain(base: Path) -> list[dict]:
    p = base / "audit.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _verify(base: Path) -> None:
    from forge.security_events import verify_chain

    ok, problems = verify_chain(base / "audit.jsonl")
    assert ok, problems[:3]


def _ledger():
    from core.skills.os_skills.monitoring import routing_ledger

    return routing_ledger.join(routing_ledger.read_records(TENANT))


def _seed_ready_history():
    from core.skills.os_skills.monitoring import routing_ledger

    def seed(n, **kw):
        for _ in range(n):
            tid = routing_ledger.new_turn_id()
            routing_ledger.record_decision(
                tenant_id=TENANT, turn_id=tid, surface="bridge", phase="shadow",
                bundled=kw.get("bundled", "native"), used=kw.get("bundled", "native"),
                source="bundled", skill="native", skill_conf=0.8,
                features={"complexity": "simple"})
            routing_ledger.record_outcome(tenant_id=TENANT, turn_id=tid, surface="bridge",
                                          used=kw.get("bundled", "native"), ok=True, latency_ms=5)
    seed(470)
    seed(40, bundled="acs")
    # ADR-2092 G4: the bridge enters Phase 2 only after a 7-day console soak.
    soak = {"v": 1, "kind": "decision", "ts": time.time() - 8 * 86400,
            "turn_id": routing_ledger.new_turn_id(), "surface": "console",
            "phase": "dual_write", "bundled": "native", "skill": "native",
            "skill_conf": 0.9, "used": "native", "source": "bundled",
            "clamped": False, "features": {}}
    with open(routing_ledger.ledger_path(TENANT), "a") as fh:
        fh.write(json.dumps(soak) + "\n")
    from core.skills.os_skills.monitoring import readiness

    assert readiness.cached_evaluate(TENANT, "bridge", background=False).ready


def _router_records(chain):
    return [e for e in chain if e["event_type"] == "skill.executed"
            and (e.get("details") or {}).get("skill_id") == "os.delegation_router"]


def test_plain_turn_is_recorded_once_with_outcome(bridge):
    adapter, base, acs = bridge
    out = _send(adapter, base, "Wie spät ist es?")
    assert out, "no reply written"
    rows = _ledger()
    assert len(rows) == 1
    row = rows[0]
    assert row["surface"] == "bridge" and row["used"] == "native" and row["phase"] == "shadow"
    assert row["outcome"] is not None and row["outcome"]["ok"] is True
    chain = _chain(base)
    rec = _router_records(chain)
    assert len(rec) == 1
    d = rec[0]["details"]
    assert d["lom"].endswith("delegation_policy.py:_consult_router") and d["lom_hash"]
    assert d["decision"]["engine"] == "native"
    assert "Wie spät" not in json.dumps(chain)
    assert acs == []
    _verify(base)


def test_shadow_big_data_turn_keeps_the_bundled_engine_and_records_the_advice(bridge):
    adapter, base, acs = bridge
    out = _send(adapter, base, "Big Data?")
    assert any("ACS-ANSWER" in (o.get("text") or "") for o in out)
    assert acs == ["Big Data?"]
    (row,) = _ledger()
    assert (row["bundled"], row["skill"], row["used"], row["source"]) == ("acs", "native", "acs", "bundled")
    assert row["features"]["complexity"] == "simple" and row["features"]["is_big_data"] is True
    assert row["outcome"]["used"] == "acs" and row["outcome"]["ok"] is True
    (rec,) = _router_records(_chain(base))
    assert rec["details"]["decision"] == {"engine": "native", "decision": "native",
                                          "bundled_engine": "acs",
                                          "shadow": True, "confidence": 0.8}
    _verify(base)


def test_phase2_without_evidence_is_refused_and_audited(bridge, monkeypatch):
    adapter, base, acs = bridge
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    from core.skills.os_skills.monitoring import readiness

    readiness.cached_evaluate(TENANT, "bridge", background=False)
    _send(adapter, base, "Big Data?")
    _send(adapter, base, "Big Data?")
    assert len(acs) == 2  # still delegated: nothing cleared Phase 2
    refused = [e for e in _chain(base) if e["event_type"] == "routing.phase2_refused"]
    assert len(refused) == 1  # once per process per reason, not per turn
    assert refused[0]["details"]["reason"] == "console_soak_incomplete"
    assert all(r["phase"] == "shadow" for r in _ledger())
    _verify(base)


def test_phase2_real_is_refused_outright(bridge, monkeypatch):
    adapter, base, acs = bridge
    _seed_ready_history()
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_real")
    _send(adapter, base, "Big Data?")
    assert acs == ["Big Data?"]
    (refused,) = [e for e in _chain(base) if e["event_type"] == "routing.phase2_refused"]
    assert refused["details"]["reason"] == "phase2_real_unsupported"
    _verify(base)


def test_cleared_phase2_de_escalates_audit_first_and_joins_the_outcome(bridge, monkeypatch):
    adapter, base, acs = bridge
    _seed_ready_history()
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    out = _send(adapter, base, "Big Data?")
    assert acs == []  # the Skill's de-escalation was served: no fan-out
    assert out and not any("ACS-ANSWER" in (o.get("text") or "") for o in out)
    live = [r for r in _ledger() if r["phase"] == "dual_write" and r["surface"] == "bridge"]
    assert len(live) == 1
    row = live[0]
    assert (row["bundled"], row["used"], row["source"]) == ("acs", "native", "skill")
    assert row["outcome"]["used"] == "native"
    chain = _chain(base)
    decisions = [e for e in chain if e["event_type"] == "routing.phase2_decision"]
    assert len(decisions) == 1
    d = decisions[0]["details"]
    assert (d["bundled_engine"], d["used_engine"], d["turn_id"]) == ("acs", "native", row["turn_id"])
    _verify(base)


def test_cleared_phase2_still_delegates_what_the_skill_confirms(bridge, monkeypatch):
    adapter, base, acs = bridge
    _seed_ready_history()
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    _send(adapter, base, "Analysiere 2 TB Logs")
    assert acs == ["Analysiere 2 TB Logs"]
    row = [r for r in _ledger() if r["phase"] == "dual_write" and r["surface"] == "bridge"][0]
    assert (row["used"], row["source"]) == ("acs", "bundled")
    _verify(base)


def test_explicit_delegate_is_never_de_escalated(bridge, monkeypatch):
    adapter, base, acs = bridge
    _seed_ready_history()
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    _send(adapter, base, "/delegate Big Data?")
    assert acs == ["Big Data?"]
    assert not [e for e in _chain(base) if e["event_type"] == "routing.phase2_decision"]
    _verify(base)


def test_flags_off_big_data_turn_never_writes_a_phase2_decision(bridge, monkeypatch):
    """Adversarial review 2026-09-28, HIGH: the flags-off carve-out runs ACS BEFORE
    the routing record, so a post-hoc record must never claim a Skill route change."""
    adapter, base, acs = bridge
    from corvin_core import feature_flags

    monkeypatch.setattr(
        feature_flags, "is_enabled",
        lambda flag, tid=TENANT, *a, **k: flag == "bridge_big_data_delegation",
    )
    _seed_ready_history()
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    _send(adapter, base, "Big Data?")
    assert acs == ["Big Data?"]
    assert not [e for e in _chain(base) if e["event_type"] == "routing.phase2_decision"]
    row = [r for r in _ledger() if r["outcome"] is not None and r["surface"] == "bridge"][-1]
    assert (row["phase"], row["source"], row["used"]) == ("shadow", "bundled", "acs")
    assert row["outcome"]["used"] == "acs"
    _verify(base)


# ── synthetic lifecycle simulation (ADR-2092 G4) ─────────────────────────────
# The gates are fed synthetic history in a sandboxed CORVIN_HOME; every routed turn
# still goes through ``adapter.process_one``. Synthetic rows can prove the mechanism;
# they must never be written into a live ledger to clear its gate.

def _seed_skill_served(n: int, *, ok: bool) -> None:
    from core.skills.os_skills.monitoring import routing_ledger

    for _ in range(n):
        tid = routing_ledger.new_turn_id()
        routing_ledger.record_decision(
            tenant_id=TENANT, turn_id=tid, surface="bridge", phase="dual_write",
            bundled="acs", used="native", source="skill", skill="native", skill_conf=0.8,
            features={"complexity": "simple", "is_big_data": True})
        routing_ledger.record_outcome(tenant_id=TENANT, turn_id=tid, surface="bridge",
                                      used="native", ok=ok, latency_ms=5)


def _wait_for(pred, timeout=5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_lifecycle_trailing_skill_trips_rollback_and_returns_to_shadow(bridge, monkeypatch):
    adapter, base, acs = bridge
    from core.skills.os_skills.monitoring import readiness, rollback_detector

    _seed_ready_history()
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")

    # 1. Gates clear: the Skill's de-escalation is served.
    _send(adapter, base, "Big Data?")
    assert acs == []
    assert not rollback_detector.rollback_active(TENANT)

    # 2. A degraded period: Skill-served turns fail, bundled turns keep succeeding.
    _seed_skill_served(120, ok=False)

    # 3. The next Skill-served outcome runs the judge, which trips audit-first.
    _send(adapter, base, "Big Data?")
    assert acs == []
    assert _wait_for(lambda: rollback_detector.rollback_active(TENANT))
    trips = [e for e in _chain(base) if e["event_type"] == "routing.rollback_triggered"]
    assert len(trips) == 1
    assert trips[0]["details"]["skill_success_rate"] < trips[0]["details"]["bundled_success_rate"]

    # 4. Tripped: the same request is delegated again, the refusal is audited.
    _send(adapter, base, "Big Data?")
    assert acs == ["Big Data?"]
    refused = [e for e in _chain(base) if e["event_type"] == "routing.phase2_refused"]
    assert [e["details"]["reason"] for e in refused] == ["rollback_active"]

    # 5. An operator reset does not reopen Phase 2 by itself: the evidence gate now
    #    sees native trailing delegated in the bucket and keeps the turn in shadow.
    import delegation_policy as dp

    rollback_detector.reset_rollback(
        TENANT, audit=lambda et, d: dp._audit(et, d, tenant_id=TENANT))
    assert not rollback_detector.rollback_active(TENANT)
    readiness.clear_cache()
    verdict = readiness.cached_evaluate(TENANT, "bridge", background=False)
    assert not verdict.ready and verdict.reason.startswith("native_trails_delegated")
    _send(adapter, base, "Big Data?")
    assert acs == ["Big Data?", "Big Data?"]
    assert [e["event_type"] for e in _chain(base)].count("routing.rollback_reset") == 1
    _verify(base)


def test_live_shaped_history_never_disagrees_so_phase2_stays_shadow(bridge, monkeypatch):
    """Shape of the live ledger on 2026-10-04: every turn native, the Skill always
    agrees. Phase 2 can only de-escalate to native, so it has nothing to change and
    the gate says so instead of activating a no-op."""
    adapter, base, acs = bridge
    from core.skills.os_skills.monitoring import readiness, routing_ledger

    for i in range(600):
        tid = routing_ledger.new_turn_id()
        cx = ("simple", "medium", "complex")[i % 3]
        routing_ledger.record_decision(
            tenant_id=TENANT, turn_id=tid, surface="console", phase="shadow",
            bundled="native", used="native", source="bundled", skill="native",
            skill_conf=0.8, features={"complexity": cx})
        routing_ledger.record_outcome(tenant_id=TENANT, turn_id=tid, surface="console",
                                      used="native", ok=True, latency_ms=5)
    verdict = readiness.cached_evaluate(TENANT, "console", background=False)
    assert (verdict.ready, verdict.reason, verdict.decisions) == (False, "skill_never_disagrees", 600)

    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    _send(adapter, base, "Wie spät ist es?")
    row = [r for r in _ledger() if r["surface"] == "bridge"][-1]
    assert (row["phase"], row["used"], row["source"]) == ("shadow", "native", "bundled")
    assert acs == []
    _verify(base)
