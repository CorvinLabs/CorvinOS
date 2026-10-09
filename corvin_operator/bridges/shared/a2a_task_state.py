"""Layer 38 — receiver-side task stage store (ADR-2242).

The receiver records how far each inbound A2A task has come
(``delivered → accepted → processing → completed|failed|rejected|timeout``) so the
SENDER can ask — with a signed, content-free ``task_id`` ping — instead of
waiting blind for the one final answer.

Invariants
----------

* **Monotonic.** A stage only moves forward by rank; a terminal stage is
  immutable. A replayed or reordered event can never regress a task.
* **Origin-bound.** :func:`lookup` answers only for the origin that sent the
  task; a foreign or unknown ``task_id`` is indistinguishable from "never seen"
  (no enumeration oracle).
* **Content-free.** Only ``{stage, stage_seq, updated_at, reason}`` is stored —
  never instruction text, worker output or attachments.
* **Restart-honest.** A task persisted as ``accepted``/``processing`` by an
  earlier process has no live worker any more; it is closed as
  ``failed(reason=restart)`` on first read, never left "processing" forever.
* **Best-effort.** Recording never raises into the A2A path.

Layout: ``<tenant>/global/a2a_feed/task_state.jsonl`` (append-only; the last line
per ``(origin_id, task_id)`` wins), dir 0o700 / file 0o600. Retention follows the
feed (``a2a_feed.RETENTION_DAYS``); GDPR erasure rides ``a2a_feed.erase_peer``.

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

#: stage -> rank. Equal rank = terminal siblings: the first to arrive wins.
STAGE_RANK: dict[str, int] = {
    "delivered": 2, "accepted": 3, "processing": 4,
    "completed": 5, "failed": 5, "rejected": 5, "timeout": 5,
}
TERMINAL = frozenset(s for s, r in STAGE_RANK.items() if r == 5)
#: closed reason vocabulary (audit-safe: no free text ever reaches the chain)
REASONS = frozenset({
    "", "busy", "restart", "injection", "gate", "worker_error", "timeout", "group_refused",
    # WHY the worker path refused a task (a2a_worker.WorkerResult.reason_code)
    "data_flow", "gate_error", "egress", "house_rules", "house_rules_unavailable", "quota",
    "license", "attachments", "engine_unavailable", "engine_failed", "engine_error", "refused",
})

_FILE = "task_state.jsonl"
_MAX_LIVE = 4000            # in-memory entries; oldest evicted
_MAX_FILE_BYTES = 4 * 1024 * 1024
_ID_MAX = 128

_lock = threading.RLock()
_mem: dict[tuple[str, str], dict[str, Any]] = {}
_loaded_dirs: set[str] = set()


def _root(tenant_id: str | None = None) -> Path:
    import a2a_feed  # noqa: PLC0415 — same dir/lock/retention as the feed
    return a2a_feed.feed_dir(tenant_id)


def _key(origin_id: Any, task_id: Any) -> tuple[str, str] | None:
    if not isinstance(origin_id, str) or not isinstance(task_id, str):
        return None
    o, t = origin_id[:_ID_MAX], task_id[:_ID_MAX]
    return (o, t) if o and t else None


def _load(root: Path) -> None:
    """Fold the file into memory once per process; close orphans of a dead process."""
    rk = str(root)
    if rk in _loaded_dirs:
        return
    _loaded_dirs.add(rk)
    path = root / _FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    cutoff = time.time() - _retention_days() * 86400
    for line in lines:
        try:
            r = json.loads(line)
            k = _key(r.get("origin_id"), r.get("task_id"))
            st = r.get("stage")
            if k is None or st not in STAGE_RANK or float(r.get("updated_at") or 0) < cutoff:
                continue
            cur = _mem.get(k)
            if cur is None or _advances(cur["stage"], st):
                _mem[k] = {"stage": st, "stage_seq": int(r.get("stage_seq") or 0),
                           "updated_at": float(r["updated_at"]),
                           "reason": r.get("reason") if r.get("reason") in REASONS else "",
                           "boot": False}
        except (ValueError, TypeError, KeyError):
            continue
    # Anything still in flight was owned by a previous process: its worker is gone.
    for k, e in list(_mem.items()):
        if not e.get("boot") and e["stage"] not in TERMINAL:
            e.update(stage="failed", reason="restart", updated_at=time.time(),
                     stage_seq=e["stage_seq"] + 1, boot=False)
            _persist(root, k, e)


def _retention_days() -> int:
    try:
        import a2a_feed  # noqa: PLC0415
        return int(a2a_feed.RETENTION_DAYS)
    except Exception:  # noqa: BLE001
        return 30


def _advances(cur: str, new: str) -> bool:
    if cur in TERMINAL:
        return False
    return STAGE_RANK[new] > STAGE_RANK.get(cur, 0)


def _persist(root: Path, k: tuple[str, str], e: dict[str, Any]) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / _FILE
        if path.exists() and path.stat().st_size > _MAX_FILE_BYTES:
            _compact(path)
        line = json.dumps({"origin_id": k[0], "task_id": k[1], "stage": e["stage"],
                           "stage_seq": e["stage_seq"], "updated_at": e["updated_at"],
                           "reason": e.get("reason", "")}) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 — never into the A2A path
        pass


def _compact(path: Path) -> None:
    """Rewrite with only the live entries (atomic replace)."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for (o, t), e in _mem.items():
            fh.write(json.dumps({"origin_id": o, "task_id": t, "stage": e["stage"],
                                 "stage_seq": e["stage_seq"], "updated_at": e["updated_at"],
                                 "reason": e.get("reason", "")}) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def record_stage(origin_id: str, task_id: str, stage: str, reason: str = "",
                 *, tenant_id: str | None = None, create: bool = True) -> dict[str, Any] | None:
    """Advance ``task_id`` to ``stage``. Returns ``{prev, stage, stage_seq}`` when the
    stage changed, ``None`` when it was dropped (regression, terminal, unknown, bad id).

    ``create=False`` only advances a task that is already known: the pre-authentication
    reject paths use it so an unauthenticated sender can never make this store grow."""
    try:
        k = _key(origin_id, task_id)
        if k is None or stage not in STAGE_RANK:
            return None
        reason = reason if reason in REASONS else ""
        root = _root(tenant_id)
        with _lock:
            _load(root)
            cur = _mem.get(k)
            if cur is None and not create:
                return None
            if cur is not None and not _advances(cur["stage"], stage):
                return None
            prev = cur["stage"] if cur else ""
            e = {"stage": stage, "stage_seq": (cur["stage_seq"] + 1) if cur else 1,
                 "updated_at": time.time(), "reason": reason, "boot": True}
            _mem[k] = e
            if len(_mem) > _MAX_LIVE:
                for old in sorted(_mem, key=lambda x: _mem[x]["updated_at"])[: len(_mem) - _MAX_LIVE]:
                    _mem.pop(old, None)
            _persist(root, k, e)
            return {"prev": prev, "stage": stage, "stage_seq": e["stage_seq"]}
    except Exception:  # noqa: BLE001
        return None


def lookup(origin_id: str, task_id: str, *, tenant_id: str | None = None) -> dict[str, Any]:
    """Stage of ``task_id`` as seen by THIS receiver for ``origin_id``.

    Unknown, foreign and malformed ids all answer ``{"stage": "unknown"}``."""
    try:
        k = _key(origin_id, task_id)
        if k is None:
            return {"stage": "unknown"}
        root = _root(tenant_id)
        with _lock:
            _load(root)
            e = _mem.get(k)
            if e is None:
                return {"stage": "unknown"}
            return {"stage": e["stage"], "stage_seq": e["stage_seq"],
                    "updated_at": int(e["updated_at"]), "reason": e.get("reason", "")}
    except Exception:  # noqa: BLE001
        return {"stage": "unknown"}


_answered: dict[tuple[str, str], str] = {}


def note_query(origin_id: str, task_id: str, stage: str) -> bool:
    """True when this answer differs from the previous one for the task (so a poller
    asking every few seconds is audited once per stage change, not once per poll)."""
    k = _key(origin_id, task_id)
    if k is None:
        return False
    with _lock:
        changed = _answered.get(k) != stage
        _answered[k] = stage
        if len(_answered) > _MAX_LIVE:
            for old in list(_answered)[: len(_answered) - _MAX_LIVE]:
                _answered.pop(old, None)
        return changed


def erase_origin(origin_id: str, *, tenant_id: str | None = None) -> int:
    """GDPR Art. 17: drop every task state of one origin (memory + file)."""
    try:
        root = _root(tenant_id)
        with _lock:
            _load(root)
            gone = [k for k in _mem if k[0] == origin_id]
            for k in gone:
                _mem.pop(k, None)
            for k in [k for k in _answered if k[0] == origin_id]:
                _answered.pop(k, None)
            path = root / _FILE
            if path.exists():
                _compact(path)
            return len(gone)
    except Exception:  # noqa: BLE001
        return 0


def clear(*, tenant_id: str | None = None) -> int:
    try:
        root = _root(tenant_id)
        with _lock:
            n = len(_mem)
            _mem.clear()
            _answered.clear()
            _loaded_dirs.discard(str(root))
            try:
                (root / _FILE).unlink()
            except FileNotFoundError:
                pass
            return n
    except Exception:  # noqa: BLE001
        return 0


def _reset_for_tests() -> None:
    with _lock:
        _mem.clear()
        _answered.clear()
        _loaded_dirs.clear()


__all__ = ["STAGE_RANK", "TERMINAL", "REASONS", "record_stage", "lookup",
           "erase_origin", "clear", "note_query"]
