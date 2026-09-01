"""Skills monitoring endpoints for operator dashboard (Phase 8 k=2).

Exposes cache stats, circuit-breaker state, rate-limiter metrics.
Integrates with hardening layer for production observability.

Endpoints:
  - GET /api/skills/cache-stats
  - GET /api/skills/circuit-breaker
  - GET /api/skills/rate-limiter/<client_id>
  - GET /api/skills/health
  - POST /api/skills/cache/clear
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import json

from core.skills.corvin_skills.resolver import SkillDependencyResolver
from core.skills.corvin_skills.hardening import SkillServiceHardening
# Fallback auth stub if get_current_user not available
try:
    from core.console.corvin_console.auth import get_current_user
except ImportError:
    async def get_current_user():
        return {"user_id": "default", "tenant_id": "_default"}


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


def get_resolver(tenant_id: str = "_default") -> SkillDependencyResolver:
    """Get resolver for tenant."""
    return SkillDependencyResolver(tenant_id=tenant_id)


@router.get("/cache-stats", summary="Get cache statistics")
async def get_cache_stats(
    tenant_id: str = "_default",
    current_user = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get cache hit-rate, size, eviction stats.

    Response:
      {
        "size": int (current entries),
        "max_size": int (capacity),
        "hits": int,
        "misses": int,
        "evictions": int,
        "invalidations": int,
        "hit_rate": float (0.0–1.0),
        "recommendations": [str] (e.g., "hit_rate below 70% target")
      }
    """
    resolver = get_resolver(tenant_id)
    stats = resolver.stats()

    # Add recommendations
    recommendations = []
    if stats.get("hit_rate", 0) < 0.7:
        recommendations.append("Cache hit-rate below 70% target — consider increasing TTL or max_size")
    if stats.get("evictions", 0) > 100:
        recommendations.append("High eviction rate — consider increasing max_size")

    return {
        **stats,
        "recommendations": recommendations,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/circuit-breaker", summary="Get circuit-breaker state")
async def get_circuit_breaker(
    current_user = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get circuit-breaker health status.

    Response:
      {
        "state": "CLOSED" | "OPEN" | "HALF_OPEN",
        "failure_count": int,
        "success_count": int,
        "last_failure_time": datetime | null,
        "recommendations": [str]
      }
    """
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/rate-limiter/{client_id}", summary="Get rate-limiter state")
async def get_rate_limiter(
    client_id: str,
    current_user = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get rate-limiter token state for a client.

    Response:
      {
        "tokens": float (remaining tokens),
        "rate_limit_per_minute": int,
        "refill_rate_per_second": float,
        "quota_status": "GREEN" | "YELLOW" | "RED"
      }
    """
    hardening = get_hardening()
    state = hardening.rate_limiter.get_bucket_state(client_id)
    rate_limit = hardening.rate_limiter.rate_limit_per_minute
    refill_rate = rate_limit / 60

    # Quota status
    if state["tokens"] > rate_limit * 0.5:
        quota_status = "GREEN"
    elif state["tokens"] > 0:
        quota_status = "YELLOW"
    else:
        quota_status = "RED"

    return {
        **state,
        "rate_limit_per_minute": rate_limit,
        "refill_rate_per_second": refill_rate,
        "quota_status": quota_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health", summary="Get overall health status")
async def get_health(
    current_user = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get overall Skill System health (cache + circuit-breaker + rate-limit).

    Response: synthesized health from all components.
    """
    hardening = get_hardening()
    resolver = get_resolver()

    health = hardening.health_status()
    cache_stats = resolver.stats()

    # Overall status
    is_healthy = (
        health["circuit_breaker"]["state"] == "CLOSED"
        and cache_stats.get("hit_rate", 0) > 0.5
    )

    return {
        "healthy": is_healthy,
        "cache": cache_stats,
        "hardening": health,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/cache/clear", summary="Clear skill cache")
async def clear_cache(
    tenant_id: str = "_default",
    current_user = Depends(get_current_user),
) -> Dict[str, Any]:
    """Clear cache for a tenant (admin operation).

    Used after manual manifest updates or troubleshooting.
    """
    resolver = get_resolver(tenant_id)
    stats_before = resolver.stats()
    resolver.invalidate()
    stats_after = resolver.stats()

    return {
        "status": "success",
        "tenant_id": tenant_id,
        "entries_cleared": stats_before.get("size", 0),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# === Phase 5: Learning Dashboard Endpoints ===


@router.get("/status", summary="Get all skills status")
async def get_skills_status(
    tenant_id: str = "_default",
    current_user = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get status of all active skills for dashboard.

    Response:
      {
        "tenant_id": str,
        "skills": [
          {
            "id": str (e.g., "os.delegation_router"),
            "version": str,
            "enabled": bool,
            "score": float (0.0–1.0) | null,
            "runs_24h": int,
            "errors_24h": int,
            "last_run": datetime | null,
            "status": "healthy" | "degraded" | "error"
          },
          ...
        ],
        "timestamp": datetime
      }
    """
    from pathlib import Path
    from core.skills.skill_manager import SkillManager

    try:
        skill_mgr = SkillManager(Path.home() / '.corvin', tenant_id)
        active_skills = skill_mgr.list_active_skills() if hasattr(skill_mgr, 'list_active_skills') else []

        skills_list = []
        for skill_id in active_skills:
            try:
                status = skill_mgr.get_skill_status(skill_id) if hasattr(skill_mgr, 'get_skill_status') else None
                if status:
                    # Determine health status
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
            except Exception as e:
                # Log but continue; one skill failure shouldn't break the dashboard
                import logging
                logging.warning(f"Failed to get status for {skill_id}: {e}")

        return {
            "tenant_id": tenant_id,
            "skills": skills_list,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        # Fallback: return empty list if SkillManager unavailable
        return {
            "tenant_id": tenant_id,
            "skills": [],
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/{skill_id}/metrics", summary="Get skill learning metrics")
async def get_skill_metrics(
    skill_id: str,
    tenant_id: str = "_default",
    current_user = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get detailed learning metrics for a skill.

    Response:
      {
        "skill_id": str,
        "version": str,
        "metrics": {
          "total_runs": int,
          "total_errors": int,
          "score_history": [
            {"epoch": int, "score": float, "timestamp": datetime}
          ],
          "score_trend": float (-1.0 to +1.0, positive = improving),
          "feedback_breakdown": {
            "by_outcome": {"success": int, "failure": int},
            "by_task_shape": {<task>: int},
            "by_decision": {<decision>: int}
          },
          "anomalies": [str] (unusual patterns detected)
        },
        "recommendations": [str],
        "timestamp": datetime
      }
    """
    try:
        from core.skills.skill_manager import SkillManager
        from pathlib import Path

        skill_mgr = SkillManager(Path.home() / '.corvin', tenant_id)

        # Placeholder implementation — real metrics from grading_stats.json
        # TODO: Load actual grading_stats and feedback_log for this skill
        return {
            "skill_id": skill_id,
            "version": "1.0.0",  # TODO: get from manifest
            "metrics": {
                "total_runs": 0,
                "total_errors": 0,
                "score_history": [],
                "score_trend": 0.0,
                "feedback_breakdown": {
                    "by_outcome": {"success": 0, "failure": 0},
                    "by_task_shape": {},
                    "by_decision": {},
                },
                "anomalies": [],
            },
            "recommendations": [
                "Monitor this skill for learning convergence",
                "Check feedback events in audit log if score plateaus",
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load metrics: {str(e)}")
