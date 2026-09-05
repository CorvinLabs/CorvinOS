"""Console API: Orchestration History + Performance (ADR-0612)."""

from __future__ import annotations

import logging
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query
except ImportError:
    APIRouter = None

from core.skills.orchestration.learning_integration import get_learner

log = logging.getLogger(__name__)


def create_orchestration_router() -> APIRouter:
    """Create FastAPI router for orchestration visibility."""
    if APIRouter is None:
        raise ImportError("FastAPI not installed")

    router = APIRouter(prefix="/api/skills", tags=["orchestration"])

    @router.get("/{skill_id}/orchestration/history")
    async def get_orchestration_history(
        skill_id: str,
        limit: int = Query(100, ge=1, le=1000),
    ) -> dict[str, Any]:
        """GET /api/skills/{skill_id}/orchestration/history — Recent invocations."""
        learner = get_learner()
        model = learner.get_model(skill_id)

        if not model:
            raise HTTPException(
                status_code=404,
                detail=f"No orchestration data for skill {skill_id}",
            )

        return {
            "skill_id": skill_id,
            "total_invocations": sum(s.invocations for s in model.stats.values()),
            "model_confidence": model.confidence,
            "plugin_performance": [
                {
                    "plugin_id": stats.plugin_id,
                    "capability_id": stats.capability_id,
                    "invocations": stats.invocations,
                    "success_rate": stats.success_rate,
                    "p50_latency_ms": stats.p50_latency_ms,
                    "slo_met_rate": stats.slo_met_rate,
                }
                for stats in model.stats.values()
            ],
        }

    @router.get("/{skill_id}/orchestration/recommendation")
    async def get_recommendation(
        skill_id: str,
        capability_id: str,
        allowed_plugins: str = Query("", description="Comma-separated list"),
    ) -> dict[str, Any]:
        """GET /api/skills/{skill_id}/orchestration/recommendation — Plugin recommendation."""
        learner = get_learner()
        plugins = [p.strip() for p in allowed_plugins.split(",") if p.strip()]

        if not plugins:
            raise HTTPException(
                status_code=400,
                detail="allowed_plugins required",
            )

        recommendation = learner.recommend(skill_id, capability_id, plugins)

        if not recommendation:
            return {
                "skill_id": skill_id,
                "capability_id": capability_id,
                "recommendation": None,
                "reason": "No performance data available",
            }

        plugin_id, confidence = recommendation
        return {
            "skill_id": skill_id,
            "capability_id": capability_id,
            "recommendation": {
                "plugin_id": plugin_id,
                "confidence": confidence,
            },
        }

    @router.post("/{skill_id}/orchestration/feedback/{invocation_id}")
    async def submit_feedback(
        skill_id: str,
        invocation_id: str,
        rating: str = Query("neutral", regex="^(good|neutral|bad)$"),
    ) -> dict[str, Any]:
        """POST /api/skills/{skill_id}/orchestration/feedback/{invocation_id} — Submit feedback."""
        # Mock: real implementation would wire to learning backend
        return {
            "invocation_id": invocation_id,
            "skill_id": skill_id,
            "feedback_received": True,
            "rating": rating,
            "note": "Feedback recorded (mock)",
        }

    return router
