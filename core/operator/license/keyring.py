"""Single embedded ring (public keys, kids, revoked_kids, generation).

ADR-0703 §2.3: The keyring is the module constant RING imported by validator,
instance_identity, the receiver and trust.py.

Load-bearing invariant: No second ring literal or trust-anchor file in the
product tree (pinning test enforces this).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet

# ── Ring Structure ─────────────────────────────────────────────────

@dataclass(frozen=True)
class RingKey:
    """Public key entry in the ring."""
    kid: str              # key identifier (e.g., "lic-v2", "mkt-v1")
    algorithm: str        # "Ed25519" or "RSA-4096" (legacy)
    public_key: str       # PEM-encoded public key
    expires_at: int | None  # Unix timestamp or None


@dataclass(frozen=True)
class Ring:
    """Embedded ring of public keys (root-signed, serial-checked)."""
    generation: int          # Generation floor (monotonic)
    kids: Dict[str, RingKey]  # kid -> RingKey
    revoked_kids: FrozenSet[str]  # Revoked kid set
    serial: int | None       # Root signature serial (None in embedded)


# ── Embedded Ring ──────────────────────────────────────────────────
# Phase 0 key generation: root-v1 (Ed25519), lic-v2 (Ed25519), ibc-v2 (RSA), mkt-v1 (Ed25519)
# These are test placeholder keys; Phase 1.2 will rotate to live keys from Corvin-Keys.
RING = Ring(
    generation=1,
    kids={
        "root-v1": RingKey(
            kid="root-v1",
            algorithm="Ed25519",
            public_key=(
                "-----BEGIN PUBLIC KEY-----\n"
                "MCowBQYDK2VwAyEA+DYemS4SEam7VJKSHhSvq/Ax5D+DdsPEY3duNEUX+gg=\n"
                "-----END PUBLIC KEY-----"
            ),
            expires_at=None  # Root key never expires
        ),
        "lic-v2": RingKey(
            kid="lic-v2",
            algorithm="Ed25519",
            public_key=(
                "-----BEGIN PUBLIC KEY-----\n"
                "MCowBQYDK2VwAyEApGEaQVNMJ4EeFqeEXx4VqxB2z/2B7v8N2j3nTmH5C3I=\n"
                "-----END PUBLIC KEY-----"
            ),
            expires_at=None
        ),
        "ibc-v2": RingKey(
            kid="ibc-v2",
            algorithm="RSA-4096",
            public_key=(
                "-----BEGIN PUBLIC KEY-----\n"
                "MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAycTguJlqKhUTmKg8\n"
                "7vZkJl5Lq0Z8e4mZ9WqPvJhG/d3N+z8kL2QrH3PkTq5R9Ql8LdM9Z7v2c8Op\n"
                "KQZYYQhQ7Uj1YL3d7KQzU1OQ0K+L5b8E9R8L7MzC3Q5U8L7KzC3Q5U8L7KzC\n"
                "3Q5U8L7KzC3Q5U8L7KzC3Q5U8L7KzC3Q5U8L7KzC3Q5U8L7KzC3Q5U8L7Kz\n"
                "C3Q5U8L7KzC3Q5U8L7KzC3Q5U8L7KzC3Q5U8L7KzC3Q5U8L7KzC3Q5U8L7K\n"
                "-----END PUBLIC KEY-----"
            ),
            expires_at=None
        ),
        "mkt-v1": RingKey(
            kid="mkt-v1",
            algorithm="Ed25519",
            public_key=(
                "-----BEGIN PUBLIC KEY-----\n"
                "MCowBQYDK2VwAyEA7Xt8e6A8mQ3K9L2Y4P5H6Q7R8S9T0U1V2W3X4Y5Z6a=\n"
                "-----END PUBLIC KEY-----"
            ),
            expires_at=None
        ),
    },
    revoked_kids=frozenset(),
    serial=None,  # Embedded ring is not signed; only authority server has serial
)
