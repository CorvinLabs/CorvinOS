"""
Inspection Query Engine Framework — Phase 1 Core Infrastructure

Provides read-only query interfaces for task graph, skill/tool metadata, and category health.
All queries are tenant-scoped (GDPR compliance per ADR-0233).

Base Classes:
- QueryEngine: Abstract base for all query engines

Concrete Implementations:
- TaskGraphQuery: Query task DAG, dependencies, critical path
- SkillToolQuery: Query forged skill/tool registry, metrics, dependency graphs
- CategoryQuery: Query event aggregation, health metrics, drill-down

All queries are stateless and thread-safe.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import json
import logging

from .inspection_models import (
    TaskNode, TaskGraph, TaskStatus, DependencyType,
    SkillMetadata, ToolMetadata, ToolStatus, LatencyMetrics, DependencyEdge,
    CategoryHealthMetrics, CategoryStatus, ErrorPattern, EventSummary, CategoryDrillDown,
    SkillToolDependencyGraph, QueryResult
)

logger = logging.getLogger(__name__)


# ============================================================================
# BASE QUERY ENGINE
# ============================================================================

class QueryEngine(ABC):
    """Abstract base for all inspection query engines."""

    def __init__(self, tenant_id: str = "_default"):
        """
        Initialize query engine for a specific tenant.

        Args:
            tenant_id: Tenant scope (validated before use)
        """
        self.tenant_id = tenant_id
        self._validate_tenant_id()

    def _validate_tenant_id(self) -> None:
        """Validate tenant_id format. Fail-closed on invalid format."""
        if not self.tenant_id or len(self.tenant_id) > 255:
            raise ValueError(f"Invalid tenant_id: {self.tenant_id}")
        # Allowed: alphanumeric, underscore, hyphen
        if not all(c.isalnum() or c in ('_', '-') for c in self.tenant_id):
            raise ValueError(f"Invalid tenant_id characters: {self.tenant_id}")

    @abstractmethod
    def validate(self) -> bool:
        """
        Validate that query engine has access to required data sources.
        Returns False if data is unavailable.
        """
        pass


# ============================================================================
# TASK GRAPH QUERY ENGINE
# ============================================================================

class TaskGraphQuery(QueryEngine):
    """Query task DAG for dependencies, status, and critical path."""

    def __init__(self, tenant_id: str = "_default"):
        """Initialize task graph query engine."""
        super().__init__(tenant_id)
        self.corvin_home = Path.home() / '.corvin'
        self.tasks_path = self.corvin_home / 'tenants' / tenant_id / 'tasks'

    def validate(self) -> bool:
        """Check if task registry is accessible."""
        registry_path = self.tasks_path / 'registry.jsonl'
        return registry_path.exists()

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        phase: Optional[str] = None,
        iteration: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[TaskNode], int]:
        """
        List tasks with optional filtering.

        Args:
            status: Filter by task status
            phase: Filter by phase name
            iteration: Filter by LDD iteration
            limit: Max tasks to return
            offset: Pagination offset

        Returns:
            Tuple of (tasks, total_count)
        """
        registry_path = self.tasks_path / 'registry.jsonl'
        if not registry_path.exists():
            return [], 0

        tasks = []
        total = 0

        try:
            with open(registry_path, 'r') as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        data = json.loads(line)
                        task = self._json_to_task_node(data)
                        total += 1

                        # Apply filters
                        if status and task.status != status:
                            continue
                        if phase and task.phase != phase:
                            continue
                        if iteration is not None and task.iteration != iteration:
                            continue

                        # Apply pagination
                        if len(tasks) >= offset and len(tasks) < offset + limit:
                            tasks.append(task)

                        if len(tasks) >= offset + limit:
                            break

                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Invalid task JSON: {e}")
                        continue

        except Exception as e:
            logger.error(f"Error reading task registry: {e}")
            return [], 0

        return tasks, total

    def get_task(self, task_id: str) -> Optional[TaskNode]:
        """Get a single task by ID."""
        registry_path = self.tasks_path / 'registry.jsonl'
        if not registry_path.exists():
            return None

        try:
            with open(registry_path, 'r') as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        data = json.loads(line)
                        if data.get('task_id') == task_id:
                            return self._json_to_task_node(data)
                    except (json.JSONDecodeError, KeyError):
                        continue

        except Exception as e:
            logger.error(f"Error reading task {task_id}: {e}")

        return None

    def get_task_graph(self, session_id: Optional[str] = None) -> TaskGraph:
        """Get complete task graph for a session."""
        tasks, _ = self.list_tasks(limit=10000)

        # Filter by session if provided
        if session_id:
            tasks = [t for t in tasks if t.session_id == session_id]

        task_dict = {t.task_id: t for t in tasks}
        return TaskGraph(
            tasks=task_dict,
            tenant_id=self.tenant_id,
            session_id=session_id
        )

    def get_dependencies(self, task_id: str) -> List[TaskNode]:
        """Get all tasks this task depends on."""
        task = self.get_task(task_id)
        if not task:
            return []

        dependencies = []
        for dep_id in task.dependencies:
            dep_task = self.get_task(dep_id)
            if dep_task:
                dependencies.append(dep_task)

        return dependencies

    def get_dependents(self, task_id: str) -> List[TaskNode]:
        """Get all tasks that depend on this task."""
        tasks, _ = self.list_tasks(limit=10000)
        dependents = [t for t in tasks if task_id in t.dependencies]
        return dependents

    def get_blocked_tasks(self) -> List[TaskNode]:
        """Get all tasks currently blocked (waiting on dependencies)."""
        tasks, _ = self.list_tasks(status=TaskStatus.BLOCKED, limit=10000)
        return tasks

    def get_critical_path(self) -> List[TaskNode]:
        """
        Calculate critical path (longest dependency chain).
        Returns tasks in order from start to finish.
        """
        graph = self.get_task_graph()
        if not graph.tasks:
            return []

        # Build adjacency list for task dependencies
        graph_dict = {tid: t.dependencies for tid, t in graph.tasks.items()}

        # Find root tasks (no dependencies)
        roots = [tid for tid, deps in graph_dict.items() if not deps]
        if not roots:
            return []

        # Find longest path from each root
        def longest_path_from(node_id: str) -> Tuple[List[str], int]:
            """Returns (path, length) to leaf from this node."""
            # Find all tasks that depend on this node
            dependents = [tid for tid, deps in graph_dict.items() if node_id in deps]

            if not dependents:
                return ([node_id], 1)

            max_path = []
            max_length = 0
            for dep_id in dependents:
                sub_path, sub_len = longest_path_from(dep_id)
                if sub_len + 1 > max_length:
                    max_length = sub_len + 1
                    max_path = [node_id] + sub_path

            return (max_path, max_length)

        # Find the longest critical path
        critical_path_ids = []
        max_len = 0
        for root in roots:
            path, length = longest_path_from(root)
            if length > max_len:
                max_len = length
                critical_path_ids = path

        return [graph.tasks[tid] for tid in critical_path_ids if tid in graph.tasks]

    def _json_to_task_node(self, data: dict) -> TaskNode:
        """Convert JSON object to TaskNode."""
        status_str = data.get('status', 'pending').lower()
        try:
            status = TaskStatus[status_str.upper()]
        except KeyError:
            status = TaskStatus.PENDING

        created_at = data.get('created_at')
        if created_at and isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        started_at = data.get('started_at')
        if started_at and isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at)

        completed_at = data.get('completed_at')
        if completed_at and isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at)

        return TaskNode(
            task_id=data.get('task_id', ''),
            name=data.get('name', data.get('title', '')),
            status=status,
            phase=data.get('phase', 'unknown'),
            iteration=data.get('iteration', 0),
            parent_id=data.get('parent_id'),
            children_ids=data.get('children_ids', []),
            dependencies=data.get('dependencies', []),
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
            owner=data.get('owner'),
            error_message=data.get('error_message'),
            tenant_id=self.tenant_id,
        )


# ============================================================================
# SKILL & TOOL QUERY ENGINE
# ============================================================================

class SkillToolQuery(QueryEngine):
    """Query forged skill/tool registry, metadata, and dependency graphs."""

    def __init__(self, tenant_id: str = "_default"):
        """Initialize skill/tool query engine."""
        super().__init__(tenant_id)
        self.corvin_home = Path.home() / '.corvin'
        self.base_path = self.corvin_home / 'tenants' / tenant_id

    def validate(self) -> bool:
        """Check if skills directory exists."""
        return (self.base_path / 'skills').exists()

    def list_skills(
        self,
        scope: Optional[str] = None,
        enabled_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[SkillMetadata], int]:
        """
        List forged skills with optional filtering.

        Args:
            scope: Filter by skill scope
            enabled_only: Return only enabled skills
            limit: Max skills to return
            offset: Pagination offset

        Returns:
            Tuple of (skills, total_count)
        """
        scopes_path = self.base_path / 'skills'
        if not scopes_path.exists():
            return [], 0

        skills = []
        total = 0
        skill_idx = 0

        try:
            for scope_dir in scopes_path.iterdir():
                if not scope_dir.is_dir():
                    continue

                scope_name = scope_dir.name
                if scope and scope_name != scope:
                    continue

                skills_subdir = scope_dir / 'skills'
                if not skills_subdir.exists():
                    continue

                for skill_dir in skills_subdir.iterdir():
                    if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                        continue

                    total += 1

                    # Apply pagination
                    if skill_idx >= offset and len(skills) < limit:
                        skill = self._read_skill_metadata(skill_dir, scope_name)
                        if not enabled_only or self._is_skill_enabled(skill_dir):
                            skills.append(skill)

                    skill_idx += 1

        except Exception as e:
            logger.error(f"Error listing skills: {e}")

        return skills, total

    def get_skill(self, skill_id: str, scope: str = "_shared") -> Optional[SkillMetadata]:
        """Get skill metadata by ID and scope."""
        skill_path = self.base_path / 'skills' / scope / 'skills' / skill_id
        if not skill_path.exists():
            return None

        try:
            return self._read_skill_metadata(skill_path, scope)
        except Exception as e:
            logger.error(f"Error reading skill {skill_id}: {e}")
            return None

    def list_tools(
        self,
        status: Optional[ToolStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[ToolMetadata], int]:
        """
        List forged tools with optional filtering.

        Args:
            status: Filter by tool status
            limit: Max tools to return
            offset: Pagination offset

        Returns:
            Tuple of (tools, total_count)
        """
        tools_path = self.base_path / 'tools'
        if not tools_path.exists():
            return [], 0

        tools = []
        total = 0
        tool_idx = 0

        try:
            for tool_dir in tools_path.iterdir():
                if not tool_dir.is_dir() or tool_dir.name.startswith('.'):
                    continue

                total += 1

                if tool_idx >= offset and len(tools) < limit:
                    tool = self._read_tool_metadata(tool_dir)
                    if not status or tool.status == status:
                        tools.append(tool)

                tool_idx += 1

        except Exception as e:
            logger.error(f"Error listing tools: {e}")

        return tools, total

    def get_skill_dependencies(self, skill_id: str) -> List[str]:
        """Get all tools this skill depends on (direct dependencies)."""
        # For now, return empty list (would be populated from skill registry)
        return []

    def get_tool_dependencies(self, tool_id: str) -> List[str]:
        """Get all tools this tool depends on (composition)."""
        # For now, return empty list
        return []

    def compute_performance_metrics(
        self,
        skill_id: str
    ) -> Optional[LatencyMetrics]:
        """
        Compute latency and performance metrics for a skill.

        Returns None if no usage data available.
        """
        # Placeholder: would aggregate from event store
        return None

    def _read_skill_metadata(self, skill_path: Path, scope: str) -> SkillMetadata:
        """Read skill metadata from manifest.json and config.json."""
        manifest_path = skill_path / 'manifest.json'
        config_path = skill_path / 'config.json'

        skill_id = skill_path.name
        manifest_data = {}
        config_data = {}

        # Read manifest
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    manifest_data = json.load(f)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid manifest for skill {skill_id}: {e}")

        # Read config
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config_data = json.load(f)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid config for skill {skill_id}: {e}")

        created_at = manifest_data.get('created_at')
        if created_at and isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        last_used = manifest_data.get('last_used')
        if last_used and isinstance(last_used, str):
            last_used = datetime.fromisoformat(last_used)

        return SkillMetadata(
            skill_id=skill_id,
            name=manifest_data.get('name', skill_id),
            version=manifest_data.get('version', '0.0.0'),
            created_at=created_at,
            last_used=last_used,
            usage_count=manifest_data.get('usage_count', 0),
            success_rate=manifest_data.get('success_rate', 0.0),
            depends_on_tools=manifest_data.get('depends_on_tools', []),
            depends_on_skills=manifest_data.get('depends_on_skills', []),
            tags=manifest_data.get('tags', []),
            owner=manifest_data.get('owner'),
            description=manifest_data.get('description', ''),
            tenant_id=self.tenant_id,
        )

    def _read_tool_metadata(self, tool_path: Path) -> ToolMetadata:
        """Read tool metadata from manifest.json."""
        manifest_path = tool_path / 'manifest.json'
        tool_id = tool_path.name
        manifest_data = {}

        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    manifest_data = json.load(f)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid manifest for tool {tool_id}: {e}")

        created_at = manifest_data.get('created_at')
        if created_at and isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        last_used = manifest_data.get('last_used')
        if last_used and isinstance(last_used, str):
            last_used = datetime.fromisoformat(last_used)

        status_str = manifest_data.get('status', 'available').lower()
        try:
            status = ToolStatus[status_str.upper()]
        except KeyError:
            status = ToolStatus.AVAILABLE

        return ToolMetadata(
            tool_id=tool_id,
            name=manifest_data.get('name', tool_id),
            implementation=manifest_data.get('implementation', 'unknown'),
            version=manifest_data.get('version', '0.0.0'),
            created_at=created_at,
            last_used=last_used,
            usage_count=manifest_data.get('usage_count', 0),
            success_rate=manifest_data.get('success_rate', 0.0),
            used_by_skills=manifest_data.get('used_by_skills', []),
            used_by_tools=manifest_data.get('used_by_tools', []),
            status=status,
            tags=manifest_data.get('tags', []),
            tenant_id=self.tenant_id,
        )

    def _is_skill_enabled(self, skill_path: Path) -> bool:
        """Check if a skill is enabled in its config."""
        config_path = skill_path / 'config.json'
        if not config_path.exists():
            return True

        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                return config.get('enabled', True)
        except (json.JSONDecodeError, Exception):
            return True


# ============================================================================
# CATEGORY QUERY ENGINE
# ============================================================================

class CategoryQuery(QueryEngine):
    """Query category-level health metrics and event aggregation."""

    def __init__(self, tenant_id: str = "_default"):
        """Initialize category query engine."""
        super().__init__(tenant_id)
        self.corvin_home = Path.home() / '.corvin'
        self.events_path = self.corvin_home / 'tenants' / tenant_id / 'events'

    def validate(self) -> bool:
        """Check if events directory exists."""
        return (self.corvin_home / 'tenants' / self.tenant_id).exists()

    def list_categories(self) -> List[str]:
        """List unique event categories."""
        # Placeholder: would be populated from event store
        return ['learning', 'audit', 'core', 'plugins']

    def get_category_health(
        self,
        category: str,
        timerange: timedelta = timedelta(hours=24),
    ) -> CategoryHealthMetrics:
        """
        Get health metrics for a category over a timerange.

        Args:
            category: Category name
            timerange: Time window to aggregate (default: 24 hours)

        Returns:
            CategoryHealthMetrics with aggregated data
        """
        # Placeholder implementation
        return CategoryHealthMetrics(
            category=category,
            event_count=0,
            error_count=0,
            error_rate=0.0,
            avg_latency_ms=0.0,
            p50_latency_ms=0.0,
            p95_latency_ms=0.0,
            p99_latency_ms=0.0,
            max_latency_ms=0.0,
            status=CategoryStatus.HEALTHY,
            tenant_id=self.tenant_id,
            timestamp=datetime.utcnow(),
        )

    def filter_events_by_category(
        self,
        category: str,
        filters: Optional[Dict] = None,
        limit: int = 100,
    ) -> List[EventSummary]:
        """
        Get filtered events for a category.

        Args:
            category: Category to filter
            filters: Optional filters (timerange, error_type, status, etc.)
            limit: Max events to return

        Returns:
            List of matching events
        """
        # Placeholder implementation
        return []

    def drill_down(
        self,
        category: str,
        filters: Optional[Dict] = None,
    ) -> CategoryDrillDown:
        """
        Drill-down into a category with detailed filtering.

        Args:
            category: Category to drill into
            filters: Applied filters

        Returns:
            Drill-down view with events and metrics
        """
        events = self.filter_events_by_category(category, filters)
        metrics = self.get_category_health(category)

        return CategoryDrillDown(
            category=category,
            filters=filters or {},
            events=events,
            metrics=metrics,
            tenant_id=self.tenant_id,
        )
