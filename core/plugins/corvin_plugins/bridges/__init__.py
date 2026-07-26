"""Bundled bridge supervisors — the Node daemons as bundled-layer plugins.

The seven messenger bridges are Node.js daemons (``operator/bridges/<channel>/
daemon.js``), not Python modules. This package does not reimplement them; it
supervises them as subprocesses behind the shipped-dark
``bridge_supervisor_plugins`` feature flag. See :mod:`.supervisor` for the full
contract — start gate, duplicate-start probe, and the deliberate absence of an
automatic restart.

Importing this package must not require the Console or ``bridge_manager``: both
are resolved lazily at ``on_load()`` time so ``core/plugins`` stays importable in
a headless core (ADR-0234).
"""
from __future__ import annotations

from .registry_entries import declaration_entries, declaration_entry
from .supervisor import (
    BRIDGE_CHANNELS,
    BRIDGE_PLUGIN_CLASSES,
    FLAG_ID,
    BridgeSupervisorPlugin,
    DiscordBridgePlugin,
    EmailBridgePlugin,
    SignalBridgePlugin,
    SlackBridgePlugin,
    TeamsBridgePlugin,
    TelegramBridgePlugin,
    WhatsAppBridgePlugin,
)

__all__ = [
    "BRIDGE_CHANNELS",
    "BRIDGE_PLUGIN_CLASSES",
    "FLAG_ID",
    "BridgeSupervisorPlugin",
    "DiscordBridgePlugin",
    "EmailBridgePlugin",
    "SignalBridgePlugin",
    "SlackBridgePlugin",
    "TeamsBridgePlugin",
    "TelegramBridgePlugin",
    "WhatsAppBridgePlugin",
    "declaration_entries",
    "declaration_entry",
]
