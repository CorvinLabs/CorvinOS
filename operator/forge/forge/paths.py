"""Corvin path resolver — single root for all generated user-data.

Resolution order (the SINGLE source of truth every reader+writer must use):
  1. ``CORVIN_HOME`` env var (expanded) if set
  2. ``<repo-root>/.corvin/`` when called from inside a recognisable repo
  3. ``~/.corvin/`` — last-resort fallback outside a repo.

Hard cut (CLAUDE.md rebrand): there is NO legacy ``.corvinOS`` fallback — it lived
only here, diverged from every other resolver, and is removed. Legacy installs
migrate via corvin_migrate.py.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path


def _resolve_env() -> str | None:
    # CORVIN_HOME env is read when explicitly set (e.g. in tests and CI).
    # The Phase 7 note referred to removing legacy aliases like ATELIER_HOME —
    # those are gone. CORVIN_HOME itself remains the canonical override knob.
    return os.environ.get("CORVIN_HOME")


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
        # Hard cut (CLAUDE.md rebrand): the ONLY repo-relative home is .corvin.
        # The legacy .corvinOS fallback was removed — it existed ONLY here and
        # diverged from the other three paths.py copies + bridge_paths.js (all of
        # which resolve .corvin only), creating a reader≠writer split. Legacy
        # installs migrate via corvin_migrate.py, never via a hot-path fallback.
        # (path-audit 2026-06-25 — legacy_ref cluster)
        return repo / ".corvin"
    return Path.home() / ".corvin"


def voice_config_dir() -> Path:
    """Return the voice config directory (service.env / .env API keys, setup marker).

    SSOT resolution — IDENTICAL across every reader/writer (installer key step,
    console engines page, bridge_manager, and the voice STT/TTS scripts) and every
    platform:
        1. VOICE_CONFIG_DIR env override (explicit; lets a launcher pin the dir)
        2. XDG_CONFIG_HOME/corvin-voice
        3. ~/.config/corvin-voice

    Uniform on Windows too — exactly like ``~/.corvin`` is cross-platform-uniform.
    A prior ``%APPDATA%\\Local`` Windows branch made the console write a directory
    the installer key step and the voice scripts never read (reader≠writer;
    also ``%APPDATA%`` is Roaming, so ``\\Local`` was a nonstandard dir). The STT
    file-key fallback (openai_whisper.py) therefore never saw console-configured
    keys on Windows. Collapsing to one rule closes that split (path-audit 2026-07-06).

    Console routes and voice scripts MUST resolve through this rule (or the
    byte-identical mirrors in corvinOS/shared/paths.py + the voice scripts) — never
    hard-code Path.home()/".config". Guard: tests/test_voice_config_ssot.py.
    """
    override = os.environ.get("VOICE_CONFIG_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else (Path.home() / ".config")
    return base / "corvin-voice"


def voice_dir() -> Path:
    return corvin_home() / "voice"


def cowork_dir() -> Path:
    return corvin_home() / "cowork"


def forge_dir() -> Path:
    return corvin_home() / "forge"


# ── ADR-0007 Phase 1.2 — tenant-aware resolvers ───────────────────────────
#
# `tenant_home(tid)` returns ``<corvin_home>/tenants/<tid>/`` for the
# active tenant. Default tenant is ``_default`` (implicit single-operator
# path). Phase 1.4 will land the migration that moves
# ``<corvin_home>/global/*`` into ``tenants/_default/global/*``; until
# then these resolvers are NEW additive functions and the legacy paths
# above stay byte-identical for backward compatibility.
#
# Tenant resolution: explicit arg > ``CORVIN_TENANT_ID`` env > ``_default``.
# The canonical contract lives in ``forge.tenants``; this copy duplicates
# the helpers to match the existing three-copy paths.py pattern (forge,
# cowork, voice/bridges/shared). Phase 2 will consolidate.
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
    """Return ``<corvin_home>/tenants/<tid>/`` for the active tenant.

    Phase 1.2 contract: no filesystem side effects. Phase 1.4 owns
    directory creation via the migration helper.
    """
    return corvin_home() / "tenants" / _resolve_tenant_id(tenant_id)


def tenant_global_dir(tenant_id: str | None = None) -> Path:
    """Return ``<tenant_home>/global/`` — state-store root for the active tenant."""
    return tenant_home(tenant_id) / "global"


def tenant_sessions_dir(tenant_id: str | None = None) -> Path:
    """Return ``<tenant_home>/sessions/`` — per-bridge session root for the active tenant."""
    return tenant_home(tenant_id) / "sessions"


# ── Cross-platform path-component safety ─────────────────────────────────────
# A session/workdir directory name is derived from a logical key such as the
# console chat_key ``web:<sid>`` or a bridge key ``discord:<id>``. The ``:`` is
# legal in a POSIX path component but ILLEGAL in a Windows filename (it is the
# drive separator) — so ``mkdir(".../sessions/web:<sid>")`` raised
# ``NotADirectoryError: [WinError 267]`` on a fresh Windows install and NO chat
# could ever be created. ``fs_safe_component`` neutralises every character that
# is illegal in a leaf path component on the *current* OS.
#
# PLATFORM-AWARE BY DESIGN: on POSIX it only touches ``/`` and NUL, so existing
# Linux/macOS installs keep byte-identical ``web:<sid>`` dirs (no migration, no
# orphaned workdirs, no reader≠writer drift). On Windows it replaces the full
# reserved set. Runtime homes are machine-local, so the per-OS divergence is
# never observed across a shared filesystem.
_WINDOWS_ILLEGAL = set('<>:"/\\|?*') | {chr(c) for c in range(32)}
_POSIX_ILLEGAL = {"/", "\0"}
_WINDOWS_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def fs_safe_component(name: str, *, windows: bool | None = None) -> str:
    """Return ``name`` made safe to use as a single filesystem path component on
    the target OS. Replaces illegal characters with ``_``; on Windows also strips
    trailing dots/spaces and guards reserved device names. Never returns empty.

    ``windows`` defaults to the running OS; pass it explicitly to test the
    Windows branch from a POSIX CI host."""
    is_win = (os.name == "nt") if windows is None else windows
    illegal = _WINDOWS_ILLEGAL if is_win else _POSIX_ILLEGAL
    out = "".join("_" if ch in illegal else ch for ch in name)
    if is_win:
        out = out.rstrip(" .")  # Windows: no trailing space or dot
        stem = out.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            out = "_" + out
    # A component that collapsed to empty or to "."/".." must never address the
    # parent or the dir itself.
    if not out or set(out) <= {"."}:
        out = "_" + out.replace(".", "")
    return out


def safe_session_subdir(base: Path, raw_component: str, *,
                        windows: bool | None = None) -> Path:
    """``base / fs_safe_component(raw_component)``, but prefer a pre-existing
    legacy (un-sanitised) sibling if one is already on disk — so an in-place
    upgrade on POSIX keeps reading the original ``web:<sid>`` workdir instead of
    silently switching to a fresh empty one. On Windows the legacy name can never
    have been created, so the safe name always wins."""
    safe = base / fs_safe_component(raw_component, windows=windows)
    legacy = base / raw_component
    try:
        if legacy != safe and legacy.exists() and not safe.exists():
            return legacy
    except OSError:
        pass  # an illegal legacy name can raise on stat → just use safe
    return safe


def tenant_forge_dir(tenant_id: str | None = None) -> Path:
    """Return ``<tenant_home>/forge/`` — forge user-scope workspace for the active tenant."""
    return tenant_home(tenant_id) / "forge"


def tenant_skill_forge_dir(tenant_id: str | None = None) -> Path:
    """Return ``<tenant_home>/skill-forge/`` — skill-forge user-scope workspace."""
    return tenant_home(tenant_id) / "skill-forge"


def tenant_voice_dir(tenant_id: str | None = None) -> Path:
    """Return ``<tenant_home>/voice/`` — voice runtime state for the active tenant."""
    return tenant_home(tenant_id) / "voice"


def tenant_cowork_dir(tenant_id: str | None = None) -> Path:
    """Return ``<tenant_home>/cowork/`` — cowork persona cache for the active tenant."""
    return tenant_home(tenant_id) / "cowork"
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
