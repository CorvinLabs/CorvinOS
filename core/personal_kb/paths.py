"""Every path of the personal knowledge base, resolved through the tenant path API.

Layout (ADR-2325 item 1, PLAN-0973 section 3):

    <tenant_home>/personal_kb/
        _index.json  _lockouts.json  .pepper  _revoked/<owner_hash>
        <owner_id>/owner.json meta.enc manifest.enc objects/ versions/ policy/versions/ .lock
    <corvin_home>/secrets/personal_kb/<tenant_id>/<owner_id>.key   (device key, convenient mode)

No function here creates an owner directory: only ``owner.create_owner`` does. A path that
traverses a symlink or a Windows reparse point is refused.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from core.paths.tenant import corvin_home, tenant_home
from core.tenants import validate_tenant_id

from .ids import OID_RE, is_ulid

ROOT_NAME = "personal_kb"


class UnsafePath(RuntimeError):
    """A path component is a symlink or reparse point, or an id is malformed."""


def root(tenant_id: str) -> Path:
    return tenant_home(tenant_id) / ROOT_NAME


def _check_owner_id(owner_id: str) -> str:
    if not is_ulid(owner_id):
        raise UnsafePath("malformed owner id")
    return owner_id


def owner_dir(tenant_id: str, owner_id: str) -> Path:
    return root(tenant_id) / _check_owner_id(owner_id)


def owner_json(tenant_id: str, owner_id: str) -> Path:
    return owner_dir(tenant_id, owner_id) / "owner.json"


def meta_file(tenant_id: str, owner_id: str) -> Path:
    return owner_dir(tenant_id, owner_id) / "meta.enc"


def manifest_file(tenant_id: str, owner_id: str) -> Path:
    return owner_dir(tenant_id, owner_id) / "manifest.enc"


def object_file(tenant_id: str, owner_id: str, oid: str) -> Path:
    if not OID_RE.match(oid or ""):
        raise UnsafePath("malformed object id")
    return owner_dir(tenant_id, owner_id) / "objects" / f"{oid}.enc"


def version_file(tenant_id: str, owner_id: str, oid: str, n: int) -> Path:
    if not OID_RE.match(oid or "") or int(n) < 1:
        raise UnsafePath("malformed version reference")
    return owner_dir(tenant_id, owner_id) / "versions" / f"{oid}.{int(n)}.enc"


def policy_version_file(tenant_id: str, owner_id: str, n: int) -> Path:
    if int(n) < 1:
        raise UnsafePath("malformed policy version")
    return owner_dir(tenant_id, owner_id) / "policy" / "versions" / f"{int(n)}.enc"


def lock_file(tenant_id: str, owner_id: str) -> Path:
    return owner_dir(tenant_id, owner_id) / ".lock"


def index_file(tenant_id: str) -> Path:
    return root(tenant_id) / "_index.json"


def lockouts_file(tenant_id: str) -> Path:
    return root(tenant_id) / "_lockouts.json"


def pepper_file(tenant_id: str) -> Path:
    return root(tenant_id) / ".pepper"


def tenant_lock_file(tenant_id: str) -> Path:
    return root(tenant_id) / ".tenant.lock"


def revoked_dir(tenant_id: str) -> Path:
    return root(tenant_id) / "_revoked"


def revoked_marker(tenant_id: str, owner_hash: str) -> Path:
    if not (len(owner_hash) == 16 and all(c in "0123456789abcdef" for c in owner_hash)):
        raise UnsafePath("malformed owner hash")
    return revoked_dir(tenant_id) / owner_hash


def device_key_dir(tenant_id: str) -> Path:
    validate_tenant_id(tenant_id)
    return corvin_home() / "secrets" / ROOT_NAME / tenant_id


def device_key_file(tenant_id: str, owner_id: str, epoch: int = 1) -> Path:
    """One file per key epoch, so a rekey writes the new key BEFORE the old one goes (ADR-2326 item 8)."""
    return device_key_dir(tenant_id) / f"{_check_owner_id(owner_id)}.e{int(epoch)}.key"


def device_key_files(tenant_id: str, owner_id: str) -> list[Path]:
    d = device_key_dir(tenant_id)
    return sorted(d.glob(f"{_check_owner_id(owner_id)}.*key")) if d.is_dir() else []


_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def is_link(p: Path) -> bool:
    """True for a symlink or (Windows) a reparse point. A missing path is not a link."""
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _REPARSE)


def assert_no_links(path: Path, stop_at: Path) -> None:
    """Refuse when *path* or any parent up to and including *stop_at* is a link."""
    cur = Path(path)
    stop = Path(stop_at)
    seen = 0
    while True:
        if is_link(cur):
            raise UnsafePath("refusing to follow a link inside the personal store")
        if cur == stop or cur.parent == cur or seen > 64:
            return
        cur = cur.parent
        seen += 1
