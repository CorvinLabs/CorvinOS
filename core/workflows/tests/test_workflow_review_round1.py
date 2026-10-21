"""Regression tests for the workflow adversarial review round 1 (backend findings).

  WF-01 — the CLI never falls back to the canned stub engine silently. The default
          engine is the real Claude engine; `stub` only runs on an explicit
          `--engine stub`, and a stub run is marked as synthetic in the run output
          and in the run.started audit record.
  WF-20 — a spawn refused by the pre-spawn gates is attempted exactly once, and
          `ask_human` (which writes to the outbox) is never retried.

The scheduler-path proof for WF-01 (real subprocess, real engine) lives in
corvin_operator/bridges/shared/test_scheduler.py.

Run directly:  python3 core/workflows/tests/test_workflow_review_round1.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent
sys.path.insert(0, str(_PKG_ROOT))

from corvin_workflows import DAGRunner, StubEngine  # noqa: E402
from corvin_workflows import cli  # noqa: E402
from corvin_workflows.engines_claude import SpawnRefusedError  # noqa: E402
from corvin_workflows.runner import _retry_config  # noqa: E402
from corvin_workflows.storage import load_workflow  # noqa: E402

_EXAMPLE = _PKG_ROOT / "corvin_workflows" / "examples" / "news_sentiment.awp.yaml"

_ONE_AGENT_WF = """\
awp: "1.1.0"
workflow:
  name: wf_review_round1_refused
  description: one agent node with a retry policy
orchestration:
  engine: dag
  graph:
    - id: worker
      type: agent
      agent: worker
      depends_on: []
      retry:
        max_retries: 2
"""


class _RefusingEngine:
    """Stands in for ClaudeCliEngine: every spawn is refused by a gate."""

    name = "refusing"

    def __init__(self) -> None:
        self.calls = 0

    def spawn(self, call):  # noqa: ARG002 — signature mirrors the engines
        self.calls += 1
        raise SpawnRefusedError("spawn refused for agent='worker': test gate")


class WF01StubIsNeverSilentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.mkdtemp(prefix="wf01-cli-")
        self._env_backup = {k: os.environ.get(k) for k in ("PATH", "CORVIN_HOME")}
        os.environ["CORVIN_HOME"] = str(Path(self._td) / "home")
        # Hide any real `claude` binary so the default engine cannot start.
        os.environ["PATH"] = self._td

    def tearDown(self) -> None:
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._td, ignore_errors=True)

    def _run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(argv)
            except SystemExit as e:  # argparse / explicit exits
                rc = int(e.code or 0) if isinstance(e.code, int) else 2
        return rc, out.getvalue(), err.getvalue()

    def test_run_without_engine_does_not_fall_back_to_stub(self) -> None:
        rc, out, _err = self._run_cli(["run", str(_EXAMPLE)])
        # Without `claude` on PATH the real engine must fail loudly, never produce
        # canned Reuters/Bloomberg data with exit 0.
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn("Reuters", out)
        self.assertNotIn("[STUB", out)

    def test_explicit_stub_run_is_marked_synthetic(self) -> None:
        rc, out, _err = self._run_cli(["run", str(_EXAMPLE), "--engine", "stub"])
        self.assertEqual(rc, 0, out)
        self.assertIn("[STUB", out)
        self.assertIn("Synthetic demo data", out)

    def test_explicit_stub_json_payload_flags_engine_and_synthetic(self) -> None:
        rc, out, _err = self._run_cli(
            ["run", str(_EXAMPLE), "--engine", "stub", "--format", "json"]
        )
        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["engine"], "stub")
        self.assertIs(payload["synthetic"], True)

    def test_run_started_audit_names_the_engine(self) -> None:
        doc = load_workflow(_EXAMPLE)
        result = DAGRunner(doc, engine=StubEngine()).run(inputs={})
        started = [e for e in result.audit if e.get("event") == "run.started"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].get("engine"), "stub")


class WF20RetryNeverRepeatsRefusedSpawnTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.mkdtemp(prefix="wf20-")
        self.path = Path(self._td) / "wf.awp.yaml"
        self.path.write_text(_ONE_AGENT_WF, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self._td, ignore_errors=True)

    def test_refused_spawn_is_attempted_exactly_once(self) -> None:
        doc = load_workflow(self.path)
        engine = _RefusingEngine()
        result = DAGRunner(doc, engine=engine).run(inputs={})
        self.assertEqual(result.state, "failed")
        self.assertEqual(engine.calls, 1, "a gate refusal must not be retried")

    def test_ask_human_is_never_retried(self) -> None:
        # ask_human writes to the shared outbox; a retry would send the message twice.
        max_retries, _interval, _strategy = _retry_config(
            {"id": "ask", "type": "ask_human", "retry": {"max_retries": 2}}
        )
        self.assertEqual(max_retries, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
