"""NotificationRouter: Task events → Discord/Email (ADR-0362)."""

import asyncio
import httpx
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass


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

    def __init__(self, prefs_store: Optional[Dict] = None):
        self.prefs = prefs_store or {}
        self.http_client = httpx.AsyncClient(timeout=10.0)

    async def on_phase_completed(self, data: Dict):
        """Handle phase completion event → Discord."""
        task_id = data.get("task_id")
        phase_id = data.get("phase_id")

        message = f"✅ Phase `{phase_id}` completed (task: `{task_id}`)"
        await self._send_discord(message, color=0x00FF00)

    async def on_task_completed(self, data: Dict):
        """Handle task completion event → Discord."""
        task_id = data.get("task_id")
        phases_count = data.get("phases", 0)

        message = f"🎉 Task `{task_id}` COMPLETE ({phases_count} phases)"
        await self._send_discord(message, color=0x0080FF)

    async def on_phase_failed(self, data: Dict):
        """Handle phase failure event → Discord."""
        task_id = data.get("task_id")
        phase_id = data.get("phase_id")
        error = data.get("error", "Unknown error")

        message = f"❌ Phase `{phase_id}` failed: {error}"
        await self._send_discord(message, color=0xFF0000)

    async def on_phase_heartbeat(self, data: Dict):
        """Send periodic heartbeat for long-running phase."""
        phase_id = data.get("phase_id")
        elapsed_s = data.get("elapsed_s", 0)
        remaining_s = data.get("remaining_s", 0)
        status = data.get("status", "running")

        if status == "warning_timeout_approaching":
            message = f"⚠️ Phase `{phase_id}` timeout in {remaining_s}s"
            await self._send_discord(message, color=0xFFFF00)
        else:
            # Only send every 5 min (skip some heartbeats to avoid spam)
            if elapsed_s % 300 == 0:
                message = f"💫 Phase `{phase_id}` running... ({elapsed_s}s elapsed, {remaining_s}s remaining)"
                await self._send_discord(message, color=0x808080)

    async def on_phase_stalled(self, data: Dict):
        """Notify when phase is running too long (stall detection)."""
        phase_id = data.get("phase_id")
        elapsed_s = data.get("elapsed_s", 0)
        threshold_s = data.get("threshold_s", 900)
        reason = data.get("reason", "Unknown")

        message = f"⏱️ Phase `{phase_id}` stalled: {reason}"
        await self._send_discord(message, color=0xFF8800)

    async def _send_discord(self, message: str, color: int = 0x808080):
        """Send message to Discord webhook (fire-and-forget)."""
        webhook_url = self.prefs.get("default", {}).get("discord_webhook")
        if not webhook_url:
            return

        payload = {
            "embeds": [
                {
                    "title": "Vibe Engineering Task Update",
                    "description": message,
                    "color": color,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
            ]
        }

        try:
            async with self.http_client as client:
                await client.post(webhook_url, json=payload)
        except Exception as e:
            print(f"Discord send error: {e}")

    async def set_preferences(self, user_id: str, prefs: NotificationPreferences):
        """Set notification preferences for user."""
        self.prefs[user_id] = prefs

    def get_preferences(self, user_id: str) -> NotificationPreferences:
        """Get user notification preferences."""
        return self.prefs.get(user_id, NotificationPreferences(user_id=user_id))
