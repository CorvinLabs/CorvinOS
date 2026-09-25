"""Layer 38 — pairing binding keys (ADR-2064, 2026-09-25 round 4).

A friendship token is a BEARER secret: whoever holds it can derive the
pairing's HMAC keys. An instance id is not a secret either (every signed
ping response carries it). So "the bound peer" could be impersonated by
anyone who held the token — enough to re-point where our signed tasks go,
or to forge a revoke notice.

Binding therefore rests on something the token does not contain: each
instance has a long-term X25519 key pair. The public halves are exchanged
during the pairing handshake (in the HMAC-signed ack and the recv_key-signed
ack response), and a per-pairing binding secret is derived with ECDH +
HKDF(kid). Messages that change a pairing's routing or state — repeat acks,
revoke notices, reconnect pushes — must carry a MAC under that secret once
both public keys are known. Someone who finds the token later cannot derive
it (no private key), not even from recorded traffic.

The private key lives in ``<CORVIN_HOME>/global/remote_trigger/a2a_bind_key``
(0600). Losing it (CORVIN_HOME wiped) means re-pairing, exactly like losing
the pairing keys themselves.

CI lint: MUST NOT import the anthropic SDK.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import os
import threading
from pathlib import Path

_lock = threading.Lock()
_cached: dict[str, tuple[bytes, str]] = {}  # key path → (private raw, public hex)


def _key_path() -> Path:
    override = os.environ.get("CORVIN_A2A_BIND_KEY_PATH")
    if override:
        return Path(override)
    try:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location(
            "_paths_a2a_binding", Path(__file__).resolve().parent / "paths.py")
        mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        home = mod.corvin_home()
    except Exception:  # noqa: BLE001
        home = Path(os.environ.get("CORVIN_HOME") or Path.home() / ".corvin")
    return Path(home) / "global" / "remote_trigger" / "a2a_bind_key"


def _write_new_key(path: Path, raw: bytes, *, replace: bool) -> None:
    """Durable create: fsync the temp file BEFORE it becomes visible (a power
    loss right after creation left a zero-length key that broke every
    handshake — round 5). ``replace`` swaps out a corrupt key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        if replace:
            os.replace(tmp, path)
        else:
            try:
                os.link(tmp, path)  # first writer wins across processes
            except FileExistsError:
                pass
    finally:
        tmp.unlink(missing_ok=True)


def _load_or_create() -> tuple[bytes, str] | None:
    """Never raises: any failure means "no binding key" (legacy semantics)."""
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization as _ser
    except ImportError:
        return None
    path = _key_path()
    key = str(path)
    with _lock:
        if key in _cached:
            return _cached[key]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Cross-PROCESS lock (round 6): gateway, bridge adapter and MCP
            # server of one instance must end up with the SAME key even when
            # they start together over a missing or corrupt file.
            lock_fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                try:
                    import fcntl as _fcntl
                    _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
                except ImportError:  # pragma: no cover — non-POSIX: link race only
                    pass
                try:
                    data = path.read_bytes()
                except FileNotFoundError:
                    data = None
                if data is None or len(data) != 32:
                    fresh = X25519PrivateKey.generate().private_bytes(
                        _ser.Encoding.Raw, _ser.PrivateFormat.Raw, _ser.NoEncryption())
                    # Corrupt → replaced; the identity it held is lost either
                    # way, pairings re-bind via re-pair / pin reset.
                    _write_new_key(path, fresh, replace=data is not None)
                    data = path.read_bytes()
                    if len(data) != 32:
                        return None
            finally:
                os.close(lock_fd)
            priv = X25519PrivateKey.from_private_bytes(data)
            pub = priv.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw).hex()
        except Exception:  # noqa: BLE001 — unreadable dir, bad key material …
            return None
        _cached[key] = (data, pub)
        return _cached[key]


def local_bind_pub() -> str | None:
    """This instance's binding public key (64 hex chars), or None without
    the cryptography package."""
    kp = _load_or_create()
    return kp[1] if kp else None


def clean_pub(raw: object) -> str | None:
    if isinstance(raw, str) and len(raw) == 64:
        try:
            bytes.fromhex(raw)
            return raw.lower()
        except ValueError:
            return None
    return None


def _secret(peer_pub_hex: str, kid: str) -> bytes | None:
    kp = _load_or_create()
    peer = clean_pub(peer_pub_hex)
    if kp is None or peer is None:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import (
            X25519PrivateKey, X25519PublicKey,
        )
        shared = X25519PrivateKey.from_private_bytes(kp[0]).exchange(
            X25519PublicKey.from_public_bytes(bytes.fromhex(peer)))
    except Exception:  # noqa: BLE001 — invalid point etc.
        return None
    if not any(shared):  # low-order point → all-zero secret
        return None
    # HKDF-SHA256 (extract + one expand block), info bound to the pairing.
    prk = _hmac.new(b"corvin-a2a-bind-v1", shared, hashlib.sha256).digest()
    return _hmac.new(prk, b"corvin-a2a-bind|" + kid.encode("utf-8") + b"\x01",
                     hashlib.sha256).digest()


def bind_mac(peer_pub_hex: str, kid: str, message: bytes) -> str | None:
    secret = _secret(peer_pub_hex, kid)
    if secret is None:
        return None
    return _hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_bind_mac(peer_pub_hex: str, kid: str, message: bytes, mac: object) -> bool:
    if not isinstance(mac, str) or len(mac) != 64 or not mac.isascii():
        return False
    expected = bind_mac(peer_pub_hex, kid, message)
    return expected is not None and _hmac.compare_digest(expected, mac.lower())


__all__ = ["local_bind_pub", "clean_pub", "bind_mac", "verify_bind_mac"]
