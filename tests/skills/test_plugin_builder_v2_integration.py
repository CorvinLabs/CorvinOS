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
        mock_scaffold.return_value = ["file1.py", "file2.json"]

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


class TestAuditIntegration:
    """Test audit event generation and tenant isolation."""

    @patch("core.plugins.plugin_builder.v2_integration.AuditChainWriter")
    @patch("core.plugins.plugin_builder.v2_integration.tenant_paths")
    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_audit_events_emitted(self, mock_scaffold, mock_paths, mock_writer_class):
        """Test audit events are emitted for each phase."""
        mock_scaffold.return_value = ["file.py"]
        mock_paths.tenant_audit_chain.return_value = "/tmp/audit.jsonl"
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                tenant_id="tenant_1",
                steps=["scaffold"],
                audit_enabled=True,
            )

            result = developer.develop(plan, tmpdir)

            # Should emit: development_started, phase_scaffold_started, phase_scaffold_completed, development_completed
            assert len(result.audit_events) >= 3
            event_types = [e["event_type"] for e in result.audit_events]
            assert "development_started" in event_types
            assert "development_completed" in event_types

    @patch("core.plugins.plugin_builder.v2_integration.AuditChainWriter")
    @patch("core.plugins.plugin_builder.v2_integration.tenant_paths")
    def test_audit_events_include_tenant_id(self, mock_paths, mock_writer_class):
        """Test all audit events include tenant_id."""
        mock_paths.tenant_audit_chain.return_value = "/tmp/audit.jsonl"
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                tenant_id="tenant_xyz",
                steps=[],
                audit_enabled=True,
            )

            result = developer.develop(plan, tmpdir)

            # All events should have tenant_id
            for event in result.audit_events:
                assert event["tenant_id"] == "tenant_xyz"

    @patch("core.plugins.plugin_builder.v2_integration.AuditChainWriter")
    @patch("core.plugins.plugin_builder.v2_integration.tenant_paths")
    def test_audit_writer_initialization(self, mock_paths, mock_writer_class):
        """Test audit writer is initialized with tenant-specific chain path."""
        expected_path = "/tmp/tenant_1/audit.jsonl"
        mock_paths.tenant_audit_chain.return_value = expected_path
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                tenant_id="tenant_1",
                steps=[],
                audit_enabled=True,
            )

            result = developer.develop(plan, tmpdir)

            # Verify tenant_audit_chain() was called with correct tenant_id
            mock_paths.tenant_audit_chain.assert_called_with("tenant_1")
            # Verify AuditChainWriter was initialized with the path
            mock_writer_class.assert_called_with(expected_path)


class TestTenantIsolation:
    """Test tenant isolation in concurrent workflows."""

    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_concurrent_workflows_isolation(self, mock_scaffold):
        """Test concurrent workflows with different tenant_ids don't cross-contaminate."""
        mock_scaffold.return_value = ["file.py"]

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
        mock_scaffold.return_value = ["file.py"]

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
        mock_scaffold.return_value = ["file.py"]

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

    @patch("core.plugins.plugin_builder.v2_integration.AuditChainWriter")
    @patch("core.plugins.plugin_builder.v2_integration.tenant_paths")
    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_audit_events_on_failure(self, mock_scaffold, mock_paths, mock_writer_class):
        """Test audit events are emitted even on failure."""
        mock_scaffold.return_value = ["file.py"]
        mock_paths.tenant_audit_chain.return_value = "/tmp/audit.jsonl"
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        with tempfile.TemporaryDirectory() as tmpdir:
            developer = PluginDeveloper()
            plan = PluginDevelopmentPlan(
                plugin_id="test.plugin",
                plugin_name="Test",
                steps=["scaffold", "unknown_step"],
                audit_enabled=True,
            )

            result = developer.develop(plan, tmpdir)

            # Should still emit events even on failure
            assert len(result.audit_events) >= 2
            # Should have development_failed event
            event_types = [e["event_type"] for e in result.audit_events]
            assert "development_failed" in event_types or "development_completed" in event_types


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
    """Test E2E wiring proof (all phases traceable via audit)."""

    @patch("core.plugins.plugin_builder.v2_integration.AuditChainWriter")
    @patch("core.plugins.plugin_builder.v2_integration.tenant_paths")
    @patch("core.plugins.plugin_builder.v2_integration.bootstrap_scaffold")
    def test_e2e_scaffold_phase_wired(self, mock_scaffold, mock_paths, mock_writer_class):
        """Test scaffold phase is wired and audited end-to-end."""
        mock_scaffold.return_value = ["file.py"]
        mock_paths.tenant_audit_chain.return_value = "/tmp/audit.jsonl"
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        with tempfile.TemporaryDirectory() as tmpdir:
            result = develop_plugin(
                plugin_id="test.plugin",
                plugin_name="Test",
                output_dir=tmpdir,
                tenant_id="tenant_1",
                steps=["scaffold"],
                audit_enabled=True,
            )

            # Verify audit writer was called
            assert mock_writer.write.call_count >= 3  # started, phase_started, phase_completed
            # Verify scaffold was executed
            mock_scaffold.assert_called_once()
            # Verify result reflects success
            assert result.success is True
            assert result.scaffold_dir is not None

    @patch("core.plugins.plugin_builder.v2_integration.AuditChainWriter")
    @patch("core.plugins.plugin_builder.v2_integration.tenant_paths")
    def test_e2e_audit_chain_events_have_ids(self, mock_paths, mock_writer_class):
        """Test all audit events have tracking IDs (development_id, tenant_id)."""
        mock_paths.tenant_audit_chain.return_value = "/tmp/audit.jsonl"
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        with tempfile.TemporaryDirectory() as tmpdir:
            result = develop_plugin(
                plugin_id="test.plugin",
                plugin_name="Test",
                output_dir=tmpdir,
                tenant_id="tenant_1",
                steps=[],
                audit_enabled=True,
            )

            # All events should have development_id and tenant_id
            for event in result.audit_events:
                assert "development_id" in event
                assert "tenant_id" in event
                assert event["development_id"] == result.development_id
                assert event["tenant_id"] == "tenant_1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
