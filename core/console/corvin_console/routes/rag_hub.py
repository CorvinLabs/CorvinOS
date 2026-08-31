"""RAG Hub API Routes — Provider Marketplace.

Endpoints:
- GET  /hub/providers — list/search providers
- GET  /hub/providers/{id} — get provider details
- POST /hub/providers — publish a provider
- GET  /hub/trending — trending providers
- GET  /hub/top-rated — highest-rated providers
- GET  /hub/most-downloaded — most-downloaded providers
- POST /hub/reviews — add a review
- GET  /hub/reviews/{provider_id} — get reviews
"""
from __future__ import annotations

import logging
from typing import Annotated, Any


from fastapi import APIRouter, Depends, Query

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

logger = logging.getLogger(__name__)

# Per-tenant hub cache — keyed by tenant_id so each tenant's hub points at its
# own directory. Using env-var tenant was an ADR-0007 violation (finding HIGH).
_HUB_CACHE: dict[str, Any] = {}


def _get_hub(tenant_id: str) -> Any | None:
    """Return a RAGHub for the given tenant, creating it on first access."""
    if tenant_id in _HUB_CACHE:
        return _HUB_CACHE[tenant_id]
    try:
        from shared.rag_hub import RAGHub  # noqa: PLC0415
        hub_dir = _forge_paths.tenant_global_dir(tenant_id) / "rag_hub"
        hub = RAGHub(hub_dir)
        _HUB_CACHE[tenant_id] = hub
        logger.info("RAG Hub initialized for tenant %s (%s)", tenant_id, hub_dir)
        return hub
    except ImportError as e:
        logger.warning("RAG Hub not available: %s", e)
        return None
    except Exception as e:
        logger.error("Failed to initialize RAG Hub for tenant %s: %s", tenant_id, e)
        return None


router = APIRouter(prefix="/hub", tags=["console-rag-hub"])


# ── Providers: List & Search ────────────────────────────────

@router.get("/providers")
async def list_providers(
    q: str = Query("", description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    zone: str = Query("", description="Filter by compliance zone (EU, US, etc.)"),
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """List or search providers."""
    tenant_id = _session.tenant_id if _session else "_default"
    hub = _get_hub(tenant_id)
    if hub is None:
        return {"providers": [], "total": 0}

    try:
        if q:
            providers = hub.search(q, limit=limit)
        elif zone:
            providers = hub.filter_by_zone(zone)
        else:
            providers = hub.list(limit=limit, offset=offset)

        return {
            "providers": [p.to_dict() for p in providers],
            "total": len(hub.providers),
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.error("Failed to list providers: %s", e)
        return {"providers": [], "total": 0, "error": "hub_error"}


@router.get("/providers/{provider_id}")
async def get_provider(
    provider_id: str,
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get provider details."""
    tenant_id = _session.tenant_id if _session else "_default"
    hub = _get_hub(tenant_id)
    if hub is None:
        return {"error": "Hub not available"}

    try:
        provider = hub.get(provider_id)
        if not provider:
            return {"error": "provider_not_found"}

        reviews = hub.get_reviews(provider_id)
        return {
            "provider": provider.to_dict(),
            "reviews": [r.to_dict() for r in reviews],
            "review_count": len(reviews),
        }
    except Exception as e:
        logger.error("Failed to get provider: %s", e)
        return {"error": "hub_error"}


# ── Publishing ─────────────────────────────────────────────

@router.post("/providers")
async def publish_provider(
    req: dict[str, Any],
    _session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Publish a provider to the Hub.

    Expected request body:
    {
        "id": "my-elasticsearch",
        "name": "Elasticsearch (Production)",
        "description": "Enterprise keyword search",
        "author": "my-team",
        "version": "1.0",
        "data_classification": "INTERNAL",
        "compliance_zone": "EU",
        "capabilities": ["keyword-search", "filtering"],
        "manifest_hash": "sha256-hex-digest",
        "manifest_download_url": "https://..."  # Optional
    }
    """
    tenant_id = _session.tenant_id
    hub = _get_hub(tenant_id)
    if hub is None:
        console_audit.action_failed(
            tenant_id=tenant_id,
            sid_fingerprint=_session.sid_fingerprint,
            action="hub.publish",
            target_kind="rag_provider",
            target_id=str(req.get("id", "")),
            reason="hub_unavailable",
        )
        return {"error": "Hub not available"}

    try:
        from shared.rag_hub import RAGHubProvider  # noqa: PLC0415

        provider = RAGHubProvider(
            id=req["id"],
            name=req["name"],
            description=req["description"],
            author=req["author"],
            version=req["version"],
            data_classification=req["data_classification"],
            compliance_zone=req["compliance_zone"],
            capabilities=req["capabilities"],
            manifest_hash=req["manifest_hash"],
            manifest_download_url=req.get("manifest_download_url"),
        )

        published = hub.publish(provider)
        console_audit.action_performed(
            tenant_id=tenant_id,
            sid_fingerprint=_session.sid_fingerprint,
            action="hub.publish",
            target_kind="rag_provider",
            target_id=published.id,
        )
        return {
            "status": "published",
            "provider": published.to_dict(),
        }
    except Exception as e:
        logger.error("Failed to publish provider: %s", e)
        console_audit.action_failed(
            tenant_id=tenant_id,
            sid_fingerprint=_session.sid_fingerprint,
            action="hub.publish",
            target_kind="rag_provider",
            target_id=str(req.get("id", "")),
            reason="publish_failed",
        )
        return {"error": "publish_failed"}


# ── Analytics ──────────────────────────────────────────────

@router.get("/trending")
async def get_trending(
    limit: int = Query(10, ge=1, le=50),
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get trending providers."""
    tenant_id = _session.tenant_id if _session else "_default"
    hub = _get_hub(tenant_id)
    if hub is None:
        return {"providers": []}

    try:
        providers = hub.get_trending(limit=limit)
        return {"providers": [p.to_dict() for p in providers]}
    except Exception as e:
        logger.error("Failed to get trending: %s", e)
        return {"providers": [], "error": "hub_error"}


@router.get("/top-rated")
async def get_top_rated(
    limit: int = Query(10, ge=1, le=50),
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get highest-rated providers."""
    tenant_id = _session.tenant_id if _session else "_default"
    hub = _get_hub(tenant_id)
    if hub is None:
        return {"providers": []}

    try:
        providers = hub.get_top_rated(limit=limit)
        return {"providers": [p.to_dict() for p in providers]}
    except Exception as e:
        logger.error("Failed to get top-rated: %s", e)
        return {"providers": [], "error": "hub_error"}


@router.get("/most-downloaded")
async def get_most_downloaded(
    limit: int = Query(10, ge=1, le=50),
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get most-downloaded providers."""
    tenant_id = _session.tenant_id if _session else "_default"
    hub = _get_hub(tenant_id)
    if hub is None:
        return {"providers": []}

    try:
        providers = hub.get_most_downloaded(limit=limit)
        return {"providers": [p.to_dict() for p in providers]}
    except Exception as e:
        logger.error("Failed to get most-downloaded: %s", e)
        return {"providers": [], "error": "hub_error"}


# ── Reviews ────────────────────────────────────────────────

@router.post("/reviews")
async def add_review(
    req: dict[str, Any],
    _session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Add a review to a provider.

    Expected request body:
    {
        "provider_id": "my-elasticsearch",
        "rating": 5,
        "text": "Works great, highly recommend!"
    }
    """
    tenant_id = _session.tenant_id
    hub = _get_hub(tenant_id)
    if hub is None:
        console_audit.action_failed(
            tenant_id=tenant_id,
            sid_fingerprint=_session.sid_fingerprint,
            action="hub.review",
            target_kind="rag_provider",
            target_id=str(req.get("provider_id", "")),
            reason="hub_unavailable",
        )
        return {"error": "Hub not available"}

    try:
        from shared.rag_hub import RAGHubReview  # noqa: PLC0415

        review = RAGHubReview(
            provider_id=req["provider_id"],
            # SessionRecord has no per-user identity (single-tenant-owner
            # model -- see auth.py) -- .user_id never existed, so this
            # always 500'd before reaching add_review(). tier is the
            # closest honest, available label ("owner").
            author=_session.tier,
            rating=min(5, max(1, int(req["rating"]))),
            text=req["text"][:500],
        )

        added = hub.add_review(req["provider_id"], review)
        console_audit.action_performed(
            tenant_id=tenant_id,
            sid_fingerprint=_session.sid_fingerprint,
            action="hub.review",
            target_kind="rag_provider",
            target_id=req["provider_id"],
        )
        return {
            "status": "added",
            "review": added.to_dict(),
        }
    except Exception as e:
        logger.error("Failed to add review: %s", e)
        console_audit.action_failed(
            tenant_id=tenant_id,
            sid_fingerprint=_session.sid_fingerprint,
            action="hub.review",
            target_kind="rag_provider",
            target_id=str(req.get("provider_id", "")),
            reason="review_failed",
        )
        return {"error": "review_failed"}


@router.get("/reviews/{provider_id}")
async def get_reviews(
    provider_id: str,
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get reviews for a provider."""
    tenant_id = _session.tenant_id if _session else "_default"
    hub = _get_hub(tenant_id)
    if hub is None:
        return {"reviews": []}

    try:
        reviews = hub.get_reviews(provider_id)
        return {"reviews": [r.to_dict() for r in reviews]}
    except Exception as e:
        logger.error("Failed to get reviews: %s", e)
        return {"reviews": [], "error": "hub_error"}


# ── Import / Export ────────────────────────────────────────

@router.post("/import")
async def import_provider(
    req: dict[str, Any],
    _session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Import a provider manifest.

    Expected request body:
    {
        "manifest_yaml": "apiVersion: rag.corvin.io/v1alpha1\n...",
        "provider_id": "my-elasticsearch"  # Optional override
    }
    """
    tenant_id = _session.tenant_id
    # ADR-0144 CON-01: this path writes a {provider_id}.yaml into the SAME gated
    # registry dir as custom_provider.create — enforce rag_providers_max here too,
    # via the shared single-source gate so the two paths cannot drift.
    from ._rag_license_gate import (  # noqa: PLC0415
        enforce_rag_providers_max,
        sanitize_provider_id,
    )

    _req_pid = req.get("provider_id")
    # Constrain an explicit provider_id to a safe filename stem (path-traversal
    # defence) BEFORE the try block, so the HTTP 400/402 it raises propagates
    # instead of being swallowed by the broad ``except Exception`` below. A None
    # id is derived from manifest metadata.name inside import_manifest, which
    # sanitizes it there.
    provider_id = sanitize_provider_id(str(_req_pid)) if _req_pid is not None else None
    enforce_rag_providers_max(
        tenant_id,
        _session.sid_fingerprint,
        requested_id=str(_req_pid or "pending"),
        audit_action="hub.import",
    )
    try:
        from shared.rag_import_export import RAGProviderImportExport  # noqa: PLC0415

        manifest_yaml = req.get("manifest_yaml", "")

        registry_dir = _forge_paths.tenant_global_dir(tenant_id) / "rag"

        success, imported_id, error = RAGProviderImportExport.import_manifest(
            manifest_yaml,
            registry_dir,
            provider_id,
        )

        if not success:
            console_audit.action_failed(
                tenant_id=tenant_id,
                sid_fingerprint=_session.sid_fingerprint,
                action="hub.import",
                target_kind="rag_provider",
                target_id=str(provider_id or ""),
                reason="import_failed",
            )
            return {"status": "failed", "error": "import_failed"}

        # Track download in Hub
        hub = _get_hub(tenant_id)
        if hub:
            hub.increment_download_count(imported_id)

        console_audit.action_performed(
            tenant_id=tenant_id,
            sid_fingerprint=_session.sid_fingerprint,
            action="hub.import",
            target_kind="rag_provider",
            target_id=imported_id,
        )
        return {
            "status": "imported",
            "provider_id": imported_id,
        }
    except Exception as e:
        logger.error("Failed to import provider: %s", e)
        console_audit.action_failed(
            tenant_id=tenant_id,
            sid_fingerprint=_session.sid_fingerprint,
            action="hub.import",
            target_kind="rag_provider",
            target_id="",
            reason="import_error",
        )
        return {"status": "failed", "error": "import_error"}


@router.post("/export")
async def export_provider(
    req: dict[str, Any],
    _session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Export a provider manifest for sharing.

    Expected request body:
    {
        "provider_id": "my-elasticsearch"
    }
    """
    tenant_id = _session.tenant_id
    try:
        from shared.rag_import_export import RAGProviderImportExport  # noqa: PLC0415

        manifest_file = _forge_paths.tenant_global_dir(tenant_id) / "rag" / f"{req['provider_id']}.yaml"

        manifest_yaml = RAGProviderImportExport.export_manifest(manifest_file)
        manifest_hash = RAGProviderImportExport.compute_manifest_hash(manifest_yaml)

        console_audit.action_performed(
            tenant_id=tenant_id,
            sid_fingerprint=_session.sid_fingerprint,
            action="hub.export",
            target_kind="rag_provider",
            target_id=req["provider_id"],
        )
        return {
            "status": "exported",
            "manifest_yaml": manifest_yaml,
            "manifest_hash": manifest_hash,
            "share_link": f"https://hub.corvin.local/manifest/{manifest_hash}",
        }
    except Exception as e:
        logger.error("Failed to export provider: %s", e)
        console_audit.action_failed(
            tenant_id=tenant_id,
            sid_fingerprint=_session.sid_fingerprint,
            action="hub.export",
            target_kind="rag_provider",
            target_id=str(req.get("provider_id", "")),
            reason="export_failed",
        )
        return {"status": "failed", "error": "export_failed"}
