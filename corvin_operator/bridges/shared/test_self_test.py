"""Tests for ``operator/bridges/shared/self_test.py``.

Approach: every check runs against a throwaway ``CORVIN_HOME`` so the host's
real ``~/.corvin`` is never touched. The few checks that probe external
binaries (engine CLIs) are exercised through the ``shutil.which`` seam so we
don't depend on what's installed on the test host.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import self_test as st  # noqa: E402


class _Sandbox:
    """Context manager that points ``CORVIN_HOME`` and the user vault into
    a temp tree so checks see a fully-controlled fixture."""

    def __enter__(self) -> "_Sandbox":
        self.tmp = tempfile.TemporaryDirectory(prefix="corvin-st-")
        self.root = Path(self.tmp.name)
        self.tenant_id = "_default"
        self.home = self.root / "tenants" / self.tenant_id
        self.home.mkdir(parents=True)
        self.env_patch = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(self.root),
            "CORVIN_TENANT_ID": self.tenant_id,
            # Make sure no leftover override points the audit elsewhere.
            "VOICE_AUDIT_PATH": "",
        })
        self.env_patch.start()
        # Force a fresh vault path so production secrets are never inspected.
        self.fake_vault = self.root / ".config" / "corvin-voice" / "secrets.json"
        self.home_patch = mock.patch.object(
            Path, "home", lambda: self.root)
        self.home_patch.start()
        return self

    def __exit__(self, *exc) -> None:
        self.home_patch.stop()
        self.env_patch.stop()
        self.tmp.cleanup()


class CheckResultValidationTests(unittest.TestCase):
    def test_rejects_unknown_severity(self) -> None:
        with self.assertRaises(ValueError):
            st.CheckResult("x", "DEBUG", True, "")

    def test_accepts_known_severities(self) -> None:
        for sev in (st.CRITICAL, st.WARNING, st.INFO):
            st.CheckResult("x", sev, True, "")


class SelfTestResultSemanticsTests(unittest.TestCase):
    def test_ok_true_when_no_critical(self) -> None:
        r = st.SelfTestResult(checks=[
            st.CheckResult("a", st.CRITICAL, True, ""),
            st.CheckResult("b", st.WARNING, False, "warn"),
            st.CheckResult("c", st.INFO, True, ""),
        ])
        self.assertTrue(r.ok)
        self.assertFalse(r.all_green)
        self.assertEqual([c.name for c in r.warnings], ["b"])
        self.assertEqual(r.critical_failures, [])

    def test_ok_false_when_any_critical_fails(self) -> None:
        r = st.SelfTestResult(checks=[
            st.CheckResult("a", st.CRITICAL, False, "boom"),
            st.CheckResult("b", st.WARNING, True, ""),
        ])
        self.assertFalse(r.ok)
        self.assertEqual([c.name for c in r.critical_failures], ["a"])

    def test_all_green_when_every_check_ok(self) -> None:
        r = st.SelfTestResult(checks=[
            st.CheckResult("a", st.CRITICAL, True, ""),
            st.CheckResult("b", st.WARNING, True, ""),
        ])
        self.assertTrue(r.all_green)
        self.assertTrue(r.ok)

    def test_to_dict_round_trips(self) -> None:
        r = st.SelfTestResult(checks=[
            st.CheckResult("a", st.CRITICAL, True, "ok"),
        ])
        d = r.to_dict()
        self.assertIn("ok", d)
        self.assertIn("checks", d)
        self.assertEqual(d["checks"][0]["name"], "a")
        # Must be JSON-serialisable for the --json mode.
        json.dumps(d)


class TenantTreeCheckTests(unittest.TestCase):
    def test_warns_when_subdirs_missing(self) -> None:
        with _Sandbox():
            checks = st._check_tenant_tree()
            names = {c.name: c for c in checks}
            self.assertTrue(names["tenant.resolved"].ok)
            self.assertTrue(names["tenant.home_exists"].ok)
            self.assertFalse(names["tenant.subdirs_present"].ok)
            self.assertEqual(names["tenant.subdirs_present"].severity, st.WARNING)

    def test_all_green_when_subdirs_present(self) -> None:
        with _Sandbox() as sbx:
            for sub in ("global", "sessions", "forge", "skill-forge",
                        "voice", "cowork"):
                (sbx.home / sub).mkdir()
            checks = st._check_tenant_tree()
            names = {c.name: c for c in checks}
            self.assertTrue(names["tenant.subdirs_present"].ok)

    def test_critical_when_tenant_home_missing(self) -> None:
        with _Sandbox() as sbx:
            # Remove tenant home that _Sandbox created.
            import shutil
            shutil.rmtree(sbx.home)
            checks = st._check_tenant_tree()
            names = {c.name: c for c in checks}
            self.assertFalse(names["tenant.home_exists"].ok)
            self.assertEqual(names["tenant.home_exists"].severity, st.CRITICAL)


class MemoryCheckTests(unittest.TestCase):
    def test_critical_when_recall_db_world_readable(self) -> None:
        with _Sandbox() as sbx:
            mem = sbx.home / "global" / "memory"
            mem.mkdir(parents=True)
            db = mem / "recall.db"
            db.write_bytes(b"sqlite-stub")
            os.chmod(db, 0o644)  # insecure
            checks = st._check_memory()
            mode_check = next(c for c in checks if c.name == "memory.recall_db_mode")
            self.assertFalse(mode_check.ok)
            self.assertEqual(mode_check.severity, st.CRITICAL)
            self.assertIn("INSECURE", mode_check.detail)

    def test_passes_when_recall_db_mode_0600(self) -> None:
        import sqlite3
        with _Sandbox() as sbx:
            mem = sbx.home / "global" / "memory"
            mem.mkdir(parents=True)
            db = mem / "recall.db"
            con = sqlite3.connect(str(db))
            con.execute("CREATE TABLE x(a)")
            con.close()
            os.chmod(db, 0o600)
            checks = st._check_memory()
            mode = next(c for c in checks if c.name == "memory.recall_db_mode")
            opn = next(c for c in checks if c.name == "memory.recall_db_openable")
            self.assertTrue(mode.ok)
            self.assertTrue(opn.ok)

    def test_info_only_when_no_db_yet(self) -> None:
        with _Sandbox():
            checks = st._check_memory()
            db_check = next(c for c in checks if c.name == "memory.recall_db")
            self.assertEqual(db_check.severity, st.INFO)
            self.assertTrue(db_check.ok)


class VaultCheckTests(unittest.TestCase):
    def test_critical_when_vault_world_readable(self) -> None:
        with _Sandbox() as sbx:
            sbx.fake_vault.parent.mkdir(parents=True)
            sbx.fake_vault.write_text("{}")
            os.chmod(sbx.fake_vault, 0o644)
            checks = st._check_vault()
            mode = next(c for c in checks if c.name == "vault.mode_0600")
            self.assertFalse(mode.ok)
            self.assertEqual(mode.severity, st.CRITICAL)

    def test_info_only_when_no_vault(self) -> None:
        with _Sandbox():
            checks = st._check_vault()
            self.assertEqual(len(checks), 1)
            self.assertEqual(checks[0].severity, st.INFO)
            self.assertTrue(checks[0].ok)


class EngineProbeTests(unittest.TestCase):
    def test_critical_when_claude_cli_missing(self) -> None:
        with mock.patch.object(st.shutil, "which", return_value=None):
            checks = st._check_engines(quick=True)
            cli = next(c for c in checks if c.name == "engine.claude_cli")
            self.assertFalse(cli.ok)
            self.assertEqual(cli.severity, st.CRITICAL)

    def test_optional_engines_never_critical(self) -> None:
        # Even when codex/opencode are missing, they must not be CRITICAL.
        def which(name: str):
            return "/usr/bin/claude" if name == "claude" else None
        completed = subprocess.CompletedProcess(args=[], returncode=0,
                                                stdout="claude 1.0", stderr="")
        with mock.patch.object(st.shutil, "which", side_effect=which), \
                mock.patch.object(st.subprocess, "run", return_value=completed):
            checks = st._check_engines(quick=False)
        codex = next(c for c in checks if c.name == "engine.codex_cli")
        opencode = next(c for c in checks if c.name == "engine.opencode_cli")
        # Both are INFO regardless of whether they were found.
        self.assertEqual(codex.severity, st.INFO)
        self.assertEqual(opencode.severity, st.INFO)
        self.assertTrue(codex.ok)
        self.assertTrue(opencode.ok)


class CLITests(unittest.TestCase):
    def test_json_output_is_parseable(self) -> None:
        with _Sandbox():
            # Capture stdout via subprocess so the production stdout-redirect
            # path is exercised.
            r = subprocess.run(
                [sys.executable, str(HERE / "self_test.py"), "--quick", "--json"],
                env={**os.environ,
                     "CORVIN_HOME": os.environ["CORVIN_HOME"],
                     "CORVIN_TENANT_ID": "_default"},
                capture_output=True, text=True, timeout=15,
            )
            self.assertIn(r.returncode, (0, 1))
            data = json.loads(r.stdout)
            self.assertIn("checks", data)
            self.assertIn("ok", data)

    def test_strict_flag_fails_on_warnings(self) -> None:
        # Fake a result with only-warnings, then run main() directly.
        only_warning = st.SelfTestResult(checks=[
            st.CheckResult("a", st.CRITICAL, True, ""),
            st.CheckResult("b", st.WARNING, False, "warn"),
        ])
        with mock.patch.object(st, "run_self_test", return_value=only_warning):
            rc_strict = st.main(["--strict"])
            rc_default = st.main([])
        self.assertEqual(rc_default, 0)
        self.assertEqual(rc_strict, 1)

    def test_critical_failure_exits_one(self) -> None:
        crit = st.SelfTestResult(checks=[
            st.CheckResult("a", st.CRITICAL, False, "boom"),
        ])
        with mock.patch.object(st, "run_self_test", return_value=crit):
            self.assertEqual(st.main([]), 1)


class AuditEmissionPrivacyTests(unittest.TestCase):
    """The audit event MUST NOT contain detail fields beyond check names
    and counts. PII / paths / version strings stay out of the chain."""

    def test_emit_details_contain_only_names_and_counts(self) -> None:
        captured: dict = {}

        def fake_write_event(path, event_type, **kw):
            captured["event_type"] = event_type
            captured["details"] = kw.get("details", {})
            captured["severity"] = kw.get("severity")
            return {}

        fake_se = mock.MagicMock()
        fake_se.write_event = fake_write_event
        with mock.patch.dict(sys.modules,
                             {"forge.security_events": fake_se}):
            result = st.SelfTestResult(checks=[
                st.CheckResult("a", st.CRITICAL, False, "secret-detail/foo/bar"),
                st.CheckResult("b", st.WARNING, False, "another-secret"),
            ])
            st._emit_audit(result)
        self.assertEqual(captured["event_type"], "boot.self_test_failed")
        self.assertEqual(captured["severity"], "CRITICAL")
        # The literal detail strings must NOT be in the audit payload.
        serialised = json.dumps(captured["details"])
        self.assertNotIn("secret-detail", serialised)
        self.assertNotIn("another-secret", serialised)
        # Names + counts are allowed.
        self.assertEqual(set(captured["details"]["critical_failures"]), {"a"})
        self.assertEqual(set(captured["details"]["warnings"]), {"b"})


class FullRunTests(unittest.TestCase):
    def test_run_self_test_smoke_quick(self) -> None:
        with _Sandbox():
            result = st.run_self_test(quick=True)
            self.assertIsInstance(result, st.SelfTestResult)
            # At minimum: tenant.resolved, memory paths, vault, claude_cli.
            names = {c.name for c in result.checks}
            self.assertIn("tenant.resolved", names)
            self.assertIn("audit.path", names)
            self.assertIn("engine.claude_cli", names)


class Layer33ChecksTests(unittest.TestCase):
    """Layer 33 self-test surface — added in Phase 7 of ADR-0040."""

    def test_library_and_handlers_present(self) -> None:
        with _Sandbox():
            checks = st._check_artifacts(quick=True)
            by_name = {c.name: c for c in checks}
            self.assertIn("artifacts.library_importable", by_name)
            self.assertTrue(by_name["artifacts.library_importable"].ok)
            self.assertEqual(by_name["artifacts.library_importable"].severity,
                             st.CRITICAL)
            self.assertIn("artifacts.mcp_handlers_registered", by_name)
            self.assertTrue(by_name["artifacts.mcp_handlers_registered"].ok)
            self.assertEqual(
                by_name["artifacts.mcp_handlers_registered"].severity,
                st.CRITICAL)

    def test_auto_register_hook_introspection(self) -> None:
        with _Sandbox():
            checks = st._check_artifacts(quick=True)
            hook_check = next(c for c in checks
                              if c.name == "artifacts.auto_register_hook")
            # In this repo both hook and hooks.json are present.
            self.assertTrue(hook_check.ok)
            self.assertEqual(hook_check.severity, st.INFO)


# ─── M3.6 — Layer 35 + 37 EU compliance checks ────────────────────────


def _write_tenant_yaml(sandbox: _Sandbox, content: str) -> Path:
    """Helper: write a tenant.corvin.yaml into the sandbox tenant
    home so _load_tenant_config_for_self_test() finds it."""
    cfg = sandbox.home / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(content)
    return cfg


class EgressPresetCheckTests(unittest.TestCase):
    """ADR-0043 / Layer 35 self-test integration."""

    def test_no_yaml_returns_info_not_configured(self) -> None:
        with _Sandbox():
            results = st._check_egress_preset()
            names = {c.name for c in results}
            self.assertIn("egress.preset_loaded", names)
            for r in results:
                self.assertTrue(r.ok)
                self.assertEqual(r.severity, st.INFO)

    def test_disabled_returns_info(self) -> None:
        with _Sandbox() as sb:
            _write_tenant_yaml(sb, """
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  egress:
    enabled: false
""")
            results = st._check_egress_preset()
            for r in results:
                self.assertEqual(r.severity, st.INFO)

    def test_eu_production_preset_loads_cleanly(self) -> None:
        with _Sandbox() as sb:
            _write_tenant_yaml(sb, """
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  egress:
    enabled: true
    default_action: deny
    allowed_hosts:
      - localhost
      - 127.0.0.1
    forbidden_hosts:
      - api.anthropic.com
""")
            results = st._check_egress_preset()
            critical = [r for r in results if r.severity == st.CRITICAL]
            self.assertEqual(critical, [])
            # Should be INFO "egress.preset_loaded" reporting counts.
            loaded = [r for r in results if r.name == "egress.preset_loaded"]
            self.assertTrue(any(r.ok for r in loaded))

    def test_deny_all_with_empty_allowed_warns(self) -> None:
        with _Sandbox() as sb:
            _write_tenant_yaml(sb, """
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  egress:
    enabled: true
    default_action: deny
    allowed_hosts: []
""")
            results = st._check_egress_preset()
            warns = [r for r in results
                     if r.severity == st.WARNING and not r.ok]
            self.assertTrue(warns, "expected a consistency WARNING")

    def test_malformed_yaml_returns_critical(self) -> None:
        with _Sandbox() as sb:
            _write_tenant_yaml(sb, """
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  egress:
    enabled: true
    default_action: maybe  # invalid
""")
            results = st._check_egress_preset()
            crit = [r for r in results if r.severity == st.CRITICAL]
            self.assertTrue(crit, f"expected CRITICAL, got {results}")


class AuditSealerCheckTests(unittest.TestCase):
    """ADR-0044 / Layer 37 self-test integration."""

    def test_no_yaml_returns_info(self) -> None:
        with _Sandbox():
            results = st._check_audit_sealer()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].severity, st.INFO)
            self.assertTrue(results[0].ok)

    def test_disabled_returns_info(self) -> None:
        with _Sandbox() as sb:
            _write_tenant_yaml(sb, """
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  audit:
    encryption_at_rest:
      enabled: false
""")
            results = st._check_audit_sealer()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].severity, st.INFO)
            self.assertIn("disabled", results[0].detail)

    def test_enabled_missing_binary_is_critical(self) -> None:
        with _Sandbox() as sb:
            _write_tenant_yaml(sb, """
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  audit:
    encryption_at_rest:
      enabled: true
      recipient: "age1xyz"
      sealer_cmd: age
""")
            # Patch sealer_binary_available to claim age is missing
            with mock.patch(
                "audit_sealer.sealer_binary_available", return_value=False
            ):
                results = st._check_audit_sealer()
            crit = [r for r in results if r.severity == st.CRITICAL]
            self.assertEqual(len(crit), 1)
            self.assertIn("age", crit[0].detail)

    def test_enabled_present_binary_is_info(self) -> None:
        with _Sandbox() as sb:
            _write_tenant_yaml(sb, """
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  audit:
    encryption_at_rest:
      enabled: true
      recipient: "age1xyz"
      sealer_cmd: age
    retention_years: 7
""")
            with mock.patch(
                "audit_sealer.sealer_binary_available", return_value=True
            ):
                results = st._check_audit_sealer()
            crit = [r for r in results if r.severity == st.CRITICAL]
            self.assertEqual(crit, [])
            info = [r for r in results if r.severity == st.INFO and r.ok]
            self.assertEqual(len(info), 1)

    def test_invalid_audit_block_is_critical(self) -> None:
        with _Sandbox() as sb:
            _write_tenant_yaml(sb, """
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  audit:
    encryption_at_rest:
      enabled: true
      recipient: ""  # invalid: enabled requires non-empty recipient
""")
            results = st._check_audit_sealer()
            crit = [r for r in results if r.severity == st.CRITICAL]
            self.assertTrue(crit, f"expected CRITICAL, got {results}")


class ComplianceManifestVersionPinTests(unittest.TestCase):
    """ADR-0057 M4: eu_production profile must set spec.compliance_manifest.min_version."""

    def _check_with_yaml(self, yaml_content: str) -> list:
        with _Sandbox() as sb:
            _write_tenant_yaml(sb, yaml_content)
            # Point manifest_dir at a temp dir so the check doesn't fail on
            # missing compliance/ directory (skips manifest rules, but the
            # version-pin check runs regardless).
            import tempfile
            with tempfile.TemporaryDirectory() as mdir:
                with mock.patch.dict(os.environ,
                                     {"CORVIN_COMPLIANCE_MANIFEST_DIR": mdir}):
                    return st._check_compliance_manifest()

    def test_eu_production_no_min_version_emits_warning(self) -> None:
        results = self._check_with_yaml("""
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  deployment_profile: eu_production
""")
        pins = [r for r in results if r.name == "compliance.manifest.version_pin"]
        self.assertTrue(pins, f"expected version_pin WARNING, got {results}")
        self.assertEqual(pins[0].severity, st.WARNING)
        self.assertFalse(pins[0].ok)
        self.assertIn("min_version", pins[0].detail)

    def test_eu_production_ollama_no_min_version_emits_warning(self) -> None:
        results = self._check_with_yaml("""
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  deployment_profile: eu_production_ollama
""")
        pins = [r for r in results if r.name == "compliance.manifest.version_pin"]
        self.assertTrue(pins, f"expected version_pin WARNING for ollama, got {results}")
        self.assertEqual(pins[0].severity, st.WARNING)

    def test_eu_production_with_min_version_no_warning(self) -> None:
        results = self._check_with_yaml("""
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  deployment_profile: eu_production
  compliance_manifest:
    min_version: "1.0.0"
""")
        pins = [r for r in results if r.name == "compliance.manifest.version_pin"]
        self.assertEqual(pins, [], f"expected no version_pin warning, got {pins}")

    def test_dev_profile_no_warning(self) -> None:
        results = self._check_with_yaml("""
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec:
  deployment_profile: dev
""")
        pins = [r for r in results if r.name == "compliance.manifest.version_pin"]
        self.assertEqual(pins, [], "dev profile must not emit version_pin warning")

    def test_no_profile_no_warning(self) -> None:
        results = self._check_with_yaml("""
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: _default
spec: {}
""")
        pins = [r for r in results if r.name == "compliance.manifest.version_pin"]
        self.assertEqual(pins, [], "missing profile must not emit version_pin warning")


class A2AKeyFileCheckTests(unittest.TestCase):
    """_check_a2a_key_files: mode-0600 enforcement for A2A credentials."""

    def _origins_dir(self) -> Path:
        return st._REPO_ROOT / "operator" / "cowork" / "remote_origins"

    def _endpoints_dir(self) -> Path:
        return st._REPO_ROOT / "operator" / "cowork" / "remote_endpoints"

    def test_info_when_no_directories_exist(self) -> None:
        """No A2A dirs → INFO (not yet provisioned)."""
        with mock.patch.object(st.Path, "exists", return_value=False):
            checks = st._check_a2a_key_files()
        names = [c.name for c in checks]
        self.assertIn("a2a.origin_key_mode", names)
        for c in checks:
            self.assertEqual(c.severity, st.INFO)
            self.assertTrue(c.ok)

    def test_critical_on_world_readable_origin_key(self) -> None:
        """A world-readable origin JSON must trigger CRITICAL."""
        origins = self._origins_dir()
        key_file = origins / "_test_origin.json"
        try:
            origins.mkdir(parents=True, exist_ok=True)
            key_file.write_text('{"hmac_key": "deadbeef"}')
            os.chmod(key_file, 0o644)
            checks = st._check_a2a_key_files()
            origin_check = next(
                (c for c in checks if c.name == "a2a.origin_key_mode"), None
            )
            self.assertIsNotNone(origin_check)
            self.assertFalse(origin_check.ok)
            self.assertEqual(origin_check.severity, st.CRITICAL)
        finally:
            if key_file.exists():
                os.chmod(key_file, 0o600)
                key_file.unlink(missing_ok=True)

    def test_ok_when_all_files_mode_0600(self) -> None:
        """All key files mode 0600 → INFO OK."""
        endpoints = self._endpoints_dir()
        key_file = endpoints / "_test_endpoint.json"
        try:
            endpoints.mkdir(parents=True, exist_ok=True)
            key_file.write_text('{"api_key": "secret"}')
            os.chmod(key_file, 0o600)
            checks = st._check_a2a_key_files()
            endpoint_check = next(
                (c for c in checks if c.name == "a2a.endpoint_key_mode"), None
            )
            self.assertIsNotNone(endpoint_check)
            self.assertTrue(endpoint_check.ok)
        finally:
            if key_file.exists():
                key_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
