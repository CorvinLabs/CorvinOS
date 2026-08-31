"""
Inspection Layer — Query Engine for execution observability.

Phase 1.1: Base classes and data models for task graph, skill/tool metadata,
and category health metrics.

This package provides read-only query interfaces for visibility into:
1. Task Graph Visualization (DAG of task dependencies and status)
2. Skill & Tool Inspector (forged capability registry with metadata)
3. Category Inspector (category-level health and event aggregation)

All queries are tenant-scoped (GDPR Art. 5, 6, 32) and return frozen data
structures to prevent accidental mutations.

References:
  - CONCEPT-0021: Context-Pipeline v2 Complete Redesign
  - ADR-0276: Task Graph Visualization
  - ADR-0277: Skill & Tool Inspector
  - ADR-0278: Category Inspector
  - ADR-0323: Inspection Framework
"""

__version__ = "0.1.0"
__all__ = [
    # Data Models
    "TaskStatus",
    "TaskNode",
    "TaskGraph",
    "ForgedSkillMetadata",
    "ForgedToolMetadata",
    "SkillToolDependencyGraph",
    "ErrorPattern",
    "EventSummary",
    "CategoryHealthMetrics",
    "CategoryDrillDown",
    "ToolStatus",
    "CategoryStatus",
    # Query Engines
    "QueryEngine",
    "TaskGraphQuery",
    "SkillToolQuery",
    "CategoryQuery",
]

from .data_models import (
    TaskStatus,
    TaskNode,
    TaskGraph,
    ForgedSkillMetadata,
    ForgedToolMetadata,
    SkillToolDependencyGraph,
    ErrorPattern,
    EventSummary,
    CategoryHealthMetrics,
    CategoryDrillDown,
    ToolStatus,
    CategoryStatus,
)

from .query_engine import (
    QueryEngine,
    TaskGraphQuery,
    SkillToolQuery,
    CategoryQuery,
)
