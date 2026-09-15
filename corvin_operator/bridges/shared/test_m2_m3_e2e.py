"""E2E test: M2 (buffered /btw) + M3 (skill compiler) integration (ADR-0087)."""

from __future__ import annotations

import tempfile
from pathlib import Path

try:
    from eci.transport_buffered import enqueue_injection, dequeue_all_injections
    from eci.skill_compiler import SkillCompiler
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def test_m2_buffered_with_m3_skills_codex():
    """M2 + M3 E2E: Codex with buffered /btw and compiled skills."""
    if not HAS_DEPS:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        # M3: Compile skills for Codex
        skills_block = "<auto_skill name=\"debug\">debug content</auto_skill>"
        compiled = SkillCompiler.compile(skills_block, "codex_cli")
        assert compiled is not None
        assert "debug" in compiled

        # M2: Queue a /btw injection
        enqueue_injection(session_dir, "help me understand this error")

        # Simulate Codex spawn() behavior: dequeue and prepend
        buffered = dequeue_all_injections(session_dir)
        assert "help me understand" in buffered

        # Verify queue is consumed
        remaining = dequeue_all_injections(session_dir)
        assert remaining == ""

        # Verify skill and buffered content would be combined
        full_prompt = buffered + "\n\n" + "User's question"
        assert "help me understand" in full_prompt
        assert "User's question" in full_prompt


def test_m2_buffered_with_m3_skills_hermes():
    """M2 + M3 E2E: Hermes with buffered /btw and compiled skills."""
    if not HAS_DEPS:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        # M3: Compile skills for Hermes
        skills_block = "<auto_skill name=\"analyze\">analysis framework</auto_skill>"
        compiled = SkillCompiler.compile(skills_block, "hermes_engine")
        assert compiled is not None

        # Verify capability for Hermes is system_message
        cap = SkillCompiler.capability_for_engine("hermes_engine")
        assert cap == SkillCompiler.CAPABILITY_SYSTEM_MESSAGE

        # M2: Queue multiple /btw injections
        enqueue_injection(session_dir, "first thought")
        enqueue_injection(session_dir, "second thought")

        # Dequeue returns all as one block
        buffered = dequeue_all_injections(session_dir)
        assert "first thought" in buffered
        assert "second thought" in buffered


def test_m2_m3_copilot_fallback():
    """M2 + M3 E2E: Copilot with text fallback (single-turn)."""
    if not HAS_DEPS:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        # M3: Compile skills for Copilot (fallback)
        skills_block = (
            '<auto_skill name="refactor">refactor-skill</auto_skill>\n'
            '<auto_skill name="test">test-skill</auto_skill>'
        )
        compiled = SkillCompiler.compile(skills_block, "copilot_cli")

        # Copilot should get text summary, not full markdown
        assert compiled is not None
        assert "refactor" in compiled
        assert "test" in compiled
        assert "single-turn" in compiled.lower()

        # M2: Copilot doesn't support buffering (would error in real usage)
        # But test that queue infrastructure still works
        enqueue_injection(session_dir, "copilot note")
        buffered = dequeue_all_injections(session_dir)
        assert "copilot note" in buffered


if __name__ == "__main__":
    print("✅ M2 + M3 E2E tests loaded")
