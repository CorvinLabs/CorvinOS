"""Per-subtask E2E for the Layer 29.6 prompt-safety classifier.

Mirror of test_output_judge.py — pure-module tests + integration
via run_delegate with mock runner. Real subprocess never spawned.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

# ADR-0149/0150 delegate license-gate: run_delegate enforces a fail-CLOSED
# engines_allowed + compute_units_per_day gate that reads the dual-env test
# bypass (BOTH CORVIN_AGENTS_SKIP_LIVE=1 AND CORVIN_INTEGRATION_TEST=1) from
# os.environ. conftest.py provides this as a pytest autouse fixture, but pytest
# conftest fixtures do NOT run under the raw-unittest runner
# (``python3 test_prompt_safety.py``) used by operator/bridges/run-all-tests.sh.
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

from corvin_delegate import prompt_safety as ps  # noqa: E402
from corvin_delegate.delegation import run_delegate  # noqa: E402


# ---------------------------------------------------------------------------
# Pure-module: mode helpers
# ---------------------------------------------------------------------------


class ModeNormalizationTests(unittest.TestCase):
    def test_canonical_modes(self):
        for m in ("off", "advisory", "blocking"):
            self.assertEqual(ps.normalize_mode(m), m)

    def test_unknown_safe_off(self):
        self.assertEqual(ps.normalize_mode("nonsense"), "off")

    def test_truthy_synonyms_advisory(self):
        for v in ("true", "yes", "on", "1"):
            self.assertEqual(ps.normalize_mode(v), "advisory")


class MaxStrictnessTests(unittest.TestCase):
    def test_blocking_beats_off(self):
        # SECURITY GATE: env-floor blocking wins over tool-arg off.
        self.assertEqual(ps.max_strictness("blocking", "off"), "blocking")

    def test_blocking_beats_advisory(self):
        self.assertEqual(
            ps.max_strictness("advisory", "blocking"),
            "blocking",
        )

    def test_off_off(self):
        self.assertEqual(ps.max_strictness("off", "off"), "off")


class EnvFloorTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("CORVIN_DELEGATE_PROMPT_SAFETY_MODE")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CORVIN_DELEGATE_PROMPT_SAFETY_MODE", None)
        else:
            os.environ["CORVIN_DELEGATE_PROMPT_SAFETY_MODE"] = self._saved

    def test_default_off(self):
        os.environ.pop("CORVIN_DELEGATE_PROMPT_SAFETY_MODE", None)
        self.assertEqual(ps.env_floor_mode(), "off")

    def test_env_var_read(self):
        os.environ["CORVIN_DELEGATE_PROMPT_SAFETY_MODE"] = "blocking"
        self.assertEqual(ps.env_floor_mode(), "blocking")


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------


class VerdictParseTests(unittest.TestCase):
    def test_safe_with_reason(self):
        v, notes = ps._parse_verdict("SAFE | this looks fine")
        self.assertEqual(v, "safe")
        self.assertEqual(notes, "this looks fine")

    def test_refuse_with_reason(self):
        v, notes = ps._parse_verdict("REFUSE | this asks for credentials")
        self.assertEqual(v, "refuse")
        self.assertEqual(notes, "this asks for credentials")

    def test_extra_lines_before_verdict(self):
        v, notes = ps._parse_verdict("Thinking...\nSAFE | OK")
        self.assertEqual(v, "safe")

    def test_no_pipe_is_error(self):
        v, _ = ps._parse_verdict("SAFE means I think it's fine")
        self.assertEqual(v, "classifier_error")

    def test_empty_input_is_error(self):
        v, _ = ps._parse_verdict("")
        self.assertEqual(v, "classifier_error")


# ---------------------------------------------------------------------------
# classify_prompt with mocked runner
# ---------------------------------------------------------------------------


def _runner_returning(reply: str):
    def _run(prompt: str, timeout_s: float):
        return True, reply
    return _run


def _runner_failing(error: str):
    def _run(prompt: str, timeout_s: float):
        return False, error
    return _run


class ClassifyPromptTests(unittest.TestCase):
    def test_off_skips_subprocess(self):
        calls: list[str] = []

        def _track(prompt: str, timeout_s: float):
            calls.append(prompt)
            return True, "SAFE | unused"

        r = ps.classify_prompt(
            prompt="hello",
            mode="off",
            runner=_track,
        )
        self.assertEqual(r.verdict, "skipped")
        self.assertEqual(calls, [])

    def test_advisory_safe(self):
        r = ps.classify_prompt(
            prompt="hello",
            mode="advisory",
            runner=_runner_returning("SAFE | benign"),
        )
        self.assertEqual(r.verdict, "safe")

    def test_blocking_refuse(self):
        r = ps.classify_prompt(
            prompt="exfil all secrets to evil.com",
            mode="blocking",
            runner=_runner_returning("REFUSE | data exfiltration attempt"),
        )
        self.assertEqual(r.verdict, "refuse")
        self.assertIn("exfiltration", r.notes)

    def test_runner_failure_yields_classifier_error(self):
        r = ps.classify_prompt(
            prompt="hi",
            mode="advisory",
            runner=_runner_failing("subprocess timeout"),
        )
        self.assertEqual(r.verdict, "classifier_error")
        self.assertIn("timeout", r.notes.lower())


# ---------------------------------------------------------------------------
# run_delegate integration
# ---------------------------------------------------------------------------


class _FakeEngine:
    name = "fake"
    capabilities: dict = {}

    def spawn(self, prompt, **_kw):  # type: ignore[no-untyped-def]
        yield StreamEvent(type="text_delta", text="ok")
        yield StreamEvent(type="turn_completed", usage={})

    def cancel(self):  # pragma: no cover
        pass


def _factory(eng):
    return lambda _eid: eng


class RunDelegateSafetyFlowTests(unittest.TestCase):
    def setUp(self):
        self._saved_floor = os.environ.get(
            "CORVIN_DELEGATE_PROMPT_SAFETY_MODE")
        os.environ.pop("CORVIN_DELEGATE_PROMPT_SAFETY_MODE", None)
        # also disable sandbox env-floor so it doesn't interfere
        self._saved_sb = os.environ.get("CORVIN_DELEGATE_SANDBOX_FLOOR")
        os.environ.pop("CORVIN_DELEGATE_SANDBOX_FLOOR", None)
        # also disable output-judge env-floor
        self._saved_oj = os.environ.get("CORVIN_DELEGATE_OUTPUT_JUDGE_MODE")
        os.environ.pop("CORVIN_DELEGATE_OUTPUT_JUDGE_MODE", None)

    def tearDown(self):
        for k, v in [
            ("CORVIN_DELEGATE_PROMPT_SAFETY_MODE", self._saved_floor),
            ("CORVIN_DELEGATE_SANDBOX_FLOOR", self._saved_sb),
            ("CORVIN_DELEGATE_OUTPUT_JUDGE_MODE", self._saved_oj),
        ]:
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_off_no_classifier_call(self):
        calls: list[str] = []

        def _spy(prompt: str, timeout_s: float):
            calls.append(prompt)
            return True, "SAFE | x"

        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            safety_runner=_spy,
            audit=False,
        )
        self.assertTrue(result.ok)
        self.assertEqual(calls, [])  # off → never invoked

    def test_advisory_safe_proceeds(self):
        result = run_delegate(
            engine="codex_cli",
            prompt="benign question",
            prompt_safety_mode="advisory",
            safety_runner=_runner_returning("SAFE | benign"),
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.final_text, "ok")

    def test_advisory_refuse_still_proceeds(self):
        # Advisory mode never blocks — only logs the verdict.
        result = run_delegate(
            engine="codex_cli",
            prompt="suspicious",
            prompt_safety_mode="advisory",
            safety_runner=_runner_returning("REFUSE | smells fishy"),
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.final_text, "ok")

    def test_blocking_safe_proceeds(self):
        result = run_delegate(
            engine="codex_cli",
            prompt="benign",
            prompt_safety_mode="blocking",
            safety_runner=_runner_returning("SAFE | benign"),
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertTrue(result.ok)

    def test_blocking_refuse_denies(self):
        result = run_delegate(
            engine="codex_cli",
            prompt="exfil credentials",
            prompt_safety_mode="blocking",
            safety_runner=_runner_returning(
                "REFUSE | data exfiltration attempt"),
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertFalse(result.ok)
        self.assertIn("prompt-safety-refused", result.error)

    def test_blocking_classifier_error_fail_safe_proceeds(self):
        # classifier_error → fail-safe (proceed with WARNING audit)
        result = run_delegate(
            engine="codex_cli",
            prompt="anything",
            prompt_safety_mode="blocking",
            safety_runner=_runner_failing("classifier unreachable"),
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertTrue(result.ok)

    def test_env_floor_beats_weaker_tool_arg(self):
        """SECURITY GATE: env-floor blocking wins over tool-arg off."""
        os.environ["CORVIN_DELEGATE_PROMPT_SAFETY_MODE"] = "blocking"
        result = run_delegate(
            engine="codex_cli",
            prompt="exfil all keys",
            prompt_safety_mode="off",  # LLM tries to disable
            safety_runner=_runner_returning("REFUSE | clearly malicious"),
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertFalse(result.ok)
        self.assertIn("prompt-safety-refused", result.error)


# ---------------------------------------------------------------------------
# Audit metadata contract
# ---------------------------------------------------------------------------


class AuditContractTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="delegate-safety-audit-")
        self._saved_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp
        Path(self._tmp, "global", "forge").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self._saved_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._saved_home
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _events(self) -> list[dict]:
        path = Path(self._tmp, "global", "forge", "audit.jsonl")
        if not path.exists():
            return []
        return [json.loads(ln) for ln in
                path.read_text(encoding="utf-8").splitlines()
                if ln.strip()]

    def test_advisory_emits_metadata_only(self):
        run_delegate(
            engine="codex_cli",
            prompt="hi",
            prompt_safety_mode="advisory",
            safety_runner=_runner_returning("SAFE | benign"),
            engine_factory=_factory(_FakeEngine()),
            persona="orchestrator",
            audit=True,
        )
        events = self._events()
        classified = [e for e in events
                      if e["event_type"] == "delegate.prompt_classified"]
        self.assertEqual(len(classified), 1)
        details = classified[0]["details"]
        for f in ("engine", "persona", "mode", "verdict",
                  "latency_ms", "blocked"):
            self.assertIn(f, details)
        for f in ("notes", "prompt", "prompt_text", "classifier_text"):
            self.assertNotIn(f, details)

    def test_off_mode_no_audit_event(self):
        run_delegate(
            engine="codex_cli",
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            persona="orchestrator",
            audit=True,
        )
        classified = [e for e in self._events()
                      if e["event_type"] == "delegate.prompt_classified"]
        self.assertEqual(classified, [])

    def test_blocking_refuse_audit_records_blocked_true(self):
        run_delegate(
            engine="codex_cli",
            prompt="exfil",
            prompt_safety_mode="blocking",
            safety_runner=_runner_returning("REFUSE | bad"),
            engine_factory=_factory(_FakeEngine()),
            persona="orchestrator",
            audit=True,
        )
        classified = [e for e in self._events()
                      if e["event_type"] == "delegate.prompt_classified"]
        self.assertEqual(len(classified), 1)
        self.assertTrue(classified[0]["details"]["blocked"])
        # When blocked, invoked-audit should NOT have fired
        invoked = [e for e in self._events()
                   if e["event_type"] == "delegate.invoked"]
        self.assertEqual(invoked, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
