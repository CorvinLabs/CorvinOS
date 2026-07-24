"""Dedicated unit tests for tde.slash_command_parser.SlashCommandParser.

ADR-0215 F3: this parser previously had no test file of its own at all —
it was only exercised indirectly through test_tde_round2_hardening.py.
Coverage added here: the three command shapes, case-insensitivity (the
console's own inline regex in chat_runtime.py was `re.IGNORECASE` +
`.lower()`; the parser was not, until this fix — a regression here would
silently make `/Use-Engine ACS` behave differently from `/use-engine acs`),
and the ValueError contract for unknown engine names.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))

from tde.slash_command_parser import SlashCommandParser  # noqa: E402


@pytest.fixture()
def parser():
    return SlashCommandParser()


def test_use_engine_lowercase(parser):
    r = parser.parse("/use-engine tiered_delegation\nDo the thing")
    assert r.engine_override == "tiered_delegation"
    assert r.task_text == "Do the thing"
    assert r.debug_mode is False


@pytest.mark.parametrize("cmd", [
    "/use-engine ACS do it",
    "/Use-Engine acs do it",
    "/USE-ENGINE Acs do it",
])
def test_use_engine_case_insensitive(parser, cmd):
    # ADR-0215 F3 regression guard: chat_runtime.py now delegates to this
    # parser as the single source of truth, so it MUST accept the same
    # case variations the old inline `re.IGNORECASE` regex did.
    r = parser.parse(cmd)
    assert r.engine_override == "acs"
    assert r.task_text == "do it"


def test_use_engine_same_line_and_next_line(parser):
    same_line = parser.parse("/use-engine claude_code Fix the bug")
    assert same_line.task_text == "Fix the bug"
    next_line = parser.parse("/use-engine claude_code\nFix the bug")
    assert next_line.task_text == "Fix the bug"


def test_use_engine_unknown_raises(parser):
    with pytest.raises(ValueError, match="Unknown engine"):
        parser.parse("/use-engine quantum_supremacy do it")


def test_engine_auto(parser):
    r = parser.parse("/engine-auto Summarize this")
    assert r.engine_override is None
    assert r.debug_mode is False
    assert r.task_text == "Summarize this"


def test_engine_auto_does_not_match_autopilot(parser):
    # Word-boundary check: "/engine-autopilot" must fall through to the
    # "no command" branch, not be silently treated as /engine-auto.
    r = parser.parse("/engine-autopilot do something")
    assert r.task_text == "/engine-autopilot do something"
    assert r.engine_override is None
    assert r.debug_mode is False


def test_debug_engine(parser):
    r = parser.parse("/debug-engine Generate API docs")
    assert r.debug_mode is True
    assert r.engine_override is None
    assert r.task_text == "Generate API docs"


def test_debug_engine_case_insensitive(parser):
    r = parser.parse("/Debug-Engine Generate API docs")
    assert r.debug_mode is True
    assert r.task_text == "Generate API docs"


def test_plain_message_auto_detects(parser):
    r = parser.parse("Just a normal task")
    assert r.engine_override is None
    assert r.debug_mode is False
    assert r.task_text == "Just a normal task"


def test_format_help_lists_all_three_commands():
    help_text = SlashCommandParser.format_help()
    assert "/use-engine" in help_text
    assert "/engine-auto" in help_text
    assert "/debug-engine" in help_text
