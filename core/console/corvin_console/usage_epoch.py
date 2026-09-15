"""Counting epoch for usage + cost views — ADR-0760.

The problem this solves, and the one it refuses to solve
-------------------------------------------------------

An operator wants the OS and worker series to start from the same point, so the
two are comparable. The obvious way to get that is to trim the audit chain. That
is **forbidden** and this module exists partly to make the alternative easy
enough that nobody tries: the chain is append-only, hash-linked, verified by the
ADR-0232 boot tripwire and is the GDPR Art. 30/32 record. Deleting from it does
not reset a counter, it breaks the chain and bricks the next boot.

So nothing is ever deleted. Instead ONE timestamp per tenant says *from when* the
console counts. History stays exactly where it is and stays readable — clearing
the epoch brings every historical turn straight back.

Why one module and not a field on each reader
---------------------------------------------

``model_usage`` (turn shares) and ``model_selection_learner`` (dollars) read the
same chain for the same panels. If each kept its own window they would disagree,
and two numbers on one screen that disagree about *which turns they counted* is
worse than no reset at all. Both call :func:`epoch_ts`.

What the epoch is NOT
---------------------

It is not a retention policy, not an erasure mechanism (GDPR Art. 17 erasure is
L36's job and works on the data, not on a view), and not a filter anything
security-bearing may consult. It changes what a dashboard counts. Every consumer
that shows a number derived from it must also show the window — an unlabelled
"482 turns" that silently means "since Tuesday" is a lie the cheap way.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

#: File name under ``<tenant>/global/``. Small, versioned, operator-editable.
_FILENAME = "usage_epoch.json"

_SCHEMA_VERSION = 1


def _path(tenant_id: str) -> Path | None:
    """``<tenant_home>/global/usage_epoch.json``, or None if unresolvable.

    Resolved through the shared tenant-path helper rather than composed here —
    the same rule the audit chain follows (ADR-0007/ADR-0650). A tenant root we
    cannot resolve means "no epoch", never a guessed path.
    """
    try:
        from core.paths import tenant_home  # noqa: PLC0415

        return Path(tenant_home(tenant_id)) / "global" / _FILENAME
    except Exception:  # noqa: BLE001
        try:
            from forge import paths as _forge_paths  # type: ignore  # noqa: PLC0415

            return Path(_forge_paths.tenant_global_dir(tenant_id)) / _FILENAME
        except Exception:  # noqa: BLE001
            return None


def read(tenant_id: str) -> dict[str, Any]:
    """The full epoch record, or ``{}`` when none is set.

    Never raises: an unreadable or corrupt file means "no epoch" — the honest
    all-time view — and not an error the panels have to render.
    """
    path = _path(tenant_id)
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    ts = data.get("epoch_ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or ts <= 0:
        return {}
    return data


def epoch_ts(tenant_id: str) -> float:
    """The cut-off as a unix timestamp, or ``0.0`` for "count everything".

    ``0.0`` is deliberately the same value a missing file produces, so a caller
    can write ``if ts and event_ts < ts: skip`` and get the all-time behaviour
    for free rather than branching on a sentinel.
    """
    return float(read(tenant_id).get("epoch_ts") or 0.0)


def set_epoch(
    tenant_id: str,
    *,
    at: float | None = None,
    reason: str = "",
    actor: str = "",
) -> dict[str, Any]:
    """Start counting from ``at`` (default: now). Returns the stored record.

    Writes atomically at mode 0o600 — the same discipline every other file in
    this tree follows, and for the same reason: a writer that honours the
    process umask leaves a state file readable to anyone on the host.
    """
    path = _path(tenant_id)
    if path is None:
        raise RuntimeError(f"cannot resolve a tenant home for {tenant_id!r}")
    record = {
        "version": _SCHEMA_VERSION,
        "epoch_ts": float(at if at is not None else time.time()),
        "set_at": time.time(),
        # Free-text operator note. Never a credential, never a user identifier —
        # the console passes a session fingerprint, not a uid (GDPR Art. 5).
        "reason": reason[:500],
        "actor": actor[:128],
    }
    _write(path, record)
    return record


def clear_epoch(tenant_id: str) -> None:
    """Drop the epoch: every panel returns to the full all-time view.

    History was never removed, so this genuinely restores it — which is the
    property that makes the whole mechanism safe to offer in the first place.
    """
    path = _path(tenant_id)
    if path is None or not path.exists():
        return
    try:
        path.unlink()
    except OSError:
        pass


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".usage_epoch.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def window_note(tenant_id: str) -> dict[str, Any]:
    """What a panel needs to LABEL its numbers.

    Returned by every endpoint whose figures the epoch narrows, so the window is
    part of the same payload as the numbers it applies to — a caller cannot
    render one without having been handed the other.
    """
    record = read(tenant_id)
    if not record:
        return {"active": False, "epoch_ts": None, "since_iso": None, "reason": ""}
    from datetime import datetime, timezone

    ts = float(record["epoch_ts"])
    return {
        "active": True,
        "epoch_ts": ts,
        "since_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "reason": str(record.get("reason") or ""),
    }
