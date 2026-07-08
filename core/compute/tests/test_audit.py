"""Phase 13.6 — Audit chain + path-gate + tenant-config tests."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]

sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))

from corvin_compute.audit import (  # noqa: E402
    AuditFieldNotAllowed, emit, redact_sensitive_fields,
)
from corvin_compute.iteration import param_fingerprint  # noqa: E402


class AuditAllowListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp(prefix="corvin-compute-audit-")
        self.audit_path = Path(self.td) / "audit.jsonl"
        self.captured: list[dict] = []

        def _fake_write(path, event_type, *, details=None, severity=None,
                        **kw):
            self.captured.append({
                "path": str(path),
                "event": event_type,
                "details": details or {},
                "severity": severity,
            })

        self._fake_write = _fake_write

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def test_iteration_event_rejects_params_in_clear(self) -> None:
        with self.assertRaises(AuditFieldNotAllowed):
            emit(
                "compute.iteration_completed",
                path=self.audit_path,
                run_id="compute_abc",
                tenant_id="_default",
                iter=1, loss=0.5, wall_ms=100,
                param_fingerprint="sha256:abc",
                strategy="grid",
                cache_hit=False,
                params={"window": 10},  # forbidden!
                write_event_fn=self._fake_write,
            )

    def test_iteration_event_admits_allowed_fields(self) -> None:
        emit(
            "compute.iteration_completed",
            path=self.audit_path,
            run_id="compute_abc",
            tenant_id="_default",
            iter=1, loss=0.5, wall_ms=100,
            param_fingerprint="sha256:abc",
            strategy="grid",
            cache_hit=False,
            write_event_fn=self._fake_write,
        )
        self.assertEqual(len(self.captured), 1)
        details = self.captured[0]["details"]
        self.assertEqual(details["iter"], 1)
        self.assertEqual(details["param_fingerprint"], "sha256:abc")
        self.assertNotIn("params", details)

    def test_run_started_admits_required_fields(self) -> None:
        emit(
            "compute.run_started",
            path=self.audit_path,
            run_id="compute_abc",
            tenant_id="_default",
            tool_name="echo",
            strategy="grid",
            budget={"max_iterations": 10},
            write_event_fn=self._fake_write,
        )
        self.assertEqual(self.captured[0]["event"], "compute.run_started")

    def test_run_terminal_rejects_unknown_field(self) -> None:
        with self.assertRaises(AuditFieldNotAllowed):
            emit(
                "compute.run_terminal",
                path=self.audit_path,
                run_id="compute_abc",
                tenant_id="_default",
                state="converged",
                total_iterations=10, total_wall_s=5.0,
                best_loss=0.1, convergence_reason="eps-reached",
                worker_pid=1234,  # forbidden!
                write_event_fn=self._fake_write,
            )


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_canonical_ordering(self) -> None:
        f1 = param_fingerprint({"a": 1, "b": 2})
        f2 = param_fingerprint({"b": 2, "a": 1})
        self.assertEqual(f1, f2)

    def test_fingerprint_deterministic_across_calls(self) -> None:
        params = {"window": 7, "method": "downside", "k": 1.5}
        f1 = param_fingerprint(params)
        f2 = param_fingerprint(params)
        self.assertEqual(f1, f2)
        self.assertTrue(f1.startswith("sha256:"))
        self.assertEqual(len(f1), len("sha256:") + 16)


class SensitiveRedactionTests(unittest.TestCase):
    def test_sensitive_field_redacted(self) -> None:
        params = {
            "window": 14,
            "api_endpoint": "https://internal.example.com/v1",
        }
        redacted = redact_sensitive_fields(params, ["api_endpoint"])
        self.assertEqual(redacted["window"], 14)
        self.assertTrue(redacted["api_endpoint"].startswith("<hash:"))
        self.assertTrue(redacted["api_endpoint"].endswith(">"))

    def test_non_sensitive_field_clear(self) -> None:
        params = {"window": 14, "method": "std"}
        redacted = redact_sensitive_fields(params, [])
        self.assertEqual(redacted, params)

    def test_redaction_deterministic(self) -> None:
        params = {"key": "secret-value"}
        r1 = redact_sensitive_fields(params, ["key"])
        r2 = redact_sensitive_fields(params, ["key"])
        self.assertEqual(r1["key"], r2["key"])


class PathGateTests(unittest.TestCase):
    """Layer-10 path-gate must deny direct writes to <corvin_home>/**/compute/**."""

    def setUp(self) -> None:
        # Bring the path_gate module in fresh per test so caches don't leak.
        sys.path.insert(0, str(REPO_ROOT / "operator" / "voice" / "hooks"))
        for mod in [m for m in list(sys.modules) if m == "path_gate"]:
            del sys.modules[mod]
        import path_gate  # noqa: F401
        self.gate = sys.modules["path_gate"]
        self.td = tempfile.mkdtemp(prefix="corvin-compute-pg-")
        self.corvin_home = Path(self.td) / "corvin"
        self._old_env: dict[str, str | None] = {}
        for k in ("CORVIN_HOME",):
            self._old_env[k] = os.environ.get(k)
        os.environ["CORVIN_HOME"] = str(self.corvin_home)

    def tearDown(self) -> None:
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def test_compute_artifact_dir_is_protected(self) -> None:
        path = (self.corvin_home / "tenants" / "_default" / "compute"
                / "runs" / "compute_xxx" / "summary.json")
        self.assertTrue(self.gate.is_protected_path(str(path)))

    def test_compute_socket_is_protected(self) -> None:
        sock = (self.corvin_home / "tenants" / "_default" / "compute"
                / "worker.sock")
        self.assertTrue(self.gate.is_protected_path(str(sock)))

    def test_non_compute_path_not_protected(self) -> None:
        path = self.corvin_home / "tenants" / "_default" / "other.txt"
        self.assertFalse(self.gate.is_protected_path(str(path)))

    def test_bash_redirect_into_compute_denied(self) -> None:
        target = (self.corvin_home / "tenants" / "_default" / "compute"
                  / "runs" / "compute_xxx" / "summary.json")
        cmd = f"echo X > {target}"
        # The extractor surfaces a list of targets + a fail-closed flag.
        # On parseable input the flag is False; the redirect target is
        # in the list and the path is classified as protected.
        targets, fail_closed = self.gate._bash_targets(cmd)
        self.assertFalse(fail_closed)
        self.assertIn(str(target), [str(t) for t in targets])
        self.assertTrue(any(self.gate.is_protected_path(str(t))
                            for t in targets))

    def test_bash_eval_with_compute_hint_fail_closed(self) -> None:
        cmd = 'eval "$(echo > /tmp/compute/foo)"'
        targets, fail_closed = self.gate._bash_targets(cmd)
        self.assertTrue(fail_closed,
                        f"eval with 'compute' hint should fail-closed; "
                        f"got fail_closed={fail_closed} / targets={targets}")


class TenantConfigTests(unittest.TestCase):
    """ComputeConfig schema slot in tenant.corvin.yaml."""

    def setUp(self) -> None:
        gateway_root = REPO_ROOT / "plugins" / "core" / "gateway"
        # Skip when corvin-gateway venv isn't bootstrapped (pydantic missing).
        try:
            sys.path.insert(0, str(gateway_root))
            from corvin_gateway.tenant_config import (  # noqa: E402,F401
                TenantConfig, ComputeConfig,
            )
            self.TenantConfig = TenantConfig
            self.ComputeConfig = ComputeConfig
        except ImportError:
            self.skipTest("corvin-gateway venv not bootstrapped — pydantic absent")

    def test_default_compute_is_none(self) -> None:
        cfg = self.TenantConfig.default("_default")
        self.assertIsNone(cfg.spec.compute)

    def test_compute_block_defaults(self) -> None:
        cc = self.ComputeConfig()
        # ADR-0013: the strategy-loop master switch is enabled by default per
        # tenant (matches tenant_config.ComputeConfig + fabric_config.FabricConfig,
        # both `enabled: bool = True`). The data-access switches
        # (fabric_enabled/datasource_enabled/oracle_enabled) stay False-gated.
        self.assertTrue(cc.enabled)
        self.assertEqual(cc.max_parallel_iterations, 4)
        self.assertEqual(cc.max_concurrent_runs, 2)
        self.assertEqual(cc.max_iterations_per_run, 200)
        self.assertEqual(cc.top_k_size, 5)
        self.assertFalse(cc.disallow_llm_strategies)
        self.assertEqual(cc.strategies_allowed, ["grid", "random", "bayesian"])

    def test_compute_extra_keys_forbidden(self) -> None:
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.ComputeConfig(max_parallel_runs=4)  # typo for _iterations

    def test_compute_clamps(self) -> None:
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.ComputeConfig(max_parallel_iterations=100)
        with self.assertRaises(ValidationError):
            self.ComputeConfig(max_concurrent_runs=99)
        with self.assertRaises(ValidationError):
            self.ComputeConfig(top_k_size=0)

    def test_strategies_allowed_overrideable(self) -> None:
        cc = self.ComputeConfig(strategies_allowed=["grid", "random"])
        self.assertEqual(cc.strategies_allowed, ["grid", "random"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
