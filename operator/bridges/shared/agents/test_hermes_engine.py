"""Tests for HermesEngine (L22 WorkerEngine wrapping Ollama HTTP API).

Structure mirrors test_engines_e2e.py:
  - Fast unit tests (always run): protocol conformance, capabilities,
    AST lint for anthropic import, error handling for unavailable Ollama
  - Live tests (skip when CORVIN_AGENTS_SKIP_LIVE=1 OR Ollama absent):
    real HTTP round-trip to Ollama, single-turn "pong" probe.

The live tests pick the model via CORVIN_HERMES_TEST_MODEL (default:
whatever CORVIN_HERMES_MODEL resolves to, falling back to nous-hermes-2).
If the model is not pulled yet, the test is skipped gracefully.

Run:
    python3 operator/bridges/shared/agents/test_hermes_engine.py
"""

from __future__ import annotations

import ast
import os
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
sys.path.insert(0, str(SHARED))

from agents import StreamEvent, WorkerEngine, collect  # noqa: E402
from agents.hermes_engine import HermesEngine, HERMES_MODEL_ALIASES  # noqa: E402


SKIP_LIVE = (
    os.environ.get("CORVIN_AGENTS_SKIP_LIVE") == "1"
    or os.environ.get("CORVIN_AGENTS_SKIP_LIVE") == "1"
)
PROMPT_PONG = "Reply with exactly the single word: pong"

_OLLAMA_BASE = os.environ.get("CORVIN_OLLAMA_BASE_URL", "http://localhost:11434")
_TEST_MODEL = os.environ.get(
    "CORVIN_HERMES_TEST_MODEL",
    os.environ.get("CORVIN_HERMES_MODEL", "qwen3:1.7b"),
)


def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen(f"{_OLLAMA_BASE}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def _model_available(model: str) -> bool:
    """Check if the given model is already pulled in the local Ollama."""
    try:
        with urllib.request.urlopen(f"{_OLLAMA_BASE}/api/tags", timeout=3) as resp:
            import json
            data = json.loads(resp.read())
        pulled = [m.get("name", "") for m in data.get("models", [])]
        # Normalise: "qwen3:1.7b" and "qwen3" might both match "qwen3:1.7b"
        return any(
            m == model or m.startswith(model.split(":")[0] + ":")
            for m in pulled
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fast unit tests (always run)
# ---------------------------------------------------------------------------


class ProtocolContractTests(unittest.TestCase):

    def test_hermes_engine_satisfies_protocol(self) -> None:
        engine = HermesEngine()
        self.assertIsInstance(engine, WorkerEngine)
        self.assertEqual(engine.name, "hermes")

    def test_capabilities_declared_correctly(self) -> None:
        caps = HermesEngine.capabilities
        self.assertEqual(caps["mid_stream_inject"], "buffered")  # M2: buffered /btw queue
        self.assertEqual(caps["hooks"], "teb_brokered")           # M4: TEB-brokered hooks
        self.assertFalse(caps["skills_tool"])
        self.assertFalse(caps["mcp"])
        self.assertTrue(caps["stream_json"])
        self.assertFalse(caps["session_pinning"])
        self.assertIn("read_only", caps["permission_modes"])
        self.assertTrue(caps["add_system_prompt"])

    def test_model_alias_resolution_in_init(self) -> None:
        e = HermesEngine(model="hermes-fast")
        self.assertEqual(e.model, HERMES_MODEL_ALIASES["hermes-fast"])

    def test_model_alias_resolution_in_spawn(self) -> None:
        # Alias passed to spawn() should resolve via HERMES_MODEL_ALIASES
        engine = HermesEngine(base_url="http://localhost:19999")
        events = list(engine.spawn("hi", model="hermes-fast"))
        # We expect an error from the bad port, but the session_started
        # event should NOT appear here — it would carry the resolved model
        # We just verify no crash and we get an error event
        types = [ev.type for ev in events]
        self.assertIn("error", types)

    def test_custom_base_url(self) -> None:
        e = HermesEngine(base_url="http://gpu-node:11434")
        self.assertEqual(e.base_url, "http://gpu-node:11434")

    def test_base_url_trailing_slash_stripped(self) -> None:
        e = HermesEngine(base_url="http://localhost:11434/")
        self.assertEqual(e.base_url, "http://localhost:11434")

    def test_unknown_model_passthrough(self) -> None:
        e = HermesEngine(model="nous-hermes-2:7b")
        self.assertEqual(e.model, "nous-hermes-2:7b")

    def test_ollama_unavailable_yields_error_not_raises(self) -> None:
        engine = HermesEngine(base_url="http://localhost:19999")
        events = list(engine.spawn("hello"))
        self.assertGreater(len(events), 0)
        self.assertEqual(events[-1].type, "error")
        self.assertIn("ollama", events[0].error.lower())

    def test_error_event_carries_message(self) -> None:
        engine = HermesEngine(base_url="http://localhost:19999")
        events = list(engine.spawn("hello"))
        err = [ev for ev in events if ev.type == "error"]
        self.assertTrue(all(isinstance(ev.error, str) for ev in err))
        self.assertTrue(all(ev.error for ev in err))

    def test_cancel_before_spawn_does_not_raise(self) -> None:
        engine = HermesEngine()
        engine.cancel()  # Must not raise

    def test_does_not_import_anthropic(self) -> None:
        source = (HERE / "hermes_engine.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        "anthropic", alias.name or "",
                        msg="hermes_engine.py MUST NOT import anthropic",
                    )
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn(
                    "anthropic", node.module or "",
                    msg="hermes_engine.py MUST NOT import anthropic",
                )

    def test_all_aliases_have_string_values(self) -> None:
        for alias, tag in HERMES_MODEL_ALIASES.items():
            self.assertIsInstance(alias, str)
            self.assertIsInstance(tag, str)
            self.assertTrue(tag, f"alias {alias!r} maps to empty string")

    def test_env_override_model(self) -> None:
        with patch.dict(os.environ, {"CORVIN_HERMES_MODEL": "hermes-fast"}):
            from agents.hermes_engine import _resolve_default_model
            resolved = _resolve_default_model()
            self.assertEqual(resolved, HERMES_MODEL_ALIASES["hermes-fast"])

    def test_env_override_base_url(self) -> None:
        with patch.dict(os.environ,
                        {"CORVIN_OLLAMA_BASE_URL": "http://custom:9999"}):
            from agents.hermes_engine import _resolve_base_url
            resolved = _resolve_base_url()
            self.assertEqual(resolved, "http://custom:9999")

    def test_default_model_resolves_to_installed_qwen3(self) -> None:
        """Adversarial fresh-install finding (BLOCKER): a &lt;6 GB box only pulls
        qwen3:1.7b, but the default used to hardcode qwen3:8b → every Hermes turn
        errored 'model not available' with no recovery. With no env override, the
        default must resolve to the qwen3 tag ACTUALLY installed in Ollama."""
        from agents import hermes_engine as he
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORVIN_HERMES_MODEL", None)
            # Only the small model is pulled.
            with patch.object(he, "_installed_ollama_models",
                              return_value=["qwen3:1.7b"]):
                self.assertEqual(he._resolve_default_model(), "qwen3:1.7b")
            # Full box with the 8b present → keep the 8b default.
            with patch.object(he, "_installed_ollama_models",
                              return_value=["qwen3:1.7b", "qwen3:8b"]):
                self.assertEqual(he._resolve_default_model(), "qwen3:8b")
            # Ollama unreachable / empty → fall back to the built-in default so the
            # actionable "run: ollama pull" path still fires.
            with patch.object(he, "_installed_ollama_models", return_value=[]):
                self.assertEqual(he._resolve_default_model(), he._DEFAULT_MODEL)

    def test_pick_installed_qwen3_prefers_largest(self) -> None:
        from agents import hermes_engine as he
        self.assertEqual(he._pick_installed_qwen3(["qwen3:1.7b", "qwen3:14b"]),
                         "qwen3:14b")
        self.assertIsNone(he._pick_installed_qwen3(["llama3:8b"]))


# ---------------------------------------------------------------------------
# Live tests (require Ollama + pulled model)
# ---------------------------------------------------------------------------


@unittest.skipIf(SKIP_LIVE, "CORVIN_AGENTS_SKIP_LIVE=1")
@unittest.skipUnless(_ollama_reachable(), f"Ollama not reachable at {_OLLAMA_BASE}")
@unittest.skipUnless(
    _model_available(_TEST_MODEL),
    f"Model {_TEST_MODEL!r} not pulled — run: ollama pull {_TEST_MODEL}",
)
class LiveHermesEngineTests(unittest.TestCase):

    def setUp(self) -> None:
        self.engine = HermesEngine(model=_TEST_MODEL)

    def test_pong(self) -> None:
        result = collect(self.engine.spawn(PROMPT_PONG))
        self.assertIsNone(result.error, f"expected no error, got: {result.error}")
        self.assertIn("pong", result.final_text.lower())

    def test_system_prompt_accepted(self) -> None:
        result = collect(self.engine.spawn(
            "What is your name?",
            system="You are called TestBot. Always say your name is TestBot.",
        ))
        self.assertIsNone(result.error)
        self.assertTrue(result.final_text, "expected non-empty response")

    def test_events_have_correct_shape(self) -> None:
        events = list(self.engine.spawn(PROMPT_PONG))
        types = [ev.type for ev in events]
        self.assertIn("session_started", types, f"events: {types}")
        self.assertIn("turn_completed", types, f"events: {types}")
        completed = [ev for ev in events if ev.type == "turn_completed"]
        self.assertEqual(len(completed), 1)
        usage = completed[0].usage or {}
        self.assertIn("input_tokens", usage)
        self.assertIn("output_tokens", usage)

    def test_text_delta_events_accumulate(self) -> None:
        events = list(self.engine.spawn("Say: hello world"))
        deltas = [ev.text for ev in events if ev.type == "text_delta"]
        self.assertGreater(len(deltas), 0, "expected at least one text_delta event")
        joined = "".join(deltas)
        self.assertTrue(joined.strip(), "accumulated text must be non-empty")

    def test_usage_populated(self) -> None:
        result = collect(self.engine.spawn("Say hello."))
        self.assertIsNone(result.error)
        self.assertGreater(result.usage.get("output_tokens", 0), 0)
        self.assertGreater(result.usage.get("input_tokens", 0), 0)

    def test_final_text_non_empty(self) -> None:
        result = collect(self.engine.spawn("Reply with a single word: yes"))
        self.assertIsNone(result.error)
        self.assertTrue(result.final_text.strip())

    def test_spawn_respects_timeout(self) -> None:
        start = time.monotonic()
        # 120s timeout — should complete well under it for a short prompt
        result = collect(self.engine.spawn("Say hi.", timeout=120.0))
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 60.0, "expected to finish within 60s on any reasonable hardware")
        self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()
