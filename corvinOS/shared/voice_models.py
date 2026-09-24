"""Offline voice models — the ONE place that knows which Piper voice serves a
language, where it lives, how to fetch it safely, and whether it is usable.

Used by three callers that used to disagree:
  * the installer (``corvinOS/installer/steps/piper.py``) — install-time download
  * the console (``routes/profile.py`` / ``routes/voice.py``) — a language change
    in Settings → Voice provisions that language's voice at runtime
  * the bridges (``/lang set``) — same trigger from a chat

Design constraints (why it looks the way it does):
  * **Atomic downloads.** A model is written to ``<name>.<pid>.part`` and only
    ``os.replace``d into place after its size matched the server's
    Content-Length. Previously a download interrupted mid-stream left a
    truncated ``.onnx`` that every later check treated as "present" (size > 0)
    — a permanently broken offline voice nobody could see.
  * **Validation, not existence.** ``model_valid`` checks size + a parseable
    ``.onnx.json``; an invalid model is deleted and re-fetched (self-healing).
  * **No cross-language lies.** ``model_path(lang)`` answers for THAT language
    only. The wrong-language last resort stays in say.py where it is logged.
  * **Never raises into a request.** ``request()`` returns immediately and runs
    the download on a daemon thread; ``status()`` reports progress.

stdlib + optional httpx only: this module is imported by the installer before
any optional dependency is guaranteed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from corvinOS.shared.paths import voice_config_dir

HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"

# language → path below HF_BASE (without extension). Female voices where one
# exists (project default). Every entry was verified to resolve (HTTP 200) on
# 2026-09-24. SSOT: say.py::_PIPER_MODELS and adapter.py::_PIPER_MODELS hold the
# last path segment of each entry — guarded by tests/test_voice_models.py.
PIPER_VOICES: dict[str, str] = {
    "de": "de/de_DE/kerstin/low/de_DE-kerstin-low",
    "en": "en/en_US/lessac/medium/en_US-lessac-medium",
    "es": "es/es_ES/sharvard/medium/es_ES-sharvard-medium",
    "fr": "fr/fr_FR/siwis/medium/fr_FR-siwis-medium",
    "it": "it/it_IT/paola/medium/it_IT-paola-medium",
    "nl": "nl/nl_NL/mls/medium/nl_NL-mls-medium",
    "pl": "pl/pl_PL/gosia/medium/pl_PL-gosia-medium",
    "pt": "pt/pt_BR/faber/medium/pt_BR-faber-medium",
    "ru": "ru/ru_RU/irina/medium/ru_RU-irina-medium",
    "tr": "tr/tr_TR/dfki/medium/tr_TR-dfki-medium",
    "uk": "uk/uk_UA/lada/x_low/uk_UA-lada-x_low",
    "zh": "zh/zh_CN/huayan/x_low/zh_CN-huayan-x_low",
    "sv": "sv/sv_SE/nst/medium/sv_SE-nst-medium",
    "da": "da/da_DK/talesyntese/medium/da_DK-talesyntese-medium",
    "no": "no/no_NO/talesyntese/medium/no_NO-talesyntese-medium",
    "cs": "cs/cs_CZ/jirka/medium/cs_CZ-jirka-medium",
    "fi": "fi/fi_FI/harri/medium/fi_FI-harri-medium",
    "el": "el/el_GR/rapunzelina/low/el_GR-rapunzelina-low",
    "ar": "ar/ar_JO/kareem/medium/ar_JO-kareem-medium",
}

# Languages the console offers that Piper has no voice for at all. They are
# spoken by the cloud tiers (OpenAI / edge-tts) only — reported as such, never
# as "ready offline".
NO_OFFLINE_VOICE = frozenset({"ja", "ko"})

_ALIASES = {"nb": "no", "nn": "no", "zh-hans": "zh", "zh-hant": "zh", "zh-cn": "zh",
            "zh-tw": "zh", "pt-br": "pt", "pt-pt": "pt"}

# A real Piper voice is ≥ 5 MB (x_low); anything under this is an error page
# or a truncated transfer.
_MIN_MODEL_BYTES = 1_000_000


class VoiceModelError(RuntimeError):
    """A voice model could not be provisioned; the message is operator-facing."""


def normalise_lang(code: str | None) -> str:
    """"de-DE" → "de", "zh-Hans" → "zh", "nb" → "no". Empty for None/""."""
    c = (code or "").strip().lower().replace("_", "-")
    if not c:
        return ""
    if c in _ALIASES:
        return _ALIASES[c]
    primary = c.split("-")[0]
    return _ALIASES.get(primary, primary)


def model_dir() -> Path:
    env = os.environ.get("CORVIN_PIPER_MODEL_DIR", "").strip()
    return Path(env) if env else voice_config_dir() / "piper-models"


def config_file() -> Path:
    return voice_config_dir() / "config.json"


def stem_for(lang: str) -> str | None:
    rel = PIPER_VOICES.get(normalise_lang(lang))
    return rel.rsplit("/", 1)[-1] if rel else None


def model_valid(onnx: Path) -> bool:
    """True when *onnx* and its ``.onnx.json`` look like a complete voice."""
    try:
        if not onnx.is_file() or onnx.stat().st_size < _MIN_MODEL_BYTES:
            return False
        cfg = Path(str(onnx) + ".json")
        json.loads(cfg.read_text(encoding="utf-8"))
        return True
    except (OSError, ValueError):
        return False


def _read_config() -> dict:
    try:
        data = json.loads(config_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def model_path(lang: str) -> Path | None:
    """A VALID model for exactly *lang*, or None. config.json first (what the
    installer recorded), then the canonical file name in model_dir()."""
    lc = normalise_lang(lang)
    if not lc:
        return None
    recorded = _read_config().get(f"piper_model_{lc}")
    if recorded and model_valid(Path(recorded)):
        return Path(recorded)
    stem = stem_for(lc)
    if stem:
        cand = model_dir() / f"{stem}.onnx"
        if model_valid(cand):
            return cand
    return None


def _write_config_key(lang: str, onnx: Path) -> None:
    """Record ``piper_model_<lang>`` atomically (tmp + replace): config.json is
    read on every TTS call, a torn write would disable every language."""
    cfg_path = config_file()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = _read_config()
    cfg[f"piper_model_{lang}"] = str(onnx)
    cfg.setdefault("lang_default", lang)
    tmp = cfg_path.with_name(f"{cfg_path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cfg_path)


# ── download ────────────────────────────────────────────────────────────────

def fetch(url: str, dest: Path, *, timeout: float = 120.0, attempts: int = 3,
          progress=None) -> bool:
    """Download *url* to *dest* atomically. True only for a complete file.

    httpx (own TLS stack) → curl → urllib, each attempt into a private
    ``.part`` file whose size must equal Content-Length when the server sends
    one. ``progress(done, total)`` is called for the httpx path.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f"{dest.name}.{os.getpid()}.part")
    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(min(2 ** attempt, 10))
        for method in (_fetch_httpx, _fetch_curl, _fetch_urllib):
            try:
                if method(url, part, timeout, progress) and part.stat().st_size > 0:
                    os.replace(part, dest)
                    return True
            except Exception:  # noqa: BLE001 — every failure falls through
                pass
            finally:
                if part.exists():
                    try:
                        part.unlink()
                    except OSError:
                        pass
    return False


def _fetch_httpx(url: str, part: Path, timeout: float, progress) -> bool:
    import httpx  # noqa: PLC0415 — optional dependency
    # trust_env honours HTTPS_PROXY; the OS store matters behind TLS-inspecting
    # corporate proxies, so prefer truststore when it is installed.
    verify: object = True
    try:
        import ssl  # noqa: PLC0415

        import truststore  # type: ignore[import-not-found]  # noqa: PLC0415
        verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        pass
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout, verify=verify) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(part, "wb") as fh:
            for chunk in r.iter_bytes(chunk_size=1 << 16):
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    return total == 0 or part.stat().st_size == total


def _fetch_curl(url: str, part: Path, timeout: float, progress) -> bool:
    curl = shutil.which("curl")
    if not curl:
        return False
    # -f: HTTP errors exit non-zero instead of saving the error page;
    # curl itself fails (exit 18) on a transfer shorter than Content-Length.
    r = subprocess.run([curl, "-fsSL", "--connect-timeout", "20", "--max-time", str(int(timeout * 5)),
                        "-o", str(part), url], capture_output=True, check=False)
    return r.returncode == 0


def _fetch_urllib(url: str, part: Path, timeout: float, progress) -> bool:
    import urllib.request  # noqa: PLC0415
    # urlretrieve raises ContentTooShortError on a short transfer.
    urllib.request.urlretrieve(url, str(part))
    return True


def download_piper_model(lang: str, progress=None) -> Path:
    """Fetch the Piper voice for *lang* (validated, atomic) and record it."""
    lc = normalise_lang(lang)
    rel = PIPER_VOICES.get(lc)
    if not rel:
        if lc in NO_OFFLINE_VOICE:
            raise VoiceModelError(f"no offline voice exists for '{lc}' — spoken by the online voices only")
        raise VoiceModelError(f"no offline voice is known for '{lc}'")
    stem = rel.rsplit("/", 1)[-1]
    d = model_dir()
    onnx = d / f"{stem}.onnx"
    cfg = Path(str(onnx) + ".json")
    if not model_valid(onnx):
        # Invalid (truncated / error page / missing json) — start clean.
        for p in (onnx, cfg):
            try:
                p.unlink()
            except OSError:
                pass
        if not fetch(f"{HF_BASE}/{rel}.onnx.json", cfg, timeout=60):
            raise VoiceModelError(f"could not download the {lc} voice configuration (network or proxy?)")
        if not fetch(f"{HF_BASE}/{rel}.onnx", onnx, timeout=300, progress=progress):
            raise VoiceModelError(f"could not download the {lc} voice model (network or proxy?)")
        if not model_valid(onnx):
            raise VoiceModelError(f"the downloaded {lc} voice is incomplete — it will be retried")
    _write_config_key(lc, onnx)
    return onnx


# ── engine ──────────────────────────────────────────────────────────────────

def piper_engine_available() -> bool:
    import importlib.util  # noqa: PLC0415
    try:
        if importlib.util.find_spec("piper") is not None:
            return True
    except (ImportError, ValueError):
        pass
    exe = "piper.exe" if sys.platform == "win32" else "piper"
    return (Path(sys.executable).parent / exe).is_file() or shutil.which("piper") is not None


def ensure_piper_engine() -> bool:
    """Install piper-tts into THIS interpreter when it is missing (self-healing
    for a tool venv built with --no-deps or damaged by an interrupted update)."""
    if piper_engine_available():
        return True
    uv = shutil.which("uv")
    cmds = []
    if uv:
        cmds.append([uv, "pip", "install", "--python", sys.executable, "piper-tts"])
    cmds.append([sys.executable, "-m", "pip", "install", "--quiet", "piper-tts"])
    for cmd in cmds:
        try:
            if subprocess.run(cmd, capture_output=True, timeout=600, check=False).returncode == 0:
                import importlib  # noqa: PLC0415
                importlib.invalidate_caches()
                if piper_engine_available():
                    return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


# ── status + background jobs ────────────────────────────────────────────────

def language_status(lang: str) -> dict:
    """What an operator needs to know about *lang*'s offline voice, without
    touching the network."""
    lc = normalise_lang(lang)
    job = _JOBS.get(lc)
    if job and job["state"] in ("queued", "downloading"):
        return dict(job)
    p = model_path(lc)
    if p is not None:
        state = "ready" if piper_engine_available() else "engine_missing"
        return {"lang": lc, "state": state, "model": p.name}
    if lc in NO_OFFLINE_VOICE or (lc and lc not in PIPER_VOICES):
        return {"lang": lc, "state": "online_only",
                "detail": "no offline voice exists for this language; the online voices speak it"}
    out = {"lang": lc, "state": "missing"}
    if job and job["state"] == "failed":
        out.update(state="failed", error=job.get("error", ""))
    return out


_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def request(lang: str, *, on_done=None) -> dict:
    """Make sure *lang* gets its offline voice. Never blocks, never raises.

    Returns the current status; starts at most one download per language per
    process. ``on_done(status_dict)`` runs on the worker thread afterwards
    (the console uses it to write the audit record)."""
    lc = normalise_lang(lang)
    if not lc:
        return {"lang": "", "state": "missing"}
    with _LOCK:
        cur = _JOBS.get(lc)
        if cur and cur["state"] in ("queued", "downloading"):
            return dict(cur)
        st = language_status(lc)
        if st["state"] in ("ready", "online_only"):
            return st
        job = {"lang": lc, "state": "queued", "started": time.time(), "done": 0, "total": 0}
        _JOBS[lc] = job

    def _progress(done: int, total: int) -> None:
        job["done"], job["total"] = done, total

    def _work() -> None:
        job["state"] = "downloading"
        try:
            if not ensure_piper_engine():
                raise VoiceModelError("the offline speech engine (piper-tts) could not be installed")
            download_piper_model(lc, progress=_progress)
            job.update(state="ready", finished=time.time())
        except Exception as e:  # noqa: BLE001 — surfaced via status, never raised
            job.update(state="failed", error=str(e)[:300], finished=time.time())
        if on_done:
            try:
                on_done(dict(job))
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_work, name=f"voice-model-{lc}", daemon=True).start()
    return dict(job)


def wait(lang: str, timeout: float = 600.0) -> dict:
    """Block until *lang*'s job finishes (tests, CLI). Returns the final status."""
    lc = normalise_lang(lang)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = _JOBS.get(lc)
        if not job or job["state"] not in ("queued", "downloading"):
            break
        time.sleep(0.2)
    return language_status(lc)


def main(argv: list[str] | None = None) -> int:
    """``python -m corvinOS.shared.voice_models <lang>…`` — provision now."""
    langs = (argv if argv is not None else sys.argv[1:]) or ["en"]
    rc = 0
    for lang in langs:
        request(lang)
        st = wait(lang)
        print(f"{st['lang'] or lang}: {st['state']}" + (f" — {st.get('error')}" if st.get("error") else ""))
        if st["state"] not in ("ready", "online_only"):
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
