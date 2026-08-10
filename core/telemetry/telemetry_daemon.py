"""Background telemetry daemon (ADR-0288).

Runs as a background thread/task. Every hour:
1. Compute stability digest for all enabled flags
2. POST to /v1/telemetry/feature-stability
3. Log result (success or failure)

Failures are non-fatal; telemetry is best-effort.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable

from core.telemetry.stability_metrics import compute_digest

logger = logging.getLogger(__name__)


class TelemetryDaemon:
    """Background task for sending stability metrics."""

    def __init__(
        self,
        send_fn: Callable[[dict], int] | None = None,
        interval_seconds: int = 3600,
        enabled: bool = True,
    ):
        """Initialize daemon.

        Args:
            send_fn: Function to send telemetry (returns status code). If None, no-op.
            interval_seconds: How often to send (default 1 hour)
            enabled: Whether telemetry is enabled (checks spec.telemetry.stability_metrics)
        """
        self.send_fn = send_fn
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None

    async def run(self) -> None:
        """Main daemon loop (async)."""
        if not self.enabled:
            logger.debug("Telemetry daemon is disabled")
            return

        self._running = True
        logger.info(f"Telemetry daemon started (interval: {self.interval_seconds}s)")

        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                await self.send_digest()
            except asyncio.CancelledError:
                logger.info("Telemetry daemon cancelled")
                break
            except Exception as e:
                logger.error(f"Telemetry daemon error: {e}", exc_info=True)

    async def send_digest(self) -> None:
        """Compute and send one digest."""
        if not self.enabled or not self.send_fn:
            return

        try:
            # Compute digest
            digest = compute_digest(
                tenant_id="_default",  # TODO: get from context
                instance_id="unknown",  # TODO: get from config
            )

            # Send
            payload = digest.to_dict()
            status = await self._send_async(payload)

            if 200 <= status < 300:
                logger.info(f"Telemetry sent (status {status})")
            else:
                logger.warning(f"Telemetry send failed (status {status})")

        except Exception as e:
            logger.error(f"Error sending telemetry: {e}", exc_info=True)

    async def _send_async(self, payload: dict) -> int:
        """Send payload via send_fn (wrapped in executor for non-async functions)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.send_fn, payload)

    def start(self) -> None:
        """Start daemon task (for asyncio event loop)."""
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    def stop(self) -> None:
        """Stop daemon task."""
        self._running = False
        if self._task:
            self._task.cancel()


# Singleton instance (initialized by app startup)
_DAEMON: TelemetryDaemon | None = None


def initialize_daemon(
    send_fn: Callable[[dict], int] | None = None,
    enabled: bool = True,
) -> TelemetryDaemon:
    """Initialize the global telemetry daemon."""
    global _DAEMON
    _DAEMON = TelemetryDaemon(send_fn=send_fn, enabled=enabled)
    return _DAEMON


def get_daemon() -> TelemetryDaemon | None:
    """Get the global telemetry daemon."""
    return _DAEMON


def send_telemetry_now(payload: dict) -> int:
    """Synchronously send telemetry (for manual calls or tests)."""
    daemon = get_daemon()
    if daemon and daemon.send_fn:
        return daemon.send_fn(payload)
    logger.warning("Telemetry daemon not initialized or send_fn not set")
    return 0
