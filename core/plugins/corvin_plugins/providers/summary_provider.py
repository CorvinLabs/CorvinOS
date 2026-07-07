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
        self._active: _SPProto = ClaudeCliSummaryProvider()  # type: ignore[assignment]

    def set_active(self, provider: _SPProto) -> None:
        with self._lock:
            self._active = provider

    def get_active(self) -> _SPProto:
        with self._lock:
            return self._active


_registry: SummaryProviderRegistry = SummaryProviderRegistry()


def get_active() -> _SPProto:
    return _registry.get_active()


def set_active(provider: _SPProto) -> None:
    _registry.set_active(provider)
