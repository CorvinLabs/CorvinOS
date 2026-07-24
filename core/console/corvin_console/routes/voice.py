"""Voice REST endpoints — STT + TTS for the web messenger (ADR-0037 Iter 3b).

Endpoints
---------
  POST /v1/console/voice/transcribe   audio blob (multipart) → text
  POST /v1/console/voice/tts          {text, lang?} → audio/ogg blob

STT delegates to ``operator/voice/scripts/stt/`` (the same provider
chain bridges use). TTS first summarizes and resolves the user's
provider/voice pins, then synthesizes via an in-process OpenAI branch
(only when the resolved provider is OpenAI and CORVIN_TTS_LOCAL_ONLY
permits cloud egress) with ``operator/voice/scripts/say.py`` as the
subprocess fallback chain (openai → edge → piper).

Audit policy (load-bearing — CLAUDE.md § Layer 23):
    voice.transcribed audit emits METADATA ONLY, never transcript text.
This module never writes ``text`` to any audit field. The same goes
for TTS — only ``len(text)`` is logged.
"""
from __future__ import annotations

import asyncio
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status as http_status
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session

import logging
_log = logging.getLogger(__name__)


_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
# Source-tree path; in a wheel install operator/* is vendored under
# corvin_console/_vendor/operator/* (hatch_build.py) and _REPO points at
# site-packages/.. where no operator/ exists — so say.py was "not found" and TTS
# failed on every pip install. Resolve to whichever layout actually has the files.
_VENDOR_OPERATOR = _THIS_DIR.parent / "_vendor" / "operator"


def _resolve_operator_dir(*parts: str) -> Path:
    repo = _REPO.joinpath("operator", *parts)
    if repo.is_dir():
        return repo
    vendored = _VENDOR_OPERATOR.joinpath(*parts)
    return vendored if vendored.is_dir() else repo


_VOICE_SCRIPTS = _resolve_operator_dir("voice", "scripts")
_STT_DIR = _VOICE_SCRIPTS / "stt"
_VOICE_SHARED = _resolve_operator_dir("bridges", "shared")

if str(_VOICE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_VOICE_SCRIPTS))
if str(_VOICE_SHARED) not in sys.path:
    sys.path.insert(0, str(_VOICE_SHARED))

try:
    from stt import transcribe as _stt_transcribe  # noqa: E402
    from stt import STTError, STTTimeout, STTProviderUnavailable  # noqa: E402
    _STT_OK = True
except Exception:  # pragma: no cover
    _stt_transcribe = None  # type: ignore[assignment]
    STTError = STTTimeout = STTProviderUnavailable = Exception  # type: ignore[misc,assignment]
    _STT_OK = False

try:
    # say.py's ``provider_status()`` (ADR-0185 M4) — imported as a module
    # (not shelled out to, unlike the TTS synth path below) purely for the
    # cheap, non-mocked status probe the Console status panel needs.
    import say as _say_module  # noqa: E402 — voice/scripts/say.py
    _SAY_STATUS_OK = True
except Exception:  # pragma: no cover
    _say_module = None  # type: ignore[assignment]
    _SAY_STATUS_OK = False

try:
    import profile as _profile_module  # noqa: E402 — bridges/shared/profile.py
    _PROFILE_OK = True
except Exception:  # pragma: no cover
    _profile_module = None  # type: ignore[assignment]
    _PROFILE_OK = False

try:
    # Language detection for Smart Hybrid voice-language resolution (BUG-1.2)
    sys.path.insert(0, str(_VOICE_SCRIPTS))
    from detect_lang import detect_confident as _detect_confident_de_en  # noqa: E402
    _DETECT_LANG_OK = True
except Exception:  # pragma: no cover
    _detect_confident_de_en = None  # type: ignore[assignment]
    _DETECT_LANG_OK = False

try:
    # i18n for BCP-47 normalization
    sys.path.insert(0, str(_VOICE_SHARED))
    import i18n as _i18n  # noqa: E402 — bridges/shared/i18n.py
    _I18N_OK = True
except Exception:  # pragma: no cover
    _i18n = None  # type: ignore[assignment]
    _I18N_OK = False


router = APIRouter()


# Concurrent TTS syntheses allowed per console process. voice_tts/voice_segment
# were plain sync `def`s, so FastAPI ran them in Starlette's anyio threadpool
# (40 tokens, never overridden) and each held its token for up to
# _TTS_SUMMARIZE_TIMEOUT_S + _TTS_TIMEOUT_S (~145 s worst case) with no cap.
# 40 concurrent /voice/tts calls therefore drained the pool and stalled EVERY
# other sync route in the console — the same "froze the whole console" class the
# voice_transcribe comment above was written to fix, except nothing bounded who
# else could fill the pool. They are async now: waiters park on the event loop
# (cheap) instead of squatting a thread, and the blocking body runs in the pool
# only once a slot is free.
_TTS_MAX_CONCURRENCY = int(os.environ.get("CORVIN_TTS_MAX_CONCURRENCY", "4"))
# Waiting forever would just move the queue instead of bounding it. A caller that
# cannot get a slot in time is told "no audio" (204) — TTS is an optional
# enhancement and degrading silently is this module's established contract.
_TTS_SLOT_WAIT_S = float(os.environ.get("CORVIN_TTS_SLOT_WAIT_S", "20"))

_tts_sem: "asyncio.Semaphore | None" = None
_tts_sem_lock = threading.Lock()

# Session-recap SUMMARIZE phase gets its own (smaller) bound, separate from
# the TTS slots: it runs a ~120 s LLM subprocess in the shared anyio
# threadpool (default 40 tokens, shared with every sync route in the
# console). Unbounded, ~40 parallel recap clicks would drain that pool and
# stall the whole console — the exact incident class the TTS semaphore's own
# history documents. It must NOT hold a TTS slot for those 120 s either
# (that starved the automatic turn voice, refutation finding 2026-07-17), so
# it is a second, independent semaphore.
_RECAP_SUMMARIZE_MAX_CONCURRENCY = int(
    os.environ.get("CORVIN_RECAP_SUMMARIZE_MAX_CONCURRENCY", "2"))

_recap_sem: "asyncio.Semaphore | None" = None
_recap_sem_lock = threading.Lock()


def _get_tts_semaphore() -> "asyncio.Semaphore":
    """Lazily create the semaphore — it must be bound to the running loop, and
    there is none at import time."""
    global _tts_sem
    if _tts_sem is None:
        with _tts_sem_lock:
            if _tts_sem is None:
                _tts_sem = asyncio.Semaphore(_TTS_MAX_CONCURRENCY)
    return _tts_sem


def _get_recap_semaphore() -> "asyncio.Semaphore":
    global _recap_sem
    if _recap_sem is None:
        with _recap_sem_lock:
            if _recap_sem is None:
                _recap_sem = asyncio.Semaphore(_RECAP_SUMMARIZE_MAX_CONCURRENCY)
    return _recap_sem


async def _run_with_tts_slot(sync_fn, *args, no_slot_log: str = "") -> Response:
    """Bounded async wrapper shared by every synthesis route (voice_tts,
    voice_session_summary, voice_segment): acquire a synthesis slot with a
    bounded wait, run the actual (blocking) work in the threadpool, always
    release. Each route's own log message on a slot-acquire timeout is kept
    caller-specific (they describe what gets skipped in different terms);
    voice_session_summary passes none, silently 204ing like the others did
    before this was consolidated."""
    try:
        await asyncio.wait_for(_get_tts_semaphore().acquire(), _TTS_SLOT_WAIT_S)
    except asyncio.TimeoutError:
        if no_slot_log:
            _log.warning(no_slot_log)
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)
    try:
        return await run_in_threadpool(sync_fn, *args)
    finally:
        _get_tts_semaphore().release()


def _publish_voice_live_event(tenant_id: str, sid: str | None, resp: Response, *, label: str) -> None:
    """Push the just-archived voice file onto the session's live WS stream.

    Best-effort / must never raise: TTS itself already succeeded (or degraded
    to 204) by the time this runs, and a live-attach hiccup must not turn that
    into a request failure. No-op when there is no sid (recap/summary callers
    that never pass one) or the archive write failed — ``_persist_turn_voice``
    signals that by omitting X-Corvin-Voice-File, exactly like the existing
    204-on-failure contract this route already follows everywhere else.
    """
    if not sid:
        return
    name = resp.headers.get("X-Corvin-Voice-File")
    if not name:
        return
    try:
        from .. import chat_runtime as _cr  # noqa: PLC0415 — avoid import cycle at module load
        _cr.publish_voice_event(sid, _cr.voice_dir(tenant_id, sid) / name, label)
    except Exception:  # noqa: BLE001
        pass


def _detect_audio_mime(data: bytes) -> str:
    """Detect audio MIME type from magic bytes.

    say.py may produce OGG-Opus (OpenAI) or MP3 (edge-tts). We detect
    the actual format so the browser receives the correct Content-Type
    and can play it without relying on a hard-coded assumption.
    """
    if data[:4] == b"OggS":
        return "audio/ogg"
    # MP3: ID3 tag header, or sync word 0xFF 0xFB/0xF3/0xFA
    if data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:4] == b"fLaC":
        return "audio/flac"
    return "audio/ogg"  # safe default — the browser will try to decode


# TTS synthesis subprocess wall-clock cap. TTS is an optional enhancement, so
# this is deliberately short — a hung/misconfigured provider must not stall the
# turn. say.py's per-provider timeout (CORVIN_TTS_PROVIDER_TIMEOUT_S, default
# 10s) sits well under this so the auto-chain can try each provider and still
# finish within the budget.
_TTS_TIMEOUT_S = float(os.environ.get("CORVIN_TTS_TIMEOUT_S", "25"))

# OpenAI TTS in-process timeout (shorter than subprocess timeout since no fork overhead)
_OPENAI_TTS_TIMEOUT_S = float(os.environ.get("CORVIN_OPENAI_TTS_TIMEOUT_S", "8"))

_MAX_AUDIO_BYTES = 25 * 1024 * 1024   # 25 MiB hard cap
_ALLOWED_AUDIO_TYPES = (
    "audio/webm", "audio/ogg", "audio/mp4", "audio/x-m4a",
    "audio/mpeg", "audio/wav", "audio/x-wav",
    "video/webm",  # MediaRecorder on Chromium reports video/webm even audio-only
)
_DEFAULT_LANGS = ("de", "en")


def _strip_mime_params(ct: str | None) -> str:
    """Drop any `;param=value` suffix from a Content-Type header.

    Browsers send things like ``audio/webm;codecs=opus`` — the base
    type is what our allowlist gates on.
    """
    if not ct:
        return ""
    return ct.split(";", 1)[0].strip().lower()


def _stt_unavailable_message() -> str:
    """Translate an STT-unavailable failure into a safe, actionable message.

    ADR-0185 Decision 4 / Must-NOT: a resolver failure must never surface
    as the raw ``{"detail": "no STT provider available; chain=...; "
    "failures=..."}`` JSON it used to (that string embeds internal
    provider names and failure reasons and is not something an end user
    can act on). This calls the same ``provider_status()`` introspection
    the Console's voice-status panel uses, so the reason shown here is
    never out of sync with what that panel shows — and it never echoes
    the resolver's own exception text.
    """
    local_ready = openai_ready = False
    model_missing = package_missing = False
    if _STT_OK:
        try:
            from stt import provider_status as _stt_provider_status  # noqa: PLC0415
            status = _stt_provider_status()
            local = status.get("local", {})
            openai = status.get("openai", {})
            local_ready = bool(local.get("ready"))
            openai_ready = bool(openai.get("ready"))
            package_missing = local.get("package_installed") is False
            model_missing = (
                local.get("package_installed") is True
                and local.get("model_present") is False
            )
        except Exception:  # noqa: BLE001 — message must never raise
            pass

    if local_ready or openai_ready:
        # Transient failure (provider was ready a moment ago, e.g. a
        # revoked key or a mid-call crash) — don't claim total absence.
        return (
            "Speech-to-text failed unexpectedly. Please try again, or open "
            "Settings → Voice to check provider status."
        )
    if model_missing:
        return (
            "Speech-to-text isn't ready yet — the local speech model hasn't "
            "finished downloading, and no OpenAI API key is configured. "
            "Open Settings → Voice to check status or retry the download."
        )
    if package_missing and not openai_ready:
        return (
            "Speech-to-text isn't set up yet — no local speech engine and no "
            "API key configured. Open Settings → Voice to finish setup."
        )
    return (
        "Speech-to-text isn't available right now. Open Settings → Voice to "
        "check provider status and finish setup."
    )


# ── Status (ADR-0185 M4) ────────────────────────────────────────────────


class ProviderStatus(BaseModel):
    """Per-provider status row for the Console voice-status panel."""
    ready: bool = Field(description="Usable right now")
    package_installed: bool = Field(description="Underlying package/binary importable")
    model_present: bool | None = Field(
        None, description="Local model file present on disk; null if not applicable",
    )
    key_configured: bool | None = Field(
        None, description="API key resolvable; null if not applicable",
    )
    detail: str = Field(description="Short, human-readable, non-leaky status line")


class VoiceStatusResponse(BaseModel):
    stt: dict[str, ProviderStatus] = Field(default_factory=dict)
    tts: dict[str, ProviderStatus] = Field(default_factory=dict)


def _safe_provider_status(name: str, info: dict) -> ProviderStatus:
    """Build a ``ProviderStatus`` from a provider's raw status dict without
    ever letting a schema mismatch (missing/extra/wrong-typed key) turn into
    an uncaught ``ValidationError`` — and therefore a real 500 — for the
    whole ``/voice/status`` response. A single malformed entry degrades to a
    safe, honest "status unavailable" row instead of taking every other
    provider's status down with it (ADR-0185 review finding: the same class
    of two-call-sites-silently-disagree drift this repo has hit before).
    """
    try:
        return ProviderStatus(**info)
    except Exception as exc:  # noqa: BLE001
        _log.warning("malformed status for provider %r", name, exc_info=True)
        return ProviderStatus(
            ready=False,
            package_installed=False,
            model_present=None,
            key_configured=None,
            detail=f"status unavailable ({exc.__class__.__name__})",
        )


@router.get("/voice/status", response_model=VoiceStatusResponse)
def voice_status(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> VoiceStatusResponse:
    """Per-provider STT/TTS readiness for the Voice settings page.

    Cheap introspection only (package-import checks, model-file
    existence, API-key presence) — never triggers a transcription or a
    speech synthesis call.
    """
    stt_raw: dict[str, dict] = {}
    if _STT_OK:
        try:
            from stt import provider_status as _stt_provider_status  # noqa: PLC0415
            stt_raw = _stt_provider_status()
        except Exception:  # noqa: BLE001
            _log.warning("STT status probe failed", exc_info=True)

    tts_raw: dict[str, dict] = {}
    if _SAY_STATUS_OK and _say_module is not None:
        try:
            tts_raw = _say_module.provider_status()
        except Exception:  # noqa: BLE001
            _log.warning("TTS status probe failed", exc_info=True)

    return VoiceStatusResponse(
        stt={name: _safe_provider_status(name, info) for name, info in stt_raw.items()},
        tts={name: _safe_provider_status(name, info) for name, info in tts_raw.items()},
    )


# ── STT ───────────────────────────────────────────────────────────────


@router.post("/voice/transcribe")
async def voice_transcribe(
    audio: Annotated[UploadFile, File(description="Recorded audio blob")],
    lang: Annotated[str | None, Form(min_length=2, max_length=8)] = None,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
) -> dict[str, Any]:
    if not _STT_OK:
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            "STT module not importable",
        )
    base_ct = _strip_mime_params(audio.content_type)
    if base_ct and base_ct not in _ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"unsupported audio type: {audio.content_type}",
        )

    # Read in bounded chunks and stop the moment the cap is passed. `await
    # audio.read()` materialised the WHOLE upload in RAM and only then measured
    # it, so the 25 MiB cap cost a 2 GB allocation to enforce against a 2 GB
    # POST. The Content-Length middleware (standalone.py) rejects the honest case
    # earlier; this is the backstop for a missing or lying header.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await audio.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_AUDIO_BYTES:
            raise HTTPException(
                http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"audio exceeds {_MAX_AUDIO_BYTES} bytes",
            )
        chunks.append(chunk)
    blob = b"".join(chunks)
    if not blob:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "empty audio")

    # Persist to a tempfile — providers want a path, not a handle.
    suffix = ".webm" if base_ct.endswith("webm") else (
        ".ogg" if base_ct.endswith("ogg") else (
            ".m4a" if "mp4" in base_ct or "m4a" in base_ct else (
                ".mp3" if base_ct.endswith("mpeg") else (
                    ".wav" if "wav" in base_ct else ".bin"
                )
            )
        )
    )
    with tempfile.NamedTemporaryFile(prefix="corvin_stt_", suffix=suffix, delete=False) as fh:
        fh.write(blob)
        path = Path(fh.name)

    t0 = time.monotonic()
    try:
        try:
            # _stt_transcribe is a synchronous, CPU/IO-heavy call (local Whisper
            # budget is up to 120 s, and the very first call on a fresh install may
            # also download the GGML model in-band). Calling it directly on the
            # asyncio loop froze the ENTIRE console — every SSE chat stream, healthz,
            # and other tab — for the duration. Offload to the threadpool so the
            # loop stays responsive (voice_tts is already a sync def for the same
            # reason; this route must stay async for the awaited UploadFile.read()).
            result = await run_in_threadpool(_stt_transcribe, path, lang=lang)
        except STTTimeout as e:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="voice.transcribe",
                target_kind="voice",
                target_id="web",
                reason="timeout",
            )
            _log.warning("STT timeout", exc_info=True)
            raise HTTPException(http_status.HTTP_504_GATEWAY_TIMEOUT, "upstream timeout")
        except (STTProviderUnavailable, STTError) as e:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="voice.transcribe",
                target_kind="voice",
                target_id="web",
                reason="provider-error",
            )
            _log.warning("STT error", exc_info=True)
            # ADR-0185 Decision 4 / Must-NOT: never surface the resolver's raw
            # "no STT provider available; chain=...; failures=..." exception
            # text to the chat transcript — translate it to an actionable,
            # non-leaky message instead. The full exception is already
            # logged above (exc_info=True) for operators.
            raise HTTPException(
                http_status.HTTP_502_BAD_GATEWAY, _stt_unavailable_message(),
            ) from e
    finally:
        try:
            path.unlink()
        except OSError:
            pass

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # ── METADATA-ONLY audit (CLAUDE.md § Layer 23) ────────────────────
    # Text must NEVER appear in any audit field.
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="voice.transcribed",
        target_kind="voice",
        target_id="web",
    )

    return {
        "ok":          True,
        "text":        result.text,
        "lang":        result.lang,
        "provider":    result.provider,
        "elapsed_ms":  elapsed_ms,
        "bytes":       len(blob),
    }


# ── TTS ───────────────────────────────────────────────────────────────


def _resolve_tts_provider() -> str | None:
    """Return the user-configured TTS provider, or None for auto-chain.

    Returns one of: "openai", "edge", "piper", None.
    None means say.py will use its automatic chain (openai → edge → piper).

    The operator-level env var ``CORVIN_TTS_PROVIDER`` takes FINAL precedence
    (over the profile). say.py honours it too, but the in-process OpenAI branch
    in ``_voice_tts_sync`` runs BEFORE say.py — so it MUST be resolved here or a
    ``CORVIN_TTS_PROVIDER=piper`` (or ``edge``) pin would silently ship reply
    text to OpenAI's cloud despite the operator pinning a local provider. Env
    wins over profile; ``auto`` means "no pin, use the auto-chain".
    """
    env_provider = os.environ.get("CORVIN_TTS_PROVIDER", "").strip()
    if env_provider and env_provider != "auto":
        return env_provider
    if env_provider == "auto":
        return None
    if not _PROFILE_OK or _profile_module is None:
        return None
    try:
        profile = _profile_module.load()
        provider = profile.get("tts_provider")
        if isinstance(provider, str) and provider.strip() and provider.strip() != "auto":
            return provider.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_tts_voice(lang: str) -> str | None:
    """Return the user's profile-configured TTS voice for the given language.

    Priority:
      1. tts_voice_<lang-prefix>  (e.g. tts_voice_de, tts_voice_en)
      2. tts_voice                (global fallback voice)
      3. None                     → say.py uses its own language-based default

    This ensures the voice the user selected in the console settings is always
    honoured instead of being overridden by the language-based default in say.py.
    """
    if not _PROFILE_OK or _profile_module is None:
        return None
    try:
        profile = _profile_module.load()
        prefix = lang.lower().split("-")[0]          # "zh-Hans" → "zh"
        lang_key = f"tts_voice_{prefix}"
        voice = profile.get(lang_key) or profile.get("tts_voice")
        if isinstance(voice, str) and voice.strip():
            return voice.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


# ADR-0194 Phase 3 — hard cap on the full read-aloud. One click must never turn a
# pathological wall of text into an unbounded synthesis bill; the client is told the
# real count via X-Corvin-Voice-Segments and stops there.
_MAX_VOICE_SEGMENTS = 24
_TTS_PROVIDER_CHAR_LIMIT = 4000  # OpenAI TTS-1 hard cap is 4096; stay under it
_TTS_SUMMARIZE_MAX_CHARS = 400   # same default build_voice_summary() uses for bridges
# summarize.py's OWN internal budget is CLI (45s) + Hermes (60s) = up to 105s
# worst case (see summarize.py's _SUMMARY_CLI_TIMEOUT_S/_SUMMARY_HERMES_TIMEOUT_S).
# A shorter wrapper timeout here would routinely cut off a legitimate
# in-progress CLI attempt before summarize.py's own fallback chain even runs —
# matches adapter.py::build_voice_summary's identical 120s parent-cap
# convention for the exact same subprocess (bridge/console parity).
_TTS_SUMMARIZE_TIMEOUT_S = float(os.environ.get("CORVIN_TTS_SUMMARIZE_TIMEOUT_S", "120"))


def _resolve_voice_output_language(candidate_text: str) -> str:
    """Resolve the language voice summaries should be generated in.

    Smart Hybrid approach (BUG-1.2 fix — Language-Routing):
    1. If user has explicitly set `display_language` → use that (User FIRST)
    2. If not set → auto-detect from text (Text-First for new users)
    3. If detection fails → fallback to "de" (default)

    Mirrors adapter.py::_resolve_voice_output_language exactly for console parity.
    """
    # ── (1) User preference IF explicitly set ────────────────────────────────
    output_language = ""
    if _PROFILE_OK and _profile_module is not None:
        try:
            raw = _profile_module.load().get("display_language") or ""
            if raw:  # Only if explicitly set by user
                if _I18N_OK and _i18n is not None:
                    output_language = _i18n.normalise(raw)
                    if output_language:
                        return output_language  # User preference is authoritative
        except Exception:  # noqa: BLE001
            pass

    # ── (2) Auto-detect from text (for unseeded profiles) ────────────────────
    if _DETECT_LANG_OK and _detect_confident_de_en is not None:
        detected = _detect_confident_de_en(candidate_text)
        if detected:
            return detected

    # ── (3) Fallback: German default ─────────────────────────────────────────
    return "de"


def _tts_audience_block(lang: str) -> str:
    """The layer-12 listener-profile block for ``summarize.py --audience``,
    resolved exactly like ``adapter.py::build_voice_summary()`` does.

    Returns "" when no audience fields are set (or the profile module is
    unavailable), which makes ``--audience`` be omitted entirely — the
    summarizer then behaves exactly as before.
    """
    if not _PROFILE_OK or _profile_module is None:
        return ""
    try:
        return _profile_module.for_tts_audience(lang) or ""
    except Exception:  # noqa: BLE001 — a broken profile must never break TTS
        return ""


def _summarize_for_speech(text: str, lang: str) -> str | None:
    """Best-effort condensation of *text* into a real, faithful spoken
    summary (learnings/metaphor annex included, per the user's audience
    settings) via ``summarize.py`` — the SAME script the standalone
    ``/voice/summarize`` endpoint and every messenger bridge's
    ``adapter.py::build_voice_summary()`` already use. Returns ``None`` on
    any failure (missing script, timeout, empty output) so the caller can
    fall back to the raw text — this must never break TTS, only improve it.

    PRE-STRIPS code blocks via strip_for_tts.py (--mode code-only) BEFORE
    passing to summarize.py — mirrors the exact preprocessing that
    adapter.py::build_voice_summary() does. Failure to strip falls back to
    raw text (fail-soft). This ensures the LLM-summarizer sees clean prose
    without code-blocks / table-noise that would distract it.

    LANGUAGE ROUTING (BUG-1.2 fix): Uses smart-hybrid language resolution:
    user display_language → auto-detect from text → fallback to de.
    """
    summarize_path = _VOICE_SCRIPTS / "summarize.py"
    stripper_path = _VOICE_SCRIPTS / "strip_for_tts.py"
    if not summarize_path.exists():
        return None

    # Pre-strip code blocks so summarize.py sees clean prose (adapter.py parity).
    cleaned_text = text
    if stripper_path.exists():
        try:
            clean_env = {k: v for k, v in os.environ.items()
                         if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_BASE')}
            pre = subprocess.run(
                [sys.executable, str(stripper_path), "--mode", "code-only"],
                input=text, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=10, check=True, env=clean_env,
            ).stdout.strip()
            # Explicit check: if stripper consumed all content, fall back to raw text.
            if pre:
                cleaned_text = pre
            else:
                _log.debug("voice_tts: strip_for_tts returned empty — using raw text")
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as e:
            _log.debug("voice_tts: strip_for_tts failed (%s) — using raw text", type(e).__name__)
            # Fall through to raw text
            pass

    # BUG-1.2 FIX: Smart-hybrid language resolution (User pref → Auto-detect → de fallback)
    resolved_lang = _resolve_voice_output_language(cleaned_text)

    cmd = [sys.executable, str(summarize_path),
           "--lang", resolved_lang if resolved_lang in ("de", "en") else "de",
           "--max-chars", str(_TTS_SUMMARIZE_MAX_CHARS)]
    # The docstring above promised the annex "per the user's audience settings"
    # since this helper was written, but --audience was never actually passed:
    # the console voice has therefore NEVER spoken the LERN-ZUGABE / metaphor
    # annex, while every messenger bridge did (adapter.py::build_voice_summary
    # passes it). Same profile, same block, same summarizer — bridge parity.
    #
    # 2026-07-24: use resolved_lang everywhere, not the frontend `lang` param.
    # The audience block and the OUTPUT-LANGUAGE pin were keyed off `lang`
    # (body.lang) while --lang was keyed off resolved_lang (server-side profile
    # resolution) — the two could disagree, so the summary language and the
    # audience-block language could drift apart.
    audience = _tts_audience_block(resolved_lang)
    if audience:
        cmd += ["--audience", audience]
    # Always pin the output language — de/en included. summarize.py now emits an
    # explicit OUTPUT-LANGUAGE directive for every locale, so this is what
    # guarantees "always the profile language" even when the answer text is in a
    # different language. Previously omitted for de/en, which let an English
    # answer be summarised — and spoken — in English for a German-pinned user.
    #
    # Precedence: the profile-resolved language wins (resolved_lang prefers an
    # explicit profile pin). If that only yielded a de/en base (no pin / weak
    # detect) but the frontend passed an explicit non-de/en locale, honour that
    # so multi-language users don't regress to the pivot.
    out_lang = resolved_lang or "de"
    if out_lang in ("de", "en") and lang and lang.split("-")[0] not in ("de", "en"):
        out_lang = lang
    cmd += ["--output-language", out_lang]
    try:
        proc = subprocess.run(
            cmd,
            input=cleaned_text, capture_output=True, text=True,
            timeout=_TTS_SUMMARIZE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        _log.warning("voice_tts: summarize.py timed out after %.0fs — "
                     "speaking the raw (truncated) text instead",
                     _TTS_SUMMARIZE_TIMEOUT_S)
        return None
    except OSError as exc:
        _log.warning("voice_tts: could not start summarize.py (%s) — "
                     "speaking the raw (truncated) text instead", exc)
        return None
    if proc.returncode != 0:
        _log.warning("voice_tts: summarize.py exited %d — speaking the raw "
                     "(truncated) text instead. stderr tail: %s",
                     proc.returncode, proc.stderr.strip()[-400:])
        return None
    summary = proc.stdout.strip()
    if not summary:
        return None
    if "[summarize] degraded:" in proc.stderr:
        _log.info("voice_tts: summarize.py used its degraded (near-verbatim) "
                  "fallback this turn — both LLM backends were unavailable.")
    return summary


class TtsRequest(BaseModel):
    # No max_length here — the handler truncates to _TTS_PROVIDER_CHAR_LIMIT so
    # long responses (e.g. code blocks) degrade gracefully instead of returning
    # a 422. For long responses the caller should pre-summarize via /voice/summarize.
    text: str = Field(..., min_length=1, max_length=50000)
    # Any BCP-47 code is accepted (e.g. "de", "en", "zh", "ja", "fr").
    lang: str = Field("de", min_length=2, max_length=10,
                      pattern=r"^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{1,8})*$")
    # ADR-0194 Phase 1 — when the caller names its chat session, the synthesised
    # audio is ARCHIVED into that session's workdir instead of being thrown away,
    # so the turn keeps a replayable <audio> player. Optional: every other caller
    # (e.g. the first-boot greeting in SetupGate) simply omits it and gets today's
    # behaviour. Pattern-bounded because it becomes a path component downstream.
    sid: str | None = Field(None, min_length=1, max_length=128,
                            pattern=r"^[A-Za-z0-9_-]+$")
    model_config = {"extra": "forbid"}


# ADR-0194 Phase 1 — archive the turn's spoken audio into the session workdir.
# say.py ALREADY wrote an audio file; this route used to read the bytes and delete
# it. Keeping a copy under <workdir>/voice/<key>.<ext> makes it an ordinary chat
# artifact for free: served inline by the workdir route, rendered as a real <audio>
# player by ArtifactCard, rehydrated on reload, and erased with the session
# (Layer 33/36). Costs no extra synthesis and no turn latency — this route runs
# AFTER the turn's `done`.
_AUDIO_EXT_BY_MIME = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/flac": ".flac",
}


def _say_cmd(out_path: "Path", text: str, lang: str) -> list[str]:
    """Build say.py's argv — the ONE definition of a subtle positional contract.

    say.py takes `<out_path> <text> [<lang> [<voice> [<provider>]]]`. The voice slot
    must ALWAYS be present (empty string = "use the default") or a pinned provider
    slides into argv[4] and is silently read as a voice NAME.

    The text is scrubbed of NUL and other C0 control characters (tab/newline
    excepted) because they cannot travel through argv at all: subprocess raises
    ValueError("embedded null byte") BEFORE exec. voice_tts catches only
    TimeoutExpired, so that escaped as a 500 and the frontend rendered a red
    "TTS failed" banner — breaking the deliberate design (204-on-failure
    everywhere else) that a TTS problem never surfaces as an error to the user.
    Control characters are unspeakable anyway; dropping them loses nothing.
    """
    safe_text = "".join(
        ch for ch in text
        if ch in "\t\n" or (ord(ch) >= 0x20 and ord(ch) != 0x7F)
    )
    voice = _resolve_tts_voice(lang)
    provider = _resolve_tts_provider()
    cmd = [sys.executable, str(_VOICE_SCRIPTS / "say.py"),
           str(out_path), safe_text, lang, voice or ""]
    if provider:
        cmd.append(provider)
    return cmd


# The OpenAI TTS-1 voice names the in-process branch accepts verbatim. A
# profile ``tts_voice`` that is NOT one of these (e.g. an edge neural voice
# like "de-DE-KatjaNeural") cannot be honoured by OpenAI — the branch then
# falls back to "nova" rather than sending a doomed request. say.py's own
# ``_openai_voice_for`` passes any override verbatim; the closed set here is
# deliberately stricter because an in-process API error would burn the
# timeout budget before the say.py chain even starts.
_OPENAI_TTS_VOICES = frozenset({
    "alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer",
})

# V6: once-per-process dedup for the in-process OpenAI TTS failure WARNING —
# a permanently dead PAID tier was invisible at DEBUG, but warning on every
# turn would spam the log. Benign data race under the threadpool: worst case
# is one extra WARNING.
_openai_tts_warned_once = False


def _try_openai_tts(text: str, lang: str, voice: "str | None") -> "bytes | None":
    """Direct OpenAI TTS call, in-process. Returns audio bytes or None on failure.

    Mirrors ``adapter.py::_try_openai_tts`` (the messenger bridges' twin):

    * **CORVIN_TTS_LOCAL_ONLY=1 disables it entirely** — OpenAI TTS ships the
      reply text to OpenAI's cloud, which is forbidden under the EU local-only
      egress guarantee (L35 / EU_PRODUCTION). Returning None falls through to
      say.py, which enforces the same flag on its openai AND edge tiers.
    * **timeout + no retries** — without these the SDK defaults to 600 s with
      retries; a degraded network would park the request threadpool worker in
      TTS for minutes before say.py's chain is even attempted.
    * *text* must be the SUMMARIZED speech text and *voice* the profile-resolved
      voice — the caller (``_voice_tts_sync``) runs ``_summarize_for_speech`` and
      ``_resolve_tts_voice`` first, so this branch speaks exactly what the
      say.py chain would speak.

    Best-effort only — any failure returns None and must never break TTS.
    """
    # EU local-only egress guarantee — checked FIRST, before any key resolution,
    # exactly like the adapter twin (adapter.py::_try_openai_tts).
    if os.environ.get("CORVIN_TTS_LOCAL_ONLY") == "1":
        return None

    try:
        import openai as openai_module
    except ImportError:
        return None  # openai package not installed, skip to say.py

    try:
        import provider_keys as _pk  # noqa: PLC0415
        key = _pk.resolve_key("tts_openai_api_key")
    except Exception:  # noqa: BLE001
        return None

    if not (key or "").strip():
        return None  # no key configured, skip to say.py

    # Honour the user's profile voice when it names a real OpenAI voice;
    # anything else (edge/piper voice names, unset) falls back to "nova".
    openai_voice = (voice or "").strip().lower()
    if openai_voice not in _OPENAI_TTS_VOICES:
        openai_voice = "nova"

    try:
        client = openai_module.OpenAI(
            api_key=key, timeout=_OPENAI_TTS_TIMEOUT_S, max_retries=0,
        )
        response = client.audio.speech.create(
            model="tts-1",
            voice=openai_voice,
            input=text[:_TTS_PROVIDER_CHAR_LIMIT],  # OpenAI TTS-1 hard cap 4096
            speed=1.0,
        )
        return response.content
    except Exception as e:  # noqa: BLE001
        # WARNING once per process, DEBUG afterwards (V6). CONTENT-FREE:
        # type + HTTP status only — str(e) can embed the request payload,
        # i.e. the text being spoken (compliance: no PII/prompt in logs).
        global _openai_tts_warned_once
        level = logging.DEBUG if _openai_tts_warned_once else logging.WARNING
        _openai_tts_warned_once = True
        _log.log(level, "in-process OpenAI TTS failed (will try say.py): %s status=%s",
                 type(e).__name__, getattr(e, "status_code", ""))
        return None


def _say_env() -> dict[str, str]:
    """os.environ plus the OpenAI TTS/STT keys resolved through the console's OWN
    canonical resolver, injected under the env-var names say.py reads.

    Why this matters — the reported "I saved an OpenAI key and TTS still fails":
    say.py resolves its key from its OWN environment and, failing that, from
    ``VOICE_CONFIG_DIR/service.env`` — where VOICE_CONFIG_DIR is derived from
    say.py's OWN HOME/XDG_CONFIG_HOME. When the console runs under systemd with a
    different HOME/XDG than the shell that BYOK wrote the key from, say.py's
    fallback reads the WRONG service.env, finds no key, and silently skips OpenAI
    (→ edge/piper → possibly 204 "unavailable"). The console, in contrast,
    resolves the key against the RIGHT service_env_path. Handing say.py the value
    directly removes its dependency on re-deriving that path in a child process.

    Only fills a name the environment does NOT already carry, so an operator's
    explicit env-var export is never overridden. Best-effort: a resolver failure
    must never break TTS, so it just leaves the env as-is.
    """
    env = dict(os.environ)
    try:
        import provider_keys as _pk  # noqa: PLC0415 — bridges/shared, on sys.path
    except Exception:  # noqa: BLE001
        return env
    # say.py reads CORVIN_TTS_OPENAI_KEY / OPENAI_API_KEY; whisper reads
    # CORVIN_STT_OPENAI_KEY / OPENAI_API_KEY. Resolve the dedicated names.
    for key_name, env_var in (("tts_openai_api_key", "CORVIN_TTS_OPENAI_KEY"),
                              ("stt_openai_api_key", "CORVIN_STT_OPENAI_KEY")):
        if (env.get(env_var) or "").strip():
            continue  # an explicit export wins — never clobber it
        try:
            val = _pk.resolve_key(key_name)
        except Exception:  # noqa: BLE001
            val = None
        if val:
            env[env_var] = val
    return env


def _tts_failed_response(proc: "subprocess.CompletedProcess[str]", stage: str) -> Response:
    """Degrade to 204 (playback skipped), but STOP throwing away why.

    say.py writes the concrete per-provider reason to stderr — "no OPENAI_API_KEY",
    "openai package not installed", "OpenAI TTS failed: <API error>", "edge-tts:
    <network error>". The console used to discard it, so a user who "saved a key
    and TTS still doesn't work" — and whoever debugs it — had nothing to go on but
    a generic banner. The reason now lands in the log AND on an
    ``X-Corvin-Voice-Reason`` header (204 stays 204, so the silent-degradation UX
    is unchanged; the header is diagnostic only, never rendered as an error).
    """
    tail = (proc.stderr or "").strip().splitlines()
    reason = tail[-1][:200] if tail else f"no diagnostic ({stage})"
    _log.warning("voice_tts: no audio (%s) — say.py said: %s", stage,
                 " | ".join(t.strip() for t in tail[-4:]) or "(silent)")
    # HTTP headers are latin-1 only (Starlette encodes strictly): a stderr
    # line carrying '…', '→' or CJK raised UnicodeEncodeError and turned
    # the designed silent-204 degradation into a 500. Replace instead.
    reason = reason.encode("latin-1", "replace").decode("latin-1")
    return Response(status_code=http_status.HTTP_204_NO_CONTENT,
                    headers={"X-Corvin-Voice-Reason": reason})


def _cleanup_tts_tmp(out_path: "Path") -> None:
    """Unlink a say.py temp target AND its ``.wav`` sibling. Best-effort.

    say.py's Piper tier synthesizes into ``out_path.with_suffix(".wav")``
    before replacing it onto ``out_path``; when the outer subprocess timeout
    SIGKILLs say.py mid-synthesis that sibling survives. The old ``finally``
    blocks unlinked only the ``.opus`` target, so ``corvin_tts_*.wav`` files
    accumulated in the tempdir (V2b, review 2026-07-20).
    """
    for p in (out_path, out_path.with_suffix(".wav")):
        try:
            p.unlink()
        except OSError:
            pass


def _serve_tts_response(rec: session_auth.SessionRecord, body: TtsRequest,
                        data: bytes, provider: str) -> Response:
    """Format TTS response consistently (mime type detection, archiving, audit)."""
    mime = _detect_audio_mime(data)
    _voice_file = (_persist_turn_voice(rec.tenant_id, body.sid, body.text, data, mime)
                   if body.sid else None)
    _headers = {"Content-Length": str(len(data)),
                "X-Corvin-Lang": body.lang,
                "X-Corvin-TTS-Format": mime,
                "X-Corvin-TTS-Provider": provider}
    if _voice_file:
        _headers["X-Corvin-Voice-File"] = _voice_file

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="voice.tts",
        target_kind="voice",
        target_id="web",
    )
    return Response(content=data, media_type=mime, headers=_headers)


def _persist_turn_voice(tenant_id: str, sid: str, text: str,
                        data: bytes, mime: str, suffix: str = "") -> "str | None":
    """Write this turn's audio into the session's voice archive. Best-effort.

    The extension follows the SNIFFED mime, never say.py's argv suffix: say.py
    emits OGG-Opus (OpenAI) / MP3 (edge) / WAV (piper) and does NOT transcode, so
    a hard-coded ".opus" would hand the browser a mislabelled container.

    Archiving must never break playback — any failure returns None and the caller
    still serves the audio.
    """
    try:
        from .. import chat_runtime as _cr  # noqa: PLC0415 — avoid import cycle at module load
        # Only archive into a session that actually exists; never create stray dirs
        # for an unknown/expired sid.
        if not _cr._workdir(tenant_id, sid).exists():
            return None
        vdir = _cr.voice_dir(tenant_id, sid)
        vdir.mkdir(parents=True, exist_ok=True)
        dest = vdir / f"{_cr.voice_key(text)}{suffix}{_AUDIO_EXT_BY_MIME.get(mime, '.ogg')}"
        # The tmp name must be unique PER WRITE, not per process. It was a pure
        # function of (tenant, sid, key, suffix, mime), so two concurrent
        # writers of the SAME segment (a double-click, or the client's
        # play-N/fetch-N+1 pipeline retrying) opened the same .tmp in "wb" with
        # independent offsets and replace() atomically published a torn file —
        # replace() is atomic only if the SOURCE is private. A pid suffix does
        # NOT fix it: voice_tts/voice_segment are sync `def`s, so FastAPI runs
        # them in its threadpool — the colliding writers are threads of the SAME
        # process and share the pid. uuid4 is unique per call.
        tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_bytes(data)
            tmp.replace(dest)  # atomic — a half-written file must never be served
        except Exception:
            # Don't leave the partial behind on ENOSPC/EIO: a stale .tmp is
            # picked up by nothing now (the finders skip it) but it would sit
            # in the session dir forever.
            tmp.unlink(missing_ok=True)
            raise
        # Keep the session's archive under its ceiling (GDPR Art. 5(1)(e)); an
        # evicted turn simply loses its player and re-synthesises on replay.
        # keep=dest.name so a mis-set tiny cap can't delete the audio we just
        # made — that would burn a synthesis per turn and never show a player.
        _cr.prune_voice_archive(tenant_id, sid, keep=dest.name)
        return dest.name
    except Exception:  # noqa: BLE001
        return None


@router.post("/voice/tts")
async def voice_tts(
    body: TtsRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Response:
    """Bounded async wrapper — see _TTS_MAX_CONCURRENCY. The synthesis itself is
    unchanged and still runs in the threadpool, but only once a slot is free."""
    resp = await _run_with_tts_slot(
        _voice_tts_sync, body, rec,
        no_slot_log=(
            f"voice_tts: no synthesis slot within {_TTS_SLOT_WAIT_S:.0f}s "
            f"({_TTS_MAX_CONCURRENCY} concurrent) — skipping playback for this turn"
        ),
    )
    # Live-attach the just-archived player onto the open chat WS (ADR-0194
    # live-replay) — additive to, and independent of, the ephemeral playTts()
    # auto-speak-once path above; runs back on the event loop (not the sync
    # threadpool worker), so it's safe to touch the asyncio-based pub/sub here.
    _publish_voice_live_event(rec.tenant_id, body.sid, resp, label="voice")
    return resp


def _voice_tts_sync(
    body: TtsRequest,
    rec: session_auth.SessionRecord,
) -> Response:
    """TTS pipeline: summarize → resolve provider + voice → OpenAI in-process
    when the resolved provider is OpenAI (pinned "openai", or no pin — say.py's
    auto-chain leads with openai anyway) → say.py subprocess fallback.

    Why the in-process OpenAI branch exists at all: say.py shells out to system
    Python which may not have pip installed. Calling OpenAI directly avoids that
    fragility (and the fork overhead). But it is a PARITY branch, not a shortcut:
    it runs AFTER summarization and provider/voice resolution, so it speaks the
    same condensed summary with the same resolved voice the say.py chain would —
    and it is skipped entirely when the user pinned a non-OpenAI provider
    (e.g. piper) or CORVIN_TTS_LOCAL_ONLY=1 forbids cloud egress (see
    _try_openai_tts). On any failure the say.py chain (openai → edge → piper,
    with its own local-only enforcement) runs unchanged.
    """
    # Speak a real, condensed summary (learnings/metaphor annex included, same
    # as every messenger bridge via adapter.py::build_voice_summary) instead of
    # the raw answer text — see _summarize_for_speech's docstring for why this
    # was previously missing here. Falls back to a blind truncation at the
    # provider character limit (OpenAI TTS-1: 4096 chars, edge-tts: ~8000) if
    # summarization is unavailable or fails; this must never block TTS.
    # ADR-0194 LIC-VOICETTS-SPAWN-01: this route spawns the SAME paid Haiku
    # `claude -p` that /voice/summarize meters with enforce_chat_turns — and it
    # was not metered at all, so the gate was one endpoint away from being routed
    # around. Charged on the voice axis, NOT chat's: this route runs
    # automatically once per turn, so the chat axis would bill every turn twice.
    # Unlimited on every tier today, so this is a no-op until a tier says
    # otherwise. Before the summarize spawn, fail-closed.
    from ._compute_license_gate import enforce_voice_summaries  # noqa: PLC0415
    enforce_voice_summaries(
        rec.tenant_id, rec.sid_fingerprint, audit_action="voice.tts",
    )

    # summarize.py now BOUNDS even its degraded (no-LLM) fallback to the spoken
    # budget (2026-07-24) — it can no longer return the whole answer, so the
    # common path is always a short summary. The `or body.text` branch is the
    # last-ditch case where summarize.py could not run at all (spawn failure);
    # even then we must not read the whole answer word-for-word, so it is bounded
    # to a spoken size here rather than clamped only at the 4096 provider limit.
    _summary = _summarize_for_speech(body.text, body.lang)
    if _summary:
        tts_text = _summary[:_TTS_PROVIDER_CHAR_LIMIT]
    else:
        # No summary at all — bound the raw answer to roughly the spoken budget
        # at a sentence boundary instead of speaking up to 4096 raw chars.
        _raw = " ".join((body.text or "").split())
        _cut = _raw[: _TTS_SUMMARIZE_MAX_CHARS * 2]
        _dot = max(_cut.rfind(". "), _cut.rfind("! "), _cut.rfind("? "))
        tts_text = (_cut[: _dot + 1] if _dot > 80 else _cut).strip()

    # Resolve the user's pins ONCE, before choosing a synthesis path. Before
    # this restructure the OpenAI branch ran FIRST — on raw un-summarized text,
    # with a hardcoded "nova" voice, ignoring a pinned tts_provider (a piper pin
    # still went to OpenAI's cloud) and ignoring CORVIN_TTS_LOCAL_ONLY (an L35
    # violation under EU local-only deployments).
    provider = _resolve_tts_provider()
    voice = _resolve_tts_voice(body.lang)

    # In-process OpenAI only when the resolved provider IS OpenAI: an explicit
    # "openai" pin, or no pin at all — say.py's auto-chain leads with openai, so
    # with a key configured this is the same tier without the fork overhead.
    # Any other pin (piper, edge) must reach say.py untouched.
    if provider in (None, "openai"):
        _tts_data = _try_openai_tts(tts_text, body.lang, voice)
        if _tts_data is not None:
            return _serve_tts_response(rec, body, _tts_data, "openai")

    # Fallback: say.py (openai, edge-tts, piper, or degraded)
    say_path = _VOICE_SCRIPTS / "say.py"
    if not say_path.exists():
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            "say.py not found")

    # Fallback to say.py subprocess (edge-tts, piper)
    with tempfile.NamedTemporaryFile(prefix="corvin_tts_", suffix=".opus", delete=False) as fh:
        out_path = Path(fh.name)

    try:
        cmd = _say_cmd(out_path, tts_text, body.lang)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, env=_say_env(),
            timeout=_TTS_TIMEOUT_S,
        )
        if proc.returncode != 0:
            console_audit.action_failed(
                tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
                action="voice.tts", target_kind="voice", target_id="web",
                reason="say-exit-nonzero",
            )
            return _tts_failed_response(proc, "say-exit-nonzero")

        size = out_path.stat().st_size if out_path.exists() else 0
        if size == 0 or not proc.stdout.strip():
            return _tts_failed_response(proc, "all-providers-failed")
        data = out_path.read_bytes()
    except subprocess.TimeoutExpired as exc:
        console_audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="voice.tts", target_kind="voice", target_id="web",
            reason="timeout",
        )
        # V1: this was the ONE degrade path returning a bare 204 without the
        # X-Corvin-Voice-Reason diagnostic surface. Route it through
        # _tts_failed_response with a synthetic "timeout" line appended to
        # whatever say.py managed to write to stderr before the kill.
        stderr_txt = exc.stderr or ""
        if isinstance(stderr_txt, (bytes, bytearray)):
            stderr_txt = stderr_txt.decode("utf-8", "replace")
        stderr_txt = ((stderr_txt.rstrip() + "\n") if stderr_txt.strip() else "") \
            + f"timeout: say.py exceeded {_TTS_TIMEOUT_S:g}s"
        return _tts_failed_response(
            subprocess.CompletedProcess(cmd, -1, stdout="", stderr=stderr_txt),
            "say-timeout",
        )
    finally:
        _cleanup_tts_tmp(out_path)

    return _serve_tts_response(rec, body, data, "say.py")


# ── Session recap — a spoken recap of a WHOLE session, not one turn ─────────
# User-requested feature: a button next to the voice-replay controls that
# recaps the session's goal/method/current-state, understandable rather than
# theoretical, and that DELIBERATELY comes back worded differently every time
# it's pressed (rotating "angle" below) — the opposite of every other voice
# endpoint's determinism convention, and intentionally so; see
# operator/voice/scripts/summarize.py's generate_session_recap() docstring.

_SESSION_RECAP_MAX_CHARS = 700
# Budget for the built User:/Assistant: transcript fed to the LLM — well
# under SummarizeRequest's 20000-char precedent, since a session recap only
# needs the SHAPE of the conversation (goal, method, outcome), not every
# word of every turn.
_SESSION_RECAP_TRANSCRIPT_BUDGET = 20000

_SESSION_RECAP_ANGLES_DE = [
    "Beginne mit dem eigentlichen Ziel der Session und ob es erreicht wurde.",
    "Beginne mit der Methode bzw. dem Vorgehen, das benutzt wurde.",
    "Beginne mit dem größten Fortschritt oder der wichtigsten Erkenntnis.",
    "Beginne mit einer überraschenden Wendung oder einem Umweg im Verlauf.",
    "Beginne mit dem aktuellen Stand — wo die Sache gerade steht.",
]
_SESSION_RECAP_ANGLES_EN = [
    "Start with the actual goal of the session and whether it was reached.",
    "Start with the method or approach that was used.",
    "Start with the biggest progress made or the key insight.",
    "Start with a surprising turn or detour along the way.",
    "Start with the current state — where things stand right now.",
]


class SessionSummaryRequest(BaseModel):
    sid: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    lang: str = Field("de", min_length=2, max_length=10,
                      pattern=r"^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{1,8})*$")
    model_config = {"extra": "forbid"}


def _build_session_transcript(tenant_id: str, sid: str, *,
                              budget: int = _SESSION_RECAP_TRANSCRIPT_BUDGET) -> str:
    """User:/Assistant: transcript of the whole session, for the recap LLM
    call — NOT for display anywhere. Goals are usually stated in the first
    exchange and the current state in the most recent one, so when the full
    transcript doesn't fit the budget, the first exchange and as much of the
    tail as fits are kept; the middle (the least essential part for a recap)
    is what gets dropped, never the start or the end.
    """
    from .. import chat_runtime as _cr  # noqa: PLC0415 — avoid import cycle at module load
    turns = _cr.read_turns(tenant_id, sid)
    lines: list[str] = []
    for turn in turns:
        role = turn.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _cr._turn_text(turn).strip()
        if not text:
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {text}")
    if not lines:
        return ""
    full = "\n\n".join(lines)
    if len(full) <= budget:
        return full
    # Head lines are clamped to a quarter of the budget each: an oversized
    # first message (a large paste) used to consume the whole budget — or blow
    # straight past it, since `remaining` went negative and only the TAIL was
    # dropped. The recap then covered only the first exchange (the "current
    # state" the docstring promises never to drop was gone), and a >128 KiB
    # head crashed summarize.py's `claude -p <payload>` argv spawn (E2BIG).
    head = [ln[: budget // 4] for ln in lines[:2]]
    head_text = "\n\n".join(head)
    remaining = budget - len(head_text) - 20
    tail_lines: list[str] = []
    tail_len = 0
    for line in reversed(lines[2:]):
        if tail_len + len(line) > remaining:
            break
        tail_lines.insert(0, line)
        tail_len += len(line) + 2
    return head_text + "\n\n[...]\n\n" + "\n\n".join(tail_lines)


@router.post("/voice/session-summary")
async def voice_session_summary(
    body: SessionSummaryRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Response:
    """Two-phase: the summarize LLM call runs OUTSIDE the synthesis slot.

    The summarize alone may take up to _TTS_SUMMARIZE_TIMEOUT_S (120 s); a
    recap holding one of the _TTS_MAX_CONCURRENCY slots through it meant a
    few parallel recap clicks could starve every concurrent turn's
    /voice/tts past its 20 s slot wait — silent 204s for the automatic turn
    voice. Only the say.py phase actually contends for TTS resources, so
    only it takes a slot (mirroring voice_tts's semaphore gating). The
    summarize phase is NOT unbounded either — it holds one of
    _RECAP_SUMMARIZE_MAX_CONCURRENCY recap slots so parallel clicks cannot
    drain the shared anyio threadpool (see the semaphore's comment).
    """
    try:
        await asyncio.wait_for(_get_recap_semaphore().acquire(), _TTS_SLOT_WAIT_S)
    except asyncio.TimeoutError:
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)
    try:
        text_result = await run_in_threadpool(_voice_session_summary_text, body, rec)
    finally:
        _get_recap_semaphore().release()
    if isinstance(text_result, Response):
        return text_result
    recap_text, lang = text_result
    return await _run_with_tts_slot(
        _voice_session_summary_tts, body, rec, recap_text, lang)


def _voice_session_summary_sync(
    body: SessionSummaryRequest,
    rec: session_auth.SessionRecord,
) -> Response:
    """Both phases in one synchronous call. The slot seam lives in the async
    route above — this composition is the full pipeline for tests and any
    future sync caller."""
    text_result = _voice_session_summary_text(body, rec)
    if isinstance(text_result, Response):
        return text_result
    return _voice_session_summary_tts(body, rec, *text_result)


def _voice_session_summary_text(
    body: SessionSummaryRequest,
    rec: session_auth.SessionRecord,
) -> "Response | tuple[str, str]":
    from .. import chat_runtime as _cr  # noqa: PLC0415 — avoid import cycle at module load

    if _cr.get_session(rec.tenant_id, body.sid) is None:
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    # Same voice-axis metering as /voice/tts and /voice/summarize — this is
    # the SAME paid Haiku claude -p spawn class, just given a whole
    # transcript instead of one reply. See ADR-0194 LIC-VOICETTS-SPAWN-01.
    from ._compute_license_gate import enforce_voice_summaries  # noqa: PLC0415
    enforce_voice_summaries(
        rec.tenant_id, rec.sid_fingerprint, audit_action="voice.session_summary",
    )

    transcript = _build_session_transcript(rec.tenant_id, body.sid)
    if not transcript:
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    summarize_path = _VOICE_SCRIPTS / "summarize.py"
    if not summarize_path.exists():
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    # `lang` only picks which of the two hand-written angle templates
    # (de/en) supplies the leading hook; it is NOT the caller's requested
    # locale. body.lang (validated BCP-47-shaped by SessionSummaryRequest)
    # is that locale — passed through unmodified below via --output-language
    # (mirrors _summarize_for_speech's identical split) and to _say_cmd for
    # TTS voice selection. Collapsing body.lang itself to de/en here (as this
    # endpoint used to) silently spoke every zh-Hans/fr/ja session recap in
    # German — the templates' own hardcoded language (found 2026-07-16).
    lang = body.lang if body.lang in ("de", "en") else "de"
    angle = random.choice(_SESSION_RECAP_ANGLES_DE if lang == "de" else _SESSION_RECAP_ANGLES_EN)

    cmd = [sys.executable, str(summarize_path),
           "--session-recap-mode",
           "--lang", lang,
           "--max-chars", str(_SESSION_RECAP_MAX_CHARS),
           "--angle", angle]
    if body.lang and body.lang not in ("de", "en"):
        cmd += ["--output-language", body.lang]
    try:
        proc = subprocess.run(
            cmd,
            input=transcript,
            capture_output=True, text=True,
            timeout=_TTS_SUMMARIZE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        console_audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="voice.session_summary", target_kind="voice", target_id="web",
            reason="timeout",
        )
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    if proc.returncode != 0 or not proc.stdout.strip():
        console_audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="voice.session_summary", target_kind="voice", target_id="web",
            # rc=0 + empty stdout is the "both recap LLM backends unavailable"
            # path (generate_session_recap returns "") — labelling it
            # exit-nonzero sent the operator debugging the wrong thing.
            reason=("summarize-exit-nonzero" if proc.returncode != 0
                    else "summarize-empty-output"),
        )
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    return proc.stdout.strip()[:_TTS_PROVIDER_CHAR_LIMIT], lang


def _voice_session_summary_tts(
    body: SessionSummaryRequest,
    rec: session_auth.SessionRecord,
    recap_text: str,
    lang: str,
) -> Response:
    with tempfile.NamedTemporaryFile(prefix="corvin_tts_", suffix=".opus", delete=False) as fh:
        out_path = Path(fh.name)
    try:
        # body.lang, not the de/en-collapsed `lang` above: _say_cmd resolves the
        # TTS voice from the real locale (same as voice_tts's own _say_cmd call).
        cmd2 = _say_cmd(out_path, recap_text, body.lang)
        proc2 = subprocess.run(cmd2, capture_output=True, text=True,
                               env=_say_env(), timeout=_TTS_TIMEOUT_S)
        if proc2.returncode != 0:
            return Response(status_code=http_status.HTTP_204_NO_CONTENT)
        size = out_path.stat().st_size if out_path.exists() else 0
        if size == 0 or not proc2.stdout.strip():
            return Response(status_code=http_status.HTTP_204_NO_CONTENT)
        data = out_path.read_bytes()
    except subprocess.TimeoutExpired:
        console_audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="voice.session_summary", target_kind="voice", target_id="web",
            reason="tts-timeout",
        )
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)
    finally:
        _cleanup_tts_tmp(out_path)

    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="voice.session_summary", target_kind="voice", target_id="web",
    )
    mime = _detect_audio_mime(data)
    # Deliberately NOT archived via _persist_turn_voice: that helper is keyed
    # by voice_key(text) — a hash of the SOURCE text — so the same content
    # always maps to the same archive slot. A session recap has no stable
    # source text (it's regenerated fresh, worded differently, every click),
    # so there is no stable key to file it under; archiving it under a
    # made-up key would either collide across clicks (overwriting the
    # previous recap) or require a whole second, session-recap-specific
    # archive/pruning/erasure scheme for a lightweight, ephemeral feature
    # that doesn't need permanence. Play once, gone — same UX contract as
    # every other voice failure path (204, never an error banner).
    return Response(content=data, media_type=mime, headers={
        "Content-Length": str(len(data)),
        "X-Corvin-Lang": lang,
        "X-Corvin-TTS-Format": mime,
    })


class VoiceSegmentRequest(BaseModel):
    # The FULL answer text — the server splits it; the client never round-trips
    # segment text, so the split can evolve without a frontend change.
    text: str = Field(..., min_length=1, max_length=200000)
    lang: str = Field("de", min_length=2, max_length=10,
                      pattern=r"^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{1,8})*$")
    sid: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    index: int = Field(0, ge=0, le=_MAX_VOICE_SEGMENTS - 1)
    model_config = {"extra": "forbid"}


@router.post("/voice/segment")
async def voice_segment(
    body: VoiceSegmentRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Response:
    """Bounded async wrapper — see _TTS_MAX_CONCURRENCY. A read-aloud fires one
    of these per segment, so this route is the easier of the two to pile up."""
    resp = await _run_with_tts_slot(
        _voice_segment_sync, body, rec,
        no_slot_log=(
            f"voice_segment: no synthesis slot within {_TTS_SLOT_WAIT_S:.0f}s — "
            "ending the read-aloud playlist here"
        ),
    )
    # Live-attach this segment's player, labelled the same way attach_voice_
    # artifacts numbers it on reload ("voice i/n"), so a mid-playlist reload
    # doesn't relabel a segment that's already visible live.
    _idx_hdr = resp.headers.get("X-Corvin-Voice-Index")
    _total_hdr = resp.headers.get("X-Corvin-Voice-Segments")
    _label = f"voice {int(_idx_hdr) + 1}/{_total_hdr}" if _idx_hdr and _total_hdr else "voice"
    _publish_voice_live_event(rec.tenant_id, body.sid, resp, label=_label)
    return resp


def _voice_segment_sync(
    body: VoiceSegmentRequest,
    rec: session_auth.SessionRecord,
) -> Response:
    """Speak ONE segment of the FULL answer (ADR-0194 Phase 3).

    `/voice/tts` speaks a ≤400-char SUMMARY — by construction a long answer is
    never actually read out. This serves the other rendering: the whole text, split
    by `chat_runtime.split_for_speech`, one segment per request.

    One segment per request is what makes it progressive without inventing any
    concurrency: the client plays segment 0 while fetching segment 1, so audio
    starts in seconds instead of after the whole answer is synthesised — and every
    request stays inside the same bounded say.py budget /voice/tts already uses. No
    background threads, no polling, no job state to reap.

    204 = "no such segment" (index past the end) or "no audio" (provider down) —
    the client stops the playlist. Like /voice/tts, TTS failure is never an error
    banner: it is an optional enhancement, not the task result.
    """
    from .. import chat_runtime as _cr  # noqa: PLC0415 — avoid import cycle at module load

    segments = _cr.split_for_speech(body.text)
    if not segments or body.index >= len(segments):
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)
    total = min(len(segments), _MAX_VOICE_SEGMENTS)
    if body.index >= total:
        # Bounded on purpose: a pathological wall of text must not turn one click
        # into an unbounded synthesis bill. The client is told the real cap via
        # X-Corvin-Voice-Segments and simply stops there.
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    with tempfile.NamedTemporaryFile(prefix="corvin_seg_", suffix=".opus", delete=False) as fh:
        out_path = Path(fh.name)
    try:
        proc = subprocess.run(
            # Clamp exactly like /voice/tts does. split_for_speech is documented
            # to emit an oversized token WHOLE rather than cut it (a sliced URL
            # is unspeakable), so the segmenter's own cap is explicitly allowed
            # to be exceeded — which left this route with no guard at all. A
            # segment past the provider limit makes say.py exit non-zero -> 204,
            # and playFull reads 204 as end-of-playlist, so ONE oversized
            # segment silently truncates the whole read-aloud from there on.
            _say_cmd(out_path, segments[body.index][:_TTS_PROVIDER_CHAR_LIMIT], body.lang),
            capture_output=True, text=True, env=_say_env(), timeout=_TTS_TIMEOUT_S,
        )
        # `not proc.stdout.strip()` is say.py's DOCUMENTED failure signal
        # ("0 + empty stdout -> silently disabled / all providers failed", say.py
        # module docstring) and it had zero consumers. It matters because a
        # provider can fail AFTER creating the file: _try_edge's exception path
        # returns False without unlinking out_path, so a partial clip survives
        # with a non-zero size and rc=0 — and was served as a successful
        # synthesis AND archived into the session for replay.
        if (proc.returncode != 0 or not proc.stdout.strip()
                or not out_path.exists() or out_path.stat().st_size == 0):
            return Response(status_code=http_status.HTTP_204_NO_CONTENT)
        data = out_path.read_bytes()
    except subprocess.TimeoutExpired:
        console_audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="voice.segment", target_kind="voice", target_id="web",
            reason="timeout",
        )
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)
    finally:
        _cleanup_tts_tmp(out_path)

    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="voice.segment", target_kind="voice", target_id="web",
    )
    mime = _detect_audio_mime(data)
    # Archived alongside the summary under the SAME turn key, suffixed by index, so
    # the whole read-aloud is replayable later and dies with the session.
    name = _persist_turn_voice(rec.tenant_id, body.sid, body.text, data, mime,
                               suffix=f"-f{body.index:02d}")
    headers = {"Content-Length": str(len(data)),
               "X-Corvin-Lang": body.lang,
               "X-Corvin-TTS-Format": mime,
               "X-Corvin-Voice-Segments": str(total),
               "X-Corvin-Voice-Index": str(body.index)}
    if name:
        headers["X-Corvin-Voice-File"] = name
    return Response(content=data, media_type=mime, headers=headers)


# ── Voice Summarize ──────────────────────────────────────────────────────────

class SummarizeRequest(BaseModel):
    """Summarize a response for TTS playback."""
    text: str = Field(..., min_length=1, max_length=20000)
    lang: str = Field(default="de", description="Language: 'de' or 'en'")
    max_chars: int = Field(default=400, ge=100, le=2000)


class SummarizeResponse(BaseModel):
    """Summarized text for voice output."""
    summary: str
    original_len: int
    summary_len: int


@router.post("/voice/summarize", response_model=SummarizeResponse)
def voice_summarize(
    body: SummarizeRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> SummarizeResponse:
    """Summarize response text for voice output (Layer 12 voice summary).

    Takes a full response, generates a TTS-friendly summary using Claude,
    and returns the shortened version so the user can hear the key points
    instead of a full transcript.
    """
    summarize_path = _VOICE_SCRIPTS / "summarize.py"
    if not summarize_path.exists():
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            "summarize.py not found"
        )

    # ADR-0150 LIC-VOICESUMM-SPAWN-01: voice summarize spawns a paid Haiku
    # `claude -p` (plus an optional dialectic-judge second spawn = up to 2x).
    # Metered before the subprocess, fail-closed.
    #
    # ADR-0194: moved from the chat_turns_per_day axis to the voice axis. /voice/tts
    # spawns the IDENTICAL summarizer and could not be put on the chat axis (it runs
    # automatically once per turn — charging it there would bill every chat turn
    # twice), so a chat-axis gate here meant the two endpoints had different meters
    # and the cheaper one was simply a way around this one. Same spend, same axis.
    from ._compute_license_gate import enforce_voice_summaries  # noqa: PLC0415
    enforce_voice_summaries(
        rec.tenant_id, rec.sid_fingerprint, audit_action="voice.summarize",
    )

    # Validate language
    if body.lang not in ("de", "en"):
        body.lang = "de"

    try:
        # Call summarize.py with stdin input
        proc = subprocess.run(
            [
                sys.executable,
                str(summarize_path),
                "--lang", body.lang,
                "--max-chars", str(body.max_chars),
            ],
            input=body.text,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if proc.returncode != 0:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="voice.summarize",
                target_kind="voice",
                target_id="web",
                reason="summarize-exit-nonzero",
            )
            raise HTTPException(
                http_status.HTTP_502_BAD_GATEWAY,
                f"summarize failed (rc={proc.returncode})"
            )

        summary = proc.stdout.strip()
        if not summary:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="voice.summarize",
                target_kind="voice",
                target_id="web",
                reason="summarize-empty-output",
            )
            fallback = body.text[:body.max_chars].strip()
            return SummarizeResponse(
                summary=fallback,
                original_len=len(body.text),
                summary_len=len(fallback),
            )

        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="voice.summarize",
            target_kind="voice",
            target_id="web",
        )

        return SummarizeResponse(
            summary=summary,
            original_len=len(body.text),
            summary_len=len(summary),
        )

    except subprocess.TimeoutExpired:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="voice.summarize",
            target_kind="voice",
            target_id="web",
            reason="timeout",
        )
        raise HTTPException(
            http_status.HTTP_504_GATEWAY_TIMEOUT,
            "summarize timeout"
        )
