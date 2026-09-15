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

Bucketing (task_type=complexity tier, pooled across personas — subsystem
is always "all") uses tools_called per completed turn as the only
complexity signal os_turn.* events carry. Personas are real (bridge
conversations can route to a non-default persona; the console web chat
is always "assistant") but a per-persona split just doubled every tier's
dashboard card without telling the operator anything useful — dropped
2026-09-12 on operator feedback; `persona` is still captured per-turn in
``_read_completed_turns`` for any future consumer that does need it.
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
import re
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
    # Real dollars for this tier's turns, priced exactly as compute_cost_efficiency
    # prices them (same tokens, same published rates). A tier's volume and its
    # reliability are only half the operator's question; the other half is what
    # that volume costs, and the tier bucketing already has every turn in hand.
    actual_usd: float = 0.0
    baseline_usd: float = 0.0
    priced_turns: int = 0


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
            # ADR-0696 — real token usage, added to os_turn.completed
            # 2026-09-12 (chat_runtime.py::_os_emit_completed). Absent on
            # events emitted before that change, hence the 0 default; a
            # turn with no usage is excluded from cost totals downstream,
            # never assigned a guessed cost.
            entry["input_tokens"] = int(det.get("input_tokens") or 0)
            entry["output_tokens"] = int(det.get("output_tokens") or 0)
            # 2026-09-13 finding — for a cache-heavy turn these dwarf
            # input_tokens/output_tokens above (one real sample: 3.2M
            # cache-read tokens vs 266 input_tokens); priced separately
            # below at Anthropic's published cache multipliers.
            entry["cache_creation_input_tokens"] = int(det.get("cache_creation_input_tokens") or 0)
            entry["cache_read_input_tokens"] = int(det.get("cache_read_input_tokens") or 0)
            entry["completed_ts"] = rec.get("ts")

    return [t for t in turns.values() if "exit_code" in t]


def _read_acs_completions(chain_path: Path, max_bytes: int) -> list[dict[str, Any]]:
    """Real ACS worker completions from ``acs.engine_completed`` events.

    ACS is the substantive-work delegation path (full tool access, e.g. a
    21-tool-call agentic run) — the OS-turn chain ``_read_completed_turns``
    reads only ever sees the cheap "OS manager" layer, never this. Each
    ``acs.engine_completed`` record is already a complete, single-event
    completion (no started/completed join needed, unlike os_turn.*).

    Note (2026-09-13 finding): ``acs_runtime.py::_audit_path()`` writes to
    ``<corvin_home>/tenants/<tid>/global/audit.jsonl`` — NOT the canonical
    ``.../global/forge/audit.jsonl`` ``_read_completed_turns`` reads. This is
    the pre-existing "audit chain split" ADR-0650/0654 already document
    (multiple live chain files per tenant); reading from where ACS actually
    writes is the correct fix for THIS dashboard, not a chain consolidation
    (that requires the documented seam mechanism, out of scope here).
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

    completions: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event_type") != "acs.engine_completed":
            continue
        det = rec.get("details") or {}
        completions.append({
            "model": det.get("model_id", ""),
            "input_tokens": int(det.get("input_tokens") or 0),
            "output_tokens": int(det.get("output_tokens") or 0),
            "cache_creation_input_tokens": int(det.get("cache_creation_input_tokens") or 0),
            "cache_read_input_tokens": int(det.get("cache_read_input_tokens") or 0),
            "completed_ts": rec.get("ts"),
        })
    return completions


def _acs_chain_paths(tenant_id: str) -> list[Path]:
    """Every chain file ACS worker completions can be found in, oldest first.

    TWO, not one, and both are needed for a complete answer:

    * ``<global>/audit.jsonl`` — where ``acs_runtime._audit_path()`` wrote
      until 2026-09-15. A hand-composed path one directory above the canonical
      chain (the ADR-0650 split). Append-only history; CLAUDE.md forbids
      merging, rewriting or deleting it, so it is READ, never touched.
    * ``<global>/forge/audit.jsonl`` — the canonical chain
      (``tenant_audit_chain()``), where ACS writes from 2026-09-15 on.

    Reading only the first would lose every new record the moment the writer
    was corrected; reading only the second would lose all history. Records are
    de-duplicated by the caller on (ts, model, tokens), so a record that
    somehow exists in both is counted once.
    """
    from core.console.corvin_console import _bootstrap

    global_dir = _bootstrap.forge_paths.tenant_global_dir(tenant_id)
    return [global_dir / "audit.jsonl", global_dir / "forge" / "audit.jsonl"]


def _read_worker_spans(chain_path: Path, max_bytes: int) -> list[dict[str, Any]]:
    """Delegated WORKER spend from ``engine.span.end`` (role=worker).

    The third delegation path, alongside ACS: a gateway ``POST
    /v1/tenants/{tid}/runs`` spawns a worker engine and closes the run with an
    engine span. Until ADR-0759 that span carried neither a ``model_id`` nor
    token counts, so this spend could not be priced at all and the dashboard
    reported the OS turns as if they were the whole bill. Spans that still lack
    either (anything recorded before that change) are skipped here rather than
    estimated — same honesty rule as everywhere else in this module.
    """
    if not chain_path.exists():
        return []
    try:
        size = chain_path.stat().st_size
        with chain_path.open("rb") as fh:
            fh.seek(max(0, size - max_bytes))
            buf = fh.read()
    except OSError:
        return []

    out: list[dict[str, Any]] = []
    for line in buf.decode("utf-8", errors="replace").splitlines():
        if '"engine.span.end"' not in line:
            continue
        try:
            record = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        details = record.get("details")
        if not isinstance(details, dict) or details.get("role") != "worker":
            continue
        model = str(details.get("model_id") or "")
        if not model:
            continue
        in_tok = int(details.get("input_tokens") or 0)
        out_tok = int(details.get("output_tokens") or 0)
        cw_tok = int(details.get("cache_write_tokens") or 0)
        cr_tok = int(details.get("cache_read_tokens") or 0)
        if not (in_tok or out_tok or cw_tok or cr_tok):
            continue
        out.append({
            "completed_ts": record.get("ts"),
            "model": model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_creation_input_tokens": cw_tok,
            "cache_read_input_tokens": cr_tok,
        })
    return out


# ── ADR-0696: real cost-efficiency trend ────────────────────────────────
#
# Real, published per-1K-token USD pricing (Anthropic first-party API
# rates). Matched by prefix since the CLI may report a dated snapshot
# suffix (e.g. "claude-haiku-4-5-20251001"). A model not listed here is
# excluded from cost totals rather than assigned a guessed price — same
# honesty rule as the rest of this module.
# Longest-prefix matched (see _price_for_model) so "claude-opus-5" cannot
# shadow a hypothetical "claude-opus-5-x" entry, and a dated CLI suffix
# ("claude-haiku-4-5-20251001") still resolves.
# Verified against the published first-party rate card 2026-09-15.
_MODEL_PRICING_USD_PER_1K: dict[str, tuple[float, float]] = {
    # Top tier — more expensive than Opus. Absent until 2026-09-15, which
    # silently EXCLUDED every turn on these models from the cost totals
    # rather than pricing it (see _price_for_model returning None).
    "claude-fable-5-1": (0.010, 0.050),
    "claude-fable-5": (0.010, 0.050),
    "claude-mythos-5-1": (0.010, 0.050),
    "claude-mythos-5": (0.010, 0.050),
    "claude-opus-5": (0.005, 0.025),
    "claude-opus-4-8": (0.005, 0.025),
    "claude-opus-4-7": (0.005, 0.025),
    "claude-opus-4-6": (0.005, 0.025),
    "claude-sonnet-5": (0.002, 0.010),
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-haiku-4-5": (0.001, 0.005),
}

# Reference model for the "baseline" counterfactual — "what this same traffic
# would have cost on Opus 5", applied to each turn's REAL observed token
# counts. A documented modeling choice, not a fabricated number.
#
# NOT "the most expensive recognized tier" (as this comment claimed until
# 2026-09-15): the Fable/Mythos entries above are priced ABOVE Opus, so a
# turn on one of those shows as a NEGATIVE saving against this baseline.
# That is the honest reading — it cost more than the reference — and the
# label the UI shows names the reference model explicitly for that reason.
_BASELINE_MODEL_PREFIX = "claude-opus-5"

# Anthropic's published prompt-caching multipliers, applied to a model's own
# INPUT rate (cache tokens are never priced at the output rate). 2026-09-13
# finding: for a cache-heavy Claude Code turn these tokens dwarf plain
# input_tokens — one real sample had cache_read_input_tokens=3,236,410 vs
# input_tokens=266 — so omitting them (the state before this fix) understated
# real cost by orders of magnitude, not a rounding error.
# _CACHE_WRITE_MULTIPLIER prices the undifferentiated
# ``cache_creation_input_tokens`` total at the cheaper 5-minute-TTL rate
# (1.25x) rather than the 1-hour-TTL rate (2x): a documented, conservative
# choice when the finer ephemeral_5m/ephemeral_1h split isn't captured —
# never a fabricated split, just a stated assumption on a real count.
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.1


#: Cross-region inference-profile prefixes Bedrock prepends to the model id
#: (``eu.anthropic.claude-sonnet-5``). The table below is keyed on the bare
#: family, and the lookup is prefix-ANCHORED, so an unstripped routing prefix
#: matches nothing and the turn is dropped from cost totals entirely.
#:
#: That is not a cosmetic miss. On a Bedrock-authenticated install every ACS
#: worker turn inherits its model from ``ANTHROPIC_MODEL`` (acs_runtime.py's
#: ``_resolve_worker_model`` step 3), which is exactly where the prefixed id
#: comes from — so 100% of delegated worker spend priced as $0.00 while still
#: counting toward ``total_turns``. Verified on this install 2026-09-15:
#: ``ANTHROPIC_MODEL=eu.anthropic.claude-sonnet-5``.
#:
#: Stripping the prefix is NOT the same as guessing a price: the model family
#: it names is identical, and Bedrock lists Claude at the same per-token rates
#: as the first-party API. An id whose family is still unknown after stripping
#: is excluded as before — the honesty rule is unchanged.
_ROUTING_PREFIX = re.compile(r"^(?:[a-z]{2,6}\.)?anthropic\.")


def _price_for_model(model: str) -> Optional[tuple[float, float]]:
    """(input_usd_per_1k, output_usd_per_1k) for a recognized model, else None."""
    if not model:
        return None
    # Two independent corrections, both required:
    #   1. strip the routing prefix, or Bedrock/Vertex ids price at $0.00
    #      (see _ROUTING_PREFIX above);
    #   2. match the LONGEST prefix, not the first dict hit — the table
    #      contains nested keys (claude-fable-5 is a prefix of
    #      claude-fable-5-1), so first-match would price a newer variant
    #      at its predecessor's rate the moment the two rates diverge.
    candidate = _ROUTING_PREFIX.sub("", model)
    best: Optional[tuple[str, tuple[float, float]]] = None
    for prefix, price in _MODEL_PRICING_USD_PER_1K.items():
        if candidate.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, price)
    if best is not None:
        return best[1]
    return None


def _turn_cost_usd(
    price: tuple[float, float],
    in_tok: int, out_tok: int,
    cache_write_tok: int, cache_read_tok: int,
) -> float:
    """Real $ for one turn's full token accounting against one model's price.

    All four token categories from the real usage object — never just
    input/output (see _CACHE_WRITE_MULTIPLIER docstring for why that alone
    silently discarded most of the real spend on cache-heavy turns).
    """
    return (
        (in_tok / 1000.0) * price[0]
        + (out_tok / 1000.0) * price[1]
        + (cache_write_tok / 1000.0) * price[0] * _CACHE_WRITE_MULTIPLIER
        + (cache_read_tok / 1000.0) * price[0] * _CACHE_READ_MULTIPLIER
    )


@dataclass
class CostDayPoint:
    date: str
    actual_usd: float
    baseline_usd: float
    # Coverage — how many of that day's real os_turn.completed events actually
    # carried usable model+token data vs. how many completed turns happened
    # that day in total. A day can look like a cost crash purely because
    # coverage was thin (e.g. the emitter was mid-rollout or briefly broken),
    # not because spend actually dropped — surfacing both counts lets a
    # reader tell "real drop" from "sparse data" instead of the two looking
    # identical (live incident 2026-09-13: 1/28 turns counted read as a 99%
    # cost crash from the previous day's 12/70).
    counted_turns: int = 0
    total_turns: int = 0


@dataclass
class CostEfficiencyResult:
    has_data: bool
    daily: list[CostDayPoint]
    total_actual_usd: float
    total_baseline_usd: float
    savings_percent: float
    # Real per-model turn counts across every counted turn. A single-key mix
    # (e.g. {"claude-haiku-4-5-20251001": 244}) means savings_percent is just
    # that model's fixed price ratio against the baseline model — a pricing
    # fact, not evidence of a routing decision — so the UI can say so instead
    # of presenting it as an optimization result (live finding 2026-09-13).
    model_mix: dict[str, int] = field(default_factory=dict)
    # ACS-delegated worker spend (core/console's os_turn chain never sees this
    # — ACS is the substantive-work path with full tool access, e.g. a
    # 21-tool-call agentic run, and writes its own acs.engine_completed
    # events). Kept as a SEPARATE series rather than folded into `daily`
    # above — never blend two independently-measured cost sources into one
    # number without saying so (2026-09-13 finding: the OS-turn chain alone
    # was only ever showing the cheap "OS manager" slice, never this).
    acs_daily: list[CostDayPoint] = field(default_factory=list)
    acs_total_actual_usd: float = 0.0
    acs_total_baseline_usd: float = 0.0
    acs_model_mix: dict[str, int] = field(default_factory=dict)
    # ADR-0761 — per-model DOLLARS, not just per-model turn counts.
    # {model_id: {"actual_usd", "baseline_usd", "turns"}}. The mix above answers
    # "which models ran"; this answers "what did each one cost, and what would
    # the same real tokens have cost on the reference model" — the only pairing
    # from which a reader can see WHERE a saving came from rather than just
    # that there was one. Same real token counts, same published rates, no new
    # estimation step.
    model_cost: dict[str, dict[str, float]] = field(default_factory=dict)
    acs_model_cost: dict[str, dict[str, float]] = field(default_factory=dict)


def _price_and_baseline(
    model: str, in_tok: int, out_tok: int,
    cache_write_tok: int = 0, cache_read_tok: int = 0,
) -> Optional[tuple[float, float]]:
    """(actual_usd, baseline_usd) for one turn's full token accounting, or
    None if unpriceable. Baseline reuses the SAME real token counts (input,
    output, cache-write, cache-read) at the reference model's price — "what
    if the same real work had used Opus", not a different token count."""
    price = _price_for_model(model)
    if price is None:
        return None
    baseline_price = _MODEL_PRICING_USD_PER_1K[_BASELINE_MODEL_PREFIX]
    actual = _turn_cost_usd(price, in_tok, out_tok, cache_write_tok, cache_read_tok)
    baseline = _turn_cost_usd(baseline_price, in_tok, out_tok, cache_write_tok, cache_read_tok)
    return actual, baseline


def compute_cost_efficiency(
    tenant_id: str,
    chain_path: Optional[Path] = None,
) -> CostEfficiencyResult:
    """Real cost-efficiency trend from actual token usage + published pricing.

    Every number here traces to a real ``os_turn.completed`` event's real
    ``input_tokens``/``output_tokens`` and a real published per-model price.
    A turn whose model isn't in ``_MODEL_PRICING_USD_PER_1K``, or that
    carries no token counts (events emitted before ADR-0696, or a turn that
    ended before any usage arrived), is excluded rather than estimated.
    ``has_data=False`` means exactly that — no turn in the scanned window
    has both a recognized model AND real token counts — so the honest
    result is "insufficient data", never a fabricated number.
    """
    if chain_path is None:
        from core.console.corvin_console import _bootstrap

        chain_path = _bootstrap.forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

    # ADR-0760 — the SAME counting epoch model_usage applies. Two panels on one
    # screen deriving their numbers from different windows is worse than no
    # reset at all, so the window is read from one place, never re-decided here.
    try:
        from core.console.corvin_console import usage_epoch  # noqa: PLC0415

        since_ts = usage_epoch.epoch_ts(tenant_id)
    except Exception:  # noqa: BLE001
        since_ts = 0.0

    turns = [
        t for t in _read_completed_turns(chain_path, _MAX_SCAN_BYTES)
        if not since_ts or float(t.get("completed_ts") or 0) >= since_ts
    ]
    baseline_price = _MODEL_PRICING_USD_PER_1K[_BASELINE_MODEL_PREFIX]

    by_day: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])  # date -> [actual, baseline]
    by_day_total: dict[str, int] = defaultdict(int)
    by_day_counted: dict[str, int] = defaultdict(int)
    model_mix: dict[str, int] = defaultdict(int)
    model_cost: dict[str, dict[str, float]] = {}
    counted = 0
    for t in turns:
        ts = t.get("completed_ts")
        if ts is None:
            continue
        day = datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
        by_day_total[day] += 1

        price = _price_for_model(t.get("model") or "")
        in_tok = t.get("input_tokens") or 0
        out_tok = t.get("output_tokens") or 0
        cache_write_tok = t.get("cache_creation_input_tokens") or 0
        cache_read_tok = t.get("cache_read_input_tokens") or 0
        if price is None or (in_tok == 0 and out_tok == 0 and cache_write_tok == 0 and cache_read_tok == 0):
            continue
        actual = _turn_cost_usd(price, in_tok, out_tok, cache_write_tok, cache_read_tok)
        baseline = _turn_cost_usd(baseline_price, in_tok, out_tok, cache_write_tok, cache_read_tok)
        by_day[day][0] += actual
        by_day[day][1] += baseline
        by_day_counted[day] += 1
        _model = t.get("model") or ""
        model_mix[_model] += 1
        _acc = model_cost.setdefault(_model, {"actual_usd": 0.0, "baseline_usd": 0.0, "turns": 0})
        _acc["actual_usd"] += actual
        _acc["baseline_usd"] += baseline
        _acc["turns"] += 1
        counted += 1

    # Every day with at least one completed turn gets a bar — including a day
    # with zero counted turns (cost 0, coverage 0/N) — so a coverage gap shows
    # up as a visibly thin bar instead of silently vanishing from the chart.
    daily = [
        CostDayPoint(
            date=day,
            actual_usd=round(by_day[day][0], 4) if day in by_day else 0.0,
            baseline_usd=round(by_day[day][1], 4) if day in by_day else 0.0,
            counted_turns=by_day_counted.get(day, 0),
            total_turns=by_day_total[day],
        )
        for day in sorted(by_day_total)
    ]
    total_actual = sum(p.actual_usd for p in daily)
    total_baseline = sum(p.baseline_usd for p in daily)
    savings_pct = (
        max(0.0, min(100.0, (1 - total_actual / total_baseline) * 100))
        if total_baseline > 0
        else 0.0
    )

    # ACS-delegated worker spend — separate chain, separate series (see
    # CostEfficiencyResult.acs_daily docstring above for why it's never
    # blended into `daily`).
    acs_completions: list[dict[str, Any]] = []
    _seen_delegated: set[tuple] = set()
    _chain_paths = _acs_chain_paths(tenant_id)
    for _chain in _chain_paths:
        acs_completions.extend(_read_acs_completions(_chain, _MAX_SCAN_BYTES))
    # Gateway worker runs (ADR-0759). Only on the canonical chain — the
    # dispatcher has always written there.
    acs_completions.extend(_read_worker_spans(_chain_paths[-1], _MAX_SCAN_BYTES))
    _deduped: list[dict[str, Any]] = []
    for _c in acs_completions:
        _key = (_c.get("completed_ts"), _c.get("model"),
                _c.get("input_tokens"), _c.get("output_tokens"),
                _c.get("cache_creation_input_tokens"), _c.get("cache_read_input_tokens"))
        if _key in _seen_delegated:
            continue
        _seen_delegated.add(_key)
        _deduped.append(_c)
    acs_completions = [
        c for c in _deduped
        if not since_ts or float(c.get("completed_ts") or 0) >= since_ts
    ]
    acs_by_day: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    acs_by_day_total: dict[str, int] = defaultdict(int)
    acs_by_day_counted: dict[str, int] = defaultdict(int)
    acs_model_mix: dict[str, int] = defaultdict(int)
    acs_model_cost: dict[str, dict[str, float]] = {}
    for c in acs_completions:
        ts = c.get("completed_ts")
        if ts is None:
            continue
        day = datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
        acs_by_day_total[day] += 1

        model = c.get("model") or ""
        in_tok = c.get("input_tokens") or 0
        out_tok = c.get("output_tokens") or 0
        cache_write_tok = c.get("cache_creation_input_tokens") or 0
        cache_read_tok = c.get("cache_read_input_tokens") or 0
        has_usage = in_tok or out_tok or cache_write_tok or cache_read_tok
        priced = (
            _price_and_baseline(model, in_tok, out_tok, cache_write_tok, cache_read_tok)
            if has_usage else None
        )
        if priced is None:
            continue
        acs_by_day[day][0] += priced[0]
        acs_by_day[day][1] += priced[1]
        acs_by_day_counted[day] += 1
        acs_model_mix[model] += 1
        _acc = acs_model_cost.setdefault(model, {"actual_usd": 0.0, "baseline_usd": 0.0, "turns": 0})
        _acc["actual_usd"] += priced[0]
        _acc["baseline_usd"] += priced[1]
        _acc["turns"] += 1

    acs_daily = [
        CostDayPoint(
            date=day,
            actual_usd=round(acs_by_day[day][0], 4) if day in acs_by_day else 0.0,
            baseline_usd=round(acs_by_day[day][1], 4) if day in acs_by_day else 0.0,
            counted_turns=acs_by_day_counted.get(day, 0),
            total_turns=acs_by_day_total[day],
        )
        for day in sorted(acs_by_day_total)
    ]
    acs_total_actual = round(sum(p.actual_usd for p in acs_daily), 4)
    acs_total_baseline = round(sum(p.baseline_usd for p in acs_daily), 4)

    return CostEfficiencyResult(
        has_data=counted > 0,
        daily=daily,
        total_actual_usd=round(total_actual, 4),
        total_baseline_usd=round(total_baseline, 4),
        savings_percent=round(savings_pct, 2),
        model_mix=dict(model_mix),
        acs_daily=acs_daily,
        acs_total_actual_usd=acs_total_actual,
        acs_total_baseline_usd=acs_total_baseline,
        acs_model_mix=dict(acs_model_mix),
        model_cost=_round_cost(model_cost),
        acs_model_cost=_round_cost(acs_model_cost),
    )


def _round_cost(acc: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """Round the per-model dollars for transport. 4 decimals: a single cheap
    turn costs ~$0.0002, and rounding to cents would report it as free."""
    return {
        model: {
            "actual_usd": round(v["actual_usd"], 4),
            "baseline_usd": round(v["baseline_usd"], 4),
            "turns": int(v["turns"]),
        }
        for model, v in acc.items()
    }


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

    # The SAME counting window the cost view uses. Two sections of one card
    # counting over different periods is precisely what the window exists to
    # prevent; the honest consequence — few samples right after a reset — is the
    # one the cost section already carries.
    try:
        from core.console.corvin_console import usage_epoch  # noqa: PLC0415

        since_ts = usage_epoch.epoch_ts(tenant_id)
    except Exception:  # noqa: BLE001
        since_ts = 0.0

    turns = [
        t for t in _read_completed_turns(chain_path, _MAX_SCAN_BYTES)
        if not since_ts or float(t.get("completed_ts") or 0) >= since_ts
    ]
    if not turns:
        return []

    tools_called_values = [int(t.get("tools_called") or 0) for t in turns]
    bounds = _tier_bounds(tools_called_values)
    cap = _complexity_cap(tools_called_values)

    # Bucketed by tier only — pooled across whatever emitted the turn
    # (console web chat, always "assistant"; a bridge conversation, which
    # can carry a real routed persona like "coder"). A per-persona split
    # here meant the dashboard silently doubled every tier's card whenever
    # a bridge conversation used a non-default persona, which told the
    # operator nothing useful (2026-09-12 operator feedback) — the
    # dashboard's question is "how complex are successful turns at this
    # tier", not "...for this persona". `persona` is still captured in
    # `_read_completed_turns` and available to any future consumer that
    # does need the split.
    buckets: dict[str, _Bucket] = defaultdict(_Bucket)
    for t in turns:
        tools_called = int(t.get("tools_called") or 0)
        tier = _tier_for(tools_called, bounds)
        success = t.get("exit_code") == 0 and not t.get("timed_out")

        b = buckets[tier]
        b.total += 1
        if success:
            b.successes += 1
            b.complexity_sum_success += min(1.0, tools_called / cap)
        if t.get("model"):
            b.model_counts[t["model"]] += 1

        priced = _price_and_baseline(
            t.get("model") or "",
            int(t.get("input_tokens") or 0),
            int(t.get("output_tokens") or 0),
            int(t.get("cache_creation_input_tokens") or 0),
            int(t.get("cache_read_input_tokens") or 0),
        )
        if priced is not None and any((
            t.get("input_tokens"), t.get("output_tokens"),
            t.get("cache_creation_input_tokens"), t.get("cache_read_input_tokens"),
        )):
            b.actual_usd += priced[0]
            b.baseline_usd += priced[1]
            b.priced_turns += 1

    now = datetime.now(timezone.utc).isoformat()
    results: list[StoredThreshold] = []
    for tier, b in sorted(buckets.items()):
        learned = (b.complexity_sum_success / b.successes) if b.successes else 0.5
        learned = max(0.1, min(0.9, learned))
        success_rate = (b.successes / b.total) if b.total else 0.0
        dominant_model = max(b.model_counts, key=b.model_counts.get) if b.model_counts else "n/a"

        results.append(
            StoredThreshold(
                task_type=tier,
                subsystem="all",
                tenant_id=tenant_id,
                learned_threshold=learned,
                base_threshold=0.5,
                timestamp=now,
                sample_count=b.total,
                converged=b.total >= _MIN_SAMPLES_CONVERGED,
                success_rate=success_rate,
                notes=(
                    f"descriptive stat from {b.total} real os_turn events "
                    f"({success_rate:.0%} success; dominant model: {dominant_model}); "
                    f"NOT a live routing decision — see module docstring"
                ),
                dominant_model=dominant_model,
                actual_usd=round(b.actual_usd, 4),
                baseline_usd=round(b.baseline_usd, 4),
                priced_turns=b.priced_turns,
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
