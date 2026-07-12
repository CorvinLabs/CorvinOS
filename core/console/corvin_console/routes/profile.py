"""Profile route — single-operator user profile + voice-audience.

Phase G (Console Tab #2): the eingeloggte User edits the bridge-wide
profile.json that drives:

  * identity fields surfaced via ``profile.for_system_prompt()``
    (name, display_language, tone, timezone, default_persona,
    voice_note_max_sentences)
  * voice-audience fields surfaced via ``profile.for_tts_audience()``
    (Layer 12 — level, jargon, style, background, metaphors, domains,
    learning, chat_render)

Endpoints
---------
  GET    /v1/console/profile           → current profile + schema
  PUT    /v1/console/profile           → full upsert (Re-Auth + audit)
  POST   /v1/console/profile/reset     → wipe (Re-Auth + audit)
  POST   /v1/console/profile/preview   → render TTS-audience block live
                                         (no persistence, no audit)

Compliance baseline mirrors ``routes/settings.py``:
  * Re-Auth required on every write (Re-Auth token verified against
    session fingerprint)
  * Pydantic ``extra="forbid"`` schema rejects unknown keys
  * Audit ``console.action_performed action=profile.write|profile.reset``
    OR ``console.action_failed`` with curated reason
  * File mode 0o600 enforced by ``profile.save()``
"""
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field, conint, field_validator

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session, verify_reauth

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_VOICE_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_VOICE_SHARED) not in sys.path:
    sys.path.insert(0, str(_VOICE_SHARED))

import i18n as _i18n        # noqa: E402 — voice/bridges/shared/i18n.py
import profile as _profile  # noqa: E402 — voice/bridges/shared/profile.py


router = APIRouter()


# ── Schema ────────────────────────────────────────────────────────────


class IdentityFields(BaseModel):
    """Identity / personal fields. All optional; absent = unchanged.

    Pass ``null`` (Python ``None``) on a field to delete it.
    """
    name:                     str | None = Field(None, max_length=120)
    display_language:         str | None = Field(None, max_length=8)
    tone:                     str | None = Field(None, max_length=80)
    timezone:                 str | None = Field(None, max_length=60)
    default_persona:          str | None = Field(None, max_length=60)
    voice_note_max_sentences: conint(ge=1, le=10) | None = None
    custom_instructions:      str | None = Field(None, max_length=500)

    model_config = {"extra": "forbid"}

    @field_validator("display_language")
    @classmethod
    def _normalise_display_language(cls, v: str | None) -> str | None:
        """Route through the SAME BCP-47 validator the `/lang set` slash
        command uses (i18n.normalise) instead of storing raw client input.

        Confirmed live bug (2026-07-12): this field previously had no
        validation beyond a max_length cap, so a value like a bare "zh"
        (not the canonical "zh-Hans") was stored verbatim. Every downstream
        i18n.t() lookup (the welcome greeting, voice-summary language pin)
        then silently fell through its own fallback chain to English —
        neither the configured language nor the user's actual language.
        See docs/troubleshooting.md #34.
        """
        if v is None:
            return None
        normalised = _i18n.normalise(v)
        if not normalised:
            raise ValueError(
                f"{v!r} is not a recognised BCP-47 language code"
            )
        return normalised


class AudienceFields(BaseModel):
    """Layer-12 voice-audience fields + TTS voice. All optional; absent = unchanged.

    Validators mirror ``profile._sanitize_voice_audience``. Pass ``null``
    on a field to delete it.
    """
    voice_audience_level:       Literal["novice", "intermediate", "expert"] | None = None
    voice_audience_jargon:      conint(ge=0, le=5) | None = None
    voice_audience_style:       Literal["concise", "verbose", "example-driven"] | None = None
    voice_audience_background:  str | None = Field(None, max_length=200)
    voice_audience_metaphors:   Literal["on", "off"] | None = None
    voice_audience_domains:     list[str] | None = Field(None, max_length=8)
    voice_audience_learning:    conint(ge=0, le=3) | None = None
    voice_audience_chat_render: Literal["on", "off"] | None = None
    tts_voice:                  str | None = Field(None, max_length=50)
    tts_voice_de:               str | None = Field(None, max_length=50)
    tts_voice_en:               str | None = Field(None, max_length=50)
    # Provider selection: "auto" (chain), "openai", "edge", "piper".
    # Stored in profile; passed to say.py as argv[5] by voice.py.
    tts_provider:               Literal["auto", "openai", "edge", "piper"] | None = None

    model_config = {"extra": "forbid"}


class ProfileWriteRequest(BaseModel):
    """Full upsert body. The Console SPA sends the whole edited form
    every time — partial updates are not first-class here. Fields absent
    from the body are LEFT UNCHANGED; explicit ``null`` deletes them."""
    identity:      IdentityFields | None = None
    audience:      AudienceFields | None = None
    re_auth_token: str | None = None

    model_config = {"extra": "forbid"}


class ProfileResetRequest(BaseModel):
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}


_LANG_PATTERN = r"^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{1,8})*$"


class ProfilePreviewRequest(BaseModel):
    """Live-preview the TTS-audience block. No persistence, no audit."""
    audience: AudienceFields
    lang: Literal["de", "en"] = "de"
    model_config = {"extra": "forbid"}


class VoiceTestRequest(BaseModel):
    """Test a TTS voice with a short sample. Returns audio as base64."""
    voice: str = Field(..., max_length=50)
    lang: str = Field("de", min_length=2, max_length=10, pattern=_LANG_PATTERN)
    model_config = {"extra": "forbid"}


# ── Helpers ───────────────────────────────────────────────────────────


_IDENTITY_KEYS = (
    "name", "display_language", "tone", "timezone",
    "default_persona", "voice_note_max_sentences",
    "custom_instructions",
)
_AUDIENCE_KEYS = (
    "voice_audience_level",
    "voice_audience_jargon",
    "voice_audience_style",
    "voice_audience_background",
    "voice_audience_metaphors",
    "voice_audience_domains",
    "voice_audience_learning",
    "voice_audience_chat_render",
    "tts_voice",
    "tts_voice_de",
    "tts_voice_en",
    "tts_provider",
)


def _project_current() -> dict[str, Any]:
    """Snapshot the current profile, projected into the two sections
    the SPA renders. Reading is free + cached in profile.py."""
    d = _profile.load(force=True)
    identity = {k: d.get(k) for k in _IDENTITY_KEYS}
    audience = {k: d.get(k) for k in _AUDIENCE_KEYS}
    return {"identity": identity, "audience": audience, "extra": d.get("_extra") or {}}


def _apply_section(current: dict[str, Any], section_body: Any | None, keys: tuple[str, ...]) -> dict[str, Any]:
    """Merge a Pydantic-validated section back into the profile dict.

    ``model_fields_set`` carries the keys the client actually sent,
    distinguishing "field absent → leave alone" from "field = null →
    delete". Pydantic's ``model_dump()`` would collapse both cases.
    """
    if section_body is None:
        return current
    sent = section_body.model_fields_set
    for k in keys:
        if k not in sent:
            continue  # not in body → unchanged
        v = getattr(section_body, k)
        if v is None:
            current.pop(k, None)
        else:
            current[k] = v
    return current


# ── Routes ────────────────────────────────────────────────────────────


@router.get("/profile")
def profile_index(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the current profile snapshot plus the live TTS-audience
    block rendering for both languages (used by the SPA to show a
    side-by-side preview without an extra round-trip)."""
    snapshot = _project_current()
    return {
        "tenant_id":   rec.tenant_id,
        "profile":     snapshot,
        "preview_de":  _profile.for_tts_audience("de"),
        "preview_en":  _profile.for_tts_audience("en"),
        "system_block": _profile.for_system_prompt(),
        "schema": {
            "audience": {
                "level":      ["novice", "intermediate", "expert"],
                "jargon":     {"min": 0, "max": 5},
                "style":      ["concise", "verbose", "example-driven"],
                "metaphors":  ["on", "off"],
                "domains":    {"max_items": 8},
                "learning":   {"min": 0, "max": 3},
                "chat_render": ["on", "off"],
                "background_max_chars": 200,
                "tts_voice_options": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
                "tts_voice_default": "nova",
            },
            "identity": {
                "voice_note_max_sentences": {"min": 1, "max": 10},
                "name_max_chars": 120,
                "tone_max_chars": 80,
                "timezone_max_chars": 60,
                "persona_max_chars": 60,
            },
        },
    }


@router.put("/profile")
def profile_write(
    body: ProfileWriteRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="profile.write",
            target_kind="profile",
            target_id="self",
            reason="reauth-failed",
        )
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "re-auth failed")

    if body.identity is None and body.audience is None:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="profile.write",
            target_kind="profile",
            target_id="self",
            reason="empty-body",
        )
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "must send at least one of identity/audience",
        )

    try:
        current = _profile.load(force=True)
        merged = dict(current)
        merged = _apply_section(merged, body.identity, _IDENTITY_KEYS)
        merged = _apply_section(merged, body.audience, _AUDIENCE_KEYS)
        _profile._validate_tts_voices(merged)
        _profile.save(merged)
    except ValueError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="profile.write",
            target_kind="profile",
            target_id="self",
            reason="validation-failed",
        )
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            str(e),
        ) from e
    except OSError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="profile.write",
            target_kind="profile",
            target_id="self",
            reason="io-error",
        )
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"write failed: {e}",
        ) from e

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="profile.write",
        target_kind="profile",
        target_id="self",
    )
    snapshot = _project_current()
    return {
        "ok":          True,
        "profile":     snapshot,
        "preview_de":  _profile.for_tts_audience("de"),
        "preview_en":  _profile.for_tts_audience("en"),
        "system_block": _profile.for_system_prompt(),
    }


@router.post("/profile/reset")
def profile_reset(
    body: ProfileResetRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="profile.reset",
            target_kind="profile",
            target_id="self",
            reason="reauth-failed",
        )
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "re-auth failed")
    _profile.reset()
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="profile.reset",
        target_kind="profile",
        target_id="self",
    )
    return {"ok": True, "profile": _project_current()}


@router.post("/profile/preview")
def profile_preview(
    body: ProfilePreviewRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Render the TTS-audience block for the candidate audience config
    WITHOUT persisting anything. The SPA uses this to show ‚so spräche
    der Bot' before the user hits Save."""
    # Build a transient dict that profile.for_tts_audience can consume.
    # The helper reads from disk via load(), so we temporarily inject
    # the candidate values, render, and restore.
    candidate = body.audience.model_dump(exclude_none=False)
    current = _profile.load(force=True)
    transient = dict(current)
    for k in _AUDIENCE_KEYS:
        if k in candidate:
            v = candidate[k]
            if v is None:
                transient.pop(k, None)
            else:
                transient[k] = v
    # for_tts_audience reads via load() — we monkey-patch the cache for
    # the duration of this call so we never touch disk.
    orig_load = _profile.load
    try:
        _profile.load = lambda force=False, _t=transient: _t  # type: ignore[assignment]
        block = _profile.for_tts_audience(body.lang)
    finally:
        _profile.load = orig_load
    return {"ok": True, "lang": body.lang, "block": block, "empty": not block}


@router.post("/voice-test")
def voice_test(
    body: VoiceTestRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Synthesize a short test sentence with the specified TTS voice.
    Returns audio as base64-encoded OGG. No persistence, no audit.

    Delegates to adapter.py's synthesize_voice_note to reuse robust
    OpenAI API key discovery and TTS logic.
    """
    voice = body.voice.strip().lower()
    if voice not in {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid voice '{voice}'",
        )

    test_messages: dict[str, str] = {
        "de": "Hallo! Dies ist eine Testsprachprobe mit dieser Stimme.",
        "en": "Hello! This is a test voice sample with this voice.",
        "zh": "你好！这是使用此语音的测试样本。",
        "zh-Hant": "你好！這是使用此語音的測試樣本。",
        "ja": "こんにちは！これはこの声のテストサンプルです。",
        "ko": "안녕하세요! 이것은 이 목소리의 테스트 샘플입니다.",
        "fr": "Bonjour ! Ceci est un échantillon vocal de test avec cette voix.",
        "es": "¡Hola! Esta es una muestra de voz de prueba con esta voz.",
    }
    # Match on the BCP-47 prefix (e.g. "zh-Hans" → "zh") as a fallback.
    lang_lc = body.lang.lower()
    text = (
        test_messages.get(body.lang)
        or test_messages.get(lang_lc[:2])
        or test_messages["en"]
    )

    # Import synthesize_voice_note from adapter to handle TTS + key discovery
    try:
        sys_path_insert = _VOICE_SHARED not in sys.path
        if sys_path_insert:
            sys.path.insert(0, str(_VOICE_SHARED))
        try:
            from adapter import synthesize_voice_note
        finally:
            if sys_path_insert and _VOICE_SHARED in sys.path:
                sys.path.remove(str(_VOICE_SHARED))
    except ImportError:
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "adapter module not available",
        )

    try:
        audio_path = synthesize_voice_note(text, body.lang, voice)
        if audio_path is None:
            raise HTTPException(
                http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                "TTS synthesis failed (OpenAI API key or service unavailable)",
            )
        audio_bytes = audio_path.read_bytes()
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        return {
            "ok": True,
            "voice": voice,
            "lang": body.lang,
            "audio_base64": audio_b64,
            "mime_type": "audio/ogg",
        }
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).error("TTS synthesis failed", exc_info=True)
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "TTS synthesis failed",
        ) from e
