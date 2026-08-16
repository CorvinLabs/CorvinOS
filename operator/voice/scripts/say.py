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

The whole chain runs under a wall-clock deadline (default 22s, override via
CORVIN_TTS_TOTAL_BUDGET_S) kept strictly below the console's outer 25s
subprocess budget: each provider attempt is clamped to the remaining budget,
and a provider with too little budget left is skipped with a stderr note.

Set CORVIN_SAY_NO_FALLBACK=1 to make a PINNED provider hard-fail (no
auto-chain fallback) when it can't produce audio — a strict/isolation mode
used by tests to prove a specific tier actually works instead of being masked
by a later tier (VOICE-1). Default (unset) keeps the fall-through behaviour.

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
import json
import os
import sys
import time
from pathlib import Path

# V2-RESIDUAL (2026-07-20): anchor the total-deadline clock at MODULE IMPORT,
# not in main(). On a slow (e.g. cold Windows) interpreter start, the seconds
# spent booting + importing say.py's own deps count against the console's outer
# 25s subprocess budget but NOT against a deadline captured later in main() — so
# the internal 22s deadline could expire only AFTER the console's SIGKILL,
# orphaning the Piper grandchild. Capturing here (as early as this module runs)
# folds say.py's import cost into the budget, guaranteeing self-termination
# before the outer kill. Only the fixed interpreter-boot slice stays uncounted.
_PROCESS_START_MONOTONIC = time.monotonic()


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

# Piper binary gets a longer per-attempt cap than the network providers — a
# local first-run synth loads the model from disk (VOICE-10 chose 20s).
_PIPER_TIMEOUT_S = 20.0

# Total wall-clock budget for the WHOLE provider chain. Per-provider caps
# alone don't protect the caller: they SUM to up to 40s (openai 10 + edge 10
# + piper 20) while the console's outer subprocess budget is 25s
# (routes/voice.py::_TTS_TIMEOUT_S). When the sum overran, the console
# SIGKILLed say.py mid-Piper — orphaning the Piper grandchild and leaving the
# sibling corvin_tts_*.wav behind. say.py now enforces its OWN deadline,
# strictly below the console's, so it always finishes (or degrades to the
# documented silent skip) before the caller kills it.
_TOTAL_BUDGET_S = float(os.environ.get("CORVIN_TTS_TOTAL_BUDGET_S", "22"))
_DEADLINE_MARGIN_S = 1.0  # reserved for our own teardown before the deadline
_MIN_ATTEMPT_S = 1.0      # don't even start a provider with less than this


def _clamped_timeout(provider_timeout_s: float,
                     remaining_s: "float | None") -> "float | None":
    """Effective timeout for the next provider attempt under the total budget.

    ``remaining_s=None`` means "no deadline" — the provider timeout passes
    through unchanged. Returns ``None`` when the remaining budget (minus the
    safety margin) is too small to plausibly complete an attempt: the caller
    must SKIP the provider (with a stderr note for the failure-reason
    surface) instead of starting work the console's outer timeout would
    SIGKILL mid-flight.
    """
    if remaining_s is None:
        return provider_timeout_s
    usable = remaining_s - _DEADLINE_MARGIN_S
    if usable < _MIN_ATTEMPT_S:
        return None
    return min(provider_timeout_s, usable)


# ── OpenAI helpers ────────────────────────────────────────────────────

def _clean_env_value(v: str) -> str:
    """Normalise a dotenv value: strip a trailing ` # comment`, then
    surrounding whitespace and matching quotes.

    MUST stay byte-identical to provider_keys._clean_env_value — this used to
    be a from-scratch reimplementation that stripped ALL leading/trailing
    quote characters (`str.strip(chars)` semantics) instead of a single
    matched leading+trailing pair, so e.g. a value ending in one stray
    apostrophe (`sk-test'`) was cleaned differently by the two "must match"
    implementations. Mirrored verbatim now; see the parity guard in
    tests/test_secrets_ssot.py.
    """
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    v = v.split(" #", 1)[0].split("\t#", 1)[0].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


# WA-22: single canonical source of truth (operator/bridges/shared/secrets.py)
# — service.env is the ONE config file consulted; the second, independently
# maintained ~/.config/corvin-voice/.env is retired (nothing writes to it
# post-consolidation, and it drifted from service.env on every install this
# was audited on). This function stays a private, import-independent copy
# (say.py must keep working when invoked standalone with no PYTHONPATH set
# up) but MUST stay byte-identical to secrets.resolve_key("tts_openai_api_key")
# — see the parity guard in tests/test_secrets_ssot.py.
_CANDIDATES = ("CORVIN_TTS_OPENAI_KEY", "OPENAI_API_KEY", "OPENAI_APIKEY")


def _load_key_from_env_files() -> str | None:
    f = VOICE_CONFIG_DIR / "service.env"
    if not f.exists():
        return None
    found: dict[str, str] = {}
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
            if k in _CANDIDATES:
                cleaned = _clean_env_value(v)
                if cleaned and k not in found:
                    found[k] = cleaned
    except OSError:
        return None
    for k in _CANDIDATES:
        if found.get(k):
            return found[k]
    return None


def _resolve_key() -> str | None:
    # Every candidate checked against env first (dedicated, then general,
    # then legacy alias) before any is checked against the file — an
    # explicit env-var override always beats anything in service.env.
    for k in _CANDIDATES:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return _load_key_from_env_files()


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


def _try_openai(out_path: Path, text: str, lang: str, voice: str | None,
                timeout_s: "float | None" = None) -> bool:
    """Attempt OpenAI TTS. Returns True on success, False on any failure."""
    if timeout_s is None:
        timeout_s = _PROVIDER_TIMEOUT_S
    key = _resolve_key()
    if not key:
        sys.stderr.write("say.py: no OPENAI_API_KEY — skipping OpenAI TTS\n")
        return False
    try:
        from openai import OpenAI, RateLimitError  # type: ignore[import-not-found]
    except ImportError:
        sys.stderr.write("say.py: openai package not installed — skipping\n")
        return False

    # Retry with exponential backoff on RateLimitError (429)
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            # max_retries=0: disable SDK retries to protect the outer 25s route
            # budget (VOICE-10). We handle RateLimitError with manual backoff.
            client = OpenAI(api_key=key, timeout=timeout_s, max_retries=0)
            resp = client.audio.speech.create(
                model="tts-1",
                voice=_openai_voice_for(lang, voice),
                input=text,
                response_format="opus",
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(resp.read())
            return True
        except RateLimitError as e:
            if attempt < max_retries:
                # Exponential backoff: 1s, 2s, 4s
                wait_time = 2 ** attempt
                import time
                time.sleep(wait_time)
                continue
            # All retries exhausted
            sys.stderr.write(
                f"say.py: OpenAI TTS failed after {max_retries + 1} attempts: "
                f"RateLimitError status=429\n"
            )
            return False
        except Exception as e:  # noqa: BLE001
            # CONTENT-FREE: SDK exception str()s can embed the request payload —
            # i.e. the text being spoken. Type + HTTP status only (2026-07-17).
            sys.stderr.write(
                f"say.py: OpenAI TTS failed: {type(e).__name__} "
                f"status={getattr(e, 'status_code', '')}\n"
            )
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
    # "no" is the macro-language code the console dropdown and detect_lang
    # emit for Norwegian — alias to the Bokmål voice (edge-tts has no "no").
    "no":    "nb-NO-PernilleNeural",
    "el":    "el-GR-AthinaNeural",
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


def _try_edge(out_path: Path, text: str, lang: str,
              timeout_s: "float | None" = None) -> bool:
    """Attempt edge-tts (HTTPS, no API key). Returns True on success."""
    if timeout_s is None:
        timeout_s = _PROVIDER_TIMEOUT_S
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
            communicate.save(str(out_path)), timeout=timeout_s,
        )

    try:
        asyncio.run(_run())
        size = out_path.stat().st_size if out_path.exists() else 0
        if size == 0:
            sys.stderr.write("say.py: edge-tts produced empty output\n")
            return False
        return True
    except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
        # CONTENT-FREE: type name only — the websocket error can quote the
        # SSML request, which carries the spoken text (2026-07-17).
        sys.stderr.write(f"say.py: edge-tts failed: {type(e).__name__}\n")
        return False


# ── Piper helpers ─────────────────────────────────────────────────────

# BCP-47 prefix → Piper model stem. This is the fallback table used only for
# manual/pre-ADR-0185 setups (config.json + env override are consulted first in
# _piper_model_for); a model file is expected at PIPER_MODEL_DIR/<stem>.onnx
# (+ <stem>.onnx.json).
#
# SSOT INVARIANT (VOICE-6): these stems must stay byte-identical to the names
# corvin-install actually downloads — the last path segment of each entry in
# installer/steps/piper.py::_MODELS. They previously disagreed for 8/12
# languages (de: thorsten vs kerstin; en: amy vs lessac; es/fr/it/nl/pl/zh),
# so corvin-install reported a successful download that say.py's fallback could
# then never find. Keep this table == installer _MODELS for all 12 languages.
# Guard: test_say.sh + tests/test_installer_piper.py.
_PIPER_MODELS: dict[str, str] = {
    "de":  "de_DE-kerstin-low",
    "en":  "en_US-lessac-medium",
    "es":  "es_ES-sharvard-medium",
    "fr":  "fr_FR-siwis-medium",
    "it":  "it_IT-paola-medium",
    "nl":  "nl_NL-mls-medium",
    "pl":  "pl_PL-gosia-medium",
    "pt":  "pt_BR-faber-medium",
    "ru":  "ru_RU-irina-medium",
    "tr":  "tr_TR-dfki-medium",
    "uk":  "uk_UA-lada-x_low",
    "zh":  "zh_CN-huayan-x_low",
}

_PIPER_MODEL_DIR = Path(
    os.environ.get("CORVIN_PIPER_MODEL_DIR")
    or (VOICE_CONFIG_DIR / "piper-models")
)


def _piper_model_from_config(lang: str) -> Path | None:
    """Read piper_model_<lang> from config.json — the SSOT `corvin-install`
    (installer/steps/piper.py) actually writes to (ADR-0185 fix).

    Without this, say.py fell back to its own hardcoded ``_PIPER_MODELS``
    stem table below, which used DIFFERENT model names than the installer
    downloads for 8 of 12 languages (including de/en) — corvin-install would
    report a successful download that say.py could then never find at
    runtime. Mirrors the already-correct lookup in
    ``adapter.py::_try_piper_tts``.
    """
    try:
        cfg = json.loads((VOICE_CONFIG_DIR / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    lc = lang.lower()
    # Language-exact lookups only ("de-de" also tries its "de" primary tag —
    # the installer writes primary-tag keys). NO any-model/lang_default
    # fallback here: returning the German model for an English request spoke
    # English text through Kerstin and, worse, shadowed a correct
    # English model sitting in the stem-table tier below (review finding).
    # Cross-language fallback is a deliberate last resort in _piper_model_for.
    path_str = (
        cfg.get(f"piper_model_{lc}")
        or cfg.get(f"piper_model_{lc.split('-')[0]}")
    )
    if not path_str:
        return None
    p = Path(path_str)
    return p if p.exists() else None


def _piper_model_for(lang: str) -> Path | None:
    """Return the .onnx model path for a BCP-47 code, or None if not found.

    Resolution order: explicit env override, then config.json (what
    corvin-install actually wrote), then the legacy hardcoded stem table
    below (manual/pre-ADR-0185 setups that placed a model file by hand).
    """
    lc = lang.lower()
    env_key = f"CORVIN_PIPER_MODEL_{lc.upper().replace('-', '_')}"
    env_path = os.environ.get(env_key)
    if env_path:
        p = Path(env_path)
        return p if p.exists() else None

    from_config = _piper_model_from_config(lc)
    if from_config is not None:
        return from_config

    stem = (
        _PIPER_MODELS.get(lc)
        or _PIPER_MODELS.get(lc.split("-")[0])
    )
    if stem:
        model = _PIPER_MODEL_DIR / f"{stem}.onnx"
        if model.exists():
            return model

    # Last resort — ANY configured model (wrong-language speech beats total
    # silence only offline; note it on stderr so the degradation is visible).
    # This tier deliberately sits BELOW the stem table: it used to sit above
    # it and shadowed a correct same-language model on disk.
    try:
        cfg = json.loads((VOICE_CONFIG_DIR / "config.json").read_text(encoding="utf-8"))
        any_model = (
            cfg.get(f"piper_model_{cfg.get('lang_default', 'de')}")
            or next((v for k, v in cfg.items()
                     if k.startswith("piper_model_") and v), None)
        )
        if any_model and Path(any_model).exists():
            sys.stderr.write(
                f"say.py: no Piper model for '{lang}' — falling back to "
                f"{Path(any_model).name} (wrong-language speech)\n"
            )
            return Path(any_model)
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_piper_binary() -> str | None:
    """Locate the piper binary the same way the synth path and adapter do —
    PIPER_BIN (only if it exists) → PATH → next to the interpreter.

    Single SSOT so provider_status()/voice_doctor report exactly what the
    synth path will use: PATH-only probes reported "not installed" on the
    uv-tool installs this resolution was added for (review finding).
    """
    import shutil as _shutil  # noqa: PLC0415
    env_bin = os.environ.get("PIPER_BIN")
    piper_bin = (
        (env_bin if (env_bin and Path(env_bin).exists()) else None)
        or _shutil.which("piper")
        or _shutil.which("piper-tts")
    )
    if not piper_bin:
        exe_dir = Path(sys.executable).parent
        for cand in ("piper", "piper.exe"):
            if (exe_dir / cand).exists():
                return str(exe_dir / cand)
    return piper_bin


def _try_piper(out_path: Path, text: str, lang: str,
               timeout_s: "float | None" = None) -> bool:
    """Attempt Piper TTS (fully local). Returns True on success.

    Tries the Python piper-tts package first, then the piper binary.
    Model files must be present in PIPER_MODEL_DIR (default:
    ~/.config/corvin-voice/piper-models/). ``timeout_s`` bounds the piper
    BINARY tier; the in-process piper-tts package tier has no timeout hook
    (bounded instead by the caller skipping Piper entirely when the total
    budget is low — see _clamped_timeout).
    """
    if timeout_s is None:
        timeout_s = _PIPER_TIMEOUT_S
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
        # piper-tts >= 1.4.1 renamed the WAV writer: synthesize() is now a
        # generator yielding AudioChunks and its 2nd positional arg is a
        # SynthesisConfig, NOT the wave file. Passing the wave handle there wrote
        # zero frames → wave close raised "# channels not specified". Use the
        # dedicated WAV writer, which the pinned versions expose.
        with wave.open(str(wav_path), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        wav_path.replace(out_path)  # replace (not rename): overwrite-safe on Windows
        return True
    except ImportError:
        pass  # fall through to binary
    except Exception as e:  # noqa: BLE001
        # Don't give up on Piper here — the binary tier below is an independent
        # code path (older/newer API surface) and is the whole point of the
        # offline fallback. Drop the stray partial WAV and fall through.
        # CONTENT-FREE: type name only, never str(e) (2026-07-17).
        sys.stderr.write(
            f"say.py: piper-tts Python API failed ({type(e).__name__}) "
            f"— trying piper binary\n"
        )
        try:
            out_path.with_suffix(".wav").unlink(missing_ok=True)
        except OSError:
            pass

    # ── Try piper binary ──────────────────────────────────────────────
    import shutil
    import subprocess as _sp

    piper_bin = _resolve_piper_binary()
    if not piper_bin:
        sys.stderr.write("say.py: piper binary not found (pip install piper-tts)\n")
        return False

    wav_path = out_path.with_suffix(".wav")
    try:
        # VOICE-10: keep this UNDER the caller's outer TTS budget
        # (routes/voice.py::_TTS_TIMEOUT_S == 25s). A 120s inner cap meant the
        # outer timeout fired first and killed a slow first Piper run
        # inconsistently (orphaned subprocess, no clean fallback). The default
        # (_PIPER_TIMEOUT_S == 20s) is comfortably below 25s yet ample for a
        # local Piper synth once the model is loaded; main()'s total-deadline
        # clamp shrinks it further when earlier providers ate into the budget.
        # input as UTF-8 BYTES (not text=True): text mode encodes stdin with
        # the locale codec — cp1252 on Windows — which mojibakes umlauts and
        # raises UnicodeEncodeError for ru/uk/zh/tr, exactly the languages in
        # the model table. Mirrors the adapter's piper call.
        _no_window = 0
        if sys.platform == "win32":
            _no_window = getattr(_sp, "CREATE_NO_WINDOW", 0)
        result = _sp.run(
            [piper_bin, "--model", str(model_path), "--output_file", str(wav_path)],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,
            creationflags=_no_window,
        )
        if result.returncode != 0:
            # CONTENT-FREE: piper echoes its input line on some error paths,
            # so forwarding stderr could leak the spoken text. Return code
            # only (2026-07-17).
            sys.stderr.write(
                f"say.py: piper binary failed: rc={result.returncode}\n"
            )
            return False
        size = wav_path.stat().st_size if wav_path.exists() else 0
        if size == 0:
            sys.stderr.write("say.py: piper binary produced empty output\n")
            return False
        wav_path.replace(out_path)  # replace (not rename): overwrite-safe on Windows
        return True
    except Exception as e:  # noqa: BLE001
        # CONTENT-FREE: type + errno (E2BIG vs EPERM stays diagnosable)
        # — a CalledProcessError-style str() would embed argv (2026-07-17).
        sys.stderr.write(
            f"say.py: piper binary error: {type(e).__name__} "
            f"errno={getattr(e, 'errno', '')}\n"
        )
        try:
            wav_path.unlink(missing_ok=True)  # don't accumulate orphan WAVs
        except OSError:
            pass
        return False


# ── Status introspection (ADR-0185 M4) ─────────────────────────────────


def provider_status() -> dict[str, dict]:
    """Structured per-engine status for the Console voice-status panel.

    Cheap introspection only — NEVER synthesizes audio, NEVER raises.
    Mirrors ``stt/resolver.py::provider_status()``'s shape so the Console
    can render STT and TTS rows the same way:

      ready:              bool       — usable right now
      package_installed:  bool       — underlying package/binary present
      model_present:      bool|None  — local voice model on disk (None: n/a)
      key_configured:     bool|None  — API key resolvable (None: n/a)
      detail:             str        — short, human-readable, non-leaky status
    """
    status: dict[str, dict] = {}

    # -- openai -- (own try/except: a probe failure here must never wipe out
    # the edge/piper rows below — each provider is isolated, matching
    # stt/resolver.py::provider_status()'s pattern, ADR-0185 review finding)
    try:
        key = _resolve_key()
        try:
            import openai  # type: ignore[import-not-found]  # noqa: F401
            package_installed = True
        except ImportError:
            package_installed = False
        ready = bool(key) and package_installed
        if ready:
            detail = "ready"
        elif not key:
            detail = "no API key configured"
        else:
            detail = "openai package not installed"
        status["openai"] = {
            "ready": ready,
            "package_installed": package_installed,
            "model_present": None,
            "key_configured": bool(key),
            "detail": detail,
        }
    except Exception as exc:  # noqa: BLE001 — status probe must never crash
        status["openai"] = {
            "ready": False,
            "package_installed": False,
            "model_present": None,
            "key_configured": None,
            "detail": f"status probe failed ({exc.__class__.__name__})",
        }

    # -- edge-tts --
    try:
        try:
            import edge_tts  # type: ignore[import-not-found]  # noqa: F401
            edge_installed = True
        except ImportError:
            edge_installed = False
        status["edge"] = {
            "ready": edge_installed,
            "package_installed": edge_installed,
            "model_present": None,
            "key_configured": None,
            "detail": "ready (needs internet at synth time)" if edge_installed
                      else "edge-tts not installed",
        }
    except Exception as exc:  # noqa: BLE001 — status probe must never crash
        status["edge"] = {
            "ready": False,
            "package_installed": False,
            "model_present": None,
            "key_configured": None,
            "detail": f"status probe failed ({exc.__class__.__name__})",
        }

    # -- piper --
    try:
        try:
            import piper  # type: ignore[import-not-found]  # noqa: F401
            piper_installed = True
        except ImportError:
            # Same resolution as the synth path (PIPER_BIN + interpreter
            # neighbor), not a bare PATH probe — otherwise status reports
            # "not installed" on uv-tool installs where the synth path works.
            piper_installed = _resolve_piper_binary() is not None
        model_present = any(
            _piper_model_for(lang) is not None for lang in _PIPER_MODELS
        )
        ready = piper_installed and model_present
        if ready:
            detail = "ready"
        elif not piper_installed:
            detail = "piper not installed"
        else:
            detail = "no Piper voice model downloaded yet"
        status["piper"] = {
            "ready": ready,
            "package_installed": piper_installed,
            "model_present": model_present,
            "key_configured": None,
            "detail": detail,
        }
    except Exception as exc:  # noqa: BLE001 — status probe must never crash
        status["piper"] = {
            "ready": False,
            "package_installed": False,
            "model_present": None,
            "key_configured": None,
            "detail": f"status probe failed ({exc.__class__.__name__})",
        }

    return status


# ── Entry point ───────────────────────────────────────────────────────

# Ordered provider list for the "auto" chain.
_AUTO_CHAIN = ("openai", "edge", "piper")

# Extensions we accept as a plausible synthesis target. Everything the three
# providers can actually emit, plus the common containers callers convert to.
_AUDIO_EXTS = {
    ".opus", ".ogg", ".oga", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".webm",
}


def _looks_like_swapped_args(out_path_arg: str) -> bool:
    """True when argv[1] reads like the TEXT, not the output path.

    say.py's positional order is ``<out_path> <text>`` — callers who invert
    it silently created Ogg files literally NAMED after the spoken sentence
    ("Das ist Test Nummer 1." landed as a repo-root file, found 2026-07-17).
    Heuristic: no known audio extension AND shaped like a sentence
    (contains whitespace and ends in sentence punctuation — note a trailing
    "." is NOT a suffix to pathlib, so ".opus"-style paths never match).
    Real callers (routes/voice.py::_say_cmd, daemon.js) always pass
    extension-carrying paths, which short-circuit on the extension check.
    """
    p = Path(out_path_arg)
    if p.suffix.lower() in _AUDIO_EXTS:
        return False
    stripped = out_path_arg.strip()
    has_ws = any(c.isspace() for c in stripped)
    return has_ws and stripped.endswith((".", "!", "?", "…"))


def main() -> int:
    if len(sys.argv) < 3:
        sys.stderr.write(
            "usage: say.py <out_path> <text> [<lang> [<voice> [<provider>]]]\n"
        )
        return 2
    if _looks_like_swapped_args(sys.argv[1]):
        sys.stderr.write(
            "say.py: argv[1] looks like spoken text, not an audio out-path "
            "(no audio extension, sentence-shaped) — arguments swapped?\n"
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

    # EU local-only egress guarantee: openai + edge both ship text to a cloud
    # (OpenAI / Microsoft), so they are disabled when CORVIN_TTS_LOCAL_ONLY=1
    # (EU_PRODUCTION). Enforced in _run so it covers BOTH the pinned-provider
    # path and the auto-chain — previously only voice_lib.sh honored the flag.
    local_only = os.environ.get("CORVIN_TTS_LOCAL_ONLY", "0") == "1"

    # Total-deadline budget (V2): whatever the chain does, it must finish
    # before the console's outer subprocess timeout SIGKILLs us — see the
    # _TOTAL_BUDGET_S comment above. Anchored at module import
    # (_PROCESS_START_MONOTONIC), not "now", so a slow interpreter start eats
    # into the budget instead of pushing the deadline past the outer SIGKILL
    # (V2-RESIDUAL 2026-07-20).
    deadline = _PROCESS_START_MONOTONIC + _TOTAL_BUDGET_S

    def _run(name: str) -> bool:
        if local_only and name in ("openai", "edge"):
            sys.stderr.write(
                f"say.py: provider '{name}' disabled by CORVIN_TTS_LOCAL_ONLY\n"
            )
            return False
        base_timeout = _PIPER_TIMEOUT_S if name == "piper" else _PROVIDER_TIMEOUT_S
        timeout_s = _clamped_timeout(base_timeout, deadline - time.monotonic())
        if timeout_s is None:
            sys.stderr.write(
                f"say.py: skipping provider '{name}' — total TTS budget "
                f"({_TOTAL_BUDGET_S:g}s) exhausted\n"
            )
            return False
        if name == "openai":
            return _try_openai(out_path, text, lang, voice_override, timeout_s)
        if name == "edge":
            return _try_edge(out_path, text, lang, timeout_s)
        if name == "piper":
            return _try_piper(out_path, text, lang, timeout_s)
        return False

    strict = os.environ.get("CORVIN_SAY_NO_FALLBACK", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

    if provider != "auto":
        # Preferred provider first; on failure fall through to the auto-chain so
        # voice always works even if the configured provider is temporarily broken
        # (e.g. missing API key, network outage, not installed).
        if _run(provider):
            sys.stdout.write(str(out_path))
            return 0
        if strict:
            # No-fallback (VOICE-1 isolation): a pinned provider must hard-fail
            # instead of masking a dead tier behind the auto-chain. Silent skip
            # (exit 0 + empty stdout) — the caller/test sees "no audio from the
            # pinned tier", never a phantom success written by a different tier.
            sys.stderr.write(
                f"say.py: pinned provider '{provider}' failed and "
                "CORVIN_SAY_NO_FALLBACK is set — not falling back\n"
            )
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
