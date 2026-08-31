"""Customer-self-service license-download portal.

ADR-0019 §Phase 1 delivery: two parallel channels — email (Mailgun,
operator-side) AND portal (this module). The portal is the
canonical source-of-truth; email is convenience.

Phase 1 minimum:
  * Single bearer token from env (``CORVIN_LICENSE_PORTAL_BEARER``).
  * Endpoint returns the installed license.jwt verbatim when bearer
    matches and a license is installed.
  * Audit event ``license.portal_served`` per successful download,
    ``license.portal_denied`` per refusal.

Phase 2+ multi-customer portal lives in the corvin-cloud repo as
a separate FastAPI router that maps Stripe-customer bearer tokens
to per-customer license blobs in the outbound queue.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path


def _bearer_from_env() -> str | None:
    """Return the configured portal bearer or None when not configured.

    Without configuration the portal endpoint refuses every request
    with ``reason="portal-disabled"`` — the operator hasn't opted in.
    """
    raw = os.environ.get("CORVIN_LICENSE_PORTAL_BEARER", "").strip()
    if not raw:
        return None
    if len(raw) < 16:
        # Reject suspiciously-short bearer secrets. The env var is
        # supposed to be at least 16 chars / 64 bits of entropy.
        return None
    return raw


def portal_enabled() -> bool:
    """True iff a bearer is configured. UI / tests can probe this."""
    return _bearer_from_env() is not None


def bearer_fingerprint(value: str) -> str:
    """First 12 chars of sha256. Matches the customer-id convention."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def check_bearer(presented: str) -> bool:
    """Constant-time compare against the configured bearer.

    Returns False when:
    - portal is disabled (no env var configured)
    - bearer is None / empty
    - bearer length doesn't match (so compare_digest doesn't leak length)
    - constant-time compare fails
    """
    configured = _bearer_from_env()
    if configured is None:
        return False
    if not presented:
        return False
    # Constant-time compare requires equal-length inputs. compare_digest
    # tolerates unequal lengths but the timing of the bool conversion
    # could leak — be explicit.
    a = configured.encode("utf-8")
    b = presented.encode("utf-8")
    if len(a) != len(b):
        # Still call compare_digest to keep wall-clock comparable.
        hmac.compare_digest(a, a)
        return False
    return hmac.compare_digest(a, b)


def read_installed_license_bytes(license_file: Path) -> str:
    """Read the installed JWT verbatim, or raise FileNotFoundError."""
    return license_file.read_text(encoding="utf-8").strip()


__all__ = [
    "portal_enabled",
    "bearer_fingerprint",
    "check_bearer",
    "read_installed_license_bytes",
]
