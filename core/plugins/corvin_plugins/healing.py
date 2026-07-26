"""Self-healing orchestrator for sick plugins (ADR-0231 Stage 3).

**Ships dark.** ADR-0231 approves Stages 1–2 and says Stage 3 is "to be
re-proposed after Stage 2 is stable (1+ release in production)". The code lives
here behind the ``plugin_self_healing`` flag, default off, which is how this repo
honours a gate like that: the mechanism exists and is testable, and an operator
turns it on deliberately once they have the production evidence the ADR asks for.

The ADR's constraints are the design, not suggestions:

* **Only reversible actions.** Level 1 circuit-break, level 2 soft-restart
  (``on_unload`` → ``on_load``), level 3 disable-and-degrade. Explicitly
  **never** a hard kill, a force-delete, or any data mutation.
* **Per-plugin policy.** ``circuit_break_only`` for anything precious (the audit
  backend never gets restarted autonomously), ``soft_restart`` where a restart is
  safe, ``disable_and_degrade`` where the platform runs fine without the plugin.
* **Bounded.** At most ``max_heals_per_hour`` actions per plugin, so a plugin that
  fails on every load cannot become a restart loop.
* **Only transient failures.** A plugin whose restart is immediately followed by
  another failure escalates once and then stops — repeated identical failures are
  a logic error, and healing a logic error just hides it.
* **Every action is audited** with the plugin id, the action and the reason code.
  Healing is visible or it is not allowed to happen.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from . import circuit_breaker as _breakers

log = logging.getLogger("corvin.plugins.healing")


class HealingPolicy(str, Enum):
    """What is allowed to happen to a given plugin."""

    #: Contain only. Never restarted autonomously — for audit and anything else
    #: where a restart could lose in-flight state.
    CIRCUIT_BREAK_ONLY = "circuit_break_only"
    #: on_unload() then on_load(). Safe where the plugin holds no durable state.
    SOFT_RESTART = "soft_restart"
    #: Disable the plugin and keep running without it (the degrade path).
    DISABLE_AND_DEGRADE = "disable_and_degrade"
    #: Explicitly opt a plugin out of healing entirely.
    NONE = "none"


class HealingAction(str, Enum):
    CIRCUIT_BREAK = "circuit_break"
    SOFT_RESTART = "soft_restart"
    DISABLE = "disable"
    ESCALATE = "escalate"
    NOOP = "noop"


#: Default policy per plugin TYPE. Conservative by construction: a type whose
#: failure could cost data or compliance evidence gets containment only.
DEFAULT_POLICY_BY_TYPE: Dict[str, HealingPolicy] = {
    "audit_backend": HealingPolicy.CIRCUIT_BREAK_ONLY,   # audit is precious
    "user_backend": HealingPolicy.CIRCUIT_BREAK_ONLY,    # auth: never auto-restart
    "compute_engine": HealingPolicy.CIRCUIT_BREAK_ONLY,  # may hold a running job
    "stt_provider": HealingPolicy.SOFT_RESTART,
    "summary_provider": HealingPolicy.SOFT_RESTART,
    "notification_backend": HealingPolicy.SOFT_RESTART,
    "recall_backend": HealingPolicy.CIRCUIT_BREAK_ONLY,   # owns a database
    "router_backend": HealingPolicy.DISABLE_AND_DEGRADE,  # native routing is fine
    "worker_engine": HealingPolicy.DISABLE_AND_DEGRADE,   # degrade ladder ends native
    "bridge_channel": HealingPolicy.CIRCUIT_BREAK_ONLY,
    "data_connector": HealingPolicy.CIRCUIT_BREAK_ONLY,
}

#: Failures before healing is considered at all.
DEFAULT_THRESHOLD = 3
#: Ceiling on autonomous actions per plugin per hour.
DEFAULT_MAX_HEALS_PER_HOUR = 3
#: A restart followed by a failure within this window counts as "did not help".
RESTART_GRACE_S = 60.0


@dataclass
class HealingRecord:
    plugin_id: str
    action: HealingAction
    reason: str
    at: float = field(default_factory=time.time)
    succeeded: bool = True

    def to_dict(self) -> dict:
        return {
            "plugin_id": self.plugin_id,
            "healing_action": self.action.value,
            "reason": self.reason,
            "succeeded": self.succeeded,
        }


class HealingOrchestrator:
    """Decides and applies a healing action for an unhealthy plugin.

    Stateless with respect to health: it is *told* about a failing plugin (by the
    health collector) rather than polling, so there is exactly one poller in the
    system.
    """

    def __init__(
        self,
        *,
        enabled: Callable[[], bool] | bool = False,
        policies: Optional[Dict[str, HealingPolicy]] = None,
        threshold: int = DEFAULT_THRESHOLD,
        max_heals_per_hour: int = DEFAULT_MAX_HEALS_PER_HOUR,
        audit_emit: Optional[Callable[[str, dict], None]] = None,
    ):
        self._enabled = enabled
        self._policies = dict(policies or {})
        self.threshold = threshold
        self.max_heals_per_hour = max_heals_per_hour
        self._audit_emit = audit_emit
        self._history: Dict[str, List[HealingRecord]] = {}
        self._last_restart: Dict[str, float] = {}

    # ── policy ───────────────────────────────────────────────────────────────

    def policy_for(self, plugin_id: str, plugin_type: str = "") -> HealingPolicy:
        """Per-plugin override wins over the per-type default; unknown → contain."""
        if plugin_id in self._policies:
            return self._policies[plugin_id]
        return DEFAULT_POLICY_BY_TYPE.get(plugin_type, HealingPolicy.CIRCUIT_BREAK_ONLY)

    def set_policy(self, plugin_id: str, policy: HealingPolicy) -> None:
        self._policies[plugin_id] = policy

    def is_enabled(self) -> bool:
        return self._enabled() if callable(self._enabled) else bool(self._enabled)

    # ── budget ───────────────────────────────────────────────────────────────

    def _recent(self, plugin_id: str, window_s: float = 3600.0) -> List[HealingRecord]:
        cutoff = time.time() - window_s
        recent = [r for r in self._history.get(plugin_id, []) if r.at >= cutoff]
        self._history[plugin_id] = recent
        return recent

    def budget_left(self, plugin_id: str) -> int:
        acted = [r for r in self._recent(plugin_id) if r.action is not HealingAction.NOOP]
        return max(0, self.max_heals_per_hour - len(acted))

    def history(self, plugin_id: str) -> List[HealingRecord]:
        return list(self._recent(plugin_id))

    # ── the decision ─────────────────────────────────────────────────────────

    def consider(
        self,
        plugin_id: str,
        *,
        plugin_type: str = "",
        consecutive_failures: int = 0,
        error_code: str = "",
    ) -> HealingRecord:
        """Decide and apply.  Returns what happened; never raises.

        Every early return is itself recorded as a NOOP with a reason, so "why did
        nothing happen" is answerable from the history rather than from the source.
        """
        if not self.is_enabled():
            return self._record(plugin_id, HealingAction.NOOP, "healing_disabled")
        if consecutive_failures < self.threshold:
            return self._record(plugin_id, HealingAction.NOOP, "below_threshold")

        policy = self.policy_for(plugin_id, plugin_type)
        if policy is HealingPolicy.NONE:
            return self._record(plugin_id, HealingAction.NOOP, "policy_none")

        if self.budget_left(plugin_id) <= 0:
            # A plugin that keeps failing is a logic error, not a transient one.
            # Escalate once and stop acting.
            return self._record(
                plugin_id, HealingAction.ESCALATE, "heal_budget_exhausted"
            )

        # A failure right after a restart means the restart did not help.
        last = self._last_restart.get(plugin_id)
        if last is not None and (time.time() - last) < RESTART_GRACE_S:
            return self._record(
                plugin_id, HealingAction.ESCALATE, "restart_did_not_help"
            )

        if policy is HealingPolicy.CIRCUIT_BREAK_ONLY:
            return self._circuit_break(plugin_id, error_code)
        if policy is HealingPolicy.SOFT_RESTART:
            return self._soft_restart(plugin_id, error_code)
        return self._disable_and_degrade(plugin_id, error_code)

    # ── the three reversible actions ─────────────────────────────────────────

    def _circuit_break(self, plugin_id: str, error_code: str) -> HealingRecord:
        """Level 1: refuse calls for the cooldown.  Nothing is destroyed."""
        breaker = _breakers.get_breaker(plugin_id)
        breaker.record_failure()  # pushes it to OPEN at/above threshold
        return self._record(
            plugin_id, HealingAction.CIRCUIT_BREAK, error_code or "unhealthy"
        )

    def _soft_restart(self, plugin_id: str, error_code: str) -> HealingRecord:
        """Level 2: on_unload() then on_load() with the SAME context.

        Reversible: a failed restart leaves the plugin unregistered and the breaker
        open, which is the level-1 state — never a half-initialised plugin still
        receiving traffic.
        """
        from .registry import get_registry

        registry = get_registry()
        try:
            plugin = registry.get(plugin_id)
            ctx = registry._contexts.get(plugin_id)  # same context, by design
        except Exception:  # noqa: BLE001
            return self._record(
                plugin_id, HealingAction.NOOP, "not_registered", succeeded=False
            )

        try:
            registry.unregister(plugin_id)
        except Exception as exc:  # noqa: BLE001
            log.error("soft-restart unload of %r failed (%s)", plugin_id, type(exc).__name__)
            return self._record(
                plugin_id, HealingAction.SOFT_RESTART, "unload_failed", succeeded=False
            )

        if ctx is None:
            return self._record(
                plugin_id, HealingAction.SOFT_RESTART, "no_context", succeeded=False
            )

        try:
            registry.register(plugin, ctx)
        except Exception as exc:  # noqa: BLE001
            log.error("soft-restart load of %r failed (%s)", plugin_id, type(exc).__name__)
            _breakers.get_breaker(plugin_id).record_failure(exc)
            return self._record(
                plugin_id, HealingAction.SOFT_RESTART, "load_failed", succeeded=False
            )

        self._last_restart[plugin_id] = time.time()
        _breakers.get_breaker(plugin_id).reset()
        return self._record(
            plugin_id, HealingAction.SOFT_RESTART, error_code or "unhealthy"
        )

    def _disable_and_degrade(self, plugin_id: str, error_code: str) -> HealingRecord:
        """Level 3: unregister and detach the provider slot; the platform degrades.

        Reversible by an operator re-enabling the plugin. The registry record on
        disk is deliberately NOT touched: an autonomous action must not rewrite the
        operator's configuration.
        """
        from .registry import get_registry

        registry = get_registry()
        try:
            plugin = registry.get(plugin_id)
            plugin_type = getattr(plugin, "plugin_type", "")
        except Exception:  # noqa: BLE001
            return self._record(
                plugin_id, HealingAction.NOOP, "not_registered", succeeded=False
            )

        try:
            registry.unregister(plugin_id)
        except Exception as exc:  # noqa: BLE001
            log.error("disable of %r failed (%s)", plugin_id, type(exc).__name__)
            return self._record(
                plugin_id, HealingAction.DISABLE, "unregister_failed", succeeded=False
            )

        try:
            from .state import _detach_providers

            _detach_providers(plugin_type)
        except Exception as exc:  # noqa: BLE001
            log.error("provider detach failed (%s)", type(exc).__name__)

        return self._record(plugin_id, HealingAction.DISABLE, error_code or "unhealthy")

    # ── bookkeeping ──────────────────────────────────────────────────────────

    def _record(
        self,
        plugin_id: str,
        action: HealingAction,
        reason: str,
        *,
        succeeded: bool = True,
    ) -> HealingRecord:
        rec = HealingRecord(
            plugin_id=plugin_id, action=action, reason=reason, succeeded=succeeded
        )
        self._history.setdefault(plugin_id, []).append(rec)
        if action is not HealingAction.NOOP:
            log.warning(
                "healing action %s for %r (reason=%s, ok=%s)",
                action.value, plugin_id, reason, succeeded,
            )
            self._emit_audit("plugin.healing_action", rec.to_dict())
        return rec

    def _emit_audit(self, event_type: str, details: dict) -> None:
        if self._audit_emit is None:
            return
        try:
            self._audit_emit(event_type, details)
        except Exception as exc:  # noqa: BLE001
            log.error("healing audit emit failed (%s)", type(exc).__name__)


__all__ = [
    "DEFAULT_MAX_HEALS_PER_HOUR",
    "DEFAULT_POLICY_BY_TYPE",
    "DEFAULT_THRESHOLD",
    "HealingAction",
    "HealingOrchestrator",
    "HealingPolicy",
    "HealingRecord",
    "RESTART_GRACE_S",
]
