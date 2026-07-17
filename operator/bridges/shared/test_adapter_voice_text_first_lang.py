#!/usr/bin/env python3
"""ADR-0194 Phase 2 — the spoken language follows the answer TEXT.

`_resolve_voice_output_language` used to be profile-FIRST: the per-turn detector
only got a say when the static `display_language` pin happened to be non-de/en. A
user pinned to `de` who received an English answer therefore heard it spoken in
German. Phase 2 inverts the contract — the text decides; the pin is only the
tie-breaker for text the de/en detector cannot speak to (ambiguous, or non-Latin
script, where a genuine zh-Hans/ja/ar user must keep their pin).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import adapter  # type: ignore  # noqa: E402


def _pin(display_language: str) -> None:
    class _P:
        @staticmethod
        def load():
            return {"display_language": display_language}
    adapter._voice_profile = _P


_EN = "This is clearly an English answer with the usual function words in it."
_DE = "Das ist eindeutig eine deutsche Antwort mit den ueblichen Funktionswoertern."


def test_english_text_beats_a_german_pin() -> None:
    """The regression this phase exists for."""
    _pin("de")
    assert adapter._resolve_voice_output_language(_EN) == "en"


def test_german_text_stays_german() -> None:
    _pin("de")
    assert adapter._resolve_voice_output_language(_DE) == "de"


def test_german_text_beats_an_english_pin() -> None:
    """Symmetry: the inversion must work in both directions."""
    _pin("en")
    assert adapter._resolve_voice_output_language(_DE) == "de"


def test_ambiguous_text_falls_back_to_the_pin() -> None:
    """No signal → the static pin is a better answer than a guess."""
    _pin("de")
    assert adapter._resolve_voice_output_language("OK.") == "de"


def test_non_latin_user_keeps_their_pin() -> None:
    """The de/en detector must never mask a genuine non-de/en user: it returns
    None on non-Latin script, so the pin survives."""
    _pin("zh-Hans")
    assert adapter._resolve_voice_output_language("这是一个中文回答") == "zh-Hans"


# ── Confidence margin (found 2026-07-17) ────────────────────────────────────
# The raw score() vote flipped short German answers to English because
# "was"/"in"/"an"/one-letter "a" only count as English in the word lists —
# score("Was war in Datei A los?") was de=1/en=3, so a de-pinned user got an
# English voice AND an English base prompt, silently self-consistent.


def test_short_german_question_with_bilingual_words_does_not_flip_to_english() -> None:
    """The reported flip case: bilingual overlap words must not fake an
    English majority. Below the confidence margin the detector says None
    and the pin wins."""
    _pin("de")
    text = "Was war in Datei A los?"
    assert adapter._detect_confident_de_en(text) != "en"
    assert adapter._resolve_voice_output_language(text) == "de"


def test_short_genuine_english_answer_still_detected_as_english() -> None:
    """The margin must not overshoot: a real short English answer keeps
    beating a German pin (the whole point of text-first)."""
    _pin("de")
    text = "The file was updated as requested."
    assert adapter._detect_confident_de_en(text) == "en"
    assert adapter._resolve_voice_output_language(text) == "en"


def test_tie_between_de_and_en_returns_none_and_pin_wins() -> None:
    """Equal (weak) evidence on both sides is a tie → None → static pin."""
    _pin("en")
    text = "Der Server is up."  # de: "der" = 1, en: "is" = 1
    assert adapter._detect_confident_de_en(text) is None
    assert adapter._resolve_voice_output_language(text) == "en"


def test_umlauts_count_as_a_strong_german_signal() -> None:
    """detect()'s umlaut tiebreak is reused: ä/ö/ü/ß never occur in English
    text, so they lift a sparse German sentence over the margin."""
    _pin("en")
    text = "Prüf das nochmal."  # only 1 function word, but an umlaut
    assert adapter._detect_confident_de_en(text) == "de"
    assert adapter._resolve_voice_output_language(text) == "de"


# ── Hardening (adversarial round, 2026-07-17) ───────────────────────────────


def test_german_answer_with_python_code_block_does_not_flip_to_english() -> None:
    """Code is English-keyword soup by construction (if/not/for/in/is/and/
    with/the ...) — before code-stripping, a German answer embedding a
    Python block scored a CONFIDENT "en" that no umlaut boost could beat."""
    _pin("de")
    text = (
        "Die Änderung ist drin und die Prüfung läuft jetzt sauber durch:\n"
        "```python\n"
        "if not all(x is None for x in results) and the_flag:\n"
        "    for item in results:\n"
        "        print(item, the_flag, is_done)\n"
        "```\n"
        "Sag Bescheid, wenn noch etwas fehlt."
    )
    assert adapter._detect_confident_de_en(text) == "de"
    assert adapter._resolve_voice_output_language(text) == "de"


def test_inline_code_spans_are_not_scored() -> None:
    _pin("de")
    text = "Die Funktion `is_done(x) if x else None` ist jetzt sauber, und alles läuft."
    assert adapter._detect_confident_de_en(text) == "de"


def test_short_one_sided_english_answer_is_still_detected() -> None:
    """One-sided evidence relaxation: with ZERO German hits, two English
    hits suffice — "Done. All tests pass." must keep beating a German pin
    instead of falling under the mixed-evidence ≥3 bar."""
    _pin("de")
    text = "Done. All tests pass."
    assert adapter._detect_confident_de_en(text) == "en"
    assert adapter._resolve_voice_output_language(text) == "en"


def test_short_one_sided_german_answer_is_still_detected() -> None:
    """Symmetry for the one-sided relaxation."""
    _pin("en")
    text = "Der Fix ist drin."  # 2 German hits, zero English signal
    assert adapter._detect_confident_de_en(text) == "de"
    assert adapter._resolve_voice_output_language(text) == "de"


def test_single_shared_function_word_is_not_enough_one_sided() -> None:
    """The one-sided bar stays at 2, not 1: a lone hit like "is" occurs in
    third Latin languages (nl/da/...) and must not override a genuine
    non-de/en pin."""
    _pin("nl")
    assert adapter._detect_confident_de_en("Dit is de test.") is None
    assert adapter._resolve_voice_output_language("Dit is de test.") == "nl"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
