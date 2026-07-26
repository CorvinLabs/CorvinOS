"""NotificationBackend registry and default implementation (ADR-0033).

Usage (plugin on_load):
    ctx.notification_registry.set_active(self)

Usage (caller):
    from corvin_plugins.providers.notification_backend import get_active
    get_active().notify("adapter.rate_limited", {"severity": "warn"}, tenant_id=tid)
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corvin_plugins.protocol import NotificationBackend as _NBProto

_log = logging.getLogger("corvin.notify")


# ── Default implementation ────────────────────────────────────────────────────

class LogNotificationBackend:
    """Default: write to logger + audit chain.  No external system required."""

    def notify(
        self,
        event: str,
        payload: dict,
        *,
        tenant_id: str = "_default",
        severity: str = "info",
    ) -> None:
        level = {
            "info": logging.INFO,
            "warn": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }.get(severity, logging.INFO)
        _log.log(level, "notify event=%r tenant=%r payload=%r", event, tenant_id, payload)


# ── Registry ──────────────────────────────────────────────────────────────────

class NotificationBackendRegistry:
    """Holds the active NotificationBackend for this process.

    Only one provider is active at a time. Thread-safe.
    Must NOT cache get_active() results across calls — registry may be
    updated by a hot-reload.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: _NBProto = LogNotificationBackend()  # type: ignore[assignment]

    def set_active(self, provider: _NBProto) -> None:
        with self._lock:
            self._active = provider

    def get_active(self) -> _NBProto:
        with self._lock:
            return self._active

    def clear(self) -> None:
        """Restore the bundled default provider."""
        with self._lock:
            self._active = LogNotificationBackend()  # type: ignore[assignment]

    def clear_if_active(self, provider: object) -> bool:
        """Restore the default only if ``provider`` is the one installed.

        Instance-checked on purpose.  A plugin unloading must not evict a
        provider that a DIFFERENT plugin installed after it — clearing by type
        alone would hand the slot back to the default while the other plugin
        still believes it is active.
        """
        with self._lock:
            if self._active is not provider:
                return False
            self._active = LogNotificationBackend()  # type: ignore[assignment]
            return True


_registry: NotificationBackendRegistry = NotificationBackendRegistry()


def get_active() -> _NBProto:
    return _registry.get_active()


def set_active(provider: _NBProto) -> None:
    _registry.set_active(provider)


def clear() -> None:
    _registry.clear()


def clear_if_active(provider: object) -> bool:
    return _registry.clear_if_active(provider)
