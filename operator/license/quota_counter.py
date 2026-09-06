"""Daily quota counter for Brain v0.2 features — ADR-0365.

Tracks daily usage for:
- brain_tasks_per_day (orchestration tasks)
- tool_forge_per_day (tool generation)
- skill_forge_per_day (skill creation)

Storage:
    <corvin_home>/quotas/<tenant_id>_<feature>_<date>.json

Format:
    {"count": 5, "reset_time": "2026-08-18T00:00:00Z"}

Fail contract (LIC-2 parity):
    On I/O error, fail-closed (deny) for free tier, fail-open for member tier.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# HIGH-002 FIX: Module-level lock serializes ALL read-modify-write operations
# across threads within a single process. Combined with file-level fcntl.flock
# for cross-process safety, this ensures the ENTIRE quota operation (read current
# count, check limit, increment, write) is atomic — preventing TOCTOU races where
# multiple threads could all read count=0, all see they're under limit, and all
# increment, blowing past the cap. MUST HOLD this lock for the duration of:
#   1. _load(path)
#   2. Evaluate (current + 1) > limit_int check
#   3. _save(path) with new count
# Releasing the lock between check and increment is NOT PERMITTED (HIGH-002 fix).
_INCREMENT_LOCK = threading.Lock()

_IS_WINDOWS = sys.platform.startswith("win")

try:
    import msvcrt  # type: ignore
except ImportError:
    msvcrt = None  # type: ignore[assignment]

try:
    import fcntl
except ImportError:
    # Windows fallback
    import types as _types

    fcntl = _types.ModuleType("fcntl")  # type: ignore[assignment]
    fcntl.LOCK_EX = 2  # type: ignore[attr-defined]
    fcntl.LOCK_UN = 8  # type: ignore[attr-defined]
    fcntl.flock = lambda *_a, **_k: 0  # type: ignore[attr-defined]

_log = logging.getLogger("corvin.license.quota_counter")

try:
    from .limits import LicenseLimitError
except ImportError:
    from limits import LicenseLimitError  # type: ignore[no-redef]


def _today_utc() -> str:
    """Return today's date in UTC as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _quota_path(corvin_home: Path, tenant_id: str, feature: str, date_str: str) -> Path:
    """Return path to quota counter file."""
    quota_dir = corvin_home / "quotas"
    quota_dir.mkdir(parents=True, exist_ok=True)
    return quota_dir / f"{tenant_id}_{feature}_{date_str}.json"


def _lock_path(corvin_home: Path, tenant_id: str, feature: str, date_str: str) -> Path:
    """Return path to lock file for atomicity."""
    quota_dir = corvin_home / "quotas"
    return quota_dir / f"{tenant_id}_{feature}_{date_str}.lock"


def _load(path: Path) -> dict[str, Any]:
    """Load quota counter file, returning {'count': int}."""
    if not path.exists():
        return {"count": 0}
    try:
        mode = path.stat().st_mode & 0o777
        # Windows: NTFS has no POSIX group/other bits, so st_mode always looks
        # permissive there regardless of real ACLs — skip the check.
        if not sys.platform.startswith("win") and mode & 0o077:
            _log.warning(
                "quota_counter: file mode 0o%o too permissive (expected 0600)",
                mode,
            )
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"count": 0}
        count = raw.get("count", 0)
        if isinstance(count, int) and count >= 0:
            return {"count": count}
        return {"count": 0}
    except Exception as exc:
        _log.warning("quota_counter: load failed (%s) — starting fresh", exc)
        return {"count": 0}


def _save(path: Path, data: dict[str, Any]) -> None:
    """Save quota counter file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".qc.", suffix=".tmp")
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


def get_limit(feature: str) -> Any:
    """Active-tier limit for ``feature`` (``None`` = unlimited).

    Module-level indirection over ``validator.get_limit`` (imported lazily —
    the validator imports this module). Being a module attribute is what
    lets tests and operator tooling patch the limit; the previous function-
    local ``from .validator import get_limit`` silently bypassed every such
    patch (``monkeypatch.setattr(_quota, "get_limit", ...)`` was a no-op).
    """
    from .validator import get_limit as _gl  # type: ignore

    return _gl(feature)


def increment_and_check(
    corvin_home: Path,
    feature: str,
    tenant_id: str,
    counter_file: str = "quota_counter.json",
) -> int:
    """Atomically increment today's counter and raise LicenseLimitError if over quota.

    Args:
        corvin_home: Path to ~/.corvin directory
        feature: License feature key (brain_tasks_per_day, tool_forge_per_day, etc.)
        tenant_id: Tenant identifier (isolation)
        counter_file: Unused, kept for compatibility

    Returns:
        New counter value (for informational purposes)

    Raises:
        LicenseLimitError: When today's quota is exhausted for this feature/tenant.
    """
    try:
        return _do_increment_and_check(corvin_home, feature, tenant_id)
    except LicenseLimitError:
        raise  # intentional signal — always re-raise
    except Exception as exc:
        # LIC-2: retry ONCE
        try:
            return _do_increment_and_check(corvin_home, feature, tenant_id)
        except LicenseLimitError:
            raise
        except Exception as exc2:
            exc = exc2

        # Persistent failure. Decide fail-open vs fail-closed by the limit shape:
        _limit_is_finite = True
        _limit_val: Any = None
        _tier = "free"
        try:
            from .validator import get_limit as _gl, active_tier as _at  # type: ignore

            _limit_val = _gl(feature)
            _limit_is_finite = _limit_val is not None
            _tier = _at()
        except Exception:
            _limit_is_finite = True  # cannot determine → finite → deny

        _log.error(
            "quota_counter: gate error (%s) on a %s %s limit — %s (fail-%s)",
            type(exc).__name__,
            "FINITE" if _limit_is_finite else "UNLIMITED",
            feature,
            "denying" if _limit_is_finite else "allowing",
            "closed" if _limit_is_finite else "open",
        )

        if _limit_is_finite:
            raise LicenseLimitError(
                feature, requested=None, limit=_limit_val, tier=_tier
            )
        return 0  # fail-open for member tier


def _do_increment_and_check(
    corvin_home: Path,
    feature: str,
    tenant_id: str,
) -> int:
    """Inner implementation with no retry logic."""
    today = _today_utc()
    path = _quota_path(corvin_home, tenant_id, feature, today)
    lock_path = _lock_path(corvin_home, tenant_id, feature, today)

    with _INCREMENT_LOCK:
        try:
            # File-level locking for cross-process atomicity
            with open(lock_path, "w") as lock_file:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                except (AttributeError, OSError):
                    # Windows or fcntl unavailable — rely on the module-level lock
                    pass

                try:
                    limit = get_limit(feature)
                    if limit is None:
                        # Unlimited tier: nothing to enforce and nothing to
                        # track — no counter file is written (returns 0).
                        return 0
                    data = _load(path)
                    current = max(0, int(data.get("count", 0)))

                    if limit is not None:
                        try:
                            limit_int = int(limit)
                        except (TypeError, ValueError):
                            # Malformed limit — fail-closed
                            raise LicenseLimitError(
                                feature,
                                requested=current + 1,
                                limit=limit,
                                tier="free",
                            )

                        # Check if limit would be exceeded
                        if current >= limit_int:
                            # A `reset_time` was computed here and never used.
                            # Worse, it crashed: it called
                            # `timezone.utc.localize(...)`, which is pytz's API
                            # -- `datetime.timezone` has no `localize`. The
                            # expression only evaluated when
                            # `now.day < 28`, so on days 1-27 of every month
                            # this raised AttributeError at exactly the moment a
                            # user hit their quota. The caller's `except
                            # Exception` then re-ran the gate and reported
                            # "feature not available on tier 'free'" instead of
                            # "limit exceeded" -- the wrong message, on 27 days
                            # out of ~30. Removed: the value had no consumer.
                            raise LicenseLimitError(
                                feature,
                                requested=current + 1,
                                limit=limit_int,
                                tier="free",
                            )

                    # Increment and save
                    new_count = current + 1
                    new_data = {
                        "count": new_count,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    _save(path, new_data)
                    return new_count

                finally:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    except (AttributeError, OSError):
                        pass

        except LicenseLimitError:
            raise
        except Exception:
            raise


def get_today_count(
    corvin_home: Path,
    feature: str,
    tenant_id: str,
) -> int:
    """Return how many units have been used today. Fail-open: 0 on error."""
    try:
        today = _today_utc()
        path = _quota_path(corvin_home, tenant_id, feature, today)
        data = _load(path)
        return int(data.get("count", 0))
    except Exception:
        return 0
