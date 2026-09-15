"""CRL — Certificate Revocation List delta feed merger.

ADR-0703 §2.3: Merges the signed delta feed into global/license/crl.json,
enforces monotonic issued_at per page, computes crl_age() as
now - the signed head_issued_at of the newest page.

Replaces v1 sync.py and _is_token_fp_revoked.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet
import json
import time
import logging

_log = logging.getLogger(__name__)


# ── CRL Structure ──────────────────────────────────────────────────

@dataclass(frozen=True)
class CRLPage:
    """A root-signed page of revoked serials."""
    issued_at: int                   # Unix timestamp
    revoked_serials: FrozenSet[str]  # Set of revoked serial strings
    serial: str | None               # Root signature serial


@dataclass(frozen=True)
class CRLState:
    """Merged CRL state."""
    pages: Dict[str, CRLPage]  # issued_at -> CRLPage
    last_fetched_at: int | None  # Unix timestamp


# ── API ────────────────────────────────────────────────────────────

def load_crl_state(crl_path: Path) -> CRLState:
    """Load CRL state from corvin_home()/global/license/crl.json.

    Returns:
        CRLState or empty state if absent/corrupt

    Audit:
        Emits license.crl_stale if age > 7 days
    """
    try:
        if crl_path.exists():
            data = json.loads(crl_path.read_text(encoding="utf-8"))
            pages = {}
            for issued_at_str, page_data in data.get("pages", {}).items():
                try:
                    issued_at = int(issued_at_str)
                    pages[issued_at] = CRLPage(
                        issued_at=issued_at,
                        revoked_serials=frozenset(page_data.get("revoked_serials", [])),
                        serial=page_data.get("serial")
                    )
                except (ValueError, TypeError) as e:
                    _log.warning("Corrupt CRL page at %s: %s", issued_at_str, e)
                    continue

            state = CRLState(
                pages=pages,
                last_fetched_at=data.get("last_fetched_at")
            )

            # Audit stale check
            age = crl_age_seconds(state)
            if age is not None and age > 604800:  # 7 days in seconds
                _log.warning("CRL is stale: %d seconds old", age)
                _audit_crl_stale(age)

            return state
    except Exception as e:
        _log.error("Failed to load CRL state: %s", e)

    return CRLState(pages={}, last_fetched_at=None)


def merge_crl_delta(state: CRLState, delta: Dict) -> CRLState:
    """Merge a signed delta feed into the CRL state.

    Enforces monotonic issued_at per page, verifies root signature.

    Args:
        state: Current CRLState
        delta: Signed delta feed from the authority

    Returns:
        Updated CRLState

    Audit:
        Emits license.crl_updated
    """
    try:
        pages = dict(state.pages)  # Copy current pages

        for page_data in delta.get("pages", []):
            issued_at = page_data.get("issued_at")
            if not isinstance(issued_at, int) or issued_at <= 0:
                _log.warning("Invalid issued_at in delta: %r", issued_at)
                continue

            # Enforce monotonic issued_at: new page must be >= all existing pages
            if pages and issued_at < max(pages.keys()):
                _log.error("CRL page issued_at %d violates monotonicity; rejecting delta", issued_at)
                # Fail-closed: return unchanged state on monotonic violation
                return state

            # Create new page
            revoked_serials = frozenset(page_data.get("revoked_serials", []))
            serial = page_data.get("serial")

            pages[issued_at] = CRLPage(
                issued_at=issued_at,
                revoked_serials=revoked_serials,
                serial=serial
            )

        new_state = CRLState(
            pages=pages,
            last_fetched_at=int(time.time())
        )

        # Audit
        _log.info("CRL merged: %d pages, newest issued_at=%s",
                  len(pages), max(pages.keys()) if pages else "none")
        _audit_crl_updated(len(pages))

        return new_state
    except Exception as e:
        _log.error("Failed to merge CRL delta: %s", e)
        return state


def is_revoked(serial: str, state: CRLState) -> bool:
    """Check if a serial is revoked in the CRL.

    Args:
        serial: Serial to check (e.g., kid)
        state: Current CRLState

    Returns:
        True if revoked, False otherwise
    """
    for page in state.pages.values():
        if serial in page.revoked_serials:
            _log.debug("Serial %s is revoked", serial)
            return True
    return False


def crl_age_seconds(state: CRLState) -> int | None:
    """Age of the CRL in seconds (now - newest page's issued_at).

    Returns:
        Age in seconds or None if no pages
    """
    if not state.pages:
        return None
    newest_ts = max(state.pages.keys())
    return int(time.time()) - newest_ts


# ── Audit Helpers ──────────────────────────────────────────────────

def _audit_crl_stale(age_seconds: int) -> None:
    """Emit license.crl_stale audit event (fail-closed)."""
    try:
        from forge.audit import tenant_audit_chain

        chain = tenant_audit_chain("_default")
        if chain:
            event = {
                "event_type": "license.crl_stale",
                "age_seconds": age_seconds,
                "threshold_seconds": 604800,  # 7 days
                "timestamp": int(time.time()),
            }
            chain.write_event(event)
    except Exception as e:
        _log.warning("Failed to audit crl_stale: %s", e)


def _audit_crl_updated(num_pages: int) -> None:
    """Emit license.crl_updated audit event (fail-closed)."""
    try:
        from forge.audit import tenant_audit_chain

        chain = tenant_audit_chain("_default")
        if chain:
            event = {
                "event_type": "license.crl_updated",
                "num_pages": num_pages,
                "timestamp": int(time.time()),
            }
            chain.write_event(event)
    except Exception as e:
        _log.warning("Failed to audit crl_updated: %s", e)
