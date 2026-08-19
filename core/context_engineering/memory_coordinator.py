"""MemoryCoordinator — Persistent-to-Ephemeral Bridge (ADR-0358).

Loads task templates from PROJECT or GLOBAL memory layers and persists
learning events after task execution. Bridges MemoryContext (persistent)
with ExecutionContext (ephemeral).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class MemoryCoordinatorError(Exception):
    """Base exception for MemoryCoordinator errors."""

    pass


class MemoryLayerNotFound(MemoryCoordinatorError):
    """Raised when neither PROJECT nor GLOBAL memory layers exist."""

    pass


class EventPersistenceError(MemoryCoordinatorError):
    """Raised when persisting learning events fails."""

    pass


class MemoryCoordinator:
    """Bridge between persistent memory and ephemeral task execution.

    Responsibilities:
    1. Load task templates from PROJECT > GLOBAL hierarchy
    2. Persist learning events after task execution
    3. Handle memory layer fallback and errors gracefully
    """

    def __init__(self, corvin_home: Optional[str] = None, tenant_id: str = "_default"):
        """Initialize MemoryCoordinator.

        Args:
            corvin_home: Path to CORVIN_HOME. If None, uses environment variable.
            tenant_id: Tenant identifier for multi-tenant isolation (default: "_default").

        Raises:
            ValueError: If corvin_home not provided and CORVIN_HOME env var not set.
            ValueError: If tenant_id is invalid.
        """
        if corvin_home is None:
            corvin_home = os.environ.get("CORVIN_HOME")
            if not corvin_home:
                raise ValueError(
                    "corvin_home not provided and CORVIN_HOME environment variable not set"
                )

        # Validate tenant_id (fail-closed)
        if not isinstance(tenant_id, str) or len(tenant_id.strip()) == 0:
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        self.corvin_home = Path(corvin_home)
        self.tenant_id = tenant_id
        self._project_memory_path = self.corvin_home / "tenants" / tenant_id / "project_memory"
        self._global_memory_path = self.corvin_home / "tenants" / tenant_id / "global_memory"
        self._learning_events_path = (
            self.corvin_home / "tenants" / tenant_id / "learning" / "events.jsonl"
        )

    def load_task_template(self, task_type: str) -> Dict[str, Any]:
        """Load task template from PROJECT or GLOBAL memory hierarchy.

        Attempts to load from PROJECT memory first; falls back to GLOBAL
        if not found. Task template contains typical duration, strategy,
        common errors, and success rate.

        Args:
            task_type: Task type identifier (e.g., 'code_fix', 'documentation')

        Returns:
            Task template dict with keys: task_type, typical_duration, typical_strategy,
            typical_errors, success_rate, project_patterns

        Raises:
            MemoryLayerNotFound: If neither PROJECT nor GLOBAL memory has the template.
        """
        # Try PROJECT layer first
        project_template = self._load_from_project(task_type)
        if project_template is not None:
            return project_template

        # Fall back to GLOBAL layer
        global_template = self._load_from_global(task_type)
        if global_template is not None:
            return global_template

        # Neither layer has the template
        raise MemoryLayerNotFound(
            f"Task template '{task_type}' not found in PROJECT or GLOBAL memory layers"
        )

    def _load_from_project(self, task_type: str) -> Optional[Dict[str, Any]]:
        """Load template from PROJECT memory layer.

        Args:
            task_type: Task type identifier

        Returns:
            Template dict if found, None otherwise.
        """
        if not self._project_memory_path.exists():
            return None

        template_file = self._project_memory_path / f"{task_type}.json"
        if not template_file.exists():
            return None

        try:
            with open(template_file, "r") as f:
                template = json.load(f)
            template["_source"] = "project"
            return template
        except (json.JSONDecodeError, IOError) as e:
            raise MemoryCoordinatorError(
                f"Failed to load PROJECT template '{task_type}': {str(e)}"
            )

    def _load_from_global(self, task_type: str) -> Optional[Dict[str, Any]]:
        """Load template from GLOBAL memory layer.

        Args:
            task_type: Task type identifier

        Returns:
            Template dict if found, None otherwise.
        """
        if not self._global_memory_path.exists():
            return None

        template_file = self._global_memory_path / f"{task_type}.json"
        if not template_file.exists():
            return None

        try:
            with open(template_file, "r") as f:
                template = json.load(f)
            template["_source"] = "global"
            return template
        except (json.JSONDecodeError, IOError) as e:
            raise MemoryCoordinatorError(
                f"Failed to load GLOBAL template '{task_type}': {str(e)}"
            )

    def persist_learning_event(
        self,
        task_id: str,
        tenant_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Persist a learning event to the jsonl file.

        Learning events are immutable records of task execution insights.
        Persisted as JSON Lines format (one event per line) with timestamps.

        Args:
            task_id: Task identifier
            tenant_id: Tenant identifier (must match coordinator's tenant_id)
            event_type: Type of learning event (e.g., 'strategy_success', 'error_pattern')
            payload: Event data (dict)

        Raises:
            ValueError: If tenant_id doesn't match coordinator's tenant_id (fail-closed).
            EventPersistenceError: If persistence fails.
        """
        # Validate tenant_id matches (fail-closed on mismatch)
        if tenant_id != self.tenant_id:
            raise ValueError(
                f"tenant_id mismatch: got {tenant_id}, expected {self.tenant_id}. "
                f"Create a new MemoryCoordinator for tenant {tenant_id}."
            )

        # Create directory structure if needed
        self._learning_events_path.parent.mkdir(parents=True, exist_ok=True)

        # Prepare event record
        event_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "tenant_id": tenant_id,
            "event_type": event_type,
            "payload": payload,
        }

        # Append to jsonl file (atomically)
        try:
            with open(self._learning_events_path, "a") as f:
                f.write(json.dumps(event_record) + "\n")
        except (IOError, OSError) as e:
            raise EventPersistenceError(
                f"Failed to persist learning event: {str(e)}"
            )

    def persist_learning_events_batch(
        self,
        task_id: str,
        tenant_id: str,
        events: List[Dict[str, Any]],
    ) -> None:
        """Persist multiple learning events in a batch.

        More efficient than calling persist_learning_event multiple times.

        Args:
            task_id: Task identifier
            tenant_id: Tenant identifier
            events: List of event dicts, each with keys: event_type, payload

        Raises:
            EventPersistenceError: If persistence fails.
        """
        if not events:
            return

        # Create directory structure if needed
        self._learning_events_path.parent.mkdir(parents=True, exist_ok=True)

        # Prepare event records
        event_records = []
        for event in events:
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id,
                "tenant_id": tenant_id,
                "event_type": event.get("event_type", "unknown"),
                "payload": event.get("payload", {}),
            }
            event_records.append(record)

        # Append all events to jsonl file
        try:
            with open(self._learning_events_path, "a") as f:
                for record in event_records:
                    f.write(json.dumps(record) + "\n")
        except (IOError, OSError) as e:
            raise EventPersistenceError(
                f"Failed to persist learning events batch: {str(e)}"
            )

    def read_learning_events(
        self,
        task_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 0,
    ) -> List[Dict[str, Any]]:
        """Read learning events from the jsonl file.

        Args:
            task_id: Filter by task_id (None = all tasks)
            event_type: Filter by event_type (None = all types)
            limit: Maximum events to return (0 = all)

        Returns:
            List of event dicts matching filters.
        """
        if not self._learning_events_path.exists():
            return []

        events = []
        try:
            with open(self._learning_events_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    event = json.loads(line)

                    # Apply filters
                    if task_id and event.get("task_id") != task_id:
                        continue
                    if event_type and event.get("event_type") != event_type:
                        continue

                    events.append(event)

                    # Check limit
                    if limit > 0 and len(events) >= limit:
                        break

        except (IOError, json.JSONDecodeError) as e:
            raise MemoryCoordinatorError(
                f"Failed to read learning events: {str(e)}"
            )

        return events

    def get_learning_event_stats(self) -> Dict[str, Any]:
        """Get statistics about learning events.

        Returns:
            Dict with keys: total_events, event_types, tasks_count
        """
        if not self._learning_events_path.exists():
            return {
                "total_events": 0,
                "event_types": {},
                "tasks_count": 0,
            }

        total_events = 0
        event_types = {}
        task_ids = set()

        try:
            with open(self._learning_events_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    event = json.loads(line)
                    total_events += 1
                    event_type = event.get("event_type", "unknown")
                    event_types[event_type] = event_types.get(event_type, 0) + 1
                    task_ids.add(event.get("task_id"))

        except (IOError, json.JSONDecodeError):
            # Return partial stats if reading fails
            pass

        return {
            "total_events": total_events,
            "event_types": event_types,
            "tasks_count": len(task_ids),
        }

    def memory_available(self) -> bool:
        """Check if memory coordinator is functional.

        Returns True if at least the global memory layer exists,
        False otherwise.

        Returns:
            True if memory system is available, False if degraded.
        """
        # Check if we can at least read/write (directories exist or creatable)
        try:
            self._learning_events_path.parent.mkdir(parents=True, exist_ok=True)
            return True
        except (OSError, IOError):
            return False
