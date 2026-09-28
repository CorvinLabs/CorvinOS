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
#: ADR-2092 G4 rollout order: the bridge may enter Phase 2 only after the console
#: has run it for this long without a rollback trip.
CONSOLE_SOAK_S = 7 * 86400
_CACHE_TTL_S = 300.0
_cache: dict[tuple[str, str], tuple[float, "Readiness"]] = {}


@dataclasses.dataclass(frozen=True)
class Readiness:
    ready: bool
    reason: str
    decisions: int
    join_rate: Optional[float]
    disagreements: int


def _complexity(row: dict) -> str:
    return (row.get("features") or {}).get("complexity", "unknown")


def evaluate(tenant_id: str, surface: str, *, now: Optional[float] = None) -> Readiness:
    now = time.time() if now is None else now
    if rollback_detector.rollback_active(tenant_id):
        return Readiness(False, "rollback_active", 0, None, 0)
    records = routing_ledger.read_records(tenant_id, since_ts=now - WINDOW_S)
    if surface == "bridge":
        console_live = [r["ts"] for r in records if r.get("kind") == "decision"
                        and r.get("surface") == "console" and r.get("phase") == "dual_write"]
        if not console_live or now - min(console_live) < CONSOLE_SOAK_S:
            return Readiness(False, "console_soak_incomplete", 0, None, 0)
    joined = routing_ledger.join(records, surface=surface)
    summary = routing_ledger.summarize(records, surface=surface)
    n, join_rate = summary["decisions"], summary["join_rate"]
    disagree = [r for r in joined if r.get("skill") == "native" and r.get("bundled") != "native"
                and not (r.get("features") or {}).get("force_delegate")]
    if n < MIN_READY_SAMPLES:
        return Readiness(False, "insufficient_samples", n, join_rate, len(disagree))
    if join_rate is None or join_rate < MIN_JOIN_RATE:
        return Readiness(False, "join_rate_below_floor", n, join_rate, len(disagree))
    if not disagree:
        return Readiness(False, "skill_never_disagrees", n, join_rate, 0)
    for bucket in sorted({_complexity(r) for r in disagree}):
        delegated = [r for r in disagree if _complexity(r) == bucket and r["outcome"] is not None
                     and r["outcome"].get("used") != "native"]
        native = [r for r in joined if r["outcome"] is not None
                  and r["outcome"].get("used") == "native" and _complexity(r) == bucket]
        if len(delegated) < MIN_BUCKET_SAMPLES or len(native) < MIN_BUCKET_SAMPLES:
            return Readiness(False, f"insufficient_bucket_samples:{bucket}", n, join_rate, len(disagree))
        d_rate = sum(1 for r in delegated if r["outcome"]["ok"]) / len(delegated)
        n_rate = sum(1 for r in native if r["outcome"]["ok"]) / len(native)
        if d_rate - n_rate > MAX_PROXY_DROP:
            return Readiness(False, f"native_trails_delegated:{bucket}", n, join_rate, len(disagree))
    return Readiness(True, "ready", n, join_rate, len(disagree))


_refreshing: set[tuple[str, str]] = set()
_lock = threading.Lock()
_EVALUATING = Readiness(False, "evaluating", 0, None, 0)


def _refresh(key: tuple[str, str]) -> None:
    try:
        result = evaluate(*key)
    except Exception:  # noqa: BLE001 — an unreadable ledger is simply "not ready"
        result = Readiness(False, "evaluation_failed", 0, None, 0)
    with _lock:
        _cache[key] = (time.monotonic(), result)
        _refreshing.discard(key)


def cached_evaluate(tenant_id: str, surface: str, *, background: bool = True) -> Readiness:
    """Never scans the ledger on the caller's thread when ``background`` is True.

    The console calls this from inside ``stream_turn``; a ledger scan there is a
    synchronous file read on the event loop (the 2026-09-13 stall shape). A stale
    or missing verdict is refreshed on a daemon thread and, until it lands, the
    answer is "not ready" — the fail-safe direction.
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
            return _cache.get(key, (0.0, _EVALUATING))[1]
    if start:
        threading.Thread(target=_refresh, args=(key,), daemon=True,
                         name="l5-readiness").start()
    return hit[1] if hit is not None else _EVALUATING


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _refreshing.clear()
