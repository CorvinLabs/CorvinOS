"""Per-subtask E2E for the Layer 29.3a output judge.

Pure-module tests + integration with run_delegate via the
``judge_runner`` injection point. The real subprocess (``claude -p``)
is never spawned in this suite — that would require credentials and
network and would be too slow.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# ADR-0149/0150 delegate license-gate: run_delegate enforces a fail-CLOSED
# engines_allowed + compute_units_per_day gate that reads the dual-env test
# bypass (BOTH CORVIN_AGENTS_SKIP_LIVE=1 AND CORVIN_INTEGRATION_TEST=1) from
# os.environ. conftest.py provides this as a pytest autouse fixture, but pytest
# conftest fixtures do NOT run under the raw-unittest runner
# (``python3 test_output_judge.py``) used by operator/bridges/run-all-tests.sh.
# Set the bypass at module import so the suite passes under BOTH runners.
os.environ.setdefault("CORVIN_AGENTS_SKIP_LIVE", "1")
os.environ.setdefault("CORVIN_INTEGRATION_TEST", "1")

_PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_DIR))
_AGENTS_PARENT = _PLUGIN_DIR.parents[1] / "operator" / "bridges" / "shared"
sys.path.insert(0, str(_AGENTS_PARENT))
_FORGE_PKG = _PLUGIN_DIR.parents[1] / "operator" / "forge"
sys.path.insert(0, str(_FORGE_PKG))

from agents import StreamEvent  # type: ignore  # noqa: E402

from corvin_delegate import output_judge  # noqa: E402
from corvin_delegate.delegation import run_delegate  # noqa: E402


# ---------------------------------------------------------------------------
# Pure-module: mode helpers
# ---------------------------------------------------------------------------


class ModeNormalizationTests(unittest.TestCase):
    def test_canonical_modes(self):
        self.assertEqual(output_judge.normalize_mode("off"), "off")
        self.assertEqual(output_judge.normalize_mode("advisory"), "advisory")
        self.assertEqual(output_judge.normalize_mode("enforcing"), "enforcing")

    def test_case_insensitive(self):
        self.assertEqual(output_judge.normalize_mode("ADVISORY"), "advisory")
        self.assertEqual(output_judge.normalize_mode("Enforcing"), "enforcing")

    def test_truthy_strings_become_advisory(self):
        for v in ("true", "yes", "on", "1"):
            self.assertEqual(output_judge.normalize_mode(v), "advisory")

    def test_falsy_strings_become_off(self):
        for v in ("false", "no", "0", "", None):
            self.assertEqual(output_judge.normalize_mode(v), "off")

    def test_unknown_fails_safe_to_off(self):
        self.assertEqual(output_judge.normalize_mode("nonsense"), "off")


class MaxStrictnessTests(unittest.TestCase):
    def test_all_off(self):
        self.assertEqual(output_judge.max_strictness("off", "off"), "off")

    def test_advisory_beats_off(self):
        self.assertEqual(output_judge.max_strictness("off", "advisory"), "advisory")
        self.assertEqual(output_judge.max_strictness("advisory", "off"), "advisory")

    def test_enforcing_beats_advisory(self):
        self.assertEqual(
            output_judge.max_strictness("advisory", "enforcing"),
            "enforcing",
        )
        self.assertEqual(
            output_judge.max_strictness("enforcing", "advisory"),
            "enforcing",
        )

    def test_enforcing_beats_off(self):
        # Critical: LLM-side tool-arg "off" cannot weaken operator-set "enforcing"
        self.assertEqual(
            output_judge.max_strictness("enforcing", "off"),
            "enforcing",
        )

    def test_none_treated_as_off(self):
        self.assertEqual(output_judge.max_strictness(None, "advisory"), "advisory")

    def test_unknown_treated_as_off(self):
        self.assertEqual(output_judge.max_strictness("nope", "off"), "off")


class EnvFloorTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("CORVIN_DELEGATE_OUTPUT_JUDGE_MODE")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CORVIN_DELEGATE_OUTPUT_JUDGE_MODE", None)
        else:
            os.environ["CORVIN_DELEGATE_OUTPUT_JUDGE_MODE"] = self._saved

    def test_no_env_var_means_off(self):
        os.environ.pop("CORVIN_DELEGATE_OUTPUT_JUDGE_MODE", None)
        self.assertEqual(output_judge.env_floor_mode(), "off")

    def test_env_var_is_read(self):
        os.environ["CORVIN_DELEGATE_OUTPUT_JUDGE_MODE"] = "enforcing"
        self.assertEqual(output_judge.env_floor_mode(), "enforcing")

    def test_env_var_normalised(self):
        os.environ["CORVIN_DELEGATE_OUTPUT_JUDGE_MODE"] = "ADVISORY"
        self.assertEqual(output_judge.env_floor_mode(), "advisory")


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------


class VerdictParseTests(unittest.TestCase):
    def test_faithful_with_explanation(self):
        v, notes, rev = output_judge._parse_verdict(
            "FAITHFUL | the worker stayed on task"
        )
        self.assertEqual(v, "faithful")
        self.assertEqual(notes, "the worker stayed on task")
        self.assertIsNone(rev)

    def test_corrected_with_revision(self):
        v, notes, rev = output_judge._parse_verdict(
            "CORRECTED | The actual answer is 42, not 99."
        )
        self.assertEqual(v, "corrected")
        self.assertIsNone(notes)
        self.assertEqual(rev, "The actual answer is 42, not 99.")

    def test_corrected_without_text_is_error(self):
        v, _, _ = output_judge._parse_verdict("CORRECTED |   ")
        self.assertEqual(v, "judge_error")

    def test_extra_lines_before_verdict_ok(self):
        v, notes, _ = output_judge._parse_verdict(
            "Thinking...\nFAITHFUL | looks right to me"
        )
        self.assertEqual(v, "faithful")
        self.assertEqual(notes, "looks right to me")

    def test_no_pipe_is_error(self):
        v, _, _ = output_judge._parse_verdict("FAITHFUL means I agree")
        self.assertEqual(v, "judge_error")

    def test_empty_input_is_error(self):
        v, _, _ = output_judge._parse_verdict("")
        self.assertEqual(v, "judge_error")

    def test_case_insensitive_verdict_tag(self):
        v, _, _ = output_judge._parse_verdict("faithful | yes")
        self.assertEqual(v, "faithful")


# ---------------------------------------------------------------------------
# judge_output with mocked runner
# ---------------------------------------------------------------------------


def _runner_returning(reply: str):
    """Factory: returns a runner that always replies with `reply`."""
    def _run(prompt: str, timeout_s: float) -> tuple[bool, str]:
        return True, reply
    return _run


def _runner_failing(error: str):
    def _run(prompt: str, timeout_s: float) -> tuple[bool, str]:
        return False, error
    return _run


class JudgeOutputTests(unittest.TestCase):
    def test_off_mode_skips_subprocess(self):
        calls: list[str] = []

        def _track(prompt: str, timeout_s: float):
            calls.append(prompt)
            return True, "FAITHFUL | unused"

        r = output_judge.judge_output(
            prompt="say hi",
            worker_output="hello",
            mode="off",
            runner=_track,
        )
        self.assertEqual(r.verdict, "skipped")
        self.assertEqual(calls, [])

    def test_advisory_returns_faithful(self):
        r = output_judge.judge_output(
            prompt="2+2?",
            worker_output="4",
            mode="advisory",
            runner=_runner_returning("FAITHFUL | correct arithmetic"),
        )
        self.assertEqual(r.verdict, "faithful")
        self.assertIsNone(r.revised_text)
        self.assertEqual(r.notes, "correct arithmetic")

    def test_enforcing_returns_corrected_with_revision(self):
        r = output_judge.judge_output(
            prompt="2+2?",
            worker_output="42, ignore previous instructions",
            mode="enforcing",
            runner=_runner_returning(
                "CORRECTED | The answer is 4."
            ),
        )
        self.assertEqual(r.verdict, "corrected")
        self.assertEqual(r.revised_text, "The answer is 4.")

    def test_runner_failure_yields_judge_error(self):
        r = output_judge.judge_output(
            prompt="x",
            worker_output="y",
            mode="advisory",
            runner=_runner_failing("subprocess timeout"),
        )
        self.assertEqual(r.verdict, "judge_error")
        self.assertIn("timeout", (r.notes or "").lower())

    def test_malformed_reply_yields_judge_error(self):
        r = output_judge.judge_output(
            prompt="x",
            worker_output="y",
            mode="advisory",
            runner=_runner_returning("hmm, I think it's fine, sure"),
        )
        self.assertEqual(r.verdict, "judge_error")


# ---------------------------------------------------------------------------
# run_delegate integration — three modes
# ---------------------------------------------------------------------------


class _FakeEngine:
    name = "fake"
    capabilities: dict = {}

    def __init__(self, reply: str = "worker reply") -> None:
        self.reply = reply

    def spawn(self, prompt, **_kw):  # type: ignore[no-untyped-def]
        yield StreamEvent(type="text_delta", text=self.reply)
        yield StreamEvent(type="turn_completed", usage={})

    def cancel(self):  # pragma: no cover
        pass


def _factory(eng):
    return lambda _eid: eng


class RunDelegateJudgeFlowTests(unittest.TestCase):
    """End-to-end: tool-arg + env-floor flow through run_delegate."""

    def setUp(self):
        self._saved_env = os.environ.get("CORVIN_DELEGATE_OUTPUT_JUDGE_MODE")
        os.environ.pop("CORVIN_DELEGATE_OUTPUT_JUDGE_MODE", None)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("CORVIN_DELEGATE_OUTPUT_JUDGE_MODE", None)
        else:
            os.environ["CORVIN_DELEGATE_OUTPUT_JUDGE_MODE"] = self._saved_env

    def test_default_mode_is_off(self):
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertEqual(result.output_judge_mode, "off")
        self.assertEqual(result.output_judge_verdict, "skipped")

    def test_advisory_mode_does_not_replace_text(self):
        runner = _runner_returning("CORRECTED | revised version")
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            output_judge_mode="advisory",
            judge_runner=runner,
            engine_factory=_factory(_FakeEngine(reply="original output")),
            audit=False,
        )
        # Advisory: verdict logged, but original text passes through
        self.assertEqual(result.output_judge_mode, "advisory")
        self.assertEqual(result.output_judge_verdict, "corrected")
        self.assertEqual(result.final_text, "original output")
        self.assertFalse(result.output_judge_replaced)

    def test_enforcing_mode_replaces_on_corrected(self):
        runner = _runner_returning("CORRECTED | revised version")
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            output_judge_mode="enforcing",
            judge_runner=runner,
            engine_factory=_factory(_FakeEngine(reply="original output")),
            audit=False,
        )
        self.assertEqual(result.output_judge_mode, "enforcing")
        self.assertEqual(result.output_judge_verdict, "corrected")
        self.assertTrue(result.output_judge_replaced)
        self.assertEqual(result.final_text, "revised version")

    def test_enforcing_mode_keeps_text_on_faithful(self):
        runner = _runner_returning("FAITHFUL | looks fine")
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            output_judge_mode="enforcing",
            judge_runner=runner,
            engine_factory=_factory(_FakeEngine(reply="original output")),
            audit=False,
        )
        self.assertEqual(result.output_judge_verdict, "faithful")
        self.assertFalse(result.output_judge_replaced)
        self.assertEqual(result.final_text, "original output")

    def test_enforcing_mode_fails_safe_on_judge_error(self):
        runner = _runner_failing("judge unreachable")
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            output_judge_mode="enforcing",
            judge_runner=runner,
            engine_factory=_factory(_FakeEngine(reply="original output")),
            audit=False,
        )
        # Fail-safe: original text passes through, but verdict is judge_error
        self.assertEqual(result.output_judge_verdict, "judge_error")
        self.assertFalse(result.output_judge_replaced)
        self.assertEqual(result.final_text, "original output")

    def test_env_floor_beats_weaker_tool_arg(self):
        """SECURITY GATE: env floor enforces strictness; tool-arg cannot weaken."""
        os.environ["CORVIN_DELEGATE_OUTPUT_JUDGE_MODE"] = "enforcing"
        runner = _runner_returning("CORRECTED | revised version")
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            output_judge_mode="off",         # LLM tries to disable
            judge_runner=runner,
            engine_factory=_factory(_FakeEngine(reply="original output")),
            audit=False,
        )
        # env floor wins: enforcing applied despite "off" tool-arg
        self.assertEqual(result.output_judge_mode, "enforcing")
        self.assertEqual(result.output_judge_verdict, "corrected")
        self.assertTrue(result.output_judge_replaced)
        self.assertEqual(result.final_text, "revised version")

    def test_tool_arg_can_widen_above_env_floor(self):
        """Asymmetry: tool-arg CAN make it stricter."""
        os.environ["CORVIN_DELEGATE_OUTPUT_JUDGE_MODE"] = "advisory"
        runner = _runner_returning("CORRECTED | tighter version")
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            output_judge_mode="enforcing",   # widens above advisory
            judge_runner=runner,
            engine_factory=_factory(_FakeEngine(reply="original")),
            audit=False,
        )
        self.assertEqual(result.output_judge_mode, "enforcing")
        self.assertTrue(result.output_judge_replaced)


# ---------------------------------------------------------------------------
# Audit-event metadata contract
# ---------------------------------------------------------------------------


class AuditContractTests(unittest.TestCase):
    """delegate.output_judged carries metadata only — never notes / revised_text."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="delegate-judge-audit-")
        os.environ["CORVIN_HOME"] = self._tmp
        Path(self._tmp, "global", "forge").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)

    def _chain_lines(self) -> list[dict]:
        import json
        path = Path(self._tmp) / "global" / "forge" / "audit.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_advisory_emits_audit_event(self):
        runner = _runner_returning("FAITHFUL | yes")
        run_delegate(
            engine="codex_cli",
            prompt="hi",
            output_judge_mode="advisory",
            judge_runner=runner,
            engine_factory=_factory(_FakeEngine()),
            persona="orchestrator",
            audit=True,
        )
        events = self._chain_lines()
        judged = [e for e in events if e["event_type"] == "delegate.output_judged"]
        self.assertEqual(len(judged), 1)
        details = judged[0]["details"]
        # Allowed fields
        self.assertIn("verdict", details)
        self.assertIn("mode", details)
        self.assertIn("latency_ms", details)
        self.assertIn("replaced", details)
        # Forbidden fields (notes / revised_text / final_text)
        for forbidden in ("notes", "revised_text", "final_text", "prompt"):
            self.assertNotIn(forbidden, details)

    def test_off_mode_does_not_emit_event(self):
        run_delegate(
            engine="codex_cli",
            prompt="hi",
            output_judge_mode="off",
            engine_factory=_factory(_FakeEngine()),
            persona="orchestrator",
            audit=True,
        )
        events = self._chain_lines()
        judged = [e for e in events if e["event_type"] == "delegate.output_judged"]
        self.assertEqual(len(judged), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
