"""A2A Friendship Token — ADR-0070.

Self-contained pairing token where URL is optional.  Both peers run
``import-token``; connection state is PENDING until a URL is known,
then upgrades to ACTIVE.

Token format::

    corvin-a2a:ft1:<base64url(payload_json)>.<base64url(hmac_sig)>

HMAC signing uses a key derived from the shared key embedded in the token:

    sig_key = HMAC-SHA256(key_bytes, b"ft1-sig-v1")

This lets both sides verify integrity without a separate server-side
master key.  The shared key IS the credential; sig_key is derived
so tampering with any field (including ``key`` itself) is detectable.

CI lint: module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import base64
import hmac as _hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── constants ──────────────────────────────────────────────────────────

TOKEN_PREFIX = "corvin-a2a:ft1:"
_MAX_LABEL_LEN = 64
_EXPIRY_TOLERANCE_S = 30.0


class FriendshipError(Exception):
    """Raised on token format or validation failure."""


# ── path helpers ────────────────────────────────────────────────────────

def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    return Path(env) if env else Path.home() / ".corvin"


def _my_url_path() -> Path:
    return _corvin_home() / "global" / "remote_trigger" / "my_a2a_url"


def get_my_url() -> str | None:
    """Return own A2A base URL from env var or persisted config file."""
    env = os.environ.get("CORVIN_A2A_URL")
    if env:
        return env.strip().rstrip("/") or None
    p = _my_url_path()
    if p.exists():
        val = p.read_text("utf-8").strip().rstrip("/")
        return val or None
    return None


def set_my_url(url: str) -> None:
    """Persist own A2A base URL to config file (mode 0600)."""
    p = _my_url_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(url.strip().rstrip("/"), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


# ── base64url helpers ───────────────────────────────────────────────────

def _b64_enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64_dec(s: str) -> bytes:
    pad = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)


# ── signature derivation ────────────────────────────────────────────────

def _derive_sig_key(key_hex: str) -> bytes:
    """Derive the token integrity-signing key from the shared A2A key."""
    return _hmac.new(bytes.fromhex(key_hex), b"ft1-sig-v1", "sha256").digest()


# ── FriendshipToken dataclass ───────────────────────────────────────────

@dataclass
class FriendshipToken:
    kid: str                        # key ID (UUID4)
    key: str                        # shared HMAC key (hex, 64 chars = 32 bytes)
    url: str | None                 # peer's A2A base URL — optional
    label: str | None
    expires: float | None           # unix timestamp or None
    constraints: dict[str, Any] = field(default_factory=dict)

    @property
    def personas(self) -> list[str]:
        return list(self.constraints.get("personas") or [])

    @property
    def max_ttl_s(self) -> int | None:
        v = self.constraints.get("max_ttl_s")
        return int(v) if v is not None else None


# ── Token generation ────────────────────────────────────────────────────

def create_friendship_token(
    *,
    url: str | None = None,
    kid: str | None = None,
    label: str | None = None,
    ttl_seconds: float | None = 30 * 86400,
    personas: list[str] | None = None,
    max_ttl_s: int | None = None,
) -> tuple[FriendshipToken, str]:
    """Generate a friendship token.  Writes NOTHING to disk.

    Returns ``(FriendshipToken, token_string)``.

    ``ttl_seconds=None`` → token never expires (explicit opt-out required).
    """
    actual_kid = kid or str(uuid.uuid4())
    key = secrets.token_hex(32)     # 256-bit shared key
    now = time.time()
    expires = (now + ttl_seconds) if ttl_seconds is not None else None

    constraints: dict[str, Any] = {}
    if personas:
        constraints["personas"] = [str(p) for p in personas]
    if max_ttl_s is not None:
        constraints["max_ttl_s"] = int(max_ttl_s)

    payload_dict: dict[str, Any] = {
        "kid": actual_kid,
        "key": key,
        "v": 1,
    }
    if url is not None:
        payload_dict["url"] = url.strip().rstrip("/")
    if label is not None:
        payload_dict["lbl"] = label[:_MAX_LABEL_LEN]
    if expires is not None:
        payload_dict["exp"] = expires
    if constraints:
        payload_dict["con"] = constraints

    payload_bytes = json.dumps(
        payload_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")

    sig_key = _derive_sig_key(key)
    sig = _hmac.new(sig_key, payload_bytes, "sha256").digest()
    token_str = f"{TOKEN_PREFIX}{_b64_enc(payload_bytes)}.{_b64_enc(sig)}"

    return FriendshipToken(
        kid=actual_kid,
        key=key,
        url=url.strip().rstrip("/") if url else None,
        label=label[:_MAX_LABEL_LEN] if label else None,
        expires=expires,
        constraints=constraints,
    ), token_str


# ── Token parsing & verification ────────────────────────────────────────

def parse_and_verify(token_str: str) -> FriendshipToken:
    """Parse and verify a friendship token.

    Both the format and the HMAC signature are checked.
    Raises ``FriendshipError`` on any failure.
    """
    if not token_str.startswith(TOKEN_PREFIX):
        raise FriendshipError(f"not a friendship token (expected prefix '{TOKEN_PREFIX}')")
    rest = token_str[len(TOKEN_PREFIX):]
    dot = rest.rfind(".")
    if dot < 1:
        raise FriendshipError("token missing signature separator '.'")
    payload_b64, sig_b64 = rest[:dot], rest[dot + 1:]

    try:
        payload_bytes = _b64_dec(payload_b64)
        sig_bytes = _b64_dec(sig_b64)
    except Exception as exc:
        raise FriendshipError(f"base64 decode failed: {exc}") from exc

    try:
        d = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise FriendshipError(f"payload JSON invalid: {exc}") from exc

    for req in ("kid", "key", "v"):
        if req not in d:
            raise FriendshipError(f"payload missing required field: {req!r}")

    key: str = str(d["key"])
    if len(key) != 64 or not all(c in "0123456789abcdefABCDEF" for c in key):
        raise FriendshipError("key field is not a valid 32-byte hex string")

    sig_key = _derive_sig_key(key)
    expected = _hmac.new(sig_key, payload_bytes, "sha256").digest()
    if not _hmac.compare_digest(expected, sig_bytes):
        raise FriendshipError("HMAC verification failed — token may have been tampered")

    expires: float | None = float(d["exp"]) if "exp" in d else None
    if expires is not None and time.time() > expires + _EXPIRY_TOLERANCE_S:
        raise FriendshipError("token has expired")

    url_raw = d.get("url")
    label_raw = d.get("lbl")
    constraints = dict(d.get("con") or {})

    return FriendshipToken(
        kid=str(d["kid"]),
        key=key,
        url=str(url_raw).strip().rstrip("/") if url_raw else None,
        label=str(label_raw)[:_MAX_LABEL_LEN] if label_raw else None,
        expires=expires,
        constraints=constraints,
    )


# ── Config dict helpers ─────────────────────────────────────────────────

def _allowed_personas(token: FriendshipToken) -> list[str]:
    """Resolve persona list — defaults to ['assistant'] if unconstrained."""
    return token.personas or ["assistant"]


def _derive_channel_keys(shared_key_hex: str) -> tuple[str, str]:
    """Derive direction-separated HMAC and recv keys from a shared key.

    Using the same key for signing outbound requests AND verifying inbound
    responses is a HMAC key-confusion risk: knowledge of one direction's
    signed material could be replayed in the other direction.

    Derivation (MED-01, ADR-0099):
        hmac_key = HMAC-SHA256(shared_key, b"a2a-hmac-v1")  — sign outbound
        recv_key  = HMAC-SHA256(shared_key, b"a2a-recv-v1") — verify inbound

    Both sides derive the same pair from the same shared_key, so the
    protocol is still symmetric, but cross-role confusion is prevented.
    """
    import hashlib as _hl
    import hmac as _hm
    kb = bytes.fromhex(shared_key_hex)
    hmac_key = _hm.new(kb, b"a2a-hmac-v1", _hl.sha256).hexdigest()
    recv_key  = _hm.new(kb, b"a2a-recv-v1", _hl.sha256).hexdigest()
    return hmac_key, recv_key


def to_origin_dict(token: FriendshipToken) -> dict[str, Any]:
    """Build origin config dict for ``remote_origins/<kid>.json``.

    State is PENDING (enabled=False) when the peer's URL is unknown.
    """
    active = token.url is not None
    hmac_key, recv_key = _derive_channel_keys(token.key)
    d: dict[str, Any] = {
        "origin_id": token.kid,
        "hmac_key": hmac_key,   # verifies inbound envelopes FROM peer
        "recv_key": recv_key,   # signs outbound responses TO peer
        "_friendship_key_version": 2,   # marks derived keys (ADR-0099)
        "enabled": active,
        "state": "ACTIVE" if active else "PENDING",
        "spawn_worker": False,
        "allowed_personas": _allowed_personas(token),
        "_friendship": True,
    }
    if token.max_ttl_s is not None:
        d["max_ttl_s"] = token.max_ttl_s
    if token.label:
        d["label"] = token.label
    if token.expires is not None:
        d["_ft_expires"] = token.expires
    return d


def to_endpoint_dict(token: FriendshipToken) -> dict[str, Any]:
    """Build endpoint config dict for ``remote_endpoints/<kid>.json``.

    url is empty string when peer URL is not yet known (PENDING state).
    """
    active = token.url is not None
    url_str = (token.url + "/v1/a2a/receive") if token.url else ""
    hmac_key, recv_key = _derive_channel_keys(token.key)
    d: dict[str, Any] = {
        "endpoint_id": token.kid,
        "url": url_str,
        "hmac_key": hmac_key,   # signs outbound envelopes TO peer
        "recv_key": recv_key,   # verifies inbound responses FROM peer
        "_friendship_key_version": 2,
        "enabled": active,
        "state": "ACTIVE" if active else "PENDING",
        "_friendship": True,
    }
    if token.label:
        d["label"] = token.label
    if token.expires is not None:
        d["_ft_expires"] = token.expires
    return d


# ── set-url helper ──────────────────────────────────────────────────────

def activate_connection(
    kid: str,
    peer_url: str,
    *,
    origins_dir: Path,
    endpoints_dir: Path,
) -> None:
    """Upgrade a PENDING connection to ACTIVE by setting the peer's URL.

    Modifies both the origin and endpoint config files atomically.
    Raises ``FriendshipError`` if the connection is not found.
    """
    origin_path = origins_dir / f"{kid}.json"
    endpoint_path = endpoints_dir / f"{kid}.json"
    if not origin_path.exists() and not endpoint_path.exists():
        raise FriendshipError(f"connection {kid!r} not found")

    peer_url = peer_url.strip().rstrip("/")

    for path in (origin_path, endpoint_path):
        if not path.exists():
            continue
        cfg = json.loads(path.read_text("utf-8"))
        if not cfg.get("_friendship"):
            raise FriendshipError(f"{path.name} is not a friendship connection")
        cfg["state"] = "ACTIVE"
        cfg["enabled"] = True
        if path == endpoint_path:
            cfg["url"] = peer_url + "/v1/a2a/receive"
        _atomic_write(path, cfg)


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    if path.exists():
        os.chmod(path, 0o600)


# ── ADR-0198 — proactive reconnect (dynamic-IP peers) ───────────────────
#
# Concept: a peer whose own address changes at runtime (e.g. an LTE router
# handing out a new public IP) PUSHES a signed reconnect notification to
# every peer that already holds it as an ACTIVE endpoint, instead of relying
# on the next outbound call failing with a TransportError and an operator
# manually re-running ``activate_connection``. The notification travels as
# a TaskEnvelope carrying ``reconnect={"new_url": ...}`` (see
# remote_trigger_receiver.TaskEnvelope / RemoteTriggerSender.send_reconnect)
# so it is authenticated by the SAME HMAC keys already established at
# pairing time — no new credential, no new trust root.

def update_endpoint_url(kid: str, new_url: str, *, endpoints_dir: Path) -> bool:
    """Rewrite the ``url`` field of an existing, ACTIVE friendship endpoint.

    Unlike :func:`activate_connection`, this does NOT create or enable a
    connection — it only updates a peer that is already ACTIVE, so a
    reconnect notification can never be used to bootstrap trust. Returns
    False (no-op) for a missing, disabled, PENDING, or non-friendship file.
    """
    path = endpoints_dir / f"{kid}.json"
    if not path.exists():
        return False
    try:
        cfg = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    if not cfg.get("_friendship") or not cfg.get("enabled") or cfg.get("state") != "ACTIVE":
        return False
    new_url = new_url.strip().rstrip("/")
    if not new_url:
        return False
    cfg["url"] = new_url if new_url.endswith("/v1/a2a/receive") else new_url + "/v1/a2a/receive"
    _atomic_write(path, cfg)
    return True


def detect_local_ip() -> str:
    """Best-effort local outbound-interface IP, or "" on any failure.

    Pure local socket operation (UDP connect without sending data) — no
    external egress, so it does not implicate L35 egress-lockdown allowlists.
    This is a proxy signal for "the network interface changed", not the
    peer-visible public IP; operators behind NAT/CGNAT still need their
    ``my_a2a_url`` kept current (e.g. via a DDNS updater) for the URL this
    module re-announces to actually be reachable.
    """
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return ""


def _last_known_ip_path() -> Path:
    return _corvin_home() / "global" / "remote_trigger" / "last_known_ip"


def check_and_broadcast_reconnect(
    *, endpoints_dir: Path | None = None,
) -> int:
    """If the local interface IP changed since the last check, proactively
    push a signed reconnect notification (current ``get_my_url()``) to every
    ACTIVE friendship endpoint. Fail-soft: never raises, returns the number
    of peers successfully notified (0 on no-op or any failure).

    Intended to be polled from an existing background loop (e.g. the
    presence heartbeat) rather than run on its own thread.
    """
    current_ip = detect_local_ip()
    if not current_ip:
        return 0

    ip_path = _last_known_ip_path()
    try:
        last_ip = ip_path.read_text("utf-8").strip() if ip_path.exists() else ""
    except OSError:
        last_ip = ""

    if current_ip == last_ip:
        return 0

    try:
        ip_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = ip_path.with_suffix(".tmp")
        tmp.write_text(current_ip, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, ip_path)
    except OSError:
        pass

    if not last_ip:
        # First observation this boot — nothing to compare against yet,
        # avoid announcing on every fresh start.
        return 0

    my_url = get_my_url()
    if not my_url:
        return 0

    try:
        from remote_trigger_sender import (  # noqa: PLC0415
            RemoteTriggerSender, RemoteEndpointRegistry,
        )
    except Exception:  # noqa: BLE001
        return 0

    registry = RemoteEndpointRegistry(endpoints_dir)
    sender = RemoteTriggerSender(endpoints_dir, registry)
    notified = 0
    for endpoint_id in registry.list_ids():
        try:
            cfg = registry.load(endpoint_id)
        except Exception:  # noqa: BLE001
            continue
        if not cfg.get("_friendship") or cfg.get("state") != "ACTIVE":
            continue
        try:
            if sender.send_reconnect(endpoint_id, my_url):
                notified += 1
        except Exception:  # noqa: BLE001
            pass
    return notified
