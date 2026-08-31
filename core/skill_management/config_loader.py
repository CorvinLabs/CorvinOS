"""Tenant configuration loader for skills."""

import json
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

from core.skill_management.tenant_validator import validate_tenant_id


@dataclass
class SkillPreferences:
    """User skill preferences."""
    enabled_skills: List[str]
    disabled_skills: List[str]
    skill_aliases: Dict[str, str]
    tool_cost_limits: Dict[str, int]


@dataclass
class SkillConfig:
    """Tenant-level skill configuration."""
    auto_cleanup_local: bool
    cleanup_ttl_days: int
    github_sync_enabled: bool
    github_sync_repo: Optional[str]
    github_sync_branch: str
    github_sync_push_frequency: str  # 'daily', 'weekly', 'manual'


class ConfigLoader:
    """Load and manage tenant skill configuration."""

    def __init__(self, tenant_id: str = "_default"):
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self.base_path = Path.home() / ".corvin" / "tenants" / tenant_id
        self.config_dir = self.base_path / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load_skill_config(self) -> SkillConfig:
        """Load skill configuration from tenant.corvin.yaml."""
        config_file = self.config_dir / "tenant.corvin.yaml"

        # Defaults
        defaults = {
            "auto_cleanup_local": True,
            "cleanup_ttl_days": 90,
            "github_sync_enabled": False,
            "github_sync_repo": None,
            "github_sync_branch": "main",
            "github_sync_push_frequency": "manual"
        }

        if not config_file.exists():
            return SkillConfig(**defaults)

        try:
            with open(config_file) as f:
                config_data = yaml.safe_load(f) or {}

            # Navigate to spec.skills
            spec = config_data.get("spec", {})
            skills_spec = spec.get("skills", {})

            return SkillConfig(
                auto_cleanup_local=skills_spec.get("auto_cleanup_local", defaults["auto_cleanup_local"]),
                cleanup_ttl_days=skills_spec.get("cleanup_ttl_days", defaults["cleanup_ttl_days"]),
                github_sync_enabled=skills_spec.get("github_sync", {}).get("enabled", defaults["github_sync_enabled"]),
                github_sync_repo=skills_spec.get("github_sync", {}).get("repo"),
                github_sync_branch=skills_spec.get("github_sync", {}).get("branch", defaults["github_sync_branch"]),
                github_sync_push_frequency=skills_spec.get("github_sync", {}).get("push_frequency", defaults["github_sync_push_frequency"])
            )
        except Exception as e:
            print(f"Warning: Failed to load config: {e}. Using defaults.")
            return SkillConfig(**defaults)

    def load_skill_preferences(self) -> SkillPreferences:
        """Load skill preferences from skill-prefs.json."""
        prefs_file = self.config_dir / "skill-prefs.json"

        defaults = {
            "enabled_skills": [],
            "disabled_skills": [],
            "skill_aliases": {},
            "tool_cost_limits": {}
        }

        if not prefs_file.exists():
            return SkillPreferences(**defaults)

        try:
            with open(prefs_file) as f:
                prefs_data = json.load(f)

            return SkillPreferences(
                enabled_skills=prefs_data.get("enabled_skills", defaults["enabled_skills"]),
                disabled_skills=prefs_data.get("disabled_skills", defaults["disabled_skills"]),
                skill_aliases=prefs_data.get("skill_aliases", defaults["skill_aliases"]),
                tool_cost_limits=prefs_data.get("tool_cost_limits", defaults["tool_cost_limits"])
            )
        except Exception as e:
            print(f"Warning: Failed to load preferences: {e}. Using defaults.")
            return SkillPreferences(**defaults)

    def save_skill_preferences(self, prefs: SkillPreferences) -> bool:
        """Save skill preferences to disk."""
        prefs_file = self.config_dir / "skill-prefs.json"

        try:
            data = {
                "enabled_skills": prefs.enabled_skills,
                "disabled_skills": prefs.disabled_skills,
                "skill_aliases": prefs.skill_aliases,
                "tool_cost_limits": prefs.tool_cost_limits
            }
            with open(prefs_file, "w") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            print(f"Failed to save preferences: {e}")
            return False

    def is_skill_enabled(self, skill_id: str) -> bool:
        """Check if a skill is enabled."""
        prefs = self.load_skill_preferences()
        return skill_id not in prefs.disabled_skills

    def enable_skill(self, skill_id: str) -> bool:
        """Enable a skill."""
        prefs = self.load_skill_preferences()
        if skill_id in prefs.disabled_skills:
            prefs.disabled_skills.remove(skill_id)
        return self.save_skill_preferences(prefs)

    def disable_skill(self, skill_id: str) -> bool:
        """Disable a skill."""
        prefs = self.load_skill_preferences()
        if skill_id not in prefs.disabled_skills:
            prefs.disabled_skills.append(skill_id)
        return self.save_skill_preferences(prefs)

    def get_skill_alias(self, alias: str) -> Optional[str]:
        """Resolve a skill alias to skill ID."""
        prefs = self.load_skill_preferences()
        return prefs.skill_aliases.get(alias)

    def set_skill_alias(self, alias: str, skill_id: str) -> bool:
        """Set a skill alias."""
        prefs = self.load_skill_preferences()
        prefs.skill_aliases[alias] = skill_id
        return self.save_skill_preferences(prefs)


def load_tenant_skill_config(tenant_id: str = "_default") -> SkillConfig:
    """Public API: Load skill config."""
    loader = ConfigLoader(tenant_id)
    return loader.load_skill_config()


def load_tenant_skill_prefs(tenant_id: str = "_default") -> SkillPreferences:
    """Public API: Load skill preferences."""
    loader = ConfigLoader(tenant_id)
    return loader.load_skill_preferences()
