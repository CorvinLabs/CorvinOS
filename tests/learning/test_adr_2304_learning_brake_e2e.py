"""ADR-2304 learning brake (PLAN-0966 WP3) through the real boundaries.

Seeded store -> ONE ``LearningHost.tick`` -> the anomaly record, THEN one ``learning.frozen``
-> the learned-config writers refuse through the real console routes (``POST /feedback/skill``,
``POST /learning/feedback``, ``POST /api/dod/feedback``), each refusal chained as
``learning.freeze_refused_write`` -> ``POST /learning/config/unfreeze`` (CSRF, tenant from the
session) lifts it -> writing works again. Edge-triggered, audit-first, no rollback, no switch.

Seeds go through production writers only (``SkillAdapter.apply_config_delta``,
``EventStore.write_event``); nothing in the brake path is faked except, in one test, a chain
write that is made to fail.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.learning.console_client import boot_learning_registry, console_client

ROUTER = "os.delegation_router"
DOD = "os.definition_of_done_verifier"
UNFREEZE = "/v1/console/learning/config/unfreeze"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _tick(host, now=None):
    host._last_anomaly_check = 0.0
    return _run(host.tick(now=now))


def _chain(sb) -> list[dict]:
    path = sb.tenant_home / "global" / "forge" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _named(sb, name: str) -> list[dict]:
    return [r for r in _chain(sb) if name in (r.get("event"), r.get("event_type"))]


def _index(sb, name: str) -> int:
    return next(i for i, r in enumerate(_chain(sb)) if name in (r.get("event"), r.get("event_type")))


def _cfg(sb, skill=ROUTER) -> dict:
    return json.loads((sb.tenant_home / "skills" / f"{skill.replace('.', '_')}_config.json").read_text())


def _verify_chain(sb) -> None:
    from core.deployment.audit_sink import _forge

    se, fp = _forge()
    ok, problems = se.verify_chain(fp.tenant_audit_chain(sb.tenant_id))
    assert ok, problems


def _seed_oscillation(sb, n: int = 4) -> None:
    """n learned-config versions within the hour, through the production writer."""
    from core.skills.os_skills.skill_adapter import SkillAdapter

    for i in range(n):
        SkillAdapter(ROUTER, sb.tenant_id).apply_config_delta(
            {"confidence_threshold": 0.01 if i % 2 == 0 else -0.01}, confidence_delta=0.0)


def _seed_execution(sb, skill_id: str) -> None:
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent

    EventStore(sb.tenant_home, tenant_id=sb.tenant_id).write_event(LearningEvent.create(
        event_type=EventType.SKILL_EXECUTED, skill_id=skill_id, tenant_id=sb.tenant_id,
        signal={"status": "success", "output": {"ok": True}},
        lom="tests/learning/test_adr_2304_learning_brake_e2e.py:_seed_execution"))


def _stream4(sb, rating: int = 2):
    return sb.client.post("/v1/console/feedback/skill", headers=sb.csrf_headers, json={
        "skill_id": ROUTER, "subject_id": f"task-{time.time_ns()}", "rating": rating,
        "category": "accuracy"})


@pytest.fixture
def gate_clock(monkeypatch):
    """The ADR-0534 throttle (5 signals / 60 s) is real; advance ITS clock, never sleep."""
    from core.learning import feedback_gate

    offset = [0.0]
    monkeypatch.setattr(feedback_gate, "time", SimpleNamespace(time=lambda: time.time() + offset[0]))
    return offset


def test_oscillation_freezes_once_refuses_writes_and_a_human_lifts_it(tmp_path, gate_clock):
    with console_client(tmp_path) as sb:
        _seed_oscillation(sb)
        assert len(_cfg(sb)["versions"]) == 4
        before = _cfg(sb)

        from core.background.learning_host import LearningHost

        host = LearningHost(sb.tenant_id)
        _tick(host)
        osc = _named(sb, "skill.config_oscillation")
        frozen = _named(sb, "learning.frozen")
        assert len(osc) == 1 and len(frozen) == 1, (osc, frozen)
        assert _index(sb, "skill.config_oscillation") < _index(sb, "learning.frozen")   # anomaly first
        d = frozen[0]["details"]
        assert (d["skill_id"], d["reason"], d["version_id"]) == (ROUTER, "skill.config_oscillation", "v4")
        assert frozen[0]["severity"] == "WARNING" and frozen[0].get("hash")
        state = _cfg(sb)
        assert state["optimizer"]["frozen"] is True
        assert state["optimizer"]["frozen_reason"] == "skill.config_oscillation"
        # no rollback: same config, same versions, same active version
        assert (state["config"], state["versions"], state["active_version"]) == (
            before["config"], before["versions"], before["active_version"])

        # edge-triggered: the same host and a restarted host record nothing new
        _tick(host)
        _tick(LearningHost(sb.tenant_id))
        assert len(_named(sb, "learning.frozen")) == 1
        daemon = sb.client.get("/v1/console/api/learning/daemon").json()["data"]["anomalies"]
        assert [a.get("freeze") for a in daemon if a["event"] == "skill.config_oscillation"] == ["frozen"]

        # refused through the stream-4 route (SkillAdapter.apply_config_delta)
        _seed_execution(sb, ROUTER)
        gate_clock[0] += 61.0
        r = _stream4(sb)
        assert r.status_code == 200, r.text
        assert r.json()["config_applied"] is False
        assert "LearningFrozen" in r.json()["message"]
        assert _cfg(sb)["versions"] == before["versions"]
        refused = _named(sb, "learning.freeze_refused_write")
        assert len(refused) == 1
        assert (refused[0]["details"]["target"], refused[0]["details"]["reason"]) == (
            "skill_config", "skill.config_oscillation")

        # refused through the optimizer route (SkillAdapter.run_optimizer_epoch): no epoch either
        emitter = boot_learning_registry(sb)
        epoch = _cfg(sb)["optimizer"]["epoch"]
        gate_clock[0] += 61.0
        r = sb.client.post("/v1/console/learning/feedback", headers=sb.csrf_headers,
                           json={"task_id": "task-f", "outcome_quality": "excellent", "would_repeat": True})
        assert r.status_code == 200, r.text
        hyps = r.json()["hypotheses"]
        assert hyps and all(h["optimizer_reason"] == "learning_frozen" and not h["accepted"] for h in hyps)
        assert _cfg(sb)["optimizer"]["epoch"] == epoch
        assert len(_named(sb, "learning.freeze_refused_write")) == 1 + len(hyps)
        emitter.stop(timeout=5.0)

        # lifting needs CSRF ...
        r = sb.client.post(UNFREEZE, params={"skill_id": ROUTER})
        assert r.status_code == 403, r.text
        # ... and cannot reach another tenant's brake
        from corvin_console import auth as _auth

        for sub in ("global/auth", "global/forge", "global/console/sessions"):
            (sb.home / "tenants" / "tenant_b" / sub).mkdir(parents=True, exist_ok=True)
        other = _auth.create_session(tenant_id="tenant_b", token_fingerprint="test-fp-b")
        sb.client.cookies.set("corvin_console_sid", other.sid)
        r = sb.client.post(UNFREEZE, params={"skill_id": ROUTER}, headers={
            "x-csrf-token": _auth.derive_csrf_token(other.csrf_secret, other.sid)})
        sb.client.cookies.set("corvin_console_sid", sb.sid)
        assert r.status_code in (403, 404), r.text
        assert _cfg(sb)["optimizer"]["frozen"] is True
        assert _named(sb, "learning.unfrozen") == []

        # the operator lifts it
        r = sb.client.post(UNFREEZE, params={"skill_id": ROUTER}, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"skill_id": ROUTER, "frozen": False, "previous_reason": "skill.config_oscillation"}
        unfrozen = _named(sb, "learning.unfrozen")
        assert len(unfrozen) == 1 and unfrozen[0]["details"]["skill_id"] == ROUTER
        assert _cfg(sb)["optimizer"]["frozen"] is False
        r = sb.client.post(UNFREEZE, params={"skill_id": ROUTER}, headers=sb.csrf_headers)
        assert r.status_code == 404  # not frozen any more

        # writing works again; the still-active anomaly does not re-freeze (the human decided)
        gate_clock[0] += 61.0
        r = _stream4(sb)
        assert r.status_code == 200 and r.json()["config_applied"] is True, r.text
        _tick(host)
        assert len(_named(sb, "learning.frozen")) == 1
        assert _cfg(sb)["optimizer"]["frozen"] is False
        _verify_chain(sb)


def test_a_freeze_whose_record_fails_changes_nothing_and_is_retried(tmp_path, monkeypatch):
    with console_client(tmp_path) as sb:
        _seed_oscillation(sb)
        from core.background.learning_host import LearningHost
        from core.deployment import audit_sink

        real_emit = audit_sink.emit

        def _failing(event_type, *a, **k):
            if event_type == "learning.frozen":
                raise audit_sink.AuditWriteFailed("simulated")
            return real_emit(event_type, *a, **k)

        monkeypatch.setattr(audit_sink, "emit", _failing)
        host = LearningHost(sb.tenant_id)
        _tick(host)
        assert len(_named(sb, "skill.config_oscillation")) == 1
        assert _named(sb, "learning.frozen") == []
        assert _cfg(sb)["optimizer"].get("frozen") is False
        monkeypatch.setattr(audit_sink, "emit", real_emit)
        _tick(host)
        assert len(_named(sb, "learning.frozen")) == 1
        assert _cfg(sb)["optimizer"]["frozen"] is True
        assert len(_named(sb, "skill.config_oscillation")) == 1  # the anomaly is not re-recorded
        _verify_chain(sb)


def _seed_confidence_regression(sb, skill_id: str) -> None:
    """20 executions six days ago at confidence 0.9, 20 in the last hour at 0.6."""
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent

    store = EventStore(sb.tenant_home, tenant_id=sb.tenant_id)
    now = datetime.now(timezone.utc)
    for when, conf in ((now - timedelta(days=3), 0.9), (now - timedelta(minutes=30), 0.6)):
        for i in range(20):
            ev = LearningEvent.create(
                event_type=EventType.SKILL_EXECUTED, skill_id=skill_id, tenant_id=sb.tenant_id,
                signal={"status": "success", "output": {"confidence": conf}},
                lom="tests/learning/test_adr_2304_learning_brake_e2e.py:_seed_confidence_regression")
            store.write_event(dataclasses.replace(
                ev, timestamp=(when + timedelta(seconds=i)).isoformat()))


def _dod_feedback(sb, gate_clock, check: str, verdict: str):
    gate_clock[0] += 61.0
    return sb.client.post("/v1/console/api/dod/feedback", headers=sb.csrf_headers, json={
        "task_id": "dod-1", "check_name": check, "feedback": verdict})


def test_dod_weight_oscillation_freezes_the_dod_weights(tmp_path, gate_clock):
    """The DoD weights are frozen from data production writes: four weight changes through
    ``POST /api/dod/feedback`` (each a chained ``skill_config_updated``) within the hour."""
    with console_client(tmp_path) as sb:
        r = sb.client.post("/v1/console/api/dod/verify", headers=sb.csrf_headers, json={
            "task_id": "dod-1", "task_type": "feature", "commit_msg": "fix: reproduce with pytest"})
        assert r.status_code == 200, r.text
        for check, verdict in (("reproducibility", "accurate"), ("audit_trail", "inaccurate"),
                               ("test_evidence", "accurate"), ("docs_sync", "inaccurate")):
            r = _dod_feedback(sb, gate_clock, check, verdict)
            assert r.status_code == 200 and r.json()["weights_changed"] is True, r.text
        weights = sb.tenant_home / "global" / "forge" / "dod_weights.json"
        stored = weights.read_text()
        from core.background.learning_host import LearningHost

        _tick(LearningHost(sb.tenant_id))
        assert [r["details"]["skill_id"] for r in _named(sb, "skill.config_oscillation")] == [DOD]
        [frozen] = _named(sb, "learning.frozen")
        assert (frozen["details"]["skill_id"], frozen["details"]["reason"]) == (DOD, "skill.config_oscillation")

        # the DoD feedback route refuses before anything is chained as a config change
        def _dod_updates():
            return [x for x in _chain(sb) if "skill_config_updated" in (x.get("event"), x.get("event_type"))
                    and (x.get("details") or {}).get("skill_id") == DOD]

        assert len(_dod_updates()) == 4
        r = _dod_feedback(sb, gate_clock, "reproducibility", "accurate")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error"] == "learning_frozen"
        assert weights.read_text() == stored
        assert len(_dod_updates()) == 4
        refused = _named(sb, "learning.freeze_refused_write")
        assert [(x["details"]["skill_id"], x["details"]["target"]) for x in refused] == [(DOD, "dod_weights")]

        # every other writer of the weight file is braked too (the persistence layer itself)
        from core.skills.os_skills.definition_of_done_verifier.weight_persistence import WeightPersistence
        from core.skills.os_skills.skill_adapter import LearningFrozen

        with pytest.raises(LearningFrozen):
            WeightPersistence(tenant_id=sb.tenant_id).save_weights({"feature": {"w_repro": 0.3}}, {})
        assert weights.read_text() == stored

        # lifted by the operator -> the rating is learned again
        r = sb.client.post(UNFREEZE, params={"skill_id": DOD}, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        r = _dod_feedback(sb, gate_clock, "reproducibility", "accurate")
        assert r.status_code == 200, r.text
        assert weights.read_text() != stored
        _verify_chain(sb)


def test_confidence_regression_freezes_a_skill_that_reports_confidence(tmp_path):
    """``output.confidence`` is the shape the router's executions carry. The DoD skill's
    production executions carry ``{score, passed}`` and never a confidence, so the regression
    does not apply to it (its brake is the chained-change oscillation above)."""
    with console_client(tmp_path) as sb:
        _seed_confidence_regression(sb, ROUTER)
        r = sb.client.post("/v1/console/api/dod/verify", headers=sb.csrf_headers, json={
            "task_id": "dod-1", "task_type": "feature", "commit_msg": "fix: reproduce with pytest"})
        assert r.status_code == 200, r.text
        from core.background.learning_host import LearningHost

        _tick(LearningHost(sb.tenant_id))
        assert [r["details"]["skill_id"] for r in _named(sb, "skill.confidence_regression")] == [ROUTER]
        [frozen] = _named(sb, "learning.frozen")
        assert (frozen["details"]["skill_id"], frozen["details"]["reason"]) == (
            ROUTER, "skill.confidence_regression")


def test_reporting_anomalies_do_not_freeze(tmp_path):
    """Operator chat answer D2 ("okay", 2026-10-26, to PLAN-0966): stall / latency / rejection rate only report."""
    from core.learning import anomaly_monitor

    assert anomaly_monitor.FREEZES_LEARNING == {"skill.config_oscillation", "skill.confidence_regression"}
    with console_client(tmp_path) as sb:
        from core.learning.event_store import EventStore
        from core.learning.learning_events import EventType, LearningEvent

        store = EventStore(sb.tenant_home, tenant_id=sb.tenant_id)
        for i, ok in enumerate([False, False, True, True, True] * 4):
            store.write_event(LearningEvent.create(
                event_type=EventType.OUTCOME, skill_id=ROUTER, tenant_id=sb.tenant_id,
                signal={"task_id": f"s-{i}", "status": "completed" if ok else "failed", "success": ok},
                lom="tests/learning/test_adr_2304_learning_brake_e2e.py:stall"))
        from core.background.learning_host import LearningHost

        _tick(LearningHost(sb.tenant_id))
        assert len(_named(sb, "skill.convergence_stall")) == 1
        assert _named(sb, "learning.frozen") == []
        assert not (sb.tenant_home / "skills" / "os_delegation_router_config.json").exists()


def test_the_brake_has_no_switch():
    """No flag, no environment variable, no setting reaches the brake path."""
    from core.background import learning_host
    from core.skills.os_skills import skill_adapter

    code = "".join(inspect.getsource(f) for f in (
        skill_adapter.freeze_learning, skill_adapter.refuse_if_frozen, skill_adapter._record_refusal,
        skill_adapter.SkillAdapter.freeze, skill_adapter.SkillAdapter.unfreeze,
        skill_adapter.SkillAdapter._refuse_frozen, learning_host.LearningHost._freeze,
        learning_host.LearningHost._check_anomalies))
    for needle in ("environ", "getenv", "feature_flag", "is_enabled", "flag"):
        assert needle not in code, needle
