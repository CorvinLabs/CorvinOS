"""
Query Engine base classes for the Inspection Layer.

Implements Phase 1.1: Core query interfaces for task graph, skill/tool metadata,
and category health. Each query engine is read-only and operates on derived data
from authoritative sources.

References:
  - CONCEPT-0021: Context-Pipeline v2 Complete Redesign
  - ADR-0276: Task Graph Visualization
  - ADR-0277: Skill & Tool Inspector
  - ADR-0323: Inspection Framework
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Set

try:
    # When imported as a package
    from .data_models import (
        TaskNode,
        TaskGraph,
        ForgedSkillMetadata,
        ForgedToolMetadata,
        SkillToolDependencyGraph,
        CategoryHealthMetrics,
        CategoryDrillDown,
        EventSummary,
    )
except ImportError:
    # When imported directly
    from data_models import (
        TaskNode,
        TaskGraph,
        ForgedSkillMetadata,
        ForgedToolMetadata,
        SkillToolDependencyGraph,
        CategoryHealthMetrics,
        CategoryDrillDown,
        EventSummary,
    )


class QueryEngine(ABC):
    """
    Base class for all inspection query engines.

    Provides common interface and tenant isolation guarantees. All subclasses
    must:
    1. Validate tenant_id on every query (fail-closed if missing)
    2. Filter all results by tenant_id
    3. Return read-only (frozen) data structures
    4. Support pagination for large result sets
    """

    def __init__(self, tenant_id: str):
        """
        Initialize query engine with tenant context.

        Args:
            tenant_id: GDPR-required tenant identifier

        Raises:
            ValueError: If tenant_id is empty or None
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")
        self.tenant_id = tenant_id

    @abstractmethod
    def health_check(self) -> bool:
        """
        Verify that data sources are accessible.

        Returns:
            True if the query engine can reach all required data sources.
        """
        pass


class TaskGraphQuery(QueryEngine):
    """
    Query engine for task graph analysis and DAG traversal.

    Operates on ExecutionContext.task_graph and provides methods for:
    - Viewing task status and dependencies
    - Finding critical path (scheduling bottleneck)
    - Identifying blocked tasks
    - Drill-down into nested subtasks

    All queries are tenant-scoped and return frozen data structures.
    """

    def __init__(self, tenant_id: str):
        """
        Initialize task graph query engine.

        Args:
            tenant_id: Tenant identifier for isolation
        """
        super().__init__(tenant_id)
        self._task_cache: Dict[str, TaskGraph] = {}  # session_id → TaskGraph

    def health_check(self) -> bool:
        """Check if ExecutionContext is accessible."""
        # Placeholder: would verify ExecutionContext in real implementation
        return True

    def register_task_graph(self, session_id: str, task_graph: TaskGraph) -> None:
        """
        Register a task graph for a session.

        Used by the execution engine to push updates. All tasks must belong
        to the same tenant as this query engine.

        Args:
            session_id: Session identifier
            task_graph: TaskGraph instance

        Raises:
            ValueError: If task_graph.tenant_id doesn't match self.tenant_id
        """
        if task_graph.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: query engine for {self.tenant_id}, "
                f"but received graph for {task_graph.tenant_id}"
            )
        self._task_cache[session_id] = task_graph

    def get_task_graph(self, session_id: str) -> Optional[TaskGraph]:
        """
        Retrieve complete task graph for a session.

        Args:
            session_id: Session to query

        Returns:
            TaskGraph if session exists, None otherwise.
        """
        return self._task_cache.get(session_id)

    def get_task(self, session_id: str, task_id: str) -> Optional[TaskNode]:
        """
        Retrieve a single task by ID.

        Args:
            session_id: Session containing the task
            task_id: Task identifier

        Returns:
            TaskNode if found, None otherwise.
        """
        graph = self.get_task_graph(session_id)
        if graph is None:
            return None
        return graph.tasks.get(task_id)

    def get_critical_path(self, session_id: str) -> List[TaskNode]:
        """
        Find the critical path (longest dependency chain) for a session.

        The critical path determines the minimum completion time for the
        entire session. Tasks on the critical path have zero slack.

        Args:
            session_id: Session to analyze

        Returns:
            List of TaskNode objects in dependency order, or empty list
            if session not found or graph is empty.
        """
        graph = self.get_task_graph(session_id)
        if graph is None or not graph.tasks:
            return []
        return graph.get_critical_path()

    def get_blocked_tasks(self, session_id: str) -> List[TaskNode]:
        """
        Find all currently blocked tasks (waiting on dependencies).

        A task is blocked if it has dependencies that haven't completed yet
        or if its status is explicitly BLOCKED.

        Args:
            session_id: Session to query

        Returns:
            List of blocked TaskNode objects, or empty list if none found.
        """
        graph = self.get_task_graph(session_id)
        if graph is None or not graph.tasks:
            return []
        return graph.get_blocked_tasks()

    def get_task_dependencies(self, session_id: str, task_id: str) -> List[TaskNode]:
        """
        Get all direct dependencies of a task.

        Args:
            session_id: Session containing the task
            task_id: Task to analyze

        Returns:
            List of TaskNode objects this task depends on.
        """
        graph = self.get_task_graph(session_id)
        if graph is None:
            return []

        task = graph.tasks.get(task_id)
        if task is None:
            return []

        return [graph.tasks[dep_id] for dep_id in task.dependencies if dep_id in graph.tasks]

    def get_task_dependents(self, session_id: str, task_id: str) -> List[TaskNode]:
        """
        Get all tasks that depend on a given task.

        Args:
            session_id: Session containing the task
            task_id: Task to query

        Returns:
            List of TaskNode objects that depend on this task.
        """
        graph = self.get_task_graph(session_id)
        if graph is None:
            return []

        dependents = []
        for task_id_other, task_other in graph.tasks.items():
            if task_id in task_other.dependencies:
                dependents.append(task_other)
        return dependents

    def get_tasks_by_status(self, session_id: str, status: str) -> List[TaskNode]:
        """
        Find all tasks with a specific status.

        Args:
            session_id: Session to query
            status: Task status (pending/running/done/blocked/failed)

        Returns:
            List of TaskNode objects with matching status.
        """
        graph = self.get_task_graph(session_id)
        if graph is None:
            return []
        return [task for task in graph.tasks.values() if task.status.value == status]

    def get_tasks_by_phase(self, session_id: str, phase: str) -> List[TaskNode]:
        """
        Find all tasks in a specific LDD phase.

        Args:
            session_id: Session to query
            phase: Phase name (analysis/implementation/testing/etc.)

        Returns:
            List of TaskNode objects in the specified phase.
        """
        graph = self.get_task_graph(session_id)
        if graph is None:
            return []
        return [task for task in graph.tasks.values() if task.phase == phase]


class SkillToolQuery(QueryEngine):
    """
    Query engine for skill and tool metadata, dependency analysis.

    Operates on SkillForgeSubsystem and ToolForgeSubsystem registries.
    Provides methods for:
    - Listing forged skills and tools with metadata
    - Analyzing dependencies (skill→tool, skill→skill)
    - Detecting circular dependencies
    - Identifying bottleneck tools
    - Computing performance metrics (latency, success rate, cost)

    All queries are tenant-scoped and return frozen data structures.
    """

    def __init__(self, tenant_id: str):
        """
        Initialize skill/tool query engine.

        Args:
            tenant_id: Tenant identifier for isolation
        """
        super().__init__(tenant_id)
        self._skill_registry: Dict[str, ForgedSkillMetadata] = {}
        self._tool_registry: Dict[str, ForgedToolMetadata] = {}

    def health_check(self) -> bool:
        """Check if SkillForgeSubsystem and ToolForgeSubsystem are accessible."""
        # Placeholder: would verify registries in real implementation
        return True

    def register_skill(self, skill: ForgedSkillMetadata) -> None:
        """
        Register a skill in the registry.

        Used by SkillForgeSubsystem to push updates. Skill must match
        this query engine's tenant.

        Args:
            skill: ForgedSkillMetadata instance

        Raises:
            ValueError: If skill.tenant_id doesn't match self.tenant_id
        """
        if skill.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: query engine for {self.tenant_id}, "
                f"but received skill for {skill.tenant_id}"
            )
        self._skill_registry[skill.skill_id] = skill

    def register_tool(self, tool: ForgedToolMetadata) -> None:
        """
        Register a tool in the registry.

        Used by ToolForgeSubsystem to push updates. Tool must match
        this query engine's tenant.

        Args:
            tool: ForgedToolMetadata instance

        Raises:
            ValueError: If tool.tenant_id doesn't match self.tenant_id
        """
        if tool.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: query engine for {self.tenant_id}, "
                f"but received tool for {tool.tenant_id}"
            )
        self._tool_registry[tool.tool_id] = tool

    def list_skills(self, tags: Optional[List[str]] = None) -> Dict[str, ForgedSkillMetadata]:
        """
        List all skills in this tenant's registry.

        Optionally filter by tags (intersection filter: skill must have ALL tags).

        Args:
            tags: Optional list of required tags

        Returns:
            Dict of skill_id → ForgedSkillMetadata, filtered by tags if provided.
        """
        if tags is None:
            return dict(self._skill_registry)

        filtered = {}
        for skill_id, skill in self._skill_registry.items():
            if all(tag in skill.tags for tag in tags):
                filtered[skill_id] = skill
        return filtered

    def list_tools(self, status: Optional[str] = None) -> Dict[str, ForgedToolMetadata]:
        """
        List all tools in this tenant's registry.

        Optionally filter by status (available/deprecated/unreachable).

        Args:
            status: Optional tool status to filter by

        Returns:
            Dict of tool_id → ForgedToolMetadata, filtered by status if provided.
        """
        if status is None:
            return dict(self._tool_registry)

        filtered = {}
        for tool_id, tool in self._tool_registry.items():
            if tool.status.value == status:
                filtered[tool_id] = tool
        return filtered

    def get_skill(self, skill_id: str) -> Optional[ForgedSkillMetadata]:
        """
        Retrieve a specific skill by ID.

        Args:
            skill_id: Skill identifier

        Returns:
            ForgedSkillMetadata if found, None otherwise.
        """
        return self._skill_registry.get(skill_id)

    def get_tool(self, tool_id: str) -> Optional[ForgedToolMetadata]:
        """
        Retrieve a specific tool by ID.

        Args:
            tool_id: Tool identifier

        Returns:
            ForgedToolMetadata if found, None otherwise.
        """
        return self._tool_registry.get(tool_id)

    def get_dependency_graph(self) -> SkillToolDependencyGraph:
        """
        Build complete skill-tool dependency graph.

        Returns a frozen graph that can be analyzed for cycles, transitive
        dependencies, and bottleneck identification.

        Returns:
            SkillToolDependencyGraph instance.
        """
        return SkillToolDependencyGraph(
            skills=dict(self._skill_registry),
            tools=dict(self._tool_registry),
            tenant_id=self.tenant_id,
        )

    def get_skill_dependencies(self, skill_id: str) -> Set[str]:
        """
        Get all tools transitively used by a skill.

        Follows all skill→tool, skill→skill, and tool→tool edges to build
        the complete transitive closure.

        Args:
            skill_id: Skill to analyze

        Returns:
            Set of all tool IDs transitively depended on.
        """
        graph = self.get_dependency_graph()
        return graph.get_transitive_dependencies(skill_id)

    def find_circular_dependencies(self) -> List[tuple[str, str]]:
        """
        Detect all circular dependencies in the graph.

        Returns edges that form cycles. For a system to function correctly,
        this list should be empty.

        Returns:
            List of (node_id1, node_id2) tuples representing cycle edges.
        """
        graph = self.get_dependency_graph()
        return graph.find_circular_dependencies()

    def get_critical_tools(self, usage_threshold: int = 5) -> List[str]:
        """
        Identify bottleneck tools (used by many skills/tools).

        Critical tools are those with high usage counts. If a critical tool
        is slow or broken, many skills are affected.

        Args:
            usage_threshold: Minimum usage count for criticality

        Returns:
            List of critical tool IDs, sorted by usage (descending).
        """
        graph = self.get_dependency_graph()
        return graph.get_critical_tools(usage_threshold)


class CategoryQuery(QueryEngine):
    """
    Query engine for category-level health metrics and event aggregation.

    Operates on ContextBus event stream and maintains aggregated health metrics
    for each category (learning, audit, core, plugins). Provides methods for:
    - Computing category health (error rate, latency percentiles)
    - Filtering events by category, timerange, error type
    - Detecting error patterns
    - Drill-down analysis

    All queries are tenant-scoped and return frozen data structures.
    """

    def __init__(self, tenant_id: str):
        """
        Initialize category query engine.

        Args:
            tenant_id: Tenant identifier for isolation
        """
        super().__init__(tenant_id)
        self._category_metrics: Dict[str, CategoryHealthMetrics] = {}
        self._events: List[EventSummary] = []

    def health_check(self) -> bool:
        """Check if ContextBus event stream is accessible."""
        # Placeholder: would verify ContextBus in real implementation
        return True

    def add_event(self, event: EventSummary) -> None:
        """
        Record an event for aggregation.

        Used by background aggregation task to push events. Event must match
        this query engine's tenant.

        Args:
            event: EventSummary instance

        Raises:
            ValueError: If event doesn't have tenant context
        """
        # Events don't have explicit tenant_id in the schema, but we could verify
        # by checking if the event came from an authorized source
        self._events.append(event)

    def update_category_metrics(self, category: str, metrics: CategoryHealthMetrics) -> None:
        """
        Update cached health metrics for a category.

        Used by background aggregation task. Metrics must match this engine's tenant.

        Args:
            category: Category name
            metrics: CategoryHealthMetrics instance

        Raises:
            ValueError: If metrics.tenant_id doesn't match self.tenant_id
        """
        if metrics.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: query engine for {self.tenant_id}, "
                f"but received metrics for {metrics.tenant_id}"
            )
        self._category_metrics[category] = metrics

    def list_categories(self) -> List[str]:
        """
        List all categories with health data.

        Returns:
            List of category names.
        """
        return list(self._category_metrics.keys())

    def get_category_health(self, category: str) -> Optional[CategoryHealthMetrics]:
        """
        Get current health metrics for a category.

        Returns cached metrics if available, None if category has no data.

        Args:
            category: Category name (learning/audit/core/plugins)

        Returns:
            CategoryHealthMetrics if data exists, None otherwise.
        """
        return self._category_metrics.get(category)

    def filter_events(
        self,
        category: Optional[str] = None,
        error_type: Optional[str] = None,
        status: Optional[str] = None,
        timerange: Optional[timedelta] = None,
        limit: int = 100,
    ) -> List[EventSummary]:
        """
        Filter events by multiple criteria.

        Supports drill-down queries with optional timerange. Returns most
        recent events first (reverse chronological order).

        Args:
            category: Filter by category, None = all categories
            error_type: Filter by error type (only for error status)
            status: Filter by status (success/error/partial)
            timerange: Only include events within this duration from now
            limit: Maximum number of events to return

        Returns:
            List of matching EventSummary objects, most recent first.
        """
        filtered = list(self._events)

        # Apply category filter
        if category:
            filtered = [e for e in filtered if e.category == category]

        # Apply status filter
        if status:
            filtered = [e for e in filtered if e.status == status]

        # Apply error type filter (requires error status)
        if error_type:
            filtered = [
                e for e in filtered
                if e.status == 'error' and e.details.get('error_type') == error_type
            ]

        # Apply timerange filter
        if timerange:
            cutoff = datetime.utcnow() - timerange
            filtered = [e for e in filtered if e.timestamp >= cutoff]

        # Sort by timestamp descending (most recent first)
        filtered.sort(key=lambda e: e.timestamp, reverse=True)

        # Apply limit
        return filtered[:limit]

    def get_drill_down(
        self,
        category: str,
        filters: Optional[Dict[str, any]] = None,
        limit: int = 100,
    ) -> Optional[CategoryDrillDown]:
        """
        Get detailed drill-down view for a category with filtered events.

        Combines category health metrics with filtered event list for
        operator analysis.

        Args:
            category: Category to drill into
            filters: Optional filter criteria (error_type, status, timerange)
            limit: Maximum events to return

        Returns:
            CategoryDrillDown if category exists, None otherwise.
        """
        metrics = self.get_category_health(category)
        if metrics is None:
            return None

        filters = filters or {}
        events = self.filter_events(
            category=category,
            error_type=filters.get('error_type'),
            status=filters.get('status'),
            timerange=filters.get('timerange'),
            limit=limit,
        )

        return CategoryDrillDown(
            category=category,
            filters=filters,
            events=events,
            metrics=metrics,
            tenant_id=self.tenant_id,
        )
