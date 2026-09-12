"""Geo Privacy Validator (ADR-0683): Fail-closed filtering of geo attributes."""

from .validator import GeoPrivacyValidator, PrivacyViolationError

__all__ = ["GeoPrivacyValidator", "PrivacyViolationError"]
