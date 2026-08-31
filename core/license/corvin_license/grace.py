"""Grace-period state machine.

When a previously-valid license expires, the gate does NOT slam shut
immediately. The operator gets a documented 30-day grace period to
renew. During grace:
  - GET endpoints stay open
  - Mutations on premium routes refuse with 402-Payment-Required
  - A `license.grace_started` audit event fires once per grace entry

Past grace, the gate goes fully closed for premium routes — but
**never** for the Apache-core. The open-source community always
keeps every load-bearing feature.

State file:
  ``<corvin_home>/global/license/state.json``  (mode 0o600)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parents[2]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402


GRACE_PERIOD_SECONDS = 30 * 24 * 3600  # 30 days


class GraceStateMalformed(Exception):
    """state.json present but unreadable."""


@dataclass
class GraceState:
    """Persisted grace-period state."""
    # Last seen valid expiry; when this moment passes, grace timer starts.
    last_known_valid_until: int | None = None
    # Timestamp when we first observed the token as expired.
    expired_observed_at: int | None = None
    # Customer ID fingerprint of the token that expired (for audit cross-ref).
    expired_customer_fingerprint: str | None = None
    # Version of state-file schema (for future migrations).
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GraceState":
        return cls(
            last_known_valid_until=d.get("last_known_valid_until"),
            expired_observed_at=d.get("expired_observed_at"),
            expired_customer_fingerprint=d.get("expired_customer_fingerprint"),
            schema_version=int(d.get("schema_version", 1)),
        )


# ── Disk I/O ──────────────────────────────────────────────────────────

def _state_path() -> Path:
    home = _forge_paths.corvin_home()
    return home / "global" / "license" / "state.json"


def _load_raw() -> dict[str, Any] | None:
    path = _state_path()
    if not path.exists():
        return None
    # Mode check
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise GraceStateMalformed(
            f"state-file-mode-too-permissive: 0o{mode:o} (expected 0o600)"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise GraceStateMalformed(f"state-file-malformed: {exc}") from exc


def load_state() -> GraceState:
    """Read state.json; return empty defaults when absent."""
    raw = _load_raw()
    if raw is None:
        return GraceState()
    return GraceState.from_dict(raw)


def save_state(state: GraceState) -> None:
    """Atomic-write state.json, mode 0o600."""
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(state.to_dict(), sort_keys=True, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Grace-period semantics ────────────────────────────────────────────

@dataclass(frozen=True)
class GraceStatus:
    """Public-facing grace status snapshot."""
    in_grace: bool
    grace_started_at: int | None
    grace_ends_at: int | None
    seconds_remaining: int | None
    state: str  # "active", "in-grace", "expired", "no-license"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess(
    *,
    valid_until: int | None,
    now: int | None = None,
    persisted: GraceState | None = None,
) -> GraceStatus:
    """Compute the grace status given a token's valid_until + persisted state.

    Arguments:
      valid_until: unix epoch of the token's exp claim, OR None when no
                   valid license is present. None means "no-license" which
                   may resolve to in-grace (if state remembers an expiry)
                   or expired (if grace also elapsed).
      now: epoch override for tests.
      persisted: previously-loaded GraceState; if None, loaded fresh.

    Outcomes:
      - Active license (valid_until > now) → state="active", in_grace=False
      - Expired token (valid_until <= now), within grace → in_grace=True
      - Expired token past grace → state="expired"
      - No license file, no remembered expiry → state="no-license"
      - No license file BUT remembered expiry within grace → in-grace
    """
    now = now or int(time.time())
    persisted = persisted if persisted is not None else load_state()

    # Active: token is still valid right now.
    if valid_until is not None and valid_until > now:
        return GraceStatus(
            in_grace=False,
            grace_started_at=None,
            grace_ends_at=None,
            seconds_remaining=None,
            state="active",
        )

    # We may be expired or licenseless. The grace-period anchor is
    # whichever expiry is most recent: the token's expiry (if any) or
    # the persisted last_known_valid_until.
    anchors: list[int] = []
    if valid_until is not None:
        anchors.append(valid_until)
    if persisted.last_known_valid_until is not None:
        anchors.append(persisted.last_known_valid_until)
    if not anchors:
        return GraceStatus(
            in_grace=False,
            grace_started_at=None,
            grace_ends_at=None,
            seconds_remaining=None,
            state="no-license",
        )

    grace_starts = max(anchors)  # Most recent expiry
    grace_ends = grace_starts + GRACE_PERIOD_SECONDS
    if now < grace_ends:
        return GraceStatus(
            in_grace=True,
            grace_started_at=grace_starts,
            grace_ends_at=grace_ends,
            seconds_remaining=grace_ends - now,
            state="in-grace",
        )
    return GraceStatus(
        in_grace=False,
        grace_started_at=grace_starts,
        grace_ends_at=grace_ends,
        seconds_remaining=0,
        state="expired",
    )


def remember_valid_license(
    *,
    valid_until: int,
    customer_fingerprint: str,
) -> None:
    """Persist that we've seen a valid license — anchors the grace timer
    in case the next read fails to find license.jwt."""
    state = load_state()
    if state.last_known_valid_until is None or valid_until > state.last_known_valid_until:
        state.last_known_valid_until = valid_until
        state.expired_observed_at = None  # reset on renewal
        state.expired_customer_fingerprint = customer_fingerprint
        save_state(state)


def mark_observed_expired(
    *,
    now: int | None = None,
    customer_fingerprint: str | None = None,
) -> bool:
    """Stamp the "first time we noticed the expiry" timestamp.

    Returns True if this call was the first observation (caller should
    emit a `license.grace_started` audit event), False if grace was
    already in progress.
    """
    state = load_state()
    if state.expired_observed_at is not None:
        return False
    state.expired_observed_at = now or int(time.time())
    if customer_fingerprint is not None:
        state.expired_customer_fingerprint = customer_fingerprint
    save_state(state)
    return True


def reset_state() -> None:
    """Remove persisted state. Used by CLI revoke / re-install flows."""
    path = _state_path()
    if path.exists():
        path.unlink()


__all__ = [
    "GRACE_PERIOD_SECONDS",
    "GraceState",
    "GraceStateMalformed",
    "GraceStatus",
    "assess",
    "load_state",
    "save_state",
    "remember_valid_license",
    "mark_observed_expired",
    "reset_state",
]
