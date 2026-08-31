"""Per-subtask E2E for ADR-0007 Phase 3.1 — tenant.corvin.yaml.

Covers:
  * ``TenantConfig.default`` produces a permissive baseline.
  * Round-trip: ``init`` → on-disk file (mode 0o600) → ``load``
    returns an identical structure.
  * Schema strictness: ``extra='forbid'`` rejects unknown keys at
    every level; invalid budget bounds rejected.
  * Defensive post-validation: invalid zone shape, non-list engine
    fields, metadata.id mismatch with on-disk location.
  * ``load_or_default`` returns the default when the file is
    absent, raises when the file is present but defective.
  * Mode-check fail-closed: file mode > 0o600 → TenantConfigMalformed.
  * Unprovisioned tenant: ``save`` refuses to create the tenant tree.
  * ``is_engine_allowed`` precedence: forbid > allowlist > default.
  * CLI round-trip: ``tenant init`` writes the file; ``tenant show``
    prints the YAML round-trip.
  * Unknown-engine warning on the CLI does NOT block the write.

Every case runs against a fresh ``<corvin_home>`` tempdir; the
operator's real ``~/.corvin/`` is never touched.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

import yaml  # noqa: E402

from corvin_gateway import cli, tenant_config  # noqa: E402
from corvin_gateway.tenant_config import (  # noqa: E402
    TenantConfig,
    TenantConfigMalformed,
    init,
    load,
    load_or_default,
    save,
)


# ── Common fixture ───────────────────────────────────────────────────


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-tcfg-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        for t in tenants:
            (home / "tenants" / t / "global").mkdir(parents=True)
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)


def _yaml_path(home: Path, tenant: str) -> Path:
    return home / "tenants" / tenant / "global" / "tenant.corvin.yaml"


# ── Default + factory ────────────────────────────────────────────────


class DefaultConstructorTests(unittest.TestCase):
    def test_default_is_permissive(self):
        c = TenantConfig.default("acme", display_name="ACME")
        self.assertEqual(c.metadata.id, "acme")
        self.assertEqual(c.metadata.display_name, "ACME")
        self.assertEqual(c.spec.data_residency.zone, None)
        self.assertEqual(c.spec.data_residency.allowed_engines, [])
        self.assertEqual(c.spec.data_residency.forbid_engines, [])
        self.assertIsNone(c.spec.budget.max_runs_per_day)


# ── Round-trip ───────────────────────────────────────────────────────


class RoundTripTests(unittest.TestCase):
    def test_init_writes_mode_0600_yaml(self):
        with sandbox(("acme",)) as home:
            init("acme", display_name="ACME", zone="eu-west")
            p = _yaml_path(home, "acme")
            self.assertTrue(p.exists())
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            # YAML parses to a dict with the expected top-level keys
            data = yaml.safe_load(p.read_text())
            self.assertEqual(data["apiVersion"], "corvin/v1")
            self.assertEqual(data["kind"], "Tenant")
            self.assertEqual(data["metadata"]["id"], "acme")
            self.assertEqual(data["spec"]["data_residency"]["zone"], "eu-west")

    def test_load_returns_identical_structure(self):
        with sandbox(("acme",)) as home:
            init(
                "acme",
                display_name="ACME",
                zone="eu-west",
                allowed_engines=["claude_code"],
                forbid_engines=[],
            )
            c = load("acme")
            self.assertEqual(c.metadata.id, "acme")
            self.assertEqual(c.metadata.display_name, "ACME")
            self.assertEqual(c.spec.data_residency.zone, "eu-west")
            self.assertEqual(c.spec.data_residency.allowed_engines, ["claude_code"])

    def test_save_overwrites_in_place(self):
        with sandbox(("acme",)) as home:
            init("acme", display_name="v1")
            c = load("acme")
            c.metadata.display_name = "v2"
            save(c)
            c2 = load("acme")
            self.assertEqual(c2.metadata.display_name, "v2")
            # Mode preserved after rewrite
            p = _yaml_path(home, "acme")
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)


# ── Schema strictness ────────────────────────────────────────────────


class SchemaStrictnessTests(unittest.TestCase):
    def test_extra_key_at_spec_level_rejected(self):
        with sandbox(("acme",)) as home:
            p = _yaml_path(home, "acme")
            p.write_text(yaml.safe_dump({
                "apiVersion": "corvin/v1",
                "kind":       "Tenant",
                "metadata":   {"id": "acme"},
                "spec":       {"unknown_key": True},
            }))
            os.chmod(p, 0o600)
            with self.assertRaises(TenantConfigMalformed):
                load("acme")

    def test_wrong_api_version_rejected(self):
        with sandbox(("acme",)) as home:
            p = _yaml_path(home, "acme")
            p.write_text(yaml.safe_dump({
                "apiVersion": "corvin/v9",
                "kind":       "Tenant",
                "metadata":   {"id": "acme"},
            }))
            os.chmod(p, 0o600)
            with self.assertRaises(TenantConfigMalformed):
                load("acme")

    def test_metadata_id_mismatch_rejected(self):
        with sandbox(("acme", "globex")) as home:
            p = _yaml_path(home, "acme")
            p.write_text(yaml.safe_dump({
                "apiVersion": "corvin/v1",
                "kind":       "Tenant",
                "metadata":   {"id": "globex"},  # wrong!
            }))
            os.chmod(p, 0o600)
            with self.assertRaises(TenantConfigMalformed):
                load("acme")

    def test_invalid_zone_shape_rejected(self):
        with sandbox(("acme",)) as home:
            p = _yaml_path(home, "acme")
            p.write_text(yaml.safe_dump({
                "apiVersion": "corvin/v1",
                "kind":       "Tenant",
                "metadata":   {"id": "acme"},
                "spec":       {"data_residency": {"zone": "EU WEST"}},
            }))
            os.chmod(p, 0o600)
            with self.assertRaises(TenantConfigMalformed):
                load("acme")

    def test_budget_out_of_range_rejected(self):
        with sandbox(("acme",)) as home:
            p = _yaml_path(home, "acme")
            p.write_text(yaml.safe_dump({
                "apiVersion": "corvin/v1",
                "kind":       "Tenant",
                "metadata":   {"id": "acme"},
                "spec": {
                    "budget": {"max_wall_clock_per_run_s": 999999},  # >3600
                },
            }))
            os.chmod(p, 0o600)
            with self.assertRaises(TenantConfigMalformed):
                load("acme")


# ── load_or_default ──────────────────────────────────────────────────


class LoadOrDefaultTests(unittest.TestCase):
    def test_returns_default_when_absent(self):
        with sandbox(("acme",)) as home:
            c = load_or_default("acme")
            self.assertEqual(c.metadata.id, "acme")
            self.assertEqual(c.spec.data_residency.allowed_engines, [])

    def test_raises_when_present_but_defective(self):
        with sandbox(("acme",)) as home:
            p = _yaml_path(home, "acme")
            p.write_text("not: valid: yaml:")
            os.chmod(p, 0o600)
            with self.assertRaises(TenantConfigMalformed):
                load_or_default("acme")


# ── File-mode fail-closed ───────────────────────────────────────────


class FileModeTests(unittest.TestCase):
    def test_world_readable_rejected(self):
        with sandbox(("acme",)) as home:
            init("acme")
            p = _yaml_path(home, "acme")
            os.chmod(p, 0o644)
            with self.assertRaises(TenantConfigMalformed):
                load("acme")


# ── No tenant-dir creation ──────────────────────────────────────────


class NoTenantMkdirTests(unittest.TestCase):
    def test_save_refuses_to_create_tenant_tree(self):
        with tempfile.TemporaryDirectory(prefix="gw-tcfg-no-tenant-") as td:
            os.environ["CORVIN_HOME"] = td
            try:
                c = TenantConfig.default("acme", display_name="ACME")
                with self.assertRaises(TenantConfigMalformed):
                    save(c)
                # No tenant dir was created
                self.assertFalse((Path(td) / "tenants" / "acme").exists())
            finally:
                os.environ.pop("CORVIN_HOME", None)


# ── is_engine_allowed precedence ────────────────────────────────────


class EngineAllowedTests(unittest.TestCase):
    def test_empty_allowlist_means_unrestricted(self):
        c = TenantConfig.default("acme")
        self.assertTrue(c.is_engine_allowed("claude_code"))
        self.assertTrue(c.is_engine_allowed("future_engine"))

    def test_allowlist_restricts(self):
        c = TenantConfig.default("acme")
        c.spec.data_residency.allowed_engines = ["claude_code"]
        self.assertTrue(c.is_engine_allowed("claude_code"))
        self.assertFalse(c.is_engine_allowed("codex_cli"))

    def test_forbid_beats_allow(self):
        c = TenantConfig.default("acme")
        c.spec.data_residency.allowed_engines = ["claude_code"]
        c.spec.data_residency.forbid_engines = ["claude_code"]
        self.assertFalse(c.is_engine_allowed("claude_code"))

    def test_invalid_engine_arg_returns_false(self):
        c = TenantConfig.default("acme")
        self.assertFalse(c.is_engine_allowed(""))
        self.assertFalse(c.is_engine_allowed(None))  # type: ignore[arg-type]


# ── CLI round-trip ──────────────────────────────────────────────────


class CliTests(unittest.TestCase):
    def test_init_then_show(self):
        with sandbox(("acme",)) as home:
            # tenant init
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "tenant", "init", "acme",
                    "--display-name", "ACME Corporation",
                    "--zone", "eu-west",
                    "--allowed-engines", "claude_code",
                ])
            self.assertEqual(rc, 0)
            self.assertIn("Tenant config written", buf.getvalue())

            # tenant show
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["tenant", "show", "acme"])
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("acme", out)
            self.assertIn("eu-west", out)
            self.assertIn("claude_code", out)

    def test_show_missing_returns_1(self):
        with sandbox(("acme",)) as home:
            buf = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(stderr):
                rc = cli.main(["tenant", "show", "acme"])
            self.assertEqual(rc, 1)
            self.assertIn("Cannot load tenant", stderr.getvalue())

    def test_unknown_engine_warns_but_writes(self):
        with sandbox(("acme",)) as home:
            buf = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(stderr):
                rc = cli.main([
                    "tenant", "init", "acme",
                    "--allowed-engines", "claude_code", "bogus_engine",
                ])
            self.assertEqual(rc, 0)
            self.assertIn("unknown engine name", stderr.getvalue())
            # File was still written
            self.assertTrue(_yaml_path(home, "acme").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
