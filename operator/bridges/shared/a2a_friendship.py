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
    """
    now = time.time()
    # Remove entries older than 1 day
    stale = [k for k, ts in _endpoint_heartbeat_cache.items()
             if (now - ts) > _HEARTBEAT_CACHE_MAX_AGE_S]
    for k in stale:
        del _endpoint_heartbeat_cache[k]

    # If still over limit, evict oldest 10%
    if len(_endpoint_heartbeat_cache) > _HEARTBEAT_CACHE_MAX_ENTRIES:
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
