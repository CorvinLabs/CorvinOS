"""Verify that Profile Identity settings (Language, Custom Instructions) actually work.

Test that:
1. Language setting is enforced in system prompt
2. Custom instructions are injected into system prompt
3. Tone and other settings are included
4. All settings survive save/load cycles
"""
import pytest
from pathlib import Path
from operator.bridges.shared import profile as prof


def test_language_setting_enforced_in_system_prompt(tmp_path: Path, monkeypatch):
    """Language setting MUST be enforced with 'ALWAYS answer in X' language directive."""
    monkeypatch.setattr(prof, "HOME", tmp_path)

    # Save profile with German language
    prof.save({
        "display_language": "de",
        "name": "Test User"
    })

    # Generate system prompt
    prompt = prof.for_system_prompt()

    # VERIFY: Must contain ALWAYS directive for German
    assert "ALWAYS answer in de" in prompt, f"German ALWAYS directive missing in:\n{prompt}"
    assert "do not switch languages" in prompt, "Language override clause missing"


def test_custom_instructions_injected_into_system_prompt(tmp_path: Path, monkeypatch):
    """Custom instructions must appear in system prompt exactly as saved."""
    monkeypatch.setattr(prof, "HOME", tmp_path)

    instructions = "Always structure replies with bullet points. Use metric units."
    prof.save({
        "custom_instructions": instructions
    })

    prompt = prof.for_system_prompt()

    # VERIFY: Custom instructions must appear verbatim
    assert f"- Custom instructions: {instructions}" in prompt, \
        f"Instructions not found in:\n{prompt}"


def test_multiple_identity_fields_combined(tmp_path: Path, monkeypatch):
    """All Identity fields should appear together in system prompt."""
    monkeypatch.setattr(prof, "HOME", tmp_path)

    prof.save({
        "name": "Silvio",
        "display_language": "de",
        "tone": "warm",
        "timezone": "Europe/Berlin",
        "voice_note_max_sentences": 3,
        "custom_instructions": "Use German technical terms. Format code in backticks."
    })

    prompt = prof.for_system_prompt()

    # VERIFY: All fields appear
    assert "- Name: Silvio" in prompt
    assert "ALWAYS answer in de" in prompt
    assert "- Tone: warm" in prompt
    assert "- Timezone: Europe/Berlin" in prompt
    assert "- Voice-note summary cap: 3 sentences" in prompt
    assert "- Custom instructions: Use German technical terms. Format code in backticks." in prompt


def test_language_overrides_incoming_language(tmp_path: Path, monkeypatch):
    """System prompt must explicitly say to ignore incoming message language."""
    monkeypatch.setattr(prof, "HOME", tmp_path)

    prof.save({"display_language": "en"})
    prompt = prof.for_system_prompt()

    # VERIFY: Explicitly says to override incoming language
    assert "do not switch languages because a message" in prompt
    assert "quoted snippet or a code sample is in another language" in prompt


def test_custom_instructions_survives_save_load(tmp_path: Path, monkeypatch):
    """Custom instructions must be persisted and reloaded correctly."""
    monkeypatch.setattr(prof, "HOME", tmp_path)

    original_instructions = "This is a longer set of custom instructions. Use it everywhere."

    # Save
    prof.save({"custom_instructions": original_instructions})

    # Load fresh instance (simulates application restart)
    loaded = prof.load()

    # VERIFY: Instructions survived save/load
    assert loaded.get("custom_instructions") == original_instructions


def test_empty_profile_returns_empty_system_prompt(tmp_path: Path, monkeypatch):
    """Profile with no fields set should return empty string (no token waste)."""
    monkeypatch.setattr(prof, "HOME", tmp_path)

    prof.save({})
    prompt = prof.for_system_prompt()

    # VERIFY: Empty profile = zero token cost
    assert prompt == ""


def test_none_fields_not_included_in_system_prompt(tmp_path: Path, monkeypatch):
    """Null/None fields should not appear in system prompt."""
    monkeypatch.setattr(prof, "HOME", tmp_path)

    prof.save({
        "name": "User",
        "custom_instructions": None,  # Explicitly null
        "tone": None
    })

    prompt = prof.for_system_prompt()

    # VERIFY: Only set field appears
    assert "- Name: User" in prompt
    assert "Custom instructions" not in prompt
    assert "Tone" not in prompt


@pytest.mark.parametrize("lang", ["de", "en", "es", "fr", "zh-Hans"])
def test_language_setting_appears_for_all_supported_langs(tmp_path: Path, monkeypatch, lang: str):
    """Each supported language must appear with ALWAYS directive."""
    monkeypatch.setattr(prof, "HOME", tmp_path)

    prof.save({"display_language": lang})
    prompt = prof.for_system_prompt()

    assert f"ALWAYS answer in {lang}" in prompt


def test_voice_note_max_sentences_affects_prompt(tmp_path: Path, monkeypatch):
    """Voice note sentence cap must appear in system prompt."""
    monkeypatch.setattr(prof, "HOME", tmp_path)

    prof.save({"voice_note_max_sentences": 5})
    prompt = prof.for_system_prompt()

    assert "- Voice-note summary cap: 5 sentences" in prompt


def test_custom_instructions_can_be_deleted(tmp_path: Path, monkeypatch):
    """Setting custom_instructions to None should remove it from prompt."""
    monkeypatch.setattr(prof, "HOME", tmp_path)

    # Set initial instructions
    prof.save({"custom_instructions": "Original instructions", "name": "User"})
    prompt1 = prof.for_system_prompt()
    assert "Original instructions" in prompt1

    # Delete by setting to None
    prof.save({"custom_instructions": None, "name": "User"})
    prompt2 = prof.for_system_prompt()

    # VERIFY: Instructions gone but name remains
    assert "Original instructions" not in prompt2
    assert "- Name: User" in prompt2
