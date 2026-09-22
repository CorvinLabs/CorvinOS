"""orchestration_router.py — bridge-agnostic routing for orchestration events.

Routes a single OrchestrationCompleteEvent to all enabled bridges (Discord,
WhatsApp, Telegram, Email) and Console WebSocket. Each bridge handler consumes
a standardized envelope format, independent of bridge-specific transport.

THE MECHANISM
-------------
1. orchestration_aggregator.py emits OrchestrationCompleteEvent
2. orchestration_router.route_event() routes to all enabled channels
3. Each bridge (discord/daemon.js, whatsapp/daemon.js, etc.) reads standardized
   envelope from its outbox/
4. Bridge-specific handler formats for native protocol (embeds, media attachments, etc.)
5. Console receives event via WebSocket for real-time display

This decouples orchestration logic from bridge-specific details, allowing
a single event to reach all transports without duplication.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

logger = logging.getLogger(__name__)

# Bridge channels supported by orchestration routing
SUPPORTED_CHANNELS = {"discord", "whatsapp", "telegram", "email", "console"}

# Envelope template — transport-agnostic format for all bridges
ENVELOPE_TEMPLATE = {
    "channel": None,  # set per bridge
    "message_type": "orchestration_complete",
    "event_type": None,  # SUCCESS | MIXED
    "batch_id": None,
    "task_count": 0,
    "success_count": 0,
    "failed_tasks": [],
    "text": None,  # Plain-text summary
    "voice_attachment_path": None,  # OGG file path (all bridges support audio)
    "timestamp": None,
    "metadata": {},  # Bridge-specific overrides
}


@dataclass(frozen=True)
class RoutingConfig:
    """Per-channel routing configuration."""

    channel: str
    enabled: bool
    send_voice: bool = True  # Most bridges support voice
    format: str = "standard"  # standard | html | markdown


def _get_routing_config(channel: str) -> RoutingConfig:
    """Load channel-specific config (from tenant.corvin.yaml or env)."""
    # Simplified: hardcode sensible defaults; later load from tenant config
    defaults = {
        "discord": RoutingConfig(channel="discord", enabled=True, send_voice=True),
        "whatsapp": RoutingConfig(channel="whatsapp", enabled=True, send_voice=True),
        "telegram": RoutingConfig(channel="telegram", enabled=True, send_voice=True),
        "email": RoutingConfig(channel="email", enabled=False, send_voice=True),  # Opt-in
        "console": RoutingConfig(channel="console", enabled=True, send_voice=True),
    }
    return defaults.get(channel, RoutingConfig(channel=channel, enabled=False))


def _outbox_dir() -> Path:
    """Resolve shared outbox directory (mirroring completion_notify pattern)."""
    corvin_home = os.environ.get("CORVIN_HOME")
    if corvin_home:
        base = Path(os.path.expanduser(os.path.expandvars(corvin_home)))
    else:
        base = Path.home() / ".corvin"
    return base / "bridges" / "shared" / "outbox"


def _write_envelope(channel: str, envelope: dict) -> bool:
    """Write standardized envelope to shared outbox for bridge daemon to consume.

    Args:
        channel: "discord" | "whatsapp" | "telegram" | "email" | "console"
        envelope: envelope dict with standardized schema

    Returns:
        True if write succeeded, False on error
    """
    outbox = _outbox_dir()
    outbox.mkdir(parents=True, exist_ok=True)

    # Envelope filename: orchestration_CHANNEL_TIMESTAMP.json
    batch_id = envelope.get("batch_id", "unknown")
    filename = outbox / f"orchestration_{channel}_{int(time.time())}_{batch_id[:8]}.json"

    try:
        # Write atomically (temp + rename)
        temp_path = filename.with_suffix(".json.tmp")
        with open(str(temp_path), "w") as f:
            json.dump(envelope, f, indent=2, default=str)
        temp_path.replace(filename)

        logger.info(f"orchestration_router: routed to {channel}: {filename.name}")
        return True

    except Exception as e:
        logger.error(f"orchestration_router: failed to route to {channel}: {e}")
        return False


def route_event(
    event_type: str,
    batch_id: str,
    task_count: int,
    success_count: int,
    failed_tasks: list,
    text: str,
    voice_attachment_path: Optional[str] = None,
    channels: Optional[list[str]] = None,
    metadata: Optional[dict] = None,
) -> dict[str, bool]:
    """Route a single orchestration event to all enabled bridges.

    Args:
        event_type: "ORCHESTRATION_COMPLETE_SUCCESS" | "ORCHESTRATION_COMPLETE_MIXED"
        batch_id: batch identifier
        task_count: total tasks in batch
        success_count: successful tasks
        failed_tasks: list of {task_id, error} dicts
        text: plain-text summary for all bridges
        voice_attachment_path: path to OGG audio file
        channels: list of channels to route to (default: all enabled)
        metadata: bridge-specific overrides {channel: {...}}

    Returns:
        dict[channel_name, success: bool] for each routed channel
    """
    if channels is None:
        # Route to all enabled channels
        channels = [ch for ch in SUPPORTED_CHANNELS if _get_routing_config(ch).enabled]

    metadata = metadata or {}
    results = {}

    logger.info(
        f"orchestration_router: routing event {batch_id} to {len(channels)} channel(s)"
    )

    for channel in channels:
        config = _get_routing_config(channel)

        if not config.enabled:
            logger.debug(f"orchestration_router: {channel} disabled, skipping")
            results[channel] = False
            continue

        # Build envelope for this channel
        envelope = ENVELOPE_TEMPLATE.copy()
        envelope.update(
            {
                "channel": channel,
                "event_type": event_type,
                "batch_id": batch_id,
                "task_count": task_count,
                "success_count": success_count,
                "failed_tasks": failed_tasks,
                "text": text,
                "voice_attachment_path": voice_attachment_path if config.send_voice else None,
                "timestamp": time.time(),
                "metadata": metadata.get(channel, {}),
            }
        )

        # Route to bridge-specific handler
        if channel == "console":
            # Console uses WebSocket (not outbox file)
            results[channel] = _emit_console_event(envelope)
        else:
            # All other bridges use shared outbox
            results[channel] = _write_envelope(channel, envelope)

    # Summary log
    succeeded = sum(1 for v in results.values() if v)
    logger.info(
        f"orchestration_router: delivered to {succeeded}/{len(results)} channel(s)"
    )

    return results


def _emit_console_event(envelope: dict) -> bool:
    """Emit orchestration event to Console via in-memory queue (WebSocket handler).

    Console subscribes to this queue and broadcasts to connected WebSocket clients.
    This is not a file-based outbox like other bridges — it's a real-time event.
    """
    try:
        # Import here to avoid circular imports
        try:
            from console.routes.orchestration_events import (
                console_orchestration_queue,
            )

            console_orchestration_queue.put_nowait(envelope)
            logger.info("orchestration_router: emitted to console WebSocket")
            return True

        except ImportError:
            logger.warning(
                "orchestration_router: console module not available, skipping console emit"
            )
            return False

    except Exception as e:
        logger.error(f"orchestration_router: failed to emit to console: {e}")
        return False


# --- Integration with orchestration_aggregator ---


def on_orchestration_complete(
    batch_id: str,
    task_count: int,
    success_count: int,
    failed_tasks: list,
    total_duration_secs: float,
    voice_attachment_path: Optional[str] = None,
) -> dict[str, bool]:
    """Called by orchestration_aggregator when a batch completes.

    Generates text summary and routes to all bridges.
    """
    # Determine event type
    event_type = (
        "ORCHESTRATION_COMPLETE_SUCCESS"
        if len(failed_tasks) == 0
        else "ORCHESTRATION_COMPLETE_MIXED"
    )

    # Generate plain-text summary (same for all bridges)
    if event_type == "ORCHESTRATION_COMPLETE_SUCCESS":
        text = f"✅ {task_count} background tasks completed successfully in {_format_duration(total_duration_secs)}"
    else:
        text = f"⚠️  {success_count} of {task_count} background tasks completed ({len(failed_tasks)} failed)"

    # Route to all bridges
    return route_event(
        event_type=event_type,
        batch_id=batch_id,
        task_count=task_count,
        success_count=success_count,
        failed_tasks=failed_tasks,
        text=text,
        voice_attachment_path=voice_attachment_path,
    )


def _format_duration(seconds: float) -> str:
    """Format duration in human-readable form."""
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
