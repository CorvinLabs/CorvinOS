"""STT provider resolver + fallback chain.

Resolution order:

1. ``CORVIN_STT_PROVIDER=<name>`` env override → use this provider
   exclusively. No fallback. Fail-loud if unavailable.
2. Otherwise: walk ``DEFAULT_CHAIN`` in order, take the first
   provider where ``is_available()`` returns True. If none, raise
   ``STTProviderUnavailable`` aggregating the chain.

The chain default is ``openai → local`` (changed 2026-07-09): cloud Whisper
is meaningfully more accurate than any local GGML model small enough to
auto-download, so when an API key is configured it should always win.
``is_available()`` already makes ``openai`` a no-op without a key, so this
still degrades to local-only for operators with no API key, air-gapped
environments, or a data-residency policy forbidding OpenAI — no separate
"key present?" branch needed. Operators who want local-first (e.g. to avoid
sending audio off-box even when a key exists) set
``CORVIN_STT_CHAIN=local,openai`` (or pin via ``CORVIN_STT_PROVIDER=local``).

A failed ``transcribe()`` call falls through to the next provider in
the chain ONLY when the failure was provider-side
(``STTProviderUnavailable`` or ``STTTranscriptionFailed``).
``STTTimeout`` is a structural failure that re-raises to the caller —
falling back on timeout would multiply the user's wait time.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable, Optional

from .base import (
    STTError,
    STTProvider,
    STTProviderUnavailable,
    STTTimeout,
    STTTranscriptionFailed,
    TranscriptResult,
)
from .openai_whisper import OpenAIWhisperProvider
from .local_whisper import LocalWhisperProvider


# Order matters — first available wins by default.
DEFAULT_CHAIN: tuple[str, ...] = ("openai", "local")

_PROVIDERS: dict[str, type[STTProvider]] = {
    "openai": OpenAIWhisperProvider,
    "local":  LocalWhisperProvider,
}


def _chain_from_env() -> tuple[str, ...]:
    raw = os.environ.get("CORVIN_STT_CHAIN", "")
    if not raw.strip():
        return DEFAULT_CHAIN
    names = tuple(p.strip() for p in raw.split(",") if p.strip())
    # Drop unknowns silently — typos shouldn't crash the resolver. The
    # documented behaviour is "try every known name in order"; an
    # unknown name simply isn't in _PROVIDERS so we skip it.
    return tuple(n for n in names if n in _PROVIDERS) or DEFAULT_CHAIN


def available_providers() -> list[str]:
    """Names of providers whose ``is_available()`` returns True now."""
    out: list[str] = []
    for name in _PROVIDERS:
        try:
            if _PROVIDERS[name]().is_available():
                out.append(name)
        except Exception:  # noqa: BLE001
            # Defensive: probing should never crash the resolver.
            continue
    return out


def provider_status() -> dict[str, dict]:
    """Structured per-provider status for the Console voice-status panel
    (ADR-0185 M4, Decision 4).

    Cheap introspection only — NEVER calls ``transcribe()``, NEVER raises.
    Goes a bit further than ``is_available()`` so the UI can show *why* a
    provider isn't ready (missing package vs. missing model file vs. no
    API key) instead of a flat true/false. Each entry:

      ready:              bool       — usable right now (mirrors is_available())
      package_installed:  bool       — underlying package imports
      model_present:      bool|None  — local model file on disk (None: n/a)
      key_configured:     bool|None  — API key resolvable (None: n/a)
      detail:             str        — short, human-readable, non-leaky status

    Never includes raw exception text or internal chain/failure strings —
    those must never reach the UI (ADR-0185 Must-NOT).
    """
    status: dict[str, dict] = {}

    # -- local (pywhispercpp / opt-in faster-whisper) --
    try:
        from .local_whisper import (
            _DEFAULT_MODEL,
            _faster_whisper_importable,
            _models_dir,
            _prefer_faster_whisper,
        )

        if _prefer_faster_whisper() and _faster_whisper_importable():
            status["local"] = {
                "ready": True,
                "package_installed": True,
                "model_present": None,
                "key_configured": None,
                "detail": "faster-whisper engine active (CORVIN_STT_LOCAL_ENGINE override)",
            }
        else:
            try:
                import pywhispercpp.model  # noqa: F401
                package_installed = True
            except ImportError:
                package_installed = False
            model_name = os.environ.get("CORVIN_STT_LOCAL_MODEL", _DEFAULT_MODEL)
            model_path = _models_dir() / f"ggml-{model_name}.bin"
            model_present = model_path.exists() and model_path.stat().st_size > 0
            ready = package_installed and model_present
            if ready:
                detail = f"ready (model {model_name!r})"
            elif not package_installed:
                detail = "pywhispercpp not installed"
            else:
                detail = f"model {model_name!r} not downloaded yet"
            status["local"] = {
                "ready": ready,
                "package_installed": package_installed,
                "model_present": model_present,
                "key_configured": None,
                "detail": detail,
            }
    except Exception as exc:  # noqa: BLE001 — status probe must never crash
        status["local"] = {
            "ready": False,
            "package_installed": False,
            "model_present": None,
            "key_configured": None,
            "detail": f"status probe failed ({exc.__class__.__name__})",
        }

    # -- openai --
    try:
        from .openai_whisper import _resolve_api_key

        key_configured = bool(_resolve_api_key())
        try:
            import openai  # noqa: F401
            package_installed = True
        except ImportError:
            package_installed = False
        ready = key_configured and package_installed
        if ready:
            detail = "ready"
        elif not key_configured:
            detail = "no API key configured"
        else:
            detail = "openai package not installed"
        status["openai"] = {
            "ready": ready,
            "package_installed": package_installed,
            "model_present": None,
            "key_configured": key_configured,
            "detail": detail,
        }
    except Exception as exc:  # noqa: BLE001
        status["openai"] = {
            "ready": False,
            "package_installed": False,
            "model_present": None,
            "key_configured": False,
            "detail": f"status probe failed ({exc.__class__.__name__})",
        }

    return status


def resolve(name: str | None = None) -> STTProvider:
    """Pick a provider, honouring env override + chain defaults.

    Raises ``STTProviderUnavailable`` if no provider in the chain is
    available. Callers that need a specific provider pass *name*
    explicitly (same semantics as the env override).
    """
    explicit = name or os.environ.get("CORVIN_STT_PROVIDER")
    if explicit:
        cls = _PROVIDERS.get(explicit)
        if cls is None:
            raise STTProviderUnavailable(
                f"unknown STT provider {explicit!r}; "
                f"known: {sorted(_PROVIDERS)}"
            )
        instance = cls()
        if not instance.is_available():
            raise STTProviderUnavailable(
                f"STT provider {explicit!r} is not available "
                f"(check API key / deps / model). "
                f"No fallback because CORVIN_STT_PROVIDER pinned it."
            )
        return instance

    chain = _chain_from_env()
    failures: list[str] = []
    for n in chain:
        cls = _PROVIDERS.get(n)
        if cls is None:
            continue
        try:
            instance = cls()
            if instance.is_available():
                return instance
            failures.append(f"{n}: not available")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{n}: {exc}")
    raise STTProviderUnavailable(
        f"no STT provider available; chain={chain}; failures={failures}"
    )


def transcribe(
    audio_path: Path,
    *,
    lang: str | None = None,
    timeout_s: float | None = None,
    provider: str | None = None,
) -> TranscriptResult:
    """High-level entry: resolve + call + fall through to next on failure.

    ``provider=<name>`` pins the choice and disables fallback (mirror
    of the env-override semantics in ``resolve``).
    """
    explicit = provider or os.environ.get("CORVIN_STT_PROVIDER")
    if explicit:
        # Pinned — no fallback by contract.
        p = resolve(explicit)
        return p.transcribe(audio_path, lang=lang, timeout_s=timeout_s)

    chain = _chain_from_env()
    failures: list[str] = []
    last_error: Optional[Exception] = None
    for n in chain:
        cls = _PROVIDERS.get(n)
        if cls is None:
            continue
        try:
            instance = cls()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{n}: init failed: {exc}")
            last_error = exc
            continue
        if not instance.is_available():
            failures.append(f"{n}: not available")
            continue
        try:
            return instance.transcribe(
                audio_path, lang=lang, timeout_s=timeout_s,
            )
        except STTTimeout:
            # Timeout is structural — don't multiply user wait by
            # falling through.
            raise
        except (STTProviderUnavailable, STTTranscriptionFailed) as exc:
            failures.append(f"{n}: {exc}")
            last_error = exc
            continue
    if last_error is not None:
        raise STTProviderUnavailable(
            f"all STT providers exhausted; chain={chain}; "
            f"failures={failures}"
        ) from last_error
    raise STTProviderUnavailable(
        f"no STT provider available; chain={chain}; failures={failures}"
    )
