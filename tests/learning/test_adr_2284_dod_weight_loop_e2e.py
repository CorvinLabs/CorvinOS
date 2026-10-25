"""ADR-2284 decision 1 through the real console router: verify -> feedback x N -> verify.

* ``POST /v1/console/api/dod/verify`` records the verification's task type with its OUTCOME
  (and the execution the ADR-0534 reality check needs);
* ``POST /v1/console/api/dod/feedback`` passes the ADR-0534 gate (``EventStore.write_event``),
  looks the task type up by ``task_id`` (unknown -> 422), feeds ``DoD_WeightOptimizer`` and
  writes ONE chained ``skill_config_updated`` per change before persisting it per tenant;
* the verifier applies a learned weight only at confidence > 0.5 (ADR-0723), clamped to
  [0.05, 0.6] and summing to 1.

The gate's throttle (5 signals / 60 s per skill) is real; the test advances the GATE's clock by
61 s per rating instead of sleeping. Nothing else is faked.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from tests.learning.console_client import console_client

VERIFY = "/v1/console/api/dod/verify"
FEEDBACK = "/v1/console/api/dod/feedback"
SKILL = "os.definition_of_done_verifier"
DEFAULTS = {"w_reach": 0.20, "w_audit": 0.25, "w_test": 0.20, "w_docs": 0.20, "w_repro": 0.15}


@pytest.fixture
def gate_clock(monkeypatch):
    from core.learning import feedback_gate

    offset = [0.0]
    monkeypatch.setattr(feedback_gate, "time", SimpleNamespace(time=lambda: time.time() + offset[0]))
    return offset


def _tenant_chain(sb) -> list[dict]:
    chain = sb.tenant_home / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    return [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]


def _config_updates(sb) -> list[dict]:
    out = []
    for rec in _tenant_chain(sb):
        if "skill_config_updated" not in (rec.get("event"), rec.get("event_type")):
            continue
        details = rec.get("details") if isinstance(rec.get("details"), dict) else rec
        if details.get("skill_id") == SKILL:
            out.append(rec)
    return out


def _details(rec: dict) -> dict:
    return rec.get("details") if isinstance(rec.get("details"), dict) else rec


def _verify(sb, task_id: str, task_type: str = "feature") -> dict:
    r = sb.client.post(VERIFY, headers=sb.csrf_headers, json={
        "task_id": task_id, "task_type": task_type, "commit_msg": "fix: reproduce with pytest"})
    assert r.status_code == 200, r.text
    return r.json()


def _rate(sb, gate_clock, task_id: str, verdict: str, check: str = "reproducibility"):
    gate_clock[0] += 61.0
    return sb.client.post(FEEDBACK, headers=sb.csrf_headers, json={
        "task_id": task_id, "check_name": check, "feedback": verdict, "note": "free text, never stored"})


def test_feedback_moves_weights_only_past_the_confidence_gate(tmp_path, gate_clock):
    with console_client(tmp_path) as sb:
        first = _verify(sb, "dod-loop-1")
        assert first["weights"] == pytest.approx(DEFAULTS)
        assert first["weights_source"] == "default"

        # five accepted ratings: confidence of w_repro reaches 0.5, not > 0.5 -> still the defaults
        for i in range(5):
            r = _rate(sb, gate_clock, "dod-loop-1", "accurate")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["task_type"] == "feature"
            assert body["confidence"]["w_repro"] == pytest.approx(0.1 * (i + 1))
            assert body["applied_weights"] == pytest.approx(DEFAULTS)
        assert len(_config_updates(sb)) == 5
        assert _verify(sb, "dod-loop-2")["weights"] == pytest.approx(DEFAULTS)

        # the sixth crosses the gate (0.6 > 0.5): the next verification scores with it
        r = _rate(sb, gate_clock, "dod-loop-1", "accurate")
        assert r.status_code == 200, r.text
        learned = _verify(sb, "dod-loop-3")
        assert learned["weights_source"] == "learned"
        w = learned["weights"]
        assert w != pytest.approx(DEFAULTS)
        assert w["w_repro"] > DEFAULTS["w_repro"]
        assert sum(w.values()) == pytest.approx(1.0)
        assert all(0.05 - 1e-9 <= v <= 0.6 + 1e-9 for v in w.values())

        # one chained record per change, metadata only
        updates = _config_updates(sb)
        assert len(updates) == 6
        for rec in updates:
            d = _details(rec)
            assert d["task_type"] == "feature"
            assert set(d["old_weights"]) == set(DEFAULTS) == set(d["new_weights"])
            assert d["old_weights"] != d["new_weights"]
            assert "w_repro" in d["confidence"]
            assert rec.get("hash") and "prev_hash" in rec
        assert "free text" not in (sb.tenant_home / "global" / "forge" / "audit.jsonl").read_text()
        # the records are consecutive links of the tenant chain
        chain = _tenant_chain(sb)
        by_hash = {r.get("hash"): i for i, r in enumerate(chain)}
        for rec in updates:
            assert by_hash[rec["prev_hash"]] == by_hash[rec["hash"]] - 1

        # persisted per tenant with its confidence: a fresh instance (a restart) sees the gate open
        from core.skills.os_skills.definition_of_done_verifier.weight_persistence import (
            WeightPersistence, weights_path)
        path = weights_path(sb.tenant_id)
        assert path == sb.tenant_home / "global" / "forge" / "dod_weights.json"
        assert WeightPersistence(tenant_id=sb.tenant_id).load_confidence()["feature"]["w_repro"] == \
            pytest.approx(0.6)


def test_unknown_task_unlearnable_type_and_unknown_check_are_refused(tmp_path, gate_clock):
    with console_client(tmp_path) as sb:
        r = _rate(sb, gate_clock, "never-verified", "inaccurate")
        assert r.status_code == 422, r.text

        _verify(sb, "dod-odd-type", task_type="something_else")
        r = _rate(sb, gate_clock, "dod-odd-type", "inaccurate")
        assert r.status_code == 422, r.text

        _verify(sb, "dod-ok")
        r = _rate(sb, gate_clock, "dod-ok", "inaccurate", check="test_coverage")
        assert r.status_code == 422, r.text
        assert _config_updates(sb) == []

        r = _rate(sb, gate_clock, "dod-ok", "inaccurate")
        assert r.status_code == 200, r.text
        assert len(_config_updates(sb)) == 1
        assert r.json()["confidence"]["w_repro"] == pytest.approx(0.1)


def test_the_gate_throttle_refuses_a_burst_and_nothing_is_learned(tmp_path, gate_clock):
    with console_client(tmp_path) as sb:
        _verify(sb, "dod-burst")
        codes = []
        for _ in range(7):
            codes.append(sb.client.post(FEEDBACK, headers=sb.csrf_headers, json={
                "task_id": "dod-burst", "check_name": "audit_trail", "feedback": "accurate"}).status_code)
        assert codes[:5] == [200] * 5 and codes[5:] == [429, 429], codes
        assert len(_config_updates(sb)) == 5


# ── optimizer invariants the route relies on (REVIEW-0057) ───────────────────────────────

def test_repeated_extreme_feedback_stays_in_the_box_and_sums_to_one():
    from core.skills.os_skills.definition_of_done_verifier.weight_optimizer import DoD_WeightOptimizer

    for delta in (0.99, -0.99):
        opt = DoD_WeightOptimizer()
        for _ in range(50):
            opt.observe_feedback("feature", delta, ["audit_trail"])
        for weights in (opt.weights["feature"], opt.recommend_next_weights("feature")):
            assert sum(weights.values()) == pytest.approx(1.0)
            assert all(0.05 - 1e-9 <= v <= 0.6 + 1e-9 for v in weights.values()), weights


def test_unknown_task_type_is_refused_not_folded_into_another_bucket():
    from core.skills.os_skills.definition_of_done_verifier.weight_optimizer import (
        DoD_WeightOptimizer, UnknownTaskType)

    opt = DoD_WeightOptimizer()
    with pytest.raises(UnknownTaskType):
        opt.observe_feedback("something_else", 0.2, ["audit_trail"])
    assert opt.weights["cli_command"] == DoD_WeightOptimizer.DEFAULT_WEIGHTS
    assert opt.recommend_next_weights("something_else") == DoD_WeightOptimizer.DEFAULT_WEIGHTS


def test_the_confidence_gate_is_strictly_above_one_half_and_survives_a_restart(tmp_path):
    from core.skills.os_skills.definition_of_done_verifier.weight_optimizer import DoD_WeightOptimizer
    from core.skills.os_skills.definition_of_done_verifier.weight_persistence import WeightPersistence

    opt = DoD_WeightOptimizer()
    for _ in range(5):
        opt.observe_feedback("plugin", -0.2, ["docs_sync"])
    assert opt.recommend_next_weights("plugin") == DoD_WeightOptimizer.DEFAULT_WEIGHTS
    opt.observe_feedback("plugin", -0.2, ["docs_sync"])
    learned = opt.recommend_next_weights("plugin")
    assert learned["w_docs"] > DoD_WeightOptimizer.DEFAULT_WEIGHTS["w_docs"]

    store = WeightPersistence(storage_path=tmp_path / "w.json")
    assert store.save_weights(opt.weights, opt.confidence, opt.sample_count)
    fresh = DoD_WeightOptimizer.from_state(WeightPersistence(storage_path=tmp_path / "w.json").load_state())
    assert fresh.recommend_next_weights("plugin") == pytest.approx(learned)
    assert fresh.sample_count["plugin"] == 6
