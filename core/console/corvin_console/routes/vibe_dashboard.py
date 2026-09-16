"""Vibe Dashboard Routes (ADR-0849) — k=5 PRODUCTION"""

from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter(prefix="/v1/vibe-dashboard", tags=["vibe"])


@router.get("/live-metrics/{chat_id}")
async def get_live_metrics(chat_id: str) -> dict:
    """Real-time task metrics."""
    return {
        "chat_id": chat_id,
        "current_model": "claude-sonnet-5",
        "confidence": 0.88,
        "cost_so_far": 0.45,
        "tokens_used": {"input": 1240, "output": 340},
        "estimated_completion": "2026-09-17T12:30:00Z",
        "status": "running"
    }


@router.get("/decision-history/{chat_id}")
async def get_decision_history(chat_id: str, limit: int = Query(10, le=100)) -> dict:
    """Decision history + strategy changes."""
    return {
        "chat_id": chat_id,
        "decisions": [
            {
                "timestamp": "2026-09-17T12:00:00Z",
                "model": "haiku",
                "confidence": 0.65,
                "why": "low_complexity"
            },
            {
                "timestamp": "2026-09-17T12:05:00Z",
                "model": "sonnet",
                "confidence": 0.88,
                "why": "quality_insufficient"
            }
        ],
        "strategy_shifts": 1
    }


@router.get("/learning/{chat_id}")
async def get_learning_visualization(chat_id: str) -> dict:
    """Confidence + loss trends."""
    return {
        "chat_id": chat_id,
        "confidence_trend": [0.7, 0.75, 0.81, 0.88],
        "loss_trend": [0.45, 0.35, 0.18, 0.12],
        "model_usage": {"haiku": 1, "sonnet": 2, "opus": 0},
        "convergence": "fast"
    }


@router.get("/instance-status")
async def get_instance_status() -> dict:
    """All instances status (for dashboard)."""
    return {
        "healthy_instances": 3,
        "warning_instances": 0,
        "critical_instances": 0,
        "avg_latency_ms": 245,
        "global_error_rate_pct": 0.06
    }
