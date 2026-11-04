"""Adversarial review 2026-11-05, area DR (delegation routing + its learning loop).

Every test drives a production entry point: the shared routing rule with a real
``engine.engine_selection`` / ``delegation.route_selection_policy`` hook writing into a
sandboxed hash chain, the real console router (``GET /v1/console/feedback/skill/*``),
the real phase gate (``dual_write.decide``) over a ledger written by the real ledger
writer, and the real path-gate predicate.

Two findings stay OPEN (their fix needs a decision or a file outside this review's
scope); their reproductions run only with ``DR_OPEN=1`` so the shared tree stays green:
    DR_OPEN=1 .venv/bin/python -m pytest tests/learning/test_review_20261105_DR_e2e.py
"""
from __future__ import annotations

import json
import os
import types

import pytest

from tests.learning.console_client import console_client

_OPEN = pytest.mark.skipif(
    not os.environ.get("DR_OPEN"),
    reason="open finding (fix outside the DR scope / needs a decision): run with DR_OPEN=1",
)


def _records(sb, name: str) -> list[dict]:
    out = []
    for r in sb.chain_records():
        if (r.get("event") or r.get("event_type")) == name:
            out.append(r)
    return out


def _details(rec: dict) -> dict:
    d = rec.get("details")
    return d if isinstance(d, dict) else rec


# ── DR-1: the two ADR-0251 D2 refusals were chained with their payload floored ─────

def _enable_extension_points(sb) -> None:
    from corvin_plugins import extension_points as ep

    ep.clear_all()
    flags = sb.tenant_home / "global" / "features.json"
    flags.write_text(json.dumps({"flags": {ep.FLAG_ID: True}}), encoding="utf-8")


def test_dr1_refused_engine_hook_record_keeps_mode_and_rejected_engine(tmp_path):
    with console_client(tmp_path) as sb:
        import delegation_policy as dp
        from corvin_plugins import extension_points as ep

        _enable_extension_points(sb)
        try:
            ep.register_hook("engine.engine_selection", lambda req: "tde",
                             plugin_id="hostile", tenant_id=sb.tenant_id)
            served = dp.resolve_worker_engine(
                mode="native", force_delegate=False, is_big_data=False,
                tde_available=False, quota_ok=False, tenant_id=sb.tenant_id)
        finally:
            ep.clear_all()
        assert served == "native"
        recs = _records(sb, "plugin.extension_engine_refused")
        assert len(recs) == 1, [r.get("event") for r in sb.chain_records()]
        d = _details(recs[0])
        # The record's whole content is "a hook tried to route to X on a Y install".
        assert d.get("refused") == "tde" and d.get("operator_mode") == "native", d
        assert d.get("bundled") == "native" and d.get("point") == "engine.engine_selection", d
        assert "_dropped_fields" not in d, d
        assert recs[0].get("severity") == "WARNING", recs[0]


def test_dr1_refused_route_hook_record_keeps_the_rejected_route(tmp_path):
    with console_client(tmp_path) as sb:
        import delegation_policy as dp
        from corvin_plugins import extension_points as ep

        _enable_extension_points(sb)
        try:
            ep.register_hook("delegation.route_selection_policy", lambda req: "tde",
                             plugin_id="hostile", tenant_id=sb.tenant_id)
            verdict = dp.resolve_delegation_route(False, tenant_id=sb.tenant_id)
        finally:
            ep.clear_all()
        assert verdict is False
        recs = _records(sb, "plugin.extension_route_refused")
        assert len(recs) == 1, [r.get("event") for r in sb.chain_records()]
        d = _details(recs[0])
        assert d.get("refused") == "tde" and d.get("bundled") == "native", d
        assert d.get("point") == "delegation.route_selection_policy", d
        assert "_dropped_fields" not in d, d
        assert recs[0].get("severity") == "WARNING", recs[0]


# ── DR-2: the stored-feedback counts were capped at the read window ────────────────

def test_dr2_feedback_status_and_history_count_every_stored_record(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.feedback import history_store

        n = history_store._MAX_READ + 7
        path = history_store._path(sb.tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # The real writer's line shape, written in bulk (5 000+ fsyncs would take minutes).
        with open(path, "w", encoding="utf-8") as fh:
            for i in range(n):
                fh.write(json.dumps({
                    "feedback_id": f"fb_{i}", "timestamp": f"2026-11-05T00:00:{i % 60:02d}Z",
                    "skill_id": "os.delegation_router", "subject_id": f"t{i}",
                    "rating": 2 if i < 7 else -1, "category": "accuracy"}, sort_keys=True) + "\n")
        history_store.append(sb.tenant_id, feedback_id="fb_last", timestamp="2026-11-05T01:00:00Z",
                             skill_id="os.delegation_router", subject_id="tl", rating=1,
                             category="accuracy")
        st = sb.client.get("/v1/console/feedback/skill/status")
        assert st.status_code == 200, st.text
        assert st.json()["total_received"] == n + 1, st.json()
        assert st.json()["last_feedback_timestamp"] == "2026-11-05T01:00:00Z"
        h = sb.client.get("/v1/console/feedback/skill/history",
                          params={"skill_id": "os.delegation_router", "limit": 3})
        assert h.status_code == 200, h.text
        body = h.json()
        assert body["total_count"] == n + 1, body["total_count"]
        assert [e["feedback_id"] for e in body["feedback_events"]] == [
            "fb_last", f"fb_{n - 1}", f"fb_{n - 2}"]
        expected_avg = (7 * 2 + (n - 7) * -1 + 1) / (n + 1)
        assert abs(body["avg_rating"] - expected_avg) < 1e-9, body["avg_rating"]


# ── DR-3 (OPEN): the L5 gate state is not protected against a naive worker write ───

@_OPEN
@pytest.mark.parametrize("leaf", [
    ("learning", "routing", "rollback_state.json"),
    ("learning", "routing", "rollback_pending.json"),
    ("learning", "routing", "routing_ledger.jsonl"),
    ("skills", "os_delegation_router_config.json"),
])
def test_dr3_phase2_gate_state_is_write_protected(tmp_path, monkeypatch, leaf):
    """A worker that deletes ``rollback_state.json`` clears a tripped rollback without
    the audited operator reset; 500 appended ledger lines clear the readiness gate."""
    import importlib.util
    import pathlib

    home = tmp_path / "corvin_home"
    monkeypatch.setenv("CORVIN_HOME", str(home))
    repo = pathlib.Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "_dr_path_gate", repo / "corvin_operator" / "voice" / "hooks" / "path_gate.py")
    pg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pg)
    target = home.joinpath("tenants", "_default", *leaf)
    assert pg.is_protected_path(str(target)), target


# ── DR-5 (OPEN): an operator's "correct" rating switches the router's advice off ───

@_OPEN
def test_dr5_rating_the_router_correct_never_stops_its_advice_being_served(tmp_path):
    """``loop_closure`` moves the threshold UP on a positive outcome ("more selective").
    For ``os.delegation_router`` that threshold is the acceptance bar of its
    de-escalation advice (``dual_write.clamp``: served iff confidence >= threshold), so
    each "this was right" rating makes the Skill's advice LESS likely to be served, and
    each "this was wrong" rating more likely. The default bar also differs: 0.75 before
    any learned version (delegation_policy), 0.70 as the loop's starting point."""
    with console_client(tmp_path) as sb:
        from core.skills.os_skills import delegation_router
        from core.learning.learning_events import EventType, LearningEvent
        from core.skills.os_skills.monitoring import dual_write

        # The trust gate admits feedback only for a recorded execution (as the registry
        # leaves one in the tenant's learning store).
        ev = LearningEvent.create(EventType.SKILL_EXECUTED, "os.delegation_router",
                                  sb.tenant_id, signal={"output": {}})
        f = sb.tenant_home / "learning" / "events" / (ev.timestamp[:10] + ".jsonl")
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev.to_dict()) + "\n")
        for i in range(3):
            r = sb.client.post("/v1/console/feedback/skill", headers=sb.csrf_headers, json={
                "skill_id": "os.delegation_router", "subject_id": f"turn{i}",
                "rating": 2, "category": "accuracy"})
            assert r.status_code == 200 and r.json()["config_applied"], r.text
        out = delegation_router.advise_from_input({
            "tenant_id": sb.tenant_id, "bundled_engine": "acs", "mode": "acs",
            "features": {"complexity": "simple", "len_bucket": "xs"}, "shadow": False})
        assert out["engine"] == "native"
        served, source, _ = dual_write.clamp(
            bundled="acs", skill_engine=out["engine"], skill_conf=out["confidence"],
            force_delegate=False, threshold=out.get("confidence_threshold", 0.75))
        assert (served, source) == ("native", "skill"), out


# ── DR-4 (OPEN): a healthy Phase 2 refuses itself once its window holds no proxy ───

@_OPEN
def test_dr4_a_healthy_phase2_is_not_refused_by_its_own_success(tmp_path, monkeypatch):
    """Readiness compares the bundled rule's DELEGATED outcomes of disagreements with
    native outcomes. In a cleared Phase 2 every disagreement is served native by the
    Skill, so after WINDOW_S (30 d) of healthy dual_write no delegated disagreement is
    left in the window and the gate answers ``insufficient_bucket_samples`` — Phase 2
    switches itself off (and the bridge's console soak restarts) with the Skill's
    served success equal to the bundled one."""
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import dual_write, readiness, routing_ledger

        dual_write.reset_for_tests()
        clock = [1_800_000_000.0 - 29 * 86400]
        monkeypatch.setattr(routing_ledger, "time",
                            types.SimpleNamespace(time=lambda: clock[0]))
        for i in range(600):
            clock[0] += 3600
            tid = routing_ledger.new_turn_id()
            skill_served = i % 2 == 0
            routing_ledger.record_decision(
                tenant_id=sb.tenant_id, turn_id=tid, surface="console", phase="dual_write",
                bundled="acs" if skill_served else "native", used="native",
                source="skill" if skill_served else "bundled", skill="native",
                skill_conf=0.8, features={"complexity": "simple", "mode": "acs"})
            routing_ledger.record_outcome(tenant_id=sb.tenant_id, turn_id=tid,
                                          surface="console", used="native", ok=True,
                                          latency_ms=5)
        now = clock[0] + 60
        verdict = readiness.evaluate(sb.tenant_id, "console", now=now)
        assert verdict.ready, verdict
        dual_write.reset_for_tests()
