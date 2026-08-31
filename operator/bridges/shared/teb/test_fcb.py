"""E2E tests for Function-Call Bridge (ADR-0069 M2).

Coverage:
  1.  mcp_tool_to_openai converts name/description/inputSchema correctly.
  2.  mcp_tools_to_openai_list handles empty + multi-item lists.
  3.  openai_call_to_mcp_call extracts name + arguments.
  4.  openai_call_to_mcp_call handles string-serialised arguments.
  5.  mcp_result_to_openai_message serialises None/str/dict results.
  6.  extract_tool_calls_from_ollama_chunk returns [] on non-tool chunks.
  7.  extract_tool_calls_from_ollama_chunk returns list on tool chunks.
  8.  is_tool_call_chunk returns correct bool.
  9.  AST lint: no `import anthropic` in teb package.
  10. HermesEngine spawn accepts tools= kwarg without crash.
  11. HermesEngine tool-use loop: fake Ollama emits tool_call then text.
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
    extract_tool_calls_from_ollama_chunk,
    is_tool_call_chunk,
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

    def test_extract_no_tool_calls(self) -> None:
        chunk = {"message": {"content": "hello"}, "done": False}
        self.assertEqual(extract_tool_calls_from_ollama_chunk(chunk), [])

    def test_extract_tool_calls(self) -> None:
        chunk = {
            "message": {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "f", "arguments": {}}}],
            },
            "done": False,
        }
        calls = extract_tool_calls_from_ollama_chunk(chunk)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "f")

    def test_is_tool_call_chunk_true(self) -> None:
        chunk = {"message": {"tool_calls": [{"function": {"name": "x"}}]}}
        self.assertTrue(is_tool_call_chunk(chunk))

    def test_is_tool_call_chunk_false(self) -> None:
        chunk = {"message": {"content": "text"}, "done": True}
        self.assertFalse(is_tool_call_chunk(chunk))


class HermesToolUseTests(unittest.TestCase):
    """Test the HermesEngine tool-use loop with a fake HTTP server."""

    def _make_ollama_lines(self, tool_call_name: str, tool_call_args: dict, final_text: str):
        """Generate fake Ollama NDJSON lines simulating a tool-use turn."""
        # Line 1: model requests a tool call
        line1 = json.dumps({
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": tool_call_name, "arguments": tool_call_args}}],
            },
            "done": True,
            "prompt_eval_count": 10,
            "eval_count": 5,
        })
        # Line 2 (after tool result sent back): final text response
        line2 = json.dumps({
            "message": {"role": "assistant", "content": final_text},
            "done": False,
        })
        line3 = json.dumps({
            "message": {"content": ""},
            "done": True,
            "prompt_eval_count": 20,
            "eval_count": 10,
        })
        return [line1.encode(), line2.encode(), line3.encode()]

    def test_hermes_spawn_accepts_tools_kwarg(self) -> None:
        from agents.hermes_engine import HermesEngine
        import inspect
        sig = inspect.signature(HermesEngine.spawn)
        self.assertIn("tools", sig.parameters)
        self.assertIn("tool_executor", sig.parameters)

    def test_hermes_tool_use_loop_mock(self) -> None:
        """Simulate Ollama returning a tool_call, executor runs it, model replies."""
        from agents.hermes_engine import HermesEngine
        from agents import collect, StreamEvent

        eng = HermesEngine(model="hermes-fast", base_url="http://localhost:11434")

        tool_lines_round1 = [
            json.dumps({
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": "code.test_tool", "arguments": {"x": 42}}}],
                },
                "done": True, "prompt_eval_count": 5, "eval_count": 3,
            }).encode(),
        ]
        tool_lines_round2 = [
            json.dumps({"message": {"content": "Result: 42"}, "done": False}).encode(),
            json.dumps({"message": {"content": ""}, "done": True,
                        "prompt_eval_count": 10, "eval_count": 8}).encode(),
        ]

        call_count = [0]
        fake_responses = [tool_lines_round1, tool_lines_round2]

        class FakeResponse:
            def __init__(self, lines):
                self._lines = lines
                self._idx = 0
            def __iter__(self):
                return iter(self._lines)
            def close(self):
                pass

        def fake_urlopen(req, timeout=None):
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(fake_responses):
                return FakeResponse(fake_responses[idx])
            return FakeResponse([])

        executor_calls = []

        def fake_executor(name, args):
            executor_calls.append((name, args))
            return f"executed {name}"

        tools_def = [{"type": "function", "function": {"name": "code.test_tool",
                                                         "description": "test",
                                                         "parameters": {}}}]

        import urllib.request
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            result = collect(eng.spawn(
                "use the tool",
                tools=tools_def,
                tool_executor=fake_executor,
            ))

        self.assertGreater(call_count[0], 1, "Should have made >1 HTTP call (tool loop)")
        self.assertEqual(len(executor_calls), 1)
        self.assertEqual(executor_calls[0][0], "code.test_tool")
        self.assertIn("Result", result.final_text)


class SkillCompilerTests(unittest.TestCase):

    def _compiler(self):
        from eci.skill_compiler import SkillCompiler
        return SkillCompiler

    def test_compile_none_returns_none(self) -> None:
        SC = self._compiler()
        self.assertIsNone(SC.compile(None, "hermes"))

    def test_compile_empty_returns_none(self) -> None:
        SC = self._compiler()
        self.assertIsNone(SC.compile("   ", "hermes"))

    def test_compile_passes_through_block(self) -> None:
        SC = self._compiler()
        block = "<auto_skill name='test'>body</auto_skill>"
        result = SC.compile(block, "hermes")
        self.assertEqual(result, block)

    def test_compile_passes_through_for_cc(self) -> None:
        SC = self._compiler()
        block = "<auto_skill name='x'>y</auto_skill>"
        self.assertEqual(SC.compile(block, "claude_code"), block)

    def test_should_inject_all_engines(self) -> None:
        SC = self._compiler()
        for engine in ("claude_code", "hermes", "codex_cli", "opencode", "gemini"):
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
    for cls in [FcbTranslationTests, HermesToolUseTests, SkillCompilerTests, AstLintTebTests]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
