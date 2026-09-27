"""
Bridge Autonomy Detector — Determine if current execution context supports rescheduling.

Part of ADR-2083: Non-Interactive Bridge Autonomy Design.
Enables autonome loops + workflows to adapt to their runtime environment:
- Interactive (CLI/Web): use ScheduleWakeup for periodic reschedule
- Non-Interactive (Discord/Slack): run as background task (no reschedule)
"""

import os
import sys
from enum import Enum
from typing import Optional


class BridgeAutonomyMode(Enum):
    """Execution autonomy classification."""
    INTERACTIVE = "interactive"           # CLI, web UI, IDE extensions — has reschedule capability
    NON_INTERACTIVE = "non_interactive"   # Discord, Slack, Email bridges — no reschedule
    HEADLESS = "headless"                 # systemd, cron, async worker — run-to-completion


class BridgeType(Enum):
    """Concrete bridge / transport implementation."""
    CLI = "cli"
    WEB = "web"
    DISCORD = "discord"
    SLACK = "slack"
    EMAIL = "email"
    A2A = "a2a"                           # App-to-App (L38)
    UNKNOWN = "unknown"


def detect_bridge_type() -> BridgeType:
    """Detect which bridge is currently executing this agent."""
    # Check environment variables (set by bridge at startup)
    bridge_env = os.environ.get("CORVIN_BRIDGE_TYPE", "").lower()
    if bridge_env == "discord":
        return BridgeType.DISCORD
    if bridge_env == "slack":
        return BridgeType.SLACK
    if bridge_env == "email":
        return BridgeType.EMAIL
    if bridge_env == "a2a":
        return BridgeType.A2A
    if bridge_env == "web":
        return BridgeType.WEB

    # Fallback: check for TTY (interactive CLI)
    if sys.stdin.isatty() and sys.stdout.isatty():
        return BridgeType.CLI

    # Default: assume non-interactive (safe default)
    return BridgeType.UNKNOWN


def detect_autonomy_mode() -> BridgeAutonomyMode:
    """Determine if current context supports rescheduling."""
    bridge_type = detect_bridge_type()

    # Interactive → can reschedule (ScheduleWakeup available)
    if bridge_type in (BridgeType.CLI, BridgeType.WEB):
        return BridgeAutonomyMode.INTERACTIVE

    # Non-interactive → cannot reschedule (use background execution)
    if bridge_type in (BridgeType.DISCORD, BridgeType.SLACK, BridgeType.EMAIL, BridgeType.A2A):
        return BridgeAutonomyMode.NON_INTERACTIVE

    # Headless (systemd/cron) → can run to completion
    if bridge_type == BridgeType.UNKNOWN and not sys.stdin.isatty():
        return BridgeAutonomyMode.HEADLESS

    # Default: non-interactive (conservative)
    return BridgeAutonomyMode.NON_INTERACTIVE


def is_rescheduling_available() -> bool:
    """Can the current bridge reschedule tasks?"""
    mode = detect_autonomy_mode()
    return mode == BridgeAutonomyMode.INTERACTIVE


def is_non_interactive_bridge() -> bool:
    """Is this a non-interactive bridge (Discord, Slack, etc.)?"""
    mode = detect_autonomy_mode()
    return mode == BridgeAutonomyMode.NON_INTERACTIVE


def get_bridge_type() -> BridgeType:
    """Get the concrete bridge type (for auditing, logging)."""
    return detect_bridge_type()


def get_autonomy_mode_description() -> str:
    """Human-readable autonomy mode for logging."""
    mode = detect_autonomy_mode()
    bridge = detect_bridge_type()

    descriptions = {
        (BridgeAutonomyMode.INTERACTIVE, BridgeType.CLI): "Interactive CLI — ScheduleWakeup available",
        (BridgeAutonomyMode.INTERACTIVE, BridgeType.WEB): "Web UI — ScheduleWakeup available",
        (BridgeAutonomyMode.NON_INTERACTIVE, BridgeType.DISCORD): "Discord Bridge — background execution only",
        (BridgeAutonomyMode.NON_INTERACTIVE, BridgeType.SLACK): "Slack Bridge — background execution only",
        (BridgeAutonomyMode.NON_INTERACTIVE, BridgeType.EMAIL): "Email Bridge — background execution only",
        (BridgeAutonomyMode.NON_INTERACTIVE, BridgeType.A2A): "A2A Bridge — background execution only",
        (BridgeAutonomyMode.HEADLESS, BridgeType.UNKNOWN): "Headless (systemd/cron) — run to completion",
    }

    return descriptions.get((mode, bridge), f"{mode.value} (bridge: {bridge.value})")
