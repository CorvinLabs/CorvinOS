"""MCP Tool: learning_loops.list() — List all learning loops with filters.

Lists all learning loops for the current tenant with optional filtering
by plugin, skill, or status.

Tool signature:
  learning_loops.list(
      tenant_id: str,
      plugin_id: Optional[str] = None,
      skill_id: Optional[str] = None,
      status: Optional[str] = None,
      sort_by: str = "plugin_id",  # plugin_id, status, last_event_ts, health_score
      limit: int = 1000
  ) -> List[LearningLoopSummary]

Performance:
- Index-backed queries, <50ms for 1000 entries
- Tenant isolation (fail-closed)

ADR-0907: KG MCP Learning-Loop Tools (Stream 2.3)
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Optional, List, Literal

from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService


class LearningLoopSummary:
    """Summary view of a learning loop (for list() responses)."""

    def __init__(
        self,
        loop_id: str,
        plugin_id: str,
        description: str,
        status: str,
        last_event_ts: datetime,
        event_count_7d: int,
        health_score: float,
        owner_skill: Optional[str] = None,
    ):
        self.loop_id = loop_id
        self.plugin_id = plugin_id
        self.description = description
        self.status = status
        self.last_event_ts = last_event_ts
        self.event_count_7d = event_count_7d
        self.health_score = health_score
        self.owner_skill = owner_skill

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "loop_id": self.loop_id,
            "plugin_id": self.plugin_id,
            "description": self.description,
            "status": self.status,
            "last_event_ts": self.last_event_ts.isoformat(),
            "event_count_7d": self.event_count_7d,
            "health_score": self.health_score,
            "owner_skill": self.owner_skill,
        }


def list_learning_loops(
    service: LearningLoopService,
    tenant_id: str,
    plugin_id: Optional[str] = None,
    skill_id: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: str = "plugin_id",
    limit: int = 1000,
) -> dict:
    """List all learning loops with optional filters.

    Args:
        service: LearningLoopService instance
        tenant_id: Tenant ID (for audit/isolation)
        plugin_id: Filter by plugin_id (optional)
        skill_id: Filter by owner_skill (optional)
        status: Filter by status (optional)
        sort_by: Sort field (plugin_id, status, last_event_ts, health_score)
        limit: Max entries to return (1000 default)

    Returns:
        Dict with loops and total count:
        {
            "loops": [...],
            "total": N,
            "filters": {...},
            "sort_by": "...",
        }
    """
    from core.tenants.validation import validate_tenant_id
    import logging

    logger = logging.getLogger(__name__)

    try:
        validate_tenant_id(tenant_id)
    except ValueError as exc:
        logger.error(f"Invalid tenant_id: {exc}")
        return {"loops": [], "total": 0, "error": "Invalid tenant"}

    # Validate service tenant
    if service.tenant_id != tenant_id:
        logger.error(f"Service tenant mismatch: {service.tenant_id} != {tenant_id}")
        return {"loops": [], "total": 0, "error": "Tenant mismatch"}

    # List from service
    try:
        entries = service.list_loops(
            plugin_id=plugin_id,
            status=status,
            limit=limit,
        )
    except Exception as exc:
        logger.error(f"Failed to list loops: {exc}")
        return {"loops": [], "total": 0, "error": str(exc)}

    # Convert to summary objects
    summaries = [
        LearningLoopSummary(
            loop_id=e.loop_id,
            plugin_id=e.plugin_id,
            description=e.description,
            status=e.status,
            last_event_ts=e.last_event_ts,
            event_count_7d=e.event_count_7d,
            health_score=e.health_score,
            owner_skill=e.owner_skill,
        )
        for e in entries
    ]

    # Filter by skill if provided
    if skill_id:
        summaries = [s for s in summaries if s.owner_skill == skill_id]

    # Sort
    sort_keys = {
        "plugin_id": lambda s: s.plugin_id,
        "status": lambda s: s.status,
        "last_event_ts": lambda s: s.last_event_ts,
        "health_score": lambda s: s.health_score,
    }
    if sort_by in sort_keys:
        summaries.sort(key=sort_keys[sort_by])

    return {
        "loops": [s.to_dict() for s in summaries],
        "total": len(summaries),
        "filters": {
            "plugin_id": plugin_id,
            "skill_id": skill_id,
            "status": status,
        },
        "sort_by": sort_by,
        "timestamp": datetime.utcnow().isoformat(),
    }
