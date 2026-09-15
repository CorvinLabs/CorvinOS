"""Notification Router — delivers CompletionEvents to Discord/Console (ADR-0655).

Listen to audit chain for completion_event_* records, generate summaries,
synthesize voice, and route to Discord/Console via completion_notify.deliver_ready().
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from core.learning.completion_detectors.completion_event import (
    CompletionEvent,
    CompletionStatus,
    CompletionTaskType,
)
from core.notification.summary_generator import StructuredSummary, SummaryGenerator
from core.notification.pii_scrubber import PIIScrubber

_log = logging.getLogger("core.notification.notification_router")


@dataclass(frozen=True)
class RoutedNotification:
    """Immutable notification ready for delivery."""
    task_id: str
    channel: str  # "discord", "console", "email"
    chat_id: Optional[str]
    sender_id: Optional[str]
    text: str
    voice_path: Optional[str]
    summary: StructuredSummary
    timestamp: str


class NotificationRouter:
    """Routes CompletionEvents from audit chain to Discord/Console (ADR-0655).

    Architecture:
    1. Poll audit chain for completion_event_* records (every 5s)
    2. For each new event: check if already delivered (dedup via event_id)
    3. Generate StructuredSummary (LLM-powered)
    4. Synthesize voice (outcome-first, optional)
    5. Route to messenger (Discord, Console, Email)
    6. Mark as delivered in audit chain
    """

    def __init__(
        self,
        audit_chain_path: str | Path = "~/.corvin/global/forge/audit.jsonl",
        corvin_home: str | Path = "~/.corvin",
        poll_interval_seconds: float = 5.0,
    ):
        self.audit_chain_path = Path(audit_chain_path).expanduser()
        self.corvin_home = Path(corvin_home).expanduser()
        self.poll_interval_seconds = poll_interval_seconds

        self.summary_generator = SummaryGenerator()
        self.pii_scrubber = PIIScrubber()

        # Track delivered events (in-memory; persisted via audit chain)
        self._delivered_event_ids: set[str] = set()
        self._last_chain_pos = 0

    async def run(self):
        """Main loop: poll audit chain, route completions."""
        _log.info(f"NotificationRouter started (poll interval: {self.poll_interval_seconds}s)")

        while True:
            try:
                await self._poll_and_route_once()
            except Exception as e:
                _log.error(f"Error in poll cycle: {e}", exc_info=True)

            await asyncio.sleep(self.poll_interval_seconds)

    async def _poll_and_route_once(self):
        """Poll audit chain for new completion events, route to messengers."""
        if not self.audit_chain_path.exists():
            return

        try:
            with open(self.audit_chain_path, "r") as f:
                # Seek to last known position
                lines = f.readlines()
                new_lines = lines[self._last_chain_pos:]
                self._last_chain_pos = len(lines)

            for line in new_lines:
                if not line.strip():
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Check if this is a completion event
                event_type = record.get("event_type", "")
                if not event_type.startswith("completion_event_"):
                    continue

                event_id = record.get("event_id")
                if event_id in self._delivered_event_ids:
                    continue  # Already routed

                # Parse and route
                await self._route_event(record)
                self._delivered_event_ids.add(event_id)

        except OSError as e:
            _log.warning(f"Cannot read audit chain: {e}")

    async def _route_event(self, record: Dict[str, Any]):
        """Route a single completion event to appropriate channels."""
        try:
            # Reconstruct CompletionEvent from audit record
            completion_data = record.get("details", {})

            task_id = record.get("task_id") or completion_data.get("task_id")
            task_type_str = completion_data.get("task_type", "workflow")
            status_str = completion_data.get("status", "completed")
            origin_channel = completion_data.get("origin", {}).get("channel", "discord")
            origin_chat_id = completion_data.get("origin", {}).get("chat_id")
            origin_sender = completion_data.get("origin", {}).get("sender_id")

            if not task_id:
                _log.warning("Completion event missing task_id, skipping")
                return

            # Generate summary (LLM-powered)
            summary = self.summary_generator.create_from_record(
                task_id=task_id,
                task_type=task_type_str,
                status=status_str,
                details=completion_data.get("details", {}),
                outcome=completion_data.get("outcome"),
            )

            # Scrub PII before voice synthesis
            scrubbed_text = self.pii_scrubber.scrub(summary.text)

            # Voice synthesis (outcome-first, ADR-0596/0597)
            voice_path = await self._synthesize_voice(summary, task_type_str)

            # Route to messenger
            notification = RoutedNotification(
                task_id=task_id,
                channel=origin_channel,
                chat_id=origin_chat_id,
                sender_id=origin_sender,
                text=scrubbed_text,
                voice_path=voice_path,
                summary=summary,
                timestamp=datetime.utcnow().isoformat() + "Z",
            )

            await self._deliver(notification)

        except Exception as e:
            _log.error(f"Error routing completion event: {e}", exc_info=True)

    async def _deliver(self, notification: RoutedNotification):
        """Deliver notification to appropriate channel (Discord, Console, etc.)."""
        if not notification.channel:
            return

        try:
            if notification.channel == "discord":
                await self._deliver_discord(notification)
            elif notification.channel == "console":
                await self._deliver_console(notification)
            else:
                _log.warning(f"Unknown channel: {notification.channel}")

        except Exception as e:
            _log.error(f"Failed to deliver to {notification.channel}: {e}", exc_info=True)

    async def _deliver_discord(self, notification: RoutedNotification):
        """Deliver to Discord via completion_notify.deliver_ready()."""
        try:
            from operator.bridges.shared.completion_notify import deliver_ready

            # Call deliver_ready with Discord routing
            deliver_ready(
                task_id=notification.task_id,
                text=notification.text,
                voice_path=notification.voice_path,
                channel="discord",
                chat_id=notification.chat_id,
                sender_id=notification.sender_id,
                tenant_id="_default",  # TODO: Extract from event
            )

            _log.info(f"Delivered to Discord: {notification.task_id}")

        except Exception as e:
            _log.error(f"Discord delivery failed: {e}", exc_info=True)

    async def _deliver_console(self, notification: RoutedNotification):
        """Deliver to Console (toast + optional playback)."""
        # TODO k=4: Wire to Console notification API
        _log.info(f"Console delivery: {notification.task_id} (TODO)")

    async def _synthesize_voice(
        self, summary: StructuredSummary, task_type: str
    ) -> Optional[str]:
        """Synthesize voice notification (outcome-first, ADR-0596/0597)."""
        try:
            # Use outcome-first structure: status first, then details
            voice_text = f"{summary.outcome_first}. {summary.text}"

            # Try to synthesize via operator voice module
            try:
                from operator.voice.scripts.tts_openai import synthesize
                voice_path = await synthesize(
                    text=voice_text,
                    output_dir=self.corvin_home / "notifications" / "voice",
                    format="opus",
                )
                _log.info(f"Voice synthesized: {voice_path}")
                return voice_path
            except ImportError:
                _log.debug("Voice synthesis unavailable (tts_openai not installed)")
                return None

        except Exception as e:
            _log.warning(f"Voice synthesis failed: {e}")
            return None  # Fail-safe: text delivery still works
