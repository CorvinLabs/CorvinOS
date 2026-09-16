"""TaskManager Subsystem (ADR-0850) — k=5 PRODUCTION

Centralized task orchestration with state tracking, resource gating, dependency resolution.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Set, List, Optional
from datetime import datetime
import asyncio


class TaskState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    task_id: str
    state: TaskState = TaskState.QUEUED
    cost_budget: float = 10.0  # USD
    cost_spent: float = 0.0
    confidence_required: float = 0.7
    dependencies: Set[str] = field(default_factory=set)
    subtasks: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    @property
    def cost_remaining(self) -> float:
        return max(0, self.cost_budget - self.cost_spent)
    
    @property
    def is_within_budget(self) -> bool:
        return self.cost_spent < self.cost_budget


class ResourceQuotas:
    """Track quotas per model (hourly limits)."""
    
    def __init__(self):
        self.quotas = {
            "haiku": 1000,
            "sonnet": 500,
            "opus": 100
        }
        self.used_this_hour = {
            "haiku": 0,
            "sonnet": 0,
            "opus": 0
        }
        self.last_reset_hour = datetime.utcnow().hour
    
    def _reset_if_needed(self) -> None:
        """Reset hourly counters if hour changed."""
        current_hour = datetime.utcnow().hour
        if current_hour != self.last_reset_hour:
            self.used_this_hour = {k: 0 for k in self.used_this_hour}
            self.last_reset_hour = current_hour
    
    async def check_quota(self, model: str) -> bool:
        """Check if we have quota remaining."""
        self._reset_if_needed()
        remaining = self.quotas[model] - self.used_this_hour[model]
        return remaining > 0
    
    async def increment_usage(self, model: str, count: int = 1) -> None:
        """Record model usage."""
        self._reset_if_needed()
        self.used_this_hour[model] = min(
            self.used_this_hour[model] + count,
            self.quotas[model]
        )


class TaskManager:
    """Production task orchestration."""
    
    name = "task_manager"
    version = "1.0.0"
    
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.quotas = ResourceQuotas()
        self.task_lock = asyncio.Lock()
    
    async def create_task(
        self, 
        task_id: str,
        cost_budget: float = 10.0,
        confidence_required: float = 0.7,
        dependencies: Optional[Set[str]] = None
    ) -> Task:
        """Create task with constraints."""
        async with self.task_lock:
            if task_id in self.tasks:
                raise ValueError(f"Task {task_id} already exists")
            
            task = Task(
                task_id=task_id,
                cost_budget=cost_budget,
                confidence_required=confidence_required,
                dependencies=dependencies or set()
            )
            self.tasks[task_id] = task
            return task
    
    async def update_state(self, task_id: str, state: TaskState) -> None:
        """Update task state with timestamp."""
        async with self.task_lock:
            if task_id not in self.tasks:
                raise ValueError(f"Task {task_id} not found")
            
            task = self.tasks[task_id]
            task.state = state
            
            if state == TaskState.RUNNING and not task.started_at:
                task.started_at = datetime.utcnow().isoformat() + "Z"
            elif state in [TaskState.COMPLETED, TaskState.FAILED] and not task.completed_at:
                task.completed_at = datetime.utcnow().isoformat() + "Z"
    
    async def wait_for_dependencies(self, task_id: str, timeout_s: int = 300) -> bool:
        """Block until all dependencies complete."""
        if task_id not in self.tasks:
            return False
        
        task = self.tasks[task_id]
        start_time = datetime.utcnow()
        
        while True:
            # Check if all dependencies are done
            all_done = all(
                self.tasks.get(dep_id, Task(task_id=dep_id)).state in [TaskState.COMPLETED, TaskState.FAILED]
                for dep_id in task.dependencies
            )
            
            if all_done:
                return True
            
            # Check timeout
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            if elapsed > timeout_s:
                return False
            
            await asyncio.sleep(0.1)
    
    async def check_quota(self, model: str) -> bool:
        """Check if we have quota remaining."""
        return await self.quotas.check_quota(model)
    
    async def deduct_cost(self, task_id: str, amount: float) -> bool:
        """Deduct cost from task budget."""
        async with self.task_lock:
            if task_id not in self.tasks:
                return False
            
            task = self.tasks[task_id]
            if task.cost_spent + amount > task.cost_budget:
                return False
            
            task.cost_spent += amount
            return True
    
    async def add_subtask(self, task_id: str, subtask_id: str) -> None:
        """Register a subtask."""
        async with self.task_lock:
            if task_id in self.tasks:
                self.tasks[task_id].subtasks.append(subtask_id)
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        return self.tasks.get(task_id)
    
    def list_tasks(self, state_filter: Optional[TaskState] = None) -> List[Task]:
        """List all tasks, optionally filtered by state."""
        tasks = list(self.tasks.values())
        if state_filter:
            tasks = [t for t in tasks if t.state == state_filter]
        return tasks
