"""LLM-based voice summary synthesis for multi-task orchestration events.

Transforms OrchestrationCompleteEvent into synthesized German voice notes
using deterministic templates (fast path) + LLM fallback (natural language).

Module: Stream 2, Voice Summary Generation
Author: Claude Haiku 4.5
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TaskResult:
    """Single task result within orchestration event."""

    task_name: str
    status: str  # "success", "failed", "timeout", "cancelled"
    duration_seconds: float
    error_message: Optional[str] = None


@dataclass
class OrchestrationCompleteEvent:
    """Multi-task orchestration completion event (tenant-scoped).

    Attributes:
        event_type: Type of orchestration event (required)
        tenant_id: Tenant identifier (required, fail-closed if missing)
        tasks: List of task results
        timestamp: Event timestamp (ISO-8601 UTC)
        voice_attachment_path: Optional path to synthesized voice file
    """

    event_type: str  # "orchestration.completed" | "orchestration.mixed_failure"
    tenant_id: str  # REQUIRED: Tenant scope (fail-closed if missing)
    tasks: list[TaskResult] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    voice_attachment_path: Optional[str] = None

    def __post_init__(self):
        """Validate tenant_id after initialization (fail-closed)."""
        if not self.tenant_id or not isinstance(self.tenant_id, str):
            raise ValueError("tenant_id must be a non-empty string")
        if not self.event_type or not isinstance(self.event_type, str):
            raise ValueError("event_type must be a non-empty string")


# Templates for deterministic summary generation (no LLM call)
_TEMPLATE_SUCCESS = (
    "Drei Hintergrund-Tasks erfolgreich abgeschlossen. "
    "{task_summaries} "
    "Alle Systeme bereit für nächste Operation."
)

_TEMPLATE_MIXED_FAILURE = (
    "Drei Hintergrund-Tasks mit Problemen abgeschlossen. "
    "{task_summaries} "
    "Bitte wiederholen Sie den fehlgeschlagenen Task."
)

# Per-task template
_TASK_SUCCESS_TEMPLATE = "{task_name} erfolgreich in {duration_formatted}."
_TASK_FAILED_TEMPLATE = "{task_name} fehlgeschlagen mit {error}."
_TASK_TIMEOUT_TEMPLATE = "{task_name} Timeout nach {duration_formatted}."

# Thresholds
_LLM_FALLBACK_TIMEOUT_S = 5
_VOICE_SYNTHESIS_TIMEOUT_S = 30
_MIN_TASK_COUNT_FOR_SUMMARY = 2


def _format_duration(seconds: float) -> str:
    """Format duration into natural German text.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "5 Minuten 23 Sekunden" or "1 Minute 7 Sekunden"
    """
    if seconds < 1:
        return f"{int(seconds * 1000)} Millisekunden"

    minutes = int(seconds // 60)
    secs = int(seconds % 60)

    if minutes == 0:
        return f"{secs} Sekunde{'n' if secs != 1 else ''}"

    if secs == 0:
        return f"{minutes} Minute{'n' if minutes != 1 else ''}"

    return f"{minutes} Minute{'n' if minutes != 1 else ''} {secs} Sekunde{'n' if secs != 1 else ''}"


def _generate_task_summary(task: TaskResult) -> str:
    """Generate single task summary from template.

    Args:
        task: Task result

    Returns:
        Formatted task summary string
    """
    duration_formatted = _format_duration(task.duration_seconds)

    if task.status == "success":
        return _TASK_SUCCESS_TEMPLATE.format(
            task_name=task.task_name,
            duration_formatted=duration_formatted,
        )
    elif task.status == "failed":
        error = task.error_message or "unbekannter Fehler"
        return _TASK_FAILED_TEMPLATE.format(
            task_name=task.task_name,
            error=error,
        )
    elif task.status == "timeout":
        return _TASK_TIMEOUT_TEMPLATE.format(
            task_name=task.task_name,
            duration_formatted=duration_formatted,
        )
    else:
        # fallback for unknown status
        return f"{task.task_name} {task.status}."


def _is_all_success(event: OrchestrationCompleteEvent) -> bool:
    """Check if all tasks succeeded.

    Args:
        event: Orchestration event

    Returns:
        True if all tasks have status == "success"
    """
    return all(task.status == "success" for task in event.tasks)


def _generate_deterministic_summary(event: OrchestrationCompleteEvent) -> Optional[str]:
    """Generate summary from deterministic templates (no LLM call).

    Fast path: if event matches known patterns (all success, all failure, mixed),
    return template-based summary. Returns None if pattern is too complex for
    templates (e.g., too many tasks, unusual error patterns).

    Args:
        event: Orchestration event

    Returns:
        Summary string if pattern matched, None otherwise
    """
    if not event.tasks or len(event.tasks) < _MIN_TASK_COUNT_FOR_SUMMARY:
        return None

    # Only handle 2-5 tasks (more = too complex for deterministic template)
    if len(event.tasks) > 5:
        return None

    # All tasks must be one of the known statuses
    all_tasks_valid = all(
        task.status in ("success", "failed", "timeout")
        for task in event.tasks
    )
    if not all_tasks_valid:
        return None

    # Generate per-task summaries
    task_summaries = "\n".join(_generate_task_summary(task) for task in event.tasks)

    # Pick template based on outcome
    if _is_all_success(event):
        template = _TEMPLATE_SUCCESS
    else:
        template = _TEMPLATE_MIXED_FAILURE

    return template.format(task_summaries=task_summaries)


async def _call_llm_fallback(event: OrchestrationCompleteEvent) -> Optional[str]:
    """Call LLM to generate natural language summary (fallback).

    Attempts to call Claude API with orchestration event. On timeout or error,
    returns None (caller sends text-only message).

    Args:
        event: Orchestration event

    Returns:
        Summary string from LLM, or None on timeout/error
    """
    try:
        # Import here to avoid hard dependency
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic()

        # Build prompt
        task_details = "\n".join(
            f"- {task.task_name}: {task.status} ({_format_duration(task.duration_seconds)})"
            + (f" — {task.error_message}" if task.error_message else "")
            for task in event.tasks
        )

        prompt = f"""Summarize this orchestration event as a brief, natural German voice note (2-3 sentences).

Tasks completed:
{task_details}

Event type: {event.event_type}

Voice note (German, casual tone, suitable for text-to-speech):"""

        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-opus-5",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=_LLM_FALLBACK_TIMEOUT_S,
        )

        if response.content and len(response.content) > 0:
            return response.content[0].text.strip()

        return None

    except asyncio.TimeoutError:
        logger.warning(
            f"LLM fallback timeout (>{_LLM_FALLBACK_TIMEOUT_S}s) for orchestration event"
        )
        return None
    except Exception as e:
        logger.warning(f"LLM fallback error: {type(e).__name__}: {e}")
        return None


def _get_outbox_path() -> Path:
    """Get shared outbox directory path.

    Returns:
        Path to .corvin/shared/outbox/
    """
    corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
    outbox = Path(corvin_home) / "shared" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    return outbox


def _synthesize_voice_file(
    summary_text: str,
    lang: str = "de",
    voice: str = "shimmer",
) -> Optional[str]:
    """Synthesize text to voice using say.py.

    Args:
        summary_text: Text to synthesize
        lang: BCP-47 language code (default: "de" for German)
        voice: Voice name for OpenAI TTS (default: "shimmer")

    Returns:
        Absolute path to OGG file, or None if synthesis failed
    """
    outbox = _get_outbox_path()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_file = outbox / f"orchestration_summary_{timestamp}.ogg"

    say_script = Path(__file__).parent.parent.parent.parent / "corvin_operator" / "voice" / "scripts" / "say.py"

    if not say_script.exists():
        logger.warning(f"say.py not found at {say_script}")
        return None

    try:
        # Call say.py subprocess
        result = subprocess.run(
            [
                "python3",
                str(say_script),
                str(out_file),
                summary_text,
                lang,
                voice,
            ],
            capture_output=True,
            text=True,
            timeout=_VOICE_SYNTHESIS_TIMEOUT_S,
        )

        if result.returncode == 0:
            # Success: exit 0 + path on stdout
            stdout = result.stdout.strip()
            if stdout and Path(stdout).exists():
                logger.debug(f"Voice synthesis succeeded: {stdout}")
                return stdout
            elif out_file.exists():
                # Fallback: check if file was created anyway
                logger.debug(f"Voice synthesis succeeded (file at {out_file})")
                return str(out_file)

        # Exit 0 + empty stdout = silently disabled
        if result.returncode == 0:
            logger.debug("Voice synthesis disabled (say.py skipped all providers)")
            return None

        # Exit 2 = usage error
        logger.warning(f"say.py usage error: {result.stderr}")
        return None

    except subprocess.TimeoutExpired:
        logger.warning(
            f"Voice synthesis timeout (>{_VOICE_SYNTHESIS_TIMEOUT_S}s)"
        )
        return None
    except Exception as e:
        logger.warning(f"Voice synthesis error: {type(e).__name__}: {e}")
        return None


async def synthesize_orchestration_summary(
    event: OrchestrationCompleteEvent,
) -> Optional[str]:
    """Synthesize orchestration event to voice summary file (LLM + TTS).

    Pipeline:
      1. Try deterministic template (fast, no LLM call)
      2. If no match: call LLM for natural language summary (5s timeout)
      3. Synthesize summary text to OGG-Opus voice file (30s timeout)
      4. Update event.voice_attachment_path with file path
      5. Return file path, or None if synthesis failed

    Fail-graceful: any error → return None, caller sends text-only message.

    Args:
        event: Orchestration completion event

    Returns:
        Absolute path to OGG-Opus file, or None if synthesis failed
    """
    if not event or not event.tasks:
        logger.warning("Empty orchestration event, skipping voice synthesis")
        return None

    # Step 1: Deterministic template
    summary_text = _generate_deterministic_summary(event)

    # Step 2: LLM fallback
    if summary_text is None:
        logger.debug("Deterministic template no match, calling LLM fallback")
        summary_text = await _call_llm_fallback(event)

    # If LLM also failed, return None (caller sends text-only)
    if summary_text is None:
        logger.warning("All summary generation methods failed")
        return None

    logger.debug(f"Generated summary: {summary_text[:100]}...")

    # Step 3: Voice synthesis
    voice_file = _synthesize_voice_file(summary_text, lang="de", voice="shimmer")

    if voice_file:
        # Update event with voice attachment path
        event.voice_attachment_path = voice_file
        logger.debug(f"Voice summary written to {voice_file}")

    return voice_file
