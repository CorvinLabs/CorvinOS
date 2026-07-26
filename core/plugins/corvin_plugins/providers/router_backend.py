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
        self._active: _RBProto = ChainRouterBackend()  # type: ignore[assignment]

    def set_active(self, provider: _RBProto) -> None:
        with self._lock:
            self._active = provider

    def get_active(self) -> _RBProto:
        with self._lock:
            return self._active

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
