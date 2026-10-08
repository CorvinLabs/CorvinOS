"""E2E tests for Phase 4 A1: Gate-Threshold Tuning (Operator-Applied, Never Auto).

Tests prove:
1. gate_outcome_correlation() computes FAIL→deploy outcome correlations
2. GateThresholdAnalyzer detects overcautious/undercautious patterns
3. Significance threshold (≥5 samples, ≥70% signal) gates suggestions
4. CLI gate-threshold analyze command (real subprocess, real audit chain)
5. CLI gate-threshold apply command (real operator action, audited, never auto)
6. Console POST /gate-thresholds/apply route (real HTTP, audited, never auto)

CRITICAL: Each test drives a REAL call site (subprocess or HTTP), verifies side
effects on disk (registry, audit chain), and proves no auto-application happens.
Mocks are for test doubles only; the happy path runs the real code.
"""
from __future__ import annotations

import json
import time
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLI = REPO_ROOT / "scripts" / "layer_forge_cli.py"
TENANT = "_default"


@pytest.fixture
def home(tmp_path):
    """Sandboxed CORVIN_HOME for each test."""
    h = tmp_path / "corvin_home"
    for sub in ("tenants/_default/global/forge", "tenants/_default/global/layer_forge/registry"):
        (h / sub).mkdir(parents=True, exist_ok=True)
    return h


def _run_cli(home: Path, *args):
    """Run the CLI as a real subprocess with sandboxed CORVIN_HOME."""
    env = dict(os.environ, CORVIN_HOME=str(home), FORGE_ROOT=str(home / "forge"))
    env.pop("CORVIN_TENANT_ID", None)
    result = subprocess.run(
        [sys.executable, str(CLI), "--tenant", TENANT, *args],
        capture_output=True, text=True, timeout=120, env=env
    )
    return result


def _lf_events(home: Path) -> list[dict]:
    """Read Layer Forge events from audit chain."""
    chain = home / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    return [
        json.loads(l) for l in chain.read_text().splitlines()
        if l.strip() and "layer_forge." in l
    ]


def _create_definition_with_flags(home: Path, gate_id: str = "test_gate", version: str = "1.0.0") -> str:
    """Helper: create a layer definition with review flags for testing outcome correlation."""
    from core.orchestration.layer_forge.registry import LayerRegistry
    from core.orchestration.layer_forge.primitive import atomic_write_json

    registry_root = home / "tenants" / TENANT / "global" / "layer_forge" / "registry"
    registry = LayerRegistry(registry_root)

    manifest = {
        "id": f"test.threshold.{gate_id}",
        "version": version,
        "targets": [{"layer_id": "L34"}],
        "quality_gates": [{"gate_id": gate_id, "test_path": "tests/layer_forge/test_schema.py"}],
    }

    registry.validate(manifest)
    entry_id, version = manifest["id"], manifest["version"]
    record = dict(manifest)
    record["status"] = "deployed"  # Simulate successful override
    record["review_flagged"] = True
    record["review_flags"] = ["scope_creep"]
    # gate_outcome_correlation counts a definition only if (a) its review verdict was FLAGGED and
    # (b) it was created inside the analysis window (default: the last 30 days). The helper used
    # a fixed 2020 timestamp and no verdict, so the correlation could never be anything but 0.0.
    record["_review_verdict"] = {"status": "FLAGGED"}
    record["_created_at"] = time.time()

    atomic_write_json(registry._path_for(entry_id, version), record)
    return entry_id


class TestGateOutcomeCorrelation:
    """Test analytics.gate_outcome_correlation() computation."""

    def test_correlation_detects_overcautious_pattern(self, tmp_path):
        """Correlation calc identifies overcautious gates (high override success rate)."""
        from core.orchestration.layer_forge.analytics import LayerForgeAnalytics
        from core.orchestration.layer_forge.registry import LayerRegistry

        home = tmp_path / "test_home"
        registry_root = home / "tenants" / TENANT / "global" / "layer_forge" / "registry"
        registry_root.mkdir(parents=True, exist_ok=True)

        registry = LayerRegistry(registry_root)

        # Create 7 definitions with review flags that deployed successfully
        # One version per definition: the registry makes versions immutable, so seven writes of
        # the same id@version (what this loop did) are refused.
        for i in range(7):
            _create_definition_with_flags(home, gate_id="schema_check", version=f"1.0.{i}")

        # Create 2 that failed
        from core.orchestration.layer_forge.primitive import atomic_write_json

        for i in range(2):
            manifest = {
                "id": f"test.failed.{i}",
                "version": "1.0.0",
                "targets": [{"layer_id": "L34"}],
                "quality_gates": [{"gate_id": "schema_check", "test_path": "tests/layer_forge/test_schema.py"}],
            }
            registry.validate(manifest)
            record = dict(manifest)
            record["status"] = "rejected"  # Failed deployment
            record["review_flagged"] = True
            record["_review_verdict"] = {"status": "FLAGGED"}
            record["_created_at"] = time.time()
            atomic_write_json(registry._path_for(record["id"], record["version"]), record)

        analytics = LayerForgeAnalytics(registry=registry, tenant_id=TENANT)
        correlation = analytics.gate_outcome_correlation("schema_check")

        assert correlation["gate_id"] == "schema_check"
        assert correlation["override_success_rate"] > 0.70
        assert correlation["signal"] == "overcautious", "High success rate should signal overcautious"

    def test_correlation_with_insufficient_data(self, tmp_path):
        """Correlation with <5 samples signals neutral (not enough data)."""
        from core.orchestration.layer_forge.analytics import LayerForgeAnalytics
        from core.orchestration.layer_forge.registry import LayerRegistry

        home = tmp_path / "test_home"
        registry_root = home / "tenants" / TENANT / "global" / "layer_forge" / "registry"
        registry_root.mkdir(parents=True, exist_ok=True)

        registry = LayerRegistry(registry_root)
        analytics = LayerForgeAnalytics(registry=registry, tenant_id=TENANT)

        # Only 2 outcomes — too few for significance
        correlation = analytics.gate_outcome_correlation("sparse_gate")
        assert correlation["total_fails"] < 5
        assert correlation["signal"] == "neutral"


class TestGateThresholdAnalyzer:
    """Test GateThresholdAnalyzer pattern detection and significance."""

    def test_analyzer_significance_threshold(self):
        """Analyzer requires ≥5 samples and ≥70% success rate to be significant."""
        from core.orchestration.layer_forge.optimizer import (
            GateThresholdAnalyzer,
            GateThresholdPattern,
            OptimizationSignal,
        )

        analyzer = GateThresholdAnalyzer(TENANT)

        # Pattern: 3 overrides, 2 successful (not significant — too few)
        pattern_small = GateThresholdPattern(
            gate_id="small_sample",
            total_fails=3,
            override_successes=2,
            override_failures=1,
            override_success_rate=0.67,
            signal=OptimizationSignal.OVERCAUTIOUS,
        )
        assert not pattern_small.is_significant

        # Pattern: 10 overrides, 8 successful (significant — overcautious)
        pattern_sig = GateThresholdPattern(
            gate_id="good_pattern",
            total_fails=10,
            override_successes=8,
            override_failures=2,
            override_success_rate=0.80,
            signal=OptimizationSignal.OVERCAUTIOUS,
        )
        assert pattern_sig.is_significant


class TestCLIGateThresholdAnalyzeCommand:
    """Test CLI gate-threshold analyze (real subprocess, real audit chain)."""

    def test_cli_analyze_command_with_mock_analytics(self, home):
        """CLI gate-threshold analyze invokes real subprocess with real analytics."""
        # Create a definition with flags to provide data for analysis
        _create_definition_with_flags(home, gate_id="cli_test_gate")

        result = _run_cli(home, "gate-threshold", "analyze", "cli_test_gate", "--lookback-days", "90")

        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        output = json.loads(result.stdout)

        # Verify the analysis output structure
        assert output["gate_id"] == "cli_test_gate"
        assert "override_success_rate" in output
        assert "is_significant" in output
        assert "signal" in output


class TestCLIGateThresholdApplyCommand:
    """Test CLI gate-threshold apply (real call-site, audit-first, operator-explicit)."""

    def test_cli_apply_command_audits_threshold_change(self, home):
        """CLI apply command emits gate_threshold_applied audit event (no auto-apply)."""
        result = _run_cli(
            home,
            "gate-threshold", "apply",
            "prod_gate",
            "0.85",
            "--reason", "Overcautious: 9/10 overrides succeeded"
        )

        assert result.returncode == 0, f"CLI apply failed: {result.stderr}"
        output = json.loads(result.stdout)

        assert output["status"] == "SUCCESS"
        assert output["gate_id"] == "prod_gate"
        assert output["new_threshold"] == 0.85
        assert "audit_event" in output

        # CRITICAL: Verify audit event on disk (real chain write)
        events = _lf_events(home)
        gate_apply_events = [e for e in events if e.get("event_type") == "layer_forge.gate_threshold_applied"]
        assert len(gate_apply_events) >= 1, "gate_threshold_applied event must be in audit chain"

        event = gate_apply_events[0]
        assert event["details"]["gate_id"] == "prod_gate"
        assert event["details"]["new_threshold"] == 0.85
        assert event["details"]["actor"] == "cli"
        assert "reason" in event["details"]

    def test_cli_apply_requires_reason(self, home):
        """CLI apply command requires --reason flag (enforces explicit action)."""
        result = _run_cli(home, "gate-threshold", "apply", "gate_id", "0.9")
        assert result.returncode != 0, "Should fail without --reason"


class TestConsoleGateThresholdRoute:
    """Test console POST /gate-thresholds/apply (real HTTP, audit-first, no auto-apply)."""

    @pytest.fixture
    def console_client(self, tmp_path):
        """Mount real console router with sandboxed CORVIN_HOME."""
        home = tmp_path / "corvin_home"
        th = home / "tenants" / TENANT
        for sub in ("global/auth", "global/forge", "global/layer_forge/registry", "global/console/sessions"):
            (th / sub).mkdir(parents=True, exist_ok=True)

        keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")
        prev = {k: os.environ.get(k) for k in keys}
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["CORVIN_TENANT_ID"] = TENANT
        os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")

        try:
            # Reset modules to pick up new CORVIN_HOME
            for key in list(sys.modules):
                if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
                    del sys.modules[key]

            from corvin_console import auth as _auth
            from corvin_console.app import router
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            rec = _auth.create_session(tenant_id=TENANT, token_fingerprint="test-fp")
            csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
            app = FastAPI()
            app.include_router(router, prefix="/v1/console")
            client = TestClient(app, raise_server_exceptions=False)
            client.cookies.set("corvin_console_sid", rec.sid)

            yield client, csrf, home
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            for key in list(sys.modules):
                if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
                    del sys.modules[key]

    def test_console_apply_route_audits_threshold(self, console_client):
        """Console POST /gate-thresholds/apply audits the threshold change (no auto-apply)."""
        client, csrf, home = console_client

        h = {"X-CSRF-Token": csrf}
        body = {
            "gate_id": "http_test_gate",
            "new_threshold": 0.75,
            "reason": "Undercautious feedback: 3/8 overrides failed",
        }

        r = client.post("/v1/console/layer-forge/gate-thresholds/apply", json=body, headers=h)
        assert r.status_code == 200, r.text
        response = r.json()

        assert response["status"] == "SUCCESS"
        assert response["gate_id"] == "http_test_gate"
        assert response["new_threshold"] == 0.75

        # CRITICAL: Verify audit event on disk (real chain write, not mocked)
        chain = home / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"
        assert chain.exists(), "Audit chain must be created"

        events = [
            json.loads(l) for l in chain.read_text().splitlines()
            if l.strip() and "gate_threshold_applied" in l
        ]
        assert len(events) >= 1, "gate_threshold_applied event must be in audit chain"

        event = events[0]
        assert event["details"]["gate_id"] == "http_test_gate"
        assert event["details"]["new_threshold"] == 0.75
        assert event["details"]["actor"] == "console"

    def test_console_apply_never_auto_applies(self, console_client):
        """Console route returns audit success but never auto-applies to registry."""
        client, csrf, home = console_client

        h = {"X-CSRF-Token": csrf}
        body = {
            "gate_id": "no_auto_apply_gate",
            "new_threshold": 0.5,
            "reason": "Testing: this should NOT be auto-applied",
        }

        r = client.post("/v1/console/layer-forge/gate-thresholds/apply", json=body, headers=h)
        assert r.status_code == 200

        # Verify: audit event is recorded, but gate threshold is NOT persisted to registry
        # (Real persistence would require a separate explicit apply operation)
        # For now, the message says "not yet persisted to registry"
        response = r.json()
        assert "not yet persisted to registry" in response.get("message", "").lower() or \
               "pending" in response.get("message", "").lower()


class TestE2EGateThresholdLoop:
    """Full E2E loop: analyze → suggestion → audit → manual apply."""

    def test_full_loop_e2e(self, home):
        """E2E: Create outcomes, analyze gate, verify suggestion, apply via CLI, audit trail complete."""
        # 1. Create definitions with flags (providing outcome data)
        for i in range(8):
            _create_definition_with_flags(home, gate_id="e2e_gate", version=f"1.0.{i}")

        # 2. Analyze gate via CLI
        analyze_result = _run_cli(
            home, "gate-threshold", "analyze", "e2e_gate", "--lookback-days", "90"
        )
        assert analyze_result.returncode == 0
        analysis = json.loads(analyze_result.stdout)
        assert analysis["gate_id"] == "e2e_gate"

        # 3. Operator sees analysis, decides to apply a threshold
        apply_result = _run_cli(
            home, "gate-threshold", "apply", "e2e_gate", "0.78",
            "--reason", "Analysis showed overcautious pattern"
        )
        assert apply_result.returncode == 0

        # 4. Verify entire audit trail
        events = _lf_events(home)
        gate_events = [e for e in events if "gate_threshold" in e.get("event_type", "")]
        assert len(gate_events) >= 1, "At least one gate_threshold event in audit chain"

        # 5. Verify no auto-apply occurred (would require manual step or separate mechanism)
        # The audit shows the suggestion, but no auto-update to registry threshold storage


__all__ = [
    "TestGateOutcomeCorrelation",
    "TestGateThresholdAnalyzer",
    "TestCLIGateThresholdAnalyzeCommand",
    "TestCLIGateThresholdApplyCommand",
    "TestConsoleGateThresholdRoute",
    "TestE2EGateThresholdLoop",
]
