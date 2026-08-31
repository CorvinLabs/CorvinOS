"""Tests for /skill command parsing and handling."""

import pytest
from core.chat_runtime.skill_command import (
    parse_skill_command,
    detect_skill_creation_trigger,
)


class TestSkillCommandParsing:
    """Test /skill command parser."""

    def test_parse_skill_create_command(self):
        """Parse /skill create <prompt>."""
        cmd = parse_skill_command('/skill create "validate JSON files"')
        assert cmd.subcommand == "create"
        assert cmd.prompt == "validate JSON files"

    def test_parse_skill_create_single_quotes(self):
        """Parse with single quotes."""
        cmd = parse_skill_command("/skill create 'analyze code complexity'")
        assert cmd.subcommand == "create"
        assert cmd.prompt == "analyze code complexity"

    def test_parse_skill_status_command(self):
        """Parse /skill status <run_id>."""
        cmd = parse_skill_command("/skill status run-abc123")
        assert cmd.subcommand == "status"
        assert cmd.run_id == "run-abc123"

    def test_parse_skill_list_command(self):
        """Parse /skill list."""
        cmd = parse_skill_command("/skill list")
        assert cmd.subcommand == "list"

    def test_parse_invalid_command(self):
        """Reject non-/skill commands."""
        assert parse_skill_command("/other command") is None
        assert parse_skill_command("no slash here") is None

    def test_parse_missing_prompt(self):
        """Reject /skill create without prompt."""
        with pytest.raises(ValueError, match="Usage"):
            parse_skill_command("/skill create")

    def test_parse_unknown_subcommand(self):
        """Reject unknown subcommands."""
        with pytest.raises(ValueError, match="Unknown subcommand"):
            parse_skill_command("/skill delete something")


class TestSkillCreationTrigger:
    """Test natural language trigger detection."""

    def test_detect_german_erzeuge_trigger(self):
        """Detect 'erzeuge mir einen skill' trigger."""
        result = detect_skill_creation_trigger(
            "erzeuge mir einen skill der JSON validiert"
        )
        assert result is not None
        assert "JSON" in result or "validiert" in result

    def test_detect_german_erstelle_trigger(self):
        """Detect 'erstelle einen skill' trigger."""
        result = detect_skill_creation_trigger(
            "erstelle einen skill für log-analyse"
        )
        assert result is not None

    def test_detect_english_create_trigger(self):
        """Detect 'create a skill' trigger."""
        result = detect_skill_creation_trigger(
            "create a skill that validates JSON"
        )
        assert result is not None
        assert "validate" in result.lower() or "JSON" in result

    def test_detect_english_generate_trigger(self):
        """Detect 'generate a skill' trigger."""
        result = detect_skill_creation_trigger(
            "generate a skill for code analysis"
        )
        assert result is not None

    def test_no_trigger_in_normal_message(self):
        """No trigger in regular messages."""
        assert detect_skill_creation_trigger("Hello, how are you?") is None
        assert detect_skill_creation_trigger("What time is it?") is None

    def test_case_insensitive_trigger(self):
        """Trigger detection is case-insensitive."""
        result = detect_skill_creation_trigger(
            "ERZEUGE MIR EINEN SKILL der LOGS PARSED"
        )
        assert result is not None
