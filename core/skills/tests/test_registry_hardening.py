"""Registry hardening — adversarial review 2026-09-07 (F-K2 / F-K3).

* LoM is REQUIRED: an execution without one is refused (audited error result,
  the Skill never runs) — like a tenant violation.
* ``lom_hash`` binds ``file:function`` to that function's source.
* The chain record carries an allowlisted ``decision`` — proven through the
  REAL core audit writer (``audit.audit_event`` → ``forge.security_events``
  floor), not a mock: the floor used to strip ``output`` and leave nothing.
* ``tier=compliance`` (``os.capabilities``): ``unregister`` / ``disable_skill``
  raise ``SkillDisableRefused``; three failures do NOT auto-disable; every
  refusal is a ``skill.disable.refused`` chain record.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from core.skills.os_skills_phase1 import CapabilitiesSkill, register_builtin_skills
from core.skills.skill_registry_phase1 import (
    CoreAuditBackend,
    Skill,
    SkillDisableRefused,
    SkillMetadata,
    SkillOrigin,
    SkillsRegistry,
    SkillTier,
    decision_summary,
)

LOM = "core/skills/tests/test_registry_hardening.py:_chain"


def _chain() -> list[dict]:
    path = Path(os.environ["VOICE_AUDIT_PATH"])
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class _Boom(Skill):
    def __init__(self, tier: SkillTier):
        super().__init__(SkillMetadata(
            id=f"test.boom.{tier.value}", name="boom", description="raises", version="0.0.1",
            origin=SkillOrigin.BUILTIN, owner="tests", tier=tier,
        ))

    def execute(self, input):  # noqa: A002
        raise RuntimeError("boom")


@pytest.fixture
def registry() -> SkillsRegistry:
    reg = SkillsRegistry(CoreAuditBackend("_default"))
    register_builtin_skills(reg)
    return reg


class TestLomRequired:
    def test_missing_lom_is_refused_and_audited(self, registry):
        for lom in (None, "", "   "):
            r = registry.execute("os.capabilities", {"tenant_id": "_default", "gated_flags": ["x"]}, lom=lom)
            assert r.status == "error"
            assert "LoM missing" in r.error_message
            assert r.output is None  # the Skill did not run
        recs = [c for c in _chain() if c["event_type"] == "skill.executed"]
        assert len(recs) == 3
        assert all(c["details"]["status"] == "error" for c in recs)
        assert all("LoM missing" in c["details"]["error_message"] for c in recs)
        # a refused execution does not count as a Skill failure
        assert registry.is_enabled("os.capabilities")

    def test_lom_hash_binds_to_the_named_function_source(self, registry):
        r = registry.execute("os.capabilities", {"tenant_id": "_default", "gated_flags": ["x"]}, lom=LOM)
        assert r.status == "success"
        import ast
        text = Path(__file__).read_text()
        node = next(n for n in ast.walk(ast.parse(text))
                    if isinstance(n, ast.FunctionDef) and n.name == "_chain")
        expected = hashlib.sha256(ast.get_source_segment(text, node).encode()).hexdigest()
        assert r.lom_hash == expected
        # unknown function / outside repo → UNRESOLVABLE (None): never a foreign
        # file, and never a label hash that is indistinguishable from a real
        # source hash (round-2 review, R2-B1). execute() refuses such a LoM.
        label = "../../etc/passwd:root"
        assert registry._compute_lom_hash(label) is None
        refused = registry.execute("os.headless_mode", {"headless_enabled": False}, lom=label)
        assert refused.status == "error" and "LoM unresolvable" in (refused.error_message or "")


class TestDecisionReachesTheChain:
    def test_capabilities_decision_survives_the_core_writer_floor(self, registry):
        r = registry.execute(
            "os.capabilities", {"tenant_id": "_default", "gated_flags": ["a", "b", "c"]}, lom=LOM,
        )
        assert r.status == "success"
        rec = [c for c in _chain() if c["event_type"] == "skill.executed"][-1]["details"]
        assert rec["lom"] == LOM
        assert rec["lom_hash"] == r.lom_hash
        assert rec["skill_id"] == "os.capabilities"
        assert rec["decision"]["flag_count"] == 3
        assert rec["decision"]["flags_on"] == 0
        assert len(rec["decision"]["flags_hash"]) == 16
        assert "output" not in rec and "flags" not in rec["decision"]  # counts + hash, never the map
        assert "_dropped_fields" not in rec, rec

    def test_router_decision_is_content_free(self, registry):
        r = registry.execute(
            "os.delegation_router",
            {"complexity": 9, "task_type": "analysis", "tenant_id": "_default",
             "shadow": True, "bundled_engine": "native",
             "user_context": {"note": "my email is a@b.de"}},
            lom=LOM,
        )
        rec = [c for c in _chain() if c["event_type"] == "skill.executed"][-1]["details"]
        assert rec["decision"] == {
            "engine": r.output["engine"], "bundled_engine": "native", "shadow": True,
            "confidence": r.output["confidence"], "confidence_threshold": 0.7,
        } or rec["decision"]["engine"] == r.output["engine"]
        assert "reasoning" not in rec["decision"]
        assert "a@b.de" not in json.dumps(rec)

    def test_decision_summary_never_copies_free_text(self):
        out = {"engine": "x" * 200, "reasoning": "secret text", "mode": "headless",
               "flags": {"b": True, "a": False}, "merged_tier": {"engine": "claude-sonnet-4", "priority": 7},
               "injected_tier": None}
        d = decision_summary(out)
        assert "reasoning" not in d and "x" * 200 not in json.dumps(d)
        assert d["mode"] == "headless" and d["flag_count"] == 2 and d["flags_on"] == 1
        assert d["engine"] == "claude-sonnet-4" and d["priority"] == 7 and d["injected_tier"] is False
        assert decision_summary(None) is None
        assert decision_summary("text") == {"kind": "str"}


class TestComplianceTier:
    def test_capabilities_is_declared_compliance(self):
        assert CapabilitiesSkill().metadata.tier is SkillTier.COMPLIANCE

    def test_unregister_and_disable_are_refused_and_audited(self, registry):
        with pytest.raises(SkillDisableRefused):
            registry.unregister("os.capabilities")
        with pytest.raises(SkillDisableRefused):
            registry.disable_skill("os.capabilities")
        assert registry.get("os.capabilities") is not None
        assert registry.is_enabled("os.capabilities")
        refused = [c for c in _chain() if c["event_type"] == "skill.disable.refused"]
        assert [c["details"]["operation"] for c in refused] == ["unregister", "disable"]
        assert all(c["details"]["tier"] == "compliance" for c in refused)
        assert all(c["details"]["skill_id"] == "os.capabilities" for c in refused)
        # a core-tier Skill keeps its off switches
        assert registry.disable_skill("os.plugin_builder") is True
        assert not registry.is_enabled("os.plugin_builder")
        registry.unregister("os.plugin_builder")
        assert registry.get("os.plugin_builder") is None

    def test_three_failures_do_not_auto_disable_a_compliance_skill(self):
        reg = SkillsRegistry(CoreAuditBackend("_default"))
        reg.register(_Boom(SkillTier.COMPLIANCE))
        reg.register(_Boom(SkillTier.CORE))
        for _ in range(4):
            reg.execute("test.boom.compliance", {}, lom=LOM)
            reg.execute("test.boom.core", {}, lom=LOM)
        assert reg.is_enabled("test.boom.compliance")
        assert not reg.is_enabled("test.boom.core")
        # the fourth call still REACHED the compliance Skill (error from the Skill, not "auto-disabled")
        r = reg.execute("test.boom.compliance", {}, lom=LOM)
        assert r.error_message == "boom"
        types = [c["event_type"] for c in _chain()]
        assert types.count("skill.disable.refused") == 1
        assert types.count("skill.auto.disabled") == 1
        refused = next(c for c in _chain() if c["event_type"] == "skill.disable.refused")["details"]
        assert refused["operation"] == "auto_disable" and refused["failures"] == 3
