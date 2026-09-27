"""Cron Trigger Poller — continuously poll for Skill loss signals.

Runs on a scheduled interval (default: 1 hour) and uses SkillLossTriggerDetector
to check all tenant audit trails for Skill confidence below threshold (0.70).
When loss signals are detected:

1. Emit audit event: skill_forge_triggered_by_cron
2. Check if autonomous_forge_enabled for tenant
3. If enabled: trigger Skill Forge optimizer (Phase 7 flow)
4. If disabled: log event but don't trigger

Tenant scope: Iterates all active tenants independently.
Side effects: Audit events (all), forge triggers (selective per config).
Fail-closed: Exceptions logged, don't crash, retry next hour.

ADR-0613: Loss signals feed autonomous forge loop.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). The
only importer outside tests is ``corvin_console/routes/cron_status_routes.py``,
which ``corvin_console.app`` does not mount; nothing calls
``start_cron_service()``.

Audit (fixed 2026-09-27): both events used to be appended as raw, UNCHAINED
JSON lines straight into ``tenant_audit_chain()`` — which breaks the hash
chain the ADR-0232 boot tripwire verifies — and a failed write was swallowed.
They now go through ``forge.security_events.write_event`` (registered below),
audit-first: no forge trigger is written unless its record committed.
"""

import json
import logging
import re
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from core.paths import tenant_audit_chain, corvin_home
from core.tenants import validate_tenant_id

# ``skill-forge`` carries a dash, so this subtree resolves its siblings
# RELATIVELY (as ``autonomous/`` already does) — it works under whatever
# parent name the loader registered (``corvin_operator.skill_forge`` in the
# console and tests). An absolute ``corvin_operator.skill_forge.autonomous``
# import only resolved if some earlier importer had created that alias.
from ..autonomous import (
    SkillLossTriggerDetector,
    LossTrigger,
)

logger = logging.getLogger(__name__)


_SAFE_SKILL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# ── Audit events (registered with the core writer at import) ────────────
_CRON_EVENTS = {
    "skill_forge_triggered_by_cron": (
        "WARNING",
        frozenset({"skill_id", "skill_version", "confidence", "event_count",
                   "lookback_hours", "trigger_time"}),
    ),
    "skill_forge_optimizer_triggered": (
        "INFO",
        frozenset({"skill_id", "skill_version", "confidence"}),
    ),
}


def _security_events():
    """The core audit writer (``corvin_operator/forge/forge/security_events``)."""
    import sys

    forge_dir = Path(__file__).resolve().parents[2] / "forge"
    if forge_dir.is_dir() and str(forge_dir) not in sys.path:
        sys.path.insert(0, str(forge_dir))
    from forge import security_events  # type: ignore[import-not-found]

    for event_type, (severity, fields) in _CRON_EVENTS.items():
        security_events.EVENT_SEVERITY.setdefault(event_type, severity)
        security_events.register_event_allowlist(event_type, fields)
    return security_events


def _audit(tenant_id: str, event_type: str, details: dict) -> dict:
    """Write one chained record to the tenant's core audit chain.

    Raises on failure (fail closed) and when the metadata floor dropped a
    field — a record that silently lost fields is not the record we meant.
    """
    se = _security_events()
    severity = _CRON_EVENTS[event_type][0]
    record = se.write_event(
        tenant_audit_chain(tenant_id),
        event_type,
        severity=severity,
        tool="skill_forge.cron",
        # No ``tenant_id`` in details: the chain FILE is the tenant's own
        # (tenant_audit_chain(tenant_id)), and the core writer refuses a
        # details.tenant_id that differs from the PROCESS tenant — which a
        # poller iterating every tenant in one process would hit for all but
        # one of them (AuditTenantMismatch).
        details=details,
    )
    dropped = (record.get("details") or {}).get("_dropped_fields")
    if dropped:
        raise RuntimeError(f"audit floor dropped {dropped} from {event_type}")
    return record


class CronTriggerPoller:
    """Poll for Skill loss signals on a scheduled interval.

    Constraints (load-bearing):
    - Reads audit trail only (no mutations)
    - Tenant isolation: each tenant polled independently
    - Fail-closed: exception → log, don't crash, retry next hour
    - Configurable: poll interval, autonomy flag, alert channels
    """

    def __init__(self):
        """Initialize poller with default settings."""
        self.detector = SkillLossTriggerDetector()
        self._last_poll_time: Optional[float] = None
        self._last_poll_count: int = 0

    def run_once(self, tenant_id: str) -> List[LossTrigger]:
        """Poll TriggerDetector for a single tenant.

        Args:
            tenant_id: Tenant identifier (validated)

        Returns:
            List[LossTrigger]: Loss signals detected for this tenant

        Side effects:
            - Emits audit event: skill_forge_triggered_by_cron
            - If autonomous_forge_enabled: triggers forge (Phase 7)
            - If disabled: logs event only
        """
        try:
            validate_tenant_id(tenant_id)
        except ValueError as e:
            logger.error(f"Invalid tenant ID for polling: {e}")
            return []

        logger.debug(f"Polling tenant {tenant_id} for loss signals...")

        try:
            # Detect loss signals
            triggers = self.detector.detect_loss_signals(tenant_id)

            if not triggers:
                logger.debug(f"No loss signals for tenant {tenant_id}")
                return []

            # Log each trigger as an audit event
            for trigger in triggers:
                self._emit_loss_alert(tenant_id, trigger)

            return triggers

        except Exception as e:
            logger.error(
                f"Error polling tenant {tenant_id}: {type(e).__name__}: {e}",
                exc_info=True,
            )
            return []

    def poll_all_tenants(self) -> int:
        """Poll all active tenants for loss signals.

        Returns:
            int: Total number of loss triggers across all tenants

        Side effects:
            - Updates _last_poll_time and _last_poll_count
            - Emits audit events for each trigger
        """
        logger.info("Starting cron poll of all tenants for loss signals...")
        self._last_poll_time = time.time()
        self._last_poll_count = 0

        # Get list of active tenants
        tenants = self._list_active_tenants()
        if not tenants:
            logger.warning("No active tenants found; skipping poll")
            return 0

        logger.debug(f"Polling {len(tenants)} active tenants")

        for tenant_id in tenants:
            triggers = self.run_once(tenant_id)
            self._last_poll_count += len(triggers)

        logger.info(
            f"Cron poll complete: {self._last_poll_count} loss signals "
            f"across {len(tenants)} tenants"
        )
        return self._last_poll_count

    def _emit_loss_alert(self, tenant_id: str, trigger: LossTrigger) -> None:
        """Emit audit event and optional forge trigger on loss signal.

        Args:
            tenant_id: Tenant identifier
            trigger: Loss signal (skill confidence < 0.70)

        Side effects:
            - Writes audit event: skill_forge_triggered_by_cron
            - If autonomous_forge_enabled: calls _trigger_forge()
        """
        # Emit audit event
        self._write_audit_event(tenant_id, trigger)

        # Check if autonomous forge is enabled
        if self._should_trigger_forge(tenant_id):
            logger.info(
                f"Autonomous forge enabled for tenant {tenant_id}; "
                f"triggering optimizer for skill {trigger.skill_id}"
            )
            self._trigger_forge(tenant_id, trigger)
        else:
            logger.debug(
                f"Autonomous forge disabled for tenant {tenant_id}; "
                f"loss signal logged but forge not triggered"
            )

    def _write_audit_event(self, tenant_id: str, trigger: LossTrigger) -> None:
        """Write the chained skill_forge_triggered_by_cron audit event.

        Raises on failure: the caller (run_once) then triggers nothing.
        """
        _audit(tenant_id, "skill_forge_triggered_by_cron", {
            "skill_id": trigger.skill_id,
            "skill_version": trigger.version,
            "confidence": trigger.confidence,
            "event_count": trigger.event_count,
            "lookback_hours": trigger.lookback_hours,
            "trigger_time": trigger.trigger_time.isoformat(),
        })
        logger.debug(
            f"Audit event written for skill {trigger.skill_id} "
            f"(confidence={trigger.confidence:.2f})"
        )

    def _should_trigger_forge(self, tenant_id: str) -> bool:
        """Check if autonomous forge is enabled for tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            bool: True if autonomous_forge_enabled in config, else False
        """
        config_path = self._get_config_path(tenant_id)
        if not config_path.exists():
            logger.debug(f"No config file found for tenant {tenant_id}")
            return False

        try:
            with open(config_path, "r") as f:
                config = json.load(f)

            autonomous_forge = config.get("autonomous_forge", {})
            enabled = autonomous_forge.get("enabled", False)

            logger.debug(
                f"Autonomous forge enabled={enabled} for tenant {tenant_id}"
            )
            return enabled

        except Exception as e:
            logger.error(
                f"Error reading config for tenant {tenant_id}: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            return False

    def _trigger_forge(self, tenant_id: str, trigger: LossTrigger) -> None:
        """Trigger Skill Forge optimizer (Phase 7 flow).

        Args:
            tenant_id: Tenant identifier
            trigger: Loss signal

        Side effects:
            - Writes skill_forge_triggered_by_optimizer file
            - Logs event to audit trail
        """
        try:
            # Audit FIRST: no trigger file without its committed record.
            self._write_forge_triggered_event(tenant_id, trigger)

            # Write trigger signal file that optimizer picks up
            trigger_dir = (
                corvin_home()
                / "tenants"
                / tenant_id
                / "global"
                / "skill-forge"
                / "triggers"
            )
            trigger_dir.mkdir(parents=True, exist_ok=True)

            # Serialise BEFORE opening: ``trigger_time`` is a datetime, and
            # json.dump used to raise mid-write, leaving a truncated file
            # and no trigger (the TypeError was swallowed below).
            # skill_id comes from audit data and becomes a file name.
            if not _SAFE_SKILL_ID.match(trigger.skill_id or ""):
                raise ValueError(f"unsafe skill_id for trigger file: {trigger.skill_id!r}")
            trigger_file = trigger_dir / f"{trigger.skill_id}.json"
            payload = asdict(trigger)
            payload["trigger_time"] = trigger.trigger_time.isoformat()
            trigger_file.write_text(json.dumps(payload))

            logger.info(
                f"Skill forge triggered for {trigger.skill_id} "
                f"in tenant {tenant_id}"
            )

        except Exception as e:
            logger.error(
                f"Failed to trigger forge: {type(e).__name__}: {e}",
                exc_info=True,
            )

    def _write_forge_triggered_event(
        self, tenant_id: str, trigger: LossTrigger
    ) -> None:
        """Write skill_forge_optimizer_triggered audit event.

        Args:
            tenant_id: Tenant identifier
            trigger: Loss signal
        """
        _audit(tenant_id, "skill_forge_optimizer_triggered", {
            "skill_id": trigger.skill_id,
            "skill_version": trigger.version,
            "confidence": trigger.confidence,
        })

    def _get_config_path(self, tenant_id: str) -> Path:
        """Get path to tenant autonomous_forge.yaml config.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Path to config file (may not exist)
        """
        return (
            corvin_home()
            / "tenants"
            / tenant_id
            / "global"
            / "autonomous_forge.yaml"
        )

    def _list_active_tenants(self) -> List[str]:
        """List all active tenants by scanning directory structure.

        Returns:
            List[str]: Tenant IDs found in ~/.corvin/tenants/

        Default: At least returns ["_default"] if it exists.
        """
        tenants_dir = corvin_home() / "tenants"
        tenants = []

        if not tenants_dir.exists():
            logger.warning(f"Tenants directory not found: {tenants_dir}")
            return []

        try:
            for entry in tenants_dir.iterdir():
                if entry.is_dir():
                    tenant_id = entry.name
                    try:
                        validate_tenant_id(tenant_id)
                        tenants.append(tenant_id)
                    except ValueError:
                        logger.debug(
                            f"Skipping invalid tenant ID: {tenant_id}"
                        )
        except Exception as e:
            logger.error(
                f"Error listing tenants: {type(e).__name__}: {e}",
                exc_info=True,
            )

        return tenants

    def get_status(self) -> dict:
        """Get poller status (last poll time, count, enabled).

        Returns:
            dict: Status information
        """
        return {
            "last_poll_time": self._last_poll_time,
            "last_poll_count": self._last_poll_count,
            "enabled": True,
        }
