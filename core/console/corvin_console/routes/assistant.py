"""Console floating assistant — stateless chat endpoint.

Routes:
  POST /assistant/message   → run one claude -p turn, return text response.
  GET  /assistant/ping      → check if assistant is available.

The assistant is stateless (no bridge sessions, no audit chain, no memory
recall).  Multi-turn context is provided by the client as a history list
that is embedded verbatim in the prompt.
"""
from __future__ import annotations

import html
import subprocess
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from .. import _spawn_gates  # shared fail-closed pre-spawn chokepoint (CRITICAL compliance)
from ..deps import require_csrf, require_session

router = APIRouter()

# ── System prompt ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are the **Corvin Console Assistant** — an expert helper embedded in the \
CorvinOS operator web console.

## Language (critical rule)
Detect the operator's language from their FIRST message and reply in that \
exact language for the entire conversation. All languages are supported — \
German, English, French, Spanish, Italian, Dutch, Portuguese, Russian, \
Japanese, Chinese, Korean, Arabic, Polish, and any other language the \
operator uses. The <console_context user_language="..."> tag provides a \
BCP-47 hint from the browser, but the operator's actual message text takes \
precedence. Never switch languages mid-conversation.

## Reply style
- Short and direct: 2–4 sentences for simple questions, up to 10 lines for \
complex ones.
- No markdown headers or bullet lists unless the operator explicitly asks for \
a comparison or overview.
- Warm but efficient. Never condescending.
- When suggesting navigation: use the exact sidebar label the operator sees.

## CorvinOS Console — pages and their purpose
| Path | Sidebar label | What it does |
|---|---|---|
| /app/dashboard | Dashboard | Live overview: sessions, bridge health, recent activity |
| /app/sessions | Sessions | All active conversation sessions per bridge/chat |
| /app/bridges | Bridges | Connect messaging channels: Discord, WhatsApp, Telegram, Web |
| /app/personas | Personas | AI personality profiles — system prompt, MCP servers, LDD layers |
| /app/workflows | Workflows | Multi-step background tasks: create, edit, trigger, inspect runs |
| /app/connectors | Connectors | External data source and API connector configuration |
| /app/agent-hub | Agent Hub | Connect remote A2A agents for task delegation |
| /app/engines | Engines | AI engine settings: ClaudeCode, Hermes (local Ollama), Copilot, OpenCode |
| /app/skills | Skills | Forge skills: create, grade, promote, purge |
| /app/tools | Tools | Forge tools: create, test, promote |
| /app/memory | Memory | User recall DB and user model profiles |
| /app/audit | Audit | Hash-chained tamper-evident audit log (GDPR Art. 30/32) |
| /app/license | License | License key management, tier, feature limits |
| /app/settings | Settings | Rate limits, whitelist, voice config, global defaults |
| /app/members | Members | User roles: owner › admin › member › observer; grant/revoke |
| /app/space | Space | File and workspace browser |
| /app/compute | Compute | ML experiment runner with parameter sweeps (L25) |
| /app/remote-trigger | Remote Trigger | A2A inbound/outbound endpoint management |
| /app/setup | Setup | Initial setup wizard: engine, bridge, first-run checks |

## Core CorvinOS concepts
- **Bridge**: a messaging channel adapter (Discord bot, WhatsApp cloud, \
Telegram bot). Each bridge has a settings.json that hot-reloads.
- **Persona**: an AI personality with its own system prompt, LDD config, \
MCP servers, permission scope. Bundle personas: operator/cowork/personas/.
- **Workflow**: automated multi-step task that runs independently in the \
background. Triggered manually or via API.
- **Forge tool**: dynamically generated Python tool, sandboxed in bwrap, \
available via MCP. Named "code.<tool_name>".
- **Skill**: Markdown prompt fragment auto-injected into AI context for \
specific tasks; grades up through task→session→project→user scopes.
- **Engine**: the AI model backend. Default: ClaudeCode (Anthropic). \
Hermes = local Ollama (zero egress, CONFIDENTIAL-capable). Copilot CLI, \
OpenCode available.
- **Audit log**: tamper-evident hash-chained events at \
~/.corvin/tenants/<id>/global/audit.jsonl. Verified by daily timer.
- **Tenant**: fully isolated environment (data, settings, audit chain). \
Default: _default. Multiple tenants require Pro+ license.
- **Session permit**: server-issued JWT (ADR-0095) authorising the current \
subscription tier. Renewed every 48 h by the refresh daemon.
- **Consent gate**: per-user opt-in before the AI responds (GDPR Art. 6/7). \
Commands: /consent on|off|<ttl>. Deny-by-default.
- **LDD**: Loss-Driven Development — 12 quality layers that guide how the \
AI tackles engineering tasks (loops, dialectics, E2E-first, doc-sync, …).

## Actions you can trigger
When the operator says "go to", "open", "show", "navigate to" a page, \
embed this JSON at the very end of your response (the UI executes it and \
strips it from the displayed text):
{"_actions": [{"type": "navigate", "path": "/app/<section>"}]}

For settings you can patch:
{"_actions": [{"type": "patch_setting", "route": "/settings/<field>", \
"body": {…}, "label": "<human-readable description>"}]}

Only include the JSON when an action is warranted. Otherwise omit it entirely.

## What you cannot do
- Access the internet or external URLs
- Read file contents, audit logs, or conversation history directly
- Execute shell commands on the server
- Access message content from bridge conversations
"""

# ── Request / response models ──────────────────────────────────────────────


class HistoryEntry(BaseModel):
    role: str   # "user" | "assistant"
    content: str = Field(..., max_length=2_000)
    model_config = {"extra": "forbid"}


class AssistantMessageRequest(BaseModel):
    message: str = Field(..., max_length=4_000)
    context: dict[str, Any] = Field(default_factory=dict)
    history: list[HistoryEntry] = Field(default_factory=list, max_length=10)
    model_config = {"extra": "forbid"}


# ── Helpers ────────────────────────────────────────────────────────────────


def _build_context_tag(ctx: dict[str, Any]) -> str:
    attrs: list[str] = []
    if ctx.get("current_page"):
        attrs.append(f'page="{html.escape(str(ctx["current_page"]), quote=True)}"')
    st = ctx.get("setup_status") or {}
    if "engine_connected" in st:
        attrs.append(
            f'engine="{("connected" if st["engine_connected"] else "not_connected")}"'
        )
    bridges = st.get("bridges_configured") or []
    if bridges:
        attrs.append(f'bridges="{",".join(str(b) for b in bridges)}"')
    if ctx.get("license_tier"):
        attrs.append(f'tier="{html.escape(str(ctx["license_tier"]), quote=True)}"')
    if ctx.get("personas"):
        personas_str = ",".join(str(p) for p in ctx["personas"])
        attrs.append(f'personas="{html.escape(personas_str, quote=True)}"')
    if ctx.get("language"):
        attrs.append(f'user_language="{html.escape(str(ctx["language"]), quote=True)}"')
    if not attrs:
        return ""
    return f"<console_context {' '.join(attrs)} />\n"


def _build_history_block(history: list[HistoryEntry]) -> str:
    if not history:
        return ""
    lines = ["<conversation_history>"]
    for entry in history:
        role = "User" if entry.role == "user" else "Assistant"
        lines.append(f"{role}: {entry.content}")
    lines.append("</conversation_history>")
    return "\n".join(lines) + "\n"


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get("/assistant/ping")
def assistant_ping(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Check if claude CLI is available for the assistant."""
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, timeout=5)
        available = r.returncode == 0
        version = (
            r.stdout.decode("utf-8", errors="replace").strip().split("\n")[0]
            if available
            else None
        )
    except FileNotFoundError:
        available, version = False, None
    except Exception:
        available, version = False, None
    return {"available": available, "version": version}


@router.post("/assistant/message")
def assistant_message(
    body: AssistantMessageRequest,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Run one assistant turn via claude -p and return the text response.

    The prompt includes:
    - <console_context> tag with current page, engine state, bridges, tier
    - <conversation_history> block (up to last 10 messages for multi-turn context)
    - The current user message
    """
    # ADR-0150 LIC-ASSISTANT-SPAWN-01: the console floating-assistant spawns a
    # paid (default-Sonnet, free-form) `claude -p` turn — the THIRD interactive
    # chat surface alongside the web-chat WS and the design-assistant WS. Charge
    # the chat_turns_per_day axis (not compute) before spawning, fail-closed. HTTP
    # route → the 402 propagates directly.
    from ._compute_license_gate import enforce_chat_turns  # noqa: PLC0415
    enforce_chat_turns(
        _rec.tenant_id, _rec.sid_fingerprint,
        audit_action="assistant.turn", channel="assistant",
    )

    # ── Round-4 finding #2: fail-closed, audit-first pre-spawn gate ──────────
    # The floating console assistant spawns `claude -p` on the operator's
    # message. The bridge adapter gates every OS-turn spawn with L44
    # acceptable-use + ADR-0141 capability + L34/L35; this route did NOT — an
    # authenticated ungated LLM spawn path. Gate `body.message` (the user's
    # actual instruction; the system prompt + context tag are trusted framing)
    # BEFORE the subprocess.run; on deny return the refusal as the assistant
    # response (the gate already wrote its L16 deny event, audit-first).
    # The console assistant answers UI-help questions — it never processes
    # protected user data, so classify as PUBLIC to avoid L34 blocking
    # claude_code (us_cloud) when the tenant's INTERNAL matrix only allows
    # eu_cloud/local.  L44 + capability gates still run fail-closed.
    _asst_refusal = _spawn_gates.check_console_spawn_or_refusal(
        body.message, tenant_id=_rec.tenant_id, persona="assistant",
        channel="assistant", chat_key=f"assistant:{_rec.sid_fingerprint}",
        engine_id="claude_code", classification="PUBLIC",
    )
    if _asst_refusal is not None:
        console_audit.action_performed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="assistant.turn",
            target_kind="assistant",
            target_id="console",
            trigger="pre_spawn_gate_blocked",
        )
        return {"ok": True, "response": _asst_refusal}

    ctx_tag = _build_context_tag(body.context)
    history_block = _build_history_block(body.history)
    full_prompt = f"{ctx_tag}{history_block}{body.message}"

    try:
        result = subprocess.run(
            [
                "claude",
                "-p",
                "--max-turns", "1",
                "--tools", "",
                "--system-prompt", _SYSTEM_PROMPT,
                full_prompt,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        response = result.stdout.strip()
        if not response and result.stderr:
            err = result.stderr.strip().split("\n")[0]
            response = f"Sorry, I couldn't process that: {err}"
    except FileNotFoundError:
        response = (
            "Claude Code CLI is not installed or not in PATH. "
            "Please connect your engine in the Setup section."
        )
    except subprocess.TimeoutExpired:
        response = "The request timed out (60 s). Please try again with a shorter question."
    except Exception:
        response = "An unexpected error occurred. Please try again."

    console_audit.action_performed(
        tenant_id=_rec.tenant_id,
        sid_fingerprint=_rec.sid_fingerprint,
        action="assistant.turn",
        target_kind="assistant",
        target_id="console",
    )
    return {"ok": True, "response": response}
