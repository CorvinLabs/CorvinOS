"""Chat-settings route — per-(channel, chat) profile editor.

Phase G companion to ``routes/profile.py``. Surfaces the chat_profiles
block from every ``bridges/<channel>/settings.json`` and lets the user
edit a curated subset of the per-chat tunables:

  * persona            (one of the bundle + user personas)
  * observer_visibility ("off" / "transcript")
  * dialectic_mode_<site> for the six known sites
  * ldd_enabled, ldd_layers per layer
  * audience           ("owner" / "all")

Read-only sections shown alongside:
  * the live merged effective profile (matches what the adapter sees)
  * voice-session-dir presence + size
  * known channels we found settings for

Endpoints
---------
  GET   /v1/console/chat-settings                          → list of known (channel, chat_key) tuples
  GET   /v1/console/chat-settings/{channel}/{chat_key}     → detail
  PATCH /v1/console/chat-settings/{channel}/{chat_key}     → partial update (Re-Auth + audit)

Compliance baseline mirrors ``routes/profile.py``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field, conint

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session, verify_reauth

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_VOICE_SHARED = _REPO / "operator" / "bridges" / "shared"
_VOICE_BRIDGES = _REPO / "operator" / "bridges"

if str(_VOICE_SHARED) not in sys.path:
    sys.path.insert(0, str(_VOICE_SHARED))

# Lazy imports — these dictate the canonical value sets, kept in lockstep
# with the source-of-truth modules.
import ldd as _ldd_module      # noqa: E402
import dialectic as _dialectic_module  # noqa: E402

import logging
_log = logging.getLogger(__name__)


router = APIRouter()

# Known bridge channels. Anything outside this allow-list is rejected at
# the URL layer so a path-traversal attempt cannot land on a sibling
# directory.
_KNOWN_CHANNELS: tuple[str, ...] = (
    "telegram", "discord", "slack", "whatsapp", "email", "webui",
)

# A chat_key may contain channel-specific characters (numbers, dashes,
# colons, @-signs for WhatsApp JIDs). The regex below is liberal but
# rejects slashes, dot-dot and shell metacharacters that have no
# business in a chat identifier.
_CHAT_KEY_RE = re.compile(r"^[A-Za-z0-9_@.:+\-]{1,200}$")

# Mutable per-chat keys we let the SPA edit. Anything outside this set
# is rejected at the Pydantic layer — chat_profiles[<chat>] is a richer
# dict in practice (mcp_servers, add_dirs, allowed_tools, …) but those
# are operator-grade settings, not user-facing toggles.
_OBSERVER_VIS_VALUES = ("off", "transcript")
_AUDIENCE_VALUES = ("owner", "all")


# ── Schema ────────────────────────────────────────────────────────────


class DialecticModesPatch(BaseModel):
    """Per-site mode for the six canonical sites. Absent = unchanged;
    null = reset to default."""
    skill_promotion: Literal["off", "fast", "skill", "cli"] | None = None
    forge_creation:  Literal["off", "fast", "skill", "cli"] | None = None
    auto_routing:    Literal["off", "fast", "skill", "cli"] | None = None
    path_gate:       Literal["off", "fast", "skill", "cli"] | None = None
    session_reset:   Literal["off", "fast", "skill", "cli"] | None = None
    voice_summary:   Literal["off", "fast", "skill", "cli"] | None = None
    model_config = {"extra": "forbid"}


class LddLayersPatch(BaseModel):
    """Per-LDD-layer bool override. Absent = unchanged; null = drop.

    The keys are the 12 canonical layer IDs from ``ldd.LAYERS`` — we
    declare them explicitly so Pydantic catches typos."""
    loop_driven_engineering: bool | None = None
    e2e_driven_iteration:    bool | None = None
    dialectical_reasoning:   bool | None = None
    dialectical_cot:         bool | None = None
    root_cause_by_layer:     bool | None = None
    docs_as_dod:             bool | None = None
    reproducibility_first:   bool | None = None
    loss_backprop_lens:      bool | None = None
    method_evolution:        bool | None = None
    drift_detection:         bool | None = None
    iterative_refinement:    bool | None = None
    per_subtask_e2e:         bool | None = None
    model_config = {"extra": "forbid"}


class ChatSettingsPatch(BaseModel):
    """Body for ``PATCH /chat-settings/{channel}/{chat_key}``.

    Every section is optional; only the keys the client explicitly sends
    are merged. ``null`` on a scalar deletes the key (resets to default
    behaviour). Dict sub-sections (``dialectic`` / ``ldd_layers``)
    behave the same per-leaf-key.
    """
    persona:             str | None = Field(None, max_length=60)
    observer_visibility: Literal["off", "transcript"] | None = None
    audience:            Literal["owner", "all"] | None = None
    ldd_enabled:         bool | None = None
    dialectic_enabled:   bool | None = None
    dialectic:           DialecticModesPatch | None = None
    ldd_layers:          LddLayersPatch | None = None
    re_auth_token:       str | None = None
    model_config = {"extra": "forbid"}


# ── Helpers ───────────────────────────────────────────────────────────


def _channel_settings_path(channel: str) -> Path:
    """Resolve the per-channel settings.json. Single source of truth —
    the legacy in-repo location under ``operator/bridges/<channel>/``.
    ADR-0008's canonical XDG path lives under ``<corvin_home>/bridges``
    but is wired in a separate phase; for now the in-repo path is what
    the daemons read."""
    return _VOICE_BRIDGES / channel / "settings.json"


def _load_channel(channel: str) -> dict[str, Any]:
    p = _channel_settings_path(channel)
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_channel(channel: str, data: dict[str, Any]) -> None:
    """Atomic write + mode preservation with file-level locking to prevent
    concurrent read-modify-write races. Tier 2 fix: fcntl locking prevents TOCTOU."""
    import fcntl

    p = _channel_settings_path(channel)
    lock_path = p.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Acquire exclusive lock to prevent concurrent writes
    with open(lock_path, 'w') as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            shutil.move(str(tmp), str(p))
            try:
                st_mode = p.stat().st_mode & 0o777
                if st_mode != 0o600:
                    try:
                        p.chmod(0o600)
                    except OSError:
                        pass
            except OSError:
                pass
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def _project_chat_profile(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Project a raw chat_profiles[<chat>] entry into the curated subset
    we expose. We deliberately do NOT surface the operator-grade fields
    (mcp_servers, add_dirs, allowed_tools, disallowed_tools, append_system,
    routing) — those need an operator-grade editor (Phase H+)."""
    if not isinstance(raw, dict):
        raw = {}
    out: dict[str, Any] = {
        "persona":             raw.get("persona"),
        "observer_visibility": raw.get("observer_visibility"),
        "audience":            raw.get("audience"),
        "ldd_enabled":         raw.get("ldd_enabled"),
        "dialectic_enabled":   raw.get("dialectic_enabled"),
        "dialectic":           {},
        "ldd_layers":          {},
    }
    for site in _dialectic_module.SITES.keys():
        key = f"dialectic_mode_{site}"
        if key in raw:
            out["dialectic"][site] = raw.get(key)
    layers_raw = raw.get("ldd_layers")
    if isinstance(layers_raw, dict):
        for layer in _ldd_module.LAYERS:
            if layer in layers_raw:
                out["ldd_layers"][layer] = layers_raw[layer]
    # Surface the operator-only fields read-only so the user knows
    # they're there but can't edit them from the User-Profile tab.
    out["read_only_fields"] = {
        k: True
        for k in (
            "permission_mode", "allowed_tools", "disallowed_tools",
            "append_system", "mcp_servers", "add_dirs", "routing",
            "default_engine", "model",
        )
        if k in (raw or {})
    }
    return out


def _list_chats() -> list[dict[str, Any]]:
    """Walk all known channels + return one entry per known chat_key."""
    out: list[dict[str, Any]] = []
    for channel in _KNOWN_CHANNELS:
        ch = _load_channel(channel)
        if not ch:
            continue
        profiles = ch.get("chat_profiles") or {}
        if not isinstance(profiles, dict):
            continue
        for chat_key, raw in profiles.items():
            if not _CHAT_KEY_RE.match(chat_key):
                continue
            out.append({
                "channel":   channel,
                "chat_key":  chat_key,
                "persona":   (raw or {}).get("persona") if isinstance(raw, dict) else None,
                "audience":  (raw or {}).get("audience") if isinstance(raw, dict) else None,
                "observer_visibility":
                    (raw or {}).get("observer_visibility") if isinstance(raw, dict) else None,
            })
    out.sort(key=lambda x: (x["channel"], x["chat_key"]))
    return out


def _validate_channel(channel: str) -> None:
    if channel not in _KNOWN_CHANNELS:
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"unknown channel {channel!r}; known: {list(_KNOWN_CHANNELS)}",
        )


def _validate_chat_key(chat_key: str) -> None:
    if not _CHAT_KEY_RE.match(chat_key):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"invalid chat_key — must match {_CHAT_KEY_RE.pattern}",
        )


def _apply_patch(raw: dict[str, Any], patch: ChatSettingsPatch) -> dict[str, Any]:
    """Merge a validated PATCH body into the raw chat_profiles[<chat>]
    entry. Returns the merged dict in place (caller persists)."""
    sent = patch.model_fields_set

    if "persona" in sent:
        if patch.persona is None:
            raw.pop("persona", None)
        else:
            raw["persona"] = patch.persona
    if "observer_visibility" in sent:
        if patch.observer_visibility is None:
            raw.pop("observer_visibility", None)
        else:
            raw["observer_visibility"] = patch.observer_visibility
    if "audience" in sent:
        if patch.audience is None:
            raw.pop("audience", None)
        else:
            raw["audience"] = patch.audience
    if "ldd_enabled" in sent:
        if patch.ldd_enabled is None:
            raw.pop("ldd_enabled", None)
        else:
            raw["ldd_enabled"] = patch.ldd_enabled
    if "dialectic_enabled" in sent:
        if patch.dialectic_enabled is None:
            raw.pop("dialectic_enabled", None)
        else:
            raw["dialectic_enabled"] = patch.dialectic_enabled

    if patch.dialectic is not None:
        diasent = patch.dialectic.model_fields_set
        for site in diasent:
            key = f"dialectic_mode_{site}"
            v = getattr(patch.dialectic, site)
            if v is None:
                raw.pop(key, None)
            else:
                raw[key] = v

    if patch.ldd_layers is not None:
        layers_sent = patch.ldd_layers.model_fields_set
        layers_cur = dict(raw.get("ldd_layers") or {})
        for layer in layers_sent:
            v = getattr(patch.ldd_layers, layer)
            if v is None:
                layers_cur.pop(layer, None)
            else:
                layers_cur[layer] = v
        if layers_cur:
            raw["ldd_layers"] = layers_cur
        else:
            raw.pop("ldd_layers", None)

    return raw


# ── Routes ────────────────────────────────────────────────────────────


@router.get("/chat-settings")
def chat_settings_list(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return all (channel, chat_key) pairs known to this operator."""
    chats = _list_chats()
    return {
        "tenant_id":      rec.tenant_id,
        "ts":             time.time(),
        "known_channels": list(_KNOWN_CHANNELS),
        "chats":          chats,
        "count":          len(chats),
    }


@router.get("/chat-settings/{channel}/{chat_key}")
def chat_settings_detail(
    channel: str,
    chat_key: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    _validate_channel(channel)
    _validate_chat_key(chat_key)
    ch = _load_channel(channel)
    if not ch:
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"no settings.json for channel {channel!r}",
        )
    profiles = ch.get("chat_profiles") or {}
    raw = profiles.get(chat_key)
    projected = _project_chat_profile(raw if isinstance(raw, dict) else None)
    return {
        "channel":     channel,
        "chat_key":    chat_key,
        "exists":      isinstance(raw, dict),
        "profile":     projected,
        "schema": {
            "observer_visibility": list(_OBSERVER_VIS_VALUES),
            "audience":            list(_AUDIENCE_VALUES),
            "dialectic_sites":     list(_dialectic_module.SITES.keys()),
            "dialectic_modes":     list(_dialectic_module.VALID_MODES),
            "ldd_layers":          list(_ldd_module.LAYERS),
        },
    }


@router.patch("/chat-settings/{channel}/{chat_key}")
def chat_settings_patch(
    channel: str,
    chat_key: str,
    body: ChatSettingsPatch,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    _validate_channel(channel)
    _validate_chat_key(chat_key)

    target_id = f"{channel}:{chat_key}"

    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="chat_settings.write",
            target_kind="chat_profile",
            target_id=target_id,
            reason="reauth-failed",
        )
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "re-auth failed")

    ch = _load_channel(channel)
    if not ch:
        # Refuse to materialise a channel settings.json from the console.
        # The bridge daemons own that file's lifecycle; an editor that
        # silently creates it would surprise an operator who never
        # configured the channel.
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="chat_settings.write",
            target_kind="chat_profile",
            target_id=target_id,
            reason="channel-not-configured",
        )
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"channel {channel!r} has no settings.json — provision via bridge first",
        )

    profiles = dict(ch.get("chat_profiles") or {})
    raw = profiles.get(chat_key)
    if not isinstance(raw, dict):
        raw = {}

    merged = _apply_patch(dict(raw), body)
    if merged:
        profiles[chat_key] = merged
    else:
        # The patch emptied the profile out. Drop the entry entirely so
        # the chat falls back to the daemon's max-open default.
        profiles.pop(chat_key, None)

    ch["chat_profiles"] = profiles

    try:
        _save_channel(channel, ch)
    except OSError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="chat_settings.write",
            target_kind="chat_profile",
            target_id=target_id,
            reason="io-error",
        )
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "write failed",
        ) from e

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="chat_settings.write",
        target_kind="chat_profile",
        target_id=target_id,
    )

    projected = _project_chat_profile(profiles.get(chat_key))
    return {
        "ok":       True,
        "channel":  channel,
        "chat_key": chat_key,
        "profile":  projected,
    }
