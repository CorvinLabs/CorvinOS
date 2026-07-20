"""Layer 38 — A2A invite-code pairing (Console routes).

One-step user-friendly pairing flow on top of the existing HMAC key exchange.
No JSON file copying required — share a code, paste it, done.

Flow
----
1. Issuer (A): POST /remote-trigger/pair/generate
   Returns an invite_code (base64-encoded JSON with all 4 key pairs).

2. Redeemer (B): POST /remote-trigger/pair/redeem
   Decodes invite, installs local endpoint + origin files, then calls
   A's /remote-trigger/pair/accept (server-to-server, HMAC-signed).

3. Issuer (A): POST /remote-trigger/pair/accept   (public, HMAC-gated)
   Verifies one-time accept key, installs B's origin + endpoint files,
   deletes pending invite.

Result: fully bidirectional pairing — both sides can send and receive.

Security properties
-------------------
* Four independent HMAC-SHA256 key pairs (one per direction × role).
* One-time accept_key that is deleted on first use — no replay.
* ±300 s time window on the accept call.
* Pending invites stored at mode 0600; key material never logged.
* Path-gate and L16 audit chain are NOT bypassed — file writes go through
  normal OS primitives, not through forge/skill-forge.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

# Serialize all peer-count check+write pairs to prevent TOCTOU race where two
# concurrent pairing requests both read count=0 and both pass the a2a_peers_max
# limit, resulting in 2 peers registered against a free-tier limit of 1.
_pair_lock: threading.Lock = threading.Lock()

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..utils import atomic_write_json
from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

# ── path helpers ──────────────────────────────────────────────────────

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]

_BRIDGES_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

_OPERATOR = _REPO / "operator"
if str(_OPERATOR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR))

try:
    from license.validator import get_limit as _lic_get_limit  # type: ignore[import]
except ImportError:
    try:
        from license.limits import FREE_TIER as _FREE_TIER  # type: ignore[import]
    except ImportError:
        _FREE_TIER: dict = {}
    _lic_get_limit = _FREE_TIER.get  # type: ignore[assignment]

_COWORK_DIR = _REPO / "operator" / "cowork"
_ORIGINS_DEFAULT = _COWORK_DIR / "remote_origins"
_ENDPOINTS_DEFAULT = _COWORK_DIR / "remote_endpoints"
_PENDING_DEFAULT = _COWORK_DIR / "pending_invites"


def _origins_dir() -> Path:
    env = os.environ.get("REMOTE_ORIGINS_DIR")
    return Path(env) if env else _ORIGINS_DEFAULT


def _endpoints_dir() -> Path:
    env = os.environ.get("REMOTE_ENDPOINTS_DIR")
    return Path(env) if env else _ENDPOINTS_DEFAULT


def _pending_dir() -> Path:
    env = os.environ.get("REMOTE_PENDING_DIR")
    return Path(env) if env else _PENDING_DEFAULT


# ── crypto helpers ────────────────────────────────────────────────────

def _gen_key() -> str:
    return secrets.token_hex(32)


def _write_secure(path: Path, data: dict) -> None:
    atomic_write_json(path, data)


def _sign(key_hex: str, payload: str) -> str:
    key = bytes.fromhex(key_hex)
    return _hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def _verify(key_hex: str, payload: str, sig: str) -> bool:
    return _hmac.compare_digest(_sign(key_hex, payload), sig)


router = APIRouter()


def _check_a2a_peers_max() -> None:
    """Raise HTTP 402 if a2a_peers_max licence limit is reached.

    MUST be called while holding _pair_lock to prevent TOCTOU races where two
    concurrent requests both read the same count and both pass before either
    writes an origin file. All callsites use ``with _pair_lock:`` covering both
    this check and the subsequent _write_secure() calls.
    """
    _a2a_max = _lic_get_limit("a2a_peers_max")
    if _a2a_max is None:
        return  # unlimited (enterprise tier or licence not loaded)
    # Guard against malformed SesT tokens that encode integer limits as JSON bool
    # (e.g. a2a_peers_max: true). Treat bool True as limit=1, False as disabled.
    if isinstance(_a2a_max, bool):
        if not _a2a_max:
            raise HTTPException(status_code=402, detail={
                "error": "license_limit", "feature": "a2a_peers_max",
                "msg": "A2A pairing not available on this licence tier.",
                "upgrade_url": "https://corvin-labs.com/pricing",
            })
        _a2a_max = 1
    _existing = sum(1 for _ in _origins_dir().glob("*.json")) if _origins_dir().exists() else 0
    if _existing >= _a2a_max:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "license_limit",
                "feature": "a2a_peers_max",
                "existing": _existing,
                "msg": (
                    f"Free tier allows at most {_a2a_max} A2A peer(s) "
                    f"({_existing} registered). "
                    "Upgrade to Member plan for more peers."
                ),
                "upgrade_url": "https://corvin-labs.com/pricing",
            },
        )


# ── GET /remote-trigger/pair/my-info ──────────────────────────────────

@router.get("/remote-trigger/pair/my-info")
def pair_my_info(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return this instance's identity for display in the Pair UI."""
    meta: dict[str, Any] = {"instance_id": "", "label": ""}
    try:
        from instance_identity import instance_id_metadata  # type: ignore[import]
        meta = instance_id_metadata()
    except Exception:
        pass
    return {
        "instance_id": meta.get("instance_id", ""),
        "label": meta.get("label", ""),
        "tenant_id": rec.tenant_id,
    }


# ── POST /remote-trigger/pair/generate ────────────────────────────────

class GenerateRequest(BaseModel):
    label: str = Field(default="", max_length=64)
    url: str = Field(..., description="A2A receive URL of this instance")
    console_url: str = Field(..., description="Console base URL of this instance")
    peer_origin_id: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$",
        description="ID the peer should use for this instance",
    )
    max_ttl_s: int = Field(default=300, ge=10, le=3600)
    ttl_minutes: int = Field(default=60, ge=5, le=2880)


@router.post("/remote-trigger/pair/generate")
def pair_generate(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: GenerateRequest,
) -> dict[str, Any]:
    """Generate a one-time bidirectional invite code."""
    instance_id = ""
    try:
        from instance_identity import get_instance_id  # type: ignore[import]
        instance_id = get_instance_id()
    except Exception:
        pass

    accept_id = str(uuid.uuid4())
    expires_at = time.time() + body.ttl_minutes * 60

    # Four independent keys — one per direction × role.
    r2i_hmac = _gen_key()  # redeemer signs task envelopes → issuer verifies
    r2i_recv = _gen_key()  # issuer signs response envelopes → redeemer verifies
    i2r_hmac = _gen_key()  # issuer signs task envelopes → redeemer verifies
    i2r_recv = _gen_key()  # redeemer signs response envelopes → issuer verifies
    accept_key = _gen_key()  # one-time handshake key

    accept_url = (
        body.console_url.rstrip("/") + "/v1/console/remote-trigger/pair/accept"
    )

    invite: dict[str, Any] = {
        "v": 1,
        "accept_id": accept_id,
        "accept_url": accept_url,
        "issuer_instance_id": instance_id,
        "issuer_url": body.url,
        "issuer_label": body.label or "Corvin",
        "origin_id": body.peer_origin_id,
        "max_ttl_s": body.max_ttl_s,
        "r2i_hmac_key": r2i_hmac,
        "r2i_recv_key": r2i_recv,
        "i2r_hmac_key": i2r_hmac,
        "i2r_recv_key": i2r_recv,
        "accept_key": accept_key,
        "expires_at": expires_at,
    }

    # Persist pending invite (one-time; deleted on accept).
    _write_secure(_pending_dir() / f"{accept_id}.json", invite)

    invite_code = base64.urlsafe_b64encode(
        json.dumps(invite).encode()
    ).decode()

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.pair.generate",
        target_kind="a2a_invite",
        target_id=accept_id,
    )
    return {
        "invite_code": invite_code,
        "accept_id": accept_id,
        "expires_at": expires_at,
        "accept_url": accept_url,
    }


# ── POST /remote-trigger/pair/redeem ──────────────────────────────────

class RedeemRequest(BaseModel):
    invite_code: str
    our_url: str = Field(..., description="Our A2A receive URL")
    our_console_url: str = Field(..., description="Our console base URL")
    our_label: str = Field(default="", max_length=64)
    our_origin_id: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$",
        description="ID we use for ourselves (issuer will store this)",
    )
    spawn_worker: bool = Field(default=False)


@router.post("/remote-trigger/pair/redeem")
async def pair_redeem(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: RedeemRequest,
) -> dict[str, Any]:
    """Redeem an invite code: install local files and notify the issuer.

    State-mutating (registers a remote origin/endpoint), so it is
    CSRF-gated like every other console mutation — the SPA already
    attaches ``X-CSRF-Token`` on this call.
    """

    # Decode invite
    try:
        raw = base64.urlsafe_b64decode(body.invite_code.strip().encode())
        invite = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid invite code — could not decode")

    if invite.get("v") != 1:
        raise HTTPException(status_code=422, detail="Unsupported invite version")

    if time.time() > invite.get("expires_at", 0):
        raise HTTPException(status_code=422, detail="Invite code has expired")

    try:
        accept_id: str = invite["accept_id"]
        accept_url: str = invite["accept_url"]
        issuer_url: str = invite["issuer_url"]
        issuer_instance_id: str = invite["issuer_instance_id"]
        issuer_label: str = invite.get("issuer_label", "Corvin")
        origin_id_for_issuer: str = invite["origin_id"]
    except KeyError as _ke:
        raise HTTPException(status_code=422, detail=f"Malformed invite: missing field {_ke}") from _ke
    if not re.fullmatch(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$", origin_id_for_issuer):
        raise HTTPException(status_code=422, detail="Malformed invite: invalid origin_id")
    try:
        r2i_hmac: str = invite["r2i_hmac_key"]
        r2i_recv: str = invite["r2i_recv_key"]
        i2r_hmac: str = invite["i2r_hmac_key"]
    except KeyError as _ke:
        raise HTTPException(status_code=422, detail=f"Malformed invite: missing field {_ke}") from _ke
    i2r_recv: str = invite["i2r_recv_key"]
    accept_key: str = invite["accept_key"]
    max_ttl_s: int = int(invite.get("max_ttl_s", 300))

    our_instance_id = ""
    try:
        from instance_identity import get_instance_id  # type: ignore[import]
        our_instance_id = get_instance_id()
    except Exception:
        our_instance_id = str(uuid.uuid4())

    # ADR-0094: enforce a2a_peers_max before creating any pairing files.
    # Lock held across check+write to prevent TOCTOU (two concurrent redeems
    # both reading count=0 before either writes an origin file).
    with _pair_lock:
        _check_a2a_peers_max()

        # 1. Install endpoint file (we → issuer)
        ep_file = _endpoints_dir() / f"{origin_id_for_issuer}.json"
        _write_secure(ep_file, {
            "endpoint_id": origin_id_for_issuer,
            "url": issuer_url,
            "hmac_key": r2i_hmac,
            "recv_key": r2i_recv,
            "instance_id": issuer_instance_id,
            "enabled": True,
            "default_ttl_s": min(60, max_ttl_s),
            "label": issuer_label,
            "our_origin_id": body.our_origin_id,
        })

        # 2. Install origin file (issuer → we)
        orig_file = _origins_dir() / f"{origin_id_for_issuer}.json"
        _write_secure(orig_file, {
            "origin_id": origin_id_for_issuer,
            "hmac_key": i2r_hmac,
            "recv_key": i2r_recv,
            "enabled": True,
            "max_ttl_s": max_ttl_s,
            "allowed_personas": ["assistant"],
            "spawn_worker": body.spawn_worker,
        })

    # 3. Server-to-server: notify issuer with our connection info
    nonce = secrets.token_hex(16)
    ts = time.time()
    accept_payload: dict[str, Any] = {
        "accept_id": accept_id,
        "ts": ts,
        "nonce": nonce,
        "redeemer_instance_id": our_instance_id,
        "redeemer_url": body.our_url,
        "redeemer_label": body.our_label or "Corvin",
        "origin_id_for_us": body.our_origin_id,
    }
    payload_str = json.dumps(accept_payload, sort_keys=True)
    accept_payload["signature"] = _sign(accept_key, payload_str)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(accept_url, json=accept_payload)
        if resp.status_code != 200:
            ep_file.unlink(missing_ok=True)
            orig_file.unlink(missing_ok=True)
            raise HTTPException(
                status_code=502,
                detail=f"Issuer rejected the pairing request (HTTP {resp.status_code}). "
                       f"Check that the console URL is reachable and the invite hasn't been used already.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        ep_file.unlink(missing_ok=True)
        orig_file.unlink(missing_ok=True)
        raise HTTPException(
            status_code=502,
            detail="could not reach issuer",
        )

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.pair.redeem",
        target_kind="a2a_origin",
        target_id=origin_id_for_issuer,
    )
    return {
        "ok": True,
        "paired_with": origin_id_for_issuer,
        "issuer_label": issuer_label,
        "issuer_instance_id": issuer_instance_id,
        "our_origin_id": body.our_origin_id,
        "bidirectional": True,
    }


# ── POST /remote-trigger/pair/accept  (public, HMAC-gated) ───────────

class AcceptRequest(BaseModel):
    accept_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
    ts: float
    nonce: str
    redeemer_instance_id: str
    redeemer_url: str
    redeemer_label: str
    origin_id_for_us: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
    signature: str


@router.post("/remote-trigger/pair/accept")
def pair_accept(body: AcceptRequest) -> dict[str, Any]:
    """Server-to-server: redeemer calls this so the issuer installs pairing files.

    No session cookie required — authentication is via the one-time accept_key
    embedded in the pending invite.
    """
    pending_file = _pending_dir() / f"{body.accept_id}.json"
    processed_file = _pending_dir() / f"{body.accept_id}.used"
    try:
        os.rename(pending_file, processed_file)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Unknown or already-used invite")

    try:
        invite = json.loads(processed_file.read_text("utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Could not read pending invite")

    if time.time() > invite.get("expires_at", 0):
        processed_file.unlink(missing_ok=True)
        raise HTTPException(status_code=410, detail="Invite expired")

    accept_key: str = invite["accept_key"]
    check_payload: dict[str, Any] = {
        "accept_id": body.accept_id,
        "ts": body.ts,
        "nonce": body.nonce,
        "redeemer_instance_id": body.redeemer_instance_id,
        "redeemer_url": body.redeemer_url,
        "redeemer_label": body.redeemer_label,
        "origin_id_for_us": body.origin_id_for_us,
    }
    if not _verify(accept_key, json.dumps(check_payload, sort_keys=True), body.signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    if abs(time.time() - body.ts) > 300:
        raise HTTPException(status_code=422, detail="Timestamp out of ±300 s window")

    try:
        r2i_hmac: str = invite["r2i_hmac_key"]
        r2i_recv: str = invite["r2i_recv_key"]
        i2r_hmac: str = invite["i2r_hmac_key"]
        i2r_recv: str = invite["i2r_recv_key"]
    except KeyError as _ke:
        raise HTTPException(status_code=422, detail=f"Malformed invite: missing field {_ke}") from _ke
    max_ttl_s: int = int(invite.get("max_ttl_s", 300))
    redeemer_id = body.origin_id_for_us

    # ADR-0094: issuer also enforces a2a_peers_max when installing its own
    # pairing files for the redeemer (redeemer already checked its own limit
    # in pair_redeem; this protects the issuer's peer count independently).
    # Lock held across check+write to prevent concurrent accept races.
    with _pair_lock:
        _check_a2a_peers_max()

        # Install origin file (redeemer → us)
        _write_secure(_origins_dir() / f"{redeemer_id}.json", {
            "origin_id": redeemer_id,
            "hmac_key": r2i_hmac,
            "recv_key": r2i_recv,
            "enabled": True,
            "max_ttl_s": max_ttl_s,
            "allowed_personas": ["assistant"],
            "spawn_worker": False,
        })

        # Install endpoint file (us → redeemer)
        _write_secure(_endpoints_dir() / f"{redeemer_id}.json", {
            "endpoint_id": redeemer_id,
            "url": body.redeemer_url,
            "hmac_key": i2r_hmac,
            "recv_key": i2r_recv,
            "instance_id": body.redeemer_instance_id,
            "enabled": True,
            "default_ttl_s": min(60, max_ttl_s),
            "label": body.redeemer_label,
        })

    # Delete processed invite — one-time use enforced.
    processed_file.unlink(missing_ok=True)

    console_audit.action_performed(
        tenant_id="_default",
        sid_fingerprint="system",
        action="a2a.pair.accepted",
        target_kind="a2a_origin",
        target_id=redeemer_id,
    )
    return {"ok": True, "paired_with": redeemer_id}


# ── CLI-Token flow (ADR-0063) ─────────────────────────────────────────────
# These routes expose the simpler self-contained token format from ADR-0063.
# No server-to-server callback required — the token carries everything.


class CLIInviteRequest(BaseModel):
    url: str = Field(..., description="This instance's A2A base URL")
    origin_id: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$",
        description="origin_id the peer should register for this instance",
    )
    label: str = Field(default="", max_length=64)
    scope: str = Field(default="assistant", description="comma-separated personas")
    ttl_hours: float = Field(default=168.0, ge=0.1, le=8760, description="token validity in hours")
    single_use: bool = Field(default=False)
    spawn_worker: bool = Field(default=False)
    max_call_ttl_s: int = Field(default=300, ge=10, le=3600)


class CLIInviteResponse(BaseModel):
    token: str
    ikey: str
    oid: str
    exp: float | None


@router.post("/remote-trigger/pair/cli-invite")
def generate_cli_invite(
    body: CLIInviteRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> CLIInviteResponse:
    """Generate an ADR-0063 CLI-style invite token."""
    import a2a_invite as _inv  # type: ignore[import-not-found]
    import a2a_invite_registry as _reg  # type: ignore[import-not-found]
    from instance_identity import get_instance_id  # type: ignore[import-not-found]

    personas = [p.strip() for p in body.scope.split(",") if p.strip()] or ["assistant"]
    try:
        token, token_str = _inv.generate_invite(
            iid=get_instance_id(),
            origin_id=body.origin_id,
            url=body.url,
            allowed_personas=personas,
            max_ttl_s=body.max_call_ttl_s,
            ttl_seconds=body.ttl_hours * 3600,
            single_use=body.single_use,
            label=body.label or None,
            spawn_worker=body.spawn_worker,
        )
    except _inv.InviteError as exc:
        raise HTTPException(status_code=400, detail="invalid request") from exc

    registry = _reg.InviteRegistry()
    registry.create(_reg.InviteEntry(
        ikey=token.ikey,
        oid=token.oid,
        lbl=token.lbl or "",
        iat=token.iat,
        exp=token.exp,
        su=token.su,
    ))
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.invite.created",
        target_kind="a2a_invite",
        target_id=token.ikey,
    )
    return CLIInviteResponse(token=token_str, ikey=token.ikey, oid=token.oid, exp=token.exp)


class CLIAcceptRequest(BaseModel):
    token: str = Field(..., description="ADR-0063 invite token string")
    overwrite: bool = Field(default=False)


class CLIAcceptResponse(BaseModel):
    ok: bool
    oid: str
    url: str
    personas: list[str]
    spawn_worker: bool
    exp: float | None


@router.post("/remote-trigger/pair/cli-accept")
def accept_cli_invite(
    body: CLIAcceptRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> CLIAcceptResponse:
    """Accept an ADR-0063 CLI invite token and install origin + endpoint files."""
    import a2a_invite as _inv  # type: ignore[import-not-found]
    import a2a_invite_registry as _reg  # type: ignore[import-not-found]
    from instance_identity import get_instance_id  # type: ignore[import-not-found]

    try:
        result = _inv.parse_invite(body.token.strip())
    except _inv.InviteError as exc:
        raise HTTPException(status_code=400, detail="invalid token") from exc

    token, payload_bytes, sig_bytes = result  # type: ignore[misc]

    local_iid = get_instance_id()
    registry: _reg.InviteRegistry | None = None
    if token.iid == local_iid:
        registry = _reg.InviteRegistry()
        if not _inv.verify_invite_sig(payload_bytes, sig_bytes):
            raise HTTPException(status_code=400, detail="invalid token signature")

    validation = _inv.validate_invite(token, registry=registry)
    if not validation.ok:
        raise HTTPException(status_code=400, detail="token rejected")

    origin_path = _origins_dir() / f"{token.oid}.json"
    endpoint_path = _endpoints_dir() / f"{token.oid}.json"
    if (origin_path.exists() or endpoint_path.exists()) and not body.overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"origin/endpoint {token.oid!r} already exists; set overwrite=true to replace",
        )

    # ADR-0094: enforce a2a_peers_max before installing new pairing files.
    with _pair_lock:
        _check_a2a_peers_max()
        _write_secure(origin_path, _inv.invite_to_origin_dict(token))
        _write_secure(endpoint_path, _inv.invite_to_endpoint_dict(token, local_instance_id=local_iid))

    if registry is not None:
        registry.mark_accepted(token.ikey)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.invite.accepted",
        target_kind="a2a_origin",
        target_id=token.oid,
    )
    return CLIAcceptResponse(
        ok=True,
        oid=token.oid,
        url=token.url,
        personas=token.pa,
        spawn_worker=token.spawn_worker,
        exp=token.exp,
    )


class InviteListEntry(BaseModel):
    ikey: str
    oid: str
    lbl: str
    iat: float
    exp: float | None
    su: bool
    status: str


@router.get("/remote-trigger/pair/invites")
def list_invites(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List all issued ADR-0063 invite tokens."""
    import a2a_invite_registry as _reg  # type: ignore[import-not-found]

    registry = _reg.InviteRegistry()
    entries = registry.list_all()
    return {
        "invites": [
            InviteListEntry(
                ikey=e.ikey,
                oid=e.oid,
                lbl=e.lbl,
                iat=e.iat,
                exp=e.exp,
                su=e.su,
                status=e.status,
            ).model_dump()
            for e in entries
        ]
    }


@router.delete("/remote-trigger/pair/invites/{ikey}")
def revoke_invite(
    ikey: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> dict[str, Any]:
    """Revoke an issued ADR-0063 invite token by its ikey."""
    import a2a_invite_registry as _reg  # type: ignore[import-not-found]

    registry = _reg.InviteRegistry()
    ok = registry.revoke(ikey)
    if not ok:
        raise HTTPException(status_code=404, detail=f"invite {ikey!r} not found")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.invite.revoked",
        target_kind="a2a_invite",
        target_id=ikey,
    )
    return {"ok": True, "ikey": ikey}


# ── ADR-0070: Friendship Token ────────────────────────────────────────────
# Optional-URL token-based pairing. Both peers run import; PENDING → ACTIVE
# once a URL is known.

import a2a_friendship as _ft  # type: ignore[import-not-found]

import logging
_log = logging.getLogger(__name__)


# ── GET /remote-trigger/pair/my-url ──────────────────────────────────

@router.get("/remote-trigger/pair/my-url")
def get_my_a2a_url(
    request: Request,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return this instance's own A2A base URL.

    Also provides a suggested URL derived from the request's Host header
    when no URL has been configured yet.  The A2A receive endpoint lives
    on the same gateway server as the console, so the request origin is
    a reliable default candidate.
    """
    _ = rec
    stored = _ft.get_my_url()

    # Build a suggested URL when none is stored yet.
    # Priority:
    #   1. X-Forwarded-Host (set by reverse proxies / Cloudflare)
    #   2. Outbound IP of this machine (connect trick — no packet sent)
    #   3. Request Host header as last resort
    suggested: str | None = None
    if not stored:
        try:
            import socket as _socket
            scheme = request.headers.get("x-forwarded-proto", "http")

            # 1. Behind a reverse proxy / Cloudflare tunnel?
            fwd_host = request.headers.get("x-forwarded-host", "")
            if fwd_host:
                suggested = f"{scheme}://{fwd_host}"
            else:
                # 2. Detect the machine's actual outbound IP (no packet sent).
                host_header = request.headers.get("host", "")
                # Extract port from Host header if present.
                if ":" in host_header:
                    _, port_str = host_header.rsplit(":", 1)
                else:
                    port_str = "80" if scheme == "http" else "443"
                try:
                    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 53))
                    real_ip = s.getsockname()[0]
                    s.close()
                    if real_ip and not real_ip.startswith("127.") and real_ip != "::1":
                        port_part = f":{port_str}" if port_str not in ("80", "443") else ""
                        suggested = f"{scheme}://{real_ip}{port_part}"
                    else:
                        # Fall back to Host header
                        if host_header:
                            suggested = f"{scheme}://{host_header}"
                except Exception:
                    if host_header:
                        suggested = f"{scheme}://{host_header}"
        except Exception:
            pass

    # Auto-save the suggested URL if nothing is stored yet.
    # The user can always override it via POST /my-url or the UI "Ändern" button.
    if not stored and suggested:
        try:
            _ft.set_my_url(suggested)
            stored = suggested
        except Exception:
            pass

    return {"url": stored, "suggested": suggested}


# ── POST /remote-trigger/pair/my-url ─────────────────────────────────

class MyUrlRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=512)


@router.post("/remote-trigger/pair/my-url")
def set_my_a2a_url(
    body: MyUrlRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Persist this instance's A2A base URL so it can be shown in the UI."""
    _ft.set_my_url(body.url)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.config.url_set",
        target_kind="a2a_config",
        target_id="my_url",
    )
    return {"ok": True, "url": body.url.strip().rstrip("/")}


# ── POST /remote-trigger/pair/friendship/create ───────────────────────

class FriendshipCreateRequest(BaseModel):
    url: str = Field(default="", max_length=512, description="Own A2A base URL — optional")
    label: str = Field(default="", max_length=64)
    ttl_hours: float = Field(default=720.0, ge=0, description="0 = no expiry")
    personas: str = Field(default="", description="comma-separated persona list")
    max_call_ttl_s: int = Field(default=0, ge=0, le=3600)
    remember_url: bool = Field(default=False, description="Also save url as my-url")


class FriendshipCreateResponse(BaseModel):
    token: str
    kid: str
    expires: float | None


@router.post("/remote-trigger/pair/friendship/create")
def friendship_create(
    body: FriendshipCreateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> FriendshipCreateResponse:
    """Generate a friendship token.  Writes nothing to disk here."""
    url_val: str | None = body.url.strip().rstrip("/") or None
    label_val: str | None = body.label.strip() or None
    ttl: float | None = body.ttl_hours * 3600 if body.ttl_hours > 0 else None
    personas: list[str] = (
        [p.strip() for p in body.personas.split(",") if p.strip()]
        if body.personas.strip() else []
    )
    max_ttl: int | None = body.max_call_ttl_s if body.max_call_ttl_s > 0 else None

    try:
        token, token_str = _ft.create_friendship_token(
            url=url_val,
            label=label_val,
            ttl_seconds=ttl,
            personas=personas if personas else None,
            max_ttl_s=max_ttl,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid request") from exc

    if body.remember_url and url_val:
        _ft.set_my_url(url_val)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.friendship.created",
        target_kind="a2a_friendship",
        target_id=token.kid,
    )
    return FriendshipCreateResponse(token=token_str, kid=token.kid, expires=token.expires)


# ── POST /remote-trigger/pair/friendship/import ───────────────────────

class FriendshipImportRequest(BaseModel):
    token: str = Field(..., description="Friendship token string (corvin-a2a:ft1:…)")
    peer_url: str = Field(default="", max_length=512, description="Peer URL if not in token")
    overwrite: bool = Field(default=False)
    spawn_worker: bool = Field(default=False, description="Grant Executor permission (default: Observer)")


class FriendshipImportResponse(BaseModel):
    ok: bool
    kid: str
    state: str
    url: str | None
    label: str | None
    personas: list[str]
    expires: float | None


@router.post("/remote-trigger/pair/friendship/import")
def friendship_import(
    body: FriendshipImportRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> FriendshipImportResponse:
    """Import a friendship token: write origin + endpoint config files."""
    try:
        token = _ft.parse_and_verify(body.token.strip())
    except _ft.FriendshipError as exc:
        raise HTTPException(status_code=400, detail="invalid token") from exc

    # Override/set URL from request body if not embedded in token.
    peer_url_override = body.peer_url.strip().rstrip("/") or None
    if peer_url_override:
        from dataclasses import replace
        token = replace(token, url=peer_url_override)

    origin_path = _origins_dir() / f"{token.kid}.json"
    endpoint_path = _endpoints_dir() / f"{token.kid}.json"
    if (origin_path.exists() or endpoint_path.exists()) and not body.overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"connection {token.kid!r} already exists; set overwrite=true to replace",
        )

    # ADR-0094: enforce a2a_peers_max before installing new pairing files.
    with _pair_lock:
        _check_a2a_peers_max()
        origin_cfg = _ft.to_origin_dict(token)
        if body.spawn_worker:
            origin_cfg["spawn_worker"] = True
        _write_secure(origin_path, origin_cfg)
        _write_secure(endpoint_path, _ft.to_endpoint_dict(token))

    state = "ACTIVE" if token.url is not None else "PENDING"
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.friendship.imported",
        target_kind="a2a_friendship",
        target_id=token.kid,
    )
    return FriendshipImportResponse(
        ok=True,
        kid=token.kid,
        state=state,
        url=token.url,
        label=token.label,
        personas=token.personas or ["assistant"],
        expires=token.expires,
    )


# ── POST /remote-trigger/pair/friendship/set-url ──────────────────────

class FriendshipSetUrlRequest(BaseModel):
    kid: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    peer_url: str = Field(..., min_length=1, max_length=512)


@router.post("/remote-trigger/pair/friendship/set-url")
def friendship_set_url(
    body: FriendshipSetUrlRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Upgrade a PENDING friendship connection to ACTIVE by providing the peer URL."""
    try:
        # A1 (2026-07-20): activate_connection is a read-modify-write on the
        # origin+endpoint files (sets state=ACTIVE / enabled=true) — hold
        # _pair_lock like every other RMW in this module so a concurrent
        # PATCH (e.g. enabled=false) is not silently overwritten. Cross-
        # process serialisation comes from config_file_lock inside
        # activate_connection itself.
        with _pair_lock:
            _ft.activate_connection(
                body.kid,
                body.peer_url.strip().rstrip("/"),
                origins_dir=_origins_dir(),
                endpoints_dir=_endpoints_dir(),
            )
    except _ft.FriendshipError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.friendship.activated",
        target_kind="a2a_friendship",
        target_id=body.kid,
    )
    return {"ok": True, "kid": body.kid, "state": "ACTIVE"}


# ── DELETE /remote-trigger/pair/friendship/{kid} ──────────────────────

@router.delete("/remote-trigger/pair/friendship/{kid}")
def friendship_revoke(
    kid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Delete a friendship connection (both origin and endpoint files)."""
    if not kid or "/" in kid or "\\" in kid or ":" in kid or kid.startswith("."):
        raise HTTPException(status_code=400, detail="invalid kid")
    origin_path = _origins_dir() / f"{kid}.json"
    endpoint_path = _endpoints_dir() / f"{kid}.json"

    # Verify at least one is a friendship connection.
    found = False
    for p in (origin_path, endpoint_path):
        if p.exists():
            try:
                cfg = json.loads(p.read_text("utf-8"))
                if cfg.get("_friendship"):
                    found = True
            except Exception:
                pass

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"connection {kid!r} not found",
        )

    origin_path.unlink(missing_ok=True)
    endpoint_path.unlink(missing_ok=True)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.friendship.revoked",
        target_kind="a2a_friendship",
        target_id=kid,
    )
    return {"ok": True, "kid": kid}


# ── GET /remote-trigger/pair/friendship/connections ───────────────────

@router.get("/remote-trigger/pair/friendship/connections")
def friendship_connections(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List all friendship connections (PENDING + ACTIVE)."""
    _ = rec
    seen: dict[str, dict[str, Any]] = {}

    for path in sorted(_origins_dir().glob("*.json")):
        try:
            cfg = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if not cfg.get("_friendship"):
            continue
        kid = path.stem
        seen[kid] = {
            "kid": kid,
            "state": cfg.get("state", "ACTIVE"),
            # A4-RESIDUAL: sanitize the on-disk label on the way OUT too — a
            # pre-existing record (or one written before the sanitizer existed)
            # could carry a bidi override (U+202E) / ANSI escape that would
            # spoof the friendship name in the React UI.
            "label": _clean_label(cfg.get("label") or "") or None,
            "personas": cfg.get("allowed_personas", []),
            "url": None,
            "expires": cfg.get("_ft_expires"),
        }

    for path in sorted(_endpoints_dir().glob("*.json")):
        try:
            cfg = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if not cfg.get("_friendship"):
            continue
        kid = path.stem
        url = cfg.get("url") or None
        if kid in seen:
            seen[kid]["url"] = url
        else:
            seen[kid] = {
                "kid": kid,
                "state": cfg.get("state", "ACTIVE"),
                # A4-RESIDUAL: sanitize on the way out (see above).
                "label": _clean_label(cfg.get("label") or "") or None,
                "personas": [],
                "url": url,
                "expires": cfg.get("_ft_expires"),
            }

    connections = sorted(seen.values(), key=lambda c: c["kid"])
    return {"connections": connections, "count": len(connections)}


# ── PATCH /remote-trigger/origins/{origin_id} ─────────────────────────


class OriginPatchRequest(BaseModel):
    spawn_worker: bool | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    allowed_personas: list[str] | None = Field(default=None)
    max_ttl_s: int | None = Field(default=None, ge=10, le=86400)
    label: str | None = Field(default=None, max_length=80)
    # M2 tool policy (ADR-0144): deny-by-default in the receiver; these are
    # explicit per-connection opt-ins mirroring remote_trigger_receiver.
    allow_bash: bool | None = Field(default=None)
    allow_network: bool | None = Field(default=None)
    allow_read_files: bool | None = Field(default=None)
    allow_write_files: bool | None = Field(default=None)
    allow_subagents: bool | None = Field(default=None)


_ORIGIN_TOOL_FLAGS = (
    "allow_bash",
    "allow_network",
    "allow_read_files",
    "allow_write_files",
    "allow_subagents",
)


def _clean_label(raw: str) -> str:
    """Canonicalize a connection label. Delegates to the shared sanitizer
    (NFC-normalize, drop non-printable, trim, cap) so operator-set and
    peer-authored labels are canonicalized identically — see
    ``a2a_friendship.sanitize_label``."""
    return _ft.sanitize_label(raw, max_len=80)


def _validate_endpoint_url(raw: str) -> str:
    """A6 (2026-07-20): schema-check an operator-supplied endpoint URL —
    http/https only, non-empty host, no embedded credentials (userinfo).

    Deliberately NO L35 / danger-category egress gate here: outbound A2A
    peer POSTs never pass the L35 engine-egress gate (documented egress
    honesty — see ``a2a_friendship.update_endpoint_url``), and this URL is
    operator-set via an authenticated CSRF-protected console session, not
    peer-controlled. Raises HTTPException(400) on violation.
    """
    from urllib.parse import urlsplit
    url = raw.strip()
    try:
        parts = urlsplit(url)
        host = parts.hostname
        has_credentials = parts.username is not None or parts.password is not None
    except ValueError:
        parts = None
        host = None
        has_credentials = False
    if (
        parts is None
        or parts.scheme not in ("http", "https")
        or not host
        or has_credentials
    ):
        raise HTTPException(
            status_code=400,
            detail="invalid url: must be http(s) with a host and no embedded credentials",
        )
    return url


@router.patch("/remote-trigger/origins/{origin_id}")
def patch_origin(
    origin_id: str,
    body: OriginPatchRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Update permission settings for an inbound origin."""
    # A8 (2026-07-20): ':' blocked too — 'C:foo' is drive-relative on Windows
    # and would escape the origins dir (peek_label applies the same rule).
    if (not origin_id or "/" in origin_id or "\\" in origin_id
            or ":" in origin_id or origin_id.startswith(".")):
        raise HTTPException(status_code=400, detail="invalid origin_id")
    if body.allowed_personas is not None:
        if len(body.allowed_personas) > 32 or any(
            not p or len(p) > 64 or not p.isprintable() for p in body.allowed_personas
        ):
            raise HTTPException(status_code=400, detail="invalid allowed_personas")
    origin_path = _origins_dir() / f"{origin_id}.json"
    # Hold _pair_lock across the read-modify-write so a concurrent PATCH or
    # pairing write can't cause a lost update (e.g. silently un-disabling an
    # origin, or resurrecting stale keys over a fresh re-pair). The file lock
    # (A2, 2026-07-20) extends the same guarantee across PROCESSES — the
    # bridge receiver and voice CLI rewrite these files too.
    changed: list[str] = []
    with _pair_lock, _ft.config_file_lock(_origins_dir()):
        if not origin_path.exists():
            raise HTTPException(status_code=404, detail=f"Origin {origin_id!r} not found")
        try:
            cfg = json.loads(origin_path.read_text("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to read origin file") from exc
        if body.spawn_worker is not None:
            cfg["spawn_worker"] = body.spawn_worker
            changed.append(f"spawn_worker={body.spawn_worker}")
        if body.enabled is not None:
            cfg["enabled"] = body.enabled
            changed.append(f"enabled={body.enabled}")
        if body.allowed_personas is not None:
            cfg["allowed_personas"] = body.allowed_personas
            changed.append("allowed_personas")
        if body.max_ttl_s is not None:
            cfg["max_ttl_s"] = body.max_ttl_s
            changed.append(f"max_ttl_s={body.max_ttl_s}")
        if body.label is not None:
            cleaned = _clean_label(body.label)
            if cleaned:
                cfg["label"] = cleaned
            else:
                cfg.pop("label", None)
            changed.append("label")
        for flag in _ORIGIN_TOOL_FLAGS:
            val = getattr(body, flag)
            if val is not None:
                cfg[flag] = val
                changed.append(f"{flag}={val}")
        _write_secure(origin_path, cfg)
    # Record WHICH rights changed — granting a peer allow_bash must not look
    # like a label rename in the audit chain (values are non-PII booleans).
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.origin.updated",
        target_kind="a2a_origin",
        target_id=origin_id,
        trigger=",".join(changed) if changed else None,
    )
    return {
        "ok": True,
        "origin_id": origin_id,
        "spawn_worker": cfg.get("spawn_worker", False),
        "enabled": cfg.get("enabled", True),
        "allowed_personas": cfg.get("allowed_personas", []),
        "max_ttl_s": cfg.get("max_ttl_s"),
        # A4-RESIDUAL: when label is not in the patch body, this echoes the raw
        # on-disk value — sanitize it so a pre-existing bidi/ANSI label can't
        # spoof the UI in the PATCH response either.
        "label": _clean_label(cfg.get("label") or "") or None,
        **{flag: bool(cfg.get(flag, False)) for flag in _ORIGIN_TOOL_FLAGS},
    }


# ── DELETE /remote-trigger/origins/{origin_id} ────────────────────────


@router.delete("/remote-trigger/origins/{origin_id}")
def delete_origin(
    origin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Delete an inbound origin config.

    Friendship origins (``_friendship: true``) must be removed via
    ``DELETE /remote-trigger/pair/friendship/{kid}`` instead.
    """
    if (not origin_id or "/" in origin_id or "\\" in origin_id
            or ":" in origin_id or origin_id.startswith(".")):
        raise HTTPException(status_code=400, detail="invalid origin_id")
    origin_path = _origins_dir() / f"{origin_id}.json"
    if not origin_path.exists():
        raise HTTPException(status_code=404, detail=f"Origin {origin_id!r} not found")
    try:
        cfg = json.loads(origin_path.read_text("utf-8"))
        if cfg.get("_friendship"):
            raise HTTPException(
                status_code=409,
                detail="Friendship origins must be revoked via "
                       "DELETE /remote-trigger/pair/friendship/{kid}",
            )
    except HTTPException:
        raise
    except Exception:
        pass  # unreadable file — still allow operator to clean up
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.origin.deleted",
        target_kind="a2a_origin",
        target_id=origin_id,
    )
    origin_path.unlink(missing_ok=True)
    return {"ok": True, "origin_id": origin_id}


# ── DELETE /remote-trigger/endpoints/{endpoint_id} ───────────────────


@router.delete("/remote-trigger/endpoints/{endpoint_id}")
def delete_endpoint(
    endpoint_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Delete an outbound endpoint config.

    Friendship endpoints must be removed via
    ``DELETE /remote-trigger/pair/friendship/{kid}`` instead.
    """
    if (not endpoint_id or "/" in endpoint_id or "\\" in endpoint_id
            or ":" in endpoint_id or endpoint_id.startswith(".")):
        raise HTTPException(status_code=400, detail="invalid endpoint_id")
    endpoint_path = _endpoints_dir() / f"{endpoint_id}.json"
    if not endpoint_path.exists():
        raise HTTPException(status_code=404, detail=f"Endpoint {endpoint_id!r} not found")
    try:
        cfg = json.loads(endpoint_path.read_text("utf-8"))
        if cfg.get("_friendship"):
            raise HTTPException(
                status_code=409,
                detail="Friendship endpoints must be revoked via "
                       "DELETE /remote-trigger/pair/friendship/{kid}",
            )
    except HTTPException:
        raise
    except Exception:
        pass
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.endpoint.deleted",
        target_kind="a2a_endpoint",
        target_id=endpoint_id,
    )
    endpoint_path.unlink(missing_ok=True)
    return {"ok": True, "endpoint_id": endpoint_id}


# ── PATCH /remote-trigger/endpoints/{endpoint_id} ────────────────────


class EndpointPatchRequest(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    url: str | None = Field(default=None, max_length=512)
    enabled: bool | None = Field(default=None)
    default_ttl_s: int | None = Field(default=None, ge=10, le=86400)


@router.patch("/remote-trigger/endpoints/{endpoint_id}")
def patch_endpoint(
    endpoint_id: str,
    body: EndpointPatchRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Update metadata for an outbound endpoint (label, URL, enabled, TTL)."""
    # A8 (2026-07-20): ':' blocked too — 'C:foo' is drive-relative on Windows
    # and would escape the endpoints dir (peek_label applies the same rule).
    if (not endpoint_id or "/" in endpoint_id or "\\" in endpoint_id
            or ":" in endpoint_id or endpoint_id.startswith(".")):
        raise HTTPException(status_code=400, detail="invalid endpoint_id")
    validated_url = _validate_endpoint_url(body.url) if body.url is not None else None
    endpoint_path = _endpoints_dir() / f"{endpoint_id}.json"
    # Same lost-update guard as patch_origin — a concurrent re-pair writing
    # this endpoint's keys must not be clobbered by a stale read-back. The
    # file lock (A2, 2026-07-20) extends the guarantee across PROCESSES
    # (bridge receiver reconnect updates, voice CLI set-url).
    with _pair_lock, _ft.config_file_lock(_endpoints_dir()):
        if not endpoint_path.exists():
            raise HTTPException(status_code=404, detail=f"Endpoint {endpoint_id!r} not found")
        try:
            cfg = json.loads(endpoint_path.read_text("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to read endpoint file") from exc
        if body.label is not None:
            cleaned = _clean_label(body.label)
            if cleaned:
                cfg["label"] = cleaned
            else:
                cfg.pop("label", None)
        if validated_url is not None:
            cfg["url"] = validated_url
        if body.enabled is not None:
            cfg["enabled"] = body.enabled
        if body.default_ttl_s is not None:
            cfg["default_ttl_s"] = body.default_ttl_s
        _write_secure(endpoint_path, cfg)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="a2a.endpoint.updated",
        target_kind="a2a_endpoint",
        target_id=endpoint_id,
    )
    return {
        "ok": True,
        "endpoint_id": endpoint_id,
        # A4-RESIDUAL: sanitize the echoed on-disk label (see patch_origin).
        "label": _clean_label(cfg.get("label") or "") or None,
        "url": cfg.get("url"),
        "enabled": cfg.get("enabled", True),
        "default_ttl_s": cfg.get("default_ttl_s"),
    }
