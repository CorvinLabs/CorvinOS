"""Regression: `_classify_browser_intent` must be gated like every other
console LLM spawn (adversarial review finding).

Before this fix, ANY chat message matching the broad browsing-signal regex
(covers ordinary words like "login", "click", "seite", or any message
containing ".com"/".de"/".io" — e.g. a plain email address) triggered an
ungated `claude -p` subprocess spawn, hardcoded to the cloud `claude` binary,
with no L44/L34/L35 pre-spawn gate, no chat-turn quota charge, and no audit
event — even for a tenant explicitly configured to use a local-only engine
(Hermes). This locks in: (1) the gate is consulted and can veto the spawn,
(2) a non-claude_code effective engine skips the classifier entirely rather
than falling back to a hardcoded cloud binary, (3) a gate-orchestration error
fails closed (no auto-spawn), never open.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for p in ("core/console", "operator/bridges/shared", "operator/forge"):
    sys.path.insert(0, str(_REPO / p))

import corvin_console.routes.chat as chat_routes  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class ClassifyBrowserIntentGatingTests(unittest.TestCase):
    BROWSE_ISH_PROMPT = "kannst du bitte auf die webseite gehen und dich einloggen?"

    def test_no_signal_words_never_reaches_the_gate_or_spawns(self) -> None:
        with patch("corvin_console.chat_runtime._effective_os_engine") as eng, \
             patch("subprocess.run") as run:
            out = _run(chat_routes._classify_browser_intent(
                "hi, how are you?", tenant_id="t1", sid_fingerprint="fp1"))
        self.assertIsNone(out)
        eng.assert_not_called()
        run.assert_not_called()

    def test_gate_denial_blocks_the_spawn(self) -> None:
        with patch("corvin_console.chat_runtime._effective_os_engine",
                    return_value="claude_code"), \
             patch("corvin_console._spawn_gates.check_console_spawn_or_refusal",
                    return_value="refused: house rules") as gate, \
             patch("subprocess.run") as run:
            out = _run(chat_routes._classify_browser_intent(
                self.BROWSE_ISH_PROMPT, tenant_id="t1", sid_fingerprint="fp1"))
        self.assertIsNone(out)
        gate.assert_called_once()
        run.assert_not_called()

    def test_gate_orchestration_error_fails_closed_no_spawn(self) -> None:
        with patch("corvin_console.chat_runtime._effective_os_engine",
                    return_value="claude_code"), \
             patch("corvin_console._spawn_gates.check_console_spawn_or_refusal",
                    side_effect=RuntimeError("boom")), \
             patch("subprocess.run") as run:
            out = _run(chat_routes._classify_browser_intent(
                self.BROWSE_ISH_PROMPT, tenant_id="t1", sid_fingerprint="fp1"))
        self.assertIsNone(out)
        run.assert_not_called()

    def test_non_claude_engine_skips_classifier_entirely_no_cloud_spawn(self) -> None:
        """A tenant configured for Hermes (or any non-claude_code engine) must
        never have its raw prompt shelled out to a hardcoded cloud `claude`
        binary just because the classifier ignored engine configuration."""
        with patch("corvin_console.chat_runtime._effective_os_engine",
                    return_value="hermes"), \
             patch("corvin_console._spawn_gates.check_console_spawn_or_refusal") as gate, \
             patch("subprocess.run") as run:
            out = _run(chat_routes._classify_browser_intent(
                self.BROWSE_ISH_PROMPT, tenant_id="t1", sid_fingerprint="fp1"))
        self.assertIsNone(out)
        gate.assert_not_called()
        run.assert_not_called()

    def test_gate_allows_and_classifier_runs_through_configured_binary(self) -> None:
        class _FakeCompleted:
            stdout = "BROWSE: open example.com and log in"
            returncode = 0

        with patch("corvin_console.chat_runtime._effective_os_engine",
                    return_value="claude_code"), \
             patch("corvin_console._spawn_gates.check_console_spawn_or_refusal",
                    return_value=None), \
             patch("corvin_console.chat_runtime._claude_binary",
                    return_value="/opt/claude/claude"), \
             patch("subprocess.run", return_value=_FakeCompleted()) as run:
            out = _run(chat_routes._classify_browser_intent(
                self.BROWSE_ISH_PROMPT, tenant_id="t1", sid_fingerprint="fp1"))
        self.assertEqual(out, "open example.com and log in")
        run.assert_called_once()
        called_argv = run.call_args[0][0]
        self.assertEqual(called_argv[0], "/opt/claude/claude")
        self.assertNotIn("--tools", called_argv)  # bogus flag must be gone


if __name__ == "__main__":
    unittest.main(verbosity=2)
