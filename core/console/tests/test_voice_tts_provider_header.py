"""``X-Corvin-TTS-Provider`` must name the TIER that spoke, not the mechanism.

say.py leads with openai and falls back to edge then piper, so "the subprocess
produced audio" says nothing about which of the three actually synthesized. The
header used to carry the literal string ``"say.py"`` for every subprocess
synthesis, which made the configured priority chain unverifiable from outside
the box — the one observable that claims to report the provider reported a
filename instead (found 2026-09-21 while proving openai → edge → piper E2E).

The contract now spans two modules: say.py writes ``say.py: provider=<tier>``
to stderr on success, and ``routes/voice.py::_say_provider`` reads it. Both
halves are pinned here, because a rename on either side degrades silently — the
header would simply go back to saying "say.py" and nothing would fail.

The POSITIVE side (a real say.py run reporting a real tier) needs a working
provider, i.e. network or an installed Piper model, so it is not asserted here;
it is proven live against the running console. What IS asserted without any
such dependency is the negative control below: on failure say.py must emit NO
marker, so the header can never name a tier that did not speak.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import voice as V

_SAY_PY = (Path(__file__).resolve().parents[3]
           / "corvin_operator" / "voice" / "scripts" / "say.py")


# ── the reader ──────────────────────────────────────────────────────────────

def test_marker_names_the_tier():
    stderr = ("say.py: OpenAI TTS failed: PermissionDeniedError status=403\n"
              "say.py: provider=edge\n")
    assert V._say_provider(stderr) == "say.py:edge"


@pytest.mark.parametrize("tier", ["openai", "edge", "piper"])
def test_every_chain_tier_is_reportable(tier):
    """A tier say.py can choose but the console cannot name would be reported
    as the bare mechanism — i.e. the fix would be silently inert for it."""
    assert V._say_provider(f"say.py: provider={tier}\n") == f"say.py:{tier}"


def test_absent_marker_stays_the_bare_mechanism():
    """No guessing. An older say.py, or a caller that did not capture stderr,
    leaves the tier genuinely unknown — and a guess derived from the chain
    order would read like a measurement while being an assumption."""
    assert V._say_provider("") == "say.py"
    assert V._say_provider("say.py: edge-tts failed: TimeoutError\n") == "say.py"


def test_unrecognised_token_is_not_echoed_into_the_header():
    """Fail-closed: the value lands in an HTTP header, so an unknown token from
    a future say.py (or a stderr line that merely looks like the marker) must
    not travel through unvalidated."""
    assert V._say_provider("say.py: provider=rm -rf /\n") == "say.py"
    assert V._say_provider("say.py: provider=\n") == "say.py"


def test_last_marker_wins():
    """say.py emits the marker once, on the winning tier. If a future change
    ever emitted per-attempt lines, the final one is the tier that served."""
    assert V._say_provider(
        "say.py: provider=openai\nsay.py: provider=piper\n") == "say.py:piper"


# ── the writer ──────────────────────────────────────────────────────────────

def test_say_py_emits_the_exact_marker_the_console_parses():
    """Pin the two halves together. Either side can be renamed in isolation
    and the only symptom is a header quietly reverting to "say.py"."""
    src = _SAY_PY.read_text(encoding="utf-8")
    assert 'f"say.py: provider={name}\\n"' in src, (
        f"{_SAY_PY.name} no longer writes the marker "
        f"{V._SAY_PROVIDER_MARKER!r} that _say_provider parses")


def test_no_marker_when_every_tier_fails():
    """Negative control, dependency-free: local-only forbids openai + edge and
    a pinned openai with no fallback cannot reach piper either, so say.py exits
    0 having synthesized nothing. It must NOT claim a tier — otherwise the
    header could name a provider that never produced audio.

    This is also the check that would catch a marker moved out of the success
    path into `_run`, where it would fire on every attempt including failures.
    """
    out = Path(os.environ.get("TEMP", "/tmp")) / "corvin_tts_marker_probe.opus"
    env = {**os.environ,
           "CORVIN_TTS_LOCAL_ONLY": "1",
           "CORVIN_TTS_PROVIDER": "openai",
           "CORVIN_SAY_NO_FALLBACK": "1",
           "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            [sys.executable, str(_SAY_PY), str(out), "Hallo Welt.", "de"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, timeout=120,
        )
    finally:
        for p in (out, out.with_suffix(".wav")):
            try:
                p.unlink()
            except OSError:
                pass

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", (
        "a failed chain must not report an out-path")
    assert V._SAY_PROVIDER_MARKER not in proc.stderr, (
        f"say.py claimed a tier after synthesizing nothing: {proc.stderr!r}")
    assert V._say_provider(proc.stderr) == "say.py"
