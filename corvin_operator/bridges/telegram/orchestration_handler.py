"""orchestration_handler.py — Telegram handler for orchestration completion events.

Processes standardized orchestration_complete envelopes from orchestration_router.py
and formats them as Telegram messages (HTML text + optional OGG voice note).

Input: Envelope from orchestration_router.py (already in shared outbox/telegram_*.json)
Output: Formatted Telegram message via bot.send_message() + bot.send_voice()

Error handling is fail-graceful: voice note failure does not block text delivery.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


def format_orchestration_summary(payload: dict) -> str:
    """Format orchestration event into HTML-formatted Telegram message.

    Args:
        payload: Standardized envelope from orchestration_router.py

    Returns:
        HTML-formatted text string for Telegram
    """
    event_type = payload.get("event_type", "UNKNOWN")
    task_count = payload.get("task_count", 0)
    success_count = payload.get("success_count", 0)
    failed_tasks = payload.get("failed_tasks", [])

    lines = []

    # Header with emoji indicator
    if event_type == "ORCHESTRATION_COMPLETE_SUCCESS":
        lines.append("<b>✅ All Orchestration Tasks Complete</b>")
    elif event_type == "ORCHESTRATION_COMPLETE_MIXED":
        lines.append(
            f"<b>⚠️  Orchestration Complete: {success_count}/{task_count} Succeeded</b>"
        )
    else:
        lines.append(f"<b>🔔 Orchestration Event: {event_type}</b>")

    lines.append("")  # Blank line

    # Summary stats
    lines.append(f"<i>Tasks:</i> {success_count}/{task_count} completed")

    # Duration if available
    timestamp = payload.get("timestamp")
    if timestamp:
        # Calculate rough duration from timestamp
        import time
        elapsed = time.time() - timestamp
        duration_str = _format_duration(elapsed)
        lines.append(f"<i>Duration:</i> {duration_str}")

    # Failed tasks list (if any)
    if failed_tasks:
        lines.append("")
        lines.append("<b>Failed Tasks:</b>")
        for task in failed_tasks[:5]:  # Show first 5
            task_id = task.get("task_id", "unknown")
            error = task.get("error", "unknown error")
            # Escape HTML entities
            error_safe = error.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"  • <code>{task_id}</code>: {error_safe}")
        if len(failed_tasks) > 5:
            lines.append(f"  ... and {len(failed_tasks) - 5} more")

    lines.append("")  # Blank line
    lines.append("<i>All systems ready for next operation</i>")

    return "\n".join(lines)


async def handle_orchestration_complete(
    payload: dict,
    chat_id: int | str,
    bot: Any,
) -> bool:
    """Handle orchestration_complete event and send to Telegram chat.

    This is the main entry point called by the outbox poller when a message_type
    of 'orchestration_complete' is detected.

    Args:
        payload: Standardized envelope dict from orchestration_router.py
        chat_id: Telegram numeric chat_id (int or str)
        bot: telegram.Bot instance

    Returns:
        True if at least text message was sent, False if both text and voice failed
    """
    text_sent = False
    voice_sent = False

    # 1. Format and send HTML text summary
    try:
        html_text = format_orchestration_summary(payload)
        await bot.send_message(
            int(chat_id),
            html_text,
            parse_mode="HTML",
        )
        logger.info(f"orchestration: sent text summary to chat {chat_id}")
        text_sent = True

    except Exception as e:
        logger.error(
            f"orchestration: failed to send text message to {chat_id}: {e}"
        )

    # 2. Send voice note if attachment_path is provided
    voice_path = payload.get("voice_attachment_path")
    if voice_path and os.path.exists(voice_path):
        try:
            with open(voice_path, "rb") as audio_file:
                await bot.send_voice(
                    int(chat_id),
                    audio_file,
                    caption="Background tasks completion summary",
                )
                logger.info(
                    f"orchestration: sent voice note ({voice_path}) to chat {chat_id}"
                )
                voice_sent = True

        except FileNotFoundError:
            logger.warning(
                f"orchestration: voice attachment not found: {voice_path}"
            )

        except Exception as e:
            logger.warning(
                f"orchestration: voice note delivery failed (text was sent): {e}"
            )
            # Non-blocking: text was already sent, so don't fail

    if not text_sent and not voice_sent:
        logger.error(
            f"orchestration: failed to send both text and voice to {chat_id}"
        )
        return False

    return True


def _format_duration(seconds: float) -> str:
    """Format duration in human-readable form.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted duration string (e.g., "5m 23s")
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m {s}s" if s > 0 else f"{m}m"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m" if m > 0 else f"{h}h"


# --- Synchronous wrapper for daemon.js integration (if needed) ---


def handle_orchestration_complete_sync(
    payload: dict,
    chat_id: int | str,
    bot: Any,
) -> bool:
    """Synchronous wrapper for handle_orchestration_complete.

    Use this if the calling context is synchronous (not async).
    Requires an event loop to be running in the background.

    Args:
        payload: Standardized envelope dict
        chat_id: Telegram chat_id
        bot: telegram.Bot instance

    Returns:
        True if message was sent, False otherwise
    """
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Event loop is running in another thread; schedule as task
            task = asyncio.run_coroutine_threadsafe(
                handle_orchestration_complete(payload, chat_id, bot),
                loop,
            )
            return task.result(timeout=30)
        else:
            # Run synchronously
            return asyncio.run(
                handle_orchestration_complete(payload, chat_id, bot)
            )

    except Exception as e:
        logger.error(f"orchestration: sync wrapper failed: {e}")
        return False
