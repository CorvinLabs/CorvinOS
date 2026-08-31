"""Landing-page endpoints — unauthenticated, read-only projection of
bundle data for the public hero/gallery (ADR-0037).

These endpoints are deliberately narrow: they return ONLY the fields
that are safe to surface to an anonymous visitor (name, description,
namespace, generic enable flags). Tenant-specific overrides are NOT
exposed here — those are owner-self-service after sign-in.

Security stance
---------------
* No auth dependency — these routes are designed for the marketing
  landing rendered before the login wall.
* No cookie / session inspection.
* No write paths.
* Bundle source only; user-override dirs are NOT walked.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter


_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
# Source-tree path; in a wheel install operator/* is vendored under
# corvin_console/_vendor/operator/* (and _REPO points outside site-packages where
# no operator/ exists), so the persona gallery was empty on a wheel install.
# Resolve to whichever layout actually has the files (path-audit 2026-06-25 #MED6).
_VENDOR_OPERATOR = _THIS_DIR.parent / "_vendor" / "operator"


def _resolve_bundle_dir() -> Path:
    repo = _REPO / "operator" / "cowork" / "personas"
    if repo.is_dir():
        return repo
    vendored = _VENDOR_OPERATOR / "cowork" / "personas"
    return vendored if vendored.is_dir() else repo


_BUNDLE_DIR = _resolve_bundle_dir()


router = APIRouter()


def _project_publishable(p: dict[str, Any]) -> dict[str, Any]:
    """Reduce a persona JSON to the subset that's safe for an
    unauthenticated visitor of the landing page."""
    return {
        "name":               p.get("name"),
        "description":        (p.get("description") or "").strip(),
        "tool_namespace":     p.get("tool_namespace"),
        "forge_enabled":      bool(p.get("forge_enabled")),
        "skill_forge_enabled": bool(p.get("skill_forge_enabled")),
        "ldd_preset":         p.get("ldd_preset"),
    }


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@router.get("/personas")
def landing_personas() -> dict[str, Any]:
    """Public projection of bundle personas for the landing-page gallery."""
    items: list[dict[str, Any]] = []
    if _BUNDLE_DIR.exists():
        for f in sorted(_BUNDLE_DIR.iterdir()):
            if f.suffix != ".json":
                continue
            body = _load(f)
            if body is None or not body.get("name"):
                continue
            items.append(_project_publishable(body))
    return {"count": len(items), "personas": items}
