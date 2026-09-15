"""Corvin STT — Speech-to-Text provider abstraction.

Engine-agnostic by design: STT runs at the bridge / adapter layer
BEFORE any engine subprocess is spawned. Whether the operator uses
Claude Code, Codex CLI, Gemini CLI, or any future WorkerEngine,
voice messages are transcribed via this module and the engine sees
only the resulting text.

Public surface:

* ``STTProvider`` — Protocol every provider implements.
* ``TranscriptResult`` — dataclass returned by ``transcribe()``.
* ``STTError`` — base exception (timeout, provider-down, api-error).
* ``resolve()`` — pick a provider given env / config; returns the
  first reachable one, or the explicit override.
* ``transcribe()`` — convenience wrapper that resolves + calls.

See ``base.py`` for the contract details and ``resolver.py`` for the
selection logic.
"""
from __future__ import annotations

from .base import (
    STTError,
    STTProviderUnavailable,
    STTTimeout,
    STTTranscriptionFailed,
    TranscriptResult,
)
from .resolver import (
    DEFAULT_CHAIN,
    available_providers,
    provider_status,
    resolve,
    transcribe,
)

__all__ = [
    "STTError",
    "STTProviderUnavailable",
    "STTTimeout",
    "STTTranscriptionFailed",
    "TranscriptResult",
    "DEFAULT_CHAIN",
    "available_providers",
    "provider_status",
    "resolve",
    "transcribe",
]
