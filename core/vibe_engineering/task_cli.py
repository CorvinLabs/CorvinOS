"""Phase 3.1a: Task CLI for Auto-Resume and Monitoring.

Commands:
  corvin task list              # Show all pending tasks
  corvin task resume <task_id>  # Resume from checkpoint
  corvin task status <task_id>  # Show current status
  corvin task monitor <task_id> # Watch task in real-time
"""

import asyncio
import json
import logging
from typing import Optional, List
from pathlib import Path
from .status_snapshot import get_publisher, TaskState

logger = logging.getLogger(__name__)

class TaskCLI:
    """CLI interface for task management (Phase 3.1a)."""

    def __init__(self, state_store, vibe_engine):
        self.state_store = state_store
        self.vibe_engine = vibe_engine
        self.publisher = get_publisher()

    async def list_tasks(self) -> List[str]:
        """List all pending tasks (not completed) from publisher history."""
        logger.info("Listing tasks...")
        seen_tasks = set()
        pending = []

        # Scan publisher history for non-terminal tasks
        for snapshot in self.publisher.history:
            if snapshot.task_id not in seen_tasks:
                if snapshot.state not in [TaskState.COMPLETED, TaskState.FAILED]:
                    pending.append(snapshot.task_id)
                    seen_tasks.add(snapshot.task_id)

        logger.info(f"Found {len(pending)} pending tasks")
        return pending

    async def resume(self, task_id: str) -> dict:
        """Resume task from last checkpoint (load JSON, validate, prepare to run)."""
        logger.info(f"Resuming task {task_id}...")

        try:
            checkpoints_dir = Path("~/.corvin/vibe/checkpoints").expanduser()
            checkpoints_dir.mkdir(parents=True, exist_ok=True)

            if not checkpoints_dir.exists():
                return {"status": "error", "reason": "Checkpoints directory not found"}

            # Find latest checkpoint for this task
            checkpoints = sorted(checkpoints_dir.glob(f"{task_id}*.json"), reverse=True)
            if not checkpoints:
                return {"status": "error", "reason": f"No checkpoint found for {task_id}"}

            latest_checkpoint_path = checkpoints[0]
            logger.info(f"Found checkpoint: {latest_checkpoint_path}")

            # Load and validate checkpoint JSON
            with open(latest_checkpoint_path, 'r') as f:
                checkpoint_data = json.load(f)

            checkpoint_id = checkpoint_data.get("checkpoint_id", latest_checkpoint_path.stem)
            iteration_num = checkpoint_data.get("iteration_num", 0)
            context_state = checkpoint_data.get("context_state", {})

            logger.info(f"Checkpoint loaded: iteration {iteration_num}, context keys: {list(context_state.keys())}")

            return {
                "status": "loaded",
                "checkpoint_id": checkpoint_id,
                "task_id": task_id,
                "iteration": iteration_num,
                "context_summary": {
                    "goal": context_state.get("goal", ""),
                    "progress": context_state.get("progress", {})
                }
            }

        except json.JSONDecodeError as e:
            logger.error(f"Checkpoint JSON parse error: {e}")
            return {"status": "error", "reason": f"JSON parse error in checkpoint: {e}"}
        except Exception as e:
            logger.error(f"Resume failed: {e}")
            return {"status": "error", "reason": str(e)}

    async def status(self, task_id: str) -> dict:
        """Show current task status."""
        latest = self.publisher.get_latest(task_id)
        if latest:
            return latest.to_dict()
        return {"status": "not_found", "task_id": task_id}

    async def monitor(self, task_id: str, poll_interval: float = 2.0, max_iterations: int = 1800):
        """Watch task status in real-time (polling).

        Args:
            task_id: Task ID to monitor
            poll_interval: Seconds between status checks
            max_iterations: Max polls before timeout (default 1800 = 1 hour at 2s interval)
        """
        logger.info(f"Monitoring task {task_id} (Ctrl+C to stop)...")
        iteration = 0
        try:
            while iteration < max_iterations:
                snapshot = self.publisher.get_latest(task_id)
                if snapshot:
                    print(snapshot.to_cli_summary())
                    if snapshot.state in [TaskState.COMPLETED, TaskState.FAILED]:
                        logger.info("Task finished.")
                        break
                else:
                    logger.warning(f"Task {task_id} not found in history (may have been purged)")
                    break
                iteration += 1
                await asyncio.sleep(poll_interval)

            if iteration >= max_iterations:
                logger.warning(f"Monitor timeout: reached max iterations ({max_iterations})")
        except KeyboardInterrupt:
            logger.info("Monitor stopped.")

    async def auto_resume_last_unfinished(self) -> dict:
        """Find and resume the last unfinished task (auto-start)."""
        history = self.publisher.history
        unfinished = [
            s for s in history
            if s.state not in [TaskState.COMPLETED, TaskState.FAILED]
            and s.can_resume
        ]

        if unfinished:
            latest = unfinished[-1]
            logger.info(f"Auto-resuming task {latest.task_id}")
            return await self.resume(latest.task_id)

        logger.info("No unfinished tasks to resume.")
        return {"status": "not_found", "reason": "No unfinished tasks to resume"}

# CLI entry points (would be wired into click / argparse)

async def cli_list_tasks(state_store, vibe_engine):
    """corvin task list"""
    cli = TaskCLI(state_store, vibe_engine)
    tasks = await cli.list_tasks()
    for task_id in tasks:
        print(f"  - {task_id}")

async def cli_resume_task(task_id: str, state_store, vibe_engine):
    """corvin task resume <task_id>"""
    cli = TaskCLI(state_store, vibe_engine)
    result = await cli.resume(task_id)
    print(f"Result: {result}")

async def cli_task_status(task_id: str, state_store, vibe_engine):
    """corvin task status <task_id>"""
    cli = TaskCLI(state_store, vibe_engine)
    status = await cli.status(task_id)
    print(json.dumps(status, indent=2))

async def cli_monitor_task(task_id: str, state_store, vibe_engine):
    """corvin task monitor <task_id>"""
    cli = TaskCLI(state_store, vibe_engine)
    await cli.monitor(task_id)
