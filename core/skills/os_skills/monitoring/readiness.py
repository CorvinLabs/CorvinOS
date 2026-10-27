"""L5 Phase 2 readiness — the evidence gate before the Skill may route (ADR-2092 G2/G4).

Phase 2 lets the Skill change the served engine, and only ever toward ``native``
(G3). This module answers, from the routing ledger alone, whether that is justified on
a surface. It never guesses: below the sample floors the answer is "not ready" with a
reason, never a recommendation (ADR-0763).

The evidence for a de-escalation is a PROXY and is labelled as one: where the Skill
advised ``native`` and the bundled rule delegated, the delegated turns' success rate is
compared with the success rate of natively served turns of the same complexity class.
Those are different turns, so this cannot prove the counterfactual; it only rules out
activating against visible evidence of harm. The live safety net is the rollback judge.
"""
from __future__ import annotations

import dataclasses
import threading
import time
from typing import Optional

from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

MIN_READY_SAMPLES = 500
MIN_JOIN_RATE = 0.95
MIN_BUCKET_SAMPLES = 30
MAX_PROXY_DROP = 0.02
WINDOW_S = 30 * 86400
#: ADR-2092 G4 rollout order (the soak rule, :func:`console_soak_start`): the bridge
#: may enter Phase 2 only when the console has been in dual_write CONTINUOUSLY for
#: ``CONSOLE_SOAK_S``. The soak starts at the LATER of (a) the first console decision
#: recorded with phase ``dual_write`` in the current unbroken run, (b) the last
#: rollback trip and (c) the last rollback reset. "Unbroken" means no two consecutive
#: console dual_write decisions — and not the newest one and now — are more than
#: ``SOAK_MAX_GAP_S`` apart, so dual_write decisions must be present in the last 24 h
#: and a console that left dual_write (env switched back, a rollback, an idle day)
#: restarts the clock. The phase read is the EFFECTIVE one recorded per decision, not
#: the requested ``CORVIN_ACP_PHASE``.
CONSOLE_SOAK_S = 7 * 86400
SOAK_MAX_GAP_S = 86400
_CACHE_TTL_S = 300.0
_cache: dict[tuple[str, str], tuple[float, "Readiness"]] = {}


@dataclasses.dataclass(frozen=True)
class Readiness:
    ready: bool
    reason: str
    decisions: int
    join_rate: Optional[float]
    disagreements: int
    #: Decisions whose bundled rule chose a non-native engine. Zero means Phase 2
    #: has no subject on this surface: it can only de-escalate TO native.
    delegating_decisions: int = 0
    #: NOT-JUDGED turns (cancelled / orphaned by a restart / no engine ran): counted
    #: here and excluded from ``decisions``, the join rate and every success rate.
    not_judged: int = 0


def _complexity(row: dict) -> str:
    return (row.get("features") or {}).get("complexity", "unknown")


def console_soak_start(records: list[dict], tenant_id: str, *,
                       now: float) -> Optional[float]:
    """Start of the console's current continuous dual_write soak, or None.

    The rule (see ``CONSOLE_SOAK_S``): walk the console ``dual_write`` decisions
    newest-first while each gap (newest-to-now included) is at most
    ``SOAK_MAX_GAP_S``; the oldest decision reached starts the run. The soak starts
    at the later of that and the last rollback trip/reset. None — no soak — when the
    console has no dual_write decision in the last ``SOAK_MAX_GAP_S`` or the rollback
    history cannot be read (fail-closed).
    """
    live = sorted((r["ts"] for r in records if r.get("kind") == "decision"
                   and r.get("surface") == "console" and r.get("phase") == "dual_write"),
                  reverse=True)
    if not live or now - live[0] > SOAK_MAX_GAP_S:
        return None
    start = live[0]
    for ts in live[1:]:
        if start - ts > SOAK_MAX_GAP_S:
            break
        start = ts
    changed, readable = rollback_detector.last_change_ts(tenant_id)
    if not readable:
        return None
    if changed is not None:
        start = max(start, changed)
    return start


def evaluate(tenant_id: str, surface: str, *, now: Optional[float] = None) -> Readiness:
    now = time.time() if now is None else now
    if rollback_detector.rollback_active(tenant_id):
        return Readiness(False, "rollback_active", 0, None, 0)
    records = routing_ledger.read_records(tenant_id, since_ts=now - WINDOW_S)
    # NOT-JUDGED turns (cancelled, orphaned, no engine) leave every number below.
    joined = routing_ledger.judged(routing_ledger.join(records, surface=surface))
    summary = routing_ledger.summarize(records, surface=surface)
    n, join_rate = summary["decisions"], summary["join_rate"]
    nj = summary["not_judged"]
    delegating = sum(1 for r in joined if r.get("bundled") != "native")
    disagree = [r for r in joined if r.get("skill") == "native" and r.get("bundled") != "native"
                and not (r.get("features") or {}).get("force_delegate")]
    if delegating == 0 and n >= MIN_BUCKET_SAMPLES:
        # Checked BEFORE the bridge's console-soak gate: a bridge that never delegates has no
        # subject either, and the soak gate answered first forever (review 2026-10-12, ACP-6).
        # The bundled rule never delegated, so there is nothing to de-escalate: more
        # samples cannot change this, so say so before asking for more. Distinct from
        # a Skill that had the chance to disagree and never did.
        return Readiness(False, "no_delegation_subject", n, join_rate, 0, 0, nj)
    if surface == "bridge":
        soak_start = console_soak_start(records, tenant_id, now=now)
        if soak_start is None or now - soak_start < CONSOLE_SOAK_S:
            # Report the surface's REAL counts: a hard 0 here read as "the bridge
            # produced no decisions" when it had hundreds.
            return Readiness(False, "console_soak_incomplete", n, join_rate,
                             len(disagree), delegating, nj)
    if n < MIN_READY_SAMPLES:
        return Readiness(False, "insufficient_samples", n, join_rate, len(disagree), delegating, nj)
    if join_rate is None or join_rate < MIN_JOIN_RATE:
        return Readiness(False, "join_rate_below_floor", n, join_rate, len(disagree), delegating, nj)
    if not disagree:
        return Readiness(False, "skill_never_disagrees", n, join_rate, 0, delegating, nj)
    for bucket in sorted({_complexity(r) for r in disagree}):
        delegated = [r for r in disagree if _complexity(r) == bucket and r["outcome"] is not None
                     and r["outcome"].get("used") != "native"]
        native = [r for r in joined if r["outcome"] is not None
                  and r["outcome"].get("used") == "native" and _complexity(r) == bucket]
        if len(delegated) < MIN_BUCKET_SAMPLES or len(native) < MIN_BUCKET_SAMPLES:
            return Readiness(False, f"insufficient_bucket_samples:{bucket}", n, join_rate,
                             len(disagree), delegating, nj)
        d_rate = sum(1 for r in delegated if r["outcome"]["ok"]) / len(delegated)
        n_rate = sum(1 for r in native if r["outcome"]["ok"]) / len(native)
        if d_rate - n_rate > MAX_PROXY_DROP:
            return Readiness(False, f"native_trails_delegated:{bucket}", n, join_rate,
                             len(disagree), delegating, nj)
    return Readiness(True, "ready", n, join_rate, len(disagree), delegating, nj)


_refreshing: set[tuple[str, str]] = set()
_lock = threading.Lock()
#: The transient placeholder while a verdict is computed. Not a gate verdict:
#: ``dual_write.effective_phase`` serves shadow for it without auditing a refusal.
EVALUATING_REASON = "evaluating"
_EVALUATING = Readiness(False, EVALUATING_REASON, 0, None, 0)


#: Bumped by :func:`clear_cache`. A refresh that started before an invalidation (a
#: rollback trip/reset) computed its verdict on the old state and is discarded.
_generation = 0


def _refresh(key: tuple[str, str]) -> None:
    with _lock:
        gen = _generation
    try:
        result = evaluate(*key)
    except Exception:  # noqa: BLE001 — an unreadable ledger is simply "not ready"
        result = Readiness(False, "evaluation_failed", 0, None, 0)
    with _lock:
        if gen != _generation:
            return   # invalidated mid-flight: never store a pre-invalidation verdict
        _cache[key] = (time.monotonic(), result)
        _refreshing.discard(key)


def cached_evaluate(tenant_id: str, surface: str, *, background: bool = True) -> Readiness:
    """Never scans the ledger on the caller's thread when ``background`` is True.

    The console calls this from inside ``stream_turn``; a ledger scan there is a
    synchronous file read on the event loop (the 2026-09-13 stall shape). A stale
    or missing verdict is refreshed on a daemon thread and, until it lands, the
    answer is "not ready" (reason ``evaluating``) — the fail-safe direction: a
    stale verdict, "ready" included, is never served while its refresh runs.
    """
    key = (tenant_id, surface)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        fresh = hit is not None and now - hit[0] < _CACHE_TTL_S
        start = not fresh and key not in _refreshing
        if start:
            _refreshing.add(key)
    if fresh:
        return hit[1]
    if not background:
        if start:
            _refresh(key)
        with _lock:
            hit = _cache.get(key)
            if hit is not None and time.monotonic() - hit[0] < _CACHE_TTL_S:
                return hit[1]
            return _EVALUATING
    if start:
        threading.Thread(target=_refresh, args=(key,), daemon=True,
                         name="l5-readiness").start()
    return _EVALUATING


def clear_cache() -> None:
    global _generation
    with _lock:
        _generation += 1
        _cache.clear()
        _refreshing.clear()
