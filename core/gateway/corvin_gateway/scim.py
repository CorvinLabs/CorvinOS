"""SCIM 2.0 stub — per-tenant user provisioning.

ADR-0007 Phase 3.5. Implements the minimal SCIM 2.0 ``Users``
resource that an identity-provider (Keycloak, Okta, Azure AD) can
provision against. The intent is reach, not feature completeness:
Phase 3.5 ships the surface that lets Strand B integrate with a
real IdP without inventing a parallel provisioning protocol.

Endpoints (under ``/v1/tenants/{tid}/scim/v2``):

* ``GET    /Users``         list users + total
* ``POST   /Users``         create a user; returns 201
* ``GET    /Users/{uid}``   fetch a user; 404 if missing
* ``DELETE /Users/{uid}``   delete a user; 204 if present, 404 else

What this module does NOT do
----------------------------

* No PATCH/PUT semantics. Phase 3.6 lands those alongside the
  Keycloak smoke (Keycloak uses both).
* No filters / pagination. SCIM operators usually paginate over
  large user counts; Phase 7 may add ``startIndex``/``count`` when
  rate-limiting + persistent stores land. The Phase 3.5 list
  endpoint returns every user the operator has, period.
* No bulk endpoint. SCIM's ``/Bulk`` is a separate sub-resource;
  most IdPs don't require it for basic provisioning.
* No external schema fetch. The ``schemas`` array on every
  resource carries the SCIM-core User URN verbatim; we don't
  validate against an external SCIM schema document.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge.tenants import validate_tenant_id  # noqa: E402
from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


# ── Event registration ──────────────────────────────────────────────


_SCIM_EVENTS = {
    "gateway.scim_user_created":   "INFO",
    "gateway.scim_user_deleted":   "INFO",
    "gateway.scim_user_conflict":  "WARNING",
    "gateway.scim_user_patched":   "INFO",
    "gateway.scim_patch_rejected": "WARNING",
}
for _evt, _sev in _SCIM_EVENTS.items():
    _security_events.EVENT_SEVERITY.setdefault(_evt, _sev)


SCIM_PATCH_OP_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


# ── Constants ───────────────────────────────────────────────────────


SCIM_FILENAME = "users.json"
_REQUIRED_MODE = 0o600

SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,256}$")


# ── Exceptions ──────────────────────────────────────────────────────


class ScimStoreMalformed(Exception):
    """users.json missing tenant dir, mode > 0o600, malformed JSON."""


class ScimValidationError(Exception):
    """Inbound payload missing required fields or violating SCIM-core."""


class ScimConflict(Exception):
    """userName already present in this tenant."""


# ── Path helpers ────────────────────────────────────────────────────


def _scim_dir(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "scim"


def _users_path(tenant_id: str) -> Path:
    return _scim_dir(tenant_id) / SCIM_FILENAME


def _audit_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"


def _audit(
    event_type: str,
    *,
    tenant_id: str,
    details: dict[str, Any] | None = None,
    severity: str | None = None,
) -> None:
    try:
        _security_events.write_event(
            _audit_path(tenant_id), event_type,
            severity=severity, details=dict(details or {}),
            hash_chain=True,
        )
    except Exception:
        pass


# ── On-disk IO ──────────────────────────────────────────────────────


def _load_store(tenant_id: str) -> dict[str, Any]:
    p = _users_path(tenant_id)
    if not p.exists():
        return {"users": {}}
    try:
        st = p.stat()
    except OSError as e:
        raise ScimStoreMalformed(f"stat failed for {p}: {e}") from e
    mode = st.st_mode & 0o777
    if mode != _REQUIRED_MODE:
        raise ScimStoreMalformed(
            f"{p}: mode 0o{mode:o}, want 0o{_REQUIRED_MODE:o}"
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ScimStoreMalformed(f"{p}: bad JSON: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
        raise ScimStoreMalformed(f"{p}: top-level must carry a users dict")
    return data


def _atomic_write_store(tenant_id: str, data: dict[str, Any]) -> Path:
    scim_dir = _scim_dir(tenant_id)
    if not scim_dir.parent.parent.exists():
        raise ScimStoreMalformed(
            f"tenant directory does not exist: {scim_dir.parent.parent}"
        )
    scim_dir.mkdir(parents=True, exist_ok=True)
    target = _users_path(tenant_id)
    tmp = target.with_suffix(target.suffix + ".tmp")
    body = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _REQUIRED_MODE)
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)
    os.chmod(target, _REQUIRED_MODE)
    return target


# ── Resource projection (in-memory <-> SCIM JSON) ───────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _user_to_scim(uid: str, entry: dict[str, Any], *, location: str) -> dict[str, Any]:
    """Project an on-disk user entry to the SCIM 2.0 JSON shape."""
    return {
        "schemas":     [SCIM_USER_SCHEMA],
        "id":          uid,
        "userName":    entry.get("userName", ""),
        "active":      bool(entry.get("active", True)),
        "emails":      list(entry.get("emails", [])),
        "displayName": entry.get("displayName", ""),
        "meta": {
            "resourceType": "User",
            "created":      entry.get("created", ""),
            "lastModified": entry.get("lastModified", ""),
            "location":     location,
        },
    }


def _normalise_inbound_user(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ScimValidationError("body must be a JSON object")
    schemas = payload.get("schemas") or []
    if not isinstance(schemas, list) or SCIM_USER_SCHEMA not in schemas:
        raise ScimValidationError(
            f"body must declare schemas={SCIM_USER_SCHEMA!r}"
        )
    username = payload.get("userName")
    if not isinstance(username, str) or not _USERNAME_RE.match(username):
        raise ScimValidationError(
            "userName must be a non-empty string of [A-Za-z0-9._@+-]{1,256}"
        )
    emails = payload.get("emails") or []
    if not isinstance(emails, list):
        raise ScimValidationError("emails must be a list when present")
    safe_emails: list[dict[str, Any]] = []
    for e in emails:
        if not isinstance(e, dict):
            raise ScimValidationError("each email entry must be a mapping")
        value = e.get("value")
        if not isinstance(value, str) or not value:
            raise ScimValidationError("email.value must be a non-empty string")
        safe_emails.append({
            "value":   value,
            "primary": bool(e.get("primary", False)),
        })
    active = payload.get("active", True)
    if not isinstance(active, bool):
        raise ScimValidationError("active must be boolean when present")
    display = payload.get("displayName", "")
    if display and not isinstance(display, str):
        raise ScimValidationError("displayName must be a string when present")
    return {
        "userName":    username,
        "active":      active,
        "emails":      safe_emails,
        "displayName": display,
    }


# ── Public registry API ──────────────────────────────────────────────


class ScimUserStore:
    """Per-tenant SCIM user storage. Stateless; every call touches
    the filesystem. Mirror of the Phase 2.2 ``RunRegistry`` shape."""

    def create(self, tenant_id: str, payload: Any) -> tuple[str, dict[str, Any]]:
        """Persist a new user. Returns (uid, on-disk-entry).
        Raises :class:`ScimConflict` when ``userName`` already exists,
        :class:`ScimValidationError` on schema violations."""
        validate_tenant_id(tenant_id)
        normalised = _normalise_inbound_user(payload)
        username = normalised["userName"]
        try:
            store = _load_store(tenant_id)
        except ScimStoreMalformed:
            store = {"users": {}}
        # Conflict on userName (case-insensitive per SCIM 2.0)
        for existing_uid, existing in store["users"].items():
            if existing.get("userName", "").casefold() == username.casefold():
                _audit(
                    "gateway.scim_user_conflict", tenant_id=tenant_id,
                    details={"userName": username, "existing_uid": existing_uid},
                )
                raise ScimConflict(
                    f"userName {username!r} already exists (id={existing_uid})"
                )
        uid = str(uuid.uuid4())
        now = _now_iso()
        entry = {
            **normalised,
            "created":      now,
            "lastModified": now,
        }
        store["users"][uid] = entry
        _atomic_write_store(tenant_id, store)
        _audit(
            "gateway.scim_user_created", tenant_id=tenant_id,
            details={"uid": uid, "userName": username},
        )
        return uid, entry

    def list(self, tenant_id: str) -> dict[str, dict[str, Any]]:
        validate_tenant_id(tenant_id)
        try:
            store = _load_store(tenant_id)
        except ScimStoreMalformed:
            return {}
        return dict(store.get("users", {}))

    def get(self, tenant_id: str, uid: str) -> dict[str, Any] | None:
        validate_tenant_id(tenant_id)
        try:
            store = _load_store(tenant_id)
        except ScimStoreMalformed:
            return None
        entry = store.get("users", {}).get(uid)
        if not isinstance(entry, dict):
            return None
        return entry

    def delete(self, tenant_id: str, uid: str) -> bool:
        validate_tenant_id(tenant_id)
        try:
            store = _load_store(tenant_id)
        except ScimStoreMalformed:
            return False
        if uid not in store.get("users", {}):
            return False
        del store["users"][uid]
        _atomic_write_store(tenant_id, store)
        _audit(
            "gateway.scim_user_deleted", tenant_id=tenant_id,
            details={"uid": uid},
        )
        return True

    def patch(self, tenant_id: str, uid: str, payload: Any) -> dict[str, Any] | None:
        """Apply a SCIM 2.0 PatchOp body to *uid*.

        Supported operations (RFC 7644 §3.5.2): ``replace`` and
        ``add`` on the curated set of fields (``active``,
        ``displayName``, ``emails``, ``userName``). ``remove`` of
        a whole field is supported for ``displayName`` and
        ``emails``; ``remove`` of ``userName`` is rejected (a
        SCIM User MUST have a userName).

        Returns the updated entry on success, ``None`` when the
        user does not exist. Raises :class:`ScimValidationError`
        on a malformed PatchOp body.
        """
        validate_tenant_id(tenant_id)
        try:
            store = _load_store(tenant_id)
        except ScimStoreMalformed:
            return None
        users = store.get("users", {})
        if uid not in users:
            return None
        entry = users[uid]

        if not isinstance(payload, dict):
            _audit(
                "gateway.scim_patch_rejected", tenant_id=tenant_id,
                details={"uid": uid, "reason": "body-not-object"},
            )
            raise ScimValidationError("PatchOp body must be a JSON object")
        schemas = payload.get("schemas") or []
        if SCIM_PATCH_OP_SCHEMA not in schemas:
            _audit(
                "gateway.scim_patch_rejected", tenant_id=tenant_id,
                details={"uid": uid, "reason": "missing-patchop-schema"},
            )
            raise ScimValidationError(
                f"PatchOp body must declare schemas={SCIM_PATCH_OP_SCHEMA!r}"
            )
        ops = payload.get("Operations")
        if not isinstance(ops, list) or not ops:
            _audit(
                "gateway.scim_patch_rejected", tenant_id=tenant_id,
                details={"uid": uid, "reason": "empty-operations"},
            )
            raise ScimValidationError("PatchOp Operations must be non-empty")

        applied: list[dict[str, str]] = []
        for op in ops:
            if not isinstance(op, dict):
                raise ScimValidationError("each Operation must be a mapping")
            verb = (op.get("op") or "").lower()
            path = op.get("path")
            value = op.get("value")
            if verb not in ("add", "replace", "remove"):
                raise ScimValidationError(
                    f"unsupported PatchOp verb: {verb!r}"
                )
            if not isinstance(path, str) or not path:
                raise ScimValidationError("Operation.path must be a non-empty string")
            field = path.lower()
            if field == "active":
                if verb == "remove":
                    raise ScimValidationError(
                        "remove on 'active' is not supported"
                    )
                if not isinstance(value, bool):
                    raise ScimValidationError(
                        "active patch value must be boolean"
                    )
                entry["active"] = value
            elif field == "displayname":
                if verb == "remove":
                    entry["displayName"] = ""
                else:
                    if not isinstance(value, str):
                        raise ScimValidationError(
                            "displayName patch value must be string"
                        )
                    entry["displayName"] = value
            elif field == "emails":
                if verb == "remove":
                    entry["emails"] = []
                else:
                    if not isinstance(value, list):
                        raise ScimValidationError(
                            "emails patch value must be list"
                        )
                    new_emails: list[dict[str, Any]] = []
                    for e in value:
                        if not isinstance(e, dict):
                            raise ScimValidationError(
                                "each email patch entry must be a mapping"
                            )
                        v = e.get("value")
                        if not isinstance(v, str) or not v:
                            raise ScimValidationError(
                                "email.value must be a non-empty string"
                            )
                        new_emails.append({
                            "value":   v,
                            "primary": bool(e.get("primary", False)),
                        })
                    entry["emails"] = new_emails
            elif field == "username":
                if verb == "remove":
                    raise ScimValidationError(
                        "userName cannot be removed (SCIM User MUST have one)"
                    )
                if not isinstance(value, str) or not _USERNAME_RE.match(value):
                    raise ScimValidationError(
                        "userName patch value must match charset"
                    )
                # Conflict check against other users
                for other_uid, other in users.items():
                    if other_uid == uid:
                        continue
                    if other.get("userName", "").casefold() == value.casefold():
                        _audit(
                            "gateway.scim_user_conflict", tenant_id=tenant_id,
                            details={
                                "userName":     value,
                                "existing_uid": other_uid,
                            },
                        )
                        raise ScimConflict(
                            f"userName {value!r} already exists "
                            f"(id={other_uid})"
                        )
                entry["userName"] = value
            else:
                raise ScimValidationError(f"unsupported patch path: {path!r}")
            applied.append({"op": verb, "path": path})

        entry["lastModified"] = _now_iso()
        users[uid] = entry
        _atomic_write_store(tenant_id, store)
        _audit(
            "gateway.scim_user_patched", tenant_id=tenant_id,
            details={"uid": uid, "operations": applied},
        )
        return entry


# ── SCIM error helper for the HTTP layer ─────────────────────────────


def scim_error(status: int, detail: str, scim_type: str | None = None) -> dict[str, Any]:
    """SCIM 2.0 error response envelope (RFC 7644 §3.12)."""
    body: dict[str, Any] = {
        "schemas": [SCIM_ERROR_SCHEMA],
        "status":  str(status),
        "detail":  detail,
    }
    if scim_type is not None:
        body["scimType"] = scim_type
    return body
