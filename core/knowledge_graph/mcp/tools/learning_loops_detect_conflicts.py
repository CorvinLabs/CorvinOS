"""MCP Tool: learning_loops.detect_conflicts() — Find duplicate learning loops.

Detects learning loops declared by multiple plugins (duplicate loop_ids).
This is a data consistency check that helps operators find and resolve
duplicates before they cause issues.

Tool signature:
  learning_loops.detect_conflicts(
      tenant_id: Optional[str] = None
  ) -> List[DuplicateWarning]

Performance:
- Scans entire index, ~50ms for 1000 entries
- Results are audit-logged

ADR-0907: KG MCP Learning-Loop Tools (Stream 2.3)
"""

from __future__ import annotations

from typing import Optional, List

from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService


class DuplicateWarning:
    """Warning about a duplicate learning loop."""

    def __init__(
        self,
        loop_id: str,
        plugin_ids: List[str],
        conflict_count: int,
        action_taken: str = "logged",
    ):
        self.loop_id = loop_id
        self.plugin_ids = sorted(set(plugin_ids))
        self.conflict_count = conflict_count
        self.action_taken = action_taken

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "loop_id": self.loop_id,
            "plugin_ids": self.plugin_ids,
            "conflict_count": self.conflict_count,
            "action_taken": self.action_taken,
            "severity": "warning" if self.conflict_count == 2 else "critical",
        }


def detect_conflicts(
    service: LearningLoopService,
    tenant_id: str,
) -> dict:
    """Detect learning loops declared by multiple plugins.

    Args:
        service: LearningLoopService instance
        tenant_id: Tenant ID (for audit/isolation)

    Returns:
        Dict with conflicts list or error message:
        {
            "conflicts": [
                {
                    "loop_id": "...",
                    "plugin_ids": [...],
                    "conflict_count": N,
                    "action_taken": "logged",
                    "severity": "warning" | "critical"
                },
                ...
            ],
            "total_conflicts": N,
            "timestamp": "ISO8601",
        }
    """
    from core.tenants.validation import validate_tenant_id
    from datetime import datetime
    import logging

    logger = logging.getLogger(__name__)

    try:
        validate_tenant_id(tenant_id)
    except ValueError as exc:
        logger.error(f"Invalid tenant_id: {exc}")
        return {"conflicts": [], "total_conflicts": 0, "error": "Invalid tenant"}

    # Validate service tenant
    if service.tenant_id != tenant_id:
        logger.error(f"Service tenant mismatch: {service.tenant_id} != {tenant_id}")
        return {"conflicts": [], "total_conflicts": 0, "error": "Tenant mismatch"}

    # Detect conflicts
    try:
        conflicts = service.detect_duplicate_loops()
    except Exception as exc:
        logger.error(f"Failed to detect conflicts: {exc}")
        return {"conflicts": [], "total_conflicts": 0, "error": str(exc)}

    # Convert to warning objects
    warnings = [
        DuplicateWarning(
            loop_id=c["loop_id"],
            plugin_ids=c["plugin_ids"],
            conflict_count=c["conflict_count"],
            action_taken=c["action_taken"],
        )
        for c in conflicts
    ]

    # Build response
    result = {
        "conflicts": [w.to_dict() for w in warnings],
        "total_conflicts": len(warnings),
        "timestamp": datetime.utcnow().isoformat(),
    }

    # Log summary
    if warnings:
        logger.warning(
            f"Detected {len(warnings)} duplicate learning loop(s): "
            f"{', '.join(w.loop_id for w in warnings)}"
        )

    return result
