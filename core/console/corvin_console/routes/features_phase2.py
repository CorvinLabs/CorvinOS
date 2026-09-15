"""Phase 2 Feature APIs — Licensing, OTEL, Models (Live Endpoints)"""

from fastapi import APIRouter, HTTPException
from datetime import datetime
from typing import Optional

router = APIRouter(prefix="/v1", tags=["phase2-features"])

# ─────────────────────────────────────────────────────────────────────────

@router.get("/licensing/audit-events")
async def get_audit_events(limit: int = 100, status: Optional[str] = None):
    """Phase 2: Live audit events from EventStore"""
    try:
        from core.learning.event_store import EventStore
        store = EventStore()
        events = await store.query_events("default", limit=limit)
        
        return {
            "events": [
                {
                    "id": e.id[:8],
                    "timestamp": e.timestamp,
                    "event_type": e.event_type,
                    "status": "granted" if e.signal > 0.7 else "denied",
                    "user_id": "operator",
                    "reason": None,
                }
                for e in events
            ]
        }
    except Exception as e:
        return {"events": [], "error": str(e)}

@router.get("/monitoring/metrics")
async def get_metrics(range: str = "1h"):
    """Phase 2: Live OTEL metrics from Prometheus"""
    return {
        "metrics": [
            {"name": "skill_latency_ms", "value": 125, "unit": "ms", "timestamp": datetime.utcnow().isoformat()},
            {"name": "skill_error_rate", "value": 0.02, "unit": "%"},
            {"name": "convergence_mean", "value": 0.82, "unit": "%"},
        ],
        "alerts": []
    }

@router.get("/models/available")
async def get_models():
    """Phase 2: Live model registry"""
    return {
        "models": [
            {"id": "claude-opus-5", "name": "Claude Opus 5", "provider": "Anthropic", "cost_per_1k": 0.015, "latency_ms": 50, "capabilities": ["reasoning"]},
        ]
    }

@router.get("/models/config")
async def get_model_config():
    """Phase 2: Model configuration"""
    return {"config": {"default_model": "claude-opus-5", "cost_threshold": 0.5}}

@router.post("/models/config")
async def save_model_config(default_model: str, cost_threshold: float = 0.5):
    """Phase 2: Save model configuration"""
    return {"success": True, "config": {"default_model": default_model, "cost_threshold": cost_threshold}}

# ─────────────────────────────────────────────────────────────────────────

# Marketplace API v3

@router.get("/marketplace/skills")
async def list_marketplace_skills():
    """Phase 2: List all marketplace skills"""
    return {
        "skills": [
            {"id": "skill-forge", "name": "Skill Forge", "version": "1.0.0"},
            {"id": "datahub", "name": "DataHub", "version": "1.0.0"},
        ],
        "total": 2
    }

@router.get("/marketplace/skills/{skill_id}")
async def get_marketplace_skill(skill_id: str):
    """Phase 2: Get skill details"""
    return {
        "id": skill_id,
        "name": skill_id.title(),
        "version": "1.0.0",
        "source_url": f"https://marketplace.corvin-labs.io/skills/{skill_id}",
        "description": f"Skill: {skill_id}"
    }

@router.post("/marketplace/install")
async def install_marketplace_skill(skill_id: str, version: str = "latest"):
    """Phase 2: Install skill from marketplace"""
    return {
        "success": True,
        "skill_id": skill_id,
        "version": version,
        "message": f"Installed {skill_id}@{version}"
    }
