"""
Inspection Layer Data Models — Phase 1 Core Infrastructure

Defines immutable data structures for task graph, skill/tool metadata, and category health.
All models are tenant-scoped for GDPR compliance (ADR-0233).

Data Models:
- TaskNode: Represents a single task with status, dependencies, and execution metadata
- SkillMetadata: Forged skill registry entry with performance metrics
- ToolMetadata: Forged tool registry entry with performance metrics
- CategoryHealth: Category-level health metrics (error rate, latency percentiles)
- DependencyEdge: Represents a dependency relationship (skill→tool, skill→skill)
- EventSummary: A single event for drill-down inspection

All dataclasses are frozen (immutable) to prevent accidental mutation.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set
from decimal import Decimal


# ============================================================================
# ENUMS
# ============================================================================

class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


class ToolStatus(Enum):
    """Tool availability status."""
    AVAILABLE = "available"
    DEPRECATED = "deprecated"
    UNAVAILABLE = "unavailable"


class CategoryStatus(Enum):
    """Category health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class DependencyType(Enum):
    """Type of dependency relationship."""
    SKILL_USES_TOOL = "skill_uses_tool"
    SKILL_CALLS_SKILL = "skill_calls_skill"
    TOOL_COMPOSES_TOOL = "tool_composes_tool"
    TASK_DEPENDS_ON_TASK = "task_depends_on_task"


# ============================================================================
# TASK GRAPH MODELS
# ============================================================================

@dataclass(frozen=True)
class TaskNode:
    """Represents a single task in the execution DAG."""
    task_id: str                          # UUID
    name: str                             # Human-readable name
    status: TaskStatus                    # Current execution status
    phase: str                            # Phase within LDD (analysis, impl, testing, etc.)
    iteration: int                        # LDD iteration count (k=1, 2, 3, ...)
    parent_id: Optional[str] = None       # Parent task_id for nested tasks
    children_ids: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)  # task_ids this depends on
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    estimated_duration: Optional[timedelta] = None
    actual_duration: Optional[timedelta] = None
    error_message: Optional[str] = None   # If status == FAILED
    owner: Optional[str] = None           # Persona or agent ID
    tenant_id: str = "_default"           # Tenant scope (GDPR)


@dataclass(frozen=True)
class TaskGraph:
    """Represents the complete task DAG for a session."""
    tasks: Dict[str, TaskNode] = field(default_factory=dict)  # task_id → TaskNode
    tenant_id: str = "_default"
    session_id: Optional[str] = None


# ============================================================================
# SKILL & TOOL MODELS
# ============================================================================

@dataclass(frozen=True)
class LatencyMetrics:
    """Latency statistics for a skill or tool."""
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    min_latency_ms: float = 0.0
    sample_count: int = 0


@dataclass(frozen=True)
class SkillMetadata:
    """Metadata for a forged skill."""
    skill_id: str                         # "corvinOS_unified_context_bridge"
    name: str
    version: str                          # "1.0.0"
    created_at: Optional[datetime] = None
    last_used: Optional[datetime] = None
    usage_count: int = 0
    success_rate: float = 0.0             # 0.0-1.0
    latency: Optional[LatencyMetrics] = None
    cost_estimate: float = 0.0            # compute units per invocation
    depends_on_tools: List[str] = field(default_factory=list)  # tool_ids
    depends_on_skills: List[str] = field(default_factory=list)  # skill_ids
    tags: List[str] = field(default_factory=list)  # ["learning", "core", "plugin"]
    owner: Optional[str] = None
    description: str = ""
    tenant_id: str = "_default"


@dataclass(frozen=True)
class ToolMetadata:
    """Metadata for a forged tool."""
    tool_id: str                          # Tool name/identifier
    name: str
    implementation: str                   # MCP, HTTP, subprocess, etc.
    version: str
    created_at: Optional[datetime] = None
    last_used: Optional[datetime] = None
    usage_count: int = 0
    success_rate: float = 0.0
    latency: Optional[LatencyMetrics] = None
    avg_cost_per_call: float = 0.0
    used_by_skills: List[str] = field(default_factory=list)  # skill_ids
    used_by_tools: List[str] = field(default_factory=list)  # tool_ids
    status: ToolStatus = ToolStatus.AVAILABLE
    tags: List[str] = field(default_factory=list)
    tenant_id: str = "_default"


@dataclass(frozen=True)
class DependencyEdge:
    """Represents a dependency relationship."""
    source_id: str                        # skill_id, tool_id, or task_id
    target_id: str                        # skill_id, tool_id, or task_id
    edge_type: DependencyType
    weight: int = 1                       # How many times this edge is traversed
    tenant_id: str = "_default"


# ============================================================================
# CATEGORY HEALTH MODELS
# ============================================================================

@dataclass(frozen=True)
class ErrorPattern:
    """Represents a deduplicated error type."""
    error_type: str                       # Exception class name
    count: int
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    sample_messages: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class EventSummary:
    """Summary of a single event for inspection."""
    event_id: str
    category: str                         # "learning", "audit", "core", etc.
    timestamp: datetime
    event_type: str                       # "skill_created", "event_logged", etc.
    status: str                           # "success", "error", "partial"
    details: Dict = field(default_factory=dict)
    duration_ms: Optional[float] = None
    tenant_id: str = "_default"


@dataclass(frozen=True)
class CategoryHealthMetrics:
    """Category-level health statistics."""
    category: str                         # "learning", "audit", "core", "plugins"
    event_count: int                      # Total events in timerange
    error_count: int
    error_rate: float                     # error_count / event_count
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    subcategories: Dict[str, int] = field(default_factory=dict)  # subcat → count
    recent_events: List[EventSummary] = field(default_factory=list)  # Last N events
    error_patterns: List[ErrorPattern] = field(default_factory=list)  # Top error types
    status: CategoryStatus = CategoryStatus.HEALTHY
    tenant_id: str = "_default"
    timestamp: Optional[datetime] = None


@dataclass(frozen=True)
class CategoryDrillDown:
    """Detailed drill-down view of a category."""
    category: str
    filters: Dict = field(default_factory=dict)  # Applied filters
    events: List[EventSummary] = field(default_factory=list)
    metrics: Optional[CategoryHealthMetrics] = None
    tenant_id: str = "_default"


# ============================================================================
# COMBINED VIEW MODELS
# ============================================================================

@dataclass(frozen=True)
class SkillToolDependencyGraph:
    """Complete skill/tool dependency graph."""
    skills: Dict[str, SkillMetadata] = field(default_factory=dict)
    tools: Dict[str, ToolMetadata] = field(default_factory=dict)
    edges: List[DependencyEdge] = field(default_factory=list)
    tenant_id: str = "_default"
    timestamp: Optional[datetime] = None


# ============================================================================
# QUERY RESULT MODELS
# ============================================================================

@dataclass(frozen=True)
class QueryResult:
    """Base class for query results."""
    success: bool = True
    error_message: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
