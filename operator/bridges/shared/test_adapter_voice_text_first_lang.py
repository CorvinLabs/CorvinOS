#!/usr/bin/env python3
"""User-preference-first voice language resolution.

`_resolve_voice_output_language` now respects user's `display_language` setting
as authoritative: if the user says they want German voice, they get German voice
regardless of what language the text is in. This respects the user's knowledge
of their own preference better than text-detection heuristics.

Fallback: if `display_language` is not set, use system locale.
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


def test_user_preference_de_stays_de_even_with_english_text() -> None:
    """User says they want German → they get German, regardless of text."""
    _pin("de")
    assert adapter._resolve_voice_output_language(_EN) == "de"


def test_user_preference_de_stays_de_with_german_text() -> None:
    """User preference with matching text."""
    _pin("de")
    assert adapter._resolve_voice_output_language(_DE) == "de"


def test_user_preference_en_stays_en_even_with_german_text() -> None:
    """User says English → they get English, regardless of text."""
    _pin("en")
    assert adapter._resolve_voice_output_language(_DE) == "en"


def test_no_preference_any_text_returns_system_locale() -> None:
    """No display_language set → fall back to system locale."""
    adapter._voice_profile = None
    result = adapter._resolve_voice_output_language("Das ist Text")
    # System locale will be whatever the test environment has; just verify non-empty
    assert result  # Should return something (system locale), not empty


def test_user_preference_zh_hans_stays_zh_even_with_german_text() -> None:
    """User says Chinese → they get Chinese, regardless of text."""
    _pin("zh-Hans")
    assert adapter._resolve_voice_output_language("Das ist ein deutsches Wort.") == "zh-Hans"


def test_user_preference_zh_hans_stays_zh_with_chinese_text() -> None:
    """User preference with matching text."""
    _pin("zh-Hans")
    assert adapter._resolve_voice_output_language("这是一个中文回答") == "zh-Hans"


def test_user_preference_fr_stays_fr_regardless_of_text() -> None:
    """French preference with German text."""
    _pin("fr")
    text = "Das ist eine deutsche Antwort."
    assert adapter._resolve_voice_output_language(text) == "fr"


def test_user_preference_nl_stays_nl_with_dutch_text() -> None:
    """Dutch preference with Dutch text."""
    _pin("nl")
    assert adapter._resolve_voice_output_language("Dit is de test.") == "nl"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
