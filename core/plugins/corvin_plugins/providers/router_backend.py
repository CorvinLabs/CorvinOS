"""RouterBackend registry and default implementation (ADR-0033).

Usage (plugin on_load):
    ctx.router_registry.set_active(self)

Usage (caller):
    from corvin_plugins.providers.router_backend import get_active
    result = get_active().route(text, personas, model=m, mode="heuristic",
                                tenant_id=tid)
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corvin_plugins.protocol import RouterBackend as _RBProto

_log = logging.getLogger("corvin.router")


# ── Default implementation ────────────────────────────────────────────────────

class ChainRouterBackend:
    """Default: delegate to operator/bridges/shared/router.py (ADR-0033).

    Wraps the existing fake → heuristic → embeddings → Anthropic SDK → CLI
    chain with zero behavior change.  All parameters (model, mode, timeout,
    min_confidence) are passed through so the chain can use them.
    Must NOT raise (ADR-0033 must-NOT).
    """

    def _router_mod(self):  # type: ignore[return]
        try:
            import router as _r  # type: ignore[import]
            return _r
        except ImportError:
            pass
        _shared = (
            Path(__file__).resolve().parents[6]
            / "operator/bridges/shared"
        )
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        try:
            import router as _r  # type: ignore[import]
            return _r
        except ImportError:
            return None

    def route(
        self,
        text: str,
        personas: list[dict],
        *,
        model: str = "",
        min_confidence: float = 0.5,
        timeout: float = 12.0,
        mode: str = "heuristic",
        tenant_id: str = "_default",
    ) -> dict | None:
        try:
            m = self._router_mod()
            if m is None:
                return None
            kwargs: dict = {"min_confidence": min_confidence, "mode": mode}
            if model:
                kwargs["model"] = model
            if timeout != 12.0:
                kwargs["timeout"] = timeout
            return m.route(text, personas, **kwargs)
        except Exception as exc:
            _log.debug("router.route failed: %s", exc)
            return None


# ── Registry ──────────────────────────────────────────────────────────────────

class RouterBackendRegistry:
    """Holds the active RouterBackend for this process.  Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner_plugin_id: str | None = None
        self._active: _RBProto = ChainRouterBackend()  # type: ignore[assignment]

    def set_active(self, provider: _RBProto) -> None:
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

    def get_active(self) -> _RBProto:
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
            self._active = ChainRouterBackend()  # type: ignore[assignment]
            return True

    def owner_plugin_id(self) -> str | None:
        """The plugin that installed the current provider, if it is known."""
        with self._lock:
            return self._owner_plugin_id

    def clear(self) -> None:
        """Restore the bundled default provider."""
        with self._lock:
            self._active = ChainRouterBackend()  # type: ignore[assignment]

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
            self._active = ChainRouterBackend()  # type: ignore[assignment]
            return True


_registry: RouterBackendRegistry = RouterBackendRegistry()


def get_active() -> _RBProto:
    return _registry.get_active()


def set_active(provider: _RBProto) -> None:
    _registry.set_active(provider)


def clear() -> None:
    _registry.clear()


def clear_if_active(provider: object) -> bool:
    return _registry.clear_if_active(provider)


def release_owned_by(plugin_id: str) -> bool:
    return _registry.release_owned_by(plugin_id)
