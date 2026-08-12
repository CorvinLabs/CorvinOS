"""Multi-Tenant Skill Isolation (ADR-0312)."""

from __future__ import annotations


def tenant_skill_key(tenant_id: str, skill_name: str, skill_version: str) -> str:
    """Create a tenant-scoped skill key."""
    return f"{tenant_id}:{skill_name}:{skill_version}"


class TenantSkillManager:
    """Manages skills per tenant (isolation)."""

    def __init__(self):
        self.skills: dict[str, dict] = {}

    def save_skill(self, tenant_id: str, skill_name: str, skill_version: str, skill_data: dict) -> None:
        """Save skill with tenant scope."""
        key = tenant_skill_key(tenant_id, skill_name, skill_version)
        self.skills[key] = skill_data

    def load_skill(self, tenant_id: str, skill_name: str, skill_version: str) -> dict | None:
        """Load tenant-scoped skill."""
        key = tenant_skill_key(tenant_id, skill_name, skill_version)
        return self.skills.get(key)

    def list_tenant_skills(self, tenant_id: str) -> list[str]:
        """List all skills for a tenant."""
        prefix = f"{tenant_id}:"
        return [k for k in self.skills.keys() if k.startswith(prefix)]
