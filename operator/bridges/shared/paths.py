"""Corvin path resolver — single root for all generated user-data.

``CORVIN_HOME`` is the canonical env var. On disk, ``~/.corvin/`` (or
``<repo>/.corvin/``) is the default.

Resolution order:
  1. ``CORVIN_HOME`` env var (canonical override)
  2. ``<repo-root>/.corvin/`` if it already exists
  3. ``<repo-root>/.corvin/`` as default when in a recognisable repo
  4. ``~/.corvin/`` — last-resort fallback when called from outside
     a recognisable repo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_DEPRECATION_LOGGED: set[str] = set()


def _log_once(key: str, message: str) -> None:
    if key in _DEPRECATION_LOGGED:
        return
    _DEPRECATION_LOGGED.add(key)
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:
        pass


def _resolve_env() -> str | None:
    new = os.environ.get("CORVIN_HOME")
    if new is None:
        return None
    new = new.strip()
    # A whitespace-only value (" ", "\t", "\n") is not a real override —
    # treat it the same as unset rather than resolving to a bogus path like
    # Path(" ") relative to the cwd. Mirrors operator/cowork/lib/paths.py and
    # operator/forge/forge/paths.py — the a111d09 sweep fixed those two but
    # missed this third copy (found closing the red test_paths suite).
    if not new:
        return None
    return new


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent
    return None


def corvin_home() -> Path:
    env = _resolve_env()
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    repo = _repo_root()
    if repo is not None:
        return repo / ".corvin"
    return Path.home() / ".corvin"


def voice_dir(tenant_id: str | None = None) -> Path:
    """Return the voice directory for a given tenant.

    Delegates to tenant_voice_dir() for ADR-0007 tenant isolation.
    When tenant_id is None, resolves via CORVIN_TENANT_ID env or "_default".
    """
    return tenant_voice_dir(tenant_id)


def cowork_dir() -> Path:
    return corvin_home() / "cowork"


def forge_dir() -> Path:
    return corvin_home() / "forge"


# ── ADR-0007 Phase 1.2 — tenant-aware resolvers ───────────────────────────
# See operator/forge/forge/paths.py for the canonical contract.
import re as _tenants_re

_DEFAULT_TENANT_ID = "_default"
_TENANT_ID_RE = _tenants_re.compile(r"^[a-z0-9_][a-z0-9_-]{0,62}$")


def _validate_tenant_id(tenant_id: str) -> str:
    if not isinstance(tenant_id, str):
        raise ValueError(
            f"tenant_id must be str, got {type(tenant_id).__name__}"
        )
    if not tenant_id:
        raise ValueError("tenant_id must not be empty")
    if tenant_id.startswith("__"):
        raise ValueError(
            f"tenant_id {tenant_id!r} starts with '__' (reserved)"
        )
    if not _TENANT_ID_RE.match(tenant_id):
        raise ValueError(
            f"tenant_id {tenant_id!r} fails charset rule "
            f"[a-z0-9_][a-z0-9_-]{{0,62}}"
        )
    return tenant_id


def _resolve_tenant_id(tenant_id: str | None) -> str:
    if tenant_id is not None:
        return _validate_tenant_id(tenant_id)
    env = os.environ.get("CORVIN_TENANT_ID")
    if env:
        return _validate_tenant_id(env)
    return _DEFAULT_TENANT_ID


def tenant_home(tenant_id: str | None = None) -> Path:
    return corvin_home() / "tenants" / _resolve_tenant_id(tenant_id)


def tenant_global_dir(tenant_id: str | None = None) -> Path:
    return tenant_home(tenant_id) / "global"


def tenant_sessions_dir(tenant_id: str | None = None) -> Path:
    return tenant_home(tenant_id) / "sessions"


def tenant_forge_dir(tenant_id: str | None = None) -> Path:
    return tenant_home(tenant_id) / "forge"


def tenant_skill_forge_dir(tenant_id: str | None = None) -> Path:
    return tenant_home(tenant_id) / "skill-forge"


def tenant_voice_dir(tenant_id: str | None = None) -> Path:
    return tenant_home(tenant_id) / "voice"


def tenant_cowork_dir(tenant_id: str | None = None) -> Path:
    return tenant_home(tenant_id) / "cowork"


def voice_sessions_dir(tenant_id: str | None = None) -> Path:
    """Return the voice sessions directory for a given tenant.

    ADR-0007 isolation: returns tenant-specific path via tenant_sessions_dir().
    """
    return tenant_sessions_dir(tenant_id) / "voice"


def voice_session_dir(channel: str, safe_chat_key: str, tenant_id: str | None = None) -> Path:
    """Return the per-chat session directory for one bridge channel.

    Path: <tenant_voice_sessions_dir>/<channel>/<safe_chat_key>/
    The safe_chat_key must be pre-sanitized by the caller (e.g., via _safe_id).
    Never creates directories — identity-only resolver.
    """
    return voice_sessions_dir(tenant_id) / _validate_bridge_channel(channel) / safe_chat_key


# ── ADR-0008 — bridge runtime state out of repo ──────────────────────────
# All bridge runtime state (inbox/outbox/processed/attachments queues,
# settings.json with credentials, auth/, voice.log) lives under
# ``<corvin_home>/bridges/<channel>/<kind>/`` so the repo tree contains
# zero user-private data. Identity-only — no FS side effects. The Phase 8.2
# migration helper is the single owner of mkdir for this tree; resolvers
# never create directories.

_BRIDGE_CHANNELS = frozenset({
    "telegram", "discord", "slack", "whatsapp", "email", "shared",
})
_BRIDGE_KINDS = frozenset({
    "inbox", "outbox", "processed", "attachments", "auth", "log",
    "settings", "root",
})
_BRIDGE_CHANNEL_RE = _tenants_re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _validate_bridge_channel(channel: str) -> str:
    if not isinstance(channel, str):
        raise ValueError(
            f"bridge channel must be str, got {type(channel).__name__}"
        )
    if not _BRIDGE_CHANNEL_RE.match(channel):
        raise ValueError(
            f"bridge channel {channel!r} fails charset rule "
            f"[a-z][a-z0-9_-]{{0,31}}"
        )
    return channel


def _validate_bridge_kind(kind: str) -> str:
    if kind not in _BRIDGE_KINDS:
        raise ValueError(
            f"bridge kind {kind!r} not in {sorted(_BRIDGE_KINDS)}"
        )
    return kind


def bridges_home() -> Path:
    """Root of all bridge runtime state — `<corvin_home>/bridges/`."""
    return corvin_home() / "bridges"


def bridge_channel_dir(channel: str) -> Path:
    """Per-channel runtime root — `<bridges_home>/<channel>/`."""
    return bridges_home() / _validate_bridge_channel(channel)


def bridge_runtime_dir(channel: str, kind: str) -> Path:
    """Resolve a specific runtime subdir for one bridge channel.

    Identity-only: never touches the filesystem. The Phase 8.2 migration
    helper is responsible for `mkdir`.

    Kinds:
      - ``inbox`` / ``outbox`` / ``processed`` — message queues
      - ``attachments`` — user-uploaded files (private)
      - ``auth`` — credential material (WhatsApp pairing state)
      - ``log`` — per-daemon log dir (single voice.log inside)
      - ``settings`` — channel root (returns the channel dir;
        settings.json lives as a file inside)
      - ``root`` — alias for ``settings``

    Resolution:
      Always returns ``<bridges_home>/<channel>/<kind>/`` for queue/log
      kinds, or ``<bridges_home>/<channel>/`` for ``settings`` / ``root``.
      An ENV-override path takes precedence:
        - ``CORVIN_BRIDGES_HOME`` — root override (everything resolves
          below it)
        - ``CORVIN_BRIDGE_<CHANNEL>_<KIND>`` — single-leaf override
          (per-test sandbox path; mirrors the adapter's ``ADAPTER_INBOX``
          / ``ADAPTER_OUTBOX`` / ``ADAPTER_PROCESSED`` triple)
    """
    channel = _validate_bridge_channel(channel)
    kind = _validate_bridge_kind(kind)
    env_key = f"CORVIN_BRIDGE_{channel.upper()}_{kind.upper()}"
    env_override = os.environ.get(env_key)
    if env_override:
        return Path(os.path.expanduser(os.path.expandvars(env_override)))
    root_override = os.environ.get("CORVIN_BRIDGES_HOME")
    base = Path(os.path.expanduser(os.path.expandvars(root_override))) if root_override else bridges_home()
    channel_dir = base / channel
    if kind in ("settings", "root"):
        return channel_dir
    return channel_dir / kind


def bridge_settings_path(channel: str) -> Path:
    """The per-channel ``settings.json`` file (credentials, mode 0o600)."""
    return bridge_runtime_dir(channel, "root") / "settings.json"


def bridge_log_path(channel: str) -> Path:
    """The per-channel ``voice.log`` file inside the ``log/`` subdir."""
    return bridge_runtime_dir(channel, "log") / "voice.log"


def legacy_bridge_runtime_dir(channel: str, kind: str) -> Path | None:
    """Return the legacy in-repo path for a bridge runtime dir, if it
    can be located. Returns None when no repo root can be derived.

    The Phase 8.2 migration helper uses this to detect content to move;
    no other code path should read from the legacy location.
    """
    channel = _validate_bridge_channel(channel)
    kind = _validate_bridge_kind(kind)
    repo = _repo_root()
    if repo is None:
        return None
    # Try new operator/bridges layout first, fall back to legacy plugins/ location
    channel_dir = repo / "operator" / "bridges" / channel
    if not channel_dir.exists():
        channel_dir = repo / "operator" / "bridges" / channel
    if kind in ("settings", "root"):
        return channel_dir
    return channel_dir / kind
