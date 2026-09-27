"""E2E tests for Function-Call Bridge (ADR-0069 M2).

Coverage:
  1.  mcp_tool_to_openai converts name/description/inputSchema correctly.
  2.  mcp_tools_to_openai_list handles empty + multi-item lists.
  3.  openai_call_to_mcp_call extracts name + arguments.
  4.  openai_call_to_mcp_call handles string-serialised arguments.
  5.  mcp_result_to_openai_message serialises None/str/dict results.
  6-8. (Ollama NDJSON chunk parser — removed with Hermes, ADR-2087)
  9.  AST lint: no `import anthropic` in teb package.
  10-11. (HermesEngine tool-use loop — removed with Hermes, ADR-2087)
  12. SkillCompiler.compile returns None on empty input.
  13. SkillCompiler.compile passes through non-empty block unchanged.
  14. SkillCompiler.should_inject_via_system_prompt True for all engines.

Run:
    python3 operator/bridges/shared/teb/test_fcb.py
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
sys.path.insert(0, str(SHARED))

from teb.fcb import (  # noqa: E402
    mcp_result_to_openai_message,
    mcp_tool_to_openai,
    mcp_tools_to_openai_list,
    openai_call_to_mcp_call,
)


class FcbTranslationTests(unittest.TestCase):

    def test_mcp_tool_to_openai_basic(self) -> None:
        spec = {
            "name": "code.csv_diff",
            "description": "Diff two CSVs",
            "inputSchema": {
                "type": "object",
                "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                "required": ["a", "b"],
            },
        }
        result = mcp_tool_to_openai(spec)
        self.assertEqual(result["type"], "function")
        fn = result["function"]
        self.assertEqual(fn["name"], "code.csv_diff")
        self.assertEqual(fn["description"], "Diff two CSVs")
        self.assertIn("properties", fn["parameters"])

    def test_mcp_tool_to_openai_missing_schema(self) -> None:
        spec = {"name": "simple", "description": "no schema"}
        result = mcp_tool_to_openai(spec)
        self.assertEqual(result["function"]["parameters"]["type"], "object")

    def test_mcp_tools_to_openai_list_empty(self) -> None:
        self.assertEqual(mcp_tools_to_openai_list([]), [])

    def test_mcp_tools_to_openai_list_multi(self) -> None:
        specs = [
            {"name": "a", "description": "A"},
            {"name": "b", "description": "B"},
        ]
        result = mcp_tools_to_openai_list(specs)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["function"]["name"], "a")
        self.assertEqual(result[1]["function"]["name"], "b")

    def test_openai_call_to_mcp_call_basic(self) -> None:
        call = {"function": {"name": "my_tool", "arguments": {"x": 1}}}
        result = openai_call_to_mcp_call(call)
        self.assertEqual(result["name"], "my_tool")
        self.assertEqual(result["arguments"], {"x": 1})

    def test_openai_call_string_arguments(self) -> None:
        call = {"function": {"name": "t", "arguments": '{"key": "val"}'}}
        result = openai_call_to_mcp_call(call)
        self.assertEqual(result["arguments"], {"key": "val"})

    def test_openai_call_invalid_string_arguments(self) -> None:
        call = {"function": {"name": "t", "arguments": "not json"}}
        result = openai_call_to_mcp_call(call)
        self.assertEqual(result["arguments"], {})

    def test_mcp_result_none(self) -> None:
        msg = mcp_result_to_openai_message(None)
        self.assertEqual(msg["role"], "tool")
        self.assertEqual(msg["content"], "(no output)")

    def test_mcp_result_string(self) -> None:
        msg = mcp_result_to_openai_message("hello")
        self.assertEqual(msg["content"], "hello")

    def test_mcp_result_dict(self) -> None:
        msg = mcp_result_to_openai_message({"rows": 5})
        self.assertIn("rows", msg["content"])

    def test_mcp_result_with_tool_call_id(self) -> None:
        msg = mcp_result_to_openai_message("ok", tool_call_id="call_abc")
        self.assertEqual(msg["tool_call_id"], "call_abc")


class SkillCompilerTests(unittest.TestCase):

    def _compiler(self):
        from eci.skill_compiler import SkillCompiler
        return SkillCompiler

    def test_compile_none_returns_none(self) -> None:
        SC = self._compiler()
        self.assertIsNone(SC.compile(None, "codex_cli"))

    def test_compile_empty_returns_none(self) -> None:
        SC = self._compiler()
        self.assertIsNone(SC.compile("   ", "codex_cli"))

    def test_compile_passes_through_block(self) -> None:
        SC = self._compiler()
        block = "<auto_skill name='test'>body</auto_skill>"
        result = SC.compile(block, "codex_cli")
        self.assertEqual(result, block)

    def test_compile_passes_through_for_cc(self) -> None:
        SC = self._compiler()
        block = "<auto_skill name='x'>y</auto_skill>"
        self.assertEqual(SC.compile(block, "claude_code"), block)

    def test_should_inject_all_engines(self) -> None:
        SC = self._compiler()
        for engine in ("claude_code", "codex_cli", "opencode", "gemini"):
            self.assertTrue(SC.should_inject_via_system_prompt(engine))


class AstLintTebTests(unittest.TestCase):

    def test_no_import_anthropic_in_teb(self) -> None:
        teb_dir = Path(__file__).resolve().parent
        violations: list[str] = []
        for py_file in teb_dir.glob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            src = py_file.read_text()
            tree = ast.parse(src, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "anthropic":
                            violations.append(f"{py_file.name}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").startswith("anthropic"):
                        violations.append(f"{py_file.name}:{node.lineno}")
        self.assertEqual(violations, [], f"import anthropic found: {violations}")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [FcbTranslationTests, SkillCompilerTests, AstLintTebTests]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
