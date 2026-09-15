"""Layer 33 — Session Artifact Memory library.

See ADR-0040 and ``docs/claude-ref/layer-33-artifacts.md`` for the full
contract. This module owns the on-disk manifest, sha-prefix sharding,
file locking, and the registration / retrieval / pin operations. MCP
handlers live in ``forge.mcp_server``; the auto-register PostToolUse
hook lives in ``operator/voice/hooks/path_gate.py``.

Privacy invariant (load-bearing): no description text and no artifact
content ever enters audit-event details. Names, sha256, mime, size,
by_tool only.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .paths import tenant_global_dir, tenant_sessions_dir
from .tenants import current_tenant

try:
    # Best-effort: helper-model + recall PII-redaction lift the description
    # through Layer 28's redaction pipeline. Tests and minimal deployments
    # may not have these wired; the module degrades to "store raw" then.
    from operator_bridges_shared_pii_redact import pii_redact as _pii_redact  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _pii_redact = None


# ── Constants ──────────────────────────────────────────────────────────────

MANIFEST_FILENAME = ".manifest.jsonl"
MANIFEST_LOCK_FILENAME = ".manifest.lock"
CONFIG_FILENAME = "artifacts.config.json"

# Default config — overridden by <corvin_home>/global/artifacts.config.json
_DEFAULT_CONFIG: dict[str, Any] = {
    "storage_backend": "jsonl",
    "session_artifact_ttl_days": 7,
    "auto_register_mimes": [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/svg+xml",
        "text/csv",
        "text/html",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
    "description_model": "haiku-4.5",
    "description_max_tokens": 60,
    "description_language": "auto",
    "manifest_lock_timeout_ms": 5000,
    "max_artifact_size_bytes": 104_857_600,  # 100 MB
    "pre_warn_on_reset": True,
}

_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_TAG_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")
_NAME_MAX_LEN = 200
_TAG_MAX_LEN = 40
_TAGS_MAX = 16

_in_process_lock = threading.RLock()


# ── Dataclasses ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArtifactEntry:
    ts: float
    name: str
    sha256: str
    size: int
    mime: str
    path_rel: str
    by_tool: str = ""
    run_id: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    pinned: bool = False

    def to_jsonl(self) -> str:
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_jsonl(cls, line: str) -> "ArtifactEntry":
        d = json.loads(line)
        return cls(
            ts=float(d["ts"]),
            name=str(d["name"]),
            sha256=str(d["sha256"]),
            size=int(d["size"]),
            mime=str(d.get("mime", "")),
            path_rel=str(d["path_rel"]),
            by_tool=str(d.get("by_tool", "")),
            run_id=str(d.get("run_id", "")),
            description=str(d.get("description", "")),
            tags=list(d.get("tags") or []),
            pinned=bool(d.get("pinned", False)),
        )


class ArtifactError(Exception):
    """Raised on validation / IO failures the caller must handle."""


class ManifestLockTimeout(ArtifactError):
    """Raised when fcntl.flock cannot be acquired within the configured budget."""


# ── Path resolution ────────────────────────────────────────────────────────


def session_artifacts_dir(session_key: str, tenant_id: str | None = None) -> Path:
    """Return ``<tenant>/sessions/<session_key>/artifacts/``.

    ``session_key`` is the same key Layer 8 uses — typically
    ``"<bridge>:<chat_key>"`` (e.g. ``"discord:1502...."``).
    """
    if not session_key or "/" in session_key or ".." in session_key:
        raise ArtifactError(f"invalid session_key: {session_key!r}")
    # ``session_key`` is typically ``<bridge>:<chat_key>``; the ``:`` is illegal
    # in a Windows filename, so sanitise to a safe leaf component (POSIX no-op,
    # so existing dirs are unchanged).
    from .paths import fs_safe_component  # noqa: PLC0415
    return tenant_sessions_dir(tenant_id) / fs_safe_component(session_key) / "artifacts"


def global_artifacts_dir(tenant_id: str | None = None) -> Path:
    """Return ``<tenant>/global/artifacts/`` — the pinned-scope root."""
    return tenant_global_dir(tenant_id) / "artifacts"


def _manifest_path(artifacts_root: Path) -> Path:
    return artifacts_root / MANIFEST_FILENAME


def _lock_path(artifacts_root: Path) -> Path:
    return artifacts_root / MANIFEST_LOCK_FILENAME


def _config_path(tenant_id: str | None = None) -> Path:
    return tenant_global_dir(tenant_id) / CONFIG_FILENAME


def load_config(tenant_id: str | None = None) -> dict[str, Any]:
    """Load config from ``<tenant>/global/artifacts.config.json`` with defaults."""
    cfg = dict(_DEFAULT_CONFIG)
    path = _config_path(tenant_id)
    if path.exists():
        try:
            override = json.loads(path.read_text())
            if isinstance(override, dict):
                cfg.update(override)
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


# ── Sanitisation ───────────────────────────────────────────────────────────


def _sanitise_name(name: str) -> str:
    name = (name or "").strip()
    name = _NAME_SAFE_RE.sub("_", name)
    name = name.strip("._-") or "artifact"
    return name[:_NAME_MAX_LEN]


def _sanitise_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    out: list[str] = []
    for raw in tags[:_TAGS_MAX]:
        t = _TAG_SAFE_RE.sub("_", str(raw)).strip("._-")
        if t:
            out.append(t[:_TAG_MAX_LEN])
    return out


def _shard_for(sha: str) -> str:
    return sha[:2]


def _detect_mime(path: Path) -> str:
    """MIME detection without depending on python-magic.

    Reads the first 16 bytes and matches a small set of magic numbers
    for the artifact types we actually care about; falls back to
    ``mimetypes.guess_type`` for extension-based detection; finally
    returns ``application/octet-stream``. Replace with libmagic later
    if the operator wires it.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        head = b""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"RIFF" and len(head) >= 12:
        try:
            with path.open("rb") as fh:
                tail = fh.read(12)
            if tail[8:12] == b"WEBP":
                return "image/webp"
        except OSError:
            pass
    if head[:5] == b"<?xml" or head[:4] == b"<svg":
        return "image/svg+xml"
    if head[:2] == b"PK":
        # Likely OOXML / zip — guess by extension.
        ext_guess = mimetypes.guess_type(str(path))[0]
        if ext_guess:
            return ext_guess
        return "application/zip"
    guessed = mimetypes.guess_type(str(path))[0]
    return guessed or "application/octet-stream"


# ── Locking ────────────────────────────────────────────────────────────────


class _ManifestLock:
    """fcntl-based exclusive lock with a millisecond-precision timeout."""

    def __init__(self, lock_path: Path, timeout_ms: int):
        self.lock_path = lock_path
        self.timeout_ms = timeout_ms
        self._fh = None
        self._held = False

    def __enter__(self) -> "_ManifestLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.lock_path.open("a+")
        deadline = time.monotonic() + (self.timeout_ms / 1000.0)
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._held = True
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._fh.close()
                    self._fh = None
                    raise ManifestLockTimeout(
                        f"could not acquire {self.lock_path} within "
                        f"{self.timeout_ms} ms")
                time.sleep(0.05)

    def __exit__(self, *exc) -> None:
        try:
            if self._held and self._fh is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            if self._fh is not None:
                self._fh.close()
            self._fh = None
            self._held = False


# ── Audit emission (best-effort) ───────────────────────────────────────────


def _emit_audit(event_type: str, *, severity: str, details: dict[str, Any],
                tenant_id: str | None = None) -> None:
    """Append one event to the unified hash chain. Never raises.

    Detail policy: caller is responsible for omitting description /
    content / paths-outside-artifact-tree. This function does not
    sanitise.
    """
    try:
        from .security_events import write_event  # type: ignore

        audit_path = tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        write_event(audit_path, event_type, severity=severity,
                    tool="forge.artifacts", run_id="",
                    details=details)
    except Exception:
        pass


# ── PII-redaction (best-effort, never blocks) ─────────────────────────────


def _redact_description(text: str) -> str:
    """Run the recall-layer PII redaction if available; pass-through otherwise."""
    if not text:
        return text
    if _pii_redact is None:
        return text
    try:
        return _pii_redact(text)
    except Exception:  # pragma: no cover
        return text


# ── Public API ─────────────────────────────────────────────────────────────


def register(
    *,
    source_path: Path,
    artifacts_root: Path,
    name: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
    by_tool: str = "",
    run_id: str = "",
    pinned: bool = False,
    tenant_id: str | None = None,
    move: bool = False,
) -> ArtifactEntry:
    """Register ``source_path`` as an artifact under ``artifacts_root``.

    The file is **copied** by default (``move=False``) so the original
    location stays intact for the caller; ``move=True`` does an atomic
    rename into the sharded path (used by the auto-register hook when
    the tool wrote outside the artifact tree).

    ``artifacts_root`` is ``session_artifacts_dir(...)`` or
    ``global_artifacts_dir(...)``. The library does not pick the scope
    on the caller's behalf — that is a policy decision for the MCP
    handler or the auto-register hook.

    Returns the ``ArtifactEntry`` that was appended to the manifest.
    """
    source_path = Path(source_path)
    if not source_path.is_file():
        raise ArtifactError(f"source_path is not a file: {source_path}")

    cfg = load_config(tenant_id)
    size = source_path.stat().st_size
    if size > int(cfg["max_artifact_size_bytes"]):
        raise ArtifactError(
            f"artifact too large: {size} > {cfg['max_artifact_size_bytes']}")

    sha = _sha256_file(source_path)
    mime = _detect_mime(source_path)
    safe_name = _sanitise_name(name or source_path.name)
    safe_tags = _sanitise_tags(tags)

    artifacts_root.mkdir(parents=True, exist_ok=True)
    shard_dir = artifacts_root / _shard_for(sha)
    shard_dir.mkdir(parents=True, exist_ok=True)
    dst = shard_dir / f"{sha[:8]}_{safe_name}"

    if dst.exists() and dst.stat().st_size == size:
        # Idempotent: same content already registered. Append a new
        # manifest line anyway so the by_tool / description / ts reflect
        # this registration. The on-disk file is shared.
        pass
    else:
        if move:
            os.replace(source_path, dst)
        else:
            shutil.copy2(source_path, dst)
    os.chmod(dst, 0o644)

    entry = ArtifactEntry(
        ts=time.time(),
        name=safe_name,
        sha256=sha,
        size=size,
        mime=mime,
        path_rel=str(dst.relative_to(artifacts_root)),
        by_tool=str(by_tool or "")[:200],
        run_id=str(run_id or "")[:80],
        description=_redact_description(description or ""),
        tags=safe_tags,
        pinned=bool(pinned),
    )
    _append_manifest(artifacts_root, entry, tenant_id=tenant_id)

    _emit_audit(
        "artifact.registered",
        severity="INFO",
        details={
            "name": entry.name,
            "sha256": entry.sha256,
            "size": entry.size,
            "mime": entry.mime,
            "by_tool": entry.by_tool,
            "scope": "global" if pinned else "session",
        },
        tenant_id=tenant_id,
    )
    return entry


def _append_manifest(artifacts_root: Path, entry: ArtifactEntry,
                     *, tenant_id: str | None) -> None:
    cfg = load_config(tenant_id)
    line = entry.to_jsonl() + "\n"
    with _in_process_lock:
        with _ManifestLock(_lock_path(artifacts_root),
                           int(cfg["manifest_lock_timeout_ms"])):
            manifest = _manifest_path(artifacts_root)
            with manifest.open("a") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(manifest, 0o644)


def _iter_manifest(artifacts_root: Path) -> Iterator[ArtifactEntry]:
    manifest = _manifest_path(artifacts_root)
    if not manifest.exists():
        return iter(())
    def _gen() -> Iterator[ArtifactEntry]:
        with manifest.open("r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield ArtifactEntry.from_jsonl(line)
                except (json.JSONDecodeError, KeyError, ValueError):
                    # Corrupt line — skip but flag with a single audit event.
                    _emit_audit(
                        "artifact.manifest_line_skipped",
                        severity="WARNING",
                        details={"manifest": str(manifest), "line_excerpt": line[:80]},
                    )
                    continue
    return _gen()


def list_active(artifacts_root: Path, *, mime: str | None = None,
                after_ts: float | None = None,
                limit: int = 20) -> list[ArtifactEntry]:
    """Return the currently-active artifacts in this scope.

    Manifest is append-only; "active" = latest entry per ``name`` wins.
    Tombstones (entries with ``size=-1``) hide an artifact. The result
    is sorted by ``ts`` descending and truncated to ``limit``.
    """
    by_name: dict[str, ArtifactEntry] = {}
    for entry in _iter_manifest(artifacts_root):
        by_name[entry.name] = entry  # latest wins by file order
    active = [e for e in by_name.values() if e.size >= 0]
    if mime:
        active = [e for e in active if e.mime == mime]
    if after_ts is not None:
        active = [e for e in active if e.ts > after_ts]
    active.sort(key=lambda e: e.ts, reverse=True)
    return active[:limit]


def find_by_name(artifacts_root: Path, name: str) -> ArtifactEntry | None:
    """Return the latest active entry for ``name``, or None."""
    safe = _sanitise_name(name)
    candidate: ArtifactEntry | None = None
    for entry in _iter_manifest(artifacts_root):
        if entry.name == safe:
            candidate = entry
    if candidate and candidate.size < 0:
        return None
    return candidate


def read_artifact_bytes(artifacts_root: Path, entry: ArtifactEntry,
                        *, max_bytes: int) -> bytes:
    """Return up to ``max_bytes`` of the artifact's content.

    The caller emits the ``artifact.read`` audit event; this layer
    does not, because byte-range / extract semantics are richer than
    a single event class.
    """
    path = artifacts_root / entry.path_rel
    if not path.is_file():
        raise ArtifactError(f"artifact missing on disk: {entry.name}")
    with path.open("rb") as fh:
        return fh.read(max_bytes)


def pin(*, session_root: Path, global_root: Path, name: str,
        tenant_id: str | None = None) -> ArtifactEntry:
    """Promote a session-scope artifact to ``<global>/artifacts/``.

    The session entry remains (append-only); a new entry is written
    into the global manifest with ``pinned: true``. The on-disk file
    is copied (not moved) so a subsequent session reset doesn't
    unlink the pinned copy.
    """
    entry = find_by_name(session_root, name)
    if entry is None:
        raise ArtifactError(f"no session artifact named {name!r}")
    src = session_root / entry.path_rel
    if not src.is_file():
        raise ArtifactError(f"session artifact missing on disk: {entry.name}")

    global_root.mkdir(parents=True, exist_ok=True)
    shard_dir = global_root / _shard_for(entry.sha256)
    shard_dir.mkdir(parents=True, exist_ok=True)
    dst = shard_dir / f"{entry.sha256[:8]}_{entry.name}"
    if not dst.exists():
        shutil.copy2(src, dst)
        os.chmod(dst, 0o644)

    pinned_entry = ArtifactEntry(
        ts=time.time(),
        name=entry.name,
        sha256=entry.sha256,
        size=entry.size,
        mime=entry.mime,
        path_rel=str(dst.relative_to(global_root)),
        by_tool=entry.by_tool,
        run_id=entry.run_id,
        description=entry.description,
        tags=entry.tags,
        pinned=True,
    )
    _append_manifest(global_root, pinned_entry, tenant_id=tenant_id)
    _emit_audit(
        "artifact.pinned",
        severity="INFO",
        details={"name": entry.name, "sha256": entry.sha256},
        tenant_id=tenant_id,
    )
    return pinned_entry


def purge_session(session_root: Path, *,
                  tenant_id: str | None = None) -> int:
    """Remove every artifact and the manifest for one session.

    Called by Layer 8 during ``/new`` / ``/clear`` / ``/reset`` **after**
    the audit-first event has been emitted. Returns the number of
    on-disk files that were removed (manifest-counted, not FS-walked).
    """
    if not session_root.exists():
        return 0
    active = list_active(session_root, limit=10_000)
    count = len(active)
    _emit_audit(
        "artifact.session_purged",
        severity="CRITICAL",
        details={"count": count},
        tenant_id=tenant_id,
    )
    shutil.rmtree(session_root, ignore_errors=True)
    return count


def purge_one(artifacts_root: Path, name: str,
              *, tenant_id: str | None = None) -> bool:
    """Tombstone one artifact: write a ``size=-1`` entry, unlink the file.

    Returns True if something was removed. The audit event
    ``artifact.purged`` is always emitted for non-misses.
    """
    entry = find_by_name(artifacts_root, name)
    if entry is None:
        return False
    file_path = artifacts_root / entry.path_rel
    try:
        file_path.unlink()
    except FileNotFoundError:
        pass
    tombstone = ArtifactEntry(
        ts=time.time(),
        name=entry.name,
        sha256=entry.sha256,
        size=-1,
        mime=entry.mime,
        path_rel=entry.path_rel,
        by_tool=entry.by_tool,
        run_id=entry.run_id,
        description="",
        tags=[],
        pinned=entry.pinned,
    )
    _append_manifest(artifacts_root, tombstone, tenant_id=tenant_id)
    _emit_audit(
        "artifact.purged",
        severity="INFO",
        details={"name": entry.name, "sha256": entry.sha256},
        tenant_id=tenant_id,
    )
    return True


def reconcile_manifest(artifacts_root: Path,
                       *, tenant_id: str | None = None) -> int:
    """Rebuild ``.manifest.jsonl`` from the filesystem.

    Used when the manifest is missing or corrupt beyond skip-tolerance.
    Walks the shard directories, hashes each file, and writes a fresh
    manifest with ``description="(reconciled)"``. Returns the number
    of entries written. No audit emission per-entry — one summary
    event ``artifact.manifest_reconciled`` covers the operation.
    """
    artifacts_root.mkdir(parents=True, exist_ok=True)
    manifest = _manifest_path(artifacts_root)
    if manifest.exists():
        manifest.rename(manifest.with_suffix(".jsonl.bak"))
    count = 0
    for shard in sorted(artifacts_root.glob("[0-9a-f][0-9a-f]")):
        if not shard.is_dir():
            continue
        for f in sorted(shard.iterdir()):
            if not f.is_file():
                continue
            sha = _sha256_file(f)
            # Skip files whose disk-name doesn't carry our 8-char sha prefix.
            base = f.name
            if "_" not in base:
                continue
            name = base.split("_", 1)[1]
            entry = ArtifactEntry(
                ts=f.stat().st_mtime,
                name=name,
                sha256=sha,
                size=f.stat().st_size,
                mime=_detect_mime(f),
                path_rel=str(f.relative_to(artifacts_root)),
                by_tool="(reconciled)",
                run_id="",
                description="(reconciled)",
                tags=[],
                pinned=False,
            )
            _append_manifest(artifacts_root, entry, tenant_id=tenant_id)
            count += 1
    _emit_audit(
        "artifact.manifest_reconciled",
        severity="WARNING",
        details={"count": count, "scope_root": str(artifacts_root)},
        tenant_id=tenant_id,
    )
    return count


# ── Internal helpers ───────────────────────────────────────────────────────


def _sha256_file(path: Path, *, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()
