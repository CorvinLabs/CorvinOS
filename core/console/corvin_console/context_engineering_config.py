"""Context Engineering Configuration Management.

Allows operator to control CE behavior:
- Enable/disable stages (memory_lookup, graph_inference, skill_injection)
- Set relevance thresholds
- Configure quota + degradation behavior
- Audit all changes to hash-chained trail

ADR-0275 (Vibe Engineering Surface) + ADR-0276 (License Gate).
Wave 1-4 compatible.
"""

import asyncio
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


class DegradationStrategy(str, Enum):
    """How to degrade if context doesn't meet threshold."""
    DROP_LOWEST_CONFIDENCE = "drop_lowest_confidence"
    DROP_ALL = "drop_all"
    WARN_ONLY = "warn_only"


class HardLimitBehavior(str, Enum):
    """Behavior when quota limit hit."""
    FAIL_CLOSED = "fail_closed"
    DEGRADE = "degrade"
    WARN_ONLY = "warn_only"


class ContextEngineeringConfigSchema(BaseModel):
    """Pydantic schema for CE configuration validation."""

    class Config:
        extra = "forbid"  # Reject unknown fields
        frozen = False

    # Top-level
    enabled: bool = True
    license_tier: str = Field("free", pattern="^(free|paid)$")

    # Stages configuration
    stages: Dict[str, Dict[str, Any]] = Field(
        default_factory=lambda: {
            "memory_lookup": {
                "enabled": True,
                "max_results": 10,
                "relevance_threshold": 0.60,
            },
            "graph_inference": {
                "enabled": True,
                "max_depth": 3,
                "timeout_sec": 5.0,
            },
            "skill_injection": {
                "enabled": True,
                "max_skills": 5,
                "prefer_repo_skills": True,
            },
        }
    )

    # Degradation configuration
    degradation: Dict[str, Any] = Field(
        default_factory=lambda: {
            "strategy": "drop_lowest_confidence",
            "min_confidence": 0.70,
            "preserve_mandatory": True,
        }
    )

    # Quota configuration
    quota: Dict[str, Any] = Field(
        default_factory=lambda: {
            "daily_units": 10,
            "soft_limit_percent": 80,
            "hard_limit_behavior": "fail_closed",
        }
    )

    # Audit configuration
    audit: Dict[str, bool] = Field(
        default_factory=lambda: {
            "log_all_stages": True,
            "log_degradation": True,
            "log_skipped_skills": False,
        }
    )

    @validator("stages")
    def validate_stages(cls, v):
        """Ensure all required stages present."""
        required = {"memory_lookup", "graph_inference", "skill_injection"}
        if not required.issubset(set(v.keys())):
            raise ValueError(f"Missing required stages: {required - set(v.keys())}")
        return v

    @validator("degradation")
    def validate_degradation(cls, v):
        """Ensure valid degradation config."""
        if "strategy" not in v:
            raise ValueError("degradation.strategy required")
        valid_strategies = [s.value for s in DegradationStrategy]
        if v["strategy"] not in valid_strategies:
            raise ValueError(f"Invalid strategy: {v['strategy']}")
        return v

    @validator("quota")
    def validate_quota(cls, v):
        """Ensure valid quota config."""
        if "daily_units" not in v or v["daily_units"] <= 0:
            raise ValueError("quota.daily_units must be > 0")
        return v


class ConfigValidationError(Exception):
    """Raised when config validation fails."""
    pass


class ContextEngineeringConfigManager:
    """Manage CE config: load, validate, update, persist.

    Atomic updates: all changes are transactional (update all or none).
    """

    def __init__(self, tenant_id: str = "_default", config_dir: Optional[Path] = None):
        self.tenant_id = tenant_id
        if config_dir is None:
            config_dir = Path.home() / ".corvin" / "tenants" / tenant_id / "global"
        self.config_path = config_dir / "context-engineering.yaml"
        self._config: Optional[ContextEngineeringConfigSchema] = None
        self._lock = asyncio.Lock()  # Atomic updates
        self.load()

    def load(self) -> ContextEngineeringConfigSchema:
        """Load config from YAML (or defaults if file missing)."""
        if self.config_path.exists():
            try:
                with open(self.config_path) as f:
                    data = yaml.safe_load(f)
                self._config = ContextEngineeringConfigSchema(**data)
                logger.info(f"Loaded CE config for {self.tenant_id}")
            except (ValueError, yaml.YAMLError) as e:
                logger.error(f"Invalid CE config for {self.tenant_id}: {e}")
                raise  # Fail-closed: invalid config blocks startup
        else:
            self._config = ContextEngineeringConfigSchema()  # Defaults
            logger.info(f"Using default CE config for {self.tenant_id}")

        return self._config

    async def update(self, changes: Dict[str, Any]) -> ContextEngineeringConfigSchema:
        """Atomically update config (validate, save, reload)."""
        async with self._lock:
            # Merge changes into current config
            current_dict = self._config.dict()
            merged = self._deep_merge(current_dict, changes)

            # Validate new config
            try:
                new_config = ContextEngineeringConfigSchema(**merged)
            except ValueError as e:
                raise ConfigValidationError(f"Invalid changes: {e}")

            # Persist to disk
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w") as f:
                yaml.dump(new_config.dict(), f, default_flow_style=False)

            # Update in-memory
            self._config = new_config
            logger.info(f"Updated CE config for {self.tenant_id}")

            # TODO: Emit audit event (integration in Phase 1, Week 3)
            # await audit_chain.append(
            #     event_type="context_engineering_config_changed",
            #     tenant_id=self.tenant_id,
            #     payload={"changes": changes},
            # )

            return new_config

    async def reset_to_defaults(self) -> ContextEngineeringConfigSchema:
        """Reset to factory defaults."""
        logger.info(f"Resetting CE config to defaults for {self.tenant_id}")
        return await self.update({})

    def get(self) -> ContextEngineeringConfigSchema:
        """Get current config (no load)."""
        return self._config or self.load()

    def dict(self) -> Dict[str, Any]:
        """Export config as dict."""
        return self.get().dict()

    @staticmethod
    def _deep_merge(base: Dict, updates: Dict) -> Dict:
        """Deep merge updates into base dict."""
        result = base.copy()
        for key, value in updates.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ContextEngineeringConfigManager._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
