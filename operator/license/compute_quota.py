"""Daily compute-unit quota counter — ADR-0094 M2.

Tracks how many compute_run invocations have been dispatched today,
keyed by UTC date string. Enforces compute_units_per_day against the
active licence limits before each compute job.

Storage:
    <corvin_home>/global/license/compute_quota.json   (mode 0600)
Format:
    {"2026-06-06": 7, "2026-06-07": 2}

Fail-open contract:
    I/O errors NEVER block compute — operational failures fall through.
    Only LicenseLimitError is intentionally re-raised.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Windows — no fcntl module.
    # This module is reachable from adapter.py/chat_runtime.py/dispatcher.py/
    # routes/compute*.py via lazy, function-local imports with no guaranteed
    # ordering against operator/forge's or corvin_console's own _wincompat
    # shim install — a bare top-level `import fcntl` here crashed the first
    # Windows caller to reach it with ModuleNotFoundError (adversarial review
    # finding; same bug class as the 17-module Windows sweep, this file was
    # missed). The lock is single-host advisory-only and quota writes are
    # already atomic via temp-file + os.replace, so a no-op is correctness-
    # preserving within one Windows process.
    import types as _types

    fcntl = _types.ModuleType("fcntl")  # type: ignore[assignment]
    fcntl.LOCK_EX = 2  # type: ignore[attr-defined]
    fcntl.LOCK_UN = 8  # type: ignore[attr-defined]
    fcntl.flock = lambda *_a, **_k: 0  # type: ignore[attr-defined]

_log = logging.getLogger("corvin.license.compute_quota")

from .limits import FREE_TIER, LicenseLimitError  # noqa: E402  # type: ignore


# ── Internal helpers ──────────────────────────────────────────────────────────

def _quota_path(corvin_home: Path, counter_file: str = "compute_quota.json") -> Path:
    return corvin_home / "global" / "license" / counter_file


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _cutoff_date() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")


def _load(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        mode = path.stat().st_mode & 0o777
        # Windows: NTFS has no POSIX group/other bits, so st_mode always looks
        # permissive there regardless of real ACLs — skip the check.
        if not sys.platform.startswith("win") and mode & 0o077:
            # Log a warning but still read the data — returning {} would reset
            # the counter to 0 and allow an attacker with chmod access to bypass
            # the quota by repeatedly making the file world-readable.
            _log.warning(
                "compute_quota: file mode 0o%o too permissive (expected 0600) — "
                "mode will be corrected on next write",
                mode,
            )
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        # Q1/Q2 (ADR-0144): exclude non-finite floats (inf/nan) — int(inf) → OverflowError,
        # int(nan) → ValueError, both would be caught by the outer except and return {},
        # which silently resets the quota. Explicit finite-check prevents the bypass.
        return {
            k: int(v) for k, v in raw.items()
            if (isinstance(v, int) and not isinstance(v, bool))  # explicit parens: and > or
            or (isinstance(v, float) and v == v and abs(v) != float("inf"))
        }
    except Exception as exc:
        _log.warning("compute_quota: load failed (%s) — starting fresh", exc)
        return {}


def _save(path: Path, data: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cq.", suffix=".tmp")
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


def _emit_quota_exceeded(
    *,
    channel: str,
    chat_key: str,
    requested: int,
    limit: int,
    tier: str,
    feature: str = "compute_units_per_day",
) -> None:
    """Best-effort audit event; never raises."""
    try:
        _repo = Path(__file__).resolve().parents[2]
        _shared = _repo / "bridges" / "shared"
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from audit import audit_event  # type: ignore
        audit_event(
            "compute.quota_exceeded",
            channel=channel,
            chat_key=chat_key,
            feature=feature,
            requested_value=requested,
            limit_value=limit,
            tier=tier,
        )
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def get_today_count(corvin_home: Path, counter_file: str = "compute_quota.json") -> int:
    """Return how many units have been used today. Fail-open: 0 on error."""
    try:
        data = _load(_quota_path(corvin_home, counter_file))
        return int(data.get(_today_utc(), 0))
    except Exception:
        return 0


def increment_and_check(
    corvin_home: Path,
    *,
    channel: str = "",
    chat_key: str = "",
    feature: str = "compute_units_per_day",
    counter_file: str = "compute_quota.json",
) -> None:
    """Atomically increment today's counter and raise LicenseLimitError if over quota.

    Fail-open on I/O errors: operational failures never block the gated action.
    Only LicenseLimitError (intentional limit violation) is re-raised.

    Args:
        corvin_home: Tenant corvin home directory.
        channel: Bridge channel name (for audit event).
        chat_key: Chat key (for audit event).
        feature: License limit key to enforce (default compute_units_per_day;
                 ADR-0150: chat/design surfaces pass chat_turns_per_day).
        counter_file: Per-feature counter filename under global/license/.

    Raises:
        LicenseLimitError: Today's ``feature`` limit is exhausted.
    """
    try:
        _do_increment_and_check(
            corvin_home, channel=channel, chat_key=chat_key,
            feature=feature, counter_file=counter_file,
        )
    except LicenseLimitError:
        raise  # intentional signal — always re-raise
    except Exception as exc:
        _log.warning(
            "compute_quota: gate error (%s) — allowing through (fail-open)", exc
        )
        # Emit audit event so operators know the quota gate is degraded.
        try:
            _repo = Path(__file__).resolve().parents[2]
            _shared = _repo / "bridges" / "shared"
            if str(_shared) not in sys.path:
                sys.path.insert(0, str(_shared))
            from audit import audit_event  # type: ignore
            # Reason CODE, not str(exc): an OSError message can embed a filesystem
            # path (PII/infra leak). The exception type is the metadata-only signal.
            audit_event("compute.quota_gate_degraded",
                        severity="WARNING", details={"reason": type(exc).__name__})
        except Exception:
            pass


def _do_increment_and_check(
    corvin_home: Path,
    *,
    channel: str,
    chat_key: str,
    feature: str = "compute_units_per_day",
    counter_file: str = "compute_quota.json",
) -> None:
    from .validator import get_limit, active_tier  # type: ignore

    today = _today_utc()
    path = _quota_path(corvin_home, counter_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    lock_path = path.parent / f".{counter_file}.lock"
    lock_path.touch()
    with open(lock_path) as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            data = _load(path)
            current = max(0, int(data.get(today, 0)))  # A4: floor clamp — negative file value ≠ bonus
            limit = get_limit(feature)

            if limit is not None:
                try:
                    limit_int = int(limit)
                except (TypeError, ValueError):
                    # Malformed limit in SesT (e.g. "unlimited" string instead of
                    # None or int) — fail-closed rather than allowing through.
                    # Without this, the ValueError propagates to the outer
                    # `except Exception` handler which fails open.
                    _log.warning(
                        "compute_quota: %s limit %r is not "
                        "numeric — treating as exceeded (fail-closed).", feature, limit
                    )
                    raise LicenseLimitError(
                        feature,
                        requested=current + 1,
                        limit=limit,
                        tier=active_tier(),
                    )

                if (current + 1) > limit_int:
                    tier = active_tier()
                    _emit_quota_exceeded(
                        channel=channel,
                        chat_key=chat_key,
                        requested=current + 1,
                        limit=limit_int,
                        tier=tier,
                        feature=feature,
                    )
                    raise LicenseLimitError(
                        feature,
                        requested=current + 1,
                        limit=limit_int,
                        tier=tier,
                    )

            data[today] = current + 1
            # Prune entries older than 7 days (prevents unbounded growth)
            cutoff = _cutoff_date()
            data = {k: v for k, v in data.items() if k >= cutoff}
            _save(path, data)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


__all__ = [
    "get_today_count",
    "increment_and_check",
    "LicenseLimitError",
]
