"""Config Applier — Gate 3 Implementation (ADR-0676).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). The
only importers are tests/test_track_b_*; config_history.jsonl is a plain
history file, NOT the audit chain.

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
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from uuid import uuid4

logger = logging.getLogger(__name__)

# Every learned parameter lives in [0.0, 1.0] — applied values, NEW parameters
# and replayed (rolled-back) values alike.
_PARAM_MIN, _PARAM_MAX = 0.0, 1.0

# skill_id becomes a directory name under the tenant home: no separators,
# no "..", nothing a path could be built from.
_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _clamp(value: float) -> float:
    return max(_PARAM_MIN, min(_PARAM_MAX, value))


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

    def __init__(self, corvin_home: Optional[str] = None):
        """Initialize config applier.

        ``corvin_home`` defaults to the canonical resolver (``$CORVIN_HOME``),
        never a hard-wired ``~/.corvin``.
        """
        if corvin_home is None:
            from core.paths.tenant import corvin_home as _resolve_home

            self.corvin_home = _resolve_home()
        else:
            self.corvin_home = Path(corvin_home).expanduser()
        # In-memory: (tenant_id, skill_id) → config. Keyed per tenant so one
        # tenant's learned config is never served to another.
        self.configs: Dict[Tuple[str, str], Dict[str, Any]] = {}

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

        # Validate identifiers (they become path components) and deltas
        valid, error_msg = self._validate_ids(skill_id, tenant_id)
        if valid:
            valid, error_msg = self._validate_deltas(parameter_deltas)
        if not valid:
            logger.warning(f"Invalid config delta for {skill_id!r}: {error_msg}")
            return False, error_msg, None

        key = (tenant_id, skill_id)
        # Apply deltas; a NEW parameter starts from 0.0 and is clamped like
        # any other (it used to be stored raw, so a first delta of -0.5
        # produced a negative threshold).
        updated_config = self.configs.get(key, {}).copy()
        for param, delta in parameter_deltas.items():
            updated_config[param] = _clamp(updated_config.get(param, 0.0) + delta)

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
            # Only a persisted update becomes the live config — a failed
            # write must not leave an unrecorded config in memory.
            self.configs[key] = updated_config
            logger.info(
                f"Config updated: skill={skill_id}, version={version}, "
                f"deltas={parameter_deltas}, reason={reason}"
            )
            return True, f"Config applied: version {version}", event

        except Exception as e:
            logger.error(f"Failed to persist config update: {e}")
            return False, str(e), None

    @staticmethod
    def _validate_ids(skill_id: str, tenant_id: str) -> Tuple[bool, Optional[str]]:
        """Reject identifiers that would escape the tenant/skill directory."""
        from core.tenants import validate_tenant_id

        try:
            validate_tenant_id(tenant_id)
        except ValueError as exc:
            return False, f"Invalid tenant_id: {exc}"
        if not isinstance(skill_id, str) or not _SKILL_ID_RE.match(skill_id) or ".." in skill_id:
            return False, f"Invalid skill_id: {skill_id!r}"
        return True, None

    def _validate_deltas(self, deltas: Dict[str, float]) -> Tuple[bool, Optional[str]]:
        """Validate config deltas."""
        if not deltas:
            return False, "No deltas provided"

        for param, delta in deltas.items():
            if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                return False, f"Delta for {param} must be numeric"
            if not math.isfinite(delta):
                # NaN slipped through ``abs(delta) > 1.0`` (always False).
                return False, f"Delta for {param} must be a finite numeric value"
            if abs(delta) > 1.0:
                return False, f"Delta for {param} too large (max ±1.0): {delta}"

        return True, None

    def _get_history_path(self, skill_id: str, tenant_id: str) -> Path:
        """Get path to config_history.jsonl for skill."""
        ok, err = self._validate_ids(skill_id, tenant_id)
        if not ok:
            raise ValueError(err)
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

    def get_config(self, skill_id: str, tenant_id: str = "_default") -> Dict[str, Any]:
        """Get current config for skill (of ``tenant_id``)."""
        return dict(self.configs.get((tenant_id, skill_id), {}))

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
                # Replay with the SAME clamping apply_config_delta uses, or
                # a rollback reconstructs values apply never produced.
                config = {}
                for e in history:
                    if e["version"] <= version:
                        for param, delta in e["parameter_deltas"].items():
                            config[param] = _clamp(config.get(param, 0.0) + delta)
                target_config = config
                break

        if target_config is None:
            logger.warning(f"Version {version} not found in history")
            return False

        self.configs[(tenant_id, skill_id)] = target_config
        logger.info(f"Config rolled back: skill={skill_id}, version={version}")
        return True


# Global singleton
_config_applier = None


def get_config_applier(corvin_home: Optional[str] = None) -> ConfigApplier:
    """Get or create config applier singleton."""
    global _config_applier
    if _config_applier is None:
        _config_applier = ConfigApplier(corvin_home=corvin_home)
    return _config_applier
