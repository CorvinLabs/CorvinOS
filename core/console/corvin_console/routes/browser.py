"""Browser automation REST surface + live view (ADR-0182 M3/M4).

This router is BOTH:
  * the tool surface an engine drives (navigate/observe/click/fill/…), and
  * the live-view backend the user watches (screencast frame + action log +
    confirm prompts + pause/take-over).

Auth: reads require a session, mutations require CSRF — enforced via
``require_session_or_token``/``require_csrf_or_token`` (ADR-0193), which
accept EITHER the SPA's cookie session (unchanged) OR a short-lived internal
bearer token (``..browser.internal_auth``) minted per chat-turn for the
native ``corvin-browser`` MCP tool, which calls this same router over
loopback HTTP so it drives the SAME live sessions the live-view watches.
Tenant scoping comes from the resulting SessionRecord either way, never an
env var.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status as http_status
from pydantic import BaseModel

from .. import audit as console_audit
from .. import auth as session_auth
from ..browser.internal_auth import require_csrf_or_token, require_session_or_token
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


def _notify_fn(tenant_id: str, *, text: str) -> None:
    """ADR-0189: best-effort proactive voice notification for a browser-agent
    pause (needs_login / needs_approval), on top of the in-chat text delta /
    action-log entry already recorded. No-ops silently if the tenant has no
    notify routing configured — this is an ADDITION, never a required
    delivery path. Wired into BrowserSessionManager itself (not a specific
    caller's polling loop) so it fires for ANY start_agent() caller."""
    try:
        from ..browser import notify as _br_notify
        channel, chat_id = _notify_resolver(tenant_id)
        _br_notify.notify_pause(channel=channel, chat_id=chat_id, tenant_id=tenant_id,
                                label="browser task", text=text)
    except Exception:  # noqa: BLE001 — never let a notify failure break the agent loop
        logger.debug("browser pause notify failed", exc_info=True)


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
            notify_fn=_notify_fn,
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
    # ADR-0189/ADR-0193: host(s) the CALLER already deemed in-scope (e.g. the
    # exact site the user's own request named) get ephemeral, this-session-
    # only navigation auto-approval — never persisted, never merged into the
    # tenant allowlist. Previously only reachable via chat.py's direct
    # BrowserSessionManager.create() call (bypassing this REST route
    # entirely); the corvin-browser MCP tool has no such back door, since it
    # must go through this same REST API as any other caller — so the field
    # is now accepted here too. An empty/omitted list changes nothing.
    task_scoped_hosts: list[str] | None = None
    # ADR-0200: when set, attach to the user's OWN Chrome at this CDP endpoint
    # (a ws:// URL from a debug Chrome the USER started) instead of launching an
    # empty Chromium. Gated on an active `real-chrome` attach consent — refused
    # otherwise. Omitted/None keeps the default launched-Chromium behaviour.
    cdp_endpoint: str | None = None
    model_config = {"extra": "forbid"}


# ── session lifecycle ─────────────────────────────────────────────────────────
@router.post("/browser/session")
async def create_session(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)],
    body: CreateSessionReq | None = None,
) -> dict[str, Any]:
    headless = body.headless if (body and body.headless is not None) else _default_headless()
    task_scoped_hosts = (body.task_scoped_hosts or None) if body else None
    cdp_endpoint = (body.cdp_endpoint or None) if body else None
    if cdp_endpoint:
        # ADR-0200: attaching to the user's real logged-in Chrome requires an
        # active, non-expired `real-chrome` consent — fail-closed otherwise.
        from ..browser import attach_consent as _ac  # noqa: PLC0415
        if not _ac.active(rec.tenant_id):
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=("Attaching to your real Chrome needs an active "
                        "'real-chrome' consent. Grant it in the console "
                        "(Browser → Attach to my Chrome), then retry."))
        # Real-login attach is ALWAYS visible — the user must see it act; a
        # headless attach would hide actions on their bank/mail sessions.
        headless = False
    try:
        sid = await _mgr().create(rec.tenant_id, headless=headless,
                                   owner_fingerprint=rec.sid_fingerprint,
                                   task_scoped_hosts=task_scoped_hosts,
                                   cdp_endpoint=cdp_endpoint)
    except RuntimeError as e:   # session cap reached or allowlist config error
        raise HTTPException(status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=str(e)) from e
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="browser.session.create", target_kind="browser_session", target_id=sid)
    return {"session": sid}


@router.post("/browser/{sid}/close")
async def close_session(
    sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)],
) -> dict[str, Any]:
    await _act(_mgr().close(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint))
    return {"closed": sid}


# ── ADR-0200: real-chrome attach consent + launch helper ─────────────────────

class AttachConsentReq(BaseModel):
    ttl_s: int | None = None      # clamped to [60s, 12h]; None → 1h default
    model_config = {"extra": "forbid"}


@router.post("/browser/attach/consent")
async def grant_attach_consent(
    body: AttachConsentReq | None,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)],
) -> dict[str, Any]:
    """Grant (or refresh) the real-chrome attach consent for this tenant."""
    from ..browser import attach_consent as _ac  # noqa: PLC0415
    ttl = body.ttl_s if body else None
    expires_at = _ac.grant(rec.tenant_id, ttl, audit_fn=_audit_fn)
    return {"active": True, "expires_at": expires_at,
            "remaining_s": _ac.status(rec.tenant_id)["remaining_s"]}


@router.delete("/browser/attach/consent")
async def revoke_attach_consent(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)],
) -> dict[str, Any]:
    from ..browser import attach_consent as _ac  # noqa: PLC0415
    _ac.revoke(rec.tenant_id, audit_fn=_audit_fn)
    return {"active": False}


@router.get("/browser/attach/consent")
async def attach_consent_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session_or_token)],
) -> dict[str, Any]:
    from ..browser import attach_consent as _ac  # noqa: PLC0415
    return _ac.status(rec.tenant_id)


class ConfirmModeReq(BaseModel):
    mode: str                      # "confirm-each" | "watch"
    ttl_s: int | None = None       # watch-mode TTL, clamped to [60s, 30m]
    model_config = {"extra": "forbid"}


@router.post("/browser/attach/confirm-mode")
async def set_confirm_mode(
    body: ConfirmModeReq,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)],
) -> dict[str, Any]:
    """Q3: confirm-each (default) or watch-mode-with-hard-TTL. Watch-mode never
    disables audit or egress — it only suppresses the interactive prompt, and
    only on attached (real-login) sessions."""
    from ..browser import confirm_mode as _cm  # noqa: PLC0415
    if body.mode == "watch":
        _cm.set_watch(rec.tenant_id, body.ttl_s, audit_fn=_audit_fn)
    elif body.mode == "confirm-each":
        _cm.set_confirm_each(rec.tenant_id, audit_fn=_audit_fn)
    else:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                            detail="mode must be 'confirm-each' or 'watch'")
    return _cm.status(rec.tenant_id)


@router.get("/browser/attach/confirm-mode")
async def get_confirm_mode(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session_or_token)],
) -> dict[str, Any]:
    from ..browser import confirm_mode as _cm  # noqa: PLC0415
    return _cm.status(rec.tenant_id)


@router.get("/browser/attach/launch-command")
async def attach_launch_command(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session_or_token)],
    port: int = 9222,
    profile: str | None = None,
) -> dict[str, Any]:
    """The exact per-OS command the USER runs to start a debug Chrome CorvinOS
    can attach to (Q4: we never launch it for them)."""
    from ..browser.session import cdp_launch_command  # noqa: PLC0415
    if port < 1024 or port > 65535:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                            detail="port must be 1024–65535")
    return {"command": cdp_launch_command(port, profile), "port": port}


# ── actions (tool surface) ────────────────────────────────────────────────────
def _owned_session(rec: session_auth.SessionRecord, sid: str):
    """Look up a browser session, verifying the caller owns it — prevents one
    console user from driving or observing another user's browser session."""
    return _mgr().session(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint)


@router.post("/browser/{sid}/navigate")
async def navigate(sid: str, body: NavigateReq,
                   rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    # ADR-0193 adversarial-review finding: this route's `confirm_cross_host`
    # was always the BrowserSession default (False) — correct for a HUMAN
    # typing a URL into the SPA's own bar (that IS the informed consent,
    # per BrowserSession.navigate's own docstring), but the corvin-browser
    # MCP tool also calls this exact route for an LLM-DECIDED navigation,
    # which is exactly the indirect-prompt-injection surface ADR-0187/0189
    # built the confirm for. `rec.is_internal_tool` (set only for the
    # token-authenticated MCP-tool path, never for a real cookie session)
    # tells the two callers apart without weakening the manual-operator path.
    obs = await _act(s.navigate(body.url, confirm_cross_host=rec.is_internal_tool))
    return obs.to_dict()

@router.post("/browser/{sid}/observe")
async def observe(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    obs = await _act(s.observe())
    return obs.to_dict()

@router.post("/browser/{sid}/click")
async def click(sid: str, body: IndexReq,
                rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    await _act(s.click(body.index))
    return {"ok": True}

@router.post("/browser/{sid}/fill")
async def fill(sid: str, body: FillReq,
               rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    await _act(s.fill(body.index, body.text))
    return {"ok": True}

@router.post("/browser/{sid}/fill_secret")
async def fill_secret(sid: str, body: FillSecretReq,
                      rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    await _act(s.fill_secret(body.index, body.vault_key))
    return {"ok": True}

@router.post("/browser/{sid}/read")
async def read(sid: str, body: ReadReq,
               rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    txt = await _act(s.read(body.index))
    return {"text": txt}

@router.post("/browser/{sid}/scroll")
async def scroll(sid: str, body: ScrollReq,
                 rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    await _act(s.scroll(body.direction))
    return {"ok": True}

@router.post("/browser/{sid}/back")
async def back(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    obs = await _act(s.back())
    return obs.to_dict()


# ── ADR-0183 S2: expanded action surface ──────────────────────────────────────
@router.post("/browser/{sid}/hover")
async def hover(sid: str, body: IndexReq,
                rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    await _act(s.hover(body.index))
    return {"ok": True}

@router.post("/browser/{sid}/key")
async def key(sid: str, body: KeyReq,
              rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    await _act(s.key(body.key))
    return {"ok": True}

@router.post("/browser/{sid}/select_option")
async def select_option(sid: str, body: SelectReq,
                        rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    await _act(s.select_option(body.index, body.value))
    return {"ok": True}

@router.post("/browser/{sid}/upload_file")
async def upload_file(sid: str, body: UploadReq,
                      rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    await _act(s.upload_file(body.index, body.filename))
    return {"ok": True}

@router.post("/browser/{sid}/drag")
async def drag(sid: str, body: DragReq,
               rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    await _act(s.drag(body.from_index, body.to_index))
    return {"ok": True}

@router.post("/browser/{sid}/tabs")
async def tabs(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    return {"tabs": await _act(s.tabs())}

@router.post("/browser/{sid}/switch_tab")
async def switch_tab(sid: str, body: SwitchTabReq,
                     rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    obs = await _act(s.switch_tab(body.index))
    return obs.to_dict()

@router.post("/browser/{sid}/extract_table")
async def extract_table(sid: str, body: IndexReq,
                        rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    return await _act(s.extract_table(body.index))

@router.post("/browser/{sid}/extract_form_schema")
async def extract_form_schema(sid: str,
                              rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    s = _owned_session(rec, sid)
    return {"forms": await _act(s.extract_form_schema())}

@router.post("/browser/{sid}/screenshot")
async def screenshot(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    """Return the current viewport as a base64 JPEG data URL (mark overlay
    painted on) — the tool-surface counterpart to the live-view frame.jpg GET,
    so a WorkerEngine driving browser.* can fetch a screenshot too."""
    s = _owned_session(rec, sid)
    png = await _act(s.screenshot(marks=True))
    return {"data_url": s.screenshot_data_url(png)}


# ── live view ─────────────────────────────────────────────────────────────────
@router.get("/browser/{sid}/frame.jpg")
async def frame(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_session_or_token)]):
    try:
        png = _mgr().frame(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if png is None:
        return Response(status_code=204)
    return Response(content=png, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})

@router.get("/browser/{sid}/actions")
async def actions(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_session_or_token)],
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
                  rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    try:
        ok = _mgr().resolve_confirm(rec.tenant_id, sid, body.id, body.approved,
                                    owner_fingerprint=rec.sid_fingerprint)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"resolved": ok}

@router.post("/browser/{sid}/pause")
async def pause(sid: str, body: PauseReq,
                rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    try:
        _mgr().set_paused(rec.tenant_id, sid, body.paused,
                          owner_fingerprint=rec.sid_fingerprint)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"paused": body.paused}


# ── agent loop (natural-language "give it a note", ADR-0182 Part A) ────────────
@router.post("/browser/{sid}/agent")
async def run_agent(sid: str, body: AgentReq,
                    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
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
async def stop_agent(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
    try:
        _mgr().stop_agent(rec.tenant_id, sid, owner_fingerprint=rec.sid_fingerprint)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"stopped": True}


@router.post("/browser/{sid}/agent/continue")
async def continue_agent(sid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf_or_token)]):
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
