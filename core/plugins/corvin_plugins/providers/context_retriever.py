"""ContextRetriever registry and default implementation (ADR-0599, ADR-0033).

A ``context_retriever`` is a swappable strategy for *choosing which context is
put in front of the model* at two surfaces that do it badly today: the CEL memory
stage (lexical substring) and TDE step assembly (blind truncation). It only ever
NARROWS or REORDERS a candidate set — it never adds.

Usage (plugin on_load):
    ctx.context_retriever_registry.set_active(self)

Usage (caller — fail-open seam):
    from corvin_plugins.providers import context_retriever
    selected = context_retriever.get_active().select(query, candidates,
                                                      budget=..., tenant_id=tid)

The bundled default is :class:`PassthroughContextRetriever`, whose ``select``
returns ``candidates`` UNCHANGED. That is the load-bearing property: with no
plugin active, every seam behaves byte-for-byte as it does today, so this core
contract is additive (ADR-0233) and behaviour-neutral until a provider installs
itself via ``set_active``.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corvin_plugins.protocol import ContextRetriever as _CRProto

_log = logging.getLogger("corvin.context_retriever")


# ── Default implementation ────────────────────────────────────────────────────

class PassthroughContextRetriever:
    """Default: identity selection — return ``candidates`` unchanged.

    This is what makes the two ADR-0599 seams fail-open by construction. The
    caller hands its already-produced (and already-gated, ADR-0297) candidate
    list; the passthrough hands the SAME list straight back, so nothing is
    reranked, narrowed or added. No retriever effect exists until a plugin calls
    ``set_active``.
    """

    def select(
        self,
        query: str,
        candidates: list,
        *,
        budget: int | None = None,
        tenant_id: str | None = None,
    ) -> list:
        return candidates


# ── Registry ──────────────────────────────────────────────────────────────────

class ContextRetrieverRegistry:
    """Holds the active ContextRetriever for this process.  Thread-safe.

    Mirrors ``RecallBackendRegistry`` (ADR-0033): identity ownership recorded at
    ``set_active`` time via ``loading.current()`` so the slot is released by the
    plugin that took it, not by matching the object or guessing from
    ``plugin_type``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner_plugin_id: str | None = None
        self._active: _CRProto = PassthroughContextRetriever()  # type: ignore[assignment]

    def set_active(self, provider: _CRProto) -> None:
        """Install ``provider`` as the active retriever for this process.

        Records WHICH PLUGIN did it (``loading.current()``) so the slot can later
        be released by plugin identity. A plugin that installs a helper object
        still owns the slot.
        """
        from .. import loading as _loading

        _who = _loading.current()
        with self._lock:
            # Only a plugin that is LOADING may claim ownership. A set_active()
            # from anywhere else keeps the previous owner rather than erasing it —
            # the same "lesser wrong" the recall registry documents.
            if _who is not None:
                self._owner_plugin_id = _who.plugin_id
            self._active = provider

    def get_active(self) -> _CRProto:
        with self._lock:
            return self._active

    def release_owned_by(self, plugin_id: str) -> bool:
        """Release the slot if ``plugin_id`` is the plugin that took it.

        Identity-based: the object in the slot may be a helper the plugin created
        rather than the plugin itself. Ownership recorded at ``set_active`` time
        is the only thing that answers "is this slot yours" correctly.
        """
        with self._lock:
            if self._owner_plugin_id is None or self._owner_plugin_id != plugin_id:
                return False
            self._owner_plugin_id = None
            self._active = PassthroughContextRetriever()  # type: ignore[assignment]
            return True

    def owner_plugin_id(self) -> str | None:
        """The plugin that installed the current retriever, if it is known."""
        with self._lock:
            return self._owner_plugin_id

    def clear(self) -> None:
        """Restore the bundled passthrough retriever."""
        with self._lock:
            self._owner_plugin_id = None
            self._active = PassthroughContextRetriever()  # type: ignore[assignment]

    def clear_if_active(self, provider: object) -> bool:
        """Restore the default only if ``provider`` is the one installed.

        Instance-checked on purpose: a plugin unloading must not evict a retriever
        a DIFFERENT plugin installed after it.
        """
        with self._lock:
            if self._active is not provider:
                return False
            self._owner_plugin_id = None
            self._active = PassthroughContextRetriever()  # type: ignore[assignment]
            return True


_registry: ContextRetrieverRegistry = ContextRetrieverRegistry()


def get_active() -> _CRProto:
    return _registry.get_active()


def set_active(provider: _CRProto) -> None:
    _registry.set_active(provider)


def clear() -> None:
    _registry.clear()


def clear_if_active(provider: object) -> bool:
    return _registry.clear_if_active(provider)


def release_owned_by(plugin_id: str) -> bool:
    return _registry.release_owned_by(plugin_id)


def owner_plugin_id() -> str | None:
    return _registry.owner_plugin_id()
