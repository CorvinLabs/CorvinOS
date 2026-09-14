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
# TODO: Phase 1.2 — populate from Phase 0 key generation
# Stub: minimal ring for transition
RING = Ring(
    generation=0,
    kids={
        "root-v1": RingKey(
            kid="root-v1",
            algorithm="Ed25519",
            public_key="",  # TODO: placeholder
            expires_at=None
        ),
    },
    revoked_kids=frozenset(),
    serial=None,
)
