"""Session-pinned language detection (ADR-0262)."""
from __future__ import annotations

from plugin_builder.language import DEFAULT_LANGUAGE, LanguagePin, detect_language


def test_umlaut_is_a_strong_german_signal():
    assert detect_language("Ich möchte ein Plugin bauen.") == "de"


def test_german_stopwords_without_umlauts():
    assert detect_language("Das Plugin soll mit dem Kalender sprechen.") == "de"


def test_english_default_on_no_signal():
    assert detect_language("") == DEFAULT_LANGUAGE
    assert detect_language("xyz123") == DEFAULT_LANGUAGE


def test_english_text_detected():
    assert detect_language("I want to build a plugin that helps me.") == "en"


def test_short_ja_nein_tokens_detect_german():
    assert detect_language("ja") == "de"
    assert detect_language("nein") == "de"
    assert detect_language("yes") == "en"


def test_pin_locks_after_first_resolve():
    pin = LanguagePin()
    assert pin.language is None
    assert pin.resolve("Ich möchte das gern besprechen.") == "de"
    # A later, clearly-English text must NOT flip the pinned language.
    assert pin.resolve("this is now pure English text") == "de"
    assert pin.language == "de"
