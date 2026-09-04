"""Shared utility functions for learning module (L5 gates).

Provides reusable helpers for:
- Timestamp parsing and formatting
- Statistical calculations (mean, std)
- Time remaining formatting
"""

from datetime import datetime, timedelta
from typing import Tuple
import logging
import math

logger = logging.getLogger(__name__)


def parse_iso_timestamp(ts: str) -> datetime:
    """Parse ISO 8601 timestamp string to datetime.

    Handles 'Z' timezone suffix (UTC).

    Args:
        ts: ISO 8601 timestamp string (e.g., "2026-09-01T12:34:56.789Z")

    Returns:
        datetime object in UTC

    Raises:
        ValueError: If timestamp is malformed
    """
    if not ts or not isinstance(ts, str):
        raise ValueError(f"Invalid timestamp: {ts!r}")

    try:
        # Remove 'Z' suffix if present (indicates UTC)
        ts_clean = ts.replace("Z", "")
        return datetime.fromisoformat(ts_clean)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Failed to parse timestamp {ts!r}: {e}") from e


def format_iso_timestamp() -> str:
    """Format current time as ISO 8601 string with UTC suffix.

    Returns:
        ISO 8601 timestamp string with 'Z' suffix (e.g., "2026-09-01T12:34:56.789Z")
    """
    return datetime.utcnow().isoformat() + "Z"


def compute_mean_std(values: list) -> Tuple[float, float]:
    """Compute mean and standard deviation of values.

    Args:
        values: List of numeric values

    Returns:
        Tuple of (mean, std) — both 0.0 if values is empty or has 1 element
    """
    if len(values) == 0:
        return 0.0, 0.0

    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)

    std = math.sqrt(variance)

    return mean, std


def format_time_remaining(delta: timedelta) -> str:
    """Format a timedelta as HH:MM:SS string.

    Args:
        delta: Time remaining

    Returns:
        Formatted string (e.g., "01:30:45 remaining")
    """
    hours, remainder = divmod(delta.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d} remaining"


def parse_time_remaining_string(time_str: str) -> Tuple[bool, int]:
    """Parse time remaining string to total seconds.

    Args:
        time_str: Format "HH:MM:SS remaining", or special strings like "Approval not found", "inf"

    Returns:
        Tuple of (is_valid, total_seconds)
        - (False, 0) if parse fails or special string (cannot revoke)
        - (True, seconds) if successfully parsed
    """
    if not time_str or not isinstance(time_str, str):
        return False, 0

    # Handle special cases
    if "inf" in time_str.lower() or "not found" in time_str.lower():
        return False, 0  # Cannot revoke

    try:
        parts = time_str.split(":")[: 3]  # HH:MM:SS
        if len(parts) < 3:
            # Not in HH:MM:SS format
            return False, 0

        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2].split()[0])  # Extract digits before " remaining"

        return True, hours * 3600 + minutes * 60 + seconds
    except (ValueError, IndexError, AttributeError, TypeError) as e:
        logger.warning(f"[Learning Utils] Could not parse time_remaining: {time_str!r}: {e}")
        return False, 0
