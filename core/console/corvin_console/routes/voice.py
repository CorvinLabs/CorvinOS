"""Voice REST endpoints — STT + TTS for the web messenger (ADR-0037 Iter 3b).

Endpoints
---------
  POST /v1/console/voice/transcribe   audio blob (multipart) → text
  POST /v1/console/voice/tts          {text, lang?} → audio/ogg blob

STT delegates to ``operator/voice/scripts/stt/`` (the same provider
chain bridges use). TTS shells out to ``operator/voice/scripts/say.py``
to avoid pulling the OpenAI client into the console process.

Audit policy (load-bearing — CLAUDE.md § Layer 23):
    voice.transcribed audit emits METADATA ONLY, never transcript text.
This module never writes ``text`` to any audit field. The same goes
for TTS — only ``len(text)`` is logged.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
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


router = APIRouter()


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

    blob = await audio.read()
    if not blob:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "empty audio")
    if len(blob) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"audio exceeds {_MAX_AUDIO_BYTES} bytes",
        )

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
    The operator-level env var CORVIN_TTS_PROVIDER takes final precedence;
    that is handled inside say.py itself so we don't duplicate the logic here.
    """
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


_TTS_PROVIDER_CHAR_LIMIT = 4000  # OpenAI TTS-1 hard cap is 4096; stay under it
_TTS_SUMMARIZE_MAX_CHARS = 400   # same default build_voice_summary() uses for bridges
# summarize.py's OWN internal budget is CLI (45s) + Hermes (60s) = up to 105s
# worst case (see summarize.py's _SUMMARY_CLI_TIMEOUT_S/_SUMMARY_HERMES_TIMEOUT_S).
# A shorter wrapper timeout here would routinely cut off a legitimate
# in-progress CLI attempt before summarize.py's own fallback chain even runs —
# matches adapter.py::build_voice_summary's identical 120s parent-cap
# convention for the exact same subprocess (bridge/console parity).
_TTS_SUMMARIZE_TIMEOUT_S = float(os.environ.get("CORVIN_TTS_SUMMARIZE_TIMEOUT_S", "120"))


def _summarize_for_speech(text: str, lang: str) -> str | None:
    """Best-effort condensation of *text* into a real, faithful spoken
    summary (learnings/metaphor annex included, per the user's audience
    settings) via ``summarize.py`` — the SAME script the standalone
    ``/voice/summarize`` endpoint and every messenger bridge's
    ``adapter.py::build_voice_summary()`` already use. Returns ``None`` on
    any failure (missing script, timeout, empty output) so the caller can
    fall back to the raw text — this must never break TTS, only improve it.

    Before this helper existed, ``POST /voice/tts`` spoke the raw, full
    answer text (truncated blindly at ``_TTS_PROVIDER_CHAR_LIMIT``) —
    ``/voice/summarize`` was a fully-working, tested endpoint that the
    frontend never called (confirmed via grep: zero references to
    "voice/summarize" anywhere under web-next/src). Every messenger bridge
    (Discord, WhatsApp, ...) speaks a real condensed summary; the console
    read the raw text word-for-word — this is the console-specific gap
    behind that discrepancy (found 2026-07-14, reported as "voice summary
    works in Discord but not in the console chat").
    """
    summarize_path = _VOICE_SCRIPTS / "summarize.py"
    if not summarize_path.exists():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(summarize_path),
             "--lang", lang if lang in ("de", "en") else "de",
             "--max-chars", str(_TTS_SUMMARIZE_MAX_CHARS)],
            input=text, capture_output=True, text=True,
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
    model_config = {"extra": "forbid"}


@router.post("/voice/tts")
def voice_tts(
    body: TtsRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Response:
    say_path = _VOICE_SCRIPTS / "say.py"
    if not say_path.exists():
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            "say.py not found")

    # Speak a real, condensed summary (learnings/metaphor annex included, same
    # as every messenger bridge via adapter.py::build_voice_summary) instead of
    # the raw answer text — see _summarize_for_speech's docstring for why this
    # was previously missing here. Falls back to a blind truncation at the
    # provider character limit (OpenAI TTS-1: 4096 chars, edge-tts: ~8000) if
    # summarization is unavailable or fails; this must never block TTS.
    tts_text = _summarize_for_speech(body.text, body.lang) or body.text[:_TTS_PROVIDER_CHAR_LIMIT]

    with tempfile.NamedTemporaryFile(prefix="corvin_tts_", suffix=".opus", delete=False) as fh:
        out_path = Path(fh.name)

    try:
        # Resolve voice and provider from the user's profile.
        # say.py argv: <out_path> <text> [<lang> [<voice> [<provider>]]]
        voice = _resolve_tts_voice(body.lang)
        provider = _resolve_tts_provider()
        cmd = [sys.executable, str(say_path), str(out_path), tts_text, body.lang]
        # Always pass voice (empty string means "use default") so that the
        # provider arg is always at argv[5] when present.
        cmd.append(voice or "")
        if provider:
            cmd.append(provider)

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            # TTS is an OPTIONAL enhancement, not part of the task result. Keep
            # the wait short so a hung/misconfigured provider (e.g. a fresh
            # install with no working TTS, or edge-tts blocking on the network)
            # does not stall the turn for a minute. say.py also has per-provider
            # timeouts well under this (CORVIN_TTS_PROVIDER_TIMEOUT_S).
            timeout=_TTS_TIMEOUT_S,
        )
        if proc.returncode != 0:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="voice.tts",
                target_kind="voice",
                target_id="web",
                reason="say-exit-nonzero",
            )
            # Optional feature failed → degrade SILENTLY (204), never surface a
            # red "TTS failed" error that looks like the task itself failed. The
            # frontend treats 204 as "no audio, skip playback".
            return Response(status_code=http_status.HTTP_204_NO_CONTENT)

        size = out_path.stat().st_size if out_path.exists() else 0
        if size == 0:
            # say.py exited 0 with no audio — all providers unavailable.
            # Graceful degradation: signal the frontend to skip playback silently.
            return Response(status_code=http_status.HTTP_204_NO_CONTENT)

        data = out_path.read_bytes()
    except subprocess.TimeoutExpired:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="voice.tts",
            target_kind="voice",
            target_id="web",
            reason="timeout",
        )
        # A TTS timeout is NOT a task failure — degrade silently (204) so the
        # reply still lands without an alarming red banner (the fresh-install /
        # no-working-TTS case). Audit still records the timeout for the operator.
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="voice.tts",
        target_kind="voice",
        target_id="web",
    )
    mime = _detect_audio_mime(data)
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Length": str(len(data)),
                 "X-Corvin-Lang": body.lang,
                 "X-Corvin-TTS-Format": mime},
    )


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
    # `claude -p` (plus an optional dialectic-judge second spawn = up to 2x). Meter
    # it on the chat_turns_per_day axis (interactive single-turn, same axis as the
    # three other interactive surfaces) before the subprocess, fail-closed.
    from ._compute_license_gate import enforce_chat_turns  # noqa: PLC0415
    enforce_chat_turns(
        rec.tenant_id, rec.sid_fingerprint,
        audit_action="voice.summarize", channel="voice",
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
