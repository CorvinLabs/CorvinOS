"""Guard: chat output takes its status icons from ``status_markers``, never a literal.

ADR-2269 binds the icon vocabulary to ONE module. A status glyph typed as a string
literal in a chat-output module is a second source that can drift from the taught
meaning, so this test fails on the next one. The scan is AST-based: comments and
docstrings are not chat output and are ignored; every other string literal is.

Positive control: the scan must reach every module in ``CHAT_OUTPUT_MODULES`` and
count string literals in each of them, and the checker itself must flag a
synthetic literal and pass a docstring. A guard that scanned nothing would pass
vacuously, so both are asserted before the clean-tree assertion.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Modules whose strings reach an operator in a chat (bridge channels, console web
# chat, system notices, notification texts). Machine output (JSON badges, CLI logs,
# audit records) is deliberately not listed — see ADR-2269 Amendment 2026-10-20.
CHAT_OUTPUT_MODULES = (
    "corvin_operator/bridges/shared/adapter.py",
    "corvin_operator/bridges/shared/bg_scope.py",
    "corvin_operator/bridges/shared/completion_notify.py",
    "corvin_operator/bridges/shared/orchestration_router.py",
    "corvin_operator/bridges/shared/task_progress.py",
    "corvin_operator/bridges/shared/decision_registry.py",
    "corvin_operator/bridges/shared/scheduler.py",
    "corvin_operator/bridges/shared/ldd.py",
    "corvin_operator/bridges/shared/agents/claude_code.py",
    "corvin_operator/bridges/telegram/orchestration_handler.py",
    "corvin_operator/bridges/email/orchestration_handler.py",
    "core/console/corvin_console/chat_runtime.py",
    "core/console/corvin_console/routes/orchestration_events.py",
)

# Base code points of the vocabulary (⚠ and ℹ are also written with U+FE0F, which
# the base character covers). A bare base character in a chat string is a status.
_GLYPHS = ("✅", "❌", "⚠", "ℹ", "🔍", "⏳", "💡", "📌")


def find_literal_glyphs(source: str, filename: str = "<src>") -> list[tuple[int, str]]:
    """(line, text) for every non-docstring string literal carrying a status glyph."""
    tree = ast.parse(source, filename=filename)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            if any(g in node.value for g in _GLYPHS):
                hits.append((node.lineno, node.value[:80]))
    return hits


def count_string_literals(source: str) -> int:
    tree = ast.parse(source)
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))


class StatusGlyphLiteralGuardTests(unittest.TestCase):
    def test_checker_flags_a_literal_and_ignores_docstrings(self) -> None:
        # Positive control for the checker itself.
        src = 'def f():\n    """doc ✅ is fine"""\n    return "✅ done"\n'
        self.assertEqual(len(find_literal_glyphs(src)), 1)
        self.assertEqual(find_literal_glyphs('x = "⚠ bare"\n')[0][0], 1)
        self.assertEqual(find_literal_glyphs('x = "plain"  # ✅ comment\n'), [])

    def test_scan_reaches_every_chat_output_module(self) -> None:
        # Positive control for the scan: every listed file exists, parses and yields
        # string literals. Without this the clean-tree test below proves nothing.
        self.assertEqual(len(CHAT_OUTPUT_MODULES), 13)
        for rel in CHAT_OUTPUT_MODULES:
            path = REPO / rel
            self.assertTrue(path.is_file(), f"missing chat-output module: {rel}")
            self.assertGreater(count_string_literals(path.read_text(encoding="utf-8")), 0, rel)

    def test_no_status_glyph_literal_in_chat_output(self) -> None:
        offenders = []
        for rel in CHAT_OUTPUT_MODULES:
            text = (REPO / rel).read_text(encoding="utf-8")
            for line, value in find_literal_glyphs(text, rel):
                offenders.append(f"{rel}:{line}: {value!r}")
        self.assertEqual(offenders, [], "use status_markers.icon() / _status_icon() instead")


if __name__ == "__main__":
    unittest.main()
