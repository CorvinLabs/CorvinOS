"""Regressions for core.learning.feedback_collector / config_applier
(adversarial review 2026-09-27)."""

from __future__ import annotations

import asyncio
import json

import pytest

from core.learning import feedback_collector as fc
from core.learning.config_applier import ConfigApplier


def test_rated_feedback_is_accepted_not_typeerror():
    collector = fc.FeedbackCollector(tenant_id="_default")
    res = asyncio.run(collector.collect_feedback(
        skill_id="s", task_id="t", outcome_feedback="yes", quality_rating=4,
    ))
    assert res.accepted is True
    assert collector.get_all_feedback()[0]["quality_rating"] == 4


def test_reason_with_too_much_pii_rejects_submission():
    collector = fc.FeedbackCollector(tenant_id="_default")
    res = asyncio.run(collector.collect_feedback(
        skill_id="s", task_id="t", outcome_feedback="yes",
        reason="a@x.io b@x.io c@x.io d@x.io",
    ))
    assert res.accepted is False
    assert collector.get_all_feedback() == []


def test_get_feedback_collector_is_per_tenant(monkeypatch):
    monkeypatch.setattr(fc, "_feedback_collectors", {})
    a = fc.get_feedback_collector("tenant-a")
    b = fc.get_feedback_collector("tenant-b")
    assert a is not b
    assert (a.tenant_id, b.tenant_id) == ("tenant-a", "tenant-b")
    assert fc.get_feedback_collector("tenant-a") is a
    asyncio.run(b.collect_feedback(skill_id="s", task_id="t", outcome_feedback="no"))
    assert a.get_all_feedback() == []


def test_get_feedback_collector_rejects_bad_tenant(monkeypatch):
    monkeypatch.setattr(fc, "_feedback_collectors", {})
    with pytest.raises(ValueError):
        fc.get_feedback_collector("../escape")


@pytest.fixture
def applier(tmp_path):
    return ConfigApplier(corvin_home=str(tmp_path))


def test_new_parameter_is_clamped(applier):
    ok, _, _ = applier.apply_config_delta(skill_id="s", parameter_deltas={"threshold": -0.5})
    assert ok is True
    assert applier.get_config("s")["threshold"] == 0.0


def test_nan_delta_rejected(applier):
    ok, msg, _ = applier.apply_config_delta(skill_id="s", parameter_deltas={"t": float("nan")})
    assert ok is False and "numeric" in msg


@pytest.mark.parametrize("skill_id", ["../../escape", "a/b", "..", ""])
def test_skill_id_cannot_escape_tenant_dir(applier, tmp_path, skill_id):
    ok, msg, _ = applier.apply_config_delta(skill_id=skill_id, parameter_deltas={"t": 0.1})
    assert ok is False and "skill_id" in msg
    assert not (tmp_path / "escape").exists()


def test_bad_tenant_rejected(applier):
    ok, msg, _ = applier.apply_config_delta(
        skill_id="s", parameter_deltas={"t": 0.1}, tenant_id="../x")
    assert ok is False and "tenant_id" in msg


def test_config_is_tenant_scoped(applier):
    applier.apply_config_delta(skill_id="s", parameter_deltas={"t": 0.4}, tenant_id="tenant-a")
    assert applier.get_config("s", tenant_id="tenant-a") == {"t": 0.4}
    assert applier.get_config("s", tenant_id="tenant-b") == {}


def test_rollback_replays_with_clamping(applier, tmp_path):
    applier.apply_config_delta(skill_id="s", parameter_deltas={"t": 0.9})
    applier.apply_config_delta(skill_id="s", parameter_deltas={"t": 0.9})  # clamps to 1.0
    applier.apply_config_delta(skill_id="s", parameter_deltas={"t": -0.2})  # 0.8
    assert applier.rollback_config("s", version=2) is True
    assert applier.get_config("s")["t"] == 1.0  # not 1.8


def test_default_home_honours_corvin_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    applier = ConfigApplier()
    ok, _, _ = applier.apply_config_delta(skill_id="s", parameter_deltas={"t": 0.1})
    assert ok is True
    hist = tmp_path / "tenants" / "_default" / "skills" / "s" / "config_history.jsonl"
    assert json.loads(hist.read_text().splitlines()[0])["version"] == 1
