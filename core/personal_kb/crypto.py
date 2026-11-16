"""Cryptographic primitives of the personal knowledge base (ADR-2325 item 2, ADR-2326 item 2).

* AES-256-GCM per object, fresh random 96-bit nonce per write, length-prefixed associated data.
* Data key (DEK) wrapped by a key-encryption key: scrypt over a passphrase, a device key, or a
  recovery key. Wrap parameters (salt, scrypt cost) are stored with the wrap, so they can be raised.
* A decryption failure raises :class:`DecryptError` and never names content; there is no
  plaintext fallback.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import re
import struct
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"PKB1"
FORMAT_VERSION = 1
KEY_BYTES = 32
NONCE_BYTES = 12
_HEADER = struct.Struct(">4sBI")  # magic, format version, key epoch

# Production cost: N=2^17, r=8, p=1 (about 128 MiB per derivation). Stored per wrap, so a test
# may lower the module default without weakening a wrap written by production code.
KDF_PARAMS: dict[str, int] = {"n": 2 ** 17, "r": 8, "p": 1}
KDF_MAX_N = 2 ** 20


class DecryptError(Exception):
    """Authentication failed: wrong key, altered ciphertext, or altered associated data."""


def new_key() -> bytes:
    return os.urandom(KEY_BYTES)


def aad(*fields: Any) -> bytes:
    """Length-prefixed associated data; no two field lists produce the same bytes."""
    out = bytearray()
    for f in fields:
        b = str(f).encode("utf-8")
        out += struct.pack(">I", len(b)) + b
    return bytes(out)


def seal(key: bytes, plaintext: bytes, *, aad_bytes: bytes, epoch: int) -> bytes:
    """The header (magic, format, epoch) is authenticated too: it is part of the associated data."""
    nonce = os.urandom(NONCE_BYTES)
    header = _HEADER.pack(MAGIC, FORMAT_VERSION, int(epoch))
    ct = AESGCM(key).encrypt(nonce, plaintext, header + aad_bytes)
    return header + nonce + ct


def blob_epoch(blob: bytes) -> int:
    if len(blob) < _HEADER.size + NONCE_BYTES + 16:
        raise DecryptError("truncated")
    magic, ver, epoch = _HEADER.unpack_from(blob, 0)
    if magic != MAGIC or ver != FORMAT_VERSION:
        raise DecryptError("unknown format")
    return int(epoch)


def open_(key: bytes, blob: bytes, *, aad_bytes: bytes) -> bytes:
    blob_epoch(blob)
    header = blob[:_HEADER.size]
    nonce = blob[_HEADER.size:_HEADER.size + NONCE_BYTES]
    ct = blob[_HEADER.size + NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, ct, header + aad_bytes)
    except (InvalidTag, ValueError) as exc:
        raise DecryptError("authentication failed") from exc


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"), validate=True)


def kek_from_passphrase(passphrase: str, kdf: dict[str, Any]) -> bytes:
    n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
    if not (2 <= n <= KDF_MAX_N and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 16):
        raise DecryptError("unsupported key-derivation parameters")
    salt = _unb64(kdf["salt"])
    return Scrypt(salt=salt, length=KEY_BYTES, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))


def new_kdf() -> dict[str, Any]:
    return {"alg": "scrypt", "salt": _b64(os.urandom(16)), **KDF_PARAMS}


def wrap_aad(owner_id: str, epoch: int, kind: str) -> bytes:
    return aad("wrap", owner_id, epoch, kind)


def wrap(dek: bytes, kek: bytes, *, owner_id: str, epoch: int, kind: str) -> dict[str, str]:
    nonce = os.urandom(NONCE_BYTES)
    ct = AESGCM(kek).encrypt(nonce, dek, wrap_aad(owner_id, epoch, kind))
    return {"nonce": _b64(nonce), "ct": _b64(ct)}


def unwrap(w: dict[str, Any], kek: bytes, *, owner_id: str, epoch: int, kind: str) -> bytes:
    try:
        return AESGCM(kek).decrypt(_unb64(w["nonce"]), _unb64(w["ct"]), wrap_aad(owner_id, epoch, kind))
    except (InvalidTag, KeyError, ValueError, TypeError) as exc:
        raise DecryptError("unwrap failed") from exc


def hkdf(key: bytes, info: str, length: int = KEY_BYTES) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info.encode()).derive(key)


def hmac_hex(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


# ---- recovery code ---------------------------------------------------------------------------

def new_recovery_code() -> tuple[str, bytes]:
    """(display code, 32-byte key). The code is base32 in groups of 5, shown once."""
    raw = os.urandom(KEY_BYTES)
    text = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(text[i:i + 5] for i in range(0, len(text), 5)), raw


def recovery_key_from_code(code: str) -> bytes:
    text = re.sub(r"[\s-]", "", str(code or "")).upper()
    pad = "=" * (-len(text) % 8)
    try:
        raw = base64.b32decode(text + pad)
    except (ValueError, TypeError) as exc:
        raise DecryptError("malformed recovery code") from exc
    if len(raw) != KEY_BYTES:
        raise DecryptError("malformed recovery code")
    return raw


# ---- passphrase policy (ADR-2326 item 1) -----------------------------------------------------

_COMMON = frozenset("""
password passwort 123456789012 qwertzuiop12 qwertyuiop12 iloveyou1234 letmein12345 welcome12345
passwort1234 password1234 hallowelt123 administrator correcthorsebatterystaple 000000000000
111111111111 123123123123 abcdefghijkl abc123abc123 monkeymonkey sommer2024!! winter2024!!
""".split())
MIN_PASSPHRASE = 12
MIN_ENTROPY_BITS = 50.0


def passphrase_problem(pw: str) -> str | None:
    """None when acceptable, else a short reason. Length, common list, rough entropy estimate."""
    pw = str(pw or "")
    if len(pw) < MIN_PASSPHRASE:
        return f"use at least {MIN_PASSPHRASE} characters"
    if pw.lower() in _COMMON or len(set(pw)) <= 3 or re.fullmatch(r"(.{1,%d}?)\1+" % (len(pw) // 2), pw):
        return "this passphrase is too common"
    pool = 0
    if re.search(r"[a-z]", pw):
        pool += 26
    if re.search(r"[A-Z]", pw):
        pool += 26
    if re.search(r"[0-9]", pw):
        pool += 10
    if re.search(r"[^A-Za-z0-9]", pw):
        pool += 33
    # Repetition penalty: count runs and repeated characters only once.
    effective = len(re.sub(r"(.)\1+", r"\1", pw))
    bits = effective * math.log2(max(pool, 2))
    if bits < MIN_ENTROPY_BITS:
        return "this passphrase is too easy to guess; make it longer or more varied"
    return None
