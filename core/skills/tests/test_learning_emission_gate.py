"""Skills opt OUT of learning emission (``SkillMetadata.learn``), never out of audit.

Adversarial review 2026-09-06 (F31): ``os.capabilities`` executed 346× in ten
minutes of ordinary console polling; every execution produced an audit record
AND a learning event — doubling chain growth and filling the learning store
with events no optimizer consumes. Deterministic flag/manifest lookups are
audited (compliance) and not learned from.
"""
from __future__ import annotations

from typing import Any, Dict

from core.skills.os_skills_phase1 import (
    CapabilitiesSkill,
    DelegationRouterSkill,
    HeadlessModeSkill,
    PluginHealthMonitoringSkill,
    register_builtin_skills,
)
from core.skills.skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin, SkillsRegistry


class _Audit:
    def __init__(self):
        self.events: list[dict] = []

    def write_event(self, event: Dict[str, Any]) -> None:
        self.events.append(event)


class _Learning:
    def __init__(self):
        self.events: list[dict] = []

    def emit_event(self, event: Dict[str, Any]) -> bool:
        self.events.append(event)
        return True


def _registry() -> tuple[SkillsRegistry, _Audit, _Learning]:
    audit, learning = _Audit(), _Learning()
    reg = SkillsRegistry(audit_backend=audit, tenant_id="_default", learning_backend=learning)
    register_builtin_skills(reg)
    return reg, audit, learning


def test_lookup_skills_declare_learn_false():
    assert CapabilitiesSkill().metadata.learn is False
    assert HeadlessModeSkill().metadata.learn is False
    assert PluginHealthMonitoringSkill().metadata.learn is False
    assert DelegationRouterSkill().metadata.learn is True  # the loop's subject
    assert SkillMetadata.__dataclass_fields__["learn"].default is True  # opt-out, never default-off


def test_learn_false_is_audited_but_not_learned():
    reg, audit, learning = _registry()
    res = reg.execute("os.capabilities", {"tenant_id": "_default", "gated_flags": ["vibe_engineering"]})
    assert res.status == "success"
    assert [e.get("skill_id") for e in audit.events if e.get("skill_id") == "os.capabilities"], "must be audited"
    assert not [e for e in learning.events if e.get("skill_id") == "os.capabilities"], "must NOT be learned"


def test_learn_true_is_audited_and_learned():
    reg, audit, learning = _registry()
    res = reg.execute("os.delegation_router", {"complexity": 4, "task_type": "chat", "tenant_id": "_default"})
    assert res.status == "success"
    assert [e for e in audit.events if e.get("skill_id") == "os.delegation_router"]
    assert [e for e in learning.events if e.get("skill_id") == "os.delegation_router"]


def test_custom_skill_without_the_field_defaults_to_learning():
    class Echo(Skill):
        def __init__(self):
            super().__init__(SkillMetadata(id="test.echo", name="Echo", description="", version="0.0.1",
                                           origin=SkillOrigin.BUILTIN, owner="test"))

        def execute(self, input):
            return {"ok": True}

    reg, audit, learning = _registry()
    reg.register(Echo())
    assert reg.execute("test.echo", {}).status == "success"
    assert [e for e in learning.events if e.get("skill_id") == "test.echo"]
