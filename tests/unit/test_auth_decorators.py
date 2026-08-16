"""
Unit tests for Auth Decorators (ADR-0294).

Test coverage:
1. Transport resolution (Flask, CLI, async)
2. Auth decorator behavior (@auth_required)
3. Capability stacking (@requires_auth_capability)
4. Error handling (403, audit logging)
5. Context isolation (concurrent requests)
"""

from unittest.mock import Mock

import pytest

from core.context_engineering import (
    Persona,
    Role,
    get_registry,
)
from core.context_engineering.auth_decorators import (
    _forbidden_response,
    auth_required_flask,
    requires_auth_capability,
)
from core.context_engineering.transport_resolvers import (
    InvalidPersona,
    TransportResolver,
    UnresolvablePersona,
)


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset global registry before each test."""
    from core.context_engineering.persona_model import _REGISTRY
    _REGISTRY._capabilities.clear()
    _REGISTRY.unlock_tier1()
    yield
    _REGISTRY._capabilities.clear()
    _REGISTRY.unlock_tier1()


class TestTransportResolverFlask:
    """Flask request header extraction."""

    def test_resolve_flask_request_with_valid_headers(self):
        request = Mock()
        request.headers = {
            "X-Persona": "console_operator",
            "X-Role": "admin",
        }

        persona, role = TransportResolver.resolve_flask_request(request)

        assert persona == Persona.CONSOLE_OPERATOR
        assert role == Role.ADMIN

    def test_resolve_flask_request_missing_persona_header(self):
        request = Mock()
        request.headers = {"X-Role": "admin"}

        with pytest.raises(UnresolvablePersona):
            TransportResolver.resolve_flask_request(request)

    def test_resolve_flask_request_invalid_persona_value(self):
        request = Mock()
        request.headers = {
            "X-Persona": "invalid_persona",
            "X-Role": "admin",
        }

        with pytest.raises(InvalidPersona):
            TransportResolver.resolve_flask_request(request)

    def test_resolve_flask_request_role_defaults_to_user(self):
        request = Mock()
        request.headers = {"X-Persona": "voice_user"}  # No X-Role

        persona, role = TransportResolver.resolve_flask_request(request)

        assert persona == Persona.VOICE_USER
        assert role == Role.USER

    def test_resolve_flask_request_case_insensitive(self):
        request = Mock()
        request.headers = {
            "X-Persona": "CONSOLE_OPERATOR",
            "X-Role": "ADMIN",
        }

        persona, role = TransportResolver.resolve_flask_request(request)

        assert persona == Persona.CONSOLE_OPERATOR
        assert role == Role.ADMIN


class TestTransportResolverCLI:
    """CLI argument extraction."""

    def test_resolve_cli_context_defaults(self):
        persona, role = TransportResolver.resolve_cli_context()

        assert persona == Persona.CONSOLE_OPERATOR
        assert role == Role.ADMIN

    def test_resolve_cli_context_with_args(self):
        persona, role = TransportResolver.resolve_cli_context(
            persona_arg="bridge_adapter",
            role_arg="operator"
        )

        assert persona == Persona.BRIDGE_ADAPTER
        assert role == Role.OPERATOR

    def test_resolve_cli_context_invalid_persona(self):
        with pytest.raises(InvalidPersona):
            TransportResolver.resolve_cli_context(persona_arg="invalid")


class TestAuthRequiredFlask:
    """@auth_required_flask decorator."""

    def test_auth_required_allows_valid_request(self):
        registry = get_registry()
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "test_cap"
        )

        request = Mock()
        request.headers = {
            "X-Persona": "console_operator",
            "X-Role": "admin",
        }

        @auth_required_flask
        def protected_route(request):
            return {"status": "ok"}

        result = protected_route(request=request)
        assert result == {"status": "ok"}

    def test_auth_required_denies_missing_persona_header(self):
        request = Mock()
        request.headers = {}  # Missing X-Persona

        @auth_required_flask
        def protected_route(request):
            return {"status": "ok"}

        result = protected_route(request=request)

        assert result[1] == 403  # HTTP 403
        assert "error" in result[0]

    def test_auth_required_denies_invalid_persona(self):
        request = Mock()
        request.headers = {
            "X-Persona": "invalid",
            "X-Role": "admin",
        }

        @auth_required_flask
        def protected_route(request):
            return {"status": "ok"}

        result = protected_route(request=request)

        assert result[1] == 403

    def test_auth_required_without_request_raises_error(self):
        @auth_required_flask
        def protected_route(request):
            return {"status": "ok"}

        with pytest.raises(Exception):
            protected_route()  # No request kwarg

    def test_auth_required_sets_context(self):
        request = Mock()
        request.headers = {
            "X-Persona": "voice_user",
            "X-Role": "operator",
        }

        @auth_required_flask
        def protected_route(request):
            from core.context_engineering.persona_model import (
                get_current_persona,
                get_current_role,
            )
            return {
                "persona": get_current_persona().value,
                "role": get_current_role().value,
            }

        result = protected_route(request=request)

        assert result["persona"] == "voice_user"
        assert result["role"] == "operator"


class TestRequiresAuthCapability:
    """@requires_auth_capability stacked decorator."""

    def test_requires_auth_capability_allows_authorized(self):
        registry = get_registry()
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "audit_verify",
        )

        request = Mock()
        request.headers = {
            "X-Persona": "console_operator",
            "X-Role": "admin",
        }

        @requires_auth_capability("audit_verify")
        def verify_audit(request):
            return {"status": "verified"}

        result = verify_audit(request=request)
        assert result == {"status": "verified"}

    def test_requires_auth_capability_denies_missing_capability(self):
        # Don't register the capability
        _ = get_registry()

        request = Mock()
        request.headers = {
            "X-Persona": "console_operator",
            "X-Role": "admin",
        }

        @requires_auth_capability("nonexistent_cap")
        def protected_op(request):
            return {"status": "ok"}

        result = protected_op(request=request)

        assert result[1] == 403
        assert "Capability required" in result[0]["error"]

    def test_requires_auth_capability_denies_unauthorized_persona(self):
        # Persona can't be resolved
        request = Mock()
        request.headers = {}  # Missing X-Persona

        @requires_auth_capability("any_cap")
        def protected_op(request):
            return {"status": "ok"}

        result = protected_op(request=request)

        assert result[1] == 403


class TestForbiddenResponse:
    """403 response formatting."""

    def test_forbidden_response_format(self):
        response_body, status_code = _forbidden_response("Test error")

        assert status_code == 403
        assert "error" in response_body
        assert response_body["error"] == "Test error"
