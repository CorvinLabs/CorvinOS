"""Per-subtask E2E for the Layer 29.5 bwrap sandbox.

Pure-module tests + integration via run_delegate. The bwrap binary
is mocked via monkey-patching ``shutil.which`` so the suite runs
identically on hosts with or without bwrap installed.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_DIR))
_AGENTS_PARENT = _PLUGIN_DIR.parents[1] / "operator" / "bridges" / "shared"
sys.path.insert(0, str(_AGENTS_PARENT))
_FORGE_PKG = _PLUGIN_DIR.parents[1] / "operator" / "forge"
sys.path.insert(0, str(_FORGE_PKG))

from agents import StreamEvent  # type: ignore  # noqa: E402

from corvin_delegate import sandbox  # noqa: E402
from corvin_delegate.delegation import run_delegate  # noqa: E402


# ---------------------------------------------------------------------------
# Pure-module: mode resolution + decision
# ---------------------------------------------------------------------------


class ModeNormalizationTests(unittest.TestCase):
    def test_canonical_modes(self):
        for m in ("off", "advisory", "enforcing"):
            self.assertEqual(sandbox.normalize_mode(m), m)

    def test_case_insensitive(self):
        self.assertEqual(sandbox.normalize_mode("ADVISORY"), "advisory")

    def test_unknown_fails_safe_to_off(self):
        self.assertEqual(sandbox.normalize_mode("nonsense"), "off")

    def test_truthy_synonyms(self):
        for v in ("true", "yes", "on", "1"):
            self.assertEqual(sandbox.normalize_mode(v), "advisory")


class MaxStrictnessTests(unittest.TestCase):
    def test_enforcing_beats_off(self):
        # Critical: LLM-side tool-arg cannot weaken operator-set enforcing
        self.assertEqual(
            sandbox.max_strictness("enforcing", "off"),
            "enforcing",
        )

    def test_off_off_off(self):
        self.assertEqual(sandbox.max_strictness("off", "off"), "off")

    def test_advisory_to_enforcing_widens(self):
        self.assertEqual(
            sandbox.max_strictness("advisory", "enforcing"),
            "enforcing",
        )


class EnvFloorTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("CORVIN_DELEGATE_SANDBOX_FLOOR")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CORVIN_DELEGATE_SANDBOX_FLOOR", None)
        else:
            os.environ["CORVIN_DELEGATE_SANDBOX_FLOOR"] = self._saved

    def test_default_off(self):
        os.environ.pop("CORVIN_DELEGATE_SANDBOX_FLOOR", None)
        self.assertEqual(sandbox.env_floor_mode(), "off")

    def test_advisory_set(self):
        os.environ["CORVIN_DELEGATE_SANDBOX_FLOOR"] = "advisory"
        self.assertEqual(sandbox.env_floor_mode(), "advisory")

    def test_enforcing_set(self):
        os.environ["CORVIN_DELEGATE_SANDBOX_FLOOR"] = "enforcing"
        self.assertEqual(sandbox.env_floor_mode(), "enforcing")


# ---------------------------------------------------------------------------
# bwrap argv composition
# ---------------------------------------------------------------------------


class BuildBwrapArgsTests(unittest.TestCase):
    def test_basic_shape(self):
        args = sandbox.build_bwrap_args(
            engine_id="codex_cli",
            hermetic_dir=Path("/tmp/test"),
        )
        # First positional is the bwrap binary
        self.assertTrue(args[0].endswith("bwrap"))
        # Required namespace flags
        self.assertIn("--unshare-pid", args)
        self.assertIn("--unshare-ipc", args)
        self.assertIn("--unshare-uts", args)
        self.assertIn("--die-with-parent", args)
        # Hermetic dir is bound RW + chdir'd
        self.assertIn("--bind", args)
        self.assertIn("/tmp/test", args)
        self.assertIn("--chdir", args)
        # Default network: shared
        self.assertIn("--share-net", args)

    def test_engine_specific_ro_mounts(self):
        args = sandbox.build_bwrap_args(
            engine_id="claude_code",
            hermetic_dir=Path("/tmp/x"),
        )
        # ~/.claude should appear as ro-bind-try
        joined = " ".join(args)
        self.assertIn(".claude", joined)

    def test_extra_ro_mounts(self):
        args = sandbox.build_bwrap_args(
            engine_id="codex_cli",
            hermetic_dir=Path("/tmp/x"),
            extra_ro_mounts=[Path("/srv/data")],
        )
        self.assertIn("/srv/data", args)

    def test_extra_rw_mounts(self):
        args = sandbox.build_bwrap_args(
            engine_id="codex_cli",
            hermetic_dir=Path("/tmp/x"),
            extra_rw_mounts=[Path("/var/output")],
        )
        # Both --bind for hermetic_dir AND --bind for extra mount
        self.assertEqual(args.count("--bind"), 2)

    def test_unshare_net_when_requested(self):
        args = sandbox.build_bwrap_args(
            engine_id="codex_cli",
            hermetic_dir=Path("/tmp/x"),
            allow_net=False,
        )
        self.assertIn("--unshare-net", args)
        self.assertNotIn("--share-net", args)


# ---------------------------------------------------------------------------
# Decision matrix (mocked bwrap availability)
# ---------------------------------------------------------------------------


class DecideSandboxTests(unittest.TestCase):
    def setUp(self):
        # Save the real is_bwrap_available so we can monkey-patch
        self._real_is_avail = sandbox.is_bwrap_available

    def tearDown(self):
        sandbox.is_bwrap_available = self._real_is_avail  # type: ignore

    def _set_bwrap(self, available: bool):
        sandbox.is_bwrap_available = lambda: available  # type: ignore

    def test_off_skips_regardless(self):
        self._set_bwrap(True)
        self.assertEqual(sandbox.decide_sandbox("off"), "skipped-off")
        self._set_bwrap(False)
        self.assertEqual(sandbox.decide_sandbox("off"), "skipped-off")

    def test_advisory_with_bwrap_uses_it(self):
        self._set_bwrap(True)
        self.assertEqual(sandbox.decide_sandbox("advisory"), "bwrap")

    def test_advisory_without_bwrap_falls_back(self):
        self._set_bwrap(False)
        self.assertEqual(
            sandbox.decide_sandbox("advisory"),
            "fallback-no-bwrap",
        )

    def test_enforcing_with_bwrap_uses_it(self):
        self._set_bwrap(True)
        self.assertEqual(sandbox.decide_sandbox("enforcing"), "bwrap")

    def test_enforcing_without_bwrap_denies(self):
        self._set_bwrap(False)
        self.assertEqual(
            sandbox.decide_sandbox("enforcing"),
            "denied-no-bwrap",
        )


# ---------------------------------------------------------------------------
# Integration with run_delegate
# ---------------------------------------------------------------------------


class _RecordingEngine:
    name = "fake"
    capabilities: dict = {}

    def __init__(self):
        self.spawn_kwargs: dict = {}

    def spawn(self, prompt, **kw):  # type: ignore[no-untyped-def]
        self.spawn_kwargs = dict(kw)
        yield StreamEvent(type="text_delta", text="ok")
        yield StreamEvent(type="turn_completed", usage={})

    def cancel(self):  # pragma: no cover
        pass


def _factory(eng):
    return lambda _eid: eng


class SandboxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._real_is_avail = sandbox.is_bwrap_available
        self._saved_floor = os.environ.get("CORVIN_DELEGATE_SANDBOX_FLOOR")
        os.environ.pop("CORVIN_DELEGATE_SANDBOX_FLOOR", None)
        # Sandboxed audit chain
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="delegate-sandbox-test-")
        self._saved_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp

    def tearDown(self):
        sandbox.is_bwrap_available = self._real_is_avail  # type: ignore
        if self._saved_floor is None:
            os.environ.pop("CORVIN_DELEGATE_SANDBOX_FLOOR", None)
        else:
            os.environ["CORVIN_DELEGATE_SANDBOX_FLOOR"] = self._saved_floor
        if self._saved_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._saved_home
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_default_off_no_argv_prefix(self):
        sandbox.is_bwrap_available = lambda: True  # type: ignore
        eng = _RecordingEngine()
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            engine_factory=_factory(eng),
            audit=False,
        )
        self.assertTrue(result.ok)
        # No argv_prefix passed when sandbox is off
        self.assertNotIn("argv_prefix", eng.spawn_kwargs)

    def test_advisory_with_bwrap_passes_argv_prefix(self):
        sandbox.is_bwrap_available = lambda: True  # type: ignore
        eng = _RecordingEngine()
        run_delegate(
            engine="codex_cli",
            prompt="hi",
            sandbox_mode="advisory",
            engine_factory=_factory(eng),
            audit=False,
        )
        argv_prefix = eng.spawn_kwargs.get("argv_prefix")
        self.assertIsNotNone(argv_prefix)
        self.assertIn("--unshare-pid", argv_prefix)
        self.assertIn("--die-with-parent", argv_prefix)

    def test_advisory_without_bwrap_falls_back_native(self):
        sandbox.is_bwrap_available = lambda: False  # type: ignore
        eng = _RecordingEngine()
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            sandbox_mode="advisory",
            engine_factory=_factory(eng),
            audit=False,
        )
        self.assertTrue(result.ok)
        self.assertNotIn("argv_prefix", eng.spawn_kwargs)

    def test_enforcing_without_bwrap_denies(self):
        sandbox.is_bwrap_available = lambda: False  # type: ignore
        eng = _RecordingEngine()
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            sandbox_mode="enforcing",
            engine_factory=_factory(eng),
            audit=False,
        )
        self.assertFalse(result.ok)
        self.assertIn("sandbox-denied", result.error)
        # Engine never got spawned
        self.assertEqual(eng.spawn_kwargs, {})

    def test_env_floor_beats_weaker_tool_arg(self):
        """SECURITY GATE: env-floor enforcing wins over tool-arg off."""
        os.environ["CORVIN_DELEGATE_SANDBOX_FLOOR"] = "enforcing"
        sandbox.is_bwrap_available = lambda: False  # type: ignore
        eng = _RecordingEngine()
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            sandbox_mode="off",   # LLM tries to disable
            engine_factory=_factory(eng),
            audit=False,
        )
        # env floor enforcing → bwrap missing → deny
        self.assertFalse(result.ok)
        self.assertIn("sandbox-denied", result.error)

    def test_audit_on_bwrap_use(self):
        sandbox.is_bwrap_available = lambda: True  # type: ignore
        eng = _RecordingEngine()
        Path(self._tmp, "global", "forge").mkdir(parents=True, exist_ok=True)
        run_delegate(
            engine="codex_cli",
            prompt="hi",
            sandbox_mode="advisory",
            engine_factory=_factory(eng),
            persona="orchestrator",
            audit=True,
        )
        import json
        events = []
        path = Path(self._tmp, "global", "forge", "audit.jsonl")
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        sandboxed = [e for e in events
                     if e["event_type"] == "delegate.sandboxed"]
        self.assertEqual(len(sandboxed), 1)
        self.assertEqual(sandboxed[0]["details"]["decision"], "bwrap")
        self.assertEqual(sandboxed[0]["details"]["mode"], "advisory")

    def test_audit_on_bwrap_missing(self):
        sandbox.is_bwrap_available = lambda: False  # type: ignore
        eng = _RecordingEngine()
        Path(self._tmp, "global", "forge").mkdir(parents=True, exist_ok=True)
        run_delegate(
            engine="codex_cli",
            prompt="hi",
            sandbox_mode="enforcing",
            engine_factory=_factory(eng),
            persona="orchestrator",
            audit=True,
        )
        import json
        events = []
        path = Path(self._tmp, "global", "forge", "audit.jsonl")
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        unavail = [e for e in events
                   if e["event_type"] == "delegate.sandbox_unavailable"]
        self.assertEqual(len(unavail), 1)
        self.assertEqual(unavail[0]["details"]["mode"], "enforcing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
