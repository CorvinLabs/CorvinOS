"""Corvin path resolver — single root for all generated user-data (cross-platform).

Canonical home: CORVIN_HOME env var, else ~/.corvin/ (or <repo>/.corvin/ if in a repo).
Voice config: ~/.config/corvin-voice/ (uniform on every platform; VOICE_CONFIG_DIR / XDG_CONFIG_HOME honored).

Resolution order for CORVIN_HOME:
  1. CORVIN_HOME env var (canonical override)
  2. <repo-root>/.corvin/ if already exists
  3. <repo-root>/.corvin/ when in a recognisable repo
  4. ~/.corvin/ — last-resort fallback

ADR-0007: Multi-tenant support via CORVIN_TENANT_ID (default: _default).
ADR-0008: Bridge runtime state under <corvin_home>/bridges/<channel>/<kind>/.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

_DEPRECATION_LOGGED: set[str] = set()


def _log_once(key: str, message: str) -> None:
    """Log a message exactly once, to stderr."""
    if key in _DEPRECATION_LOGGED:
        return
    _DEPRECATION_LOGGED.add(key)
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:
        pass


def _resolve_env() -> str | None:
    """Resolve CORVIN_HOME from environment."""
    return os.environ.get("CORVIN_HOME")


def _repo_root() -> Path | None:
    """Find repo root by looking for .corvin_repo marker or plugins/ dir."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent
    return None


def corvin_home() -> Path:
    """Return the Corvin home directory (cross-platform)."""
    env = _resolve_env()
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    repo = _repo_root()
    if repo is not None:
        return repo / ".corvin"
    return Path.home() / ".corvin"


def _platform_name() -> str:
    """Detect platform: 'linux', 'darwin', 'windows'."""
    system = platform.system()
    if system == "Darwin":
        return "darwin"
    elif system == "Windows":
        return "windows"
    else:
        return "linux"


def voice_config_dir() -> Path:
    """Return voice config directory — SSOT, byte-identical to
    forge.paths.voice_config_dir() and the voice-script mirrors.

    Resolution (uniform on every platform, like ~/.corvin):
        1. VOICE_CONFIG_DIR env override
        2. XDG_CONFIG_HOME/corvin-voice
        3. ~/.config/corvin-voice

    The former Windows %APPDATA%\\Local branch made the console write a dir the
    installer + voice scripts never read (reader≠writer, path-audit 2026-07-06).
    Guard: tests/test_voice_config_ssot.py.
    """
    override = os.environ.get("VOICE_CONFIG_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else (Path.home() / ".config")
    return base / "corvin-voice"


def voice_dir(tenant_id: str | None = None) -> Path:
    """Return the voice directory for a given tenant (delegates to tenant_voice_dir)."""
    return tenant_voice_dir(tenant_id)


def cowork_dir() -> Path:
    """Return cowork directory."""
    return corvin_home() / "cowork"


def forge_dir() -> Path:
    """Return forge directory."""
    return corvin_home() / "forge"


# ── ADR-0007 Phase 1.2 — tenant-aware resolvers ───────────────────────────
import re as _tenants_re

_DEFAULT_TENANT_ID = "_default"
_TENANT_ID_RE = _tenants_re.compile(r"^[a-z0-9_][a-z0-9_-]{0,62}$")


def _validate_tenant_id(tenant_id: str) -> str:
    """Validate tenant_id format."""
    if not isinstance(tenant_id, str):
        raise ValueError(f"tenant_id must be str, got {type(tenant_id).__name__}")
    if not tenant_id:
        raise ValueError("tenant_id must not be empty")
    if tenant_id.startswith("__"):
        raise ValueError(f"tenant_id {tenant_id!r} starts with '__' (reserved)")
    if not _TENANT_ID_RE.match(tenant_id):
        raise ValueError(
            f"tenant_id {tenant_id!r} fails charset rule [a-z0-9_][a-z0-9_-]{{0,62}}"
        )
    return tenant_id


def _resolve_tenant_id(tenant_id: str | None) -> str:
    """Resolve tenant_id from arg or CORVIN_TENANT_ID env, default to _default."""
    if tenant_id is not None:
        return _validate_tenant_id(tenant_id)
    env = os.environ.get("CORVIN_TENANT_ID")
    if env:
        return _validate_tenant_id(env)
    return _DEFAULT_TENANT_ID


def tenant_home(tenant_id: str | None = None) -> Path:
    """Return tenant home directory."""
    return corvin_home() / "tenants" / _resolve_tenant_id(tenant_id)


def tenant_global_dir(tenant_id: str | None = None) -> Path:
    """Return tenant global directory."""
    return tenant_home(tenant_id) / "global"


def tenant_sessions_dir(tenant_id: str | None = None) -> Path:
    """Return tenant sessions directory."""
    return tenant_home(tenant_id) / "sessions"


def tenant_forge_dir(tenant_id: str | None = None) -> Path:
    """Return tenant forge directory."""
    return tenant_home(tenant_id) / "forge"


def tenant_skill_forge_dir(tenant_id: str | None = None) -> Path:
    """Return tenant skill-forge directory."""
    return tenant_home(tenant_id) / "skill-forge"


def tenant_voice_dir(tenant_id: str | None = None) -> Path:
    """Return tenant voice directory."""
    return tenant_home(tenant_id) / "voice"


def tenant_cowork_dir(tenant_id: str | None = None) -> Path:
    """Return tenant cowork directory."""
    return tenant_home(tenant_id) / "cowork"


def tenant_workflow_runs_dir(tenant_id: str | None = None) -> Path:
    """Return tenant workflow-runs directory (ADR-0188 M5: paused/resumable
    AWP run checkpoints, one JSON file per run_id)."""
    return tenant_home(tenant_id) / "workflow_runs"


def voice_sessions_dir(tenant_id: str | None = None) -> Path:
    """Return voice sessions directory for a given tenant."""
    return tenant_sessions_dir(tenant_id) / "voice"


def voice_session_dir(
    channel: str, safe_chat_key: str, tenant_id: str | None = None
) -> Path:
    """Return per-chat session directory for one bridge channel.

    Path: <tenant_voice_sessions_dir>/<channel>/<safe_chat_key>/
    """
    return (
        voice_sessions_dir(tenant_id)
        / _validate_bridge_channel(channel)
        / safe_chat_key
    )


# ── ADR-0008 — bridge runtime state out of repo ──────────────────────────
_BRIDGE_CHANNELS = frozenset({
    "telegram",
    "discord",
    "slack",
    "whatsapp",
    "email",
    "shared",
})
_BRIDGE_KINDS = frozenset({
    "inbox",
    "outbox",
    "processed",
    "attachments",
    "auth",
    "log",
    "settings",
    "root",
})
_BRIDGE_CHANNEL_RE = _tenants_re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _validate_bridge_channel(channel: str) -> str:
    """Validate bridge channel name."""
    if not isinstance(channel, str):
        raise ValueError(
            f"bridge channel must be str, got {type(channel).__name__}"
        )
    if not _BRIDGE_CHANNEL_RE.match(channel):
        raise ValueError(
            f"bridge channel {channel!r} fails charset rule [a-z][a-z0-9_-]{{0,31}}"
        )
    return channel


def _validate_bridge_kind(kind: str) -> str:
    """Validate bridge kind."""
    if kind not in _BRIDGE_KINDS:
        raise ValueError(f"bridge kind {kind!r} not in {sorted(_BRIDGE_KINDS)}")
    return kind


def bridges_home() -> Path:
    """Return root of all bridge runtime state: <corvin_home>/bridges/."""
    return corvin_home() / "bridges"


def bridge_channel_dir(channel: str) -> Path:
    """Return per-channel runtime root: <bridges_home>/<channel>/."""
    return bridges_home() / _validate_bridge_channel(channel)


def bridge_runtime_dir(channel: str, kind: str) -> Path:
    """Resolve a specific runtime subdir for one bridge channel.

    Kinds: inbox, outbox, processed, attachments, auth, log, settings, root.
    Respects CORVIN_BRIDGES_HOME and CORVIN_BRIDGE_<CH>_<KIND> env overrides.
    """
    channel = _validate_bridge_channel(channel)
    kind = _validate_bridge_kind(kind)
    env_key = f"CORVIN_BRIDGE_{channel.upper()}_{kind.upper()}"
    env_override = os.environ.get(env_key)
    if env_override:
        return Path(os.path.expanduser(os.path.expandvars(env_override)))
    root_override = os.environ.get("CORVIN_BRIDGES_HOME")
    base = (
        Path(os.path.expanduser(os.path.expandvars(root_override)))
        if root_override
        else bridges_home()
    )
    channel_dir = base / channel
    if kind in ("settings", "root"):
        return channel_dir
    return channel_dir / kind


def bridge_settings_path(channel: str) -> Path:
    """Return path to per-channel settings.json (credentials, mode 0600)."""
    return bridge_runtime_dir(channel, "root") / "settings.json"


def bridge_log_path(channel: str) -> Path:
    """Return path to per-channel voice.log file."""
    return bridge_runtime_dir(channel, "log") / "voice.log"


def legacy_bridge_runtime_dir(channel: str, kind: str) -> Path | None:
    """Return legacy in-repo path for bridge runtime dir (migration helper only)."""
    channel = _validate_bridge_channel(channel)
    kind = _validate_bridge_kind(kind)
    repo = _repo_root()
    if repo is None:
        return None
    channel_dir = repo / "operator" / "bridges" / channel
    if kind in ("settings", "root"):
        return channel_dir
    return channel_dir / kind
