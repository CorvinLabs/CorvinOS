#!/usr/bin/env python3
"""TTS helper — text → audio file (OGG-Opus, MP3, or WAV).

Provider chain (first available wins, unless pinned):
  1. OpenAI TTS-1  — best quality; needs OPENAI_API_KEY; cloud (US).
  2. edge-tts      — Microsoft Edge TTS; no key; internet (HTTPS/EU-MS).
                     pip install edge-tts
  3. Piper         — fully local, no internet, no key; GDPR/air-gap safe.
                     pip install piper-tts  OR install piper binary.
                     Models: ~/.config/corvin-voice/piper-models/
  4. silent skip   — exit 0 + empty stdout; caller falls through to text-only.

Pin a provider via CORVIN_TTS_PROVIDER=openai|edge|piper (operator env)
or via the tts_provider field in the user profile (console settings).

Usage:
    say.py <out_path> <text> [<lang> [<voice> [<provider>]]]

    lang      — BCP-47 code (e.g. "de", "en", "zh", "ja").  Default: "de".
    voice     — explicit OpenAI voice name; ignored by edge/piper.
    provider  — pin to one of: openai, edge, piper, auto. Default: auto.

Exit codes:
    0  + path on stdout  → success, audio written to <out_path>
    0  + empty stdout    → silently disabled / all providers failed.
    2                    → usage error (bad argv).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

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


VOICE_CONFIG_DIR = _resolve_voice_config_dir()

# Per-provider wall-clock cap. Network providers (OpenAI, edge-tts) can otherwise
# block indefinitely — e.g. edge-tts hanging on its Microsoft websocket on a
# fresh/headless install — which used to stall the whole TTS call until the
# caller's outer timeout fired. Keeping each provider short lets the auto-chain
# fail fast to the next provider (or to silent text-only) within budget.
_PROVIDER_TIMEOUT_S = float(os.environ.get("CORVIN_TTS_PROVIDER_TIMEOUT_S", "10"))


# ── OpenAI helpers ────────────────────────────────────────────────────

def _load_key_from_env_files() -> str | None:
    for fname in (".env", "service.env"):
        f = VOICE_CONFIG_DIR / fname
        if not f.exists():
            continue
        try:
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                # Handle shell-style `export KEY=value` lines (bridge.sh /
                # voice_lib.sh write these); without stripping the prefix the key
                # became "export OPENAI_API_KEY" and never matched, so a shell
                # service.env silently yielded no TTS key (path-audit 2026-07-06).
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                k, _, v = line.partition("=")
                k = k.strip()
                if k in ("OPENAI_API_KEY", "OPENAI_APIKEY", "CORVIN_TTS_OPENAI_KEY"):
                    v = v.strip().strip('"').strip("'")
                    if v:
                        return v
        except OSError:
            continue
    return None


def _resolve_key() -> str | None:
    return (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("CORVIN_TTS_OPENAI_KEY")
        or _load_key_from_env_files()
    )


def _openai_voice_for(lang: str, voice: str | None = None) -> str:
    """Map BCP-47 lang to an OpenAI voice, or use an explicit override."""
    if voice:
        return voice
    lc = lang.lower()
    # Default to a FEMALE OpenAI voice for every language (nova/shimmer are the
    # two female presets). "alloy" — the old catch-all — is a neutral voice, so
    # a fresh keyed install would have spoken English (and every other lang) in
    # a non-female voice by default. Fall back to "shimmer" instead. The user
    # can still override any of this via tts_voice in the console settings.
    if lc.startswith("de"):
        return "nova"
    if lc.startswith(("zh", "ja", "ko")):
        return "shimmer"
    return "shimmer"


def _try_openai(out_path: Path, text: str, lang: str, voice: str | None) -> bool:
    """Attempt OpenAI TTS. Returns True on success, False on any failure."""
    key = _resolve_key()
    if not key:
        sys.stderr.write("say.py: no OPENAI_API_KEY — skipping OpenAI TTS\n")
        return False
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError:
        sys.stderr.write("say.py: openai package not installed — skipping\n")
        return False
    try:
        client = OpenAI(api_key=key, timeout=_PROVIDER_TIMEOUT_S)
        resp = client.audio.speech.create(
            model="tts-1",
            voice=_openai_voice_for(lang, voice),
            input=text,
            response_format="opus",
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.read())
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"say.py: OpenAI TTS failed: {e}\n")
        return False


# ── edge-tts helpers ──────────────────────────────────────────────────

# BCP-47 prefix → Microsoft Edge neural voice.
# Voices chosen for naturalness; operators can extend via CORVIN_EDGE_VOICE_<LANG>.
_EDGE_VOICES: dict[str, str] = {
    "de":    "de-DE-KatjaNeural",
    "en":    "en-US-AriaNeural",
    "zh":    "zh-CN-XiaoxiaoNeural",
    "zh-hans": "zh-CN-XiaoxiaoNeural",
    "zh-hant": "zh-TW-HsiaoChenNeural",
    "ja":    "ja-JP-NanamiNeural",
    "ko":    "ko-KR-SunHiNeural",
    "fr":    "fr-FR-DeniseNeural",
    "es":    "es-ES-ElviraNeural",
    "ar":    "ar-EG-SalmaNeural",
    "ru":    "ru-RU-SvetlanaNeural",
    "hi":    "hi-IN-SwaraNeural",
    "it":    "it-IT-ElsaNeural",
    "pt":    "pt-BR-FranciscaNeural",
    "nl":    "nl-NL-ColetteNeural",
    "pl":    "pl-PL-AgnieszkaNeural",
    "sv":    "sv-SE-SofieNeural",
    "tr":    "tr-TR-EmelNeural",
    "he":    "he-IL-HilaNeural",
    "cs":    "cs-CZ-VlastaNeural",
    "da":    "da-DK-ChristelNeural",
    "fi":    "fi-FI-NooraNeural",
    "nb":    "nb-NO-PernilleNeural",
    "ro":    "ro-RO-AlinaNeural",
    "hu":    "hu-HU-NoemiNeural",
    "th":    "th-TH-PremwadeeNeural",
    "vi":    "vi-VN-HoaiMyNeural",
    "id":    "id-ID-GadisNeural",
    "ms":    "ms-MY-YasminNeural",
}


def _edge_voice_for(lang: str) -> str:
    """Return the edge-tts neural voice for a BCP-47 code.

    Checks CORVIN_EDGE_VOICE_<LANG> env override first (e.g.
    CORVIN_EDGE_VOICE_DE=de-DE-ConradNeural for a male German voice).
    """
    lc = lang.lower()
    env_key = f"CORVIN_EDGE_VOICE_{lc.upper().replace('-', '_')}"
    env_val = os.environ.get(env_key)
    if env_val and env_val.strip():
        return env_val.strip()
    return (
        _EDGE_VOICES.get(lc)
        or _EDGE_VOICES.get(lc.split("-")[0])
        or "en-US-AriaNeural"
    )


def _try_edge(out_path: Path, text: str, lang: str) -> bool:
    """Attempt edge-tts (HTTPS, no API key). Returns True on success."""
    try:
        import edge_tts  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        sys.stderr.write("say.py: edge-tts not installed (pip install edge-tts) — no TTS fallback\n")
        return False

    voice = _edge_voice_for(lang)

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice)
        # edge-tts writes MP3; the caller detects format via magic bytes.
        # Bounded so a hung Microsoft websocket can't block the whole TTS call.
        await asyncio.wait_for(
            communicate.save(str(out_path)), timeout=_PROVIDER_TIMEOUT_S,
        )

    try:
        asyncio.run(_run())
        size = out_path.stat().st_size if out_path.exists() else 0
        if size == 0:
            sys.stderr.write("say.py: edge-tts produced empty output\n")
            return False
        return True
    except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
        sys.stderr.write(f"say.py: edge-tts failed: {e}\n")
        return False


# ── Piper helpers ─────────────────────────────────────────────────────

# BCP-47 prefix → Piper model stem (quality "medium" unless noted).
# Models must be placed in PIPER_MODEL_DIR as <stem>.onnx + <stem>.onnx.json.
_PIPER_MODELS: dict[str, str] = {
    "de":  "de_DE-thorsten-medium",
    "en":  "en_US-amy-medium",
    "zh":  "zh_CN-huayan-medium",
    "ja":  "ja_JP-kokoro-medium",
    "ko":  "ko_KR-navi-x_low",
    "fr":  "fr_FR-mls_1840-low",
    "es":  "es_ES-mls_9972-low",
    "it":  "it_IT-riccardo-x_low",
    "pt":  "pt_BR-faber-medium",
    "nl":  "nl_BE-nathalie-x_low",
    "pl":  "pl_PL-mls_6892-low",
    "ru":  "ru_RU-irina-medium",
    "sv":  "sv_SE-nst-medium",
    "cs":  "cs_CZ-jirka-medium",
    "fi":  "fi_FI-harri-medium",
    "hu":  "hu_HU-anna-medium",
    "ro":  "ro_RO-mihai-medium",
    "uk":  "uk_UA-lada-x_low",
    "sk":  "sk_SK-lili-medium",
    "tr":  "tr_TR-dfki-medium",
    "vi":  "vi_VN-vivos-x_low",
}

_PIPER_MODEL_DIR = Path(
    os.environ.get("CORVIN_PIPER_MODEL_DIR")
    or (VOICE_CONFIG_DIR / "piper-models")
)


def _piper_model_for(lang: str) -> Path | None:
    """Return the .onnx model path for a BCP-47 code, or None if not found."""
    lc = lang.lower()
    env_key = f"CORVIN_PIPER_MODEL_{lc.upper().replace('-', '_')}"
    env_path = os.environ.get(env_key)
    if env_path:
        p = Path(env_path)
        return p if p.exists() else None

    stem = (
        _PIPER_MODELS.get(lc)
        or _PIPER_MODELS.get(lc.split("-")[0])
    )
    if not stem:
        return None
    model = _PIPER_MODEL_DIR / f"{stem}.onnx"
    return model if model.exists() else None


def _try_piper(out_path: Path, text: str, lang: str) -> bool:
    """Attempt Piper TTS (fully local). Returns True on success.

    Tries the Python piper-tts package first, then the piper binary.
    Model files must be present in PIPER_MODEL_DIR (default:
    ~/.config/corvin-voice/piper-models/).
    """
    model_path = _piper_model_for(lang)
    if model_path is None:
        sys.stderr.write(
            f"say.py: no Piper model for '{lang}' in {_PIPER_MODEL_DIR} — "
            f"download from https://github.com/rhasspy/piper/releases\n"
        )
        return False

    # ── Try Python piper-tts package ──────────────────────────────────
    try:
        from piper import PiperVoice  # type: ignore[import-not-found]
        import wave

        voice = PiperVoice.load(str(model_path), config_path=str(model_path) + ".json")
        wav_path = out_path.with_suffix(".wav")
        with wave.open(str(wav_path), "wb") as wav_file:
            voice.synthesize(text, wav_file)
        wav_path.rename(out_path)
        return True
    except ImportError:
        pass  # fall through to binary
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"say.py: piper-tts Python failed: {e}\n")
        return False

    # ── Try piper binary ──────────────────────────────────────────────
    import shutil
    import subprocess as _sp

    piper_bin = shutil.which("piper") or shutil.which("piper-tts")
    if not piper_bin:
        sys.stderr.write("say.py: piper binary not found (pip install piper-tts)\n")
        return False

    wav_path = out_path.with_suffix(".wav")
    try:
        result = _sp.run(
            [piper_bin, "--model", str(model_path), "--output_file", str(wav_path)],
            input=text,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            sys.stderr.write(f"say.py: piper binary failed: {result.stderr.strip()[:200]}\n")
            return False
        size = wav_path.stat().st_size if wav_path.exists() else 0
        if size == 0:
            sys.stderr.write("say.py: piper binary produced empty output\n")
            return False
        wav_path.rename(out_path)
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"say.py: piper binary error: {e}\n")
        return False


# ── Entry point ───────────────────────────────────────────────────────

# Ordered provider list for the "auto" chain.
_AUTO_CHAIN = ("openai", "edge", "piper")


def main() -> int:
    if len(sys.argv) < 3:
        sys.stderr.write(
            "usage: say.py <out_path> <text> [<lang> [<voice> [<provider>]]]\n"
        )
        return 2
    out_path = Path(sys.argv[1]).expanduser()
    text = sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) > 3 else "de"
    voice_override = sys.argv[4] if len(sys.argv) > 4 else None
    # Provider: argv[5] beats env var (profile-level beats operator-level
    # only for the explicit-pin case; env is the operator override).
    provider_arg = sys.argv[5].strip().lower() if len(sys.argv) > 5 else ""
    provider_env = os.environ.get("CORVIN_TTS_PROVIDER", "").strip().lower()
    # argv wins over env so the caller (voice.py) can pass the user-profile
    # preference while operators can still override with the env var.
    provider = provider_arg or provider_env or "auto"

    if not text.strip():
        return 0

    def _run(name: str) -> bool:
        if name == "openai":
            return _try_openai(out_path, text, lang, voice_override)
        if name == "edge":
            return _try_edge(out_path, text, lang)
        if name == "piper":
            return _try_piper(out_path, text, lang)
        return False

    if provider != "auto":
        # Preferred provider first; on failure fall through to the auto-chain so
        # voice always works even if the configured provider is temporarily broken
        # (e.g. missing API key, network outage, not installed).
        if _run(provider):
            sys.stdout.write(str(out_path))
            return 0
        sys.stderr.write(
            f"say.py: preferred provider '{provider}' failed — falling back to auto-chain\n"
        )

    # Auto chain: openai → edge → piper → silent.
    for name in _AUTO_CHAIN:
        if _run(name):
            sys.stdout.write(str(out_path))
            return 0

    # All providers failed — caller falls back to text-only delivery.
    return 0


if __name__ == "__main__":
    sys.exit(main())
