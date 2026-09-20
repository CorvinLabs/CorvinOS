#!/usr/bin/env python3
"""Guard: the TTS timeout budgets must scale with the text, and must nest.

WHY THIS IS LOAD-BEARING. Speech synthesis is not a constant-cost call — the
provider streams audio proportional to the text — but the budgets protecting it
used to be three flat numbers: the console waited 25 s
(``routes/voice.py::_TTS_TIMEOUT_S``), say.py gave its whole provider chain 22 s
(``_TOTAL_BUDGET_S``) and each network provider 10 s (``_PROVIDER_TIMEOUT_S``).

Measured on a corporate, TLS-intercepting link on 2026-09-20 — whole say.py
process, edge-tts tier, German prose, two runs each:

      150 chars … 3.1 s / 4.1 s        900 chars …  6.4 s /  6.5 s
      400 chars … 3.9 s / 4.6 s       1800 chars …  3.5 s / 14.8 s

Latency grows with length (~6.5 s per 1000 chars at the worst end) AND varies
about 4x for byte-identical input. So the flat 10 s passed a short greeting and
failed a real ~1000-char voice summary intermittently. With no OpenAI key and no
Piper model downloaded — the state of any fresh install — edge-tts is the ONLY
tier, so its timeout is the whole feature: ``/voice/tts`` answered its designed
silent 204 carrying ``edge-tts failed: TimeoutError``, and on
``/voice/session-summary`` the same failure is indistinguishable from "voice is
off". That is what "TTS im Chat geht nicht" was AFTER the cp1252 stdin hang of
the same day was fixed (see tests/test_voice_subprocess_encoding.py) — two
independent defects on one path, which is why fixing the first did not make the
feature work.

THE NESTING INVARIANT. Three budgets sit inside each other:

    console outer subprocess cap  >  say.py whole-chain deadline  >  per provider

If the outer one is ever the smaller, the console SIGKILLs say.py mid-synthesis
instead of letting it hit its own deadline — which orphans the Piper grandchild
and leaves a ``corvin_tts_*.wav`` sibling behind (the VOICE-10 failure). Two
files now compute their curve independently, so the ordering has to be asserted
rather than commented.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SAY = _REPO / "corvin_operator" / "voice" / "scripts" / "say.py"

# Representative lengths. 0 and 1 pin the floor; 400 is the designed spoken
# budget (_TTS_SUMMARIZE_MAX_CHARS); ~1000 is what the live console actually
# produced for one turn on 2026-09-20 (454 896 bytes of MP3); 4000 is the
# provider char limit (_TTS_PROVIDER_CHAR_LIMIT), i.e. the worst legal case.
_LENGTHS = (0, 1, 150, 400, 900, 1000, 1800, 4000)

# Worst observed whole-process seconds per length, from the table above. Used to
# assert the budget is above MEASURED reality, not merely above its own formula.
_MEASURED_WORST = {150: 4.1, 400: 4.6, 900: 6.5, 1800: 14.8}


def _load_say():
    """Import say.py as a module. It is a script on PATH, not a package member,
    so there is no import path for it — load it by file location."""
    assert _SAY.is_file(), (
        f"{_SAY.relative_to(_REPO)} is missing. If it MOVED, update this test — "
        "do not delete it: an unguarded budget curve is how this defect returns."
    )
    spec = importlib.util.spec_from_file_location("_say_under_test", _SAY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_voice_routes():
    """Import the console voice route module, or skip with a reason.

    Skipping is acceptable HERE and only here: this module's own dependency tree
    (fastapi + the console package) may be absent in a bare checkout, and the
    say.py half of the invariant is still asserted below either way. Every other
    assertion in this file fails rather than skips.
    """
    sys.path.insert(0, str(_REPO / "core" / "console"))
    try:
        from corvin_console.routes import voice as mod  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"console voice routes not importable here: {type(exc).__name__}: {exc}")
    return mod


@pytest.fixture(autouse=True)
def _no_operator_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measure the SHIPPED defaults.

    Both modules read their budgets from the environment at import time, so an
    operator override on the machine running the tests would silently change what
    is being asserted — and a CI box with CORVIN_TTS_TOTAL_BUDGET_S exported
    would turn this whole file into a test of that value.
    """
    for name in ("CORVIN_TTS_TIMEOUT_S", "CORVIN_TTS_TIMEOUT_PER_1K_S",
                 "CORVIN_TTS_PROVIDER_TIMEOUT_S",
                 "CORVIN_TTS_PROVIDER_TIMEOUT_PER_1K_S",
                 "CORVIN_TTS_TOTAL_BUDGET_S"):
        monkeypatch.delenv(name, raising=False)


# ── say.py: the budget must actually scale ───────────────────────────────────

def test_the_per_provider_cap_grows_with_the_text() -> None:
    """A flat cap is the defect. Prove the curve is strictly increasing."""
    say = _load_say()
    vals = [say.provider_timeout_for("x" * n) for n in _LENGTHS]
    assert all(b > a for a, b in zip(vals, vals[1:])), (
        f"provider_timeout_for is not increasing across {_LENGTHS}: {vals}. A "
        "constant cap cannot fit both a 20-char greeting and a 1000-char summary; "
        "that is exactly the regression this file exists to catch."
    )


@pytest.mark.parametrize("chars,worst", sorted(_MEASURED_WORST.items()))
def test_the_cap_is_above_measured_worst_case_latency(chars: int, worst: float) -> None:
    """The budget must clear REAL measured latency with headroom, not just be
    bigger than the previous constant. 2x the worst observed run is the margin
    chosen for the ~4x run-to-run variance seen on a TLS-intercepting link."""
    say = _load_say()
    cap = say.provider_timeout_for("x" * chars)
    assert cap >= 2 * worst, (
        f"{chars} chars: cap {cap:.1f}s is under 2x the worst MEASURED run "
        f"({worst}s) on a corporate link. edge-tts varies ~4x between identical "
        "runs, so a cap near the mean fails intermittently — which reads to the "
        "user as 'TTS works sometimes', the hardest form of this bug to report."
    )


def test_a_flat_10s_cap_would_have_failed_the_live_summary() -> None:
    """POSITIVE CONTROL: prove the OLD value really was too small.

    Without this, every assertion above passes for any sufficiently large number
    and the file never demonstrates there was a defect. The live console produced
    ~1000 chars for one turn; the measured curve needs ~6.5 s per 1000 chars at
    its worst, plus process start — comfortably over the old flat 10 s once the
    variance seen at 1800 chars is applied at all.
    """
    say = _load_say()
    assert say.provider_timeout_for("x" * 1000) > 10.0, (
        "the shipped cap for a 1000-char summary is still <= the old flat 10s, "
        "so nothing was actually fixed"
    )


def test_an_operator_pin_stays_an_absolute_pin() -> None:
    """``CORVIN_TTS_TOTAL_BUDGET_S`` is the documented escape hatch: when set it
    must win outright, not be scaled on top of. Re-imported under the env var
    because say.py reads it at module import."""
    os.environ["CORVIN_TTS_TOTAL_BUDGET_S"] = "7"
    try:
        say = _load_say()
        assert say.total_budget_for("x" * 4000) == pytest.approx(7.0), (
            "an explicit CORVIN_TTS_TOTAL_BUDGET_S pin was scaled instead of "
            "honoured; an operator bounding TTS tightly must get that bound"
        )
    finally:
        del os.environ["CORVIN_TTS_TOTAL_BUDGET_S"]


# ── The nesting invariant across both files ──────────────────────────────────

@pytest.mark.parametrize("chars", _LENGTHS)
def test_chain_deadline_leaves_room_for_one_provider_attempt(chars: int) -> None:
    say = _load_say()
    text = "x" * chars
    assert say.total_budget_for(text) > say.provider_timeout_for(text), (
        f"{chars} chars: say.py's whole-chain deadline "
        f"{say.total_budget_for(text):.1f}s does not exceed one provider attempt "
        f"({say.provider_timeout_for(text):.1f}s), so the FIRST tier is clamped "
        "below its own cap and no tier ever gets a full attempt."
    )


@pytest.mark.parametrize("chars", _LENGTHS)
def test_console_cap_stays_above_say_pys_own_deadline(chars: int) -> None:
    """THE invariant. The outer cap must be the larger one for every length, so
    say.py always reaches its own deadline and degrades the documented way
    (silent skip + a stderr reason on X-Corvin-Voice-Reason) instead of being
    SIGKILLed mid-synthesis, orphaning a Piper grandchild."""
    say = _load_say()
    voice = _load_voice_routes()
    text = "x" * chars
    outer = voice._tts_timeout_for(text)
    inner = say.total_budget_for(text)
    assert outer > inner, (
        f"{chars} chars: console outer cap {outer:.1f}s <= say.py chain deadline "
        f"{inner:.1f}s. The console would SIGKILL a synthesis say.py still "
        "believes it has budget for — the VOICE-10 orphaned-grandchild failure. "
        "Keep _TTS_TIMEOUT_PER_1K_S above 2x say.py's "
        "_PROVIDER_TIMEOUT_PER_1K_S."
    )


def test_the_invariant_test_would_notice_an_inverted_pair() -> None:
    """POSITIVE CONTROL for the test above: a comparison that can only ever pass
    is not a guard. Rebuild the same relation from deliberately inverted numbers
    and confirm the assertion is the thing that decides."""
    outer_curve = lambda n: 5.0 + 0.001 * n   # noqa: E731 — deliberately too small
    inner_curve = lambda n: 22.0 + 0.028 * n  # noqa: E731 — say.py's real shape
    assert not all(outer_curve(n) > inner_curve(n) for n in _LENGTHS), (
        "the inverted control pair compares as correctly nested, so the real "
        "comparison above cannot be trusted to detect an inversion"
    )


@pytest.mark.parametrize("chars", _LENGTHS)
def test_an_operator_pin_is_tracked_by_the_console_too(chars: int) -> None:
    """A pin on the CHILD's chain deadline must not break the nesting.

    ``CORVIN_TTS_TOTAL_BUDGET_S`` is read by say.py, so if only say.py honoured
    it an operator raising it would produce the exact SIGKILL the invariant
    forbids. The console reads the same variable for that reason.
    """
    voice = _load_voice_routes()
    os.environ["CORVIN_TTS_TOTAL_BUDGET_S"] = "300"
    try:
        say = _load_say()
        text = "x" * chars
        assert voice._tts_timeout_for(text) > say.total_budget_for(text), (
            "with CORVIN_TTS_TOTAL_BUDGET_S pinned high the console's outer cap "
            "no longer clears say.py's deadline — the pin must be tracked on "
            "BOTH sides or it re-creates the mid-synthesis kill"
        )
    finally:
        del os.environ["CORVIN_TTS_TOTAL_BUDGET_S"]


def test_piper_stays_under_even_the_shortest_outer_cap() -> None:
    """Piper is local: its cap is deliberately NOT length-scaled (model load, not
    streaming, dominates). It must still fit inside the floor of the outer cap,
    or a first-run local synth is killed on short text."""
    say = _load_say()
    voice = _load_voice_routes()
    assert say._PIPER_TIMEOUT_S < voice._tts_timeout_for(""), (
        f"Piper's flat {say._PIPER_TIMEOUT_S:g}s attempt exceeds the console's "
        f"floor {voice._tts_timeout_for(''):.1f}s — a slow first local synth "
        "would be SIGKILLed on a short greeting."
    )
