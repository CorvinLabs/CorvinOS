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


# Supported languages and their HuggingFace model paths (relative to v1.0.0 root)
# Default to FEMALE voices (project default — the user can change the voice any
# time). edge-tts (the keyless cloud fallback) is already all-female; these Piper
# picks keep the LOCAL default female too. Male defaults (Thorsten/Riccardo/
# Darkman) were swapped for verified female voices (Kerstin/Paola/Gosia).
_MODELS: dict[str, tuple[str, str]] = {
    "de": ("Deutsch     — Kerstin (female)",   "de/de_DE/kerstin/low/de_DE-kerstin-low"),
    "en": ("English     — Lessac (female)",    "en/en_US/lessac/medium/en_US-lessac-medium"),
    "es": ("Español     — Sharvard medium",    "es/es_ES/sharvard/medium/es_ES-sharvard-medium"),
    "fr": ("Français    — SIWIS (female)",     "fr/fr_FR/siwis/medium/fr_FR-siwis-medium"),
    "it": ("Italiano    — Paola (female)",     "it/it_IT/paola/medium/it_IT-paola-medium"),
    "nl": ("Nederlands  — MLS medium",         "nl/nl_NL/mls/medium/nl_NL-mls-medium"),
    "pl": ("Polski      — Gosia (female)",     "pl/pl_PL/gosia/medium/pl_PL-gosia-medium"),
    "pt": ("Português   — Faber medium (BR)",  "pt/pt_BR/faber/medium/pt_BR-faber-medium"),
    "ru": ("Русский     — Irina medium",       "ru/ru_RU/irina/medium/ru_RU-irina-medium"),
    "tr": ("Türkçe      — DFKI medium",        "tr/tr_TR/dfki/medium/tr_TR-dfki-medium"),
    "uk": ("Українська  — Lada x_low",         "uk/uk_UA/lada/x_low/uk_UA-lada-x_low"),
    "zh": ("中文         — Huayan x_low",       "zh/zh_CN/huayan/x_low/zh_CN-huayan-x_low"),
}

_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"


def ensure_piper(voice_config_dir: Path, interactive: bool = True) -> None:
    """Install Piper TTS and optionally download a voice model."""
    _install_piper(interactive)
    piper_bin = shutil.which("piper")
    if piper_bin:
        _setup_model(voice_config_dir, interactive)
        _write_bin_env(voice_config_dir, piper_bin)


# ── Install ────────────────────────────────────────────────────────────────

def _install_piper(interactive: bool) -> None:
    if shutil.which("piper"):
        print("  ✓ Piper TTS already installed")
        return

    # piper-tts has no Windows wheel for Python 3.10+; skip silently and let the
    # user use edge-tts (the cloud/edge TTS fallback that ships in the base install).
    if sys.platform == "win32":
        print("  ℹ Piper TTS not available on Windows (no wheel for Python 3.10+).")
        print("    Edge TTS will be used instead — no setup required.")
        return

    print()
    print("  Piper is a local text-to-speech engine.")
    print("  It activates automatically when OpenAI TTS is unavailable.")

    if not interactive:
        print("  Skipping — run corvin-install in a terminal to set up Piper TTS.")
        return

    answer = input("  Install Piper TTS? [Y/n]: ").strip().lower() or "y"
    if answer.startswith("n"):
        return

    print("  Installing piper-tts via pip...")
    installed = _pip_install("piper-tts")

    # Ensure pip --user bin dir is on PATH for this process
    if sys.platform == "win32":
        import os as _os
        appdata = _os.environ.get("APPDATA", "")
        scripts = str(Path(appdata) / "Python" / "Scripts") if appdata else ""
        if scripts and scripts not in _os.environ.get("PATH", ""):
            _os.environ["PATH"] = scripts + ";" + _os.environ.get("PATH", "")
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
        return

    sys_lang = _detect_language()
    print()
    print(f"  Piper needs a voice model (~60–100 MB). Detected language: {sys_lang}.")
    print()

    if not interactive:
        # Non-interactive mode: auto-download English model so Piper works out of the box
        _, rel_path = _MODELS["en"]
        print(f"  Non-interactive: downloading default English model...")
        _download_model("en", rel_path, model_dir, config_file)
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
        print("  ⚠ Skipping — set piper_model_<lang> in config.json later")
        return

    try:
        choice = int(raw)
        chosen_lang = ordered[choice - 1]
    except (ValueError, IndexError):
        print(f"  ⚠ Unknown choice '{raw}' — skipping model download")
        return

    _, rel_path = _MODELS[chosen_lang]
    _download_model(chosen_lang, rel_path, model_dir, config_file)


def _find_existing_model(config_file: Path) -> Path | None:
    """Return the first configured piper model path that actually exists on disk."""
    try:
        cfg = json.loads(config_file.read_text())
        for key, val in cfg.items():
            if key.startswith("piper_model_") and val:
                p = Path(val)
                if p.exists() and p.stat().st_size > 0:
                    return p
    except Exception:
        pass
    return None


def _detect_language() -> str:
    """Detect the system language — checks POSIX locale vars, then Windows locale."""
    supported = set(_MODELS.keys())
    for var in ("LC_ALL", "LANG", "LANGUAGE"):
        raw = os.environ.get(var, "")
        if raw:
            m = re.match(r"([a-zA-Z]{2,3})", raw)
            if m and m.group(1).lower() in supported:
                return m.group(1).lower()

    # Windows: POSIX env vars are typically absent; use locale module instead.
    if sys.platform == "win32":
        try:
            import locale as _locale
            # Returns e.g. ('de_DE', 'cp1252') or (None, None)
            lang_code, _ = _locale.getdefaultlocale()
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

    if onnx_path.exists() and onnx_path.stat().st_size > 0:
        print(f"  ✓ Model already present: {onnx_path.name}")
    else:
        print(f"  Downloading {onnx_path.name} (this may take a minute)...")
        if not _fetch(f"{_HF_BASE}/{rel_path}.onnx", onnx_path):
            print("  ⚠ Download failed — try again later")
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
    """Download url → dest. Returns True on success.

    Priority: httpx (best TLS, base dep) → curl → wget → urllib.
    Every tool falls through to the next on failure — not just on absence —
    so a Windows curl/urllib TLS or socket error never blocks the chain.
    """

    def _cleanup() -> None:
        try:
            if dest.exists() and dest.stat().st_size == 0:
                dest.unlink()
        except OSError:
            pass

    # 1. httpx — cross-platform, own TLS stack (no Windows Schannel issues),
    #    already a base dependency. Handles streaming + progress natively.
    try:
        import httpx as _httpx
        if not silent:
            print("  ", end="", flush=True)
        with _httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            done = 0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    fh.write(chunk)
                    done += len(chunk)
                    if not silent and total > 0:
                        pct = done * 100 // total
                        print(f"\r  {pct:3d}%  {done // 1024 // 1024} MB / {total // 1024 // 1024} MB",
                              end="", flush=True)
        if not silent:
            print()
        if dest.exists() and dest.stat().st_size > 0:
            return True
        _cleanup()
    except Exception:
        _cleanup()
        # fall through to curl

    # 2. curl — available on Windows 10+ and most Linux/macOS. Use POSIX path
    #    on Windows to avoid backslash escape issues with curl's -o flag.
    if shutil.which("curl"):
        dest_str = dest.as_posix() if sys.platform == "win32" else str(dest)
        flags = ["-L", "--silent" if silent else "--progress-bar", "-o", dest_str]
        r = subprocess.run(["curl"] + flags + [url], check=False)
        if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return True
        _cleanup()

    # 3. wget — common on Linux.
    if shutil.which("wget"):
        flags = ["-q" if silent else "--show-progress", "-O", str(dest)]
        r = subprocess.run(["wget"] + flags + [url], check=False)
        if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return True
        _cleanup()

    # 4. urllib — stdlib, always available. May raise WinError 10054 after a
    #    complete transfer on Windows; treat any non-empty file as success.
    try:
        import urllib.request
        if not silent:
            print("  (retrying with urllib ...)")

        def _report(block: int, block_size: int, total: int) -> None:
            if silent or total <= 0:
                return
            done = min(block * block_size, total)
            pct = done * 100 // total
            print(f"\r  {pct:3d}%  {done // 1024 // 1024} MB / {total // 1024 // 1024} MB",
                  end="", flush=True)

        urllib.request.urlretrieve(url, str(dest), reporthook=_report)
        if not silent:
            print()
        return dest.exists() and dest.stat().st_size > 0
    except Exception:
        if not silent:
            print()
        # WinError 10054: connection reset AFTER full transfer — file is valid.
        if dest.exists() and dest.stat().st_size > 0:
            return True
        return False


def _save_model_config(config_file: Path, lang: str, onnx_path: str) -> None:
    try:
        cfg = json.loads(config_file.read_text()) if config_file.exists() else {}
    except Exception:
        cfg = {}

    cfg[f"piper_model_{lang}"] = str(onnx_path)
    cfg.setdefault("lang_default", lang)
    config_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    print(f"  ✓ Saved piper_model_{lang} in config.json")


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
