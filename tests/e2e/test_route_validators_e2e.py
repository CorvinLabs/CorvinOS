"""E2E tests for Flask route validators — Phase 10 (ADR-0297)

Real Flask test client integration tests.

Tests:
1. GET with valid path parameter
2. GET with invalid path parameter (400)
3. POST with valid JSON
4. POST with invalid JSON (422)
"""

import pytest
from flask import Flask, request, jsonify
from core.validation.route_validators import validate_input
from core.validation.integration import register_validation_middleware, ValidationTestClient


@pytest.fixture
def app():
    """Create Flask app for E2E testing."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    # Register validation middleware
    register_validation_middleware(app)

    # Define test routes
    @app.route("/api/users/<user_id>")
    @validate_input(
        path_params={"user_id": "peer_id"},
        tenant_id_from="header",
        tenant_id_field="X-Tenant-ID",
    )
    def get_user(user_id: str):
        return jsonify({"user_id": user_id, "status": "ok"}), 200

    @app.route("/api/users", methods=["POST"])
    @validate_input(
        json_schema={"email": "email"},
        tenant_id_from="header",
        tenant_id_field="X-Tenant-ID",
    )
    def create_user():
        data = request.get_json()
        return jsonify({"id": 123, "email": data.get("email")}), 201

    @app.route("/api/flags/<flag_id>")
    @validate_input(
        path_params={"flag_id": "flag_id"},
        tenant_id_from="header",
        tenant_id_field="X-Tenant-ID",
    )
    def get_flag(flag_id: str):
        return jsonify({"flag_id": flag_id, "enabled": True}), 200

    return app


@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()


@pytest.fixture
def test_client(client):
    """Wrap test client with ValidationTestClient."""
    return ValidationTestClient(client)


class TestRouteValidatorsE2E:
    """E2E tests for Flask route validators."""

    def test_get_with_valid_path_parameter(self, test_client):
        """GET with valid path parameter returns 200."""
        data, status = test_client.get_with_validation(
            "/api/users/user_123",
            headers={"X-Tenant-ID": "tenant_1"},
        )
        assert status == 200
        assert data["user_id"] == "user_123"

    def test_get_with_invalid_path_parameter(self, test_client):
        """GET with invalid path parameter returns 400."""
        data, status = test_client.get_with_validation(
            "/api/users/invalid!!!user",
            headers={"X-Tenant-ID": "tenant_1"},
            expect_error=True,
        )
        assert status == 400
        assert "error" in data or "Invalid" in str(data)

    def test_get_without_tenant_id(self, test_client):
        """GET without tenant_id returns 403."""
        data, status = test_client.get_with_validation(
            "/api/users/user_123",
            headers={},
            expect_error=True,
        )
        assert status == 403

    def test_post_with_valid_json(self, test_client):
        """POST with valid JSON returns 201."""
        data, status = test_client.post_with_validation(
            "/api/users",
            json={"email": "test@example.com"},
            headers={"X-Tenant-ID": "tenant_1"},
            expect_status=201,
        )
        assert status == 201
        assert data["email"] == "test@example.com"

    def test_post_with_invalid_json(self, test_client):
        """POST with invalid JSON returns 422."""
        data, status = test_client.post_with_validation(
            "/api/users",
            json={"email": "not_an_email"},
            headers={"X-Tenant-ID": "tenant_1"},
            expect_status=422,
        )
        assert status == 422

    def test_flag_route_with_valid_parameter(self, test_client):
        """Flag route with valid parameter returns 200."""
        data, status = test_client.get_with_validation(
            "/api/flags/feature_x",
            headers={"X-Tenant-ID": "tenant_1"},
        )
        assert status == 200
        assert data["flag_id"] == "feature_x"

    def test_flag_route_with_invalid_parameter(self, test_client):
        """Flag route with invalid parameter returns 400."""
        data, status = test_client.get_with_validation(
            "/api/flags/Feature_INVALID",  # Not lowercase
            headers={"X-Tenant-ID": "tenant_1"},
            expect_error=True,
        )
        assert status == 400
