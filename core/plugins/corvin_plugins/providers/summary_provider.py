"""SummaryProvider registry and default implementation (ADR-0033).

Usage (plugin on_load):
    ctx.summary_registry.set_active(self)

Usage (caller):
    from corvin_plugins.providers.summary_provider import get_active
    spoken = get_active().summarize(long_text, lang="de", tenant_id=tid)
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corvin_plugins.protocol import SummaryProvider as _SPProto

_log = logging.getLogger("corvin.summary")


# ── Default implementation ────────────────────────────────────────────────────

class ClaudeCliSummaryProvider:
    """Default: delegate to operator/voice/scripts/summarize.py via subprocess.

    Mirrors the existing call site in the adapter — same CLI contract,
    same naive-truncation fallback when the script is unavailable.
    Must NOT import the anthropic SDK directly (ADR-0033 must-NOT).
    """

    def _script_path(self) -> str | None:
        from pathlib import Path
        candidates = [
            Path(__file__).resolve().parents[6]
            / "operator/voice/scripts/summarize.py",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return None

    def summarize(
        self,
        text: str,
        *,
        lang: str = "de",
        max_chars: int = 400,
        tenant_id: str = "_default",
    ) -> str:
        script = self._script_path()
        if script is None:
            _log.debug("summarize.py not found — naive truncation fallback")
            return text[:max_chars]
        try:
            result = subprocess.run(
                # sys.executable, not "python3": the latter is absent on Windows
                # and can be a different interpreter than the one with the deps
                # (path-audit 2026-07-07 round-2).
                [sys.executable, script, "--lang", lang, "--max-chars", str(max_chars)],
                input=text,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            _log.warning("summarize.py exited %d: %s", result.returncode, result.stderr[:200])
        except subprocess.TimeoutExpired:
            _log.warning("summarize.py timed out — naive truncation fallback")
        except Exception as exc:
            _log.warning("summarize.py failed: %s", exc)
        return text[:max_chars]


# ── Registry ──────────────────────────────────────────────────────────────────

class SummaryProviderRegistry:
    """Holds the active SummaryProvider for this process.  Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner_plugin_id: str | None = None
        self._active: _SPProto = ClaudeCliSummaryProvider()  # type: ignore[assignment]

    def set_active(self, provider: _SPProto) -> None:
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

    def get_active(self) -> _SPProto:
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
            self._active = ClaudeCliSummaryProvider()  # type: ignore[assignment]
            return True

    def owner_plugin_id(self) -> str | None:
        """The plugin that installed the current provider, if it is known."""
        with self._lock:
            return self._owner_plugin_id

    def clear(self) -> None:
        """Restore the bundled default provider."""
        with self._lock:
            self._owner_plugin_id = None
            self._active = ClaudeCliSummaryProvider()  # type: ignore[assignment]

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
            self._active = ClaudeCliSummaryProvider()  # type: ignore[assignment]
            return True


_registry: SummaryProviderRegistry = SummaryProviderRegistry()


def get_active() -> _SPProto:
    return _registry.get_active()


def set_active(provider: _SPProto) -> None:
    _registry.set_active(provider)


def clear() -> None:
    _registry.clear()


def clear_if_active(provider: object) -> bool:
    return _registry.clear_if_active(provider)


def release_owned_by(plugin_id: str) -> bool:
    return _registry.release_owned_by(plugin_id)
