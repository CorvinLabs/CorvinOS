"""Integration tests for Plugin-Builder v2 orchestration (ADR-0262, ADR-0534, ADR-0613).

Tests the complete workflow:
1. Scaffolding (Phase A)
2. Testing (Phase C)
3. Building (Phase B)
4. Registration (TenantSkillArchitecture)

Covers:
- Tenant isolation (concurrent workflows with different tenant_ids)
- Audit trail generation + hash-chain integrity
- Error recovery (scaffold preserved on build failure)
- E2E wiring proof (all phases traceable via audit)
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from core.plugins.plugin_builder.v2_integration import (
    PluginDeveloper,
    PluginDevelopmentPlan,
    DevelopmentResult,
    develop_plugin,
)



def _fake_scaffold(*, output_dir, plugin_id, **_kw):
    """Stand-in for bootstrap_scaffold with its real return shape: the files it wrote, keyed by
    role. The developer takes the scaffold directory from the written ``plugin.py`` (it used to
    reconstruct a path that never existed), so a mock must write one."""
    scaffold = Path(output_dir) / f"{plugin_id.replace('.', '_')}_scaffold"
    scaffold.mkdir(parents=True, exist_ok=True)
    plugin = scaffold / "plugin.py"
    plugin.write_text("# fake\n")
    return {"plugin": plugin}


class TestPluginDevelopmentPlan:
    """Test PluginDevelopmentPlan initialization and validation."""

    def test_plan_initialization_defaults(self):
        """Test plan initializes with defaults."""
        plan = PluginDevelopmentPlan(
            plugin_id="test.plugin",
            plugin_name="Test Plugin",
        )
        assert plan.plugin_id == "test.plugin"
        assert plan.plugin_name == "Test Plugin"
        assert plan.plugin_type == "data_connector"
        assert plan.steps == ["scaffold", "test", "build"]
        assert plan.audit_enabled is True
        assert plan.register_with_architecture is True
        assert plan.tenant_id is not None  # Set to current_tenant()

    def test_plan_initialization_custom(self):
        """Test plan initializes with custom values."""
        plan = PluginDevelopmentPlan(
            plugin_id="custom.plugin",
            plugin_name="Custom Plugin",
            plugin_type="provider",
            description="Custom description",
            author="test@example.com",
            tenant_id="tenant_1",
            steps=["scaffold"],
            audit_enabled=False,
        )
        assert plan.plugin_id == "custom.plugin"
        assert plan.plugin_type == "provider"
        assert plan.tenant_id == "tenant_1"
        assert plan.audit_enabled is False
        assert plan.steps == ["scaffold"]

    def test_plan_tenant_validation(self):
        """Test plan validates tenant_id."""
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                tenant_id="",  # Empty tenant_id
            )

    def test_plan_step_defaults(self):
        """Test plan defaults steps if None."""
        plan = PluginDevelopmentPlan(
            plugin_id="test.plugin",
            plugin_name="Test",
            steps=None,
        )
        assert plan.steps == ["scaffold", "test", "build"]


class TestDevelopmentResult:
    """Test DevelopmentResult data structure."""

    def test_result_initialization(self):
        """Test result initializes with defaults."""
        result = DevelopmentResult()
        assert result.success is False
        assert result.errors == []
        assert result.warnings == []
        assert result.audit_events == []
        assert result.development_id is not None
        assert result.tenant_id == "_default"

    def test_result_to_dict(self):
        """Test result serialization to dict."""
        result = DevelopmentResult(
            success=True,
            tenant_id="tenant_1",
            phase_completed="build",
            errors=["test error"],
        )
        result_dict = result.to_dict()

        assert result_dict["success"] is True
        assert result_dict["tenant_id"] == "tenant_1"
        assert result_dict["phase_completed"] == "build"
        assert result_dict["errors"] == ["test error"]
        assert "development_id" in result_dict

    def test_result_audit_events_list(self):
        """Test result maintains audit events list."""
        result = DevelopmentResult()
        result.audit_events.append({
            "event_type": "development_started",
            "timestamp": "2026-09-19T12:00:00Z",
        })
        assert len(result.audit_events) == 1
        assert result.audit_events[0]["event_type"] == "development_started"


class TestPluginDeveloper:
    """Test PluginDeveloper orchestration."""

    def test_developer_initialization(self):
        """Test developer initializes with steps."""
        developer = PluginDeveloper()
        assert "scaffold" in developer.steps
        assert "test" in developer.steps
        assert "build" in developer.steps
        assert "register" in developer.steps

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_develop_scaffold_only(self, mock_scaffold):
        """Test develop() with scaffold-only step."""
        mock_scaffold.side_effect = _fake_scaffold

        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                tenant_id="_default",
                steps=["scaffold"],
                audit_enabled=False,
            )
            result = developer.develop(plan, tmpdir)

            assert result.success is True
            assert result.scaffold_dir is not None
            assert result.scaffold_dir.exists()
            mock_scaffold.assert_called_once()

    def test_develop_unknown_step(self):
        """Test develop() with unknown step."""
        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                steps=["unknown_step"],
                audit_enabled=False,
            )
            result = developer.develop(plan, tmpdir)

            assert result.success is False
            assert any("Unknown step" in e for e in result.errors)

    def test_develop_step_failure(self):
        """Test develop() stops on step failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                steps=["scaffold", "test"],
                audit_enabled=False,
            )

            # Mock scaffold to create dummy dir
            with patch(
                "core.plugins.plugin_builder.v2_integration.bootstrap_scaffold",
                return_value=["file.py"],
            ):
                # Mock test to fail
                with patch(
                    "core.plugins.plugin_builder.v2_integration.PluginTestRunner"
                ) as mock_runner:
                    mock_runner.return_value.run_tests.return_value.is_success.return_value = False
                    mock_runner.return_value.run_tests.return_value.failed = 1
                    mock_runner.return_value.run_tests.return_value.exit_code = 1

                    result = developer.develop(plan, tmpdir)

                    # Should succeed on scaffold, fail on test
                    assert result.success is False
                    assert result.phase_completed == "scaffold"
                    assert any("failed" in e.lower() for e in result.errors)

    def test_develop_elapsed_time(self):
        """Test develop() tracks elapsed time."""
        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                steps=[],
                audit_enabled=False,
            )

            start = time.time()
            result = developer.develop(plan, tmpdir)
            elapsed = time.time() - start

            assert result.elapsed_seconds > 0
            assert result.elapsed_seconds <= elapsed + 1  # Allow 1s tolerance


def _stage_records(tenant_id: str) -> list[dict]:
    """The plugin_builder.development records on THIS tenant's core chain (not a mock)."""
    chain = _chain_path(tenant_id)
    if not chain.exists():
        return []
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    return [r for r in recs if r.get("event_type") == "plugin_builder.development"
            and (r.get("details") or {}).get("tenant_id") == tenant_id]


def _chain_path(tenant_id: str) -> Path:
    """The file the sandboxed core writer appends to (the test harness points VOICE_AUDIT_PATH at
    the sandbox's chain; outside a harness it is the tenant's chain)."""
    from core.paths import tenant as tenant_paths

    override = os.environ.get("VOICE_AUDIT_PATH")
    return Path(override) if override else tenant_paths.tenant_audit_chain(tenant_id)


@pytest.fixture
def chain_tenant(monkeypatch):
    """The process tenant decides whose chain may be written (a cross-tenant write is refused),
    so a development run for tenant X is audited from a process that IS tenant X."""
    def _use(tenant_id: str) -> str:
        monkeypatch.setenv("CORVIN_TENANT_ID", tenant_id)
        return tenant_id
    return _use


class TestAuditIntegration:
    """Audit goes through the core writer onto the tenant chain (ADR-0654), content-free."""

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_audit_events_emitted(self, mock_scaffold, chain_tenant):
        mock_scaffold.side_effect = _fake_scaffold
        tenant = chain_tenant("tenant_1")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = PluginDeveloper().develop(
                PluginDevelopmentPlan(plugin_id="test.plugin", plugin_name="Test", tenant_id=tenant,
                                      steps=["scaffold"], audit_enabled=True), tmpdir)

        # development_started, phase_scaffold_started, phase_scaffold_completed, development_completed
        stages = [e["event_type"] for e in result.audit_events]
        assert stages == ["development_started", "phase_scaffold_started",
                          "phase_scaffold_completed", "development_completed"]
        on_chain = [r["details"]["stage"] for r in _stage_records(tenant)]
        assert on_chain == stages  # what the result reports is what the chain holds

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_audit_records_are_content_free_and_the_chain_still_verifies(self, mock_scaffold, chain_tenant):
        from corvin_operator.forge.forge import security_events as se

        mock_scaffold.side_effect = _fake_scaffold
        tenant = chain_tenant("_default")
        with tempfile.TemporaryDirectory() as tmpdir:
            PluginDeveloper().develop(
                PluginDevelopmentPlan(plugin_id="test.plugin", plugin_name="Test", tenant_id=tenant,
                                      description="SENTINEL_DESCRIPTION", author="SENTINEL_AUTHOR",
                                      steps=["scaffold"], audit_enabled=True), tmpdir)
        chain = _chain_path(tenant)
        text = chain.read_text()
        assert "plugin_builder.development" in text
        assert "SENTINEL_DESCRIPTION" not in text and "SENTINEL_AUTHOR" not in text
        # The previous implementation appended its own sha256 line: not a record the writer
        # produced, so the MAC check failed. A writer-produced record leaves the chain intact.
        assert se.verify_chain(chain)[0] is True

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_audit_events_include_tenant_id(self, mock_scaffold, chain_tenant):
        mock_scaffold.side_effect = _fake_scaffold
        tenant = chain_tenant("tenant_2")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = PluginDeveloper().develop(
                PluginDevelopmentPlan(plugin_id="test.plugin", plugin_name="Test", tenant_id=tenant,
                                      steps=["scaffold"], audit_enabled=True), tmpdir)
        assert result.audit_events and all(e["tenant_id"] == tenant for e in result.audit_events)
        assert all(r["details"]["tenant_id"] == tenant for r in _stage_records(tenant))

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_a_plan_for_another_tenant_is_not_written_to_this_chain(self, mock_scaffold, chain_tenant):
        """Cross-tenant isolation: the core writer refuses a record for a tenant other than the
        process's. The run still completes; the refusal is a warning, never a forged record."""
        mock_scaffold.side_effect = _fake_scaffold
        chain_tenant("_default")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = PluginDeveloper().develop(
                PluginDevelopmentPlan(plugin_id="test.plugin", plugin_name="Test", tenant_id="tenant_9",
                                      steps=["scaffold"], audit_enabled=True), tmpdir)
        assert result.success is True and result.audit_events == []
        assert any("not committed" in w or "unavailable" in w for w in result.warnings)
        assert _stage_records("tenant_9") == []


class TestTenantIsolation:
    """Test tenant isolation in concurrent workflows."""

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_concurrent_workflows_isolation(self, mock_scaffold):
        """Test concurrent workflows with different tenant_ids don't cross-contaminate."""
        mock_scaffold.side_effect = _fake_scaffold

        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()

            # Run two workflows concurrently
            plan_1 = PluginDevelopmentPlan(
                plugin_id="test.plugin1",
                plugin_name="Test 1",
                tenant_id="tenant_1",
                steps=["scaffold"],
                audit_enabled=False,
            )

            plan_2 = PluginDevelopmentPlan(
                plugin_id="test.plugin2",
                plugin_name="Test 2",
                tenant_id="tenant_2",
                steps=["scaffold"],
                audit_enabled=False,
            )

            result_1 = developer.develop(plan_1, tmpdir)
            result_2 = developer.develop(plan_2, tmpdir)

            # Results should be independent
            assert result_1.tenant_id == "tenant_1"
            assert result_2.tenant_id == "tenant_2"
            assert result_1.development_id != result_2.development_id
            assert result_1.scaffold_dir != result_2.scaffold_dir

    def test_result_tenant_id_preserved(self):
        """Test result preserves tenant_id from plan."""
        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                tenant_id="my_tenant",
                steps=[],
                audit_enabled=False,
            )

            result = developer.develop(plan, tmpdir)

            assert result.tenant_id == "my_tenant"


class TestErrorRecovery:
    """Test error handling and artifact preservation."""

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_scaffold_preserved_on_test_failure(self, mock_scaffold):
        """Test scaffold is preserved when test fails."""
        mock_scaffold.side_effect = _fake_scaffold

        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                steps=["scaffold", "test"],
                audit_enabled=False,
            )

            with patch(
                "core.plugins.plugin_builder.v2_integration.PluginTestRunner"
            ) as mock_runner:
                mock_runner.return_value.run_tests.return_value.is_success.return_value = False
                mock_runner.return_value.run_tests.return_value.failed = 1
                mock_runner.return_value.run_tests.return_value.exit_code = 1

                result = developer.develop(plan, tmpdir)

                # Test failed but scaffold should still exist
                assert not result.success
                assert result.scaffold_dir is not None
                assert result.scaffold_dir.exists()

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_scaffold_preserved_on_build_failure(self, mock_scaffold):
        """Test scaffold is preserved when build fails."""
        mock_scaffold.side_effect = _fake_scaffold

        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                steps=["scaffold", "build"],
                audit_enabled=False,
            )

            with patch(
                "core.plugins.plugin_builder.v2_integration.validate_plugin_structure"
            ):
                with patch(
                    "core.plugins.plugin_builder.v2_integration.PluginTestRunner"
                ):
                    with patch(
                        "core.plugins.plugin_builder.v2_integration.PackageBuilder"
                    ) as mock_builder:
                        mock_builder.return_value.build.return_value.success = False
                        mock_builder.return_value.build.return_value.errors = ["Build error"]

                        result = developer.develop(plan, tmpdir)

                        # Build failed but scaffold should still exist
                        assert not result.success
                        assert result.scaffold_dir is not None
                        assert result.scaffold_dir.exists()

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_audit_events_on_failure(self, mock_scaffold, chain_tenant):
        """Events are recorded even when a step fails."""
        mock_scaffold.side_effect = _fake_scaffold
        tenant = chain_tenant("_default")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = PluginDeveloper().develop(
                PluginDevelopmentPlan(plugin_id="test.plugin", plugin_name="Test", tenant_id=tenant,
                                      steps=["scaffold", "unknown_step"], audit_enabled=True), tmpdir)
        stages = [e["event_type"] for e in result.audit_events]
        assert len(stages) >= 2
        assert "development_failed" in stages or "development_completed" in stages


class TestDevelopPluginFunction:
    """Test the top-level develop_plugin() convenience function."""

    @patch("core.plugins.plugin_builder.v2_integration.PluginDeveloper.develop")
    def test_develop_plugin_calls_developer(self, mock_develop):
        """Test develop_plugin() delegates to PluginDeveloper."""
        mock_develop.return_value = DevelopmentResult(success=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = develop_plugin(
                plugin_id="test.plugin",
                plugin_name="Test",
                output_dir=tmpdir,
                tenant_id="tenant_1",
            )

            assert result.success is True
            mock_develop.assert_called_once()

    def test_develop_plugin_with_defaults(self):
        """Test develop_plugin() works with minimal arguments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "core.plugins.plugin_builder.v2_integration.PluginDeveloper.develop"
            ) as mock_develop:
                mock_develop.return_value = DevelopmentResult(success=True)

                result = develop_plugin(
                    plugin_id="test.plugin",
                    plugin_name="Test",
                    output_dir=tmpdir,
                )

                assert result.success is True


class TestE2EWiringProof:
    """Developer -> scaffold phase -> core audit writer -> tenant chain."""

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_e2e_scaffold_phase_wired(self, mock_scaffold, chain_tenant):
        mock_scaffold.side_effect = _fake_scaffold
        tenant = chain_tenant("tenant_1")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = develop_plugin(plugin_id="test.plugin", plugin_name="Test", output_dir=tmpdir,
                                    tenant_id=tenant, steps=["scaffold"], audit_enabled=True)
            mock_scaffold.assert_called_once()
            assert result.success is True and result.scaffold_dir is not None
        assert len(_stage_records(tenant)) >= 3  # started, phase_started, phase_completed (+ completed)

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_e2e_audit_chain_events_have_ids(self, mock_scaffold, chain_tenant):
        mock_scaffold.side_effect = _fake_scaffold
        tenant = chain_tenant("tenant_1")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = develop_plugin(plugin_id="test.plugin", plugin_name="Test", output_dir=tmpdir,
                                    tenant_id=tenant, steps=["scaffold"], audit_enabled=True)
        records = _stage_records(tenant)
        assert records
        for r in records:
            assert r["details"]["development_id"] == result.development_id
            assert r["details"]["tenant_id"] == tenant
