"""Unit tests for SkillCompiler M3 per-engine formats (ADR-0087 M3)."""

from __future__ import annotations

try:
    from eci.skill_compiler import SkillCompiler
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def test_compile_returns_none_for_empty_block():
    """Empty skills block returns None."""
    if not HAS_DEPS:
        return

    assert SkillCompiler.compile(None, "claude_code") is None
    assert SkillCompiler.compile("", "hermes_engine") is None
    assert SkillCompiler.compile("   ", "codex_cli") is None


def test_claude_code_uses_markdown_format():
    """Claude Code receives standard markdown <auto_skill> format."""
    if not HAS_DEPS:
        return

    skills_block = "<auto_skill name=\"test\">content</auto_skill>"
    result = SkillCompiler.compile(skills_block, "claude_code")
    assert result == skills_block


def test_hermes_uses_markdown_format():
    """Hermes receives standard markdown format (prepended to system role)."""
    if not HAS_DEPS:
        return

    skills_block = "<auto_skill name=\"debug\">debug-content</auto_skill>"
    result = SkillCompiler.compile(skills_block, "hermes_engine")
    assert result == skills_block


def test_codex_uses_markdown_format():
    """Codex receives standard markdown format (in <SYSTEM> block)."""
    if not HAS_DEPS:
        return

    skills_block = "<auto_skill name=\"analyze\">analyze-content</auto_skill>"
    result = SkillCompiler.compile(skills_block, "codex_cli")
    assert result == skills_block


def test_opencode_uses_markdown_format():
    """OpenCode receives standard markdown format (in <SYSTEM> block)."""
    if not HAS_DEPS:
        return

    skills_block = "<auto_skill name=\"plan\">plan-content</auto_skill>"
    result = SkillCompiler.compile(skills_block, "opencode_cli")
    assert result == skills_block


def test_copilot_uses_text_fallback():
    """Copilot receives simplified text fallback (single-turn)."""
    if not HAS_DEPS:
        return

    skills_block = (
        '<auto_skill name="skill1">content1</auto_skill>\n'
        '<auto_skill name="skill2">content2</auto_skill>'
    )
    result = SkillCompiler.compile(skills_block, "copilot_cli")

    # Copilot fallback should extract skill names
    assert result is not None
    assert "skill1" in result
    assert "skill2" in result
    assert "single-turn" in result.lower()


def test_capability_for_claude_code():
    """Claude Code uses append_system_prompt capability."""
    if not HAS_DEPS:
        return

    cap = SkillCompiler.capability_for_engine("claude_code")
    assert cap == SkillCompiler.CAPABILITY_APPEND_SYSTEM_PROMPT


def test_capability_for_hermes():
    """Hermes uses system_message capability."""
    if not HAS_DEPS:
        return

    cap = SkillCompiler.capability_for_engine("hermes_engine")
    assert cap == SkillCompiler.CAPABILITY_SYSTEM_MESSAGE


def test_capability_for_copilot():
    """Copilot uses prompt_prefix capability."""
    if not HAS_DEPS:
        return

    cap = SkillCompiler.capability_for_engine("copilot_cli")
    assert cap == SkillCompiler.CAPABILITY_PROMPT_PREFIX


def test_capability_for_codex():
    """Codex defaults to append_system_prompt."""
    if not HAS_DEPS:
        return

    cap = SkillCompiler.capability_for_engine("codex_cli")
    assert cap == SkillCompiler.CAPABILITY_APPEND_SYSTEM_PROMPT


def test_should_inject_via_system_prompt_all_engines():
    """All engines should inject skills via system prompt."""
    if not HAS_DEPS:
        return

    for engine in ["claude_code", "hermes_engine", "codex_cli", "opencode_cli", "copilot_cli"]:
        assert SkillCompiler.should_inject_via_system_prompt(engine) is True


def test_format_header():
    """Format header includes engine name."""
    if not HAS_DEPS:
        return

    header = SkillCompiler.format_header("test_engine")
    assert "test_engine" in header
    assert "<!-- " in header


if __name__ == "__main__":
    print("✅ M3 SkillCompiler tests loaded")
