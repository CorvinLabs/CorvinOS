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


def _store_attachments(root: Path, attachments: Iterable[Any] | None) -> list[dict]:
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
            tmp = blob.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, raw)
            finally:
                os.close(fd)
            os.replace(tmp, blob)
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
        rec = {
            "id": uuid.uuid4().hex,
            "ts": time.time(),
            "direction": direction,
            "kind": kind,
            "peer_id": str(peer_id)[:128],
            "peer_label": (str(peer_label)[:80] if peer_label else None),
            "task_id": str(task_id)[:64],
            "status": str(status or "")[:32],
            "text": str(text or "")[:MAX_TEXT_CHARS],
            "data": _clip_data(data or {}),
            "attachments": _store_attachments(root, attachments),
            "duration_ms": duration_ms,
            "error": (str(error)[:256] if error else None),
        }
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        path = root / "messages.jsonl"
        with _lock:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
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


def read(
    *,
    since: float | None = None,
    limit: int = 200,
    peer_id: str | None = None,
    tenant_id: str | None = None,
) -> list[dict]:
    """Messages oldest-first; the newest ``limit`` after ``since``."""
    cutoff = time.time() - RETENTION_DAYS * 86400
    out: list[dict] = []
    for r in _iter_records(feed_dir(tenant_id) / "messages.jsonl"):
        ts = r.get("ts") or 0.0
        if ts < cutoff:
            continue
        if since is not None and ts <= since:
            continue
        if peer_id and r.get("peer_id") != peer_id:
            continue
        out.append(r)
    out.sort(key=lambda r: r.get("ts") or 0.0)
    return out[-limit:] if limit > 0 else out


# ── retention ─────────────────────────────────────────────────────────────

def _maybe_compact(root: Path) -> None:
    key = str(root)
    now = time.time()
    path = root / "messages.jsonl"
    try:
        too_big = path.stat().st_size > MAX_FEED_BYTES
    except OSError:
        return
    if not too_big and now - _last_compact.get(key, 0.0) < _COMPACT_INTERVAL_S:
        return
    _last_compact[key] = now
    compact(root)


def compact(root: Path) -> tuple[int, int]:
    """Drop expired/oversize records and orphan blobs. Returns (msgs, blobs) removed."""
    path = root / "messages.jsonl"
    cutoff = time.time() - RETENTION_DAYS * 86400
    with _lock:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            records = list(_iter_records(path))
            keep = [r for r in records if (r.get("ts") or 0.0) >= cutoff]
            # Oversize: keep the newest records that fit in half the cap.
            budget, sized = MAX_FEED_BYTES // 2, []
            for r in reversed(keep):
                n = len(json.dumps(r, ensure_ascii=False).encode("utf-8")) + 1
                if budget - n < 0:
                    break
                budget -= n
                sized.append(r)
            keep = list(reversed(sized))
            removed = len(records) - len(keep)
            if removed:
                tmp = path.with_suffix(".tmp")
                tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    for r in keep:
                        os.write(tfd, (json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8"))
                finally:
                    os.close(tfd)
                os.replace(tmp, path)
        finally:
            os.close(fd)
        referenced = {a.get("sha256") for r in keep for a in r.get("attachments") or []}
        blobs_removed = 0
        blob_dir = root / "blobs"
        if blob_dir.is_dir():
            for b in blob_dir.iterdir():
                if b.name not in referenced:
                    try:
                        b.unlink()
                        blobs_removed += 1
                    except OSError:
                        pass
    return removed, blobs_removed


def clear(tenant_id: str | None = None) -> tuple[int, int]:
    """Remove every stored message and blob. Returns (msgs, blobs) removed."""
    root = feed_dir(tenant_id)
    path = root / "messages.jsonl"
    with _lock:
        msgs = sum(1 for _ in _iter_records(path))
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        blobs = 0
        blob_dir = root / "blobs"
        if blob_dir.is_dir():
            for b in blob_dir.iterdir():
                try:
                    b.unlink()
                    blobs += 1
                except OSError:
                    pass
    return msgs, blobs


__all__ = [
    "FEED_DIRNAME", "RETENTION_DAYS", "MAX_FEED_BYTES",
    "feed_dir", "record", "read", "blob_path", "compact", "clear",
]
