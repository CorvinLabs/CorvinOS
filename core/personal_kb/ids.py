"""ULID generation and validation (Crockford base32, 26 characters)."""
from __future__ import annotations

import os
import re
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def new_ulid(now_ms: int | None = None) -> str:
    """48-bit millisecond timestamp + 80 random bits."""
    ts = int(time.time() * 1000) if now_ms is None else int(now_ms)
    value = (ts << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def is_ulid(value: object) -> bool:
    return isinstance(value, str) and bool(ULID_RE.fullmatch(value))


def new_oid() -> str:
    """A random 128-bit object id (hex). Never derived from content or from the uid."""
    return os.urandom(16).hex()


OID_RE = re.compile(r"^[0-9a-f]{32}$")
