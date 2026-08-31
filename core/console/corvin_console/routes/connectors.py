"""Connectors — MCP tool registry for workflow nodes (ADR-0039 Phase 8).

Connectors describe external services (Gmail, GitHub, Brave, etc.) that
workflow nodes can invoke. Two kinds:

  session_mcp  — Provided by Claude Code's OAuth session (Gmail, Drive,
                 Calendar, Spotify). Always available when claude -p is
                 called with any --mcp-config file. No API key needed.

  api_key_mcp  — Standard MCP servers that need an API key from the vault.
                 The key name is declared in the catalog; actual value lives
                 in <corvin_home>/config/vault.json (mode 0600).

Storage:
  <tenant_home>/global/connectors.json   — per-tenant enabled set + overrides

Routes:
  GET  /connectors              list all connectors with status
  PUT  /connectors/{cid}        enable/disable, store api_key into vault
  GET  /connectors/mcp-config   generate --mcp-config JSON for a list of IDs
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from ..utils import atomic_write_json
from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths
_REPO = _bootstrap._REPO

router = APIRouter()

# ── Connector catalog ─────────────────────────────────────────────────────

CONNECTOR_CATALOG: list[dict[str, Any]] = [
    # ── Session MCP (Claude Code OAuth — always available) ────────────
    {
        "id": "gmail",
        "name": "Gmail",
        "category": "Communication",
        "kind": "session_mcp",
        "icon": "Mail",
        "description": "Read, search, and send Gmail emails.",
        "capabilities": ["search_threads", "get_thread", "create_draft", "send"],
        "mcp_server": "claude_ai_Gmail",
        "tool_prefix": "mcp__claude_ai_Gmail__",
        "example_instruction": "Search my inbox for unread emails from today and list them.",
    },
    {
        "id": "gdrive",
        "name": "Google Drive",
        "category": "Storage",
        "kind": "session_mcp",
        "icon": "HardDrive",
        "description": "Read, search, and create files in Google Drive.",
        "capabilities": ["search_files", "read_file", "create_file", "list_recent"],
        "mcp_server": "claude_ai_Google_Drive",
        "tool_prefix": "mcp__claude_ai_Google_Drive__",
        "example_instruction": "Find all spreadsheets modified in the last week.",
    },
    {
        "id": "gcalendar",
        "name": "Google Calendar",
        "category": "Productivity",
        "kind": "session_mcp",
        "icon": "CalendarDays",
        "description": "List, create, and update calendar events.",
        "capabilities": ["list_events", "create_event", "delete_event", "suggest_time"],
        "mcp_server": "claude_ai_Google_Calendar",
        "tool_prefix": "mcp__claude_ai_Google_Calendar__",
        "example_instruction": "List my meetings for tomorrow and summarise them.",
    },
    {
        "id": "spotify",
        "name": "Spotify",
        "category": "Media",
        "kind": "session_mcp",
        "icon": "Music",
        "description": "Search tracks, manage playlists, control playback.",
        "capabilities": ["search", "get_currently_playing", "create_playlist", "fetch_tracks"],
        "mcp_server": "claude_ai_Spotify",
        "tool_prefix": "mcp__claude_ai_Spotify__",
        "example_instruction": "Search for jazz playlists and add the top result to my library.",
    },
    # ── API-key MCP ────────────────────────────────────────────────────
    {
        "id": "github",
        "name": "GitHub",
        "category": "Development",
        "kind": "api_key_mcp",
        "icon": "GitBranch",
        "description": "Interact with GitHub repos, issues, and pull requests.",
        "capabilities": ["search_code", "list_issues", "create_issue", "list_prs"],
        "api_key_env": "GITHUB_PERSONAL_ACCESS_TOKEN",
        "api_key_label": "Personal Access Token",
        "mcp_config": {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}"},
        },
        "example_instruction": "List open issues labelled 'bug' in the repository.",
    },
    {
        "id": "brave",
        "name": "Brave Search",
        "category": "Search",
        "kind": "api_key_mcp",
        "icon": "Search",
        "description": "Web and news search via Brave Search API.",
        "capabilities": ["web_search", "news_search"],
        "api_key_env": "BRAVE_API_KEY",
        "api_key_label": "Brave API Key",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
            "env": {"BRAVE_API_KEY": "${BRAVE_API_KEY}"},
        },
        "example_instruction": "Search for the latest news about EU AI Act enforcement.",
    },
    {
        "id": "notion",
        "name": "Notion",
        "category": "Productivity",
        "kind": "api_key_mcp",
        "icon": "FileText",
        "description": "Read and write Notion pages and databases.",
        "capabilities": ["search", "read_page", "create_page", "update_page"],
        "api_key_env": "NOTION_TOKEN",
        "api_key_label": "Notion Integration Token",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@notionhq/notion-mcp-server"],
            "env": {"OPENAI_API_KEY": "${OPENAI_API_KEY}", "notion_api_key": "${NOTION_TOKEN}"},
        },
        "example_instruction": "Search Notion for pages about project planning.",
    },
    {
        "id": "slack",
        "name": "Slack",
        "category": "Communication",
        "kind": "api_key_mcp",
        "icon": "MessageSquare",
        "description": "Post messages and read channels in Slack.",
        "capabilities": ["post_message", "list_channels", "search_messages"],
        "api_key_env": "SLACK_BOT_TOKEN",
        "api_key_label": "Slack Bot Token",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-slack"],
            "env": {"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}", "SLACK_TEAM_ID": "${SLACK_TEAM_ID}"},
        },
        "example_instruction": "Post a daily summary to the #briefings Slack channel.",
    },
    {
        "id": "filesystem",
        "name": "Filesystem",
        "category": "Utilities",
        "kind": "api_key_mcp",
        "icon": "FolderOpen",
        "description": "Read and write local files (sandbox path configurable).",
        "capabilities": ["read_file", "write_file", "list_directory"],
        "api_key_env": None,
        "api_key_label": None,
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "${FILESYSTEM_ROOT}"],
        },
        "config_extra": {"FILESYSTEM_ROOT": {"label": "Allowed root path", "default": "/tmp"}},
        "example_instruction": "Write the summary to /tmp/daily_report.md",
    },
    {
        "id": "memory",
        "name": "Memory",
        "category": "Utilities",
        "kind": "api_key_mcp",
        "icon": "Brain",
        "description": "Persistent key-value memory across workflow runs.",
        "capabilities": ["remember", "recall", "forget"],
        "api_key_env": None,
        "api_key_label": None,
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"],
        },
        "example_instruction": "Remember the result of this analysis for tomorrow's run.",
    },
]

# Quick lookup
_CATALOG_BY_ID: dict[str, dict[str, Any]] = {c["id"]: c for c in CONNECTOR_CATALOG}


# ── Storage helpers ────────────────────────────────────────────────────────

def _connectors_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "connectors.json"


def _vault_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "connector_vault.json"


def _read_enabled(tenant_id: str) -> dict[str, Any]:
    p = _connectors_path(tenant_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_vault(tenant_id: str) -> dict[str, str]:
    p = _vault_path(tenant_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_atomic(path: Path, data: dict) -> None:
    atomic_write_json(path, data)


def _connector_status(cid: str, enabled_cfg: dict, vault: dict) -> str:
    entry = _CATALOG_BY_ID.get(cid, {})
    if not enabled_cfg.get(cid, {}).get("enabled", False):
        return "disabled"
    if entry.get("kind") == "session_mcp":
        # Always available via Claude Code session
        return "connected"
    key_env = entry.get("api_key_env")
    if key_env:
        # Check vault or environment
        if vault.get(key_env) or os.environ.get(key_env):
            return "connected"
        return "needs_key"
    # No key required (filesystem, memory)
    return "connected"


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("/connectors")
def list_connectors(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    enabled_cfg = _read_enabled(rec.tenant_id)
    vault = _read_vault(rec.tenant_id)
    items = []
    for c in CONNECTOR_CATALOG:
        cid = c["id"]
        cfg = enabled_cfg.get(cid, {})
        status = _connector_status(cid, enabled_cfg, vault)
        items.append({
            **{k: c[k] for k in ("id","name","category","kind","icon","description","capabilities","example_instruction")},
            "enabled": cfg.get("enabled", False),
            "status": status,
            "api_key_label": c.get("api_key_label"),
            "api_key_set": bool(vault.get(c.get("api_key_env","")) or os.environ.get(c.get("api_key_env","") or "")),
            "config_extra": c.get("config_extra", {}),
            "extra_values": {k: cfg.get("extra", {}).get(k, v.get("default","")) for k, v in c.get("config_extra", {}).items()},
        })
    return {
        "tenant_id": rec.tenant_id,
        "count": len(items),
        "connectors": items,
        "connected_ids": [i["id"] for i in items if i["status"] == "connected"],
    }


class ConnectorUpdateRequest(BaseModel):
    enabled: bool = True
    api_key: str | None = Field(None, max_length=2000)
    extra: dict[str, str] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}


@router.put("/connectors/{cid}")
def update_connector(
    cid: str,
    body: ConnectorUpdateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if cid not in _CATALOG_BY_ID:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"unknown connector: {cid!r}")

    entry = _CATALOG_BY_ID[cid]
    enabled_cfg = _read_enabled(rec.tenant_id)
    vault = _read_vault(rec.tenant_id)

    enabled_cfg[cid] = {"enabled": body.enabled, "extra": body.extra}
    _write_atomic(_connectors_path(rec.tenant_id), enabled_cfg)

    if body.api_key and entry.get("api_key_env"):
        vault[entry["api_key_env"]] = body.api_key
        _write_atomic(_vault_path(rec.tenant_id), vault)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="connector.updated",
        target_kind="connector",
        target_id=cid,
    )
    return {"ok": True, "id": cid, "enabled": body.enabled}


@router.get("/connectors/mcp-config/{cids}")
def get_mcp_config(
    cids: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Generate the MCP server config dict for a comma-separated list of connector IDs."""
    id_list = [c.strip() for c in cids.split(",") if c.strip()]
    # NOTE: the vault / enabled config is deliberately NOT read here — this
    # endpoint returns MASKED config (placeholders intact), so no secret source
    # is consulted on the client-facing path (see the api_key_mcp branch below).
    servers: dict[str, Any] = {}

    for cid in id_list:
        entry = _CATALOG_BY_ID.get(cid)
        if not entry:
            continue
        if entry["kind"] == "session_mcp":
            # Session MCP: include as HTTP type, auth inherited from session
            servers[cid] = {
                "type": "http",
                "url": f"https://{_MCP_URLS.get(cid, '')}",
            }
        elif entry["kind"] == "api_key_mcp":
            # SECURITY (masking): this endpoint is CLIENT-FACING (require_session
            # only). It MUST NOT resolve ${ENV} placeholders to concrete secret
            # values — that would hand any authenticated session (or an XSS
            # reading the response body) the resolved vault + console-process-env
            # secrets, including the console's own ${OPENAI_API_KEY}. Return the
            # config with the ${...} placeholders left UNEXPANDED (masked).
            # Secret resolution for actual spawning happens strictly server-side
            # in build_mcp_config_for_node(); it never crosses the wire here.
            servers[cid] = dict(entry.get("mcp_config", {}))

    return {"mcpServers": servers, "connector_ids": id_list}


# MCP server URLs for session connectors
_MCP_URLS: dict[str, str] = {
    "gmail": "gmailmcp.googleapis.com/mcp/v1",
    "gdrive": "drivemcp.googleapis.com/mcp/v1",
    "gcalendar": "calendarmcp.googleapis.com/mcp/v1",
    "spotify": "mcp-gateway-external-pilot.spotify.net/mcp",
}


@router.get("/connectors/{messenger}/chats")
def list_messenger_chats(
    messenger: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return known chats / channels for any configured bridge messenger.

    Sources (in order, merged by chat_id):
      1. API lookup (Discord → guild channels; Telegram → getUpdates; Slack → conversations.list)
      2. Processed inbox scan (works for every channel, even without API access)

    Returns {chats: [{id, name, label, source}], count, messenger}.
    """
    import urllib.request as _req

    if messenger not in {"discord", "telegram", "slack", "whatsapp", "email", "signal", "teams"}:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, f"unknown messenger: {messenger!r}")

    ah = _forge_paths.corvin_home()
    chats: dict[str, dict[str, Any]] = {}   # keyed by chat_id

    # ── 1. API lookup ─────────────────────────────────────────────────────

    def _settings(channel: str) -> dict[str, Any]:
        for p in [ah / "bridges" / channel / "settings.json",
                  _REPO / "operator" / "bridges" / channel / "settings.json"]:
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return {}

    def _bot_request(url: str, headers: dict) -> Any:
        r = _req.Request(url, headers={**headers,
            "User-Agent": "Corvin/1.0 (workflow-builder)"})
        with _req.urlopen(r, timeout=8) as resp:
            return json.loads(resp.read())

    if messenger == "discord":
        token = _settings("discord").get("discord_token", "")
        if token:
            try:
                h = {"Authorization": f"Bot {token}"}
                guilds = _bot_request("https://discord.com/api/v10/users/@me/guilds", h)
                for guild in guilds:
                    chs = _bot_request(
                        f"https://discord.com/api/v10/guilds/{guild['id']}/channels", h
                    )
                    for ch in sorted(chs, key=lambda c: c.get("position", 0)):
                        if ch.get("type") in (0, 5):
                            chats[ch["id"]] = {
                                "id": ch["id"],
                                "name": f"#{ch['name']}",
                                "label": f"#{ch['name']} — {guild['name']}",
                                "guild": guild["name"],
                                "source": "api",
                            }
            except Exception:
                pass

    elif messenger == "telegram":
        token = _settings("telegram").get("telegram_token", "")
        if token:
            try:
                data = _bot_request(
                    f"https://api.telegram.org/bot{token}/getUpdates?limit=100", {}
                )
                for update in data.get("result", []):
                    for key in ("message", "channel_post", "edited_message", "callback_query"):
                        payload = update.get(key) or {}
                        chat = payload.get("chat") or {}
                        from_user = payload.get("from") or {}
                        if not chat.get("id"):
                            continue
                        cid = str(chat["id"])
                        ctype = chat.get("type", "private")
                        # Groups/channels use title; private chats use name
                        if ctype in ("group", "supergroup", "channel"):
                            name = chat.get("title", cid)
                            icon = "👥" if ctype != "channel" else "📢"
                        else:
                            parts = [p for p in [chat.get("first_name"), chat.get("last_name")] if p]
                            name = " ".join(parts) or chat.get("username") or cid
                            icon = "👤"
                        username = chat.get("username") or from_user.get("username")
                        chats[cid] = {
                            "id": cid,
                            "name": name,
                            "label": f"{icon} {name}" + (f" (@{username})" if username else f" · {ctype}"),
                            "source": "api",
                        }
                        break  # one chat per update
            except Exception:
                pass

    elif messenger == "slack":
        token = _settings("slack").get("slack_bot_token", "")
        if token:
            try:
                h = {"Authorization": f"Bearer {token}"}
                data = _bot_request(
                    "https://slack.com/api/conversations.list?types=public_channel,private_channel&limit=100",
                    h,
                )
                for ch in data.get("channels", []):
                    chats[ch["id"]] = {
                        "id": ch["id"],
                        "name": f"#{ch['name']}",
                        "label": f"#{ch['name']}",
                        "source": "api",
                    }
            except Exception:
                pass

    # ── 2. Inbox scan — extract names from past bridge traffic ───────────
    for inbox_dir in [
        _REPO / "operator" / "bridges" / "shared" / "processed",
        _REPO / "operator" / "bridges" / "shared" / "inbox",
        ah / "bridges" / "shared" / "processed",
    ]:
        if not inbox_dir.exists():
            continue
        for fpath in sorted(inbox_dir.glob("*.json"), reverse=True)[:500]:
            try:
                msg = json.loads(fpath.read_text(encoding="utf-8"))
                if msg.get("channel") != messenger:
                    continue
                cid = str(msg.get("chat_id", ""))
                if not cid:
                    continue
                # WhatsApp: use pushName (contact's display name in their profile)
                # Telegram: use from_name or title
                # Discord: channel has no name in inbox (names come from API)
                name = (
                    msg.get("pushName")           # WhatsApp display name
                    or msg.get("from_name")        # generic bridge field
                    or msg.get("chat_title")       # group title
                    or msg.get("username")
                    or msg.get("from")             # sender ID as fallback
                    or cid
                )
                # Strip WhatsApp JID suffix for display: "+49123@s.whatsapp.net" → "+49123"
                display_id = cid.split("@")[0] if "@" in cid else cid
                # Don't overwrite better API data
                if cid not in chats:
                    chats[cid] = {
                        "id": cid,
                        "name": name,
                        "label": f"{name} ({display_id})" if name != cid else display_id,
                        "source": "inbox",
                    }
                elif name != cid and chats[cid].get("source") == "inbox":
                    # Upgrade name if we found a better one
                    chats[cid]["name"] = name
                    display_id = cid.split("@")[0] if "@" in cid else cid
                    chats[cid]["label"] = f"{name} ({display_id})"
            except Exception:
                pass

    items = sorted(chats.values(), key=lambda c: c.get("label", ""))
    return {"chats": items, "count": len(items), "messenger": messenger}


@router.get("/connectors/discord/channels")
def list_discord_channels(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return all text channels from every Discord guild the bot is in.

    Uses the bot token from the Discord bridge settings. Returns a list of
    {name, id, guild_name, guild_id} so the UI can show "#general" instead
    of a raw snowflake ID.
    """
    import urllib.request
    import urllib.error

    # Read Discord bot token from bridge settings
    bridge_settings = _REPO / "operator" / "bridges" / "discord" / "settings.json"
    # Also check corvin_home location
    ah = _forge_paths.corvin_home()
    for candidate in [
        ah / "bridges" / "discord" / "settings.json",
        bridge_settings,
    ]:
        if candidate.exists():
            try:
                token = json.loads(candidate.read_text(encoding="utf-8")).get("discord_token", "")
                if token:
                    break
            except Exception:
                pass
    else:
        token = ""

    if not token:
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            "Discord bot token not configured in bridge settings",
        )

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "Corvin/1.0 (workflow-builder; +https://github.com/corvin-os/corvin)",
    }
    channels: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me/guilds",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            guilds = json.loads(resp.read())
    except Exception as exc:
        raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, f"Discord API error: {exc}") from exc

    for guild in guilds:
        guild_id = guild["id"]
        guild_name = guild["name"]
        try:
            req2 = urllib.request.Request(
                f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                headers=headers,
            )
            with urllib.request.urlopen(req2, timeout=8) as resp2:
                guild_channels = json.loads(resp2.read())
            for ch in sorted(guild_channels, key=lambda c: c.get("position", 0)):
                if ch.get("type") in (0, 5):   # text + news channels
                    channels.append({
                        "id": ch["id"],
                        "name": ch["name"],
                        "guild_id": guild_id,
                        "guild_name": guild_name,
                        "label": f"#{ch['name']} ({guild_name})",
                    })
        except Exception as exc:
            errors.append(f"{guild_name}: {exc}")

    return {"channels": channels, "count": len(channels), "errors": errors}


def resolve_channel_name(channel_name: str, bridge_channel: str = "discord") -> str | None:
    """Resolve '#general' → '123456789' using the Discord API.

    Returns the channel ID string, or None if not found / API unavailable.
    """
    if not channel_name.startswith("#"):
        return None

    import urllib.request

    name = channel_name.lstrip("#").lower()
    ah = _forge_paths.corvin_home()
    token = ""
    for candidate in [
        ah / "bridges" / "discord" / "settings.json",
        _REPO / "operator" / "bridges" / "discord" / "settings.json",
    ]:
        if candidate.exists():
            try:
                token = json.loads(candidate.read_text(encoding="utf-8")).get("discord_token", "")
                if token:
                    break
            except Exception:
                pass

    if not token:
        return None

    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "Corvin/1.0 (workflow-builder)",
    }
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me/guilds",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            guilds = json.loads(r.read())
        for guild in guilds:
            req2 = urllib.request.Request(
                f"https://discord.com/api/v10/guilds/{guild['id']}/channels",
                headers=headers,
            )
            with urllib.request.urlopen(req2, timeout=6) as r2:
                guild_channels = json.loads(r2.read())
            for ch in guild_channels:
                if ch.get("type") in (0, 5) and ch["name"].lower() == name:
                    return str(ch["id"])
    except Exception:
        pass
    return None


def build_mcp_config_for_node(tenant_id: str, tool_ids: list[str]) -> dict[str, Any]:
    """Build the mcpServers dict for workflow node execution. Called by the runner."""
    enabled_cfg = _read_enabled(tenant_id)
    vault = _read_vault(tenant_id)
    servers: dict[str, Any] = {}

    for cid in tool_ids:
        entry = _CATALOG_BY_ID.get(cid)
        if not entry:
            continue
        if entry["kind"] == "session_mcp":
            # Just include a stub — session auth is automatic with --mcp-config
            servers[cid] = {
                "type": "http",
                "url": f"https://{_MCP_URLS.get(cid, 'mcp.example.com/mcp')}",
            }
        elif entry["kind"] == "api_key_mcp":
            cfg = dict(entry.get("mcp_config", {}))
            if cfg.get("env"):
                resolved = {}
                for k, v in cfg["env"].items():
                    if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                        env_name = v[2:-1]
                        resolved[k] = vault.get(env_name) or os.environ.get(env_name) or ""
                    else:
                        resolved[k] = str(v)
                cfg["env"] = resolved
            if cfg.get("args"):
                cfg["args"] = [
                    (vault.get(a[2:-1]) or os.environ.get(a[2:-1]) or a)
                    if (isinstance(a, str) and a.startswith("${") and a.endswith("}"))
                    else a
                    for a in cfg["args"]
                ]
            servers[cid] = cfg

    return {"mcpServers": servers}
