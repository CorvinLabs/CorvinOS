"""
Data models for the Inspection Query Engine.

Implements Phase 1.1: Core base classes for task graph, skill/tool metadata,
and category health metrics. These are derived, read-only structures computed
from authoritative sources (ExecutionContext, SkillForgeSubsystem, ContextBus).

References:
  - CONCEPT-0021: Context-Pipeline v2 Complete Redesign
  - ADR-0276: Task Graph Visualization
  - ADR-0277: Skill & Tool Inspector
  - ADR-0278: Category Inspector
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, List, Set
from uuid import UUID


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


class ToolStatus(str, Enum):
    """Tool availability status."""
    AVAILABLE = "available"
    DEPRECATED = "deprecated"
    UNREACHABLE = "unreachable"


class CategoryStatus(str, Enum):
    """Category health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"


@dataclass(frozen=True)
class TaskNode:
    """
    Represents a single task in the execution graph.

    Immutable snapshot of a task's state, used for DAG visualization and
    dependency analysis. Tasks form a directed acyclic graph (DAG) where
    edges represent dependencies.

    Attributes:
        task_id: Unique task identifier (UUID format)
        name: Human-readable task name
        status: Current execution status (pending/running/done/blocked/failed)
        phase: LDD phase name (e.g., "analysis", "implementation", "testing")
        iteration: LDD iteration count (k=1, 2, 3, ...)
        parent_id: UUID of parent task (for nested tasks), None if top-level
        children_ids: List of child task UUIDs (nested subtasks)
        dependencies: List of task UUIDs this task depends on
        created_at: When the task was created
        started_at: When execution started, None if not started
        completed_at: When execution finished, None if not done
        estimated_duration: Predicted execution time, None if unknown
        actual_duration: Measured execution time, None if still running
        error_message: Error description if status == FAILED, else None
        owner: Persona or agent ID responsible for this task
        tenant_id: GDPR-required tenant identifier for isolation
    """
    task_id: str
    name: str
    status: TaskStatus
    phase: str
    iteration: int
    parent_id: Optional[str]
    children_ids: List[str]
    dependencies: List[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    estimated_duration: Optional[timedelta]
    actual_duration: Optional[timedelta]
    error_message: Optional[str]
    owner: str
    tenant_id: str

    def is_blocked(self) -> bool:
        """Check if this task is waiting on unresolved dependencies."""
        return self.status == TaskStatus.BLOCKED

    def is_terminal(self) -> bool:
        """Check if this task is in a terminal state (done or failed)."""
        return self.status in (TaskStatus.DONE, TaskStatus.FAILED)

    def duration_ms(self) -> Optional[float]:
        """Return actual duration in milliseconds, or None if not completed."""
        if self.actual_duration is None:
            return None
        return self.actual_duration.total_seconds() * 1000


@dataclass(frozen=True)
class TaskGraph:
    """
    Represents the complete task dependency graph.

    A DAG (directed acyclic graph) of all tasks in an execution session,
    with methods for traversal and analysis (critical path, blocked tasks, etc.).

    Attributes:
        tasks: Mapping of task_id → TaskNode
        tenant_id: Tenant identifier for isolation
        session_id: Session identifier this graph belongs to
    """
    tasks: Dict[str, TaskNode]
    tenant_id: str
    session_id: str

    def get_dag(self) -> Dict[str, List[str]]:
        """
        Return task dependencies as adjacency list (task_id → [dependent_ids]).

        This is the primary data structure for DAG visualization and traversal.
        An edge from A to B means B depends on A.

        Returns:
            Dict where keys are task_ids and values are lists of tasks that
            directly depend on each task.
        """
        adjacency = {task_id: [] for task_id in self.tasks}
        for task_id, task in self.tasks.items():
            for dep_id in task.dependencies:
                if dep_id in adjacency:
                    adjacency[dep_id].append(task_id)
        return adjacency

    def get_critical_path(self) -> List[TaskNode]:
        """
        Return the longest dependency chain (bottleneck for scheduling).

        The critical path is the longest sequence of dependent tasks. Tasks on
        the critical path directly impact overall completion time.

        Returns:
            List of TaskNode objects in order from start to finish.
        """
        # Build reverse dependency map (who depends on me)
        deps_on = {task_id: [] for task_id in self.tasks}
        for task_id, task in self.tasks.items():
            for dep_id in task.dependencies:
                if dep_id in deps_on:
                    deps_on[dep_id].append(task_id)

        # Find root nodes (no dependencies)
        roots = [tid for tid, task in self.tasks.items() if not task.dependencies]

        def longest_from(node_id: str) -> tuple[List[str], int]:
            """DFS to find longest path from node_id to any leaf."""
            dependents = deps_on.get(node_id, [])
            if not dependents:
                return ([node_id], 1)

            max_path, max_len = [], 0
            for dep_id in dependents:
                sub_path, sub_len = longest_from(dep_id)
                if sub_len + 1 > max_len:
                    max_len = sub_len + 1
                    max_path = [node_id] + sub_path
            return (max_path, max_len)

        # Find longest path starting from any root
        critical_path_ids = []
        for root in roots:
            path, length = longest_from(root)
            if length > len(critical_path_ids):
                critical_path_ids = path

        return [self.tasks[tid] for tid in critical_path_ids if tid in self.tasks]

    def get_blocked_tasks(self) -> List[TaskNode]:
        """
        Return all tasks currently waiting on unresolved dependencies.

        A task is blocked if its status is BLOCKED or if it has dependencies
        that are still PENDING or RUNNING.

        Returns:
            List of TaskNode objects with status BLOCKED or dependencies not done.
        """
        blocked = []
        for task_id, task in self.tasks.items():
            if task.status == TaskStatus.BLOCKED:
                blocked.append(task)
            else:
                # Check if any dependencies are not done
                for dep_id in task.dependencies:
                    dep_task = self.tasks.get(dep_id)
                    if dep_task and not dep_task.is_terminal():
                        blocked.append(task)
                        break
        return blocked


@dataclass(frozen=True)
class ForgedSkillMetadata:
    """
    Metadata about a forged skill (dynamically created skill).

    Skills are runtime-generated capabilities that wrap tools and other skills.
    This metadata tracks usage patterns, performance, and dependencies.

    Attributes:
        skill_id: Unique skill identifier (e.g., "corvinOS_unified_context_bridge")
        name: Human-readable skill name
        version: Semantic version string
        created_at: When this skill was first created
        last_used: When this skill was last invoked, None if never used
        usage_count: Total number of invocations
        success_rate: Fraction of invocations that succeeded (0.0-1.0)
        avg_latency_ms: Average execution time in milliseconds
        p95_latency_ms: 95th percentile latency
        p99_latency_ms: 99th percentile latency
        cost_estimate: Estimated compute units per invocation
        depends_on_tools: List of tool IDs this skill uses
        depends_on_skills: List of skill IDs this skill depends on
        tags: Classification tags (e.g., ["learning", "core", "plugin"])
        owner: Persona or agent ID that created/owns this skill
        tenant_id: Tenant identifier for isolation
    """
    skill_id: str
    name: str
    version: str
    created_at: datetime
    last_used: Optional[datetime]
    usage_count: int
    success_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    cost_estimate: float
    depends_on_tools: List[str]
    depends_on_skills: List[str]
    tags: List[str]
    owner: str
    tenant_id: str

    def is_performant(self, p95_threshold_ms: float = 100.0) -> bool:
        """Check if skill meets performance baseline (P95 < threshold)."""
        return self.p95_latency_ms < p95_threshold_ms

    def is_reliable(self, success_threshold: float = 0.95) -> bool:
        """Check if skill meets reliability baseline (success rate > threshold)."""
        return self.success_rate >= success_threshold

    def last_used_seconds_ago(self) -> Optional[float]:
        """Return seconds since last use, or None if never used."""
        if self.last_used is None:
            return None
        return (datetime.utcnow() - self.last_used).total_seconds()


@dataclass(frozen=True)
class ForgedToolMetadata:
    """
    Metadata about a forged tool (dynamically created tool).

    Tools are low-level capabilities (MCP, HTTP, subprocess) wrapped for use
    by skills. This metadata tracks availability, performance, and usage.

    Attributes:
        tool_id: Unique tool identifier
        name: Human-readable tool name
        implementation: Type of tool (e.g., "mcp", "http", "subprocess")
        version: Semantic version string
        created_at: When this tool was first created
        last_used: When this tool was last invoked, None if never used
        usage_count: Total number of invocations
        success_rate: Fraction of invocations that succeeded (0.0-1.0)
        avg_latency_ms: Average execution time
        p95_latency_ms: 95th percentile latency
        avg_cost_per_call: Average cost in compute units
        used_by_skills: List of skill IDs that use this tool
        used_by_tools: List of tool IDs that use this tool (composition)
        status: Availability status (available/deprecated/unreachable)
        tags: Classification tags
        tenant_id: Tenant identifier for isolation
    """
    tool_id: str
    name: str
    implementation: str
    version: str
    created_at: datetime
    last_used: Optional[datetime]
    usage_count: int
    success_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    avg_cost_per_call: float
    used_by_skills: List[str]
    used_by_tools: List[str]
    status: ToolStatus
    tags: List[str]
    tenant_id: str

    def is_available(self) -> bool:
        """Check if tool is available for use."""
        return self.status == ToolStatus.AVAILABLE

    def is_critical(self, usage_threshold: int = 10) -> bool:
        """Check if tool is critical (used by many skills/tools)."""
        return self.usage_count >= usage_threshold


@dataclass(frozen=True)
class SkillToolDependencyGraph:
    """
    Complete dependency graph between skills and tools.

    Tracks all relationships: which skills use which tools, which skills depend
    on other skills, and which tools compose other tools. Supports circular
    dependency detection and transitive closure computation.

    Attributes:
        skills: Mapping of skill_id → ForgedSkillMetadata
        tools: Mapping of tool_id → ForgedToolMetadata
        tenant_id: Tenant identifier for isolation
    """
    skills: Dict[str, ForgedSkillMetadata]
    tools: Dict[str, ForgedToolMetadata]
    tenant_id: str

    def get_transitive_dependencies(self, skill_id: str) -> Set[str]:
        """
        Return all tools recursively used by a skill.

        Follows skill→tool edges, skill→skill edges, and tool→tool edges
        to build the complete transitive closure.

        Args:
            skill_id: The skill to analyze

        Returns:
            Set of all tool IDs transitively depended on by this skill.
        """
        visited = set()
        to_visit = [skill_id]

        while to_visit:
            current = to_visit.pop()
            if current in visited:
                continue
            visited.add(current)

            if current in self.skills:
                skill = self.skills[current]
                # Add direct tool dependencies
                visited.update(skill.depends_on_tools)
                # Add transitive skill dependencies
                to_visit.extend(skill.depends_on_skills)

        # Return only tool IDs (filter out skill IDs from visited set)
        return {tid for tid in visited if tid in self.tools}

    def find_circular_dependencies(self) -> List[tuple[str, str]]:
        """
        Detect cycles in the dependency graph.

        Uses depth-first search to find all circular dependencies. Returns
        edges that would create cycles if followed.

        Returns:
            List of (node_id1, node_id2) tuples representing cycle edges.
        """
        cycles = []
        visited = set()
        rec_stack = set()

        def dfs(node_id: str, path: List[str]) -> None:
            visited.add(node_id)
            rec_stack.add(node_id)

            # Get neighbors (outgoing edges)
            neighbors = []
            if node_id in self.skills:
                skill = self.skills[node_id]
                neighbors.extend(skill.depends_on_skills)
                neighbors.extend(skill.depends_on_tools)
            if node_id in self.tools:
                tool = self.tools[node_id]
                neighbors.extend(tool.used_by_tools)

            for neighbor in neighbors:
                if neighbor not in visited:
                    dfs(neighbor, path + [neighbor])
                elif neighbor in rec_stack:
                    # Found a cycle
                    cycles.append((node_id, neighbor))

            rec_stack.discard(node_id)

        # Start DFS from all nodes
        all_nodes = set(self.skills.keys()) | set(self.tools.keys())
        for node_id in all_nodes:
            if node_id not in visited:
                dfs(node_id, [node_id])

        return cycles

    def get_critical_tools(self, usage_threshold: int = 5) -> List[str]:
        """
        Identify bottleneck tools (used by many skills).

        Tools with high usage counts are critical to system performance.
        If a critical tool is slow or unavailable, many skills are affected.

        Args:
            usage_threshold: Minimum usage count to be considered critical

        Returns:
            List of critical tool IDs, sorted by usage count (descending).
        """
        critical = [
            tool_id
            for tool_id, tool in self.tools.items()
            if tool.usage_count >= usage_threshold
        ]
        # Sort by usage count descending
        critical.sort(
            key=lambda tid: self.tools[tid].usage_count,
            reverse=True
        )
        return critical


@dataclass(frozen=True)
class ErrorPattern:
    """
    Aggregated error pattern in a category.

    Tracks the occurrence of a specific error type, deduplicated from multiple
    individual error events.

    Attributes:
        error_type: Exception class name or error code
        count: Total occurrences
        first_seen: When this error pattern first appeared
        last_seen: When this error pattern was last seen
        sample_messages: Sample error messages (up to 3)
    """
    error_type: str
    count: int
    first_seen: datetime
    last_seen: datetime
    sample_messages: List[str]


@dataclass(frozen=True)
class EventSummary:
    """
    Summary of a single event for drill-down inspection.

    Provides high-level information about an event without full event payload,
    suitable for displaying in tables and filtered lists.

    Attributes:
        event_id: Unique event identifier
        category: Event category (learning/audit/core/plugins)
        timestamp: When the event occurred
        event_type: Type of event (skill_created, event_logged, etc.)
        status: Outcome (success/error/partial)
        details: Key-value details (error type, message, etc.)
        duration_ms: Execution time if applicable, None otherwise
    """
    event_id: str
    category: str
    timestamp: datetime
    event_type: str
    status: str
    details: Dict[str, any]
    duration_ms: Optional[float]


@dataclass(frozen=True)
class CategoryHealthMetrics:
    """
    Health metrics for a category of events.

    Aggregates all events in a category over a time window to produce summary
    statistics: error rates, latency percentiles, error patterns, subcategory
    breakdown, and health status.

    Attributes:
        category: Category name (learning/audit/core/plugins)
        event_count: Total events in this category (time window)
        error_count: Count of events with error status
        error_rate: error_count / event_count (0.0-1.0)
        avg_latency_ms: Mean latency across all events
        p50_latency_ms: Median latency (50th percentile)
        p95_latency_ms: 95th percentile latency
        p99_latency_ms: 99th percentile latency
        max_latency_ms: Maximum latency observed
        subcategories: Count of events per subcategory
        recent_events: Last N events in this category (for drill-down)
        error_patterns: Top error types with frequencies
        status: Health status (healthy/degraded/critical)
        tenant_id: Tenant identifier for isolation
        timestamp: When these metrics were computed
    """
    category: str
    event_count: int
    error_count: int
    error_rate: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    subcategories: Dict[str, int]
    recent_events: List[EventSummary]
    error_patterns: List[ErrorPattern]
    status: CategoryStatus
    tenant_id: str
    timestamp: datetime

    def is_healthy(self) -> bool:
        """Check if this category is in a healthy state."""
        return self.status == CategoryStatus.HEALTHY

    def is_degraded(self) -> bool:
        """Check if this category is showing degradation."""
        return self.status == CategoryStatus.DEGRADED

    def is_critical(self) -> bool:
        """Check if this category requires immediate attention."""
        return self.status == CategoryStatus.CRITICAL


@dataclass(frozen=True)
class CategoryDrillDown:
    """
    Detailed drill-down view for a specific category.

    Combines health metrics with filtered event list, supporting drill-down
    operations where operators filter by timerange, error type, etc.

    Attributes:
        category: Category being analyzed
        filters: Applied filter criteria (timerange, error_type, subcategory)
        events: Filtered event list (up to limit)
        metrics: Category health metrics (for context)
        tenant_id: Tenant identifier for isolation
    """
    category: str
    filters: Dict[str, any]
    events: List[EventSummary]
    metrics: CategoryHealthMetrics
    tenant_id: str
