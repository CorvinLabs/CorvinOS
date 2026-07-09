"""OpenAI audio transcription provider.

Default model: ``gpt-4o-mini-transcribe`` (~$0.003/min as of 2026-05),
which is 50 % cheaper than ``whisper-1`` and scores higher on German and
mixed-language voice notes. Override via ``CORVIN_STT_OPENAI_MODEL`` to
pin a different model (e.g. ``whisper-1``, ``gpt-4o-transcribe``).

Uses ``openai.audio.transcriptions.create``. Network required; data is
sent to OpenAI servers. Operators with EU-residency requirements should
disable this and pin ``local`` instead (``CORVIN_STT_PROVIDER=local``).
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .base import (
    STTProvider,
    STTProviderUnavailable,
    STTTimeout,
    STTTranscriptionFailed,
    TranscriptResult,
)


_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_MODEL = "gpt-4o-mini-transcribe"

def _resolve_voice_config_dir() -> Path:
    """SSOT for the corvin-voice config dir — byte-identical to
    forge.paths.voice_config_dir(): VOICE_CONFIG_DIR → XDG_CONFIG_HOME → ~/.config,
    uniform on every platform. Guard: tests/test_voice_config_ssot.py.
    """
    override = os.environ.get("VOICE_CONFIG_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else (Path.home() / ".config")
    return base / "corvin-voice"


_VOICE_CONFIG_DIR = _resolve_voice_config_dir()

_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _load_env_value(key: str, env_path: Path) -> str | None:
    """Read a single value out of a .env-style file (mirrors say.py /
    adapter.py::_load_env_value). Tolerant of comments, blank lines,
    `export KEY=...`, and quoted values.
    """
    if not env_path.exists():
        return None
    try:
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.lstrip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        m = _ENV_LINE_RE.match(line)
        if not m or m.group(1) != key:
            continue
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            # Fully-quoted value: quotes protect everything inside; take it
            # verbatim (a `#` inside quotes is a legitimate key character).
            value = value[1:-1]
        else:
            # Unquoted (incl. `KEY="sk-x" # prod`, where the trailing comment
            # breaks the first==last quote test): drop an inline comment, then
            # peel symmetric wrapping quotes. Without this the common
            # `KEY=sk-x  # prod` idiom poisons the key and every call 401s
            # while status shows "ready".
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
        if value.strip():
            return value.strip()
    return None


def _resolve_api_key() -> str | None:
    """Return the OpenAI API key, checking STT-specific var first.

    Resolution order:
      1. CORVIN_STT_OPENAI_KEY  — dedicated STT key (preferred; not counted
         as an engine credential by the console engines page)
      2. OPENAI_API_KEY         — general OpenAI key (legacy fallback)
      3. ~/.config/corvin-voice/.env or service.env — file fallback.

    The file fallback matters because process env vars aren't always
    populated: bridge.sh/voice_lib.sh export OPENAI_API_KEY into the shell
    before Python starts on Linux/macOS, but bridge.ps1 on Windows launches
    the console/daemon directly with no equivalent .env-loading step, so
    os.environ is empty there even when the key is configured. Reading the
    file directly (same as say.py's TTS key resolution) makes STT work the
    same way regardless of how the process was launched.
    """
    # .strip(): a whitespace-only exported key is truthy, so is_available()
    # reported "ready" while every call died at auth (review finding).
    key = (
        (os.environ.get("CORVIN_STT_OPENAI_KEY") or "").strip()
        or (os.environ.get("OPENAI_API_KEY") or "").strip()
    )
    if key:
        return key
    for env_file in (_VOICE_CONFIG_DIR / ".env", _VOICE_CONFIG_DIR / "service.env"):
        for env_key in ("CORVIN_STT_OPENAI_KEY", "OPENAI_API_KEY", "OPENAI_APIKEY"):
            key = _load_env_value(env_key, env_file)
            if key:
                return key
    return None


class OpenAIWhisperProvider:
    """OpenAI audio transcription via the ``openai`` Python SDK."""

    name = "openai"

    def is_available(self) -> bool:
        if not _resolve_api_key():
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe(
        self,
        audio_path: Path,
        *,
        lang: str | None = None,
        timeout_s: float | None = None,
    ) -> TranscriptResult:
        api_key = _resolve_api_key()
        if not api_key:
            raise STTProviderUnavailable("no OpenAI API key set (CORVIN_STT_OPENAI_KEY or OPENAI_API_KEY)")
        try:
            from openai import OpenAI
            from openai import APIError, APITimeoutError
        except ImportError as exc:
            raise STTProviderUnavailable(
                "openai package not installed (pip install openai)"
            ) from exc

        budget = timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S
        # The OpenAI SDK accepts a per-request timeout; honour it.
        # max_retries=0: the SDK default of 2 retries multiplies the wall
        # clock by ~3× past `budget`, blowing the caller's STT time budget.
        client = OpenAI(api_key=api_key, timeout=budget, max_retries=0)
        model = os.environ.get("CORVIN_STT_OPENAI_MODEL", _DEFAULT_MODEL)
        kwargs: dict = {"model": model}
        if lang and lang != "auto":
            kwargs["language"] = lang

        t0 = time.monotonic()
        try:
            with open(audio_path, "rb") as f:
                resp = client.audio.transcriptions.create(file=f, **kwargs)
        except APITimeoutError as exc:
            raise STTTimeout(
                f"openai whisper timeout after {time.monotonic() - t0:.1f}s"
            ) from exc
        except APIError as exc:
            raise STTTranscriptionFailed(
                f"openai whisper API error: {exc}"
            ) from exc
        except OSError as exc:
            raise STTTranscriptionFailed(
                f"audio file unreadable: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            # Catch-all for SDK-side surprises (auth, network, etc.).
            # The SDK doesn't expose a single base class consistently
            # across versions, so we accept the breadth here.
            raise STTTranscriptionFailed(
                f"openai whisper failed: {exc}"
            ) from exc

        text = (getattr(resp, "text", "") or "").strip()
        detected_lang = getattr(resp, "language", None) or lang or None
        return TranscriptResult(
            text=text,
            provider=self.name,
            lang=detected_lang,
            duration_s=None,  # OpenAI doesn't return audio duration
        )


# Type-check at import time so a stale Protocol implementation surfaces
# loudly rather than silently dropping out of the resolver chain.
assert isinstance(OpenAIWhisperProvider(), STTProvider)
