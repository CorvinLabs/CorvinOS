"""Setup helpers — bridge connection guides, engine key management, and global commands.

Routes:
  GET  /setup/bridge/{channel}      status + pairing info per bridge
  GET  /setup/whatsapp/qr.png       proxy WhatsApp daemon QR code (PNG)
  GET  /setup/engines               list engine keys from service.env
  PUT  /setup/engines/{key}         update a single key in service.env
  GET  /setup/commands              list all available slash commands by category
"""
from __future__ import annotations

import concurrent.futures
import json as _json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status as http_status
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session, verify_reauth

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))
from forge import paths as _forge_paths  # noqa: E402

router = APIRouter()

_SETUP_COMPLETE_PATH = _forge_paths.voice_config_dir() / ".corvin_setup_complete"

# ADR-0120 — structured onboarding state (written at POST /setup/complete)
_ONBOARDING_JSON_PATH = _forge_paths.corvin_home() / "tenants" / "_default" / "global" / "onboarding.json"

# Shared path to engine_detector module
_SHARED = _REPO / "operator" / "bridges" / "shared"


def _onboarding_complete() -> bool:
    """Return True iff onboarding.json exists with complete=true."""
    try:
        data = _json.loads(_ONBOARDING_JSON_PATH.read_text())
        return bool(data.get("complete"))
    except Exception:
        return False


def _default_engine(tenant_id: str = "_default") -> str:
    """Resolve the tenant's ``spec.default_engine`` from ``tenant.corvin.yaml``.

    ADR-0007: tenant_id is supplied by the caller from the authenticated
    SessionRecord (never an env var). Returns ``""`` on any miss so callers can
    fall back gracefully. Lower-cased for stable comparison."""
    try:
        import yaml  # type: ignore[import-not-found]
        cfg = (
            _forge_paths.corvin_home()
            / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
        )
        if not cfg.is_file():
            return ""
        doc = yaml.safe_load(cfg.read_text("utf-8")) or {}
        spec = doc.get("spec") if isinstance(doc, dict) else None
        eng = (spec or {}).get("default_engine") if isinstance(spec, dict) else None
        return str(eng).strip().lower() if isinstance(eng, str) else ""
    except Exception:
        return ""


def _optional_session(
    corvin_console_sid: Annotated[str | None, Cookie()] = None,
) -> session_auth.SessionRecord | None:
    """Like require_session but returns None instead of raising 401."""
    if not corvin_console_sid:
        return None
    return session_auth.load_session(corvin_console_sid)

# ── Service.env path ──────────────────────────────────────────────────────
_SERVICE_ENV = _forge_paths.voice_config_dir() / "service.env"

# Keys we expose in the UI (display label, env var name, sensitive)
_ENGINE_KEYS: list[dict[str, Any]] = [
    {"id": "claude_code",   "label": "Claude Code",        "kind": "oauth",   "key": None,                      "url": "https://claude.ai/code"},
    {"id": "anthropic",     "label": "Anthropic API",       "kind": "api_key", "key": "ANTHROPIC_API_KEY",        "url": "https://console.anthropic.com/account/keys"},
    {"id": "openai",        "label": "OpenAI / Codex",      "kind": "api_key", "key": "OPENAI_API_KEY",           "url": "https://platform.openai.com/api-keys"},
    {"id": "stt_openai",   "label": "OpenAI Whisper (STT)", "kind": "api_key", "key": "CORVIN_STT_OPENAI_KEY",    "url": "https://platform.openai.com/api-keys"},
    {"id": "tts_openai",   "label": "OpenAI TTS (Sprache)", "kind": "api_key", "key": "CORVIN_TTS_OPENAI_KEY",    "url": "https://platform.openai.com/api-keys"},
    {"id": "gemini",        "label": "Google Gemini",       "kind": "api_key", "key": "GEMINI_API_KEY",           "url": "https://aistudio.google.com/app/apikey"},
    {"id": "ollama",        "label": "Ollama (local)",      "kind": "url",     "key": "OLLAMA_BASE_URL",          "url": "https://ollama.com/download"},
    {"id": "openrouter",    "label": "OpenRouter",          "kind": "api_key", "key": "OPENROUTER_API_KEY",       "url": "https://openrouter.ai/keys"},
    # ADR-0071 — GitHub Copilot CLI binary detection (no API key; authenticated via copilot auth login).
    {"id": "copilot",       "label": "GitHub Copilot",      "kind": "url",     "key": None,                      "url": "https://github.com/github/copilot-cli/releases"},
]

_BRIDGE_GUIDES: dict[str, dict[str, Any]] = {
    "discord": {
        "display": "Discord",
        "icon": "discord",
        "token_key": "discord_token",
        "setup_url": "https://discord.com/developers/applications",
        "steps": [
            "Go to discord.com/developers/applications and click **New Application**.",
            "Under **Bot**, click **Add Bot** → **Reset Token** → copy the token.",
            "Enable **Message Content Intent** under Privileged Gateway Intents.",
            "Under **OAuth2 → URL Generator**, select **bot** scope + **Send Messages**, **Read Message History** permissions.",
            "Open the generated URL to invite the bot to your server.",
            "Paste the bot token into the field below and save.",
        ],
        "field_label": "Bot Token",
        "field_placeholder": "Paste your Discord bot token…",
    },
    "telegram": {
        "display": "Telegram",
        "icon": "telegram",
        "token_key": "telegram_token",
        "setup_url": "https://t.me/BotFather",
        "steps": [
            "Open Telegram and start a chat with **@BotFather**.",
            "Send `/newbot` and follow the prompts to name your bot.",
            "BotFather will give you an **HTTP API token** — copy it.",
            "Paste the token into the field below and save.",
            "Send `/start` to your new bot to register your chat.",
        ],
        "field_label": "Bot Token",
        "field_placeholder": "123456789:ABCdef…",
    },
    "whatsapp": {
        "display": "WhatsApp",
        "icon": "whatsapp",
        "token_key": None,  # QR-based, no token field
        "setup_url": None,
        "steps": [
            "Click **Start WhatsApp bridge** below. Corvin installs Node.js + the WhatsApp dependencies if needed (one-time) and starts the bridge for you — no terminal required.",
            "A QR code appears below as soon as the bridge is running.",
            "On your phone: open **WhatsApp → Settings → Linked Devices → Link a Device**.",
            "Point your phone at the QR code. The bridge links automatically and this page turns green.",
            "Terminal alternative (Linux/macOS): run `bridge.sh up`. To link by number instead of QR, start with `--pair-code +49123456789`.",
        ],
        "qr_port": 7891,
        "field_label": None,
        "field_placeholder": None,
    },
    "slack": {
        "display": "Slack",
        "icon": "slack",
        "token_key": "slack_bot_token",
        "setup_url": "https://api.slack.com/apps",
        "steps": [
            "Go to api.slack.com/apps → **Create New App → From Scratch**.",
            "Under **OAuth & Permissions**, add Bot Token Scopes: `chat:write`, `channels:read`, `channels:history`.",
            "Click **Install to Workspace** → copy the **Bot User OAuth Token** (starts with `xoxb-`).",
            "Under **Event Subscriptions**, enable events and add `message.channels`, `message.im`.",
            "Paste the bot token below and save.",
        ],
        "field_label": "Bot Token (xoxb-…)",
        "field_placeholder": "xoxb-…",
    },
    "email": {
        "display": "Email",
        "icon": "email",
        "token_key": None,
        "setup_url": None,
        "steps": [
            "Set `GMAIL_USER` and `GMAIL_APP_PASSWORD` in `~/.config/corvin-voice/service.env`.",
            "Generate an App Password in your Google Account under Security → 2-Step Verification → App Passwords.",
            "Or use any IMAP/SMTP server by setting `EMAIL_IMAP_HOST`, `EMAIL_SMTP_HOST`, etc.",
            "The bridge polls IMAP every 30s for new messages.",
        ],
        "field_label": None,
        "field_placeholder": None,
    },
    "signal": {
        "display": "Signal",
        "icon": "signal",
        "token_key": "signal_phone_number",
        "setup_url": "https://github.com/bbernhard/signal-cli-rest-api",
        "steps": [
            "Install signal-cli and signal-cli-rest-api (see link above).",
            "Register a phone number: `signal-cli -a +49123456789 register`.",
            "Verify: `signal-cli -a +49123456789 verify <code>`.",
            "Start the REST API server (default port 8080).",
            "Enter the phone number below.",
        ],
        "field_label": "Phone number (+49…)",
        "field_placeholder": "+49123456789",
    },
}

# Global commands reference — all slash commands supported across all bridges
_GLOBAL_COMMANDS: dict[str, list[dict[str, Any]]] = {
    "Conversation Control": [
        {
            "name": "/clear",
            "description": "Clear chat history",
            "syntax": "/clear",
            "details": "Removes all messages from the current conversation."
        },
        {
            "name": "/new",
            "description": "Start new conversation",
            "syntax": "/new",
            "details": "Starts a fresh conversation, resets session state."
        },
        {
            "name": "/reset",
            "description": "Reset all state",
            "syntax": "/reset",
            "details": "Complete reset of chat context and session data."
        },
        {
            "name": "/stop",
            "description": "Abort currently running task",
            "syntax": "/stop",
            "details": "Sends SIGTERM to terminate the current operation immediately. Aliases: /cancel, /abbruch, /halt"
        },
        {
            "name": "/btw <text>",
            "description": "Inject a follow-up note while task is running",
            "syntax": "/btw <text>",
            "details": "Mid-stream injection to add context or redirect the running task."
        },
    ],
    "Personas & Models": [
        {
            "name": "/personas",
            "description": "List all available cowork personas",
            "syntax": "/personas",
            "details": "Shows all AI assistant styles (coder, researcher, browser, etc.) that can be activated."
        },
        {
            "name": "/persona set <name>",
            "description": "Change AI assistant style",
            "syntax": "/persona set <persona_name>",
            "details": "Example: /persona set researcher. Back to default with: /persona reset"
        },
        {
            "name": "/whoami",
            "description": "Show current persona + tools + config",
            "syntax": "/whoami",
            "details": "Displays the active persona, available abilities, and LDD configuration."
        },
        {
            "name": "/skills",
            "description": "Show available abilities in current persona",
            "syntax": "/skills",
            "details": "Lists tools and skills available in the currently active persona."
        },
    ],
    "Access Control & Teamwork": [
        {
            "name": "/role",
            "description": "Show your own role and capabilities",
            "syntax": "/role",
            "details": "Displays your current permission level (owner, admin, member, observer)."
        },
        {
            "name": "/roles",
            "description": "Full chat overview with all users",
            "syntax": "/roles",
            "details": "Owner/admin only: lists all users in this chat with their roles and capabilities."
        },
        {
            "name": "/grant <uid> <bundle>",
            "description": "Delegate authority to a user",
            "syntax": "/grant <uid> <bundle> [<ttl>] [<reason>]",
            "details": "Owner/admin: grant admin, member, or observer role. Example: /grant @user member 7d"
        },
        {
            "name": "/revoke <uid>",
            "description": "Remove a granted role",
            "syntax": "/revoke <uid>",
            "details": "Owner/admin: revoke all granted roles from a user."
        },
        {
            "name": "/leave",
            "description": "Give up your own granted role",
            "syntax": "/leave",
            "details": "Owners cannot use this. Reverts you to observer status if you were granted a role."
        },
    ],
    "Onboarding & Consent": [
        {
            "name": "/join",
            "description": "Self-register as observer",
            "syntax": "/join",
            "details": "Shows the AI disclosure card and registers you as a read-only observer in the chat."
        },
        {
            "name": "/pass",
            "description": "Acknowledge disclosure without action",
            "syntax": "/pass",
            "details": "Close the disclosure card without registering or granting consent."
        },
        {
            "name": "/consent on",
            "description": "Grant durable consent",
            "syntax": "/consent on",
            "details": "Grant permanent consent for your messages to be processed (read-only senders only)."
        },
        {
            "name": "/consent <duration>",
            "description": "Time-bounded consent",
            "syntax": "/consent <duration>",
            "details": "Examples: /consent 30s, /consent 5m, /consent 1h, /consent 7d (max 30d)"
        },
        {
            "name": "/consent status",
            "description": "Show your consent state",
            "syntax": "/consent status",
            "details": "Displays current consent grant, TTL remaining, and history."
        },
        {
            "name": "/consent off",
            "description": "Revoke consent",
            "syntax": "/consent off",
            "details": "Withdraw your consent for message processing immediately."
        },
    ],
    "Quotas & Audit": [
        {
            "name": "/quota",
            "description": "Show your usage today",
            "syntax": "/quota",
            "details": "Displays your message count and token usage for the rolling 24-hour period."
        },
        {
            "name": "/audit me",
            "description": "Your own recent events",
            "syntax": "/audit me [<n>]",
            "details": "Shows last N events (default 20) involving you. Filter by event type: /audit me 20 action_performed"
        },
        {
            "name": "/audit chat",
            "description": "Chat-wide event log",
            "syntax": "/audit chat [<n>]",
            "details": "Owner/admin only: all events in this chat. Example: /audit chat 50"
        },
    ],
    "Memory & Recall": [
        {
            "name": "/memory list",
            "description": "Show all memory topics",
            "syntax": "/memory list",
            "details": "Lists all saved topics with one-line summaries."
        },
        {
            "name": "/memory show <topic>",
            "description": "Display full memory content",
            "syntax": "/memory show <topic>",
            "details": "Retrieves the complete content of a memory topic."
        },
        {
            "name": "/memory write <topic> <text>",
            "description": "Create or overwrite memory",
            "syntax": "/memory write <topic> <text>",
            "details": "Replaces the entire content for a topic (or creates it)."
        },
        {
            "name": "/memory forget <topic>",
            "description": "Delete a memory topic",
            "syntax": "/memory forget <topic>",
            "details": "Permanently removes a topic (GDPR Art. 17 erasure)."
        },
    ],
    "Settings & Preferences": [
        {
            "name": "/voice-user-set <field>=<value>",
            "description": "Configure voice output preferences",
            "syntax": "/voice-user-set <field>=<value>",
            "details": "Set fields: level, jargon, style, background, metaphors, domains, learning. Example: /voice-user-set tone casual"
        },
        {
            "name": "/voice-user-show",
            "description": "Show listener profile",
            "syntax": "/voice-user-show",
            "details": "Displays the current voice listener profile configuration."
        },
        {
            "name": "/lang <code>",
            "description": "Set display language",
            "syntax": "/lang <code>",
            "details": "BCP-47 code. Example: /lang de for German, /lang en for English. /lang list shows all."
        },
        {
            "name": "/goal [<text>]",
            "description": "Set sticky session objective",
            "syntax": "/goal [<text>|clear]",
            "details": "Set a goal that gets injected into every LLM turn for context."
        },
    ],
    "Loss-Driven Development (LDD)": [
        {
            "name": "/ldd-status",
            "description": "Show LDD layer state",
            "syntax": "/ldd-status",
            "details": "Displays master toggle, per-layer settings, and cascade hints."
        },
        {
            "name": "/ldd-on",
            "description": "Enable LDD globally",
            "syntax": "/ldd-on",
            "details": "Activates all LDD layers for this chat."
        },
        {
            "name": "/ldd-off",
            "description": "Disable LDD globally",
            "syntax": "/ldd-off",
            "details": "Kill-switch: disables all LDD functionality."
        },
        {
            "name": "/ldd-set <layer> <on|off>",
            "description": "Toggle specific layer",
            "syntax": "/ldd-set <layer> <on|off>",
            "details": "Example: /ldd-set e2e_driven_iteration on"
        },
    ],
    "Engine & Delegation": [
        {
            "name": "/engine <name>",
            "description": "Switch AI worker engine",
            "syntax": "/engine <name>",
            "details": "Options: claude (default), hermes (local Ollama), copilot (GitHub CLI). Example: /engine hermes"
        },
    ],
    "Help & Diagnostics": [
        {
            "name": "/help",
            "description": "Show available commands",
            "syntax": "/help",
            "details": "Displays command overview in the chat. Aliases: /hilfe, /cowork, /?"
        },
        {
            "name": "/welcome",
            "description": "Show Corvin intro card",
            "syntax": "/welcome",
            "details": "Displays the bot disclosure and welcome message. Aliases: /willkommen, /start, /hi"
        },
        {
            "name": "/settings",
            "description": "Dump system state",
            "syntax": "/settings",
            "details": "Shows paths, session details, system information for debugging. Aliases: /einstellungen, /config"
        },
    ],
    "Proposals & Decisions": [
        {
            "name": "/propose <text>",
            "description": "Add idea to stack",
            "syntax": "/propose <text>",
            "details": "Anyone can propose ideas. Owner/admin can trigger with /go"
        },
        {
            "name": "/proposals",
            "description": "List proposal stack",
            "syntax": "/proposals",
            "details": "Owner/admin only: shows all pending proposals."
        },
        {
            "name": "/go [steering]",
            "description": "Trigger AI with proposals",
            "syntax": "/go [steering]",
            "details": "Owner/admin: consume the proposal stack and spawn AI to process all ideas atomically."
        },
    ],
    "Privacy & Compliance": [
        {
            "name": "/privacy",
            "description": "GDPR right to information",
            "syntax": "/privacy",
            "details": "Shows GDPR Art. 13/14 information about data processing. Alias: /datenschutz"
        },
        {
            "name": "/decision-review",
            "description": "EU AI Act Art. 14 oversight",
            "syntax": "/decision-review",
            "details": "Owner/admin: request human oversight documentation. Alias: /decision"
        },
    ],
}


def _read_service_env() -> dict[str, str]:
    # WA-22: a second, repo-relative candidate (operator/bridges/shared/
    # service.env) was checked here too, but never existed on any real
    # install (no writer ever targeted it, no git history) — dead weight
    # that just implied a second file was in play. _SERVICE_ENV (via
    # forge.paths.voice_config_dir()) is the ONE canonical file.
    result: dict[str, str] = {}
    if _SERVICE_ENV.exists():
        try:
            for line in _SERVICE_ENV.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
        except Exception:
            pass
    return result


def _write_env_key(key: str, value: str) -> None:
    """Update or add a single key in service.env."""
    if not _SERVICE_ENV.exists():
        _SERVICE_ENV.parent.mkdir(parents=True, exist_ok=True)
        # Atomic create with mode 0600 from the start — avoids TOCTOU between
        # write_text and chmod where another local user could read API keys.
        try:
            fd = os.open(_SERVICE_ENV, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with open(fd, "w", encoding="utf-8") as _fh:
                _fh.write(f"{key}={value}\n")
        except FileExistsError:
            pass  # concurrent creation — fall through to update path
        return

    lines = _SERVICE_ENV.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    new_lines = []
    replaced = False
    for line in lines:
        if pattern.match(line):
            new_lines.append(f"{key}={value}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"{key}={value}")

    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(_SERVICE_ENV.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(new_lines) + "\n")
        os.replace(tmp, _SERVICE_ENV)
        os.chmod(_SERVICE_ENV, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("/setup/bridge/{channel}")
def bridge_setup_info(
    channel: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    guide = _BRIDGE_GUIDES.get(channel)
    if not guide:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"unknown channel: {channel!r}")

    # Read current token from bridge settings
    settings_path = _forge_paths.corvin_home() / "bridges" / channel / "settings.json"
    current_token = ""
    configured = False
    if settings_path.exists():
        import json
        try:
            d = json.loads(settings_path.read_text(encoding="utf-8"))
            token_key = guide.get("token_key")
            if token_key:
                val = d.get(token_key, "")
                configured = bool(val)
                current_token = ("****" + val[-4:]) if val and len(val) > 4 else ("set" if val else "")
        except Exception:
            pass

    # WhatsApp: check if QR server is up by probing /qr.png directly.
    # Probing "/" is unreliable — some daemons return 404 on root but still serve /qr.png.
    qr_available = False
    if channel == "whatsapp":
        try:
            # Must be GET, not HEAD: the daemon's /qr.png handler is GET-only, so a
            # HEAD probe ALWAYS 404s even when a QR is available — which left the
            # console stuck on "Waiting for QR…" forever while the daemon had one.
            with urllib.request.urlopen("http://127.0.0.1:7891/qr.png", timeout=2) as resp:
                qr_available = getattr(resp, "status", 200) == 200
        except Exception:
            pass
        # WhatsApp has no token — "configured" means a Baileys session exists
        # (the device is/was linked). Without this, configured stays False and
        # the UI shows a "Start bridge / scan QR" prompt even when WhatsApp is
        # already connected and no QR will ever appear ("kein QR-Code").
        try:
            bm = _import_bridge_manager()
            if bm is not None:
                configured = bool(bm.channel_configured("whatsapp"))
            else:
                configured = any(
                    (base / "auth" / "creds.json").exists()
                    for base in (
                        _forge_paths.corvin_home() / "bridges" / "whatsapp",
                        _REPO / "operator" / "bridges" / "whatsapp",
                    )
                )
        except Exception:
            pass

    return {
        "channel": channel,
        "guide": guide,
        "configured": configured,
        "current_token_masked": current_token,
        "qr_available": qr_available,
        "qr_url": "/v1/console/setup/whatsapp/qr.png" if qr_available else None,
    }


@router.get("/setup/whatsapp/qr.png")
def whatsapp_qr_proxy(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Response:
    """Proxy the WhatsApp daemon's QR PNG from localhost:7891."""
    try:
        req = urllib.request.Request("http://127.0.0.1:7891/qr.png")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
        return Response(content=data, media_type="image/png")
    except Exception as exc:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"WhatsApp daemon not running or QR not available: {exc}") from exc


# ── WhatsApp bridge: one-click start (async start → poll) ─────────────────────
# The WhatsApp daemon (Baileys/Node.js) must be RUNNING to emit the pairing QR,
# and on a fresh box that means installing Node.js + npm deps first — minutes of
# work that must not block the request. Mirror the Hermes bootstrap pattern: a
# daemon thread does the work, the SPA polls the status, and the existing
# /qr.png probe lights up the QR once Baileys emits it.
_WA_START_LOCK = threading.Lock()
_WA_START_STATE: dict[str, Any] = {"state": "idle"}  # idle|running|done|error


def _node_install_steps() -> dict[str, Any]:
    """Per-OS manual Node.js install guidance (shown when auto-install fails)."""
    if sys.platform == "win32":
        return {"platform": "windows", "download_url": "https://nodejs.org/en/download",
                "steps": ["Download the Node.js LTS installer (.msi) from nodejs.org/en/download.",
                          "Run it (accept defaults — this adds node to PATH).",
                          "Reopen this page and click Start WhatsApp bridge again."]}
    if sys.platform == "darwin":
        return {"platform": "macos", "download_url": "https://nodejs.org/en/download",
                "steps": ["Install Node.js LTS: `brew install node` (or the installer from nodejs.org).",
                          "Click Start WhatsApp bridge again."]}
    return {"platform": "linux", "download_url": "https://nodejs.org/en/download",
            "steps": ["Install Node.js 20+: e.g. `sudo apt install nodejs npm` or use nvm.",
                      "Click Start WhatsApp bridge again."]}


def _import_bridge_manager():
    """Resolve bridge_manager from repo (source) or the wheel _vendor tree."""
    try:
        import bridge_manager  # type: ignore[import]
        return bridge_manager
    except ImportError:
        pass
    for cand in (_REPO / "operator" / "bridges",
                 _THIS_DIR.parent / "_vendor" / "operator" / "bridges"):
        if (cand / "bridge_manager.py").is_file():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            try:
                import bridge_manager  # type: ignore[import]
                return bridge_manager
            except ImportError:
                continue
    return None


def _run_wa_start_job() -> None:
    """Worker thread: install Node/deps as needed + start the WhatsApp daemon."""
    def _set_phase(msg: str) -> None:
        with _WA_START_LOCK:
            _WA_START_STATE["phase"] = msg

    try:
        bm = _import_bridge_manager()
        if bm is None:
            with _WA_START_LOCK:
                _WA_START_STATE.update({"state": "error",
                                        "result": {"error": "bridge manager not available"}})
            return
        result = bm.start_channel_detached("whatsapp", progress=_set_phase)
        if result.get("node_missing"):
            result["node_steps"] = _node_install_steps()
        with _WA_START_LOCK:
            _WA_START_STATE.update({"state": "done" if result.get("ok") else "error",
                                    "result": result})
    except Exception as exc:  # noqa: BLE001
        with _WA_START_LOCK:
            _WA_START_STATE.update({"state": "error",
                                    "result": {"error": f"Unexpected error: {exc}"}})


@router.post("/setup/whatsapp/start")
def whatsapp_start(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> dict:
    """Start the WhatsApp bridge daemon (installs Node.js + deps on demand).

    Returns immediately with {state:"running"}; the daemon comes up in the
    background and its QR is served via /setup/whatsapp/qr.png. Poll
    /setup/whatsapp/start/status for progress. A second call while running is a
    no-op returning the in-flight state.
    """
    with _WA_START_LOCK:
        if _WA_START_STATE.get("state") == "running":
            return dict(_WA_START_STATE)
        _WA_START_STATE.clear()
        _WA_START_STATE.update({"state": "running", "phase": "Starting…"})

    try:
        console_audit.action_performed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="bridge_start_requested", target_kind="bridge", target_id="whatsapp",
        )
    except Exception:  # noqa: BLE001
        pass

    threading.Thread(target=_run_wa_start_job, daemon=True).start()
    with _WA_START_LOCK:
        return dict(_WA_START_STATE)


@router.get("/setup/whatsapp/start/status")
def whatsapp_start_status(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """Return the WhatsApp-bridge start job state for the SPA to poll."""
    with _WA_START_LOCK:
        return dict(_WA_START_STATE)


@router.get("/setup/engines")
def list_engines(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    env = _read_service_env()

    # Check Claude Code login status
    claude_ok = False
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, timeout=5)
        claude_ok = r.returncode == 0
    except Exception:
        pass

    # Check GitHub Copilot CLI binary availability (ADR-0071)
    copilot_ok = False
    copilot_version: str | None = None
    try:
        _copilot_bin = os.environ.get("CORVIN_COPILOT_BIN", "copilot") or "copilot"
        r = subprocess.run([_copilot_bin, "--version"], capture_output=True, timeout=5)
        if r.returncode == 0:
            copilot_ok = True
            # First line of stdout, stripped — e.g. "GitHub Copilot CLI 1.0.56."
            raw_ver = (r.stdout or b"").decode("utf-8", errors="replace").strip().split("\n")[0]
            copilot_version = raw_ver[:80] if raw_ver else None
    except Exception:
        pass

    engines = []
    for e in _ENGINE_KEYS:
        key_name = e.get("key")
        if e["kind"] == "oauth":
            engines.append({**e, "configured": claude_ok, "value_masked": "OAuth session" if claude_ok else None})
        elif e["id"] == "copilot":
            # Binary-checked; no API key stored in service env.
            engines.append({**e, "configured": copilot_ok,
                            "value_masked": copilot_version if copilot_ok else None})
        elif key_name:
            val = env.get(key_name, "") or os.environ.get(key_name, "")
            masked = ("****" + val[-4:]) if val and len(val) > 8 else ("set" if val else None)
            engines.append({**e, "configured": bool(val), "value_masked": masked})
        else:
            engines.append({**e, "configured": False, "value_masked": None})

    return {"engines": engines, "env_path": str(_SERVICE_ENV)}


# ── Setup status + completion ──────────────────────────────────────────────

@router.get("/setup/status")
def setup_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return first-run status for the SetupGate overlay."""
    env = _read_service_env()
    anthropic_key = env.get("ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key = env.get("OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    engine_connected = bool(anthropic_key or openai_key)

    # Lightweight claude CLI probe (no spawn, just check executable exists)
    claude_ok = False
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, timeout=5)
        claude_ok = r.returncode == 0
    except Exception:
        pass
    if claude_ok:
        engine_connected = True

    # A no-API-key Hermes (local Ollama) user has no anthropic/openai key and may
    # have no claude CLI, yet a working local engine. Treat the engine as
    # connected when the configured/default engine is hermes AND Ollama is
    # reachable — reusing the SAME probe /setup/test-engine uses so the final
    # screen agrees with the engine Test button. Accurate: do NOT claim connected
    # if Ollama is down.
    ollama_reachable = False
    if not engine_connected and _default_engine(rec.tenant_id) == "hermes":
        try:
            from .engine import _probe_ollama  # local import: avoid route-load cycle
            ollama_reachable = bool(_probe_ollama().get("ollama_reachable"))
        except Exception:
            ollama_reachable = False
        if ollama_reachable:
            engine_connected = True

    bridges_path = _forge_paths.corvin_home() / "bridges"
    configured_bridges: list[str] = []
    for channel, guide in _BRIDGE_GUIDES.items():
        settings_path = bridges_path / channel / "settings.json"
        if not settings_path.exists():
            continue
        try:
            import json as _json
            d = _json.loads(settings_path.read_text(encoding="utf-8"))
            token_key = guide.get("token_key")
            if channel == "whatsapp":
                configured_bridges.append(channel)
            elif token_key and d.get(token_key):
                configured_bridges.append(channel)
        except Exception:
            pass

    # ADR-0120: check both legacy flag and structured onboarding.json
    setup_complete = _SETUP_COMPLETE_PATH.exists() or _onboarding_complete()
    return {
        "first_run": not setup_complete,
        "engine_connected": engine_connected,
        "claude_cli_ok": claude_ok,
        "anthropic_key_set": bool(anthropic_key),
        "bridges_configured": configured_bridges,
        "setup_complete": setup_complete,
    }


@router.post("/setup/complete")
def mark_setup_complete(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Mark first-run setup as done (idempotent). ADR-0120: also writes onboarding.json."""
    try:
        _SETUP_COMPLETE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SETUP_COMPLETE_PATH.touch()
        # ADR-0120: structured state file for engine_detector / CLI / M4 guard
        _ONBOARDING_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        import datetime as _dt
        state = {
            "complete": True,
            "completed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        _ONBOARDING_JSON_PATH.write_text(_json.dumps(state, indent=2))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Setup completion failed", exc_info=True)
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "Setup completion failed — please try again") from exc
    try:
        console_audit._emit(
            "setup.onboarding_complete",
            tenant_id=rec.tenant_id,
            details={"default_engine": "configured", "engine_count": 0},
        )
    except Exception:
        pass
    return {"ok": True}


# ── ADR-0120 M1: Engine auto-detection ────────────────────────────────────

@router.get("/setup/onboarding/detect")
def detect_engines(
    request: Request,
    session: Annotated[session_auth.SessionRecord | None, Depends(_optional_session)],
) -> dict[str, Any]:
    """Probe installed engine binaries — ADR-0120 M1.

    Auth: session required normally. Loopback-only exemption applies when
    onboarding has not been completed yet (onboarding.json absent / complete=false).
    This window closes automatically after POST /setup/complete.

    Returns: {engines: [EngineProbe], onboarding_complete: bool}
    """
    loopback = request.client is not None and request.client.host in (
        "127.0.0.1", "::1", "localhost",
    )
    complete = _onboarding_complete()

    if session is None and not (loopback and not complete):
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "no session")

    # Lazy import to keep boot time fast and satisfy CI no-anthropic lint
    if str(_SHARED) not in sys.path:
        sys.path.insert(0, str(_SHARED))
    try:
        import engine_detector as _ed  # noqa: PLC0415
    except ImportError as exc:
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            "engine_detector module unavailable",
        ) from exc

    probes = _ed.detect_all()
    found_count = sum(1 for p in probes if p.found)

    try:
        console_audit._emit(
            "setup.engine_probe_run",
            tenant_id=session.tenant_id if session else "_default",
            details={
                "found_count": found_count,
                "engine_ids": [p.engine_id for p in probes],
            },
        )
    except Exception:
        pass

    return {
        "engines": [p.to_dict() for p in probes],
        "onboarding_complete": complete,
    }


@router.get("/setup/commands")
def list_all_commands(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return all available slash commands organized by category.

    All commands are globally available across all messaging channels.
    No channel-specific filtering — every bridge supports every command.
    """
    return {
        "categories": _GLOBAL_COMMANDS,
        "tip": "Type `/help` in any chat for quick command reference",
    }


def _ollama_install_steps() -> dict:
    """OS-specific, numbered step-by-step Ollama install guidance for the Setup
    UI. The console runs on localhost, so sys.platform IS the user's OS."""
    if sys.platform == "win32":
        return {
            "platform": "windows",
            "download_url": "https://ollama.com/download/windows",
            "steps": [
                "Download the Ollama installer for Windows: https://ollama.com/download/windows",
                "Run the downloaded OllamaSetup.exe and finish the install — Ollama then runs automatically in the system tray.",
                "Back here, click “Set up Hermes automatically” — CorvinOS pulls the model and configures Hermes for you.",
                "Click “Test” — it turns green.",
            ],
        }
    if sys.platform == "darwin":
        return {
            "platform": "macos",
            "download_url": "https://ollama.com/download/mac",
            "steps": [
                "Download Ollama for macOS: https://ollama.com/download/mac  (or run: brew install ollama)",
                "Open the Ollama app — it starts the local server (menu-bar icon).",
                "Back here, click “Set up Hermes automatically” to pull the model and configure Hermes.",
                "Click “Test” — it turns green.",
            ],
        }
    return {
        "platform": "linux",
        "download_url": "https://ollama.com/download/linux",
        "steps": [
            "Open a terminal.",
            "Install Ollama:  curl -fsSL https://ollama.com/install.sh | sh",
            "The installer starts the service automatically (if not, run:  ollama serve).",
            "Back here, click “Set up Hermes automatically” to pull the model and configure Hermes.",
            "Click “Test” — it turns green.",
        ],
    }


class EngineTestRequest(BaseModel):
    engine_id: str = Field(..., max_length=32)
    model_config = {"extra": "forbid"}


@router.post("/setup/test-engine")
def test_engine(
    body: EngineTestRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Quick connectivity test for an engine (triggers subprocess/network — CSRF required)."""
    if body.engine_id == "claude_code":
        import shutil as _shutil
        # Resolve via PATH (same as the engine-detection badge). shutil.which
        # honours Windows PATHEXT, so it finds the npm `claude.cmd` shim that a
        # bare subprocess.run(["claude", ...]) could NOT launch — that mismatch
        # made the card say "installed" while this test said "not found".
        claude_bin = _shutil.which("claude")
        if claude_bin is None:
            return {"ok": False, "detail": "claude CLI not found — install Claude Code first"}
        # Windows cannot CreateProcess a .cmd/.bat shim directly — run it via the
        # command processor (list-form, not shell=True → no injection). Shared
        # with engine_detector's onboarding-badge probe so both agree.
        if str(_SHARED) not in sys.path:
            sys.path.insert(0, str(_SHARED))
        from engine_detector import windows_wrap as _windows_wrap  # noqa: PLC0415
        cmd = _windows_wrap([claude_bin, "--version"])
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=8)
            if r.returncode == 0:
                ver = r.stdout.decode("utf-8", errors="replace").strip().split("\n")[0]
                return {"ok": True, "detail": ver}
            return {"ok": False, "detail": "claude --version returned non-zero"}
        except FileNotFoundError:
            return {"ok": False, "detail": "claude CLI not found — install Claude Code first"}
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "detail": (
                    "claude --version timed out after 8s — often antivirus scanning "
                    "a freshly spawned shell; try running `claude --version` in a "
                    "terminal to confirm it responds"
                ),
            }
        except Exception:
            import logging
            logging.getLogger(__name__).error("Claude CLI test failed", exc_info=True)
            return {"ok": False, "detail": "Claude CLI test failed"}

    if body.engine_id == "anthropic":
        env = _read_service_env()
        key = env.get("ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return {"ok": False, "detail": "ANTHROPIC_API_KEY not set"}
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    return {"ok": True, "detail": "API key valid"}
            return {"ok": False, "detail": f"HTTP {resp.status}"}
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Anthropic API test failed", exc_info=True)
            return {"ok": False, "detail": "API connectivity test failed"}

    if body.engine_id == "hermes":
        # Hermes is the local Ollama-backed engine. Reuse the canonical
        # Ollama reachability probe used by GET /settings/engine/health so
        # the Setup "Test" button and the Engines page agree.
        from .engine import _probe_ollama  # local import: avoid route-load cycle

        health = _probe_ollama()
        if not health.get("ollama_reachable"):
            # Self-heal the common "installed but server stopped" case: start
            # `ollama serve` in the background and re-probe, so clicking Test is
            # enough to bring Hermes up (no manual `ollama serve` needed).
            try:
                from hermes_bootstrap import ensure_ollama_running  # type: ignore[import]
            except ImportError:
                try:
                    from corvin_console.hermes_bootstrap import ensure_ollama_running  # type: ignore[import]
                except ImportError:
                    ensure_ollama_running = None  # type: ignore[assignment]
            if ensure_ollama_running is not None and ensure_ollama_running(timeout=20.0):
                health = _probe_ollama()

        if health.get("ollama_reachable"):
            count = health.get("model_count", 0)
            if count == 0:
                return {
                    "ok": False,
                    "detail": "Ollama is running, but no model is installed yet.",
                    "platform": _ollama_install_steps()["platform"],
                    "steps": [
                        "Click “Set up Hermes automatically” below — CorvinOS pulls the default model (qwen3:8b) and configures Hermes.",
                        "Or, in a terminal, run:  ollama pull qwen3:8b",
                        "Then click “Test” — it turns green.",
                    ],
                }
            return {"ok": True, "detail": f"Ollama reachable — {count} model(s) installed"}
        guide = _ollama_install_steps()
        return {
            "ok": False,
            "detail": "Ollama is not installed / not reachable. Follow these steps:",
            "platform": guide["platform"],
            "download_url": guide["download_url"],
            "steps": guide["steps"],
        }

    # Engines without a cheap, non-destructive connectivity probe. Return an
    # informational ok-style result rather than a red error, so the Setup UI
    # does not present a benign "no probe available" as a failure.
    return {
        "ok": True,
        "detail": f"No connectivity test available for {body.engine_id!r} — configure and verify it in a chat.",
    }


# ── Welcome check: first-boot spoken onboarding self-check (Concept 2) ─────
#
# docs/first-run-language-and-voice-onboarding.md §2. Runs the health-check
# building blocks that already exist elsewhere in this codebase — the L44
# classifier boot probe, Hermes warm-up, the REAL (non-mocked) STT/TTS
# round-trip voice_doctor.py implements, and the existing engine
# connectivity probe (test_engine, above) — behind ONE endpoint the
# WelcomeStep screen calls once on mount. Runs as a background job (same
# async-job/poll pattern as the WhatsApp bridge start above) because a
# Hermes warm-up or a cold STT model load can take tens of seconds — too
# slow to hold a synchronous HTTP request open. Per the concept doc's
# dialectical pass: this NEVER blocks onboarding, it only changes the
# greeting's wording — "Let's go" always works regardless of outcome.

_WELCOME_CHECK_LOCK = threading.Lock()
# ADR-0007: keyed by tenant_id — a single shared dict here would let one
# tenant's in-flight/finished check clobber or leak into another's poll.
_WELCOME_CHECK_STATE: dict[str, dict[str, Any]] = {}
_WELCOME_STT_TIMEOUT_S = 45.0


def _welcome_state_for(tenant_id: str) -> dict[str, Any]:
    return _WELCOME_CHECK_STATE.setdefault(tenant_id, {"state": "idle"})


def _welcome_check_lang() -> str:
    """Resolve the greeting language: profile.display_language (seeded at
    install time per Concept 1, or set later via /lang set) -> 'en'."""
    try:
        if str(_SHARED) not in sys.path:
            sys.path.insert(0, str(_SHARED))
        import i18n as _i18n  # noqa: PLC0415
        import profile as _profile  # noqa: PLC0415
        return _i18n.resolve(_profile.get("display_language"), default="en")
    except Exception:
        return "en"


def _welcome_check_component(status: str, detail: str) -> dict[str, str]:
    return {"status": status, "detail": (detail or "")[:300]}


def _welcome_check_house_rules() -> dict[str, str]:
    # (a) L44 classifier boot probe — a logging-only API; capture whatever
    # it would have logged to turn it into a structured status.
    try:
        if str(_SHARED) not in sys.path:
            sys.path.insert(0, str(_SHARED))
        import house_rules as _house_rules  # noqa: PLC0415
        messages: list[str] = []
        _house_rules.house_rules_boot_health_check(log_fn=messages.append)
        return (
            _welcome_check_component("ok", "") if not messages
            else _welcome_check_component("degraded", messages[-1])
        )
    except Exception as exc:  # noqa: BLE001
        return _welcome_check_component("unavailable", str(exc))


def _welcome_check_hermes(engine_id: str) -> dict[str, str] | None:
    # (b) Hermes warm-up — only when hermes is the configured/fallback engine.
    if engine_id != "hermes":
        return None
    try:
        from agents.hermes_engine import ensure_hermes_ready  # noqa: PLC0415
        base_url = os.environ.get("CORVIN_HERMES_URL", "http://localhost:11434")
        model = os.environ.get("CORVIN_HERMES_MODEL", "").strip() or "qwen3:8b"
        ok, detail = ensure_hermes_ready(base_url, model, timeout=60.0)
        return _welcome_check_component("ok" if ok else "degraded", detail)
    except Exception as exc:  # noqa: BLE001
        return _welcome_check_component("unavailable", str(exc))


def _welcome_check_stt_tts() -> tuple[dict[str, str], dict[str, str]]:
    # (c) STT + TTS round-trip — voice_doctor.py's real, non-mocked checks
    # (call its functions directly, not the CLI).
    try:
        voice_scripts = _REPO / "operator" / "voice" / "scripts"
        if str(voice_scripts) not in sys.path:
            sys.path.insert(0, str(voice_scripts))
        import voice_doctor as _vd  # noqa: PLC0415
        stt_ok, stt_detail = _vd._check_stt(_WELCOME_STT_TIMEOUT_S)
        tts_ok, tts_detail, _tts_path = _vd._check_tts(_vd._DOCTOR_TTS_TEXT)
        return (
            _welcome_check_component("ok" if stt_ok else "degraded", stt_detail),
            _welcome_check_component("ok" if tts_ok else "degraded", tts_detail),
        )
    except Exception as exc:  # noqa: BLE001
        unavailable = _welcome_check_component("unavailable", str(exc))
        return unavailable, unavailable


def _welcome_check_engine(engine_id: str, rec: session_auth.SessionRecord) -> dict[str, str]:
    # (d) cheap connectivity/auth test turn against the primary engine — the
    # closest thing to a "warm-up" a cloud API has. Reuses the SAME probe
    # the Setup "Test" button uses (test_engine, above), not a second
    # hand-rolled implementation.
    try:
        result = test_engine(EngineTestRequest(engine_id=engine_id), rec)
        return _welcome_check_component(
            "ok" if result.get("ok") else "degraded", str(result.get("detail", "")),
        )
    except Exception as exc:  # noqa: BLE001
        return _welcome_check_component("unavailable", str(exc))


def _run_welcome_check_job(engine_id: str, rec: session_auth.SessionRecord) -> None:
    lang = _welcome_check_lang()

    # The four checks below are mutually independent (each only fills its own
    # `components[...]` slot) but individually slow — a cold Hermes warm-up
    # (up to 60s) and a cold STT model load (up to 45s) used to run back to
    # back, serializing to 100s+ against the frontend's much shorter poll
    # budget and silently losing the spoken greeting. Running them concurrently
    # bounds the wall-clock time to the SLOWEST single check instead of their
    # sum.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        house_rules_fut = pool.submit(_welcome_check_house_rules)
        hermes_fut = pool.submit(_welcome_check_hermes, engine_id)
        stt_tts_fut = pool.submit(_welcome_check_stt_tts)
        engine_fut = pool.submit(_welcome_check_engine, engine_id, rec)

        components: dict[str, dict[str, str]] = {"house_rules": house_rules_fut.result()}
        hermes_result = hermes_fut.result()
        if hermes_result is not None:
            components["hermes"] = hermes_result
        components["stt"], components["tts"] = stt_tts_fut.result()
        components["engine"] = engine_fut.result()

    greeting = _build_welcome_greeting(lang, components)

    with _WELCOME_CHECK_LOCK:
        _welcome_state_for(rec.tenant_id).update({
            "state": "done",
            "lang": lang,
            "components": components,
            "greeting": greeting,
        })


def _build_welcome_greeting(lang: str, components: dict[str, dict[str, str]]) -> str:
    """Assemble the spoken/written greeting from i18n `welcome.*` strings.

    Reflects the ACTUAL check outcome (never a canned line regardless of
    result) — a degraded component swaps in the matching "bad" fragment
    instead of silently claiming everything is healthy. Always includes a
    "voice to action" framing clause (you speak, Corvin acts — computer,
    browser, internet access) plus the capabilities/actions clause, so the
    user hears both the underlying idea and concrete examples, not just a
    health report."""
    if str(_SHARED) not in sys.path:
        sys.path.insert(0, str(_SHARED))
    import i18n as _i18n  # noqa: PLC0415

    def tr(key: str) -> str:
        return _i18n.t(f"welcome.{key}", lang)

    stt_ok = components.get("stt", {}).get("status") == "ok"
    tts_ok = components.get("tts", {}).get("status") == "ok"
    engine_ok = components.get("engine", {}).get("status") == "ok"

    parts = [
        tr("intro"),
        tr("checks_intro"),
        tr("check_stt_ok") if stt_ok else tr("check_stt_bad"),
        tr("check_tts_ok") if tts_ok else tr("check_tts_bad"),
        tr("check_engine_ok") if engine_ok else tr("check_engine_bad"),
        tr("control"),
        tr("voice_to_action"),
        tr("capabilities"),
        tr("closing"),
    ]
    return " ".join(p for p in parts if p)


@router.post("/setup/welcome-check")
def start_welcome_check(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Kick off the first-boot self-check + greeting build as a background
    job (same async-job/poll shape as /setup/whatsapp/start above — a
    Hermes warm-up or cold STT model load can take tens of seconds).
    Idempotent: a second call while one is already running returns the
    in-flight state instead of starting a duplicate. Poll via
    GET /setup/welcome-check/status."""
    with _WELCOME_CHECK_LOCK:
        state = _welcome_state_for(rec.tenant_id)
        if state.get("state") == "running":
            return dict(state)
        state.clear()
        state.update({"state": "running"})

    # Pass through the tenant's actual configured engine (hermes, opencode,
    # anthropic, ...) instead of collapsing anything non-hermes to
    # "claude_code" — test_engine() already has a dedicated branch per known
    # engine plus a benign generic fallback for the rest, so forcing
    # "claude_code" only mislabeled e.g. an opencode-only install as "claude
    # CLI not found".
    engine_id = _default_engine(rec.tenant_id) or "claude_code"
    threading.Thread(
        target=_run_welcome_check_job, args=(engine_id, rec), daemon=True,
    ).start()
    with _WELCOME_CHECK_LOCK:
        return dict(_welcome_state_for(rec.tenant_id))


@router.get("/setup/welcome-check/status")
def welcome_check_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Poll target for the SPA: {state: "running"} or {state: "done",
    lang, components, greeting}."""
    with _WELCOME_CHECK_LOCK:
        return dict(_welcome_state_for(rec.tenant_id))


class EngineKeyUpdate(BaseModel):
    value: str = Field(..., max_length=500)
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}


@router.put("/setup/engines/{engine_id}")
def update_engine_key(
    engine_id: str,
    body: EngineKeyUpdate,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    engine = next((e for e in _ENGINE_KEYS if e["id"] == engine_id), None)
    if not engine or not engine.get("key"):
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"engine {engine_id!r} not configurable via API")

    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="engine.key_update",
            target_kind="engine",
            target_id=engine_id,
            reason="reauth-failed",
        )
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "re-auth failed")

    try:
        _write_env_key(engine["key"], body.value)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Engine key update failed", exc_info=True)
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "Engine key update failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="engine.key_update",
        target_kind="engine",
        target_id=engine_id,
    )
    return {"ok": True, "engine_id": engine_id, "key": engine["key"]}
