"""Full E2E verification of all HermesEngine integration points (ADR-0066 M1).

Tests (run in order — later tests depend on earlier passes):

  1.  HermesEngine protocol contract (unit)
  2.  HermesEngine live Ollama round-trip (session_started → text → turn_completed)
  3.  Delegation stack: run_delegate(engine="hermes") → ok=True + non-empty text
  4.  Delegation stack: AVAILABLE_ENGINES includes "hermes"
  5.  MCP server: _tool_definitions() contains "delegate_hermes" with description
  6.  MCP server: delegate_hermes input schema matches other delegation tools
  7.  Self-test: _check_hermes_ollama() returns CHECK with name "engine.hermes_ollama"
  8.  Self-test: result is INFO (Ollama reachable) not WARNING
  9.  Adapter: _HermesEngine imported and is the real class
  10. Adapter: _call_hermes_streaming_via_engine is callable
  11. Adapter: direct dispatch returns non-empty text for hermes-worker profile
  12. Persona: hermes-worker.json loads with default_engine="hermes"
  13. Persona: routing_anchors present (for L5 auto-router)
  14. Persona: zero_config=True (no API key needed)
  15. Adapter: Hermes dispatch DOES NOT call ClaudeCodeEngine path (isolation check)

Run:
    python3 operator/bridges/shared/agents/test_hermes_e2e_full.py

Exit 0 = all green. Exit 1 = at least one failure.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# -- path setup ---------------------------------------------------------------

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
REPO_ROOT = SHARED.parent.parent.parent
AGENTS_DIR = SHARED

for p in (str(SHARED), str(AGENTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

# -- helpers ------------------------------------------------------------------

PONG_PROMPT = "Reply with exactly the single word: pong"
TEST_MODEL = os.environ.get("CORVIN_HERMES_TEST_MODEL",
                            os.environ.get("CORVIN_HERMES_MODEL", "qwen3:1.7b"))

COLOUR_OK   = "\033[32m"
COLOUR_FAIL = "\033[31m"
COLOUR_RESET = "\033[0m"
COLOUR_HEAD = "\033[1;36m"


# =============================================================================
# 1-2: HermesEngine direct
# =============================================================================

class T01_EngineProtocol(unittest.TestCase):
    """1. Protocol contract (unit, no Ollama needed)."""

    def test_satisfies_worker_engine_protocol(self) -> None:
        from agents import WorkerEngine
        from agents.hermes_engine import HermesEngine
        engine = HermesEngine()
        self.assertIsInstance(engine, WorkerEngine)
        self.assertEqual(engine.name, "hermes")

    def test_capability_keys_present(self) -> None:
        from agents.hermes_engine import HermesEngine
        caps = HermesEngine.capabilities
        for key in ("mid_stream_inject", "hooks", "skills_tool", "mcp",
                    "stream_json", "permission_modes", "add_system_prompt",
                    "session_pinning"):
            self.assertIn(key, caps, f"missing capability key: {key!r}")

    def test_does_not_import_anthropic(self) -> None:
        import ast
        src = (HERE / "hermes_engine.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("anthropic", alias.name or "")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn("anthropic", node.module or "")

    def test_ollama_unavailable_yields_error_not_raises(self) -> None:
        from agents.hermes_engine import HermesEngine
        from agents import collect
        result = collect(HermesEngine(base_url="http://localhost:19999").spawn("hi"))
        self.assertIsNotNone(result.error)
        self.assertIn("ollama", result.error.lower())


class T02_LiveOllamaRoundtrip(unittest.TestCase):
    """2. Full live round-trip through Ollama."""

    def setUp(self) -> None:
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        except Exception:
            self.skipTest("Ollama not reachable")

        # check model is pulled
        import urllib.request as _ur, json as _j
        with _ur.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
            data = _j.loads(r.read())
        pulled = [m["name"] for m in data.get("models", [])]
        model_base = TEST_MODEL.split(":")[0]
        if not any(p == TEST_MODEL or p.startswith(model_base + ":") for p in pulled):
            self.skipTest(f"model {TEST_MODEL!r} not pulled in Ollama")
        from agents.hermes_engine import HermesEngine
        self.engine = HermesEngine(model=TEST_MODEL)

    def test_pong(self) -> None:
        from agents import collect
        result = collect(self.engine.spawn(PONG_PROMPT))
        self.assertIsNone(result.error, f"unexpected error: {result.error}")
        self.assertIn("pong", result.final_text.lower())

    def test_event_sequence(self) -> None:
        events = list(self.engine.spawn(PONG_PROMPT))
        types = [ev.type for ev in events]
        self.assertIn("session_started", types)
        self.assertIn("turn_completed", types)
        completed = [ev for ev in events if ev.type == "turn_completed"]
        self.assertEqual(len(completed), 1)
        self.assertIn("input_tokens", completed[0].usage or {})

    def test_system_prompt_accepted(self) -> None:
        from agents import collect
        result = collect(self.engine.spawn(
            "What is your name?",
            system="You are called HermesBot. Always say your name is HermesBot.",
        ))
        self.assertIsNone(result.error)
        self.assertTrue(result.final_text.strip())


# =============================================================================
# 3-6: Delegation stack
# =============================================================================

class T03_DelegationStack(unittest.TestCase):
    """3-6. run_delegate and MCP server."""

    def _add_delegate_path(self) -> None:
        delegate_core = REPO_ROOT / "core" / "delegate"
        for p in (str(delegate_core), str(delegate_core / "corvin_delegate")):
            if p not in sys.path:
                sys.path.insert(0, p)

    def test_hermes_in_available_engines(self) -> None:
        self._add_delegate_path()
        from corvin_delegate.delegation import AVAILABLE_ENGINES
        self.assertIn("hermes", AVAILABLE_ENGINES,
                      f"'hermes' missing from AVAILABLE_ENGINES: {AVAILABLE_ENGINES}")

    def test_run_delegate_hermes_live(self) -> None:
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        except Exception:
            self.skipTest("Ollama not reachable")

        self._add_delegate_path()
        from corvin_delegate.delegation import run_delegate
        result = run_delegate(
            engine="hermes",
            prompt=PONG_PROMPT,
            model=TEST_MODEL,
            audit=False,
        )
        self.assertTrue(result.ok, f"delegate failed: {result.error}")
        self.assertIn("pong", result.final_text.lower())

    def test_mcp_server_has_delegate_hermes_tool(self) -> None:
        self._add_delegate_path()
        sys.path.insert(0, str(REPO_ROOT / "core" / "delegate"))
        from corvin_delegate.mcp_server import _tool_definitions, _TOOL_NAME_TO_ENGINE
        self.assertIn("delegate_hermes", _TOOL_NAME_TO_ENGINE)
        defs = {t["name"]: t for t in _tool_definitions()}
        self.assertIn("delegate_hermes", defs,
                      f"delegate_hermes not in tool definitions: {list(defs)}")
        desc = defs["delegate_hermes"].get("description", "")
        self.assertIn("Ollama", desc, "description should mention Ollama")
        self.assertIn("local", desc.lower(), "description should mention local")

    def test_mcp_tool_schema_matches_others(self) -> None:
        self._add_delegate_path()
        from corvin_delegate.mcp_server import _tool_definitions
        defs = {t["name"]: t for t in _tool_definitions()}
        hermes_props = set(defs["delegate_hermes"]["inputSchema"]["properties"])
        claude_props  = set(defs["delegate_claude_code"]["inputSchema"]["properties"])
        # Hermes tool must have at least the same fields as Claude Code tool
        missing = claude_props - hermes_props
        self.assertFalse(missing,
            f"delegate_hermes is missing input fields present in delegate_claude_code: {missing}")


# =============================================================================
# 7-8: Self-test
# =============================================================================

class T04_SelfTest(unittest.TestCase):
    """7-8. _check_hermes_ollama in self_test.py."""

    def test_hermes_ollama_check_present(self) -> None:
        sys.path.insert(0, str(SHARED))
        from self_test import _check_hermes_ollama
        results = _check_hermes_ollama()
        self.assertTrue(results, "expected at least one CheckResult")
        names = [r.name for r in results]
        self.assertIn("engine.hermes_ollama", names)

    def test_hermes_ollama_check_is_info_when_reachable(self) -> None:
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
            ollama_up = True
        except Exception:
            ollama_up = False

        sys.path.insert(0, str(SHARED))
        from self_test import _check_hermes_ollama, INFO, WARNING
        results = _check_hermes_ollama()
        check = next(r for r in results if r.name == "engine.hermes_ollama")

        if ollama_up:
            self.assertEqual(check.severity, INFO,
                f"expected INFO when Ollama reachable, got {check.severity}: {check.detail}")
            self.assertTrue(check.ok)
        else:
            self.assertEqual(check.severity, WARNING,
                f"expected WARNING when Ollama down, got {check.severity}")

    def test_hermes_check_is_never_critical(self) -> None:
        sys.path.insert(0, str(SHARED))
        from self_test import _check_hermes_ollama, CRITICAL
        results = _check_hermes_ollama()
        for r in results:
            self.assertNotEqual(r.severity, CRITICAL,
                f"hermes self-test must never be CRITICAL: {r}")


# =============================================================================
# 9-11: Adapter dispatch
# =============================================================================

class T05_AdapterDispatch(unittest.TestCase):
    """9-11. Adapter imports and dispatch."""

    def setUp(self) -> None:
        sys.path.insert(0, str(SHARED))

    def test_hermes_engine_imported_in_adapter(self) -> None:
        import adapter
        self.assertIsNotNone(adapter._HermesEngine,
                             "_HermesEngine is None in adapter — import failed")
        from agents.hermes_engine import HermesEngine
        self.assertIs(adapter._HermesEngine, HermesEngine)

    def test_call_hermes_streaming_callable(self) -> None:
        import adapter
        fn = getattr(adapter, "_call_hermes_streaming_via_engine", None)
        self.assertIsNotNone(fn, "_call_hermes_streaming_via_engine not found in adapter")
        self.assertTrue(callable(fn))

    def test_adapter_dispatch_hermes_worker_profile(self) -> None:
        """11. End-to-end: call_claude_streaming with hermes-worker profile
        routes to _call_hermes_streaming_via_engine, not Claude Code."""
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        except Exception:
            self.skipTest("Ollama not reachable")

        sys.path.insert(0, str(SHARED))
        import adapter

        profile = {
            "default_engine": "hermes",
            "model": TEST_MODEL,
            "append_system": "You are a test bot. Be concise.",
        }

        # Spy: track whether _call_hermes_streaming_via_engine was invoked
        hermes_called = []
        original = adapter._call_hermes_streaming_via_engine

        def spy_hermes(*args, **kwargs):
            hermes_called.append(True)
            return original(*args, **kwargs)

        with patch.object(adapter, "_call_hermes_streaming_via_engine", spy_hermes):
            # We need to bypass the budget preflight and fake_claude shortcircuit
            with patch.object(adapter, "_budget_preflight", return_value=(True, None)):
                result = adapter.call_claude_streaming(
                    PONG_PROMPT,
                    channel="discord",
                    chat_key="e2e-hermes-test",
                    profile=profile,
                )

        self.assertTrue(hermes_called,
            "call_claude_streaming with default_engine='hermes' did NOT "
            "route to _call_hermes_streaming_via_engine")
        self.assertTrue(result.strip(), f"expected non-empty response, got: {result!r}")
        self.assertIn("pong", result.lower(),
            f"expected 'pong' in response, got: {result!r}")

    def test_hermes_dispatch_does_not_call_claude_code(self) -> None:
        """15. Hermes path must NOT touch _call_claude_streaming_via_engine."""
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        except Exception:
            self.skipTest("Ollama not reachable")

        sys.path.insert(0, str(SHARED))
        import adapter

        profile = {"default_engine": "hermes", "model": TEST_MODEL}
        claude_called = []

        def spy_claude(*args, **kwargs):
            claude_called.append(True)
            return "claude was called (should not happen)"

        with patch.object(adapter, "_call_claude_streaming_via_engine", spy_claude):
            with patch.object(adapter, "_budget_preflight", return_value=(True, None)):
                adapter.call_claude_streaming(
                    PONG_PROMPT, channel="discord",
                    chat_key="e2e-isolation-test", profile=profile,
                )

        self.assertFalse(claude_called,
            "hermes dispatch incorrectly invoked _call_claude_streaming_via_engine")


# =============================================================================
# 12-14: Persona
# =============================================================================

class T06_Persona(unittest.TestCase):
    """12-14. hermes-worker persona."""

    def _resolver_path(self) -> Path:
        return REPO_ROOT / "operator" / "cowork" / "lib"

    def test_hermes_worker_persona_loads(self) -> None:
        persona_file = REPO_ROOT / "operator" / "cowork" / "personas" / "hermes-worker.json"
        self.assertTrue(persona_file.exists(), f"persona file missing: {persona_file}")
        persona = json.loads(persona_file.read_text())
        self.assertEqual(persona.get("default_engine"), "hermes",
                         f"expected default_engine='hermes', got {persona.get('default_engine')!r}")

    def test_routing_anchors_present(self) -> None:
        persona_file = REPO_ROOT / "operator" / "cowork" / "personas" / "hermes-worker.json"
        persona = json.loads(persona_file.read_text())
        anchors = persona.get("routing_anchors", [])
        self.assertTrue(anchors, "routing_anchors should not be empty")
        anchor_str = " ".join(anchors).lower()
        for expected in ("local", "hermes", "confidential"):
            self.assertIn(expected, anchor_str,
                f"routing_anchors should contain {expected!r}: {anchors}")

    def test_zero_config_true(self) -> None:
        persona_file = REPO_ROOT / "operator" / "cowork" / "personas" / "hermes-worker.json"
        persona = json.loads(persona_file.read_text())
        self.assertTrue(persona.get("zero_config"),
                        "hermes-worker persona should have zero_config: true")

    def test_persona_via_resolver(self) -> None:
        lib = self._resolver_path()
        if lib not in sys.path:
            sys.path.insert(0, str(lib))
            sys.path.insert(0, str(lib.parent / "lib"))
        try:
            env_backup = os.environ.get("COWORK_USER_DIR")
            os.environ["COWORK_USER_DIR"] = "/tmp/_e2e_cowork_nonexistent"
            import importlib
            import resolver as _r
            importlib.reload(_r)
            persona = _r.load("hermes-worker")
        finally:
            if env_backup is None:
                os.environ.pop("COWORK_USER_DIR", None)
            else:
                os.environ["COWORK_USER_DIR"] = env_backup
        self.assertIsNotNone(persona, "resolver.load('hermes-worker') returned None")
        self.assertEqual(persona.get("default_engine"), "hermes")


# =============================================================================
# T07 — ADR-0067 production parity (M2.1–M2.5)
# =============================================================================


class T07_ProductionParity(unittest.TestCase):
    """ADR-0067 M2.1-M2.5 production parity checks."""

    def test_m21_gate_helper_exists_in_adapter(self) -> None:
        """M2.1: _run_pre_dispatch_gates() must be present and callable."""
        sys.path.insert(0, str(SHARED))
        import adapter
        fn = getattr(adapter, "_run_pre_dispatch_gates", None)
        self.assertIsNotNone(fn, "_run_pre_dispatch_gates not found in adapter")
        self.assertTrue(callable(fn))

    def test_m21_gate_called_during_hermes_dispatch(self) -> None:
        """M2.1: gate is actually invoked when routing a hermes profile."""
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        except Exception:
            self.skipTest("Ollama not reachable")

        sys.path.insert(0, str(SHARED))
        import adapter
        gate_calls = []
        original_gate = adapter._run_pre_dispatch_gates

        def spy_gate(*args, **kwargs):
            gate_calls.append(True)
            return original_gate(*args, **kwargs)

        profile = {"default_engine": "hermes", "model": TEST_MODEL}
        with patch.object(adapter, "_run_pre_dispatch_gates", spy_gate):
            with patch.object(adapter, "_budget_preflight", return_value=(True, None)):
                adapter.call_claude_streaming(
                    "Say: test", channel="discord",
                    chat_key="gate-spy-test", profile=profile,
                )
        self.assertTrue(gate_calls,
                        "_run_pre_dispatch_gates was NOT called during hermes dispatch")

    def test_m22_hermes_events_registered(self) -> None:
        """M2.2: hermes.* and opencode.* events in security_events EVENT_SEVERITY."""
        sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))
        from forge.security_events import EVENT_SEVERITY
        for event in ("hermes.turn_start", "hermes.turn_end",
                      "hermes.turn_error", "hermes.stream_timeout",
                      "hermes.ollama_unavailable"):
            self.assertIn(event, EVENT_SEVERITY, f"missing event: {event!r}")
        for event in ("opencode.turn_start", "opencode.turn_end",
                      "opencode.turn_error"):
            self.assertIn(event, EVENT_SEVERITY, f"missing event: {event!r}")

    def test_m22_console_event_registered(self) -> None:
        """M2.2: console.engine_setting_updated registered."""
        sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))
        from forge.security_events import EVENT_SEVERITY
        self.assertIn("console.engine_setting_updated", EVENT_SEVERITY)

    def test_m23_hermes_in_engine_switch_valid_engines(self) -> None:
        """M2.3: hermes in VALID_ENGINES and ENGINE_ALIASES."""
        sys.path.insert(0, str(SHARED))
        import engine_switch
        self.assertIn("hermes", engine_switch.VALID_ENGINES)
        self.assertIn("hermes", engine_switch.ENGINE_ALIASES)
        self.assertIn("hermes-fast", engine_switch.ENGINE_ALIASES)
        self.assertIn("hermes-balanced", engine_switch.ENGINE_ALIASES)
        self.assertIn("hermes", engine_switch.supported_aliases())

    def test_m23_hermes_resolve_alias_works(self) -> None:
        """M2.3: engine_switch.resolve_alias('hermes') returns correct engine_id."""
        sys.path.insert(0, str(SHARED))
        import engine_switch
        result = engine_switch.resolve_alias("hermes")
        self.assertIsNotNone(result, "resolve_alias('hermes') returned None")
        self.assertEqual(result["engine"], "hermes")
        result_fast = engine_switch.resolve_alias("hermes-fast")
        self.assertIsNotNone(result_fast)
        self.assertEqual(result_fast["engine"], "hermes")
        self.assertEqual(result_fast["model"], "hermes-fast")

    def test_m24_console_engine_route_importable(self) -> None:
        """M2.4: console engine route is importable and has correct prefix."""
        console_path = str(REPO_ROOT / "core" / "console")
        if console_path not in sys.path:
            sys.path.insert(0, console_path)
        try:
            from corvin_console.routes.engine import router, VALID_CONSOLE_ENGINES
            self.assertTrue(hasattr(router, "routes"),
                            "engine router has no routes attribute")
            self.assertIn("claude_code", VALID_CONSOLE_ENGINES)
            self.assertIn("hermes", VALID_CONSOLE_ENGINES)
            self.assertEqual(router.prefix, "/settings/engine")
        except ImportError as e:
            self.skipTest(f"Console deps not available: {e}")

    def test_m25_engine_metrics_importable(self) -> None:
        """M2.5: engine_metrics module importable, functions callable without prometheus."""
        sys.path.insert(0, str(SHARED))
        try:
            from engine_metrics import record_hermes_turn, record_opencode_turn
            # Must not raise even without prometheus_client installed
            record_hermes_turn(outcome="success", persona="test", duration_s=1.0)
            record_hermes_turn(outcome="error", persona="", duration_s=0.5)
            record_opencode_turn(outcome="success", persona="test", duration_s=2.0)
        except ImportError as e:
            self.fail(f"engine_metrics not importable: {e}")

    def test_m25_metrics_no_anthropic_import(self) -> None:
        """M2.5: engine_metrics.py must not import anthropic."""
        import ast
        src = (SHARED / "engine_metrics.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("anthropic", alias.name or "")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn("anthropic", node.module or "")


# =============================================================================
# Runner
# =============================================================================

def run() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        T01_EngineProtocol,
        T02_LiveOllamaRoundtrip,
        T03_DelegationStack,
        T04_SelfTest,
        T05_AdapterDispatch,
        T06_Persona,
        T07_ProductionParity,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    print(f"\n{COLOUR_HEAD}=== Hermes E2E Full Verification ==={COLOUR_RESET}\n")
    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)

    print()
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    skipped = len(result.skipped)
    passed = total - failed - skipped

    if failed == 0:
        print(f"{COLOUR_OK}ALL CHECKS PASSED{COLOUR_RESET}  "
              f"({passed} passed, {skipped} skipped, {total} total)")
        return 0
    else:
        print(f"{COLOUR_FAIL}{failed} CHECK(S) FAILED{COLOUR_RESET}  "
              f"({passed} passed, {failed} failed, {skipped} skipped, {total} total)")
        return 1


if __name__ == "__main__":
    sys.exit(run())
