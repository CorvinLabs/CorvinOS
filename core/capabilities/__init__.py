"""
Deny-by-Default Capability Registry — ADR-0302

Central, immutable registry of capabilities per (actor, tenant).
Every capability claim is **explicitly denied by default**.
GDPR Art. 6 + EU AI Act Art. 5 enforcement.
"""

from core.capabilities.registry import (
    CapabilityRegistry,
    CapabilityGrantError,
    CapabilityDeniedError,
)

__all__ = [
    "CapabilityRegistry",
    "CapabilityGrantError",
    "CapabilityDeniedError",
]
