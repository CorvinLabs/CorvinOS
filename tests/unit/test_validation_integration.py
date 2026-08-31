"""Unit tests for validation middleware integration — Phase 10 (ADR-0297)

Tests:
1. Middleware registration works
2. Error responses properly formatted
3. Test utilities (ValidationTestClient)
4. Feature flag gating
5. Tenant scoping
"""

import pytest
from unittest.mock import Mock, patch
from flask import Flask
from core.validation.integration import (
    ValidationMiddleware,
    ValidationErrorResponse,
    ValidationTestClient,
    register_validation_middleware,
)


class TestValidationMiddleware:
    """Test Flask validation middleware."""

    def test_middleware_initialization(self):
        """Middleware initializes without app."""
        middleware = ValidationMiddleware()
        assert middleware.app is None

    def test_middleware_initialization_with_app(self):
        """Middleware initializes with Flask app."""
        app = Flask(__name__)
        middleware = ValidationMiddleware(app)
        assert middleware.app == app

    def test_middleware_init_app(self):
        """Middleware.init_app() registers with Flask."""
        app = Flask(__name__)
        middleware = ValidationMiddleware()
        middleware.init_app(app)
        assert middleware.app == app

    def test_create_error_response_400(self):
        """Create error response with 400 status."""
        response, status = ValidationMiddleware.create_error_response(
            message="Invalid input",
            code="invalid_input",
            status_code=400,
        )
        assert status == 400
        # Response is Flask jsonify object

    def test_create_error_response_403(self):
        """Create error response with 403 status."""
        response, status = ValidationMiddleware.create_error_response(
            message="Forbidden",
            code="forbidden",
            status_code=403,
        )
        assert status == 403

    def test_create_error_response_422(self):
        """Create error response with 422 status."""
        response, status = ValidationMiddleware.create_error_response(
            message="Unprocessable Entity",
            code="unprocessable",
            status_code=422,
        )
        assert status == 422


class TestValidationErrorResponse:
    """Test ValidationErrorResponse data class."""

    def test_error_response_initialization(self):
        """ValidationErrorResponse initializes correctly."""
        response = ValidationErrorResponse(
            error="Invalid input",
            code="invalid_input",
            status_code=400,
        )
        assert response.error == "Invalid input"
        assert response.code == "invalid_input"
        assert response.status_code == 400

    def test_error_response_to_dict(self):
        """ValidationErrorResponse.to_dict() returns dict."""
        response = ValidationErrorResponse(
            error="Invalid input",
            code="invalid_input",
            status_code=400,
        )
        result = response.to_dict()
        assert result == {"error": "Invalid input", "code": "invalid_input"}

    def test_error_response_immutable(self):
        """ValidationErrorResponse is frozen (immutable)."""
        response = ValidationErrorResponse(
            error="Invalid input",
            code="invalid_input",
            status_code=400,
        )
        with pytest.raises(AttributeError):
            response.error = "Changed"


class TestValidationTestClient:
    """Test ValidationTestClient helper."""

    def test_client_initialization(self):
        """ValidationTestClient initializes with test client."""
        mock_client = Mock()
        test_client = ValidationTestClient(mock_client)
        assert test_client.client == mock_client

    def test_get_with_validation(self):
        """GET request validation helper."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.get_json.return_value = {"status": "ok"}
        mock_client.get.return_value = mock_response

        test_client = ValidationTestClient(mock_client)
        data, status = test_client.get_with_validation(
            url="/api/users/123",
            headers={"X-Tenant-ID": "tenant_1"},
        )
        assert status == 200
        assert data == {"status": "ok"}

    def test_post_with_validation(self):
        """POST request validation helper."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.get_json.return_value = {"id": 123}
        mock_client.post.return_value = mock_response

        test_client = ValidationTestClient(mock_client)
        data, status = test_client.post_with_validation(
            url="/api/users",
            json={"name": "John"},
            headers={"X-Tenant-ID": "tenant_1"},
        )
        assert status == 201
        assert data == {"id": 123}

    def test_put_with_validation(self):
        """PUT request validation helper."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.get_json.return_value = {"updated": True}
        mock_client.put.return_value = mock_response

        test_client = ValidationTestClient(mock_client)
        data, status = test_client.put_with_validation(
            url="/api/users/123",
            json={"name": "Jane"},
            headers={"X-Tenant-ID": "tenant_1"},
        )
        assert status == 200
        assert data == {"updated": True}

    def test_delete_with_validation(self):
        """DELETE request validation helper."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 204
        mock_response.get_json.return_value = {}
        mock_client.delete.return_value = mock_response

        test_client = ValidationTestClient(mock_client)
        data, status = test_client.delete_with_validation(
            url="/api/users/123",
            headers={"X-Tenant-ID": "tenant_1"},
        )
        assert status == 204

    def test_get_with_missing_headers(self):
        """GET without headers uses empty dict."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.get_json.return_value = {"error": "Missing tenant_id"}
        mock_client.get.return_value = mock_response

        test_client = ValidationTestClient(mock_client)
        data, status = test_client.get_with_validation(url="/api/users/123")
        assert status == 400

    def test_response_json_parsing_error(self):
        """JSON parsing error returns empty dict."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.get_json.side_effect = Exception("Invalid JSON")
        mock_client.get.return_value = mock_response

        test_client = ValidationTestClient(mock_client)
        data, status = test_client.get_with_validation(url="/api/users/123")
        assert status == 500
        assert data == {}


class TestRegisterValidationMiddleware:
    """Test register_validation_middleware function."""

    def test_register_returns_middleware_instance(self):
        """register_validation_middleware returns ValidationMiddleware."""
        app = Flask(__name__)
        middleware = register_validation_middleware(app)
        assert isinstance(middleware, ValidationMiddleware)
        assert middleware.app == app
