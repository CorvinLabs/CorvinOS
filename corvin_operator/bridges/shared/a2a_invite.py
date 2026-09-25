"""A2A Invite-Token Protocol — ADR-0063 L38 M4.

Self-contained pairing token that reduces A2A peer setup from six manual
steps to two CLI commands and one copy-paste.

Token format::

    corvin-a2a:v1:<base64url(payload_json)>.<base64url(hmac_sig)>

* ``payload_json`` is compact JSON (sorted keys, no extra whitespace) of all
  token fields (see ``InviteToken``).
* ``hmac_sig`` is HMAC-SHA256(invite_master_key, payload_bytes).
* ``invite_master_key`` lives at ``<corvin_home>/global/remote_trigger/invite_master_key``
  (hex-encoded, mode 0600, generated on first call).

Security properties
-------------------
* HMAC covers every field — bit-flip in payload is detected.
* Sig verification only possible by the issuing instance (symmetric key).
  Accepting instances skip sig verification for remote-issued tokens.
* Keys in token (``hk``, ``rk``) are freshly generated per invite —
  compromise of one token does not affect other connections.
* ``exp`` field enforces time-bounded validity; ``su`` flag enforces
  single-use via the registry on the issuing instance.

CI lint: module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import base64
import hmac as _hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── constants ──────────────────────────────────────────────────────────────

TOKEN_PREFIX = "corvin-a2a:v1:"
_MASTER_KEY_ENV = "CORVIN_A2A_INVITE_MASTER_KEY_PATH"
_DEFAULT_MASTER_SUBPATH = "global/remote_trigger/invite_master_key"
_MAX_LABEL_LEN = 64
_HMAC_ALGO = "sha256"


class InviteError(Exception):
    """Raised on token format / validation failure."""


# ``oid`` is chosen by the ISSUER and the accepting side uses it verbatim as a
# file name (``remote_origins/<oid>.json``, ``remote_endpoints/<oid>.json``);
# a remote-issued token's signature cannot even be checked there. So the
# alphabet is enforced at parse time (2026-09-25, finding 1: an oid of
# ``../../x`` wrote outside the config directories). Same pattern the console
# enforces when it MINTS an invite (CLIInviteRequest.origin_id) — a leading
# alphanumeric, then [A-Za-z0-9._-], at most 64 chars, and never "..".
OID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Receive path appended to the invite URL: a plain absolute path only — no
# "@" (userinfo host swap), "?", "#", "\\" or "..".
_RP_RE = re.compile(r"^(?:/[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}){0,8}$")


def is_valid_oid(oid: object) -> bool:
    return isinstance(oid, str) and OID_RE.fullmatch(oid) is not None and ".." not in oid


# ── path helpers ───────────────────────────────────────────────────────────

def _corvin_home() -> Path:
    # Mirror forge.paths.corvin_home() without importing forge at module level.
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        return Path(env)
    return Path.home() / ".corvin"


def _master_key_path() -> Path:
    env = os.environ.get(_MASTER_KEY_ENV)
    if env:
        return Path(env)
    return _corvin_home() / _DEFAULT_MASTER_SUBPATH


def _load_or_create_master_key() -> bytes:
    """Return the per-instance invite signing key, creating it on first call."""
    path = _master_key_path()
    if path.exists():
        # Self-heal world-readable mode.
        mode = path.stat().st_mode & 0o777
        if mode != 0o600:
            os.chmod(path, 0o600)
        return bytes.fromhex(path.read_text("utf-8").strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(key.hex(), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return key


# ── base64url helpers ──────────────────────────────────────────────────────

def _b64_enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64_dec(s: str) -> bytes:
    pad = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)


# ── InviteToken ────────────────────────────────────────────────────────────

@dataclass
class InviteToken:
    """Parsed representation of an invite token payload."""

    v: int           # protocol version (always 1)
    iid: str         # issuing instance UUID
    oid: str         # origin_id the accepting instance should register
    url: str         # issuer's base URL
    rp: str          # receive path (default /v1/a2a/receive)
    hk: str          # hex HMAC key (32 bytes) — sign TaskEnvelopes TO issuer
    rk: str          # hex recv key (32 bytes) — verify ResponseEnvelopes FROM issuer
    pa: list[str]    # allowed_personas the acceptor may trigger
    mt: int          # max_ttl_s per TaskEnvelope
    iat: float       # issued_at (unix timestamp)
    ikey: str        # 16-hex-char sig prefix — stable registry ID
    exp: float | None = None   # expires_at (None = no expiry)
    su: bool = False            # single_use flag
    lbl: str | None = None      # human label ("For Klaus at SaaS Co.")
    spawn_worker: bool = False  # enable spawn_worker in origin file


# ── Generation ─────────────────────────────────────────────────────────────

def generate_invite(
    *,
    iid: str,
    origin_id: str,
    url: str,
    receive_path: str = "/v1/a2a/receive",
    allowed_personas: list[str] | None = None,
    max_ttl_s: int = 300,
    ttl_seconds: float | None = 7 * 86400,
    single_use: bool = False,
    label: str | None = None,
    spawn_worker: bool = False,
) -> tuple[InviteToken, str]:
    """Generate a fresh invite token.

    Returns ``(InviteToken, token_string)``.

    ``ttl_seconds=None`` means no expiry — explicit opt-out required.
    """
    if allowed_personas is None:
        allowed_personas = ["assistant"]
    if not is_valid_oid(origin_id):
        raise InviteError(f"invalid origin_id: {origin_id!r}")
    if not _RP_RE.fullmatch(receive_path or ""):
        raise InviteError(f"invalid receive_path: {receive_path!r}")

    now = time.time()
    exp: float | None = (now + ttl_seconds) if ttl_seconds is not None else None
    hk = secrets.token_hex(32)
    rk = secrets.token_hex(32)

    payload_dict: dict[str, Any] = {
        "hk": hk,
        "iat": now,
        "iid": iid,
        "mt": max_ttl_s,
        "oid": origin_id,
        "pa": allowed_personas,
        "rk": rk,
        "rp": receive_path,
        "su": single_use,
        "sw": spawn_worker,
        "url": url,
        "v": 1,
    }
    if exp is not None:
        payload_dict["exp"] = exp
    if label:
        payload_dict["lbl"] = label[:_MAX_LABEL_LEN]

    payload_bytes = json.dumps(
        payload_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")

    master_key = _load_or_create_master_key()
    sig = _hmac.new(master_key, payload_bytes, _HMAC_ALGO).digest()
    ikey = sig.hex()[:16]

    token_str = f"{TOKEN_PREFIX}{_b64_enc(payload_bytes)}.{_b64_enc(sig)}"

    token = InviteToken(
        v=1,
        iid=iid,
        oid=origin_id,
        url=url,
        rp=receive_path,
        hk=hk,
        rk=rk,
        pa=allowed_personas,
        mt=max_ttl_s,
        iat=now,
        exp=exp,
        su=single_use,
        lbl=label[:_MAX_LABEL_LEN] if label else None,
        ikey=ikey,
        spawn_worker=spawn_worker,
    )
    return token, token_str


# ── Parsing ────────────────────────────────────────────────────────────────

def parse_invite(token_str: str) -> tuple[InviteToken, bytes]:
    """Parse a token string into ``(InviteToken, raw_sig_bytes)``.

    Does NOT verify the signature — call ``verify_invite_sig`` separately
    when this instance is the issuer.

    Raises ``InviteError`` on malformed input.
    """
    if not token_str.startswith(TOKEN_PREFIX):
        raise InviteError("token does not start with 'corvin-a2a:v1:'")
    rest = token_str[len(TOKEN_PREFIX):]
    dot = rest.rfind(".")
    if dot < 1:
        raise InviteError("token missing signature separator '.'")
    payload_b64, sig_b64 = rest[:dot], rest[dot + 1:]
    try:
        payload_bytes = _b64_dec(payload_b64)
        sig_bytes = _b64_dec(sig_b64)
    except Exception as exc:
        raise InviteError(f"token base64 decode failed: {exc}") from exc
    try:
        d = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise InviteError(f"token payload JSON invalid: {exc}") from exc

    missing = {f for f in ("v", "iid", "oid", "url", "rp", "hk", "rk", "pa", "mt", "iat") if f not in d}
    if missing:
        raise InviteError(f"token payload missing fields: {missing}")

    if not is_valid_oid(d["oid"]):
        raise InviteError("token oid is not a valid identifier")
    # The endpoint is ``url + rp``: an ``rp`` like "@169.254.169.254/x" would
    # move the real host while the displayed ``url`` stays harmless (round 9).
    if not isinstance(d["rp"], str) or not _RP_RE.fullmatch(d["rp"]):
        raise InviteError("token receive path is not a plain path")

    ikey = sig_bytes.hex()[:16]
    token = InviteToken(
        v=int(d["v"]),
        iid=str(d["iid"]),
        oid=str(d["oid"]),
        url=str(d["url"]),
        rp=str(d["rp"]),
        hk=str(d["hk"]),
        rk=str(d["rk"]),
        pa=list(d["pa"]),
        mt=int(d["mt"]),
        iat=float(d["iat"]),
        exp=float(d["exp"]) if "exp" in d else None,
        su=bool(d.get("su", False)),
        lbl=str(d["lbl"])[:_MAX_LABEL_LEN] if d.get("lbl") else None,
        ikey=ikey,
        spawn_worker=bool(d.get("sw", False)),
    )
    return token, payload_bytes, sig_bytes  # type: ignore[return-value]


def verify_invite_sig(payload_bytes: bytes, sig_bytes: bytes) -> bool:
    """Verify the token's HMAC using this instance's master key.

    Returns True on match, False on mismatch.  Only meaningful when this
    instance is the issuer (has the matching master key).
    """
    master_key = _load_or_create_master_key()
    expected = _hmac.new(master_key, payload_bytes, _HMAC_ALGO).digest()
    return _hmac.compare_digest(expected, sig_bytes)


# ── Validation ─────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""


def validate_invite(
    token: InviteToken,
    *,
    now: float | None = None,
    registry: Any = None,  # a2a_invite_registry.InviteRegistry | None
) -> ValidationResult:
    """Validate expiry, revocation, and single-use state.

    ``registry`` is optional; without it only expiry is checked.
    """
    ts = now if now is not None else time.time()

    if token.exp is not None and ts >= token.exp:
        return ValidationResult(ok=False, reason="expired")

    if registry is not None:
        entry = registry.get(token.ikey)
        if entry is not None:
            if entry.get("revoked"):
                return ValidationResult(ok=False, reason="revoked")
            if token.su and entry.get("accepted"):
                return ValidationResult(ok=False, reason="already_accepted")

    return ValidationResult(ok=True)


# ── Origin / Endpoint dict helpers ─────────────────────────────────────────

def invite_to_origin_dict(token: InviteToken) -> dict[str, Any]:
    """Build the dict to write as ``remote_origins/<oid>.json``.

    Security: spawn_worker is ALWAYS written as False regardless of what
    the invite token requests.  Invite tokens are transported over an
    unauthenticated channel (no CA verifies the issuer); a forged or
    MITM-modified token with spawn_worker=True would give the attacker
    arbitrary code execution.  Operators who want spawn_worker must
    explicitly enable it in the origin file after accepting the invite and
    reviewing the connection (CRIT-01 hardening, ADR-0099).
    """
    return {
        "origin_id": token.oid,
        "hmac_key": token.hk,
        "recv_key": token.rk,
        "enabled": True,
        "max_ttl_s": token.mt,
        "allowed_personas": token.pa,
        "spawn_worker": False,          # always False — operator must opt in
        "_invite_requested_spawn": token.spawn_worker,  # record intent for audit
        "_invite_ikey": token.ikey,
        "_invite_issuer": token.iid,
    }


def endpoint_url_rejection_reason(token: "InviteToken") -> str | None:
    """Host gate for a FOREIGN invite's endpoint (round 9) — the same gate as
    every other pairing path (``a2a_friendship._ack_url_rejection_reason``):
    no loopback, link-local / cloud metadata or other forbidden hosts. The
    token's signature cannot be checked here, so its URL is the issuer's
    unverified choice and every later send would POST there."""
    if not _RP_RE.fullmatch(token.rp or ""):
        return "invite_rp_invalid"
    import a2a_friendship as _ft  # noqa: PLC0415 — sibling module, lazy
    return _ft._ack_url_rejection_reason(token.url.strip().rstrip("/"))


def invite_to_endpoint_dict(
    token: InviteToken,
    *,
    local_instance_id: str,
    endpoint_id: str | None = None,
) -> dict[str, Any]:
    """Build the dict to write as ``remote_endpoints/<oid>.json``.

    The endpoint lets this instance SEND tasks to the issuer.
    """
    return {
        "endpoint_id": endpoint_id or token.oid,
        "url": token.url.rstrip("/") + token.rp,
        "hmac_key": token.hk,
        "recv_key": token.rk,
        "instance_id": token.iid,
        "enabled": True,
        "default_ttl_s": token.mt,
        "our_origin_id": token.oid,
        "_invite_ikey": token.ikey,
    }
