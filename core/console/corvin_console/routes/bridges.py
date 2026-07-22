"""Bridges configuration — read + write per-channel settings.json.

Channels: telegram / discord / slack / whatsapp / email / signal / teams.
Each channel's settings live at:

    <repo>/operator/bridges/<channel>/settings.json

The bridge daemons hot-reload these files on every inbox message and
on mtime change (see CLAUDE.md § "Hot-reload convention for bridge
settings"), so an edit here takes effect immediately — no restart.

Secret handling
---------------
Fields whose key name matches any of ``_SECRET_KEY_HINTS`` are masked
on GET (shows ``****…last4``) and PUT treats a *masked* value as
"keep existing" — so the UI never sees cleartext secrets and a
round-trip Edit+Save is safe.

Write contract
--------------
PUT requires the standard ADR-0015 mutation gate: cookie + CSRF +
re-auth token. The payload replaces the whole file atomically (tmp +
rename + chmod 0600). The previous file is rotated to ``settings.json.bak``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session, verify_reauth


_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]


def _resolve_bridges_dir() -> Path:
    # Source-tree path; in a wheel install operator/* is vendored under
    # corvin_console/_vendor/operator/* and _REPO points outside site-packages, so
    # bridge.sh / package.json / per-channel dirs were unreachable on a wheel
    # install. Resolve to whichever layout has the files (path-audit #MED9).
    repo = _REPO / "operator" / "bridges"
    if repo.is_dir():
        return repo
    vendored = _THIS_DIR.parent / "_vendor" / "operator" / "bridges"
    return vendored if vendored.is_dir() else repo


_BRIDGES_DIR = _resolve_bridges_dir()

_log = logging.getLogger(__name__)


CHANNELS = (
    "telegram", "discord", "slack",
    "whatsapp", "email", "signal", "teams",
)

# Keys whose value should be masked / preserved on round-trip.
_SECRET_KEY_HINTS = (
    "token", "secret", "password", "passwd",
    "api_key", "apikey", "client_secret", "webhook_url",
    "pin", "appkey", "app_key",
)

_MASKED_PREFIX = "************"
_MAX_SETTINGS_BYTES = 256 * 1024


router = APIRouter()


# ── Enable/disable state ──────────────────────────────────────────────
#
# settings.json files are bind-mounted ``:ro`` in the production
# container (see ops/docker-compose.yml), so the enabled-toggle cannot
# live inside settings.json itself. Source of truth is a small
# ``state.json`` under the ``:rw`` mounted corvin home:
#
#     <corvin_home>/bridges/state.json
#     {
#       "channels": {
#         "discord":  {"enabled": false},
#         "whatsapp": {"enabled": true}
#       }
#     }
#
# Missing entry → channel defaults to enabled. The toggle endpoint
# writes this file and, best-effort, calls ``supervisorctl start|stop
# bridge-<channel>`` so the change takes effect immediately. If
# supervisorctl is unavailable (foreground/systemd mode, missing
# socket), the API returns ``restart_needed: true``.


def _service_env_corvin_home() -> str | None:
    """Read a CORVIN_HOME pin from ~/.config/corvin-voice/service.env.

    bridge_manager and every daemon honour this pin (bridge.sh writes it), so
    the console — the WRITER of settings.json/state.json — must resolve the
    same home or it writes a file the daemons never read (writer≠reader split
    under a documented pin). Best-effort; malformed lines are ignored."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else (Path.home() / ".config")
    env_file = base / "corvin-voice" / "service.env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k.startswith("export "):
                k = k[len("export "):].strip()
            if k != "CORVIN_HOME":
                continue
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            if v.strip():
                return v
    except OSError:
        pass
    return None


def _corvin_home() -> Path:
    """Resolve the writable corvin home the SAME way bridge_manager and the
    daemons do: env CORVIN_HOME → service.env pin → <repo>/.corvin → ~/.corvin.
    Mirrors paths.corvin_home() without importing the shared module (avoid a
    console→bridges-shared coupling)."""
    val = os.environ.get("CORVIN_HOME")
    if val and val.strip():
        return Path(val)
    pinned = _service_env_corvin_home()
    if pinned:
        return Path(pinned)
    repo_local = _REPO / ".corvin"
    if repo_local.exists() or (_REPO / ".corvin_repo").exists():
        return repo_local
    return Path.home() / ".corvin"


def _state_path() -> Path:
    return _corvin_home() / "bridges" / "state.json"


def _read_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"channels": {}}
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("bridges state.json unreadable, defaulting open: %s", e)
        return {"channels": {}}
    if not isinstance(data, dict):
        return {"channels": {}}
    channels = data.get("channels")
    if not isinstance(channels, dict):
        data["channels"] = {}
    return data


def _channel_enabled(channel: str, state: dict[str, Any] | None = None) -> bool:
    s = state if state is not None else _read_state()
    entry = s.get("channels", {}).get(channel)
    if not isinstance(entry, dict):
        return True
    return bool(entry.get("enabled", True))


def _write_state(state: dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(state, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="write state.json failed",
        ) from e
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _supervisor_toggle(channel: str, enabled: bool) -> dict[str, Any]:
    """Best-effort runtime toggle via supervisorctl.

    Returns ``{"applied": True, "via": "supervisorctl"}`` when the
    command succeeded, else ``{"applied": False, "reason": "..."}``.
    Never raises — the state-file write is the authoritative change;
    runtime activation falls back to "wirkt nach Container-Restart".
    """
    program = f"bridge-{channel}"
    action = "start" if enabled else "stop"
    cmd = ["supervisorctl", action, program]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False,
        )
    except FileNotFoundError:
        return {"applied": False, "reason": "supervisorctl not on PATH"}
    except subprocess.TimeoutExpired:
        return {"applied": False, "reason": "supervisorctl timeout"}
    out = (proc.stdout or "") + (proc.stderr or "")
    out = out.strip()
    if proc.returncode == 0:
        return {"applied": True, "via": "supervisorctl", "output": out[:200]}
    # supervisorctl returns non-zero when program doesn't exist or socket
    # unreachable — both are "not applied at runtime", state-file still wins.
    return {
        "applied": False,
        "reason": f"supervisorctl rc={proc.returncode}: {out[:200]}",
    }


# ── systemd / bridge.sh runtime apply ─────────────────────────────────
#
# On systemd hosts (Linux / WSL2) the bridges are managed by user
# units installed by ``bridge.sh up``. ``settings.json`` writes hot-
# reload for whitelist / rate-limit etc., but token rotation AND the
# first-time activation of a previously-unconfigured channel require
# a process (re)start. ``_apply_runtime_change`` orchestrates that:
#
#   - if supervisorctl knows the program  -> use that (Docker prod)
#   - elif unit is installed AND active   -> ``systemctl --user restart``
#   - else                                -> ``bridge.sh up`` (idempotent
#                                            full install + enable)
#
# Console runs as ``corvin-webui.service`` under the same user manager,
# so ``systemctl --user`` and ``bash bridge.sh up`` both work without
# additional auth. Falls back to ``restart_needed=True`` when neither
# path applies.


_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"


def _unit_name(channel: str) -> str:
    return f"corvin-voice-bridge-{channel}.service"


def _unit_installed(channel: str) -> bool:
    return (_SYSTEMD_USER_DIR / _unit_name(channel)).exists()


def _unit_active(channel: str) -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", _unit_name(channel)],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "active"


def _systemctl_action(channel: str, action: str) -> dict[str, Any]:
    """``action`` in {start, stop, restart}."""
    cmd = ["systemctl", "--user", action, _unit_name(channel)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False,
        )
    except FileNotFoundError:
        return {"applied": False, "reason": "systemctl not on PATH"}
    except subprocess.TimeoutExpired:
        return {"applied": False, "reason": f"systemctl {action} timeout"}
    if proc.returncode == 0:
        return {"applied": True, "via": f"systemctl {action}"}
    err = (proc.stderr or proc.stdout or "").strip()[:200]
    return {
        "applied": False,
        "reason": f"systemctl {action} rc={proc.returncode}: {err}",
    }


def _run_bridge_sh_up() -> dict[str, Any]:
    """Run ``bridge.sh up`` to install missing units + start configured
    channels. Idempotent. Bounded at 120 s to cover npm install on
    fresh channels.
    """
    script = _BRIDGES_DIR / "bridge.sh"
    if not script.exists():
        return {"applied": False, "reason": f"missing {script}"}
    env = os.environ.copy()
    env.setdefault("CORVIN_HOME", str(_corvin_home()))
    try:
        proc = subprocess.run(
            ["bash", str(script), "up"],
            capture_output=True, text=True, timeout=120, check=False,
            env=env,
        )
    except FileNotFoundError:
        return {"applied": False, "reason": "bash not on PATH"}
    except subprocess.TimeoutExpired:
        return {"applied": False, "reason": "bridge.sh up timeout (120s)"}
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return {"applied": True, "via": "bridge.sh up", "output": out[-400:].strip()}
    return {
        "applied": False,
        "reason": f"bridge.sh up rc={proc.returncode}: {out[-200:].strip()}",
    }


def _apply_runtime_change(
    channel: str,
    *,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Apply a settings or toggle change at runtime.

    ``enabled``:
      * ``None``  -- settings.json changed; ensure daemon running if configured
      * ``True``  -- toggle on; start (or restart if active)
      * ``False`` -- toggle off; stop
    """
    sup = _supervisor_toggle(channel, False if enabled is False else True)
    if sup.get("applied"):
        return sup

    if enabled is False:
        return _systemctl_action(channel, "stop")

    if _unit_installed(channel):
        # Use systemctl directly — bridge.sh up skips channels without credentials
        # (e.g. WhatsApp before pairing) and would disable + stop the unit.
        _ensure_npm_modules(channel)
        action = "restart" if _unit_active(channel) else "start"
        return _systemctl_action(channel, action)
    # Unit not installed yet: first-time setup via bridge.sh up.
    return _run_bridge_sh_up()


def _resolve_npm() -> str:
    """Locate npm, preferring the nvm-managed binary (mirrors bridge.sh logic)."""
    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if nvm_dir.is_dir():
        try:
            latest = sorted(nvm_dir.iterdir(), key=lambda p: p.name)[-1]
            npm = latest / "bin" / "npm"
            if npm.is_file():
                return str(npm)
        except (IndexError, OSError):
            pass
    import shutil
    return shutil.which("npm") or "npm"


def _ensure_npm_modules(channel: str) -> None:
    """Run npm install in the channel dir if node_modules is absent.

    Called before starting JS-based bridges so that bridge.sh up's
    credential-gate (which skips npm install for unconfigured channels
    like WhatsApp before pairing) cannot leave the daemon unbootable.
    """
    chan_dir = _BRIDGES_DIR / channel
    if not (chan_dir / "package.json").exists():
        return
    if (chan_dir / "node_modules").exists():
        return
    try:
        subprocess.run(
            [_resolve_npm(), "install", "--prefer-offline", "--no-audit", "--no-fund"],
            cwd=chan_dir, capture_output=True, text=True, timeout=300, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


# ── Helpers ───────────────────────────────────────────────────────────


def _settings_path(channel: str) -> Path:
    """Canonical settings location (ADR-0008 §8.3): <corvin_home>/bridges/
    <channel>/settings.json — the SAME file every daemon resolves via
    bridgeSettingsPath(). This route historically wrote the source/vendored
    channel dir instead; on a wheel install the daemons run from the runtime
    dir and materialisation deliberately skips settings.json, so a token
    saved in the UI never reached the daemon ("DISCORD_TOKEN not set" on a
    fresh install). Writer and reader now agree on the runtime path."""
    if channel not in CHANNELS:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"unknown channel: {channel!r}",
        )
    return _corvin_home() / "bridges" / channel / "settings.json"


def _legacy_settings_path(channel: str) -> Path:
    """Pre-fix location (source tree / _vendor). Read-fallback only, so
    values written by older console versions stay visible and migrate to
    the runtime path on the next PUT."""
    return _BRIDGES_DIR / channel / "settings.json"


def _read_settings(channel: str) -> dict[str, Any]:
    path = _settings_path(channel)
    if path.exists():
        return _read_json(path)
    return _read_json(_legacy_settings_path(channel))


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return any(hint in k for hint in _SECRET_KEY_HINTS)


def _mask(value: Any) -> Any:
    """Mask a secret value for GET responses."""
    if value is None:
        return None
    if isinstance(value, bool):
        # PIN-type booleans pass through.
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if not value:
            return ""
        last4 = value[-4:] if len(value) >= 8 else ""
        return f"{_MASKED_PREFIX}{last4}"
    # Lists/dicts: deep-mask only string leaves.
    if isinstance(value, list):
        return [_mask(v) for v in value]
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    return value


def _mask_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if _is_secret_key(k):
            out[k] = _mask(v)
        else:
            out[k] = v
    return out


def _is_masked(value: Any) -> bool:
    """Detect whether a PUT-side value still carries our masking marker."""
    if isinstance(value, str):
        return value.startswith(_MASKED_PREFIX)
    return False


def _merge_preserving_secrets(
    new_payload: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Merge new payload with existing, preserving both secrets and
    non-secret fields that weren't explicitly changed.

    When the user edits settings:
    - Secrets sent as masked values (****…) are restored from existing
    - Non-secret fields not in new payload are preserved from existing
    - Explicitly provided new values override everything

    Only top-level secret keys are special-cased. Nested dicts inside
    secret-keys are NOT walked — operators editing nested webhook
    objects must provide cleartext or omit the field.
    """
    # Start with all existing fields
    merged = dict(existing)
    # Override with new payload, restoring masked secrets from existing
    for k, v in new_payload.items():
        if _is_secret_key(k) and _is_masked(v) and k in existing:
            # Secret was masked in the request → restore from existing
            merged[k] = existing[k]
        else:
            # New value (secret cleartext or non-secret) → use it
            merged[k] = v
    return merged


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="read settings failed",
        ) from e
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="settings malformed",
        ) from e
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="settings top-level must be an object",
        )
    return data


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(raw.encode("utf-8")) > _MAX_SETTINGS_BYTES:
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"settings exceed {_MAX_SETTINGS_BYTES} bytes",
        )
    if path.exists():
        # Rotate previous → .bak (best-effort; failure is non-fatal).
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
    # tmp → fsync → rename, then chmod 0600.
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="write settings failed",
        ) from e
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ── Routes ────────────────────────────────────────────────────────────


@router.get("/bridges")
def list_bridges(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List known channels with on-disk configuration + enabled state."""
    state = _read_state()
    items = []
    for channel in CHANNELS:
        # Report the RUNTIME path — that is where writes land and where every
        # daemon reads. The legacy path stays a read-fallback only.
        path = _settings_path(channel)
        legacy = _legacy_settings_path(channel)
        present = path.exists() or legacy.exists()
        items.append({
            "channel":    channel,
            # Credential-based, not file-based: see _channel_connected.
            "configured": _channel_connected(channel),
            "has_settings": present,
            "enabled":    _channel_enabled(channel, state),
            "path":       str(path),
            "size_bytes": path.stat().st_size if path.exists() else 0,
        })
    return {"count": len(items), "bridges": items}


@router.get("/bridges/{channel}/settings")
def get_bridge_settings(
    channel: str,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return masked settings.json for ``channel``."""
    path = _settings_path(channel)
    payload = _read_settings(channel)
    return {
        "channel":  channel,
        "path":     str(path),
        "exists":   path.exists() or _legacy_settings_path(channel).exists(),
        "settings": _mask_payload(payload),
    }


class BridgeSettingsUpdate(BaseModel):
    settings:      dict[str, Any] = Field(..., description="full settings object")
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}


# Pydantic-validated channel slug — defensive in addition to the
# CHANNELS allowlist.
_CHANNEL_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


@router.put("/bridges/{channel}/settings")
def put_bridge_settings(
    channel: str,
    body: BridgeSettingsUpdate,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Replace the entire settings.json for ``channel``.

    Masked secret values are restored from the existing file so the UI
    can round-trip without ever holding cleartext.
    """
    if not _CHANNEL_RE.match(channel):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid channel slug",
        )
    path = _settings_path(channel)
    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="bridge.settings.write",
            target_kind="bridge",
            target_id=channel,
            reason="reauth-failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="re-auth failed",
        )

    # Merge base includes the legacy source-tree file so secrets stored there
    # by older console versions survive the move to the runtime path.
    existing = _read_settings(channel)
    merged = _merge_preserving_secrets(body.settings, existing)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, merged)

    runtime = _apply_runtime_change(channel, enabled=None)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="bridge.settings.write",
        target_kind="bridge",
        target_id=channel,
    )
    return {
        "channel":        channel,
        "path":           str(path),
        "runtime":        runtime,
        "restart_needed": not runtime.get("applied", False),
        "ok":             True,
    }


class BridgeEnabledUpdate(BaseModel):
    enabled:       bool
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}


@router.put("/bridges/{channel}/enabled")
def put_bridge_enabled(
    channel: str,
    body: BridgeEnabledUpdate,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Toggle a bridge enabled/disabled.

    Persists the state in ``<corvin_home>/bridges/state.json`` and
    tries to apply at runtime via ``supervisorctl start|stop
    bridge-<channel>``. If supervisorctl is unavailable the state-file
    write still takes effect on next daemon start.
    """
    if not _CHANNEL_RE.match(channel) or channel not in CHANNELS:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid channel slug",
        )
    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="bridge.enabled.write",
            target_kind="bridge",
            target_id=channel,
            reason="reauth-failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="re-auth failed",
        )

    state = _read_state()
    channels = state.setdefault("channels", {})
    entry = channels.setdefault(channel, {})
    entry["enabled"] = body.enabled
    _write_state(state)

    runtime = _apply_runtime_change(channel, enabled=body.enabled)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="bridge.enabled.write",
        target_kind="bridge",
        target_id=channel,
    )
    return {
        "channel":        channel,
        "enabled":        body.enabled,
        "runtime":        runtime,
        "restart_needed": not runtime.get("applied", False),
        "ok":             True,
    }


# ── Discord Zero-Config Setup ─────────────────────────────────────────
#
# Endpoints for token validation and OAuth2 URL generation.
# Phase 2 of Discord Zero-Config: Console UI integration.

class ValidateTokenRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=200, description="Discord bot token")


class ValidateTokenResponse(BaseModel):
    valid: bool
    appId: str | None = None
    appName: str | None = None
    url: str | None = None
    error: str | None = None
    permissionsHuman: list[str] | None = None


class SaveTokenRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=200, description="Discord bot token")


class SaveTokenResponse(BaseModel):
    success: bool
    error: str | None = None


# ── Disconnect / delete a channel connection ──────────────────────────
#
# Until now a channel could be created (save-token / OAuth exchange / QR
# pairing) and toggled off, but never *un*-connected: the credential stayed on
# disk forever and re-running the setup wizard was the only way to replace it.
# There was no way to hand a bridge to a different bot/account, or to revoke a
# leaked token from the UI.
#
# Two levels, because they answer different questions:
#
#   disconnect  -- "give me a fresh connection, keep my configuration"
#                  Strips credentials and link state; keeps whitelist, pin,
#                  rate limits, chat_profiles, language, routing.
#   delete      -- "remove this channel entirely"
#                  Removes settings.json outright (a .bak is kept).
#
# Both stop the daemon FIRST. Ordering is load-bearing: a running daemon
# hot-reloads settings.json and several of them re-persist credentials (e.g.
# WhatsApp's saveCreds), so cleaning the file under a live daemon can be undone
# a second later.
#
# Both also clean BOTH storage locations. The zero-config endpoints above write
# via _resolve_bridges_dir() (source/_vendor tree) while _settings_path() is the
# runtime path, and _read_settings() falls back from runtime to legacy — so
# clearing only one of them leaves a working credential behind and the channel
# silently stays connected.

# Non-secret keys that still identify a *connection* and must go when the user
# asks for a fresh one. Secret-shaped keys (token/password/api_key/...) are
# swept generically via _SECRET_KEY_HINTS, so this list only needs the ones a
# name-based match would miss.
_CONNECTION_KEYS: dict[str, tuple[str, ...]] = {
    "discord":  (),
    "telegram": (),
    "slack":    ("team_id",),
    "teams":    ("user_email",),
    "email":    ("imap_user", "smtp_user", "from_address"),
    "signal":   ("signal_linked", "_pending_device_id", "_device_link_generated_at"),
    "whatsapp": ("whatsapp_linked", "_pending_session_id", "_qr_generated_at"),
}

# Written by every setup path; never a user preference.
_CONNECTION_KEYS_COMMON = ("_token_validated_at",)

# Secret-SHAPED keys that are nonetheless user preferences, not credentials for
# the upstream service. ``pin`` is the bridge access PIN the operator chooses;
# it matches _SECRET_KEY_HINTS (masking it on GET is right) but it must survive
# a disconnect and must never count as proof that a channel is connected.
_PREFERENCE_SECRET_KEYS = frozenset({"pin"})


def _is_credential_key(key: str) -> bool:
    """True for keys that carry (or directly identify) a credential.

    Name-based on purpose: it stays correct when a channel gains a new secret
    field, which an explicit allowlist would not.
    """
    low = key.lower()
    if low in _PREFERENCE_SECRET_KEYS:
        return False
    return any(hint in low for hint in _SECRET_KEY_HINTS)


# Per-channel flags that prove a pairing exists without any secret in the file.
_LINK_FLAGS = {"signal": "signal_linked", "whatsapp": "whatsapp_linked"}


def _channel_connected(channel: str) -> bool:
    """True iff the channel actually holds a usable connection.

    Deliberately NOT ``settings.json exists``: a channel can have a settings
    file holding nothing but preferences (whitelist, PIN, rate limit) and still
    have no credential at all — on this very install ``telegram`` is exactly
    that, and the tile claimed "configured" for a bridge that cannot start.
    After a disconnect the same would be true for every channel, so the tile
    has to key off the credential, not the file.
    """
    settings = _read_settings(channel)
    flag = _LINK_FLAGS.get(channel)
    if flag and settings.get(flag):
        return True
    for key, value in settings.items():
        if not _is_credential_key(key):
            continue
        if isinstance(value, str):
            # Whitespace-only is not a credential. Must return here rather than
            # fall through: the generic emptiness check below would treat "   "
            # as truthy and report a blank token as a live connection.
            if value.strip():
                return True
            continue
        if value not in (None, False, [], {}):
            return True
    return False


def _credential_keys_for(channel: str, settings: dict[str, Any]) -> list[str]:
    """Keys to strip from ``settings`` for a disconnect of ``channel``."""
    explicit = set(_CONNECTION_KEYS.get(channel, ())) | set(_CONNECTION_KEYS_COMMON)
    return sorted(k for k in settings if k in explicit or _is_credential_key(k))


def _link_state_dirs(channel: str) -> list[Path]:
    """On-disk pairing state that lives outside settings.json.

    WhatsApp (Baileys ``useMultiFileAuthState``) keeps its linked-device
    credentials in ``<channel>/auth/``. Leaving that behind means the daemon
    silently re-attaches to the old account after a "disconnect", so it has to
    go with the credential.
    """
    dirs: list[Path] = []
    if channel == "whatsapp":
        for base in (_BRIDGES_DIR, _corvin_home() / "bridges"):
            d = base / "whatsapp" / "auth"
            if d.is_dir():
                dirs.append(d)
    return dirs


def _archive_dir(path: Path) -> str | None:
    """Move ``path`` aside with a timestamp instead of deleting outright.

    Mirrors the existing ``auth.bak.<stamp>`` convention in the whatsapp dir, so
    a mis-click stays recoverable. Returns the new name, or None on failure.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak.{stamp}")
    try:
        path.rename(dest)
        return str(dest)
    except OSError:
        _log.warning("could not archive %s", path)
        return None


class BridgeDisconnectRequest(BaseModel):
    mode:          str = Field("disconnect", pattern="^(disconnect|delete)$")
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}


@router.post("/bridges/{channel}/disconnect")
def post_bridge_disconnect(
    channel: str,
    body: BridgeDisconnectRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Drop a channel's connection so it can be set up again.

    ``mode="disconnect"`` keeps preferences and removes only credentials and
    pairing state. ``mode="delete"`` removes settings.json entirely. Both stop
    the daemon and mark the channel disabled.
    """
    if not _CHANNEL_RE.match(channel) or channel not in CHANNELS:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid channel slug",
        )
    action = f"bridge.connection.{body.mode}"
    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action=action,
            target_kind="bridge",
            target_id=channel,
            reason="reauth-failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="re-auth failed",
        )

    # 1. Stop the daemon BEFORE touching disk — a live daemon re-persists
    #    credentials and would undo the cleanup moments later.
    runtime = _apply_runtime_change(channel, enabled=False)

    # 2. Mark disabled so a restart cannot bring the channel back up against a
    #    half-removed connection.
    state = _read_state()
    state.setdefault("channels", {}).setdefault(channel, {})["enabled"] = False
    _write_state(state)

    # 3. Clean BOTH storage locations.
    cleared: list[str] = []
    removed_files: list[str] = []
    for path in (_settings_path(channel), _legacy_settings_path(channel)):
        if not path.exists():
            continue
        if body.mode == "delete":
            try:
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            except OSError:
                pass
            try:
                path.unlink()
                removed_files.append(str(path))
            except OSError:
                _log.warning("could not remove %s", path)
            continue
        current = _read_json(path)
        drop = _credential_keys_for(channel, current)
        if not drop:
            continue
        for key in drop:
            current.pop(key, None)
        _write_atomic(path, current)
        cleared.extend(k for k in drop if k not in cleared)

    # 4. Pairing state outside settings.json (WhatsApp linked device).
    archived = [a for d in _link_state_dirs(channel) if (a := _archive_dir(d))]

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=action,
        target_kind="bridge",
        target_id=channel,
    )
    return {
        "channel":        channel,
        "mode":           body.mode,
        # Key NAMES only — never their values.
        "cleared_keys":   cleared,
        "removed_files":  removed_files,
        "archived_state": archived,
        "runtime":        runtime,
        "restart_needed": not runtime.get("applied", False),
        "ok":             True,
    }


def _validate_discord_token_via_node(token: str, bridges_dir: Path) -> dict[str, Any]:
    """Internal helper: validate Discord token via Node.js subprocess.

    Returns: {valid: bool, appId?: str, appName?: str, url?: str, error?: str}

    Security: Token passed via environment variable (not script interpolation).
    """
    auto_oauth2_path = bridges_dir / "discord" / "auto_oauth2.js"

    if not auto_oauth2_path.exists():
        return {"valid": False, "error": f"auto_oauth2.js not found at {auto_oauth2_path}"}

    # Use Node.js inline script (safe structure, token via env)
    script = """
const { AutoOAuth2Generator } = require(process.env.AUTO_OAUTH2_PATH);
const gen = new AutoOAuth2Generator({log: () => {}});
gen.generateAuthorizationUrl(process.env.DISCORD_TOKEN).then(result => {
  console.log(JSON.stringify(result));
}).catch(err => {
  console.log(JSON.stringify({valid: false, error: err.message}));
});
"""

    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(bridges_dir / "discord"),
            env={
                **os.environ,
                "AUTO_OAUTH2_PATH": str(auto_oauth2_path),
                "DISCORD_TOKEN": token,  # ← Token via env, not interpolation
            },
        )

        if result.returncode != 0:
            _log.warning(f"Node.js validation failed: {result.stderr}")
            return {"valid": False, "error": "Token validation service error"}

        try:
            response_data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            _log.error(f"Invalid JSON from auto_oauth2: {result.stdout}")
            return {"valid": False, "error": "Validation response error"}

        return response_data

    except subprocess.TimeoutExpired:
        return {"valid": False, "error": "Validation timeout (>10s)"}
    except Exception as e:
        _log.error(f"Token validation error: {e}")
        return {"valid": False, "error": str(e)[:100]}


@router.post("/discord/validate-token", response_model=ValidateTokenResponse)
async def validate_discord_token(
    body: ValidateTokenRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> ValidateTokenResponse:
    """Validate a Discord bot token and generate OAuth2 URL.

    Calls AutoOAuth2Generator (Node.js) to verify token is valid via Discord API
    and generate the OAuth2 authorization URL for the user.
    """
    _log.info(f"Validating Discord token (user: {rec.user_id})")

    bridges_dir = _resolve_bridges_dir()
    response_data = _validate_discord_token_via_node(body.token, bridges_dir)

    if not response_data.get("valid"):
        return ValidateTokenResponse(
            valid=False,
            error=response_data.get("error", "Token validation failed"),
        )

    return ValidateTokenResponse(
        valid=True,
        appId=response_data.get("appId"),
        appName=response_data.get("appName"),
        url=response_data.get("url"),
        permissionsHuman=response_data.get("permissionsHuman"),
    )


@router.post("/discord/save-token", response_model=SaveTokenResponse)
async def save_discord_token(
    body: SaveTokenRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> SaveTokenResponse:
    """Save Discord bot token to settings.json (atomic write + validation).

    1. Validate token via Discord API (secure: env var, not interpolation)
    2. Load current settings
    3. Write to temp file (prevent partial corruption)
    4. Atomic rename (POSIX move)
    5. Set 0600 permissions
    """
    _log.info(f"Saving Discord token (user: {rec.user_id})")

    try:
        bridges_dir = _resolve_bridges_dir()
        discord_settings_file = bridges_dir / "discord" / "settings.json"

        # Validate token first (reuse secure helper)
        validation = _validate_discord_token_via_node(body.token, bridges_dir)
        if not validation.get("valid"):
            return SaveTokenResponse(
                success=False,
                error=validation.get("error", "Token validation failed"),
            )

        # Load current settings
        if discord_settings_file.exists():
            with open(discord_settings_file, "r") as f:
                settings = json.load(f)
        else:
            settings = {}

        # Update token
        settings["discord_token"] = body.token
        settings["_token_validated_at"] = "via-console"

        # Atomic write: tmp file → rename (prevents partial corruption on crash)
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=discord_settings_file.parent,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            json.dump(settings, tmp, indent=2)
            tmp_path = tmp.name

        try:
            # Atomic rename on POSIX
            tmp_path_obj = Path(tmp_path)
            tmp_path_obj.replace(discord_settings_file)

            # Set secure permissions
            discord_settings_file.chmod(0o600)

            _log.info(f"Discord token saved successfully (app: {validation.get('appName')})")
            console_audit.action_performed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="discord.token.write",
                target_kind="bridge",
                target_id="discord",
            )

            return SaveTokenResponse(success=True)
        except Exception as e:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
            raise e

    except Exception as e:
        _log.error(f"Token save error: {e}")
        return SaveTokenResponse(
            success=False,
            error=f"Failed to save token: {str(e)[:100]}",
        )


# ── Telegram Zero-Config Setup ────────────────────────────────────────
#
# Same pattern as Discord (Category A template):
# Validate token via Telegram API → Save to settings.json

class ValidateTelegramTokenRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=200, description="Telegram bot token")


class ValidateTelegramTokenResponse(BaseModel):
    valid: bool
    botId: str | None = None
    botUsername: str | None = None
    botName: str | None = None
    error: str | None = None


class SaveTelegramTokenRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=200, description="Telegram bot token")


class SaveTelegramTokenResponse(BaseModel):
    success: bool
    error: str | None = None


def _validate_telegram_token_via_node(token: str, bridges_dir: Path) -> dict[str, Any]:
    """Internal helper: validate Telegram token via Node.js subprocess.

    Returns: {
      valid: bool, botId?: str, botUsername?: str, botName?: str, error?: str
    }

    Security: Token passed via environment variable (not script interpolation).
    """
    provisioner_path = bridges_dir / "telegram" / "auto_telegram_provisioner.js"

    if not provisioner_path.exists():
        return {"valid": False, "error": f"auto_telegram_provisioner.js not found"}

    script = """
const { AutoTelegramTokenProvisioner } = require(process.env.PROVISIONER_PATH);
const prov = new AutoTelegramTokenProvisioner({log: () => {}});
prov.validateAndProvision(process.env.TELEGRAM_TOKEN).then(result => {
  console.log(JSON.stringify(result));
}).catch(err => {
  console.log(JSON.stringify({valid: false, error: err.message}));
});
"""

    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(bridges_dir / "telegram"),
            env={
                **os.environ,
                "PROVISIONER_PATH": str(provisioner_path),
                "TELEGRAM_TOKEN": token,
            },
        )

        if result.returncode != 0:
            _log.warning(f"Node.js validation failed: {result.stderr}")
            return {"valid": False, "error": "Token validation service error"}

        try:
            response_data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            _log.error(f"Invalid JSON from provisioner: {result.stdout}")
            return {"valid": False, "error": "Validation response error"}

        return response_data

    except subprocess.TimeoutExpired:
        return {"valid": False, "error": "Validation timeout (>10s)"}
    except Exception as e:
        _log.error(f"Token validation error: {e}")
        return {"valid": False, "error": str(e)[:100]}


@router.post("/telegram/validate-token", response_model=ValidateTelegramTokenResponse)
async def validate_telegram_token(
    body: ValidateTelegramTokenRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> ValidateTelegramTokenResponse:
    """Validate a Telegram bot token and extract bot info."""
    _log.info(f"Validating Telegram token (user: {rec.user_id})")

    bridges_dir = _resolve_bridges_dir()
    response_data = _validate_telegram_token_via_node(body.token, bridges_dir)

    if not response_data.get("valid"):
        return ValidateTelegramTokenResponse(
            valid=False,
            error=response_data.get("error", "Token validation failed"),
        )

    return ValidateTelegramTokenResponse(
        valid=True,
        botId=response_data.get("botId"),
        botUsername=response_data.get("botUsername"),
        botName=response_data.get("botName"),
    )


@router.post("/telegram/save-token", response_model=SaveTelegramTokenResponse)
async def save_telegram_token(
    body: SaveTelegramTokenRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> SaveTelegramTokenResponse:
    """Save Telegram bot token to settings.json (atomic write + validation)."""
    _log.info(f"Saving Telegram token (user: {rec.user_id})")

    try:
        bridges_dir = _resolve_bridges_dir()
        telegram_settings_file = bridges_dir / "telegram" / "settings.json"

        # Validate first
        validation = _validate_telegram_token_via_node(body.token, bridges_dir)
        if not validation.get("valid"):
            return SaveTelegramTokenResponse(
                success=False,
                error=validation.get("error", "Token validation failed"),
            )

        # Load current settings
        if telegram_settings_file.exists():
            with open(telegram_settings_file, "r") as f:
                settings = json.load(f)
        else:
            settings = {}

        # Update token
        settings["telegram_token"] = body.token
        settings["_token_validated_at"] = "via-console"

        # Atomic write: temp file → rename
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=telegram_settings_file.parent,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            json.dump(settings, tmp, indent=2)
            tmp_path = tmp.name

        try:
            tmp_path_obj = Path(tmp_path)
            tmp_path_obj.replace(telegram_settings_file)
            telegram_settings_file.chmod(0o600)

            _log.info(f"Telegram token saved (bot: @{validation.get('botUsername')})")
            console_audit.action_performed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="telegram.token.write",
                target_kind="bridge",
                target_id="telegram",
            )

            return SaveTelegramTokenResponse(success=True)
        except Exception as e:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
            raise e

    except Exception as e:
        _log.error(f"Token save error: {e}")
        return SaveTelegramTokenResponse(
            success=False,
            error=f"Failed to save token: {str(e)[:100]}",
        )


# ── Slack Zero-Config Setup (Category B: OAuth) ─────────────────────────────
#
# Pattern: OAuth authorization → token exchange → scope validation

class GenerateSlackOAuthURLRequest(BaseModel):
    client_id: str = Field(..., min_length=5)


class GenerateSlackOAuthURLResponse(BaseModel):
    url: str
    required_scopes: list[str]
    error: str | None = None


class ExchangeSlackCodeRequest(BaseModel):
    code: str = Field(..., min_length=5)


class ExchangeSlackCodeResponse(BaseModel):
    valid: bool
    access_token: str | None = None
    team_name: str | None = None
    error: str | None = None


@router.post("/slack/oauth/generate-url", response_model=GenerateSlackOAuthURLResponse)
async def generate_slack_oauth_url(
    body: GenerateSlackOAuthURLRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> GenerateSlackOAuthURLResponse:
    """Generate Slack OAuth authorization URL."""
    _log.info(f"Generating Slack OAuth URL (user: {rec.user_id})")

    bridges_dir = _resolve_bridges_dir()
    oauth_flow_path = bridges_dir / "slack" / "auto_slack_oauth_flow.js"

    if not oauth_flow_path.exists():
        return GenerateSlackOAuthURLResponse(
            url="",
            required_scopes=[],
            error="OAuth flow module not found",
        )

    script = f"""
const {{ AutoSlackOAuthFlow }} = require(process.env.OAUTH_FLOW_PATH);
const flow = new AutoSlackOAuthFlow({{log: () => {{}}}}, process.env.CLIENT_ID, '');
const result = flow.generateAuthorizationUrl();
console.log(JSON.stringify(result));
"""

    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            env={
                **os.environ,
                "OAUTH_FLOW_PATH": str(oauth_flow_path),
                "CLIENT_ID": body.client_id,
            },
        )

        if result.returncode != 0:
            return GenerateSlackOAuthURLResponse(
                url="",
                required_scopes=[],
                error="OAuth URL generation failed",
            )

        data = json.loads(result.stdout)
        return GenerateSlackOAuthURLResponse(
            url=data.get("url", ""),
            required_scopes=data.get("requiredScopes", []),
        )

    except Exception as e:
        _log.error(f"OAuth URL generation error: {e}")
        return GenerateSlackOAuthURLResponse(
            url="",
            required_scopes=[],
            error=str(e)[:100],
        )


@router.post("/slack/oauth/exchange-code", response_model=ExchangeSlackCodeResponse)
async def exchange_slack_code(
    body: ExchangeSlackCodeRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> ExchangeSlackCodeResponse:
    """Exchange OAuth code for access token."""
    _log.info(f"Exchanging Slack OAuth code (user: {rec.user_id})")

    bridges_dir = _resolve_bridges_dir()
    oauth_flow_path = bridges_dir / "slack" / "auto_slack_oauth_flow.js"
    slack_settings = bridges_dir / "slack" / "settings.json"

    # Load client_id from settings
    if not slack_settings.exists():
        return ExchangeSlackCodeResponse(
            valid=False,
            error="Slack settings not found",
        )

    try:
        # Pre-validate settings.json
        try:
            with open(slack_settings, "r") as f:
                settings = json.load(f)
        except json.JSONDecodeError as e:
            _log.error(f"Slack settings.json corrupted: {e}")
            return ExchangeSlackCodeResponse(
                valid=False,
                error="Settings file corrupted",
            )

        client_id = settings.get("client_id", "")
        client_secret = settings.get("client_secret", "")

        if not client_id:
            return ExchangeSlackCodeResponse(
                valid=False,
                error="client_id not configured",
            )

        # Step 1: Exchange code for token
        script_exchange = """
const { AutoSlackOAuthFlow, REQUIRED_SCOPES } = require(process.env.OAUTH_FLOW_PATH);
const flow = new AutoSlackOAuthFlow(
  (msg) => console.error(msg),
  process.env.CLIENT_ID,
  process.env.CLIENT_SECRET
);
(async () => {
  const result = await flow.exchangeCodeForToken(process.env.CODE);
  console.log(JSON.stringify(result));
})().catch(err => {
  console.log(JSON.stringify({valid: false, error: err.message}));
});
"""

        try:
            result = subprocess.run(
                ["node", "-e", script_exchange],
                capture_output=True,
                text=True,
                timeout=10,
                env={
                    **os.environ,
                    "OAUTH_FLOW_PATH": str(oauth_flow_path),
                    "CLIENT_ID": client_id,
                    "CLIENT_SECRET": client_secret,
                    "CODE": body.code,
                },
            )
        except subprocess.TimeoutExpired:
            _log.error("Slack OAuth exchange timeout (>10s)")
            return ExchangeSlackCodeResponse(
                valid=False,
                error="OAuth exchange timeout",
            )
        except FileNotFoundError:
            _log.error("Node.js not found or oauth_flow.js missing")
            return ExchangeSlackCodeResponse(
                valid=False,
                error="Setup error: OAuth module not found",
            )

        if result.returncode != 0:
            _log.error(f"Node.js exit {result.returncode}: {result.stderr[:200]}")
            return ExchangeSlackCodeResponse(
                valid=False,
                error="OAuth exchange failed",
            )

        # Parse token exchange result
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            _log.error(f"Failed to parse OAuth exchange response: {e}")
            return ExchangeSlackCodeResponse(
                valid=False,
                error="OAuth exchange response invalid",
            )

        if not data.get("valid"):
            return ExchangeSlackCodeResponse(
                valid=False,
                error=data.get("error", "Exchange failed"),
            )

        access_token = data.get("access_token")
        team_id = data.get("team_id")
        team_name = data.get("team_name")

        # Step 2: Validate scopes (CRITICAL)
        script_validate = """
const { AutoSlackOAuthFlow, REQUIRED_SCOPES } = require(process.env.OAUTH_FLOW_PATH);
const flow = new AutoSlackOAuthFlow((msg) => {}, "", "");
(async () => {
  const result = await flow.validateScopes(process.env.ACCESS_TOKEN);
  console.log(JSON.stringify(result));
})().catch(err => {
  console.log(JSON.stringify({valid: false, error: err.message}));
});
"""

        try:
            scope_result = subprocess.run(
                ["node", "-e", script_validate],
                capture_output=True,
                text=True,
                timeout=10,
                env={
                    **os.environ,
                    "OAUTH_FLOW_PATH": str(oauth_flow_path),
                    "ACCESS_TOKEN": access_token,
                },
            )
        except subprocess.TimeoutExpired:
            _log.warning("Scope validation timeout, continuing with caution")
            scope_result = None
        except Exception as e:
            _log.warning(f"Scope validation error, continuing: {e}")
            scope_result = None

        if scope_result and scope_result.returncode == 0:
            try:
                scope_data = json.loads(scope_result.stdout)
                if not scope_data.get("valid"):
                    _log.warning(f"Missing scopes: {scope_data.get('error')}")
                    return ExchangeSlackCodeResponse(
                        valid=False,
                        error=f"Permission error: {scope_data.get('error')}",
                    )
            except json.JSONDecodeError:
                _log.warning("Could not parse scope validation response")

        # Save token (atomic write)
        fd, tmp_path = tempfile.mkstemp(prefix=slack_settings.name + ".", dir=str(slack_settings.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                settings["slack_token"] = access_token
                settings["team_id"] = team_id
                settings["_token_validated_at"] = "via-console-oauth"
                json.dump(settings, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())

            # Atomic replace
            tmp_path_obj = Path(tmp_path)
            tmp_path_obj.replace(slack_settings)
            slack_settings.chmod(0o600)

            _log.info(f"Slack token saved (team: {team_name}, id: {team_id})")
            return ExchangeSlackCodeResponse(
                valid=True,
                access_token=access_token[:20] + "...",
                team_name=team_name,
            )
        except Exception as e:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            _log.error(f"Failed to save Slack token: {e}")
            raise

    except FileNotFoundError:
        _log.error(f"Slack settings not found: {slack_settings}")
        return ExchangeSlackCodeResponse(
            valid=False,
            error="Settings path error",
        )
    except Exception as e:
        _log.error(f"OAuth exchange error: {e}", exc_info=True)
        return ExchangeSlackCodeResponse(
            valid=False,
            error="Internal error",
        )


# ── Teams OAuth (Azure AD) ──────────────────────────────────────────────
# Category B (OAuth) pattern: Azure AD variant (tenant ID configurable)


class GenerateTeamsOAuthURLRequest(BaseModel):
    client_id: str = Field(..., description="Azure AD client ID")
    tenant_id: str = Field(default="common", description="Azure AD tenant ID (common for multi-tenant)")


class GenerateTeamsOAuthURLResponse(BaseModel):
    url: str = Field(default="", description="OAuth authorization URL")
    required_scopes: list[str] = Field(default_factory=list, description="Required scopes")
    error: str = Field(default="", description="Error message if any")


class ExchangeTeamsCodeRequest(BaseModel):
    code: str = Field(..., description="OAuth authorization code")


class ExchangeTeamsCodeResponse(BaseModel):
    valid: bool
    access_token: str = Field(default="", description="Masked access token")
    user_email: str = Field(default="", description="User email (principal name)")
    error: str = Field(default="", description="Error message if any")


@router.post("/teams/oauth/generate-url", response_model=GenerateTeamsOAuthURLResponse)
async def generate_teams_oauth_url(
    body: GenerateTeamsOAuthURLRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> GenerateTeamsOAuthURLResponse:
    """Generate Teams OAuth authorization URL."""
    _log.info(f"Generating Teams OAuth URL (user: {rec.user_id})")

    bridges_dir = _resolve_bridges_dir()
    oauth_flow_path = bridges_dir / "teams" / "auto_teams_oauth_flow.js"

    if not oauth_flow_path.exists():
        return GenerateTeamsOAuthURLResponse(
            url="",
            required_scopes=[],
            error="Teams OAuth module not found",
        )

    script = f"""
const {{ AutoTeamsOAuthFlow }} = require(process.env.OAUTH_FLOW_PATH);
const flow = new AutoTeamsOAuthFlow({{log: () => {{}}}}, process.env.CLIENT_ID, '', process.env.TENANT_ID);
const result = flow.generateAuthorizationUrl();
console.log(JSON.stringify(result));
"""

    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            env={
                **os.environ,
                "OAUTH_FLOW_PATH": str(oauth_flow_path),
                "CLIENT_ID": body.client_id,
                "TENANT_ID": body.tenant_id,
            },
        )

        if result.returncode != 0:
            return GenerateTeamsOAuthURLResponse(
                url="",
                required_scopes=[],
                error="OAuth URL generation failed",
            )

        data = json.loads(result.stdout)
        return GenerateTeamsOAuthURLResponse(
            url=data.get("url", ""),
            required_scopes=data.get("requiredScopes", []),
        )

    except subprocess.TimeoutExpired:
        return GenerateTeamsOAuthURLResponse(
            url="",
            required_scopes=[],
            error="Generation timeout",
        )
    except json.JSONDecodeError as e:
        _log.error(f"Teams OAuth URL generation response parse error: {e}")
        return GenerateTeamsOAuthURLResponse(
            url="",
            required_scopes=[],
            error="Response parse error",
        )
    except Exception as e:
        _log.error(f"Teams OAuth URL generation error: {e}")
        return GenerateTeamsOAuthURLResponse(
            url="",
            required_scopes=[],
            error="Internal error",
        )


@router.post("/teams/oauth/exchange-code", response_model=ExchangeTeamsCodeResponse)
async def exchange_teams_code(
    body: ExchangeTeamsCodeRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> ExchangeTeamsCodeResponse:
    """Exchange Teams OAuth code for access token."""
    _log.info(f"Exchanging Teams OAuth code (user: {rec.user_id})")

    bridges_dir = _resolve_bridges_dir()
    oauth_flow_path = bridges_dir / "teams" / "auto_teams_oauth_flow.js"
    teams_settings = bridges_dir / "teams" / "settings.json"

    # Load client_id, client_secret from settings
    if not teams_settings.exists():
        return ExchangeTeamsCodeResponse(
            valid=False,
            error="Teams settings not found",
        )

    try:
        # Pre-validate settings.json
        try:
            with open(teams_settings, "r") as f:
                settings = json.load(f)
        except json.JSONDecodeError as e:
            _log.error(f"Teams settings.json corrupted: {e}")
            return ExchangeTeamsCodeResponse(
                valid=False,
                error="Settings file corrupted",
            )

        client_id = settings.get("client_id", "")
        client_secret = settings.get("client_secret", "")
        tenant_id = settings.get("tenant_id", "common")

        if not client_id:
            return ExchangeTeamsCodeResponse(
                valid=False,
                error="client_id not configured",
            )

        # Exchange code for token
        script_exchange = """
const { AutoTeamsOAuthFlow } = require(process.env.OAUTH_FLOW_PATH);
const flow = new AutoTeamsOAuthFlow(
  (msg) => console.error(msg),
  process.env.CLIENT_ID,
  process.env.CLIENT_SECRET,
  process.env.TENANT_ID
);
(async () => {
  const result = await flow.exchangeCodeForToken(process.env.CODE);
  console.log(JSON.stringify(result));
})().catch(err => {
  console.log(JSON.stringify({valid: false, error: err.message}));
});
"""

        try:
            result = subprocess.run(
                ["node", "-e", script_exchange],
                capture_output=True,
                text=True,
                timeout=10,
                env={
                    **os.environ,
                    "OAUTH_FLOW_PATH": str(oauth_flow_path),
                    "CLIENT_ID": client_id,
                    "CLIENT_SECRET": client_secret,
                    "TENANT_ID": tenant_id,
                    "CODE": body.code,
                },
            )
        except subprocess.TimeoutExpired:
            _log.error("Teams OAuth exchange timeout (>10s)")
            return ExchangeTeamsCodeResponse(
                valid=False,
                error="OAuth exchange timeout",
            )
        except FileNotFoundError:
            _log.error("Node.js not found or oauth_flow.js missing")
            return ExchangeTeamsCodeResponse(
                valid=False,
                error="Setup error: OAuth module not found",
            )

        if result.returncode != 0:
            _log.error(f"Node.js exit {result.returncode}: {result.stderr[:200]}")
            return ExchangeTeamsCodeResponse(
                valid=False,
                error="OAuth exchange failed",
            )

        # Parse token exchange result
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            _log.error(f"Failed to parse Teams OAuth exchange response: {e}")
            return ExchangeTeamsCodeResponse(
                valid=False,
                error="OAuth exchange response invalid",
            )

        if not data.get("valid"):
            return ExchangeTeamsCodeResponse(
                valid=False,
                error=data.get("error", "Exchange failed"),
            )

        access_token = data.get("access_token")
        user_email = data.get("user_email", "")

        # Validate token via Microsoft Graph
        script_validate = """
const { AutoTeamsOAuthFlow } = require(process.env.OAUTH_FLOW_PATH);
const flow = new AutoTeamsOAuthFlow((msg) => {}, "", "", "");
(async () => {
  const result = await flow.validateToken(process.env.ACCESS_TOKEN);
  console.log(JSON.stringify(result));
})().catch(err => {
  console.log(JSON.stringify({valid: false, error: err.message}));
});
"""

        try:
            validate_result = subprocess.run(
                ["node", "-e", script_validate],
                capture_output=True,
                text=True,
                timeout=10,
                env={
                    **os.environ,
                    "OAUTH_FLOW_PATH": str(oauth_flow_path),
                    "ACCESS_TOKEN": access_token,
                },
            )
        except subprocess.TimeoutExpired:
            _log.warning("Token validation timeout, continuing with caution")
            validate_result = None
        except Exception as e:
            _log.warning(f"Token validation error, continuing: {e}")
            validate_result = None

        if validate_result and validate_result.returncode == 0:
            try:
                validate_data = json.loads(validate_result.stdout)
                if validate_data.get("valid"):
                    user_email = validate_data.get("user_email", user_email)
            except json.JSONDecodeError:
                _log.warning("Could not parse token validation response")

        # Save token (atomic write)
        fd, tmp_path = tempfile.mkstemp(prefix=teams_settings.name + ".", dir=str(teams_settings.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                settings["teams_token"] = access_token
                settings["user_email"] = user_email
                settings["_token_validated_at"] = "via-console-oauth"
                json.dump(settings, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())

            # Atomic replace
            tmp_path_obj = Path(tmp_path)
            tmp_path_obj.replace(teams_settings)
            teams_settings.chmod(0o600)

            _log.info(f"Teams token saved (user: {user_email})")
            return ExchangeTeamsCodeResponse(
                valid=True,
                access_token=access_token[:20] + "...",
                user_email=user_email,
            )
        except Exception as e:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            _log.error(f"Failed to save Teams token: {e}")
            raise

    except FileNotFoundError:
        _log.error(f"Teams settings not found: {teams_settings}")
        return ExchangeTeamsCodeResponse(
            valid=False,
            error="Settings path error",
        )
    except Exception as e:
        _log.error(f"Teams OAuth exchange error: {e}", exc_info=True)
        return ExchangeTeamsCodeResponse(
            valid=False,
            error="Internal error",
        )


# ── Email OAuth (Microsoft Graph + Gmail) ──────────────────────────────────


class EmailOAuthRequest(BaseModel):
    provider: str = Field(default="microsoft", description="microsoft or google")
    client_id: str
    redirect_uri: str = Field(default="http://localhost:8765/bridge-setup/callback")


class EmailOAuthResponse(BaseModel):
    url: str = Field(default="")
    provider: str = ""
    state: str = Field(default="")
    error: str = Field(default="")


class EmailCodeExchangeRequest(BaseModel):
    code: str
    state: str


class EmailCodeExchangeResponse(BaseModel):
    valid: bool
    access_token: str = Field(default="")
    email: str = Field(default="")
    error: str = Field(default="")


@router.post("/email/oauth/generate-url", response_model=EmailOAuthResponse)
async def generate_email_oauth_url(
    body: EmailOAuthRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> EmailOAuthResponse:
    """Generate Email OAuth URL (Microsoft Graph or Gmail)."""
    bridges_dir = _resolve_bridges_dir()
    oauth_flow_path = bridges_dir / "email" / "auto_email_oauth_flow.js"

    if not oauth_flow_path.exists():
        return EmailOAuthResponse(error="Email OAuth module not found")

    # Generate CSRF state token
    state = os.urandom(16).hex()

    script = f"""
const {{ AutoEmailOAuthFlow }} = require(process.env.OAUTH_FLOW_PATH);
const flow = new AutoEmailOAuthFlow(() => {{}}, process.env.CLIENT_ID, '', process.env.PROVIDER);
const result = flow.generateAuthorizationUrl(process.env.REDIRECT_URI);
result.state = process.env.STATE;
console.log(JSON.stringify(result));
"""

    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            env={
                **os.environ,
                "OAUTH_FLOW_PATH": str(oauth_flow_path),
                "CLIENT_ID": body.client_id,
                "PROVIDER": body.provider,
                "REDIRECT_URI": body.redirect_uri,
                "STATE": state,
            },
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return EmailOAuthResponse(
                url=data.get("url", ""),
                provider=body.provider,
                state=state,
            )
    except Exception as e:
        _log.error(f"Email OAuth error: {e}")
    return EmailOAuthResponse(error="Generation failed")


@router.post("/email/oauth/exchange-code", response_model=EmailCodeExchangeResponse)
async def exchange_email_code(
    body: EmailCodeExchangeRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> EmailCodeExchangeResponse:
    """Exchange Email OAuth code for access token."""
    bridges_dir = _resolve_bridges_dir()
    oauth_flow_path = bridges_dir / "email" / "auto_email_oauth_flow.js"
    email_settings = bridges_dir / "email" / "settings.json"

    if not email_settings.exists():
        return EmailCodeExchangeResponse(valid=False, error="Email settings not found")

    try:
        try:
            with open(email_settings, "r") as f:
                settings = json.load(f)
        except json.JSONDecodeError as e:
            _log.error(f"Email settings.json corrupted: {e}")
            return EmailCodeExchangeResponse(valid=False, error="Settings file corrupted")

        client_id = settings.get("client_id", "")
        client_secret = settings.get("client_secret", "")
        provider = settings.get("provider", "microsoft")
        redirect_uri = settings.get("redirect_uri", "http://localhost:8765/bridge-setup/callback")

        if not client_id or not client_secret:
            return EmailCodeExchangeResponse(valid=False, error="OAuth credentials not configured")

        script_exchange = """
const { AutoEmailOAuthFlow } = require(process.env.OAUTH_FLOW_PATH);
const flow = new AutoEmailOAuthFlow(
  (msg) => console.error(msg),
  process.env.CLIENT_ID,
  process.env.CLIENT_SECRET,
  process.env.PROVIDER
);
(async () => {
  const result = await flow.exchangeCodeForToken(process.env.CODE);
  console.log(JSON.stringify(result));
})().catch(err => {
  console.log(JSON.stringify({valid: false, error: err.message}));
});
"""

        try:
            result = subprocess.run(
                ["node", "-e", script_exchange],
                capture_output=True,
                text=True,
                timeout=10,
                env={
                    **os.environ,
                    "OAUTH_FLOW_PATH": str(oauth_flow_path),
                    "CLIENT_ID": client_id,
                    "CLIENT_SECRET": client_secret,
                    "PROVIDER": provider,
                    "CODE": body.code,
                },
            )
        except subprocess.TimeoutExpired:
            _log.error("Email OAuth exchange timeout (>10s)")
            return EmailCodeExchangeResponse(valid=False, error="OAuth exchange timeout")
        except FileNotFoundError:
            return EmailCodeExchangeResponse(valid=False, error="Setup error: OAuth module not found")

        if result.returncode != 0:
            _log.error(f"Node.js exit {result.returncode}: {result.stderr[:200]}")
            return EmailCodeExchangeResponse(valid=False, error="OAuth exchange failed")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            _log.error(f"Failed to parse Email OAuth exchange response: {e}")
            return EmailCodeExchangeResponse(valid=False, error="OAuth exchange response invalid")

        if not data.get("valid"):
            return EmailCodeExchangeResponse(valid=False, error=data.get("error", "Exchange failed"))

        access_token = data.get("access_token")

        # Validate token
        script_validate = """
const { AutoEmailOAuthFlow } = require(process.env.OAUTH_FLOW_PATH);
const flow = new AutoEmailOAuthFlow((msg) => {}, "", "", process.env.PROVIDER);
(async () => {
  const result = await flow.validateToken(process.env.ACCESS_TOKEN);
  console.log(JSON.stringify(result));
})().catch(err => {
  console.log(JSON.stringify({valid: false, error: err.message}));
});
"""

        try:
            validate_result = subprocess.run(
                ["node", "-e", script_validate],
                capture_output=True,
                text=True,
                timeout=10,
                env={
                    **os.environ,
                    "OAUTH_FLOW_PATH": str(oauth_flow_path),
                    "PROVIDER": provider,
                    "ACCESS_TOKEN": access_token,
                },
            )
            if validate_result.returncode == 0:
                validate_data = json.loads(validate_result.stdout)
                if not validate_data.get("valid"):
                    return EmailCodeExchangeResponse(valid=False, error=f"Token validation failed: {validate_data.get('error')}")
        except Exception as e:
            _log.warning(f"Email token validation error, continuing: {e}")

        # Save token (atomic write)
        fd, tmp_path = tempfile.mkstemp(prefix=email_settings.name + ".", dir=str(email_settings.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                settings["email_token"] = access_token
                settings["_token_validated_at"] = "via-console-oauth"
                json.dump(settings, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())

            tmp_path_obj = Path(tmp_path)
            tmp_path_obj.replace(email_settings)
            email_settings.chmod(0o600)

            _log.info(f"Email token saved (provider: {provider})")
            return EmailCodeExchangeResponse(
                valid=True,
                access_token=access_token[:20] + "...",
                email="configured",
            )
        except Exception as e:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            _log.error(f"Failed to save Email token: {e}")
            raise

    except FileNotFoundError:
        _log.error(f"Email settings not found: {email_settings}")
        return EmailCodeExchangeResponse(valid=False, error="Settings path error")
    except Exception as e:
        _log.error(f"Email OAuth exchange error: {e}", exc_info=True)
        return EmailCodeExchangeResponse(valid=False, error="Internal error")


# ── Signal QR Linking ──────────────────────────────────────────────────────


class SignalQRResponse(BaseModel):
    qr_data: str = Field(default="")
    session_id: str = Field(default="")
    error: str = Field(default="")


@router.get("/signal/generate-qr", response_model=SignalQRResponse)
async def generate_signal_qr(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> SignalQRResponse:
    """Generate Signal device linking QR code (user scans to link)."""
    bridges_dir = _resolve_bridges_dir()
    provisioner_path = bridges_dir / "signal" / "auto_signal_provisioner.js"
    signal_settings = bridges_dir / "signal" / "settings.json"

    if not provisioner_path.exists():
        return SignalQRResponse(error="Signal provisioner not found")

    script = f"""
const {{ AutoSignalProvisioner }} = require(process.env.PROV_PATH);
const prov = new AutoSignalProvisioner(() => {{}});
(async () => {{ const qr = await prov.generateDeviceLinkQR(); console.log(JSON.stringify(qr)); }})();
"""

    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "PROV_PATH": str(provisioner_path)},
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            device_id = data.get("device_id", "")

            # Save device_id to settings for polling
            signal_settings.parent.mkdir(parents=True, exist_ok=True)
            settings = {}
            if signal_settings.exists():
                try:
                    settings = json.loads(signal_settings.read_text())
                except json.JSONDecodeError:
                    pass

            settings["_pending_device_id"] = device_id
            settings["_device_link_generated_at"] = str(int(time.time()))
            signal_settings.write_text(json.dumps(settings, indent=2))

            return SignalQRResponse(
                qr_data=data.get("qr_data", ""),
                session_id=device_id,
            )
    except Exception as e:
        _log.error(f"Signal QR error: {e}")
    return SignalQRResponse(error="QR generation failed")


class SignalPollResponse(BaseModel):
    linked: bool = False
    error: str = Field(default="")


@router.post("/signal/poll-link", response_model=SignalPollResponse)
async def poll_signal_link(
    body: BaseModel,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> SignalPollResponse:
    """Poll for Signal device linking completion (call repeatedly after generating QR)."""
    bridges_dir = _resolve_bridges_dir()
    provisioner_path = bridges_dir / "signal" / "auto_signal_provisioner.js"
    signal_settings = bridges_dir / "signal" / "settings.json"

    if not signal_settings.exists():
        return SignalPollResponse(error="No pending link found")

    try:
        settings = json.loads(signal_settings.read_text())
        device_id = settings.get("_pending_device_id")
        generated_at = int(settings.get("_device_link_generated_at", 0))
        elapsed = int(time.time()) - generated_at

        if elapsed > 300:  # 5 minute timeout
            del settings["_pending_device_id"]
            signal_settings.write_text(json.dumps(settings, indent=2))
            return SignalPollResponse(error="QR code expired (>5min)")

        # Validate device link
        script = f"""
const {{ AutoSignalProvisioner }} = require(process.env.PROV_PATH);
const prov = new AutoSignalProvisioner(() => {{}});
(async () => {{ const result = await prov.validateDeviceLink(process.env.DEVICE_ID); console.log(JSON.stringify(result)); }})();
"""

        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "PROV_PATH": str(provisioner_path), "DEVICE_ID": device_id or ""},
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get("valid"):
                del settings["_pending_device_id"]
                settings["signal_linked"] = True
                signal_settings.write_text(json.dumps(settings, indent=2))
                return SignalPollResponse(linked=True)

        return SignalPollResponse(linked=False)
    except Exception as e:
        _log.error(f"Signal link polling error: {e}")
        return SignalPollResponse(error="Polling failed")


# ── WhatsApp QR Linking ────────────────────────────────────────────────────


class WhatsAppQRResponse(BaseModel):
    qr_data: str = Field(default="")
    session_id: str = Field(default="")
    error: str = Field(default="")


@router.get("/whatsapp/generate-qr", response_model=WhatsAppQRResponse)
async def generate_whatsapp_qr(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> WhatsAppQRResponse:
    """Generate WhatsApp Web QR code (user scans to link)."""
    bridges_dir = _resolve_bridges_dir()
    provisioner_path = bridges_dir / "whatsapp" / "auto_whatsapp_provisioner.js"
    whatsapp_settings = bridges_dir / "whatsapp" / "settings.json"

    if not provisioner_path.exists():
        return WhatsAppQRResponse(error="WhatsApp provisioner not found")

    script = f"""
const {{ AutoWhatsAppProvisioner }} = require(process.env.PROV_PATH);
const prov = new AutoWhatsAppProvisioner(() => {{}});
(async () => {{ const qr = await prov.generateQRCode(); console.log(JSON.stringify(qr)); }})();
"""

    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "PROV_PATH": str(provisioner_path)},
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            session_id = data.get("session_id", "")

            # Save session_id to settings for polling
            whatsapp_settings.parent.mkdir(parents=True, exist_ok=True)
            settings = {}
            if whatsapp_settings.exists():
                try:
                    settings = json.loads(whatsapp_settings.read_text())
                except json.JSONDecodeError:
                    pass

            settings["_pending_session_id"] = session_id
            settings["_qr_generated_at"] = str(int(time.time()))
            whatsapp_settings.write_text(json.dumps(settings, indent=2))

            return WhatsAppQRResponse(
                qr_data=data.get("qr_data", ""),
                session_id=session_id,
            )
    except Exception as e:
        _log.error(f"WhatsApp QR error: {e}")
    return WhatsAppQRResponse(error="QR generation failed")


class WhatsAppPollResponse(BaseModel):
    linked: bool = False
    error: str = Field(default="")


@router.post("/whatsapp/poll-scan", response_model=WhatsAppPollResponse)
async def poll_whatsapp_scan(
    body: BaseModel,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> WhatsAppPollResponse:
    """Poll for WhatsApp QR scan completion (call repeatedly after generating QR)."""
    bridges_dir = _resolve_bridges_dir()
    provisioner_path = bridges_dir / "whatsapp" / "auto_whatsapp_provisioner.js"
    whatsapp_settings = bridges_dir / "whatsapp" / "settings.json"

    if not whatsapp_settings.exists():
        return WhatsAppPollResponse(error="No pending link found")

    try:
        settings = json.loads(whatsapp_settings.read_text())
        session_id = settings.get("_pending_session_id")
        generated_at = int(settings.get("_qr_generated_at", 0))
        elapsed = int(time.time()) - generated_at

        if elapsed > 120:  # 2 minute timeout
            del settings["_pending_session_id"]
            whatsapp_settings.write_text(json.dumps(settings, indent=2))
            return WhatsAppPollResponse(error="QR code expired (>2min)")

        # Poll for scan completion
        script = f"""
const {{ AutoWhatsAppProvisioner }} = require(process.env.PROV_PATH);
const prov = new AutoWhatsAppProvisioner(() => {{}});
(async () => {{ const result = await prov.pollQRScanStatus(process.env.SESSION_ID, 2); console.log(JSON.stringify(result)); }})();
"""

        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "PROV_PATH": str(provisioner_path), "SESSION_ID": session_id or ""},
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get("scanned"):
                del settings["_pending_session_id"]
                settings["whatsapp_linked"] = True
                whatsapp_settings.write_text(json.dumps(settings, indent=2))
                return WhatsAppPollResponse(linked=True)

        return WhatsAppPollResponse(linked=False)
    except Exception as e:
        _log.error(f"WhatsApp scan polling error: {e}")
        return WhatsAppPollResponse(error="Polling failed")
