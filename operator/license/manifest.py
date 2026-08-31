"""GitHub trust manifest — fetch, verify, cache (ADR-0111).

The manifest lives at ``corvinlabs/corvin-trust`` on GitHub and is signed
daily by a CorvinLabs GitHub Actions workflow.  It carries:

  * SHA-256 hashes of every ``corvin_seal`` binary release.
  * The active ``nonce_epoch`` that SOBs must satisfy.
  * Revocation lists for compromised instance IDs and SOB fingerprints.

Staleness policy (mirrors ADR-0103 for consistency):
  < 3 days  → normal
  3–7 days  → ``license.manifest_stale`` WARNING
  > 7 days  → revocation list treated as empty; nonce_epoch = 0 (fail-open)
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("corvin.license.manifest")

_MANIFEST_URL = (
    "https://raw.githubusercontent.com/corvinlabs/corvin-trust/main/manifests/latest.json"
)
_MANIFEST_SIG_URL = (
    "https://raw.githubusercontent.com/corvinlabs/corvin-trust/main/manifests/latest.json.sig"
)
_CACHE_FILENAME = "license_manifest.json"
_SIG_FILENAME   = "license_manifest.json.sig"  # FND-04: cached signature, verified on read
_CACHE_TTL_WARN = 3 * 86400    # 3 days → warn
_CACHE_TTL_MAX  = 7 * 86400    # 7 days → fail-open for revocation


def _default_corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME", "").strip()
    return Path(env) if env else Path.home() / ".corvin"


def _cache_path(corvin_home: Path | None = None) -> Path:
    home = corvin_home or _default_corvin_home()
    return home / "global" / "license" / _CACHE_FILENAME


def _sig_path(corvin_home: Path | None = None) -> Path:
    return _cache_path(corvin_home).with_name(_SIG_FILENAME)


# ── Fetch + verify ─────────────────────────────────────────────────────────────

def fetch_and_cache(
    corvin_home: Path | None = None,
    *,
    timeout: float = 10.0,
) -> dict[str, Any] | None:
    """Fetch the latest manifest from GitHub, verify signature, and cache.

    Returns the manifest dict on success, None on failure (network error,
    signature mismatch, JSON parse error, etc.).
    """
    try:
        import urllib.request
        with urllib.request.urlopen(_MANIFEST_URL, timeout=timeout) as resp:
            manifest_json = resp.read()
        with urllib.request.urlopen(_MANIFEST_SIG_URL, timeout=timeout) as resp:
            sig_bytes = resp.read()
    except Exception as exc:
        log.debug("manifest: fetch failed (%s)", exc)
        return None

    try:
        from .seal_loader import verify_manifest
        if not verify_manifest(manifest_json, sig_bytes):
            log.warning("manifest: signature verification failed")
            _audit("license.manifest_sig_invalid")
            return None
    except Exception as exc:
        log.warning("manifest: verification error (%s)", exc)
        return None

    try:
        manifest = json.loads(manifest_json)
    except Exception:
        return None

    # FND-04: cache the ORIGINAL signed bytes + the signature, so the cache can
    # be re-verified on read. Do NOT inject _fetched_at into the JSON (that
    # would invalidate the signature AND let a local tamperer fake freshness);
    # staleness is derived from the cache file's mtime in load_cached_manifest.
    cache = _cache_path(corvin_home)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(cache, manifest_json)
        _atomic_write(_sig_path(corvin_home), sig_bytes)
        log.info(
            "manifest: fetched nonce_epoch=%s min_seal=%s",
            manifest.get("nonce_epoch"),
            manifest.get("min_seal_version"),
        )
        _audit(
            "license.manifest_fetched",
            nonce_epoch=manifest.get("nonce_epoch", 0),
            min_seal_version=str(manifest.get("min_seal_version", "")),
        )
        # ADR-0138 M3 D3: persist nonce_epoch independently so manifest cache
        # deletion cannot reset replay-epoch enforcement to 0.
        try:
            from .instance_epoch import write_instance_epoch
            _epoch = manifest.get("nonce_epoch", 0)
            if isinstance(_epoch, int) and _epoch > 0:
                write_instance_epoch(corvin_home or _default_corvin_home(), _epoch)
        except Exception as _ep_exc:  # noqa: BLE001
            log.warning("manifest: epoch persist failed (%s)", _ep_exc)
    except Exception as exc:
        log.warning("manifest: cache write failed (%s)", exc)

    return manifest


# ── Load cached ───────────────────────────────────────────────────────────────

def load_cached_manifest(corvin_home: Path | None = None) -> dict[str, Any] | None:
    """Load the cached manifest, VERIFYING its signature first (FND-04).

    Returns None if the cache is absent, unreadable, has no signature, or the
    signature does not verify — so a locally-tampered cache (e.g. one with an
    emptied revoked_instance_ids / reset nonce_epoch) is rejected, not trusted.
    ``_fetched_at`` is derived from the cache file mtime (NOT an in-payload
    field a tamperer could forge) for downstream staleness checks.
    """
    cache = _cache_path(corvin_home)
    try:
        manifest_bytes = cache.read_bytes()
        sig_bytes = _sig_path(corvin_home).read_bytes()
    except Exception:
        return None  # absent / unreadable / no signature sidecar
    try:
        from .seal_loader import verify_manifest
        if not verify_manifest(manifest_bytes, sig_bytes):
            log.warning("manifest: cached signature INVALID — ignoring (possible tamper)")
            _audit("license.manifest_cache_sig_invalid")
            return None
    except Exception as exc:  # noqa: BLE001 — verifier error → distrust the cache
        log.warning("manifest: cache verification error (%s)", exc)
        return None
    try:
        data = json.loads(manifest_bytes)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        data["_fetched_at"] = int(cache.stat().st_mtime)
    except OSError:
        data["_fetched_at"] = 0
    return data


def assess_staleness(
    manifest: dict[str, Any] | None,
    *,
    now: float | None = None,
) -> tuple[str, int]:
    """Return (status, age_seconds) for observability.

    status: "fresh" | "warn" | "stale" | "absent"
    """
    if manifest is None:
        return "absent", 0
    now_ = now if now is not None else time.time()
    age = int(now_ - manifest.get("_fetched_at", 0))
    if age < _CACHE_TTL_WARN:
        return "fresh", age
    if age < _CACHE_TTL_MAX:
        return "warn", age
    return "stale", age


def get_nonce_epoch(corvin_home: Path | None = None) -> int:
    """Return the nonce_epoch from the cached manifest, or 0 when absent.

    R2-08: a stale manifest previously reset the epoch to 0 (accept-any),
    silently disabling replay-epoch enforcement after 7 days offline — a
    fail-OPEN. A stale cached epoch can only LAG the true epoch (failing toward
    MORE rejection at worst), never legitimately drop to 0, so we keep enforcing
    the last-known epoch regardless of staleness.

    ADR-0138 M3 D3: when the manifest cache is absent (e.g. after a delete),
    fall back to the persisted instance_epoch.json instead of returning 0.
    A manifest cache deletion can no longer reset the epoch to 0.
    Only a fresh install with no prior manifest fetch yields 0."""
    ch = corvin_home or _default_corvin_home()
    try:
        from .instance_epoch import read_instance_epoch
        floor = max(0, int(read_instance_epoch(ch)))
    except Exception:  # noqa: BLE001
        floor = 0
    m = load_cached_manifest(ch)
    epoch_from_manifest = int(m.get("nonce_epoch", 0)) if m else 0
    # R3-confirm: FLOOR the manifest epoch against the monotonic persisted
    # instance_epoch.json. load_cached_manifest verifies the signature, so a
    # tamperer cannot forge a lower epoch — but it does NOT enforce monotonicity
    # across cache replacements: substituting an OLDER, still-validly-signed
    # manifest (lower nonce_epoch) would otherwise DOWNGRADE replay-epoch
    # enforcement and re-admit SOBs from before the last rotation. max() makes the
    # persisted high-water mark a hard floor the cache can never lower.
    return max(epoch_from_manifest, floor)


def check_instance_revoked(instance_id: str, corvin_home: Path | None = None) -> bool:
    """True if the instance_id appears in the manifest revocation list.

    R2-08: a stale manifest previously returned False for EVERY id (clearing
    the revocation list), silently un-revoking a known-bad instance after 7
    days offline — a fail-OPEN. A stale revocation list can only be MISSING new
    revocations, never legitimately dropping an existing one, so we keep
    honouring the last-known list regardless of staleness (a revoked instance
    stays revoked). Only a genuinely ABSENT manifest means "nothing to enforce"."""
    m = load_cached_manifest(corvin_home)
    if m is None:
        return False
    revoked = m.get("revoked_instance_ids", [])
    return isinstance(revoked, list) and instance_id in revoked


# ── Staleness audit at boot ────────────────────────────────────────────────────

def audit_staleness(corvin_home: Path | None = None) -> None:
    """Emit a staleness audit event.  Call once at adapter boot."""
    m = load_cached_manifest(corvin_home)
    status, age_days = assess_staleness(m)
    if status == "warn":
        log.warning("manifest: %d days old (>3 day threshold)", age_days // 86400)
        _audit("license.manifest_stale", manifest_age_days=age_days // 86400)
    elif status == "stale":
        log.warning("manifest: %d days old (revocation list cleared)", age_days // 86400)
        _audit("license.manifest_stale", manifest_age_days=age_days // 86400)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mfst.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _audit(event_type: str, **details: Any) -> None:
    try:
        import sys
        shared = Path(__file__).resolve().parents[1] / "bridges" / "shared"
        if str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        from audit import audit_event  # type: ignore[import]
        audit_event(event_type, **details)
    except Exception:  # noqa: BLE001
        pass
