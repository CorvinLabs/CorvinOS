"""integration.py — Integration of orchestration_handler into adapter.py flow.

This module provides a hook that can be called from adapter.py's
orchestration_complete flow to format and queue orchestration events
specifically for Telegram delivery.

Usage in adapter.py:
    from corvin_operator.bridges.telegram.integration import (
        format_orchestration_for_telegram,
    )

    # When orchestration completes:
    results = orchestration_router.on_orchestration_complete(
        batch_id=batch_id,
        task_count=task_count,
        success_count=success_count,
        failed_tasks=failed_tasks,
        total_duration_secs=duration,
        voice_attachment_path=voice_path,
    )

    # Additionally, format for Telegram (if desired):
    telegram_results = format_orchestration_for_telegram(
        batch_id=batch_id,
        task_count=task_count,
        success_count=success_count,
        failed_tasks=failed_tasks,
        total_duration_secs=duration,
        voice_attachment_path=voice_path,
        target_chats=[chat_id1, chat_id2],  # Optional: specific chats
    )
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


def _get_telegram_outbox_dir() -> Path:
    """Get shared Telegram outbox directory."""
    corvin_home = os.environ.get("CORVIN_HOME")
    if corvin_home:
        base = Path(os.path.expanduser(os.path.expandvars(corvin_home)))
    else:
        base = Path.home() / ".corvin"
    return base / "bridges" / "telegram" / "outbox"


def format_orchestration_for_telegram(
    batch_id: str,
    task_count: int,
    success_count: int,
    failed_tasks: list,
    total_duration_secs: float,
    voice_attachment_path: Optional[str] = None,
    target_chats: Optional[list[int | str]] = None,
) -> dict[str, bool]:
    """Format orchestration event and write to Telegram outbox.

    This is an ALTERNATIVE to orchestration_router.route_event() that
    specifically targets Telegram and uses the orchestration_handler
    formatting. Use this when you want Telegram-specific customization.

    Args:
        batch_id: Orchestration batch identifier
        task_count: Total tasks in batch
        success_count: Successfully completed tasks
        failed_tasks: List of {task_id, error} dicts
        total_duration_secs: Total duration in seconds
        voice_attachment_path: Optional path to OGG voice file
        target_chats: Optional list of specific Telegram chat IDs to target.
                      If None, the daemon.js will determine chats based on
                      settings.json whitelist.

    Returns:
        dict: {chat_id: success_bool} for each target chat, or single dict
              if target_chats was empty/None
    """
    # Determine event type
    event_type = (
        "ORCHESTRATION_COMPLETE_SUCCESS"
        if len(failed_tasks) == 0
        else "ORCHESTRATION_COMPLETE_MIXED"
    )

    # Build standardized envelope (compatible with daemon.js)
    envelope = {
        "channel": "telegram",
        "message_type": "orchestration_complete",
        "event_type": event_type,
        "batch_id": batch_id,
        "task_count": task_count,
        "success_count": success_count,
        "failed_tasks": failed_tasks,
        "text": None,  # daemon.js or handler will format
        "voice_attachment_path": voice_attachment_path,
        "timestamp": time.time(),
        "metadata": {},
    }

    # Write to outbox
    outbox = _get_telegram_outbox_dir()
    outbox.mkdir(parents=True, exist_ok=True)

    results = {}

    if not target_chats:
        # Single envelope — daemon.js will handle routing based on settings
        filename = outbox / f"orchestration_{int(time.time())}_{batch_id[:8]}.json"
        try:
            temp_path = filename.with_suffix(".json.tmp")
            with open(str(temp_path), "w") as f:
                json.dump(envelope, f, indent=2, default=str)
            temp_path.replace(filename)

            logger.info(f"telegram/integration: queued orchestration event {batch_id}")
            return {"default": True}

        except Exception as e:
            logger.error(
                f"telegram/integration: failed to write orchestration event: {e}"
            )
            return {"default": False}

    else:
        # Per-chat envelopes (advanced use case)
        for chat_id in target_chats:
            envelope_chat = envelope.copy()
            envelope_chat["target_chat_id"] = int(chat_id)

            filename = (
                outbox
                / f"orchestration_chat_{chat_id}_{int(time.time())}_{batch_id[:8]}.json"
            )
            try:
                temp_path = filename.with_suffix(".json.tmp")
                with open(str(temp_path), "w") as f:
                    json.dump(envelope_chat, f, indent=2, default=str)
                temp_path.replace(filename)

                logger.info(
                    f"telegram/integration: queued orchestration for chat {chat_id}"
                )
                results[int(chat_id)] = True

            except Exception as e:
                logger.error(
                    f"telegram/integration: failed to write for chat {chat_id}: {e}"
                )
                results[int(chat_id)] = False

        return results


# Alternative: Direct use of orchestration_handler (for testing/advanced cases)


def get_handler_module():
    """Lazy-import orchestration_handler to avoid circular deps.

    Returns:
        orchestration_handler module or None if import fails
    """
    try:
        from . import orchestration_handler
        return orchestration_handler
    except ImportError as e:
        logger.warning(f"telegram/integration: failed to import orchestration_handler: {e}")
        return None
