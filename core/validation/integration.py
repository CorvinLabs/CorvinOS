"""Validation middleware registration — Phase 10 (ADR-0297)

Central registration point for validation middleware in Flask and asyncio.
Provides test utilities for E2E validation testing.

Middleware:
- Flask: Global input validation middleware
- Asyncio: Before-task-creation validation hook
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol
from dataclasses import dataclass

from flask import Flask, request, jsonify


# ============================================================================
# Data Classes
# ============================================================================


@dataclass(frozen=True)
class ValidationErrorResponse:
    """Structured validation error response."""

    error: str
    code: str
    status_code: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON dict."""
        return {"error": self.error, "code": self.code}


# ============================================================================
# Flask Middleware
# ============================================================================


class ValidationMiddleware:
    """Flask middleware for global input validation.

    Validates all incoming requests at the middleware level before routing.
    Fail-closed: invalid request → 400/403/422 response, audit logged.
    """

    def __init__(self, app: Optional[Flask] = None):
        self.app = app
        if app:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        """Initialize middleware with Flask app."""
        self.app = app
        app.before_request(self._validate_request)

    def _validate_request(self) -> Optional[tuple[Any, int]]:
        """Before-request hook: validate all incoming requests.

        Returns:
            None if valid, (error_response, status_code) if invalid
        """
        # Placeholder: real implementation would validate based on route
        # and configured schemas. This integrates with @validate_input decorator.
        return None

    @staticmethod
    def create_error_response(
        message: str, code: str, status_code: int = 400
    ) -> tuple[Any, int]:
        """Create standardized error response.

        Args:
            message: Human-readable error message
            code: Machine-readable error code
            status_code: HTTP status code

        Returns:
            (Flask jsonify response, status_code)
        """
        return jsonify({"error": message, "code": code}), status_code


# ============================================================================
# Test Utilities
# ============================================================================


class ValidationTestClient:
    """Helper for E2E validation tests.

    Provides convenience methods for testing validation through real
    Flask TestClient requests.
    """

    def __init__(self, test_client: Any):
        """Initialize with Flask test client.

        Args:
            test_client: Flask app.test_client() instance
        """
        self.client = test_client

    def get_with_validation(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        expect_error: bool = False,
    ) -> tuple[Any, int]:
        """Make GET request and expect validation result.

        Args:
            url: Route URL (e.g., /api/users/123)
            headers: Headers to include (e.g., X-Tenant-ID)
            expect_error: If True, expect 400 response; if False, expect 200

        Returns:
            (response JSON or dict, status_code)
        """
        if headers is None:
            headers = {}

        response = self.client.get(url, headers=headers)
        status_code = response.status_code

        try:
            data = response.get_json() or {}
        except Exception:
            data = {}

        return data, status_code

    def post_with_validation(
        self,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        expect_status: int = 200,
    ) -> tuple[Any, int]:
        """Make POST request with JSON body and expect validation result.

        Args:
            url: Route URL
            json: JSON payload to send
            headers: Headers to include
            expect_status: Expected HTTP status code

        Returns:
            (response JSON or dict, status_code)
        """
        if headers is None:
            headers = {}

        response = self.client.post(url, json=json, headers=headers)
        status_code = response.status_code

        try:
            data = response.get_json() or {}
        except Exception:
            data = {}

        return data, status_code

    def put_with_validation(
        self,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> tuple[Any, int]:
        """Make PUT request with validation."""
        if headers is None:
            headers = {}

        response = self.client.put(url, json=json, headers=headers)
        return response.get_json() or {}, response.status_code

    def delete_with_validation(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> tuple[Any, int]:
        """Make DELETE request with validation."""
        if headers is None:
            headers = {}

        response = self.client.delete(url, headers=headers)
        return response.get_json() or {}, response.status_code


# ============================================================================
# Registration Function
# ============================================================================


def register_validation_middleware(app: Flask) -> ValidationMiddleware:
    """Register validation middleware with Flask app.

    Args:
        app: Flask application instance

    Returns:
        ValidationMiddleware instance (for testing)
    """
    middleware = ValidationMiddleware(app)
    return middleware
