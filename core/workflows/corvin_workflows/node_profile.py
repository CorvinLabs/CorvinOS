"""The per-node measurement stream and its report (ADR-2308 decision 5, T-0329).

One entry per spawn of an ``agent`` / ``fan_out`` / ``route(mode: classify)`` node (per item for
``fan_out``), written to
``<tenant_home>/learning/workflow_node_profile.YYYY-MM-DD.jsonl``. It is a derived measurement stream,
NOT an audit chain and not a learning event: the decision is on the chain (``workflow.node_model_resolved``),
the token parts are on the span (``engine.span.end``); the two are joined by ``span_id``. ``cost_usd`` comes
from the CLI envelope and is in neither of them, so the profile cannot be rebuilt from them and proves nothing.

What it holds is deliberately small and closed: ids, a node class (``type|tools|ctx|contract``), the model the
CLI reported, the four billed token parts, the cost and ``ok``/``error``. No prompt, no agent name, no node id
and no free text. Every entry is ``signal: proxy`` (a spawn that did not fail says nothing about whether the
answer was right), so it can feed cost and error-rate reports but must NEVER decide a model switch —
:func:`gate_eligible` is the one filter a gate has to go through.

Write discipline: daily segment files, appended with ``O_APPEND|O_NOFOLLOW`` under a lock (several processes
write: console, ``corvin-workflows`` with the scheduler, the MCP server); ``mkstemp`` only creates a new
segment; retention (90 days) removes WHOLE segments and never rewrites a line.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

_log = logging.getLogger("corvin_workflows.node_profile")

SEGMENT_PREFIX = "workflow_node_profile."
SEGMENT_SUFFIX = ".jsonl"
RETENTION_DAYS = 90
MIN_RUNS = 30                       # per (node class, model): fewer runs -> the report says "underpowered"
GATE_SIGNALS = frozenset({"oracle", "oracle_shadow"})   # proxy is never one of them

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")      # no "/" (a path), no "@" (an address), no space
_SAFE_CLASS = re.compile(r"^[a-z_]{1,32}\|[tn]\|(<8k|8k-32k|>32k|)\|json$")
_last_prune: dict[str, str] = {}


# ── location ────────────────────────────────────────────────────────────────

def profile_dir(tenant_id: str) -> Path:
    import forge.paths as _fp  # type: ignore[import-not-found]
    return Path(_fp.tenant_home(tenant_id)) / "learning"


def _segment_path(directory: Path, day: _dt.date) -> Path:
    return directory / f"{SEGMENT_PREFIX}{day.isoformat()}{SEGMENT_SUFFIX}"


def _segment_day(path: Path) -> Optional[_dt.date]:
    name = path.name
    if not (name.startswith(SEGMENT_PREFIX) and name.endswith(SEGMENT_SUFFIX)):
        return None
    try:
        return _dt.date.fromisoformat(name[len(SEGMENT_PREFIX):-len(SEGMENT_SUFFIX)])
    except ValueError:
        return None


# ── entry ───────────────────────────────────────────────────────────────────

def _safe_id(value: Any) -> str:
    """A closed id vocabulary. Anything else (an e-mail shape, a sentence, a path) becomes empty:
    this stream must never carry text a person wrote."""
    text = str(value or "")
    return text if _SAFE_ID.match(text) else ""


def _count(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else 0


def build_entry(meta: dict[str, Any], node_class: str, status: str, *, now: Optional[float] = None) -> dict[str, Any]:
    """The entry for one finished spawn, built ONLY from closed fields of ``meta``."""
    usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
    cost = usage.get("cost_usd")
    entry: dict[str, Any] = {
        "ts": round(float(now if now is not None else time.time()), 3),
        "rid": _safe_id(meta.get("run_id")),
        "span_id": _safe_id(meta.get("span_id")),
        "node_class": node_class if _SAFE_CLASS.match(node_class or "") else "",
        "model_id": _safe_id(usage.get("model_id")),
        **{k: _count(usage.get(k)) for k in _TOKEN_FIELDS},
        "cost_usd": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else None,
        "status": "ok" if status == "ok" else "error",
        "signal": "proxy",
    }
    if meta.get("item_index") is not None:
        entry["item_index"] = _count(meta.get("item_index"))
    return entry


# ── write ───────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _locked(fd: int) -> Iterator[None]:
    """An exclusive lock on the segment for the length of one append (POSIX ``flock``; the Windows
    ``msvcrt`` byte lock; no lock at all only where neither exists)."""
    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore[assignment]
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
        return
    try:
        import msvcrt  # type: ignore[import-not-found]
    except ImportError:
        yield
        return
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    try:
        yield
    finally:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _ensure_segment(seg: Path) -> None:
    """Create an empty segment atomically if it is absent: ``mkstemp`` in the same directory, then a hard
    link to the final name (fails if another process won the race, which is fine). Never replaces a file."""
    if seg.exists() or seg.is_symlink():
        return
    fd, tmp = tempfile.mkstemp(prefix=".seg-", dir=str(seg.parent))
    os.close(fd)
    try:
        os.link(tmp, seg)
    except FileExistsError:
        pass
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def append(tenant_id: str, entry: dict[str, Any], *, now: Optional[float] = None) -> Path:
    """Append one line. Raises ``OSError`` if the segment is a symlink (``O_NOFOLLOW``) or cannot be written."""
    ts = now if now is not None else float(entry.get("ts") or time.time())
    day = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).date()
    directory = profile_dir(tenant_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    seg = _segment_path(directory, day)
    _ensure_segment(seg)
    line = (json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(seg, flags, 0o600)
    try:
        with _locked(fd):
            os.write(fd, line)
    finally:
        os.close(fd)
    # Retention counts from the real today, never from the entry's own day: a back-dated entry must not
    # move the cut-off, and one older than the retention window is simply not kept.
    _prune_once_a_day(tenant_id, directory, _dt.datetime.now(_dt.timezone.utc).date())
    return seg


def _prune_once_a_day(tenant_id: str, directory: Path, today: _dt.date) -> None:
    if _last_prune.get(tenant_id) == today.isoformat():
        return
    _last_prune[tenant_id] = today.isoformat()
    try:
        prune(directory, today)
    except OSError as exc:  # retention is housekeeping; it never fails a spawn
        _log.warning("[node-profile] retention pass failed (%s)", type(exc).__name__)


def prune(directory: Path, today: _dt.date, days: int = RETENTION_DAYS) -> list[Path]:
    """Remove segments older than ``days``. Whole files only; a line is never rewritten."""
    removed: list[Path] = []
    for path in sorted(Path(directory).glob(f"{SEGMENT_PREFIX}*{SEGMENT_SUFFIX}")):
        day = _segment_day(path)
        if day is not None and (today - day).days > days and path.is_file() and not path.is_symlink():
            path.unlink()
            removed.append(path)
    return removed


def record(tenant_id: str, meta: dict[str, Any], prompt_len: int, status: str) -> None:
    """Write the entry for one finished spawn of an in-scope node. Best effort: a failure is logged and
    never decides the node's outcome. Out-of-scope spawns (no resolution in ``meta``) write nothing."""
    if meta.get("model_resolution") is None:
        return
    try:
        from .model_resolution import node_class as _node_class
        entry = build_entry(meta, _node_class(meta.get("class_view") or {}, prompt_len), status)
        append(tenant_id, entry)
    except Exception as exc:  # noqa: BLE001
        _log.warning("[node-profile] entry not written (%s)", type(exc).__name__)


# ── read + report ───────────────────────────────────────────────────────────

def read_entries(tenant_id: str) -> list[dict[str, Any]]:
    """All valid lines of all segments, oldest first. A damaged line is skipped, not fatal."""
    out: list[dict[str, Any]] = []
    directory = profile_dir(tenant_id)
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob(f"{SEGMENT_PREFIX}*{SEGMENT_SUFFIX}")):
        if _segment_day(path) is None or path.is_symlink():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def gate_eligible(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The only entries a model-switch gate may use: those with a success signal that is not a proxy.
    Every entry this module writes is ``proxy``, so today this returns nothing — by construction."""
    return [e for e in entries if e.get("signal") in GATE_SIGNALS]


def loss_report(tenant_id: str, *, min_runs: int = MIN_RUNS) -> dict[str, Any]:
    """Cost and error rate per (node class, reported model). No loss value: the production stream has no
    success signal (every entry is a proxy), so ``loss`` is ``None`` and says why."""
    entries = read_entries(tenant_id)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in entries:
        groups.setdefault((str(e.get("node_class", "")), str(e.get("model_id", ""))), []).append(e)
    rows = []
    for (cls, model), rs in sorted(groups.items()):
        costs = [r["cost_usd"] for r in rs if isinstance(r.get("cost_usd"), (int, float))]
        tokens = [sum(_count(r.get(k)) for k in _TOKEN_FIELDS) for r in rs]
        rows.append({
            "node_class": cls, "model_id": model, "runs": len(rs),
            "error_rate": round(sum(1 for r in rs if r.get("status") != "ok") / len(rs), 4),
            "mean_cost_usd": round(sum(costs) / len(costs), 6) if costs else None,
            "mean_tokens": round(sum(tokens) / len(tokens), 1),
            "underpowered": len(rs) < min_runs,
        })
    if not entries:
        status = "no_data"
    elif all(r["underpowered"] for r in rows):
        status = "underpowered"
    else:
        status = "ok"
    return {
        "status": status, "entries": len(entries), "min_runs": min_runs, "classes": rows,
        "loss": None,
        "loss_note": "no success signal in the production stream (every entry is a proxy); a loss needs the "
                     "offline oracle (ADR-2307) — see gate_eligible()",
    }
