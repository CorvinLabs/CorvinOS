"""voice_interpreter.py — shared say.py subprocess resolution. Single source
of truth for two things every say.py caller in this package needs:

  1. WHERE corvin_operator/voice/scripts lives (source tree vs. wheel install
     — corvin_operator/* is vendored under corvin_console/_vendor/corvin_operator/*
     in a wheel build, hatch_build.py).
  2. WHICH interpreter to run say.py with.

Before this module existed, routes/voice.py carried the correct (wheel-aware,
extras-checking) version of both, and voice_summary_orchestration.py carried
an independent, simpler copy: a hardcoded bare ``"python3"`` argv and a
non-wheel-aware path join. A bare ``"python3"`` resolves to whatever is first
on PATH — on most installs that is the SYSTEM python3, which has neither
``openai`` nor ``edge_tts`` installed (the console's own interpreter almost
always does, via core/console/bootstrap.sh). say.py's own OpenAI branch
catches that ImportError, writes one line to stderr, and returns False —
which reads exactly like "no provider configured", even when the operator's
OPENAI_API_KEY is valid and present. That divergence is the class of bug this
module exists to prevent: one resolver, every caller.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path


def resolve_voice_scripts_dir(console_pkg_dir: Path) -> Path:
    """Locate corvin_operator/voice/scripts from a corvin_console package dir.

    ``console_pkg_dir`` is the directory holding this file (or any other
    corvin_console module) — i.e. ``Path(__file__).resolve().parent``.

    Source-tree layout: ``<repo>/corvin_operator/voice/scripts``, three
    levels above ``core/console/corvin_console``. Wheel layout: vendored
    under ``corvin_console/_vendor/corvin_operator/voice/scripts``. Falls
    back to the (possibly nonexistent) source-tree guess when neither
    resolves, so callers get a consistent path to check with ``.exists()``.
    """
    repo_guess = console_pkg_dir.resolve().parents[2] / "corvin_operator" / "voice" / "scripts"
    if repo_guess.is_dir():
        return repo_guess
    vendored = console_pkg_dir.resolve() / "_vendor" / "corvin_operator" / "voice" / "scripts"
    return vendored if vendored.is_dir() else repo_guess


def say_interpreter(voice_scripts_dir: Path) -> list[str]:
    """argv prefix that runs say.py in an environment which HAS a TTS provider.

    Prefer THIS process's own interpreter: the installer provisions the
    console env with the TTS extras (edge-tts, openai, piper), so it is the
    one environment we can actually verify — ``find_spec`` here is a real
    check, not an assumption, and it costs nothing (no import).

    ``uv run`` is the fallback for a process running from an env without the
    extras (a bare system python3, or a minimal wheel install). It is
    deliberately NOT the default: uv resolves its project by walking up from
    the CWD, so the env say.py lands in depends on where the caller was
    launched from — inside the checkout it finds the repo venv, anywhere else
    it gets a bare ephemeral env with no edge-tts and TTS goes silently mute.

    ``--project`` pins that walk to the tree say.py itself lives in, so the
    fallback is at least deterministic rather than CWD-dependent.
    """
    if importlib.util.find_spec("edge_tts") or importlib.util.find_spec("openai"):
        return [sys.executable]
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--project", str(voice_scripts_dir.parents[2]), "python"]
    # No provider importable and no uv: say.py will exit 0 with no audio and
    # the caller answers its designed silent degrade. Spawn it anyway — its
    # own diagnostics on stderr are what a `voice-doctor` run needs to report
    # the cause.
    return [sys.executable]
