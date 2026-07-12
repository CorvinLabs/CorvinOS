"""Layer-29.5 per-subtask E2E — verify every helper-site spawns
``claude`` with ``--model claude-haiku-4-5-20251001`` (or the operator
override) by stubbing ``claude`` on PATH with a Python script that
echoes its argv as JSON.

Eight sites covered (the seven curated SITE_* constants plus the
appendix-CLI inside summarize.py which inherits the voice-summary
default):

1. summarize._summarize_via_cli            → SITE_VOICE_SUMMARY
2. summarize._appendix_via_cli             → SITE_VOICE_SUMMARY (shared)
3. dialectic._run_cli_judge                → SITE_DIALECTIC_CLI
4. dialectic._run_summary_judge            → SITE_DIALECTIC_SUMMARY_JUDGE
5. user_style._default_judge               → SITE_USER_STYLE_JUDGE
6. user_model._default_judge               → SITE_USER_MODEL_DISTILL
7. corvin_delegate.output_judge runner    → SITE_DELEGATE_OUTPUT_JUDGE
8. router._call_cli                        → SITE_ROUTER_CLI (DEFAULT_MODEL)
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

THIS = Path(__file__).resolve()
SHARED = THIS.parent
PLUGIN_ROOT = SHARED.parent.parent / "voice"  # operator/voice
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))


def _make_stub_claude(target_dir: Path, *, echo_json: bool = True) -> Path:
    """Drop a stub ``claude`` executable that prints its argv as JSON
    on stdout and exits 0. Returns the path."""
    stub = target_dir / "claude"
    if echo_json:
        body = (
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "sys.stdout.write(json.dumps({'argv': sys.argv[1:]}))\n"
        )
    else:
        body = "#!/bin/sh\necho stub\n"
    stub.write_text(body)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _argv_from_run(cwd: Path, env_overlay: dict[str, str] | None = None,
                   *, stdin: str | None = None,
                   cmd: list[str] | None = None) -> list[str]:
    """Run the stub via the same PATH the helper sees, capture argv."""
    env = os.environ.copy()
    env["PATH"] = f"{cwd}:{env.get('PATH', '')}"
    if env_overlay:
        env.update(env_overlay)
    p = subprocess.run(
        cmd or ["echo", "skip"],
        capture_output=True, text=True, env=env, cwd=cwd,
    )
    return json.loads(p.stdout)["argv"] if p.stdout.strip().startswith("{") else []


class _StubPathFixture:
    """Mixin: drop a stub claude, override PATH, restore on tearDown."""

    def _setup_stub(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="helper-site-e2e-")
        self._stub_dir = Path(self._tmp)
        _make_stub_claude(self._stub_dir)
        self._orig_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self._stub_dir}:{self._orig_path}"
        self._env_snapshot = {k: os.environ.pop(k) for k in list(os.environ)
                              if k.startswith("CORVIN_HELPER_MODEL")}
        # The summarize/dialectic sites gate on ``_claude_authenticated()`` and
        # short-circuit BEFORE spawning ``claude`` when no OAuth session /
        # ANTHROPIC_API_KEY exists (the intended fresh-install fast-fail). This
        # E2E asserts the *argv* the site would build, so we must satisfy that
        # precondition explicitly — otherwise the run never happens on a CI box
        # with no credentials and the capture stays empty (KeyError: 'args').
        self._orig_api_key = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "stub-key-for-argv-capture"

    def _teardown_stub(self) -> None:
        os.environ["PATH"] = self._orig_path
        for k in list(os.environ):
            if k.startswith("CORVIN_HELPER_MODEL"):
                del os.environ[k]
        os.environ.update(self._env_snapshot)
        if self._orig_api_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._orig_api_key
        shutil.rmtree(self._tmp, ignore_errors=True)


HAIKU = "claude-haiku-4-5-20251001"


class SummarizeViaCliTests(_StubPathFixture, unittest.TestCase):

    def setUp(self) -> None:
        self._setup_stub()
        import summarize  # type: ignore
        self.summarize = summarize

    def tearDown(self) -> None:
        self._teardown_stub()

    def test_summarize_passes_haiku_model(self) -> None:
        # Capture subprocess.run args
        captured: dict = {}
        orig_run = subprocess.run

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return orig_run(args, **kwargs)

        with patch.object(self.summarize.subprocess, "run", side_effect=fake_run):
            self.summarize._summarize_via_cli(
                text="hello world", task="", lang="de",
                target_chars=200, model=HAIKU,
            )
        self.assertIn("--model", captured["args"])
        idx = captured["args"].index("--model")
        self.assertEqual(captured["args"][idx + 1], HAIKU)

    def test_appendix_via_cli_uses_haiku_default(self) -> None:
        captured: dict = {}
        orig_run = subprocess.run

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return orig_run(args, **kwargs)

        with patch.object(self.summarize.subprocess, "run", side_effect=fake_run):
            # generate_appendix default arg is HAIKU now
            self.summarize.generate_appendix("Some text to explain.", "de")
        self.assertIn("--model", captured["args"])
        idx = captured["args"].index("--model")
        self.assertEqual(captured["args"][idx + 1], HAIKU)


class DialecticCliJudgeTests(_StubPathFixture, unittest.TestCase):

    def setUp(self) -> None:
        self._setup_stub()
        import dialectic  # type: ignore
        self.dialectic = dialectic

    def tearDown(self) -> None:
        self._teardown_stub()

    def test_run_cli_judge_injects_haiku_model(self) -> None:
        captured: dict = {}
        orig_run = subprocess.run

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return orig_run(args, **kwargs)

        with patch.object(self.dialectic.subprocess, "run", side_effect=fake_run):
            self.dialectic._run_cli_judge(
                site="forge_creation", thesis="ok", antithesis="no",
            )
        self.assertIn("--model", captured["args"])
        self.assertEqual(
            captured["args"][captured["args"].index("--model") + 1], HAIKU,
        )

    def test_summary_judge_injects_haiku_model(self) -> None:
        captured: dict = {}
        orig_run = subprocess.run

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return orig_run(args, **kwargs)

        with patch.object(self.dialectic.subprocess, "run", side_effect=fake_run):
            self.dialectic._run_summary_judge("source text", "candidate", "de")
        self.assertIn("--model", captured["args"])
        self.assertEqual(
            captured["args"][captured["args"].index("--model") + 1], HAIKU,
        )

    def test_per_site_override_takes_precedence(self) -> None:
        os.environ["CORVIN_HELPER_MODEL_DIALECTIC_CLI"] = "claude-opus-4-7"
        captured: dict = {}
        orig_run = subprocess.run

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return orig_run(args, **kwargs)

        with patch.object(self.dialectic.subprocess, "run", side_effect=fake_run):
            self.dialectic._run_cli_judge(
                site="forge_creation", thesis="ok", antithesis="no",
            )
        idx = captured["args"].index("--model")
        self.assertEqual(captured["args"][idx + 1], "claude-opus-4-7")

    def test_opt_out_drops_model_flag(self) -> None:
        os.environ["CORVIN_HELPER_MODEL_DIALECTIC_CLI"] = "none"
        captured: dict = {}
        orig_run = subprocess.run

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return orig_run(args, **kwargs)

        with patch.object(self.dialectic.subprocess, "run", side_effect=fake_run):
            self.dialectic._run_cli_judge(
                site="forge_creation", thesis="ok", antithesis="no",
            )
        self.assertNotIn("--model", captured["args"])


class UserStyleJudgeTests(_StubPathFixture, unittest.TestCase):

    def setUp(self) -> None:
        self._setup_stub()
        import user_style  # type: ignore
        self.user_style = user_style

    def tearDown(self) -> None:
        self._teardown_stub()

    def test_default_judge_injects_haiku(self) -> None:
        from user_style import Cluster, SignalCounts  # type: ignore
        cluster = Cluster(
            cluster_id="c1",
            skill_name="example",
            counts=SignalCounts(approval=0, rejection=2, rephrase=1),
        )
        captured: dict = {}
        orig_run = subprocess.run

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return orig_run(args, **kwargs)

        with patch.object(self.user_style.subprocess, "run", side_effect=fake_run):
            self.user_style._default_judge(cluster, "test rule")
        self.assertIn("--model", captured["args"])
        self.assertEqual(
            captured["args"][captured["args"].index("--model") + 1], HAIKU,
        )


class UserModelDistillTests(_StubPathFixture, unittest.TestCase):

    def setUp(self) -> None:
        self._setup_stub()
        import user_model  # type: ignore
        self.user_model = user_model

    def tearDown(self) -> None:
        self._teardown_stub()

    def test_default_judge_injects_haiku(self) -> None:
        captured: dict = {}

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

        with patch.object(self.user_model.subprocess, "run", side_effect=fake_run):
            self.user_model._default_judge("test prompt", 5.0, "claude")
        self.assertIn("--model", captured["args"])
        self.assertEqual(
            captured["args"][captured["args"].index("--model") + 1], HAIKU,
        )

    def test_per_site_override(self) -> None:
        os.environ["CORVIN_HELPER_MODEL_USER_MODEL_DISTILL"] = "claude-sonnet-4-6"
        captured: dict = {}

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

        with patch.object(self.user_model.subprocess, "run", side_effect=fake_run):
            self.user_model._default_judge("test prompt", 5.0, "claude")
        idx = captured["args"].index("--model")
        self.assertEqual(captured["args"][idx + 1], "claude-sonnet-4-6")


class DelegateOutputJudgeTests(_StubPathFixture, unittest.TestCase):

    def setUp(self) -> None:
        self._setup_stub()
        sys.path.insert(0,
            str(PLUGIN_ROOT.parent.parent / "core" / "delegate"))
        from corvin_delegate import output_judge  # type: ignore
        self.output_judge = output_judge

    def tearDown(self) -> None:
        self._teardown_stub()

    def test_real_runner_injects_haiku(self) -> None:
        captured: dict = {}
        orig_run = subprocess.run

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return orig_run(args, **kwargs)

        with patch.object(self.output_judge.subprocess, "run", side_effect=fake_run):
            self.output_judge._real_judge_runner("prompt", 5.0)
        self.assertIn("--model", captured["args"])
        self.assertEqual(
            captured["args"][captured["args"].index("--model") + 1], HAIKU,
        )

    def test_per_site_override(self) -> None:
        os.environ["CORVIN_HELPER_MODEL_DELEGATE_OUTPUT_JUDGE"] = "claude-sonnet-4-6"
        captured: dict = {}
        orig_run = subprocess.run

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return orig_run(args, **kwargs)

        with patch.object(self.output_judge.subprocess, "run", side_effect=fake_run):
            self.output_judge._real_judge_runner("prompt", 5.0)
        idx = captured["args"].index("--model")
        self.assertEqual(captured["args"][idx + 1], "claude-sonnet-4-6")

    def test_opt_out_drops_model_flag(self) -> None:
        os.environ["CORVIN_HELPER_MODEL_DELEGATE_OUTPUT_JUDGE"] = "none"
        captured: dict = {}
        orig_run = subprocess.run

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return orig_run(args, **kwargs)

        with patch.object(self.output_judge.subprocess, "run", side_effect=fake_run):
            self.output_judge._real_judge_runner("prompt", 5.0)
        self.assertNotIn("--model", captured["args"])


class RouterDefaultModelTests(unittest.TestCase):
    """Router's DEFAULT_MODEL is module-level — it picks up the
    Haiku default at import time. This test verifies the resolver
    indirection actually fires."""

    def test_default_model_is_haiku(self) -> None:
        # Re-import in a clean environment to exercise the resolver
        import importlib
        import router  # type: ignore
        importlib.reload(router)
        self.assertEqual(router.DEFAULT_MODEL, HAIKU)


if __name__ == "__main__":
    unittest.main()
