"""SkillCompiler — ADR-0087 M3.

Converts engine-agnostic SkillForge skills into the injection format
appropriate for the target engine.

Per-engine formats (M3)
-----------------------
claude_code  : <auto_skill> blocks (--append-system-prompt flag)
hermes       : <auto_skill> blocks (prepended to system role in JSON)
codex_cli    : <auto_skill> blocks (prepended as <SYSTEM> block)
opencode_cli : <auto_skill> blocks (prepended as <SYSTEM> block)
copilot_cli  : simplified text prefix (single-turn fallback)

All engines support system-prompt injection. The compiled format is identical
for all except Copilot, which degrades to plain-text fallback.

The compiler does not call collect_active_skills() itself to avoid a hard
dependency on the skill_forge package from the eci module. Callers (the adapter)
pass the pre-collected block string and the engine_id.

MUST NOT import anthropic (CI AST lint enforces).
"""

from __future__ import annotations


class SkillCompiler:
    """Stateless compiler — all methods are static."""

    # Capability string values for skills injection (M3)
    CAPABILITY_APPEND_SYSTEM_PROMPT = "append_system_prompt"  # Claude Code
    CAPABILITY_SYSTEM_MESSAGE = "system_message"              # Hermes (JSON role)
    CAPABILITY_PROMPT_PREFIX = "prompt_prefix"                # Copilot fallback

    @staticmethod
    def compile(skills_block: str | None, engine_id: str) -> str | None:
        """Convert a skills block (from collect_active_skills) to the engine's
        native injection format.

        Returns None if there are no skills or the block is empty.
        The returned string is ready to prepend to the system prompt or
        wrapped in engine-specific formatting.

        Args:
            skills_block: Raw <auto_skill> block from collect_active_skills
            engine_id: Target engine ID (claude_code, hermes_engine, codex_cli, etc.)

        Returns:
            Formatted skills block (or None if empty)
        """
        if not skills_block or not skills_block.strip():
            return None

        # M3: Per-engine format selection
        if engine_id == "copilot_cli":
            # Copilot: degrade to text summary (single-turn, no persistent state)
            return SkillCompiler._format_copilot_fallback(skills_block)

        # All other engines: standard <auto_skill> markdown format
        # Format is identical; transport differs (flag vs. role vs. prefix)
        return skills_block

    @staticmethod
    def _format_copilot_fallback(skills_block: str) -> str:
        """Copilot single-turn fallback: extract skill names and brief.

        Copilot cannot persist skills across turns, so we degrade to a
        simple text summary of available skills.
        """
        lines = []
        lines.append("Available skills (single-turn context only):")

        # Extract skill names from <auto_skill name="..."> tags
        import re
        skill_pattern = r'<auto_skill name="([^"]+)"'
        for match in re.finditer(skill_pattern, skills_block):
            skill_name = match.group(1)
            lines.append(f"  • {skill_name}")

        return "\n".join(lines) if lines else None

    @staticmethod
    def capability_for_engine(engine_id: str) -> str:
        """Return the skills-injection capability string for this engine.

        Used by the dispatcher to understand how to wire skills to the engine.
        """
        if engine_id == "claude_code":
            return SkillCompiler.CAPABILITY_APPEND_SYSTEM_PROMPT
        elif engine_id == "hermes_engine":
            return SkillCompiler.CAPABILITY_SYSTEM_MESSAGE
        elif engine_id == "copilot_cli":
            return SkillCompiler.CAPABILITY_PROMPT_PREFIX
        else:
            # Codex, OpenCode, and unknown engines default to system-prompt prepend
            return SkillCompiler.CAPABILITY_APPEND_SYSTEM_PROMPT

    @staticmethod
    def should_inject_via_system_prompt(engine_id: str) -> bool:
        """Return True if skills should be injected into the system prompt
        for this engine (vs. handled by the engine's native skill mechanism).

        Currently True for all engines: even Claude Code receives skills via
        system prompt as the baseline. The adapter may additionally wire up
        CC's native SkillTool on top, but that is not the compiler's concern.
        """
        return True

    @staticmethod
    def format_header(engine_id: str) -> str:
        """Return the header comment injected before the skills block.

        Used by the adapter to annotate which engine the block was compiled
        for (helpful for debugging / audit).
        """
        return f"<!-- skills compiled for engine: {engine_id} -->"
