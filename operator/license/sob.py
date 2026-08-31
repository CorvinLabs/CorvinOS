"""Sealed Offline Bundle client — load, validate, refresh (ADR-0111).

Typical usage (call once at adapter boot)::

    from operator.license.sob import SobClient
    from operator.license.capability import Capability

    sob = SobClient(corvin_home)
    sob.load()                          # reads sob.enc from disk, unseals
    cap = Capability(sob)               # wraps the client

    cfg = cap.get_feature_config("data_residency")   # dict or None
    limit = cap.get_limit("tenants_max")             # numeric / None / bool
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("corvin.license.sob")

_SOB_FILENAME = "sob.enc"
_SUB_KEY_FILENAME = "sub_private.key"


class SobClient:
    """Client-side lifecycle for a Sealed Offline Bundle.

    Responsibilities:
      - Discover corvin_home and locate ``sob.enc`` + ``sub_private.key``.
      - Compute the local instance_id and device_fp.
      - Call ``seal_loader.unseal()`` to decrypt and validate.
      - Cache the resulting claims for the session lifetime.
      - Support explicit ``reload()`` after a refresh.

    This class never fetches from the network.  Use ``corvin-refresh``
    (or the refresh REST endpoint) to update ``sob.enc`` on disk.
    """

    def __init__(self, corvin_home: Path | None = None) -> None:
        self._home = corvin_home or _default_corvin_home()
        self._license_dir = self._home / "global" / "license"
        self._sob_path = self._license_dir / _SOB_FILENAME
        self._sub_key_path = self._license_dir / _SUB_KEY_FILENAME
        self._claims: dict[str, Any] | None = None
        self._loaded_at: float = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> bool:
        """Read and unseal the SOB from disk.  Returns True on success.

        Sets the internal claims cache used by ``get_claims()``.
        Safe to call multiple times — re-reads from disk each time.
        Emits an audit event on success or failure.
        """
        claims = self._unseal()
        if claims is None:
            log.info("sob: no valid SOB — Free tier active")
            self._claims = None
            _audit("license.sob_absent")
            return False
        self._claims = claims
        self._loaded_at = time.time()
        log.info(
            "sob: loaded tier=%s valid_until=%s",
            claims.get("tier", "?"),
            claims.get("valid_until", "?"),
        )
        _audit(
            "license.sob_loaded",
            tier=claims.get("tier", ""),
            nonce_epoch=claims.get("nonce_epoch", 0),
        )
        return True

    def reload(self) -> bool:
        """Re-read and re-unseal. Call after ``corvin-refresh`` updates ``sob.enc``."""
        self._claims = None
        return self.load()

    def get_claims(self) -> dict[str, Any] | None:
        """Return the unsealed claims or None when no valid SOB is present."""
        return self._claims

    def is_loaded(self) -> bool:
        return self._claims is not None

    def active_tier(self) -> str:
        # Canonicalize (review R5 #10): a legacy tier like "universal" in a SOB
        # must surface as "member" to operators/audit, never the raw legacy name.
        from license.validator import canonical_tier
        return canonical_tier((self._claims or {}).get("tier", "free"))

    def is_registered(self) -> bool:
        """True when both sob.enc and sub_private.key exist on disk."""
        return self._sob_path.exists() and self._sub_key_path.exists()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _unseal(self) -> dict[str, Any] | None:
        # File existence + mode checks
        if not self._sob_path.exists() or not self._sub_key_path.exists():
            return None
        for p in (self._sob_path, self._sub_key_path):
            try:
                mode = p.stat().st_mode & 0o777
                if mode & 0o077:
                    log.warning("sob: %s has too-permissive mode 0o%o — skipping", p.name, mode)
                    _audit("license.sob_mode_error", file=p.name)
                    return None
            except OSError:
                return None

        try:
            sob_bytes = self._sob_path.read_bytes()
        except OSError:
            return None

        iid = _instance_id(self._home)
        dfp = _device_fp()

        try:
            from .seal_loader import unseal
            epoch = _current_nonce_epoch(self._home)
            try:
                return unseal(iid, dfp, sob_bytes, epoch)
            except Exception:
                # FND-LIC-07 migration: a SOB sealed with a pre-unification
                # device_fp formula will not unseal under the new dfp. Try the
                # known legacy formula(s) so existing paid installs do not
                # silently degrade to Free; on success, warn that a re-seal is
                # due (next registration re-seals with the unified formula).
                for legacy in _legacy_device_fps():
                    if legacy == dfp:
                        continue
                    try:
                        claims = unseal(iid, legacy, sob_bytes, epoch)
                        log.warning("sob: unsealed with a LEGACY device_fp — "
                                    "re-register to re-seal with the current formula")
                        return claims
                    except Exception:
                        continue
                raise
        except Exception as exc:  # noqa: BLE001
            log.warning("sob: unseal failed (%s)", exc)
            return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _default_corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME", "").strip()
    return Path(env) if env else Path.home() / ".corvin"


def _instance_id(corvin_home: Path) -> str:
    """Return the local installation UUID4 (creates it if absent)."""
    iid_path = corvin_home / "global" / "instance_id.json"
    try:
        if iid_path.exists():
            data = json.loads(iid_path.read_text())
            iid = str(data.get("instance_id", ""))
            if iid:
                return iid
    except Exception:  # noqa: BLE001
        pass
    # Generate a new one
    import uuid
    new_id = str(uuid.uuid4())
    try:
        iid_path.parent.mkdir(parents=True, exist_ok=True)
        # ADR-0138 M3 D1: atomic O_CREAT|O_EXCL so mode 0o600 is set at creation
        # time — no TOCTOU window between write and chmod.
        _fd = os.open(str(iid_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
            json.dump({"instance_id": new_id}, _fh)
    except FileExistsError:
        # Race: another process created the file between our exists() check and
        # the O_EXCL open.  Re-read the winner's value.
        try:
            _data = json.loads(iid_path.read_text())
            _iid = str(_data.get("instance_id", ""))
            if _iid:
                return _iid
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    return new_id


def _legacy_device_fps() -> list[str]:
    """Pre-FND-LIC-07 device_fp formulas, tried as unseal fallbacks so a SOB
    sealed before the formula was unified still opens (the install re-seals with
    the current formula on next registration). Best-effort; order doesn't matter."""
    import hashlib
    import socket
    import uuid
    try:
        hostname = socket.gethostname()
    except Exception:  # noqa: BLE001
        hostname = "unknown"
    mac = format(uuid.getnode(), "012x")
    out: list[str] = []
    try:
        # The pre-unification formula: single sha256 over "{hostname}:{mac}".
        out.append(hashlib.sha256(f"{hostname}:{mac}".encode()).hexdigest()[:32])
    except Exception:  # noqa: BLE001
        pass
    return out


def _device_fp() -> str:
    """Compute a stable 32-char hex device fingerprint.

    Delegates to device_fp.compute_device_fp() — single source of truth
    shared with session_refresh.py and validator.py (FND-LIC-07 fix).
    """
    import sys as _sys
    _lic_dir = str(Path(__file__).resolve().parent)
    if _lic_dir not in _sys.path:
        _sys.path.insert(0, _lic_dir)
    from device_fp import compute_device_fp as _cfp  # type: ignore
    return _cfp()


def _current_nonce_epoch(corvin_home: Path) -> int:
    """Return the nonce epoch from the cached GitHub manifest, or 0 when absent.

    R2-08: a stale manifest must NOT reset the epoch to 0. A stale cached epoch
    can only LAG the true epoch (failing toward MORE rejection at worst), never
    legitimately drop to 0, so keep enforcing the last-known epoch regardless of
    staleness.

    ADR-0138 M3 D3: when the manifest cache is absent, fall back to the
    persisted instance_epoch.json so deleting the manifest cache cannot reset
    replay-epoch enforcement to 0.
    """
    # R3-confirm: FLOOR the manifest epoch against the monotonic persisted
    # instance_epoch.json. The signature check on load_cached_manifest stops a
    # FORGED lower epoch, but not a cache SUBSTITUTION with an older, genuinely
    # signed manifest (lower nonce_epoch) — which would DOWNGRADE replay-epoch
    # enforcement and re-admit SOBs sealed before the last rotation. Taking the
    # max() with the persisted high-water mark makes the floor un-lowerable.
    floor = 0
    try:
        from .instance_epoch import read_instance_epoch
        floor = max(0, int(read_instance_epoch(corvin_home)))
    except Exception:  # noqa: BLE001
        floor = 0
    epoch_from_manifest = 0
    try:
        from .manifest import load_cached_manifest
        m = load_cached_manifest(corvin_home)
        if m:
            epoch_from_manifest = int(m.get("nonce_epoch", 0))
    except Exception:  # noqa: BLE001
        epoch_from_manifest = 0
    return max(epoch_from_manifest, floor)


def _audit(event_type: str, **details: Any) -> None:
    """Best-effort audit emit. Never raises."""
    try:
        import sys
        from pathlib import Path as _Path
        shared = _Path(__file__).resolve().parents[1] / "bridges" / "shared"
        if str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        from audit import audit_event  # type: ignore[import]
        audit_event(event_type, **details)
    except Exception:  # noqa: BLE001
        pass
