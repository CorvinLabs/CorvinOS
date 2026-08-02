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


def get_my_relay_url() -> str | None:
    """Return this instance's configured relay URL (ws:// or wss://), or
    None if never set — Stage 3 is inert without one, even if the
    a2a_relay_fallback feature flag is on."""
    env = os.environ.get("CORVIN_A2A_RELAY_URL")
    if env:
        return env.strip().rstrip("/") or None
    p = _my_relay_url_path()
    if p.exists():
        val = p.read_text("utf-8").strip().rstrip("/")
        return val or None
    return None


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
        label=(sanitize_label(label_raw) or None) if label_raw else None,
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
    if token.label:
        d["label"] = token.label
    if token.expires is not None:
        d["_ft_expires"] = token.expires
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
    if token.label:
        d["label"] = token.label
    if token.expires is not None:
        d["_ft_expires"] = token.expires
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
                    fcntl.flock(lf, fcntl.LOCK_EX)
                    locked = True
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
    origin_path = origins_dir / f"{kid}.json"
    endpoint_path = endpoints_dir / f"{kid}.json"

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
    path = endpoints_dir / f"{kid}.json"
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
    path = endpoints_dir / f"{kid}.json"
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

    ip_path = _last_known_ip_path()
    try:
        last_ip = ip_path.read_text("utf-8").strip() if ip_path.exists() else ""
    except OSError:
        last_ip = ""

    if current_ip == last_ip:
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

    if not last_ip:
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
    return pending_dir / f"{kid}.json"


def save_pending_friendship(token: FriendshipToken, *, pending_dir: Path) -> None:
    """Issuer-side (A): persist just enough of a freshly-created token to
    verify a future reciprocal ack for this ``kid`` — called from
    create_friendship_token() call sites, never from import. Single-use:
    consumed and deleted by the first valid ack (see
    process_friendship_ack_request)."""
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
    """Return the pending record for ``kid``, or None if absent/expired."""
    path = _pending_path(pending_dir, kid)
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    expires = d.get("expires")
    if expires is not None:
        try:
            if time.time() > float(expires) + _EXPIRY_TOLERANCE_S:
                return None
        except (TypeError, ValueError):
            return None
    return d


def delete_pending_friendship(kid: str, *, pending_dir: Path) -> None:
    try:
        _pending_path(pending_dir, kid).unlink(missing_ok=True)
    except OSError:
        pass


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
        from corvin_console import feature_flags as _ff  # type: ignore[import-not-found]
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
    my_label: str | None, timeout_s: float,
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

    issued_at = int(time.time())
    req_body: dict[str, Any] = {
        "kid": kid,
        "issued_at": issued_at,
        "peer_url": my_url,
    }
    clean_label = sanitize_label(my_label) if my_label else ""
    if clean_label:
        req_body["peer_label"] = clean_label
    canonical = json.dumps(req_body, separators=(",", ":"), sort_keys=True)
    req_body["signature"] = _hmac.new(
        bytes.fromhex(hmac_key), canonical.encode("utf-8"), "sha256",
    ).hexdigest()

    ack_url = issuer_url.rstrip("/") + "/v1/a2a/friendship-ack"
    opener = _urlreq.build_opener(_AckNoRedirect())
    data = json.dumps(req_body).encode("utf-8")
    http_req = _urlreq.Request(
        ack_url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(http_req, timeout=timeout_s) as resp:
            raw = resp.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                return {"ok": False, "error": "response_too_large"}
            payload = json.loads(raw.decode("utf-8"))
    except _urlerr.HTTPError as exc:
        return {"ok": False, "error": f"http_{exc.code}"}
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
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "error": "invalid_response"}

    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_response"}
    sig = payload.get("signature")
    if not isinstance(sig, str):
        return {"ok": False, "error": "unsigned_response"}
    body_for_verify = {k: v for k, v in payload.items() if k != "signature"}
    expected = _hmac.new(
        bytes.fromhex(recv_key),
        json.dumps(body_for_verify, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        "sha256",
    ).hexdigest()
    if not _hmac.compare_digest(expected, sig):
        return {"ok": False, "error": "bad_response_signature"}

    return {
        "ok": bool(payload.get("ok")),
        "reachable": bool(payload.get("reachable", False)),
        "peer_instance_id": payload.get("instance_id"),
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
    if not token.url:
        return {"ok": False, "error": "no_issuer_url"}
    if not (my_url or "").strip():
        return {"ok": False, "error": "no_own_url"}

    hmac_key, recv_key = _derive_channel_keys(token.key)
    return _ack_round_trip(
        kid=token.kid, hmac_key=hmac_key, recv_key=recv_key,
        issuer_url=token.url, my_url=my_url, my_label=my_label,
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
    endpoint_path = Path(endpoints_dir) / f"{kid}.json"
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
    if not (isinstance(hmac_key, str) and isinstance(recv_key, str) and issuer_url):
        return {"ok": False, "error": "endpoint_config_incomplete"}

    my_url = get_my_url()
    if not my_url:
        return {"ok": False, "error": "no_own_url"}

    return _ack_round_trip(
        kid=kid, hmac_key=hmac_key, recv_key=recv_key,
        issuer_url=issuer_url, my_url=my_url, my_label=my_label,
        timeout_s=timeout_s,
    )


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
    enumerate valid pending kids.
    """
    if not isinstance(req, dict):
        return 400, {"reason": "envelope_not_object"}

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

    kid = kid[:128]
    peer_url = peer_url.strip().rstrip("/")[:_ACK_MAX_URL_LEN]

    pending = load_pending_friendship(kid, pending_dir=pending_dir)
    if pending is None:
        # Opaque — indistinguishable from a bad signature (anti-enumeration).
        return 403, {"reason": "ack_rejected"}

    # ADR-0094 a2a_peers_max — the issuer's own record for this kid is about
    # to be created by THIS handler for the first time (friendship_create
    # never checked this; it wrote nothing to disk). Skip the check for a
    # kid we already have a record of (a retry/reconnect ack must not be
    # blocked by a limit that was already satisfied when the record was
    # first created).
    if not (origins_dir / f"{kid}.json").exists():
        try:
            from license.validator import get_limit as _lic_get_limit  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001
            _lic_get_limit = None
        if _lic_get_limit is not None:
            try:
                _max = _lic_get_limit("a2a_peers_max")
            except Exception:  # noqa: BLE001
                _max = None
            if _max is not None:
                _limit = 1 if _max is True else (0 if _max is False else int(_max))
                _existing = sum(1 for _ in origins_dir.glob("*.json")) if origins_dir.exists() else 0
                if _existing >= _limit:
                    return 402, {"reason": "license_limit"}

    try:
        hmac_key, recv_key = _derive_channel_keys(str(pending["key"]))
    except (KeyError, TypeError, ValueError):
        return 403, {"reason": "ack_rejected"}

    canonical_dict: dict[str, Any] = {"kid": kid, "issued_at": issued_at, "peer_url": peer_url}
    if peer_label is not None:
        canonical_dict["peer_label"] = peer_label
    canonical = json.dumps(canonical_dict, separators=(",", ":"), sort_keys=True)
    expected_sig = _hmac.new(bytes.fromhex(hmac_key), canonical.encode("utf-8"), "sha256").hexdigest()
    if not _hmac.compare_digest(signature, expected_sig):
        return 403, {"reason": "ack_rejected"}

    # Freshness — authenticated callers only past this point, so a distinct
    # reason string is safe (mirrors process_ping_request's ordering).
    now = int(time.time())
    if abs(now - issued_at) > _ACK_FRESHNESS_S:
        return 400, {"reason": "stale_ack"}

    rejection = _ack_url_rejection_reason(peer_url)
    if rejection is not None:
        return 400, {"reason": rejection}

    label = (sanitize_label(peer_label) if peer_label else "") or pending.get("label") or None
    constraints = dict(pending.get("constraints") or {})
    reconstructed = FriendshipToken(
        kid=kid, key=str(pending["key"]), url=peer_url, label=label,
        expires=pending.get("expires"), constraints=constraints,
    )

    origin_path = origins_dir / f"{kid}.json"
    endpoint_path = endpoints_dir / f"{kid}.json"
    with config_file_lock(origins_dir, endpoints_dir):
        _atomic_write(origin_path, to_origin_dict(reconstructed))
        _atomic_write(endpoint_path, to_endpoint_dict(reconstructed))

    delete_pending_friendship(kid, pending_dir=pending_dir)

    # Reachability proof (ADR-0199) — url-presence is no longer sufficient
    # for EITHER side to claim a live connection; ping the redeemer back
    # before this side reports itself reachable.
    reachable = False
    try:
        from remote_trigger_sender import (  # type: ignore[import-not-found]
            RemoteEndpointRegistry as _RER, RemoteTriggerSender as _RTS,
        )
        _sender = _RTS(endpoints_dir, _RER(endpoints_dir))
        reachable = bool(_sender.ping(kid, timeout_s=5).reachable)
    except Exception:  # noqa: BLE001 — reachability check is best-effort
        reachable = False

    if not reachable:
        with config_file_lock(origins_dir, endpoints_dir):
            for p in (origin_path, endpoint_path):
                if not p.exists():
                    continue
                try:
                    cfg = json.loads(p.read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                cfg["state"] = "UNREACHABLE"
                _atomic_write(p, cfg)

    iid = ""
    try:
        from instance_identity import get_instance_id as _get_iid  # type: ignore[import-not-found]
        iid = _get_iid()
    except Exception:  # noqa: BLE001
        iid = ""

    response: dict[str, Any] = {
        "ok": True,
        "kid": kid,
        "reachable": reachable,
        "instance_id": iid,
    }
    resp_canonical = json.dumps(response, separators=(",", ":"), sort_keys=True)
    response["signature"] = _hmac.new(
        bytes.fromhex(recv_key), resp_canonical.encode("utf-8"), "sha256",
    ).hexdigest()
    return 200, response
