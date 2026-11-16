"""In-memory unlock caches (ADR-2326 items 3 and 5).

Console: a random 256-bit unlock token goes to the browser as an HttpOnly cookie and is never
written to disk; the cache is keyed by ``sha256(token)`` and bound to the session ``sid``, so a
stolen session id (a file name) opens nothing. Idle 30 minutes, absolute 8 hours.

Bridge: a device unwrap is cached per owner for the turn plus 5 minutes.

Every ``get`` checks the revocation sentinel and the key epoch on disk: an erased owner or a
rotated key invalidates the entry in every process.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

from . import store as _store

IDLE_S = 30 * 60
ABSOLUTE_S = 8 * 3600
BRIDGE_S = 5 * 60


@dataclass
class Entry:
    tenant_id: str
    owner_id: str
    keys: _store.Keyring
    sid: str
    created: float
    last_used: float


_console: dict[str, Entry] = {}
_bridge: dict[tuple[str, str], tuple[_store.Keyring, float]] = {}
_lock = threading.Lock()


def _h(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _still_valid(tenant_id: str, owner_id: str, keys: _store.Keyring) -> bool:
    try:
        if _store.is_revoked(tenant_id, owner_id):
            return False
        owner = _store.read_owner(tenant_id, owner_id)
    except Exception:  # noqa: BLE001 - unreadable means not valid
        return False
    return not owner.get("erasing") and int(owner.get("key_epoch", 0)) == keys.epoch


def put_console(tenant_id: str, owner_id: str, keys: _store.Keyring, sid: str, now: float | None = None) -> str:
    ts = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    with _lock:
        _console[_h(token)] = Entry(tenant_id, owner_id, keys, sid, ts, ts)
    return token


def get_console(token: str | None, *, tenant_id: str, sid: str, now: float | None = None) -> Entry | None:
    if not token:
        return None
    ts = time.time() if now is None else now
    key = _h(token)
    with _lock:
        _prune_expired(ts)
        e = _console.get(key)
        if e is None:
            return None
        if e.sid != sid or e.tenant_id != tenant_id:
            return None  # a token presented with the wrong session opens nothing, and ends nothing
        if ts - e.last_used > IDLE_S or ts - e.created > ABSOLUTE_S:
            _console.pop(key, None)
            return None
    if not _still_valid(e.tenant_id, e.owner_id, e.keys):
        with _lock:
            _console.pop(key, None)
        return None
    with _lock:
        e.last_used = ts
    return e


def drop_console(token: str | None) -> Entry | None:
    if not token:
        return None
    with _lock:
        return _console.pop(_h(token), None)


def drop_owner(tenant_id: str, owner_id: str) -> None:
    with __import__("contextlib").suppress(Exception):
        from . import bridge  # noqa: PLC0415 - the bridge's decrypted store goes with the keys
        with bridge._STORES_LOCK:
            bridge._STORES.pop((tenant_id, owner_id), None)
    with _lock:
        for k in [k for k, e in _console.items() if e.tenant_id == tenant_id and e.owner_id == owner_id]:
            _console.pop(k, None)
        _bridge.pop((tenant_id, owner_id), None)


def _prune_expired(ts: float) -> None:
    for k in [k for k, e in _console.items() if ts - e.last_used > IDLE_S or ts - e.created > ABSOLUTE_S]:
        _console.pop(k, None)
    for k in [k for k, (_, exp) in _bridge.items() if exp < ts]:
        _bridge.pop(k, None)


def live_token_hashes(now: float | None = None) -> set[str]:
    ts = time.time() if now is None else now
    with _lock:
        _prune_expired(ts)
        return set(_console)


def drop_sid(sid: str) -> int:
    """Console logout: every unlock bound to that session ends."""
    with _lock:
        keys = [k for k, e in _console.items() if e.sid == sid]
        for k in keys:
            _console.pop(k, None)
        return len(keys)


def drop_owner_bridge(tenant_id: str, owner_id: str) -> None:
    with _lock:
        _bridge.pop((tenant_id, owner_id), None)


def replace_keys(tenant_id: str, owner_id: str, keys: _store.Keyring) -> None:
    """After a rekey in this process: update live console entries of that owner."""
    with _lock:
        for e in _console.values():
            if e.tenant_id == tenant_id and e.owner_id == owner_id:
                e.keys = keys
        _bridge.pop((tenant_id, owner_id), None)


def put_bridge(tenant_id: str, owner_id: str, keys: _store.Keyring, now: float | None = None) -> None:
    ts = time.time() if now is None else now
    with _lock:
        _bridge[(tenant_id, owner_id)] = (keys, ts + BRIDGE_S)


def get_bridge(tenant_id: str, owner_id: str, now: float | None = None) -> _store.Keyring | None:
    ts = time.time() if now is None else now
    with _lock:
        hit = _bridge.get((tenant_id, owner_id))
        if hit is None or hit[1] < ts:
            _bridge.pop((tenant_id, owner_id), None)
            return None
    if not _still_valid(tenant_id, owner_id, hit[0]):
        with _lock:
            _bridge.pop((tenant_id, owner_id), None)
        return None
    return hit[0]


def _reset_for_tests() -> None:
    with _lock:
        _console.clear()
        _bridge.clear()
