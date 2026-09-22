"""Compliance Layer — GDPR + EU AI Act enforcement (load-bearing)."""

from .consent import consent_required, ConsentError, CONSENT_SCOPES

__all__ = [
    "consent_required",
    "ConsentError",
    "CONSENT_SCOPES",
]
