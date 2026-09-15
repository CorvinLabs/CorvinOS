"""E2E tests for Engine Command Interface (ADR-0069 M6).

Coverage:
  1.  EngineCommandManifest present on all four engines.
  2.  ClaudeCode: mid_stream_inject="stdin_json", cancel="sigterm".
  3.  Hermes: mid_stream_inject="buffered", has model/temp/ctx native cmds.
  4.  Codex: mid_stream_inject=None.
  5.  OpenCode: mid_stream_inject=None, has model native cmd.
  6.  dispatch_btw / stdin_json → calls engine.inject(), returns success.
  7.  dispatch_btw / buffered → appends to buffer, returns buffered=True.
  8.  dispatch_btw / None → returns success=False with non-empty message.
  9.  dispatch_native / valid cmd → calls handler_method on engine.
  10. dispatch_native / unknown cmd → returns success=False.
  11. format_commands output contains engine name and transport labels.
  12. Hermes eci_set_model changes engine.model.
  13. Hermes eci_set_model rejects empty args.
  14. Hermes eci_set_temp accepts valid float, rejects out-of-range.
  15. Hermes eci_show_ctx returns model + base_url.
  16. OpenCode eci_set_model sets _override_model.
  17. AST lint: no `import anthropic` in eci package.
  18. drain_btw_buffer in adapter returns joined text, empties buffer.

Run:
    python3 operator/bridges/shared/eci/test_eci_e2e.py
"""

from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
sys.path.insert(0, str(SHARED))

from eci import CommandResult, EngineCommandManifest, NativeCommandSpec  # noqa: E402
from eci.dispatcher import CommandDispatcher  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine_with_manifest(
    name: str,
    transport: str | None,
    has_inject: bool = False,
    inject_returns: bool = True,
    native: dict | None = None,
) -> MagicMock:
    """Build a mock engine carrying an EngineCommandManifest."""
    eng = MagicMock()
    eng.name = name
    manifest = EngineCommandManifest(
        mid_stream_inject=transport,
        cancel="sigterm" if transport != "http_delete" else "http_delete",
        compact="flag" if transport == "stdin_json" else None,
        native_commands=native or {},
    )
    type(eng).command_manifest = property(lambda self: manifest)  # type: ignore[assignment]
    if has_inject:
        eng.inject.return_value = inject_returns
    return eng


# ---------------------------------------------------------------------------
# Test 1-5: Manifest presence on real engines
# ---------------------------------------------------------------------------


class ManifestPresenceTests(unittest.TestCase):

    def _import_engines(self):
        from agents.claude_code import ClaudeCodeEngine
        from agents.codex_cli import CodexCliEngine
        from agents.hermes_engine import HermesEngine
        from agents.opencode_cli import OpenCodeEngine
        return ClaudeCodeEngine, HermesEngine, CodexCliEngine, OpenCodeEngine

    def test_all_engines_have_command_manifest(self) -> None:
        CC, Hermes, Codex, OC = self._import_engines()
        for cls in (CC, Hermes, Codex, OC):
            eng = cls()
            manifest = getattr(eng, "command_manifest", None)
            self.assertIsNotNone(manifest, f"{cls.__name__} missing command_manifest")
            self.assertIsInstance(manifest, EngineCommandManifest)

    def test_claude_code_manifest_transport(self) -> None:
        from agents.claude_code import ClaudeCodeEngine
        manifest = ClaudeCodeEngine().command_manifest
        self.assertEqual(manifest.mid_stream_inject, "stdin_json")
        self.assertEqual(manifest.cancel, "sigterm")
        self.assertEqual(manifest.compact, "flag")

    def test_hermes_manifest_transport(self) -> None:
        from agents.hermes_engine import HermesEngine
        manifest = HermesEngine().command_manifest
        self.assertEqual(manifest.mid_stream_inject, "buffered")
        self.assertEqual(manifest.cancel, "http_delete")
        self.assertIsNone(manifest.compact)
        self.assertIn("model", manifest.native_commands)
        self.assertIn("temp", manifest.native_commands)
        self.assertIn("ctx", manifest.native_commands)

    def test_codex_manifest_transport(self) -> None:
        from agents.codex_cli import CodexCliEngine
        manifest = CodexCliEngine().command_manifest
        self.assertEqual(manifest.mid_stream_inject, "buffered")
        self.assertEqual(manifest.cancel, "sigterm")

    def test_opencode_manifest_has_model_cmd(self) -> None:
        from agents.opencode_cli import OpenCodeEngine
        manifest = OpenCodeEngine().command_manifest
        self.assertEqual(manifest.mid_stream_inject, "buffered")
        self.assertIn("model", manifest.native_commands)


# ---------------------------------------------------------------------------
# Test 6-8: dispatch_btw transport routing
# ---------------------------------------------------------------------------


class DispatchBtwTests(unittest.TestCase):

    def test_stdin_json_calls_engine_inject(self) -> None:
        eng = _make_engine_with_manifest("cc", "stdin_json", has_inject=True, inject_returns=True)
        buf: list[str] = []
        result = CommandDispatcher.dispatch_btw(eng, "hello", buf)
        eng.inject.assert_called_once_with("hello")
        self.assertTrue(result.success)
        self.assertFalse(result.buffered)
        self.assertEqual(buf, [])

    def test_stdin_json_inject_returns_false(self) -> None:
        eng = _make_engine_with_manifest("cc", "stdin_json", has_inject=True, inject_returns=False)
        result = CommandDispatcher.dispatch_btw(eng, "hi", [])
        self.assertFalse(result.success)

    def test_buffered_appends_to_buffer(self) -> None:
        eng = _make_engine_with_manifest("hermes", "buffered")
        buf: list[str] = []
        result = CommandDispatcher.dispatch_btw(eng, "steer me", buf)
        self.assertTrue(result.success)
        self.assertTrue(result.buffered)
        self.assertIn("steer me", buf)
        self.assertIn("nächstem Turn", result.message)

    def test_buffered_accumulates_multiple(self) -> None:
        eng = _make_engine_with_manifest("hermes", "buffered")
        buf: list[str] = []
        CommandDispatcher.dispatch_btw(eng, "first", buf)
        CommandDispatcher.dispatch_btw(eng, "second", buf)
        self.assertEqual(buf, ["first", "second"])

    def test_none_transport_returns_failure_with_message(self) -> None:
        eng = _make_engine_with_manifest("codex", None)
        result = CommandDispatcher.dispatch_btw(eng, "hi", [])
        self.assertFalse(result.success)
        self.assertGreater(len(result.message), 0)
        self.assertIn("codex", result.message)

    def test_no_manifest_returns_empty_failure(self) -> None:
        eng = MagicMock()
        eng.name = "bare"
        del eng.command_manifest
        type(eng).command_manifest = property(lambda self: None)  # type: ignore[assignment]
        result = CommandDispatcher.dispatch_btw(eng, "hi", [])
        self.assertFalse(result.success)


# ---------------------------------------------------------------------------
# Test 9-10: dispatch_native
# ---------------------------------------------------------------------------


class DispatchNativeTests(unittest.TestCase):

    def _hermes_engine(self):
        from agents.hermes_engine import HermesEngine
        return HermesEngine()

    def test_valid_native_cmd_calls_handler(self) -> None:
        eng = self._hermes_engine()
        result = CommandDispatcher.dispatch_native(eng, "model", "hermes-fast")
        self.assertTrue(result.success)
        # alias is resolved to full name in the message
        self.assertIn("Modell", result.message)

    def test_unknown_native_cmd_returns_failure(self) -> None:
        eng = self._hermes_engine()
        result = CommandDispatcher.dispatch_native(eng, "nonexistent", "")
        self.assertFalse(result.success)
        self.assertIn("nonexistent", result.message)

    def test_native_cmd_no_manifest_engine(self) -> None:
        eng = MagicMock()
        type(eng).command_manifest = property(lambda self: None)  # type: ignore[assignment]
        result = CommandDispatcher.dispatch_native(eng, "foo", "")
        self.assertFalse(result.success)


# ---------------------------------------------------------------------------
# Test 11: format_commands
# ---------------------------------------------------------------------------


class FormatCommandsTests(unittest.TestCase):

    def test_format_contains_engine_name(self) -> None:
        from agents.hermes_engine import HermesEngine
        eng = HermesEngine()
        output = CommandDispatcher.format_commands(eng)
        self.assertIn("hermes", output)

    def test_format_contains_transport_label(self) -> None:
        from agents.hermes_engine import HermesEngine
        eng = HermesEngine()
        output = CommandDispatcher.format_commands(eng)
        self.assertIn("gepuffert", output)

    def test_format_contains_native_cmds(self) -> None:
        from agents.hermes_engine import HermesEngine
        eng = HermesEngine()
        output = CommandDispatcher.format_commands(eng)
        self.assertIn("/e:model", output)
        self.assertIn("/e:temp", output)
        self.assertIn("/e:ctx", output)

    def test_format_claude_shows_live(self) -> None:
        from agents.claude_code import ClaudeCodeEngine
        eng = ClaudeCodeEngine()
        output = CommandDispatcher.format_commands(eng)
        self.assertIn("claude_code", output)
        self.assertIn("live", output)

    def test_format_codex_shows_btw_buffered(self) -> None:
        from agents.codex_cli import CodexCliEngine
        eng = CodexCliEngine()
        output = CommandDispatcher.format_commands(eng)
        self.assertIn("/btw", output)
        self.assertIn("gepuffert", output)


# ---------------------------------------------------------------------------
# Test 12-16: Hermes + OpenCode native handlers
# ---------------------------------------------------------------------------


class HermesNativeHandlerTests(unittest.TestCase):

    def _eng(self):
        from agents.hermes_engine import HermesEngine
        return HermesEngine()

    def test_set_model_changes_model(self) -> None:
        eng = self._eng()
        original = eng.model
        result = eng.eci_set_model("hermes-fast")
        self.assertTrue(result.success)
        self.assertNotEqual(eng.model, original)
        # message contains the resolved full model name, not the alias
        self.assertIn("→", result.message)

    def test_set_model_rejects_empty(self) -> None:
        eng = self._eng()
        result = eng.eci_set_model("")
        self.assertFalse(result.success)

    def test_set_temp_valid(self) -> None:
        eng = self._eng()
        result = eng.eci_set_temp("0.7")
        self.assertTrue(result.success)
        self.assertEqual(eng._temperature, 0.7)

    def test_set_temp_out_of_range(self) -> None:
        eng = self._eng()
        result = eng.eci_set_temp("3.0")
        self.assertFalse(result.success)

    def test_set_temp_not_a_number(self) -> None:
        eng = self._eng()
        result = eng.eci_set_temp("hot")
        self.assertFalse(result.success)

    def test_show_ctx_returns_model_and_url(self) -> None:
        eng = self._eng()
        result = eng.eci_show_ctx("")
        self.assertTrue(result.success)
        self.assertIn(eng.model, result.message)
        self.assertIn(eng.base_url, result.message)


class OpenCodeNativeHandlerTests(unittest.TestCase):

    def test_set_model_sets_override(self) -> None:
        from agents.opencode_cli import OpenCodeEngine
        eng = OpenCodeEngine()
        result = eng.eci_set_model("ollama/qwen3:8b")
        self.assertTrue(result.success)
        self.assertEqual(eng._override_model, "ollama/qwen3:8b")


# ---------------------------------------------------------------------------
# Test 17: AST lint — no `import anthropic` in eci package
# ---------------------------------------------------------------------------


class AstLintTests(unittest.TestCase):

    def test_no_import_anthropic_in_eci(self) -> None:
        eci_dir = Path(__file__).resolve().parent
        violations: list[str] = []
        for py_file in eci_dir.glob("*.py"):
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


# ---------------------------------------------------------------------------
# Test 18: drain_btw_buffer in adapter
# ---------------------------------------------------------------------------


class DrainBtwBufferTests(unittest.TestCase):

    def _fresh_adapter(self):
        for mod in list(sys.modules):
            if mod in ("adapter",):
                del sys.modules[mod]
        old_env = {}
        test_env = {
            "CORVIN_HOME": "/tmp/eci-test-drain",
            "CORVIN_TENANT_ID": "_default",
        }
        for k, v in test_env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            import adapter  # type: ignore
        except Exception:
            for k, orig in old_env.items():
                if orig is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = orig
            self.skipTest("adapter not importable in test isolation")
        for k, orig in old_env.items():
            if orig is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig
        return adapter

    def test_drain_empty_buffer_returns_none(self) -> None:
        adapter = self._fresh_adapter()
        result = adapter.drain_btw_buffer("test-chat-xyz-empty")
        self.assertIsNone(result)

    def test_drain_buffer_returns_joined_and_clears(self) -> None:
        adapter = self._fresh_adapter()
        with adapter._btw_buffers_guard:
            adapter._btw_buffers["test-drain-chat"] = ["first note", "second note"]
        result = adapter.drain_btw_buffer("test-drain-chat")
        self.assertIsNotNone(result)
        self.assertIn("[btw: first note]", result)
        self.assertIn("[btw: second note]", result)
        # buffer must be empty after drain
        with adapter._btw_buffers_guard:
            self.assertNotIn("test-drain-chat", adapter._btw_buffers)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    test_classes = [
        ManifestPresenceTests,
        DispatchBtwTests,
        DispatchNativeTests,
        FormatCommandsTests,
        HermesNativeHandlerTests,
        OpenCodeNativeHandlerTests,
        AstLintTests,
        DrainBtwBufferTests,
    ]
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
