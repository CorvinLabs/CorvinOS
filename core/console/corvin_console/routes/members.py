"""Members — combined view of roles + quota + consent + disclosure
state across every chat the operator owns.

Stores live at ``<corvin_home>/tenants/<tid>/global/<kind>/`` (and, for the
default tenant only, the pre-tenancy ``<corvin_home>/global/<kind>/`` the bridge
writers still use when they pass no tenant):
  * ``<corvin_home>/global/roles/<channel>__<chat>.json``
  * ``<corvin_home>/global/quota/<channel>__<chat>.json``
  * ``<corvin_home>/global/consent/<channel>__<chat>.json``
  * ``<corvin_home>/global/disclosure/<channel>__<chat>.json``

The four state-stores share a chat-key naming convention but live in
separate trees. We enumerate the union of chat-keys across all four
stores, then fan-in the per-uid records into a single per-chat row.

Phase F: read-only. Member-mutations (grant/revoke/quota set) live
behind the Phase E2 mutation surface (slash-commands stay
authoritative for now).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter()


_STORE_NAMES = ("roles", "quota", "consent", "disclosure")


# The bridge writers (roles / quota / consent / disclosure) fall back to the
# pre-tenancy path ``<corvin_home>/global/<kind>`` whenever the adapter passes
# no tenant. Those records carry no tenant id, so they can only be attributed to
# the constant default tenant -- any other tenant reads its own directory only
# (the same rule as the audit tail's legacy chain, S-11a). Before this the
# reader looked ONLY under tenants/<tid>/ and the Roles card stayed empty on an
# install whose chats are all written through the legacy path.
_LEGACY_TENANT = "_default"


def _store_dirs(tid: str, kind: str) -> list[Path]:
    """Directories to read for ``kind``, tenant path first (it wins on conflict)."""
    dirs = [_forge_paths.tenant_global_dir(tid) / kind]
    if tid == _LEGACY_TENANT:
        dirs.append(Path(_forge_paths.corvin_home()) / "global" / kind)
    return dirs


def _enumerate_chat_keys(tid: str) -> list[str]:
    """Union of chat-keys across all four state-stores.

    Filename convention: ``<channel>__<chat>.json`` (double-underscore
    separator). The ``<chat>`` part is operator-controlled and may
    contain any safe filesystem char.
    """
    keys: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for kind in _STORE_NAMES:
        for d in _store_dirs(tid, kind):
            try:
                real = str(d.resolve())
            except OSError:
                continue
            if (kind, real) in seen or not d.exists():
                continue  # ADR-0007 compat symlinks can make both paths one directory
            seen.add((kind, real))
            try:
                for entry in d.iterdir():
                    if entry.is_file() and entry.suffix == ".json":
                        keys.add(entry.stem)
            except OSError:
                continue
    return sorted(keys)


def _read_store(tid: str, kind: str, chat_key: str) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for d in reversed(_store_dirs(tid, kind)):  # legacy first, tenant path overrides
        path = d / f"{chat_key}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


def _split_chat_key(chat_key: str) -> tuple[str, str]:
    """Filename convention is ``<channel>__<chat>``. Returns
    ``(channel, chat)``; if no separator is present, returns
    ``("unknown", chat_key)``."""
    if "__" in chat_key:
        ch, _, ck = chat_key.partition("__")
        return ch, ck
    return "unknown", chat_key


@router.get("/members")
def members_list(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """One row per chat — member counts + state-summary."""
    tid = rec.tenant_id
    items: list[dict[str, Any]] = []
    for chat_key in _enumerate_chat_keys(tid):
        roles = _read_store(tid, "roles", chat_key)
        quota = _read_store(tid, "quota", chat_key)
        consent = _read_store(tid, "consent", chat_key)
        disclosure = _read_store(tid, "disclosure", chat_key)
        ch, ck = _split_chat_key(chat_key)
        # Bundle counts
        bundle_counts: dict[str, int] = {}
        for uid, info in roles.items():
            if isinstance(info, dict):
                b = info.get("bundle", "unknown")
                bundle_counts[b] = bundle_counts.get(b, 0) + 1
        items.append({
            "chat_key":          chat_key,
            "channel":           ch,
            "chat":              ck,
            "members":           len(roles),
            "bundles":           bundle_counts,
            "quota_entries":     len(quota),
            "consent_entries":   len(consent),
            "disclosure_entries": len(disclosure),
        })
    items.sort(key=lambda r: r["chat_key"])
    return {
        "tenant_id":  tid,
        "ts":         time.time(),
        "count":      len(items),
        "chats":      items,
    }


@router.get("/members/{chat_key}")
def members_detail(
    chat_key: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Per-uid drill-down for one chat — fanned in from all four stores."""
    tid = rec.tenant_id
    if not re.fullmatch(r'[A-Za-z0-9_:.-]{1,256}', chat_key):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid chat_key",
        )
    roles      = _read_store(tid, "roles", chat_key)
    quota      = _read_store(tid, "quota", chat_key)
    consent    = _read_store(tid, "consent", chat_key)
    disclosure = _read_store(tid, "disclosure", chat_key)

    if not (roles or quota or consent or disclosure):
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"no state for chat_key {chat_key!r}",
        )

    # Union of uids across the four stores.
    uids = set(roles.keys()) | set(quota.keys()) | set(consent.keys()) | set(disclosure.keys())
    rows: list[dict[str, Any]] = []
    for uid in sorted(uids):
        rows.append({
            "uid":         uid,
            "role":        roles.get(uid),
            "quota":       quota.get(uid),
            "consent":     consent.get(uid),
            "disclosure":  disclosure.get(uid),
        })
    ch, ck = _split_chat_key(chat_key)
    return {
        "tenant_id":   tid,
        "ts":          time.time(),
        "chat_key":    chat_key,
        "channel":     ch,
        "chat":        ck,
        "uid_count":   len(rows),
        "uids":        rows,
    }
