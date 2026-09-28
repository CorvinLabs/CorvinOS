"""ADR-2092 — L5 routing loop: ledger, judge, readiness, clamp, phase gate, Skill.

Everything here runs against a temporary CORVIN_HOME; the routing call site is
exercised through ``delegation_policy.route_and_record`` with the real booted Skill
registry. The audit writer is the boundary faked in this file — the real chain is
covered by ``tests/e2e/test_l5_routing_loop_bridge_e2e.py``.
"""
from __future__ import annotations

import itertools
import json
import stat
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "corvin_operator" / "bridges" / "shared"))

from core.skills.os_skills import delegation_router  # noqa: E402
from core.skills.os_skills.monitoring import (  # noqa: E402
    correctness_tracker,
    dual_write,
    readiness,
    rollback_detector,
    routing_ledger,
)

TENANT = "_default"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CORVIN_ACP_PHASE", raising=False)
    dual_write.reset_for_tests()
    yield
    dual_write.reset_for_tests()


class AuditSpy:
    def __init__(self, commit: bool = True):
        self.commit = commit
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, event_type: str, details: dict) -> bool:
        self.calls.append((event_type, dict(details)))
        return self.commit


def _seed(n: int, *, surface="console", source="bundled", bundled="native", used="native",
          skill="native", ok=True, complexity="simple", outcome_used=None, force=False):
    for _ in range(n):
        tid = routing_ledger.new_turn_id()
        routing_ledger.record_decision(
            tenant_id=TENANT, turn_id=tid, surface=surface, phase="shadow",
            bundled=bundled, used=used, source=source, skill=skill, skill_conf=0.8,
            features={"complexity": complexity, "force_delegate": force},
        )
        routing_ledger.record_outcome(
            tenant_id=TENANT, turn_id=tid, surface=surface,
            used=outcome_used or used, ok=ok, latency_ms=10,
        )


# ── ledger ──────────────────────────────────────────────────────────────────

class TestLedger:
    def test_decision_and_outcome_join_and_file_is_private(self):
        tid = routing_ledger.new_turn_id()
        assert routing_ledger.record_decision(
            tenant_id=TENANT, turn_id=tid, surface="bridge", phase="shadow",
            bundled="acs", used="acs", source="bundled", skill="native", skill_conf=0.8,
        )
        assert routing_ledger.record_outcome(
            tenant_id=TENANT, turn_id=tid, surface="bridge", used="acs", ok=True, latency_ms=5,
        )
        joined = routing_ledger.join(routing_ledger.read_records(TENANT))
        assert len(joined) == 1 and joined[0]["outcome"]["ok"] is True
        mode = stat.S_IMODE(routing_ledger.ledger_path(TENANT).stat().st_mode)
        assert mode == 0o600

    @pytest.mark.parametrize("field,value", [
        ("surface", "email"), ("phase", "phase2_real"), ("source", "plugin"),
        ("bundled", "hermes"), ("used", "gpt"), ("turn_id", "../etc"),
    ])
    def test_out_of_vocabulary_records_are_refused(self, field, value):
        kwargs = dict(tenant_id=TENANT, turn_id="abc123", surface="bridge", phase="shadow",
                      bundled="native", used="native", source="bundled")
        kwargs[field] = value
        assert routing_ledger.record_decision(**kwargs) is False
        assert routing_ledger.read_records(TENANT) == []

    def test_features_keep_only_the_closed_vocabulary(self):
        cleaned = routing_ledger.clean_features({
            "complexity": "simple", "len_bucket": "xs", "has_table": True,
            "complexity_note": "please email me", "mode": "hermes",
            "has_code": "yes", "prompt": "the user's text",
        })
        assert cleaned == {"complexity": "simple", "len_bucket": "xs", "has_table": True}

    def test_a_sensitive_value_is_dropped_even_inside_the_vocabulary(self, monkeypatch):
        monkeypatch.setitem(routing_ledger.FEATURE_VOCAB, "complexity",
                            frozenset({"simple", "alice@example.com"}))
        assert routing_ledger.clean_features({"complexity": "alice@example.com"}) == {}

    def test_a_pii_scan_that_raises_drops_the_field(self, monkeypatch):
        import core.pii.sensitive as sensitive

        def boom(_):
            raise sensitive.PIIDetectionFailedClosed("scan failed")

        monkeypatch.setattr(sensitive, "has_sensitive", boom)
        assert routing_ledger.clean_features({"complexity": "simple"}) == {}

    def test_malformed_lines_are_skipped_not_fatal(self):
        _seed(3)
        with open(routing_ledger.ledger_path(TENANT), "a") as fh:
            fh.write('{"v":1,"kind":"decision","ts":"yesterday","turn_id":"x"}\n')
            fh.write('{"v":1,"kind":"decision","ts":1,"turn_id":["a"],"surface":"bridge"}\n')
            fh.write('{"v":1,"kind":"decision","ts":1,"turn_id":"ab","surface":"bridge","used":"native","bundled":"native","source":"bundled","features":"x"}\n')
            fh.write("not json\n")
        rows = routing_ledger.join(routing_ledger.read_records(TENANT))
        assert len(rows) == 3
        assert rollback_detector.evaluate(TENANT, audit=AuditSpy()) is False

    def test_ledger_never_contains_prompt_text(self):
        import delegation_policy as dp

        prompt = "Bitte analysiere die Kundendaten von alice@example.com"
        dp.route_and_record(
            tenant_id=TENANT, bundled="native", force_delegate=False, is_big_data=False,
            mode="native", surface="bridge", features=dp.routing_features(prompt), sink={},
        )
        raw = routing_ledger.ledger_path(TENANT).read_text()
        assert "alice" not in raw and "Kundendaten" not in raw


# ── correctness judge + rollback ────────────────────────────────────────────

class TestRollback:
    def test_below_the_sample_floor_nothing_is_judged(self):
        _seed(50, source="skill", ok=False)
        _seed(50, source="bundled", ok=True)
        rows = routing_ledger.join(routing_ledger.read_records(TENANT))
        m = correctness_tracker.compute_metrics(rows)
        assert not m.judgeable and not correctness_tracker.should_rollback(m)
        assert rollback_detector.evaluate(TENANT, audit=AuditSpy()) is False

    def test_a_trailing_skill_trips_audit_first_and_persists(self):
        _seed(100, source="skill", ok=True)
        _seed(10, source="skill", ok=False)
        _seed(110, source="bundled", ok=True)
        spy = AuditSpy()
        assert rollback_detector.evaluate(TENANT, audit=spy) is True
        assert [c[0] for c in spy.calls] == ["routing.rollback_triggered"]
        assert rollback_detector.rollback_active(TENANT)
        spy2 = AuditSpy()
        assert rollback_detector.evaluate(TENANT, audit=spy2) is True
        assert spy2.calls == []  # already tripped: no second record

    def test_a_skill_within_two_percent_does_not_trip(self):
        _seed(99, source="skill", ok=True)
        _seed(1, source="skill", ok=False)
        _seed(100, source="bundled", ok=True)
        assert rollback_detector.evaluate(TENANT, audit=AuditSpy()) is False

    def test_a_torn_state_file_keeps_the_trip_active(self):
        _seed(100, source="skill", ok=False)
        _seed(100, source="bundled", ok=True)
        rollback_detector.evaluate(TENANT, audit=AuditSpy())
        rollback_detector.state_path(TENANT).write_text('{"tripp')
        assert rollback_detector.rollback_active(TENANT)
        rollback_detector.state_path(TENANT).write_text("[]")
        assert rollback_detector.rollback_active(TENANT)

    def test_reset_without_a_committed_audit_is_refused(self):
        _seed(100, source="skill", ok=False)
        _seed(100, source="bundled", ok=True)
        rollback_detector.evaluate(TENANT, audit=AuditSpy())
        with pytest.raises(RuntimeError):
            rollback_detector.reset_rollback(TENANT, audit=AuditSpy(commit=False))
        assert rollback_detector.rollback_active(TENANT)

    def test_reset_is_audited(self):
        _seed(100, source="skill", ok=False)
        _seed(100, source="bundled", ok=True)
        rollback_detector.evaluate(TENANT, audit=AuditSpy())
        spy = AuditSpy()
        rollback_detector.reset_rollback(TENANT, audit=spy)
        assert spy.calls[0][0] == "routing.rollback_reset"
        assert not rollback_detector.rollback_active(TENANT)


# ── readiness (G2 exit) ─────────────────────────────────────────────────────

class TestReadiness:
    def test_empty_ledger_is_not_ready(self):
        r = readiness.evaluate(TENANT, "console")
        assert not r.ready and r.reason == "insufficient_samples"

    def test_low_join_rate_is_not_ready(self):
        _seed(400)
        for _ in range(200):
            routing_ledger.record_decision(
                tenant_id=TENANT, turn_id=routing_ledger.new_turn_id(), surface="console",
                phase="shadow", bundled="native", used="native", source="bundled",
            )
        assert readiness.evaluate(TENANT, "console").reason == "join_rate_below_floor"

    def test_a_skill_that_never_disagrees_is_not_ready(self):
        _seed(500)
        assert readiness.evaluate(TENANT, "console").reason == "skill_never_disagrees"

    def test_disagreements_with_supporting_evidence_are_ready(self):
        _seed(470)
        _seed(40, bundled="acs", used="acs", skill="native", ok=True)
        r = readiness.evaluate(TENANT, "console")
        assert r.ready, r

    def test_native_trailing_delegated_blocks(self):
        _seed(430, ok=True)
        _seed(70, ok=False)  # native: 86 % success
        _seed(40, bundled="acs", used="acs", skill="native", ok=True)  # delegated: 100 %
        assert readiness.evaluate(TENANT, "console").reason.startswith("native_trails_delegated")

    def test_surfaces_are_judged_separately(self):
        _seed(470, surface="console")
        _seed(40, surface="console", bundled="acs", used="acs", skill="native")
        assert readiness.evaluate(TENANT, "console").ready
        assert not readiness.evaluate(TENANT, "bridge").ready

    def test_bridge_waits_for_a_seven_day_console_soak(self):
        _seed(470, surface="bridge")
        _seed(40, surface="bridge", bundled="acs", used="acs", skill="native")
        assert readiness.evaluate(TENANT, "bridge").reason == "console_soak_incomplete"
        _backdated_console_dual_write(days=3)
        assert readiness.evaluate(TENANT, "bridge").reason == "console_soak_incomplete"
        _backdated_console_dual_write(days=8)
        assert readiness.evaluate(TENANT, "bridge").ready

    def test_background_refresh_answers_not_ready_until_it_lands(self):
        _seed(470)
        _seed(40, bundled="acs", used="acs", skill="native")
        first = readiness.cached_evaluate(TENANT, "console")
        assert (first.ready, first.reason) == (False, "evaluating")
        deadline = time.time() + 5
        while time.time() < deadline and not readiness.cached_evaluate(TENANT, "console").ready:
            time.sleep(0.05)
        assert readiness.cached_evaluate(TENANT, "console").ready

    def test_explicit_delegate_disagreements_do_not_count(self):
        _seed(470)
        _seed(40, bundled="acs", used="acs", skill="native", force=True)
        assert readiness.evaluate(TENANT, "console").reason == "skill_never_disagrees"


def _backdated_console_dual_write(*, days: float):
    rec = {"v": 1, "kind": "decision", "ts": time.time() - days * 86400,
           "turn_id": routing_ledger.new_turn_id(), "surface": "console",
           "phase": "dual_write", "bundled": "native", "skill": "native",
           "skill_conf": 0.9, "used": "native", "source": "bundled",
           "clamped": False, "features": {}}
    path = routing_ledger.ledger_path(TENANT)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


# ── G3 clamp ────────────────────────────────────────────────────────────────

ENGINES = ("native", "acs", "tde")


class TestClamp:
    @pytest.mark.parametrize(
        "bundled,skill,force,conf",
        list(itertools.product(ENGINES, ENGINES + (None,), (False, True), (None, 0.1, 0.8, 1.0))),
    )
    def test_never_escalates_never_leaves_the_permitted_set(self, bundled, skill, force, conf):
        engine, source, _clamped = dual_write.clamp(
            bundled=bundled, skill_engine=skill, skill_conf=conf, force_delegate=force,
        )
        assert engine in {bundled, "native"}
        if force:
            assert engine == bundled
        if engine != bundled:
            assert source == "skill" and engine == "native"

    def test_de_escalation_below_threshold_is_not_served(self):
        assert dual_write.clamp(bundled="acs", skill_engine="native", skill_conf=0.7,
                                force_delegate=False) == ("acs", "bundled", False)

    def test_advice_toward_another_delegation_engine_is_clamped(self):
        assert dual_write.clamp(bundled="acs", skill_engine="tde", skill_conf=0.99,
                                force_delegate=False) == ("acs", "bundled", True)


# ── G4 phase gate ───────────────────────────────────────────────────────────

class TestEffectivePhase:
    def test_default_is_shadow(self):
        assert dual_write.effective_phase(tenant_id=TENANT, surface="bridge",
                                          skill_available=True) == "shadow"

    @pytest.mark.parametrize("requested,available,reason", [
        ("phase2_real", True, "phase2_real_unsupported"),
        ("phase2_dual_write", False, "skill_not_booted"),
        ("phase2_dual_write", True, "console_soak_incomplete"),
    ])
    def test_refusals_run_shadow_and_are_audited_once(self, monkeypatch, requested, available, reason):
        monkeypatch.setenv("CORVIN_ACP_PHASE", requested)
        readiness.cached_evaluate(TENANT, "bridge", background=False)
        spy = AuditSpy()
        for _ in range(3):
            assert dual_write.effective_phase(tenant_id=TENANT, surface="bridge",
                                              skill_available=available, audit=spy) == "shadow"
        assert [c[0] for c in spy.calls] == ["routing.phase2_refused"]
        assert spy.calls[0][1]["reason"] == reason

    def test_a_pure_query_does_not_consume_the_refusal_record(self, monkeypatch):
        monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_real")
        dual_write.effective_phase(tenant_id=TENANT, surface="console", skill_available=True, audit=None)
        spy = AuditSpy()
        dual_write.effective_phase(tenant_id=TENANT, surface="console", skill_available=True, audit=spy)
        assert [c[0] for c in spy.calls] == ["routing.phase2_refused"]

    def test_an_uncommitted_refusal_is_retried(self, monkeypatch):
        monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_real")
        failing = AuditSpy(commit=False)
        dual_write.effective_phase(tenant_id=TENANT, surface="console", skill_available=True, audit=failing)
        spy = AuditSpy()
        dual_write.effective_phase(tenant_id=TENANT, surface="console", skill_available=True, audit=spy)
        assert len(spy.calls) == 1

    def test_tripped_rollback_refuses(self, monkeypatch):
        monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
        _seed(100, source="skill", ok=False)
        _seed(100, source="bundled", ok=True)
        rollback_detector.evaluate(TENANT, audit=AuditSpy())
        spy = AuditSpy()
        assert dual_write.effective_phase(tenant_id=TENANT, surface="bridge",
                                          skill_available=True, audit=spy) == "shadow"
        assert spy.calls[0][1]["reason"] == "rollback_active"

    def test_ready_evidence_clears_dual_write(self, monkeypatch):
        monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
        _seed(470)
        _seed(40, bundled="acs", used="acs", skill="native")
        readiness.cached_evaluate(TENANT, "console", background=False)
        assert dual_write.effective_phase(tenant_id=TENANT, surface="console",
                                          skill_available=True) == "dual_write"


# ── the Skill ───────────────────────────────────────────────────────────────

class TestRouterSkill:
    @pytest.mark.parametrize("bundled,force,big,mode,complexity,length,table", list(itertools.product(
        ENGINES, (False, True), (False, True), ENGINES,
        ("simple", "medium", "complex", "unknown"), ("xs", "m"), (False, True),
    )))
    def test_advice_is_confirm_or_native_only(self, bundled, force, big, mode, complexity, length, table):
        engine, conf, _ = delegation_router.advise(
            bundled=bundled, force_delegate=force, is_big_data=big, mode=mode,
            features={"complexity": complexity, "len_bucket": length, "has_table": table},
        )
        assert engine in {bundled, "native"}
        assert 0.0 <= conf <= 1.0
        if force:
            assert engine == bundled

    def test_output_carries_engine_for_the_audit_projection_and_l10(self):
        out = delegation_router.DelegationRouterSkill().execute(
            {"tenant_id": TENANT, "bundled_engine": "acs", "is_big_data": True,
             "features": {"complexity": "simple", "len_bucket": "xs"}, "shadow": True})
        assert out["engine"] == out["decision"] == "native"
        from core.skills.skill_registry_phase1 import decision_summary

        assert decision_summary(out)["engine"] == "native"

    def test_legacy_complexity_int_still_works(self):
        out = delegation_router.DelegationRouterSkill().execute({"tenant_id": TENANT, "complexity": 9})
        assert out["engine"] == "native"


# ── the call site ───────────────────────────────────────────────────────────

@pytest.fixture
def booted_registry():
    from core.skills.boot import boot_skills

    boot_skills(tenant_id=TENANT, audit_emit=lambda *_: None, wire_learning=False)
    yield


class TestRouteAndRecord:
    def _route(self, dp, **kw):
        sink: dict = {}
        base = dict(tenant_id=TENANT, bundled="acs", force_delegate=False, is_big_data=True,
                    mode="native", surface="console",
                    features={"complexity": "simple", "len_bucket": "xs"}, sink=sink)
        base.update(kw)
        return dp.route_and_record(**base), sink

    def test_shadow_serves_bundled_and_records_the_advice(self, booted_registry):
        import delegation_policy as dp

        engine, sink = self._route(dp)
        assert engine == "acs" and sink["route_source"] == "bundled"
        (rec,) = routing_ledger.read_records(TENANT)
        assert rec["skill"] == "native" and rec["used"] == "acs" and rec["phase"] == "shadow"

    def test_query_mode_records_nothing(self, booted_registry):
        import delegation_policy as dp

        engine = dp.route_and_record(tenant_id=TENANT, bundled="acs", force_delegate=False,
                                     is_big_data=True, mode="native", surface="console",
                                     record=False)
        assert engine == "acs" and routing_ledger.read_records(TENANT) == []

    def test_cleared_phase2_serves_native_only_after_the_audit_commits(self, booted_registry, monkeypatch):
        import delegation_policy as dp

        monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
        _seed(470)
        _seed(40, bundled="acs", used="acs", skill="native")
        readiness.cached_evaluate(TENANT, "console", background=False)
        spy = AuditSpy(commit=True)
        monkeypatch.setattr(dp, "_audit", lambda et, d, tenant_id: spy(et, d))
        engine, sink = self._route(dp)
        assert engine == "native" and sink["route_source"] == "skill"
        assert [c[0] for c in spy.calls] == ["routing.phase2_decision"]

    def test_uncommitted_audit_keeps_the_bundled_route(self, booted_registry, monkeypatch):
        import delegation_policy as dp

        monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
        _seed(470)
        _seed(40, bundled="acs", used="acs", skill="native")
        readiness.cached_evaluate(TENANT, "console", background=False)
        monkeypatch.setattr(dp, "_audit", lambda et, d, tenant_id: False)
        engine, sink = self._route(dp)
        assert engine == "acs" and sink["route_source"] == "bundled"

    def test_explicit_delegate_is_never_overridden(self, booted_registry, monkeypatch):
        import delegation_policy as dp

        monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
        _seed(470)
        _seed(40, bundled="acs", used="acs", skill="native")
        readiness.cached_evaluate(TENANT, "console", background=False)
        monkeypatch.setattr(dp, "_audit", lambda et, d, tenant_id: True)
        engine, _ = self._route(dp, force_delegate=True)
        assert engine == "acs"

    def test_without_a_booted_registry_the_rule_stands_and_is_recorded(self):
        import delegation_policy as dp
        from core.skills import skill_registry_phase1 as reg

        saved = reg._global_registry
        reg._global_registry = None
        try:
            engine, sink = self._route(dp)
        finally:
            reg._global_registry = saved
        assert engine == "acs"
        (rec,) = routing_ledger.read_records(TENANT)
        assert rec["skill"] is None

    def test_outcome_on_a_skill_served_turn_runs_the_judge(self, booted_registry, monkeypatch):
        import delegation_policy as dp

        _seed(100, source="skill", ok=False)
        _seed(100, source="bundled", ok=True)
        calls: list[str] = []
        monkeypatch.setattr(dp, "_audit", lambda et, d, tenant_id: calls.append(et) or True)
        tid = routing_ledger.new_turn_id()
        dp.record_turn_outcome(tenant_id=TENANT, turn_id=tid, surface="bridge", used="native",
                               ok=False, latency_ms=1, source="skill")
        deadline = time.time() + 5
        while time.time() < deadline and not rollback_detector.rollback_active(TENANT):
            time.sleep(0.02)
        assert calls == ["routing.rollback_triggered"]
        assert rollback_detector.rollback_active(TENANT)

    def test_post_hoc_record_is_pinned_to_shadow(self, booted_registry, monkeypatch):
        import delegation_policy as dp

        monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
        _seed(470)
        _seed(40, bundled="acs", used="acs", skill="native")
        readiness.cached_evaluate(TENANT, "console", background=False)
        calls: list[str] = []
        monkeypatch.setattr(dp, "_audit", lambda et, d, tenant_id: calls.append(et) or True)
        engine, sink = self._route(dp, post_hoc=True)
        assert engine == "acs" and sink["route_source"] == "bundled"
        assert "routing.phase2_decision" not in calls
        row = [r for r in routing_ledger.read_records(TENANT) if r["turn_id"] == sink["turn_id"]][0]
        assert row["phase"] == "shadow"

    def test_a_skill_timeout_leaves_the_rule_standing(self, booted_registry, monkeypatch):
        import delegation_policy as dp
        from core.skills.skill_registry_phase1 import SkillExecutionResult

        reg = dp._router_registry()
        monkeypatch.setattr(reg, "execute", lambda *a, **k: SkillExecutionResult(
            skill_id="os.delegation_router", status="timeout", tenant_id=TENANT))
        engine, sink = self._route(dp)
        assert engine == "acs"
        (row,) = routing_ledger.read_records(TENANT)
        assert row["skill"] is None

    def test_each_tenant_writes_its_own_ledger(self, booted_registry):
        import delegation_policy as dp

        dp.route_and_record(tenant_id="acme", bundled="native", force_delegate=False,
                            is_big_data=False, mode="native", surface="console", sink={})
        assert routing_ledger.read_records(TENANT) == []
        assert len(routing_ledger.read_records("acme")) == 1


def test_audit_events_are_registered_in_both_registries():
    import forge.security_events as se

    for event in ("routing.phase2_decision", "routing.phase2_refused",
                  "routing.rollback_triggered", "routing.rollback_reset"):
        assert event in se.EVENT_SEVERITY
        assert event in se._EVENT_ALLOWLIST


class TestSkillInputIsPiiGated:
    """ADR-2092 G2: the ADR-0297 gate runs BEFORE the Skill input, not only at the ledger."""

    def test_hostile_features_never_reach_the_skill(self, booted_registry, monkeypatch):
        import delegation_policy as dp

        real = dp._router_registry()
        seen: list[dict] = []

        class Capturing:
            def get(self, skill_id):
                return real.get(skill_id)

            def execute(self, skill_id, input_data, **kw):
                seen.append(dict(input_data))
                return real.execute(skill_id, input_data, **kw)

        monkeypatch.setattr(dp, "_router_registry", lambda: Capturing())
        dp.route_and_record(
            tenant_id=TENANT, bundled="native", force_delegate=False, is_big_data=False,
            mode="native", surface="console", sink={},
            features={
                "prompt": "please mail max.mustermann@example.com",  # not in vocabulary
                "complexity": "alice@example.com",                    # in vocabulary, bad value
                "len_bucket": "xs",
                "has_code": "yes",                                     # not a real bool
            },
        )
        (inp,) = seen
        feats = inp["features"]
        assert "prompt" not in feats
        assert "complexity" not in feats and "has_code" not in feats
        assert feats["len_bucket"] == "xs" and feats["mode"] == "native"
        assert "example.com" not in json.dumps(inp)
