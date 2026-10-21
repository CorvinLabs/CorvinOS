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
import json
import sys
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
    "corvin_operator/bridges/shared/acs_runtime.py",
)

# JavaScript chat-output modules (T-0249): every bridge daemon (discord, telegram, slack,
# whatsapp, signal, teams, email) and every other non-test module under corvin_operator/bridges
# (shared/js, per-bridge helpers), node_modules excluded. They take their icons from
# ``status_markers.json`` (the export of the Python vocabulary) through
# ``shared/js/status_markers.js``; a log line carries no status icon at all.
_JS_BRIDGES = REPO / "corvin_operator" / "bridges"
JS_CHAT_OUTPUT_MODULES = tuple(sorted(
    p for p in _JS_BRIDGES.rglob("*.js")
    if "node_modules" not in p.parts and not p.name.startswith("test_")
))

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


# A "/" after one of these (or at the start / after a keyword such as return) begins a
# regex literal, not a division — the usual tokenizer heuristic.
_JS_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")
_JS_REGEX_KEYWORDS = ("return", "typeof", "case", "do", "else", "in", "of", "throw", "void", "delete")


def _js_regex_end(source: str, i: int) -> int:
    """Index just past the regex literal (and its flags) that starts at ``source[i] == "/"``."""
    j, n, in_class = i + 1, len(source), False
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return j  # not a regex after all; stop at the line end
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "/":
            j += 1
            while j < n and source[j].isalpha():
                j += 1
            return j
        j += 1
    return n


def _js_regex_starts_here(source: str, i: int) -> bool:
    k = i - 1
    while k >= 0 and source[k] in " \t":
        k -= 1
    if k < 0 or source[k] in _JS_REGEX_PRECEDERS or source[k] == "\n":
        return True
    word_end = k + 1
    while k >= 0 and (source[k].isalnum() or source[k] == "_"):
        k -= 1
    return source[k + 1:word_end] in _JS_REGEX_KEYWORDS


def js_string_literals(source: str) -> list[tuple[int, str]]:
    """(line, text) of every string / template literal in a JavaScript source.

    A small scanner: ``//`` and ``/* */`` comments are skipped (a comment is not chat
    output), the three quote kinds are tracked with backslash escapes, and a regex literal
    (``/…/flags`` where a division cannot stand) is skipped whole so a quote inside it —
    ``/(?:"[^"]*"|'[^']*')/`` exists in in_chat_commands.js — cannot flip the quote state.
    The positive control below feeds the checker exactly these shapes.
    """
    out: list[tuple[int, str]] = []
    i, n, line = 0, len(source), 1
    while i < n:
        ch = source[i]
        if ch == "\n":
            line += 1
            i += 1
        elif source.startswith("//", i):
            j = source.find("\n", i)
            i = n if j < 0 else j
        elif source.startswith("/*", i):
            j = source.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += source.count("\n", i, j)
            i = j
        elif ch == "/" and _js_regex_starts_here(source, i):
            i = _js_regex_end(source, i)
        elif ch in ("'", '"', "`"):
            q, j, start_line, buf = ch, i + 1, line, []
            while j < n and source[j] != q:
                if source[j] == "\\" and j + 1 < n:
                    buf.append(source[j + 1])
                    j += 2
                    continue
                if source[j] == "\n":
                    line += 1
                buf.append(source[j])
                j += 1
            out.append((start_line, "".join(buf)))
            i = j + 1
        else:
            i += 1
    return out


def find_js_literal_glyphs(source: str) -> list[tuple[int, str]]:
    return [(ln, text[:80]) for ln, text in js_string_literals(source)
            if any(g in text for g in _GLYPHS)]


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
        self.assertEqual(len(CHAT_OUTPUT_MODULES), 14)
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


class StatusMarkerJsonExportTests(unittest.TestCase):
    """T-0249: the JSON the JavaScript bridges read IS the Python vocabulary."""

    def setUp(self) -> None:
        sys.path.insert(0, str(REPO / "corvin_operator" / "bridges" / "shared"))
        import status_markers  # noqa: PLC0415
        self.sm = status_markers

    def test_shipped_json_equals_the_python_vocabulary(self) -> None:
        path = REPO / "corvin_operator" / "bridges" / "shared" / "status_markers.json"
        self.assertTrue(path.is_file(), "run: python3 status_markers.py --write-json")
        shipped = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(shipped, self.sm.as_json())
        # And the export really is the vocabulary, not a second list.
        self.assertEqual(shipped["markers"], [list(m) for m in self.sm.STATUS_MARKERS])
        self.assertEqual(shipped["icons"], {name: self.sm.icon(name) for name in shipped["icons"]})
        self.assertEqual(set(shipped["icons"].values()), {g for g, _ in self.sm.STATUS_MARKERS})


class JsStatusGlyphLiteralGuardTests(unittest.TestCase):
    """T-0249: every bridge's JS takes its icons from the export, never a literal."""

    def test_js_checker_flags_a_literal_and_ignores_comments(self) -> None:
        self.assertEqual(find_js_literal_glyphs("x = '✅ done';\n"), [(1, "✅ done")])
        self.assertEqual(find_js_literal_glyphs("// ✅ comment\nconst a = `⚠️ ${b}`;\n"), [(2, "⚠️ ${b}")])
        self.assertEqual(find_js_literal_glyphs("/* ❌\n block */ y = \"plain\";\n"), [])
        self.assertEqual(find_js_literal_glyphs("s = 'http://x/y'; t = \"⏳\";\n"), [(1, "⏳")])
        # A quote inside a regex literal must not open a string (it did, and hid every
        # literal after it in in_chat_commands.js); a division must not start a regex.
        src = "const p = r.match(/(?:\"[^\"]*\"|'[^']*'|[^\\s])+/g);\n// ❌ comment\nx = '✅ ok';\n"
        self.assertEqual(find_js_literal_glyphs(src), [(3, "✅ ok")])
        self.assertEqual(find_js_literal_glyphs("y = a / b / c; z = '❌';\n"), [(1, "❌")])
        self.assertEqual(find_js_literal_glyphs("return /x'y/.test(s) ? '✅' : 'no';\n"), [(1, "✅")])

    def test_scan_reaches_the_js_chat_output_modules(self) -> None:
        daemons = {p.parent.name for p in JS_CHAT_OUTPUT_MODULES if p.name == "daemon.js"}
        self.assertEqual(daemons, {"discord", "telegram", "slack", "whatsapp", "signal", "teams", "email"})
        names = {p.name for p in JS_CHAT_OUTPUT_MODULES}
        for required in ("in_chat_commands.js", "status_markers.js", "orchestration_handler.js"):
            self.assertIn(required, names)
        self.assertFalse(any("node_modules" in p.parts for p in JS_CHAT_OUTPUT_MODULES))
        self.assertGreaterEqual(len(JS_CHAT_OUTPUT_MODULES), 40)
        # Every daemon yields string literals (a scanner that saw none would pass vacuously);
        # a helper such as whatsapp/reconnect_backoff.js legitimately has none.
        total = 0
        for path in JS_CHAT_OUTPUT_MODULES:
            found = len(js_string_literals(path.read_text(encoding="utf-8")))
            total += found
            if path.name in ("daemon.js", "in_chat_commands.js", "orchestration_handler.js"):
                self.assertGreater(found, 0, str(path.relative_to(REPO)))
        self.assertGreater(total, 1000)

    def test_no_status_glyph_literal_in_js_chat_output(self) -> None:
        offenders = []
        for path in JS_CHAT_OUTPUT_MODULES:
            rel = path.relative_to(REPO)
            for line, value in find_js_literal_glyphs(path.read_text(encoding="utf-8")):
                offenders.append(f"{rel}:{line}: {value!r}")
        self.assertEqual(offenders, [], "use require('…/status_markers').icon(name) instead")


if __name__ == "__main__":
    unittest.main()
