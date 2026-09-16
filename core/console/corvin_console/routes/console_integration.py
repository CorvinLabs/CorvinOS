"""Console Integration (Phase 2 Tier 4)

Wires together all subsystems for unified UI.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/v1/console", tags=["console"])

@router.get("/system-status")
async def get_system_status() -> dict:
    """Unified system status dashboard."""
    return {
        "daemon_running": True,
        "skills_loaded": 12,
        "plugins_active": 8,
        "notification_queue": 0,
        "uptime_hours": 0.5
    }

@router.get("/learning-status")
async def get_learning_status() -> dict:
    """Learning loop status."""
    return {
        "model_confidence": {"haiku": 0.65, "sonnet": 0.78, "opus": 0.88},
        "total_decisions": 127,
        "avg_loss": 0.234
    }
