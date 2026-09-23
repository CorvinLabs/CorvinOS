"""Skills monitoring endpoints for operator dashboard (Phase 8 k=2).

Exposes cache stats, circuit-breaker state, rate-limiter metrics, and the
learning dashboard views over the installed OS-Skills.

Endpoints (mounted under /v1/console by app.py):
  - GET  /api/skills/cache-stats
  - GET  /api/skills/circuit-breaker
  - GET  /api/skills/rate-limiter/<client_id>
  - GET  /api/skills/health
  - POST /api/skills/cache/clear
  - GET  /api/skills/status
  - GET  /api/skills/{skill_id}/metrics

Auth + tenant (adversarial review D-01): every route requires a live console
session (``require_session``) and derives the tenant from
``rec.tenant_id`` — never from a query parameter, never from an env var
(CLAUDE.md, ADR-0007). The mutation (``cache/clear``) additionally requires
the CSRF token (``require_csrf``). The previous version imported a
``get_current_user`` that did not exist and fell back to a stub that
authenticated nobody and read ``tenant_id`` from the query string.

Shared state (D-10d): the resolver is the per-tenant process singleton
(``resolver_for``) that the hardening layer and the CLI share — a fresh
resolver per request reported an empty cache on every call, so
``/cache-stats`` was always 0, ``/cache/clear`` cleared nothing and
``/health`` was permanently unhealthy.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from core.skills.corvin_skills.hardening import SkillServiceHardening
from core.skills.corvin_skills.resolver import SkillDependencyResolver, resolver_for
from core.skills.skill_manager import SkillManager

from .. import _bootstrap
from .. import auth as session_auth
from ..deps import require_csrf, require_session

_forge_paths = _bootstrap.forge_paths
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])

# Singleton hardening instance (shared across requests)
_hardening_instance: Optional[SkillServiceHardening] = None


def get_hardening() -> SkillServiceHardening:
    """Get or initialize hardening instance."""
    global _hardening_instance
    if _hardening_instance is None:
        _hardening_instance = SkillServiceHardening(
            rate_limit_per_minute=1000,
            request_timeout_seconds=5.0,
            connection_timeout_seconds=2.0,
        )
    return _hardening_instance


def get_resolver(tenant_id: str) -> SkillDependencyResolver:
    """The per-tenant resolver singleton (shared with hardening + CLI)."""
    return resolver_for(tenant_id)


def _skill_manager(tenant_id: str) -> SkillManager:
    """SkillManager rooted at the REAL corvin home (honours CORVIN_HOME)."""
    return SkillManager(_forge_paths.corvin_home(), tenant_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/cache-stats", summary="Get cache statistics")
async def get_cache_stats(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Get cache hit-rate, size, eviction stats for the session's tenant."""
    resolver = get_resolver(rec.tenant_id)
    stats = resolver.stats()

    recommendations = []
    if stats.get("hit_rate", 0) < 0.7:
        recommendations.append("Cache hit-rate below 70% target — consider increasing TTL or max_size")
    if stats.get("evictions", 0) > 100:
        recommendations.append("High eviction rate — consider increasing max_size")

    return {
        **stats,
        "tenant_id": rec.tenant_id,
        "recommendations": recommendations,
        "timestamp": _now(),
    }


@router.get("/circuit-breaker", summary="Get circuit-breaker state")
async def get_circuit_breaker(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Get circuit-breaker health status."""
    hardening = get_hardening()
    state = hardening.circuit_breaker.state_info()

    recommendations = []
    if state["state"] == "OPEN":
        recommendations.append("Circuit breaker OPEN — manifest loading is failing; check disk I/O")
    elif state["state"] == "HALF_OPEN":
        recommendations.append("Circuit breaker HALF_OPEN — recovery in progress")

    return {
        **state,
        "recommendations": recommendations,
        "timestamp": _now(),
    }


@router.get("/rate-limiter/{client_id}", summary="Get rate-limiter state")
async def get_rate_limiter(
    client_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Get rate-limiter token state for a client."""
    hardening = get_hardening()
    state = hardening.rate_limiter.get_bucket_state(client_id)
    rate_limit = hardening.rate_limiter.rate_limit_per_minute
    refill_rate = hardening.rate_limiter.refill_rate_per_second

    if state["tokens"] > rate_limit * 0.5:
        quota_status = "GREEN"
    elif state["tokens"] >= 1:
        quota_status = "YELLOW"
    else:
        quota_status = "RED"

    return {
        **state,
        "rate_limit_per_minute": rate_limit,
        "refill_rate_per_second": refill_rate,
        "quota_status": quota_status,
        "timestamp": _now(),
    }


@router.get("/health", summary="Get overall health status")
async def get_health(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Overall Skill System health (cache + circuit-breaker + rate-limit) for the tenant."""
    hardening = get_hardening()
    resolver = get_resolver(rec.tenant_id)

    health = hardening.health_status()
    cache_stats = resolver.stats()

    total_lookups = cache_stats.get("hits", 0) + cache_stats.get("misses", 0)
    # A cache that has not been asked anything yet is not "unhealthy".
    cache_ok = total_lookups == 0 or cache_stats.get("hit_rate", 0) > 0.5
    is_healthy = health["circuit_breaker"]["state"] == "CLOSED" and cache_ok

    return {
        "healthy": is_healthy,
        "tenant_id": rec.tenant_id,
        "cache": cache_stats,
        "hardening": health,
        "timestamp": _now(),
    }


@router.post("/cache/clear", summary="Clear skill cache")
async def clear_cache(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Clear the cache of the session's tenant (admin operation, CSRF-gated)."""
    resolver = get_resolver(rec.tenant_id)
    stats_before = resolver.stats()
    resolver.invalidate()

    return {
        "status": "success",
        "tenant_id": rec.tenant_id,
        "entries_cleared": stats_before.get("size", 0),
        "timestamp": _now(),
    }


# === Phase 5: Learning Dashboard Endpoints ===


@router.get("/status", summary="Get all skills status")
async def get_skills_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Status of all active (enabled, path-verified) skills of the session's tenant."""
    tenant_id = rec.tenant_id
    try:
        skill_mgr = _skill_manager(tenant_id)
        active_skills = skill_mgr.list_active_skills()
    except Exception as exc:  # noqa: BLE001 — dashboard must render, not 500
        logger.warning("skills status unavailable for tenant %s: %s", tenant_id, exc)
        return {"tenant_id": tenant_id, "skills": [], "error": type(exc).__name__, "timestamp": _now()}

    skills_list = []
    for skill_id in active_skills:
        try:
            status = skill_mgr.get_skill_status(skill_id)
        except Exception as exc:  # noqa: BLE001 — one skill must not break the dashboard
            logger.warning("Failed to get status for %s: %s", skill_id, exc)
            continue
        if status is None:
            continue
        health = "healthy"
        if status.errors_24h > 5:
            health = "error"
        elif status.errors_24h > 0:
            health = "degraded"
        skills_list.append({
            "id": skill_id,
            "version": status.version,
            "enabled": status.enabled,
            "score": status.score,
            "runs_24h": status.runs_24h,
            "errors_24h": status.errors_24h,
            "last_run": None,  # TODO: track from grading_stats
            "status": health,
        })

    return {"tenant_id": tenant_id, "skills": skills_list, "timestamp": _now()}


@router.get("/{skill_id}/metrics", summary="Get skill learning metrics")
async def get_skill_metrics(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Detailed learning metrics for a skill (Phase 5.2) of the session's tenant.

    Loads grading_stats.json + feedback_log.jsonl to calculate:
    - Score progression over epochs
    - Feedback breakdown (by outcome, task_shape, decision)
    - Anomalies (unusual patterns)
    - Recommendations (convergence hints)
    """
    try:
        skill_mgr = _skill_manager(rec.tenant_id)
        skill_path = skill_mgr.registry.get_skill_path(skill_id)
        entry = skill_mgr.registry.get_entry(skill_id) or {}

        if not skill_path:
            raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")

        # Load grading_stats.json
        grading_stats_file = skill_path / 'grading_stats.json'
        score_history = []

        if grading_stats_file.exists():
            try:
                with open(grading_stats_file) as f:
                    grading_stats = json.load(f)
                    for epoch in grading_stats.get('epochs', []):
                        score_history.append({
                            'epoch': epoch.get('epoch'),
                            'score': epoch.get('score'),
                            'timestamp': epoch.get('timestamp'),
                        })
            except (json.JSONDecodeError, KeyError, OSError):
                pass

        # Load feedback_log.jsonl
        feedback_log_file = skill_path / 'feedback_log.jsonl'
        feedback_breakdown = {
            'by_outcome': {'success': 0, 'failure': 0},
            'by_task_shape': {},
            'by_decision': {},
        }
        total_runs = 0
        total_errors = 0

        if feedback_log_file.exists():
            try:
                with open(feedback_log_file) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        payload = event.get('payload', {})

                        outcome = payload.get('outcome')
                        if outcome:
                            feedback_breakdown['by_outcome'][outcome] = feedback_breakdown['by_outcome'].get(outcome, 0) + 1

                        task_shape = payload.get('task_shape')
                        if task_shape:
                            feedback_breakdown['by_task_shape'][task_shape] = feedback_breakdown['by_task_shape'].get(task_shape, 0) + 1

                        decision = payload.get('decision')
                        if decision:
                            feedback_breakdown['by_decision'][decision] = feedback_breakdown['by_decision'].get(decision, 0) + 1

                        total_runs += 1
                        if outcome == 'failure':
                            total_errors += 1
            except OSError:
                pass

        # Calculate score trend
        score_trend = 0.0
        if len(score_history) >= 2:
            recent_score = score_history[-1]['score'] or 0
            baseline_score = score_history[0]['score'] or 0
            if baseline_score > 0:
                score_trend = (recent_score - baseline_score) / baseline_score

        # Detect anomalies
        anomalies = []
        if total_errors > total_runs * 0.2:
            anomalies.append(f"High error rate: {total_errors}/{total_runs} ({100*total_errors//total_runs}%)")
        if len(score_history) > 5:
            last_5_scores = [s['score'] for s in score_history[-5:] if s['score']]
            if last_5_scores and last_5_scores[-1] < 0.5:
                anomalies.append("Score below 50% — learning may be stalled")
            if len(set(last_5_scores)) == 1:
                anomalies.append("Score plateau detected — no improvement over last 5 epochs")

        return {
            "skill_id": skill_id,
            "tenant_id": rec.tenant_id,
            "version": str(entry.get("version", "")),
            "metrics": {
                "total_runs": total_runs,
                "total_errors": total_errors,
                "score_history": score_history,
                "score_trend": round(score_trend, 3),
                "feedback_breakdown": feedback_breakdown,
                "anomalies": anomalies,
            },
            "recommendations": [
                "Monitor learning convergence via score_trend" if score_trend != 0 else "Learning loop in progress",
                f"Error rate: {100*total_errors//max(total_runs, 1):.1f}% — investigate if >20%" if total_errors > 0 else "No errors detected",
                "Run E2E tests to validate learning quality" if total_runs > 50 else "Collect more feedback before evaluation",
            ],
            "timestamp": _now(),
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("metrics failed for %s: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to load metrics: {type(exc).__name__}")
