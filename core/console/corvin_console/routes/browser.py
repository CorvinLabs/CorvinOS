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
    None allowlist → all hosts allowed (still audited).
    Raises on config parse errors — callers must treat this as fail-closed
    (a broken tenant.corvin.yaml must block session creation, not silently
    fall back to unrestricted egress)."""
    import yaml  # type: ignore  # raised, not swallowed
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


def _notify_resolver(tenant_id: str) -> tuple[str | None, str | None]:
    """ADR-0189: (channel, chat_id) to proactively voice-notify for THIS
    tenant's browser-agent pauses (needs_login / needs_approval), from
    spec.browser.notify_channel / notify_chat_id in tenant.corvin.yaml.

    Same manual-YAML-edit pattern as the allowlist above (no UI, no API) —
    there is no automatic mapping from a console chat session to a
    messenger identity (a Discord conversation and a console web session
    are architecturally separate systems), so an operator who wants proactive
    voice notifications for browser pauses opts in explicitly here. Absent
    or malformed config -> (None, None), which notify.notify_pause() treats
    as "no routing context, skip silently" — never an error."""
    try:
        import yaml  # type: ignore
        cfg = _forge_paths.tenant_global_dir(tenant_id) / "tenant.corvin.yaml"
        if not cfg.exists():
            return (None, None)
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        spec = data.get("spec", data)
        br = spec.get("browser", {}) if isinstance(spec.get("browser"), dict) else {}
        channel = br.get("notify_channel")
        chat_id = br.get("notify_chat_id")
        channel = channel if isinstance(channel, str) and channel else None
        chat_id = chat_id if chat_id else None
        return (channel, chat_id)
    except Exception:  # noqa: BLE001 — best-effort; never block on this
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

class AgentReq(BaseModel):
    task: str
    max_steps: int = 12
    model_config = {"extra": "forbid"}

class KeyReq(BaseModel):
    key: str
    model_config = {"extra": "forbid"}

class SelectReq(BaseModel):
    index: int
    value: str
    model_config = {"extra": "forbid"}

class UploadReq(BaseModel):
    index: int
    filename: str
    model_config = {"extra": "forbid"}

class DragReq(BaseModel):
    from_index: int
    to_index: int
    model_config = {"extra": "forbid"}

class SwitchTabReq(BaseModel):
    index: int
    model_config = {"extra": "forbid"}


async def _act(coro):
    from ..browser import BrowserActionError
    try:
        return await coro
    except BrowserActionError as e:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=str(e)) from e


def _default_headless() -> bool:
    """Open a VISIBLE window when a desktop display is available (so the operator
    sees the browser on their screen); fall back to headless on a headless host
    (the console live-view screencast still shows every action either way).
    Override with CORVIN_BROWSER_HEADLESS=1 (force headless) / =0 (force visible)."""
    import os
    forced = os.environ.get("CORVIN_BROWSER_HEADLESS")
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return not has_display


class CreateSessionReq(BaseModel):
    headless: bool | None = None      # None → auto (visible if a display exists)
    model_config = {"extra": "forbid"}


# ── session lifecycle ─────────────────────────────────────────────────────────
@router.post("/browser/session")
async def create_session(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: CreateSessionReq | None = None,
) -> dict[str, Any]:
    headless = body.headless if (body and body.headless is not None) else _default_headless()
    try:
        sid = await _mgr().create(rec.tenant_id, headless=headless,
                                   owner_fingerprint=rec.sid_fingerprint)
    except RuntimeError as e:   # session cap reached or allowlist config error
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
    await _act(_mgr().close(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint))
    return {"closed": sid}


# ── actions (tool surface) ────────────────────────────────────────────────────
def _owned_session(rec: session_auth.SessionRecord, sid: str):
    """Look up a browser session, verifying the caller owns it — prevents one
    console user from driving or observing another user's browser session."""
    return _mgr().session(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint)


@router.post("/browser/{sid}/navigate")
async def navigate(sid: str, body: NavigateReq,
                   rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    obs = await _act(s.navigate(body.url))
    return obs.to_dict()

@router.post("/browser/{sid}/observe")
async def observe(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    obs = await _act(s.observe())
    return obs.to_dict()

@router.post("/browser/{sid}/click")
async def click(sid: str, body: IndexReq,
                rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    await _act(s.click(body.index))
    return {"ok": True}

@router.post("/browser/{sid}/fill")
async def fill(sid: str, body: FillReq,
               rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    await _act(s.fill(body.index, body.text))
    return {"ok": True}

@router.post("/browser/{sid}/fill_secret")
async def fill_secret(sid: str, body: FillSecretReq,
                      rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    await _act(s.fill_secret(body.index, body.vault_key))
    return {"ok": True}

@router.post("/browser/{sid}/read")
async def read(sid: str, body: ReadReq,
               rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    txt = await _act(s.read(body.index))
    return {"text": txt}

@router.post("/browser/{sid}/scroll")
async def scroll(sid: str, body: ScrollReq,
                 rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    await _act(s.scroll(body.direction))
    return {"ok": True}

@router.post("/browser/{sid}/back")
async def back(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    obs = await _act(s.back())
    return obs.to_dict()


# ── ADR-0183 S2: expanded action surface ──────────────────────────────────────
@router.post("/browser/{sid}/hover")
async def hover(sid: str, body: IndexReq,
                rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    await _act(s.hover(body.index))
    return {"ok": True}

@router.post("/browser/{sid}/key")
async def key(sid: str, body: KeyReq,
              rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    await _act(s.key(body.key))
    return {"ok": True}

@router.post("/browser/{sid}/select_option")
async def select_option(sid: str, body: SelectReq,
                        rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    await _act(s.select_option(body.index, body.value))
    return {"ok": True}

@router.post("/browser/{sid}/upload_file")
async def upload_file(sid: str, body: UploadReq,
                      rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    await _act(s.upload_file(body.index, body.filename))
    return {"ok": True}

@router.post("/browser/{sid}/drag")
async def drag(sid: str, body: DragReq,
               rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    await _act(s.drag(body.from_index, body.to_index))
    return {"ok": True}

@router.post("/browser/{sid}/tabs")
async def tabs(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    return {"tabs": await _act(s.tabs())}

@router.post("/browser/{sid}/switch_tab")
async def switch_tab(sid: str, body: SwitchTabReq,
                     rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    obs = await _act(s.switch_tab(body.index))
    return obs.to_dict()

@router.post("/browser/{sid}/extract_table")
async def extract_table(sid: str, body: IndexReq,
                        rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    return await _act(s.extract_table(body.index))

@router.post("/browser/{sid}/extract_form_schema")
async def extract_form_schema(sid: str,
                              rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    s = _owned_session(rec, sid)
    return {"forms": await _act(s.extract_form_schema())}

@router.post("/browser/{sid}/screenshot")
async def screenshot(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    """Return the current viewport as a base64 JPEG data URL (mark overlay
    painted on) — the tool-surface counterpart to the live-view frame.jpg GET,
    so a WorkerEngine driving browser.* can fetch a screenshot too."""
    s = _owned_session(rec, sid)
    png = await _act(s.screenshot(marks=True))
    return {"data_url": s.screenshot_data_url(png)}


# ── live view ─────────────────────────────────────────────────────────────────
@router.get("/browser/{sid}/frame.jpg")
async def frame(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_session)]):
    try:
        png = _mgr().frame(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint)
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
        items = _mgr().actions(rec.tenant_id, sid, since=since,
                               owner_fingerprint=rec.sid_fingerprint)
        pending = _mgr().pending(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint)
        nxt = _mgr().next_seq(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"actions": items, "pending": pending, "next": nxt}

@router.post("/browser/{sid}/confirm")
async def confirm(sid: str, body: ConfirmReq,
                  rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    try:
        ok = _mgr().resolve_confirm(rec.tenant_id, sid, body.id, body.approved,
                                    owner_fingerprint=rec.sid_fingerprint)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"resolved": ok}

@router.post("/browser/{sid}/pause")
async def pause(sid: str, body: PauseReq,
                rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    try:
        _mgr().set_paused(rec.tenant_id, sid, body.paused,
                          owner_fingerprint=rec.sid_fingerprint)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"paused": body.paused}


# ── agent loop (natural-language "give it a note", ADR-0182 Part A) ────────────
@router.post("/browser/{sid}/agent")
async def run_agent(sid: str, body: AgentReq,
                    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    task = (body.task or "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="empty task")
    try:
        started = _mgr().start_agent(rec.tenant_id, sid, task,
                                     max_steps=max(1, min(body.max_steps, 30)),
                                     owner_fingerprint=rec.sid_fingerprint)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if not started:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT,
                            detail="an agent is already running for this session")
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="browser.agent.start", target_kind="browser_session", target_id=sid)
    return {"started": True}


@router.post("/browser/{sid}/agent/stop")
async def stop_agent(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    try:
        _mgr().stop_agent(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"stopped": True}


@router.post("/browser/{sid}/agent/continue")
async def continue_agent(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]):
    """ADR-0189: resume a session paused on needs_login/needs_approval — the
    live-view equivalent of the chat `/browser continue <sid>` command, so
    the "weiter" voice command works from the Browser page itself."""
    try:
        resumed = _mgr().continue_agent(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if not resumed:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT,
                            detail="nothing to continue (no prior paused task, or agent already running)")
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="browser.agent.continue", target_kind="browser_session", target_id=sid)
    return {"resumed": True}
