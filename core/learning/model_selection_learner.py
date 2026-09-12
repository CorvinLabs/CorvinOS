"""
Model Selection Learner — ADR-0377 Phase 2b (real-data backfill)

Computes learned routing thresholds from REAL production audit events
(``os_turn.started`` / ``os_turn.completed``), replacing the empty
scaffolding that previously backed the Model Selection Learning console
dashboard (``learned_threshold_store`` had zero production writers before
this module — verified 2026-09-12 by exhaustive grep).

WHAT THIS IS — an honest framing, load-bearing for anyone reading this later:

There is currently NO live per-subsystem Haiku/Sonnet/Opus routing decision
anywhere in CorvinOS to close a feedback loop into. ``core.orchestration.
cost_optimizer.CostOptimizer`` (ADR-0377 Phase 1) defines exactly that kind
of decision but has zero production callers. This module therefore computes
a DESCRIPTIVE statistic over real history — "at what observed tool-call
complexity level do this task bucket's real turns actually finish
successfully" — not a live routing gate. ``learned_threshold`` here means
"observed successful-completion complexity level", not "the value a live
router currently uses to pick a model." Nothing reads this module's output
to make a routing decision today. Don't cite it as a closed learning loop
in the ADR-0613 sense (see docs/claude-ref/learning-loop.md) — it closes a
DIFFERENT, narrower loop: real events -> real statistics -> real dashboard,
with no fabricated numbers anywhere in the chain.

Bucketing (task_type=complexity tier, subsystem=persona) uses tools_called
per completed turn as the only complexity signal os_turn.* events carry.
Tier boundaries and the normalization cap are DERIVED from the real observed
distribution each refresh (33rd/66th percentile for tier cuts, 95th
percentile for the cap) rather than hardcoded guesses — so every number in
the pipeline traces back to real data, including its own bucketing
parameters.

Recomputed on read (see ``refresh_store``), gated by an in-process cooldown,
rather than incrementally maintained: there is no live feedback signal to
update incrementally against, so a fresh snapshot statistic is more honest
than invented incremental state.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.learning.learned_threshold_store import LearnedThresholdStore, StoredThreshold, get_store

logger = logging.getLogger(__name__)

# Bound worst-case scan latency as the chain grows (today's canonical
# per-tenant chain is ~15MB / ~25k lines / ~80ms full parse — see
# investigation note in Corvin-ADR ADR-TBD-model-selection-learner).
_MAX_SCAN_BYTES = 64 * 1024 * 1024

# "Sample size is large enough to trust the average" — matches the same
# threshold LearnedThresholdStore.get_threshold() already uses to decide
# whether to trust a learned value over the base default.
_MIN_SAMPLES_CONVERGED = 10

# Recompute at most this often per tenant (in-process). The dashboard polls
# every 5s; without this every poll would re-scan the chain AND re-write
# every bucket to the audit-chained store, growing the chain ~1 event per
# bucket per poll for no informational gain.
_REFRESH_COOLDOWN_S = 30.0

_last_refresh: dict[str, float] = {}


@dataclass
class _Bucket:
    total: int = 0
    successes: int = 0
    complexity_sum_success: float = 0.0
    model_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _read_completed_turns(chain_path: Path, max_bytes: int) -> list[dict[str, Any]]:
    """Join os_turn.started + os_turn.completed by turn_id from the real chain.

    Returns one dict per turn that has BOTH a started and completed event
    (a started-only turn is still running and carries no outcome yet).
    """
    if not chain_path.exists():
        return []

    size = chain_path.stat().st_size
    start = max(0, size - max_bytes)
    try:
        with chain_path.open("rb") as fh:
            fh.seek(start)
            buf = fh.read()
    except OSError:
        return []

    text = buf.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # drop a partial first line from the seek

    turns: dict[str, dict[str, Any]] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = rec.get("event_type", "")
        if et not in ("os_turn.started", "os_turn.completed"):
            continue
        det = rec.get("details") or {}
        turn_id = det.get("turn_id")
        if not turn_id:
            continue
        entry = turns.setdefault(turn_id, {})
        if et == "os_turn.started":
            entry.setdefault("model", det.get("model", ""))
            entry["persona"] = det.get("persona") or "unknown"
        else:  # os_turn.completed
            entry["tools_called"] = det.get("tools_called", 0)
            entry["exit_code"] = det.get("exit_code")
            entry["timed_out"] = bool(det.get("timed_out"))
            entry.setdefault("model", det.get("model", ""))

    return [t for t in turns.values() if "exit_code" in t]


def _tier_bounds(tools_called_values: list[int]) -> tuple[float, float]:
    """33rd/66th percentile of the REAL observed tools_called distribution.

    Data-driven tier cuts instead of guessed constants — with too few
    samples to derive percentiles meaningfully, falls back to (2, 10) as a
    documented, conservative default (not presented as learned).
    """
    if len(tools_called_values) < 6:
        return (2.0, 10.0)
    vals = sorted(tools_called_values)
    n = len(vals)
    lo = vals[max(0, int(0.33 * n) - 1)]
    hi = vals[max(0, int(0.66 * n) - 1)]
    if hi <= lo:
        hi = lo + 1
    return (float(lo), float(hi))


def _complexity_cap(tools_called_values: list[int]) -> float:
    """95th percentile of the real distribution, used to normalize into [0,1]."""
    if not tools_called_values:
        return 20.0  # no data at all — documented fallback, never presented as learned
    vals = sorted(tools_called_values)
    idx = min(len(vals) - 1, int(0.95 * len(vals)))
    return max(1.0, float(vals[idx]))


def _tier_for(tools_called: int, bounds: tuple[float, float]) -> str:
    lo, hi = bounds
    if tools_called <= lo:
        return "simple"
    if tools_called <= hi:
        return "medium"
    return "complex"


def compute_learned_thresholds(
    tenant_id: str,
    chain_path: Optional[Path] = None,
) -> list[StoredThreshold]:
    """Compute learned thresholds from real os_turn.* events for one tenant.

    Pure function: reads the chain, returns StoredThreshold records. Does
    not write to the store — see ``refresh_store`` for persistence.
    """
    if chain_path is None:
        from core.console.corvin_console import _bootstrap

        chain_path = _bootstrap.forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

    turns = _read_completed_turns(chain_path, _MAX_SCAN_BYTES)
    if not turns:
        return []

    tools_called_values = [int(t.get("tools_called") or 0) for t in turns]
    bounds = _tier_bounds(tools_called_values)
    cap = _complexity_cap(tools_called_values)

    buckets: dict[tuple[str, str], _Bucket] = defaultdict(_Bucket)
    for t in turns:
        persona = t.get("persona") or "unknown"
        tools_called = int(t.get("tools_called") or 0)
        tier = _tier_for(tools_called, bounds)
        success = t.get("exit_code") == 0 and not t.get("timed_out")

        b = buckets[(tier, persona)]
        b.total += 1
        if success:
            b.successes += 1
            b.complexity_sum_success += min(1.0, tools_called / cap)
        if t.get("model"):
            b.model_counts[t["model"]] += 1

    now = datetime.now(timezone.utc).isoformat()
    results: list[StoredThreshold] = []
    for (tier, persona), b in sorted(buckets.items()):
        learned = (b.complexity_sum_success / b.successes) if b.successes else 0.5
        learned = max(0.1, min(0.9, learned))
        success_rate = (b.successes / b.total) if b.total else 0.0
        dominant_model = max(b.model_counts, key=b.model_counts.get) if b.model_counts else "n/a"

        results.append(
            StoredThreshold(
                task_type=tier,
                subsystem=persona,
                tenant_id=tenant_id,
                learned_threshold=learned,
                base_threshold=0.5,
                timestamp=now,
                sample_count=b.total,
                converged=b.total >= _MIN_SAMPLES_CONVERGED,
                notes=(
                    f"descriptive stat from {b.total} real os_turn events "
                    f"({success_rate:.0%} success; dominant model: {dominant_model}); "
                    f"NOT a live routing decision — see module docstring"
                ),
            )
        )
    return results


def refresh_store(
    tenant_id: str,
    store: Optional[LearnedThresholdStore] = None,
    *,
    force: bool = False,
) -> int:
    """Recompute + persist learned thresholds for a tenant. Returns count written.

    Cooldown-gated (``_REFRESH_COOLDOWN_S``) so a 5s dashboard poll doesn't
    re-scan the chain and re-write every bucket to the audit-chained store
    on every request. Audit-first via the same ``_SkillAuditBackend`` the
    rest of ADR-0377 Phase 2b uses (``set_threshold`` logs before
    persisting).
    """
    now = time.monotonic()
    last = _last_refresh.get(tenant_id, 0.0)
    if not force and (now - last) < _REFRESH_COOLDOWN_S:
        return 0
    _last_refresh[tenant_id] = now

    from core.learning.cost_variance_optimizer import _SkillAuditBackend

    if store is None:
        store = get_store(tenant_id)

    audit_backend = _SkillAuditBackend()
    thresholds = compute_learned_thresholds(tenant_id)
    written = 0
    for stored in thresholds:
        try:
            store.set_threshold(stored, audit_backend=audit_backend)
            written += 1
        except Exception as e:  # noqa: BLE001 — one bad bucket must not block the rest
            logger.error(
                "failed to persist learned threshold %s/%s: %s",
                stored.task_type, stored.subsystem, e,
            )
    return written
