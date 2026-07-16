"""corvin-browser MCP server (ADR-0193 Phase 1).

Exposes the browser action surface already defined in
``core/console/corvin_console/browser/tools.py`` (``BROWSER_TOOLS``) as native
MCP tools the same chat turn can call directly — see
``docs/browser-native-tool-integration.md`` and ADR-0193 for the full design.

This server does NOT reimplement browser control. ``BrowserSessionManager`` is
strictly in-process to the console's own FastAPI server (its own docstring:
"a console restart drops live sessions") — the SPA live-view polls frames and
the action log from that ONE manager singleton. This server is a separate OS
subprocess (spawned via ``--mcp-config`` alongside the ``claude`` CLI for one
chat turn), so it cannot share that in-memory state by importing the manager
directly — it would spawn a second, isolated set of browser sessions the live
view could never see. Instead, every tool function here is a thin HTTP client
against the console's own, already-running ``/v1/console/browser/*`` REST API
over loopback — the exact same routes the SPA calls — so a session created
here is the SAME session the live-view link in the model's own reply opens.

Auth: ``CORVIN_BROWSER_TOKEN`` (minted fresh per chat turn by
``chat_runtime.stream_turn``, see ``..browser.internal_auth``) is sent as the
``X-Corvin-Browser-Token`` header — verified by
``require_session_or_token``/``require_csrf_or_token`` in ``routes/browser.py``,
additive to (never a replacement for) the SPA's existing cookie+CSRF auth.
Deliberately NOT ``Authorization: Bearer``: the gateway's own ``_jwt_guard``
(``core/gateway/corvin_gateway/app.py``) rejects any non-JWT-shaped Bearer
token app-wide — a different, unrelated anti-downgrade gate for the
cloud/OIDC path that this internal, loopback-only credential should not
collide with (found live, during Phase 1 end-to-end verification).

L44 acceptable-use gate + ADR-0189 task-scoped-host extraction (ADR-0193
decision 3): moved here from ``routes/chat.py``'s ``_handle_browser_command``,
which only ran them once per ``/browser <task>`` chat command. EVERY
``browser_navigate`` call now runs its own L44 check on the target URL —
``_gate_navigate()`` is unconditional, decoupled from session
creation/reuse (an earlier version of this file gated only inside
``_ensure_session``, which short-circuited for every call after the first in
a session — an adversarial-review finding, fixed). Task-scoped-host
extraction stays tied to session *creation* only, by design: it reflects the
one host the model named when the session started, and correctly does not
widen automatically if a later call in the same session names a different
host — that still hits the normal cross-host confirm (see
``routes/browser.py::navigate``'s ``confirm_cross_host=rec.is_internal_tool``,
also an adversarial-review fix: this route's confirm used to default off
for every caller, including this tool's LLM-decided navigations).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

_HERE = Path(__file__).resolve().parent
_BRIDGES_SHARED = _HERE.parents[2] / "bridges" / "shared"
if _BRIDGES_SHARED.is_dir() and str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

from spawn_gates import check_l44  # type: ignore  # noqa: E402

# A click/fill landing on a sensitive action can park a human-in-the-loop
# confirm for up to BrowserSessionManager's own _CONFIRM_TIMEOUT_S (120s)
# before failing closed — this client's timeout must clear that plus margin,
# never race it (a client-side timeout would look like a crash, not a
# declined confirm).
_HTTP_TIMEOUT = 130.0
_DEFAULT_BASE_URL = "http://127.0.0.1:8765"

mcp = FastMCP("corvin-browser")


def _tenant_id() -> str:
    """Baked into this server's catalog entry at seed time (see
    ``mcp_manager.seed_builtin.ensure_corvin_browser``), one entry per
    tenant — unlike the per-turn token below, this is static and does not
    need to survive a subprocess-env-inheritance gap."""
    return os.environ.get("CORVIN_TENANT_ID") or "_default"


def _token() -> str:
    return os.environ.get("CORVIN_BROWSER_TOKEN", "").strip()


def _base_url() -> str:
    return (os.environ.get("CORVIN_BROWSER_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")


class BrowserToolError(RuntimeError):
    """A user-facing browser-tool failure — never a raw HTTP body/stack trace."""


def _request(method: str, path: str, *, json_body: dict | None = None,
             params: dict | None = None) -> Any:
    token = _token()
    if not token:
        # Should not happen on a real spawn (chat_runtime mints one per turn)
        # — fail closed with a clear message rather than an opaque 401.
        raise BrowserToolError(
            "Browser tool has no session token for this turn — please retry your request.")
    url = f"{_base_url()}{path}"
    headers = {"X-Corvin-Browser-Token": token}
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            resp = client.request(method, url, headers=headers, json=json_body, params=params)
    except httpx.TransportError as e:
        raise BrowserToolError(f"Could not reach the browser subsystem: {e}") from e
    if resp.status_code >= 400:
        detail = None
        try:
            detail = resp.json().get("detail")
        except Exception:  # noqa: BLE001
            pass
        raise BrowserToolError(str(detail) if detail else
                               f"Browser action failed (HTTP {resp.status_code})")
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


# ── session state (per chat-turn subprocess — see module docstring: a new
# ``claude`` turn spawns a fresh copy of this server, so this does NOT
# survive across turns; the model is expected to carry the session_id
# forward in its own reply/next tool calls, per ADR-0193 decision 7) ────────
_current_session: str | None = None


def _extract_host(url: str) -> list[str]:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return []
    return [host] if host else []


def _gate_navigate(url: str) -> None:
    """L44 acceptable-use check — called on EVERY ``browser_navigate``, not
    just the session-creating one (adversarial-review finding, ADR-0193: the
    gate used to live inside ``_ensure_session`` and so short-circuited for
    every call after the first in a session, since that function returns
    early for an existing/cached session id before ever reaching the check.
    Decoupled here so it is unconditional, matching ADR-0193 decision 3's own
    claim: 'every call path through the tool is gated at the point of use,
    not once at classification time.'"""
    tid = _tenant_id()
    refusal = check_l44(
        f"browse to {url}", tid, persona="assistant",
        channel="chat", chat_key=f"browser-mcp:{tid}", engine_id="claude_code")
    if refusal:
        raise BrowserToolError(refusal)


def _ensure_session(session_id: str, url: str) -> str:
    """Return ``session_id`` if given (an existing session), else create a
    new one — extracting the ADR-0189 task-scoped host from ``url`` for the
    new session (this carve-out is necessarily fixed at creation time: it
    reflects the FIRST host the model named, per BrowserSession's own
    immutable ``_task_scoped_hosts``; a later navigate to a different host
    within the same session correctly falls through to the normal cross-host
    confirm rather than silently widening the carve-out)."""
    global _current_session
    if session_id:
        return session_id
    if _current_session:
        return _current_session

    body = {"task_scoped_hosts": _extract_host(url)}
    result = _request("POST", "/v1/console/browser/session", json_body=body)
    sid = result.get("session")
    if not sid:
        raise BrowserToolError("Could not start a browser session.")
    _current_session = sid
    return sid


@mcp.tool()
def browser_navigate(url: str, session_id: str = "") -> dict:
    """Open a URL (http/https only). Creates a new browser session on the
    first call of a conversation (pass session_id="" / omit it); pass the
    returned session_id back in on later calls to keep using the SAME
    browser tab. Egress-gated by the tenant allowlist and an acceptable-use
    check on the target URL — run on every call, not just the first. Returns
    the Set-of-Marks observation of the loaded page plus the session_id —
    mention the session_id and the `/console/app/browser?sid=<session_id>`
    live-view link in your reply so the user can watch.
    """
    _gate_navigate(url)
    sid = _ensure_session(session_id, url)
    obs = _request("POST", f"/v1/console/browser/{sid}/navigate", json_body={"url": url})
    return {**obs, "session_id": sid}


@mcp.tool()
def browser_observe(session_id: str) -> dict:
    """Re-scan the current page and return the numbered list of interactive
    elements (Set-of-Marks). Call this after any page change."""
    return _request("POST", f"/v1/console/browser/{session_id}/observe")


@mcp.tool()
def browser_click(session_id: str, index: int) -> dict:
    """Click the element with the given mark index. Sensitive clicks
    (buy/send/delete/login) require user confirmation — this call blocks
    until the user resolves it or it times out."""
    return _request("POST", f"/v1/console/browser/{session_id}/click", json_body={"index": index})


@mcp.tool()
def browser_fill(session_id: str, index: int, text: str) -> dict:
    """Type text into the field with the given mark index. The value is
    never logged or audited."""
    return _request("POST", f"/v1/console/browser/{session_id}/fill",
                    json_body={"index": index, "text": text})


@mcp.tool()
def browser_fill_secret(session_id: str, index: int, vault_key: str) -> dict:
    """Type a secret resolved from the vault by key name into the field. The
    value never enters the model context or any log."""
    return _request("POST", f"/v1/console/browser/{session_id}/fill_secret",
                    json_body={"index": index, "vault_key": vault_key})


@mcp.tool()
def browser_read(session_id: str, index: "int | None" = None) -> dict:
    """Read visible text — of one element (by index) or the whole page body
    (index omitted). Bounded length."""
    return _request("POST", f"/v1/console/browser/{session_id}/read",
                    json_body={"index": index})


@mcp.tool()
def browser_scroll(session_id: str, direction: str = "down") -> dict:
    """Scroll the page: down | up | top | bottom."""
    return _request("POST", f"/v1/console/browser/{session_id}/scroll",
                    json_body={"direction": direction})


@mcp.tool()
def browser_back(session_id: str) -> dict:
    """Go back one page in history and return the new observation."""
    return _request("POST", f"/v1/console/browser/{session_id}/back")


@mcp.tool()
def browser_screenshot(session_id: str) -> list:
    """Return a screenshot of the current viewport with the mark overlay
    painted on."""
    result = _request("POST", f"/v1/console/browser/{session_id}/screenshot")
    data_url = result.get("data_url", "")
    if not data_url.startswith("data:image/"):
        raise BrowserToolError("Screenshot unavailable.")
    header, _, b64 = data_url.partition(",")
    fmt = "jpeg" if "jpeg" in header or "jpg" in header else "png"
    import base64 as _b64  # noqa: PLC0415 — only needed on this path
    return [Image(data=_b64.b64decode(b64), format=fmt)]


@mcp.tool()
def browser_hover(session_id: str, index: int) -> dict:
    """Hover the element with the given mark index (e.g. to reveal a
    hover-only menu) without clicking it."""
    return _request("POST", f"/v1/console/browser/{session_id}/hover", json_body={"index": index})


@mcp.tool()
def browser_key(session_id: str, key: str) -> dict:
    """Press a single named key on the page: Enter, Tab, Escape, Backspace,
    Delete, Space, Arrow*, Home/End, PageUp/PageDown, F1-F12. A committing
    key (Enter/Space) on a sensitive form requires user confirmation."""
    return _request("POST", f"/v1/console/browser/{session_id}/key", json_body={"key": key})


@mcp.tool()
def browser_select_option(session_id: str, index: int, value: str) -> dict:
    """Choose an option (by its value attribute) in the <select> with the
    given mark index. The chosen value is never logged."""
    return _request("POST", f"/v1/console/browser/{session_id}/select_option",
                    json_body={"index": index, "value": value})


@mcp.tool()
def browser_upload_file(session_id: str, index: int, filename: str) -> dict:
    """Attach a file to the file-input with the given mark index. The file
    must already exist under the session's uploads directory."""
    return _request("POST", f"/v1/console/browser/{session_id}/upload_file",
                    json_body={"index": index, "filename": filename})


@mcp.tool()
def browser_drag(session_id: str, from_index: int, to_index: int) -> dict:
    """Drag the element at from_index onto the element at to_index (e.g. a
    slider or reorder handle)."""
    return _request("POST", f"/v1/console/browser/{session_id}/drag",
                    json_body={"from_index": from_index, "to_index": to_index})


@mcp.tool()
def browser_tabs(session_id: str) -> dict:
    """List every open tab in this session (index, url, title) — including a
    tab opened by a target=_blank click or window.open."""
    return _request("POST", f"/v1/console/browser/{session_id}/tabs")


@mcp.tool()
def browser_switch_tab(session_id: str, index: int) -> dict:
    """Make the tab with the given index (from browser_tabs) the active page
    and return its Set-of-Marks observation."""
    return _request("POST", f"/v1/console/browser/{session_id}/switch_tab",
                    json_body={"index": index})


@mcp.tool()
def browser_extract_table(session_id: str, index: int) -> dict:
    """Parse the table (or table-role container) at the given mark index
    into {headers, rows} JSON. Bounded row count."""
    return _request("POST", f"/v1/console/browser/{session_id}/extract_table",
                    json_body={"index": index})


@mcp.tool()
def browser_extract_form_schema(session_id: str) -> dict:
    """Describe every <form> on the current top-level document
    (action/method + field name/type/required/label). Never includes any
    field's current value."""
    return _request("POST", f"/v1/console/browser/{session_id}/extract_form_schema")


@mcp.tool()
def browser_close(session_id: str) -> dict:
    """Close a browser session you're done with, releasing its resources.
    Not required — the session also closes on its own when the turn/tenant
    session cap needs the slot — but calling it when a task is finished is
    good practice."""
    global _current_session
    # Clear the cached session id even when the close call itself fails
    # (adversarial-review finding: a session already reaped server-side —
    # tenant cap, TTL — makes this raise, and without the `finally` every
    # later browser_navigate(session_id="") kept returning the same dead
    # id via _ensure_session's cache, wedging the tool for the rest of the
    # turn with no way to recover except a fresh subprocess next turn).
    try:
        return _request("POST", f"/v1/console/browser/{session_id}/close")
    finally:
        if _current_session == session_id:
            _current_session = None


if __name__ == "__main__":
    mcp.run()
