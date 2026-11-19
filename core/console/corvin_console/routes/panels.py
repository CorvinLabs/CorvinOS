"""``/v1/console/panels`` — AI-generated Console panels (ADR-0366).

CorvinOS is an AI OS: the operator DESCRIBES the panel they want and the KI builds
it, rather than hand-writing HTML. This is the store + serving surface for those
generated panels. The chat worker (chat_runtime, Claude Code) generates a panel's
HTML and calls POST here to install it; the shell lists GET /panels and mounts each
as an iframe panel (through the same PanelHost as every other panel).

Storage is tenant-scoped: <tenant_global>/console_panels/<id>/{index.html, meta.json}.
The id is validated as a safe path segment. Generated HTML is served same-origin and
embedded in a sandboxed iframe (allow-scripts) — it is first-party (the operator's own
KI) but still isolated. Mutations require CSRF and emit audit events per ADR-0299
(console.panel_created, console.panel_deleted — metadata only, no HTML content).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import audit as _audit
from .. import auth as session_auth
from ..deps import require_session, require_csrf
from .license_gates import require_forge_capability

try:
    from forge import paths as _forge_paths  # type: ignore
except Exception:  # noqa: BLE001 — degrade to a no-store view, never 500 the import
    _forge_paths = None  # type: ignore[assignment]

router = APIRouter(prefix="/panels", tags=["console-panels"])

#: Response headers for every route that returns stored panel HTML. The HTML is
#: LLM-generated: PanelHost embeds it in a sandboxed iframe, but the same URL opened
#: top-level (a link, a new tab) would otherwise run same-origin with the session
#: cookie. ``sandbox allow-scripts`` makes the browser treat the document as an
#: opaque origin wherever it is loaded, so it can neither read the cookie nor call
#: the console API as the operator. Same shape as the compute artifact download.
PANEL_HTML_HEADERS: dict[str, str] = {
    "Content-Security-Policy": "sandbox allow-scripts",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}

#: A panel id is a directory name and a URL segment — keep it safe on both.
# Used with ``fullmatch``: ``$`` also matches before a trailing newline, so
# ``match`` admitted "abc\n" as an id (and as part of a file name).
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,48}")


def _panels_dir(tenant_id: str) -> Path | None:
    if _forge_paths is None:
        return None
    d = Path(_forge_paths.tenant_global_dir(tenant_id)) / "console_panels"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_id(panel_id: str) -> str:
    if not _ID_RE.fullmatch(panel_id or ""):
        raise HTTPException(status_code=400, detail="invalid panel id (use a-z, 0-9, -)")
    return panel_id


def slugify(text: str) -> str:
    """A safe panel id from a human title."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "panel"


def extract_title(html: str) -> str:
    """Best-effort panel title from generated HTML (<title>/<h1>/<h2>)."""
    for pat in (r"<title[^>]*>(.*?)</title>", r"<h1[^>]*>(.*?)</h1>", r"<h2[^>]*>(.*?)</h2>"):
        m = re.search(pat, html or "", re.IGNORECASE | re.DOTALL)
        if m:
            t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if t:
                return t[:80]
    return "Panel"


#: A declared data path: a plain relative API path, no query, no dots.
_DATA_PATH_RE = re.compile(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")
MAX_DATA_PATHS = 16


def extract_data_paths(html: str) -> list[str]:
    """The console paths a panel declares it reads.

    ``<meta name="corvin-panel-data" content="/sessions /cost/summary">`` (space-
    or comma-separated). A sandboxed panel cannot call the API; it asks its host
    (``corvin:panel:fetch``), and the host answers only for a path that is declared
    here AND on its own allowlist (``PANEL_READABLE_PATHS`` in
    ``web-next/src/panels/protocol.ts``). This is the declaration half: shape
    only, de-duplicated, at most :data:`MAX_DATA_PATHS`. It grants nothing.
    """
    out: list[str] = []
    for tag in re.findall(r"<meta\b[^>]*>", html or "", re.IGNORECASE):
        if not re.search(r"""\bname\s*=\s*(["'])corvin-panel-data\1""", tag, re.IGNORECASE):
            continue
        m = re.search(r"""\bcontent\s*=\s*(["'])([^"']*)\1""", tag, re.IGNORECASE)
        if not m:
            continue
        for path in re.split(r"[\s,]+", m.group(2)):
            if path and _DATA_PATH_RE.fullmatch(path) and len(path) <= 200 and path not in out:
                out.append(path)
                if len(out) >= MAX_DATA_PATHS:
                    return out
    return out


def store_panel(
    tenant_id: str,
    panel_id: str,
    title: str,
    html: str,
    *,
    nav_group: str = "ai",
    icon: str = "Sparkles",
    created_by: str = "ai",
) -> dict:
    """Write a panel to the store. SSOT used by both the POST route and the chat
    worker's post-turn install (ADR-0366). Raises ValueError on a bad id / no store."""
    if not _ID_RE.fullmatch(panel_id or ""):
        raise ValueError(f"invalid panel id: {panel_id!r}")
    d = _panels_dir(tenant_id)
    if d is None:
        raise ValueError("panel store unavailable (forge paths absent)")
    pdir = d / panel_id
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "index.html").write_text(html, encoding="utf-8")
    meta = {
        "id": panel_id,
        "title": title,
        "nav_group": nav_group,
        "icon": icon,
        "created_at": time.time(),
        "created_by": created_by,
        # The panel's manifest declaration, read from the HTML itself so the chat
        # worker's install and the POST route record it the same way.
        "data_paths": extract_data_paths(html),
    }
    (pdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


class PanelCreate(BaseModel):
    id: str = Field(..., description="stable id / route segment, e.g. 'recent-sessions'")
    title: str = Field(..., max_length=80)
    html: str = Field(..., description="the full panel HTML/JS the KI generated")
    nav_group: str = Field("build", max_length=40)
    icon: str = Field("Sparkles", max_length=40)


@router.get("")
async def list_panels(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """List the AI-generated panels for this tenant (metadata only)."""
    d = _panels_dir(rec.tenant_id)
    if d is None:
        return {"panels": []}
    panels: list[dict] = []
    for meta_file in sorted(d.glob("*/meta.json")):
        try:
            panels.append(json.loads(meta_file.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — a broken entry must not sink the list
            continue
    return {"panels": panels}


@router.post("")
async def create_panel(
    body: PanelCreate,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _: Annotated[session_auth.SessionRecord, Depends(require_forge_capability)],
) -> dict:
    """Install a panel directly (corvin_operator/API path). The chat worker uses the
    post-turn workdir scan (chat_runtime) instead, but this endpoint is the SSOT
    surface and what the E2E test drives."""
    _validate_id(body.id)
    try:
        meta = store_panel(
            rec.tenant_id, body.id, body.title, body.html,
            nav_group=body.nav_group, icon=body.icon,
            created_by=getattr(rec, "fingerprint", None) or "operator",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # ADR-0299 + ADR-0366: audit trail for panel creation (metadata only, no HTML)
    try:
        _audit.panel_created(
            tenant_id=rec.tenant_id,
            panel_id=body.id,
            title=body.title,
            nav_group=body.nav_group,
            icon=body.icon,
            sid_fingerprint=getattr(rec, "sid_fingerprint", "unknown"),
            created_by=meta["created_by"],
        )
    except Exception:  # pragma: no cover — audit unavailable should not block the panel
        pass

    return {"ok": True, "panel": meta, "route": f"/app/{body.id}"}


@router.get("/{panel_id}/index.html", response_class=HTMLResponse)
def serve_panel(
    panel_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> HTMLResponse:
    """Serve a generated panel's HTML (embedded by PanelHost as a sandboxed iframe)."""
    _validate_id(panel_id)
    d = _panels_dir(rec.tenant_id)
    html_file = (d / panel_id / "index.html") if d else None
    if not html_file or not html_file.is_file():
        raise HTTPException(status_code=404, detail="panel not found")
    return HTMLResponse(html_file.read_text(encoding="utf-8"), headers=PANEL_HTML_HEADERS)


@router.get("/{panel_id}/manifest")
def panel_manifest(
    panel_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """A generated panel's manifest: its metadata and declared data paths.

    Read by PanelHost before it answers the panel's data requests. A panel
    installed before the declaration existed has no ``data_paths`` in its
    ``meta.json``; it is then read from the stored HTML (nothing is written).
    """
    _validate_id(panel_id)
    d = _panels_dir(rec.tenant_id)
    pdir = (d / panel_id) if d else None
    html_file = (pdir / "index.html") if pdir else None
    if not html_file or not html_file.is_file():
        raise HTTPException(status_code=404, detail="panel not found")
    meta: dict[str, Any] = {}
    try:
        loaded = json.loads((pdir / "meta.json").read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            meta = loaded
    except (OSError, ValueError):
        pass
    declared = meta.get("data_paths")
    if not isinstance(declared, list):
        declared = extract_data_paths(html_file.read_text(encoding="utf-8"))
    return {
        "id": panel_id,
        "title": meta.get("title") if isinstance(meta.get("title"), str) else "Panel",
        "data_paths": [p for p in declared if isinstance(p, str) and _DATA_PATH_RE.fullmatch(p)][:MAX_DATA_PATHS],
    }


@router.delete("/{panel_id}")
async def delete_panel(
    panel_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    """Delete a generated panel."""
    _validate_id(panel_id)
    d = _panels_dir(rec.tenant_id)
    pdir = (d / panel_id) if d else None
    if not pdir or not pdir.is_dir():
        raise HTTPException(status_code=404, detail="panel not found")
    for f in pdir.glob("*"):
        f.unlink(missing_ok=True)
    pdir.rmdir()

    # ADR-0299 + ADR-0366: audit trail for panel deletion
    try:
        _audit.panel_deleted(
            tenant_id=rec.tenant_id,
            panel_id=panel_id,
            sid_fingerprint=getattr(rec, "sid_fingerprint", "unknown"),
            deleted_by=getattr(rec, "fingerprint", None) or "operator",
        )
    except Exception:  # pragma: no cover — audit unavailable should not block the delete
        pass

    return {"ok": True, "deleted": panel_id}
