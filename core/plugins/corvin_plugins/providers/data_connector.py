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
        self._owner_plugin_id: str | None = None
        self._active: Any | None = None

    def set_active(self, provider: Any) -> None:
        """Install ``provider`` as the active one for this process.

        Records WHICH PLUGIN did it (``loading.current()``), so the slot can
        later be released by plugin identity rather than by matching the
        object or guessing from ``plugin_type``. A plugin that installs a
        helper object still owns the slot.
        """
        from .. import loading as _loading

        _who = _loading.current()
        with self._lock:
            # Only a plugin that is LOADING may claim ownership. A set_active()
            # from anywhere else (a request handler, a thread a plugin spawned,
            # a timer) used to write None here — which not only left the new
            # occupant unowned, it ERASED the previous legitimate owner, so the
            # slot could never be released by anyone again. Keeping the old
            # owner is the lesser wrong: the slot still belongs to whoever took
            # it during a load, and unloading them releases it.
            if _who is not None:
                self._owner_plugin_id = _who.plugin_id
            self._active = provider

    def clear(self) -> None:
        with self._lock:
            self._owner_plugin_id = None
            self._active = None

    def clear_if_active(self, provider: object) -> bool:
        """Detach only if ``provider`` is the one currently installed.

        Instance-checked: a plugin unloading must not evict a provider that a
        DIFFERENT plugin installed after it.
        """
        with self._lock:
            if self._active is not provider:
                return False
            self._active = None
            return True

    def get_active(self) -> Any | None:
        with self._lock:
            return self._active

    def release_owned_by(self, plugin_id: str) -> bool:
        """Release the slot if ``plugin_id`` is the plugin that took it.

        Identity-based, which is the point: the object in the slot may be a
        helper the plugin created rather than the plugin itself, and the
        plugin's ``plugin_type`` may not even name this registry. Ownership is
        recorded at ``set_active`` time and is the only thing that answers
        "is this slot yours" correctly.
        """
        with self._lock:
            if self._owner_plugin_id is None or self._owner_plugin_id != plugin_id:
                return False
            self._owner_plugin_id = None
            self._active = None
            return True

    def owner_plugin_id(self) -> str | None:
        """The plugin that installed the current provider, if it is known."""
        with self._lock:
            return self._owner_plugin_id

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


def clear_if_active(provider: object) -> bool:
    return _registry.clear_if_active(provider)


def release_owned_by(plugin_id: str) -> bool:
    return _registry.release_owned_by(plugin_id)
