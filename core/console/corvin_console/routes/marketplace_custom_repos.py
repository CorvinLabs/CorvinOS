"""Custom GitHub repositories for the Marketplace panel (ADR-0450..0453).

The console is a FastAPI app. The first cut of these endpoints shipped as a
Flask blueprint (``core/console/routes/marketplace/custom_repos.py``) that no
app ever registered, so every one of them 404'd and the panel's "Custom" tab
could only render its error state. This is the same contract on the router the
console actually mounts.

Endpoints (under the console mount, i.e. ``/v1/console``):

- ``GET    /api/v1/marketplace/custom-repositories``           — list
- ``POST   /api/v1/marketplace/custom-repositories``           — add
- ``POST   /api/v1/marketplace/custom-repositories/validate``  — validate a URL
- ``PATCH  /api/v1/marketplace/custom-repositories``           — rotate token / toggle
- ``DELETE /api/v1/marketplace/custom-repositories``           — remove
- ``POST   /api/v1/marketplace/custom-repositories/refresh``   — refresh metadata

The tenant comes from the authenticated ``SessionRecord`` (ADR-0007), never from
an env var — a console route that read ``CORVIN_TENANT_ID`` would serve one
tenant's repositories to another's session.

A stored token is never echoed back: no response here carries one, and neither
does a log line (ADR-0452).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import audit as console_audit
from ..deps import require_csrf, require_session

router = APIRouter(
    prefix="/api/v1/marketplace/custom-repositories",
    tags=["console-marketplace-custom-repos"],
)

logger = logging.getLogger(__name__)

try:
    from core.marketplace.custom_repositories import (
        RepositoryManager,
        RepositoryValidationError,
    )
    from core.marketplace.auth import SecretsStoreError
    from core.marketplace.auth.token_encryption import InvalidKeyError
except ImportError:  # pragma: no cover — degrades to 503 below
    RepositoryManager = None  # type: ignore[assignment]

    class RepositoryValidationError(Exception):  # type: ignore[no-redef]
        pass

    class SecretsStoreError(Exception):  # type: ignore[no-redef]
        pass

    class InvalidKeyError(Exception):  # type: ignore[no-redef]
        pass


#: Token encryption is fail-closed: with no configured key a token cannot be
#: stored, so adding a PRIVATE repository fails. Public repositories need no
#: token and stay unaffected. This is a deployment gap, not a request error, so
#: it answers 503 with the fix rather than a bare 500 with a stack-trace string.
_NO_KEY_DETAIL = (
    "GitHub token encryption is not configured on this instance: set "
    "CORVIN_GITHUB_TOKEN_KEY to a base64-encoded 32-byte AES-256 key and "
    "restart the console. Public repositories can be added without a token."
)


class RepoRef(BaseModel):
    repo_url: str


class RepoAdd(BaseModel):
    repo_url: str
    # Named `token_ref` by ADR-0451 and by the form that posts it, though it
    # carries the raw PAT — the encryption happens on this side, in the secrets
    # store. One name, the documented one: the superseded Flask draft called it
    # `token`, and a request built from that draft's shape is simply ignored.
    token_ref: Optional[str] = None


class RepoPatch(BaseModel):
    repo_url: str
    token_ref: Optional[str] = None
    enabled: Optional[bool] = None


def _manager(session: Any) -> Any:
    """RepositoryManager scoped to the caller's tenant."""
    if RepositoryManager is None:
        raise HTTPException(
            status_code=503, detail="Custom repository backend unavailable"
        )
    tenant_id = getattr(session, "tenant_id", None) or "_default"
    return RepositoryManager(tenant_id)


def _audit(session: Any, action: str, repo_url: str) -> None:
    """One hash-chained ``console.action_performed`` per state mutation.

    The target id is a stable digest of the repository URL — never the URL
    itself (it can name a personal GitHub account) and never the token.
    """
    import hashlib  # noqa: PLC0415

    console_audit.action_performed(
        tenant_id=getattr(session, "tenant_id", None) or "_default",
        sid_fingerprint=getattr(session, "sid_fingerprint", ""),
        action=action,
        target_kind="marketplace_custom_repository",
        target_id=hashlib.sha256(repo_url.encode("utf-8")).hexdigest()[:16],
    )


def _serialize(repo: Any) -> dict:
    """Public shape of a repository. Deliberately carries no token."""
    return {
        "repo_url": repo.repo_url,
        "status": repo.status,
        "extension_count": repo.extension_count,
        "error_message": repo.error_message,
        "last_checked": repo.last_checked,
        "enabled": getattr(repo, "enabled", True),
    }


def _require_url(repo_url: str) -> str:
    url = (repo_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="repo_url is required")
    return url


@router.get("")
async def list_repositories(session: Any = Depends(require_session)) -> dict:
    """List every custom repository registered for the caller's tenant."""
    manager = _manager(session)
    try:
        return {"repositories": [_serialize(r) for r in manager.list_repositories()]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to list custom repositories")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("", status_code=201)
async def add_repository(
    body: RepoAdd, session: Any = Depends(require_csrf)
) -> dict:
    """Register a repository, storing its token encrypted when one is given."""
    manager = _manager(session)
    url = _require_url(body.repo_url)
    token = (body.token_ref or "").strip() or None
    try:
        result = _serialize(manager.add_repository(url, token))
    except RepositoryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidKeyError as exc:
        logger.error("Token encryption key unusable: %s", exc)
        raise HTTPException(status_code=503, detail=_NO_KEY_DETAIL) from exc
    except SecretsStoreError as exc:
        logger.exception("Token storage failed")
        raise HTTPException(status_code=500, detail="Failed to store token") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to add custom repository")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _audit(session, "marketplace.custom_repository_add", url)
    return result


@router.post("/validate")
async def validate_repository(
    body: RepoRef, session: Any = Depends(require_csrf)
) -> dict:
    """Check a URL without registering it. An invalid URL is a 200 with
    ``valid: false`` — the form asks this on every keystroke pause, and a 4xx
    per character is noise, not an error."""
    manager = _manager(session)
    url = _require_url(body.repo_url)
    try:
        manager.validate_repository_url(url)
        return {"valid": True, "error": None}
    except RepositoryValidationError as exc:
        return {"valid": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Custom repository validation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("")
async def update_repository(
    body: RepoPatch, session: Any = Depends(require_csrf)
) -> dict:
    """Rotate the stored token and/or flip the enabled state."""
    manager = _manager(session)
    url = _require_url(body.repo_url)
    token = (body.token_ref or "").strip() or None
    try:
        repo = manager.get_repository(url)
        if token:
            manager.secrets_store.store_token(url, token, manager.tenant_id)
        if body.enabled is not None:
            repo = manager.set_enabled(url, body.enabled)
        result = _serialize(repo)
    except RepositoryValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidKeyError as exc:
        logger.error("Token encryption key unusable: %s", exc)
        raise HTTPException(status_code=503, detail=_NO_KEY_DETAIL) from exc
    except SecretsStoreError as exc:
        logger.exception("Token storage failed")
        raise HTTPException(status_code=500, detail="Failed to store token") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to update custom repository")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _audit(session, "marketplace.custom_repository_update", url)
    return result


@router.delete("")
async def remove_repository(
    body: RepoRef, session: Any = Depends(require_csrf)
) -> dict:
    """Unregister a repository and drop its stored token."""
    manager = _manager(session)
    url = _require_url(body.repo_url)
    try:
        manager.remove_repository(url)
    except RepositoryValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to remove custom repository")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _audit(session, "marketplace.custom_repository_remove", url)
    return {"message": "Repository removed"}


@router.post("/refresh")
async def refresh_repository(
    body: RepoRef, session: Any = Depends(require_csrf)
) -> dict:
    """Re-read a repository's current metadata.

    The GitHub fetch itself is not implemented yet (it lands with the discovery
    layer); this returns the stored record so the UI's refresh button reports
    the truth instead of a 404."""
    manager = _manager(session)
    url = _require_url(body.repo_url)
    try:
        return _serialize(manager.get_repository(url))
    except RepositoryValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to refresh custom repository")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
