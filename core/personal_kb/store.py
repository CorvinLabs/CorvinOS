"""Encrypted per-owner object store (ADR-2325).

One object file per entity, AES-256-GCM with associated data ``object|owner|oid|epoch|version``;
an encrypted manifest maps uid -> (oid, version, epoch, ciphertext hash) and detects a deleted or
rolled-back object; an encrypted ``meta`` holds consent, settings, counters, bindings and rate
state. Every write follows ADR-2325 item 4:

    lock -> revocation and epoch check -> next version -> audit intent -> version copy -> object
    -> manifest     (a failure after the audit records ``personal_kb.write_failed``)

Readers keep an in-memory index and reload it when the manifest counter moves (two processes).
No plaintext index, cache or export file is written. Writers never create the owner directory.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from . import audit, crypto, locks, model, paths
from .ids import new_oid

MAX_OBJECTS = 20_000
MAX_PLAINTEXT_BYTES = 64 * 1024 * 1024
MAX_VERSIONS = 20
MANUAL_ADDS_PER_HOUR = 60


class StoreError(RuntimeError):
    """Base class for store failures (never carries content)."""


class Revoked(StoreError):
    """The owner is being erased or is gone."""


class EpochChanged(StoreError):
    """The data key rotated since this store was opened; unwrap again."""


class QuotaExceeded(StoreError):
    pass


class VersionConflict(StoreError):
    pass


class NotFound(StoreError):
    pass


@dataclass
class Keyring:
    """The owner's data keys by epoch. ``prev`` holds the old key during a rekey sweep."""
    epoch: int
    dek: bytes
    prev: dict[int, bytes] = field(default_factory=dict)

    def key_for(self, epoch: int) -> bytes:
        if epoch == self.epoch:
            return self.dek
        if epoch in self.prev:
            return self.prev[epoch]
        raise crypto.DecryptError("no key for this epoch")


def read_owner(tenant_id: str, owner_id: str) -> dict[str, Any]:
    p = paths.owner_json(tenant_id, owner_id)
    paths.assert_no_links(p, paths.root(tenant_id))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Revoked("owner not found") from exc


def write_owner(tenant_id: str, owner_id: str, data: dict[str, Any]) -> None:
    p = paths.owner_json(tenant_id, owner_id)
    if not p.parent.is_dir():
        raise Revoked("owner directory missing")
    paths.assert_no_links(p.parent, paths.root(tenant_id))
    locks.atomic_write(p, json.dumps(data, sort_keys=True, indent=1).encode("utf-8"))


def is_revoked(tenant_id: str, owner_id: str) -> bool:
    if paths.revoked_marker(tenant_id, audit.owner_hash(owner_id)).exists():
        return True
    return not paths.owner_json(tenant_id, owner_id).exists()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _meta_aad(owner_id: str, epoch: int) -> bytes:
    return crypto.aad("meta", owner_id, epoch)


def _manifest_aad(owner_id: str, epoch: int) -> bytes:
    return crypto.aad("manifest", owner_id, epoch)


def _object_aad(owner_id: str, oid: str, epoch: int, version: int) -> bytes:
    return crypto.aad("object", owner_id, oid, epoch, version)


def _policy_aad(owner_id: str, epoch: int, n: int) -> bytes:
    return crypto.aad("policy", owner_id, epoch, n)


def empty_meta(*, consent_version: str, auto_capture: bool, now: float) -> dict[str, Any]:
    return {
        "consent": {"version": consent_version, "at": now},
        "auto_capture": bool(auto_capture),
        "evolution": True,
        "counters": {},
        "fingerprint_key": base64.b64encode(crypto.new_key()).decode("ascii"),
        "fingerprints": [],
        "bindings": [],
        "rate": {"manual_adds": [], "extraction_calls": [], "decoder_calls": [], "captured": {}},
        "policy_current": 0,
        "policy_pending": None,
        "frozen": False,
        "review_log": [],
        "decoder_errors": [],
    }


def empty_manifest() -> dict[str, Any]:
    return {"counter": 0, "objects": {}, "bytes": 0, "policy_versions": 0}


def write_encrypted_json(path: Path, keys: Keyring, data: dict[str, Any], aad_fn: Callable[[int], bytes]) -> bytes:
    blob = crypto.seal(keys.dek, json.dumps(data, sort_keys=True).encode("utf-8"),
                       aad_bytes=aad_fn(keys.epoch), epoch=keys.epoch)
    locks.atomic_write(path, blob)
    return blob


def read_encrypted_json(path: Path, keys: Keyring, aad_fn: Callable[[int], bytes]) -> dict[str, Any]:
    blob = path.read_bytes()
    ep = crypto.blob_epoch(blob)
    return json.loads(crypto.open_(keys.key_for(ep), blob, aad_bytes=aad_fn(ep)))


class PersonalStore:
    """The decrypted, in-memory view of one owner's store plus its write path."""

    def __init__(self, tenant_id: str, owner_id: str, keys: Keyring) -> None:
        self.tenant_id = tenant_id
        self.owner_id = owner_id
        self.keys = keys
        self.owner_hash = audit.owner_hash(owner_id)
        self.dir = paths.owner_dir(tenant_id, owner_id)
        self.meta: dict[str, Any] = {}
        self.manifest: dict[str, Any] = empty_manifest()
        self.index: dict[str, dict[str, Any]] = {}
        self.problems: dict[str, str] = {}  # uid -> "missing" | "corrupt"
        # One instance may serve concurrent requests (console threads): every read and write holds it.
        self._mu = threading.RLock()
        self._repaired: list[str] = []

    # ---- opening and reloading -------------------------------------------------------------
    @classmethod
    def open(cls, tenant_id: str, owner_id: str, keys: Keyring, *, expire: bool = True) -> "PersonalStore":
        from . import erasure  # noqa: PLC0415 - finish any erasure a crash interrupted (ADR-2326 item 9)

        with contextlib.suppress(Exception):
            erasure.resume_pending(tenant_id)
        s = cls(tenant_id, owner_id, keys)
        if is_revoked(tenant_id, owner_id):
            raise Revoked("owner is erased or being erased")
        paths.assert_no_links(s.dir, paths.root(tenant_id))
        s._load()
        if s._repaired:
            # What looked like a crash may be another process mid-write: decide again UNDER the lock, from
            # disk, and write only what is still one version ahead.
            with contextlib.suppress(Revoked, EpochChanged):
                with s._locked(reload=False):
                    s._load()
                    if s._repaired:
                        s._write_manifest()
        if expire:
            with contextlib.suppress(Revoked, EpochChanged):
                s.expire_due()
        return s

    def _load(self) -> None:
        with self._mu:
            meta = read_encrypted_json(paths.meta_file(self.tenant_id, self.owner_id), self.keys,
                                       lambda e: _meta_aad(self.owner_id, e))
            mf = paths.manifest_file(self.tenant_id, self.owner_id)
            manifest = read_encrypted_json(mf, self.keys, lambda e: _manifest_aad(self.owner_id, e))
            index: dict[str, dict[str, Any]] = {}
            self.problems, self._repaired = {}, []
            for uid, rec in list(manifest["objects"].items()):
                ent = self._read_object(uid, rec)
                if ent is not None:
                    index[uid] = ent
            if self._repaired:
                manifest["bytes"] = int(manifest.get("bytes", 0)) + getattr(self, "_repaired_bytes", 0)
            self._repaired_bytes = 0
            self.meta, self.manifest, self.index = meta, manifest, index

    def _read_object(self, uid: str, rec: dict[str, Any]) -> dict[str, Any] | None:
        p = paths.object_file(self.tenant_id, self.owner_id, rec["oid"])
        try:
            blob = p.read_bytes()
        except FileNotFoundError:
            self.problems[uid] = "missing"
            return None
        try:
            ep = crypto.blob_epoch(blob)
            ent = json.loads(crypto.open_(self.keys.key_for(ep), blob,
                                          aad_bytes=_object_aad(self.owner_id, rec["oid"], ep, rec["version"])))
        except (crypto.DecryptError, ValueError):
            ent = self._repair_ahead(uid, rec, blob)
            if ent is None:
                self.problems[uid] = "corrupt"
            return ent
        if ent.get("uid") != uid or ent.get("version") != rec["version"]:
            self.problems[uid] = "corrupt"
            return None
        return ent

    def _repair_ahead(self, uid: str, rec: dict[str, Any], blob: bytes) -> dict[str, Any] | None:
        """A crash between writing an object and its manifest leaves the object one version ahead
        (ADR-2325 item 4). Accept it, record the version copy written before it, and mark the manifest
        for rewrite. Anything else stays "corrupt"."""
        try:
            ep = crypto.blob_epoch(blob)
            nxt = int(rec["version"]) + 1
            ent = json.loads(crypto.open_(self.keys.key_for(ep), blob,
                                          aad_bytes=_object_aad(self.owner_id, rec["oid"], ep, nxt)))
        except (crypto.DecryptError, ValueError, KeyError):
            return None
        if ent.get("uid") != uid or ent.get("version") != nxt:
            return None
        versions = list(rec.get("versions", []))
        vsizes = dict(rec.get("vsizes", {}))
        if paths.version_file(self.tenant_id, self.owner_id, rec["oid"], rec["version"]).exists():
            versions.append(int(rec["version"]))
            vsizes[str(rec["version"])] = int(rec.get("plain", 0))
        new_len = len(json.dumps(ent).encode("utf-8"))
        rec.update({"version": nxt, "epoch": ep, "sha": _sha(blob), "versions": versions, "vsizes": vsizes,
                    "plain": new_len})
        self._repaired.append(uid)
        self._repaired_bytes = getattr(self, "_repaired_bytes", 0) + new_len
        return ent

    def reload_if_changed(self) -> bool:
        mf = paths.manifest_file(self.tenant_id, self.owner_id)
        with self._mu:
            try:
                m = read_encrypted_json(mf, self.keys, lambda e: _manifest_aad(self.owner_id, e))
            except FileNotFoundError as exc:
                raise Revoked("owner directory gone") from exc
            if m.get("counter") != self.manifest.get("counter"):
                self._load()
                return True
            return False

    # ---- reads -----------------------------------------------------------------------------
    def entries(self) -> list[dict[str, Any]]:
        """A snapshot of the readable entries, taken under the in-process lock (other threads may write)."""
        with self._mu:
            return list(self.index.values())

    def list(self, *, type: str | None = None, status: str | None = None, q: str | None = None,
             review: str | None = None) -> list[dict[str, Any]]:
        out = []
        ql = (q or "").strip().lower()
        with self._mu:
            snapshot = list(self.index.values())
        for ent in snapshot:
            if type and ent["type"] != type:
                continue
            if status and ent["status"] != status:
                continue
            if review and ent.get("review") != review:
                continue
            if ql and ql not in (ent["title"] + "\n" + ent.get("body", "") + "\n" + (ent.get("key") or "")).lower():
                continue
            out.append(ent)
        out.sort(key=lambda e: (e.get("created", 0), e["uid"]))
        return out

    def get(self, uid: str) -> dict[str, Any] | None:
        return self.index.get(uid)

    def versions(self, uid: str) -> list[int]:
        rec = self.manifest["objects"].get(uid)
        return list(rec.get("versions", [])) if rec else []

    # ---- the write lock --------------------------------------------------------------------
    @contextlib.contextmanager
    def _locked(self, *, reload: bool = True) -> Iterator[None]:
        if not self.dir.is_dir():
            raise Revoked("owner directory gone")
        with self._mu, locks.exclusive(paths.lock_file(self.tenant_id, self.owner_id)):
            if is_revoked(self.tenant_id, self.owner_id):
                raise Revoked("owner is erased or being erased")
            owner = read_owner(self.tenant_id, self.owner_id)
            if owner.get("erasing"):
                raise Revoked("owner is being erased")
            if int(owner.get("key_epoch", 0)) != self.keys.epoch:
                raise EpochChanged("key epoch moved")
            # Pick up a concurrent writer's changes before deciding anything.
            if reload:
                self.reload_if_changed()
            yield

    def _write_manifest(self) -> None:
        self.manifest["counter"] = int(self.manifest.get("counter", 0)) + 1
        write_encrypted_json(paths.manifest_file(self.tenant_id, self.owner_id), self.keys, self.manifest,
                             lambda e: _manifest_aad(self.owner_id, e))

    def _write_meta(self) -> None:
        write_encrypted_json(paths.meta_file(self.tenant_id, self.owner_id), self.keys, self.meta,
                             lambda e: _meta_aad(self.owner_id, e))

    def update_meta(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        """Re-read meta under the lock, apply *fn*, write it back. Returns fn's result."""
        with self._locked():
            self.meta = read_encrypted_json(paths.meta_file(self.tenant_id, self.owner_id), self.keys,
                                            lambda e: _meta_aad(self.owner_id, e))
            result = fn(self.meta)
            self._write_meta()
            self._write_manifest()  # the counter tells other processes to reload
            return result

    # ---- writes ----------------------------------------------------------------------------
    def put(self, entity: dict[str, Any], *, expected_version: int | None = None, manual: bool = False,
            now: float | None = None) -> dict[str, Any]:
        """Create or update one entity. Returns the stored copy (with its new version)."""
        ts = time.time() if now is None else now
        ent = model.validate(entity)
        with self._locked():
            uid = ent["uid"]
            rec = self.manifest["objects"].get(uid)
            cur_version = rec["version"] if rec else 0
            if expected_version is not None and expected_version != cur_version:
                raise VersionConflict("entity changed since it was read")
            data_len = len(json.dumps(ent).encode("utf-8"))
            if rec is None and len(self.manifest["objects"]) >= MAX_OBJECTS:
                raise QuotaExceeded("the personal store is full (object limit)")
            if int(self.manifest.get("bytes", 0)) + data_len > MAX_PLAINTEXT_BYTES:
                raise QuotaExceeded("the personal store is full (size limit)")
            if manual and rec is None:
                adds = [t for t in self.meta.get("rate", {}).get("manual_adds", []) if t > ts - 3600]
                if len(adds) >= MANUAL_ADDS_PER_HOUR:
                    raise QuotaExceeded("too many new entries in the last hour")
            if rec is None and ent["type"] in model.PREFIX:
                counters = self.meta.setdefault("counters", {})
                counters[ent["type"]] = int(counters.get(ent["type"], 0)) + 1
                ent["display_id"] = f"{model.PREFIX[ent['type']]}-{counters[ent['type']]:04d}"
            elif rec is not None:
                ent["display_id"] = (self.index.get(uid) or {}).get("display_id") or ent.get("display_id")
            new_version = cur_version + 1
            ent["version"] = new_version
            ent["updated"] = ts
            audit.emit("personal_kb.entity_written", tenant_id=self.tenant_id, owner_hash=self.owner_hash,
                       uid=uid, type=ent["type"], origin=ent["origin"], version=new_version)
            try:
                oid = rec["oid"] if rec else new_oid()
                obj = paths.object_file(self.tenant_id, self.owner_id, oid)
                for sub in ("objects", "versions"):
                    d = self.dir / sub
                    if not d.is_dir():
                        d.mkdir(mode=0o700)
                versions = list(rec.get("versions", [])) if rec else []
                vsizes = dict(rec.get("vsizes", {})) if rec else {}
                pruned = 0
                if rec:
                    old = obj.read_bytes()
                    # A key change may still be sweeping: a version made from an old-key object is re-sealed under
                    # the current key here, because the sweep's work list does not know this new version file.
                    with contextlib.suppress(crypto.DecryptError, ValueError):
                        old_ep = crypto.blob_epoch(old)
                        if old_ep != self.keys.epoch:
                            pt_old = crypto.open_(self.keys.key_for(old_ep), old,
                                                  aad_bytes=_object_aad(self.owner_id, oid, old_ep, cur_version))
                            old = crypto.seal(self.keys.dek, pt_old,
                                              aad_bytes=_object_aad(self.owner_id, oid, self.keys.epoch, cur_version),
                                              epoch=self.keys.epoch)
                    locks.atomic_write(paths.version_file(self.tenant_id, self.owner_id, oid, cur_version), old)
                    versions.append(cur_version)
                    vsizes[str(cur_version)] = int(rec.get("plain", 0))
                    while len(versions) > MAX_VERSIONS:
                        gone = versions.pop(0)
                        pruned += int(vsizes.pop(str(gone), 0))
                        with contextlib.suppress(FileNotFoundError):
                            paths.version_file(self.tenant_id, self.owner_id, oid, gone).unlink()
                blob = crypto.seal(self.keys.dek, json.dumps(ent, sort_keys=True).encode("utf-8"),
                                   aad_bytes=_object_aad(self.owner_id, oid, self.keys.epoch, new_version),
                                   epoch=self.keys.epoch)
                locks.atomic_write(obj, blob)
                self.manifest["objects"][uid] = {"oid": oid, "version": new_version, "epoch": self.keys.epoch,
                                                 "sha": _sha(blob), "versions": versions, "vsizes": vsizes,
                                                 "plain": data_len}
                self.manifest["bytes"] = max(0, int(self.manifest.get("bytes", 0)) + data_len - pruned)
                if manual and rec is None:
                    self.meta.setdefault("rate", {}).setdefault("manual_adds", [])
                    self.meta["rate"]["manual_adds"] = [t for t in self.meta["rate"]["manual_adds"]
                                                        if t > ts - 3600] + [ts]
                if rec is None or manual:
                    self._write_meta()
                self._write_manifest()
            except Exception as exc:
                with contextlib.suppress(Exception):
                    audit.emit("personal_kb.write_failed", tenant_id=self.tenant_id, owner_hash=self.owner_hash,
                               uid=uid, reason_code=type(exc).__name__[:40])
                raise
            self.index[uid] = ent
            return ent

    def forget(self, uid: str, *, fingerprint: str | None = None, expected_version: int | None = None) -> None:
        """Hard-delete an entity and all its versions. *fingerprint*: a value to remember as forgotten.
        *expected_version*: delete only if nobody wrote the entity since it was read (the consistency sweep passes it, so
        a draft another process just edited is never removed on the strength of an older snapshot)."""
        with self._locked():
            rec = self.manifest["objects"].get(uid)
            if rec is None:
                raise NotFound("no such entry")
            if expected_version is not None and int(rec.get("version", 0)) != int(expected_version):
                raise VersionConflict("entity changed since it was read")
            etype = (self.index.get(uid) or {}).get("type") or "note"
            audit.emit("personal_kb.entity_forgotten", tenant_id=self.tenant_id, owner_hash=self.owner_hash,
                       uid=uid, type=etype)
            for n in rec.get("versions", []):
                with contextlib.suppress(FileNotFoundError):
                    paths.version_file(self.tenant_id, self.owner_id, rec["oid"], n).unlink()
            with contextlib.suppress(FileNotFoundError):
                paths.object_file(self.tenant_id, self.owner_id, rec["oid"]).unlink()
            del self.manifest["objects"][uid]
            freed = int(rec.get("plain", 0)) + sum(int(v) for v in rec.get("vsizes", {}).values())
            self.manifest["bytes"] = max(0, int(self.manifest.get("bytes", 0)) - freed)
            if fingerprint:
                fp = self.fingerprint(fingerprint)
                fps = [x for x in self.meta.get("fingerprints", []) if x != fp] + [fp]
                self.meta["fingerprints"] = fps[-5000:]
                self._write_meta()
            self._write_manifest()
            self.index.pop(uid, None)

    def drop_unreadable(self) -> int:
        """Remove every entry that is missing or unreadable (listed in ``problems``): such an entry is never in
        ``index``, so no other path can name it, and while it exists an interrupted key change cannot finish."""
        n = 0
        for uid in list(self.problems):
            with contextlib.suppress(NotFound):
                self.forget(uid)
                n += 1
            self.problems.pop(uid, None)
        return n

    def restore(self, uid: str, version: int) -> dict[str, Any]:
        rec = self.manifest["objects"].get(uid)
        if rec is None or version not in rec.get("versions", []):
            raise NotFound("no such version")
        blob = paths.version_file(self.tenant_id, self.owner_id, rec["oid"], version).read_bytes()
        ep = crypto.blob_epoch(blob)
        old = json.loads(crypto.open_(self.keys.key_for(ep), blob,
                                      aad_bytes=_object_aad(self.owner_id, rec["oid"], ep, version)))
        if old.get("uid") != uid:
            raise crypto.DecryptError("version does not belong to this entry")
        cur = self.index.get(uid) or {}
        # Content comes back; state does not: a task's status, a promotion and the review stay as they are now.
        for k in ("status", "task", "promoted_to", "review", "origin", "expires_at", "decoder", "links"):
            if k in cur:
                old[k] = cur[k]
        old["version"] = rec["version"]
        return self.put(old, expected_version=rec["version"])

    # ---- fingerprints ----------------------------------------------------------------------
    def fingerprint(self, value: str) -> str:
        key = base64.b64decode(self.meta["fingerprint_key"])
        return crypto.hmac_hex(key, normalise_for_fingerprint(value))

    def is_forgotten(self, value: str) -> bool:
        return self.fingerprint(value) in set(self.meta.get("fingerprints", []))

    # ---- expiry ----------------------------------------------------------------------------
    def expire_due(self, now: float | None = None) -> int:
        ts = time.time() if now is None else now
        due = [e["uid"] for e in self.entries()
               if e.get("expires_at") and e.get("review") != "accepted" and float(e["expires_at"]) <= ts]
        for uid in due:
            with contextlib.suppress(NotFound):
                self.forget(uid)
        if due:
            audit.emit("personal_kb.expired", tenant_id=self.tenant_id, owner_hash=self.owner_hash, count=len(due))
        return len(due)

    # ---- export ----------------------------------------------------------------------------
    def export_bytes(self) -> bytes:
        """A Markdown zip built in memory (kb-compatible frontmatter as JSON, which is valid YAML)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for ent in self.list():
                front = {k: ent.get(k) for k in ("uid", "display_id", "type", "status", "origin", "review", "key",
                                                  "links", "labels", "created", "updated", "version")}
                lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()] + ["---", "",
                         f"# {ent['title']}", "", ent.get("body") or ""]
                if ent.get("task"):
                    lines += ["", "## Task", "", json.dumps(ent["task"], ensure_ascii=False)]
                name = f"{ent['type']}/{(ent.get('display_id') or ent['uid']).replace('/', '-')}.md"
                zf.writestr(name, "\n".join(lines) + "\n")
        audit.emit("personal_kb.exported", tenant_id=self.tenant_id, owner_hash=self.owner_hash,
                   entity_count=len(self.index))
        return buf.getvalue()

    # ---- policy versions (ADR-2327 item 10) ------------------------------------------------
    def write_policy_version(self, policy: dict[str, Any]) -> int:
        with self._locked():
            n = int(self.manifest.get("policy_versions", 0)) + 1
            d = self.dir / "policy" / "versions"
            d.mkdir(parents=True, exist_ok=True, mode=0o700)
            blob = crypto.seal(self.keys.dek, json.dumps(policy, sort_keys=True).encode("utf-8"),
                               aad_bytes=_policy_aad(self.owner_id, self.keys.epoch, n), epoch=self.keys.epoch)
            locks.atomic_write(paths.policy_version_file(self.tenant_id, self.owner_id, n), blob)
            self.manifest["policy_versions"] = n
            self.meta["policy_current"] = n
            self._write_meta()
            self._write_manifest()
            return n

    def read_policy_version(self, n: int) -> dict[str, Any]:
        blob = paths.policy_version_file(self.tenant_id, self.owner_id, n).read_bytes()
        ep = crypto.blob_epoch(blob)
        return json.loads(crypto.open_(self.keys.key_for(ep), blob, aad_bytes=_policy_aad(self.owner_id, ep, n)))

    # ---- rekey sweep (ADR-2326 item 8) -----------------------------------------------------
    def sweep_epoch(self, *, batch: int = 200) -> int:
        """Re-encrypt every object, version, policy version, meta and manifest still under an old epoch.
        Works through a fixed work list in slices of *batch* files and releases the lock between slices, so
        every file is read once and one slice holds the lock for a bounded time (writers wait at most 10 s).
        Returns the number of files rewritten."""
        with self._locked():
            work: list[tuple[str, int | None]] = [
                (uid, n) for uid, rec in self.manifest["objects"].items()
                for n in list(rec.get("versions", [])) + [None]]
            work += [("", n) for n in range(1, int(self.manifest.get("policy_versions", 0)) + 1)]
        done = 0
        for start in range(0, len(work), batch):
            done += self._sweep_slice(work[start:start + batch])
        with self._locked():
            self._write_meta()
            self._write_manifest()
        return done

    def _sweep_slice(self, items: list[tuple[str, int | None]]) -> int:
        done = 0
        with self._locked():
            for uid, n in items:
                if not uid:      # a policy version
                    p = paths.policy_version_file(self.tenant_id, self.owner_id, int(n or 0))
                    try:
                        blob = p.read_bytes()
                        ep = crypto.blob_epoch(blob)
                        if ep == self.keys.epoch:
                            continue
                        pt = crypto.open_(self.keys.key_for(ep), blob, aad_bytes=_policy_aad(self.owner_id, ep, n))
                    except (FileNotFoundError, crypto.DecryptError):
                        continue
                    locks.atomic_write(p, crypto.seal(self.keys.dek, pt,
                                                      aad_bytes=_policy_aad(self.owner_id, self.keys.epoch, n),
                                                      epoch=self.keys.epoch))
                    done += 1
                    continue
                rec = self.manifest["objects"].get(uid)
                if rec is None or (n is not None and n not in rec.get("versions", [])):
                    continue     # forgotten or pruned since the work list was made
                ver = rec["version"] if n is None else n
                p = (paths.object_file(self.tenant_id, self.owner_id, rec["oid"]) if n is None
                     else paths.version_file(self.tenant_id, self.owner_id, rec["oid"], n))
                try:
                    blob = p.read_bytes()
                except FileNotFoundError:
                    continue
                try:
                    ep = crypto.blob_epoch(blob)
                    if ep == self.keys.epoch:
                        continue
                    pt = crypto.open_(self.keys.key_for(ep), blob,
                                      aad_bytes=_object_aad(self.owner_id, rec["oid"], ep, ver))
                except crypto.DecryptError:
                    self.problems[uid] = "corrupt"   # unreadable under every key: left as is, never fatal
                    continue
                nb = crypto.seal(self.keys.dek, pt, aad_bytes=_object_aad(self.owner_id, rec["oid"],
                                                                         self.keys.epoch, ver),
                                 epoch=self.keys.epoch)
                locks.atomic_write(p, nb)
                if n is None:
                    rec["epoch"], rec["sha"] = self.keys.epoch, _sha(nb)
                done += 1
            if done:
                self._write_meta()
                self._write_manifest()
        return done


_WORD = re.compile(r"[a-zäöüß0-9]+")
_STOP = frozenset("a an the and or of to in on for with ich du er sie es wir ihr der die das und oder ein eine "
                  "zu im am mit für ist bin sind my mine i".split())


def normalise_for_fingerprint(value: str) -> str:
    words = sorted({w for w in _WORD.findall(str(value or "").lower()) if w not in _STOP})
    return " ".join(words)
