"""Utility functions for the plugin system (ADR-0345)."""

from datetime import datetime, timezone


def now_utc() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()
