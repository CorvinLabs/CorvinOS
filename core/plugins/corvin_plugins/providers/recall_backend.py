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
        self._active: _RBProto = SqliteRecallBackend()  # type: ignore[assignment]

    def set_active(self, provider: _RBProto) -> None:
        with self._lock:
            self._active = provider

    def get_active(self) -> _RBProto:
        with self._lock:
            return self._active


_registry: RecallBackendRegistry = RecallBackendRegistry()


def get_active() -> _RBProto:
    return _registry.get_active()


def set_active(provider: _RBProto) -> None:
    _registry.set_active(provider)
