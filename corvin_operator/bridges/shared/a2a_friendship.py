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
import contextlib
import hmac as _hmac
import json
import os
import re
import secrets
import sys
import time
import urllib.request as _urllib_request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Cross-process file locking (A2 lost-update fix, 2026-07-20) — same
# platform-independence pattern as operator/license/compute_quota.py:
# fcntl.flock on POSIX, msvcrt.locking on Windows, advisory fail-soft.
_IS_WINDOWS = sys.platform.startswith("win")

try:
    import msvcrt  # type: ignore
except ImportError:  # non-Windows — no msvcrt module.
    msvcrt = None  # type: ignore[assignment]

try:
    import fcntl  # type: ignore
except ImportError:  # Windows — no fcntl module.
    fcntl = None  # type: ignore[assignment]

from _bounded_lock import (  # noqa: E402
    LockBusy as FriendshipLockBusy,
    acquire_exclusive as _acquire_exclusive,
)

# ── Bounded config locking (never hang an operator request) ─────────────
# ``config_file_lock`` used to ``flock(LOCK_EX)`` with no timeout. A wedged
# holder hung POST /remote-trigger/pair/friendship/set-url (and the relay ack
# handler) forever. The acquire is now bounded and REFUSES at the deadline
# with :class:`FriendshipLockBusy`.
#
# Refusing — not the module's existing "advisory fail-soft" degrade — is the
# right choice HERE: fail-soft covers the case where no lock can be OBTAINED
# at all (exotic FS, container without flock), where proceeding unlocked is
# the only option. A CONTENDED lock is the opposite situation: it proves
# another writer is mid read-modify-write, and continuing unlocked is exactly
# the lost update this lock was added to prevent (A2, 2026-07-20) — on the
# peer-URL field that decides where A2A tasks get relayed.
# Deadline + refusal semantics follow core.infinite_session.event_store.
LOCK_TIMEOUT_SECONDS = 2.0

# ── constants ──────────────────────────────────────────────────────────

TOKEN_PREFIX = "corvin-a2a:ft1:"
_MAX_LABEL_LEN = 64
_EXPIRY_TOLERANCE_S = 30.0


def sanitize_label(raw: object, max_len: int = _MAX_LABEL_LEN) -> str:
    """Canonicalize a connection label from ANY source (operator input OR a
    peer-authored friendship token).

    Labels are rendered in the console, surfaced to the local agent via
    ``a2a_list_endpoints``, AND used as a routing key (``resolve()``), so an
    untrusted label must not carry control chars / bidi overrides (terminal
    or prompt spoofing) and must have a single canonical byte form (else two
    visually-identical labels evade the ambiguity guard and misroute a signed
    task). Normalize to NFC, drop non-printable code points, collapse
    surrounding whitespace, cap length. Returns "" for empty/garbage input.
    """
    import unicodedata as _ud
    s = _ud.normalize("NFC", str(raw))
    s = "".join(ch for ch in s if ch.isprintable())
    return s.strip()[:max_len]


class FriendshipError(Exception):
    """Raised on token format or validation failure."""


# ── pairing identifiers (kid) ───────────────────────────────────────────
#
# A friendship ``kid`` is chosen by the TOKEN ISSUER and travels inside the
# (self-signed) token, yet every side uses it verbatim as a file name:
# ``remote_origins/<kid>.json``, ``remote_endpoints/<kid>.json``,
# ``remote_pending_friendships/<kid>.json``. A peer-controlled string used as
# a path component is a path-traversal primitive (``../../x`` wrote a 0600
# JSON file outside the config directories — 2026-09-25 review, finding 1).
# The kid is therefore restricted to a filename-safe alphabet at PARSE time
# and re-checked wherever a path is composed from it. uuid4 (what
# create_friendship_token mints) fits comfortably.
KID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_valid_kid(kid: object) -> bool:
    """True when ``kid`` is safe to use as a single path component."""
    return isinstance(kid, str) and KID_RE.fullmatch(kid) is not None


def kid_path(directory: Path, kid: str) -> Path:
    """``<directory>/<kid>.json`` — refusing any kid that is not a plain,
    filename-safe identifier (defence in depth behind the parse-time check)."""
    if not is_valid_kid(kid):
        raise FriendshipError("invalid kid")
    return Path(directory) / f"{kid}.json"


# ── path helpers ────────────────────────────────────────────────────────

def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
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


# ── ADR-0258 Stage 3 — instance-wide relay URL ──────────────────────────
#
# v1 scope decision: ONE relay per instance, used as the fallback for every
# pairing that wants it, rather than a per-pairing relay override. Both
# peers must independently configure the SAME relay URL as part of setup
# (an explicit rendezvous agreement, like choosing a shared meeting point)
# — this module never invents or defaults one. A per-pairing override is a
# plausible future refinement, not needed for the CGNAT/roaming case this
# stage exists to solve.

def _my_relay_url_path() -> Path:
    return _corvin_home() / "global" / "remote_trigger" / "my_a2a_relay_url"


# Zero-config connectivity (concept a2a-robust-connectivity, 2026-09-24):
# an install that never chose a relay falls back to the project relay, so a
# pairing between two loopback-bound / NAT'd installs completes with nothing
# but the token. Still inert while the a2a_relay_fallback flag is off — the
# console turns that flag on (audited) when the operator creates or imports
# a friendship token, the operator action that asks for connectivity. The
# relay only ever sees routing ids and AES-GCM ciphertext (a2a_relay.py).
# "off" in the relay-url config file or CORVIN_A2A_RELAY_URL opts out.
DEFAULT_RELAY_URL = "wss://corvin-a2a-relay-production.up.railway.app/v1/a2a/relay/connect"
RELAY_OFF = "off"


def get_my_relay_url() -> str | None:
    """Return this instance's relay URL (ws:// or wss://): env, then the
    config file, then :data:`DEFAULT_RELAY_URL`. ``"off"`` in either source
    disables the relay (None). Stage 3 stays inert while the
    a2a_relay_fallback feature flag is off."""
    env = os.environ.get("CORVIN_A2A_RELAY_URL")
    if env:
        val = env.strip().rstrip("/")
        return None if val.lower() == RELAY_OFF else (val or None)
    p = _my_relay_url_path()
    if p.exists():
        val = p.read_text("utf-8").strip().rstrip("/")
        if val.lower() == RELAY_OFF:
            return None
        if val:
            return val
    return DEFAULT_RELAY_URL


def my_relay_url_is_explicit() -> bool:
    """True when the relay URL came from env or the config file (including
    an explicit ``off``), False when it is the built-in default."""
    if os.environ.get("CORVIN_A2A_RELAY_URL"):
        return True
    p = _my_relay_url_path()
    try:
        return p.exists() and bool(p.read_text("utf-8").strip())
    except OSError:
        return False


def set_my_relay_url(url: str) -> None:
    """Persist this instance's relay URL to config file (mode 0600)."""
    p = _my_relay_url_path()
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
    # Issuer's relay URL ("rly", optional, signed with the rest of the
    # payload): the redeemer reaches an issuer without an inbound route
    # through the same relay, with nothing configured on either side.
    relay_url: str | None = None
    # Issuer's pairing BINDING public key ("bpk", signed with the payload,
    # ignored by older parsers — ADR-2064 round 5). Anchors the issuer's
    # binding identity in the token itself: the redeemer never has to learn
    # it from a (spoofable) response, and a later token finder knows only the
    # public half.
    bind_pub: str | None = None
    # Issuer's display name ("nam", signed, ignored by older parsers). The
    # token LABEL is the issuer's name for the REDEEMER ("For Max"); using it
    # as the redeemer's name for the issuer made every connection on the
    # redeemer side read like the redeemer itself (round 7).
    issuer_name: str | None = None

    @property
    def personas(self) -> list[str]:
        return list(self.constraints.get("personas") or [])

    @property
    def max_ttl_s(self) -> int | None:
        v = self.constraints.get("max_ttl_s")
        return int(v) if v is not None else None


# ── Token generation ────────────────────────────────────────────────────

def local_display_name() -> str:
    """This instance's name as shown to a paired peer: the instance label,
    ``CORVIN_INSTANCE_LABEL``, else the host name. Sanitised, ≤64 chars."""
    name = ""
    try:
        import instance_identity as _ii  # noqa: PLC0415
        name = str(_ii.instance_id_metadata().get("label") or "")
    except Exception:  # noqa: BLE001
        name = ""
    if not name:
        name = os.environ.get("CORVIN_INSTANCE_LABEL", "")
    if not name:
        import socket as _socket  # noqa: PLC0415
        try:
            name = _socket.gethostname()
        except OSError:
            name = ""
    return sanitize_label(name)[:64] if name else ""


def create_friendship_token(
    *,
    url: str | None = None,
    kid: str | None = None,
    label: str | None = None,
    ttl_seconds: float | None = 30 * 86400,
    personas: list[str] | None = None,
    max_ttl_s: int | None = None,
    relay_url: str | None = None,
) -> tuple[FriendshipToken, str]:
    """Generate a friendship token.  Writes NOTHING to disk.

    Returns ``(FriendshipToken, token_string)``.

    ``ttl_seconds=None`` → token never expires (explicit opt-out required).
    ``relay_url`` → embedded as ``rly`` (ignored by older parsers).
    """
    if relay_url is not None:
        relay_url = relay_url.strip().rstrip("/") or None
        if relay_url is not None and not relay_url.startswith(("ws://", "wss://")):
            raise FriendshipError("relay_url must use ws:// or wss://")
    actual_kid = kid or str(uuid.uuid4())
    if not is_valid_kid(actual_kid):
        raise FriendshipError("kid must match [A-Za-z0-9_-]{1,64}")
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
    if relay_url:
        payload_dict["rly"] = relay_url
    try:
        import a2a_binding as _bind  # noqa: PLC0415
        _bpk = _bind.local_bind_pub()
    except Exception:  # noqa: BLE001 — no binding key → legacy pairing semantics
        _bpk = None
    if _bpk:
        payload_dict["bpk"] = _bpk
    _nam = local_display_name()
    if _nam:
        payload_dict["nam"] = _nam

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
        relay_url=relay_url,
        bind_pub=_bpk,
        issuer_name=_nam or None,
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

    # The signature only proves the token is self-consistent — the issuer
    # chose every field, kid included. See KID_RE.
    if not is_valid_kid(d["kid"]):
        raise FriendshipError("kid field is not a valid identifier")

    expires: float | None = float(d["exp"]) if "exp" in d else None
    if expires is not None and time.time() > expires + _EXPIRY_TOLERANCE_S:
        raise FriendshipError("token has expired")

    url_raw = d.get("url")
    label_raw = d.get("lbl")
    constraints = dict(d.get("con") or {})
    relay_raw = d.get("rly")
    relay_url: str | None = None
    if isinstance(relay_raw, str):
        cand = relay_raw.strip().rstrip("/")
        # Signed by the issuer, but still only ever a ws/wss rendezvous URL.
        if cand.startswith(("ws://", "wss://")) and len(cand) <= 512:
            relay_url = cand

    import a2a_binding as _bind  # noqa: PLC0415
    return FriendshipToken(
        kid=str(d["kid"]),
        key=key,
        url=str(url_raw).strip().rstrip("/") if url_raw else None,
        label=(sanitize_label(label_raw) or None) if label_raw else None,
        expires=expires,
        constraints=constraints,
        relay_url=relay_url,
        bind_pub=_bind.clean_pub(d.get("bpk")),
        issuer_name=(sanitize_label(d.get("nam")) or None) if isinstance(d.get("nam"), str) else None,
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


def _derive_enc_key(hmac_key_hex: str) -> bytes:
    """ADR-0258 Stage 3 — derive the AES-256-GCM key for relay-path payload
    confidentiality.

    Input is the pairing's ALREADY-STORED ``hmac_key`` (identical on both
    sides — origin_dict's and endpoint_dict's ``hmac_key`` are the same
    value by construction, see :func:`_derive_channel_keys`), NOT the raw
    friendship-token shared secret — that secret is never persisted to disk
    after pairing completes (ADR-0099's whole point: a leaked stored key
    must not let an attacker derive sibling keys from the original secret).
    Chaining a further HMAC off an already-derived key is a standard,
    sound KDF pattern (HMAC is a PRF regardless of whether its key input is
    an "original" secret or itself derived).

    Returns raw 32 bytes (AESGCM wants key bytes, not hex) — unlike
    hmac_key/recv_key this is never persisted to disk or serialised; it is
    re-derived on demand at the point of encryption/decryption.
    """
    import hashlib as _hl
    import hmac as _hm
    kb = bytes.fromhex(hmac_key_hex)
    return _hm.new(kb, b"a2a-enc-v1", _hl.sha256).digest()  # 32 bytes


def derive_relay_auth_key(hmac_key_hex: str) -> str:
    """ADR-0258 Stage 3 — derive the relay REGISTRATION credential.

    Input is the pairing's stored ``hmac_key`` (see :func:`_derive_enc_key`
    docstring for why — same reasoning applies here). Deliberately NOT a
    zero-knowledge proof: this raw value is sent to the relay at
    registration time and pinned there (trust-on-first-use) as a routing
    credential for the ``kid`` it registers — see a2a_relay.py's module
    docstring for the full trust-model writeup, including the accepted
    first-registration-race residual. Leaking THIS key to the relay is
    safe: it grants routing only, never content confidentiality (needs
    enc_key) or message forgery (needs hmac_key/recv_key themselves).

    Returns hex (unlike enc_key, this one IS sent over the wire as a JSON
    string, so hex — not raw bytes — is the natural form here).
    """
    import hashlib as _hl
    import hmac as _hm
    kb = bytes.fromhex(hmac_key_hex)
    return _hm.new(kb, b"a2a-relay-auth-v1", _hl.sha256).hexdigest()


def encrypt_for_relay(hmac_key_hex: str, plaintext: bytes) -> tuple[str, str]:
    """ADR-0258 Stage 3 — AEAD-encrypt a payload for relay transit.

    The relay is a dumb pipe: it must not be able to read a single byte of
    routed content even if fully compromised. AES-256-GCM via the
    already-vendored `cryptography` package (no new dependency). Returns
    ``(nonce_hex, ciphertext_hex)`` — the nonce is not secret and travels
    alongside the ciphertext; GCM's tag is appended to the ciphertext by
    the library and verified on decrypt (tamper-evident: a modified
    ciphertext raises rather than decrypting to garbage).

    ``hmac_key_hex`` — see :func:`_derive_enc_key`: the pairing's stored
    hmac_key, not the raw friendship-token secret.
    """
    import os as _os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    key = _derive_enc_key(hmac_key_hex)
    nonce = _os.urandom(12)  # 96-bit, AESGCM's recommended nonce size
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return nonce.hex(), ciphertext.hex()


class RelayDecryptError(Exception):
    """Raised when relay-path AEAD decryption/verification fails — a
    tampered ciphertext, wrong key, or corrupted transit. The caller must
    treat this identically to a bad HMAC signature: reject, do not process."""


def decrypt_from_relay(hmac_key_hex: str, nonce_hex: str, ciphertext_hex: str) -> bytes:
    """Inverse of :func:`encrypt_for_relay`. Raises :class:`RelayDecryptError`
    on any failure (bad hex, wrong key, tampered ciphertext/tag) — never
    returns partial or unverified plaintext."""
    from cryptography.exceptions import InvalidTag  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    try:
        key = _derive_enc_key(hmac_key_hex)
        nonce = bytes.fromhex(nonce_hex)
        ciphertext = bytes.fromhex(ciphertext_hex)
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except (InvalidTag, ValueError) as exc:
        raise RelayDecryptError("relay payload decryption failed") from exc


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
    _name = token.issuer_name or token.label  # see FriendshipToken.issuer_name
    if _name:
        d["label"] = _name
    if token.expires is not None:
        d["_ft_expires"] = token.expires
    if token.bind_pub:
        d["_peer_bind_pub"] = token.bind_pub  # the issuer's, from the signed token
    return d


def to_endpoint_dict(token: FriendshipToken) -> dict[str, Any]:
    """Build endpoint config dict for ``remote_endpoints/<kid>.json``.

    url is empty string when peer URL is not yet known (PENDING state).

    ``origin_id_for_send`` — CRITICAL, found 2026-07-29: without this field,
    RemoteTriggerSender.send()/ping()/send_reconnect() all fall back to
    ``self._instance_id`` (a random UUID generated at THIS instance's first
    boot) as the ``origin_id`` embedded in every outbound envelope/ping. The
    peer's origin file for this pairing is named ``<kid>.json`` — the
    sender's own instance_id essentially never matches that filename, so the
    peer's ``OriginRegistry.load(origin_id)`` fails and every authenticated
    call from a friendship-token-paired endpoint is rejected as "unknown
    origin", REGARDLESS of whether ``state`` says ACTIVE. This made the
    friendship-token flow structurally unable to actually exchange a single
    message even in the rare case both sides ended up correctly paired — the
    two directions' own ``kid`` is the same symmetric pairing identifier on
    both sides by construction (see ``_derive_channel_keys``), so it is the
    correct, stable value to send as our own origin_id.
    """
    active = token.url is not None
    url_str = (token.url + "/v1/a2a/receive") if token.url else ""
    hmac_key, recv_key = _derive_channel_keys(token.key)
    d: dict[str, Any] = {
        "endpoint_id": token.kid,
        "url": url_str,
        "hmac_key": hmac_key,   # signs outbound envelopes TO peer
        "recv_key": recv_key,   # verifies inbound responses FROM peer
        "origin_id_for_send": token.kid,  # see docstring — CRITICAL
        "_friendship_key_version": 2,
        "enabled": active,
        "state": "ACTIVE" if active else "PENDING",
        "_friendship": True,
    }
    _name = token.issuer_name or token.label  # see FriendshipToken.issuer_name
    if _name:
        d["label"] = _name
    if token.expires is not None:
        d["_ft_expires"] = token.expires
    if token.bind_pub:
        d["_peer_bind_pub"] = token.bind_pub
    return d


# ── cross-process config lock (A2, 2026-07-20) ─────────────────────────

CONFIG_LOCK_NAME = ".a2a_config.lock"


@contextlib.contextmanager
def config_file_lock(*dirs: Path):
    """Cross-PROCESS advisory lock serialising read-modify-write cycles on
    the A2A origin/endpoint config files inside ``dirs``.

    Why: the Console PATCH routes (``a2a_pair.py``), the bridge receiver
    (peer reconnect notifications → :func:`update_endpoint_url`) and the
    voice CLI (``corvin-a2a set-url`` → :func:`activate_connection`) rewrite
    the same JSON files from DIFFERENT processes — an in-process
    ``threading.Lock`` cannot serialise them, so a peer could time reconnect
    notifications to silently revert a fresh operator edit (e.g.
    ``enabled: false``). Every RMW writer takes this lock (in addition to
    any thread lock) so the read→modify→write cycle is atomic across
    processes.

    Mechanics (pattern from ``operator/license/compute_quota.py`` — the
    repo's platform-independence constraint is hard): one ``.a2a_config.lock``
    file per directory, locked via ``fcntl.flock`` on POSIX and
    ``msvcrt.locking`` on Windows. Multiple dirs are locked in sorted path
    order (deterministic → deadlock-free for writers like
    :func:`activate_connection` that touch both dirs). Advisory fail-soft:
    if a lock file cannot be created/locked (exotic FS, containers), proceed
    unlocked rather than break the operation — matching compute_quota's
    documented degradation.

    A lock that is merely BUSY is not that case and does NOT fail soft: the
    acquire is bounded by ``LOCK_TIMEOUT_SECONDS`` and raises
    :class:`FriendshipLockBusy`, which callers on a request path map to 503.
    Every lock taken before the failure is released on the way out.
    """
    handles: list[tuple[Any, bool]] = []
    try:
        for d in sorted({Path(d).resolve() for d in dirs}, key=str):
            try:
                d.mkdir(parents=True, exist_ok=True)
                lock_path = d / CONFIG_LOCK_NAME
                lf = open(lock_path, "a+")
                try:
                    os.chmod(lock_path, 0o600)
                except OSError:
                    pass
            except OSError:
                continue  # fail-soft: no lock file → advisory no-op for this dir
            locked = False
            try:
                if _IS_WINDOWS and msvcrt is not None:
                    lf.seek(0)
                    msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
                    locked = True
                elif fcntl is not None:
                    _acquire_exclusive(
                        lf, f"a2a friendship config ({d.name})",
                        timeout=LOCK_TIMEOUT_SECONDS,
                    )
                    locked = True
            except FriendshipLockBusy:
                # NOT fail-soft: another writer holds it. Release what we
                # already took, close this handle, and refuse.
                handles.append((lf, False))
                raise
            except OSError:
                pass  # advisory fail-soft (mirrors compute_quota)
            handles.append((lf, locked))
        yield
    finally:
        for lf, locked in reversed(handles):
            if locked:
                try:
                    if _IS_WINDOWS and msvcrt is not None:
                        lf.seek(0)
                        msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                    elif fcntl is not None:
                        fcntl.flock(lf, fcntl.LOCK_UN)
                except OSError:
                    pass
            lf.close()


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

    The whole read-modify-write runs under :func:`config_file_lock` (A2,
    2026-07-20) so a concurrent writer in another process (Console PATCH,
    reconnect-driven :func:`update_endpoint_url`) cannot be lost.
    """
    origin_path = kid_path(origins_dir, kid)
    endpoint_path = kid_path(endpoints_dir, kid)

    peer_url = peer_url.strip().rstrip("/")

    with config_file_lock(origins_dir, endpoints_dir):
        if not origin_path.exists() and not endpoint_path.exists():
            raise FriendshipError(f"connection {kid!r} not found")

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
    # Durable temp+fsync+rename (ADR-0198 audit-truthfulness, 2026-07-19): the
    # receiver audits an endpoint rewrite only AFTER this returns, so the bytes
    # must be on disk — an un-fsynced rename can lose the write across a crash
    # while the audit chain asserts it happened.
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(data, indent=2, sort_keys=True).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
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

# Reconnect target URLs are length-capped like every other operator-facing
# A2A URL surface (mirrors remote_trigger_receiver._MAX_RECONNECT_URL_LEN).
_MAX_RECONNECT_URL_LEN = 512


# ── ADR-0198 reconnect host classification (danger-category model) ──────
#
# 2026-07-19 REDESIGN (adversarial refutation round). The first hardening
# used a blunt "globally routable only" rule (``ipaddress.is_global``). That
# was wrong in two directions:
#
#   * it BANNED every legitimate LAN / hotspot reconnect (172.20.10.x iPhone
#     tether, 192.168.x home LAN — the exact ADR-0198 use case), causing a
#     permanent 5-minute re-broadcast storm on those deployments; and
#   * it still ACCEPTED NAT64 / 6to4 / v4-mapped IPv6 literals whose EMBEDDED
#     IPv4 is loopback / link-local metadata (Python reports the outer v6 as
#     ``is_global == True``): e.g. ``[64:ff9b::7f00:1]`` == 127.0.0.1,
#     ``[64:ff9b::a9fe:a9fe]`` == 169.254.169.254.
#
# The replacement is a DANGER-CATEGORY model, not global-vs-private:
#
#   forbidden — loopback, link-local (incl. 169.254.169.254 cloud metadata
#               and fe80::/10), unspecified (0.0.0.0/::), multicast, reserved.
#               NEVER a peer's legitimate endpoint → rejected unconditionally.
#   private   — RFC1918 (10/8, 172.16/12, 192.168/16), CGNAT 100.64/10,
#               IPv6 ULA fc00::/7. Allowed ONLY when the PREVIOUS stored URL
#               was ALSO private/LAN (established LAN pairing renumbering).
#               global→private is the SSRF "pull us inward" signature → reject.
#   global    — everything else. global→global allowed (existing checks).
#
# Every resolved address AND every embedded-IPv4 it carries is classified.
import ipaddress as _ipa

_NAT64_WKP = _ipa.ip_network("64:ff9b::/96")            # RFC 6052 well-known
_NAT64_LOCAL = _ipa.ip_network("64:ff9b:1::/48")        # RFC 8215 local-use
_LAN_V4_NETS = (
    _ipa.ip_network("10.0.0.0/8"),
    _ipa.ip_network("172.16.0.0/12"),
    _ipa.ip_network("192.168.0.0/16"),
    _ipa.ip_network("100.64.0.0/10"),                   # CGNAT (RFC 6598)
)
_ULA_V6_NET = _ipa.ip_network("fc00::/7")               # IPv6 unique-local


def _embedded_ipv4s(ip6: "_ipa.IPv6Address") -> list["_ipa.IPv4Address"]:
    """Extract every IPv4 an IPv6 address embeds (v4-mapped, 6to4, NAT64)."""
    out: list[_ipa.IPv4Address] = []
    try:
        if ip6.ipv4_mapped is not None:
            out.append(ip6.ipv4_mapped)
    except (AttributeError, ValueError):
        pass
    try:
        if ip6.sixtofour is not None:
            out.append(ip6.sixtofour)
    except (AttributeError, ValueError):
        pass
    try:
        if ip6 in _NAT64_WKP or ip6 in _NAT64_LOCAL:
            # RFC 6052 / 8215: the IPv4 is the low 32 bits of the address.
            out.append(_ipa.IPv4Address(int(ip6) & 0xFFFFFFFF))
    except (ValueError, TypeError):
        pass
    return out


def _addr_is_forbidden(ip: "_ipa.IPv4Address | _ipa.IPv6Address") -> bool:
    return bool(
        ip.is_loopback or ip.is_link_local or ip.is_unspecified
        or ip.is_multicast or ip.is_reserved
    )


def _v4_is_lan(ip: "_ipa.IPv4Address") -> bool:
    return any(ip in net for net in _LAN_V4_NETS)


def _classify_addr(ip: "_ipa.IPv4Address | _ipa.IPv6Address") -> str:
    """Return ``"forbidden"``, ``"private"``, or ``"global"`` for one address.

    For IPv6, the embedded IPv4 (if any) is classified with the SAME rules and
    the more-dangerous verdict wins (forbidden > private > global) — this is
    what closes the NAT64/6to4/v4-mapped loopback+metadata bypass.
    """
    embedded: list[_ipa.IPv4Address] = []
    if isinstance(ip, _ipa.IPv6Address):
        embedded = _embedded_ipv4s(ip)
    if _addr_is_forbidden(ip) or any(_addr_is_forbidden(e) for e in embedded):
        return "forbidden"
    if isinstance(ip, _ipa.IPv4Address) and _v4_is_lan(ip):
        return "private"
    if isinstance(ip, _ipa.IPv6Address) and ip in _ULA_V6_NET:
        return "private"
    if any(_v4_is_lan(e) for e in embedded):
        return "private"
    if ip.is_global:
        return "global"
    # Not forbidden, not LAN, not globally routable (e.g. some documentation
    # / benchmarking ranges) — treat as forbidden, fail-closed.
    return "forbidden"


def _resolve_host_classes(host: str, scheme: str, port: int | None) -> set[str] | None:
    """Classify a hostname/IP-literal host into the set of danger categories
    of ALL its addresses. Returns None on resolution failure (fail-closed)."""
    import socket
    host = host.strip("[]").lower()
    try:
        ip = _ipa.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        return {_classify_addr(ip)}
    try:
        default_port = 443 if scheme == "https" else 80
        infos = socket.getaddrinfo(host, port or default_port, proto=socket.IPPROTO_TCP)
    except (OSError, ValueError):
        return None
    if not infos:
        return None
    classes: set[str] = set()
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]
        try:
            classes.add(_classify_addr(_ipa.ip_address(addr)))
        except ValueError:
            classes.add("forbidden")
    return classes


def _previous_host_is_lan(previous_url: str) -> bool:
    """True only when the stored/previous endpoint host resolves ENTIRELY to
    private/LAN addresses (fail-closed: unknown/unresolvable/global → False).
    Used to authorise a private→private LAN renumbering while blocking the
    global→private SSRF signature."""
    from urllib.parse import urlsplit
    if not previous_url:
        return False
    try:
        parts = urlsplit(previous_url)
        host = parts.hostname
        scheme = (parts.scheme or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host.strip("[]").lower() in ("localhost",) or host.endswith(".localhost"):
        return False
    classes = _resolve_host_classes(host, scheme, parts.port)
    return classes == {"private"}


def _reconnect_url_rejection_reason(new_url: str, previous_url: str) -> str | None:
    """ADR-0198 hardening (2026-07-19 redesign): SSRF / redirect-primitive gate.

    A reconnect notification is authenticated (per-pairing HMAC), but a
    *compromised peer* must not be able to repoint our outbound A2A calls
    (signed envelopes, IBC JWTs, attachments) at internal infrastructure —
    while a legitimate LAN / hotspot peer MUST still be able to renumber.
    Rules, all fail-closed (see the danger-category model above):

    - length ≤ 512, printable, no whitespace, parseable
    - scheme http(s) only; ``http`` is accepted ONLY when the previously
      stored URL was already ``http`` — an https→http downgrade is rejected
    - resolve the host (literal IPs used directly); for EVERY resolved address
      and every embedded-IPv4 (v4-mapped / 6to4 / NAT64):
        * forbidden category (loopback, link-local incl. 169.254.169.254,
          unspecified, multicast, reserved) → reject ``reconnect_url_forbidden_host``
        * private/LAN category → allowed ONLY if the previous stored host was
          ALSO private/LAN, else reject ``reconnect_url_global_to_private``
        * global → allowed
    - resolution failure rejects (``reconnect_url_unresolvable``)

    Returns an audit-safe rejection reason string, or None when acceptable.
    """
    from urllib.parse import urlsplit

    if not new_url or len(new_url) > _MAX_RECONNECT_URL_LEN:
        return "reconnect_url_invalid_length"
    if not new_url.isprintable() or any(c.isspace() for c in new_url):
        return "reconnect_url_bad_chars"
    try:
        parts = urlsplit(new_url)
    except ValueError:
        return "reconnect_url_unparseable"
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return "reconnect_url_bad_scheme"
    if scheme == "http":
        prev_scheme = ""
        if previous_url:
            try:
                prev_scheme = (urlsplit(previous_url).scheme or "").lower()
            except ValueError:
                prev_scheme = ""
        if prev_scheme != "http":
            return "reconnect_url_scheme_downgrade"
    try:
        host = parts.hostname
    except ValueError:
        return "reconnect_url_unparseable"
    if not host:
        return "reconnect_url_no_host"
    host_l = host.strip("[]").lower()
    if host_l == "localhost" or host_l.endswith(".localhost") or host_l.endswith(".onion"):
        return "reconnect_url_forbidden_host"
    classes = _resolve_host_classes(host_l, scheme, parts.port)
    if classes is None:
        return "reconnect_url_unresolvable"
    if "forbidden" in classes:
        return "reconnect_url_forbidden_host"
    if "private" in classes:
        # Private/LAN target: allowed only as a LAN renumbering of an already
        # private/LAN pairing. global→private is the SSRF "pull us inward"
        # signature and is rejected.
        if _previous_host_is_lan(previous_url):
            return None
        return "reconnect_url_global_to_private"
    # All addresses global → allowed.
    return None


def validate_endpoint_url_change(
    kid: str, new_url: str, *, endpoints_dir: Path,
) -> str | None:
    """Read-only preflight for :func:`update_endpoint_url`.

    Returns an audit-safe rejection reason (see
    :func:`_reconnect_url_rejection_reason`, plus
    ``"no_matching_active_endpoint"`` for a missing / disabled / PENDING /
    non-friendship endpoint file), or None when the change would be applied.
    Performs NO write — the receiver uses this to audit-then-write
    (audit-first invariant, ADR-0198 hardening 2026-07-19).
    """
    if not is_valid_kid(kid):
        return "no_matching_active_endpoint"
    path = kid_path(endpoints_dir, kid)
    if not path.exists():
        return "no_matching_active_endpoint"
    try:
        cfg = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return "no_matching_active_endpoint"
    if not cfg.get("_friendship") or not cfg.get("enabled") or cfg.get("state") != "ACTIVE":
        return "no_matching_active_endpoint"
    return _reconnect_url_rejection_reason(
        new_url.strip().rstrip("/"), str(cfg.get("url") or ""),
    )


def update_endpoint_url(kid: str, new_url: str, *, endpoints_dir: Path) -> bool:
    """Rewrite the ``url`` field of an existing, ACTIVE friendship endpoint.

    Unlike :func:`activate_connection`, this does NOT create or enable a
    connection — it only updates a peer that is already ACTIVE, so a
    reconnect notification can never be used to bootstrap trust. Returns
    False (no-op) for a missing, disabled, PENDING, or non-friendship file,
    and for any URL that fails the ADR-0198 danger-category SSRF gate
    (:func:`_reconnect_url_rejection_reason`): non-http(s) scheme,
    https→http downgrade, a forbidden host (loopback, link-local incl. cloud
    metadata, unspecified, multicast, reserved — including those embedded in
    NAT64/6to4/v4-mapped IPv6 literals), a global→private redirect, or an
    unresolvable name.

    Egress-control honesty (2026-07-19 — corrected false claim): outbound A2A
    peer POSTs do NOT pass the L35 ``check_engine_egress`` gate — that gate is
    an engine-spawn control and is never applied to A2A peer URLs. The real
    controls on a reconnect-updated URL are: (a) redirect-following is disabled
    in the sender's ``_http_post`` (a 3xx is an error, not a silent internal
    fetch), and (b) this danger-category host gate. A DNS-rebinding residual
    remains (a compromised paired peer using short-TTL DNS that resolves global
    at check-time and private at send-time) and is accepted for this release:
    the peer must already be a cryptographically-paired ACTIVE friend and
    redirects are blocked.
    """
    if not is_valid_kid(kid):
        return False
    path = kid_path(endpoints_dir, kid)
    new_url = new_url.strip().rstrip("/")
    if not new_url:
        return False
    # A2 (2026-07-20): the read→gate→write below is a cross-process RMW —
    # without the file lock a peer could time reconnect notifications to
    # revert a concurrent operator edit (e.g. enabled=false) via lost update.
    with config_file_lock(endpoints_dir):
        if not path.exists():
            return False
        try:
            cfg = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return False
        if not cfg.get("_friendship") or not cfg.get("enabled") or cfg.get("state") != "ACTIVE":
            return False
        # ADR-0198 hardening (2026-07-19): fail-closed SSRF gate.
        if _reconnect_url_rejection_reason(new_url, str(cfg.get("url") or "")) is not None:
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


# ── ADR-0258 Stage 2 — mesh-VPN preferred address ───────────────────────
#
# If the operator already runs Tailscale/Headscale, that mesh VPN already
# solves "stable address regardless of physical network" — prefer it over
# detect_local_ip()'s raw interface address instead of reinventing NAT
# traversal. Scoped to Tailscale/Headscale for v1 (one well-documented,
# stable, scriptable CLI contract: `tailscale ip -4`). Generic WireGuard has
# no portable way to discover "the interface's intended stable address"
# without assuming a specific setup — an operator running plain WireGuard
# can already get the same effect today with zero new code by typing that
# address into Settings -> A2A -> My URL by hand.
_TAILSCALE_TIMEOUT_S = 2.0


def detect_mesh_vpn_address() -> str:
    """Best-effort Tailscale/Headscale IPv4 address, or "" on any failure.

    Shells out to `tailscale ip -4` — silently degrades (empty string) when
    the CLI is not installed, not logged in, or times out, exactly like
    detect_local_ip()'s degrade-on-any-failure contract. Never raises.
    """
    import shutil
    import subprocess

    exe = shutil.which("tailscale")
    if not exe:
        return ""
    try:
        proc = subprocess.run(
            [exe, "ip", "-4"],
            capture_output=True, text=True, timeout=_TAILSCALE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    addr = proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else ""
    try:
        import ipaddress as _ipa
        _ipa.IPv4Address(addr)
    except ValueError:
        return ""
    return addr


def suggest_my_url(*, scheme: str = "http", port: int = 8765) -> str | None:
    """Best local-address suggestion for 'My URL', precedence: mesh-VPN
    address (stable, preferred) > raw local-interface address (existing
    fallback). Returns None when neither is available. Callers needing the
    request-derived reverse-proxy hint (X-Forwarded-Host) keep that logic —
    this only covers the two locally-detected sources ADR-0258 adds/reuses.
    """
    mesh = detect_mesh_vpn_address()
    if mesh:
        return f"{scheme}://{mesh}:{port}"
    local = detect_local_ip()
    if local:
        return f"{scheme}://{local}:{port}"
    return None


def _local_ipv4_addresses() -> set[str]:
    """Every IPv4 address currently assigned to a local interface (Linux:
    SIOCGIFADDR per interface), unioned with detect_local_ip(). Best-effort —
    on any failure the set degrades to the outbound-interface address alone."""
    addrs: set[str] = set()
    try:
        import fcntl
        import socket
        import struct
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for _idx, name in socket.if_nameindex():
                try:
                    packed = fcntl.ioctl(
                        s.fileno(), 0x8915,  # SIOCGIFADDR
                        struct.pack("256s", name[:15].encode("utf-8")),
                    )
                    addrs.add(socket.inet_ntoa(packed[20:24]))
                except OSError:
                    continue
    except Exception:  # noqa: BLE001 — non-Linux / sandboxed: fall back below
        pass
    local = detect_local_ip()
    if local:
        addrs.add(local)
    return addrs


def heal_my_url() -> str | None:
    """Rewrite a persisted ``my_a2a_url`` whose host is a stale LAN address.

    DHCP hands out a new private address; the persisted URL keeps the old one
    and every peer (and every ack/reconnect this instance sends) is pointed at
    a dead host — measured 2026-09-24: my_url still named 192.168.2.73 while
    the interface was 192.168.2.131, and the IP-change broadcast re-announced
    the dead URL because it never touched my_url. Rewrites ONLY when all hold:
    no ``CORVIN_A2A_URL`` override, the host is a literal private (RFC 1918)
    IPv4 that no local interface carries anymore, and the current outbound
    address is private too. Hostnames, public/port-forwarded addresses, mesh
    addresses and a still-assigned address on another interface are the
    operator's choice and are never touched. Scheme, port and path are kept.
    Returns the new URL, or None when nothing changed.
    """
    import ipaddress as _ipa
    import urllib.parse as _up

    if os.environ.get("CORVIN_A2A_URL"):
        return None
    current = get_my_url()
    if not current:
        return None
    parts = _up.urlsplit(current)
    try:
        host_ip = _ipa.IPv4Address(parts.hostname or "")
    except ValueError:
        return None  # hostname / IPv6 — operator-managed
    if not host_ip.is_private or host_ip.is_loopback or host_ip.is_link_local:
        return None
    local = _local_ipv4_addresses()
    if str(host_ip) in local:
        return None
    new_ip = detect_local_ip()
    try:
        if not new_ip or not _ipa.IPv4Address(new_ip).is_private:
            return None
    except ValueError:
        return None
    netloc = new_ip + (f":{parts.port}" if parts.port else "")
    healed = _up.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    set_my_url(healed)
    import logging as _logging
    _logging.getLogger("corvin.a2a.friendship").warning(
        "A2A my_url healed: stale LAN address replaced by current interface address",
    )
    return healed


def _last_known_ip_path() -> Path:
    return _corvin_home() / "global" / "remote_trigger" / "last_known_ip"


def check_and_broadcast_reconnect(
    *, endpoints_dir: Path | None = None,
) -> int:
    """If the local interface IP changed since the last check, proactively
    push a signed reconnect notification (current ``get_my_url()``) to every
    ACTIVE friendship endpoint. Fail-soft: never raises, returns the number
    of peers the notification was DELIVERED to — i.e. that returned a
    cryptographically signed response, accept OR reject (0 on no-op or when
    every peer was unreachable). Delivery, not acceptance, drives IP
    persistence so a signed-rejecting peer cannot trigger an unbounded
    re-broadcast storm (2026-07-19 retry-storm fix).

    Intended to be polled from an existing background loop (e.g. the
    presence heartbeat) rather than run on its own thread.
    """
    current_ip = detect_local_ip()
    if not current_ip:
        return 0

    # Heal a stale persisted my_url FIRST and independently of last_known_ip:
    # the IP may have changed while no peer was ACTIVE (last_known_ip was then
    # persisted without any announcement), so an equality early-return below
    # must not be able to keep a dead URL forever.
    healed = None
    try:
        healed = heal_my_url()
    except Exception:  # noqa: BLE001 — best-effort, never break the heartbeat
        healed = None

    ip_path = _last_known_ip_path()
    try:
        last_ip = ip_path.read_text("utf-8").strip() if ip_path.exists() else ""
    except OSError:
        last_ip = ""

    if current_ip == last_ip and not healed:
        return 0

    def _persist_ip() -> None:
        try:
            ip_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = ip_path.with_suffix(".tmp")
            tmp.write_text(current_ip, encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, ip_path)
        except OSError:
            pass

    if not last_ip and not healed:
        # First observation this boot — nothing to compare against yet,
        # avoid announcing on every fresh start.
        _persist_ip()
        return 0

    try:
        from remote_trigger_sender import (  # noqa: PLC0415
            RemoteTriggerSender, RemoteEndpointRegistry,
        )
    except Exception:  # noqa: BLE001
        return 0

    registry = RemoteEndpointRegistry(endpoints_dir)
    sender = RemoteTriggerSender(endpoints_dir, registry)
    eligible: list[str] = []
    for endpoint_id in registry.list_ids():
        try:
            cfg = registry.load(endpoint_id)
        except Exception:  # noqa: BLE001
            continue
        if not cfg.get("_friendship") or cfg.get("state") != "ACTIVE":
            continue
        eligible.append(endpoint_id)

    if not eligible:
        # No peers to announce to — persist so we don't retry forever.
        _persist_ip()
        return 0

    my_url = get_my_url()
    if not my_url:
        # Peers exist but our own URL is unknown right now — do NOT persist
        # the new IP, so the change is retried on the next cycle instead of
        # being silently swallowed (2026-07-19 fix: previously the IP was
        # persisted before broadcasting, so an all-peers-unreachable cycle
        # lost the change forever).
        return 0

    # Bounded worst case: ≤ 10 s per peer (short reconnect timeout) and the
    # caller's 5-minute cadence tolerates one slow cycle; a hard 180 s
    # wall-clock budget stops a pathological many-dead-peers sweep from
    # starving the heartbeat loop. Peers skipped by the budget are retried
    # on the next cycle when the broadcast didn't reach anyone (IP not yet
    # persisted); once ≥1 peer was reached the remainder rely on their own
    # inbound-failure recovery path.
    _budget_deadline = time.monotonic() + 180.0
    delivered = 0
    for endpoint_id in eligible:
        if time.monotonic() > _budget_deadline:
            break
        try:
            # send_reconnect returns True on DELIVERY (signed response, accept
            # OR reject) — a signed-rejecting peer counts as delivered so we
            # stop re-broadcasting to it forever.
            if sender.send_reconnect(endpoint_id, my_url, timeout_s=10):
                delivered += 1
        except Exception:  # noqa: BLE001
            pass

    if delivered >= 1:
        # Persist ONLY after the new URL was delivered to at least one peer —
        # otherwise keep the stale value so the next cycle re-detects the
        # change and retries the broadcast (pending re-announce semantics).
        _persist_ip()
    return delivered


# ── ADR-0199: Lightweight Peer-Liveness Check (receiver-side heartbeat cache) ─

_endpoint_heartbeat_cache: dict[str, float] = {}
_HEARTBEAT_CACHE_MAX_ENTRIES = 10000  # Prevent unbounded growth
_HEARTBEAT_CACHE_MAX_AGE_S = 86400    # Evict entries older than 1 day


def _prune_stale_heartbeats() -> None:
    """Remove heartbeat entries older than MAX_AGE or if cache exceeds max size.

    Called automatically on each record_endpoint_heartbeat() when cache is full
    or entries are stale. Best-effort cleanup (no locking for Phase 2).
    Finding #10: Early return if under limit and no stale entries.
    """
    now = time.time()
    # Early return: if cache small enough, skip pruning entirely
    if len(_endpoint_heartbeat_cache) < _HEARTBEAT_CACHE_MAX_ENTRIES:
        # Check if any stale entries; skip sort if none
        stale = [k for k, ts in _endpoint_heartbeat_cache.items()
                 if (now - ts) > _HEARTBEAT_CACHE_MAX_AGE_S]
        if not stale:
            return  # No pruning needed (Finding #10: early exit)
        for k in stale:
            del _endpoint_heartbeat_cache[k]
        return

    # Over limit: evict oldest 10% (O(N log N) only when needed)
    oldest_keys = sorted(_endpoint_heartbeat_cache.items(), key=lambda x: x[1])
    evict_count = len(_endpoint_heartbeat_cache) // 10
    for k, _ in oldest_keys[:evict_count]:
        del _endpoint_heartbeat_cache[k]


def record_endpoint_heartbeat(origin_id: str) -> None:
    """ADR-0199: Record a heartbeat timestamp for an origin.

    Stores (or updates) the most recent successful ping response timestamp
    in the in-memory heartbeat cache. Called by the ping handler after a
    valid signed response is received.

    Prunes stale/old entries when cache reaches max size to prevent
    unbounded growth (max 10k origins, entries auto-evict after 1 day).
    """
    if origin_id and isinstance(origin_id, str):
        _prune_stale_heartbeats()
        _endpoint_heartbeat_cache[origin_id] = time.time()


def get_endpoint_last_heartbeat(origin_id: str) -> float | None:
    """ADR-0199: Retrieve the cached last-heartbeat timestamp for an origin.

    Returns the unix timestamp of the most recent successful ping response,
    or None if no heartbeat is cached for this origin_id. The timestamp is
    not validated or aged; callers should check freshness (e.g. 90s TTL).

    Note: Receiver-side heartbeat cache is in-memory and per-process.
    A future iteration (ADR-0199 Phase 2) may add persistent SQLite storage
    for cross-process / cross-restart visibility.
    """
    return _endpoint_heartbeat_cache.get(origin_id)


# ── Reciprocal friendship handshake (bidirectional pairing, 2026-07-29) ────
#
# The friendship-token flow as originally shipped was NOT bidirectional:
# create_friendship_token() wrote nothing to disk, so the issuer (A) had no
# record of the pairing until a SECOND, entirely independent token exchange
# happened in reverse — producing two unlinked kid/keypairs instead of one
# shared connection, and NEVER checking whether either side could actually
# reach the other before flipping state to "ACTIVE" (found 2026-07-29 while
# debugging an "A2A shows paired but the peer is unreachable" report;
# empirically reproduced in output/friendship_e2e_run.log).
#
# Fix, in one round trip:
#   1. A calls create_friendship_token() as before, but now ALSO persists a
#      short-lived PENDING record (save_pending_friendship) — just enough
#      (kid + the shared key) to verify an incoming reciprocal ack later.
#      Nothing sensitive beyond what the token itself already carries.
#   2. B imports the token as before (local origin+endpoint files, unchanged
#      to_origin_dict/to_endpoint_dict). If a peer URL is known, B ALSO calls
#      back to A's /v1/a2a/friendship-ack (send_friendship_ack), signed with
#      the SAME shared key both sides can derive independently
#      (_derive_channel_keys) — no separate credential, no operator action.
#   3. A verifies the ack against its pending record
#      (process_friendship_ack_request), writes ITS OWN origin+endpoint files
#      for B (reusing to_origin_dict/to_endpoint_dict via a reconstructed
#      FriendshipToken), then PINGS B back (ADR-0199 sender.ping) BEFORE ever
#      reporting the connection as reachable — url-presence alone is no
#      longer suf/ficient for either side to claim a live connection.
#   4. Both sides end up with the SAME kid, know about each other, and each
#      side's local `state` reflects a check IT PERFORMED ITSELF (never the
#      peer's self-report) — "ACTIVE" only after a successful ping, else
#      "UNREACHABLE" (a url is known but unreachable right now, distinct
#      from "PENDING" = no url known yet).

_ACK_MAX_URL_LEN = 512
_ACK_FRESHNESS_S = 30  # mirrors process_ping_request's ±30s window


def _pending_path(pending_dir: Path, kid: str) -> Path:
    return kid_path(pending_dir, kid)


def save_pending_friendship(token: FriendshipToken, *, pending_dir: Path) -> None:
    """Issuer-side (A): persist just enough of a freshly-created token to
    verify a future reciprocal ack for this ``kid`` — called from
    create_friendship_token() call sites, never from import. Single-use:
    consumed and deleted by the first valid ack (see
    process_friendship_ack_request), or by the operator revoking the
    not-yet-redeemed token (``delete_pending_friendship``)."""
    d: dict[str, Any] = {
        "kid": token.kid,
        "key": token.key,
        "label": token.label,
        "constraints": token.constraints,
        "expires": token.expires,
        "created_at": time.time(),
    }
    _atomic_write(_pending_path(pending_dir, token.kid), d)


def load_pending_friendship(kid: str, *, pending_dir: Path) -> dict[str, Any] | None:
    """Return the pending record for ``kid``, or None if absent/expired.

    An EXPIRED record is deleted on the spot (2026-09-25, finding 5): it can
    never be redeemed again, and leaving it on disk kept a relay slot and a
    live ack verifier around for a token the operator believes is dead."""
    if not is_valid_kid(kid):
        return None
    path = _pending_path(pending_dir, kid)
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    expires = d.get("expires")
    if expires is not None:
        try:
            expired = time.time() > float(expires) + _EXPIRY_TOLERANCE_S
        except (TypeError, ValueError):
            expired = True
        if expired:
            delete_pending_friendship(kid, pending_dir=pending_dir)
            return None
    return d


def delete_pending_friendship(kid: str, *, pending_dir: Path) -> bool:
    """Delete the issuer-side pending record for ``kid``. Returns True when a
    record existed. Used on the first valid ack AND by the operator's revoke
    of a created-but-never-redeemed token."""
    if not is_valid_kid(kid):
        return False
    path = _pending_path(pending_dir, kid)
    try:
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed
    except OSError:
        return False


# ── ack sender identity (2026-09-25, findings 2 + 3) ─────────────────────
#
# Both peers derive the SAME hmac_key from the one shared token key, so a
# plain HMAC over {kid, issued_at, peer_url} cannot say WHICH side produced
# an ack: B accepted its own ack reflected back at it (and rewrote its
# endpoint for A to its own URL), and anyone holding a leaked token could
# re-point the issuer at an arbitrary URL through the repeat-ack path.
#
# An ack now also carries ``sender_instance_id`` covered by a second HMAC,
# ``signature_v2`` (same key, canonical form INCLUDING the sender id). The
# legacy ``signature`` is still sent, so a peer running the previous version
# (which ignores unknown fields) keeps verifying it. The receiver:
#   * refuses an ack whose authenticated sender is itself (reflection);
#   * records the sender on the FIRST ack as ``_peer_instance_id`` and binds
#     every later (repeat) ack to it;
#   * treats an ack without a valid v2 signature as LEGACY: accepted on the
#     first-ack path, but never allowed to change a stored URL on the repeat
#     path.
_NO_INSTANCE_IDS = frozenset({"", "00000000-0000-0000-0000-000000000000"})
_MAX_INSTANCE_ID_LEN = 128


def _local_instance_id() -> str:
    """This instance's UUID, or "" when unknown (never raises)."""
    try:
        from instance_identity import get_instance_id as _get_iid  # type: ignore[import-not-found]
        iid = str(_get_iid() or "")
    except Exception:  # noqa: BLE001
        return ""
    if iid in _NO_INSTANCE_IDS or len(iid) > _MAX_INSTANCE_ID_LEN:
        return ""
    return iid


def _clean_instance_id(raw: object) -> str | None:
    if not isinstance(raw, str) or raw in _NO_INSTANCE_IDS:
        return None
    if len(raw) > _MAX_INSTANCE_ID_LEN or not raw.isprintable():
        return None
    return raw


def _ack_canonical(
    kid: str, issued_at: int, peer_url: str, peer_label: str | None,
    sender_instance_id: str | None = None, bind_pub: str | None = None,
) -> bytes:
    d: dict[str, Any] = {"kid": kid, "issued_at": issued_at, "peer_url": peer_url}
    if peer_label is not None:
        d["peer_label"] = peer_label
    if sender_instance_id is not None:
        d["sender_instance_id"] = sender_instance_id
    if bind_pub is not None:
        d["bind_pub"] = bind_pub
    return json.dumps(d, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _verified_bind_pub(req: dict[str, Any], *, kid: str, issued_at: int, peer_url: str,
                       peer_label: str | None, sender: str | None, hmac_key: str) -> str | None:
    """The sender's binding public key, if ``signature_v3`` (pairing HMAC over
    the v2 fields + ``bind_pub``) verifies. See a2a_binding."""
    import a2a_binding as _bind  # noqa: PLC0415
    pub = _bind.clean_pub(req.get("bind_pub"))
    if pub is None or sender is None:
        return None
    try:
        key = bytes.fromhex(hmac_key)
    except (TypeError, ValueError):
        return None
    expected = _hmac.new(key, _ack_canonical(kid, issued_at, peer_url, peer_label, sender, pub),
                         "sha256").hexdigest()
    return pub if _hex_sig_eq(expected, req.get("signature_v3")) else None


def _hex_sig_eq(expected: str, presented: object) -> bool:
    """Constant-time compare that never raises: ``hmac.compare_digest`` raises
    TypeError on a non-ASCII str, which turned a malformed peer signature into
    an exception (HTTP 500) instead of an opaque rejection (2026-09-25)."""
    if not isinstance(presented, str) or not presented.isascii():
        return False
    return _hmac.compare_digest(expected, presented)


def _verify_ack_signature(
    req: dict[str, Any], *, kid: str, issued_at: int, peer_url: str,
    peer_label: str | None, hmac_key: str,
) -> tuple[bool, str | None]:
    """Return ``(authentic, sender_instance_id)``. ``sender_instance_id`` is
    set ONLY when the v2 signature covering it verified; a legacy ack
    (v1 signature only) yields ``(True, None)``."""
    try:
        key = bytes.fromhex(hmac_key)
    except (TypeError, ValueError):
        return False, None
    sig_v2 = req.get("signature_v2")
    sender = _clean_instance_id(req.get("sender_instance_id"))
    if isinstance(sig_v2, str) and sender is not None:
        expected = _hmac.new(
            key, _ack_canonical(kid, issued_at, peer_url, peer_label, sender), "sha256",
        ).hexdigest()
        if _hex_sig_eq(expected, sig_v2):
            return True, sender
        return False, None
    signature = req.get("signature")
    if not isinstance(signature, str):
        return False, None
    expected = _hmac.new(
        key, _ack_canonical(kid, issued_at, peer_url, peer_label), "sha256",
    ).hexdigest()
    return _hex_sig_eq(expected, signature), None


# ── pairing audit (2026-09-25, finding 8) ────────────────────────────────
#
# The issuer-side ack path creates a NEW trusted origin+endpoint (a peer that
# may now send us signed tasks) and the repeat path re-points where our
# signed tasks go — neither left a record in the hash chain. Both now write
# one content-free event BEFORE touching a file (audit-first). Details are
# ids, enums and booleans only: never a URL, a label or a key.
#   A2A.friendship_paired      — {endpoint_id, pairing, url_changed, reason, peer_bound}
#   A2A.friendship_url_updated — {endpoint_id, pairing, url_changed, reason, peer_bound}
AUDIT_EVENT_PAIRED = "A2A.friendship_paired"
AUDIT_EVENT_URL_UPDATED = "A2A.friendship_url_updated"
AUDIT_EVENT_PEER_REVOKED = "A2A.friendship_peer_revoked"
AUDIT_FIELDS = frozenset({"endpoint_id", "pairing", "url_changed", "reason", "peer_bound"})


class FriendshipAuditError(RuntimeError):
    """The hash-chained writer is present but the audit record did not land —
    the pairing write it was guarding must not happen."""


def _audit_writer() -> tuple[Any, Path] | None:
    """``(security_events module, chain path)`` or None when no hash-chained
    writer exists in this process (minimal deploy without forge)."""
    try:
        import audit as _audit_mod  # type: ignore[import-not-found]
        se = getattr(_audit_mod, "_se", None)
        if se is not None and callable(getattr(_audit_mod, "audit_path", None)):
            return se, Path(_audit_mod.audit_path())
    except Exception:  # noqa: BLE001 — fall through to the sender's writer
        pass
    try:
        import remote_trigger_sender as _rts  # type: ignore[import-not-found]
        se = getattr(_rts, "_forge_se", None)
        if se is not None and callable(getattr(_rts, "audit_path", None)):
            return se, Path(_rts.audit_path())
    except Exception:  # noqa: BLE001
        pass
    return None


def _audit_pairing_event(event_type: str, severity: str, **details: Any) -> None:
    """Audit-first write for a pairing state change. Raises
    :class:`FriendshipAuditError` when a writer exists but the write fails;
    a no-op only when this process has no hash-chained writer at all."""
    unknown = set(details) - AUDIT_FIELDS
    if unknown:  # programming error — never ship a field nobody allowlisted
        raise FriendshipAuditError(f"unregistered audit fields: {sorted(unknown)}")
    writer = _audit_writer()
    if writer is None:
        return
    se, path = writer
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        se.write_event(
            path, event_type, severity=severity, tool="", run_id="",
            details=details, hash_chain=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise FriendshipAuditError(f"{event_type} audit write failed") from exc


def _ack_url_rejection_reason(url: str) -> str | None:
    """First-pairing host gate for a redeemer-declared callback URL.

    Deliberately MORE permissive than :func:`_reconnect_url_rejection_reason`
    (which only allows private/LAN addresses when the PREVIOUS stored url was
    also private/LAN — there is no "previous" on a brand-new pairing, so that
    rule would reject the common two-LAN-machines case this feature exists
    for). Only the unconditionally-dangerous "forbidden" category (loopback,
    link-local incl. cloud metadata, unspecified, multicast, reserved —
    including embedded in NAT64/6to4/v4-mapped IPv6) is rejected; both
    private/LAN and global addresses are accepted for a first pairing.
    """
    from urllib.parse import urlsplit
    if not url or len(url) > _ACK_MAX_URL_LEN:
        return "ack_url_invalid_length"
    if not url.isprintable() or any(c.isspace() for c in url):
        return "ack_url_bad_chars"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "ack_url_unparseable"
    if (parts.scheme or "").lower() not in ("http", "https"):
        return "ack_url_bad_scheme"
    try:
        host = parts.hostname
    except ValueError:
        return "ack_url_unparseable"
    if not host:
        return "ack_url_no_host"
    host_l = host.strip("[]").lower()
    if host_l == "localhost" or host_l.endswith(".localhost") or host_l.endswith(".onion"):
        return "ack_url_forbidden_host"
    classes = _resolve_host_classes(host_l, (parts.scheme or "").lower(), parts.port)
    if classes is None:
        return "ack_url_unresolvable"
    if "forbidden" in classes:
        return "ack_url_forbidden_host"
    return None


class _AckNoRedirect(_urllib_request.HTTPRedirectHandler):
    """Minimal no-redirect urllib handler for the ack POST (mirrors
    remote_trigger_sender._NoRedirect) — a 3xx must become an error, never a
    silently-followed internal fetch."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, ANN001
        return None


def _relay_send_ack(
    to_kid: str, hmac_key: str, req_body: dict[str, Any], timeout_s: float,
) -> dict[str, Any] | None:
    """ADR-0258 Stage 3 — relay fallback for the B->A friendship-ack POST.

    Mirrors ``remote_trigger_sender._relay_post``'s transport (same AEAD
    wrapping, same relay wire protocol, same feature-flag/relay-URL guards)
    but returns ``None`` on any failure instead of raising — this module has
    no ``TransportError`` type of its own, and the caller
    (:func:`send_friendship_ack`) already treats ``None`` identically to a
    direct-path failure (``{"ok": False, "error": "unreachable"}``).

    ``req_body`` already carries its own signature (covering only
    ``{kid, issued_at, peer_url, peer_label?}`` — unchanged); a
    ``_relay_sender_instance_id`` marker is stamped into the plaintext
    AFTER that signature so the receiving ``RelayListener`` can detect and
    drop a self-delivered ack, without altering the signed contract the
    direct-HTTP ``/v1/a2a/friendship-ack`` route also verifies.
    """
    try:
        from corvin_core import feature_flags as _ff  # type: ignore[import-not-found]
        if not _ff.is_enabled("a2a_relay_fallback"):
            return None
    except ImportError:
        return None

    relay_url = get_my_relay_url()
    if not relay_url:
        return None

    correlation_id = secrets.token_hex(16)
    my_kid = f"{to_kid}:reply:{correlation_id}"
    my_relay_auth_key = secrets.token_hex(32)  # ephemeral, single-use — no TOFU needed

    try:
        from instance_identity import get_instance_id as _get_iid  # type: ignore[import-not-found]
        my_instance_id = _get_iid()
    except Exception:  # noqa: BLE001
        my_instance_id = ""

    relay_payload = dict(req_body)
    relay_payload["_relay_sender_instance_id"] = my_instance_id

    try:
        import a2a_relay as _relay  # type: ignore[import-not-found]
        import asyncio as _asyncio

        plaintext = json.dumps(relay_payload).encode("utf-8")
        nonce_hex, ct_hex = encrypt_for_relay(hmac_key, plaintext)

        result = _asyncio.run(_relay.relay_deliver_and_wait(
            relay_url=relay_url, my_kid=my_kid, my_relay_auth_key=my_relay_auth_key,
            to_kid=to_kid, nonce_hex=nonce_hex, ciphertext_hex=ct_hex,
            task_id=correlation_id, timeout_s=timeout_s,
        ))
        resp_plain = decrypt_from_relay(hmac_key, result["nonce"], result["ciphertext"])
        return json.loads(resp_plain)
    except Exception:  # noqa: BLE001 — connect/registration/delivery/decrypt failure
        return None


def _ack_round_trip(
    *, kid: str, hmac_key: str, recv_key: str, issuer_url: str, my_url: str,
    my_label: str | None, timeout_s: float, peer_bind_pub: str | None = None,
) -> dict[str, Any]:
    """Shared core: build+sign a friendship-ack request against ``issuer_url``,
    POST it (falling back to the relay on direct failure), and verify the
    signed response. Used by both :func:`send_friendship_ack` (fresh
    import, derives keys from the token) and :func:`retry_friendship_ack`
    (recheck refresh, reuses the ALREADY-derived keys already persisted on
    disk — the raw shared token key is never stored after import, so a
    retry cannot re-derive it and must go through this keys-based path
    instead). Best-effort: never raises.
    """
    import urllib.error as _urlerr
    import urllib.request as _urlreq

    my_url = (my_url or "").strip().rstrip("/")
    if not my_url:
        return {"ok": False, "error": "no_own_url"}
    if my_label is None:
        # Tell the peer what to call us (it uses it only when its own
        # operator gave the connection no name).
        my_label = local_display_name() or None

    issued_at = int(time.time())
    req_body: dict[str, Any] = {
        "kid": kid,
        "issued_at": issued_at,
        "peer_url": my_url,
    }
    clean_label = sanitize_label(my_label) if my_label else ""
    if clean_label:
        req_body["peer_label"] = clean_label
    label_for_sig = clean_label or None
    # Legacy signature (what a previous-version peer verifies) ...
    req_body["signature"] = _hmac.new(
        bytes.fromhex(hmac_key),
        _ack_canonical(kid, issued_at, my_url, label_for_sig), "sha256",
    ).hexdigest()
    # ... plus the v2 signature that binds WHO sent this ack (see
    # "ack sender identity" above). Omitted when our own id is unknown.
    my_iid = _local_instance_id()
    if my_iid:
        req_body["sender_instance_id"] = my_iid
        req_body["signature_v2"] = _hmac.new(
            bytes.fromhex(hmac_key),
            _ack_canonical(kid, issued_at, my_url, label_for_sig, my_iid), "sha256",
        ).hexdigest()
        # v3 (ADR-2064 round 4): our binding public key, and — once we know the
        # peer's — a MAC under the ECDH binding secret the token does not hold.
        import a2a_binding as _bind  # noqa: PLC0415
        my_pub = _bind.local_bind_pub()
        if my_pub:
            canon_v3 = _ack_canonical(kid, issued_at, my_url, label_for_sig, my_iid, my_pub)
            req_body["bind_pub"] = my_pub
            req_body["signature_v3"] = _hmac.new(
                bytes.fromhex(hmac_key), canon_v3, "sha256").hexdigest()
            if peer_bind_pub:
                mac = _bind.bind_mac(peer_bind_pub, kid, canon_v3)
                if mac:
                    req_body["bind_mac"] = mac

    ack_url = issuer_url.rstrip("/") + "/v1/a2a/friendship-ack"
    opener = _urlreq.build_opener(_AckNoRedirect())
    data = json.dumps(req_body).encode("utf-8")
    http_req = _urlreq.Request(
        ack_url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    via = "direct"
    try:
        if not issuer_url.strip():
            # No direct address for the peer (token without url, or a peer
            # only ever reachable through the relay): go straight to the
            # relay instead of failing the handshake.
            raise _urlerr.URLError("no_direct_url")
        if _ack_url_rejection_reason(issuer_url.strip().rstrip("/")) is not None:
            # The peer's address comes from its own token: never let it aim
            # this POST at loopback / link-local metadata / other forbidden
            # hosts (2026-09-25, finding 4). The relay, if any, still works.
            raise _urlerr.URLError("forbidden_direct_url")
        with opener.open(http_req, timeout=timeout_s) as resp:
            raw = resp.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                return {"ok": False, "error": "response_too_large"}
            payload = json.loads(raw.decode("utf-8"))
    except _urlerr.HTTPError as exc:
        # 400/402/403 are the peer's own protocol verdict — authoritative.
        # Anything else (404 from a stale address now owned by another
        # device, a 5xx from a proxy) says nothing about the peer: try the
        # relay before giving up.
        if exc.code in (400, 402, 403):
            return {"ok": False, "error": f"http_{exc.code}"}
        payload = _relay_send_ack(kid, hmac_key, req_body, timeout_s)
        if payload is None:
            return {"ok": False, "error": f"http_{exc.code}"}
        via = "relay"
    except (_urlerr.URLError, OSError, TimeoutError):
        # ADR-0258 Stage 3 (2026-08-02): the issuer's direct URL is
        # unreachable — try the relay before giving up. Without this, an
        # issuer only reachable via relay (the exact CGNAT/hotspot scenario
        # ADR-0258 was written for) can never complete the reciprocal
        # handshake, so `_peer_knows_us` stays permanently false even once a
        # relay is configured and enabled on both sides.
        payload = _relay_send_ack(kid, hmac_key, req_body, timeout_s)
        if payload is None:
            return {"ok": False, "error": "unreachable"}
        via = "relay"
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "error": "invalid_response"}

    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_response"}
    sig = payload.get("signature")
    if not isinstance(sig, str):
        if via == "relay" and payload.get("reason") == "license_limit":
            # The issuer's refusal carries no signature (it never signs a
            # rejection), but over the relay it arrived AEAD-sealed under the
            # pairing key — map it like the direct path's HTTP 402.
            return {"ok": False, "error": "http_402"}
        return {"ok": False, "error": "unsigned_response"}
    body_for_verify = {k: v for k, v in payload.items() if k != "signature"}
    expected = _hmac.new(
        bytes.fromhex(recv_key),
        json.dumps(body_for_verify, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        "sha256",
    ).hexdigest()
    if not _hex_sig_eq(expected, sig):
        return {"ok": False, "error": "bad_response_signature"}

    return {
        "ok": bool(payload.get("ok")),
        "reachable": bool(payload.get("reachable", False)),
        "peer_instance_id": payload.get("instance_id"),
        "peer_bind_pub": payload.get("bind_pub"),  # recv_key-verified above
        "via": via,
    }


def send_friendship_ack(
    token: FriendshipToken, *, my_url: str, my_label: str | None = None,
    timeout_s: float = 10,
) -> dict[str, Any]:
    """Redeemer-side (B): notify the issuer (A) that their token was
    imported, so A can complete a RECIPROCAL pairing in this SAME round trip
    instead of requiring a second, independent token exchange in reverse.

    Signed with the hmac_key BOTH sides derive independently from the
    token's shared key (_derive_channel_keys) — no separate credential, no
    extra operator step. Best-effort: any failure returns
    ``{"ok": False, "error": <category>}`` and NEVER raises — a network
    hiccup here must not break the LOCAL import that already succeeded.
    """
    if not (my_url or "").strip():
        return {"ok": False, "error": "no_own_url"}

    hmac_key, recv_key = _derive_channel_keys(token.key)
    return _ack_round_trip(
        kid=token.kid, hmac_key=hmac_key, recv_key=recv_key,
        issuer_url=token.url or "", my_url=my_url, my_label=my_label,
        timeout_s=timeout_s,
    )


def retry_friendship_ack(
    kid: str, *, endpoints_dir: Path, my_label: str | None = None,
    timeout_s: float = 10,
) -> dict[str, Any]:
    """Re-attempt the reciprocal ack for an EXISTING connection (recheck
    refresh, 2026-08-02) — without redoing the whole token exchange.

    The initial :func:`send_friendship_ack` (at import time) is the only
    place ``_peer_knows_us`` is ever set; a plain reachability recheck
    (``friendship_recheck`` in ``routes/a2a_pair.py``) only re-pings and
    never touched it, so a connection whose first ack attempt failed
    (issuer unreachable at import time, or — before this fix — reachable
    only via a relay the ack itself never tried) stayed stuck showing
    "peer can't reach you back" forever, even after the issuer became
    reachable again. This reuses the ALREADY-derived ``hmac_key``/
    ``recv_key`` persisted in the endpoint file (the raw shared token key
    itself is discarded after import, so a genuine re-derivation is not
    possible — nor needed, since these derived keys are exactly what the
    ack round trip signs and verifies with).

    Returns the same shape as :func:`send_friendship_ack`. Best-effort:
    never raises; a missing/unreadable endpoint file or missing own URL
    returns ``{"ok": False, "error": ...}`` like any other failure.
    """
    if not is_valid_kid(kid):
        return {"ok": False, "error": "endpoint_unreadable"}
    endpoint_path = kid_path(Path(endpoints_dir), kid)
    try:
        cfg = json.loads(endpoint_path.read_text("utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "endpoint_unreadable"}
    if not cfg.get("_friendship"):
        return {"ok": False, "error": "not_a_friendship_connection"}

    hmac_key = cfg.get("hmac_key")
    recv_key = cfg.get("recv_key")
    issuer_url = cfg.get("url") or ""
    if issuer_url.endswith("/v1/a2a/receive"):
        issuer_url = issuer_url[: -len("/v1/a2a/receive")]
    if not (isinstance(hmac_key, str) and isinstance(recv_key, str)):
        return {"ok": False, "error": "endpoint_config_incomplete"}

    my_url = get_my_url()
    if not my_url:
        return {"ok": False, "error": "no_own_url"}

    result = _ack_round_trip(
        kid=kid, hmac_key=hmac_key, recv_key=recv_key,
        issuer_url=issuer_url, my_url=my_url, my_label=my_label,
        timeout_s=timeout_s, peer_bind_pub=cfg.get("_peer_bind_pub"),
    )
    if result.get("ok"):
        remember_peer_instance_id(
            kid, result.get("peer_instance_id"), endpoints_dir=Path(endpoints_dir),
            origins_dir=_sibling_origins_dir(Path(endpoints_dir)),
            peer_bind_pub=result.get("peer_bind_pub"), via=result.get("via"),
        )
    return result


def _sibling_origins_dir(endpoints_dir: Path) -> Path | None:
    """The origins dir that pairs with ``endpoints_dir`` (same resolution the
    console and receiver use), so a binding learned on a hello lands on BOTH
    files — the revoke-notice and repeat-ack gates read the origin too."""
    env = os.environ.get("REMOTE_ORIGINS_DIR")
    if env:
        return Path(env)
    names = {"remote_endpoints": "remote_origins", "endpoints": "origins"}
    sib = names.get(endpoints_dir.name)
    if sib is None:
        return None
    cand = endpoints_dir.parent / sib
    return cand if cand.is_dir() else None


def remember_peer_instance_id(
    kid: str, peer_instance_id: object, *, endpoints_dir: Path,
    origins_dir: Path | None = None, peer_bind_pub: object = None,
    via: str | None = None,
) -> bool:
    """Record the peer's instance id learned from a VERIFIED (recv_key-signed)
    ack response, so later repeat acks from the peer are bound to it (see
    "ack sender identity"). Never overwrites an existing binding, never binds
    a different instance than one already pinned on ANY of the pairing's
    files, and never records our own id. Best-effort: returns False on any
    failure.

    A binding PUBLIC KEY is never adopted here (round 6). After pairing, every
    response path is spoofable by a leaked-token holder (relay fan-out, or a
    URL moved on a legacy pairing), so keys come only from the signed token
    (issuer's ``bpk``) and the first ack (redeemer's ``bind_pub``). Legacy
    pairings keep the instance-id rule until re-paired. ``peer_bind_pub`` and
    ``via`` are accepted for call compatibility and ignored.
    """
    del peer_bind_pub, via
    iid = _clean_instance_id(peer_instance_id)
    if iid is None or not is_valid_kid(kid) or iid == _local_instance_id():
        return False
    dirs = [Path(endpoints_dir)] + ([Path(origins_dir)] if origins_dir is not None else [])
    wrote = False
    try:
        with config_file_lock(*dirs):
            cfgs: list[tuple[Path, dict]] = []
            for d in dirs:
                path = kid_path(d, kid)
                try:
                    cfg = json.loads(path.read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                if cfg.get("_friendship"):
                    cfgs.append((path, cfg))
            # One pin across ALL files (round 6): a TOFU pin on the endpoint
            # must stop a different instance from being bound on the origin.
            pinned = next((c.get(k) for _p, c in cfgs
                           for k in ("_peer_instance_id", "instance_id") if c.get(k)), None)
            if pinned and pinned != iid:
                return False
            for path, cfg in cfgs:
                if not cfg.get("_peer_instance_id"):
                    cfg["_peer_instance_id"] = iid
                    _atomic_write(path, cfg)
                    wrote = True
    except (FriendshipLockBusy, OSError):
        return False
    return wrote


# ── revoke notice (2026-09-25) ───────────────────────────────────────────
#
# Revoking a friendship deletes our keys, and the relay listener unregisters
# the kid — so afterwards the peer can no longer get an AUTHORITATIVE answer:
# its ping/ack go unanswered over the relay, which is indistinguishable from
# an outage, and it kept showing "peer knows us" forever. The revoking side
# therefore sends one signed notice with the old keys BEFORE deleting them
# (direct, else relay). It rides the friendship-ack channel so both
# transports and the relay listener's ack dispatch carry it unchanged; a
# previous-version receiver rejects it as `missing_fields` (no peer_url) and
# changes nothing.
REVOKE_NOTICE_TYPE = "revoke"
_REVOKE_WINDOW_S = 300


def _revoke_canonical(kid: str, issued_at: int, sender: str) -> bytes:
    return f"corvin-a2a-revoke|{kid}|{issued_at}|{sender}".encode("utf-8")


def relay_url_rejection_reason(url: str) -> str | None:
    """Host gate for a relay URL chosen by a token ISSUER (``rly``). Same host
    classes the ack-URL gate forbids (loopback, link-local / cloud metadata,
    unspecified, multicast, reserved; unresolvable): the listener would open
    a WebSocket there and register every pairing's relay credential."""
    from urllib.parse import urlsplit as _us
    try:
        parts = _us(str(url or "").strip())
    except ValueError:
        return "relay_url_invalid"
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        return "relay_url_invalid"
    http_like = f"{'https' if parts.scheme == 'wss' else 'http'}://{parts.netloc}"
    reason = _ack_url_rejection_reason(http_like)
    return None if reason is None else f"relay_{reason}"


def adopt_token_relay(token_relay: str | None, *, endpoints_dir: Path,
                      exclude_kid: str | None = None) -> str:
    """Decide whether an imported token's relay becomes OUR relay.

    Returns ``"adopted"``, ``"same"``, ``"none"`` (token carries no relay),
    ``"explicit"`` (the operator chose one — never overridden), ``"rejected"``
    (host gate) or ``"kept_existing"``. The last case (round 3): other
    pairings already rendezvous on the current relay; silently switching
    would strand them AND hand their relay credentials to the new host. The
    import reports it instead so the operator can decide.
    """
    if not token_relay:
        return "none"
    if my_relay_url_is_explicit():
        return "explicit"
    current = get_my_relay_url()
    if current == token_relay:
        return "same"  # already ours (the common default-relay case): no DNS needed
    if relay_url_rejection_reason(token_relay) is not None:
        return "rejected"
    others = [p for p in Path(endpoints_dir).glob("*.json")
              if p.stem != exclude_kid and _is_friendship_file(p)]
    if others and current:
        return "kept_existing"
    set_my_relay_url(token_relay)
    return "adopted"


def _is_friendship_file(path: Path) -> bool:
    try:
        return bool(json.loads(path.read_text("utf-8")).get("_friendship"))
    except (OSError, ValueError):
        return False


def send_revoke_notice(kid: str, *, endpoints_dir: Path, timeout_s: float = 5.0) -> dict[str, Any]:
    """Tell the peer that we revoked ``kid``. Best-effort, never raises; call
    it BEFORE the connection files are deleted (it needs the keys)."""
    import urllib.error as _urlerr
    import urllib.request as _urlreq

    if not is_valid_kid(kid):
        return {"ok": False, "error": "invalid_kid"}
    try:
        cfg = json.loads((endpoints_dir / f"{kid}.json").read_text("utf-8"))
        hmac_key = str(cfg["hmac_key"])
        bytes.fromhex(hmac_key)
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "endpoint_unreadable"}
    sender = _local_instance_id()
    if not sender:
        return {"ok": False, "error": "no_instance_id"}
    issued_at = int(time.time())
    import a2a_binding as _bind  # noqa: PLC0415
    _peer_pub = _bind.clean_pub(cfg.get("_peer_bind_pub"))
    body = {
        "type": REVOKE_NOTICE_TYPE, "kid": kid, "issued_at": issued_at,
        # Empty peer_url: the relay listener classifies ack-channel frames by
        # this key, and a previous-version receiver rejects the notice as
        # missing_fields ("" is falsy) without changing anything.
        "peer_url": "",
        "sender_instance_id": sender,
        "signature": _hmac.new(bytes.fromhex(hmac_key),
                               _revoke_canonical(kid, issued_at, sender), "sha256").hexdigest(),
    }
    if _peer_pub:
        _mac = _bind.bind_mac(_peer_pub, kid, _revoke_canonical(kid, issued_at, sender))
        if _mac:
            body["bind_mac"] = _mac
    url = str(cfg.get("url") or "")
    base = url[: -len("/v1/a2a/receive")] if url.endswith("/v1/a2a/receive") else url
    base = base.strip().rstrip("/")
    if base and _ack_url_rejection_reason(base) is None:
        try:
            req = _urlreq.Request(base + "/v1/a2a/friendship-ack",
                                  data=json.dumps(body).encode("utf-8"), method="POST",
                                  headers={"Content-Type": "application/json"})
            with _urlreq.build_opener(_AckNoRedirect()).open(req, timeout=timeout_s) as resp:
                if 200 <= resp.status < 300:
                    return {"ok": True, "via": "direct"}
        except (_urlerr.URLError, OSError, TimeoutError, ValueError):
            pass
    payload = _relay_send_ack(kid, hmac_key, body, timeout_s)
    if isinstance(payload, dict) and payload.get("ok"):
        return {"ok": True, "via": "relay"}
    return {"ok": False, "error": "unreachable"}


def _process_revoke_notice(
    req: dict[str, Any], *, origins_dir: Path, endpoints_dir: Path,
) -> tuple[int, dict[str, Any]]:
    """Receiving side of :func:`send_revoke_notice`: verified against the keys
    we hold for ``kid``, then marks the connection as no longer known by the
    peer. Never deletes anything — what to do with a revoked connection stays
    the operator's decision."""
    kid, issued_at = req.get("kid"), req.get("issued_at")
    signature = req.get("signature")
    sender = _clean_instance_id(req.get("sender_instance_id"))
    if not isinstance(kid, str) or not is_valid_kid(kid):
        return 400, {"reason": "invalid_kid"}
    if isinstance(issued_at, bool) or not isinstance(issued_at, int) or not isinstance(signature, str):
        return 400, {"reason": "missing_fields"}
    if sender is None:
        return 400, {"reason": "missing_fields"}
    origin_path, endpoint_path = origins_dir / f"{kid}.json", endpoints_dir / f"{kid}.json"
    try:
        cfg = json.loads(origin_path.read_text("utf-8"))
        key = bytes.fromhex(str(cfg["hmac_key"]))
    except Exception:  # noqa: BLE001
        return 403, {"reason": "ack_rejected"}  # opaque: same as a bad signature
    expected = _hmac.new(key, _revoke_canonical(kid, issued_at, sender), "sha256").hexdigest()
    if not _hex_sig_eq(expected, signature):
        return 403, {"reason": "ack_rejected"}
    if abs(time.time() - issued_at) > _REVOKE_WINDOW_S:
        return 403, {"reason": "ack_rejected"}
    if sender == _local_instance_id():
        return 400, {"reason": "ack_reflected"}
    # Binding lives on origin AND/OR endpoint (a hello records it on the
    # endpoint first) — read both, like the repeat-ack gate (round 5).
    try:
        ecfg = json.loads(endpoint_path.read_text("utf-8"))
    except (OSError, ValueError):
        ecfg = {}
    import a2a_binding as _bind  # noqa: PLC0415
    bound = (cfg.get("_peer_instance_id") or ecfg.get("_peer_instance_id")
             or ecfg.get("instance_id"))
    if bound and bound != sender:
        return 403, {"reason": "ack_peer_mismatch"}
    stored_pub = (_bind.clean_pub(cfg.get("_peer_bind_pub"))
                  or _bind.clean_pub(ecfg.get("_peer_bind_pub")))
    if stored_pub and not _bind.verify_bind_mac(
            stored_pub, kid, _revoke_canonical(kid, issued_at, sender), req.get("bind_mac")):
        # A leaked-token holder knows the keys and the public instance id —
        # not the binding secret (round 4).
        return 403, {"reason": "ack_peer_unproven"}
    try:
        _audit_pairing_event(AUDIT_EVENT_PEER_REVOKED, "WARNING", endpoint_id=kid,
                             pairing="repeat", url_changed=False, reason="peer_revoked",
                             peer_bound=bool(bound))
    except FriendshipAuditError:
        return 503, {"reason": "audit_unavailable"}
    now = time.time()
    try:
        with config_file_lock(origins_dir, endpoints_dir):
            for path in (origin_path, endpoint_path):
                try:
                    data = json.loads(path.read_text("utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                data["_peer_knows_us"] = False
                data["_peer_reports_reachable"] = False
                data["_peer_revoked_at"] = now
                _atomic_write(path, data)
    except FriendshipLockBusy:
        return 503, {"reason": "busy"}
    return 200, {"ok": True}


def process_friendship_ack_request(
    req: Any, *, pending_dir: Path, origins_dir: Path, endpoints_dir: Path,
) -> tuple[int, dict[str, Any]]:
    """Issuer-side (A): shared core for ``POST /v1/a2a/friendship-ack``.

    Verifies a redeemer's reciprocal ack against the PENDING record saved by
    :func:`save_pending_friendship`, then completes a bidirectional pairing
    in this single round trip: writes A's own origin+endpoint files for the
    redeemer (reusing :func:`to_origin_dict`/:func:`to_endpoint_dict` via a
    reconstructed :class:`FriendshipToken`), PINGS the redeemer back
    (ADR-0199) to prove real reachability before ever reporting the
    connection as live, and returns a signed response so the redeemer's own
    UI reflects the SAME verified state.

    Anti-oracle ordering: signature is verified BEFORE any other rejection
    reason is distinguished — unknown-kid and bad-signature share one opaque
    403 (mirrors process_ping_request), so an unauthenticated caller cannot
    enumerate valid pending kids. The a2a_peers_max licence gate is likewise
    evaluated only for an authenticated ack, and inside the same
    ``config_file_lock`` as the write it guards (2026-09-25, finding 6: it
    used to count-then-write unlocked, BEFORE the signature check — ten
    concurrent acks all passed a limit of one).
    """
    if not isinstance(req, dict):
        return 400, {"reason": "envelope_not_object"}
    if req.get("type") == REVOKE_NOTICE_TYPE:
        return _process_revoke_notice(req, origins_dir=origins_dir, endpoints_dir=endpoints_dir)

    kid = req.get("kid")
    issued_at = req.get("issued_at")
    peer_url = req.get("peer_url")
    peer_label = req.get("peer_label")
    signature = req.get("signature")

    if not all((kid, issued_at, peer_url, signature)):
        return 400, {"reason": "missing_fields"}
    if not isinstance(kid, str) or not isinstance(peer_url, str) or not isinstance(signature, str):
        return 400, {"reason": "missing_fields"}
    if isinstance(issued_at, bool) or not isinstance(issued_at, int):
        return 400, {"reason": "invalid_issued_at"}
    if peer_label is not None and not isinstance(peer_label, str):
        return 400, {"reason": "missing_fields"}
    if not is_valid_kid(kid):
        # A kid is a path component on this side (see KID_RE). Format-only
        # verdict: reveals nothing about which kids exist.
        return 400, {"reason": "invalid_kid"}

    peer_url = peer_url.strip().rstrip("/")[:_ACK_MAX_URL_LEN]

    pending = load_pending_friendship(kid, pending_dir=pending_dir)
    if pending is None:
        # The pending record is consumed by the FIRST successful ack. A repeat
        # ack for an already-established pairing is legitimate and must not be
        # rejected forever: the redeemer's first response may have been lost,
        # its recheck retries the ack while `_peer_knows_us` is false, and a
        # peer whose IP changed re-announces its URL the same way. Without this
        # branch every such retry got the opaque 403 and the redeemer showed
        # "peer can't reach you back" permanently (measured 2026-09-24).
        return _process_repeat_ack(
            req, kid=kid, issued_at=issued_at, peer_url=peer_url,
            peer_label=peer_label, origins_dir=origins_dir, endpoints_dir=endpoints_dir,
        )

    try:
        hmac_key, recv_key = _derive_channel_keys(str(pending["key"]))
    except (KeyError, TypeError, ValueError):
        return 403, {"reason": "ack_rejected"}

    authentic, sender_iid = _verify_ack_signature(
        req, kid=kid, issued_at=issued_at, peer_url=peer_url,
        peer_label=peer_label, hmac_key=hmac_key,
    )
    if not authentic:
        return 403, {"reason": "ack_rejected"}

    # Freshness — authenticated callers only past this point, so a distinct
    # reason string is safe (mirrors process_ping_request's ordering).
    now = int(time.time())
    if abs(now - issued_at) > _ACK_FRESHNESS_S:
        return 400, {"reason": "stale_ack"}

    own_iid = _local_instance_id()
    if sender_iid is not None and own_iid and sender_iid == own_iid:
        return 400, {"reason": "ack_reflected"}

    rejection = _ack_url_rejection_reason(peer_url)
    if rejection is not None:
        return 400, {"reason": rejection}

    # The issuer's OWN name for this peer (the token label, e.g. "For Max")
    # wins; the peer's self-declared name only fills an unlabelled token.
    label = pending.get("label") or (sanitize_label(peer_label) if peer_label else "") or None
    constraints = dict(pending.get("constraints") or {})
    reconstructed = FriendshipToken(
        kid=kid, key=str(pending["key"]), url=peer_url, label=label,
        expires=pending.get("expires"), constraints=constraints,
    )
    origin_cfg = to_origin_dict(reconstructed)
    endpoint_cfg = to_endpoint_dict(reconstructed)
    if sender_iid is not None:
        origin_cfg["_peer_instance_id"] = sender_iid
        endpoint_cfg["_peer_instance_id"] = sender_iid
        _first_pub = _verified_bind_pub(req, kid=kid, issued_at=issued_at, peer_url=peer_url,
                                        peer_label=peer_label, sender=sender_iid, hmac_key=hmac_key)
        if _first_pub:
            origin_cfg["_peer_bind_pub"] = _first_pub
            endpoint_cfg["_peer_bind_pub"] = _first_pub

    origin_path = kid_path(origins_dir, kid)
    endpoint_path = kid_path(endpoints_dir, kid)
    try:
        with config_file_lock(origins_dir, endpoints_dir, pending_dir):
            # Re-check under the lock: two holders of one token acking at the
            # same moment must not both "complete" the first pairing (the
            # later write silently replacing the earlier peer). The loser is
            # handled exactly like any other repeat ack — bound to the winner.
            if load_pending_friendship(kid, pending_dir=pending_dir) is None:
                consumed_meanwhile = True
            else:
                consumed_meanwhile = False
                limited = _peers_max_reached(origins_dir, excluding_kid=kid)
                if limited:
                    return 402, {"reason": "license_limit"}
                _audit_pairing_event(
                    AUDIT_EVENT_PAIRED, "INFO",
                    endpoint_id=kid, pairing="first", url_changed=True,
                    reason="ack_verified", peer_bound=sender_iid is not None,
                )
                _atomic_write(origin_path, origin_cfg)
                _atomic_write(endpoint_path, endpoint_cfg)
                delete_pending_friendship(kid, pending_dir=pending_dir)
    except FriendshipLockBusy:
        return 503, {"reason": "busy"}
    except FriendshipAuditError:
        return 503, {"reason": "audit_unavailable"}

    if consumed_meanwhile:
        return _process_repeat_ack(
            req, kid=kid, issued_at=issued_at, peer_url=peer_url,
            peer_label=peer_label, origins_dir=origins_dir, endpoints_dir=endpoints_dir,
        )

    return _ack_ping_back_and_respond(
        kid=kid, recv_key=recv_key, origins_dir=origins_dir, endpoints_dir=endpoints_dir,
    )


def _peers_max_reached(origins_dir: Path, *, excluding_kid: str) -> bool:
    """ADR-0094 a2a_peers_max for the issuer-side ack path. The caller MUST
    hold ``config_file_lock(origins_dir, ...)`` so the count and the write it
    guards are one atomic step. The kid being (re)written is never counted
    against its own admission."""
    try:
        from license.validator import get_limit as _lic_get_limit  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return False
    try:
        _max = _lic_get_limit("a2a_peers_max")
    except Exception:  # noqa: BLE001
        return False
    if _max is None:
        return False
    limit = 1 if _max is True else (0 if _max is False else int(_max))
    if not Path(origins_dir).exists():
        return limit <= 0
    existing = sum(1 for p in Path(origins_dir).glob("*.json") if p.stem != excluding_kid)
    return existing >= limit


def _awaiting_activation(origin: dict[str, Any]) -> bool:
    """An imported connection that is disabled ONLY because the token carried
    no address (state PENDING) — as opposed to one the operator disabled."""
    return (not origin.get("enabled") and origin.get("state") == "PENDING"
            and not origin.get("_operator_disabled"))


def _process_repeat_ack(
    req: dict[str, Any], *, kid: str, issued_at: int, peer_url: str,
    peer_label: str | None, origins_dir: Path, endpoints_dir: Path,
) -> tuple[int, dict[str, Any]]:
    """A friendship-ack for a kid whose pending record is already consumed
    (or, on the redeemer's side, never existed — the issuer's hello). Accepted
    only for an ESTABLISHED, ENABLED ``_friendship`` origin, verified against
    the channel key that origin already holds. Effect: the peer is pinged
    back exactly as on the first ack, and its endpoint URL MAY be refreshed
    to ``peer_url`` (self-heals a peer whose IP changed).

    A URL rewrite re-points where this instance sends signed tasks, so it is
    gated far more tightly than the first pairing (2026-09-25, finding 2 —
    it used to be allowed for anyone holding the pairing key, under the
    permissive first-pairing host gate):

    * the ack must carry an authenticated ``sender_instance_id`` (v2
      signature); a legacy ack is accepted only when it changes nothing;
    * that sender must be the peer bound on the first ack
      (``_peer_instance_id``) — a second importer of a leaked token cannot
      take the connection over. A pairing from before binding existed is
      bound on its first authenticated repeat ack (trust on first use);
    * an ack whose sender is THIS instance is a reflection and is refused;
    * the new URL must pass :func:`_reconnect_url_rejection_reason` against
      the stored one (no https→http downgrade, no global→private pivot);
    * the change is audited (``A2A.friendship_url_updated``) BEFORE the write.

    Every failure before signature verification is the same opaque 403 (no
    kid oracle), the ±30 s freshness window bounds replay, and a
    disabled/revoked friendship is never resurrected.
    """
    origin_path = kid_path(origins_dir, kid)
    endpoint_path = kid_path(endpoints_dir, kid)
    try:
        origin = json.loads(origin_path.read_text("utf-8"))
    except (OSError, ValueError):
        return 403, {"reason": "ack_rejected"}
    hmac_key = origin.get("hmac_key")
    recv_key = origin.get("recv_key")
    if (not origin.get("_friendship") or not (origin.get("enabled") or _awaiting_activation(origin))
            or not isinstance(hmac_key, str) or len(hmac_key) != 64
            or not isinstance(recv_key, str) or len(recv_key) != 64):
        return 403, {"reason": "ack_rejected"}

    authentic, sender_iid = _verify_ack_signature(
        req, kid=kid, issued_at=issued_at, peer_url=peer_url,
        peer_label=peer_label, hmac_key=hmac_key,
    )
    if not authentic:
        return 403, {"reason": "ack_rejected"}

    if abs(int(time.time()) - issued_at) > _ACK_FRESHNESS_S:
        return 400, {"reason": "stale_ack"}
    own_iid = _local_instance_id()
    if sender_iid is not None and own_iid and sender_iid == own_iid:
        return 400, {"reason": "ack_reflected"}
    rejection = _ack_url_rejection_reason(peer_url)
    if rejection is not None:
        return 400, {"reason": rejection}

    receive_url = peer_url + "/v1/a2a/receive"
    try:
        with config_file_lock(origins_dir, endpoints_dir):
            # Re-read both under the lock: the verdict below must be made on
            # the same bytes the write replaces.
            try:
                origin = json.loads(origin_path.read_text("utf-8"))
            except (OSError, ValueError):
                return 403, {"reason": "ack_rejected"}
            activating = not origin.get("enabled") and _awaiting_activation(origin)
            if not origin.get("_friendship") or not (origin.get("enabled") or activating):
                return 403, {"reason": "ack_rejected"}
            endpoint_missing = False
            try:
                endpoint = json.loads(endpoint_path.read_text("utf-8"))
            except (OSError, ValueError):
                # Origin without endpoint (half-written pairing, or an endpoint
                # the operator deleted while the origin stayed enabled):
                # rebuild it from the origin's keys — same shape as
                # to_endpoint_dict().
                endpoint_missing = True
                endpoint = {
                    "endpoint_id": kid,
                    "hmac_key": hmac_key,
                    "recv_key": recv_key,
                    "origin_id_for_send": kid,
                    "_friendship_key_version": origin.get("_friendship_key_version", 2),
                    "enabled": True,
                    "state": "ACTIVE",
                    "_friendship": True,
                }
                if origin.get("label"):
                    endpoint["label"] = origin["label"]
                # Keep the pairing's binding on the rebuilt record (round 6):
                # this side's own senders read it from the endpoint.
                for _k in ("_peer_instance_id", "_peer_bind_pub"):
                    if origin.get(_k):
                        endpoint[_k] = origin[_k]

            stored_url = str(endpoint.get("url") or "")
            url_changed = stored_url != receive_url
            # A binding comes ONLY from our own outbound round trip verified
            # with recv_key against the stored URL (remember_peer_instance_id
            # on import / hello, or the sender's instance pin) — never from an
            # inbound ack (round 3: a leaked-token holder bound itself with a
            # URL-repeating ack and re-pointed the endpoint with the next one).
            bound = (_clean_instance_id(origin.get("_peer_instance_id"))
                     or _clean_instance_id(endpoint.get("_peer_instance_id"))
                     or _clean_instance_id(endpoint.get("instance_id")))
            if bound is not None:
                if sender_iid is None:
                    # Legacy-format ack on a bound pairing: a keep-alive is
                    # fine, a rewrite is not (it could be anyone with the key).
                    if url_changed or endpoint_missing:
                        return 403, {"reason": "ack_peer_unbound"}
                elif sender_iid != bound:
                    return 403, {"reason": "ack_peer_mismatch"}
            else:
                if sender_iid is None:
                    if url_changed or endpoint_missing:
                        return 403, {"reason": "ack_peer_unbound"}
                elif url_changed or endpoint_missing:
                    # Unbound pairing: nobody may re-point or rebuild it via an
                    # inbound ack. It gets bound by our own verified outbound
                    # hello, after which the bound peer can move.
                    return 403, {"reason": "ack_peer_unbound"}
                # else: a v2 keep-alive repeating the stored URL — accepted,
                # but it binds NOTHING (no inbound trust on first use).

            # Binding proof (ADR-2064 round 4): an instance id is public and
            # signature_v2 uses the token's key, so a leaked-token holder could
            # claim the bound id. Once the peer's binding public key is known,
            # any CHANGE needs a MAC under the ECDH secret the token lacks. A
            # plain keep-alive stays allowed — its response is how a peer that
            # missed our key on the first ack learns it.
            import a2a_binding as _bind  # noqa: PLC0415
            stored_pub = (_bind.clean_pub(origin.get("_peer_bind_pub"))
                          or _bind.clean_pub(endpoint.get("_peer_bind_pub")))
            if stored_pub and (url_changed or endpoint_missing):
                req_pub = _bind.clean_pub(req.get("bind_pub"))
                canon_v3 = _ack_canonical(kid, issued_at, peer_url, peer_label, sender_iid, req_pub)
                if (req_pub != stored_pub or sender_iid is None
                        or not _bind.verify_bind_mac(stored_pub, kid, canon_v3, req.get("bind_mac"))):
                    return 403, {"reason": "ack_peer_unproven"}

            if url_changed and stored_url:
                reason = _reconnect_url_rejection_reason(peer_url, stored_url)
                if reason is not None:
                    return 400, {"reason": reason}

            if url_changed or endpoint_missing:
                _audit_pairing_event(
                    AUDIT_EVENT_URL_UPDATED, "WARNING" if url_changed else "INFO",
                    endpoint_id=kid, pairing="repeat", url_changed=url_changed,
                    reason="endpoint_rebuilt" if endpoint_missing else "peer_reannounced",
                    peer_bound=bound is not None,
                )
                if activating:
                    # The imported-but-URL-less connection (token without an
                    # address) learns the issuer's address from this verified,
                    # bound hello: it becomes a live connection in BOTH
                    # directions. Before, nothing but a manual set-url ever
                    # enabled it, so a relay-only issuer could never send.
                    origin["enabled"] = True
                    origin["state"] = "ACTIVE"
                    if not endpoint.get("_operator_disabled"):
                        endpoint["enabled"] = True
                        endpoint["state"] = "ACTIVE"
                if activating:
                    _atomic_write(origin_path, origin)
                endpoint["url"] = receive_url
                _atomic_write(endpoint_path, endpoint)
    except FriendshipLockBusy:
        return 503, {"reason": "busy"}
    except FriendshipAuditError:
        return 503, {"reason": "audit_unavailable"}

    return _ack_ping_back_and_respond(
        kid=kid, recv_key=recv_key, origins_dir=origins_dir, endpoints_dir=endpoints_dir,
    )


def _ack_ping_back_and_respond(
    *, kid: str, recv_key: str, origins_dir: Path, endpoints_dir: Path,
) -> tuple[int, dict[str, Any]]:
    """Issuer-side tail shared by the first ack and every repeat ack: ping the
    redeemer back (ADR-0199), record the verified state, and return the
    response signed with ``recv_key``."""
    origin_path = kid_path(origins_dir, kid)
    endpoint_path = kid_path(endpoints_dir, kid)

    # Reachability proof (ADR-0199) — url-presence is no longer sufficient
    # for EITHER side to claim a live connection; ping the redeemer back
    # before this side reports itself reachable.
    reachable = False
    via: str | None = None
    try:
        from remote_trigger_sender import (  # type: ignore[import-not-found]
            RemoteEndpointRegistry as _RER, RemoteTriggerSender as _RTS,
        )
        _sender = _RTS(endpoints_dir, _RER(endpoints_dir))
        _ping = _sender.ping(kid, timeout_s=5)
        reachable = bool(_ping.reachable)
        via = getattr(_ping, "via", None) if reachable else None
    except Exception:  # noqa: BLE001 — reachability check is best-effort
        reachable = False

    # Record the verified state either way — a repeat ack for a pairing that
    # an earlier ping marked UNREACHABLE must be able to bring it back.
    # A verified ack is itself proof that the sender holds our record (it
    # signed with the pairing key) and that its message reached us — the
    # receiving side's mirror of the sender's peer_knows_us. Before
    # 2026-09-24 only the redeemer ever set it, so the issuer showed "peer
    # can't reach you back" forever (bug #5).
    new_state = "ACTIVE" if reachable else "UNREACHABLE"
    with config_file_lock(origins_dir, endpoints_dir):
        for p in (origin_path, endpoint_path):
            if not p.exists():
                continue
            try:
                cfg = json.loads(p.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            # The transport that answered is recorded with the state it
            # proves — otherwise the issuer showed an ACTIVE connection with no
            # "via" until the connectivity manager's next ping.
            via_stale = via is not None and cfg.get("_last_via") != via
            if (cfg.get("state") != new_state or not cfg.get("_peer_knows_us")
                    or not cfg.get("_peer_reports_reachable") or via_stale):
                cfg["state"] = new_state
                cfg["_peer_knows_us"] = True
                cfg["_peer_reports_reachable"] = True
                if via is not None:
                    cfg["_last_via"] = via
                _atomic_write(p, cfg)

    iid = _local_instance_id()

    response: dict[str, Any] = {
        "ok": True,
        "kid": kid,
        "reachable": reachable,
        "instance_id": iid,
    }
    try:
        import a2a_binding as _bind  # noqa: PLC0415
        _pub = _bind.local_bind_pub()
        if _pub:
            response["bind_pub"] = _pub  # covered by the recv_key signature below
    except Exception:  # noqa: BLE001
        pass
    resp_canonical = json.dumps(response, separators=(",", ":"), sort_keys=True)
    response["signature"] = _hmac.new(
        bytes.fromhex(recv_key), resp_canonical.encode("utf-8"), "sha256",
    ).hexdigest()
    return 200, response
