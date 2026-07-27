"""RecallBackend registry and default implementation (ADR-0033).

Usage (plugin on_load):
    ctx.recall_registry.set_active(self)

Usage (caller):
    from corvin_plugins.providers.recall_backend import get_active
    get_active().index_turn(channel, chat_key,
                            user_text=..., assistant_text=..., tenant_id=tid)
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corvin_plugins.protocol import RecallBackend as _RBProto

_log = logging.getLogger("corvin.recall")


# ── Default implementation ────────────────────────────────────────────────────

class SqliteRecallBackend:
    """Default: delegate to operator/bridges/shared/conversation_recall.py.

    Lazy-imports conversation_recall to avoid a hard dependency at module load
    time. Falls back to a no-op with a warning if the module is unavailable.
    Signatures mirror conversation_recall.py 1-to-1 (ADR-0033).
    """

    def _mod(self):  # type: ignore[return]
        try:
            import conversation_recall as _r  # type: ignore[import]
            return _r
        except ImportError:
            pass
        try:
            import importlib.util  # noqa: E401
            import sys
            from pathlib import Path
            for _p in [
                Path(__file__).resolve().parents[6]
                / "operator/bridges/shared/conversation_recall.py",
            ]:
                if _p.exists():
                    spec = importlib.util.spec_from_file_location(
                        "conversation_recall", _p)
                    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                    spec.loader.exec_module(mod)  # type: ignore[union-attr]
                    sys.modules["conversation_recall"] = mod
                    return mod
        except Exception:
            pass
        return None

    def index_turn(
        self,
        channel: str,
        chat_key: str,
        *,
        user_text: str,
        assistant_text: str,
        msg_id: str = "",
        persona: str = "",
        ts: float | None = None,
        run_id: str = "",
        tenant_id: str | None = None,
    ) -> dict:
        m = self._mod()
        if m is None:
            _log.debug("conversation_recall unavailable — index_turn skipped")
            return {"ok": False, "reason": "module-unavailable"}
        try:
            return m.index_turn(
                channel, chat_key,
                user_text=user_text,
                assistant_text=assistant_text,
                msg_id=msg_id,
                persona=persona,
                ts=ts,
                run_id=run_id,
                tenant_id=tenant_id,
            ) or {}
        except Exception as exc:
            _log.warning("conversation_recall.index_turn failed: %s", exc)
            return {"ok": False, "reason": str(exc)}

    def recall(
        self,
        query: str,
        *,
        channel: str | None = None,
        chat_key: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 20,
        caller_persona: str = "",
        tenant_id: str | None = None,
    ) -> list[dict]:
        m = self._mod()
        if m is None:
            return []
        try:
            rows = m.recall(
                query,
                channel=channel,
                chat_key=chat_key,
                since=since,
                until=until,
                limit=limit,
                caller_persona=caller_persona,
                tenant_id=tenant_id,
            ) or []
            # conversation_recall returns Recall dataclass instances; convert
            # to plain dicts so the protocol return type is honoured.
            return [r if isinstance(r, dict) else vars(r) for r in rows]
        except Exception as exc:
            _log.warning("conversation_recall.recall failed: %s", exc)
            return []

    def forget(
        self,
        *,
        channel: str | None = None,
        chat_key: str | None = None,
        before_ts: float | None = None,
        tenant_id: str | None = None,
    ) -> int:
        m = self._mod()
        if m is None:
            return 0
        try:
            return int(m.forget(
                channel=channel,
                chat_key=chat_key,
                before_ts=before_ts,
                tenant_id=tenant_id,
            ) or 0)
        except Exception as exc:
            _log.warning("conversation_recall.forget failed: %s", exc)
            return 0


# ── Registry ──────────────────────────────────────────────────────────────────

class RecallBackendRegistry:
    """Holds the active RecallBackend for this process.  Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner_plugin_id: str | None = None
        self._active: _RBProto = SqliteRecallBackend()  # type: ignore[assignment]

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
            self._active = SqliteRecallBackend()  # type: ignore[assignment]
            return True

    def owner_plugin_id(self) -> str | None:
        """The plugin that installed the current provider, if it is known."""
        with self._lock:
            return self._owner_plugin_id

    def clear(self) -> None:
        """Restore the bundled default provider."""
        with self._lock:
            self._active = SqliteRecallBackend()  # type: ignore[assignment]

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
            self._active = SqliteRecallBackend()  # type: ignore[assignment]
            return True


_registry: RecallBackendRegistry = RecallBackendRegistry()


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
