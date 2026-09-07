"""Phase 1 Notification Router — Discord outbox only.

Routes StructuredSummary + CompletionEvent to notification channels.
Phase 1: Discord only (where task was spawned).
Phase 2: Add Console, Email.

Idempotent: the envelope id is derived from the completion identity and the
outbox file is written with O_EXCL, so re-routing the same completion is a
no-op instead of a second delivery.
Audit: emits NotificationSentEvent for every route attempt.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import time

from core.learning.completion_detectors.completion_event import (
    CompletionEvent,
    NotificationSentEvent,
)
from .summary_generator import StructuredSummary

_log = logging.getLogger("core.notification.notification_router")


class NotificationRouter:
    """Route notifications to channels (Phase 1: Discord only)."""

    def __init__(self, audit_backend, corvin_home: str = "~/.corvin"):
        self.audit_backend = audit_backend
        self.corvin_home = Path(corvin_home).expanduser()

    async def route(
        self,
        event: CompletionEvent,
        summary: StructuredSummary,
        voice_url: str | None = None,
    ) -> bool:
        """Route notification to appropriate channel(s).

        Phase 1: Discord only. Determine channel from event.origin["channel"].
        """
        channel = event.origin.get("channel", "unknown")

        if channel == "discord":
            return await self._route_discord(event, summary, voice_url)
        elif channel == "workflow":
            # Workflows spawned from Discord Bridge
            return await self._route_discord(event, summary, voice_url)
        else:
            _log.warning(f"Phase 1: unsupported channel {channel}")
            return False

    async def _route_discord(
        self,
        event: CompletionEvent,
        summary: StructuredSummary,
        voice_url: str | None = None,
    ) -> bool:
        """Send notification to Discord outbox (idempotent via O_EXCL)."""
        # Extract Discord metadata
        chat_id = event.metadata.get("discord_chat_id")
        channel_id = event.metadata.get("discord_channel_id")

        if not chat_id:
            _log.error(f"Missing discord_chat_id for {event.task_id}")
            return False

        # Build envelope (reuse existing outbox schema).
        #
        # The envelope id is DERIVED from the completion identity, never random:
        # the O_EXCL write below is the exactly-once guarantee, and a uuid4() id
        # can never collide, so a random id silently turned the guarantee into a
        # no-op (routing the same completion twice wrote two outbox files and
        # delivered two Discord messages). Identity fields only — no free-text
        # user content ever feeds the hash.
        envelope_id = self._dedup_id(event, channel="discord", chat_id=chat_id)
        envelope = {
            "id": envelope_id,
            "channel": "discord",
            "from": "corvin-bot",
            "chat_id": str(chat_id),  # MUST be STRING (prevent float64 precision loss)
            "ts": int(time.time()),
            "text": self._build_message(summary),
            "voice_url": voice_url,
            "provenance": {
                "task_id": event.task_id,
                "task_type": event.task_type.value,
                "status": event.status.value,
                "summary_type": summary.summary_type.value,
            },
        }

        # Write to outbox directory (O_EXCL for exactly-once)
        try:
            outbox_dir = self.corvin_home / "bridges" / "discord" / "outbox"
            outbox_dir.mkdir(parents=True, exist_ok=True)

            outbox_file = outbox_dir / f"{envelope_id}.json"

            # O_EXCL ("x"): fail if the file exists. Combined with the derived
            # envelope_id above, this is the exactly-once delivery guarantee.
            with open(outbox_file, "x") as f:
                json.dump(envelope, f, indent=2)

            _log.info(f"Routed notification {envelope_id} to Discord outbox")

        except FileExistsError:
            _log.warning(f"Notification {envelope_id} already sent (duplicate detected)")
            return True  # Already sent, not an error

        except OSError as e:
            _log.error(f"Failed to write outbox: {e}")
            return False

        # Audit emit: NotificationSentEvent
        try:
            audit_event = NotificationSentEvent(
                completion_event_id=event.hash,
                channel="discord",
                envelope_id=envelope_id,
                status="sent_to_outbox",
            )
            await self.audit_backend.emit(audit_event)

        except Exception as e:
            _log.error(f"Failed to audit notification: {e}")
            # Non-blocking: notification already in outbox, audit failure is not critical

        return True

    @staticmethod
    def _dedup_id(event: CompletionEvent, channel: str, chat_id) -> str:
        """Deterministic envelope id = the exactly-once key for this completion.

        Same completion routed to the same channel/chat → same id → the O_EXCL
        open() below raises FileExistsError on the second attempt instead of
        writing a second outbox envelope.

        Content-free by construction: only identifiers and enum values are
        hashed, never `output_summary`, `key_result` or any other free text.
        """
        return CompletionEvent.compute_hash(
            {
                "task_id": event.task_id,
                "task_type": event.task_type.value,
                "status": event.status.value,
                "tenant_id": event.tenant_id,
                "channel": channel,
                "chat_id": str(chat_id),
            }
        )

    @staticmethod
    def _build_message(summary: StructuredSummary) -> str:
        """Build Discord message text from summary."""
        lines = [
            f"**{summary.title}**",
            f"Status: {summary.outcome}",
            f"Result: {summary.key_result}",
            f"Duration: {summary.duration}",
        ]

        if summary.voice_lines:
            lines.append("")
            lines.append("_Summary:_")
            for line in summary.voice_lines:
                lines.append(f"• {line}")

        return "\n".join(lines)
