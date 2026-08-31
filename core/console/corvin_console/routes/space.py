"""CorvinSpace — personal profile + public domains (Layer 40).

Endpoints
---------
  GET  /space/profile                 → current profile (or empty defaults)
  PUT  /space/profile                 → update profile fields
  GET  /space/domains                 → list up to 5 domains
  POST /space/domains                 → create domain (max 5)
  PUT  /space/domains/{slug}          → update domain meta
  DELETE /space/domains/{slug}        → delete domain
  POST /space/domains/{slug}/publish  → publish post to domain feed
  GET  /space/domains/{slug}/feed     → domain post feed
  GET  /space/social/status           → social participation status
  POST /space/social/join             → /social-join (enable federation)
  POST /space/social/leave            → /social-leave
  POST /space/social/follow           → follow an actor
  GET  /space/social/following        → who we follow
  GET  /space/social/followers        → who follows us

Must NOT import anthropic (CI AST lint).
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

import logging
_log = logging.getLogger(__name__)

# ── Shared path setup (same pattern as routes/profile.py) ─────────────────

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_VOICE_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_VOICE_SHARED) not in sys.path:
    sys.path.insert(0, str(_VOICE_SHARED))
_OPERATOR = _REPO / "operator"
if str(_OPERATOR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR))

# Fail-closed FREE_TIER fallback for the limits this route reads. A bare
# ``{}.get`` would return None for every feature, and None is the "unlimited"
# sentinel — so an unimportable license package would FAIL OPEN (unlimited
# public domains). Hard-code the FREE_TIER cap inline so both the get_limit and
# assert_limit fallbacks stay fail-closed: free tier allows one public domain.
_SPACE_FREE_TIER_FALLBACK: dict = {"space_domains_max": 1}

try:
    from license.validator import get_limit as _lic_get_limit, assert_limit as _lic_assert_limit
    from license.limits import LicenseLimitError as _LicLimitError
except ImportError:
    try:
        from license.limits import FREE_TIER as _FREE_TIER, LicenseLimitError as _LicLimitError  # type: ignore[import]
    except ImportError:
        class _LicLimitError(Exception): pass  # type: ignore[assignment,misc]
        # Innermost fallback: license package entirely absent. Resolve via the
        # hard-coded FREE_TIER caps (fail-closed), never to None=unlimited.
        _FREE_TIER = _SPACE_FREE_TIER_FALLBACK  # type: ignore[assignment]
    _lic_get_limit = _FREE_TIER.get  # type: ignore[assignment]
    def _lic_assert_limit(feature: str, requested: int = 1, **_kw: object) -> None:  # type: ignore[assignment,misc]
        _limit = _FREE_TIER.get(feature)
        if _limit is not None and isinstance(_limit, int) and requested > _limit:
            raise _LicLimitError(feature, requested, _limit)

try:
    import space_profile as _space_profile
except ImportError:
    _space_profile = None  # type: ignore[assignment]

try:
    import space_domains as _space_domains
except ImportError:
    _space_domains = None  # type: ignore[assignment]

try:
    import social_consent as _social_consent
except ImportError:
    _social_consent = None  # type: ignore[assignment]

try:
    import social_feed as _social_feed
except ImportError:
    _social_feed = None  # type: ignore[assignment]

try:
    import social_registry as _social_registry
except ImportError:
    _social_registry = None  # type: ignore[assignment]


class _UnavailableMod:
    """Sentinel used when an optional module failed to import.
    Raises 503 on first attribute access so the server starts normally
    and only individual routes degrade gracefully.
    """
    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)

    def __getattr__(self, item: str) -> None:  # type: ignore[override]
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            f"{object.__getattribute__(self, '_name')} module unavailable",
        )


if _space_profile is None:
    _space_profile = _UnavailableMod("space_profile")  # type: ignore[assignment]
if _space_domains is None:
    _space_domains = _UnavailableMod("space_domains")  # type: ignore[assignment]
if _social_consent is None:
    _social_consent = _UnavailableMod("social_consent")  # type: ignore[assignment]
if _social_feed is None:
    _social_feed = _UnavailableMod("social_feed")  # type: ignore[assignment]
if _social_registry is None:
    _social_registry = _UnavailableMod("social_registry")  # type: ignore[assignment]


router = APIRouter()


# ── Pydantic schemas ───────────────────────────────────────────────────────


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(None, max_length=64)
    bio: str | None = Field(None, max_length=500)
    contact_handle: str | None = Field(None, max_length=80)
    website: str | None = Field(None, max_length=200)
    location: str | None = Field(None, max_length=100)
    model_config = {"extra": "forbid"}


class DomainCreateRequest(BaseModel):
    slug: str = Field(..., max_length=40, pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    name: str = Field(..., max_length=64)
    description: str = Field("", max_length=300)
    visibility: Literal["public", "followers", "private"] = "public"
    model_config = {"extra": "forbid"}


class DomainUpdateRequest(BaseModel):
    name: str | None = Field(None, max_length=64)
    description: str | None = Field(None, max_length=300)
    visibility: Literal["public", "followers", "private"] | None = None
    model_config = {"extra": "forbid"}


class DomainPublishRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    visibility: Literal["public", "followers"] = "public"
    model_config = {"extra": "forbid"}


class SocialJoinRequest(BaseModel):
    display_name: str = Field(..., max_length=64)
    host: str = Field(..., max_length=200)
    compliance_zone: Literal["eu", "us", "global"] = "eu"
    model_config = {"extra": "forbid"}


class SocialFollowRequest(BaseModel):
    actor_id: str = Field(..., max_length=128)
    inbox_url: str = Field(..., max_length=500)
    public_key_hex: str = Field(..., max_length=64)
    display_name: str | None = Field(None, max_length=64)
    compliance_zone: str | None = Field(None, max_length=32)
    model_config = {"extra": "forbid"}


# ── Profile routes ─────────────────────────────────────────────────────────


@router.get("/profile")
def space_profile_get(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the current CorvinSpace profile (or null if not yet set up)."""
    profile = _space_profile.load_profile(rec.tenant_id)
    social_actor_id = _space_profile.get_social_actor_id(rec.tenant_id)
    return {
        "tenant_id": rec.tenant_id,
        "profile": asdict(profile) if profile is not None else None,
        "social_actor_id": social_actor_id,
        "ts": time.time(),
    }


@router.put("/profile")
def space_profile_update(
    body: ProfileUpdateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Update CorvinSpace profile fields. No re-auth required (non-sensitive)."""
    updates = body.model_dump(exclude_none=True)
    try:
        profile = _space_profile.update_profile(rec.tenant_id, **updates)
    except OSError as exc:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="space.profile.write",
            target_kind="space_profile",
            target_id="self",
            reason="io-error",
        )
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "write failed",
        ) from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="space.profile.write",
        target_kind="space_profile",
        target_id="self",
    )
    social_actor_id = _space_profile.get_social_actor_id(rec.tenant_id)
    return {
        "ok": True,
        "tenant_id": rec.tenant_id,
        "profile": asdict(profile),
        "social_actor_id": social_actor_id,
        "ts": time.time(),
    }


# ── Domain routes ──────────────────────────────────────────────────────────


@router.get("/domains")
def space_domains_list(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List domains. max_domains reflects the active license tier (1 on free, None = unlimited)."""
    domains = _space_domains.list_domains(rec.tenant_id)
    raw_limit = _lic_get_limit("space_domains_max")
    # None means unlimited — surface a large sentinel so the UI can render ∞ cleanly
    max_domains = raw_limit if raw_limit is not None else 999_999
    return {
        "tenant_id": rec.tenant_id,
        "domains": [asdict(d) for d in domains],
        "max_domains": max_domains,
        "license_unlimited": raw_limit is None,
        "ts": time.time(),
    }


@router.post("/domains")
def space_domains_create(
    body: DomainCreateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Create a new domain.  402 if license limit reached, 400 if slug taken."""
    # ── License gate (cryptographically enforced via SesT Ed25519 signature) ──
    existing = _space_domains.list_domains(rec.tenant_id)
    try:
        _lic_assert_limit("space_domains_max", requested=len(existing) + 1)
    except _LicLimitError as exc:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="space.domain.create",
            target_kind="space_domain",
            target_id="pending",
            reason="license_limit_exceeded",
        )
        raise HTTPException(
            http_status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "license_limit",
                "feature": "space_domains_max",
                "msg": "license limit exceeded",
                "upgrade_url": "https://corvin-labs.com/pricing",
            },
        ) from exc
    # ── Create ────────────────────────────────────────────────────────────────
    # LIC-SPACE-DOM-TOCTOU-01: the pre-check above is an advisory fast-path; the
    # AUTHORITATIVE space_domains_max gate runs inside create_domain under the
    # write lock so concurrent creates cannot race past the free-tier cap.
    _raw_dom_limit = _lic_get_limit("space_domains_max")
    _dom_license_max = _raw_dom_limit if isinstance(_raw_dom_limit, int) else None
    try:
        domain = _space_domains.create_domain(
            slug=body.slug,
            name=body.name,
            description=body.description,
            visibility=body.visibility,
            tenant_id=rec.tenant_id,
            license_max=_dom_license_max,
        )
    except _space_domains.DomainLimitError as exc:
        if getattr(exc, "license_capped", False):
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="space.domain.create",
                target_kind="space_domain",
                target_id=body.slug,
                reason="license_limit_exceeded",
            )
            raise HTTPException(
                http_status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "license_limit",
                    "feature": "space_domains_max",
                    "msg": "license limit exceeded",
                    "upgrade_url": "https://corvin-labs.com/pricing",
                },
            ) from exc
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid request") from exc
    except (FileExistsError, ValueError) as exc:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid request") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="space.domain.create",
        target_kind="space_domain",
        target_id=body.slug,
    )
    return {"ok": True, "domain": asdict(domain), "ts": time.time()}


@router.put("/domains/{slug}")
def space_domains_update(
    slug: str,
    body: DomainUpdateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Update domain meta (name, description, visibility)."""
    updates = body.model_dump(exclude_none=True)
    try:
        domain = _space_domains.update_domain(slug, rec.tenant_id, **updates)
    except _space_domains.DomainNotFoundError as exc:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "not found") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="space.domain.update",
        target_kind="space_domain",
        target_id=slug,
    )
    return {"ok": True, "domain": asdict(domain), "ts": time.time()}


@router.delete("/domains/{slug}")
def space_domains_delete(
    slug: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Delete a domain. 404 if not found."""
    deleted = _space_domains.delete_domain(slug, rec.tenant_id)
    if not deleted:
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"Domain not found: {slug!r}",
        )
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="space.domain.delete",
        target_kind="space_domain",
        target_id=slug,
    )
    return {"ok": True, "ts": time.time()}


@router.post("/domains/{slug}/publish")
def space_domains_publish(
    slug: str,
    body: DomainPublishRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Publish a post to a domain feed. Requires social-join first."""
    # Verify domain exists
    domain = _space_domains.get_domain(slug, rec.tenant_id)
    if domain is None:
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"Domain not found: {slug!r}",
        )

    # Check social consent
    if not _social_consent.is_consented(rec.tenant_id):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "social_not_joined",
                "message": "Run /social-join first.",
            },
        )

    try:
        envelope = _social_feed.publish_post(
            content=body.content,
            visibility=body.visibility,
            tags=body.tags,
            tenant_id=rec.tenant_id,
        )
    except _social_feed.ConsentRequired as exc:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "social_not_joined",
                "message": "Run /social-join first.",
            },
        ) from exc
    except _social_feed.FeedError as exc:
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "publish failed",
        ) from exc

    _space_domains.increment_post_count(slug, rec.tenant_id)

    post_id: str = envelope.get("post_id", "")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="space.domain.publish",
        target_kind="space_domain",
        target_id=slug,
    )
    return {"ok": True, "post_id": post_id, "ts": time.time()}


@router.get("/domains/{slug}/feed")
def space_domains_feed(
    slug: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the last 20 posts for a domain."""
    domain = _space_domains.get_domain(slug, rec.tenant_id)
    if domain is None:
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"Domain not found: {slug!r}",
        )

    try:
        store = _social_feed.SocialFeedStore(rec.tenant_id)
        posts = store.list_posts(limit=20)
    except _social_feed.FeedError as exc:
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "feed read failed",
        ) from exc

    return {
        "tenant_id": rec.tenant_id,
        "slug": slug,
        "posts": posts,
        "ts": time.time(),
    }


# ── Social routes ──────────────────────────────────────────────────────────


@router.get("/social/status")
def space_social_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return current social federation consent status + graph counts if joined."""
    status = _social_consent.get_status(rec.tenant_id)

    follower_count: int | None = None
    following_count: int | None = None

    if status.get("is_enabled"):
        try:
            reg = _social_registry.SocialRegistry(rec.tenant_id)
            follower_count = len(reg.list_actors("follower"))
            following_count = len(reg.list_actors("following"))
        except Exception:
            pass  # best-effort — counts absent if registry unavailable

    return {
        "tenant_id": rec.tenant_id,
        "status": status,
        "follower_count": follower_count,
        "following_count": following_count,
        "ts": time.time(),
    }


@router.post("/social/join")
def space_social_join(
    body: SocialJoinRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Enable CorvinFed social federation (/social-join)."""
    try:
        result = _social_consent.join(
            display_name=body.display_name,
            host=body.host,
            compliance_zone=body.compliance_zone,
            tenant_id=rec.tenant_id,
        )
    except Exception as exc:
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "join failed",
        ) from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="social.join",
        target_kind="social_federation",
        target_id=rec.tenant_id,
    )
    return {"ok": True, "result": result, "ts": time.time()}


@router.post("/social/leave")
def space_social_leave(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Revoke CorvinFed social federation consent (/social-leave)."""
    result = _social_consent.leave(tenant_id=rec.tenant_id)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="social.leave",
        target_kind="social_federation",
        target_id=rec.tenant_id,
    )
    return {"ok": True, "result": result, "ts": time.time()}


@router.post("/social/follow")
def space_social_follow(
    body: SocialFollowRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Follow an actor by actor_id + inbox_url + public_key_hex."""
    if not _social_consent.is_consented(rec.tenant_id):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "social_not_joined",
                "message": "Run /social-join first.",
            },
        )

    try:
        reg = _social_registry.SocialRegistry(rec.tenant_id)
        reg.add_following(
            actor_id=body.actor_id,
            inbox_url=body.inbox_url,
            public_key_hex=body.public_key_hex,
            display_name=body.display_name,
            compliance_zone=body.compliance_zone,
        )
    except _social_registry.RegistryError as exc:
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "follow failed",
        ) from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="social.follow",
        target_kind="social_actor",
        target_id=body.actor_id,
    )
    return {"ok": True, "ts": time.time()}


@router.get("/social/following")
def space_social_following(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the list of actors we follow."""
    try:
        reg = _social_registry.SocialRegistry(rec.tenant_id)
        actors = reg.list_actors("following")
    except _social_registry.RegistryError as exc:
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "registry read failed",
        ) from exc

    return {
        "tenant_id": rec.tenant_id,
        "following": actors,
        "ts": time.time(),
    }


@router.get("/social/followers")
def space_social_followers(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the list of actors who follow us."""
    try:
        reg = _social_registry.SocialRegistry(rec.tenant_id)
        actors = reg.list_actors("follower")
    except _social_registry.RegistryError as exc:
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "registry read failed",
        ) from exc

    return {
        "tenant_id": rec.tenant_id,
        "followers": actors,
        "ts": time.time(),
    }
