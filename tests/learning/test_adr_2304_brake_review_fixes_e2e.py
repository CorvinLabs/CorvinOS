"""ADR-2304 learning brake: regressions for the c120 review findings (s2-1..5, s4-1/4/5, s5-1).

Every test drives a real entry point: the console routes (``POST /api/dod/verify``,
``POST /api/dod/feedback``, ``POST /feedback/skill``, ``POST /learning/config/unfreeze``) and
``LearningHost.tick``. Seeds go through production writers only; faults are injected only at
the file-system boundary (``os.replace``) or by a concurrent freeze in another thread.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from tests.learning.console_client import console_client
from tests.learning.test_adr_2304_learning_brake_e2e import (  # noqa: F401 — gate_clock is a fixture
    DOD, ROUTER, UNFREEZE, _cfg, _chain, _named, _seed_execution, _seed_oscillation, _stream4,
    _tick, _verify_chain, gate_clock,
)

WEIGHTS = ("global", "forge", "dod_weights.json")


def _verify(sb, task_id="dod-1"):
    r = sb.client.post("/v1/console/api/dod/verify", headers=sb.csrf_headers, json={
        "task_id": task_id, "task_type": "feature", "commit_msg": "fix: reproduce with pytest"})
    assert r.status_code == 200, r.text


def _feedback(sb, gate_clock, check="reproducibility", verdict="accurate", task_id="dod-1"):
    gate_clock[0] += 61.0
    return sb.client.post("/v1/console/api/dod/feedback", headers=sb.csrf_headers, json={
        "task_id": task_id, "check_name": check, "feedback": verdict})


def _dod_updates(sb):
    return [x for x in _chain(sb) if (x.get("event") or x.get("event_type")) == "skill_config_updated"
            and (x.get("details") or {}).get("skill_id") == DOD]


def _seed_dod_changes(sb, gate_clock, n=4):
    """n stored DoD weight changes through the production route (each chains one record)."""
    _verify(sb)
    checks = ("reproducibility", "audit_trail", "test_evidence", "docs_sync", "reachability")
    for i in range(n):
        r = _feedback(sb, gate_clock, check=checks[i % len(checks)],
                      verdict="accurate" if i % 2 == 0 else "inaccurate")
        assert r.status_code == 200, r.text
        assert r.json()["weights_changed"] is True, r.json()


# s2-1 / s4-1 / s5-1: the DoD weights can be frozen in production
def test_dod_weight_oscillation_freezes_the_dod_weights(tmp_path, gate_clock):
    with console_client(tmp_path) as sb:
        _seed_dod_changes(sb, gate_clock, n=4)
        assert len(_dod_updates(sb)) == 4
        weights = sb.tenant_home.joinpath(*WEIGHTS)
        stored = weights.read_text()

        from core.background.learning_host import LearningHost

        host = LearningHost(sb.tenant_id)
        _tick(host)
        osc = [r for r in _named(sb, "skill.config_oscillation") if r["details"]["skill_id"] == DOD]
        assert len(osc) == 1, osc
        assert (osc[0]["details"]["param_name"], osc[0]["details"]["change_count"]) == ("dod_weights", 4)
        [frozen] = _named(sb, "learning.frozen")
        assert (frozen["details"]["skill_id"], frozen["details"]["reason"]) == (DOD, "skill.config_oscillation")
        assert frozen["details"]["attempt"] == 1

        # the next rating is refused before anything is chained as a config change
        r = _feedback(sb, gate_clock)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error"] == "learning_frozen"
        assert len(_dod_updates(sb)) == 4
        assert weights.read_text() == stored
        refused = _named(sb, "learning.freeze_refused_write")
        assert [(x["details"]["skill_id"], x["details"]["target"]) for x in refused] == [(DOD, "dod_weights")]

        # edge-triggered: no second freeze while the anomaly stays active
        _tick(host)
        assert len(_named(sb, "learning.frozen")) == 1

        r = sb.client.post(UNFREEZE, params={"skill_id": DOD}, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        r = _feedback(sb, gate_clock)
        assert r.status_code == 200, r.text
        _verify_chain(sb)


def test_three_dod_changes_do_not_freeze(tmp_path, gate_clock):
    with console_client(tmp_path) as sb:
        _seed_dod_changes(sb, gate_clock, n=3)
        from core.background.learning_host import LearningHost

        _tick(LearningHost(sb.tenant_id))
        assert _named(sb, "skill.config_oscillation") == []
        assert _named(sb, "learning.frozen") == []


# s2-2: a freeze landing while the route stores a change cannot split check and write
def test_a_concurrent_freeze_waits_for_the_weight_write(tmp_path, gate_clock, monkeypatch):
    with console_client(tmp_path) as sb:
        _verify(sb)
        from corvin_console.routes import dod_verifier_dashboard as dod
        from core.skills.os_skills.skill_adapter import freeze_learning

        real = dod.emit_skill_config_updated_event
        result = {}

        def _host():
            result["frozen"] = freeze_learning(DOD, sb.tenant_id, reason="skill.config_oscillation")

        def _emit_while_host_freezes(**kw):
            t = threading.Thread(target=_host)
            t.start()
            result["thread"] = t
            t.join(0.3)          # the host is now blocked on the brake lock
            return real(**kw)

        monkeypatch.setattr(dod, "emit_skill_config_updated_event", _emit_while_host_freezes)
        r = _feedback(sb, gate_clock)
        result["thread"].join(10)
        assert r.status_code == 200, r.text
        assert result.get("frozen") is True
        weights = sb.tenant_home.joinpath(*WEIGHTS)
        assert weights.exists()                                  # the chained change was stored
        names = [x.get("event") or x.get("event_type") for x in _chain(sb)]
        assert names.index("skill_config_updated") < names.index("learning.frozen")
        assert _named(sb, "learning.freeze_refused_write") == []

        monkeypatch.setattr(dod, "emit_skill_config_updated_event", real)
        stored = weights.read_text()
        r = _feedback(sb, gate_clock, check="audit_trail")
        assert r.status_code == 409, r.text                      # never a 500
        assert len(_dod_updates(sb)) == 1 and weights.read_text() == stored
        _verify_chain(sb)


def _failing_config_replace(monkeypatch, fail):
    real = os.replace

    def _replace(src, dst, *a, **k):
        if fail[0] and str(dst).endswith("_config.json"):
            fail[0] -= 1
            raise OSError(28, "No space left on device")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", _replace)


# s2-3: record committed, state write failed -> compensated on the chain, retried as attempt 2
def test_freeze_record_without_state_is_compensated_and_retried(tmp_path, gate_clock, monkeypatch):
    with console_client(tmp_path) as sb:
        _seed_oscillation(sb)
        from core.background.learning_host import LearningHost

        host = LearningHost(sb.tenant_id)
        fail = [1]
        _failing_config_replace(monkeypatch, fail)
        _tick(host)
        assert fail[0] == 0
        assert _cfg(sb)["optimizer"]["frozen"] is False
        seq = [(r.get("event") or r.get("event_type"), r["details"].get("attempt")) for r in _chain(sb)
               if (r.get("event") or r.get("event_type", "")).startswith("learning.f")]
        assert seq == [("learning.frozen", 1), ("learning.freeze_not_applied", 1)], seq
        entry = next(v for v in host.anomalies.values() if v["event"] == "skill.config_oscillation")
        assert "freeze" not in entry and entry["freeze_attempts"] == 1

        _tick(host)
        assert _cfg(sb)["optimizer"]["frozen"] is True
        assert [r["details"]["attempt"] for r in _named(sb, "learning.frozen")] == [1, 2]
        _seed_execution(sb, ROUTER)
        gate_clock[0] += 61.0
        assert _stream4(sb).json()["config_applied"] is False
        _verify_chain(sb)


def test_state_dir_unwritable_records_nothing_and_retries(tmp_path):
    with console_client(tmp_path) as sb:
        _seed_oscillation(sb)
        from core.background.learning_host import LearningHost

        host = LearningHost(sb.tenant_id)
        skills = sb.tenant_home / "skills"
        os.chmod(skills, 0o500)
        try:
            _tick(host)
        finally:
            os.chmod(skills, 0o700)
        assert _named(sb, "learning.frozen") == [] and _cfg(sb)["optimizer"]["frozen"] is False
        _tick(host)
        assert len(_named(sb, "learning.frozen")) == 1 and _cfg(sb)["optimizer"]["frozen"] is True


def test_unfreeze_state_write_failure_is_a_clean_503(tmp_path, monkeypatch):
    with console_client(tmp_path) as sb:
        _seed_oscillation(sb)
        from core.background.learning_host import LearningHost

        _tick(LearningHost(sb.tenant_id))
        fail = [1]
        _failing_config_replace(monkeypatch, fail)
        r = sb.client.post(UNFREEZE, params={"skill_id": ROUTER}, headers=sb.csrf_headers)
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == {"error": "state_not_written"}
        assert len(_named(sb, "learning.unfrozen")) == 1
        assert len(_named(sb, "learning.unfreeze_not_applied")) == 1
        assert _cfg(sb)["optimizer"]["frozen"] is True

        skills = sb.tenant_home / "skills"
        os.chmod(skills, 0o500)          # temp file cannot be written: nothing recorded
        try:
            r = sb.client.post(UNFREEZE, params={"skill_id": ROUTER}, headers=sb.csrf_headers)
        finally:
            os.chmod(skills, 0o700)
        assert r.status_code == 503 and r.json()["detail"] == {"error": "state_not_written"}
        assert len(_named(sb, "learning.unfrozen")) == 1

        r = sb.client.post(UNFREEZE, params={"skill_id": ROUTER}, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        assert _cfg(sb)["optimizer"]["frozen"] is False
        _verify_chain(sb)


# s2-4: an alias of the stored id is refused, the file's id is never rewritten
def test_alias_id_cannot_lift_the_brake(tmp_path):
    with console_client(tmp_path) as sb:
        _seed_oscillation(sb)
        from core.background.learning_host import LearningHost

        _tick(LearningHost(sb.tenant_id))
        r = sb.client.post(UNFREEZE, params={"skill_id": "os_delegation_router"}, headers=sb.csrf_headers)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error"] == "skill_id_alias"
        assert r.json()["detail"]["canonical_skill_id"] == ROUTER
        cfg = _cfg(sb)
        assert cfg["skill_id"] == ROUTER and cfg["optimizer"]["frozen"] is True
        assert _named(sb, "learning.unfrozen") == []

        from core.skills.os_skills.skill_adapter import SkillAdapter, SkillIdAlias

        with pytest.raises(SkillIdAlias):
            SkillAdapter("os_delegation_router", sb.tenant_id).apply_config_delta(
                {"confidence_threshold": 0.01}, 0.0)
        assert _cfg(sb)["skill_id"] == ROUTER


# s2-5 / s4-4: a malformed number is not an invalid id; retried, recorded once, fail closed
def test_malformed_number_is_retried_not_marked_invalid(tmp_path, gate_clock):
    with console_client(tmp_path) as sb:
        _seed_oscillation(sb)
        p = sb.tenant_home / "skills" / "os_delegation_router_config.json"
        data = json.loads(p.read_text())
        good = data["optimizer"]["epoch"]
        data["optimizer"]["epoch"] = "x"
        p.write_text(json.dumps(data))
        from core.background.learning_host import LearningHost
        from core.skills.os_skills.skill_adapter import LearningFrozen, refuse_if_frozen

        host = LearningHost(sb.tenant_id)
        _tick(host)
        _tick(host)
        entry = next(v for v in host.anomalies.values() if v["event"] == "skill.config_oscillation")
        assert "freeze" not in entry and entry["freeze_failed"] == "config_unreadable"
        failed = _named(sb, "learning.freeze_failed")
        assert len(failed) == 1
        assert (failed[0]["details"]["cause"], failed[0]["details"]["reason"]) == (
            "config_unreadable", "skill.config_oscillation")
        with pytest.raises(LearningFrozen):          # fail closed while it does not parse
            refuse_if_frozen(ROUTER, sb.tenant_id, target="skill_config")

        data["optimizer"]["epoch"] = good
        p.write_text(json.dumps(data))
        _tick(host)
        assert [r["details"]["attempt"] for r in _named(sb, "learning.frozen")] == [3]
        _seed_execution(sb, ROUTER)
        gate_clock[0] += 61.0
        assert _stream4(sb).json()["config_applied"] is False
        _verify_chain(sb)


# s4-5: file-sourced reason / version id reach the chain and the API in closed shapes only
def test_file_sourced_reason_and_version_are_closed(tmp_path, gate_clock):
    with console_client(tmp_path) as sb:
        _verify(sb)
        skills = sb.tenant_home / "skills"
        skills.mkdir(parents=True, exist_ok=True)
        (skills / "os_definition_of_done_verifier_config.json").write_text(json.dumps({
            "skill_id": DOD, "tenant_id": sb.tenant_id, "config": {},
            "active_version": "v-Max Mustermann wohnt in Berlin",
            "optimizer": {"frozen": True, "frozen_reason": "Max Mustermann wohnt in Berlin"},
            "versions": []}))
        r = _feedback(sb, gate_clock)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["reason"] == "unknown"
        [ref] = _named(sb, "learning.freeze_refused_write")
        assert (ref["details"]["reason"], ref["details"]["version_id"]) == ("unknown", "unparsed")
        r = sb.client.post(UNFREEZE, params={"skill_id": DOD}, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        assert r.json()["previous_reason"] == "unknown"
        [un] = _named(sb, "learning.unfrozen")
        assert (un["details"]["reason"], un["details"]["version_id"]) == ("unknown", "unparsed")
        assert "Mustermann" not in json.dumps(_chain(sb))
        _verify_chain(sb)
