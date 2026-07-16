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


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
