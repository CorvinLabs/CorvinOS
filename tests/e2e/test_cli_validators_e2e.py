"""E2E tests for Click CLI validators — Phase 10 (ADR-0297)

Real Click command execution tests.

Tests:
1. Valid Click command execution
2. Invalid argument causes exit code 1
3. Valid option execution
4. CLI output validation
"""

import pytest
from click.testing import CliRunner
import click
from core.validation.cli_validators import click_validate


@click.command()
@click.argument("user_id")
@click.option("--tenant-id", required=True)
@click_validate(
    arguments={"user_id": "peer_id"},
    tenant_id_option="tenant_id",
)
def update_user_cmd(user_id: str, tenant_id: str):
    """Test command that updates a user."""
    click.echo(f"Updated user {user_id} in tenant {tenant_id}")


@click.command()
@click.argument("flag_id")
@click.option("--enabled", is_flag=True)
@click.option("--tenant-id", required=True)
@click_validate(
    arguments={"flag_id": "flag_id"},
    tenant_id_option="tenant_id",
)
def toggle_flag_cmd(flag_id: str, enabled: bool, tenant_id: str):
    """Test command that toggles a feature flag."""
    status = "enabled" if enabled else "disabled"
    click.echo(f"Flag {flag_id} is {status}")


class TestCliValidatorsE2E:
    """E2E tests for Click CLI validators."""

    def test_valid_argument_passes(self):
        """Valid Click argument passes."""
        runner = CliRunner()
        result = runner.invoke(
            update_user_cmd,
            ["user_123", "--tenant-id", "tenant_1"],
        )
        assert result.exit_code == 0
        assert "Updated user user_123" in result.output

    def test_invalid_argument_exits_with_1(self):
        """Invalid Click argument causes exit code 1."""
        runner = CliRunner()
        result = runner.invoke(
            update_user_cmd,
            ["invalid!!!user", "--tenant-id", "tenant_1"],
        )
        assert result.exit_code == 1
        assert "Error" in result.output or "Invalid" in result.output

    def test_missing_tenant_id_exits_with_1(self):
        """Missing tenant_id causes exit code 1."""
        runner = CliRunner()
        result = runner.invoke(
            update_user_cmd,
            ["user_123"],
        )
        # Click will complain about missing required option
        assert result.exit_code != 0

    def test_valid_flag_command_passes(self):
        """Valid flag command execution passes."""
        runner = CliRunner()
        result = runner.invoke(
            toggle_flag_cmd,
            ["feature_x", "--enabled", "--tenant-id", "tenant_1"],
        )
        assert result.exit_code == 0
        assert "feature_x" in result.output
        assert "enabled" in result.output

    def test_invalid_flag_id_exits_with_1(self):
        """Invalid flag ID causes exit code 1."""
        runner = CliRunner()
        result = runner.invoke(
            toggle_flag_cmd,
            ["Feature_INVALID", "--tenant-id", "tenant_1"],
        )
        assert result.exit_code == 1

    def test_optional_flag_can_be_omitted(self):
        """Optional flags can be omitted."""
        runner = CliRunner()
        result = runner.invoke(
            toggle_flag_cmd,
            ["feature_x", "--tenant-id", "tenant_1"],
        )
        assert result.exit_code == 0
        assert "disabled" in result.output  # Default is disabled

    def test_error_message_helpful(self):
        """Error message is helpful."""
        runner = CliRunner()
        result = runner.invoke(
            update_user_cmd,
            ["invalid!!!user", "--tenant-id", "tenant_1"],
        )
        assert result.exit_code == 1
        # Should mention which argument failed
        assert "Error" in result.output or "Invalid" in result.output
