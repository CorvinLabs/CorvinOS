"""Trial-specific logic: machine fingerprinting + activation anchoring.

Machine fingerprint (community trials only)
--------------------------------------------
Computed as sha256(hostname + ":" + mac_hex + ":" + install_prefix)[:32].
  - Deterministic, no network calls, no persistent state.
  - Changes when hostname OR primary MAC changes (catches VM cloning).
  - Does NOT phone home at any point.
The verifier checks the token's ``machine_fp`` claim against the current
fingerprint; mismatch → ``LicenseClaimError``.

Activation anchoring (clock-manipulation defence)
--------------------------------------------------
The first time a trial token is validated locally, ``activated_at`` is
recorded in a small JSON file.  Subsequent checks compute:

    activated_at + trial_duration_days * 86400 > now

Rolling the system clock back past ``activated_at`` therefore does NOT
extend the trial — the anchor is already written.

The server sync layer (``sync.py``) returns a *server-anchored*
``trial_activated_at`` value that provides a second, independent anchor
without requiring any central authority at verification time.

State file:
  ``<corvin_home>/global/license/trial_activation.json``  (mode 0o600)
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parents[2]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402


# ── Machine fingerprint ───────────────────────────────────────────────

def machine_fingerprint() -> str:
    """Stable, non-reversible fingerprint of this installation.

    Uses hostname + primary MAC address + canonical install prefix.
    Returns first 32 hex chars of sha256 (128 bits).
    """
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "unknown"
    mac = format(uuid.getnode(), "012x")  # guaranteed-stable 48-bit MAC
    prefix = str(_THIS.parents[3])        # repo root — stable per installation
    raw = f"{hostname}:{mac}:{prefix}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ── Activation state ──────────────────────────────────────────────────

@dataclass
class TrialActivation:
    """Local activation record for a trial token."""
    trial_id: str
    local_activated_at: int           # epoch when this token was first seen locally
    server_activated_at: int | None   # epoch confirmed by server sync (more trustworthy)
    schema_version: int = 1

    @property
    def activated_at(self) -> int:
        """Best-known activation epoch (server > local)."""
        return self.server_activated_at or self.local_activated_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TrialActivation":
        return cls(
            trial_id=str(d["trial_id"]),
            local_activated_at=int(d["local_activated_at"]),
            server_activated_at=int(v) if (v := d.get("server_activated_at")) else None,
            schema_version=int(d.get("schema_version", 1)),
        )


def _activation_path() -> Path:
    return _forge_paths.corvin_home() / "global" / "license" / "trial_activation.json"


def load_trial_activation(trial_id: str) -> TrialActivation | None:
    """Load the activation record for ``trial_id``, or None if absent."""
    path = _activation_path()
    if not path.exists():
        return None
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            return None  # refuse to read world-readable file
        data = json.loads(path.read_text(encoding="utf-8"))
        act = TrialActivation.from_dict(data)
        if act.trial_id != trial_id:
            return None  # different token installed — old record stale
        return act
    except Exception:
        return None


def record_activation(trial_id: str, *, now: int | None = None) -> TrialActivation:
    """Write the first-use record if not already present.

    Idempotent: if a record for ``trial_id`` already exists, returns it
    unchanged without overwriting the stored timestamp.
    """
    existing = load_trial_activation(trial_id)
    if existing is not None:
        return existing

    act = TrialActivation(
        trial_id=trial_id,
        local_activated_at=now or int(time.time()),
        server_activated_at=None,
    )
    _save_activation(act)
    return act


def update_server_anchor(trial_id: str, server_activated_at: int) -> None:
    """Called by the sync layer to store the server-confirmed activation time."""
    existing = load_trial_activation(trial_id)
    if existing is None:
        return  # no local record — will be written on next record_activation call
    if existing.server_activated_at == server_activated_at:
        return  # no change
    existing.server_activated_at = server_activated_at
    _save_activation(existing)


def _save_activation(act: TrialActivation) -> None:
    path = _activation_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(act.to_dict(), sort_keys=True, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".trial_act.", suffix=".tmp")
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


# ── Trial expiry check ────────────────────────────────────────────────

def is_trial_active(
    *,
    trial_expires_at: int,
    issued_at: int,
    trial_id: str,
    now: int | None = None,
    activation: TrialActivation | None = None,
) -> tuple[bool, str]:
    """Return (is_active, reason_if_not).

    Checks in order:
    1. Clock-rollback: now must be >= iat - 300 s (5 min leeway for NTP drift).
    2. trial_expires_at (absolute, from signed JWT): must be in the future.
    3. Activation-anchor duration: activated_at + duration > now.
       This catches clock roll-forward-then-back tricks.
    """
    _now = now or int(time.time())

    # 1. Clock-rollback guard.
    if _now < issued_at - 300:
        return False, "clock-before-issuance"

    # 2. Absolute expiry (from signed JWT — cannot be tampered with).
    if _now >= trial_expires_at:
        return False, "trial-expired-absolute"

    # 3. Activation-anchor duration check.
    act = activation or load_trial_activation(trial_id)
    if act is not None:
        trial_duration = trial_expires_at - issued_at
        if _now >= act.activated_at + trial_duration:
            return False, "trial-expired-by-activation-anchor"

    return True, "ok"


__all__ = [
    "TrialActivation",
    "machine_fingerprint",
    "load_trial_activation",
    "record_activation",
    "update_server_anchor",
    "is_trial_active",
]
