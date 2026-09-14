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
    # TODO: Phase 1.2 — parse JSON, emit stale check
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
    # TODO: Phase 1.2 — verify signature, merge pages
    return state


def is_revoked(serial: str, state: CRLState) -> bool:
    """Check if a serial is revoked in the CRL.

    Args:
        serial: Serial to check (e.g., kid)
        state: Current CRLState

    Returns:
        True if revoked, False otherwise
    """
    # TODO: Phase 1.2 — lookup serial across all pages
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
