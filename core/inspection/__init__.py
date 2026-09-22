"""
Inspection Layer — Query Engine for execution observability + Audio/Video Inspection.

Phase 1.1: Base classes and data models for task graph, skill/tool metadata,
and category health metrics.

Phase 3 (2026-09-22): Audio/Video inspectors with audit chain and learning loop integration.

This package provides:
1. Task Graph Visualization (DAG of task dependencies and status)
2. Skill & Tool Inspector (forged capability registry with metadata)
3. Category Inspector (category-level health and event aggregation)
4. Audio Inspector: Analyzes audio with audit logging (ADR-0232)
5. Video Inspector: Analyzes video with learning events (ADR-0314)
6. Maestro: Orchestrates parallel audio/video pipeline

All queries and analyses are tenant-scoped (GDPR Art. 5, 6, 32) and return frozen data
structures to prevent accidental mutations. Audit chain integration ensures compliance.

References:
  - CONCEPT-0021: Context-Pipeline v2 Complete Redesign
  - ADR-0276: Task Graph Visualization
  - ADR-0277: Skill & Tool Inspector
  - ADR-0278: Category Inspector
  - ADR-0323: Inspection Framework
  - ADR-0232: Compliance Hardening (Audit Chain)
  - ADR-0314: Learning Infrastructure
"""

__version__ = "0.3.0"
__all__ = [
    # Phase 1 (Existing)
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
    "QueryEngine",
    "TaskGraphQuery",
    "SkillToolQuery",
    "CategoryQuery",
    # Phase 3 (New)
    "AudioInspector",
    "AudioAnalysisResult",
    "AudioQualityLevel",
    "VideoInspector",
    "VideoAnalysisResult",
    "FrameQuality",
    "Maestro",
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

from .audio_video_inspectors import (
    AudioInspector,
    AudioAnalysisResult,
    AudioQualityLevel,
    VideoInspector,
    VideoAnalysisResult,
    FrameQuality,
    Maestro,
)
