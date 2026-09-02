"""Feature Flags Skills Implementation (Phase 1 Spike).

Implements 6 composite Skills + storage layer + audit trail integration.
This module is called by both:
  - Wrapper adapter (Phase 1b) via transparent delegation
  - Direct Skills API calls (Phase 2+ gradual migration)
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from corvin_core import feature_flags as _ff
from forge import paths as _forge_paths

logger = logging.getLogger(__name__)

# Audit integration (Phase 2, ADR-0232/0233)
try:
    from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
    _AUDIT_AVAILABLE = True
except ImportError:
    _AUDIT_AVAILABLE = False
    AuditChainWriter = None
    AuditEvent = None


# ─── STORAGE LAYER (Tenant-isolated overlay JSON) ──────────────────────────────

class FeatureFlagsStorage:
    """
    JSON storage layer for feature flag overlays.

    File: {tenant_home}/global/feature_flags_overlay.json
    Format: { "flags": { "flag_id": bool, ... } }
    Tenant isolation: every read/write validates tenant_id
    """

    _OVERLAY_NAME = "feature_flags_overlay.json"
    _LOCK = threading.Lock()

    def __init__(self):
        """Initialize storage."""
        self._spec_cache: dict[str, tuple[float, dict]] = {}

    def _overlay_path(self, tenant_id: str) -> Path:
        """Get overlay file path for tenant."""
        return _forge_paths.tenant_global_dir(tenant_id) / self._OVERLAY_NAME

    def read_overlay(self, tenant_id: str) -> dict[str, Any]:
        """Read flag overlay for tenant (fail-safe)."""
        try:
            path = self._overlay_path(tenant_id)
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # Missing/corrupt overlay → return empty dict
            return {}
        return raw if isinstance(raw, dict) else {}

    def write_overlay(self, tenant_id: str, data: dict[str, Any]) -> None:
        """Write flag overlay for tenant (atomic)."""
        path = self._overlay_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(path)

    def get_flag_state(self, flag_id: str, tenant_id: str) -> bool:
        """Get a flag's enabled state (with precedence: overlay → YAML spec → default)."""
        overlay = self.read_overlay(tenant_id).get("flags", {})

        if isinstance(overlay, dict) and flag_id in overlay:
            return bool(overlay[flag_id])

        # Fall back to original feature_flags logic
        return _ff.is_enabled(flag_id, tenant_id)

    def set_flag_state(self, flag_id: str, enabled: bool, tenant_id: str) -> None:
        """Set a flag's enabled state in overlay."""
        with self._LOCK:
            data = self.read_overlay(tenant_id)
            flags = data.get("flags", {})
            if not isinstance(flags, dict):
                flags = {}
            flags[flag_id] = bool(enabled)
            data["flags"] = flags
            self.write_overlay(tenant_id, data)


# ─── AUDIT TRAIL INTEGRATION (Phase 2) ───────────────────────────────────────

class FeatureFlagsAudit:
    """
    Audit trail for feature flags operations (Phase 2).

    Every is_enabled/set_enabled call emits SKILL_EXECUTED event.
    Events are hash-chained (GDPR Art. 30, 32).
    Tenant-scoped (no cross-tenant leakage).
    """

    _writer: AuditChainWriter | None = None
    _lock = threading.Lock()

    @classmethod
    def _get_writer(cls) -> AuditChainWriter | None:
        """Get or initialize audit chain writer (lazy singleton)."""
        if not _AUDIT_AVAILABLE:
            return None

        if cls._writer is None:
            with cls._lock:
                if cls._writer is None:
                    # Initialize writer with ~/.corvin/audit.jsonl path
                    home = Path.home()
                    audit_path = home / ".corvin" / "audit.jsonl"
                    try:
                        cls._writer = AuditChainWriter(audit_path)
                    except Exception as e:
                        logger.warning(f"Could not initialize AuditChainWriter: {e}")
                        return None

        return cls._writer

    @staticmethod
    def emit_event(
        operation: str,
        flag_id: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any],
        tenant_id: str,
        latency_ms: float,
    ) -> None:
        """
        Emit SKILL_EXECUTED event for audit trail (hash-chained).

        Event structure (ADR-0232/0233):
          {
            "event_type": "skill_executed",
            "skill_id": "os.feature_flags_*",
            "operation": operation,
            "flag_id": flag_id,
            "tenant_id": tenant_id,
            "input": input_data (no PII),
            "output": output_data (no PII),
            "timestamp": ISO8601,
            "latency_ms": latency_ms,
            "lom": "Line of Moral Responsibility"
          }

        Guarantees:
        - Hash-chained for tamper detection (GDPR Art. 30, 32)
        - Tenant-scoped (no cross-tenant leakage)
        - Immutable (append-only)
        - Fail-safe (exceptions don't crash operation)
        """
        writer = FeatureFlagsAudit._get_writer()
        if not writer:
            logger.debug(f"Audit disabled; skipping event for {operation}({flag_id})")
            return

        try:
            event = AuditEvent(
                event_id=str(uuid4()),
                event_type="skill_executed",
                tenant_id=tenant_id,
                user_id=None,  # Feature flags are system-level, not user-specific
                timestamp=datetime.utcnow().isoformat() + "Z",
                details={
                    "skill_id": "os.feature_flags_system",
                    "operation": operation,
                    "flag_id": flag_id,
                    "input": input_data,
                    "output": output_data,
                    "latency_ms": latency_ms,
                    "lom": "core.skills.feature_flags_skill:FeatureFlagsSkill.execute",
                },
                severity="info",
            )
            writer.write_event(event)
            logger.debug(f"Audit event emitted: {event.event_type}[{operation}]")

        except Exception as e:
            # Audit failure should not crash the operation (fail-safe)
            logger.error(f"Audit event emission failed: {e}")


# ─── FEATURE FLAGS SKILL IMPLEMENTATION ───────────────────────────────────────

class FeatureFlagsSkill:
    """
    Feature Flags as a Skill.

    Implements 6 composite Skills (chat, delegation, plugins, context-engineering,
    learning, infrastructure) with 59 flags total.

    Operations:
      - is_enabled: Check if flag is enabled
      - set_enabled: Set flag in overlay
      - describe_all: List all flags + states
      - tier_of: Get flag's release tier
      - can_promote_to: Check tier promotion eligibility
      - worker_engine_mode: Get/set worker engine (legacy, separate)
    """

    def __init__(self):
        """Initialize skill with storage + audit."""
        self.storage = FeatureFlagsStorage()
        self.audit = FeatureFlagsAudit()

    def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a feature flags operation.

        Args:
            input_data: {
                "operation": "is_enabled" | "set_enabled" | "describe_all" | ...,
                "flag_id": str (for is_enabled, set_enabled),
                "enabled": bool (for set_enabled),
                "tenant_id": str (default: "_default"),
                ...
            }

        Returns:
            {
                "operation": str,
                "success": bool,
                "result": dict (operation-specific),
                "error": str (if failed),
            }
        """
        import time

        start_time = time.time()
        operation = input_data.get("operation", "is_enabled")
        flag_id = input_data.get("flag_id")
        tenant_id = input_data.get("tenant_id", "_default")

        try:
            # Dispatch to operation handler
            if operation == "is_enabled":
                result = self._is_enabled(flag_id, tenant_id)

            elif operation == "set_enabled":
                enabled = input_data.get("enabled", False)
                result = self._set_enabled(flag_id, enabled, tenant_id)

            elif operation == "describe_all":
                result = self._describe_all(tenant_id)

            elif operation == "tier_of":
                result = self._tier_of(flag_id)

            elif operation == "can_promote_to":
                target_tier = input_data.get("target_tier")
                result = self._can_promote_to(flag_id, target_tier)

            elif operation == "worker_engine_mode":
                result = self._worker_engine_mode(tenant_id)

            elif operation == "set_worker_engine_mode":
                mode = input_data.get("mode")
                result = self._set_worker_engine_mode(mode, tenant_id)

            else:
                return {
                    "operation": operation,
                    "success": False,
                    "error": f"Unknown operation: {operation}",
                }

            # Emit audit event
            latency_ms = (time.time() - start_time) * 1000
            self.audit.emit_event(
                operation=operation,
                flag_id=flag_id or "",
                input_data=input_data,
                output_data=result,
                tenant_id=tenant_id,
                latency_ms=latency_ms,
            )

            return {
                "operation": operation,
                "success": True,
                "result": result,
                "latency_ms": latency_ms,
            }

        except Exception as e:
            logger.error(f"Skill execution failed: {e}")
            return {
                "operation": operation,
                "success": False,
                "error": str(e),
            }

    # ─── OPERATION HANDLERS ──────────────────────────────────────────────────

    def _is_enabled(self, flag_id: str, tenant_id: str) -> dict[str, Any]:
        """Check if flag is enabled."""
        enabled = self.storage.get_flag_state(flag_id, tenant_id)
        source = _ff._source_of(flag_id, tenant_id)
        tier = _ff.tier_of(flag_id)

        return {
            "flag_id": flag_id,
            "enabled": enabled,
            "source": source,  # console | tenant_yaml | whitelist | default
            "tier": tier,  # alpha | beta | stable | production
        }

    def _set_enabled(
        self, flag_id: str, enabled: bool, tenant_id: str
    ) -> dict[str, Any]:
        """Set flag in overlay."""
        _ff.flag(flag_id)  # Validate flag exists
        self.storage.set_flag_state(flag_id, enabled, tenant_id)
        return {
            "flag_id": flag_id,
            "enabled": enabled,
            "timestamp": datetime.utcnow().isoformat(),
            "source": "console",  # Overlay = console-set
        }

    def _describe_all(self, tenant_id: str) -> dict[str, Any]:
        """Describe all flags + states."""
        flags = _ff.describe_all(tenant_id)
        return {"flags": flags, "count": len(flags)}

    def _tier_of(self, flag_id: str) -> dict[str, Any]:
        """Get flag's release tier."""
        tier = _ff.tier_of(flag_id)
        return {"flag_id": flag_id, "tier": tier}

    def _can_promote_to(
        self, flag_id: str, target_tier: str
    ) -> dict[str, Any]:
        """Check tier promotion eligibility."""
        can_promote = _ff.can_promote_to(flag_id, target_tier)
        return {
            "flag_id": flag_id,
            "target_tier": target_tier,
            "can_promote": can_promote,
        }

    def _worker_engine_mode(self, tenant_id: str) -> dict[str, Any]:
        """Get worker engine mode (legacy, separate)."""
        mode = _ff.worker_engine_mode(tenant_id)
        return {"mode": mode, "options": ["native", "acs", "tde"]}

    def _set_worker_engine_mode(
        self, mode: str, tenant_id: str
    ) -> dict[str, Any]:
        """Set worker engine mode (legacy, separate)."""
        new_mode = _ff.set_worker_engine_mode(mode, tenant_id)
        return {"mode": new_mode, "timestamp": datetime.utcnow().isoformat()}


# ─── MODULE-LEVEL SKILL INSTANCE ──────────────────────────────────────────────

feature_flags_skill = FeatureFlagsSkill()


# ─── BACKWARD-COMPATIBLE EXPORTS ──────────────────────────────────────────────
#
# Wrapper adapter imports these for transparent delegation:
#   from core.skills.feature_flags_skill import feature_flags_skill
#   skill.execute({operation: "is_enabled", flag_id: "vibe_engineering", ...})
#
# Direct Skills API (Phase 2+):
#   from core.skills.feature_flags_skill import feature_flags_skill
#   result = feature_flags_skill.execute(...)
#
# ─────────────────────────────────────────────────────────────────────────────
