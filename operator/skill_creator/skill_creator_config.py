"""Configuration and feature flags for Skill-Creator.

Integrates with tenant config (spec.features.skill_creator_*) and
console quality subsystem.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SkillCreatorMode(str, Enum):
    """Skill-Creator execution modes."""
    DISABLED = "disabled"       # Feature off
    ENABLED = "enabled"        # Feature on, normal operation
    DEBUG = "debug"            # Extra logging


@dataclass
class SkillCreatorConfig:
    """Skill-Creator configuration.

    Loaded from tenant.corvin.yaml:
    spec:
      features:
        skill_creator_enabled: true
        skill_creator_mode: "enabled"  # or "disabled", "debug"
        skill_creator_max_iterations: 5
        skill_creator_adversarial_reviewers: 3
        skill_creator_loss_threshold: 0.1
    """

    enabled: bool = True
    """Whether Skill-Creator is enabled globally."""

    mode: SkillCreatorMode = SkillCreatorMode.ENABLED
    """Execution mode (disabled/enabled/debug)."""

    max_iterations: int = 5
    """Max LDD iterations (k_max). Hard limit; no override."""

    num_reviewers: int = 3
    """Number of adversarial reviewers per review (always 3 dimensions)."""

    loss_threshold: float = 0.1
    """Convergence threshold for LDD. Loss < threshold → converged."""

    auto_grade_bootstrap: float = 0.3
    """Auto-grade bootstrap seed for new skills. [0.0, 1.0]."""

    skills_directory: Optional[str] = None
    """Directory for generated skills. None = ~/.claude/skills/"""

    # Console UI settings
    panel_visible: bool = True
    """Whether Skill-Creator panel is visible in console → quality."""

    show_quality_metrics: bool = True
    """Show quality score and review findings in UI."""

    show_iteration_history: bool = True
    """Show LDD iteration history in UI."""

    # Limits
    max_request_length: int = 500
    """Max length of user request (chars)."""

    max_method_length: int = 5000
    """Max length of generated skill method (chars)."""

    timeout_per_phase_seconds: int = 60
    """Timeout per phase. 0 = no timeout."""

    def __post_init__(self):
        """Validate config after initialization."""
        if not (1 <= self.max_iterations <= 10):
            raise ValueError(f"max_iterations must be in [1, 10], got {self.max_iterations}")

        if self.max_iterations > 5:
            import warnings
            warnings.warn(
                f"max_iterations={self.max_iterations} exceeds recommended max (5). "
                "This may cause excessive token usage."
            )

        if not (0.0 <= self.loss_threshold <= 1.0):
            raise ValueError(f"loss_threshold must be in [0.0, 1.0], got {self.loss_threshold}")

        if not (0.0 <= self.auto_grade_bootstrap <= 1.0):
            raise ValueError(f"auto_grade_bootstrap must be in [0.0, 1.0], got {self.auto_grade_bootstrap}")

    @classmethod
    def from_tenant_yaml(cls, tenant_config: dict) -> "SkillCreatorConfig":
        """Load config from tenant.corvin.yaml.

        Args:
            tenant_config: Full tenant config dict (tenant.corvin.yaml parsed)

        Returns:
            SkillCreatorConfig instance
        """
        features = tenant_config.get("spec", {}).get("features", {})

        # Extract Skill-Creator feature flags
        enabled = features.get("skill_creator_enabled", True)
        mode_str = features.get("skill_creator_mode", "enabled")
        max_iterations = features.get("skill_creator_max_iterations", 5)
        num_reviewers = features.get("skill_creator_adversarial_reviewers", 3)
        loss_threshold = features.get("skill_creator_loss_threshold", 0.1)
        auto_grade = features.get("skill_creator_auto_grade_bootstrap", 0.3)

        try:
            mode = SkillCreatorMode(mode_str)
        except ValueError:
            mode = SkillCreatorMode.ENABLED

        return cls(
            enabled=enabled,
            mode=mode,
            max_iterations=max_iterations,
            num_reviewers=num_reviewers,
            loss_threshold=loss_threshold,
            auto_grade_bootstrap=auto_grade,
        )

    def to_dict(self) -> dict:
        """Export config as dict (for JSON serialization)."""
        return {
            "enabled": self.enabled,
            "mode": self.mode.value,
            "max_iterations": self.max_iterations,
            "num_reviewers": self.num_reviewers,
            "loss_threshold": self.loss_threshold,
            "auto_grade_bootstrap": self.auto_grade_bootstrap,
            "panel_visible": self.panel_visible,
            "show_quality_metrics": self.show_quality_metrics,
        }


# Singleton instance (loaded at console startup)
_instance: Optional[SkillCreatorConfig] = None


def get_config() -> SkillCreatorConfig:
    """Get global Skill-Creator config instance.

    Returns:
        SkillCreatorConfig (loaded from tenant config, or default)
    """
    global _instance
    if _instance is None:
        # Load from tenant config or use default
        try:
            import yaml
            from pathlib import Path
            tenant_yaml = Path.home() / ".corvin" / "tenant.corvin.yaml"
            if tenant_yaml.exists():
                with open(tenant_yaml) as f:
                    tenant_config = yaml.safe_load(f) or {}
                _instance = SkillCreatorConfig.from_tenant_yaml(tenant_config)
            else:
                _instance = SkillCreatorConfig()
        except Exception:
            # Fallback to default if load fails
            _instance = SkillCreatorConfig()

    return _instance


def set_config(config: SkillCreatorConfig) -> None:
    """Set global Skill-Creator config instance."""
    global _instance
    _instance = config


def is_enabled() -> bool:
    """Check if Skill-Creator is enabled."""
    return get_config().enabled and get_config().mode != SkillCreatorMode.DISABLED


def is_debug_mode() -> bool:
    """Check if Skill-Creator is in debug mode."""
    return get_config().mode == SkillCreatorMode.DEBUG
