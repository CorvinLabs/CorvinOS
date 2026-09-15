"""Layer 40 CorvinSpace — personal profile storage.

Storage: <tenant_global>/space/profile.json  (mode 0600)

Must NOT import anthropic (CI AST lint).
"""
from __future__ import annotations

import json
import os
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from .paths import tenant_global_dir  # type: ignore[import-not-found]
    from . import instance_identity  # type: ignore[import-not-found]
    from . import social_consent  # type: ignore[import-not-found]
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from paths import tenant_global_dir  # type: ignore[import-not-found]
    import instance_identity  # type: ignore[import-not-found]
    import social_consent  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class SpaceProfile:
    display_name: str    # max 64 chars
    bio: str             # max 500 chars
    contact_handle: str  # max 80 chars; no raw email
    website: str         # max 200 chars; URL or empty
    location: str        # max 100 chars; city/country, not coordinates
    created_at: float
    updated_at: float


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------


def profile_path(tenant_id: str | None = None) -> Path:
    """Return the path to space/profile.json for this tenant."""
    return tenant_global_dir(tenant_id) / "space" / "profile.json"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _sanitize(text: str) -> str:
    """NFKC normalize + strip whitespace."""
    return unicodedata.normalize("NFKC", text).strip()


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_profile(tenant_id: str | None = None) -> SpaceProfile | None:
    """Return SpaceProfile if profile.json exists, else None."""
    path = profile_path(tenant_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return SpaceProfile(
        display_name=data.get("display_name", ""),
        bio=data.get("bio", ""),
        contact_handle=data.get("contact_handle", ""),
        website=data.get("website", ""),
        location=data.get("location", ""),
        created_at=float(data.get("created_at", 0.0)),
        updated_at=float(data.get("updated_at", 0.0)),
    )


def save_profile(profile: SpaceProfile, tenant_id: str | None = None) -> None:
    """Atomically write profile.json with mode 0600."""
    _atomic_write(profile_path(tenant_id), asdict(profile))


def update_profile(tenant_id: str | None = None, **kwargs: Any) -> SpaceProfile:
    """Load profile (creating defaults if absent), apply kwargs, sanitize, and save.

    Only recognised SpaceProfile fields are accepted; unknown keys are ignored.
    Text fields go through NFKC normalisation + strip before saving.
    """
    now = time.time()
    existing = load_profile(tenant_id)
    if existing is None:
        existing = SpaceProfile(
            display_name="",
            bio="",
            contact_handle="",
            website="",
            location="",
            created_at=now,
            updated_at=now,
        )

    _TEXT_FIELDS = {"display_name", "bio", "contact_handle", "website", "location"}
    _FIELD_NAMES = set(_TEXT_FIELDS)

    for key, value in kwargs.items():
        if key not in _FIELD_NAMES:
            continue
        if value is None:
            continue
        if key in _TEXT_FIELDS:
            value = _sanitize(str(value))
        setattr(existing, key, value)

    existing.updated_at = now
    save_profile(existing, tenant_id)
    return existing


def get_social_actor_id(tenant_id: str | None = None) -> str | None:
    """Return the local instance_id if social consent is enabled, else None."""
    if not social_consent.is_consented(tenant_id):
        return None
    return instance_identity.get_instance_id()


__all__ = [
    "SpaceProfile",
    "profile_path",
    "load_profile",
    "save_profile",
    "update_profile",
    "get_social_actor_id",
]
