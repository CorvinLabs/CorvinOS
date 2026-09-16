"""TaskManager Subsystem — Centralized Task Orchestration (ADR-0850)"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any


class TaskState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    task_id: str
    state: TaskState
    cost: float = 0.0
    confidence: float = 1.0


class TaskManager:
    """Centralized task orchestration."""
    
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
    
    async def create_task(self, task_id: str) -> Task:
        task = Task(task_id=task_id, state=TaskState.QUEUED)
        self.tasks[task_id] = task
        return task
    
    async def update_state(self, task_id: str, state: TaskState) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].state = state
