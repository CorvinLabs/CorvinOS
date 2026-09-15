"""ulo_debug_log.py — Runtime debug logging for the ULO subsystem.

Writes NDJSON events to .log/ at the repo root.
Enabled ONLY when CORVIN_ULO_DEBUG=1 — all calls are silent no-ops otherwise.

Privacy contract
----------------
- chat_key is NEVER stored; only a 16-char SHA-256 prefix (``chat_hash``).
- Objective text (user-authored) is NEVER logged.
- Raw LLM output is NEVER logged; only structural parse metadata.
- Exception messages truncated to 120 chars; no stack frames.
- No filenames, paths, or prompt content in any event.

Log files (all mode 0600, append-only NDJSON)
----------------------------------------------
  .log/ulo_compliance.ndjson  — per-objective Haiku call: timing, parse, stats
  .log/ulo_exceptions.ndjson  — swallowed exceptions with site + type
  .log/ulo_crud.ndjson        — CRUD ops: add/delete/pause/resume/update
  .log/ulo_io.ndjson          — file I/O: load/save timing, file-not-found
  .log/ulo_concurrency.ndjson — lock wait times, concurrent-writer events

7-day rotation sweep runs once per process lifetime on the first write.
Must NOT import anthropic (CI AST lint enforces).

Enable with: CORVIN_ULO_DEBUG=1
Override log dir: CORVIN_DEBUG_LOG_DIR=/path/to/.log
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_ENABLED: bool | None = None       # lazily resolved from env
_ROTATION_DONE = False             # run sweep at most once per process
_WRITE_LOCK = threading.Lock()     # in-process serialisation
_RETENTION_DAYS = 7


def _enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = os.environ.get("CORVIN_ULO_DEBUG", "").strip() == "1"
    return _ENABLED


def _repo_root() -> Path:
    """Walk upward from __file__ until we find a repo marker.

    In a PyPI wheel install, __file__ is inside site-packages/_vendor/…
    and the walk never finds a repo marker. In that case, fall back to
    CORVIN_HOME (the user's runtime root) rather than a useless parents[3]
    that would point into site-packages.
    """
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / ".corvin_repo").exists() or (p / "plugins").is_dir():
            return p
    # Wheel/PyPI install: resolve via CORVIN_HOME so logs land in the user's
    # runtime directory (~/.corvin/.log/), not inside site-packages.
    corvin_home = os.environ.get("CORVIN_HOME", "").strip()
    if corvin_home:
        return Path(corvin_home)
    return Path.home() / ".corvin"


def _log_dir() -> Path:
    env = os.environ.get("CORVIN_DEBUG_LOG_DIR", "").strip()
    return Path(env) if env else _repo_root() / ".log"


def _chat_hash(channel: str, chat_key: str) -> str:
    """Return a 16-char SHA-256 prefix — non-reversible, not PII."""
    raw = f"{channel}\x00{chat_key}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _sweep_old_logs(log_dir: Path) -> None:
    """Delete NDJSON files older than _RETENTION_DAYS. Best-effort, once per process."""
    global _ROTATION_DONE
    if _ROTATION_DONE:
        return
    _ROTATION_DONE = True
    cutoff = time.time() - _RETENTION_DAYS * 86400
    try:
        for f in log_dir.glob("*.ndjson"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _write(filename: str, event: dict[str, Any]) -> None:
    """Append one NDJSON line to .log/<filename>. Best-effort, never raises."""
    if not _enabled():
        return
    try:
        d = _log_dir()
        with _WRITE_LOCK:
            d.mkdir(parents=True, exist_ok=True)
            _sweep_old_logs(d)
            p = d / filename
            line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            fd = os.open(p, flags, 0o600)
            # Ensure correct mode for pre-existing files too (os.open mode
            # is only applied at creation, not on open of existing file).
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
            fd_owned = False  # fdopen hasn't taken ownership yet
            try:
                with os.fdopen(fd, "a", encoding="utf-8", closefd=True) as fh:
                    fd_owned = True  # fdopen took fd ownership
                    fh.write(line)
            except Exception:
                if not fd_owned:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                raise
    except Exception:  # noqa: BLE001
        pass


# ── Public logging API ────────────────────────────────────────────────────

def log_compliance_call(
    channel: str,
    chat_key: str,
    *,
    obj_id: str,
    subprocess_ms: int,
    timed_out: bool,
    exit_code: int | None,
    raw_len: int,
    parse_ok: bool,
    compliant: bool | None,
    confidence: float | None,
    reason_code: str | None,
    compliance_rate_new: float | None,
    turns_checked: int,
    consecutive_failures: int,
) -> None:
    """Log one Haiku compliance check: subprocess + parse + stats in one event."""
    _write("ulo_compliance.ndjson", {
        "ts":                   time.time(),
        "event":                "compliance_call",
        "chat_hash":            _chat_hash(channel, chat_key),
        "obj_id":               obj_id,
        "subprocess_ms":        subprocess_ms,
        "timed_out":            timed_out,
        "exit_code":            exit_code,
        "raw_len":              raw_len,
        "parse_ok":             parse_ok,
        "compliant":            compliant,
        "confidence":           confidence,
        "reason_code":          reason_code,
        "compliance_rate_new":  compliance_rate_new,
        "turns_checked":        turns_checked,
        "consecutive_failures": consecutive_failures,
    })


def log_lock_wait(
    channel: str,
    chat_key: str,
    *,
    wait_ms: int,
    active_count: int,
) -> None:
    """Log how long a thread waited to acquire the per-chat compliance lock."""
    _write("ulo_concurrency.ndjson", {
        "ts":           time.time(),
        "event":        "lock_acquired",
        "chat_hash":    _chat_hash(channel, chat_key),
        "wait_ms":      wait_ms,
        "active_count": active_count,
    })


def log_exception(
    site: str,
    exc: BaseException,
    *,
    channel: str = "",
    chat_key: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Log a swallowed exception — the primary blind spot in static review."""
    _write("ulo_exceptions.ndjson", {
        "ts":        time.time(),
        "event":     "swallowed_exception",
        "site":      site,
        "exc_type":  type(exc).__name__,
        "exc_msg":   str(exc)[:120],
        "chat_hash": _chat_hash(channel, chat_key) if channel else "",
        **(extra or {}),
    })


def log_crud(
    op: str,
    channel: str,
    chat_key: str,
    *,
    obj_id: str = "",
    found: bool | None = None,
    scope: str = "",
    priority: str = "",
    check_trigger: str = "",
    active_count_after: int | None = None,
) -> None:
    """Log a CRUD operation — reveals which operations are dead code at runtime."""
    _write("ulo_crud.ndjson", {
        "ts":                time.time(),
        "event":             f"crud.{op}",
        "chat_hash":         _chat_hash(channel, chat_key),
        "obj_id":            obj_id,
        "found":             found,
        "scope":             scope,
        "priority":          priority,
        "check_trigger":     check_trigger,
        "active_count_after": active_count_after,
    })


def log_io(
    op: str,
    channel: str,
    chat_key: str,
    *,
    duration_ms: int,
    file_existed: bool | None = None,
    obj_count: int | None = None,
    error: str = "",
) -> None:
    """Log file I/O operations — reveals actual disk latency and error rates."""
    _write("ulo_io.ndjson", {
        "ts":           time.time(),
        "event":        f"io.{op}",
        "chat_hash":    _chat_hash(channel, chat_key),
        "duration_ms":  duration_ms,
        "file_existed": file_existed,
        "obj_count":    obj_count,
        "error":        error,
    })


__all__ = [
    "log_compliance_call",
    "log_lock_wait",
    "log_exception",
    "log_crud",
    "log_io",
]
