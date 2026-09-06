"""``SkillAdapter`` persistence + ``DelegationRouterSkill`` consuming the learned config.

Adversarial review 2026-09-06 (F7/F10): the adapter wrote to a bare ``~/.corvin``,
lost its version history on restart (every rollback failed), and nothing ever
read the config it produced. These tests pin the closure.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.skills.os_skills.feedback_loop import ConfigHypothesis
from core.skills.os_skills.skill_adapter import SkillAdapter, SkillConfig, load_skill_config
from core.skills.os_skills_phase1 import DelegationRouterSkill

TENANT = "_default"
SKILL = "os.delegation_router"


def _accept(adapter: SkillAdapter, param: str, delta: float) -> None:
    """Drive one accepted hypothesis (past baseline, clear improvement)."""
    adapter.state.epoch = 51
    adapter.state.baseline_success_rate = 0.0
    hyp = ConfigHypothesis(
        hypothesis_id=f"h-{param}", skill_id=SKILL, param=param, delta=delta,
        reason="test", confidence=0.9,
    )
    accepted, why = adapter.run_optimizer_epoch(hyp, recent_successes=10, recent_total=10)
    assert accepted, why


class TestPersistence:
    def test_default_work_dir_is_under_corvin_home(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
        adapter = SkillAdapter(SKILL, TENANT)
        assert adapter.work_dir == tmp_path / "home" / "tenants" / TENANT / "skills"
        assert str(Path.home()) not in str(adapter.work_dir) or str(tmp_path) in str(adapter.work_dir)

    def test_versions_survive_restart_and_rollback_works(self, tmp_path: Path):
        a = SkillAdapter(SKILL, TENANT, work_dir=tmp_path)
        _accept(a, "confidence_threshold", 0.05)
        _accept(a, "speed_weight", -0.10)
        assert [v.version_id for v in a.get_version_history()] == ["v1", "v2"]
        assert a.get_current_config().speed_weight == pytest.approx(0.40)

        # "restart": a fresh adapter on the same dir
        b = SkillAdapter(SKILL, TENANT, work_dir=tmp_path)
        assert [v.version_id for v in b.get_version_history()] == ["v1", "v2"]
        assert b.state.epoch == a.state.epoch  # baseline phase is not restarted
        cfg = b.rollback("v1")
        assert cfg.confidence_threshold == pytest.approx(0.75)
        assert cfg.speed_weight == pytest.approx(0.50)  # v1 predates the speed change
        with pytest.raises(ValueError):
            b.rollback("v7")

    def test_change_announcements_carry_content_free_payload(self, tmp_path: Path):
        seen: list[dict] = []
        a = SkillAdapter(SKILL, TENANT, work_dir=tmp_path, on_config_change=seen.append)
        _accept(a, "confidence_threshold", 0.05)
        a.rollback("v1")
        assert [c["change"] for c in seen] == ["hypothesis_accepted", "rollback"]
        assert seen[0]["param"] == "confidence_threshold" and seen[0]["version_id"] == "v1"
        assert set(seen[0]["config"]) == set(SkillConfig().to_dict())

    def test_unknown_param_is_refused(self):
        with pytest.raises(ValueError):
            SkillConfig().apply_delta("not_a_param", 0.1)


class TestRouterConsumesLearnedConfig:
    def test_default_config_routes_exactly_as_before(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        out = DelegationRouterSkill().execute({"complexity": 3, "task_type": "chat", "tenant_id": TENANT})
        assert out["engine"] == "claude-haiku-4"
        assert out["confidence_threshold"] == pytest.approx(0.70)
        assert out["learned_config_version"] is None

    def test_learned_threshold_escalates_low_confidence_decisions(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        a = SkillAdapter(SKILL, TENANT)  # default work dir under CORVIN_HOME
        # push the threshold above the "code < 7" branch's 0.80 confidence
        _accept(a, "confidence_threshold", 0.05)
        _accept(a, "confidence_threshold", 0.05)
        _accept(a, "confidence_threshold", 0.05)
        cfg, version = load_skill_config(SKILL, TENANT)
        assert cfg.confidence_threshold == pytest.approx(0.85) and version == "v3"

        out = DelegationRouterSkill().execute({"complexity": 4, "task_type": "code", "tenant_id": TENANT})
        assert out["engine"] == "claude-opus-5"  # sonnet (0.80 < 0.85) escalated one tier
        assert "escalated" in out["reasoning"] and "v3" in out["reasoning"]
        assert out["learned_config_version"] == "v3"

        # without a tenant the router cannot know a config and stays heuristic
        out = DelegationRouterSkill().execute({"complexity": 4, "task_type": "code"})
        assert out["engine"] == "claude-sonnet-4" and "confidence_threshold" not in out

    def test_shadow_input_is_echoed(self):
        out = DelegationRouterSkill().execute(
            {"complexity": 8, "task_type": "big_data", "shadow": True, "bundled_engine": "acs"}
        )
        assert out["shadow"] is True and out["bundled_engine"] == "acs"
        assert out["engine"] == "claude-opus-5"

    def test_unreadable_config_never_breaks_routing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        skills = tmp_path / "tenants" / TENANT / "skills"
        skills.mkdir(parents=True)
        (skills / "os_delegation_router_config.json").write_text("{not json")
        out = DelegationRouterSkill().execute({"complexity": 9, "task_type": "chat", "tenant_id": TENANT})
        assert out["engine"] == "claude-opus-5"
        assert out["confidence_threshold"] == pytest.approx(0.70)  # default, not a crash
