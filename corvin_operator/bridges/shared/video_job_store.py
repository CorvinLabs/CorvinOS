"""Host-side lifecycle of a Video Producer job on disk (ADR-2247 R3, PLAN-0946 R3).

ONE module that knows which files make up a job in a tenant's store
(``<tenant>/video_producer``), shared by the two places that remove jobs:

* the console route ``DELETE /v1/console/video/jobs/{id}`` (one job, by the operator);
* the GDPR Art. 17 layer ``L-video-producer`` in ``erasure_handlers.py`` (every job of a subject).

It also owns the two host sidecars next to the plugin's records - ``owners.json`` (job id -> the
principal that created it) and ``revisions.json`` (job id -> the job it revises) - and writes both
through one locked read-modify-write with an atomic replace, so two host processes on one store
cannot lose each other's entries.

Stdlib only: the erasure orchestrator runs without the console and without the plugin.

A job's files (the plugin's layout, ``storage.py`` / ``async_runner.py``):

* ``jobs/<id>.json``      the record (task text, storyboard)
* ``jobs/.<id>.rw``       the record's write lock
* ``jobs/<id>.lock``      the liveness lock a job process holds while it runs
* ``jobs/<id>.run/``      the 0700 hand-over directory of a job process (config with the task)
* ``videos/<id>/``        everything the job produced: scenes, audio, posters, caches, the MP4,
                          ``metadata.json`` and the production report ``report.json``
* ``thumbnails/<id>*``    thumbnails, where a build writes any
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

try:  # POSIX
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows: the process-local lock below still serialises
    fcntl = None  # type: ignore[assignment]

JOB_ID_RE = re.compile(r"^job_[0-9a-f]{8}$")
OWNERS_FILE = "owners.json"
REVISIONS_FILE = "revisions.json"
_LOCK_FILE = ".sidecars.lock"
_VALUE_RE = re.compile(r"^[A-Za-z0-9.:_\-]{1,128}$")  # the erasure orchestrator's subject-id shape
_local_lock = threading.Lock()


def _valid_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("not a video job id")
    return job_id


# ── sidecars ──────────────────────────────────────────────────────────────────

def read_map(base: Path, name: str) -> Dict[str, str]:
    """A sidecar as ``{job id: value}``; malformed entries are dropped, a missing or unreadable file
    is empty (never an exception: a broken sidecar must not take the listing down)."""
    p = Path(base) / name
    try:
        if p.is_symlink():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and JOB_ID_RE.fullmatch(k) and isinstance(v, str) and _VALUE_RE.fullmatch(v)}


@contextlib.contextmanager
def sidecar_lock(base: Path) -> Iterator[None]:
    """Exclusive lock over BOTH sidecars of one store: flock across processes (POSIX), plus a
    process-local lock (every platform)."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    with _local_lock:
        fh = open(base / _LOCK_FILE, "a")
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fh.close()  # closing drops the flock


def _write_map(base: Path, name: str, data: Dict[str, str]) -> None:
    fd, tmp = tempfile.mkstemp(dir=base, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, Path(base) / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def update_maps(base: Path, mutator: Callable[[Dict[str, Dict[str, str]]], None]) -> None:
    """Locked read-modify-write of both sidecars. ``mutator`` edits ``{"owners": {...},
    "revisions": {...}}`` in place; a sidecar whose content did not change is not rewritten."""
    base = Path(base)
    with sidecar_lock(base):
        cur = {"owners": read_map(base, OWNERS_FILE), "revisions": read_map(base, REVISIONS_FILE)}
        new = {k: dict(v) for k, v in cur.items()}
        mutator(new)
        for key, name in (("owners", OWNERS_FILE), ("revisions", REVISIONS_FILE)):
            if new[key] != cur[key]:
                bad = [k for k, v in new[key].items() if not JOB_ID_RE.fullmatch(k) or not _VALUE_RE.fullmatch(v)]
                if bad:
                    raise ValueError(f"{name}: refusing malformed entries")
                _write_map(base, name, new[key])


def record_owner(base: Path, job_id: str, owner: str) -> None:
    _valid_job_id(job_id)
    if not isinstance(owner, str) or not _VALUE_RE.fullmatch(owner):
        raise ValueError("owner is not an identifier")
    update_maps(base, lambda m: m["owners"].__setitem__(job_id, owner))


def record_revision(base: Path, job_id: str, base_job_id: str) -> None:
    _valid_job_id(job_id)
    _valid_job_id(base_job_id)
    update_maps(base, lambda m: m["revisions"].__setitem__(job_id, base_job_id))


def _drop_sidecar_entries(base: Path, job_ids: List[str]) -> None:
    gone = set(job_ids)

    def _drop(m: Dict[str, Dict[str, str]]) -> None:
        for jid in gone:
            m["owners"].pop(jid, None)
        # a deleted job loses its own revision link AND every link pointing at it - a revision whose
        # original is gone is shown as a plain video, never as a dangling reference
        m["revisions"] = {k: v for k, v in m["revisions"].items() if k not in gone and v not in gone}

    update_maps(base, _drop)


# ── a job's files ─────────────────────────────────────────────────────────────

def job_paths(base: Path, job_id: str) -> List[Path]:
    """Every path that belongs to ``job_id`` and exists (symlinks included, never followed)."""
    _valid_job_id(job_id)
    base = Path(base)
    jobs, videos, thumbs = base / "jobs", base / "videos", base / "thumbnails"
    cands = [videos / job_id, jobs / f"{job_id}.run", jobs / f"{job_id}.json",
             jobs / f".{job_id}.rw", jobs / f"{job_id}.lock"]
    if thumbs.is_dir() and not thumbs.is_symlink():
        cands += sorted(p for p in thumbs.iterdir() if p.name == job_id or p.name.startswith(job_id + "."))
    return [p for p in cands if p.is_symlink() or p.exists()]


def _footprint(path: Path) -> Tuple[int, int]:
    """(files, bytes) under ``path``; a symlink counts as one file of its own size, never followed."""
    if path.is_symlink() or not path.is_dir():
        try:
            return 1, path.lstat().st_size
        except OSError:
            return 0, 0
    files = size = 0
    for root, dirs, names in os.walk(path, followlinks=False):
        for n in names + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            try:
                size += os.lstat(os.path.join(root, n)).st_size
                files += 1
            except OSError:
                pass
    return files, size


def job_footprint(base: Path, job_id: str) -> Tuple[int, int]:
    """(files, bytes) a deletion of ``job_id`` removes."""
    files = size = 0
    for p in job_paths(base, job_id):
        f, s = _footprint(p)
        files, size = files + f, size + s
    return files, size


def job_is_live(base: Path, job_id: str) -> bool:
    """True while a process holds the job's liveness lock (``jobs/<id>.lock``, taken by the host's
    runner from acceptance until the record is terminal). Where flock does not exist this is
    unknown and reported False: the caller's own status check is then the only guard."""
    _valid_job_id(job_id)
    lock = Path(base) / "jobs" / f"{job_id}.lock"
    if fcntl is None or lock.is_symlink() or not lock.is_file():
        return False
    try:
        fd = os.open(lock, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _remove(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path)


def delete_job(base: Path, job_id: str) -> Tuple[int, int]:
    """Remove ``job_id`` whole: its video directory (scenes, audio, posters, caches, report), its
    run directory and thumbnails, its sidecar entries, and LAST its record and lock files - so a
    deletion that fails half-way leaves a job that is still listed and can be deleted again.
    Returns (files, bytes) removed. The caller decides whether the job may go (not running)."""
    _valid_job_id(job_id)
    base = Path(base)
    files, size = job_footprint(base, job_id)
    paths = job_paths(base, job_id)
    record_names = {f"{job_id}.json", f".{job_id}.rw", f"{job_id}.lock"}
    for p in paths:
        if not (p.parent == base / "jobs" and p.name in record_names):
            _remove(p)
    _drop_sidecar_entries(base, [job_id])
    for name in (f"{job_id}.json", f".{job_id}.rw", f"{job_id}.lock"):
        _remove(base / "jobs" / name)
    return files, size


def list_job_ids(base: Path) -> List[str]:
    jobs = Path(base) / "jobs"
    if not jobs.is_dir() or jobs.is_symlink():
        return []
    return sorted(p.stem for p in jobs.glob("job_*.json") if JOB_ID_RE.fullmatch(p.stem))


# ── storage usage ─────────────────────────────────────────────────────────────

def store_usage_bytes(base: Path) -> int:
    """Bytes the tenant's whole video store occupies on disk (records, videos, styles, sidecars);
    symlinks are counted by their own size and never followed."""
    base = Path(base)
    if base.is_symlink() or not base.is_dir():
        return 0
    return _footprint(base)[1]


def owner_of(base: Path, job_id: str) -> Optional[str]:
    return read_map(base, OWNERS_FILE).get(job_id)
