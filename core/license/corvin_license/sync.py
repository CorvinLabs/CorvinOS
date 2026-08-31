"""7-day validation cache with background server sync.

Corvin licenses verify offline against the embedded RS256 public key.
This layer adds a 7-day server heartbeat that:

  - Propagates revocations within 7 days (server marks token_fp as revoked)
  - Anchors trial ``activated_at`` server-side, defeating clock manipulation
  - Confirms tier/flags via a second opinion (defence-in-depth)

If the sync server is unreachable, the cached result is trusted for up to
``SYNC_CACHE_TTL`` (7 days). After that, the standard grace-period state
machine takes over exactly as for an expired paid license.

Privacy:
  The sync request sends ONLY anonymous fingerprints + epoch values —
  no JWT content, no customer_id, no IP-identifying headers are transmitted.

Cache file:
  ``<corvin_home>/global/license/sync_cache.json``  (mode 0o600)

Sync URL:
  ``CORVIN_LICENSE_SYNC_URL`` env var (default:
  ``https://license.corvin.ai/v1/validate``).
  Set ``CORVIN_LICENSE_SYNC_DISABLED=1`` to disable entirely.
  Both env vars only take effect when ``CORVIN_TEST_MODE=1`` is also set
  (ADR-0098: preventing production redirect/disable as bypass vectors).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parents[2]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402

log = logging.getLogger(__name__)

SYNC_CACHE_TTL = 7 * 24 * 3600       # 7 days — trust window
SYNC_RETRY_FLOOR = 3600               # don't retry within 1 h of a failed attempt
SYNC_TIMEOUT = 10                     # network call timeout (seconds)
DEFAULT_SYNC_URL = "https://license.corvin.ai/v1/validate"


# ── On-disk cache structure ───────────────────────────────────────────

@dataclass
class SyncCache:
    """Persisted result of the last successful server sync."""
    last_sync_at: int | None = None         # epoch of last successful sync
    cache_valid_until: int | None = None    # last_sync_at + SYNC_CACHE_TTL
    last_attempt_at: int | None = None      # epoch of last attempt (incl. failures)
    is_revoked: bool = False                # server confirmed token revoked
    server_tier: str | None = None          # server-confirmed tier
    server_flags: list[str] | None = None   # server-confirmed flag list
    trial_activated_at: int | None = None   # server-anchored trial first-use
    sync_error: str | None = None          # last error message (diagnostics only)
    schema_version: int = 1

    def is_fresh(self, now: int | None = None) -> bool:
        """True if cache is within the 7-day trust window."""
        _now = now or int(time.time())
        return bool(self.cache_valid_until and _now < self.cache_valid_until)

    def retry_allowed(self, now: int | None = None) -> bool:
        """True if enough time has passed since the last attempt."""
        if self.last_attempt_at is None:
            return True
        return (now or int(time.time())) - self.last_attempt_at >= SYNC_RETRY_FLOOR

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SyncCache":
        return cls(
            last_sync_at=d.get("last_sync_at"),
            cache_valid_until=d.get("cache_valid_until"),
            last_attempt_at=d.get("last_attempt_at"),
            is_revoked=bool(d.get("is_revoked", False)),
            server_tier=d.get("server_tier"),
            server_flags=d.get("server_flags"),
            trial_activated_at=d.get("trial_activated_at"),
            sync_error=d.get("sync_error"),
            schema_version=int(d.get("schema_version", 1)),
        )


# ── Disk I/O ──────────────────────────────────────────────────────────

def _cache_path() -> Path:
    return _forge_paths.corvin_home() / "global" / "license" / "sync_cache.json"


def load_sync_cache() -> SyncCache:
    """Load from disk; return empty defaults when absent."""
    path = _cache_path()
    if not path.exists():
        return SyncCache()
    try:
        mode = path.stat().st_mode & 0o777
        # Windows: NTFS has no POSIX group/other bits, so st_mode always looks
        # permissive there regardless of real ACLs — skip the check.
        if not sys.platform.startswith("win") and mode & 0o077:
            # Log a warning but still read the data — discarding the cache
            # here (returning a fresh SyncCache()) would silently drop a
            # legitimately-cached is_revoked=True record, letting a local
            # attacker un-revoke a cancelled/revoked license on every status
            # check just by `chmod`-ing this file to a permissive mode
            # (adversarial review finding — mirrors the exact bypass
            # compute_quota.py's _load() already guards against; the mode
            # is corrected on the next save_sync_cache() write).
            log.warning(
                "sync_cache: file mode 0o%o too permissive (expected 0600) — "
                "mode will be corrected on next write", mode,
            )
        return SyncCache.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        log.warning("sync_cache: load failed (%s) — ignoring", exc)
        return SyncCache()


def save_sync_cache(cache: SyncCache) -> None:
    """Atomic write, mode 0o600."""
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(cache.to_dict(), sort_keys=True, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sync_cache.", suffix=".tmp")
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


# ── Token fingerprint (privacy-preserving identifier) ─────────────────

def token_fingerprint(raw_jwt: str) -> str:
    """First 16 hex chars of sha256(jwt_bytes). Non-reversible, greppable."""
    return hashlib.sha256(raw_jwt.encode("utf-8")).hexdigest()[:16]


# ── Server sync ───────────────────────────────────────────────────────

class SyncDisabled(Exception):
    """CORVIN_LICENSE_SYNC_DISABLED=1 is set."""


class SyncNetworkError(Exception):
    """Server unreachable or returned unexpected status."""


def _emit_sync_disabled() -> None:
    """ADR-0093 M1.4 — best-effort audit event when sync is disabled via env."""
    try:
        from . import audit as _audit
        _audit.license_sync_disabled()
    except Exception:
        pass


# RTL2-LIC-02 (ADR-0147) / ADR-0144 B1: snapshot the test-mode env knobs at module
# import so a post-boot in-process os.environ mutation cannot redirect/disable the
# revocation-sync heartbeat. Mirrors operator/license/session_refresh.py. In
# production CORVIN_TEST_MODE is unset, so these snapshots are inert.
_TEST_MODE_SNAPSHOT: bool = os.environ.get("CORVIN_TEST_MODE") == "1"
_SYNC_URL_SNAPSHOT: str = os.environ.get("CORVIN_LICENSE_SYNC_URL", "")
_SYNC_DISABLED_SNAPSHOT: bool = os.environ.get("CORVIN_LICENSE_SYNC_DISABLED") == "1"


def _sync_url() -> str:
    """Return the license sync URL.

    CORVIN_LICENSE_SYNC_URL is only honoured when CORVIN_TEST_MODE=1 (snapshotted
    at import — RTL2-LIC-02). In production the URL is hardcoded to prevent
    redirection to mock servers (ADR-0098 security review).
    """
    if _TEST_MODE_SNAPSHOT:
        return _SYNC_URL_SNAPSHOT or DEFAULT_SYNC_URL
    return DEFAULT_SYNC_URL


def sync_with_server(
    *,
    token_fp: str,
    iat: int,
    exp: int,
    trial_id: str | None = None,
    trial_type: str | None = None,
) -> SyncCache:
    """POST to the license-sync server and return a fresh SyncCache.

    Raises ``SyncDisabled`` when disabled via env.
    Raises ``SyncNetworkError`` on any network or protocol failure.
    Never raises on unexpected server data — degrades gracefully.
    """
    # CORVIN_LICENSE_SYNC_DISABLED is only honoured in test mode to prevent
    # production bypass of server revocation sync (ADR-0098 security review).
    if _TEST_MODE_SNAPSHOT and _SYNC_DISABLED_SNAPSHOT:
        _emit_sync_disabled()
        raise SyncDisabled("CORVIN_LICENSE_SYNC_DISABLED=1")

    url = _sync_url()
    body: dict[str, Any] = {
        "token_fp": token_fp,
        "iat": iat,
        "exp": exp,
    }
    if trial_id:
        body["trial_id"] = trial_id
    if trial_type:
        body["trial_type"] = trial_type

    payload = json.dumps(body).encode("utf-8")
    req = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "corvin-license-sync/1",
        },
    )
    try:
        with urlopen(req, timeout=SYNC_TIMEOUT) as resp:
            if resp.status != 200:
                raise SyncNetworkError(f"server returned HTTP {resp.status}")
            data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError) as exc:
        raise SyncNetworkError(str(exc)) from exc

    now = int(time.time())
    return SyncCache(
        last_sync_at=now,
        cache_valid_until=now + SYNC_CACHE_TTL,
        last_attempt_at=now,
        is_revoked=bool(data.get("revoked", False)),
        server_tier=str(data["server_tier"]) if data.get("server_tier") else None,
        server_flags=list(data["server_flags"]) if isinstance(data.get("server_flags"), list) else None,
        trial_activated_at=int(data["trial_activated_at"]) if data.get("trial_activated_at") else None,
        sync_error=None,
        schema_version=1,
    )


# ── Background sync worker ────────────────────────────────────────────

_sync_lock = threading.Lock()  # one sync at a time per process


def _sync_worker(
    *,
    token_fp: str,
    iat: int,
    exp: int,
    trial_id: str | None,
    trial_type: str | None,
) -> None:
    """Background thread: sync, update cache. Best-effort — never raises."""
    if not _sync_lock.acquire(blocking=False):
        return  # another sync already in flight
    try:
        now = int(time.time())
        # Stamp attempt time before the network call so a crash
        # doesn't leave last_attempt_at=None and trigger an immediate retry.
        cache = load_sync_cache()
        cache.last_attempt_at = now
        try:
            save_sync_cache(cache)
        except Exception:
            pass

        try:
            fresh = sync_with_server(
                token_fp=token_fp,
                iat=iat,
                exp=exp,
                trial_id=trial_id,
                trial_type=trial_type,
            )
            save_sync_cache(fresh)
            log.debug("sync_cache: sync OK (tier=%s revoked=%s)", fresh.server_tier, fresh.is_revoked)
        except SyncDisabled:
            pass
        except SyncNetworkError as exc:
            cache2 = load_sync_cache()
            cache2.sync_error = str(exc)
            try:
                save_sync_cache(cache2)
            except Exception:
                pass
            log.debug("sync_cache: sync failed (%s) — cached result stands", exc)
    finally:
        _sync_lock.release()


def maybe_sync_in_background(
    *,
    raw_jwt: str,
    iat: int,
    exp: int,
    trial_id: str | None = None,
    trial_type: str | None = None,
) -> None:
    """Fire-and-forget background sync when cache is stale.

    Non-blocking. Safe to call on every license check — skips if:
    - cache is still fresh (within 7 days)
    - a retry was already attempted within the last hour
    - CORVIN_LICENSE_SYNC_DISABLED=1
    """
    cache = load_sync_cache()
    now = int(time.time())
    if cache.is_fresh(now) and not cache.is_revoked:
        return  # cache valid, no need to sync
    if not cache.retry_allowed(now):
        return  # backed off after a recent failure
    # Sync is needed — check if it's been disabled via env (import-snapshot,
    # RTL2-LIC-02). Emit only here (not on every call) so the event is a true
    # anomaly signal: "a sync was due but blocked by CORVIN_LICENSE_SYNC_DISABLED=1".
    if _TEST_MODE_SNAPSHOT and _SYNC_DISABLED_SNAPSHOT:
        _emit_sync_disabled()
        return

    fp = token_fingerprint(raw_jwt)
    t = threading.Thread(
        target=_sync_worker,
        kwargs=dict(
            token_fp=fp, iat=iat, exp=exp,
            trial_id=trial_id, trial_type=trial_type,
        ),
        daemon=True,
        name="corvin-license-sync",
    )
    t.start()


__all__ = [
    "SYNC_CACHE_TTL",
    "SyncCache",
    "SyncDisabled",
    "SyncNetworkError",
    "load_sync_cache",
    "save_sync_cache",
    "token_fingerprint",
    "sync_with_server",
    "maybe_sync_in_background",
]
