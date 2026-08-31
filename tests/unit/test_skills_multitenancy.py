"""Tests for ADR-0312: Multi-Tenant Skill Isolation."""

import pytest

from core.skills.multitenancy import TenantSkillManager, tenant_skill_key


def test_tenant_skill_key():
    key = tenant_skill_key("tenant1", "skill1", "1.0")
    assert key == "tenant1:skill1:1.0"


class TestTenantSkillManager:
    def test_save_and_load(self):
        manager = TenantSkillManager()
        skill_data = {"name": "skill1", "value": 42}

        manager.save_skill("tenant1", "skill1", "1.0", skill_data)
        loaded = manager.load_skill("tenant1", "skill1", "1.0")

        assert loaded == skill_data

    def test_tenant_isolation(self):
        manager = TenantSkillManager()
        manager.save_skill("tenant1", "skill", "1.0", {"data": "t1"})
        manager.save_skill("tenant2", "skill", "1.0", {"data": "t2"})

        t1_skill = manager.load_skill("tenant1", "skill", "1.0")
        t2_skill = manager.load_skill("tenant2", "skill", "1.0")

        assert t1_skill["data"] == "t1"
        assert t2_skill["data"] == "t2"

    def test_list_tenant_skills(self):
        manager = TenantSkillManager()
        manager.save_skill("tenant1", "skill1", "1.0", {})
        manager.save_skill("tenant1", "skill2", "1.0", {})
        manager.save_skill("tenant2", "skill1", "1.0", {})

        t1_skills = manager.list_tenant_skills("tenant1")
        assert len(t1_skills) == 2
