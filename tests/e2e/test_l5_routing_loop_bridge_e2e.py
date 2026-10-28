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
    # Order independence (review s3_routing-10): the console stream_turn harness
    # plants a file-less stand-in for `license` in sys.modules. The licence twin
    # finder (corvin_operator/license/__init__.py) then hands that stand-in out as
    # `corvin_operator.license` too, and the harness tearDown pops only `license`.
    # The adapter's `from license.validator import …` then resolves the twin stub
    # ("'license' is not a package"), falls back to the Free tier and blocks the
    # discord bridge — every turn here writes no reply. Drop every file-less
    # stand-in under either twin name for this test only.
    for name in [m for m in sys.modules
                 if m in ("license", "corvin_operator.license")
                 or m.startswith(("license.", "corvin_operator.license."))]:
        if getattr(sys.modules[name], "__file__", None) is None:
            monkeypatch.delitem(sys.modules, name)
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
    f.write_text(json.dumps(envelope), encoding="utf-8")
    adapter.process_one(f, settings={"whitelist": [SENDER], "voice_summary_mode": "never"})
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((base / "outbox").glob(f"{msg_id}*.json"))]


def _chain(base: Path) -> list[dict]:
    p = base / "audit.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


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
    # ADR-2092 G4: the bridge enters Phase 2 only after a CONTINUOUS 7-day console
    # soak (readiness.CONSOLE_SOAK_S): console dual_write decisions every 12 h for 8 days.
    with open(routing_ledger.ledger_path(TENANT), "a", encoding="utf-8") as fh:
        for half_days in range(16, -1, -1):
            soak = {"v": 1, "kind": "decision", "ts": time.time() - half_days * 43200,
                    "turn_id": routing_ledger.new_turn_id(), "surface": "console",
                    "phase": "dual_write", "bundled": "native", "skill": "native",
                    "skill_conf": 0.9, "used": "native", "source": "bundled",
                    "clamped": False, "features": {}}
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

    # 5. An operator reset does not reopen Phase 2 by itself: the reset restarts the
    #    7-day console soak (review s3_routing-1), so the bridge stays in shadow.
    import delegation_policy as dp

    rollback_detector.reset_rollback(
        TENANT, audit=lambda et, d: dp._audit(et, d, tenant_id=TENANT))
    assert not rollback_detector.rollback_active(TENANT)
    readiness.clear_cache()
    verdict = readiness.cached_evaluate(TENANT, "bridge", background=False)
    assert (verdict.ready, verdict.reason) == (False, "console_soak_incomplete")
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
    assert (verdict.ready, verdict.reason, verdict.decisions) == (False, "no_delegation_subject", 600)

    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    _send(adapter, base, "Wie spät ist es?")
    row = [r for r in _ledger() if r["surface"] == "bridge"][-1]
    assert (row["phase"], row["used"], row["source"]) == ("shadow", "native", "bundled")
    assert acs == []
    _verify(base)


# ── review s3_routing round 1 (2026-10-27) ──────────────────────────────────

def test_a_turn_refused_before_any_engine_ran_is_not_judged(bridge, monkeypatch):
    """s3_routing-4: a budget refusal used to be recorded as a native SUCCESS."""
    adapter, base, acs = bridge
    monkeypatch.setattr(adapter, "_budget_preflight",
                        lambda chat_key, prompt: (False, "Budget exhausted - request refused"))
    out = _send(adapter, base, "wie spät ist es?")
    assert out, "the refusal is still answered"
    (row,) = _ledger()
    assert (row["outcome"]["ok"], row["outcome"].get("excluded")) == (None, "no_engine")
    from core.skills.os_skills.monitoring import routing_ledger

    s = routing_ledger.summarize(routing_ledger.read_records(TENANT), surface="bridge")
    assert (s["decisions"], s["not_judged"], s["by_source"]["bundled"]["n"]) == (0, 1, 0), s
    _verify(base)


def test_a_user_cancelled_turn_is_not_judged(bridge, monkeypatch):
    """s3_routing-3 (bridge side): a /cancel'd native turn is neither a success nor a
    failure of the engine. The CLI stand-in ends the way the real spawn branch does
    on a user /cancel: ``_turn_refused("cancelled", cancelled=True)`` and no answer."""
    adapter, base, acs = bridge

    def cancelled_cli(**kwargs):
        adapter._TURN_OUTCOME.cli_spawned = True
        adapter._turn_refused("cancelled", cancelled=True)
        return ""
    monkeypatch.setattr(adapter, "_call_claude_streaming_impl", cancelled_cli)
    _send(adapter, base, "wie spät ist es?")
    (row,) = _ledger()
    assert (row["outcome"]["ok"], row["outcome"].get("excluded")) == (None, "cancelled")
    from core.skills.os_skills.monitoring import routing_ledger

    s = routing_ledger.summarize(routing_ledger.read_records(TENANT), surface="bridge")
    assert (s["decisions"], s["not_judged_by_reason"]) == (0, {"cancelled": 1}), s
    _verify(base)


def test_one_old_console_dual_write_record_does_not_open_the_bridge(bridge, monkeypatch):
    """s3_routing-1 (P6): one historic console Phase-2 turn and a fresh reset passed the soak."""
    adapter, base, acs = bridge
    from core.skills.os_skills.monitoring import readiness, rollback_detector, routing_ledger

    _seed_ready_history()
    # Replace the continuous soak with ONE console dual_write turn eight days ago.
    path = routing_ledger.ledger_path(TENANT)
    kept = [ln for ln in path.read_text().splitlines()
            if '"phase":"dual_write"' not in ln.replace(" ", "")]
    old = {"v": 1, "kind": "decision", "ts": time.time() - 8 * 86400,
           "turn_id": routing_ledger.new_turn_id(), "surface": "console",
           "phase": "dual_write", "bundled": "native", "skill": "native",
           "skill_conf": 0.9, "used": "native", "source": "bundled",
           "clamped": False, "features": {}}
    path.write_text("\n".join(kept + [json.dumps(old)]) + "\n")
    import delegation_policy as dp

    rollback_detector.reset_rollback(TENANT, audit=lambda et, d: dp._audit(et, d, tenant_id=TENANT))
    readiness.clear_cache()
    assert readiness.cached_evaluate(TENANT, "bridge", background=False).reason == "console_soak_incomplete"
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    _send(adapter, base, "Big Data?")
    assert acs == ["Big Data?"]           # bundled ACS route kept, nothing de-escalated
    row = [r for r in _ledger() if r["surface"] == "bridge"][-1]
    assert (row["phase"], row["source"]) == ("shadow", "bundled")
    _verify(base)


def test_a_shadow_turn_never_reads_the_learned_router_config(bridge, monkeypatch):
    """s3_routing-8: the learned config is read (and acted on) only by the gated Phase 2 path."""
    adapter, base, acs = bridge
    from core.skills.os_skills import skill_adapter

    calls = []
    real = skill_adapter.load_skill_config
    monkeypatch.setattr(skill_adapter, "load_skill_config",
                        lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    _send(adapter, base, "Big Data?")
    (rec,) = _router_records(_chain(base))
    assert rec["details"]["decision"]["shadow"] is True
    assert calls == []


def test_the_evaluating_placeholder_is_never_audited_as_a_refusal(bridge, monkeypatch):
    """s3_routing-9: the first turn of every process audited 'evaluating' as a refusal."""
    adapter, base, acs = bridge
    from core.skills.os_skills.monitoring import readiness

    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    readiness.clear_cache()
    _send(adapter, base, "Big Data?")       # cache empty: answered "evaluating"
    assert _wait_for(lambda: readiness._cache.get((TENANT, "bridge")) is not None)
    _send(adapter, base, "Big Data?")       # the real verdict
    reasons = [e["details"]["reason"] for e in _chain(base)
               if e["event_type"] == "routing.phase2_refused"]
    assert reasons == ["console_soak_incomplete"], reasons
    _verify(base)


def test_a_trip_whose_record_does_not_commit_persists_nothing_but_stops_phase2(bridge, monkeypatch):
    """s3_routing-7: the rollback state file was written although the audit did not commit."""
    adapter, base, acs = bridge
    from core.skills.os_skills.monitoring import rollback_detector
    import delegation_policy as dp

    _seed_ready_history()
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    _send(adapter, base, "Big Data?")       # cleared: the Skill de-escalates
    assert acs == []
    _seed_skill_served(120, ok=False)
    real = dp._audit
    monkeypatch.setattr(dp, "_audit", lambda et, d, *, tenant_id: (
        False if et == "routing.rollback_triggered" else real(et, d, tenant_id=tenant_id)))
    _send(adapter, base, "Big Data?")       # its outcome runs the judge, which trips
    assert _wait_for(lambda: rollback_detector.rollback_active(TENANT))
    assert not rollback_detector.state_path(TENANT).exists()   # audit-first: nothing persisted
    _send(adapter, base, "Big Data?")       # fail-safe: Phase 2 is off in this process
    assert acs == ["Big Data?"]
    monkeypatch.setattr(dp, "_audit", real)
    _seed_skill_served(1, ok=False)
    rows = [r for r in _ledger() if r["source"] == "skill"]
    dp.record_turn_outcome(tenant_id=TENANT, turn_id=rows[0]["turn_id"], surface="bridge",
                           used="native", ok=False, latency_ms=1, source="skill")
    assert _wait_for(lambda: rollback_detector.state_path(TENANT).exists())  # retried, committed
    trips = [e for e in _chain(base) if e["event_type"] == "routing.rollback_triggered"]
    assert len(trips) == 1
    _verify(base)


# ── review r2_s2 (2026-10-28) ───────────────────────────────────────────────

def test_a_turn_after_an_idle_gap_is_served_the_last_verdict(bridge, monkeypatch):
    """r2_s2-1: every turn more than 300 s after the previous one was served shadow in a
    cleared Phase 2; the last verdict is served while it refreshes (up to 1 h)."""
    adapter, base, acs = bridge
    from core.skills.os_skills.monitoring import readiness

    _seed_ready_history()
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    key = (TENANT, "bridge")
    with readiness._lock:
        ts, verdict = readiness._cache[key]
        readiness._cache[key] = (ts - 360.0, verdict)   # the next turn comes 6 min later
    _send(adapter, base, "Big Data?")
    assert acs == []
    row = [r for r in _ledger() if r["surface"] == "bridge"][-1]
    assert (row["phase"], row["source"], row["gate"]) == ("dual_write", "skill", "verdict")
    with readiness._lock:
        ts, verdict = readiness._cache[key]
        readiness._cache[key] = (ts - readiness._CACHE_MAX_STALE_S - 1, verdict)
    _send(adapter, base, "Big Data?")                    # older than an hour: no verdict
    assert acs == ["Big Data?"]
    row = [r for r in _ledger() if r["surface"] == "bridge"][-1]
    assert (row["phase"], row["gate"]) == ("shadow", "pending")
    _verify(base)


def test_an_uncommitted_trip_survives_a_restart_and_is_committed_by_a_later_turn(bridge, monkeypatch):
    """r2_s2-4: the pending trip lived in process memory, was retried only on a
    Skill-served outcome (impossible while it held) and vanished on restart."""
    adapter, base, acs = bridge
    from core.skills.os_skills.monitoring import rollback_detector
    import delegation_policy as dp

    _seed_ready_history()
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    _send(adapter, base, "Big Data?")       # cleared: the Skill de-escalates
    assert acs == []
    _seed_skill_served(120, ok=False)
    real = dp._audit
    monkeypatch.setattr(dp, "_audit", lambda et, d, *, tenant_id: (
        False if et == "routing.rollback_triggered" else real(et, d, tenant_id=tenant_id)))
    monkeypatch.setattr(rollback_detector, "_default_audit", lambda tid: (lambda et, d: False))
    _send(adapter, base, "Big Data?")       # its outcome runs the judge: trip, record fails
    assert _wait_for(lambda: rollback_detector.pending_path(TENANT).exists())
    assert not rollback_detector.state_path(TENANT).exists()
    # The process restarts: every in-process state is gone, the marker is not.
    rollback_detector.reset_for_tests()
    from core.skills.os_skills.monitoring import readiness

    readiness.clear_cache()
    _send(adapter, base, "Big Data?")
    assert acs == ["Big Data?"]             # Phase 2 stays off across the restart
    row = [r for r in _ledger() if r["surface"] == "bridge"][-1]
    assert (row["phase"], row["source"]) == ("shadow", "bundled")
    assert [e for e in _chain(base) if e["event_type"] == "routing.rollback_triggered"] == []
    # The audit writer recovers: the next turn's phase decision commits the trip.
    monkeypatch.setattr(rollback_detector, "_default_audit",
                        lambda tid: (lambda et, d: real(et, d, tenant_id=tid)))
    rollback_detector.reset_for_tests()     # lift the retry throttle of the failed attempt
    _send(adapter, base, "Big Data?")
    assert _wait_for(lambda: rollback_detector.state_path(TENANT).exists())
    assert _wait_for(lambda: not rollback_detector.pending_path(TENANT).exists())
    assert rollback_detector.rollback_active(TENANT)
    trips = [e for e in _chain(base) if e["event_type"] == "routing.rollback_triggered"]
    assert len(trips) == 1
    assert acs == ["Big Data?", "Big Data?"]
    _verify(base)


def test_the_soak_breaks_at_a_console_shadow_decision(bridge, monkeypatch):
    """r2_s2-3: shadow console decisions between dual_write ones were ignored."""
    adapter, base, acs = bridge
    from core.skills.os_skills.monitoring import readiness, routing_ledger

    _seed_ready_history()
    with open(routing_ledger.ledger_path(TENANT), "a") as fh:
        fh.write(json.dumps({"v": 1, "kind": "decision", "ts": time.time() - 3600,
                             "turn_id": routing_ledger.new_turn_id(), "surface": "console",
                             "phase": "shadow", "bundled": "acs", "skill": "native",
                             "skill_conf": 0.9, "used": "acs", "source": "bundled",
                             "clamped": False, "features": {}, "gate": "verdict"}) + "\n")
    readiness.clear_cache()
    assert readiness.cached_evaluate(TENANT, "bridge", background=False).reason \
        == "console_soak_incomplete"
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    _send(adapter, base, "Big Data?")
    assert acs == ["Big Data?"]
    _verify(base)
