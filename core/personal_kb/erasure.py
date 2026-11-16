"""Erasure by crypto-shredding (ADR-2326 item 9).

Two routes, one set of steps:

* **Entry points** (console route with the passphrase, ``/me erase`` for bridge-only owners, the
  operator CLI) call :func:`erase_owner` directly. No handle, uid or owner id reaches the
  orchestrator's ``erasure.requested`` record for those requests.
* **Generic Art. 17 requests** reach :class:`PersonalKbHandler` through the orchestrator with a
  bound uid, bare or as ``channel:uid``. The handler never accepts an owner id, a handle or a
  ``pkb:`` value: those would let any console member erase another owner's space.

Order: audit started -> lock, revocation sentinel, release -> destroy wraps and device key -> delete
the whole owner directory, handle counter, pair codes and index entries -> drop this process's cache
-> audit deleted. :func:`resume_pending` finishes a run a crash interrupted (console start, gateway
start, every ``store.open`` caller that wants it).
"""
from __future__ import annotations

import contextlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import audit, locks, owner, paths, session
from .ids import is_ulid

LAYER_ID = "L-personal-kb"
SENTINEL_KEEP_S = 30 * 24 * 3600


def _count_objects(d: Path) -> int:
    o = d / "objects"
    return sum(1 for _ in o.glob("*.enc")) if o.is_dir() else 0


def _rmtree(d: Path) -> None:
    delay = 0.05
    for attempt in range(6):
        if not d.exists():
            return
        try:
            shutil.rmtree(d)
            return
        except OSError:
            if attempt == 5:
                raise
            time.sleep(delay)
            delay *= 2


def erase_owner(tenant_id: str, owner_id: str, *, now: float | None = None) -> int:
    """Erase one owner's space. Idempotent. Returns the number of objects removed."""
    if not is_ulid(owner_id):
        raise ValueError("malformed owner id")
    ts = time.time() if now is None else now
    oh = audit.owner_hash(owner_id)
    audit.emit("personal_kb.erasure_started", tenant_id=tenant_id, owner_hash=oh)
    rdir = paths.revoked_dir(tenant_id)
    rdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    d = paths.owner_dir(tenant_id, owner_id)
    marker = paths.revoked_marker(tenant_id, oh)
    # The sentinel goes first, outside the lock: every writer checks it INSIDE the lock, so once it
    # exists no new write starts, and a long-held lock (a rekey sweep) cannot keep the erasure out.
    locks.atomic_write(marker, json.dumps({"at": ts, "owner_id": owner_id, "done": False}).encode())
    if d.is_dir() and not paths.is_link(d):
        with contextlib.suppress(locks.LockTimeout):
            with locks.exclusive(paths.lock_file(tenant_id, owner_id), timeout=60.0):
                with contextlib.suppress(Exception):
                    o = json.loads(paths.owner_json(tenant_id, owner_id).read_text(encoding="utf-8"))
                    o["erasing"] = True
                    locks.atomic_write(paths.owner_json(tenant_id, owner_id), json.dumps(o).encode())
    return _finish(tenant_id, owner_id)


def _finish(tenant_id: str, owner_id: str) -> int:
    oh = audit.owner_hash(owner_id)
    d = paths.owner_dir(tenant_id, owner_id)
    handle = None
    with contextlib.suppress(Exception):
        handle = json.loads(paths.owner_json(tenant_id, owner_id).read_text(encoding="utf-8")).get("handle")
    n = _count_objects(d)
    # (3) destroy the key material first: from here on the ciphertext is unreadable.
    for kf in paths.device_key_files(tenant_id, owner_id):
        with contextlib.suppress(FileNotFoundError):
            kf.unlink()
    with contextlib.suppress(Exception):
        o = json.loads(paths.owner_json(tenant_id, owner_id).read_text(encoding="utf-8"))
        o["wraps"], o["erasing"] = {}, True
        o.pop("prev", None)
        locks.atomic_write(paths.owner_json(tenant_id, owner_id), json.dumps(o).encode())
    # (4) the whole directory, then the tenant-level traces.
    if d.exists() and not paths.is_link(d):
        _rmtree(d)
    root = paths.root(tenant_id)
    if root.is_dir():
        with locks.exclusive(paths.tenant_lock_file(tenant_id)):
            # Pair records and counters first, the index second: a crash between the two writes then leaves the
            # bindings in place, so a resumed run still finds every identity to clean up.
            idx = owner._index(tenant_id)
            pf = owner._pairs_file(tenant_id)
            pairs = owner._read_json(pf, None)
            # identity keys of this owner: confirmed bindings AND redeemed-but-unconfirmed link requests
            bound_keys = {k for k, v in idx["bindings"].items() if v == owner_id}
            if pairs:
                bound_keys |= {v["skey"] for v in pairs.get("codes", {}).values()
                               if v.get("owner_id") == owner_id and v.get("skey")}
                codes = {k: v for k, v in pairs.get("codes", {}).items() if v.get("owner_id") != owner_id}
                senders = {k: v for k, v in pairs.get("senders", {}).items() if k not in bound_keys}
                if codes != pairs.get("codes") or senders != pairs.get("senders", {}):
                    pairs["codes"], pairs["senders"] = codes, senders
                    locks.atomic_write(pf, json.dumps(pairs).encode("utf-8"))
            before = len(idx["bindings"])
            idx["bindings"] = {k: v for k, v in idx["bindings"].items() if v != owner_id}
            idx["alias_of"] = {k: v for k, v in idx.get("alias_of", {}).items() if k in idx["bindings"]}
            idx["alias_bind"] = {k: v for k, v in idx.get("alias_bind", {}).items() if k in idx["bindings"]}
            if len(idx["bindings"]) != before:
                locks.atomic_write(paths.index_file(tenant_id), json.dumps(idx).encode("utf-8"))
            if handle:
                lf = paths.lockouts_file(tenant_id)
                data = owner._read_json(lf, None)
                hk = owner.handle_key(tenant_id, handle)
                if data and data.pop(hk, None) is not None:
                    locks.atomic_write(lf, json.dumps(data).encode("utf-8"))
    # (5) this process's caches; other processes see the sentinel.
    session.drop_owner(tenant_id, owner_id)
    # (6)
    audit.emit("erasure.personal_kb_deleted", tenant_id=tenant_id, owner_hash=oh, objects_deleted=n, layer=LAYER_ID)
    with contextlib.suppress(Exception):
        locks.atomic_write(paths.revoked_marker(tenant_id, oh),
                           json.dumps({"at": time.time(), "owner_id": owner_id, "done": True}).encode())
    return n


RESUME_MIN_AGE_S = 120.0


def resume_all_tenants() -> int:
    """Host start: finish interrupted erasures in every tenant (console and gateway lifespans)."""
    from core.paths.tenant import corvin_home  # noqa: PLC0415

    n = 0
    tenants = corvin_home() / "tenants"
    if not tenants.is_dir():
        return 0
    for t in sorted(tenants.iterdir()):
        if (t / paths.ROOT_NAME / "_revoked").is_dir():
            with contextlib.suppress(Exception):
                n += resume_pending(t.name, min_age_s=0.0)
    return n


def resume_pending(tenant_id: str, *, now: float | None = None, min_age_s: float = RESUME_MIN_AGE_S) -> int:
    """Finish every erasure whose sentinel exists while its owner directory still does; drop old sentinels."""
    ts = time.time() if now is None else now
    rdir = paths.revoked_dir(tenant_id)
    if not rdir.is_dir():
        return 0
    finished = 0
    for m in sorted(rdir.iterdir()):
        try:
            info = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        oid = info.get("owner_id")
        # an unfinished run: the directory may already be gone while index, pair and key leftovers remain
        # a younger unfinished run probably belongs to a process that is still erasing: leave it to that process
        if not info.get("done", True) and ts - float(info.get("at", 0)) < min_age_s:
            continue
        if is_ulid(oid) and audit.owner_hash(oid) == m.name and (
                not info.get("done", True) or paths.owner_dir(tenant_id, oid).exists()):
            _finish(tenant_id, oid)
            finished += 1
        elif float(info.get("at", ts)) < ts - SENTINEL_KEEP_S and not (is_ulid(oid) and paths.owner_dir(tenant_id, oid).exists()):
            with contextlib.suppress(OSError):
                m.unlink()
    return finished


def owners_for_subject(tenant_id: str, subject_id: str) -> list[str]:
    """Owners an orchestrator subject refers to: a bound ``channel:uid`` or a bare uid. Never an owner
    id, a handle or a ``pkb:`` value."""
    s = str(subject_id or "").strip()
    if not s or s.startswith("pkb:") or is_ulid(s):
        return []
    if ":" in s:
        channel, uid = s.split(":", 1)
        # Teams UPNs are stored lower-cased: a request typed in another case still names the same identity
        found = {owner.resolve_owner(tenant_id, channel=channel, uid=u) for u in {uid, uid.lower()}} - {None}
        return sorted(found)
    found = set(owner.owners_for_bare_uid(tenant_id, s)) | set(owner.owners_for_bare_uid(tenant_id, s.lower()))
    return sorted(found)


def _orchestrator_types() -> Any:
    try:
        import erasure_orchestrator as eo  # type: ignore[import-not-found]
        return eo
    except ImportError:
        shared = Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges" / "shared"
        if str(shared) not in sys.path:
            sys.path.append(str(shared))
        import erasure_orchestrator as eo  # type: ignore[import-not-found]
        return eo


class PersonalKbHandler:
    """GDPR Art. 17 handler for the orchestrator (generic requests by bound uid)."""

    layer_id: str = LAYER_ID

    def __init__(self, tenant_id: str = "_default") -> None:
        self.tenant_id = tenant_id

    def purge(self, subject_id: str, request_id: str) -> Any:
        eo = _orchestrator_types()
        t0 = time.time()
        try:
            resume_pending(self.tenant_id)
            owners = owners_for_subject(self.tenant_id, subject_id)
            s = str(subject_id or "")
            if s and not s.startswith("pkb:") and not is_ulid(s):
                ch, _, uid = s.partition(":") if ":" in s else (None, "", s)
                with contextlib.suppress(Exception):
                    owner.purge_pairs_for(self.tenant_id, channel=ch, uid=uid)
            if not owners:
                absent = not paths.root(self.tenant_id).is_dir()
                return eo.ErasureLayerResult(
                    layer_id=self.layer_id, status=eo.LayerStatus.SKIPPED, count=0,
                    reason="no personal space bound to this subject",
                    code=(eo.ReasonCode.STORE_ABSENT if absent else eo.ReasonCode.STORE_EMPTY).value,
                    duration_ms=int((time.time() - t0) * 1000))
            total = sum(erase_owner(self.tenant_id, oid) for oid in owners)
            return eo.ErasureLayerResult(
                layer_id=self.layer_id, status=eo.LayerStatus.APPLIED, count=max(total, len(owners)),
                reason=f"erased {len(owners)} personal space(s)", code=eo.ReasonCode.DELETED.value,
                duration_ms=int((time.time() - t0) * 1000))
        except Exception as exc:  # noqa: BLE001
            return eo.ErasureLayerResult(
                layer_id=self.layer_id, status=eo.LayerStatus.FAILED, count=0,
                reason=f"{type(exc).__name__}", code=eo.ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000))
