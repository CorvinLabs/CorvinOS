"""Phase 3.1b: Background Task Monitor for Proactive Updates.

Monitors running tasks and sends Discord notifications automatically
without waiting for user turns. Checks every N seconds, posts status
if something changed or milestone reached.
"""

import asyncio
import logging
from typing import Dict, Optional, List, Set
from datetime import datetime, timedelta
from .status_snapshot import get_publisher, TaskState, StatusSnapshot

logger = logging.getLogger(__name__)

class BackgroundMonitor:
    """Monitor tasks continuously and publish proactive updates (Phase 3.1b)."""

    def __init__(self, poll_interval: float = 30.0, discord_webhook: Optional[str] = None, cleanup_completed: bool = True):
        """
        Args:
            poll_interval: Seconds between status checks
            discord_webhook: Discord webhook URL for direct posting (bypasses publisher)
            cleanup_completed: Remove completed tasks from tracking dicts to prevent unbounded growth
        """
        self.poll_interval = poll_interval
        self.discord_webhook = discord_webhook
        self.publisher = get_publisher()
        self.is_running = False
        self.cleanup_completed = cleanup_completed

        # Track what we've already notified about
        self.last_seen_iteration: Dict[str, int] = {}
        self.last_notified: Dict[str, datetime] = {}
        self.notification_cooldown = timedelta(seconds=60)  # Min time between notifs per task
        self._background_task: Optional[asyncio.Task] = None  # Store task ref to prevent GC

    async def start(self):
        """Start background monitoring (runs forever until stopped)."""
        self.is_running = True
        logger.info(f"BackgroundMonitor started (poll interval: {self.poll_interval}s)")

        try:
            while self.is_running:
                await self._check_all_tasks()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("BackgroundMonitor stopped.")
            self.is_running = False

    def stop(self):
        """Stop monitoring."""
        self.is_running = False
        logger.info("BackgroundMonitor stopping...")

    async def _check_all_tasks(self):
        """Poll all tasks and emit notifications for changes (O(n) not O(n²))."""
        # Get all tracked tasks from publisher's index
        task_ids: Set[str] = set(self.publisher._latest_by_task.keys()) if hasattr(self.publisher, '_latest_by_task') else set()

        # Fallback to history if index not available
        if not task_ids:
            task_ids = set(s.task_id for s in self.publisher.history)

        # Check each task for updates
        for task_id in task_ids:
            await self._check_task(task_id)

        # Cleanup tracking dicts for completed tasks (prevent unbounded growth)
        if self.cleanup_completed:
            self._cleanup_completed_tasks()

    async def _check_task(self, task_id: str):
        """Check if a single task has new updates to notify about."""
        latest = self.publisher.get_latest(task_id)
        if not latest:
            return

        # Don't spam: check cooldown
        last_notif = self.last_notified.get(task_id, datetime.min)
        if datetime.now() - last_notif < self.notification_cooldown:
            return

        # Determine if we should notify
        should_notify = False
        reason = ""

        # Milestone 1: Task progressed significantly
        last_iter = self.last_seen_iteration.get(task_id, -1)
        if latest.iteration_num > last_iter + 5:  # Every 5 iterations
            should_notify = True
            reason = f"Progress milestone: iteration {latest.iteration_num}"

        # Milestone 2: State changed
        elif latest.state in [TaskState.COMPLETED, TaskState.FAILED, TaskState.AWAITING_INPUT]:
            should_notify = True
            reason = f"State changed: {latest.state.value}"

        # Milestone 3: User input needed
        elif latest.user_action_required:
            should_notify = True
            reason = f"User input needed: {latest.user_action_required.prompt}"

        # Milestone 4: Error occurred
        elif latest.blocking_reason:
            should_notify = True
            reason = f"Blocking error: {latest.blocking_reason}"

        if should_notify:
            logger.info(f"BackgroundMonitor notify: {task_id} — {reason}")
            await self._send_notification(latest, reason)
            self.last_notified[task_id] = datetime.now()
            self.last_seen_iteration[task_id] = latest.iteration_num

    def _cleanup_completed_tasks(self):
        """Remove completed/failed tasks from tracking dicts to prevent unbounded growth."""
        completed_tasks = set()
        for task_id in list(self.last_notified.keys()):
            latest = self.publisher.get_latest(task_id)
            if latest and latest.state in [TaskState.COMPLETED, TaskState.FAILED]:
                completed_tasks.add(task_id)

        for task_id in completed_tasks:
            self.last_notified.pop(task_id, None)
            self.last_seen_iteration.pop(task_id, None)
            logger.debug(f"BackgroundMonitor cleaned up tracking for {task_id}")

    async def _send_notification(self, snapshot: StatusSnapshot, reason: str):
        """Send notification to Discord (via webhook or publisher)."""
        if self.discord_webhook:
            # Direct Discord webhook POST
            try:
                embed = snapshot.to_discord_embed()
                embed["footer"] = {"text": f"{reason} | {snapshot.updated_at}"}
                # TODO: POST to webhook URL with aiohttp (https://github.com/aio-libs/aiohttp)
                logger.info(f"Discord notification queued: {reason} (webhook POST not yet implemented)")
            except Exception as e:
                logger.error(f"Discord notification failed: {e}")
        else:
            # Use publisher (routed through Discord notifier)
            await self.publisher.publish(snapshot)

# Global monitor instance
_monitor: Optional[BackgroundMonitor] = None

def get_monitor() -> BackgroundMonitor:
    """Get or create the global background monitor."""
    global _monitor
    if _monitor is None:
        _monitor = BackgroundMonitor()
    return _monitor

async def start_background_monitor(poll_interval: float = 30.0, discord_webhook: Optional[str] = None):
    """Start the background monitor (call once at app startup)."""
    monitor = BackgroundMonitor(poll_interval=poll_interval, discord_webhook=discord_webhook)
    # Run in background (don't await) — store task to prevent GC from cancelling it
    monitor._background_task = asyncio.create_task(monitor.start())
    return monitor

def stop_background_monitor():
    """Stop the background monitor (call on app shutdown)."""
    monitor = get_monitor()
    monitor.stop()
