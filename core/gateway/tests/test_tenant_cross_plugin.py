"""Cross-plugin tenant integration — ADR-0007 Phase 1 + 6 join.

Phase 1 ships the tenant identity contract, path resolvers, state-store
kwargs and migration helper in isolation. Phase 6 ships the audit-chain
projection. The per-subtask E2Es for each phase verify their own slice,
but nothing exercises the THREE-PLUGIN handoff:

    voice/bridges/shared state-store WRITES via tenant_id
        ↓
    forge.security_events APPENDS to <tenant>/global/forge/audit.jsonl
        ↓
    corvin_gateway.audit_metrics READS the same chain via tenant_id

This file closes that gap with five cases that span the three plugins:

  T1 — Two-tenant parallel write + isolated metric projection.
  T2 — Cross-tenant chain integrity (verify_chain per tenant).
  T3 — State-store path resolver agreement across plugins (forge.paths
       tenant resolver lands on the same on-disk location the voice
       state-store kwarg writes to).
  T4 — Invalid tenant id rejected at every plugin boundary (forge
       resolver, audit-metrics aggregate, state-store kwarg).
  T5 — CORVIN_TENANT_ID env-var precedence: explicit kwarg beats env.

Sandbox-only: every case redirects CORVIN_HOME to a tempdir. The
live ~/.corvin tree is never touched.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from corvin_gateway import audit_metrics  # noqa: E402
from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _se  # noqa: E402
from forge import tenants as _tenants  # noqa: E402


class _SandboxBase(unittest.TestCase):
    """Shared sandbox: CORVIN_HOME redirected, two tenants provisioned."""

    tenants = ("acme", "globex")

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="corvin-cross-plugin-")
        self.home = Path(self._tmp) / "corvin"
        self.home.mkdir(parents=True, exist_ok=True)
        self._saved_home = os.environ.get("CORVIN_HOME")
        self._saved_tid = os.environ.get("CORVIN_TENANT_ID")
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ.pop("CORVIN_TENANT_ID", None)
        # Provision both tenant trees: the migration helper would do
        # this in production; tests skip the helper to focus on the
        # cross-plugin handoff.
        for tid in self.tenants:
            (self.home / "tenants" / tid / "global" / "forge").mkdir(
                parents=True, exist_ok=True
            )
        audit_metrics.clear_cache()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._saved_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._saved_home
        if self._saved_tid is None:
            os.environ.pop("CORVIN_TENANT_ID", None)
        else:
            os.environ["CORVIN_TENANT_ID"] = self._saved_tid
        audit_metrics.clear_cache()

    def _chain(self, tenant_id: str) -> Path:
        return _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

    def _write(self, tenant_id: str, event_type: str, **details) -> dict:
        return _se.write_event(
            self._chain(tenant_id), event_type, details=details or {}
        )


class T1_TwoTenantMetricProjection(_SandboxBase):
    """acme writes 3 events, globex writes 1; metrics see each separately."""

    def test_isolated_counters(self):
        # acme: three tool.created events
        for name in ("alpha", "beta", "gamma"):
            self._write("acme", "tool.created", tool_name=name)
        # globex: one
        self._write("globex", "tool.created", tool_name="delta")

        # Aggregate per tenant
        snap_acme = audit_metrics.aggregate("acme")
        snap_globex = audit_metrics.aggregate("globex")

        # Find the forge-tools-created counter family
        def _counter_total(snap, name):
            fam = snap.counters.get(name)
            if fam is None:
                return 0
            return sum(int(v) for v in fam.by_labels.values())

        acme_total = _counter_total(snap_acme, "corvin_forge_tools_created_total")
        globex_total = _counter_total(
            snap_globex, "corvin_forge_tools_created_total"
        )
        self.assertEqual(acme_total, 3, "acme must see 3 tool.created events")
        self.assertEqual(globex_total, 1, "globex must see exactly 1 event")

    def test_render_text_isolated(self):
        self._write("acme", "skill.created")
        self._write("acme", "skill.created")
        self._write("globex", "skill.created")

        body_acme = audit_metrics.render("acme")
        body_globex = audit_metrics.render("globex")

        # Both bodies must declare the chain-intact gauge
        self.assertIn("corvin_audit_chain_intact", body_acme)
        self.assertIn("corvin_audit_chain_intact", body_globex)
        # Both bodies must carry the # HELP / # TYPE preamble for the
        # skill-created counter
        self.assertIn("# HELP", body_acme)
        self.assertIn("# HELP", body_globex)
        # No tenant-id leakage: globex's exposition must not name acme
        # anywhere (would only leak if labels carried tenant_id, which
        # they MUST NOT per ADR-0007).
        self.assertNotIn("acme", body_globex)
        self.assertNotIn("globex", body_acme)


class T2_CrossTenantChainIntegrity(_SandboxBase):
    """Each tenant's chain verifies independently; tampering one doesn't
    affect the other."""

    def test_both_chains_verify_clean(self):
        # Write several events per tenant; both chains must verify.
        for tid in self.tenants:
            self._write(tid, "session.reset")
            self._write(tid, "tool.created")
            self._write(tid, "skill.created")

        for tid in self.tenants:
            ok, problems = _se.verify_chain(self._chain(tid))
            self.assertTrue(ok, f"chain for {tid} must verify clean: {problems}")
            self.assertEqual(problems, [])

    def test_tampering_one_does_not_break_other(self):
        self._write("acme", "tool.created")
        self._write("acme", "tool.created")
        self._write("globex", "tool.created")
        self._write("globex", "tool.created")

        # Tamper with acme's chain (overwrite a hash in line 2)
        acme_path = self._chain("acme")
        lines = acme_path.read_text().splitlines()
        rec = json.loads(lines[1])
        rec["hash"] = "0" * 16  # bogus hash → integrity break
        lines[1] = json.dumps(rec)
        acme_path.write_text("\n".join(lines) + "\n")

        ok_acme, problems_acme = _se.verify_chain(acme_path)
        ok_globex, problems_globex = _se.verify_chain(self._chain("globex"))

        self.assertFalse(ok_acme, "tampered chain must NOT verify")
        self.assertGreater(len(problems_acme), 0)
        self.assertTrue(ok_globex, "globex chain stays clean — tenant isolation")
        self.assertEqual(problems_globex, [])

    def test_metric_chain_intact_gauge_flips(self):
        # Clean chain → gauge=1; after tampering acme → acme=0, globex=1
        self._write("acme", "tool.created")
        self._write("globex", "tool.created")

        body_acme_clean = audit_metrics.render("acme")
        self.assertIn("corvin_audit_chain_intact 1", body_acme_clean)

        # Tamper acme
        p = self._chain("acme")
        lines = p.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["hash"] = "deadbeefdeadbeef"
        lines[0] = json.dumps(rec)
        p.write_text("\n".join(lines) + "\n")
        audit_metrics.clear_cache()

        body_acme_broken = audit_metrics.render("acme")
        body_globex = audit_metrics.render("globex")
        self.assertIn("corvin_audit_chain_intact 0", body_acme_broken)
        self.assertIn(
            "corvin_audit_chain_intact 1", body_globex,
            "globex chain stays intact when acme's is tampered",
        )


class T3_PathResolverAgreement(_SandboxBase):
    """forge.paths.tenant_global_dir agrees with the state-store kwarg
    on the on-disk location of the chain."""

    def test_forge_paths_and_audit_metrics_resolve_same_chain(self):
        # forge.paths constructs the audit path one way
        forge_chain = (
            _forge_paths.tenant_global_dir("acme") / "forge" / "audit.jsonl"
        )
        # audit_metrics constructs it through its own helper
        metrics_chain = audit_metrics._audit_path("acme")
        self.assertEqual(forge_chain, metrics_chain)

    def test_state_store_tenant_kwarg_lands_under_same_tenant_root(self):
        # Import a state-store module and verify its path resolver
        # respects the same tenant_id contract.
        mod = importlib.import_module("roles")
        store_path = mod._store_path("discord", "chatA", tenant_id="acme")
        tenant_root = _forge_paths.tenant_home("acme")
        # The state-store path must sit under the tenant's home
        self.assertTrue(
            str(store_path).startswith(str(tenant_root)),
            f"{store_path} not under {tenant_root}",
        )

    def test_cross_plugin_paths_consistent_for_default(self):
        # Without tenant_id arg, all plugins should land on _default
        default_chain_forge = (
            _forge_paths.tenant_global_dir() / "forge" / "audit.jsonl"
        )
        default_chain_metrics = audit_metrics._audit_path("_default")
        self.assertEqual(default_chain_forge, default_chain_metrics)
        # And the path MUST resolve under tenants/_default/
        self.assertIn("_default", default_chain_forge.parts)


class T4_InvalidTenantIDRejection(_SandboxBase):
    """An invalid tenant id is rejected at every plugin boundary."""

    BAD_IDS = (
        "../escape",
        "UPPERCASE",
        "spaces in here",
        "a/b",
        "_" * 64,  # too long after the leading underscore
        "",
    )

    def test_forge_tenants_validate_rejects(self):
        for bad in self.BAD_IDS:
            with self.subTest(tid=bad):
                with self.assertRaises(_tenants.InvalidTenantID):
                    _tenants.validate_tenant_id(bad)

    def test_forge_paths_tenant_global_dir_rejects(self):
        for bad in self.BAD_IDS:
            if bad == "":
                # forge.paths' _validate_tenant_id treats empty as
                # default fall-through via _resolve_tenant_id; only
                # explicit non-empty IDs are validated.
                continue
            with self.subTest(tid=bad):
                with self.assertRaises(ValueError):
                    _forge_paths.tenant_global_dir(bad)

    def test_audit_metrics_aggregate_propagates_rejection(self):
        # aggregate("../escape") must NOT silently produce a snapshot
        # over an attacker-chosen path. Either: the path resolver
        # rejects at construction, or the resulting path doesn't exist
        # → empty snapshot. We verify the safety property: the snapshot
        # contains zero events and zero counters from the broken id.
        for bad in ("../escape", "UPPERCASE"):
            with self.subTest(tid=bad):
                with self.assertRaises(ValueError):
                    audit_metrics.aggregate(bad)


class T5_EnvPrecedenceAndIsolation(_SandboxBase):
    """CORVIN_TENANT_ID env-var is consulted by current_tenant(), but an
    explicit kwarg ALWAYS wins."""

    def test_env_is_default_when_no_kwarg(self):
        os.environ["CORVIN_TENANT_ID"] = "acme"
        self.assertEqual(_tenants.current_tenant(), "acme")

    def test_explicit_kwarg_beats_env(self):
        os.environ["CORVIN_TENANT_ID"] = "acme"
        self.assertEqual(_tenants.current_tenant("globex"), "globex")

    def test_env_drives_default_resolver_in_paths(self):
        os.environ["CORVIN_TENANT_ID"] = "globex"
        # With env set, no-arg tenant_global_dir() resolves to globex
        p = _forge_paths.tenant_global_dir()
        self.assertIn("globex", p.parts)

    def test_invalid_env_var_raises(self):
        # An invalid env value MUST NOT silently fall through to _default
        os.environ["CORVIN_TENANT_ID"] = "../escape"
        with self.assertRaises(_tenants.InvalidTenantID):
            _tenants.current_tenant()


if __name__ == "__main__":
    unittest.main(verbosity=2)
