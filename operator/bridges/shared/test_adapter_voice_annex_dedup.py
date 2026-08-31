#!/usr/bin/env python3
"""test_adapter_voice_annex_dedup.py — the voice annex must never be doubled.

Reported symptom: "Learning und Metapher werden zweimal ausgegeben." Root cause
in build_voice_summary's override + short-text paths:

  * the LERN-ZUGABE was appended UNCONDITIONALLY (`if want_appendix:`), while
    every other path — and the metapher right next to it — guards on
    `not _has_*_suffix(...)`. So a <voice> block that ALREADY carried a
    LERN-ZUGABE got a SECOND one; and
  * `_has_lern_zugabe_suffix`'s tail window (400) was too narrow: with a
    metapher bridge (~150-220 chars) sitting AFTER the annex opener, the opener
    "Und zur Einordnung," lands ~400-700 chars from the end and fell outside the
    window — so even a guarded check missed it, and the fresh (spurious) annex
    then pushed the ORIGINAL metapher out of ITS window → a second metapher too.

These tests pin: an override that already contains both annex + metapher gets
NEITHER re-appended, even when the annex opener sits far enough from the end that
the OLD 400-char window would have missed it.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _load_adapter():
    for m in ("adapter", "profile"):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    return adapter


class _AnnexProfile:
    """Fake voice profile whose audience block demands BOTH annex + metapher."""

    @staticmethod
    def for_tts_audience(lang: str = "de") -> str:
        # Must contain the LERN-ZUGABE marker and a METAPHER-BRÜCKE marker so
        # _audience_demands_appendix / _audience_demands_metapher both fire.
        return ("HÖRER-PROFIL — LERN-ZUGABE (PFLICHT): ende mit 'Und zur "
                "Einordnung,'. METAPHER-BRÜCKE: ende mit 'Bildlich gesprochen,'.")

    @staticmethod
    def chat_render_enabled() -> bool:
        return False


def _instrument(adapter):
    """Stub the two annex generators so we can COUNT re-append attempts.
    Each stub tags its output so a double-append would be visible in the text."""
    calls = {"appendix": 0, "metapher": 0}

    def fake_appendix(text: str, *, lang: str = "de") -> str:
        calls["appendix"] += 1
        return text + " Und zur Einordnung, DOPPELT_ANNEX."

    def fake_metapher(text: str, *, lang: str = "de") -> str:
        calls["metapher"] += 1
        return text + " Bildlich gesprochen, DOPPELT_METAPHER."

    adapter._append_lern_zugabe = fake_appendix
    adapter._append_metapher = fake_metapher
    adapter._resolve_voice_output_language = lambda *a, **k: "de"
    adapter._voice_profile = _AnnexProfile
    return calls


def test_override_with_both_markers_is_not_doubled() -> None:
    print("\n=== override already has annex+metapher → no re-append ===")
    adapter = _load_adapter()
    calls = _instrument(adapter)

    # An annex opener followed by ~500 chars (filler + metapher) so the opener
    # sits well past the OLD 400-char window but inside the widened 900 one.
    filler = "Das ist ein zusätzlicher erklärender Satz zum Konzept. " * 8  # ~430 chars
    override = (
        "Kurze fachliche Antwort in einem Satz. "
        "Und zur Einordnung, hier das Konzept in ein bis zwei Sätzen erklärt. "
        + filler
        + "Bildlich gesprochen, ein einziger anschaulicher Vergleich zum Schluss."
    )
    assert len(override) > 500  # opener is definitely >400 chars from the end

    out = adapter.build_voice_summary("irrelevanter langer Text " * 50,
                                      max_chars=400, override=override)

    assert calls["appendix"] == 0, (
        f"LERN-ZUGABE re-appended although already present (calls={calls})")
    assert calls["metapher"] == 0, (
        f"metapher re-appended although already present (calls={calls})")
    assert "DOPPELT_ANNEX" not in out
    assert "DOPPELT_METAPHER" not in out
    print(f"  OK — no re-append (appendix={calls['appendix']}, "
          f"metapher={calls['metapher']})")


def test_widened_window_catches_far_opener() -> None:
    print("\n=== _has_lern_zugabe_suffix sees opener the OLD 400 window missed ===")
    adapter = _load_adapter()
    # Opener ~500 chars from the end: old window=400 → False, new window=900 → True.
    text = ("Und zur Einordnung, das Konzept. "
            + ("Weiterer Fülltext zum Auffüllen des Fensters. " * 10)
            + "Bildlich gesprochen, ein Bild.")
    assert adapter._has_lern_zugabe_suffix(text) is True, (
        "widened window must detect an annex opener a metapher pushed back")
    assert adapter._has_lern_zugabe_suffix(text, window=400) is False, (
        "sanity: the OLD 400-char window genuinely missed it (the bug)")
    print("  OK — 900-window True, 400-window False (bug reproduced + fixed)")


def test_override_without_markers_still_gets_annex() -> None:
    print("\n=== override WITHOUT markers → annex+metapher added once ===")
    adapter = _load_adapter()
    calls = _instrument(adapter)
    out = adapter.build_voice_summary("x " * 50, max_chars=400,
                                      override="Nackte Antwort ganz ohne Zugabe.")
    assert calls["appendix"] == 1, "annex must be added once when absent"
    assert calls["metapher"] == 1, "metapher must be added once when absent"
    assert "DOPPELT_ANNEX" in out and "DOPPELT_METAPHER" in out
    print("  OK — each added exactly once when absent")


def main() -> int:
    test_override_with_both_markers_is_not_doubled()
    test_widened_window_catches_far_opener()
    test_override_without_markers_still_gets_annex()
    print("\nAll voice-annex dedup tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
