"""Piper local TTS: install, language detection, and model download."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .dependencies import pip_install as _pip_install


# Languages → Piper voices: the table lives in corvinOS.shared.voice_models
# (SSOT shared with the console's runtime provisioning and say.py/adapter.py).
# Only the menu labels are installer-specific. Female voices by default.
from corvinOS.shared import voice_models as _vm  # noqa: E402

_LABELS: dict[str, str] = {
    "de": "Deutsch     — Kerstin (female)",
    "en": "English     — Lessac (female)",
    "es": "Español     — Sharvard medium",
    "fr": "Français    — SIWIS (female)",
    "it": "Italiano    — Paola (female)",
    "nl": "Nederlands  — MLS medium",
    "pl": "Polski      — Gosia (female)",
    "pt": "Português   — Faber medium (BR)",
    "ru": "Русский     — Irina medium",
    "tr": "Türkçe      — DFKI medium",
    "uk": "Українська  — Lada x_low",
    "zh": "中文         — Huayan x_low",
    "sv": "Svenska     — NST medium",
    "da": "Dansk       — Talesyntese medium",
    "no": "Norsk       — Talesyntese medium",
    "cs": "Čeština     — Jirka medium",
    "fi": "Suomi       — Harri medium",
    "el": "Ελληνικά    — Rapunzelina low",
    "ar": "العربية     — Kareem medium",
}
_MODELS: dict[str, tuple[str, str]] = {
    lang: (_LABELS.get(lang, lang), rel) for lang, rel in _vm.PIPER_VOICES.items()
}

_HF_BASE = _vm.HF_BASE


def ensure_edge_tts() -> None:
    """Guarantee edge-tts is importable — the keyless middle TTS tier.

    edge-tts is declared as a base dependency in pyproject.toml, so a normal
    ``pip install corvinos`` already ships it. But the voice bridge can be
    pointed at a *separate* interpreter (e.g. a pre-existing conda/system
    Python via ``PYTHON`` in service.env) that has ``openai`` but not
    ``edge-tts`` — in which case the provider chain silently skips the middle
    tier (OpenAI → [edge missing] → Piper) and a keyless install loses its
    cloud fallback entirely. Installing it explicitly here — mirroring how
    Piper and faster-whisper are ensured — makes the OpenAI → edge → Piper
    order hold even on such non-standard interpreters.
    """
    try:
        import edge_tts  # type: ignore[import-not-found]  # noqa: F401
        print("  ✓ edge-tts already installed (keyless cloud TTS fallback)")
        return
    except ImportError:
        pass

    print("  Installing edge-tts (keyless cloud TTS fallback) via pip...")
    if _pip_install("edge-tts>=6.1.8"):
        print("  ✓ edge-tts installed")
    else:
        print("  ⚠ Could not install edge-tts — install manually: pip install edge-tts")


def ensure_piper(voice_config_dir: Path, interactive: bool = True) -> None:
    """Ensure Piper TTS (the offline fallback beneath edge-tts) is ready and
    download its voice model — unconditionally, not opt-in (ADR-0185 M2/M3).

    piper-tts is now a base dependency (pyproject.toml) with genuine
    win32/macOS/Linux wheels (cp39-abi3 stable ABI — forward-compatible with
    every supported Python, not just 3.9), so a plain ``pip install corvinos``
    already provides the `piper` binary on every platform. Without the model
    download also being unconditional, Piper would remain a permanently-
    skipped fallback tier for most installs (nobody ever had a reason to run
    the old opt-in `voice` extra), defeating the point of having it as a real
    zero-config fallback beneath edge-tts.
    """
    _install_piper(interactive)
    piper_bin = _piper_binary()
    # The RUNTIME TTS path uses the piper PYTHON API (say.py), which needs no
    # `piper` binary on PATH at all — and a `uv tool install` never exposes a
    # dependency's console script on PATH. So download the voice model whenever
    # EITHER the piper package imports OR the binary exists (INST-3/VOICE-4);
    # gating the model download on `which("piper")` left Piper a permanently-
    # skipped fallback tier for every uv-tool install. Only PIPER_BIN/service.env
    # wiring genuinely needs the binary, so that stays gated on it.
    if piper_bin or _piper_python_available():
        _setup_model(voice_config_dir, interactive)
    if piper_bin:
        _write_bin_env(voice_config_dir, piper_bin)
    else:
        print("  ⚠ 'piper' binary not found on PATH — PIPER_BIN not written to "
              "service.env (the runtime Python API path still works). Set "
              "PIPER_BIN manually only if you rely on the piper CLI.")


def _piper_binary() -> str | None:
    """Locate the `piper` executable, probing the interpreter's own bin/Scripts
    dir BEFORE PATH.

    A ``uv tool install`` / venv install places console scripts next to
    ``sys.executable`` but does not necessarily put them on the ambient PATH,
    so a bare ``shutil.which("piper")`` misses them (INST-3/VOICE-4).
    """
    exe = "piper.exe" if sys.platform == "win32" else "piper"
    cand = Path(sys.executable).parent / exe
    if cand.is_file():
        return str(cand)
    return shutil.which("piper")


def _piper_python_available() -> bool:
    """True when the ``piper`` Python package is importable.

    The runtime TTS path uses the Python API, which needs no binary on PATH —
    so its presence alone is enough to justify downloading the voice model.
    """
    import importlib.util  # noqa: PLC0415
    try:
        return importlib.util.find_spec("piper") is not None
    except (ImportError, ValueError):
        return False


# ── Install ────────────────────────────────────────────────────────────────

def _install_piper(interactive: bool) -> None:
    """Ensure the `piper` console-script is importable/on PATH.

    piper-tts is a base dependency (see pyproject.toml — ADR-0185), with
    genuine win32/macOS/Linux wheels, so this is normally a no-op: pip
    already installed it alongside corvinos itself. This function is a
    defensive fallback for the same edge case `ensure_edge_tts()` documents
    above: a voice bridge pointed at a separate interpreter that has some
    but not all base deps (e.g. via `pip install --no-deps` or a stale venv).
    Not gated behind Windows or `interactive` — Piper is no longer optional.
    """
    if shutil.which("piper"):
        print("  ✓ Piper TTS already installed")
        return

    print("  Installing piper-tts (offline TTS fallback beneath edge-tts) via pip...")
    installed = _pip_install("piper-tts")

    # Ensure pip's script dir is on PATH for this process — same PATH gotcha
    # as ensure_edge_tts()/faster-whisper installs on Windows/Linux user installs.
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        candidates: list[str] = []
        if appdata:
            python_dir = Path(appdata) / "Python"
            # The conventional `pip install --user` Scripts dir usually
            # includes a PythonXY version segment (e.g.
            # %APPDATA%\Python\Python311\Scripts) — a single hardcoded
            # `Python\Scripts` guess (no version segment) previously missed
            # this on every real Windows install (unverified-from-Linux
            # review finding). Try every PythonXY dir actually present,
            # plus the un-versioned form as a last-resort fallback.
            try:
                candidates.extend(
                    str(p / "Scripts") for p in sorted(python_dir.glob("Python3*")) if p.is_dir()
                )
            except OSError:
                pass
            candidates.append(str(python_dir / "Scripts"))
        current_path = os.environ.get("PATH", "")
        for scripts in candidates:
            if scripts and scripts not in current_path:
                current_path = scripts + ";" + current_path
        os.environ["PATH"] = current_path
    else:
        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = local_bin + ":" + os.environ.get("PATH", "")

    if shutil.which("piper"):
        print("  ✓ Piper TTS installed")
    elif installed:
        print("  ⚠ piper-tts installed but 'piper' not on PATH — re-open your shell if needed")
    else:
        print("  ⚠ Could not install piper-tts — install manually: pip install piper-tts")


# ── Model setup ────────────────────────────────────────────────────────────

def _setup_model(voice_config_dir: Path, interactive: bool) -> None:
    config_file = voice_config_dir / "config.json"
    model_dir = voice_config_dir / "piper-models"

    # Check if a model is already configured and present on disk
    existing = _find_existing_model(config_file)
    if existing:
        print(f"  ✓ Piper model already configured: {existing.name}")
        # A PREFETCHED model (install.sh/.ps1 download it BEFORE the wizard) would
        # otherwise return here having NEVER seeded display_language — leaving the
        # greeting/reply language unset. Seed it from the model's own language so
        # the profile language is set even on the prefetch path.
        _seed_profile_display_language(_config_lang_default(config_file) or _detect_language())
        return

    sys_lang = _detect_language()
    print()
    print(f"  Piper needs a voice model (~60–100 MB). Detected language: {sys_lang}.")
    print()

    if not interactive:
        # Non-interactive mode: auto-download the model for the DETECTED system
        # language, not a hardcoded English default — a German-locale user
        # (LANG=de_DE.UTF-8 or Windows "de-DE") must get a usable German voice
        # out of a non-interactive `corvin-install`, not silently fall back to
        # English (ADR-0185 M2 — sys_lang is always a valid _MODELS key,
        # _detect_language() itself defaults to "en" when unrecognized).
        # Seed the language FIRST — display_language must be set even if the
        # model download later fails (Windows CDN reset, offline, …); it drives
        # the reply/greeting language independent of whether a TTS model lands.
        _seed_profile_display_language(sys_lang)
        label, rel_path = _MODELS[sys_lang]
        print(f"  Non-interactive: downloading {label.strip()} model...")
        _download_model(sys_lang, rel_path, model_dir, config_file)
        # English too: it is the language every fallback path speaks, so an
        # offline install keeps a voice even for text the detector calls "en".
        if sys_lang != "en":
            _download_model("en", _MODELS["en"][1], model_dir, config_file)
        return

    # Build ordered menu: detected language first, rest alphabetically
    all_langs = sorted(_MODELS.keys())
    ordered = [sys_lang] + [l for l in all_langs if l != sys_lang]

    for idx, lang in enumerate(ordered, start=1):
        label, _ = _MODELS[lang]
        tag = "  ← system language (auto-detected)" if lang == sys_lang else ""
        print(f"    [{idx}] {label}{tag}")
    print("    [0] Skip — configure later in config.json")
    print()

    raw = input("  Download voice model? [1]: ").strip() or "1"
    if raw == "0":
        # Skipping the voice MODEL must NOT skip the language: display_language is
        # about the reply/greeting language, not TTS assets. Seed the detected one.
        _seed_profile_display_language(sys_lang)
        print("  ⚠ Skipping — set piper_model_<lang> in config.json later")
        return

    try:
        choice = int(raw)
        chosen_lang = ordered[choice - 1]
    except (ValueError, IndexError):
        _seed_profile_display_language(sys_lang)
        print(f"  ⚠ Unknown choice '{raw}' — skipping model download")
        return

    # Seed BEFORE the download so a failed/partial fetch still leaves the reply
    # language correctly preset (the seed is idempotent — _save_model_config
    # re-affirms it on success).
    _seed_profile_display_language(chosen_lang)
    _, rel_path = _MODELS[chosen_lang]
    _download_model(chosen_lang, rel_path, model_dir, config_file)


def _find_existing_model(config_file: Path) -> Path | None:
    """Return the first configured piper model path that actually exists on disk."""
    try:
        cfg = json.loads(config_file.read_text(encoding='utf-8'))
        for key, val in cfg.items():
            if key.startswith("piper_model_") and val:
                p = Path(val)
                if p.exists() and p.stat().st_size > 0:
                    return p
    except Exception:
        pass
    return None


def _config_lang_default(config_file: Path) -> str:
    """Return the voice language (``lang_default``) recorded in config.json, or "".

    Lets the prefetch path (model already on disk → early return in
    ``_setup_model``) recover the language to seed into ``display_language``."""
    try:
        cfg = json.loads(config_file.read_text(encoding='utf-8'))
        return str(cfg.get("lang_default") or "").strip()
    except Exception:
        return ""


def _detect_language() -> str:
    """Detect the system language — checks POSIX locale vars, then Windows locale."""
    supported = set(_MODELS.keys())
    for var in ("LC_ALL", "LANG", "LANGUAGE"):
        raw = os.environ.get(var, "")
        if raw:
            m = re.match(r"([a-zA-Z]{2,3})", raw)
            if m and m.group(1).lower() in supported:
                return m.group(1).lower()

    # Windows: POSIX env vars are typically absent; query the OS locale directly.
    if sys.platform == "win32":
        # GetUserDefaultLocaleName is the reliable, NON-deprecated source and
        # returns a BCP-47 tag like "de-DE". locale.getdefaultlocale() is
        # DEPRECATED since Python 3.11 and returns (None, None) on some Windows
        # configs — which silently defaulted a German box to "en" (part of the
        # fresh-install "language not preset" bug). Try kernel32 first, then the
        # deprecated helper as a fallback.
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(85)  # LOCALE_NAME_MAX_LENGTH
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):  # type: ignore[attr-defined]
                prefix = buf.value.replace("_", "-").split("-")[0].lower()
                if prefix in supported:
                    return prefix
        except Exception:
            pass
        try:
            import locale as _locale
            lang_code, _ = _locale.getdefaultlocale()  # deprecated fallback only
            if lang_code:
                prefix = lang_code.split("_")[0].lower()
                if prefix in supported:
                    return prefix
        except Exception:
            pass

    return "en"


def _download_model(lang: str, rel_path: str, model_dir: Path, config_file: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    name = rel_path.split("/")[-1]
    onnx_path = model_dir / f"{name}.onnx"
    json_path = model_dir / f"{name}.onnx.json"

    if onnx_path.exists() and onnx_path.stat().st_size >= _vm._MIN_MODEL_BYTES:
        print(f"  ✓ Model already present: {onnx_path.name}")
    else:
        # Absent, or truncated by an interrupted earlier download (it used to
        # count as "present" forever as long as it was non-empty).
        try:
            onnx_path.unlink()
        except OSError:
            pass
        print(f"  Downloading {onnx_path.name} (this may take a minute)...")
        # ROBUST (2026-07-28): On Windows, CDN connections reset for large files
        # (WinError 10054) even after successful transfer. Retry ONNX up to 3x.
        onnx_url = f"{_HF_BASE}/{rel_path}.onnx"
        ok = False
        for attempt in range(3):
            if attempt > 0:
                import time as _t
                _t.sleep(2)
                print(f"  (retry {attempt}/2 ONNX download...)")
            ok = _fetch(onnx_url, onnx_path, silent=False)
            if ok:
                break
        if not ok:
            print("  ⚠ Download failed after 3 attempts — try again later or download manually:")
            print(f"    URL : {onnx_url}")
            print(f"    Save: {onnx_path}")
            return
        print(f"  ✓ Downloaded {onnx_path.name}")

    # Download the model config (.onnx.json) if missing or empty — this can
    # happen on a partial install (ONNX downloaded, JSON fetch failed) or when
    # re-running corvin-install on an existing ONNX. Retry up to 3 times with a
    # short back-off; on Windows, CDN connections are sometimes reset for small
    # files (WinError 10054) even after a successful ONNX transfer.
    if not (json_path.exists() and json_path.stat().st_size > 0):
        json_url = f"{_HF_BASE}/{rel_path}.onnx.json"
        ok = False
        for attempt in range(3):
            if attempt > 0:
                import time as _t
                _t.sleep(2)
                print(f"  (retry {attempt}/2 for model config...)")
            ok = _fetch(json_url, json_path, silent=True)
            if ok:
                break
        if not ok:
            print("  ⚠ Failed to fetch model config — download it manually:")
            print(f"    URL : {json_url}")
            print(f"    Save: {json_path}")
            return

    _save_model_config(config_file, lang, str(onnx_path))


def _fetch(url: str, dest: Path, *, silent: bool = False) -> bool:
    """Download url → dest. True only for a COMPLETE file.

    Delegates to ``voice_models.fetch``: httpx → curl → urllib, each into a
    private ``.part`` file that must match Content-Length and is only then
    renamed into place. The previous in-place writes left a truncated model
    behind whenever a transfer broke (e.g. WinError 10054 mid-stream), and the
    urllib branch even accepted any non-empty file as success.
    """
    state = {"last": -1}

    def _progress(done: int, total: int) -> None:
        if silent or total <= 0:
            return
        pct = done * 100 // total
        if pct != state["last"]:
            state["last"] = pct
            print(f"\r  {pct:3d}%  {done // 1024 // 1024} MB / {total // 1024 // 1024} MB",
                  end="", flush=True)

    ok = _vm.fetch(url, dest, attempts=1, progress=_progress)
    if not silent and state["last"] >= 0:
        print()
    if not ok and not silent and sys.platform == "win32":
        print("  (urllib error: download interrupted — common on Windows, retrying next fetch)")
    return ok


def _save_model_config(config_file: Path, lang: str, onnx_path: str) -> None:
    try:
        cfg = json.loads(config_file.read_text(encoding='utf-8')) if config_file.exists() else {}
    except Exception:
        cfg = {}

    cfg[f"piper_model_{lang}"] = str(onnx_path)
    cfg.setdefault("lang_default", lang)
    # tmp + replace: config.json is read on every TTS call; a torn write
    # (Ctrl-C, AV lock, full disk) would disable every language at once.
    tmp = config_file.with_name(f"{config_file.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, config_file)
    print(f"  ✓ Saved piper_model_{lang} in config.json")

    _seed_profile_display_language(lang)


def _seed_profile_display_language(lang: str) -> None:
    """Propagate the install-time voice-language choice into
    ``profile.display_language`` — the setting that actually controls the
    LLM's default reply language (``i18n.resolve()``'s fallback chain).

    Without this, a fresh install where the user picks "Deutsch" gets
    German-sounding TTS but replies still default to English until the user
    manually runs ``/lang set de``. Reuses the exact same write path
    ``/lang set`` uses (``profile.set_value``) so there is no second source
    of truth — this only ever seeds a default, never locks it; the runtime
    override chain (`/lang set`, per-chat, per-turn auto-detect) is untouched.

    Best-effort: voice setup must never fail because this propagation step
    did.
    """
    lang = (lang or "").strip()
    if not lang:
        return
    try:
        # `corvin_operator/` has no `__init__.py` (the name collides with the stdlib
        # `operator` module), so put `corvin_operator/bridges/shared/` on sys.path and
        # import bare top-level modules — the established pattern (lang_cli.py,
        # adapter.py). Needed for both the profile writer AND i18n below.
        shared_dir = Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared"
        if str(shared_dir) not in sys.path:
            sys.path.insert(0, str(shared_dir))
        # Normalise through the SAME guard `/lang set` and the console PUT
        # validator use, so this THIRD write path can't persist a non-canonical
        # code (e.g. bare "zh" instead of "zh-Hans") that i18n.resolve() would
        # later reject → silent English fallback (troubleshooting #34).
        try:
            import i18n as _i18n  # type: ignore  # noqa: PLC0415
            lang = _i18n.normalise(lang) or lang
        except Exception:  # noqa: BLE001
            pass
        try:
            from corvin_console.profile import set_value as _set_value  # noqa: PLC0415
        except ImportError:
            import profile as _profile_mod  # type: ignore  # noqa: PLC0415
            _set_value = _profile_mod.set_value
        _set_value("display_language", lang)
    except Exception:
        pass


def _write_bin_env(voice_config_dir: Path, piper_bin: str) -> None:
    """Write PIPER_BIN (and FFMPEG_BIN if found) to service.env.

    The adapter runs under systemd with a stripped PATH — these explicit
    paths let it find the binaries without depending on login PATH.
    """
    env_file = voice_config_dir / "service.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if env_file.exists():
        lines = [l for l in env_file.read_text().splitlines()
                 if not l.startswith("PIPER_BIN=") and not l.startswith("FFMPEG_BIN=")]

    lines.append(f"PIPER_BIN={piper_bin}")

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        lines.append(f"FFMPEG_BIN={ffmpeg_bin}")

    env_file.write_text("\n".join(lines) + "\n")
    if sys.platform != "win32":
        env_file.chmod(0o600)
    print(f"  ✓ PIPER_BIN={piper_bin} written to service.env")
