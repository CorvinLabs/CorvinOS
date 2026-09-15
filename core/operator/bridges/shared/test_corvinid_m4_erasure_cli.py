"""Unit tests for ADR-0153 M4 — CorvinIDErasureHandler + corvin-id CLI.

Tests:
  1. CorvinIDErasureHandler.purge() → APPLIED when cert exists + deletes it
  2. CorvinIDErasureHandler.purge() → SKIPPED when nothing to delete
  3. corvin-id show prints instance_id (mocked instance_identity)
  4. corvin-id resolve looks up identity_registry.json and prints result
  5. corvin-id resolve emits identity.resolution_requested CRITICAL event

All tests use temporary directories for isolation.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# Path bootstrap — use the main repo's shared dir (M4 files merged to main).
# ---------------------------------------------------------------------------

_MAIN_SHARED = Path(__file__).resolve().parent
_M4_SHARED = _MAIN_SHARED  # M4 implementation is now in main repo
if str(_MAIN_SHARED) not in sys.path:
    sys.path.insert(0, str(_MAIN_SHARED))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_corvin_home(tmpdir: str) -> Path:
    """Create a minimal <corvin_home>/global/ tree in *tmpdir*."""
    home = Path(tmpdir) / "corvin_home"
    (home / "global").mkdir(parents=True)
    return home


# ---------------------------------------------------------------------------
# 1 + 2 — CorvinIDErasureHandler.purge()
# ---------------------------------------------------------------------------

class TestCorvinIDErasureHandlerPurge(unittest.TestCase):

    def _import_handler(self):
        # Fresh import each time so CORVIN_HOME env is picked up.
        if "erasure_handler_corvinid" in sys.modules:
            del sys.modules["erasure_handler_corvinid"]
        import erasure_handler_corvinid as _m
        return _m.CorvinIDErasureHandler

    def test_purge_applied_when_cert_exists(self):
        """APPLIED + cert deleted when instance_cert.jwt is present."""
        with tempfile.TemporaryDirectory() as td:
            home = _make_corvin_home(td)
            cert_path = home / "global" / "instance_cert.jwt"
            cert_path.write_text("fake-jwt-token", encoding="utf-8")

            Handler = self._import_handler()
            handler = Handler(corvin_home=home, revoke_remote=False)
            result = handler.purge(subject_id="user-1234", request_id="req-test-001")

            from erasure_orchestrator import LayerStatus
            self.assertEqual(result.status, LayerStatus.APPLIED)
            self.assertGreaterEqual(result.count, 1)
            self.assertFalse(cert_path.exists(), "cert file must be deleted")

    def test_purge_applied_removes_registry_entry(self):
        """APPLIED + registry entry removed when subject_id is in identity_registry.json."""
        with tempfile.TemporaryDirectory() as td:
            home = _make_corvin_home(td)
            registry_path = home / "global" / "identity_registry.json"
            registry_data = {
                "user-1234": {"email": "test@example.com", "name": "Test", "registered_at": "2026-01-01T00:00:00+00:00"},
                "other-user": {"email": "other@example.com", "name": "Other", "registered_at": "2026-01-01T00:00:00+00:00"},
            }
            registry_path.write_text(json.dumps(registry_data), encoding="utf-8")

            Handler = self._import_handler()
            handler = Handler(corvin_home=home, revoke_remote=False)
            result = handler.purge(subject_id="user-1234", request_id="req-test-002")

            from erasure_orchestrator import LayerStatus
            self.assertEqual(result.status, LayerStatus.APPLIED)
            remaining = json.loads(registry_path.read_text("utf-8"))
            self.assertNotIn("user-1234", remaining)
            self.assertIn("other-user", remaining)

    def test_purge_skipped_when_nothing_to_delete(self):
        """SKIPPED when neither cert nor registry entry exists for subject."""
        with tempfile.TemporaryDirectory() as td:
            home = _make_corvin_home(td)
            # No cert file, no registry

            Handler = self._import_handler()
            handler = Handler(corvin_home=home, revoke_remote=False)
            result = handler.purge(subject_id="ghost-user", request_id="req-test-003")

            from erasure_orchestrator import LayerStatus
            self.assertEqual(result.status, LayerStatus.SKIPPED)
            self.assertEqual(result.count, 0)

    def test_purge_skipped_when_registry_key_absent(self):
        """SKIPPED when registry exists but does not contain the subject_id."""
        with tempfile.TemporaryDirectory() as td:
            home = _make_corvin_home(td)
            registry_path = home / "global" / "identity_registry.json"
            registry_path.write_text(
                json.dumps({"other-user": {"email": "x@y.com", "name": "X", "registered_at": "2026-01-01T00:00:00+00:00"}}),
                encoding="utf-8",
            )

            Handler = self._import_handler()
            handler = Handler(corvin_home=home, revoke_remote=False)
            result = handler.purge(subject_id="ghost-user", request_id="req-test-004")

            from erasure_orchestrator import LayerStatus
            self.assertEqual(result.status, LayerStatus.SKIPPED)

    def test_layer_id(self):
        """layer_id must be 'L153-corvinid'."""
        Handler = self._import_handler()
        handler = Handler(corvin_home=Path("/tmp"), revoke_remote=False)
        self.assertEqual(handler.layer_id, "L153-corvinid")

    def test_no_anthropic_import(self):
        """Module must not import anthropic (CI AST lint contract)."""
        import ast
        src = (_M4_SHARED / "erasure_handler_corvinid.py").read_text("utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    self.assertFalse(
                        name.startswith("anthropic"),
                        f"erasure_handler_corvinid.py must not import anthropic, found: {name}",
                    )


# ---------------------------------------------------------------------------
# 3 — corvin-id show prints instance_id
# ---------------------------------------------------------------------------

class TestCorvinIdShowCommand(unittest.TestCase):

    def _import_cli(self):
        if "corvin_id_cli" in sys.modules:
            del sys.modules["corvin_id_cli"]
        import corvin_id_cli as _m
        return _m

    def _make_fake_instance_identity(self, instance_id: str) -> types.ModuleType:
        """Build a minimal fake instance_identity module."""
        mod = types.ModuleType("instance_identity")

        class _IBCError(Exception):
            pass

        mod.IBCError = _IBCError
        mod.get_instance_id = lambda: instance_id
        mod.get_instance_pubkey_b64 = MagicMock(side_effect=_IBCError("no key"))
        mod.instance_cert_path = lambda: Path("/nonexistent/instance_cert.jwt")
        mod.get_ibc = lambda: None
        mod.bind_instance = MagicMock()
        mod.ensure_instance_key = MagicMock()
        mod.instance_key_path = lambda: Path("/nonexistent/instance_key.pem")
        mod.rotate = MagicMock()
        return mod

    def test_show_prints_instance_id(self):
        """corvin-id show must include the instance_id in its output."""
        fake_iid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        cli = self._import_cli()

        fake_mod = self._make_fake_instance_identity(fake_iid)
        with patch.dict(sys.modules, {"instance_identity": fake_mod}):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                rc = cli.main(["show"])

        output = captured.getvalue()
        self.assertIn(fake_iid, output, "show must print the instance_id")

    def test_show_returns_zero(self):
        """corvin-id show returns exit code 0 when instance_id is readable."""
        fake_iid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        cli = self._import_cli()

        fake_mod = self._make_fake_instance_identity(fake_iid)
        with patch.dict(sys.modules, {"instance_identity": fake_mod}):
            with patch("sys.stdout", io.StringIO()):
                rc = cli.main(["show"])
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# 4 + 5 — corvin-id resolve
# ---------------------------------------------------------------------------

class TestCorvinIdResolveCommand(unittest.TestCase):

    def _import_cli(self):
        if "corvin_id_cli" in sys.modules:
            del sys.modules["corvin_id_cli"]
        import corvin_id_cli as _m
        return _m

    def _write_registry(self, home: Path, data: dict) -> Path:
        registry_path = home / "global" / "identity_registry.json"
        registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return registry_path

    def test_resolve_prints_email_and_name(self):
        """resolve looks up the registry and prints email + name."""
        with tempfile.TemporaryDirectory() as td:
            home = _make_corvin_home(td)
            target_id = "deadbeef-0000-1111-2222-333344445555"
            self._write_registry(home, {
                target_id: {
                    "email": "operator@example.com",
                    "name": "Test Operator",
                    "registered_at": "2026-01-01T00:00:00+00:00",
                }
            })

            cli = self._import_cli()
            captured = io.StringIO()
            # Patch CORVIN_HOME so the CLI reads from our temp dir.
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}):
                # Suppress audit (not wired in test environment).
                with patch("corvin_id_cli._emit_resolution_audit"):
                    with patch("sys.stdout", captured):
                        rc = cli.main(["resolve", target_id])

            output = captured.getvalue()
            self.assertEqual(rc, 0, f"resolve should return 0, got {rc}. Output: {output}")
            self.assertIn("operator@example.com", output)
            self.assertIn("Test Operator", output)
            self.assertIn(target_id, output)

    def test_resolve_returns_1_when_not_found(self):
        """resolve returns exit code 1 when instance_id is absent from registry."""
        with tempfile.TemporaryDirectory() as td:
            home = _make_corvin_home(td)
            self._write_registry(home, {"other-id": {"email": "x@y.com", "name": "X", "registered_at": "2026-01-01T00:00:00+00:00"}})

            cli = self._import_cli()
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}):
                with patch("corvin_id_cli._emit_resolution_audit"):
                    with patch("sys.stdout", io.StringIO()):
                        rc = cli.main(["resolve", "missing-id-0000-0000-0000"])

            self.assertEqual(rc, 1)

    def test_resolve_emits_critical_audit_event(self):
        """resolve ALWAYS emits identity.resolution_requested CRITICAL before returning data."""
        with tempfile.TemporaryDirectory() as td:
            home = _make_corvin_home(td)
            target_id = "cafebabe-dead-beef-0000-111122223333"
            self._write_registry(home, {
                target_id: {"email": "sec@example.com", "name": "SecOps", "registered_at": "2026-01-01T00:00:00+00:00"},
            })

            cli = self._import_cli()
            audit_calls = []

            def _fake_audit(event_type, severity, details):
                audit_calls.append((event_type, severity, details))

            # Replace the real audit_event inside the CLI's namespace.
            fake_audit_mod = types.ModuleType("audit")
            fake_audit_mod.audit_event = _fake_audit

            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}):
                with patch.dict(sys.modules, {"audit": fake_audit_mod}):
                    # Force re-import so the patched audit module is picked up.
                    if "corvin_id_cli" in sys.modules:
                        del sys.modules["corvin_id_cli"]
                    import corvin_id_cli as cli2
                    with patch("sys.stdout", io.StringIO()):
                        rc = cli2.main(["resolve", target_id])

            self.assertEqual(rc, 0)
            # There must be exactly one audit call for resolution.
            self.assertEqual(len(audit_calls), 1, f"Expected 1 audit call, got: {audit_calls}")
            event_type, severity, details = audit_calls[0]
            self.assertEqual(event_type, "identity.resolution_requested")
            self.assertEqual(severity, "CRITICAL")
            # Details must include a prefix, not the full id (privacy).
            self.assertIn("target_instance_id_prefix", details)
            self.assertEqual(details["target_instance_id_prefix"], target_id[:8])

    def test_resolve_audit_emitted_before_data_returned(self):
        """Audit event must fire before any registry data is read/returned.

        We verify ordering by making the audit call record a timestamp,
        and confirming it is the first side-effect seen.
        """
        with tempfile.TemporaryDirectory() as td:
            home = _make_corvin_home(td)
            target_id = "11112222-3333-4444-5555-666677778888"
            self._write_registry(home, {
                target_id: {"email": "a@b.com", "name": "A", "registered_at": "2026-01-01T00:00:00+00:00"},
            })

            call_order: list[str] = []

            def _fake_audit(event_type, severity, details):
                call_order.append("audit")

            cli = self._import_cli()
            fake_audit_mod = types.ModuleType("audit")
            fake_audit_mod.audit_event = _fake_audit

            original_open = open

            def _tracking_open(path, *a, **kw):
                call_order.append("file_open")
                return original_open(path, *a, **kw)

            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}):
                with patch.dict(sys.modules, {"audit": fake_audit_mod}):
                    if "corvin_id_cli" in sys.modules:
                        del sys.modules["corvin_id_cli"]
                    import corvin_id_cli as cli3
                    # Patch builtins.open inside the CLI module to track file opens.
                    with patch("corvin_id_cli.open", side_effect=_tracking_open, create=True):
                        with patch("sys.stdout", io.StringIO()):
                            cli3.main(["resolve", target_id])

            # Audit must appear before the registry file_open.
            self.assertIn("audit", call_order)
            if "file_open" in call_order:
                self.assertLess(
                    call_order.index("audit"),
                    call_order.index("file_open"),
                    "audit event must fire before the registry file is opened",
                )

    def test_resolve_warns_to_stderr_when_audit_unavailable(self):
        """resolve prints a WARNING to stderr if audit chain is unavailable but still shows data."""
        with tempfile.TemporaryDirectory() as td:
            home = _make_corvin_home(td)
            target_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            self._write_registry(home, {
                target_id: {"email": "warn@example.com", "name": "WarnUser", "registered_at": "2026-01-01T00:00:00+00:00"},
            })

            cli = self._import_cli()
            # Make audit module raise so the CLI falls back to stderr warning.
            fake_audit_mod = types.ModuleType("audit")

            def _raise(*a, **kw):
                raise RuntimeError("audit unavailable in test")

            fake_audit_mod.audit_event = _raise

            captured_err = io.StringIO()
            captured_out = io.StringIO()
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}):
                with patch.dict(sys.modules, {"audit": fake_audit_mod}):
                    if "corvin_id_cli" in sys.modules:
                        del sys.modules["corvin_id_cli"]
                    import corvin_id_cli as cli4
                    with patch("sys.stdout", captured_out):
                        with patch("sys.stderr", captured_err):
                            rc = cli4.main(["resolve", target_id])

            # Should still succeed (best-effort audit).
            self.assertEqual(rc, 0)
            # WARNING must appear on stderr.
            self.assertIn("WARNING", captured_err.getvalue())
            # Data must still appear on stdout.
            self.assertIn("warn@example.com", captured_out.getvalue())


# ---------------------------------------------------------------------------
# 6 — No anthropic import in CLI module
# ---------------------------------------------------------------------------

class TestCorvinIdCliNoAnthropicImport(unittest.TestCase):

    def test_no_anthropic_import(self):
        """corvin_id_cli.py must not import anthropic (CI AST lint contract)."""
        import ast
        src = (_M4_SHARED / "corvin_id_cli.py").read_text("utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    self.assertFalse(
                        name.startswith("anthropic"),
                        f"corvin_id_cli.py must not import anthropic, found: {name}",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
