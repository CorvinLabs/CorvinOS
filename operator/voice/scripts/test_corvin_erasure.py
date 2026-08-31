"""Unit tests for corvin_erasure.py (M4.5)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "voice" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))

import corvin_erasure  # noqa: E402


class _Sandbox:
    """Build a fresh corvin_home + tenant tree per test."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="erasure-cli-")
        self.root = Path(self.tmp.name)
        self.tenant_id = "_default"
        (self.root / "tenants" / self.tenant_id / "global" / "forge").mkdir(parents=True)
        # Reset registration
        self._original_handlers = list(corvin_erasure._REGISTERED_HANDLERS)
        corvin_erasure._REGISTERED_HANDLERS.clear()
        return self

    def __exit__(self, *exc):
        corvin_erasure._REGISTERED_HANDLERS.clear()
        corvin_erasure._REGISTERED_HANDLERS.extend(self._original_handlers)
        self.tmp.cleanup()


class TestRunBasics(unittest.TestCase):

    def test_run_with_stub_chain_completes(self):
        with _Sandbox() as sb:
            with redirect_stdout(io.StringIO()) as out:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "run", "user_42",
                    "--requester", "dpo@example.com",
                ])
            self.assertEqual(rc, 0, f"output={out.getvalue()}")
            self.assertIn("overall_status  : completed", out.getvalue())
            self.assertIn("L28-recall", out.getvalue())

    def test_run_json_format(self):
        with _Sandbox() as sb:
            with redirect_stdout(io.StringIO()) as out:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "--format", "json",
                    "run", "user_42",
                    "--requester", "dpo",
                ])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["overall_status"], "completed")
            self.assertEqual(data["request"]["subject_id"], "user_42")
            self.assertGreaterEqual(len(data["per_layer"]), 9,
                                    "real_handler_chain must register all layers")

    def test_dry_run_does_not_invoke_handlers(self):
        with _Sandbox() as sb:
            with redirect_stdout(io.StringIO()) as out:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "run", "user_42",
                    "--requester", "dpo",
                    "--dry-run",
                ])
            self.assertEqual(rc, 0)
            # Dry-run output contains the registered layer ids
            self.assertIn("L28-recall", out.getvalue())
            self.assertIn("dry_run", out.getvalue())
            # No trail file should exist
            trail_dir = sb.root / "tenants" / sb.tenant_id / "global" / "erasure"
            self.assertFalse(trail_dir.exists(),
                             "dry-run wrote trail unexpectedly")

    def test_run_rejects_pii_subject_id(self):
        with _Sandbox() as sb:
            with redirect_stderr(io.StringIO()) as err:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "run", "alice@example.com",  # PII shape
                    "--requester", "dpo",
                ])
            self.assertEqual(rc, 2)
            self.assertIn("subject_id", err.getvalue())

    def test_run_requires_requester(self):
        with _Sandbox() as sb:
            with redirect_stderr(io.StringIO()), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    corvin_erasure.main([
                        "--home", str(sb.root),
                        "run", "user_42",
                    ])

    def test_missing_home_returns_2(self):
        with redirect_stderr(io.StringIO()) as err:
            rc = corvin_erasure.main([
                "--home", "/tmp/totally-nonexistent-erasure-test-xyz",
                "run", "user_42",
                "--requester", "dpo",
            ])
        self.assertEqual(rc, 2)
        self.assertIn("not found", err.getvalue())


class TestRegisterHandler(unittest.TestCase):

    def test_registered_handler_takes_precedence_over_stub(self):
        from erasure_orchestrator import ErasureLayerResult, LayerStatus

        class _RealL28:
            layer_id = "L28-recall"

            def purge(self, subject_id, request_id):
                return ErasureLayerResult(
                    layer_id=self.layer_id,
                    status=LayerStatus.APPLIED,
                    count=5,
                    reason="real handler invoked",
                )

        with _Sandbox() as sb:
            corvin_erasure.register_handler(_RealL28())
            with redirect_stdout(io.StringIO()) as out:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "--format", "json",
                    "run", "user_42",
                    "--requester", "dpo",
                ])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            # L28-recall now reports APPLIED with count 5
            l28 = next(r for r in data["per_layer"]
                       if r["layer_id"] == "L28-recall")
            self.assertEqual(l28["status"], "applied")
            self.assertEqual(l28["count"], 5)
            # Other layers fall back to stubs (skipped)
            l33 = next(r for r in data["per_layer"]
                       if r["layer_id"] == "L33-artifacts")
            self.assertEqual(l33["status"], "skipped")


class TestListAndShow(unittest.TestCase):

    def test_list_empty(self):
        with _Sandbox() as sb:
            with redirect_stdout(io.StringIO()) as out:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "list",
                ])
            self.assertEqual(rc, 0)
            # Either "no trail directory" (never ran) or "no past erasure
            # requests" (ran but trail dir was empty afterward).
            output = out.getvalue()
            self.assertTrue(
                "no trail directory" in output
                or "no past erasure requests" in output,
                f"unexpected output: {output!r}",
            )

    def test_list_after_run(self):
        with _Sandbox() as sb:
            with redirect_stdout(io.StringIO()):
                corvin_erasure.main([
                    "--home", str(sb.root),
                    "run", "user_42",
                    "--requester", "dpo",
                ])
            with redirect_stdout(io.StringIO()) as out:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "list",
                ])
            self.assertEqual(rc, 0)
            self.assertIn("user_42", out.getvalue())
            self.assertIn("completed", out.getvalue())

    def test_show_existing(self):
        with _Sandbox() as sb:
            # Run an erasure to produce a trail file
            with redirect_stdout(io.StringIO()) as run_out:
                corvin_erasure.main([
                    "--home", str(sb.root),
                    "--format", "json",
                    "run", "user_42",
                    "--requester", "dpo",
                ])
            request_id = json.loads(run_out.getvalue())["request"]["request_id"]

            with redirect_stdout(io.StringIO()) as out:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "show", request_id,
                ])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["request"]["subject_id"], "user_42")

    def test_show_missing_returns_2(self):
        with _Sandbox() as sb:
            with redirect_stderr(io.StringIO()) as err:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "show", "er-nonexistent",
                ])
            self.assertEqual(rc, 2)
            self.assertIn("no trail file", err.getvalue())


class TestExitCodes(unittest.TestCase):
    """V-014: exit codes — COMPLETED→0, PARTIAL→2, FAILED→3."""

    def _make_failing_handler(self, layer_id: str):
        """Return a handler that always raises."""
        from erasure_orchestrator import ErasureLayerResult, LayerStatus

        class _FailHandler:
            pass
        _FailHandler.layer_id = layer_id

        def _purge(self, subject_id, request_id):
            raise RuntimeError("simulated handler failure")

        import types
        _FailHandler.purge = _purge
        return _FailHandler()

    def test_completed_exits_0(self):
        """Stub chain (all SKIPPED) → COMPLETED → exit 0."""
        with _Sandbox() as sb:
            with redirect_stdout(io.StringIO()):
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "run", "user_42",
                    "--requester", "dpo@example.com",
                    "--use-stubs",
                ])
        self.assertEqual(rc, 0)

    def test_partial_exits_2(self):
        """One FAILED handler with others SKIPPED → PARTIAL → exit 2."""
        from erasure_orchestrator import ErasureLayerResult, LayerStatus

        class _FailL28:
            layer_id = "L28-recall"

            def purge(self, subject_id, request_id):
                raise RuntimeError("simulated L28 failure")

        with _Sandbox() as sb:
            corvin_erasure.register_handler(_FailL28())
            with redirect_stdout(io.StringIO()), \
                 redirect_stderr(io.StringIO()) as err:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "run", "user_42",
                    "--requester", "dpo@example.com",
                    "--use-stubs",
                ])
        self.assertEqual(rc, 2, f"expected exit 2 for PARTIAL, got {rc}")
        self.assertIn("PARTIAL", err.getvalue())

    def test_failed_exits_3(self):
        """All handlers FAILED → overall FAILED → exit 3."""
        from erasure_orchestrator import ErasureLayerResult, LayerStatus

        class _FailAll:
            def __init__(self, lid):
                self.layer_id = lid

            def purge(self, subject_id, request_id):
                raise RuntimeError("simulated total failure")

        with _Sandbox() as sb:
            # Register failing handlers for ALL built-in stub layers, derived
            # from builtin_stub_chain() itself — a hardcoded id list here has
            # drifted twice already (L38-a2a, then L153-corvinid/L163-ulo):
            # any layer the chain gains that is not forced to fail turns the
            # expected FAILED (3) into PARTIAL (2).
            from erasure_orchestrator import builtin_stub_chain

            for handler in builtin_stub_chain():
                corvin_erasure.register_handler(_FailAll(handler.layer_id))
            with redirect_stdout(io.StringIO()), \
                 redirect_stderr(io.StringIO()) as err:
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "run", "user_42",
                    "--requester", "dpo@example.com",
                    "--use-stubs",
                ])
        self.assertEqual(rc, 3, f"expected exit 3 for FAILED, got {rc}")
        self.assertIn("FAILED", err.getvalue())

    def test_partial_stderr_contains_trail_path(self):
        """PARTIAL stderr message mentions the trail file path."""
        from erasure_orchestrator import ErasureLayerResult, LayerStatus

        class _FailL28:
            layer_id = "L28-recall"

            def purge(self, subject_id, request_id):
                raise RuntimeError("forced partial")

        with _Sandbox() as sb:
            corvin_erasure.register_handler(_FailL28())
            with redirect_stdout(io.StringIO()), \
                 redirect_stderr(io.StringIO()) as err:
                corvin_erasure.main([
                    "--home", str(sb.root),
                    "run", "user_42",
                    "--requester", "dpo@example.com",
                    "--use-stubs",
                ])
        stderr = err.getvalue()
        self.assertIn("erasure", stderr.lower())
        # trail path contains the erasure dir
        self.assertIn("erasure", stderr)


class TestNotesNotInAudit(unittest.TestCase):
    """End-to-end: notes preserved on trail, NOT in audit allow-list."""

    def test_notes_landed_only_in_trail(self):
        with _Sandbox() as sb:
            with redirect_stdout(io.StringIO()):
                rc = corvin_erasure.main([
                    "--home", str(sb.root),
                    "--format", "json",
                    "run", "user_42",
                    "--requester", "dpo",
                    "--notes", "sensitive context that should NOT leak",
                ])
            self.assertEqual(rc, 0)

            # Trail file carries the notes
            trail_dir = sb.root / "tenants" / sb.tenant_id / "global" / "erasure"
            trails = list(trail_dir.glob("er-*.json"))
            self.assertEqual(len(trails), 1)
            trail_data = json.loads(trails[0].read_text())
            self.assertEqual(
                trail_data["request"]["notes"],
                "sensitive context that should NOT leak",
            )

            # Audit chain (if exists) must NOT carry notes
            audit = sb.root / "tenants" / sb.tenant_id / "global" / "forge" / "audit.jsonl"
            if audit.exists():
                for line in audit.read_text().splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    self.assertNotIn("notes", rec.get("details", {}),
                                     "notes smuggled into audit event")


if __name__ == "__main__":
    unittest.main()
