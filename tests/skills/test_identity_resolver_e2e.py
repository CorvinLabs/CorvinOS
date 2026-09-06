"""
E2E tests for os.identity_resolver Skill via CLI (ADR-0537 Phase 1)

Tests real CLI args → IdentityResolverSkill.execute() → Skill output
"""

import pytest
from unittest.mock import patch
from core.skills.os_skills.identity_resolver_skill import (
    IdentityResolverSkill,
    IdentityResolverInput,
    TransportType,
)


class TestIdentityResolverE2ECLI:
    """E2E test suite: CLI args → IdentityResolverSkill → output"""

    def test_cli_admin_console_operator_e2e(self):
        """E2E: CLI args admin + console_operator → valid output"""
        skill = IdentityResolverSkill()

        input = IdentityResolverInput(
            transport_type=TransportType.CLI.value,
            tenant_id="_default",
            cli_args={"role": "admin", "persona": "console_operator"}
        )

        output = skill.execute(input)

        assert output.role == "admin"
        assert output.persona == "console_operator"
        assert output.tenant_id == "_default"
        assert output.resolved_from == "cli_arg"

    def test_cli_defaults_admin_console_operator_e2e(self):
        """E2E: CLI no args → uses defaults admin + console_operator"""
        skill = IdentityResolverSkill()

        input = IdentityResolverInput(
            transport_type=TransportType.CLI.value,
            tenant_id="_default",
            cli_args={}
        )

        output = skill.execute(input)

        assert output.role == "admin"
        assert output.persona == "console_operator"

    def test_cli_user_voice_user_e2e(self):
        """E2E: CLI user + voice_user → valid pair"""
        skill = IdentityResolverSkill()

        input = IdentityResolverInput(
            transport_type=TransportType.CLI.value,
            tenant_id="_default",
            cli_args={"role": "user", "persona": "voice_user"}
        )

        output = skill.execute(input)

        assert output.role == "user"
        assert output.persona == "voice_user"

    def test_cli_operator_bridge_adapter_e2e(self):
        """E2E: CLI operator + bridge_adapter → valid pair"""
        skill = IdentityResolverSkill()

        input = IdentityResolverInput(
            transport_type=TransportType.CLI.value,
            tenant_id="_default",
            cli_args={"role": "operator", "persona": "bridge_adapter"}
        )

        output = skill.execute(input)

        assert output.role == "operator"
        assert output.persona == "bridge_adapter"

    def test_cli_invalid_pair_fails_e2e(self):
        """E2E: CLI invalid pair (admin + voice_user) → SkillExecutionError"""
        skill = IdentityResolverSkill()

        from core.skills.os_skills.identity_resolver_skill import SkillExecutionError

        input = IdentityResolverInput(
            transport_type=TransportType.CLI.value,
            tenant_id="_default",
            cli_args={"role": "admin", "persona": "voice_user"}  # Invalid pair
        )

        with pytest.raises(SkillExecutionError, match="invalid.*pair"):
            skill.execute(input)

    def test_cli_multi_tenant_e2e(self):
        """E2E: CLI different tenants → separate outputs"""
        skill = IdentityResolverSkill()

        # Tenant A
        input_a = IdentityResolverInput(
            transport_type=TransportType.CLI.value,
            tenant_id="tenant_a",
            cli_args={"role": "admin", "persona": "console_operator"}
        )
        output_a = skill.execute(input_a)

        # Tenant B
        input_b = IdentityResolverInput(
            transport_type=TransportType.CLI.value,
            tenant_id="tenant_b",
            cli_args={"role": "user", "persona": "voice_user"}
        )
        output_b = skill.execute(input_b)

        # Verify tenant isolation
        assert output_a.tenant_id == "tenant_a"
        assert output_b.tenant_id == "tenant_b"
        assert output_a.role == "admin"
        assert output_b.role == "user"

    def test_cli_case_insensitive_e2e(self):
        """E2E: CLI uppercase args → normalized to lowercase"""
        skill = IdentityResolverSkill()

        input = IdentityResolverInput(
            transport_type=TransportType.CLI.value,
            tenant_id="_default",
            cli_args={"role": "ADMIN", "persona": "CONSOLE_OPERATOR"}
        )

        output = skill.execute(input)

        assert output.role == "admin"
        assert output.persona == "console_operator"
