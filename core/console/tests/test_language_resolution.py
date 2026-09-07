"""Test language resolution for web chat turns."""

from core.console.language_resolution import (
    resolve_turn_language,
    store_language_context_in_metadata,
)
from core.language import LanguageSource


class TestResolveTurnLanguage:
    """Test resolve_turn_language() function."""

    def test_profile_priority_german(self):
        """Profile='de' → resolve to German (CANONICAL)."""
        ctx = resolve_turn_language(
            user_text="Hello, check this code",
            response_text="",
            profile_lang="de"
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.PROFILE
        assert ctx.confidence == 1.0
        assert ctx.profile_set is True

    def test_input_priority_german(self):
        """No profile, user input German → resolve to German."""
        ctx = resolve_turn_language(
            user_text="Überprüf diesen Code",
            response_text="",
            profile_lang=None
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.INPUT
        assert ctx.confidence == 0.95
        assert ctx.profile_set is False

    def test_response_priority_german(self):
        """No profile/input, response German → resolve to German."""
        ctx = resolve_turn_language(
            user_text="123 test",  # ambiguous
            response_text="Das ist eine Antwort.",
            profile_lang=None
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.RESPONSE
        assert ctx.confidence == 0.85

    def test_system_default_fallback(self):
        """Nothing detected → system default."""
        ctx = resolve_turn_language(
            user_text="",
            response_text="",
            profile_lang=None,
            system_default="en"
        )
        assert ctx.resolved_lang == "en"
        assert ctx.source == LanguageSource.SYSTEM
        assert ctx.confidence == 0.5

    def test_custom_system_default(self):
        """Custom system default."""
        ctx = resolve_turn_language(
            user_text="",
            response_text="",
            profile_lang=None,
            system_default="de"
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.SYSTEM

    def test_profile_whitespace_treated_as_none(self):
        """Profile='   ' (whitespace) → treated as unset."""
        ctx = resolve_turn_language(
            user_text="Das ist Deutsch",
            response_text="",
            profile_lang="   "
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.INPUT


class TestStoreLanguageContextInMetadata:
    """Test storing language_context in turn metadata."""

    def test_store_language_context(self):
        """Language context is stored in metadata."""
        ctx = resolve_turn_language(
            user_text="Überprüf Code",
            profile_lang="de"
        )
        metadata = {}
        store_language_context_in_metadata(metadata, ctx)

        assert "language_context" in metadata
        lc = metadata["language_context"]
        assert lc["resolved_lang"] == "de"
        assert lc["source"] == "profile"
        assert lc["confidence"] == 1.0
        assert lc["profile_set"] is True

    def test_metadata_structure(self):
        """Stored metadata has expected fields."""
        ctx = resolve_turn_language(
            user_text="Hello",
            profile_lang="en"
        )
        metadata = {}
        store_language_context_in_metadata(metadata, ctx)

        lc = metadata["language_context"]
        assert set(lc.keys()) == {"resolved_lang", "source", "confidence", "profile_set"}
        assert isinstance(lc["resolved_lang"], str)
        assert isinstance(lc["source"], str)
        assert isinstance(lc["confidence"], float)
        assert isinstance(lc["profile_set"], bool)
