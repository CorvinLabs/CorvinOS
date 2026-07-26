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
import threading
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
RESTART_GRACE_S = 60

#: Cap on per-plugin action locks so an unknown id cannot grow the map.
MAX_PLUGIN_LOCKS = 1024.0
#: How long a record stays in the per-plugin history.
HISTORY_WINDOW_S = 3600.0
#: Actions that consume the hourly budget. ESCALATE is deliberately NOT one: it is
#: the orchestrator saying "I am no longer acting", so counting it would let the
#: budget inflate itself, and it must not be mistaken for a healing attempt.
BUDGETED_ACTIONS = frozenset(
    {HealingAction.CIRCUIT_BREAK, HealingAction.SOFT_RESTART, HealingAction.DISABLE}
)

#: Hard cap per plugin regardless of age. The history is a diagnostic buffer in a
#: process that runs for months: with the flag OFF every poll on an unhealthy plugin
#: appends a "healing_disabled" NOOP, and pruning only on budget_left() — which
#: NOOPs never reach — grew it without bound (measured: 5000 entries from 5000
#: considerations). Keep the recent tail, drop the rest.
MAX_HISTORY_PER_PLUGIN = 64


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
        #: Timestamps of REAL actions, kept separately from the diagnostic history.
        #: The budget must not be derivable from a buffer that gets pruned for size:
        #: enough NOOPs would push the counted actions out of the window and hand
        #: back a fresh budget — two correct mechanisms combining into a hole.
        #: Actions are inherently bounded (max_heals_per_hour), so this cannot grow.
        self._actions: Dict[str, List[float]] = {}
        self._last_restart: Dict[str, float] = {}
        #: Plugins already escalated. An escalation repeats on every poll otherwise,
        #: which floods the audit chain with the same "I gave up" record — the same
        #: mistake the health alert avoids by firing once per streak.
        self._escalated: set[str] = set()
        #: Guards the ledgers. Held only for dict/set access, NEVER across a call
        #: into a plugin — otherwise a slow on_unload() would block the Console's
        #: history() read and the plugins page would appear to hang.
        self._state_lock = threading.RLock()
        #: One lock per plugin, held for the whole decide-and-act path.
        #:
        #: HONEST STATUS: this is defence in depth, NOT a fix for a reproduced bug.
        #: Concurrent consider() calls for one plugin were measured against the
        #: pre-lock code and did NOT overrun the budget or double-restart — because
        #: PluginRegistry.unregister() removes the entry under its OWN lock before
        #: calling on_unload(), so every later thread hits PluginNotFound. That is
        #: real protection, but it is INCIDENTAL: it lives in another module, applies
        #: only to the paths that go through unregister(), and would disappear with a
        #: refactor there that nobody would connect to healing. The ledger mutations
        #: are the part that was genuinely unsynchronised — _recent() and
        #: budget_left() are read-modify-write sequences on shared dicts, and the
        #: collector thread is not the only caller (Console sync routes run in a
        #: threadpool). This makes the invariant explicit and local.
        self._plugin_locks: Dict[str, threading.RLock] = {}
        self._shared_action_lock = threading.RLock()

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

    def _action_lock(self, plugin_id: str) -> threading.RLock:
        """Per-plugin action lock, with a cap so unknown ids cannot grow the map."""
        with self._state_lock:
            lock = self._plugin_locks.get(plugin_id)
            if lock is None:
                if len(self._plugin_locks) >= MAX_PLUGIN_LOCKS:
                    return self._shared_action_lock
                lock = threading.RLock()
                self._plugin_locks[plugin_id] = lock
            return lock

    def _recent(
        self, plugin_id: str, window_s: float = HISTORY_WINDOW_S
    ) -> List[HealingRecord]:
        with self._state_lock:
            cutoff = time.time() - window_s
            recent = [r for r in self._history.get(plugin_id, []) if r.at >= cutoff]
            if len(recent) > MAX_HISTORY_PER_PLUGIN:
                recent = recent[-MAX_HISTORY_PER_PLUGIN:]
            self._history[plugin_id] = recent
            return recent

    def budget_left(self, plugin_id: str) -> int:
        """Actions still allowed this hour.  Counted from :attr:`_actions`.

        Deliberately NOT derived from the pruned history — see the note there.
        """
        with self._state_lock:
            cutoff = time.time() - HISTORY_WINDOW_S
            recent = [t for t in self._actions.get(plugin_id, []) if t >= cutoff]
            self._actions[plugin_id] = recent
            return max(0, self.max_heals_per_hour - len(recent))

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
        # The cheap rejections need no lock and are the common case on every poll.
        if not self.is_enabled():
            return self._record(plugin_id, HealingAction.NOOP, "healing_disabled")
        if consecutive_failures < self.threshold:
            return self._record(plugin_id, HealingAction.NOOP, "below_threshold")
        with self._action_lock(plugin_id):
            return self._consider_locked(plugin_id, plugin_type, error_code)

    def _consider_locked(
        self, plugin_id: str, plugin_type: str, error_code: str
    ) -> HealingRecord:
        policy = self.policy_for(plugin_id, plugin_type)
        if policy is HealingPolicy.NONE:
            return self._record(plugin_id, HealingAction.NOOP, "policy_none")

        if self.budget_left(plugin_id) <= 0:
            # A plugin that keeps failing is a logic error, not a transient one.
            # Escalate once and stop acting.
            return self._escalate(plugin_id, "heal_budget_exhausted")

        # A failure right after a restart means the restart did not help.
        last = self._last_restart.get(plugin_id)
        if last is not None and (time.time() - last) < RESTART_GRACE_S:
            return self._escalate(plugin_id, "restart_did_not_help")

        if policy is HealingPolicy.CIRCUIT_BREAK_ONLY:
            return self._circuit_break(plugin_id, error_code)
        if policy is HealingPolicy.SOFT_RESTART:
            return self._soft_restart(plugin_id, error_code)
        return self._disable_and_degrade(plugin_id, error_code)

    def _escalate(self, plugin_id: str, reason: str) -> HealingRecord:
        """Escalate at most once per plugin until it acts or recovers again."""
        with self._state_lock:
            if plugin_id in self._escalated:
                return self._record(
                    plugin_id, HealingAction.NOOP, f"already_escalated:{reason}"
                )
            self._escalated.add(plugin_id)
        return self._record(plugin_id, HealingAction.ESCALATE, reason)

    def note_recovered(self, plugin_id: str) -> None:
        """Clear the escalation latch — called when the plugin reports healthy."""
        with self._state_lock:
            self._escalated.discard(plugin_id)

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
        with self._state_lock:
            self._record_locked(plugin_id, action, rec)
        # Log and audit OUTSIDE the lock: the audit sink is I/O.
        if action is not HealingAction.NOOP:
            log.warning(
                "healing action %s for %r (reason=%s, ok=%s)",
                action.value, plugin_id, reason, succeeded,
            )
            self._emit_audit("plugin.healing_action", rec.to_dict())
        return rec

    def _record_locked(
        self, plugin_id: str, action: HealingAction, rec: "HealingRecord"
    ) -> None:
        history = self._history.setdefault(plugin_id, [])
        history.append(rec)
        # Prune HERE, not only in budget_left(): the NOOP paths never reach that
        # method, and they are the ones that fire on every poll.
        if len(history) > MAX_HISTORY_PER_PLUGIN:
            self._recent(plugin_id)
        if action in BUDGETED_ACTIONS:
            # Count it against the hourly budget in its own, unpruned ledger.
            self._actions.setdefault(plugin_id, []).append(rec.at)
            self._escalated.discard(plugin_id)  # a real action re-arms escalation
        # Audit EVERY non-NOOP, including ESCALATE (see _record): the budget question
        # and the visibility question are separate — an escalation consumes no
        # budget, but "I gave up on this plugin" is the single most important thing
        # for an operator to see. Folding the two conditions together (as an earlier
        # edit here did) silently dropped that record.

    def _emit_audit(self, event_type: str, details: dict) -> None:
        if self._audit_emit is None:
            return
        try:
            self._audit_emit(event_type, details)
        except Exception as exc:  # noqa: BLE001
            log.error("healing audit emit failed (%s)", type(exc).__name__)


__all__ = [
    "BUDGETED_ACTIONS",
    "DEFAULT_MAX_HEALS_PER_HOUR",
    "HISTORY_WINDOW_S",
    "MAX_HISTORY_PER_PLUGIN",
    "DEFAULT_POLICY_BY_TYPE",
    "DEFAULT_THRESHOLD",
    "HealingAction",
    "HealingOrchestrator",
    "HealingPolicy",
    "HealingRecord",
    "RESTART_GRACE_S",
]
