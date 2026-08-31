"""Layer 40 CorvinSpace — public domain management (max 5 per user).

Storage: <tenant_global>/space/domains/<slug>/meta.json  (mode 0600)

Must NOT import anthropic (CI AST lint).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from .paths import tenant_global_dir  # type: ignore[import-not-found]
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from paths import tenant_global_dir  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOMAIN_MAX = 5
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_FLOCK_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DomainLimitError(Exception):
    """Raised when the user already has the maximum number of domains.

    ``license_capped`` is True when the cap that triggered was the per-tenant
    license limit (space_domains_max) rather than the structural DOMAIN_MAX — the
    HTTP layer maps it to 402 (Payment Required) instead of 400.
    """

    def __init__(self, *, license_capped: bool = False) -> None:
        self.license_capped = license_capped
        super().__init__(
            "License domain limit reached." if license_capped
            else "Maximum 5 domains per user."
        )


class DomainNotFoundError(Exception):
    """Raised when the requested domain slug does not exist."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"Domain not found: {slug!r}")
        self.slug = slug


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class DomainMeta:
    slug: str           # URL-safe: [a-z0-9-]{1,40}
    name: str           # max 64 chars
    description: str    # max 300 chars
    visibility: str     # "public" | "followers" | "private"
    created_at: float
    updated_at: float
    post_count: int     # cached count, updated on publish


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def domains_dir(tenant_id: str | None = None) -> Path:
    """Return the path to the space/domains directory for this tenant."""
    return tenant_global_dir(tenant_id) / "space" / "domains"


def _meta_path(slug: str, tenant_id: str | None = None) -> Path:
    return domains_dir(tenant_id) / slug / "meta.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sanitize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _load_meta(slug: str, tenant_id: str | None = None) -> DomainMeta | None:
    path = _meta_path(slug, tenant_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return DomainMeta(
        slug=data.get("slug", slug),
        name=data.get("name", ""),
        description=data.get("description", ""),
        visibility=data.get("visibility", "public"),
        created_at=float(data.get("created_at", 0.0)),
        updated_at=float(data.get("updated_at", 0.0)),
        post_count=int(data.get("post_count", 0)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_slug(slug: str) -> str:
    """Validate and return slug. Raises ValueError if invalid."""
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid slug {slug!r}. Must match ^[a-z0-9][a-z0-9-]{{0,39}}$"
        )
    return slug


def list_domains(tenant_id: str | None = None) -> list[DomainMeta]:
    """Return all DomainMeta objects for the tenant, sorted by created_at."""
    base = domains_dir(tenant_id)
    if not base.exists():
        return []
    results: list[DomainMeta] = []
    for entry in base.iterdir():
        if entry.is_dir():
            meta = _load_meta(entry.name, tenant_id)
            if meta is not None:
                results.append(meta)
    results.sort(key=lambda m: m.created_at)
    return results


def get_domain(slug: str, tenant_id: str | None = None) -> DomainMeta | None:
    """Return DomainMeta for slug, or None if not found."""
    return _load_meta(slug, tenant_id)


def create_domain(
    slug: str,
    name: str,
    description: str = "",
    visibility: str = "public",
    tenant_id: str | None = None,
    license_max: int | None = None,
) -> DomainMeta:
    """Create a new domain.

    ADR-0094 / LIC-SPACE-DOM-TOCTOU-01: ``license_max`` (the per-tenant
    space_domains_max limit, None = unlimited) is enforced HERE, under the same
    ``_FLOCK_LOCK`` as the count + write, so concurrent creates cannot race past
    the cap (the route's pre-check is only an advisory fast-path). The effective
    cap is min(DOMAIN_MAX, license_max); a license-cap hit raises
    DomainLimitError(license_capped=True) → 402 at the HTTP layer.

    Raises:
      ValueError         — if slug format is invalid.
      DomainLimitError   — at the effective cap (license_capped marks which cap).
      FileExistsError    — if a domain with this slug already exists.
    """
    validate_slug(slug)

    with _FLOCK_LOCK:
        existing = list_domains(tenant_id)
        if license_max is not None and len(existing) >= license_max:
            raise DomainLimitError(license_capped=True)
        if len(existing) >= DOMAIN_MAX:
            raise DomainLimitError()

        # Check slug is not taken
        if any(m.slug == slug for m in existing):
            raise FileExistsError(f"Domain slug already exists: {slug!r}")

        now = time.time()
        meta = DomainMeta(
            slug=slug,
            name=_sanitize(name)[:64],
            description=_sanitize(description)[:300],
            visibility=visibility,
            created_at=now,
            updated_at=now,
            post_count=0,
        )
        _atomic_write(_meta_path(slug, tenant_id), asdict(meta))

    return meta


def update_domain(
    slug: str,
    tenant_id: str | None = None,
    **kwargs: Any,
) -> DomainMeta:
    """Load, update, and save a domain.

    Raises DomainNotFoundError if the domain does not exist.
    """
    with _FLOCK_LOCK:
        meta = _load_meta(slug, tenant_id)
        if meta is None:
            raise DomainNotFoundError(slug)

        _TEXT_FIELDS = {"name", "description"}
        _ALL_FIELDS = {"name", "description", "visibility", "post_count"}

        for key, value in kwargs.items():
            if key not in _ALL_FIELDS:
                continue
            if value is None:
                continue
            if key in _TEXT_FIELDS:
                value = _sanitize(str(value))
            setattr(meta, key, value)

        meta.updated_at = time.time()
        _atomic_write(_meta_path(slug, tenant_id), asdict(meta))

    return meta


def delete_domain(slug: str, tenant_id: str | None = None) -> bool:
    """Delete the entire domain directory. Returns False if not found."""
    import shutil

    domain_dir = domains_dir(tenant_id) / slug
    if not domain_dir.exists():
        return False
    shutil.rmtree(str(domain_dir), ignore_errors=True)
    return True


def increment_post_count(slug: str, tenant_id: str | None = None) -> None:
    """Thread-safe increment of post_count for the domain.

    Silently does nothing if the domain is not found.
    """
    with _FLOCK_LOCK:
        meta = _load_meta(slug, tenant_id)
        if meta is None:
            return
        meta.post_count += 1
        meta.updated_at = time.time()
        _atomic_write(_meta_path(slug, tenant_id), asdict(meta))


__all__ = [
    "DOMAIN_MAX",
    "DomainMeta",
    "DomainLimitError",
    "DomainNotFoundError",
    "domains_dir",
    "validate_slug",
    "list_domains",
    "get_domain",
    "create_domain",
    "update_domain",
    "delete_domain",
    "increment_post_count",
]
