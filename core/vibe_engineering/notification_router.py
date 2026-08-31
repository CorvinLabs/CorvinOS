"""NotificationRouter: task/phase events → the messenger the task came from.

WHAT WAS WRONG (fixed 2026-08-28)
---------------------------------
Three defects, each on its own sufficient to make orchestration notifications
never arrive:

1. ``async with self.http_client`` CLOSED the shared client on the first send.
   Every later send raised "client has been closed", was swallowed by a blanket
   ``except Exception``, and printed to stdout. At most ONE notification per
   process could ever be delivered — which is exactly the reported symptom
   ("the first message arrives, the intermediate ones never do").
2. The only transport was ``DISCORD_WEBHOOK_URL``, which nothing in this repo
   sets, no config template mentions and the Console cannot write. With it
   unset ``_send_discord`` returned silently, so the failure was invisible.
3. ``on_phase_heartbeat`` gated on ``elapsed_s % 300 == 0`` — an exact-multiple
   test on a value that is only ever sampled every ~1 s and is a float. It
   essentially never fired.

THE TRANSPORT NOW
-----------------
The primary transport is the durable outbox backbone the messenger daemons
actually poll (``operator/bridges/shared/task_progress``) — the same one the
completion path uses, routed from the task's own origin record. That is the
only path in this repo with a live consumer.

A webhook remains available as a SECONDARY transport for a deployment that
wants one (``DISCORD_WEBHOOK_URL``, or explicit preferences), and is now
resolved per send rather than frozen at import, so setting the variable after
start-up works. Both transports are best-effort and fully independent: a
webhook outage cannot stop the outbox delivery, or vice versa.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

try:  # httpx is only needed for the OPTIONAL webhook transport.
    import httpx
except ImportError:  # pragma: no cover - environment without httpx
    httpx = None  # type: ignore[assignment]

# Minimum seconds between two heartbeat notifications for one phase. Replaces
# the `elapsed_s % 300 == 0` test, which required an exact multiple of a
# value sampled every ~1 s and therefore almost never fired.
HEARTBEAT_MIN_INTERVAL = float(os.environ.get("VIBE_HEARTBEAT_INTERVAL", "300"))


def _load_task_progress():
    """Import the durable outbox producer, or None when unavailable.

    Located relative to the repo root so this module works both from a source
    checkout and from an installed layout; None simply disables the transport.
    """
    try:
        root = Path(__file__).resolve().parents[2]
        shared = root / "operator" / "bridges" / "shared"
        if shared.is_dir() and str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        import task_progress  # type: ignore

        return task_progress
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[notification_router] Failed to load task_progress module: {e}. "
            f"Task progress notifications will not be delivered to outbox."
        )
        return None


@dataclass
class NotificationPreferences:
    user_id: str
    enabled: bool = True
    discord_webhook: Optional[str] = None
    discord_channel_id: Optional[str] = None
    dnd_start_utc: str = "22:00"
    dnd_end_utc: str = "09:00"


class NotificationRouter:
    """Routes orchestration events to notification transports."""

    def __init__(self, prefs_store: Optional[Dict] = None,
                 http_client: Optional["httpx.AsyncClient"] = None):
        self.prefs = prefs_store or {}
        # Created lazily and reused — NEVER closed by a send. Closing the shared
        # client after one request was defect #1 above.
        self._http_client = http_client
        self._owns_client = http_client is None
        self._task_progress = _load_task_progress()
        # phase_id → monotonic timestamp of the last heartbeat notification.
        self._last_heartbeat: Dict[str, float] = {}

    # ── transports ────────────────────────────────────────────────────────

    def _webhook_url(self) -> Optional[str]:
        """Resolve the webhook per send, not once at import.

        Freezing it at construction time meant a webhook configured after
        start-up was never picked up.
        """
        pref = self.prefs.get("default")
        if isinstance(pref, dict) and pref.get("discord_webhook"):
            return pref["discord_webhook"]
        if isinstance(pref, NotificationPreferences) and pref.discord_webhook:
            return pref.discord_webhook
        return os.environ.get("DISCORD_WEBHOOK_URL") or None

    def _client(self) -> Optional["httpx.AsyncClient"]:
        if httpx is None:
            return None
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def aclose(self) -> None:
        """Release the shared HTTP client. The ONLY place it is closed."""
        if self._owns_client and self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._http_client = None

    async def _send(self, task_id: Optional[str], message: str,
                    *, kind: str = "progress", color: int = 0x808080,
                    force: bool = False) -> None:
        """Deliver one notification over every configured transport.

        Both transports are attempted independently: an outage of one must not
        suppress the other.
        """
        await self._send_outbox(task_id, message, kind=kind, force=force)
        await self._send_discord(message, color=color)

    async def _send_outbox(self, task_id: Optional[str], message: str,
                           *, kind: str, force: bool) -> None:
        """Queue the update on the durable backbone the daemons poll."""
        tp = self._task_progress
        if tp is None or not task_id:
            return
        try:
            # emit() is a small synchronous file write; keep the event loop
            # free rather than blocking it on disk.
            await asyncio.to_thread(tp.emit, str(task_id), message,
                                    kind=kind, force=force)
        except Exception as e:  # noqa: BLE001 — a status line never breaks a task
            logger.warning("task_progress emit failed for %s: %s", task_id, e)

    async def _send_discord(self, message: str, color: int = 0x808080,
                            max_retries: int = 3) -> None:
        """Optional webhook transport. No-op when no webhook is configured."""
        webhook_url = self._webhook_url()
        if not webhook_url:
            return
        client = self._client()
        if client is None:
            logger.debug("httpx unavailable; webhook transport disabled")
            return

        payload = {
            "embeds": [{
                "title": "Vibe Engineering Task Update",
                "description": message,
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }]
        }

        for attempt in range(max_retries):
            try:
                # NOT `async with client` — that closes the shared client.
                response = await client.post(webhook_url, json=payload)
                if response.status_code in (200, 204):
                    return
                if response.status_code == 429:
                    # Discord's own rate limit. Honour Retry-After; retrying
                    # blindly is what gets a bot limited at the edge.
                    retry_after = 1.0
                    try:
                        retry_after = float(
                            response.headers.get("Retry-After")
                            or response.json().get("retry_after", 1.0)
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    if attempt < max_retries - 1:
                        await asyncio.sleep(min(retry_after, 30.0))
                        continue
                    return
                if 500 <= response.status_code < 600 and attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.warning("Discord webhook error %s: %s",
                               response.status_code, response.text[:200])
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("Discord webhook send failed (attempt %d/%d): %s",
                               attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        return

    # ── event handlers ────────────────────────────────────────────────────

    async def on_phase_completed(self, data: Dict):
        """Handle phase completion event."""
        task_id = data.get("task_id")
        phase_id = data.get("phase_id")
        self._last_heartbeat.pop(f"{task_id}:{phase_id}", None)
        await self._send(task_id, f"Phase `{phase_id}` completed",
                         kind="phase", color=0x00FF00, force=True)

    async def on_task_completed(self, data: Dict):
        """Handle task completion event."""
        task_id = data.get("task_id")
        phases_count = data.get("phases", 0)
        await self._send(task_id,
                         f"Task `{task_id}` COMPLETE ({phases_count} phases)",
                         kind="phase", color=0x0080FF, force=True)

    async def on_phase_failed(self, data: Dict):
        """Handle phase failure event."""
        task_id = data.get("task_id")
        phase_id = data.get("phase_id")
        error = data.get("error", "Unknown error")
        await self._send(task_id, f"Phase `{phase_id}` failed: {error}",
                         kind="error", color=0xFF0000, force=True)

    async def on_phase_retry(self, data: Dict):
        """Handle a retry — the visible half of autonomous healing."""
        task_id = data.get("task_id")
        phase_id = data.get("phase_id")
        n = data.get("retry_count", 0)
        of = data.get("max_retries", 0)
        await self._send(task_id,
                         f"Phase `{phase_id}` failed — retrying ({n}/{of})",
                         kind="resume", color=0xFFAA00, force=True)

    async def on_phase_heartbeat(self, data: Dict):
        """Periodic update for a long-running phase.

        Throttled by ELAPSED TIME since the last heartbeat for this phase, not
        by an exact-multiple test on elapsed_s (which never fired).
        """
        task_id = data.get("task_id")
        phase_id = data.get("phase_id")
        elapsed_s = data.get("elapsed_s", 0)
        remaining_s = data.get("remaining_s", 0)
        status = data.get("status", "running")

        if status == "warning_timeout_approaching":
            await self._send(task_id,
                             f"Phase `{phase_id}` timeout in {remaining_s}s",
                             kind="warning", color=0xFFFF00, force=True)
            return

        key = f"{task_id}:{phase_id}"
        now = time.monotonic()
        last = self._last_heartbeat.get(key, 0.0)
        if last and now - last < HEARTBEAT_MIN_INTERVAL:
            return
        self._last_heartbeat[key] = now
        await self._send(
            task_id,
            f"Phase `{phase_id}` running… ({int(elapsed_s)}s elapsed, "
            f"{int(remaining_s)}s remaining)",
            kind="progress", color=0x808080,
        )

    async def on_phase_stalled(self, data: Dict):
        """Notify when a phase has been running too long (stall detection)."""
        task_id = data.get("task_id")
        phase_id = data.get("phase_id")
        reason = data.get("reason", "Unknown")
        await self._send(task_id, f"Phase `{phase_id}` stalled: {reason}",
                         kind="stall", color=0xFF8800, force=True)

    # ── preferences ───────────────────────────────────────────────────────

    async def set_preferences(self, user_id: str, prefs: NotificationPreferences):
        """Set notification preferences for a user."""
        self.prefs[user_id] = prefs

    def get_preferences(self, user_id: str) -> NotificationPreferences:
        """Get a user's notification preferences."""
        return self.prefs.get(user_id, NotificationPreferences(user_id=user_id))
