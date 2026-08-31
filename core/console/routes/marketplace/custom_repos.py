"""SUPERSEDED — dead Flask blueprint, kept only for reference.

The console is a FastAPI app and registers no Flask blueprints (there is no
``register_blueprint`` call anywhere in this repo), so every route below 404'd
from the day it was written. The live implementation of this contract is
``core/console/corvin_console/routes/marketplace_custom_repos.py``, mounted by
``corvin_console.app``. Do not add endpoints here; they cannot be reached.

Flask routes for custom repository management (ADR-0451).

Endpoints:
- GET /v1/marketplace/custom-repositories — List custom repos
- POST /v1/marketplace/custom-repositories — Add repo
- POST /v1/marketplace/custom-repositories/validate — Validate URL
- DELETE /v1/marketplace/custom-repositories — Remove repo
- PATCH /v1/marketplace/custom-repositories — Update repo
- POST /v1/marketplace/custom-repositories/refresh — Refresh metadata
"""

from __future__ import annotations

from flask import Blueprint, request, jsonify, current_app
from typing import Tuple, Dict, Any
import logging

from core.marketplace.custom_repositories import (
    RepositoryManager,
    RepositoryValidationError,
)
from core.marketplace.auth import SecretsStoreError

# Create blueprint
marketplace_custom_repos = Blueprint(
    "marketplace_custom_repos",
    __name__,
    url_prefix="/v1/marketplace/custom-repositories",
)

logger = logging.getLogger(__name__)


def get_tenant_id() -> str:
    """Extract tenant_id from request context.

    Returns:
        Tenant ID (default: '_default' for local installs)

    TODO: Wire into actual tenant context from session/auth
    """
    # For now, use a default; will be replaced with real tenant routing
    return "_default"


def get_repo_manager() -> RepositoryManager:
    """Get or create RepositoryManager for current tenant."""
    tenant_id = get_tenant_id()
    key = f"repo_manager_{tenant_id}"

    if key not in current_app.g:
        current_app.g[key] = RepositoryManager(tenant_id)

    return current_app.g[key]


# ============================================================================
# Endpoints
# ============================================================================


@marketplace_custom_repos.route("", methods=["GET"])
def list_repositories() -> Tuple[Dict[str, Any], int]:
    """List all custom repositories for this tenant.

    Response:
        {
          "repositories": [
            {
              "repo_url": "https://github.com/owner/repo",
              "status": "healthy",
              "extension_count": 5,
              "error_message": null,
              "last_checked": "2026-08-30T10:00:00+00:00"
            }
          ]
        }
    """
    try:
        manager = get_repo_manager()
        repos = manager.list_repositories()

        return jsonify({
            "repositories": [
                {
                    "repo_url": repo.repo_url,
                    "status": repo.status,
                    "extension_count": repo.extension_count,
                    "error_message": repo.error_message,
                    "last_checked": repo.last_checked,
                }
                for repo in repos
            ]
        }), 200

    except Exception as e:
        logger.exception("Failed to list repositories")
        return jsonify({"error": "Failed to list repositories", "details": str(e)}), 500


@marketplace_custom_repos.route("", methods=["POST"])
def add_repository() -> Tuple[Dict[str, Any], int]:
    """Add a custom repository.

    Request:
        {
          "repo_url": "https://github.com/owner/private-repo",
          "token": "ghp_..."  # Optional, for private repos
        }

    Response:
        {
          "repo_url": "https://github.com/owner/private-repo",
          "status": "pending",
          "extension_count": 0
        }
    """
    try:
        data = request.json or {}
        repo_url = data.get("repo_url", "").strip()
        token = data.get("token", "").strip() or None

        if not repo_url:
            return jsonify({"error": "repo_url is required"}), 400

        manager = get_repo_manager()

        # Add repository (validates URL, stores token)
        repo = manager.add_repository(repo_url, token)

        return jsonify({
            "repo_url": repo.repo_url,
            "status": repo.status,
            "extension_count": repo.extension_count,
        }), 201

    except RepositoryValidationError as e:
        logger.info(f"Repository validation failed: {e}")
        return jsonify({"error": str(e)}), 400

    except SecretsStoreError as e:
        logger.exception("Token storage failed")
        return jsonify({"error": "Failed to store token", "details": str(e)}), 500

    except Exception as e:
        logger.exception("Failed to add repository")
        return jsonify({"error": "Failed to add repository", "details": str(e)}), 500


@marketplace_custom_repos.route("/validate", methods=["POST"])
def validate_repository() -> Tuple[Dict[str, Any], int]:
    """Validate a repository URL without adding it.

    Request:
        {
          "repo_url": "https://github.com/owner/repo"
        }

    Response:
        {
          "valid": true,
          "error": null
        }
    """
    try:
        data = request.json or {}
        repo_url = data.get("repo_url", "").strip()

        if not repo_url:
            return jsonify({"error": "repo_url is required"}), 400

        manager = get_repo_manager()
        manager.validate_repository_url(repo_url)

        return jsonify({"valid": True, "error": None}), 200

    except RepositoryValidationError as e:
        return jsonify({"valid": False, "error": str(e)}), 400

    except Exception as e:
        logger.exception("Validation failed")
        return jsonify({"error": "Validation failed", "details": str(e)}), 500


@marketplace_custom_repos.route("", methods=["DELETE"])
def remove_repository() -> Tuple[Dict[str, Any], int]:
    """Remove a custom repository.

    Request:
        {
          "repo_url": "https://github.com/owner/repo"
        }

    Response:
        {
          "message": "Repository removed"
        }
    """
    try:
        data = request.json or {}
        repo_url = data.get("repo_url", "").strip()

        if not repo_url:
            return jsonify({"error": "repo_url is required"}), 400

        manager = get_repo_manager()
        manager.remove_repository(repo_url)

        return jsonify({"message": "Repository removed"}), 200

    except Exception as e:
        logger.exception("Failed to remove repository")
        return jsonify({"error": "Failed to remove repository", "details": str(e)}), 500


@marketplace_custom_repos.route("", methods=["PATCH"])
def update_repository() -> Tuple[Dict[str, Any], int]:
    """Update repository metadata (e.g., token rotation).

    Request:
        {
          "repo_url": "https://github.com/owner/repo",
          "token": "ghp_..."  # New token for rotation
        }

    Response:
        {
          "repo_url": "...",
          "status": "healthy"
        }
    """
    try:
        data = request.json or {}
        repo_url = data.get("repo_url", "").strip()
        token = data.get("token", "").strip() or None

        if not repo_url:
            return jsonify({"error": "repo_url is required"}), 400

        manager = get_repo_manager()

        # Verify repo exists
        repo = manager.get_repository(repo_url)

        # Update token if provided
        if token:
            manager.secrets_store.store_token(repo_url, token, manager.tenant_id)

        return jsonify({
            "repo_url": repo.repo_url,
            "status": repo.status,
            "extension_count": repo.extension_count,
        }), 200

    except RepositoryValidationError as e:
        return jsonify({"error": str(e)}), 404

    except SecretsStoreError as e:
        logger.exception("Token storage failed")
        return jsonify({"error": "Failed to store token", "details": str(e)}), 500

    except Exception as e:
        logger.exception("Failed to update repository")
        return jsonify({"error": "Failed to update repository", "details": str(e)}), 500


@marketplace_custom_repos.route("/refresh", methods=["POST"])
def refresh_repository() -> Tuple[Dict[str, Any], int]:
    """Refresh repository metadata (validate connection, fetch extensions).

    Request:
        {
          "repo_url": "https://github.com/owner/repo"
        }

    Response:
        {
          "repo_url": "...",
          "status": "healthy",
          "extension_count": 5
        }

    TODO: Implement actual GitHub API call + parsing
    """
    try:
        data = request.json or {}
        repo_url = data.get("repo_url", "").strip()

        if not repo_url:
            return jsonify({"error": "repo_url is required"}), 400

        manager = get_repo_manager()
        repo = manager.get_repository(repo_url)

        # TODO: Call GitHub API to fetch extensions
        # For now, return current status
        return jsonify({
            "repo_url": repo.repo_url,
            "status": repo.status,
            "extension_count": repo.extension_count,
        }), 200

    except RepositoryValidationError as e:
        return jsonify({"error": str(e)}), 404

    except Exception as e:
        logger.exception("Failed to refresh repository")
        return jsonify({"error": "Failed to refresh repository", "details": str(e)}), 500


# ============================================================================
# Error Handlers
# ============================================================================


@marketplace_custom_repos.errorhandler(400)
def bad_request(error):
    """Handle 400 errors."""
    return jsonify({"error": "Bad request", "details": str(error)}), 400


@marketplace_custom_repos.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"error": "Not found", "details": str(error)}), 404


@marketplace_custom_repos.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    logger.exception("Internal server error")
    return jsonify({"error": "Internal server error"}), 500
