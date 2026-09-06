"""
Unit tests for os.identity_resolver Skill (ADR-0537 Phase 1)

Tests:
- Flask header parsing (X-Role, X-Persona)
- CLI arg parsing (--role, --persona)
- Role+persona pair validation (privilege escalation prevention)
- Deny-by-default on invalid values
- Deny-by-default on null tenant_id (fail-closed)
"""

import pytest
from core.skills.os_skills.identity_resolver_skill import (
    IdentityResolverSkill,
    IdentityResolverInput,
    IdentityResolverOutput,
    SkillExecutionError,
    TransportType,
    VALID_PAIRS,
)


class TestIdentityResolverSkill:
    """Test suite for os.identity_resolver Skill."""

    def test_flask_resolve_admin_console_operator(self):
        """Flask resolution: admin + console_operator (valid pair)."""
        skill = IdentityResolverSkill()
        input = IdentityResolverInput(
            transport_type=TransportType.FLASK_HTTP.value,
            tenant_id="_default",
            headers={"X-Role": "admin", "X-Persona": "console_operator"}
        )

        output = skill.execute(input)

        assert output.role == "admin"
        assert output.persona == "console_operator"
        assert output.resolved_from == "flask_header"

    def test_flask_resolve_defaults(self):
        """Flask resolution with missing headers (uses defaults: user, mcp_tool)."""
        skill = IdentityResolverSkill()
        input = IdentityResolverInput(
            transport_type=TransportType.FLASK_HTTP.value,
            tenant_id="_default",
            headers={}  # No headers provided
        )

        output = skill.execute(input)

        assert output.role == "user"
        assert output.persona == "mcp_tool"

    def test_flask_resolve_case_insensitive(self):
        """Flask resolution should be case-insensitive."""
        skill = IdentityResolverSkill()
        input = IdentityResolverInput(
            transport_type=TransportType.FLASK_HTTP.value,
            tenant_id="_default",
            headers={"X-Role": "ADMIN", "X-Persona": "CONSOLE_OPERATOR"}
        )

        output = skill.execute(input)

        assert output.role == "admin"
        assert output.persona == "console_operator"

    def test_cli_resolve_admin_console_operator(self):
        """CLI resolution: admin + console_operator (valid pair)."""
        skill = IdentityResolverSkill()
        input = IdentityResolverInput(
            transport_type=TransportType.CLI.value,
            tenant_id="_default",
            cli_args={"role": "admin", "persona": "console_operator"}
        )

        output = skill.execute(input)

        assert output.role == "admin"
        assert output.persona == "console_operator"
        assert output.resolved_from == "cli_arg"

    def test_cli_resolve_defaults(self):
        """CLI resolution with missing args (uses defaults: admin, console_operator)."""
        skill = IdentityResolverSkill()
        input = IdentityResolverInput(
            transport_type=TransportType.CLI.value,
            tenant_id="_default",
            cli_args={}  # No args provided
        )

        output = skill.execute(input)

        assert output.role == "admin"
        assert output.persona == "console_operator"

    def test_flask_invalid_role(self):
        """Flask with invalid role should raise SkillExecutionError."""
        skill = IdentityResolverSkill()
        input = IdentityResolverInput(
            transport_type=TransportType.FLASK_HTTP.value,
            tenant_id="_default",
            headers={"X-Role": "superadmin", "X-Persona": "console_operator"}
        )

        with pytest.raises(SkillExecutionError, match="invalid role"):
            skill.execute(input)

    def test_flask_invalid_persona(self):
        """Flask with invalid persona should raise SkillExecutionError."""
        skill = IdentityResolverSkill()
        input = IdentityResolverInput(
            transport_type=TransportType.FLASK_HTTP.value,
            tenant_id="_default",
            headers={"X-Role": "admin", "X-Persona": "superadmin"}
        )

        with pytest.raises(SkillExecutionError, match="invalid persona"):
            skill.execute(input)

    def test_flask_invalid_pair_privilege_escalation(self):
        """Flask with invalid (role, persona) pair should raise SkillExecutionError.

        This test specifically checks that admin+voice_user is rejected
        (privilege escalation prevention).
        """
        skill = IdentityResolverSkill()
        input = IdentityResolverInput(
            transport_type=TransportType.FLASK_HTTP.value,
            tenant_id="_default",
            headers={"X-Role": "admin", "X-Persona": "voice_user"}  # Invalid pair
        )

        with pytest.raises(SkillExecutionError, match="invalid.*pair"):
            skill.execute(input)

    def test_deny_by_default_null_tenant_id(self):
        """Null tenant_id should raise SkillExecutionError (fail-closed)."""
        skill = IdentityResolverSkill()
        input = IdentityResolverInput(
            transport_type=TransportType.FLASK_HTTP.value,
            tenant_id=None,  # type: ignore
            headers={"X-Role": "admin", "X-Persona": "console_operator"}
        )

        with pytest.raises(SkillExecutionError, match="tenant_id required"):
            skill.execute(input)

    def test_unknown_transport_type(self):
        """Unknown transport type should raise SkillExecutionError."""
        skill = IdentityResolverSkill()
        input = IdentityResolverInput(
            transport_type="unknown_transport",
            tenant_id="_default",
            headers={}
        )

        with pytest.raises(SkillExecutionError, match="unknown transport"):
            skill.execute(input)


class TestValidPairs:
    """Test validity of role+persona pairs."""

    def test_all_valid_pairs_are_accepted(self):
        """All pairs in VALID_PAIRS should be accepted."""
        skill = IdentityResolverSkill()

        for role, persona in VALID_PAIRS:
            input = IdentityResolverInput(
                transport_type=TransportType.FLASK_HTTP.value,
                tenant_id="_default",
                headers={"X-Role": role, "X-Persona": persona}
            )

            output = skill.execute(input)
            assert output.role == role
            assert output.persona == persona

    def test_invalid_pairs_are_rejected(self):
        """Invalid combinations should be rejected."""
        skill = IdentityResolverSkill()

        invalid_pairs = [
            ("admin", "voice_user"),  # Admin can't be voice_user
            ("user", "console_operator"),  # User can't be console_operator
            ("operator", "voice_user"),  # Operator can't be voice_user
        ]

        for role, persona in invalid_pairs:
            input = IdentityResolverInput(
                transport_type=TransportType.FLASK_HTTP.value,
                tenant_id="_default",
                headers={"X-Role": role, "X-Persona": persona}
            )

            with pytest.raises(SkillExecutionError, match="invalid.*pair"):
                skill.execute(input)
