"""native_tts.py — the CorvinOS TTS chain as a stable, importable API for plugins.

Chat TTS (``routes/voice.py``) speaks through ``corvin_operator/voice/scripts/say.py``:
auto chain openai -> edge -> piper, the operator pin ``CORVIN_TTS_PROVIDER``, the tenant
profile's ``tts_provider`` / ``tts_voice[_<lang>]``, ``CORVIN_TTS_LOCAL_ONLY``, the
length-scaled per-provider and whole-chain deadlines, the console's own OpenAI key and
endpoint resolution. A plugin that narrates (CorvinTV) must sound and degrade EXACTLY like
that, so this module does not re-implement any of it: every value comes from the very
helpers the chat route calls (``_say_cmd``, ``_say_env``, ``_tts_timeout_for``,
``_say_provider``, ``_VOICE_SCRIPTS``). A change to the chat chain is a change here.

Two entry points:

* :func:`prepare` returns the exact ``argv`` / ``env`` / outer ``timeout_s`` for one
  synthesis, for a caller that runs the subprocess under its own supervision (a video job
  clamps it to its job budget and kills the process group on cancel).
* :func:`synthesize` runs it and returns a :class:`Synthesis` (path, provider, mime) or
  raises :class:`NativeTTSFailed` carrying the content-free stderr reasons.

``API_VERSION`` is bumped on any incompatible change; callers check it.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

API_VERSION = 1

#: The chain order say.py runs when nothing is pinned (say.py::_AUTO_CHAIN).
AUTO_CHAIN = ("openai", "edge", "piper")


class NativeTTSUnavailable(RuntimeError):
    """The chain cannot run at all on this installation (say.py missing)."""


class NativeTTSFailed(RuntimeError):
    """say.py ran but no provider produced audio (or it timed out / exited non-zero)."""

    def __init__(self, message: str, *, reasons: Optional[List[str]] = None):
        super().__init__(message)
        self.reasons = list(reasons or [])


@dataclass
class Prepared:
    argv: List[str]
    env: Dict[str, str]
    timeout_s: float
    out_path: Path
    say_path: Path


@dataclass
class Synthesis:
    path: Path
    provider: str          # "openai" | "edge" | "piper" | "" (unknown: an older say.py without the marker)
    mime: str
    reasons: List[str] = field(default_factory=list)


def _voice():
    """The chat TTS route module. Imported lazily (it pulls FastAPI in). Outside the console
    (a plugin's job process) ``routes/voice.py`` is loaded ON ITS OWN: the ``routes`` package
    ``__init__`` imports every router of the console, and some start background schedulers on
    import — seconds of start-up and stray threads in a process that only wants to speak."""
    mod = sys.modules.get(f"{__package__}.routes.voice")
    if mod is not None:
        return mod
    pkg_name = f"{__package__}.routes"
    if pkg_name not in sys.modules:
        routes_dir = Path(__file__).resolve().parent / "routes"
        stub = types.ModuleType(pkg_name)
        stub.__path__ = [str(routes_dir)]  # submodules resolve; the auto-import of all routers does not run
        stub.__package__ = pkg_name
        stub.__file__ = str(routes_dir / "__init__.py")
        sys.modules[pkg_name] = stub
    return importlib.import_module(f"{pkg_name}.voice")


def say_path() -> Path:
    """Where say.py lives in THIS installation (checkout or wheel-vendored ``_vendor``)."""
    return _voice()._VOICE_SCRIPTS / "say.py"


def pinned_provider() -> Optional[str]:
    """The provider chat TTS would pin right now (``CORVIN_TTS_PROVIDER`` beats the tenant
    profile; ``auto`` / unset = None = the auto chain)."""
    return _voice()._resolve_tts_provider()


def expected_provider() -> str:
    """The tier a healthy chain speaks with: the pin, else the head of the auto chain."""
    return pinned_provider() or AUTO_CHAIN[0]


def prepare(text: str, out_path: Path, lang: str) -> Prepared:
    """argv/env/timeout for one synthesis, byte-identical to what chat TTS runs."""
    v = _voice()
    sp = v._VOICE_SCRIPTS / "say.py"
    if not sp.is_file():
        raise NativeTTSUnavailable(f"CorvinOS TTS chain not found (say.py missing at {sp})")
    out_path = Path(out_path)
    return Prepared(argv=list(v._say_cmd(out_path, text, lang)), env=v._say_env(),
                    timeout_s=float(v._tts_timeout_for(text)), out_path=out_path, say_path=sp)


def provider_from_stderr(stderr: str) -> str:
    """The tier say.py reported (``say.py: provider=<tier>``); "" when it did not say."""
    marker = _voice()._say_provider(stderr or "")
    return marker.split(":", 1)[1] if ":" in marker else ""


def reasons_from_stderr(stderr: str, limit: int = 8) -> List[str]:
    """say.py's per-provider failure lines. They are content-free by say.py's own contract
    (no spoken text), trimmed to what a job report may carry."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip().startswith("say.py:")]
    return [ln[:200] for ln in lines if "provider=" not in ln][-limit:]


def mime_of(data: bytes) -> str:
    """The audio type of say.py's output by magic bytes (opus/ogg, mp3, wav, flac) - the
    chat route's own sniffer; the file name's extension says nothing about the provider's format."""
    return _voice()._detect_audio_mime(data)


def finish(prep: Prepared, returncode: int, stdout: str, stderr: str) -> Synthesis:
    """Interpret one finished say.py run exactly like chat TTS does (non-zero exit, empty
    stdout or an empty file = no audio)."""
    reasons = reasons_from_stderr(stderr)
    if returncode != 0:
        raise NativeTTSFailed(f"say.py exited {returncode}", reasons=reasons)
    size = prep.out_path.stat().st_size if prep.out_path.exists() else 0
    if size == 0 or not (stdout or "").strip():
        raise NativeTTSFailed("no TTS provider produced audio", reasons=reasons)
    return Synthesis(path=prep.out_path, provider=provider_from_stderr(stderr),
                     mime=mime_of(prep.out_path.read_bytes()[:16]), reasons=reasons)


def synthesize(text: str, out_path: Path, lang: str, *, timeout_s: Optional[float] = None) -> Synthesis:
    """Synthesize *text* into *out_path* through the chat TTS chain."""
    prep = prepare(text, out_path, lang)
    try:
        proc = subprocess.run(prep.argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=prep.env, timeout=min(prep.timeout_s, timeout_s or prep.timeout_s))
    except subprocess.TimeoutExpired as exc:
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        raise NativeTTSFailed(f"say.py exceeded {prep.timeout_s:g}s", reasons=reasons_from_stderr(err)) from None
    return finish(prep, proc.returncode, proc.stdout, proc.stderr)


def status() -> Dict[str, dict]:
    """say.py's own cheap provider introspection (``provider_status()``), {} when unavailable."""
    mod = getattr(_voice(), "_say_module", None)
    try:
        return dict(mod.provider_status()) if mod is not None else {}
    except Exception:  # noqa: BLE001 — a status probe never raises
        return {}
