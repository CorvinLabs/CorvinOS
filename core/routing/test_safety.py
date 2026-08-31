"""Tests for route safety validation (ADR-0324)."""

import pytest

from core.routing.safety import RouteDecision, RouteValidator


class TestRouteValidator:
    """Tests for RouteValidator."""

    def test_validate_route_allows_whitelisted_routes(self):
        """Whitelisted routes are allowed."""
        validator = RouteValidator()
        decision = validator.validate_route(
            "/health",
            source="console",
            action="read",
            tenant_id="tenant1",
        )
        assert decision.allowed is True

    def test_validate_route_denies_unknown_routes(self):
        """Unknown routes are denied (fail-closed)."""
        validator = RouteValidator()
        decision = validator.validate_route(
            "/unknown/route",
            source="console",
            action="read",
            tenant_id="tenant1",
        )
        assert decision.allowed is False

    def test_validate_route_requires_valid_tenant_id(self):
        """Invalid tenant_id raises error."""
        validator = RouteValidator()
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            validator.validate_route(
                "/health",
                source="console",
                action="read",
                tenant_id="",
            )

    def test_validate_route_normalizes_trailing_slash(self):
        """Trailing slashes are normalized."""
        validator = RouteValidator(permit_set={"/api/test"})
        decision = validator.validate_route(
            "/api/test/",
            source="console",
            action="read",
            tenant_id="tenant1",
        )
        assert decision.allowed is True

    def test_validate_route_root_path(self):
        """Root path normalization works."""
        validator = RouteValidator(permit_set={"/"})
        decision = validator.validate_route(
            "/",
            source="console",
            action="read",
            tenant_id="tenant1",
        )
        assert decision.allowed is True

    def test_audit_decision_logs_to_audit_trail(self):
        """audit_decision records decision."""
        validator = RouteValidator()
        validator.audit_decision("/test", allowed=True)
        log = validator.get_audit_log()
        assert len(log) > 0
        assert any(d.route == "/test" for d in log)

    def test_get_allowed_routes_returns_permit_set(self):
        """get_allowed_routes returns permit set."""
        validator = RouteValidator(permit_set={"/api/v1", "/api/v2"})
        routes = validator.get_allowed_routes("tenant1")
        assert "/api/v1" in routes
        assert "/api/v2" in routes

    def test_get_allowed_routes_requires_valid_tenant_id(self):
        """Invalid tenant_id raises error."""
        validator = RouteValidator()
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            validator.get_allowed_routes("")

    def test_add_route_to_permit_set(self):
        """add_route adds to permit set."""
        validator = RouteValidator()
        validator.add_route("/new/route", "tenant1")
        routes = validator.get_allowed_routes("tenant1")
        assert "/new/route" in routes

    def test_add_route_normalizes_path(self):
        """add_route normalizes trailing slash."""
        validator = RouteValidator()
        validator.add_route("/new/route/", "tenant1")
        routes = validator.get_allowed_routes("tenant1")
        assert "/new/route" in routes

    def test_remove_route_from_permit_set(self):
        """remove_route removes from permit set."""
        validator = RouteValidator(permit_set={"/removable"})
        validator.remove_route("/removable", "tenant1")
        routes = validator.get_allowed_routes("tenant1")
        assert "/removable" not in routes

    def test_remove_route_cannot_remove_genesis_routes(self):
        """Genesis routes cannot be removed."""
        validator = RouteValidator()
        with pytest.raises(ValueError, match="Cannot remove genesis route"):
            validator.remove_route("/health", "tenant1")

    def test_route_audit_log_tracks_decisions(self):
        """Audit log tracks route decisions."""
        validator = RouteValidator()
        validator.validate_route("/health", source="console", action="read", tenant_id="t1")
        validator.validate_route("/unknown", source="console", action="read", tenant_id="t1")
        log = validator.get_audit_log()
        assert len(log) >= 2

    def test_route_decision_repr(self):
        """RouteDecision has useful repr."""
        decision = RouteDecision(allowed=True, reason="whitelisted", route="/test")
        repr_str = repr(decision)
        assert "/test" in repr_str
        assert "ALLOW" in repr_str

    def test_cross_tenant_isolation_in_get_allowed_routes(self):
        """Different tenants see same permit set (current impl)."""
        validator = RouteValidator(permit_set={"/shared"})
        routes_t1 = validator.get_allowed_routes("tenant1")
        routes_t2 = validator.get_allowed_routes("tenant2")
        # Current implementation: single permit set per validator
        assert "/shared" in routes_t1
        assert "/shared" in routes_t2

    def test_clear_audit_log_for_testing(self):
        """Audit log can be cleared (test cleanup)."""
        validator = RouteValidator()
        validator.audit_decision("/test", allowed=True)
        assert len(validator.get_audit_log()) > 0
        validator.clear_audit_log()
        assert len(validator.get_audit_log()) == 0

    def test_validator_genesis_routes_always_present(self):
        """Genesis routes are always in permit set."""
        validator = RouteValidator()
        routes = validator.get_allowed_routes("tenant1")
        assert "/health" in routes
        assert "/status" in routes
        assert "/init" in routes
        assert "/shutdown" in routes

    def test_add_route_requires_valid_tenant_id(self):
        """add_route validates tenant_id."""
        validator = RouteValidator()
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            validator.add_route("/test", "")

    def test_remove_route_requires_valid_tenant_id(self):
        """remove_route validates tenant_id."""
        validator = RouteValidator()
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            validator.remove_route("/test", "")
