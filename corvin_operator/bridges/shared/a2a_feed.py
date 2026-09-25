"""Layer 38 — A2A message feed (local content store for the Agent Hub).

The audit chain is deliberately metadata-only for A2A (instruction text,
worker output and attachment bytes NEVER enter it — ADR-0048). That leaves
the operator with no way to *read* what their agent and its peers actually
exchanged. This module is that missing view: a tenant-local store of the
authenticated A2A messages this instance sent or received, with their
attachments, which the console's Agent Hub renders as a live chat feed.

What is recorded
----------------

* **Outbound** (``RemoteTriggerSender.send``): the task this instance sent
  and the verified response it got back (or the failure status).
* **Inbound** (``RemoteTriggerReceiver.receive``): only envelopes that passed
  HMAC, nonce, TTL, consent and chain-integrity checks — an unauthenticated
  sender can never write into this store — plus the signed response.

Layout (``<tenant>/global/a2a_feed/``, dir 0o700, files 0o600)::

    messages.jsonl       one JSON record per message, append-only
    blobs/<sha256>       attachment bytes, content-addressed (dedup)

Every record links to the audit chain by ``task_id`` — the chain stays the
proof, this store is the readable view of the same exchange.

Retention (GDPR Art. 5(1)(e))
-----------------------------

Records older than ``RETENTION_DAYS`` and anything beyond
``MAX_FEED_BYTES`` are compacted away; unreferenced blobs are removed in
the same pass. The operator can wipe the store at any time
(:func:`clear`), which the console audits as ``A2A.feed_cleared``.

Failure mode
------------

Recording is best-effort and NEVER raises into the A2A path: a full disk
or a permission problem must not turn a delivered message into a failure.

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl  # POSIX only; the store degrades to a thread lock without it
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

FEED_DIRNAME = "a2a_feed"
RETENTION_DAYS = 30
MAX_FEED_BYTES = 32 * 1024 * 1024
# Attachment bytes are budgeted separately: a record line is ~500 B but can
# reference 1 MiB of blobs, so the line cap alone let blobs/ grow to tens of
# GB within the retention window (2026-09-25, round 2).
MAX_BLOB_BYTES = 512 * 1024 * 1024
MAX_TEXT_CHARS = 64_000
MAX_DATA_BYTES = 256 * 1024
_COMPACT_INTERVAL_S = 3600.0

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_lock = threading.Lock()
_last_compact: dict[str, float] = {}


# ── paths ─────────────────────────────────────────────────────────────────

def _load_paths():
    # Same file-path load audit.py uses: corvin_operator/forge/paths.py is a
    # stub without tenant helpers and may shadow this one on sys.path.
    spec = importlib.util.spec_from_file_location(
        "_paths_a2a_feed", Path(__file__).resolve().parent / "paths.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("paths.py not loadable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def feed_dir(tenant_id: str | None = None) -> Path:
    override = os.environ.get("CORVIN_A2A_FEED_DIR")
    if override:
        return Path(override)
    return _load_paths().tenant_global_dir(tenant_id) / FEED_DIRNAME


def _ensure_dir(root: Path) -> None:
    (root / "blobs").mkdir(parents=True, exist_ok=True)
    for d in (root, root / "blobs"):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


# ── attachments ───────────────────────────────────────────────────────────

def _att_fields(att: Any) -> tuple[str, str, str, str]:
    if isinstance(att, dict):
        return (str(att.get("name", "")), str(att.get("mime", "")),
                str(att.get("sha256", "")), str(att.get("content_b64", "")))
    return (str(getattr(att, "name", "")), str(getattr(att, "mime", "")),
            str(getattr(att, "sha256", "")), str(getattr(att, "content_b64", "")))


@contextlib.contextmanager
def _store_lock(root: Path):
    """Serialise every mutation of the store — across threads AND processes.

    One sidecar lock file (never replaced, unlike ``messages.jsonl``) guards
    blob writes, the append, ``seq`` allocation, compaction and clear. The
    gateway (receiver), the bridge adapter, the MCP server and the CLI
    (senders) are separate processes writing the same store; locking the data
    file itself was unsafe because compaction REPLACES it — a writer that had
    opened the old inode appended into a deleted file (2026-09-25 review).
    """
    with _lock:
        fd = os.open(root / "store.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _store_attachments(root: Path, attachments: Iterable[Any] | None) -> list[dict]:
    """Caller holds the store lock."""
    out: list[dict] = []
    for att in attachments or []:
        name, mime, _declared, b64 = _att_fields(att)
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            continue
        # Content-address by the digest WE compute: never trust a declared
        # digest as a filename.
        sha = hashlib.sha256(raw).hexdigest()
        blob = root / "blobs" / sha
        if not blob.exists():
            _write_atomic(blob, raw)
            _blob_bytes(root, added=len(raw))
        out.append({"name": name[:128], "mime": mime[:128],
                    "size": len(raw), "sha256": sha})
    return out


def blob_path(sha256: str, tenant_id: str | None = None) -> Path | None:
    """Path of a stored attachment, or None for a malformed/missing digest."""
    if not _SHA_RE.match(sha256 or ""):
        return None
    p = feed_dir(tenant_id) / "blobs" / sha256
    return p if p.is_file() else None


# ── write ─────────────────────────────────────────────────────────────────

def _clip_data(data: Any) -> dict:
    if not isinstance(data, dict):
        return {}
    try:
        encoded = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        return {}
    if len(encoded.encode("utf-8")) <= MAX_DATA_BYTES:
        return json.loads(encoded)
    return {"_truncated": True, "preview": encoded[:MAX_DATA_BYTES // 4]}


def _next_seq(root: Path) -> int:
    """Monotonic per-store sequence number. Caller holds the store lock.

    The read cursor. Wall-clock ``ts`` is assigned per writer and can land out
    of append order across threads/processes (a slow writer with an earlier
    ts appends after a fast one), so a ``since=ts`` cursor skipped messages
    for good. ``seq`` is allocated under the same lock as the append, so
    append order == seq order by construction. It survives compaction and
    clear (never reused, never lowered), so a client cursor stays valid.
    """
    path = root / "seq"
    try:
        cur = int(path.read_text(encoding="ascii").strip() or 0)
    except (OSError, ValueError):
        cur = 0
    if not path.exists():
        # First allocation on a store that predates seq: continue above any
        # seq already present (and above ids handed out via overrides).
        cur = max([cur] + [int(r.get("seq") or 0) for r in _iter_records(root / "messages.jsonl")]
                  + list(_load_overrides(root).values()))
    nxt = cur + 1
    _write_atomic(path, str(nxt).encode("ascii"))
    return nxt


# ── records without seq (legacy / rolling-restart writers) ─────────────────
#
# Records written by the first store version, or appended by a process that
# still runs it during a rolling restart, carry no ``seq``. They get one from
# the SAME counter, recorded in a sidecar map (record id -> seq) under the
# store lock. ``messages.jsonl`` is NEVER rewritten for this (round 4: a
# read-path rewrite replaced the file while old-version writers, which lock
# only the data file, were appending: their records went into the unlinked
# inode). compact() folds the map into the records it rewrites anyway.

def _load_overrides(root: Path) -> dict[str, int]:
    try:
        data = json.loads((root / "seq_overrides.json").read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _apply_overrides(records: list[dict], overrides: dict[str, int]) -> list[dict]:
    for r in records:
        if not r.get("seq") and str(r.get("id")) in overrides:
            r["seq"] = overrides[str(r.get("id"))]
    return records


def _assign_missing_seq(root: Path) -> list[dict]:
    """Give every seq-less record (in file = append order) a seq from the
    counter, persisted in the sidecar, and return the records WITH seqs as
    read under the lock. Returning the locked snapshot matters (round 5): a
    reader that parsed the file before a compaction folded the sidecar and
    cleared it would otherwise pair a stale file with an empty map and drop
    the record as seq 0."""
    with _store_lock(root):
        overrides = _load_overrides(root)
        records = list(_iter_records(root / "messages.jsonl"))
        changed = False
        for r in records:
            rid = str(r.get("id") or "")
            if r.get("seq") or not rid or rid in overrides:
                continue
            overrides[rid] = _next_seq(root)
            changed = True
        if changed:
            _write_atomic(root / "seq_overrides.json",
                          json.dumps(overrides).encode("utf-8"))
        return _apply_overrides(records, overrides)


def record(
    *,
    direction: str,
    kind: str,
    peer_id: str,
    task_id: str,
    text: str = "",
    data: dict | None = None,
    status: str = "",
    attachments: Iterable[Any] | None = None,
    duration_ms: int | None = None,
    peer_label: str | None = None,
    error: str | None = None,
    tenant_id: str | None = None,
) -> dict | None:
    """Append one message. Returns the stored record, or None on any failure."""
    try:
        if direction not in ("in", "out") or kind not in ("task", "response"):
            return None
        root = feed_dir(tenant_id)
        _ensure_dir(root)
        with _store_lock(root):
            rec = {
                "id": uuid.uuid4().hex,
                "seq": _next_seq(root),
                "ts": time.time(),
                "direction": direction,
                "kind": kind,
                "peer_id": str(peer_id)[:128],
                "peer_label": (str(peer_label)[:80] if peer_label else None),
                "task_id": str(task_id)[:64],
                "status": str(status or "")[:32],
                "text": str(text or "")[:MAX_TEXT_CHARS],
                "data": _clip_data(data or {}),
                # Blobs are stored under the same lock as the line that
                # references them, so compaction can never collect a blob
                # whose message is not written yet.
                "attachments": _store_attachments(root, attachments),
                "duration_ms": duration_ms,
                "error": (str(error)[:256] if error else None),
            }
            line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
            fd = os.open(root / "messages.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
        _maybe_compact(root)
        return rec
    except Exception:
        return None


# ── read ──────────────────────────────────────────────────────────────────

def _iter_records(path: Path) -> Iterable[dict]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict):
                    yield r
    except FileNotFoundError:
        return


def read_page(
    *,
    after: int | None = None,
    before: int | None = None,
    limit: int = 200,
    peer_id: str | None = None,
    tenant_id: str | None = None,
) -> tuple[list[dict], bool]:
    """One page of messages in append (``seq``) order, plus ``has_more``.

    * ``after``  — the OLDEST ``limit`` messages with seq > after (live
      polling: never skips; ``has_more`` says poll again right away).
    * ``before`` — the NEWEST ``limit`` messages with seq < before (history).
    * neither    — the newest ``limit`` messages (initial load).
    Records written before ``seq`` existed count as seq 0.
    """
    cutoff = time.time() - RETENTION_DAYS * 86400
    rows: list[dict] = []
    root = feed_dir(tenant_id)
    records = _apply_overrides(list(_iter_records(root / "messages.jsonl")),
                               _load_overrides(root))
    if any(not r.get("seq") for r in records):
        try:
            records = _assign_missing_seq(root)
        except Exception:  # noqa: BLE001 — a read must never fail on this
            pass
    for r in records:
        if (r.get("ts") or 0.0) < cutoff:
            continue
        if peer_id and r.get("peer_id") != peer_id:
            continue
        seq = int(r.get("seq") or 0)
        if after is not None and seq <= after:
            continue
        if before is not None and seq >= before:
            continue
        rows.append(r)
    rows.sort(key=lambda r: (int(r.get("seq") or 0), r.get("ts") or 0.0))
    if limit <= 0 or len(rows) <= limit:
        return rows, False
    if after is not None:
        return rows[:limit], True
    return rows[-limit:], True


def read(
    *,
    since: float | None = None,
    limit: int = 200,
    peer_id: str | None = None,
    tenant_id: str | None = None,
) -> list[dict]:
    """Messages in append order; newest ``limit`` (``since`` = legacy ts filter)."""
    rows, _more = read_page(limit=0, peer_id=peer_id, tenant_id=tenant_id)
    if since is not None:
        rows = [r for r in rows if (r.get("ts") or 0.0) > since]
    return rows[-limit:] if limit > 0 else rows


# ── retention ─────────────────────────────────────────────────────────────

_blob_estimate: dict[str, tuple[float, int]] = {}  # root → (scanned_at, bytes)
_BLOB_RESCAN_S = 300.0


def _blob_bytes(root: Path, added: int = 0) -> int:
    """Blob-directory size without a scandir per write: a periodic scan plus
    what this process stored since (other processes are caught by the next
    rescan). Round 3: the per-write scandir was O(blobs) on every record()."""
    key = str(root)
    now = time.time()
    scanned_at, total = _blob_estimate.get(key, (0.0, -1))
    if total < 0 or now - scanned_at > _BLOB_RESCAN_S:
        try:
            total = sum(e.stat().st_size for e in os.scandir(root / "blobs") if e.is_file())
        except OSError:
            total = 0
        scanned_at = now
    total += added
    _blob_estimate[key] = (scanned_at, total)
    return total


def _maybe_compact(root: Path) -> None:
    key = str(root)
    now = time.time()
    try:
        too_big = (root / "messages.jsonl").stat().st_size > MAX_FEED_BYTES
    except OSError:
        return
    if not too_big:
        too_big = _blob_bytes(root) > MAX_BLOB_BYTES
    if not too_big and now - _last_compact.get(key, 0.0) < _COMPACT_INTERVAL_S:
        return
    _last_compact[key] = now
    compact(root)


def _fair_trim(records: list[dict], size_of: Any, budget: int) -> list[dict]:
    """Drop records until ``sum(size_of)`` fits ``budget``, always taking the
    OLDEST record of the peer that currently holds the most bytes. Keeps the
    original (append) order of what remains."""
    from collections import defaultdict, deque
    per: dict[str, deque] = defaultdict(deque)
    tot: dict[str, int] = defaultdict(int)
    sizes = [size_of(r) for r in records]
    for idx, r in enumerate(records):
        pid = str(r.get("peer_id") or "")
        per[pid].append(idx)
        tot[pid] += sizes[idx]
    total = sum(tot.values())
    drop: set[int] = set()
    while total > budget and tot:
        pid = max(tot, key=tot.get)
        idx = per[pid].popleft()
        drop.add(idx)
        tot[pid] -= sizes[idx]
        total -= sizes[idx]
        if not per[pid]:
            del tot[pid]
    return [r for i, r in enumerate(records) if i not in drop]


def compact(root: Path) -> tuple[int, int]:
    """Drop expired/oversize records and orphan blobs. Returns (msgs, blobs) removed."""
    path = root / "messages.jsonl"
    cutoff = time.time() - RETENTION_DAYS * 86400
    _ensure_dir(root)
    with _store_lock(root):
        overrides = _load_overrides(root)
        records = _apply_overrides(list(_iter_records(path)), overrides)
        keep = [r for r in records if (r.get("ts") or 0.0) >= cutoff]
        # Budgets are trimmed FAIRLY (round 7): over budget, the peer with
        # the largest share loses its oldest records first — one paired peer
        # flooding large messages or attachments can no longer evict every
        # OTHER peer's conversation.
        def _line_size(r: dict) -> int:
            return len(json.dumps(r, ensure_ascii=False).encode("utf-8")) + 1

        line_total = sum(_line_size(r) for r in keep)
        if line_total > MAX_FEED_BYTES // 2:
            keep = _fair_trim(keep, _line_size, MAX_FEED_BYTES // 2)

        blob_dir = root / "blobs"
        _sizes: dict[str, int] = {}

        def _blob_size(sha: str) -> int:
            if sha not in _sizes:
                try:
                    _sizes[sha] = (blob_dir / sha).stat().st_size
                except OSError:
                    _sizes[sha] = 0
            return _sizes[sha]

        def _att_size(r: dict) -> int:
            return sum(_blob_size(a.get("sha256", "")) for a in r.get("attachments") or [])

        unique = {a.get("sha256", "") for r in keep for a in r.get("attachments") or []}
        blob_total = sum(_blob_size(sha) for sha in unique if sha)
        # Low-water mark: once over the cap, trim to HALF of it — trimming to
        # exactly the cap made every following attachment write re-run a full
        # compaction under the global lock. (Shared blobs are counted per
        # reference while trimming: a conservative upper bound.)
        if blob_total > MAX_BLOB_BYTES:
            keep = _fair_trim(keep, _att_size, MAX_BLOB_BYTES // 2)
        removed = len(records) - len(keep)
        if removed or overrides:
            # The rewrite persists override seqs into the records themselves.
            _write_atomic(path, "".join(
                json.dumps(r, ensure_ascii=False) + "\n" for r in keep).encode("utf-8"))
            if overrides:
                _write_atomic(root / "seq_overrides.json", b"{}")
        referenced = {a.get("sha256") for r in keep for a in r.get("attachments") or []}
        blobs_removed = 0
        if blob_dir.is_dir():
            for b in blob_dir.iterdir():
                if b.name.startswith(".") or b.name in referenced:
                    continue  # in-flight temp files and live blobs stay
                try:
                    b.unlink()
                    blobs_removed += 1
                except OSError:
                    pass
        _blob_estimate.pop(str(root), None)  # rescan on next check
    return removed, blobs_removed


def clear(tenant_id: str | None = None) -> tuple[int, int]:
    """Remove every stored message and blob. Returns (msgs, blobs) removed."""
    root = feed_dir(tenant_id)
    _ensure_dir(root)
    path = root / "messages.jsonl"
    with _store_lock(root):
        msgs = sum(1 for _ in _iter_records(path))
        for f in (path, root / "seq_overrides.json"):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        blobs = 0
        for b in (root / "blobs").iterdir():
            try:
                b.unlink()
                blobs += 1
            except OSError:
                pass
    return msgs, blobs


__all__ = [
    "FEED_DIRNAME", "RETENTION_DAYS", "MAX_FEED_BYTES",
    "feed_dir", "record", "read", "read_page", "blob_path", "compact", "clear",
]
