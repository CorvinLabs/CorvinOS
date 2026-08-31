"""STTProvider Protocol + result + exception types.

Every provider declares the same contract: take a file path on disk,
return a transcript with metadata. No streaming, no chunking — voice
notes are bounded by bridge upload limits (Discord 25 MB, Telegram
50 MB, WhatsApp 16 MB).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


# ── Exceptions ──────────────────────────────────────────────────────


class STTError(Exception):
    """Base for every STT-layer failure."""


class STTProviderUnavailable(STTError):
    """Raised when the provider cannot be reached at all.

    Examples: missing API key, missing dependency (pywhispercpp not
    installed), no GPU when the local provider requires one. The
    resolver catches this and falls through to the next provider in
    the chain.
    """


class STTTimeout(STTError):
    """Raised when the provider did not respond inside the budget.

    Distinct from ``STTTranscriptionFailed`` so callers (and the
    resolver) can decide whether to retry / fall back / surface to
    the user differently.
    """


class STTTranscriptionFailed(STTError):
    """Provider was reachable, but the API call itself failed.

    Includes garbled audio (provider returns empty), HTTP 5xx, model
    errors. The resolver still tries the next provider in the chain
    on this class — operator-side failures aren't tied to network
    reachability.
    """


# ── Result ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TranscriptResult:
    """Output of one transcription call.

    The chain ``transcript`` text is the only PII-bearing field; the
    audit-event emission deliberately records only the *length*
    (``chars``) and never the content. Callers needing the content
    take it directly from this object.
    """

    text: str
    provider: str
    lang: str | None = None  # provider-detected ("de", "en", ...) or None
    duration_s: float | None = None  # audio duration if the provider exposes it
    chars: int = field(init=False)  # convenience: len(text), kept for audit

    def __post_init__(self) -> None:
        # Use object.__setattr__ because frozen dataclasses block normal assignment.
        object.__setattr__(self, "chars", len(self.text))


# ── Provider Protocol ───────────────────────────────────────────────


@runtime_checkable
class STTProvider(Protocol):
    """Minimal contract every STT backend implements."""

    name: str

    def is_available(self) -> bool:
        """Cheap reachability probe.

        Called by the resolver before attempting transcription. MUST
        NOT make a paid API call; check env vars + import-ability +
        local resource (e.g. model file present, GPU reachable).
        Returns True if ``transcribe()`` is worth trying.
        """
        ...

    def transcribe(
        self,
        audio_path: Path,
        *,
        lang: str | None = None,
        timeout_s: float | None = None,
    ) -> TranscriptResult:
        """Convert *audio_path* to text.

        Raises ``STTProviderUnavailable`` if the provider was reachable
        at ``is_available()`` time but became unavailable since
        (e.g. API key revoked). Raises ``STTTimeout`` on budget
        exhaustion. Raises ``STTTranscriptionFailed`` on any other
        API-side error.

        ``lang`` is a hint (``"de"``, ``"en"``, ...); ``None`` means
        auto-detect. Providers that don't support hints ignore it.

        ``timeout_s`` is the budget in seconds; ``None`` means
        provider default (typically ~60 s).
        """
        ...
