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

import json
import logging
import os
import tempfile
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

_logger = logging.getLogger(__name__)


@dataclass
class LossEntry:
    """Single loss measurement.

    ADR-0222 F3 — CANONICAL KEY. The loss profile is keyed on
    ``(task_type, model_id)`` where ``task_type`` IS the step's ``step.action``
    (``read_file``, ``analyze_data``, …), passed straight through by the
    executor. There is deliberately NO finer ``step_kind`` /
    "mechanical vs reasoning-dense" dimension: 0218/0219/0220 planned one but it
    was never implemented and had no measured justification. Do NOT re-introduce
    a ``step_kind`` key without first shipping a real classifier as its own item
    and showing (per F1's counterfactual) that the finer key lowers loss —
    otherwise it just fragments the evidence and slows learning.
    """
    timestamp: float
    task_type: str  # == step.action (the canonical action key; NOT a step_kind)
    model_id: str
    loss_pct: float  # 0-100
    engine: str  # Which engine was chosen
    complexity: str = "moderate"
    measured: bool = True  # True = real local-vs-remote comparison, False = proxy
    alternative_scores: dict[str, float] = field(default_factory=dict)
    # Gap #2: Real token measurement (ADR-0218/0219)
    tokens_delegated: Optional[int] = None
    tokens_local: Optional[int] = None


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

    def __init__(self, model_id: str = "default", persist_path: "Optional[Path]" = None):
        """Initialize tracker.

        Args:
            model_id: Current model (e.g., "claude-sonnet-5"). Used to detect model changes.
            persist_path: ADR-0219 R4 — a TENANT-scoped file the MEASURED
                (shadow-run) entries are appended to and reloaded from, so a
                tenant's hard-won loss evidence survives across its own sessions
                instead of resetting every session (the amplifier could never
                learn otherwise). None = in-session only (tests / CLI / the
                "default" session_key), preserving prior behaviour byte-for-byte.
                Tenant-scoped path keeps ADR-0215 F4 isolation intact — one
                tenant's file never influences another's.
        """
        self.history: list[LossEntry] = []
        self.current_model_id = model_id
        self._persist_path = persist_path
        if persist_path is not None:
            self._load_from_disk()

    def record_delegation_result(
        self,
        task_type: str,
        engine: str,
        loss_pct: float,
        complexity: str = "moderate",
        measured: bool = True,
        alternative_scores: Optional[dict[str, float]] = None,
        model_id: Optional[str] = None,
        tokens_delegated: Optional[int] = None,
        tokens_local: Optional[int] = None,
    ):
        """Record a delegation outcome.

        Args:
            task_type: Task classification (code_generation, etc)
            engine: Which engine was chosen
            loss_pct: Measured quality loss (0-100)
            complexity: Task complexity bucket (simple/moderate/complex)
            measured: True for real local-vs-remote comparison, False for proxy
            alternative_scores: Softmax scores of other engines (for off-policy learning)
            model_id: ADR-0222 F2 — the model that ACTUALLY produced this step's
                output. Previously every entry was stamped with
                ``current_model_id`` (a session constant), so the log was
                single-arm: an argmin over models degenerated to "keep the one
                worker model", and the moment a step ran on a different model its
                loss was still logged under the constant — silently corrupting the
                fit meant to evaluate that other model. Now the real per-step
                model is recorded. Falls back to ``current_model_id`` only when a
                caller has no per-step model (proxy / legacy).
            tokens_delegated: Gap #2 — actual tokens consumed by delegated execution
            tokens_local: Gap #2 — actual tokens consumed by local execution
        """

        entry = LossEntry(
            timestamp=time.time(),
            task_type=task_type,
            model_id=(model_id or self.current_model_id),
            loss_pct=max(0.0, min(100.0, float(loss_pct))),
            engine=engine,
            complexity=complexity,
            measured=measured,
            alternative_scores=alternative_scores or {},
            tokens_delegated=tokens_delegated,
            tokens_local=tokens_local,
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

        # ADR-0219 R4: persist only MEASURED entries. Proxy entries are frequent
        # (every delegation) and cheap to regenerate in-session; the shadow-run
        # measurements are the rare, expensive, cross-session-valuable signal.
        # Persisting only them bounds I/O without losing what matters.
        if measured and self._persist_path is not None:
            self._append_entry_to_disk(entry)

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
        self, task_type: str, complexity: str = "moderate", engine: Optional[str] = None,
        model_id: Optional[str] = None,
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
            model_id: ADR-0222 F2 — which model's arm to estimate. Defaults to
                the current worker model (legacy behaviour). Now that entries
                carry the REAL executed model, passing a different model_id
                estimates THAT arm — the per-arm query the (action, model) fit
                needs. A route-up Sonnet step's loss no longer masquerades as
                Haiku evidence and vice-versa.

        Returns:
            Estimated loss as fraction (0.0-1.0). DEFAULT until enough evidence.
        """

        self._prune_history()

        _arm_model = model_id or self.current_model_id
        relevant = [
            e for e in self.history
            if e.task_type == task_type and e.model_id == _arm_model
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

    def estimate_cost_ratio(
        self,
        task_type: str,
        model_id: Optional[str] = None,
    ) -> Optional[float]:
        """Estimate cost ratio (delegated_tokens / local_tokens) for a task type.

        Gap #2 Token Measurement: Calculate whether delegation saves tokens.
        Used by Gate 4 (cost-aware delegation gate) to block expensive delegations.

        Args:
            task_type: Task classification
            model_id: Which model's arm to estimate (defaults to current worker model)

        Returns:
            Cost ratio (delegated/local), or None if insufficient data.
            Ratio < 1.0: delegation saves tokens (cheap)
            1.0 <= ratio <= 1.5: break-even, still allow for learning
            ratio > 1.5: expensive, block delegation (Gate 4)
        """

        self._prune_history()

        _arm_model = model_id or self.current_model_id
        relevant = [
            e for e in self.history
            if e.task_type == task_type
            and e.model_id == _arm_model
            and e.tokens_delegated is not None
            and e.tokens_local is not None
        ]

        if len(relevant) < self.MIN_SAMPLES:
            return None

        # Simple arithmetic mean of cost ratios (not exponential decay like loss)
        ratios = [e.tokens_delegated / e.tokens_local for e in relevant if e.tokens_local > 0]
        if not ratios:
            return None

        return sum(ratios) / len(ratios)

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

    def record_unmeasured(
        self, task_type: str, engine: str, complexity: str = "moderate",
    ) -> None:
        """Record a delegation whose quality could NOT be measured (ADR-0219 R5).

        Used for side-effecting actions, which can never be shadow-compared (you
        cannot safely run a mutation twice). The old path recorded these via the
        success proxy at 1% loss — i.e. 'assumed good' from a signal that only
        knows the step did not crash, not that its output was correct. Instead we
        record the CONSERVATIVE default loss, flagged unmeasured: no quality
        CLAIM is made, so the estimate does not drift optimistic and the Gate-3
        quality gate keeps an unverifiable side-effecting step conservative
        rather than delegating it on a false 'good'."""
        self.record_delegation_result(
            task_type=task_type,
            engine=engine,
            loss_pct=self.DEFAULT_LOSS_PCT,   # neutral: neither good nor bad
            complexity=complexity,
            measured=False,
        )

    def measured_count_for(self, task_type: str) -> int:
        """How many MEASURED entries exist for this task_type at the current
        model — the evidence mass the adaptive shadow rate keys on (R5)."""
        return sum(1 for e in self.history
                   if e.measured and e.task_type == task_type
                   and e.model_id == self.current_model_id)

    # ── ADR-0219 R4: cross-session persistence ────────────────────────────────

    def _prune_cutoff_ts(self) -> float:
        """Entries older than PRUNE_AFTER_HALF_LIVES half-lives are dead weight."""
        return time.time() - (self.DECAY_HALF_LIFE_DAYS * 86400 * self.PRUNE_AFTER_HALF_LIVES)

    def _append_entry_to_disk(self, entry: LossEntry) -> None:
        """Append one measured entry as a JSONL line. Best-effort, fail-soft —
        a persistence failure must never break a delegation turn."""
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(asdict(entry), separators=(",", ":")) + "\n"
            # Append is atomic for a single short line on POSIX (< PIPE_BUF), so
            # concurrent same-tenant sessions can both append without a lock.
            with open(self._persist_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as e:
            _logger.warning("loss-profile persist append failed (%s): %s",
                            type(e).__name__, e)

    def _load_from_disk(self) -> None:
        """Load persisted measured entries, drop decayed ones, cap to
        MAX_ENTRIES, and compact the file if it has grown past the cap. Loaded
        entries seed self.history so a new session starts already-learned."""
        p = self._persist_path
        try:
            if not p.exists():
                return
            cutoff = self._prune_cutoff_ts()
            loaded: list[LossEntry] = []
            with open(p, "r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        d = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue  # skip a torn/partial line, keep the rest
                    ts = float(d.get("timestamp", 0) or 0)
                    if ts < cutoff:
                        continue  # decayed to near-zero weight — drop
                    try:
                        loaded.append(LossEntry(
                            timestamp=ts,
                            task_type=str(d.get("task_type", "")),
                            model_id=str(d.get("model_id", "")),
                            loss_pct=max(0.0, min(100.0, float(d.get("loss_pct", 0)))),
                            engine=str(d.get("engine", "")),
                            complexity=str(d.get("complexity", "moderate")),
                            measured=bool(d.get("measured", True)),
                            alternative_scores=dict(d.get("alternative_scores", {}) or {}),
                            tokens_delegated=int(d.get("tokens_delegated")) if d.get("tokens_delegated") is not None else None,
                            tokens_local=int(d.get("tokens_local")) if d.get("tokens_local") is not None else None,
                        ))
                    except (TypeError, ValueError):
                        continue
            # Keep the most recent MAX_ENTRIES (they carry the most weight).
            loaded.sort(key=lambda e: e.timestamp)
            if len(loaded) > self.MAX_ENTRIES:
                loaded = loaded[-self.MAX_ENTRIES:]
            self.history = loaded
            # Compaction: if the file held more (decayed/over-cap) than we kept,
            # rewrite it atomically to the pruned set so it can't grow unbounded.
            self._compact_if_needed(len(loaded))
        except OSError as e:
            _logger.warning("loss-profile load failed (%s): %s", type(e).__name__, e)
            self.history = []

    def _compact_if_needed(self, kept: int) -> None:
        try:
            # Cheap heuristic: rewrite only when the file has clearly more lines
            # than we kept (decayed/over-cap accumulation).
            with open(self._persist_path, "r", encoding="utf-8") as fh:
                on_disk = sum(1 for ln in fh if ln.strip())
            if on_disk <= kept + 50:
                return
            fd, tmp = tempfile.mkstemp(dir=str(self._persist_path.parent),
                                       prefix=self._persist_path.name + ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for e in self.history:
                    fh.write(json.dumps(asdict(e), separators=(",", ":")) + "\n")
            os.replace(tmp, self._persist_path)
            _logger.info("loss-profile compacted %d→%d entries", on_disk, kept)
        except OSError as e:
            _logger.warning("loss-profile compaction skipped (%s): %s", type(e).__name__, e)

    def clear(self):
        """Clear all history."""
        self.history = []
        _logger.info("Loss profile cleared")


# Keyed registry: in-session learning must survive per-request construction
# of SendIntegration / engines (F26), but MUST NOT bleed across sessions or
# tenants (ADR-0215 F4). Bounded LRU — see module docstring.
_MAX_SESSION_TRACKERS = 500
_session_trackers: "OrderedDict[str, LossProfileTracker]" = OrderedDict()


def _persist_path_for(session_key: str) -> "Optional[Path]":
    """TENANT-scoped persistence file for a session_key of the form
    ``"{tenant_id}:{sid}"`` (ADR-0219 R4). Cross-session learning is per TENANT,
    so the path drops the sid — every session of one tenant shares one file,
    and different tenants never share (ADR-0215 F4 isolation).

    Returns None for the legacy ``"default"`` key or when tenant resolution is
    unavailable — those callers stay in-session-only exactly as before.
    Opt out entirely with CORVIN_TDE_LOSS_PERSIST=0.
    """
    if os.environ.get("CORVIN_TDE_LOSS_PERSIST", "1").strip().lower() in ("0", "false", "no"):
        return None
    tenant = session_key.split(":", 1)[0].strip()
    if not tenant or tenant == "default":
        return None
    try:
        from forge import paths as _forge_paths  # type: ignore  # noqa: PLC0415
        # tenant_global_dir validates the tenant id (validate_tenant_id) and is
        # the same tenant-scoped root every other per-tenant artifact uses.
        return _forge_paths.tenant_global_dir(tenant) / "tde" / "loss_profile.jsonl"
    except Exception as e:  # noqa: BLE001 — no forge / bad tenant → in-session only
        _logger.debug("loss-profile persistence unavailable for %r: %s", session_key, e)
        return None


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

    tracker = LossProfileTracker(model_id=model_id,
                                 persist_path=_persist_path_for(session_key))
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
