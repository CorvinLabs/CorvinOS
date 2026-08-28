"""Phase 3.1b: Background Task Monitor for Proactive Updates.

Monitors running tasks and sends Discord notifications automatically
without waiting for user turns. Checks every N seconds, posts status
if something changed or milestone reached.
"""

import asyncio
import logging
from typing import Dict, Optional, List, Set
from datetime import datetime, timedelta
import aiohttp
import json
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
        # Tasks whose TERMINAL state (completed/failed) has been announced.
        # Without this, `_cleanup_completed_tasks` deleted the cooldown entry of
        # a task that is still in the publisher's index, so the very next poll
        # saw "state changed: completed" with no cooldown, notified again, and
        # cleaned up again — an unbounded notification loop, one message per
        # poll interval, forever. It is also what makes the cleanup safe at all.
        self.terminal_notified: Set[str] = set()

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

        # Milestone 1: Task progressed significantly.
        # The baseline for a task the monitor has not tracked yet is 0, not -1.
        # The old `-1` default made the first threshold 4 instead of 5, so a
        # task was announced one iteration early on first sight and every
        # subsequent step was measured from a shifted origin.
        last_iter = self.last_seen_iteration.get(task_id, 0)
        if latest.iteration_num > last_iter + 5:  # Every 5 iterations
            should_notify = True
            reason = f"Progress milestone: iteration {latest.iteration_num}"

        # Milestone 2: State changed — announced exactly once per task.
        elif latest.state in [TaskState.COMPLETED, TaskState.FAILED,
                              TaskState.AWAITING_INPUT]:
            if task_id in self.terminal_notified:
                return
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
            if latest.state in (TaskState.COMPLETED, TaskState.FAILED,
                                TaskState.AWAITING_INPUT):
                self.terminal_notified.add(task_id)

    def _cleanup_completed_tasks(self):
        """Remove completed/failed tasks from tracking dicts to prevent unbounded growth."""
        completed_tasks = set()
        for task_id in list(self.last_notified.keys()):
            latest = self.publisher.get_latest(task_id)
            if latest and latest.state in [TaskState.COMPLETED, TaskState.FAILED]:
                completed_tasks.add(task_id)

        for task_id in completed_tasks:
            # Mark the task as finished-with BEFORE dropping its cooldown.
            # Dropping the cooldown alone (the old behaviour) re-armed the very
            # notification this cleanup had just finished with: the task is
            # still in the publisher's index, so the next poll saw "state
            # changed: completed" with no cooldown, notified, and cleaned up
            # again — one message per poll interval, forever.
            self.terminal_notified.add(task_id)
            self.last_notified.pop(task_id, None)
            self.last_seen_iteration.pop(task_id, None)
            logger.debug(f"BackgroundMonitor cleaned up tracking for {task_id}")

    async def _send_notification(self, snapshot: StatusSnapshot, reason: str):
        """Send notification to Discord (via webhook with retry, or publisher fallback)."""
        if self.discord_webhook:
            await self._send_discord_webhook(snapshot, reason)
        else:
            await self.publisher.publish(snapshot)

    async def _send_discord_webhook(self, snapshot: StatusSnapshot, reason: str, max_retries: int = 3):
        """Send to Discord webhook with exponential backoff retry (fire-and-forget)."""
        embed = snapshot.to_discord_embed()
        embed["footer"] = {"text": f"{reason} | {snapshot.updated_at}"}
        payload = {"embeds": [embed]}

        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.discord_webhook,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if 200 <= resp.status < 300:
                            # Any 2xx is success. Testing only for 204 meant a
                            # 200 (what Discord returns for ?wait=true) fell
                            # through every branch and the loop RE-POSTED the
                            # same message up to max_retries times.
                            logger.debug(f"Discord webhook posted: {snapshot.task_id} — {reason}")
                            return
                        elif resp.status == 429:
                            # Discord's own rate limit. Retrying blindly is what
                            # gets a bot limited at the edge — honour the
                            # Retry-After the API hands back.
                            retry_after = 1.0
                            try:
                                body = await resp.json()
                                retry_after = float(
                                    resp.headers.get("Retry-After")
                                    or body.get("retry_after", 1.0)
                                )
                            except Exception:  # noqa: BLE001
                                pass
                            logger.warning(
                                f"Discord rate limited; retry in {retry_after}s")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(min(retry_after, 30.0))
                                continue
                            return
                        elif resp.status >= 400:
                            text = await resp.text()
                            logger.warning(f"Discord webhook failed (status {resp.status}): {text}")
                            if resp.status >= 500 and attempt < max_retries - 1:
                                await asyncio.sleep(2 ** attempt)
                                continue
                            return
            except asyncio.TimeoutError:
                logger.warning(f"Discord webhook timeout (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except aiohttp.ClientError as e:
                logger.warning(f"Discord webhook client error: {e} (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"Discord webhook unexpected error: {e}")
                return

# Global monitor instance
_monitor: Optional[BackgroundMonitor] = None

def get_monitor() -> BackgroundMonitor:
    """Get or create the global background monitor."""
    global _monitor
    if _monitor is None:
        _monitor = BackgroundMonitor()
    return _monitor

async def start_background_monitor(poll_interval: float = 30.0, discord_webhook: Optional[str] = None):
    """Start the background monitor (call once at app startup).

    The started monitor becomes THE global instance. Previously this created a
    local one and left the module global pointing at a different, idle monitor
    — so `stop_background_monitor()` stopped the wrong object and the polling
    loop kept running after shutdown, holding the event loop open.

    Idempotent: calling it twice returns the already-running monitor rather
    than starting a second polling loop against the same publisher.
    """
    global _monitor
    if _monitor is not None and _monitor.is_running:
        return _monitor
    _monitor = BackgroundMonitor(poll_interval=poll_interval,
                                 discord_webhook=discord_webhook)
    # Run in background (don't await) — store task to prevent GC from cancelling it
    _monitor._background_task = asyncio.create_task(_monitor.start())
    return _monitor

def stop_background_monitor():
    """Stop the background monitor (call on app shutdown)."""
    global _monitor
    if _monitor is None:
        return
    _monitor.stop()
    task = _monitor._background_task
    if task is not None and not task.done():
        # stop() only clears the flag; the loop is parked in `await sleep()` for
        # up to poll_interval seconds and would keep the loop alive that long.
        task.cancel()
