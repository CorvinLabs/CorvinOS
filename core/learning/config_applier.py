"""Config Applier — Gate 3 Implementation (ADR-0676).

Applies learned config deltas to SkillInstance and persists to config_history.jsonl.

Architecture:
1. Optimizer computes config deltas (e.g., threshold=0.7→0.65)
2. ConfigApplier validates deltas (bounds, safe ranges)
3. Applies to SkillInstance in-memory
4. Persists to ~/.corvin/tenants/<tenant>/skills/<skill_id>/config_history.jsonl
5. Emits ConfigUpdatedEvent to audit trail

Persistence format (JSONL):
{
  "config_update_id": "uuid",
  "timestamp": "2026-09-17T...",
  "skill_id": "os.delegation_router",
  "version": "N",
  "parameter_deltas": {"confidence_threshold": -0.05},
  "reason": "feedback_driven",
  "feedback_id": "uuid",
  "applied": true
}
"""

import logging
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class ConfigUpdateEvent:
    """Event emitted when config is updated."""
    config_update_id: str
    skill_id: str
    version: int
    parameter_deltas: Dict[str, float]
    reason: str
    feedback_id: Optional[str] = None
    timestamp: Optional[str] = None
    applied: bool = False


class ConfigApplier:
    """Applies and persists config updates."""

    def __init__(self, corvin_home: str = "~/.corvin"):
        """Initialize config applier."""
        self.corvin_home = Path(corvin_home).expanduser()
        self.configs: Dict[str, Dict[str, Any]] = {}  # In-memory: skill_id → config

    def apply_config_delta(
        self,
        skill_id: str,
        parameter_deltas: Dict[str, float],
        reason: str = "feedback_driven",
        feedback_id: Optional[str] = None,
        tenant_id: str = "_default",
    ) -> Tuple[bool, str, Optional[ConfigUpdateEvent]]:
        """
        Apply config delta to skill.

        Returns: (success, message, event)
        """
        config_update_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Validate deltas
        valid, error_msg = self._validate_deltas(parameter_deltas)
        if not valid:
            logger.warning(f"Invalid config delta for {skill_id}: {error_msg}")
            return False, error_msg, None

        # Load current config
        config = self.configs.get(skill_id, {})

        # Apply deltas
        updated_config = config.copy()
        for param, delta in parameter_deltas.items():
            if param in updated_config:
                updated_config[param] += delta
                # Clamp to valid bounds
                updated_config[param] = max(0.0, min(1.0, updated_config[param]))
            else:
                # New parameter
                updated_config[param] = delta

        # Store in-memory
        self.configs[skill_id] = updated_config

        # Persist to config_history.jsonl
        history_path = self._get_history_path(skill_id, tenant_id)
        version = self._get_next_version(history_path)

        event = ConfigUpdateEvent(
            config_update_id=config_update_id,
            skill_id=skill_id,
            version=version,
            parameter_deltas=parameter_deltas,
            reason=reason,
            feedback_id=feedback_id,
            timestamp=timestamp,
            applied=True,
        )

        try:
            self._persist_config_update(history_path, event)
            logger.info(
                f"Config updated: skill={skill_id}, version={version}, "
                f"deltas={parameter_deltas}, reason={reason}"
            )
            return True, f"Config applied: version {version}", event

        except Exception as e:
            logger.error(f"Failed to persist config update: {e}")
            return False, str(e), None

    def _validate_deltas(self, deltas: Dict[str, float]) -> Tuple[bool, Optional[str]]:
        """Validate config deltas."""
        if not deltas:
            return False, "No deltas provided"

        for param, delta in deltas.items():
            if not isinstance(delta, (int, float)):
                return False, f"Delta for {param} must be numeric"
            if abs(delta) > 1.0:
                return False, f"Delta for {param} too large (max ±1.0): {delta}"

        return True, None

    def _get_history_path(self, skill_id: str, tenant_id: str) -> Path:
        """Get path to config_history.jsonl for skill."""
        path = self.corvin_home / "tenants" / tenant_id / "skills" / skill_id
        path.mkdir(parents=True, exist_ok=True)
        return path / "config_history.jsonl"

    def _get_next_version(self, history_path: Path) -> int:
        """Get next version number from history file."""
        if not history_path.exists():
            return 1

        try:
            with open(history_path, "r") as f:
                lines = f.readlines()
                if lines:
                    last_event = json.loads(lines[-1])
                    return last_event.get("version", 0) + 1
        except Exception as e:
            logger.warning(f"Failed to read version from history: {e}")

        return 1

    def _persist_config_update(self, history_path: Path, event: ConfigUpdateEvent) -> None:
        """Persist config update to JSONL file."""
        event_dict = {
            "config_update_id": event.config_update_id,
            "timestamp": event.timestamp,
            "skill_id": event.skill_id,
            "version": event.version,
            "parameter_deltas": event.parameter_deltas,
            "reason": event.reason,
            "feedback_id": event.feedback_id,
            "applied": event.applied,
        }

        with open(history_path, "a") as f:
            f.write(json.dumps(event_dict) + "\n")

    def get_config(self, skill_id: str) -> Dict[str, Any]:
        """Get current config for skill."""
        return self.configs.get(skill_id, {})

    def get_config_history(self, skill_id: str, tenant_id: str = "_default") -> list:
        """Get config history for skill."""
        history_path = self._get_history_path(skill_id, tenant_id)
        if not history_path.exists():
            return []

        history = []
        try:
            with open(history_path, "r") as f:
                for line in f:
                    history.append(json.loads(line))
        except Exception as e:
            logger.error(f"Failed to read config history: {e}")

        return history

    def rollback_config(self, skill_id: str, version: int, tenant_id: str = "_default") -> bool:
        """Rollback config to previous version."""
        history = self.get_config_history(skill_id, tenant_id)

        # Find config at target version
        target_config = None
        for event in history:
            if event["version"] == version:
                # Reconstruct config by replaying deltas up to this version
                config = {}
                for e in history:
                    if e["version"] <= version:
                        for param, delta in e["parameter_deltas"].items():
                            config[param] = config.get(param, 0) + delta
                target_config = config
                break

        if target_config is None:
            logger.warning(f"Version {version} not found in history")
            return False

        self.configs[skill_id] = target_config
        logger.info(f"Config rolled back: skill={skill_id}, version={version}")
        return True


# Global singleton
_config_applier = None


def get_config_applier(corvin_home: str = "~/.corvin") -> ConfigApplier:
    """Get or create config applier singleton."""
    global _config_applier
    if _config_applier is None:
        _config_applier = ConfigApplier(corvin_home=corvin_home)
    return _config_applier
