"""Web-chat routes — REST session management + WebSocket streaming.

See ``chat_runtime.py`` for the runtime contract and v1 scope notes
(ADR-0037 § Iteration 3a — minimal).

Endpoints
---------
  GET    /v1/console/chat/sessions                 → list sessions
  POST   /v1/console/chat/sessions                 → create session
  DELETE /v1/console/chat/sessions/{sid}           → delete session
  WS     /v1/console/chat/sessions/{sid}/stream    → bidirectional turn stream

WebSocket protocol
------------------
Client → server: ``{"type": "user", "text": "..."}``
Server → client: streamed events from ``chat_runtime.stream_turn``
                 (``delta`` / ``tool_use`` / ``result`` / ``error`` / ``done``)

A session may be cancelled mid-turn by sending ``{"type": "cancel"}``.

Robustness contract (the socket is long-lived, never dropped on a turn
failure):
  * A turn that raises is reported as an in-band ``{"type": "error"}`` +
    ``{"type": "done"}`` — the WebSocket stays open for the next turn.
  * ``{"type": "ping"}`` is answered with ``{"type": "pong"}`` *also while a
    turn is in flight*, giving server→client keepalive during long tool
    calls that emit no deltas (prevents idle-proxy disconnects).
  * The socket only closes on client disconnect or auth/not-found (4401/4404).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import re
import sys
import time
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Cookie, Depends, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect, status as http_status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from .. import chat_runtime
from .. import task_manager as tm_module
from ..deps import require_csrf, require_session
from ..utils import read_json_or_empty as _read_json_safe

router = APIRouter()

logger = logging.getLogger(__name__)

# Auto-browser detection — used to route bare URLs and explicit navigate-intent
# messages to the browser agent without requiring the /browser prefix.
_URL_START_RE = re.compile(r'^https?://\S', re.I)
_BROWSE_INTENT_RE = re.compile(
    r'^(?:öffne|besuche|geh\s+auf|schau\s+auf|navigate\s+to|browse\s+to|open)\s+https?://\S',
    re.I | re.UNICODE,
)


def _detect_browser_task(prompt: str) -> str | None:
    """Return the task string when the message should auto-trigger the browser.

    Conservative — only fires on:
    (a) A message that starts with an http(s) URL (user typed a bare link), or
    (b) A message that starts with a German/English navigate-verb followed by a URL.
    Returns None for everything else to avoid false-positives in normal chat.
    """
    stripped = prompt.strip()
    if _URL_START_RE.match(stripped) or _BROWSE_INTENT_RE.match(stripped):
        return stripped
    return None


# Broad browsing-signal pre-gate — only messages matching this are worth the LLM
# intent classification, so ordinary chat/coding turns never pay the extra call.
_BROWSE_SIGNAL_RE = re.compile(
    r"\b(öffne|besuche|geh\s+auf|schau\s+(dir|auf)|navigat|browse|open|go\s+to|visit|"
    r"website|webseite|seite|klick|click|ausf[üu]llen|fill\s+(in|out|the)|log\s?in|"
    r"einlogg|such[e]?\s+(auf|bei|on)|search\s+on|screenshot|warenkorb|checkout|"
    r"\.com|\.de|\.org|\.io|\.net|\.ai)\b",
    re.I | re.UNICODE,
)


async def _classify_browser_intent(prompt: str) -> str | None:
    """LET THE AGENT DECIDE (ADR-0182): for a browsing-ish message with no bare URL,
    ask the model whether it's a live-web-browsing request and, if so, distil the
    task. Gated by ``_BROWSE_SIGNAL_RE`` so normal chat never triggers a spawn.
    Returns the task string, or None (→ normal chat turn)."""
    if not _BROWSE_SIGNAL_RE.search(prompt):
        return None
    import os
    import shutil
    import subprocess

    def _spawn() -> str:
        sysp = (
            "Classify the user's message. If it is a request to browse or operate a "
            "LIVE WEBSITE (open a site, navigate, search on a site, fill a form, click, "
            "read a live page, check a cart/checkout), reply EXACTLY 'BROWSE: <one "
            "concise task>'. Otherwise (normal conversation, coding, general questions) "
            "reply EXACTLY 'NO'. Output only that one line.")
        binp = (os.environ.get("CORVIN_CLAUDE_BIN", "").strip()
                or shutil.which("claude") or "claude")
        try:
            r = subprocess.run(
                [binp, "-p", "--max-turns", "1", "--tools", "", "--system-prompt", sysp, prompt],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
            return (r.stdout or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    out = await asyncio.to_thread(_spawn)
    m = re.match(r"\s*BROWSE:\s*(.+)", out, re.I | re.S)
    if m:
        task = m.group(1).strip()
        return task or prompt.strip()
    return None


def _project(sess: chat_runtime.WebChatSession) -> dict[str, Any]:
    return {
        "sid":             sess.sid,
        "chat_key":        sess.chat_key,
        "title":           sess.title or "New Chat",
        "created_at":      sess.created_at,
        "last_active_at":  sess.last_active_at,
        "turn_count":      sess.turn_count,
        "has_workdir":     sess.workdir is not None,
    }

@router.get("/chat/sessions")
def list_chat_sessions(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    items = [_project(s) for s in chat_runtime.list_sessions(rec.tenant_id)]
    return {"tenant_id": rec.tenant_id, "count": len(items), "sessions": items}

class CreateSessionRequest(BaseModel):
    title:         str = Field("", max_length=120)
    re_auth_token: str | None = None  # Optional — sessions are cheap, no PIN required.
    model_config = {"extra": "forbid"}

@router.post("/chat/sessions")
def create_chat_session(
    body: CreateSessionRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    sess = chat_runtime.create_session(rec.tenant_id, title=body.title or "")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="chat.session.create",
        target_kind="chat_session",
        target_id=sess.sid,
    )
    return {"ok": True, "session": _project(sess)}

@router.delete("/chat/sessions/{sid}")
def delete_chat_session(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not chat_runtime.delete_session(rec.tenant_id, sid):
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="chat.session.delete",
        target_kind="chat_session",
        target_id=sid,
    )
    return {"ok": True, "sid": sid}

class RenameSessionRequest(BaseModel):
    title: str = Field("", max_length=120)
    model_config = {"extra": "forbid"}

@router.patch("/chat/sessions/{sid}")
def rename_chat_session(
    sid: str,
    body: RenameSessionRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    sess = chat_runtime.rename_session(rec.tenant_id, sid, body.title)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="chat.session.rename",
        target_kind="chat_session",
        target_id=sid,
    )
    return {"ok": True, "session": _project(sess)}

@router.get("/chat/sessions/{sid}/turns")
def get_chat_turns(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict[str, Any]:
    """Return the persisted message history for a chat session.

    Used by the SPA on session-open to re-hydrate the chat view, so a
    tab refresh / page reload does not lose the conversation.
    """
    # Make sure the caller owns the session (cheap, also gives 404).
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")
    turns = chat_runtime.read_turns(rec.tenant_id, sid, limit=limit)
    return {"sid": sid, "count": len(turns), "turns": turns}

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Also allow forward-slash-separated sub-paths (for ACS output files).
_SAFE_SUBPATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,511}$")
# Matches both acs_runtime format (acs-{ts}-{4hex}) and chat_runtime format (acs-web-{ts}-{3hex})
# plus fixture/demo IDs like acs-demo-full-audit-001
_ACS_RUN_ID_RE = re.compile(r"^acs-[a-z0-9]+-[a-z0-9]{3,12}(-[a-z0-9]+)*$")
_WORKER_ID_RE  = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

@router.get("/chat/sessions/{sid}/workdir/{filepath:path}")
def get_workdir_file(
    sid: str,
    filepath: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> FileResponse:
    """Serve a file from the session workdir (images, PDFs, CSVs generated by Claude).

    filepath may contain forward-slash-separated subdirectories so that ACS
    output files (e.g. acs/runs/<id>/output/chart.png) resolve correctly.
    The resolved path is always verified to stay within the workdir.
    """
    if not _SAFE_SUBPATH.match(filepath):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid path")
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")
    fpath = sess.workdir / filepath
    # Prevent path traversal — resolved path must stay within workdir.
    try:
        fpath.resolve().relative_to(sess.workdir.resolve())
    except ValueError:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "access denied")
    if not fpath.is_file():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "file not found")
    mime, _ = mimetypes.guess_type(str(fpath))
    filename = Path(filepath).name
    # Serve INLINE so the browser renders the artifact in-place (images, PDFs,
    # HTML, audio, video) inside the chat. Passing `filename=` would emit
    # `Content-Disposition: attachment`, which forces a download and breaks
    # inline <iframe>/<video>/<audio> rendering. The client's download buttons
    # use the HTML `download` attribute, which still triggers a Save for
    # same-origin URLs regardless of the inline disposition, so downloads keep
    # working. We still advertise the filename so a manual save uses a sane name.
    return FileResponse(
        path=str(fpath),
        media_type=mime or "application/octet-stream",
        filename=filename,
        content_disposition_type="inline",
    )

@router.get("/chat/sessions/{sid}/workdir-path")
def get_session_workdir_path(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    reveal: bool = Query(False),
) -> dict[str, Any]:
    """Return the session workdir path and optionally open it in the OS file manager.

    ``?reveal=true`` triggers xdg-open / open / explorer.exe on the server.
    For a local self-hosted install the server IS the user's machine, so this
    opens the folder in their native file manager immediately.
    """
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")
    workdir = sess.workdir
    workdir.mkdir(parents=True, exist_ok=True)
    opened = False
    if reveal:
        try:
            import subprocess  # noqa: PLC0415
            if sys.platform == "win32":
                subprocess.Popen(["explorer.exe", str(workdir)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 8))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(workdir)])
            else:
                subprocess.Popen(["xdg-open", str(workdir)])
            opened = True
        except Exception:
            pass
    return {"ok": True, "path": str(workdir), "opened": opened}

_ATTACH_MAX_BYTES = 20 * 1024 * 1024   # 20 MB per file
_ATTACH_MAX_FILES = 10
_ATTACH_ALLOWED_MIMES: frozenset[str] = frozenset({
    "text/plain", "text/csv", "text/html", "text/markdown",
    "application/json", "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
})
_ATTACH_ALLOWED_EXTS: frozenset[str] = frozenset({
    ".txt", ".csv", ".md", ".json", ".yaml", ".yml", ".toml",
    ".pdf", ".xlsx", ".xls",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".py", ".js", ".ts", ".html", ".css", ".sql",
})

def _safe_attach_name(raw: str) -> str:
    """Return a sanitised attachment filename (ASCII, no path sep)."""
    name = Path(raw).name
    # Replace unsafe chars, keep extension
    clean = re.sub(r"[^\w.\- ]", "_", name).strip()
    return clean or "file"


@router.post("/chat/sessions/{sid}/attachments")
async def upload_attachments(
    sid: str,
    files: Annotated[list[UploadFile], File(description="One or more files to attach")],
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> JSONResponse:
    """Upload one or more files into the session workdir/attachments/ directory.

    Returns a list of ``{name, size, mime, path}`` descriptors so the frontend
    can embed the paths in the next user message for Claude to read.
    """
    if len(files) > _ATTACH_MAX_FILES:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            f"Too many files — max {_ATTACH_MAX_FILES} per upload")
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    attach_dir = sess.workdir / "attachments"
    attach_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for upload in files:
        ext = Path(upload.filename or "").suffix.lower()
        if ext and ext not in _ATTACH_ALLOWED_EXTS:
            raise HTTPException(
                http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"File type {ext!r} not allowed",
            )
        data = await upload.read(_ATTACH_MAX_BYTES + 1)
        if len(data) > _ATTACH_MAX_BYTES:
            raise HTTPException(
                http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"File exceeds 20 MB limit: {upload.filename!r}",
            )
        safe_name = _safe_attach_name(upload.filename or "file")
        dest = attach_dir / safe_name
        # Avoid overwrite by appending a counter suffix
        if dest.exists():
            base, dot_ext = (safe_name.rsplit(".", 1) if "." in safe_name
                             else (safe_name, ""))
            for i in range(1, 100):
                candidate = attach_dir / (f"{base}_{i}.{dot_ext}" if dot_ext else f"{base}_{i}")
                if not candidate.exists():
                    dest = candidate
                    safe_name = dest.name
                    break
        dest.write_bytes(data)
        mime = upload.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        results.append({
            "name": safe_name,
            "size": len(data),
            "mime": mime,
            "path": f"attachments/{safe_name}",
        })

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid[:12],
        action="upload",
        target_kind="chat_attachment",
        target_id=sid,
    )
    return JSONResponse({"attachments": results})


# Decoupled confirm channel (ADR-0183 S1): "/browser confirm <sid> <yes|no>"
# resolves the OLDEST pending human-in-the-loop confirmation for that browser
# session from the main chat, without needing the live-view browser tab (which
# shares the tenant CSRF session with the tool driver). Additive — the
# live-view Approve/Decline buttons keep working exactly as before.
_BROWSER_CONFIRM_CMD_RE = re.compile(
    r"^confirm\s+(\S+)\s+(yes|no|y|n|approve|deny|decline)\s*$", re.IGNORECASE)


async def _handle_browser_confirm_command(websocket, rec, match: "re.Match") -> None:
    """Resolve the oldest pending confirm for a browser session from chat.

    Fail-closed: an unknown/foreign session id or a session with nothing
    pending is reported as a clear, distinct error — never silently ignored,
    never guessed at.
    """
    from . import browser as _br  # reuse the singleton manager
    sid = match.group(1)
    approved = match.group(2).lower() in ("yes", "y", "approve")
    mgr = _br._mgr()
    try:
        resolved = mgr.resolve_oldest_pending(rec.tenant_id, sid, approved)
    except KeyError:
        await websocket.send_json({"type": "error",
            "message": f"no browser session '{sid}' for this tenant."})
        await websocket.send_json({"type": "done"})
        return
    if not resolved:
        await websocket.send_json({"type": "error",
            "message": f"no pending confirmation for browser session '{sid}'."})
        await websocket.send_json({"type": "done"})
        return
    with contextlib.suppress(Exception):
        console_audit.action_performed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="chat.browser_confirm", target_kind="browser_session", target_id=sid)
    verb = "approved" if approved else "declined"
    await websocket.send_json({"type": "delta",
        "text": f"{'✅' if approved else '❌'} {verb} the pending action for browser session `{sid}`.\n"})
    await websocket.send_json({"type": "done"})


async def _handle_browser_command(websocket, rec, task: str) -> None:
    """ADR-0182 Part B — `/browser <task>` in the command-center chat: open a
    visible browser, run the browser-agent loop, and stream its progress back as
    chat deltas. Sensitive actions surface in the Browser page's confirm panel.
    The agent keeps running in the background if the streaming window elapses.

    ADR-0183 S1 additive sub-command — `/browser confirm <sid> <yes|no>` —
    resolves a pending confirm from chat instead of starting a new agent task;
    see ``_handle_browser_confirm_command``."""
    from . import browser as _br  # reuse the singleton manager + helpers
    if not task:
        await websocket.send_json({"type": "error", "message": "usage: /browser <task>"})
        await websocket.send_json({"type": "done"})
        return
    _confirm_match = _BROWSER_CONFIRM_CMD_RE.match(task)
    if _confirm_match:
        await _handle_browser_confirm_command(websocket, rec, _confirm_match)
        return
    # Same gates as a normal chat turn: (1) L44 acceptable-use on the task text
    # (it drives an LLM + a browser), fail-closed; (2) charge the chat-turn quota
    # so /browser can't be a metered-spawn bypass.
    try:
        from .. import _spawn_gates  # noqa: PLC0415 — _spawn_gates.py lives in
        # corvin_console/, one level above routes/ — was never imported in this
        # module at all: every call below raised NameError, silently caught by
        # the broad except and reported as "safety check failed" — the L44 gate
        # never actually ran for /browser (fails closed, but the feature was
        # entirely non-functional; adversarial review finding).
        _refusal = _spawn_gates.check_console_spawn_or_refusal(
            task, tenant_id=rec.tenant_id, persona="assistant",
            channel="chat", chat_key=f"browser:{rec.sid_fingerprint}",
            engine_id="claude_code", classification="PUBLIC")
    except Exception:  # noqa: BLE001
        # L44 fail-closed: a classifier error must refuse the command, never
        # silently auto-approve it (this drives an LLM + a live browser).
        await websocket.send_json({"type": "error",
            "message": "Browser command temporarily unavailable (safety check failed)."})
        await websocket.send_json({"type": "done"})
        return
    if _refusal:
        await websocket.send_json({"type": "delta", "text": _refusal})
        await websocket.send_json({"type": "done"})
        return
    try:
        from ._compute_license_gate import enforce_chat_turns  # noqa: PLC0415
        enforce_chat_turns(rec.tenant_id, rec.sid_fingerprint,
                           audit_action="chat.browser_command", channel="chat")
    except HTTPException:
        await websocket.send_json({"type": "error", "code": 402,
            "message": "daily chat-turn limit reached (chat_turns_per_day)"})
        await websocket.send_json({"type": "done"})
        return
    mgr = _br._mgr()
    try:
        sid = await mgr.create(rec.tenant_id, headless=_br._default_headless(),
                               owner_fingerprint=rec.sid_fingerprint)
    except Exception as e:  # noqa: BLE001 — cap reached / launch failure
        await websocket.send_json({"type": "error", "message": f"could not start browser: {e}"})
        await websocket.send_json({"type": "done"})
        return
    with contextlib.suppress(Exception):
        console_audit.action_performed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="chat.browser_command", target_kind="browser_session", target_id=sid)

    await websocket.send_json({"type": "delta", "text":
        f"🌐 Browser started — open **Browser** in the sidebar to watch live.\n"
        f"**Task:** {task}\n\n"})
    # auto_close: a chat-initiated session closes itself when the agent finishes,
    # so it can never leak / wedge the per-tenant session cap.
    if not mgr.start_agent(rec.tenant_id, sid, task, auto_close=True):
        await websocket.send_json({"type": "delta", "text": "⚠️ an agent is already running.\n"})
        await websocket.send_json({"type": "done"})
        return

    since = 0
    max_wall = 180.0
    waited = 0.0
    try:
        while True:
            await asyncio.sleep(1.0)
            waited += 1.0
            try:
                evs = mgr.actions(rec.tenant_id, sid, since=since)
                since = mgr.next_seq(rec.tenant_id, sid)
                running = mgr.agent_running(rec.tenant_id, sid)
            except KeyError:
                break     # session closed (e.g. auto_close after the agent finished)
            for e in evs:
                a = e.get("action", "")
                if a == "agent_step":
                    await websocket.send_json({"type": "delta",
                        "text": f"• **{e.get('plan','')}** — {e.get('reason','')}\n"})
                elif a == "confirm_request":
                    await websocket.send_json({"type": "delta",
                        "text": f"⚠️ needs your approval: “{e.get('name','')}” — approve it in the Browser page.\n"})
                elif a in ("agent_finished",):
                    await websocket.send_json({"type": "delta",
                        "text": f"\n✅ **Done** — {e.get('summary') or e.get('reason','')}\n"})
                elif a == "agent_error":
                    await websocket.send_json({"type": "delta",
                        "text": f"\n⚠️ {e.get('error','')}\n"})
            if not running:
                break
            if waited >= max_wall:
                await websocket.send_json({"type": "delta", "text":
                    "\n⏳ Still working — keep watching in the **Browser** page; "
                    "it continues in the background.\n"})
                break
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            mgr.stop_agent(rec.tenant_id, sid)
        raise
    await websocket.send_json({"type": "done"})


@router.websocket("/chat/sessions/{sid}/stream")
async def chat_stream(
    websocket: WebSocket,
    sid: str,
    corvin_console_sid: Annotated[str | None, Cookie()] = None,
) -> None:
    """Bidirectional WebSocket — client sends user turns, server streams events.

    Authentication: the same session-cookie the REST API uses. The
    WebSocket upgrade carries the cookie automatically when same-origin.
    """
    # Manual auth check — FastAPI dependencies don't run for WebSocket
    # without a custom dependency-wrapper; doing it inline keeps the
    # control flow obvious.
    if not corvin_console_sid:
        await websocket.close(code=4401)
        return
    rec = session_auth.load_session(corvin_console_sid)
    if rec is None:
        await websocket.close(code=4401)
        return

    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    await websocket.send_json({"type": "ready", "session": _project(sess)})
    with contextlib.suppress(Exception):
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="chat.ws.connected",
            target_kind="chat",
            target_id=sid,
        )

    # _stream_task holds the asyncio.Task running stream_turn while a turn is
    # in flight.  A cancel message (or a WebSocket disconnect) cancels the task,
    # which propagates CancelledError into the async generator so aclosing()
    # calls gen.aclose() and the subprocess is terminated cleanly.
    _stream_task: asyncio.Task[None] | None = None

    async def _run_turn(prompt: str) -> None:
        # Robustness contract: a turn failure must NEVER drop the WebSocket.
        # stream_turn yields its own {"error"}/{"done"} on handled failures,
        # but it can still raise before the first yield (task-dir mkdir,
        # create_task, arg build, subprocess spawn) or mid-stream (engine /
        # MCP / I/O hiccup). Any such exception is converted to an in-band
        # error+done event so the chat shows the failure and the socket stays
        # open for the next turn. Only CancelledError (genuine cancel /
        # client disconnect) propagates to the outer handler for cleanup.
        try:
            async with contextlib.aclosing(
                chat_runtime.stream_turn(sess, prompt)
            ) as gen:
                async for event in gen:
                    await websocket.send_json(event)
        except asyncio.CancelledError:
            raise
        except Exception as _exc:  # noqa: BLE001 — never let a turn kill the socket
            logger.exception("chat turn failed (sid=%s)", sid)
            _exc_type = type(_exc).__name__
            _exc_msg = str(_exc)
            with contextlib.suppress(Exception):
                console_audit.action_failed(
                    tenant_id=rec.tenant_id,
                    sid_fingerprint=rec.sid_fingerprint,
                    action="chat.turn.failed",
                    target_kind="chat",
                    target_id=sid,
                    reason=f"{_exc_type}: {_exc_msg[:200]}",
                )
            # LimitOverrunError (or its ValueError re-raise) means a single JSON line from
            # the subprocess exceeded the StreamReader buffer — typically a very large tool result.
            if isinstance(_exc, (ValueError, asyncio.LimitOverrunError)) and (
                "Separator is found" in _exc_msg or "chunk is longer than limit" in _exc_msg
            ):
                _user_msg = (
                    "Claude's response contained a line too large for the stream buffer "
                    "(tool result or file content exceeded 8 MB). Try a shorter request or "
                    "avoid reading very large files in one shot."
                )
            else:
                _user_msg = f"The turn failed unexpectedly ({_exc_type}). Check server logs for details."
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "message": _user_msg})
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "done"})

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                if _stream_task and not _stream_task.done():
                    _stream_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await _stream_task
                return
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "invalid json"})
                continue
            if not isinstance(msg, dict):
                # A syntactically-valid but non-object JSON message (e.g. the
                # bare text "42" or "[1,2]") parses fine but has no .get() —
                # would otherwise crash the whole connection with an
                # uncaught AttributeError (adversarial review finding).
                await websocket.send_json({"type": "error", "message": "expected a JSON object"})
                continue
            mtype = msg.get("type")
            if mtype == "user":
                prompt = str(msg.get("text") or "").strip()
                if not prompt:
                    await websocket.send_json({"type": "error", "message": "empty user text"})
                    continue
                # Browser command (ADR-0182 Part B): drive the visible browser from
                # the command-center chat. `/browser <task>` starts a browser-agent
                # loop and streams its progress back as chat deltas. Handled here
                # (async) rather than in the sync slash-dispatcher below.
                if prompt.lower().startswith(("/browser ", "/browse ")):
                    task = prompt.split(" ", 1)[1].strip()
                    await _handle_browser_command(websocket, rec, task)
                    continue
                # Auto-browser: bare URL or explicit navigate-intent → browser agent
                # without requiring the /browser prefix.  Conservative heuristic:
                # (a) message starts with https?:// — the user typed a raw URL, or
                # (b) message starts with a navigate verb followed by a URL.
                _auto_task = _detect_browser_task(prompt)
                if _auto_task is None:
                    # No bare URL — let the agent DECIDE from natural language
                    # (gated by browsing-signal words so normal chat isn't slowed).
                    try:
                        _auto_task = await _classify_browser_intent(prompt)
                    except Exception:  # noqa: BLE001
                        _auto_task = None
                if _auto_task:
                    await _handle_browser_command(websocket, rec, _auto_task)
                    continue
                # Slash-command dispatcher (command center): handle every console
                # slash-command deterministically so it NEVER leaks to the LLM as a
                # confusing prompt. Returns None for CCC commands (/create*/erase/
                # audit → handled downstream by entity_extract) and for plain text;
                # a string is the assistant reply for this turn (no engine spawn, no
                # chat-turn quota charge). A dispatcher error must never break chat.
                try:
                    from .. import slash_commands as _slash  # noqa: PLC0415
                    _sc_reply = _slash.handle(
                        prompt,
                        tier=getattr(rec, "tier", None),
                        tenant_id=rec.tenant_id,
                        fingerprint=rec.sid_fingerprint,
                        configured_engine=chat_runtime._configured_os_engine(rec.tenant_id),
                    )
                except Exception:  # noqa: BLE001
                    _sc_reply = None
                if _sc_reply is not None:
                    try:
                        console_audit.action_performed(
                            tenant_id=rec.tenant_id,
                            sid_fingerprint=rec.sid_fingerprint,
                            action="chat.slash_command",
                            target_kind="chat",
                            target_id=sid,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    await websocket.send_json({"type": "delta", "text": _sc_reply})
                    await websocket.send_json({"type": "done"})
                    continue
                # Guard: engine must be configured before we charge the quota.
                _engine_msg = chat_runtime.get_engine_unavailable_message(rec.tenant_id)
                if _engine_msg:
                    await websocket.send_json({
                        "type": "error", "code": 503,
                        "message": _engine_msg,
                    })
                    await websocket.send_json({"type": "done"})
                    continue
                # ADR-0150 LIC-WEBCHAT-SPAWN-01: each user turn spawns a paid
                # `claude -p`. Charge the SEPARATE chat_turns_per_day axis (NOT the
                # compute-workload counter — that would make free chat 1/day) before
                # spawning. On 402 send an in-band error and keep the socket open.
                try:
                    from ._compute_license_gate import enforce_chat_turns  # noqa: PLC0415
                    enforce_chat_turns(
                        rec.tenant_id, rec.sid_fingerprint,
                        audit_action="chat.turn_started", channel="chat",
                    )
                except HTTPException:
                    await websocket.send_json({
                        "type": "error", "code": 402,
                        "message": "daily chat-turn limit reached (chat_turns_per_day)",
                    })
                    await websocket.send_json({"type": "done"})
                    continue
                # Run stream_turn as a Task so that a concurrent cancel message
                # (or WebSocket disconnect) can interrupt it.  We race the task
                # against the next receive_text() so we can react to "cancel"
                # while the generator is running.
                _stream_task = asyncio.create_task(_run_turn(prompt))
                try:
                    while not _stream_task.done():
                        recv_task = asyncio.create_task(websocket.receive_text())
                        done, _ = await asyncio.wait(
                            {_stream_task, recv_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if recv_task in done:
                            try:
                                side_raw = recv_task.result()
                            except WebSocketDisconnect:
                                _stream_task.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await _stream_task
                                return
                            try:
                                side_msg = json.loads(side_raw)
                                if not isinstance(side_msg, dict):
                                    side_msg = {}
                            except json.JSONDecodeError:
                                side_msg = {}
                            if side_msg.get("type") == "cancel":
                                _stream_task.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await _stream_task
                                await websocket.send_json({"type": "done"})
                                with contextlib.suppress(Exception):
                                    console_audit.action_performed(
                                        tenant_id=rec.tenant_id,
                                        sid_fingerprint=rec.sid_fingerprint,
                                        action="chat.turn.cancelled",
                                        target_kind="chat",
                                        target_id=sid,
                                    )
                                break
                            if side_msg.get("type") == "ping":
                                # Answer client heartbeats DURING a turn too.
                                # This is the only server→client traffic while a
                                # long tool call runs with no deltas, so it keeps
                                # the socket alive through idle-killing proxies
                                # (and lets the client confirm liveness).
                                with contextlib.suppress(Exception):
                                    await websocket.send_json({"type": "pong"})
                            # Other mid-turn messages are ignored — only cancel
                            # and ping are actioned while a turn is in flight.
                        else:
                            # _stream_task finished; cancel the pending recv.
                            recv_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await recv_task
                    if not _stream_task.cancelled():
                        # Propagate any exception from the turn task.
                        _stream_task.result()
                except asyncio.CancelledError:
                    raise
                finally:
                    _stream_task = None
            elif mtype == "ping":
                await websocket.send_json({"type": "pong"})
            elif mtype == "cancel":
                # cancel received outside of a running turn — acknowledge and ignore.
                await websocket.send_json({"type": "info", "message": "no active turn"})
            else:
                await websocket.send_json({"type": "error", "message": f"unknown type: {mtype!r}"})
    except WebSocketDisconnect:
        if _stream_task and not _stream_task.done():
            _stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _stream_task
        with contextlib.suppress(Exception):
            console_audit.action_performed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="chat.ws.disconnected",
                target_kind="chat",
                target_id=sid,
            )
        return

# ── Task API (ADR-0080 M1) ──────────────────────────────────────────────

@router.get("/chat/sessions/{sid}/tasks")
def list_session_tasks(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    status: str | None = Query(None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List all tasks for a chat session with optional filtering and pagination."""
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    tasks_dir = sess.workdir / "tasks"
    tm = tm_module.TaskManager(tasks_dir)
    all_tasks = tm.list_tasks(sess.chat_key)

    # Filter by status if provided
    if status:
        filtered_tasks = [t for t in all_tasks if t.status.value == status]
    else:
        filtered_tasks = all_tasks

    # Pagination
    total = len(filtered_tasks)
    tasks_page = filtered_tasks[offset:offset + limit]

    return {
        "sid": sid,
        "chat_key": sess.chat_key,
        "total": total,
        "limit": limit,
        "offset": offset,
        "count": len(tasks_page),
        "tasks": [t.to_dict() for t in tasks_page],
    }

@router.get("/chat/sessions/{sid}/tasks/{task_id}")
def get_session_task(
    sid: str,
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Get task metadata by ID."""
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    tasks_dir = sess.workdir / "tasks"
    tm = tm_module.TaskManager(tasks_dir)
    task = tm.get_task(task_id)

    if task is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "task not found")

    # Security: ensure task belongs to this session
    if task.chat_key != sess.chat_key:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "task not in this session")

    return {"ok": True, "task": task.to_dict()}

async def _event_stream_gen(
    tm: tm_module.TaskManager,
    task_id: str,
    start_seq: int | None,
) -> AsyncIterator[str]:
    """Generate SSE events for a task."""
    # events_since is a SYNC generator (plain def) — `async for` over it
    # raises TypeError at runtime. Sync iteration inside this async def is
    # legal; the file read is small and non-blocking in practice.
    for seq, event in tm.events_since(task_id, start_seq=start_seq):
        yield f"id: {seq}\n"
        yield f"data: {json.dumps(event)}\n"
        yield "\n"

@router.get("/chat/sessions/{sid}/tasks/{task_id}/events")
async def stream_task_events(
    sid: str,
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    last_event_id: str | None = Query(None),
) -> StreamingResponse:
    """Stream task events via SSE (Server-Sent Events).

    Query: ?last_event_id=5 to resume from event sequence 5.
    Browser automatically sends Last-Event-ID header on reconnect.
    """
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    tasks_dir = sess.workdir / "tasks"
    tm = tm_module.TaskManager(tasks_dir)
    task = tm.get_task(task_id)

    if task is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "task not found")

    if task.chat_key != sess.chat_key:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "task not in this session")

    # Parse start_seq from Last-Event-ID header or query param
    start_seq = None
    if last_event_id:
        try:
            start_seq = int(last_event_id)
        except ValueError:
            pass

    return StreamingResponse(
        _event_stream_gen(tm, task_id, start_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

@router.delete("/chat/sessions/{sid}/tasks/{task_id}")
def cancel_session_task(
    sid: str,
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Cancel a task."""
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    tasks_dir = sess.workdir / "tasks"
    tm = tm_module.TaskManager(tasks_dir)
    task = tm.get_task(task_id)

    if task is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "task not found")

    if task.chat_key != sess.chat_key:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "task not in this session")

    ok = tm.cancel_task(task_id)
    if not ok:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            "task is not in a cancellable state",
        )

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="chat.task.cancel",
        target_kind="chat_task",
        target_id=task_id,
    )
    return {"ok": True, "task_id": task_id}

# ── WDAT Audit Trail (ADR-0109) ───────────────────────────────────────────────

_THIS_DIR_CHAT = Path(__file__).resolve().parent
_SHARED_PATH = _THIS_DIR_CHAT.parents[3] / "operator" / "bridges" / "shared"
if str(_SHARED_PATH) not in sys.path:
    sys.path.insert(0, str(_SHARED_PATH))

def _list_session_acs_runs(workdir: Path) -> list[dict[str, Any]]:
    """Return ACS run summaries from a session's workdir."""
    runs_dir = workdir / "acs" / "runs"
    runs: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return runs
    for run_dir in sorted(runs_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        manifest = _read_json_safe(run_dir / "manifest.json")
        result_file = run_dir / "result.json"
        result = _read_json_safe(result_file)
        # A run is active when result.json doesn't exist yet (no final state written)
        is_active = not result_file.exists()
        runs.append({
            "run_id":        run_dir.name,
            "workflow_id":   manifest.get("workflow_id") or result.get("workflow_id", ""),
            "status":        result.get("status") or ("running" if is_active else "unknown"),
            "is_active":     is_active,
            "started_at":    manifest.get("started_at") or run_dir.stat().st_mtime,
            "total_workers": result.get("workers_spawned", 0),
            "iterations":    result.get("iterations", 0),
            "duration_s":    result.get("elapsed_s", 0.0),
        })
    return runs

def _build_wdat_graph(
    report: dict[str, Any],
    tool_calls_by_worker: "dict[str, list[dict[str, Any]]] | None" = None,
    engine_timing_by_worker: "dict[str, dict[str, Any]] | None" = None,
) -> dict[str, Any]:
    """Transform wdat_report output into React Flow nodes + edges.

    Four-level graph (ADR-0109 M6):
        Manager → Workers → Engine (wdat_engine) → Tool-calls (wdat_tool)

    engine_timing_by_worker comes from acs.engine_started/completed events
    in the audit chain and enriches engine nodes with actual timing data.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    tool_calls_by_worker   = tool_calls_by_worker   or {}
    engine_timing_by_worker = engine_timing_by_worker or {}

    STATUS_COLORS = {
        "success": "#00E676",
        "partial": "#FF9800",
        "failed":  "#FF1744",
    }

    # spawn_nonce → iteration number from manager decisions
    nonce_to_iter: dict[str, int] = {}
    manager_decisions = report.get("manager_decisions") or []
    for d in manager_decisions:
        if d.get("spawn_nonce"):
            nonce_to_iter[d["spawn_nonce"]] = int(d.get("iteration", 0))

    # ── Layout constants: vertical-first (iterations stack top → bottom) ──────
    # Mirror the layout used by ComputeGraphView.tsx so both graph surfaces
    # read the same way: iter column at x=0, workers fan right per row.
    ITER_X      =   0    # x-centre of all manager-decision (iteration) nodes
    W_START     = 210    # x-offset from ITER_X to the first worker column
    X_WORKER    = 170    # horizontal gap between workers in the same depth row
    DEPTH_ROW_H = 150    # extra y per sub-worker depth level within one iteration
    Y_ROW_MIN   = 210    # minimum row height (worker + engine + tools + gap)

    # Pre-group workers by iteration so row heights can be calculated up-front.
    workers = report.get("workers") or []
    workers_by_iter: dict[int, list[dict]] = {}
    for w in workers:
        it = nonce_to_iter.get(w.get("spawn_nonce") or "", 0)
        workers_by_iter.setdefault(it, []).append(w)

    # Sorted iteration numbers and per-iteration Y positions (accumulated).
    sorted_iters = sorted({int(d.get("iteration", 0)) for d in manager_decisions})
    iter_y: dict[int, float] = {}
    current_y = 0.0
    for it in sorted_iters:
        iter_y[it] = current_y
        wlist = workers_by_iter.get(it, [])
        max_depth = max((int(w.get("depth") or 0) for w in wlist), default=0)
        has_tools  = any(w.get("worker_id", "") in tool_calls_by_worker for w in wlist)
        row_h = Y_ROW_MIN + max_depth * DEPTH_ROW_H + (60 if has_tools else 0)
        current_y += row_h

    # Track which manager-decision node IDs exist so edge generation can guard
    # against dangling edges for workers with unmatched spawn_nonce (fallback it=0).
    mgr_node_ids: set[int] = set()

    # Manager decision nodes — one per iteration, stacked vertically
    for d in sorted(manager_decisions, key=lambda x: int(x.get("iteration", 0))):
        it = int(d.get("iteration", 0))
        iy = iter_y.get(it, 0.0)
        mgr_node_ids.add(it)
        nodes.append({
            "id":       f"mgr_{it}",
            "type":     "wdat_manager",
            "position": {"x": float(ITER_X), "y": iy},
            "data": {
                "label":         f"Iter {it}\n{d.get('decision_type', '?')}",
                "iteration":     it,
                "decision_type": d.get("decision_type"),
                "decision_hash": (d.get("decision_hash") or "")[:16],
                "n_subtasks":    d.get("n_subtasks", 0),
                "spawn_nonce":   (d.get("spawn_nonce") or "")[:8],
                "model_id":      d.get("model_id", ""),
            },
        })

    # Worker nodes — horizontal fan right of their iteration row, grouped by depth.
    # depth=0 workers sit at iter_y; depth-N workers sit depth*DEPTH_ROW_H lower.
    worker_positions: dict[str, tuple[float, float]] = {}

    for it, wlist in workers_by_iter.items():
        base_y = iter_y.get(it, 0.0)
        # Group workers by depth so each depth level gets its own horizontal row.
        depth_groups: dict[int, list[dict]] = {}
        for w in wlist:
            d_lv = int(w.get("depth") or 0)
            depth_groups.setdefault(d_lv, []).append(w)

        for depth, dlist in sorted(depth_groups.items()):
            for j, w in enumerate(dlist):
                x = float(ITER_X + W_START + j * X_WORKER)
                y = base_y + depth * DEPTH_ROW_H
                status = w.get("status")
                color  = STATUS_COLORS.get(status or "", "#6e7681")
                engine = (w.get("engine") or "?")[:16]
                wid    = w["worker_id"]
                tool_n = len(tool_calls_by_worker.get(wid, []))
                worker_positions[wid] = (x, y)
                nodes.append({
                    "id":       f"worker_{wid}",
                    "type":     "wdat_worker",
                    "position": {"x": x, "y": y},
                    "data": {
                        "label":            f"{engine}\n{status or '?'}",
                        "worker_id":        wid,
                        "depth":            depth,
                        "parent_worker_id": w.get("parent_worker_id"),
                        "status":           status,
                        "confidence":       w.get("confidence"),
                        "color":            color,
                        "instruction_hash": (w.get("instruction_hash") or "")[:16],
                        "output_hash":      (w.get("output_hash") or "")[:16],
                        "duration_ms":      w.get("duration_ms"),
                        "tokens_used":      w.get("tokens_used"),
                        "tool_count":       tool_n,
                        "engine_attestation": w.get("engine_attestation") or {},
                    },
                })

    # Edges: manager → top-level workers (guard: skip if mgr node doesn't exist)
    for it, wlist in workers_by_iter.items():
        if it not in mgr_node_ids:
            continue
        for w in wlist:
            if not w.get("parent_worker_id"):
                edges.append({
                    "id":       f"e_mgr{it}_{w.get('worker_id', 'unknown')}",
                    "source":   f"mgr_{it}",
                    "target":   f"worker_{w.get('worker_id', 'unknown')}",
                    "style":    {"stroke": "#555", "strokeWidth": 1},
                    "markerEnd": {"type": "arrowclosed", "color": "#555"},
                })

    # Edges: parent worker → sub-worker (delegation)
    for w in workers:
        pid = w.get("parent_worker_id")
        if pid:
            edges.append({
                "id":       f"e_sub_{pid}_{w['worker_id']}",
                "source":   f"worker_{pid}",
                "target":   f"worker_{w['worker_id']}",
                "animated": True,
                "style":    {"stroke": "#E040FB", "strokeWidth": 2, "strokeDasharray": "5,3"},
                "markerEnd": {"type": "arrowclosed", "color": "#E040FB"},
            })

    # ── Engine attestation nodes (wdat_engine) ─────────────────────────────────
    # One engine node per worker, between the worker pill and the tool-call row.
    # Data comes from engine_attestation in acs.worker_traced (post-completion)
    # or from model_id / engine field in acs.worker_spawned (while running).
    # Layout: worker_y + ENGINE_Y_OFFSET
    ENGINE_Y_OFFSET = 88  # px below worker top → engine node top

    ENGINE_COLORS: dict[str, str] = {
        "claude_code": "#60a5fa",   # blue
        "hermes":      "#a78bfa",   # purple (local Ollama)
        "codex_cli":   "#34d399",   # green
        "opencode":    "#fb923c",   # orange
        "copilot":     "#f472b6",   # pink
    }
    DEFAULT_ENGINE_COLOR = "#6e7681"

    # engine centre positions — tool-call nodes hang from these
    engine_positions: dict[str, tuple[float, float]] = {}

    for wid, (wx, wy) in worker_positions.items():
        w_data = next((w for w in workers if w["worker_id"] == wid), None)
        if not w_data:
            continue
        att       = w_data.get("engine_attestation") or {}
        engine_id = att.get("engine_id") or "claude_code"
        model_id  = att.get("model_id") or w_data.get("engine") or "?"
        locality  = att.get("locality") or ""
        color     = ENGINE_COLORS.get(engine_id, DEFAULT_ENGINE_COLOR)

        ey = wy + ENGINE_Y_OFFSET
        # engine node is 110 px wide — position its left edge so centre = wx
        ex_left = wx - 55.0
        engine_positions[wid] = (wx, ey)  # store centre for tool layout

        # Merge audit-chain timing (acs.engine_started/completed) if available
        timing = engine_timing_by_worker.get(wid) or {}

        nodes.append({
            "id":       f"engine_{wid}",
            "type":     "wdat_engine",
            "position": {"x": ex_left, "y": ey},
            "data": {
                "label":       model_id[:22],
                "engine_id":   timing.get("engine_id") or engine_id,
                "model_id":    timing.get("model_id")  or model_id,
                "locality":    timing.get("locality")   or locality,
                "color":       color,
                "worker_id":   wid,
                "duration_ms": timing.get("duration_ms"),
                "tokens_used": timing.get("tokens_used"),
                "exit_code":   timing.get("exit_code"),
            },
        })
        edges.append({
            "id":     f"e_eng_{wid}",
            "source": f"worker_{wid}",
            "target": f"engine_{wid}",
            "style":  {"stroke": color, "strokeWidth": 1.5},
            "markerEnd": {"type": "arrowclosed", "color": color},
        })

    # ── Tool-call nodes (wdat_tool) ────────────────────────────────────────────
    # Hang below the engine node, laid out in a horizontal row.
    TOOL_COLORS = {"allow": "#00E676", "deny": "#FF1744"}
    TOOL_W   = 80   # node width  (matches WdatToolNode CSS)
    TOOL_GAP = 8    # horizontal gap between nodes
    TOOL_Y_OFFSET = 50  # px below engine node top → tool row

    for wid, calls in tool_calls_by_worker.items():
        if not calls:
            continue
        # Prefer hanging off engine node; fall back to worker for missing workers
        if wid in engine_positions:
            cx, cy = engine_positions[wid]
            ty = cy + TOOL_Y_OFFSET
            parent = f"engine_{wid}"
        elif wid in worker_positions:
            cx, cy = worker_positions[wid]
            ty = cy + ENGINE_Y_OFFSET + TOOL_Y_OFFSET
            parent = f"worker_{wid}"
        else:
            continue

        n = len(calls)
        row_width = n * TOOL_W + (n - 1) * TOOL_GAP
        start_x = cx - row_width / 2.0

        # Single anchor edge parent → first tool (groups the fan visually)
        edges.append({
            "id":     f"e_tools_{wid}",
            "source": parent,
            "target": f"tool_{wid}_0",
            "style":  {"stroke": "#334155", "strokeWidth": 1, "strokeDasharray": "3,3"},
        })

        for i, tc in enumerate(calls):
            tool_id  = f"tool_{wid}_{i}"
            decision = tc.get("decision", "allow")
            color    = TOOL_COLORS.get(decision, "#6e7681")
            tx       = start_x + i * (TOOL_W + TOOL_GAP)

            nodes.append({
                "id":       tool_id,
                "type":     "wdat_tool",
                "position": {"x": tx, "y": ty},
                "data": {
                    "label":     tc.get("tool", "?"),
                    "decision":  decision,
                    "color":     color,
                    "seq":       tc.get("seq", i + 1),
                    "worker_id": wid,
                },
            })
            if i > 0:
                edges.append({
                    "id":     f"e_tool_{wid}_{i}",
                    "source": parent,
                    "target": tool_id,
                    "style":  {"stroke": "#334155", "strokeWidth": 1, "strokeDasharray": "3,3"},
                })

    return {
        "mode":  "wdat",
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "run_id":                  report.get("run_id", ""),
            "chain_integrity":         report.get("chain_integrity", "empty"),
            "total_workers":           report.get("total_workers", 0),
            "total_manager_decisions": report.get("total_manager_decisions", 0),
            "eu_ai_act":               report.get("eu_ai_act") or {},
        },
    }

@router.get("/chat/sessions/{sid}/wdat")
def list_session_wdat_runs(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List ACS runs with WDAT audit data for a chat session."""
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")
    # Scan BOTH the persisted workdir AND the canonically-resolved one. The
    # persisted workdir is an absolute path captured at session creation; if
    # CORVIN_HOME later changes (repo moved / service.env pin missing) it goes
    # stale while ACS writes under the live home — re-deriving via
    # chat_runtime._workdir keeps the Worker-Engine graph visible (the worker
    # graph must ALWAYS render). Dedupe by run_id.
    runs: list[dict[str, Any]] = []
    _seen_run_ids: set[str] = set()
    for _wd in (sess.workdir, chat_runtime._workdir(rec.tenant_id, sid)):
        for _r in _list_session_acs_runs(_wd):
            if _r["run_id"] not in _seen_run_ids:
                _seen_run_ids.add(_r["run_id"])
                runs.append(_r)
    return {
        "sid":   sid,
        "count": len(runs),
        "runs":  runs,
    }

@router.get("/chat/sessions/{sid}/wdat/{run_id}/graph")
def get_session_wdat_graph(
    sid: str,
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return a React Flow graph for a WDAT audit run."""
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    # Validate run_id format to prevent path traversal (same pattern as get_worker_trace)
    if not _ACS_RUN_ID_RE.match(run_id):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id format")

    # Verify run belongs to this session. Check the persisted workdir AND the
    # canonically-resolved one — a stale persisted absolute path (post CORVIN_HOME
    # change) must not 404 a run that exists under the live home (the worker graph
    # must always render; wdat_report below glob-scans the live home anyway).
    run_dir = sess.workdir / "acs" / "runs" / run_id
    if not run_dir.exists():
        _alt = chat_runtime._workdir(rec.tenant_id, sid) / "acs" / "runs" / run_id
        if not _alt.exists():
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "run not found in this session")

    try:
        import wdat_report as _wr  # type: ignore[import-untyped]
        from forge import paths as _fp_wr  # type: ignore[import-untyped]
        report = _wr.generate_report(run_id, tenant_id=rec.tenant_id, corvin_home=_fp_wr.corvin_home())
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).exception("WDAT report generation failed for run_id=%s", run_id)
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "WDAT report generation failed") from exc

    # Single audit-chain pass: collect forge.tool_executed + acs.engine_* events
    # for this run, so the graph can show engine nodes and tool-call nodes.
    from forge import paths as _fp  # type: ignore[import-untyped]

    _GRAPH_EVENT_TYPES = {"forge.tool_executed", "acs.engine_started", "acs.engine_completed"}

    tool_calls_by_worker: dict[str, list[dict[str, Any]]] = {}
    # engine_timing[worker_id] = {started_at, completed_at, duration_ms, tokens_used, engine_id, model_id}
    engine_timing_by_worker: dict[str, dict[str, Any]] = {}

    audit_path = _fp.corvin_home() / "tenants" / rec.tenant_id / "global" / "audit.jsonl"
    if audit_path.exists():
        try:
            raw_tool_calls: list[dict[str, Any]] = []
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                et = obj.get("event_type")
                if et not in _GRAPH_EVENT_TYPES:
                    continue
                details = obj.get("details") or {}
                if details.get("run_id") != run_id:
                    continue

                wid = details.get("worker_id", "")
                if et == "forge.tool_executed":
                    raw_tool_calls.append({
                        "ts":        obj.get("ts", 0.0),
                        "tool":      details.get("tool_name", ""),
                        "decision":  details.get("decision", "allow"),
                        "worker_id": wid,
                    })
                elif et == "acs.engine_started":
                    entry = engine_timing_by_worker.setdefault(wid, {})
                    entry["started_at"] = obj.get("ts", 0.0)
                    entry.setdefault("engine_id", details.get("engine_id", ""))
                    entry.setdefault("model_id",  details.get("model_id", ""))
                    entry.setdefault("locality",   details.get("locality", ""))
                elif et == "acs.engine_completed":
                    entry = engine_timing_by_worker.setdefault(wid, {})
                    entry["completed_at"] = obj.get("ts", 0.0)
                    entry["duration_ms"]  = details.get("duration_ms")
                    entry["tokens_used"]  = details.get("tokens_used")
                    entry["exit_code"]    = details.get("exit_code")
                    # prefer completed-event values (API-confirmed) over started-event
                    if details.get("engine_id"):
                        entry["engine_id"] = details["engine_id"]
                    if details.get("model_id"):
                        entry["model_id"] = details["model_id"]
                    if details.get("locality"):
                        entry["locality"] = details["locality"]

            # Group first, then number per-worker so seq reflects the
            # order of calls WITHIN each worker (not across all workers).
            raw_tool_calls.sort(key=lambda e: e["ts"])
            for tc in raw_tool_calls:
                tool_calls_by_worker.setdefault(tc["worker_id"], []).append(tc)
            for per_worker in tool_calls_by_worker.values():
                for i, tc in enumerate(per_worker, 1):
                    tc["seq"] = i
        except OSError:
            pass

    return _build_wdat_graph(
        report,
        tool_calls_by_worker=tool_calls_by_worker,
        engine_timing_by_worker=engine_timing_by_worker,
    )

@router.get("/chat/sessions/{sid}/wdat/{run_id}/workers/{worker_id}/trace")
def get_worker_trace(
    sid: str,
    run_id: str,
    worker_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """ADR-0109 M6 — return the ordered tool-call trace for a single ACS worker.

    Reads forge.tool_executed events from the WDAT audit chain filtered by
    run_id and worker_id. Metadata only — no tool input params or output.
    """
    if not _ACS_RUN_ID_RE.match(run_id):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id format")
    if not _WORKER_ID_RE.match(worker_id):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid worker_id format")

    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    run_dir = sess.workdir / "acs" / "runs" / run_id
    if not run_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "run not found in this session")

    # Read the WDAT audit chain for forge.tool_executed events.
    # Use forge.paths.corvin_home() — same resolver as the rest of the console —
    # to avoid the empty-string Path("").expanduser() == Path(".") pitfall.
    from forge import paths as _fp  # type: ignore[import-untyped]

    audit_path = _fp.corvin_home() / "tenants" / rec.tenant_id / "global" / "audit.jsonl"

    tool_calls: list[dict[str, Any]] = []
    if audit_path.exists():
        try:
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("event_type") != "forge.tool_executed":
                    continue
                details = obj.get("details") or {}
                if details.get("run_id") != run_id:
                    continue
                if details.get("worker_id") != worker_id:
                    continue
                tool_calls.append({
                    "ts":        obj.get("ts", 0.0),
                    "tool":      details.get("tool_name", ""),
                    "decision":  details.get("decision", "allow"),
                })
        except OSError:
            pass

    # Sort by timestamp (turn_seq not yet emitted; ts is sufficient)
    tool_calls.sort(key=lambda e: e["ts"])
    for i, tc in enumerate(tool_calls, 1):
        tc["seq"] = i

    denied  = sum(1 for t in tool_calls if t["decision"] == "deny")
    return {
        "worker_id":  worker_id,
        "run_id":     run_id,
        "tool_calls": tool_calls,
        "summary": {
            "total_calls":  len(tool_calls),
            "denied_calls": denied,
            "error_calls":  0,
        },
    }

# ── OS-Turn Audit (EU AI Act Art. 12/13 — every user interaction) ─────────────

_OS_TURN_EVENTS = {"os_turn.started", "os_turn.tool_called", "os_turn.completed"}

@router.get("/chat/sessions/{sid}/os-turns")
def list_session_os_turns(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(default=20, ge=1, le=500),
) -> dict[str, Any]:
    """List recent OS-turn audit entries for a console chat session.

    An OS-turn is one round of user-message → OS-engine-spawn → reply.
    Each turn emits os_turn.started, zero or more os_turn.tool_called, and
    os_turn.completed. This endpoint reconstructs turns from the L16 audit
    chain filtered by the session's chat_key.

    Metadata only — no prompt text, no tool inputs/outputs (GDPR Art. 5).
    """
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    chat_key = sess.chat_key  # e.g. "web:<sid>"
    from forge import paths as _fp  # type: ignore[import-untyped]

    # os_turn.* events are written by the bridge adapter, whose audit chain
    # is unified (scope-independent) at corvin_home()/global/forge — mirror
    # of bridges/shared/audit.py::_forge_workspace_root(). On a fully
    # migrated install corvin_home()/global is the ADR-0007 compat symlink
    # to tenants/_default/global, so this converges with the tenant path.
    # Exposure stays per-session via the chat_key filter below.
    audit_path = _fp.corvin_home() / "global" / "forge" / "audit.jsonl"
    if not audit_path.is_file():
        return {"sid": sid, "chat_key": chat_key, "count": 0, "turns": []}

    # Single pass: collect os_turn.* events for this chat_key, group by turn_id.
    turns_by_id: dict[str, dict[str, Any]] = {}
    turns_order: list[str] = []

    try:
        with audit_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                # Cheap substring pre-filter: the chain holds every event
                # family; skip the json.loads for the vast majority of
                # non-os_turn lines (file is read fully per request).
                if '"os_turn.' not in raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                et = ev.get("event_type", "")
                if et not in _OS_TURN_EVENTS:
                    continue
                d = ev.get("details") or {}
                if d.get("chat_key") != chat_key:
                    continue
                turn_id = d.get("turn_id", "")
                if not turn_id:
                    continue
                if turn_id not in turns_by_id:
                    turns_by_id[turn_id] = {
                        "turn_id":    turn_id,
                        "persona":    d.get("persona", ""),
                        # Chain records carry epoch-float "ts"; the SPA
                        # expects ISO-8601 (it slices HH:MM:SS out of it).
                        # Use gmtime + Z suffix to match the execution-log
                        # endpoint format (avoids display-time skew when
                        # the server is not in UTC).
                        "started_at": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(float(ev.get("ts", 0.0) or 0.0)),
                        ),
                        "tools":      [],
                        "completed":  False,
                        "duration_ms": 0,
                        "tools_called": 0,
                        "exit_code":   0,
                        "timed_out":   False,
                        # Requested model from os_turn.started; the
                        # completed event overwrites with the confirmed one.
                        "model":       d.get("model", ""),
                    }
                    turns_order.append(turn_id)
                t = turns_by_id[turn_id]
                if et == "os_turn.tool_called":
                    # M3: include seq for execution-log ordering; old events
                    # without seq get sequential position (len+1 as fallback).
                    seq = d.get("seq") or (len(t["tools"]) + 1)
                    t["tools"].append({"name": d.get("tool_name", ""), "seq": seq})
                elif et == "os_turn.completed":
                    t["completed"] = True
                    t["duration_ms"] = d.get("duration_ms", 0)
                    t["tools_called"] = d.get("tools_called", 0)
                    t["exit_code"] = d.get("exit_code", 0)
                    t["timed_out"] = d.get("timed_out", False)
                    if d.get("model"):  # confirmed model wins, but never
                        t["model"] = d["model"]  # erase the requested one
    except OSError:
        pass

    # Return most-recent first, capped at limit
    turns = [turns_by_id[tid] for tid in reversed(turns_order)][:limit]
    return {"sid": sid, "chat_key": chat_key, "count": len(turns), "turns": turns}

# ── Execution Log — flat chronological view of all OS + ACS events ────────────
# Returns a unified timeline of os_turn.* + acs.* events for a session so the
# console can render the "all calls" deep-dive tab. Metadata-only per GDPR Art. 5.

_EXEC_LOG_EVENTS = {
    # OS-turn lifecycle
    "os_turn.started", "os_turn.tool_called", "os_turn.completed",
    # ACS run + manager lifecycle
    "acs.run_start", "acs.run_error",
    "acs.manager_call", "acs.manager_decided", "acs.manager_error",
    "acs.manager_l34_blocked", "acs.manager_l35_blocked",
    "acs.manager_gates_unavailable",
    # ACS worker lifecycle
    "acs.worker_spawned", "acs.worker_traced", "acs.worker_error",
    "acs.worker_gates_unavailable", "acs.worker_l34_blocked",
    # Engine lifecycle
    "acs.engine_started", "acs.engine_completed", "acs.engine_error",
    # Sub-delegation
    "acs.delegation",
    # Gate chain + convergence diagnostics
    "acs.gate_chain_evaluated", "acs.gate_abort", "acs.max_rejections_reached",
    "acs.m4_adaptive_workers", "acs.loss_plateau", "acs.loss_regression",
    # Budget
    "acs.budget_exhausted",
    # Workflow terminal events
    "acs.workflow_complete", "acs.workflow_failed",
}
_EXEC_LOG_ALLOWED_DETAILS: set[str] = {
    # OS-turn fields
    "model", "model_id", "engine_id", "duration_ms", "tokens_used",
    "tool_name", "seq", "tools_called", "exit_code", "timed_out",
    # Worker / engine fields
    "worker_id", "run_id", "turn_id", "iteration", "decision_type",
    "status", "workers_spawned", "passed", "aggregate_score",
    "gate_count", "loss_total", "loss_delta", "confidence",
    "n_subtasks", "artifact_count", "locality", "engine",
    # Traceability / integrity hashes (non-PII)
    "spawn_nonce", "instruction_hash", "decision_hash", "output_hash",
    # Worker spawn context
    "depth", "parent_worker_id", "subtask_count", "can_delegate",
    # Adaptive / convergence fields
    "adaptive_n", "base_n", "loss_gap",
    # Budget / error / gate fields
    "count", "reason", "max_loops", "max_depth",
}

@router.get("/chat/sessions/{sid}/execution-log")
def get_session_execution_log(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict[str, Any]:
    """Return a flat chronological list of all OS-turn + ACS events for this session.

    Gives the console its 'all calls' deep-dive view — every engine invocation,
    tool call, worker spawn, and manager decision in temporal order.
    Metadata-only: no prompt text, no tool inputs/outputs (GDPR Art. 5).
    """
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    chat_key = sess.chat_key
    from forge import paths as _fp  # type: ignore[import-untyped]

    corvin_home = _fp.corvin_home()
    # Two separate audit chains:
    # • os_turn.* events → bridge audit = global/forge/audit.jsonl
    #   (written by audit.audit_event() which resolves to corvin_home/global/forge/)
    # • acs.* events → tenant audit = tenants/<tid>/global/audit.jsonl
    audit_paths = [
        corvin_home / "global" / "forge" / "audit.jsonl",
        corvin_home / "tenants" / rec.tenant_id / "global" / "audit.jsonl",
    ]

    def _parse_chain(path: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not path.is_file():
            return result
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    if '"os_turn.' not in raw and '"acs.' not in raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    et = ev.get("event_type", "")
                    if et not in _EXEC_LOG_EVENTS:
                        continue
                    d = ev.get("details") or {}
                    # os_turn: chat_key lives inside details (bridge audit format)
                    # acs.*:   top-level chat_key is populated by acs_runtime
                    ev_chat_key = d.get("chat_key") or ev.get("chat_key") or ""
                    if ev_chat_key and ev_chat_key != chat_key:
                        continue
                    # ACS events without chat_key: scope by session workdir presence
                    if not ev_chat_key and et.startswith("acs."):
                        run_id = d.get("run_id", "")
                        if run_id and not (sess.workdir / "acs" / "runs" / run_id).is_dir():
                            continue
                    result.append(ev)
        except OSError:
            pass
        return result

    entries: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for ev in (e for p in audit_paths for e in _parse_chain(p)):
        d = ev.get("details") or {}
        # Dedup by all discriminating fields — seq distinguishes multiple
        # tool_called events within the same turn; worker_id + iteration
        # distinguish per-worker events within the same ACS run.
        dedup_key = (
            f'{ev.get("event_type")}:'
            f'{d.get("turn_id") or ""}:'
            f'{d.get("run_id") or ""}:'
            f'{d.get("worker_id") or ""}:'
            f'{d.get("iteration") or 0}:'
            f'{d.get("seq") or 0}'
        )
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        et = ev.get("event_type", "")
        d = ev.get("details") or {}
        ts = float(ev.get("ts", 0.0) or 0.0)
        # Build a metadata-only detail dict
        safe_d: dict[str, Any] = {
            k: v for k, v in d.items() if k in _EXEC_LOG_ALLOWED_DETAILS
        }
        entries.append({
            "ts":         ts,
            "ts_iso":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
            "event_type": et,
            "role":       "os" if et.startswith("os_turn.") else "acs",
            "details":    safe_d,
        })

    entries.sort(key=lambda e: e["ts"])
    entries = entries[-limit:]
    return {"sid": sid, "chat_key": chat_key, "count": len(entries), "entries": entries}


# ── ADR-0171 M3 — universal engine spans (engine-agnostic) ───────────────────
_ENGINE_SPAN_EVENTS = {"engine.span.start", "engine.span.end"}
_ENGINE_SPAN_ALLOWED_DETAILS: set[str] = {
    "span_id", "parent_span_id", "role", "engine_id", "model_id",
    "run_id", "turn_id", "started_at", "status", "duration_ms",
    "tokens_used", "tool_call_count",
    "trace_available",  # ADR-0172 M1: drill-down signal
}


@router.get("/chat/sessions/{sid}/engine-spans")
def list_session_engine_spans(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    role: str = Query(default="", description="filter: os|manager|worker (empty=all)"),
    limit: int = Query(default=500, ge=1, le=4000),
) -> dict[str, Any]:
    """ADR-0171 — the UNIVERSAL engine-span view: every engine invocation (OS or
    worker, any engine_id, ACS-delegated or direct) as one paired span. Built from
    engine.span.start/end across BOTH audit chains, so the console renders every
    engine engine-agnostically — independent of the ACS/compute path. Metadata
    only (GDPR Art. 5)."""
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")
    chat_key = sess.chat_key
    from forge import paths as _fp  # type: ignore[import-untyped]
    corvin_home = _fp.corvin_home()
    audit_paths = [
        corvin_home / "global" / "forge" / "audit.jsonl",                 # OS spans
        corvin_home / "tenants" / rec.tenant_id / "global" / "audit.jsonl",  # worker spans
    ]
    # Run dirs that belong to this session (persisted + canonical home — same
    # stale-workdir fallback as /wdat so a home change can't hide spans).
    _run_roots = []
    for _wd in (sess.workdir, chat_runtime._workdir(rec.tenant_id, sid)):
        rd = _wd / "acs" / "runs"
        if rd.is_dir() and rd not in _run_roots:
            _run_roots.append(rd)

    def _in_session(ev: dict[str, Any]) -> bool:
        d = ev.get("details") or {}
        ck = d.get("chat_key") or ev.get("chat_key") or ""
        if ck:
            return ck == chat_key
        # Worker spans carry no chat_key → scope by the run dir living under the session.
        run_id = d.get("run_id", "")
        if run_id:
            return any((root / run_id).is_dir() for root in _run_roots)
        return False

    spans: dict[str, dict[str, Any]] = {}
    for path in audit_paths:
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    if '"engine.span.' not in raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("event_type") not in _ENGINE_SPAN_EVENTS:
                        continue
                    if not _in_session(ev):
                        continue
                    d = {k: v for k, v in (ev.get("details") or {}).items()
                         if k in _ENGINE_SPAN_ALLOWED_DETAILS}
                    span_id = d.get("span_id")
                    if not span_id:
                        continue
                    cur = spans.setdefault(span_id, {"span_id": span_id, "completed": False})
                    cur.update({k: v for k, v in d.items() if k != "span_id"})
                    try:
                        _ts = float(ev.get("ts", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        _ts = 0.0  # corrupted/hand-edited line — never 500 the view
                    if ev.get("event_type") == "engine.span.end":
                        cur["completed"] = True
                        cur["ts_end"] = _ts
                    else:
                        cur.setdefault("ts_start", _ts)
        except OSError:
            continue

    out = list(spans.values())
    if role:
        out = [s for s in out if s.get("role") == role]
    # Fall back to ts_end so an end-only span (start rotated out) still orders
    # chronologically instead of sorting to 0 and being truncated by out[-limit:].
    out.sort(key=lambda s: s.get("ts_start") or s.get("started_at")
             or s.get("ts_end") or 0.0)
    out = out[-limit:]
    # Roll up the engines seen so the UI can label "every engine audited".
    engines = sorted({s.get("engine_id") for s in out if s.get("engine_id")})
    roles = sorted({s.get("role") for s in out if s.get("role")})
    return {"sid": sid, "chat_key": chat_key, "count": len(out),
            "engines": engines, "roles": roles, "spans": out}


# ── ADR-0172 M2 — Worker-Trace endpoints ─────────────────────────────────────

@router.get("/chat/sessions/{sid}/worker-trace/{run_id}/{worker_id}")
def get_worker_deep_trace(
    sid: str,
    run_id: str,
    worker_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """ADR-0172 M2 — tool-call trace for a single worker invocation.

    Reads the pre-extracted workers/<worker_id>.trace.jsonl written by the ACS
    runtime after the worker subprocess completed. Metadata only (GDPR Art. 5):
    tool names, sequence, decision (allow/deny) — never tool input or output.

    Returns 404 when no trace file exists (older run, cancelled worker, or
    engine that does not produce tool-call data).
    """
    if not _ACS_RUN_ID_RE.match(run_id):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id format")
    if not _WORKER_ID_RE.match(worker_id):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid worker_id format")

    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    run_dir = sess.workdir / "acs" / "runs" / run_id
    if not run_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "run not found in this session")

    try:
        from worker_trace import read_trace, summarize_trace  # type: ignore[import-untyped]
    except ImportError:
        raise HTTPException(http_status.HTTP_501_NOT_IMPLEMENTED,
                            "worker_trace module not available")

    trace_file = run_dir / "workers" / f"{worker_id}.trace.jsonl"
    events = read_trace(trace_file)
    if not events:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no trace for this worker")

    # Split by event type: tool.called vs. agent.spawned
    tool_calls = [e for e in events if e.get("event") == "tool.called"]
    agent_calls = [e for e in events if e.get("event") == "agent.spawned"]

    # Safe field projection — never expose anything outside the allowed set.
    _TRACE_FIELDS = {"ts", "seq", "tool_name", "decision", "duration_ms",
                     "exit_code", "child_span_id", "depth"}
    return {
        "worker_id":  worker_id,
        "run_id":     run_id,
        "span_id":    events[0].get("span_id", "") if events else "",
        "tool_calls": [{k: v for k, v in e.items() if k in _TRACE_FIELDS}
                       for e in tool_calls],
        "agent_calls": [{k: v for k, v in e.items() if k in _TRACE_FIELDS}
                        for e in agent_calls],
        "summary":    summarize_trace(events),
    }


@router.get("/chat/sessions/{sid}/run-trace/{run_id}")
def get_run_trace(
    sid: str,
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """ADR-0172 M2 — aggregated tool-call trace for all workers in an ACS run.

    Reads all workers/*.trace.jsonl files under the run directory and returns
    a per-worker summary + a span-tree index keyed on span_id. Metadata only.
    """
    if not _ACS_RUN_ID_RE.match(run_id):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id format")

    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    run_dir = sess.workdir / "acs" / "runs" / run_id
    if not run_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "run not found in this session")

    try:
        from worker_trace import read_trace, summarize_trace  # type: ignore[import-untyped]
    except ImportError:
        raise HTTPException(http_status.HTTP_501_NOT_IMPLEMENTED,
                            "worker_trace module not available")

    workers_dir = run_dir / "workers"
    workers: list[dict[str, Any]] = []
    if workers_dir.is_dir():
        for trace_file in sorted(workers_dir.glob("*.trace.jsonl")):
            wid = trace_file.stem.replace(".trace", "")
            events = read_trace(trace_file)
            if not events:
                continue
            span_id = events[0].get("span_id", "") if events else ""
            workers.append({
                "worker_id": wid,
                "span_id":   span_id,
                "summary":   summarize_trace(events),
            })

    return {
        "run_id":       run_id,
        "worker_count": len(workers),
        "workers":      workers,
        "total_tool_calls": sum(w["summary"]["total_tool_calls"] for w in workers),
    }


@router.get("/chat/sessions/{sid}/debug")
def get_session_debug_log(
    sid: str,
    n: int = 200,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = ...,
) -> dict[str, Any]:
    """Return the last *n* structured debug events from <workdir>/chat_debug.jsonl.

    Events cover: turn.start, delegation.decision, acs.run.start/done,
    turn.done, plus any error/retry events emitted by the streaming engine.
    Useful for autonomous debugging — read to understand exactly what happened
    in a session without needing to reproduce the issue.

    Query params:
      n — max events to return (default 200, max 2000)
    """
    n = min(max(1, n), 2000)
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    def _read_jsonl(path: Path) -> list[dict]:
        events: list[dict] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        events.append({"_raw": line, "_parse_error": True})
        except FileNotFoundError:
            pass
        return events

    # Collect from current + up to 2 rotated files (oldest first)
    base = sess.workdir / "chat_debug.jsonl"
    all_events: list[dict] = []
    for suffix in [".jsonl.2", ".jsonl.1", ""]:
        p = base.with_suffix(suffix) if suffix else base
        all_events.extend(_read_jsonl(p))

    # Return last n
    tail = all_events[-n:] if len(all_events) > n else all_events
    return {
        "ok": True,
        "sid": sid,
        "total_events": len(all_events),
        "returned": len(tail),
        "events": tail,
    }
