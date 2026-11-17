"""Owners, keys, unlock, pairing and rekey (ADR-2326).

Ownership is proven by holding a key. A console owner unlocks with handle + passphrase; a bridge
turn of a bound identity unwraps the device key (``convenient`` mode only). ``strict`` owners have
no device wrap and are console-only. A bridge-only owner (``/me on``) has no passphrase and no
recovery code. Nothing here stores, logs or returns a passphrase, a recovery code (except once, at
creation or rekey, to the caller) or a data key.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import audit, capabilities, crypto, limits, locks, paths
from .ids import is_ulid, new_ulid
from .store import (Keyring, PersonalStore, empty_manifest, empty_meta, read_owner, write_encrypted_json,
                    write_owner)

HANDLE_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
MODES = ("convenient", "strict")
CONSENT_VERSION = "pkb-consent-1"
# Channels whose sender id the platform authenticates (ADR-2326 item 6). Email is excluded,
# WhatsApp too (its bridge is a user account whose sender field is the chat, not the author).
AUTHENTICATED_SENDER_CHANNELS = frozenset({"discord", "telegram", "slack", "teams", "signal"})
PAIR_TTL_S = 600
PAIR_ATTEMPTS = 5
SENDER_REDEEMS_PER_10_MIN = 5


class OwnerError(RuntimeError):
    """A refused owner operation. ``str()`` is safe to show to the user."""


class UnlockFailed(OwnerError):
    pass


@dataclass
class CreatedOwner:
    owner_id: str
    recovery_code: str | None
    keys: Keyring


# ---- tenant-level files -------------------------------------------------------------------------

def _ensure_root(tenant_id: str) -> None:
    root = paths.root(tenant_id)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths.assert_no_links(root, root.parent)
    pf = paths.pepper_file(tenant_id)
    if not pf.exists():
        with locks.exclusive(paths.tenant_lock_file(tenant_id)):
            if not pf.exists():
                locks.atomic_write(pf, os.urandom(32))


def _pepper(tenant_id: str) -> bytes:
    _ensure_root(tenant_id)
    return paths.pepper_file(tenant_id).read_bytes()


def handle_key(tenant_id: str, handle: str) -> str:
    return crypto.hmac_hex(_pepper(tenant_id), "handle:" + handle)


def identity_key(tenant_id: str, channel: str, uid: str) -> str:
    return crypto.hmac_hex(_pepper(tenant_id), f"id:{channel}:{uid}")


def _read_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _index(tenant_id: str) -> dict[str, Any]:
    data = _read_json(paths.index_file(tenant_id), {"v": 1, "bindings": {}})
    data.setdefault("bindings", {})
    return data


def _pairs_file(tenant_id: str):
    return paths.root(tenant_id) / "_pairs.json"


def _alias_live(idx: dict[str, Any], akey: str) -> bool:
    """An alias is valid only while the chat account it was recorded for is still bound to the same owner."""
    bkey = idx.get("alias_bind", {}).get(akey)
    owner_of_alias = idx["bindings"].get(akey)
    return bkey is not None and owner_of_alias is not None and idx["bindings"].get(bkey) == owner_of_alias


def add_alias(tenant_id: str, *, channel: str, alias: str, owner_id: str, bound_uid: str) -> bool:
    """A second identity of a bound chat account (Teams: the UPN next to the AAD object id the space is bound to).
    Stored lower-cased; valid only while its account stays bound (``alias_bind``); listed under the account
    (``alias_of``) so unlinking removes it. A stale alias of another owner (UPN reassigned, account unlinked) is taken
    over; a live one of another owner is never taken. Checked under the tenant lock."""
    alias = str(alias or "").strip().lower()
    if not alias or alias in ("unknown", "none", "null", "anon") or alias == str(bound_uid).lower():
        return False
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        idx = _index(tenant_id)
        bkey = identity_key(tenant_id, channel, bound_uid)
        if idx["bindings"].get(bkey) != owner_id:          # the account was unlinked meanwhile: no alias for it
            return False
        key = identity_key(tenant_id, channel, alias)
        cur = idx["bindings"].get(key)
        if cur is not None and cur != owner_id:
            if key not in idx.get("alias_bind", {}) or _alias_live(idx, key):
                return False                               # a real binding, or a live alias of someone else
            old = idx["alias_bind"].get(key)
            if old in idx.get("alias_of", {}):
                idx["alias_of"][old] = [k for k in idx["alias_of"][old] if k != key]
        idx["bindings"][key] = owner_id
        idx.setdefault("alias_bind", {})[key] = bkey
        listed = idx.setdefault("alias_of", {}).setdefault(bkey, [])
        if key not in listed:
            listed.append(key)
        locks.atomic_write(paths.index_file(tenant_id), json.dumps(idx).encode("utf-8"))
        return True


def _bind_index(tenant_id: str, channel: str, uid: str, owner_id: str) -> None:
    """Record ``HMAC(pepper, channel:uid) -> owner_id``. Caller holds the tenant lock."""
    idx = _index(tenant_id)
    idx["bindings"][identity_key(tenant_id, channel, uid)] = owner_id
    locks.atomic_write(paths.index_file(tenant_id), json.dumps(idx).encode("utf-8"))


def list_owner_ids(tenant_id: str) -> list[str]:
    root = paths.root(tenant_id)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if is_ulid(p.name) and p.is_dir() and not paths.is_link(p))


def find_by_handle(tenant_id: str, handle: str) -> str | None:
    for oid in list_owner_ids(tenant_id):
        with contextlib.suppress(Exception):
            if read_owner(tenant_id, oid).get("handle") == handle:
                return oid
    return None


def read_owner_handle(tenant_id: str, owner_id: str) -> str:
    return str(read_owner(tenant_id, owner_id).get("handle") or "")


def owner_info(tenant_id: str, owner_id: str) -> dict[str, Any]:
    """Non-secret facts about an owner (mode, whether a passphrase exists)."""
    o = read_owner(tenant_id, owner_id)
    w = o.get("wraps", {})
    return {"unlock_mode": o.get("unlock_mode"), "has_passphrase": "pass" in w, "has_device": "device" in w,
            "key_epoch": o.get("key_epoch"), "default": bool(o.get("default"))}


# ---- creation -----------------------------------------------------------------------------------

def _write_device_key(tenant_id: str, owner_id: str, epoch: int = 1) -> bytes:
    d = paths.device_key_dir(tenant_id)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = crypto.new_key()
    p = paths.device_key_file(tenant_id, owner_id, epoch)
    locks.atomic_write(p, key)
    if not capabilities.restrict_to_owner(p):
        with contextlib.suppress(FileNotFoundError):
            p.unlink()
        raise OwnerError("this system cannot protect a device key; choose strict mode")
    return key


def _read_device_key(tenant_id: str, owner_id: str, epoch: int) -> bytes:
    p = paths.device_key_file(tenant_id, owner_id, epoch)
    if paths.is_link(p):
        raise UnlockFailed("not available")
    try:
        key = p.read_bytes()
    except FileNotFoundError as exc:
        raise UnlockFailed("not available") from exc
    if len(key) != crypto.KEY_BYTES:
        raise UnlockFailed("not available")
    return key


def _init_store_files(tenant_id: str, owner_id: str, keys: Keyring, *, auto_capture: bool, now: float,
                      bindings: list[dict[str, str]] | None = None, consent_version: str = CONSENT_VERSION) -> None:
    d = paths.owner_dir(tenant_id, owner_id)
    for sub in ("objects", "versions"):
        (d / sub).mkdir(mode=0o700, exist_ok=True)
    meta = empty_meta(consent_version=consent_version, auto_capture=auto_capture, now=now)
    meta["bindings"] = list(bindings or [])
    write_encrypted_json(paths.meta_file(tenant_id, owner_id), keys, meta,
                         lambda e: crypto.aad("meta", owner_id, e))
    write_encrypted_json(paths.manifest_file(tenant_id, owner_id), keys, empty_manifest(),
                         lambda e: crypto.aad("manifest", owner_id, e))


def create_owner(tenant_id: str, handle: str, passphrase: str, *, consent: bool, unlock_mode: str = "convenient",
                 auto_capture: bool = True, now: float | None = None) -> CreatedOwner:
    ts = time.time() if now is None else now
    if not consent:
        raise OwnerError("consent is required to create a personal space")
    handle = str(handle or "").strip().lower()
    if not HANDLE_RE.match(handle):
        raise OwnerError("the handle must be 3-32 characters: a-z, 0-9, dot, dash, underscore")
    if unlock_mode not in MODES:
        raise OwnerError("unknown mode")
    problem = crypto.passphrase_problem(passphrase)
    if problem:
        raise OwnerError(problem)
    try:
        limits.check_creation(ts)
    except limits.TooSoon as exc:
        raise OwnerError("too many new spaces in the last minute; try again shortly") from exc
    if unlock_mode == "convenient" and not capabilities.probe().get("secure_files", False):
        raise OwnerError("this system cannot protect a device key; choose strict mode")
    _ensure_root(tenant_id)
    kdf = crypto.new_kdf()
    kek = limits.run_kdf(lambda: crypto.kek_from_passphrase(passphrase, kdf))
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        if find_by_handle(tenant_id, handle):
            raise OwnerError("this handle is taken")
        owner_id = new_ulid(int(ts * 1000))
        keys = Keyring(epoch=1, dek=crypto.new_key())
        code, rkey = crypto.new_recovery_code()
        wraps: dict[str, Any] = {
            "pass": {"kdf": kdf, **crypto.wrap(keys.dek, kek, owner_id=owner_id, epoch=1, kind="pass")},
            "recovery": crypto.wrap(keys.dek, rkey, owner_id=owner_id, epoch=1, kind="recovery"),
        }
        audit.emit("personal_kb.owner_created", tenant_id=tenant_id, owner_hash=audit.owner_hash(owner_id),
                   unlock_mode=unlock_mode, schema=1)
        d = paths.owner_dir(tenant_id, owner_id)
        d.mkdir(mode=0o700)
        try:
            if unlock_mode == "convenient":
                dev = _write_device_key(tenant_id, owner_id)
                wraps["device"] = crypto.wrap(keys.dek, dev, owner_id=owner_id, epoch=1, kind="device")
            write_owner(tenant_id, owner_id, {"schema": 1, "owner_id": owner_id, "handle": handle, "created": ts,
                                              "unlock_mode": unlock_mode, "key_epoch": 1, "wraps": wraps,
                                              "erasing": False})
            _init_store_files(tenant_id, owner_id, keys, auto_capture=auto_capture, now=ts)
        except Exception:
            _remove_tree(d)
            with contextlib.suppress(Exception):
                paths.device_key_file(tenant_id, owner_id, 1).unlink()
            raise
    return CreatedOwner(owner_id=owner_id, recovery_code=code, keys=keys)


def create_bridge_owner(tenant_id: str, *, channel: str, uid: str, display: str = "", chat_type: str,
                        now: float | None = None) -> CreatedOwner:
    """``/me on``: a bridge-only owner (convenient, no passphrase, no recovery code)."""
    ts = time.time() if now is None else now
    _check_bindable(channel, uid, chat_type)
    try:
        limits.check_creation(ts)
    except limits.TooSoon as exc:
        raise OwnerError("too many new spaces in the last minute; try again shortly") from exc
    if not capabilities.probe().get("secure_files", False):
        raise OwnerError("this system cannot protect a device key")
    _ensure_root(tenant_id)
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        ikey = identity_key(tenant_id, channel, uid)
        if ikey in _index(tenant_id)["bindings"]:
            raise OwnerError("this chat account already has a personal space")
        owner_id = new_ulid(int(ts * 1000))
        keys = Keyring(epoch=1, dek=crypto.new_key())
        audit.emit("personal_kb.owner_created", tenant_id=tenant_id, owner_hash=audit.owner_hash(owner_id),
                   unlock_mode="bridge", schema=1)
        d = paths.owner_dir(tenant_id, owner_id)
        d.mkdir(mode=0o700)
        try:
            dev = _write_device_key(tenant_id, owner_id)
            wraps = {"device": crypto.wrap(keys.dek, dev, owner_id=owner_id, epoch=1, kind="device")}
            write_owner(tenant_id, owner_id, {"schema": 1, "owner_id": owner_id,
                                              "handle": "chat-" + secrets.token_hex(4), "created": ts,
                                              "unlock_mode": "convenient", "key_epoch": 1, "wraps": wraps,
                                              "erasing": False})
            _init_store_files(tenant_id, owner_id, keys, auto_capture=True, now=ts,
                              bindings=[{"channel": channel, "uid": uid, "display": display[:80]}])
            _bind_index(tenant_id, channel, uid, owner_id)
        except Exception:
            _remove_tree(d)
            with contextlib.suppress(Exception):
                paths.device_key_file(tenant_id, owner_id, 1).unlink()
            raise
    return CreatedOwner(owner_id=owner_id, recovery_code=None, keys=keys)


# ---- the tenant's default space (no login) ------------------------------------------------------
# Every install has one personal space that is simply there: created on first use, wrapped by the device
# key alone (no handle, no passphrase, no recovery code), opened by any authenticated console session of the
# tenant. Login flows (handle + passphrase) stay in this module for a later release; nothing here depends on
# them. Automatic learning starts OFF: switching it on is the consent act (see the console route).

DEFAULT_CONSENT_VERSION = "default-space-no-consent"


def find_default(tenant_id: str) -> str | None:
    """The owner id of the tenant's default space, or None when none exists yet."""
    for oid in list_owner_ids(tenant_id):
        with contextlib.suppress(Exception):
            if read_owner(tenant_id, oid).get("default"):
                return oid
    return None


def ensure_default_owner(tenant_id: str, now: float | None = None) -> str:
    """Create the default space once (under the tenant lock) and return its owner id."""
    existing = find_default(tenant_id)
    if existing:
        return existing
    ts = time.time() if now is None else now
    if not capabilities.probe().get("secure_files", False):
        raise OwnerError("this system cannot protect a device key")
    _ensure_root(tenant_id)
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        existing = find_default(tenant_id)
        if existing:
            return existing
        try:
            limits.check_creation(ts)
        except limits.TooSoon as exc:
            raise OwnerError("the personal space is being prepared; try again shortly") from exc
        owner_id = new_ulid(int(ts * 1000))
        keys = Keyring(epoch=1, dek=crypto.new_key())
        audit.emit("personal_kb.owner_created", tenant_id=tenant_id, owner_hash=audit.owner_hash(owner_id),
                   unlock_mode="default", schema=1)
        d = paths.owner_dir(tenant_id, owner_id)
        d.mkdir(mode=0o700)
        try:
            dev = _write_device_key(tenant_id, owner_id)
            wraps = {"device": crypto.wrap(keys.dek, dev, owner_id=owner_id, epoch=1, kind="device")}
            write_owner(tenant_id, owner_id, {"schema": 1, "owner_id": owner_id,
                                              "handle": "space-" + secrets.token_hex(4), "created": ts,
                                              "unlock_mode": "convenient", "key_epoch": 1, "wraps": wraps,
                                              "erasing": False, "default": True})
            _init_store_files(tenant_id, owner_id, keys, auto_capture=False, now=ts,
                              consent_version=DEFAULT_CONSENT_VERSION)
        except Exception:
            _remove_tree(d)
            with contextlib.suppress(Exception):
                paths.device_key_file(tenant_id, owner_id, 1).unlink()
            raise
    return owner_id


def unlock_default(tenant_id: str, *, surface: str = "console") -> tuple[str, Keyring]:
    """Open the default space with the device key (creating it on first use). No credentials."""
    oid = ensure_default_owner(tenant_id)
    return oid, unlock_with_device(tenant_id, oid, surface=surface)


# ---- unlock -------------------------------------------------------------------------------------

def _keyring_from(owner: dict[str, Any], dek: bytes) -> Keyring:
    epoch = int(owner["key_epoch"])
    keys = Keyring(epoch=epoch, dek=dek)
    prev = owner.get("prev")
    if prev:
        old = crypto.unwrap(prev, dek, owner_id=owner["owner_id"], epoch=epoch, kind=f"prev-{prev['epoch']}")
        keys.prev[int(prev["epoch"])] = old
    return keys


def _fail(tenant_id: str, surface: str, session_key: str, hkey: str | None, now: float) -> None:
    limits.record_session_failure(session_key, now)
    fails = limits.record_handle_failure(tenant_id, hkey, now) if hkey else 0
    with contextlib.suppress(audit.PersonalKbAuditError):
        audit.emit("personal_kb.unlock_failed", tenant_id=tenant_id, surface=surface,
                   attempt_bucket="1" if fails <= 1 else "2-4" if fails <= 4 else "5+")


def unlock_with_passphrase(tenant_id: str, handle: str, passphrase: str, *, session_key: str,
                           surface: str = "console", now: float | None = None) -> tuple[str, Keyring]:
    ts = time.time() if now is None else now
    limits.check_session(session_key, ts)
    handle = str(handle or "").strip().lower()
    owner_id = find_by_handle(tenant_id, handle) if HANDLE_RE.match(handle) else None
    if owner_id is None:
        _fail(tenant_id, surface, session_key, None, ts)
        raise UnlockFailed("handle or passphrase is wrong")
    hkey = handle_key(tenant_id, handle)
    with limits.handle_attempt(tenant_id, hkey):
        limits.check_handle(tenant_id, hkey, ts)
        owner = read_owner(tenant_id, owner_id)
        w = owner.get("wraps", {}).get("pass")
        if not w or owner.get("erasing"):
            _fail(tenant_id, surface, session_key, hkey, ts)
            raise UnlockFailed("handle or passphrase is wrong")
        try:
            kek = limits.run_kdf(lambda: crypto.kek_from_passphrase(str(passphrase or ""), w["kdf"]))
            dek = crypto.unwrap(w, kek, owner_id=owner_id, epoch=int(owner["key_epoch"]), kind="pass")
        except crypto.DecryptError:
            _fail(tenant_id, surface, session_key, hkey, ts)
            raise UnlockFailed("handle or passphrase is wrong") from None
    limits.clear_session(session_key)
    limits.clear_handle(tenant_id, hkey)
    keys = _keyring_from(owner, dek)
    audit.emit("personal_kb.unlocked", tenant_id=tenant_id, owner_hash=audit.owner_hash(owner_id),
               surface=surface, mode="passphrase")
    with contextlib.suppress(Exception):   # an unfinished sweep never blocks the unlock
        resume_rekey_if_pending(tenant_id, owner_id, keys)
    return owner_id, keys


def unlock_with_device(tenant_id: str, owner_id: str, *, surface: str) -> Keyring:
    owner = read_owner(tenant_id, owner_id)
    w = owner.get("wraps", {}).get("device")
    if not w or owner.get("erasing") or owner.get("unlock_mode") == "strict":
        raise UnlockFailed("not available")
    dev = _read_device_key(tenant_id, owner_id, int(owner["key_epoch"]))
    try:
        dek = crypto.unwrap(w, dev, owner_id=owner_id, epoch=int(owner["key_epoch"]), kind="device")
    except crypto.DecryptError:
        raise UnlockFailed("not available") from None
    keys = _keyring_from(owner, dek)
    audit.emit("personal_kb.unlocked", tenant_id=tenant_id, owner_hash=audit.owner_hash(owner_id),
               surface=surface, mode="device")
    return keys


def unlock_with_recovery(tenant_id: str, handle: str, recovery_code: str, new_passphrase: str, *,
                         session_key: str, now: float | None = None) -> tuple[str, Keyring]:
    ts = time.time() if now is None else now
    limits.check_session(session_key, ts)
    problem = crypto.passphrase_problem(new_passphrase)
    if problem:
        raise OwnerError(problem)
    handle = str(handle or "").strip().lower()
    owner_id = find_by_handle(tenant_id, handle) if HANDLE_RE.match(handle) else None
    if owner_id is None:
        _fail(tenant_id, "console", session_key, None, ts)
        raise UnlockFailed("handle or recovery code is wrong")
    hkey = handle_key(tenant_id, handle)
    limits.check_handle(tenant_id, hkey, ts)
    failed = False
    with locks.exclusive(paths.lock_file(tenant_id, owner_id)):
        owner = read_owner(tenant_id, owner_id)
        _not_erasing(tenant_id, owner_id, owner)
        w = owner.get("wraps", {}).get("recovery")
        try:
            if not w:
                raise crypto.DecryptError("no recovery wrap")
            rkey = crypto.recovery_key_from_code(recovery_code)
            dek = crypto.unwrap(w, rkey, owner_id=owner_id, epoch=int(owner["key_epoch"]), kind="recovery")
        except crypto.DecryptError:
            failed = True
        if not failed:
            kdf = crypto.new_kdf()
            kek = limits.run_kdf(lambda: crypto.kek_from_passphrase(new_passphrase, kdf))
            audit.emit("personal_kb.recovery_used", tenant_id=tenant_id, owner_hash=audit.owner_hash(owner_id),
                       key_epoch=int(owner["key_epoch"]))
            owner["wraps"]["pass"] = {"kdf": kdf, **crypto.wrap(dek, kek, owner_id=owner_id,
                                                               epoch=int(owner["key_epoch"]), kind="pass")}
            write_owner(tenant_id, owner_id, owner)
    if failed:
        _fail(tenant_id, "console", session_key, hkey, ts)
        raise UnlockFailed("handle or recovery code is wrong")
    limits.clear_session(session_key)
    limits.clear_handle(tenant_id, hkey)
    keys = _keyring_from(owner, dek)
    with contextlib.suppress(Exception):
        resume_rekey_if_pending(tenant_id, owner_id, keys)
    return owner_id, keys


def _not_erasing(tenant_id: str, owner_id: str, owner: dict[str, Any]) -> None:
    from .store import is_revoked  # noqa: PLC0415

    if owner.get("erasing") or is_revoked(tenant_id, owner_id):
        raise UnlockFailed("not available")


@contextlib.contextmanager
def _guarded_check(tenant_id: str, owner_id: str, session_key: str, surface: str):
    """A passphrase check inside an unlocked session (change passphrase, rekey) is slowed down and audited
    exactly like an unlock: session backoff, per-handle delay, one attempt per handle at a time."""
    ts = time.time()
    limits.check_session(session_key, ts)
    handle = read_owner(tenant_id, owner_id).get("handle") or ""
    hkey = handle_key(tenant_id, handle) if handle else None
    if hkey is None:
        yield
        return
    with limits.handle_attempt(tenant_id, hkey):
        limits.check_handle(tenant_id, hkey, ts)
        try:
            yield
        except UnlockFailed:
            _fail(tenant_id, surface, session_key, hkey, time.time())
            raise
        limits.clear_handle(tenant_id, hkey)


def change_passphrase(tenant_id: str, owner_id: str, old: str, new: str, *, session_key: str = "",
                      surface: str = "console") -> None:
    problem = crypto.passphrase_problem(new)
    if problem:
        raise OwnerError(problem)
    with _guarded_check(tenant_id, owner_id, session_key, surface), \
            locks.exclusive(paths.lock_file(tenant_id, owner_id)):
        owner = read_owner(tenant_id, owner_id)
        _not_erasing(tenant_id, owner_id, owner)
        w = owner.get("wraps", {}).get("pass")
        if not w:
            raise OwnerError("this space has no passphrase")
        try:
            kek_old = limits.run_kdf(lambda: crypto.kek_from_passphrase(old, w["kdf"]))
            dek = crypto.unwrap(w, kek_old, owner_id=owner_id, epoch=int(owner["key_epoch"]), kind="pass")
        except crypto.DecryptError:
            raise UnlockFailed("the current passphrase is wrong") from None
        kdf = crypto.new_kdf()
        kek = limits.run_kdf(lambda: crypto.kek_from_passphrase(new, kdf))
        audit.emit("personal_kb.passphrase_changed", tenant_id=tenant_id, owner_hash=audit.owner_hash(owner_id),
                   key_epoch=int(owner["key_epoch"]))
        owner["wraps"]["pass"] = {"kdf": kdf, **crypto.wrap(dek, kek, owner_id=owner_id,
                                                           epoch=int(owner["key_epoch"]), kind="pass")}
        write_owner(tenant_id, owner_id, owner)


# ---- rekey (ADR-2326 item 8) --------------------------------------------------------------------

def rekey(tenant_id: str, owner_id: str, keys: Keyring, passphrase: str, *, session_key: str = "",
          surface: str = "console", sweep: bool = True,
          on_switched: Callable[[Keyring], None] | None = None) -> tuple[Keyring, str]:
    """New DEK, epoch, device key and recovery code; old wraps destroyed at once; then sweep.
    Returns (new keyring, new recovery code). *on_switched* runs inside the owner lock right after
    ``owner.json`` names the new epoch (the console updates its unlocked sessions there, so no request in
    between sees an epoch mismatch). ``sweep=False`` leaves the re-encryption to the caller (the console
    runs it in the background, so the new recovery code reaches the owner at once)."""
    with _guarded_check(tenant_id, owner_id, session_key, surface), \
            locks.exclusive(paths.lock_file(tenant_id, owner_id)):
        owner = read_owner(tenant_id, owner_id)
        _not_erasing(tenant_id, owner_id, owner)
        if owner.get("prev"):
            raise OwnerError("a previous key change is still being finished; unlock again first")
        if int(owner["key_epoch"]) != keys.epoch:
            raise OwnerError("the key changed meanwhile; unlock again")
        w = owner.get("wraps", {}).get("pass")
        if not w:
            raise OwnerError("this space has no passphrase")
        try:
            kek_old = limits.run_kdf(lambda: crypto.kek_from_passphrase(passphrase, w["kdf"]))
            crypto.unwrap(w, kek_old, owner_id=owner_id, epoch=keys.epoch, kind="pass")
        except crypto.DecryptError:
            raise UnlockFailed("the passphrase is wrong") from None
        old_epoch, new_epoch = keys.epoch, keys.epoch + 1
        new = Keyring(epoch=new_epoch, dek=crypto.new_key(), prev={old_epoch: keys.dek})
        kdf = crypto.new_kdf()
        kek = limits.run_kdf(lambda: crypto.kek_from_passphrase(passphrase, kdf))
        code, rkey = crypto.new_recovery_code()
        wraps: dict[str, Any] = {
            "pass": {"kdf": kdf, **crypto.wrap(new.dek, kek, owner_id=owner_id, epoch=new_epoch, kind="pass")},
            "recovery": crypto.wrap(new.dek, rkey, owner_id=owner_id, epoch=new_epoch, kind="recovery"),
        }
        audit.emit("personal_kb.key_rotated", tenant_id=tenant_id, owner_hash=audit.owner_hash(owner_id),
                   key_epoch=new_epoch)
        had_device = owner.get("unlock_mode") == "convenient" and "device" in owner.get("wraps", {})
        if had_device:
            dev = _write_device_key(tenant_id, owner_id, new_epoch)
            wraps["device"] = crypto.wrap(new.dek, dev, owner_id=owner_id, epoch=new_epoch, kind="device")
        owner["prev"] = {"epoch": old_epoch, **crypto.wrap(keys.dek, new.dek, owner_id=owner_id, epoch=new_epoch,
                                                          kind=f"prev-{old_epoch}")}
        owner["wraps"] = wraps
        owner["key_epoch"] = new_epoch
        write_owner(tenant_id, owner_id, owner)
        if on_switched is not None:
            with contextlib.suppress(Exception):
                on_switched(new)
        # only now that owner.json names the new epoch does the old key file go
        for old in paths.device_key_files(tenant_id, owner_id):
            if old != paths.device_key_file(tenant_id, owner_id, new_epoch):
                with contextlib.suppress(FileNotFoundError):
                    old.unlink()
    # Pending chat-link requests are sealed to the owner's key: they are re-sealed under the new epoch here, while the
    # old key is still in memory (the sweep does not cover _pairs.json).
    with contextlib.suppress(Exception):
        _reseal_pending_pairs(tenant_id, owner_id, keys, new)
    if sweep:
        with contextlib.suppress(Exception):   # the sweep resumes at the next unlock; the code must reach the owner
            resume_rekey_if_pending(tenant_id, owner_id, new)
    return new, code


def resume_rekey_if_pending(tenant_id: str, owner_id: str, keys: Keyring) -> bool:
    """Finish an interrupted sweep: re-encrypt, then destroy ``prev``. Returns True when it ran."""
    owner = read_owner(tenant_id, owner_id)
    if not owner.get("prev") or not keys.prev:
        return False
    store = PersonalStore.open(tenant_id, owner_id, keys, expire=False)
    store.sweep_epoch()
    if store.problems:
        return False   # an unreadable object keeps the old key until the owner removes it (status bar, drop_unreadable)
    with locks.exclusive(paths.lock_file(tenant_id, owner_id)):
        owner = read_owner(tenant_id, owner_id)
        if int(owner.get("key_epoch", 0)) == keys.epoch:
            owner.pop("prev", None)
            write_owner(tenant_id, owner_id, owner)
    keys.prev.clear()
    return True


# ---- binding and pairing (ADR-2326 items 5-7) --------------------------------------------------

def mask_uid(channel: str, uid: str) -> str:
    uid = str(uid or "")
    if uid.isdigit() or channel in ("discord", "telegram"):
        return "…" + uid[-4:]
    return uid[:3] + "…"


def _check_bindable(channel: str, uid: str, chat_type: str) -> None:
    if chat_type != "private":
        raise OwnerError("use a private chat")
    if channel not in AUTHENTICATED_SENDER_CHANNELS:
        raise OwnerError("this chat channel cannot be linked")
    if not uid or str(uid).strip().lower() in ("unknown", "none", "null", "anon"):
        raise OwnerError("this chat account cannot be linked")


def resolve_owner(tenant_id: str, *, channel: str, uid: str) -> str | None:
    if not paths.root(tenant_id).is_dir() or not uid:
        return None
    idx = _index(tenant_id)
    ikey = identity_key(tenant_id, channel, uid)
    if ikey in idx.get("alias_bind", {}) and not _alias_live(idx, ikey):
        return None
    oid = idx["bindings"].get(ikey)
    if oid and is_ulid(oid) and paths.owner_json(tenant_id, oid).exists():
        return oid
    return None


def owners_for_bare_uid(tenant_id: str, uid: str) -> list[str]:
    """Owners bound to *uid* on any authenticated channel (a bare-uid Art. 17 request)."""
    out = []
    for ch in sorted(AUTHENTICATED_SENDER_CHANNELS):
        oid = resolve_owner(tenant_id, channel=ch, uid=uid)
        if oid and oid not in out:
            out.append(oid)
    return out


def _prune(pairs: dict[str, Any], ts: float) -> dict[str, Any]:
    pairs["codes"] = {k: v for k, v in pairs.get("codes", {}).items() if float(v.get("expires", 0)) > ts}
    pairs["senders"] = {k: [t for t in v if t > ts - 600] for k, v in pairs.get("senders", {}).items()
                        if any(t > ts - 600 for t in v)}
    return pairs


def _seal_pending(tenant_id: str, owner_id: str, pair_id: str, data: dict[str, str]) -> dict[str, str] | None:
    """Encrypt the redeemer's raw identity to the owner (device-wrapped key); never plaintext on disk."""
    try:
        keys = unlock_with_device(tenant_id, owner_id, surface="pairing")
    except OwnerError:
        return None
    blob = crypto.seal(keys.dek, json.dumps(data).encode("utf-8"),
                       aad_bytes=crypto.aad("pair", owner_id, pair_id, keys.epoch), epoch=keys.epoch)
    return {"blob": b64(blob)}


def _open_pending(store: PersonalStore, pair_id: str, sealed: dict[str, str]) -> dict[str, str]:
    blob = base64.b64decode(sealed["blob"])
    ep = crypto.blob_epoch(blob)
    return json.loads(crypto.open_(store.keys.key_for(ep), blob, aad_bytes=crypto.aad("pair", store.owner_id,
                                                                                         pair_id, ep)))


def purge_pairs_for(tenant_id: str, *, channel: str | None = None, uid: str) -> int:
    """Art. 17: drop pending pair records and sender counters of an identity."""
    if not paths.root(tenant_id).is_dir():
        return 0
    keys = {identity_key(tenant_id, ch, uid) for ch in ([channel] if channel else sorted(AUTHENTICATED_SENDER_CHANNELS))}
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        pairs = _read_json(_pairs_file(tenant_id), None)
        if not pairs:
            return 0
        before = len(pairs.get("codes", {})) + len(pairs.get("senders", {}))
        pairs["codes"] = {k: v for k, v in pairs.get("codes", {}).items() if v.get("skey") not in keys}
        pairs["senders"] = {k: v for k, v in pairs.get("senders", {}).items() if k not in keys}
        removed = before - len(pairs["codes"]) - len(pairs["senders"])
        if removed:
            locks.atomic_write(_pairs_file(tenant_id), json.dumps(pairs).encode("utf-8"))
        return removed


def issue_pair_code(tenant_id: str, owner_id: str, now: float | None = None) -> str:
    ts = time.time() if now is None else now
    if owner_info(tenant_id, owner_id).get("unlock_mode") == "strict":
        raise OwnerError("this space is console only, so no chat account can be linked")
    alphabet = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(8))
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        pairs = _prune(_read_json(_pairs_file(tenant_id), {"codes": {}, "senders": {}}), ts)
        audit.emit("personal_kb.pair_issued", tenant_id=tenant_id, owner_hash=audit.owner_hash(owner_id))
        pairs["codes"][hashlib.sha256(code.encode()).hexdigest()] = {
            "owner_id": owner_id, "expires": ts + PAIR_TTL_S, "attempts": 0, "state": "issued",
            "pair_id": secrets.token_hex(8)}
        locks.atomic_write(_pairs_file(tenant_id), json.dumps(pairs).encode("utf-8"))
    return code


def redeem_pair_code(tenant_id: str, code: str, *, channel: str, uid: str, display: str, chat_type: str,
                     chat_key: str = "", now: float | None = None) -> None:
    """``/me link <code>``: records a pending binding the owner must confirm in the console."""
    ts = time.time() if now is None else now
    if not paths.root(tenant_id).is_dir():
        raise OwnerError("this code is not valid")
    code = re.sub(r"[\s-]", "", str(code or "")).upper()
    h = hashlib.sha256(code.encode()).hexdigest()
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        pairs = _prune(_read_json(_pairs_file(tenant_id), {"codes": {}, "senders": {}}), ts)
        codes = pairs.setdefault("codes", {})
        if chat_type != "private":
            if codes.pop(h, None) is not None:
                locks.atomic_write(_pairs_file(tenant_id), json.dumps(pairs).encode("utf-8"))
            with contextlib.suppress(audit.PersonalKbAuditError):
                audit.emit("personal_kb.pair_failed", tenant_id=tenant_id, channel=channel, reason_code="not_private")
            raise OwnerError("use a private chat; this code is now invalid")
        skey = identity_key(tenant_id, channel, uid)
        recent = [t for t in pairs.setdefault("senders", {}).get(skey, []) if t > ts - 600]
        if len(recent) >= SENDER_REDEEMS_PER_10_MIN:
            raise OwnerError("too many attempts; wait a few minutes")
        pairs["senders"][skey] = recent + [ts]
        rec = codes.get(h)
        problem = None
        if rec is None or float(rec.get("expires", 0)) <= ts or rec.get("state") != "issued":
            problem = "invalid"
        else:
            try:
                _check_bindable(channel, uid, chat_type)
            except OwnerError:
                problem = "not_bindable"
            if problem is None and skey in _index(tenant_id)["bindings"]:
                problem = "already_bound"
        if problem:
            if rec is not None:
                rec["attempts"] = int(rec.get("attempts", 0)) + 1
                if rec["attempts"] >= PAIR_ATTEMPTS:
                    codes.pop(h, None)
            locks.atomic_write(_pairs_file(tenant_id), json.dumps(pairs).encode("utf-8"))
            with contextlib.suppress(audit.PersonalKbAuditError):
                audit.emit("personal_kb.pair_failed", tenant_id=tenant_id, channel=channel, reason_code=problem)
            raise OwnerError("this chat account is already linked" if problem == "already_bound"
                             else "this code is not valid")
        sealed = _seal_pending(tenant_id, rec["owner_id"], rec["pair_id"],
                               {"uid": uid, "channel": channel, "display": str(display or "")[:80],
                                "chat_key": str(chat_key or "")[:200]})
        if sealed is None:
            codes.pop(h, None)
            locks.atomic_write(_pairs_file(tenant_id), json.dumps(pairs).encode("utf-8"))
            raise OwnerError("this space cannot be linked to a chat account")
        # only the keyed identity hash stays readable (Art. 17 purge); channel, uid and name are sealed
        rec.update({"state": "redeemed", "skey": skey, "sealed": sealed})
        locks.atomic_write(_pairs_file(tenant_id), json.dumps(pairs).encode("utf-8"))


def _reseal_pending_pairs(tenant_id: str, owner_id: str, old: Keyring, new: Keyring) -> int:
    """Move every sealed pending link request of this owner from the old key epoch to the new one."""
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        pairs = _read_json(_pairs_file(tenant_id), None)
        if not pairs:
            return 0
        n = 0
        for v in pairs.get("codes", {}).values():
            if v.get("owner_id") != owner_id or not v.get("sealed"):
                continue
            try:
                blob = base64.b64decode(v["sealed"]["blob"])
                ep = crypto.blob_epoch(blob)
                raw = crypto.open_(old.key_for(ep), blob, aad_bytes=crypto.aad("pair", owner_id, v["pair_id"], ep))
            except (crypto.DecryptError, KeyError, ValueError):
                continue
            nb = crypto.seal(new.dek, raw, aad_bytes=crypto.aad("pair", owner_id, v["pair_id"], new.epoch),
                             epoch=new.epoch)
            v["sealed"] = {"blob": b64(nb)}
            n += 1
        if n:
            locks.atomic_write(_pairs_file(tenant_id), json.dumps(pairs).encode("utf-8"))
        return n


def pending_pairs(tenant_id: str, store: PersonalStore, now: float | None = None) -> list[dict[str, str]]:
    ts = time.time() if now is None else now
    pairs = _read_json(_pairs_file(tenant_id), {"codes": {}})
    out = []
    for v in pairs.get("codes", {}).values():
        if v.get("owner_id") != store.owner_id or v.get("state") != "redeemed" or float(v.get("expires", 0)) <= ts:
            continue
        try:
            raw = _open_pending(store, v["pair_id"], v["sealed"])
        except (crypto.DecryptError, KeyError, ValueError):
            continue
        ch = str(raw.get("channel") or "")
        out.append({"pair_id": v["pair_id"], "channel": ch, "display": raw.get("display", ""),
                    "masked": mask_uid(ch, str(raw.get("uid") or ""))})
    return out


def confirm_pair(tenant_id: str, store: PersonalStore, pair_id: str, now: float | None = None) -> dict[str, str]:
    ts = time.time() if now is None else now
    owner_id = store.owner_id
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        pairs = _read_json(_pairs_file(tenant_id), {"codes": {}})
        hit = next(((h, v) for h, v in pairs.get("codes", {}).items()
                    if v.get("pair_id") == pair_id and v.get("owner_id") == owner_id
                    and v.get("state") == "redeemed" and float(v.get("expires", 0)) > ts), None)
        if hit is None:
            raise OwnerError("this link request is not valid any more")
        h, rec = hit
        try:
            raw = _open_pending(store, rec["pair_id"], rec["sealed"])
        except (crypto.DecryptError, KeyError, ValueError):
            raise OwnerError("this link request is not valid any more") from None
        rec = {**rec, "uid": raw["uid"], "channel": str(raw.get("channel") or ""), "display": raw.get("display", ""),
               "chat_key": raw.get("chat_key", "")}
        idx = _index(tenant_id)
        ikey = identity_key(tenant_id, rec["channel"], rec["uid"])
        if ikey in idx["bindings"]:
            raise OwnerError("this chat account is already linked")
        audit.emit("personal_kb.pair_confirmed", tenant_id=tenant_id, owner_hash=audit.owner_hash(owner_id),
                   channel=rec["channel"])
        binding = {"channel": rec["channel"], "uid": rec["uid"], "display": rec.get("display", "")}
        store.update_meta(lambda m: m.setdefault("bindings", []).append(binding))
        _bind_index(tenant_id, rec["channel"], rec["uid"], owner_id)
        pairs["codes"].pop(h, None)
        locks.atomic_write(_pairs_file(tenant_id), json.dumps(pairs).encode("utf-8"))
    return {"channel": binding["channel"], "display": binding["display"], "masked": mask_uid(rec["channel"], rec["uid"]),
            "_chat_key": rec.get("chat_key", "")}


def unbind(tenant_id: str, store: PersonalStore, channel: str, uid: str) -> bool:
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        idx = _index(tenant_id)
        ikey = identity_key(tenant_id, channel, uid)
        if idx["bindings"].get(ikey) != store.owner_id:
            return False
        audit.emit("personal_kb.identity_unbound", tenant_id=tenant_id, owner_hash=store.owner_hash, channel=channel)
        idx["bindings"].pop(ikey, None)
        for akey in idx.get("alias_of", {}).pop(ikey, []):   # the aliases of this chat account go with it
            if idx["bindings"].get(akey) == store.owner_id:
                idx["bindings"].pop(akey, None)
            idx.get("alias_bind", {}).pop(akey, None)
        locks.atomic_write(paths.index_file(tenant_id), json.dumps(idx).encode("utf-8"))

        def _drop(m: dict[str, Any]) -> None:
            m["bindings"] = [b for b in m.get("bindings", []) if not (b.get("channel") == channel and b.get("uid") == uid)]
        store.update_meta(_drop)
    return True


# ---- helpers ------------------------------------------------------------------------------------

def _remove_tree(d) -> None:
    import shutil

    if d.exists() and not paths.is_link(d):
        shutil.rmtree(d, ignore_errors=True)


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")
