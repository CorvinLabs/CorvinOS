"""Browser automation REST surface + live view (ADR-0182 M3/M4).

This router is BOTH:
  * the tool surface an engine drives (navigate/observe/click/fill/…), and
  * the live-view backend the user watches (screencast frame + action log +
    confirm prompts + pause/take-over).

Auth: reads require a session, mutations require CSRF. Tenant scoping comes from
the authenticated SessionRecord (never an env var).
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status as http_status
from pydantic import BaseModel

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session
from .. import _bootstrap

logger = logging.getLogger("corvin.routes.browser")
_forge_paths = _bootstrap.forge_paths

router = APIRouter()

# ── manager singleton (wired with the compliance hooks) ──────────────────────

def _home(tenant_id: str):
    return _forge_paths.tenant_home(tenant_id) / "browser"


def _audit_fn(*, tenant_id: str, event: str, details: dict) -> None:
    try:
        console_audit._emit(event, tenant_id=tenant_id, details=details)
    except Exception:  # noqa: BLE001 — audit is best-effort, never blocks an action
        logger.debug("browser audit emit skipped")


def _vault_resolver(tenant_id: str, key: str):
    """Best-effort vault lookup for fill_secret. Returns None if unavailable —
    fill_secret then fails cleanly rather than typing a placeholder."""
    try:
        from forge import secret_vault  # type: ignore
        out = secret_vault.resolve_secrets([key], tenant_id=tenant_id)  # type: ignore[call-arg]
        if isinstance(out, dict):
            v = out.get(key)
            return v if isinstance(v, str) and v else None
    except Exception:  # noqa: BLE001
        pass
    return None


def _allowlist_resolver(tenant_id: str):
    """(allowlist, forbidden) from spec.browser in tenant.corvin.yaml.
    None allowlist → all hosts allowed (still audited)."""
    try:
        import yaml  # type: ignore
        cfg = _forge_paths.tenant_global_dir(tenant_id) / "tenant.corvin.yaml"
        if not cfg.exists():
            return (None, None)
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        spec = data.get("spec", data)
        br = spec.get("browser", {}) if isinstance(spec.get("browser"), dict) else {}
        allow = br.get("allowed_hosts")
        forbid = br.get("forbidden_hosts")
        allow = allow if isinstance(allow, list) and allow else None
        forbid = forbid if isinstance(forbid, list) and forbid else None
        return (allow, forbid)
    except Exception:  # noqa: BLE001
        return (None, None)


_manager = None

def _mgr():
    global _manager
    if _manager is None:
        from ..browser import BrowserSessionManager
        _manager = BrowserSessionManager(
            home_resolver=_home,
            audit_fn=_audit_fn,
            vault_resolver=_vault_resolver,
            allowlist_resolver=_allowlist_resolver,
        )
    return _manager


# ── request models ───────────────────────────────────────────────────────────
class NavigateReq(BaseModel):
    url: str
    model_config = {"extra": "forbid"}

class IndexReq(BaseModel):
    index: int
    model_config = {"extra": "forbid"}

class FillReq(BaseModel):
    index: int
    text: str
    model_config = {"extra": "forbid"}

class FillSecretReq(BaseModel):
    index: int
    vault_key: str
    model_config = {"extra": "forbid"}

class ReadReq(BaseModel):
    index: int | None = None
    model_config = {"extra": "forbid"}

class ScrollReq(BaseModel):
    direction: str = "down"
    model_config = {"extra": "forbid"}

class ConfirmReq(BaseModel):
    id: str
    approved: bool
    model_config = {"extra": "forbid"}

class PauseReq(BaseModel):
    paused: bool
    model_config = {"extra": "forbid"}


async def _act(coro):
    from ..browser import BrowserActionError
    try:
        return await coro
    except BrowserActionError as e:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=str(e)) from e


# ── session lifecycle ─────────────────────────────────────────────────────────
@router.post("/browser/session")
async def create_session(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    try:
        sid = await _mgr().create(rec.tenant_id, headless=True)
    except RuntimeError as e:   # session cap reached
        raise HTTPException(status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=str(e)) from e
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="browser.session.create", target_kind="browser_session", target_id=sid)
    return {"session": sid}


@router.post("/browser/{sid}/close")
async def close_session(
    sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    await _act(_mgr().close(rec.tenant_id, sid))
    return {"closed": sid}


# ── actions (tool surface) ────────────────────────────────────────────────────
@router.post("/browser/{sid}/navigate")
async def navigate(sid: str, body: NavigateReq,
                   rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _mgr().session(rec.tenant_id, sid)
    obs = await _act(s.navigate(body.url))
    return obs.to_dict()

@router.post("/browser/{sid}/observe")
async def observe(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _mgr().session(rec.tenant_id, sid)
    obs = await _act(s.observe())
    return obs.to_dict()

@router.post("/browser/{sid}/click")
async def click(sid: str, body: IndexReq,
                rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _mgr().session(rec.tenant_id, sid)
    await _act(s.click(body.index))
    return {"ok": True}

@router.post("/browser/{sid}/fill")
async def fill(sid: str, body: FillReq,
               rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _mgr().session(rec.tenant_id, sid)
    await _act(s.fill(body.index, body.text))
    return {"ok": True}

@router.post("/browser/{sid}/fill_secret")
async def fill_secret(sid: str, body: FillSecretReq,
                      rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _mgr().session(rec.tenant_id, sid)
    await _act(s.fill_secret(body.index, body.vault_key))
    return {"ok": True}

@router.post("/browser/{sid}/read")
async def read(sid: str, body: ReadReq,
               rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _mgr().session(rec.tenant_id, sid)
    txt = await _act(s.read(body.index))
    return {"text": txt}

@router.post("/browser/{sid}/scroll")
async def scroll(sid: str, body: ScrollReq,
                 rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _mgr().session(rec.tenant_id, sid)
    await _act(s.scroll(body.direction))
    return {"ok": True}

@router.post("/browser/{sid}/back")
async def back(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _mgr().session(rec.tenant_id, sid)
    obs = await _act(s.back())
    return obs.to_dict()


# ── live view ─────────────────────────────────────────────────────────────────
@router.get("/browser/{sid}/frame.jpg")
async def frame(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_session)]):
    try:
        png = _mgr().frame(rec.tenant_id, sid)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if png is None:
        return Response(status_code=204)
    return Response(content=png, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})

@router.get("/browser/{sid}/actions")
async def actions(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
                  since: int = Query(0, ge=0)):
    try:
        items = _mgr().actions(rec.tenant_id, sid, since=since)
        pending = _mgr().pending(rec.tenant_id, sid)
        nxt = _mgr().next_seq(rec.tenant_id, sid)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"actions": items, "pending": pending, "next": nxt}

@router.post("/browser/{sid}/confirm")
async def confirm(sid: str, body: ConfirmReq,
                  rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    try:
        ok = _mgr().resolve_confirm(rec.tenant_id, sid, body.id, body.approved)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"resolved": ok}

@router.post("/browser/{sid}/pause")
async def pause(sid: str, body: PauseReq,
                rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    try:
        _mgr().set_paused(rec.tenant_id, sid, body.paused)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"paused": body.paused}
