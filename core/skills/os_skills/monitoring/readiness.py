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
#: ``CONSOLE_SOAK_S``. The soak starts at the LATER of (a) the oldest console decision
#: of the current unbroken dual_write run, (b) the last rollback trip and (c) the last
#: rollback reset. The run is walked newest-first over the console decisions that
#: CONSULTED the gate (ledger ``gate`` = ``verdict``; a line without ``gate`` reads as
#: one) or would have (``gate`` = ``pending``), and it is broken by (1) any such
#: decision recorded with phase ``shadow`` — the env switched back, a refusal, a
#: not-ready verdict, a rollback, and every ``pending`` decision: no verdict existed,
#: so the turn was served shadow (review r3_s2-2: one dual_write burst a day kept the
#: run alive while 96 % of the console's turns were pending shadow) — and (2) a gap of
#: more than ``SOAK_MAX_GAP_S`` between two consecutive dual_write decisions, or
#: between the newest one and now. In addition, at least ``SOAK_MIN_DUAL_WRITE_SHARE``
#: of ALL console decisions since the soak start (``post_hoc`` ones included: pinned
#: to shadow) must be dual_write, or there is no soak. The phase read is the EFFECTIVE
#: one recorded per decision, not the requested ``CORVIN_ACP_PHASE``. Review r2_s2-3:
#: shadow decisions between dual_write ones were ignored, so a console in shadow 96 %
#: of a week completed the soak.
CONSOLE_SOAK_S = 7 * 86400
SOAK_MAX_GAP_S = 86400
SOAK_MIN_DUAL_WRITE_SHARE = 0.95
_CACHE_TTL_S = 300.0
#: Stale-while-revalidate bound. A verdict older than ``_CACHE_TTL_S`` is refreshed,
#: and until the refresh lands the last verdict is served, as long as it is younger
#: than this. Older, missing, or invalidated by :func:`clear_cache` (a rollback trip or
#: reset of a tenant bumps THAT tenant's generation and drops only its verdicts) -> the
#: ``evaluating`` placeholder,
#: which ``dual_write.effective_phase`` serves as shadow. Review r2_s2-1: answering
#: ``evaluating`` for every merely stale entry served shadow to every turn that came
#: more than 300 s after the previous one (66 % of the live console's turns).
_CACHE_MAX_STALE_S = 3600.0
#: ``(monotonic time, verdict, rollback state stamp)`` per (tenant, surface). The stamp
#: is ``rollback_detector.state_stamp`` when the verdict was computed: a trip or reset
#: written by ANY process changes it, and a verdict under another stamp is treated as
#: invalidated (review r3_s2-4: ``clear_cache`` is process-local).
_cache: dict[tuple[str, str], tuple[float, "Readiness", tuple]] = {}


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
    #: The ledger's read tail (``routing_ledger._READ_TAIL_BYTES``) began INSIDE the
    #: ``WINDOW_S`` window: every number above covers only the visible part of it
    #: (``routing_ledger.read_window``; review DLG-24). Reported on every verdict.
    window_truncated: bool = False


def _complexity(row: dict) -> str:
    return (row.get("features") or {}).get("complexity", "unknown")


def console_soak_start(records: list[dict], tenant_id: str, *,
                       now: float) -> Optional[float]:
    """Start of the console's current continuous dual_write soak, or None.

    The rule is stated at ``CONSOLE_SOAK_S``. None — no soak — when the newest
    gate-consulting console decision is shadow, when the console has no dual_write
    decision in the last ``SOAK_MAX_GAP_S`` or when the rollback history cannot be
    read (fail-closed).
    """
    console = [r for r in records
               if r.get("kind") == "decision" and r.get("surface") == "console"]
    consulted = sorted(
        ((r["ts"], r.get("phase")) for r in console
         if r.get("gate", "verdict") in ("verdict", "pending")),
        key=lambda x: x[0], reverse=True)
    start: Optional[float] = None
    prev = now
    for ts, phase in consulted:
        if phase != "dual_write" or prev - ts > SOAK_MAX_GAP_S:
            break
        start = prev = ts
    if start is None:
        return None
    changed, readable = rollback_detector.last_change_ts(tenant_id)
    if not readable:
        return None
    if changed is not None:
        start = max(start, changed)
    inside = [r for r in console if r["ts"] >= start]
    dual = sum(1 for r in inside if r.get("phase") == "dual_write")
    if not inside or dual / len(inside) < SOAK_MIN_DUAL_WRITE_SHARE:
        return None
    return start


def evaluate(tenant_id: str, surface: str, *, now: Optional[float] = None) -> Readiness:
    """The verdict over the last ``WINDOW_S`` of the ledger.

    When the ledger's read tail starts inside the window (a busy install: the tail
    holds ~17 k turns) the verdict says so with ``window_truncated`` — and on the
    bridge, whose console-soak gate needs ``CONSOLE_SOAK_S`` of contiguous history,
    a soak that cannot be established on the visible part answers
    ``window_truncated`` instead of ``console_soak_incomplete``: the evidence was
    cut, not missing (review DLG-24, 2026-11-05).
    """
    now = time.time() if now is None else now
    if rollback_detector.rollback_active(tenant_id):
        return Readiness(False, "rollback_active", 0, None, 0)
    records, truncated_from = routing_ledger.read_window(tenant_id, since_ts=now - WINDOW_S)
    verdict = _judge(records, tenant_id, surface, now=now, truncated_from=truncated_from)
    if truncated_from is not None:
        verdict = dataclasses.replace(verdict, window_truncated=True)
    return verdict


def _judge(records: list[dict], tenant_id: str, surface: str, *, now: float,
           truncated_from: Optional[float]) -> Readiness:
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
            # A soak the visible tail is too short to contain is unprovable, not
            # incomplete: name the cut (DLG-24) — with less than CONSOLE_SOAK_S
            # visible, waiting can never complete it.
            if truncated_from is not None and now - truncated_from < CONSOLE_SOAK_S:
                return Readiness(False, "window_truncated", n, join_rate,
                                 len(disagree), delegating, nj)
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
#: ``_generation`` is the whole-cache one (``clear_cache()``), ``_tenant_generation``
#: the per-tenant one (``clear_cache(tenant_id)``): a trip or reset of tenant X
#: invalidates only X's verdicts (review r4_s2-2: a global clear served every other
#: tenant's next console turn as ``pending`` shadow and restarted its bridge soak).
_generation = 0
_tenant_generation: dict[str, int] = {}


def _stamp(tenant_id: str) -> tuple:
    try:
        return rollback_detector.state_stamp(tenant_id)
    except Exception:  # noqa: BLE001 — unknown: never equal to a stored stamp
        return ("error", time.monotonic_ns())


def _refresh(key: tuple[str, str]) -> None:
    with _lock:
        gen = (_generation, _tenant_generation.get(key[0], 0))
    try:
        rollback_detector.maybe_retry_pending(key[0])
    except Exception:  # noqa: BLE001 — the marker keeps restricting either way
        pass
    stamp = _stamp(key[0])
    try:
        result = evaluate(*key)
    except Exception:  # noqa: BLE001 — an unreadable ledger is simply "not ready"
        result = Readiness(False, "evaluation_failed", 0, None, 0)
    changed = _stamp(key[0]) != stamp
    with _lock:
        if gen != (_generation, _tenant_generation.get(key[0], 0)):
            return   # invalidated mid-flight: never store a pre-invalidation verdict
        _refreshing.discard(key)
        if changed:
            return   # another process tripped/reset mid-flight: same rule
        _cache[key] = (time.monotonic(), result, stamp)


def cached_evaluate(tenant_id: str, surface: str, *, background: bool = True) -> Readiness:
    """Never scans the ledger on the caller's thread when ``background`` is True.

    The console calls this from inside ``stream_turn``; a ledger scan there is a
    synchronous file read on the event loop (the 2026-09-13 stall shape).

    Stale-while-revalidate (review r2_s2-1): a verdict younger than ``_CACHE_TTL_S``
    is served as is; an older one is refreshed on a daemon thread and served until
    the refresh lands, up to ``_CACHE_MAX_STALE_S``. A missing verdict, one older
    than that, and anything invalidated — by :func:`clear_cache` in this process, or
    by a rollback trip/reset written by ANY process (the rollback state's stamp —
    generation, inode, mtime — differs from the one the verdict was computed under;
    review r3_s2-4) — answer "not ready" (reason ``evaluating``) until a fresh one
    lands. The only rollback read here is that stamp (one ``stat`` and a small
    read); ``effective_phase`` checks ``rollback_active`` before it asks.
    """
    key = (tenant_id, surface)
    stamp = _stamp(tenant_id)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[2] != stamp:
            _cache.pop(key, None)   # tripped/reset by another process: invalidated
            hit = None
        age = None if hit is None else now - hit[0]
        fresh = age is not None and age < _CACHE_TTL_S
        servable = age is not None and age < _CACHE_MAX_STALE_S
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
            if (hit is not None and hit[2] == stamp
                    and time.monotonic() - hit[0] < _CACHE_MAX_STALE_S):
                return hit[1]
            return _EVALUATING
    if start:
        threading.Thread(target=_refresh, args=(key,), daemon=True,
                         name="l5-readiness").start()
    return hit[1] if servable else _EVALUATING


def clear_cache(tenant_id: Optional[str] = None) -> None:
    """Invalidate cached verdicts: only ``tenant_id``'s (every surface) when given —
    what a rollback trip or reset does — else the whole cache (tests)."""
    global _generation
    with _lock:
        if tenant_id is None:
            _generation += 1
            _cache.clear()
            _refreshing.clear()
            return
        _tenant_generation[tenant_id] = _tenant_generation.get(tenant_id, 0) + 1
        for key in [k for k in _cache if k[0] == tenant_id]:
            _cache.pop(key, None)
        for key in [k for k in _refreshing if k[0] == tenant_id]:
            _refreshing.discard(key)
