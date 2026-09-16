"""Marketplace Hub (Phase 2 Tier 3) — Discovery + Cards"""

from fastapi import APIRouter

router = APIRouter(prefix="/v1/marketplace/hub", tags=["marketplace"])

@router.get("/cards")
async def get_hub_cards() -> dict:
    """Get marketplace hub cards (5 main categories)."""
    return {
        "cards": [
            {
                "id": "skills",
                "title": "Skills Marketplace",
                "description": "100+ reusable skills for routing, learning, orchestration",
                "icon": "sparkles",
                "count": 102,
                "link": "/marketplace/skills"
            },
            {
                "id": "plugins",
                "title": "Plugin Registry",
                "description": "Vetted plugins for compliance, observability, integrations",
                "icon": "puzzle",
                "count": 45,
                "link": "/marketplace/plugins"
            },
            {
                "id": "workflows",
                "title": "Workflow Templates",
                "description": "Pre-built task workflows for common patterns",
                "icon": "workflow",
                "count": 28,
                "link": "/marketplace/workflows"
            },
            {
                "id": "models",
                "title": "Model Adapters",
                "description": "Connect Anthropic + Bedrock + Vertex + more",
                "icon": "cpu",
                "count": 8,
                "link": "/marketplace/models"
            },
            {
                "id": "integrations",
                "title": "Integrations",
                "description": "Connect to Slack, GitHub, Linear, DataDog, etc.",
                "icon": "link",
                "count": 34,
                "link": "/marketplace/integrations"
            }
        ]
    }

@router.get("/search")
async def search_marketplace(q: str) -> dict:
    """Search marketplace."""
    return {"results": [], "query": q}

@router.get("/featured")
async def get_featured() -> dict:
    """Get featured items."""
    return {"featured": []}
