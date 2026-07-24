"""ADR-0214: Loss Profile Tracker (In-Session Learning).

Tracks actual loss from delegated steps to learn delegation patterns.
Post-hoc measurement: After execution, compare local vs delegated output.

Features:
- In-session only (reset on new session)
- Model-ID keying (detect when model changes)
- Exponential decay weighting (half-life 7 days) + pruning of dead entries
- Separates measured loss from proxy-derived loss (proxy entries carry
  lower evidence weight so fabricated defaults can't dominate learning)

ADR-0215 F4: ``get_session_tracker()`` used to be a single process-wide
singleton with no session/tenant key, directly contradicting the "In-session
only" claim above — one tenant's delegation-quality evidence silently
influenced every other concurrent tenant's delegation decisions in the same
process (a real ADR-0007 tenant-isolation bug, not just a docstring lie).
Fixed: the module now keeps a *keyed* registry of trackers
(``session_key -> LossProfileTracker``), bounded by a simple LRU eviction so
long-running processes with many short-lived sessions don't grow this dict
without limit. Callers that don't pass a ``session_key`` fall back to the
literal string ``"default"`` — this preserves old single-tenant/CLI behavior
byte-for-byte, it just stops silently mixing keyed and unkeyed callers.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

_logger = logging.getLogger(__name__)


@dataclass
class LossEntry:
    """Single loss measurement."""
    timestamp: float
    task_type: str
    model_id: str
    loss_pct: float  # 0-100
    engine: str  # Which engine was chosen
    complexity: str = "moderate"
    measured: bool = True  # True = real local-vs-remote comparison, False = proxy
    alternative_scores: dict[str, float] = field(default_factory=dict)


class LossProfileTracker:
    """Track and learn from actual delegation outcomes."""

    # Configuration
    MAX_ENTRIES = 1000
    DECAY_HALF_LIFE_DAYS = 7
    # Prune entries older than this many half-lives (weight < ~6%).
    PRUNE_AFTER_HALF_LIVES = 4
    # Conservative: 10% default loss until we have data (matches ADR-0214 and
    # RobustEngineDetector's no-history default).
    DEFAULT_LOSS_PCT = 10.0
    # Minimum evidence mass before estimates leave the conservative default.
    MIN_SAMPLES = 5
    # Proxy entries count less than measured ones.
    PROXY_WEIGHT = 0.25

    def __init__(self, model_id: str = "default"):
        """Initialize tracker.

        Args:
            model_id: Current model (e.g., "claude-sonnet-5"). Used to detect model changes.
        """
        self.history: list[LossEntry] = []
        self.current_model_id = model_id

    def record_delegation_result(
        self,
        task_type: str,
        engine: str,
        loss_pct: float,
        complexity: str = "moderate",
        measured: bool = True,
        alternative_scores: Optional[dict[str, float]] = None,
    ):
        """Record a delegation outcome.

        Args:
            task_type: Task classification (code_generation, etc)
            engine: Which engine was chosen
            loss_pct: Measured quality loss (0-100)
            complexity: Task complexity bucket (simple/moderate/complex)
            measured: True for real local-vs-remote comparison, False for proxy
            alternative_scores: Softmax scores of other engines (for off-policy learning)
        """

        entry = LossEntry(
            timestamp=time.time(),
            task_type=task_type,
            model_id=self.current_model_id,
            loss_pct=max(0.0, min(100.0, float(loss_pct))),
            engine=engine,
            complexity=complexity,
            measured=measured,
            alternative_scores=alternative_scores or {},
        )

        self.history.append(entry)

        # Eviction if over limit: drop the OLDEST PROXY entry first — the
        # rare measured (shadow-run) entries are the valuable signal and must
        # not be flushed out by a flood of proxy records (round-2 finding).
        if len(self.history) > self.MAX_ENTRIES:
            for i, e in enumerate(self.history):
                if not e.measured:
                    self.history.pop(i)
                    break
            else:
                self.history.pop(0)

        _logger.debug(
            "Recorded loss: %s / %s / %.1f%% / measured=%s / model=%s",
            task_type, engine, entry.loss_pct, measured, self.current_model_id,
        )

    def record_via_proxy(
        self,
        task_type: str,
        engine: str,
        schema_valid: bool,
        downstream_ok: bool,
        complexity: str = "moderate",
    ):
        """Record outcome via proxy metrics (not actual loss measurement).

        Used for the ~95% of delegations without a shadow-run, to avoid 100%
        measurement overhead. Proxy entries are down-weighted in estimates
        (PROXY_WEIGHT) so they can't drown out real measurements.

        Args:
            task_type: Task classification
            engine: Engine chosen
            schema_valid: Did output pass schema validation?
            downstream_ok: Did downstream steps succeed?
            complexity: Task complexity bucket
        """

        # Proxy loss: assume 1% if schema OK and downstream OK, else 10%
        loss_pct = 1.0 if (schema_valid and downstream_ok) else 10.0

        self.record_delegation_result(
            task_type=task_type,
            engine=engine,
            loss_pct=loss_pct,
            complexity=complexity,
            measured=False,
        )

    def estimate_loss_for_task_type(
        self, task_type: str, complexity: str = "moderate", engine: Optional[str] = None
    ) -> float:
        """
        Estimate loss for a task type (used in detection and delegation gates).

        Exponentially-decayed weighted average over entries matching
        (task_type, model_id[, engine]); complexity narrows the match when
        enough same-complexity samples exist. Proxy entries carry PROXY_WEIGHT.

        Args:
            engine: When given, only entries for that engine count — a
                learned claude_code outcome must not masquerade as evidence
                about TDE delegation quality.

        Returns:
            Estimated loss as fraction (0.0-1.0). DEFAULT until enough evidence.
        """

        self._prune_history()

        relevant = [
            e for e in self.history
            if e.task_type == task_type and e.model_id == self.current_model_id
            and (engine is None or e.engine == engine)
        ]

        # Prefer complexity-matched subset when it has enough evidence on its own.
        same_complexity = [e for e in relevant if e.complexity == complexity]
        if self._evidence_mass(same_complexity) >= self.MIN_SAMPLES:
            relevant = same_complexity

        if self._evidence_mass(relevant) < self.MIN_SAMPLES:
            return self.DEFAULT_LOSS_PCT / 100.0

        now = time.time()
        half_life_sec = self.DECAY_HALF_LIFE_DAYS * 86400.0
        num = 0.0
        den = 0.0
        for e in relevant:
            age_weight = 0.5 ** ((now - e.timestamp) / half_life_sec)
            w = age_weight * (1.0 if e.measured else self.PROXY_WEIGHT)
            num += w * e.loss_pct
            den += w

        if den <= 0.0:
            return self.DEFAULT_LOSS_PCT / 100.0
        return (num / den) / 100.0

    def _evidence_mass(self, entries: list[LossEntry]) -> float:
        """Effective sample count: measured=1, proxy=PROXY_WEIGHT."""
        return sum(1.0 if e.measured else self.PROXY_WEIGHT for e in entries)

    def evidence_for(
        self, task_type: str, complexity: str = "moderate", engine: Optional[str] = None
    ) -> float:
        """Evidence mass backing estimate_loss_for_task_type().

        Lets callers (RobustEngineDetector, delegation gate) distinguish a
        LEARNED 10% loss from the conservative no-evidence DEFAULT of 10%.
        Mirrors estimate_loss_for_task_type()'s selection exactly: prefers the
        complexity-matched subset when that subset alone carries enough
        evidence (round-2 finding: ignoring complexity here let simple-task
        samples unlock delegation for complex steps).
        """
        self._prune_history()
        relevant = [
            e for e in self.history
            if e.task_type == task_type and e.model_id == self.current_model_id
            and (engine is None or e.engine == engine)
        ]
        same_complexity = [e for e in relevant if e.complexity == complexity]
        if self._evidence_mass(same_complexity) >= self.MIN_SAMPLES:
            return self._evidence_mass(same_complexity)
        return self._evidence_mass(relevant)

    def _prune_history(self):
        """Drop entries so old their decay weight is negligible."""
        now = time.time()
        max_age = self.DECAY_HALF_LIFE_DAYS * 86400.0 * self.PRUNE_AFTER_HALF_LIVES
        self.history = [e for e in self.history if (now - e.timestamp) < max_age]

    def set_model(self, model_id: str):
        """Update model (e.g., after an upgrade from Sonnet to Fable)."""
        self.current_model_id = model_id
        _logger.info(f"Loss profile: updated model_id to {model_id}")

    def stats(self) -> dict[str, Any]:
        """Return stats about learning."""
        self._prune_history()

        by_task_type: dict[str, list[float]] = {}
        for entry in self.history:
            by_task_type.setdefault(entry.task_type, []).append(entry.loss_pct)

        return {
            "total_measurements": len(self.history),
            "measured_count": sum(1 for e in self.history if e.measured),
            "proxy_count": sum(1 for e in self.history if not e.measured),
            "avg_loss_by_task_type": {
                k: sum(v) / len(v) for k, v in by_task_type.items()
            },
            "model_id": self.current_model_id,
            "measurements_this_model": sum(
                1 for e in self.history if e.model_id == self.current_model_id
            ),
        }

    def clear(self):
        """Clear all history."""
        self.history = []
        _logger.info("Loss profile cleared")


# Keyed registry: in-session learning must survive per-request construction
# of SendIntegration / engines (F26), but MUST NOT bleed across sessions or
# tenants (ADR-0215 F4). Bounded LRU — see module docstring.
_MAX_SESSION_TRACKERS = 500
_session_trackers: "OrderedDict[str, LossProfileTracker]" = OrderedDict()


def get_session_tracker(
    model_id: str = "default", session_key: str = "default"
) -> LossProfileTracker:
    """Get or create the loss tracker for ``session_key``.

    ``session_key`` should uniquely identify the (tenant, session) this
    tracker's evidence is allowed to influence — callers in this codebase use
    ``f"{tenant_id}:{sid}"`` (see ``chat_runtime._stream_tde_turn``). The
    default ``"default"`` preserves prior single-tenant/CLI behavior for
    callers (tests, standalone scripts) that have no session concept.
    """
    global _session_trackers
    if session_key in _session_trackers:
        _session_trackers.move_to_end(session_key)  # LRU touch
        return _session_trackers[session_key]

    tracker = LossProfileTracker(model_id=model_id)
    _session_trackers[session_key] = tracker
    if len(_session_trackers) > _MAX_SESSION_TRACKERS:
        evicted_key, _ = _session_trackers.popitem(last=False)
        _logger.info("Evicted loss tracker for session_key=%s (LRU cap)", evicted_key)
    return tracker


def clear_session_tracker(session_key: str = "default") -> None:
    """Drop the tracker for ``session_key`` (e.g. on session teardown)."""
    _session_trackers.pop(session_key, None)


def _reset_all_session_trackers_for_tests() -> None:
    """Test-only: wipe the keyed registry between test cases."""
    _session_trackers.clear()
