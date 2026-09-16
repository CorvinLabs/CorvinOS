"""Task Completion Verification for Context Pipeline.

Reads the canonical task_registry.json and provides methods to:
1. Check if a task is ACCEPTED (done, don't suggest again)
2. Filter task suggestions (remove completed tasks)
3. Mark tasks as blocked/in-progress

Used by the context engineering pipeline to avoid re-suggesting
completed initiatives (e.g., "Skill Forge v2.0", "Model Selector").
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Set
from datetime import datetime


class TaskCompletionVerifier:
    """Verifies task completion status from canonical registry."""

    def __init__(self, registry_path: str = None):
        """Initialize from task_registry.json."""
        if registry_path is None:
            registry_path = str(Path.home() / ".corvin" / "task_registry.json")

        self.registry_path = Path(registry_path)
        self.registry: Dict = {}
        self._load_registry()

    def _load_registry(self):
        """Load task_registry.json into memory."""
        if not self.registry_path.exists():
            # Registry doesn't exist yet; empty state is OK
            self.registry = {"tasks": {}, "timestamp": None}
            return

        try:
            with open(self.registry_path) as f:
                self.registry = json.load(f)
        except Exception as e:
            print(f"⚠️  Failed to load task registry: {e}")
            self.registry = {"tasks": {}, "timestamp": None}

    def is_completed(self, task_id: str) -> bool:
        """Check if a task is ACCEPTED (completed)."""
        tasks = self.registry.get("tasks", {})
        task = tasks.get(task_id, {})
        return task.get("status") == "ACCEPTED"

    def get_completed_tasks(self) -> Set[str]:
        """Return set of all ACCEPTED (completed) task IDs."""
        tasks = self.registry.get("tasks", {})
        return {
            task_id for task_id, task in tasks.items()
            if task.get("status") == "ACCEPTED"
        }

    def get_in_progress_tasks(self) -> Set[str]:
        """Return set of IN_PROGRESS task IDs."""
        tasks = self.registry.get("tasks", {})
        return {
            task_id for task_id, task in tasks.items()
            if task.get("status") == "IN_PROGRESS"
        }

    def get_blocked_tasks(self) -> Set[str]:
        """Return set of BLOCKED task IDs."""
        tasks = self.registry.get("tasks", {})
        return {
            task_id for task_id, task in tasks.items()
            if task.get("status") == "BLOCKED"
        }

    def filter_suggestions(self, suggested_tasks: List[str]) -> List[str]:
        """Remove completed tasks from suggestion list.

        Args:
            suggested_tasks: List of task_ids to filter

        Returns:
            Filtered list with ACCEPTED tasks removed
        """
        completed = self.get_completed_tasks()
        return [task for task in suggested_tasks if task not in completed]

    def get_task_info(self, task_id: str) -> Optional[Dict]:
        """Get full task information from registry."""
        tasks = self.registry.get("tasks", {})
        return tasks.get(task_id)

    def format_status_for_context(self) -> str:
        """Format task status for inclusion in context brief.

        Returns a short summary of task completion status to inject
        into the context engineering pipeline, so the model knows
        which initiatives are done and which are active.
        """
        completed = self.get_completed_tasks()
        in_progress = self.get_in_progress_tasks()
        blocked = self.get_blocked_tasks()

        lines = []
        lines.append("## Task Completion Status (Canonical Registry)")
        lines.append(f"- ✅ COMPLETED: {len(completed)} tasks (don't suggest these)")
        lines.append(f"- 🟡 IN_PROGRESS: {len(in_progress)} tasks (active)")
        lines.append(f"- ❌ BLOCKED: {len(blocked)} tasks (waiting for fixes)")

        if in_progress:
            lines.append("\n### Active Initiatives (Focus Here):")
            tasks = self.registry.get("tasks", {})
            for task_id in sorted(in_progress)[:5]:  # Show first 5
                task = tasks.get(task_id, {})
                title = task.get("title", task_id)
                lines.append(f"- 🟡 {title} ({task_id})")

        if blocked:
            lines.append("\n### Blocked (Waiting for Resolution):")
            tasks = self.registry.get("tasks", {})
            for task_id in sorted(blocked)[:3]:  # Show first 3
                task = tasks.get(task_id, {})
                title = task.get("title", task_id)
                lines.append(f"- ❌ {title} ({task_id})")

        return "\n".join(lines)


# Singleton instance for easy access
_verifier: Optional[TaskCompletionVerifier] = None


def get_verifier() -> TaskCompletionVerifier:
    """Get or create the singleton verifier instance."""
    global _verifier
    if _verifier is None:
        _verifier = TaskCompletionVerifier()
    return _verifier


def is_task_completed(task_id: str) -> bool:
    """Check if a task is completed (convenience function)."""
    return get_verifier().is_completed(task_id)


def filter_suggested_tasks(suggestions: List[str]) -> List[str]:
    """Filter out completed tasks from suggestions (convenience function)."""
    return get_verifier().filter_suggestions(suggestions)
