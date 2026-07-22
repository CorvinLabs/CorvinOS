"""Verify that Profile Identity settings (Language, Custom Instructions) actually work.

Test that:
1. Language setting is enforced in system prompt
2. Custom instructions are injected into system prompt
3. Tone and other settings are included
4. All settings survive save/load cycles
5. The console-side ProfilePatch accepts the new 2000-char custom_instructions cap
"""
import sys
from pathlib import Path

import pytest


def _shared_profile_module():
    """Import the real, bare-name `profile` module the same way
    lang_cli.py / adapter.py do — `operator.bridges.shared.profile` is never
    importable as a dotted path (no `operator/__init__.py`, name collides
    with stdlib `operator`). Same helper as tests/test_installer_piper.py."""
    shared_dir = Path(__file__).resolve().parent.parent / "operator" / "bridges" / "shared"
    if str(shared_dir) not in sys.path:
        sys.path.insert(0, str(shared_dir))
    import profile as _profile_mod  # type: ignore  # noqa: PLC0415
    return _profile_mod


@pytest.fixture()
def prof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The shared profile module, redirected to a throwaway profile file.

    PROFILE_FILE is computed at import time (XDG), so tests must retarget the
    module attribute AND reset the mtime cache — never the real
    ~/.config/corvin-voice/profile.json.
    """
    mod = _shared_profile_module()
    monkeypatch.setattr(mod, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(mod, "_cache", None, raising=False)
    monkeypatch.setattr(mod, "_cache_mtime", 0.0, raising=False)
    return mod


def test_language_setting_enforced_in_system_prompt(prof):
    """Language setting MUST be enforced with 'ALWAYS answer in X' language directive."""
    prof.save({
        "display_language": "de",
        "name": "Test User"
    })

    prompt = prof.for_system_prompt()

    assert "ALWAYS answer in de" in prompt, f"German ALWAYS directive missing in:\n{prompt}"
    assert "do not switch languages" in prompt, "Language override clause missing"


def test_custom_instructions_injected_into_system_prompt(prof):
    """Custom instructions must appear in system prompt exactly as saved."""
    instructions = "Always structure replies with bullet points. Use metric units."
    prof.save({
        "custom_instructions": instructions
    })

    prompt = prof.for_system_prompt()

    assert f"- Custom instructions: {instructions}" in prompt, \
        f"Instructions not found in:\n{prompt}"


def test_multiple_identity_fields_combined(prof):
    """All Identity fields should appear together in system prompt."""
    prof.save({
        "name": "Silvio",
        "display_language": "de",
        "tone": "warm",
        "timezone": "Europe/Berlin",
        "voice_note_max_sentences": 3,
        "custom_instructions": "Use German technical terms. Format code in backticks."
    })

    prompt = prof.for_system_prompt()

    assert "- Name: Silvio" in prompt
    assert "ALWAYS answer in de" in prompt
    assert "- Tone: warm" in prompt
    assert "- Timezone: Europe/Berlin" in prompt
    assert "- Voice-note summary cap: 3 sentences" in prompt
    assert "- Custom instructions: Use German technical terms. Format code in backticks." in prompt


def test_language_overrides_incoming_language(prof):
    """System prompt must explicitly say to ignore incoming message language."""
    prof.save({"display_language": "en"})
    prompt = prof.for_system_prompt()

    assert "do not switch languages because a message" in prompt
    assert "quoted snippet or a code sample is in another language" in prompt


def test_custom_instructions_survives_save_load(prof):
    """Custom instructions must be persisted and reloaded correctly."""
    original_instructions = "This is a longer set of custom instructions. Use it everywhere."

    prof.save({"custom_instructions": original_instructions})

    loaded = prof.load(force=True)

    assert loaded.get("custom_instructions") == original_instructions


def test_empty_profile_returns_empty_system_prompt(prof):
    """Profile with no fields set should return empty string (no token waste)."""
    prof.save({})
    prompt = prof.for_system_prompt()

    assert prompt == ""


def test_none_fields_not_included_in_system_prompt(prof):
    """Null/None fields should not appear in system prompt."""
    prof.save({
        "name": "User",
        "custom_instructions": None,  # Explicitly null
        "tone": None
    })

    prompt = prof.for_system_prompt()

    assert "- Name: User" in prompt
    assert "Custom instructions" not in prompt
    assert "Tone" not in prompt


@pytest.mark.parametrize("lang", ["de", "en", "es", "fr", "zh-Hans"])
def test_language_setting_appears_for_all_supported_langs(prof, lang: str):
    """Each supported language must appear with ALWAYS directive."""
    prof.save({"display_language": lang})
    prompt = prof.for_system_prompt()

    assert f"ALWAYS answer in {lang}" in prompt


def test_voice_note_max_sentences_affects_prompt(prof):
    """Voice note sentence cap must appear in system prompt."""
    prof.save({"voice_note_max_sentences": 5})
    prompt = prof.for_system_prompt()

    assert "- Voice-note summary cap: 5 sentences" in prompt


def test_custom_instructions_can_be_deleted(prof):
    """Setting custom_instructions to None should remove it from prompt."""
    prof.save({"custom_instructions": "Original instructions", "name": "User"})
    prompt1 = prof.for_system_prompt()
    assert "Original instructions" in prompt1

    prof.save({"custom_instructions": None, "name": "User"})
    prompt2 = prof.for_system_prompt()

    assert "Original instructions" not in prompt2
    assert "- Name: User" in prompt2


# ── Console-side cap (the actual c732633 change: 500 → 2000 chars) ──────────

def _identity_fields_model():
    console_dir = Path(__file__).resolve().parent.parent / "core" / "console"
    if str(console_dir) not in sys.path:
        sys.path.insert(0, str(console_dir))
    from corvin_console.routes.profile import IdentityFields  # noqa: PLC0415
    return IdentityFields


def test_console_accepts_2000_char_custom_instructions():
    """The console IdentityFields must accept custom_instructions up to 2000
    chars (matches the frontend maxLength={2000})."""
    IdentityFields = _identity_fields_model()
    IdentityFields(custom_instructions="x" * 2000)  # must not raise


def test_console_rejects_2001_char_custom_instructions():
    """2001 chars must be rejected — the cap is load-bearing, not cosmetic."""
    IdentityFields = _identity_fields_model()
    with pytest.raises(Exception):
        IdentityFields(custom_instructions="x" * 2001)
