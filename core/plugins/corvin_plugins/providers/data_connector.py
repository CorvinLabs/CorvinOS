"""DataConnector registry (ADR-0030 plugin type ``data_connector``, L24).

``data_connector`` was listed in ``KNOWN_PLUGIN_TYPES`` from the start but had no
registry handle on ``PluginContext``, so such a plugin could implement
``on_load()`` and find nothing to register with.  This closes that gap with the
same shape as the ADR-0033 providers.

Compliance note (L24): connector audit records METADATA ONLY — never row contents or
query payloads (L24 snapshot rules, GDPR Art. 5).  A provider that
puts row data into an audit detail or a log line breaks that guarantee.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

_log = logging.getLogger("corvin.data.connector")


class DataConnectorRegistry:
    """Holds the active data connector for this process.  Thread-safe.

    No default implementation: absence means "the built-in DSI adapters apply"
    (L24 resolves its own chain), not "data access is off".
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Any | None = None

    def set_active(self, provider: Any) -> None:
        with self._lock:
            self._active = provider

    def clear(self) -> None:
        with self._lock:
            self._active = None

    def get_active(self) -> Any | None:
        with self._lock:
            return self._active

    def is_installed(self) -> bool:
        return self.get_active() is not None


_registry: DataConnectorRegistry = DataConnectorRegistry()


def get_active() -> Any | None:
    return _registry.get_active()


def set_active(provider: Any) -> None:
    _registry.set_active(provider)


def clear() -> None:
    _registry.clear()


def is_installed() -> bool:
    return _registry.is_installed()
