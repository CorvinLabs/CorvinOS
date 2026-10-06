"""SessionContextBridge (PLAN-0932, ADR-2101 P4 task/goal half, ADR-0865).

ADR-2101's 2026-10-02 amendment confirmed no ``SessionContextBridge`` was
ever built, and neither existing ``ContextBridge`` class
(``core/orchestration/subsystems/``, ``core/skills/os_skills/phase1/``) is
reachable or persists anything. This module is a from-scratch
implementation of ADR-0865's design, corrected to this repo's own
conventions — ADR-0865's own code sample does not follow them:

1. No hardcoded ``~/.corvin`` — resolved via ``tenant_global_dir(tenant_id)``.
2. No direct ``open(audit.jsonl, 'a')`` — ``forge.security_events.write_event``
   via ``tenant_audit_chain(tenant_id)``, audit-FIRST: the chain write must
   commit before the snapshot file is touched.
3. No ``SessionManager(...).suspend()`` — that class has no production
   callers and no such method; this module is invoked from the REAL
   session-boundary call sites instead (``reset_claude_session_state`` on
   the bridge, ``delete_session`` on the console).
4. One storage path, not two conflicting ones from the ADR's own text:
   ``tenant_global_dir(tenant_id) / "session_context_bridge" / f"{task_id}.json"``.

``task_registry_snapshot`` from ADR-0865's schema is deliberately dropped —
the task registry (ADR-0760) is already the durable source of completion
state; duplicating it here would recreate the exact "two sources disagree"
failure ADR-2101 exists to name and stop.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("corvin.session_context_bridge")

_SUBDIR = "session_context_bridge"


@dataclass(frozen=True)
class ContextSnapshot:
    """Opaque task/goal state captured at a session boundary.

    ``task_state`` / ``ldd_state`` / ``learning_events`` are caller-supplied
    and opaque to the bridge by design (ADR-0865) — this module only owns
    getting them to disk and back safely, never their shape.
    """

    task_id: str
    snapshot_timestamp: str  # ISO-8601 UTC
    session_id: str
    tenant_id: str
    task_state: dict = field(default_factory=dict)
    ldd_state: dict = field(default_factory=dict)
    learning_events: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ContextSnapshot":
        return cls(
            task_id=str(data["task_id"]),
            snapshot_timestamp=str(data["snapshot_timestamp"]),
            session_id=str(data.get("session_id") or ""),
            tenant_id=str(data.get("tenant_id") or "_default"),
            task_state=dict(data.get("task_state") or {}),
            ldd_state=dict(data.get("ldd_state") or {}),
            learning_events=list(data.get("learning_events") or []),
        )


def _snapshot_path(tenant_id: str, task_id: str) -> Path:
    from forge.paths import tenant_global_dir  # noqa: PLC0415
    return tenant_global_dir(tenant_id) / _SUBDIR / f"{task_id}.json"


class SessionContextBridge:
    """Snapshot / restore task+goal state across a session boundary.

    Every method is fail-closed toward "nothing happened": a snapshot that
    can't be audited is never written, and a restore that can't be parsed
    returns ``None`` rather than raising — a turn must never break because
    this bridge had a bad day (same discipline as every other CEL hook in
    this repo, e.g. P3's ``goal_drift_hook``).
    """

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    def snapshot(self, task_id: str, context: ContextSnapshot) -> bool:
        """Write ``context`` for ``task_id``. Returns whether it was written.

        Audit-FIRST (ADR-2101 / CLAUDE.md "audit chain as ground truth"):
        the hash-chain write must commit before the file is touched — a
        chain that can't accept the record must leave no on-disk trace of
        it either, mirroring ``a2a_feed.py``'s 503-on-audit-failure pattern.
        """
        if not task_id:
            return False
        try:
            from forge.paths import tenant_audit_chain  # noqa: PLC0415
            from forge.security_events import write_event  # noqa: PLC0415
            write_event(
                tenant_audit_chain(self.tenant_id), "context_bridge.snapshot_created",
                tool="session_context_bridge",
                details={"tenant_id": self.tenant_id, "task_id": task_id,
                         "session_id": context.session_id},
            )
        except Exception as exc:  # noqa: BLE001 — audit-first: no commit, no file
            logger.error(
                "context_bridge: snapshot AUDIT-WRITE FAILED (tenant %s, task %s): %s "
                "— snapshot NOT written", self.tenant_id, task_id, type(exc).__name__,
            )
            return False
        try:
            path = _snapshot_path(self.tenant_id, task_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps(context.to_dict(), ensure_ascii=False)
            fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(body)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return True
        except Exception:  # noqa: BLE001 — never raise into the caller's reset/cleanup path
            logger.exception("context_bridge: snapshot file write failed (tenant %s, task %s)",
                             self.tenant_id, task_id)
            return False

    def restore(self, task_id: str) -> "ContextSnapshot | None":
        """Return the snapshot for ``task_id``, or ``None``.

        NEVER raises: a missing file, corrupt JSON, or malformed schema all
        degrade to "start fresh" (ADR-0865's own constraint), each audited
        under its own ``reason`` so the distinction is visible without ever
        blocking a turn.
        """
        if not task_id:
            return None
        path = _snapshot_path(self.tenant_id, task_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            snap = ContextSnapshot.from_dict(raw)
        except Exception:  # noqa: BLE001 — corrupt or malformed: degrade, don't raise
            self._audit_restore_failed(task_id, "corrupt")
            return None
        try:
            from forge.paths import tenant_audit_chain  # noqa: PLC0415
            from forge.security_events import write_event  # noqa: PLC0415
            write_event(
                tenant_audit_chain(self.tenant_id), "context_bridge.restored",
                tool="session_context_bridge",
                details={"tenant_id": self.tenant_id, "task_id": task_id,
                         "session_id": snap.session_id},
            )
        except Exception as exc:  # noqa: BLE001 — advisory: the restore still succeeds
            logger.error(
                "context_bridge: restore AUDIT-WRITE FAILED (tenant %s, task %s): %s",
                self.tenant_id, task_id, type(exc).__name__,
            )
        return snap

    def _audit_restore_failed(self, task_id: str, reason: str) -> None:
        try:
            from forge.paths import tenant_audit_chain  # noqa: PLC0415
            from forge.security_events import write_event  # noqa: PLC0415
            write_event(
                tenant_audit_chain(self.tenant_id), "context_bridge.restore_failed",
                severity="WARNING", tool="session_context_bridge",
                details={"tenant_id": self.tenant_id, "task_id": task_id, "reason": reason},
            )
        except Exception:  # noqa: BLE001 — never raise out of a failure-reporting path
            logger.exception("context_bridge: restore_failed AUDIT-WRITE FAILED too "
                             "(tenant %s, task %s, reason %s)", self.tenant_id, task_id, reason)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_FLAG_ID = "session_context_bridge_enabled"


def _is_enabled(tenant_id: str) -> bool:
    """Re-checked on every call (fail-closed), same discipline as P3's
    ``goal_drift_hook._is_enabled`` — flag off or unreadable -> False."""
    try:
        from corvin_core import feature_flags as _ff  # noqa: PLC0415
        return bool(_ff.is_enabled(_FLAG_ID, tenant_id))
    except Exception:  # noqa: BLE001 — no flag subsystem -> off (ship-dark)
        return False


def maybe_snapshot_context(
    tenant_id: str, task_id: "str | None", session_id: str,
    *, task_state: dict | None = None, ldd_state: dict | None = None,
    learning_events: list | None = None,
) -> bool:
    """Flag-gated snapshot hook for the real session-boundary call sites
    (``reset_claude_session_state`` on the bridge, ``delete_session`` on
    the console). Returns whether a snapshot was actually written. NEVER
    raises — a broken bridge must never break a reset/cleanup path."""
    try:
        if not task_id or not _is_enabled(tenant_id):
            return False
        snap = ContextSnapshot(
            task_id=task_id, snapshot_timestamp=utc_now_iso(), session_id=session_id,
            tenant_id=tenant_id, task_state=task_state or {}, ldd_state=ldd_state or {},
            learning_events=learning_events or [],
        )
        return SessionContextBridge(tenant_id).snapshot(task_id, snap)
    except Exception:  # noqa: BLE001 — never raise into the caller's reset/cleanup path
        logger.exception("context_bridge: maybe_snapshot_context unexpected error")
        return False


def maybe_restore_context(tenant_id: str, task_id: "str | None") -> "ContextSnapshot | None":
    """Flag-gated restore hook for the start of a new session that carries
    a ``task_id`` matching an existing snapshot. NEVER raises."""
    try:
        if not task_id or not _is_enabled(tenant_id):
            return None
        return SessionContextBridge(tenant_id).restore(task_id)
    except Exception:  # noqa: BLE001 — never raise into the caller's turn-start path
        logger.exception("context_bridge: maybe_restore_context unexpected error")
        return None
