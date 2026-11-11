"""The project bundle ``corvin.project-bundle`` v1 (ADR-2323): building one (Decisions 7, 8, 9, 12, 16).

``build`` walks each registered project with ``os.scandir`` and NEVER follows a link: a symlink or a junction is left out
(``link``), its target and contents never read; a regular file is opened with ``O_NOFOLLOW`` (on Windows after an
``fsutil.is_link`` check) and its ``fstat`` must equal the walk's ``os.lstat`` (``st_dev``, ``st_ino``, ``st_size``; never
``DirEntry.stat()``, which reports zeros on Windows); a file with more than one hard link is left out (``hard_link``); a
directory on another filesystem than the project root is left out (``mount``). What ``bundle_rules.protected_entry``
names never travels; a nested CorvinOS ``project.yaml`` is left out (``nested_manifest``). Text files are scanned for
credential shapes while they are read; a hit refuses the export (``secret_shape``, naming the path, never the match) and
no ZIP remains. The envelope ``project-bundle.json`` is written last at the ZIP root.

Git history (part ``git_history``): ``git bundle create --branches --tags`` (never ``--all``) of the project repository and
of a nested ``docs/`` repository, through ``changes.neutral_git_args`` and ``changes.neutral_git_env``; the build checks that
its own bundle lists only ``refs/heads/*``, ``refs/tags/*`` and ``HEAD``.

``preview`` answers the same counts and ``left_out`` without writing anything.
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Optional

import yaml

from core.bundle_common import BundleCheckError
from core.bundle_common import secrets as _secrets

from . import bundle_rules, changes, manifest, registry
from .fsutil import dirent_is_junction, is_link

FORMAT = "corvin.project-bundle"
FORMAT_VERSION = 1
ENVELOPE_NAME = "project-bundle.json"
PROJECTS_PREFIX = "projects/"

#: ADR-2323 Decision 9 (the per-entry container rules read the forge keys of the same names)
LIMITS_MUTABLE: dict[str, int] = {
    "max_compressed_bytes": 500 << 20,
    "max_uncompressed_bytes": 2 << 30,
    "max_entries": 50_000,
    "max_compression_ratio": 100,
    "ratio_check_min_bytes": 1 << 20,
    "max_envelope_bytes": 4 << 20,
    "max_projects": 20,
    "max_depth": 64,
    "max_name_chars": 1000,
    "max_name_bytes": 1000,
    # the history scan of Decision 12
    "max_history_objects": 200_000,
    "max_history_read_bytes": 2 << 30,
    "max_history_blob_scan_bytes": 1 << 20,
}
LIMITS = MappingProxyType(LIMITS_MUTABLE)

#: the never-list of the export IS bundle_rules.protected_entry (the same function V1 uses; never a second list)
NEVER_LIST = bundle_rules.protected_entry

GENERATED = frozenset({"node_modules", ".venv", "__pycache__", "build", "dist", "target"})
PART_NAMES = ("files", "kb", "git_history", "videos_index", "generated")
LEFT_OUT_REASONS = (*bundle_rules.REASONS, "generated", "link", "hard_link", "mount", "nested_manifest",
                    "unportable_name", "special_file")
NOTES = ("Archives inside the project are not checked for secrets.",)

_IS_WINDOWS = os.name == "nt"
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
_scandir = os.scandir
_CHUNK = 1 << 20
_SNIFF = 8192
_MANIFEST_MAX = 64 * 1024
_GIT_TIMEOUT = 300

DETACHED_WARNING = "Git history needs a branch checked out"
NO_COMMITS_WARNING = "The repository has no commits yet; git history is not included"
UNREADABLE_WARNING = "Git history could not be read safely; it is not included"

# Decision 17 (English, fixed)
MESSAGES = {
    "secret_shape": "{path} looks like it contains a secret. Exclude or clean it, then export again.",
    "secret_in_history": ("{path} in the git history looks like it contains a secret. Export without git history, or remove "
                          "it from the history yourself."),
    "too_large": "The export would be larger than 500 MB. Leave out {part} and try again.",
    "too_many": "At most 20 projects fit in one bundle.",
    "not_found": "There is no such project.",
    "path_too_long": "{path} is nested too deep or named too long for a project bundle.",
    "unreadable": "The project could not be read ({detail}).",
}


class BundleRefused(Exception):
    """The export is refused. ``path`` is project-relative (never a match, never a commit); ``part`` names a part to leave
    out (``too_large``)."""

    def __init__(self, reason_class: str, path: Optional[str] = None, *, part: Optional[str] = None,
                 project_id: Optional[str] = None, detail: str = ""):
        self.reason_class = reason_class
        self.path = path
        self.part = part
        self.project_id = project_id
        tpl = MESSAGES.get(reason_class, "The export was refused ({reason}).")
        self.message = tpl.format(path=path or "", part=part or "files", reason=reason_class, detail=detail)
        super().__init__(self.message)


@dataclass(frozen=True)
class Parts:
    kb: bool = True
    git_history: bool = True
    generated: bool = False


@dataclass
class _File:
    rel: str
    abs: str
    size: int
    dev: int
    ino: int
    mtime: float
    mode: int
    part: str                      # files | kb | generated | videos_index


@dataclass
class _Walk:
    pid: str
    root: Path
    files: list[_File] = field(default_factory=list)
    left_out: list[dict[str, str]] = field(default_factory=list)
    part_bytes: dict[str, int] = field(default_factory=dict)


@dataclass
class BuildResult:
    path: Path
    envelope: dict[str, Any]
    counts: dict[str, int]
    warnings: list[str]


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────────────────────────────

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """A ULID: 48 bits of milliseconds and 80 random bits, Crockford base32 (26 characters)."""
    n = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in range(25, -1, -1))


_ARCHIVE_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00",
                  b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07", b"\x28\xb5\x2f\xfd")
_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")


def is_text(head: bytes) -> bool:
    """The secret scan covers TEXT files (ADR-2323 Decisions 5, 10): not an archive or compressed stream by its content (a
    nested archive is a file and is never opened), and no NUL byte in the first 8 KiB unless a UTF-16/32 byte order mark
    says the NULs are text."""
    if head.startswith(_ARCHIVE_MAGIC) or (len(head) >= 262 and head[257:262] == b"ustar"):
        return False
    return b"\x00" not in head or head.startswith(_BOMS)


def _zip_date(mtime: float) -> tuple[int, int, int, int, int, int]:
    t = time.localtime(max(mtime, 315532800))
    if t.tm_year > 2107:
        return (2107, 12, 31, 23, 59, 58)
    return (max(t.tm_year, 1980), t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def _git_base_env() -> dict[str, str]:
    return changes._env()


def _git(repo: Path, *args: str, timeout: int = _GIT_TIMEOUT, stdin=None, stdout=subprocess.PIPE,
         popen: bool = False):
    """Every git call of the bundle: ``neutral_git_args`` + ``neutral_git_env`` (no hooks, no filters, no transport)."""
    env = changes.neutral_git_env(repo, _git_base_env())
    argv = ["git", "--no-pager", "-C", str(repo), *changes.neutral_git_args(repo), *args]
    if popen:
        return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    return subprocess.run(argv, capture_output=stdout is subprocess.PIPE, timeout=timeout, env=env, stdin=stdin)


def _is_repo(d: Path) -> bool:
    g = d / ".git"
    return g.is_dir() and not is_link(g) and not is_link(d)


def _open_nofollow(path: str) -> int:
    """``os.open`` without following a link. Windows has no ``O_NOFOLLOW``: the path is checked with ``fsutil.is_link``
    first (the fstat/lstat comparison afterwards catches a swap)."""
    if (_IS_WINDOWS or not _O_NOFOLLOW) and is_link(path):
        raise OSError(errno.ELOOP, "is a link")
    return os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_BINARY)


def _project(tenant_id: str, project_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    e = registry.get(tenant_id, project_id) if isinstance(project_id, str) and manifest.PROJECT_ID_RE.match(project_id) else None
    if e is None or not isinstance(e.get("path"), str) or e.get("pending"):
        raise BundleRefused("not_found", project_id=project_id)
    root = Path(e["path"])
    if is_link(root) or not root.is_dir():
        raise BundleRefused("unreadable", project_id=project_id, detail="its folder is missing or is a link")
    try:
        man = manifest.load(root)
    except manifest.ManifestError as exc:
        raise BundleRefused("unreadable", project_id=project_id, detail="its project.yaml is invalid") from exc
    if man.get("id") != project_id or man.get("tenant") != tenant_id:
        raise BundleRefused("unreadable", project_id=project_id, detail="its project.yaml names another project")
    return root, e, man


def _nested_manifest(fd: int, size: int) -> bool:
    """A CorvinOS manifest below the root: a mapping with ``schema: 1`` and a valid ``id`` (ADR-2323 Decision 10, M13)."""
    if size > _MANIFEST_MAX:
        return False
    data = b""
    while len(data) < size:
        b = os.read(fd, size - len(data))
        if not b:
            break
        data += b
    return looks_like_manifest(data)


def looks_like_manifest(data: bytes) -> bool:
    try:
        text = data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("utf-8-sig")
        doc = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - not parseable: a plain file
        return False
    return (isinstance(doc, dict) and doc.get("schema") == manifest.SCHEMA and isinstance(doc.get("id"), str)
            and bool(manifest.PROJECT_ID_RE.match(doc["id"])))


# ── the walk ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _part_of(rel_parts: tuple[str, ...]) -> str:
    if len(rel_parts) >= 2 and rel_parts[0] == "docs" and rel_parts[1] == "kb":
        return "kb"
    if rel_parts in (("videos", "INDEX.md"), ("sources.md",)):
        return "videos_index"
    return "files"


def _walk(pid: str, root: Path, parts: Parts) -> _Walk:
    w = _Walk(pid=pid, root=root)
    root_st = os.lstat(root)
    seen_fold: set[str] = set()

    def leave(rel: str, reason: str) -> None:
        w.left_out.append({"path": rel, "reason": reason})

    stack: list[tuple[str, tuple[str, ...], str]] = [(str(root), (), "files")]
    while stack:
        d, dparts, dpart = stack.pop()
        try:
            with _scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            raise BundleRefused("unreadable", project_id=pid, detail=type(exc).__name__) from None
        for ent in entries:
            rel_parts = (*dparts, ent.name)
            rel = "/".join(rel_parts)
            if len(rel_parts) > LIMITS["max_depth"] - 2 or len(f"{PROJECTS_PREFIX}{pid}/{rel}".encode("utf-8", "surrogateescape")) > LIMITS["max_name_bytes"]:
                raise BundleRefused("path_too_long", rel, project_id=pid)
            reason = NEVER_LIST(PurePosixPath(*rel_parts))
            if reason is not None:
                leave(rel, reason)
                continue
            if ent.is_symlink() or dirent_is_junction(ent):
                leave(rel, "link")
                continue
            try:
                ent.name.encode("utf-8")
            except UnicodeEncodeError:
                leave(rel, "unportable_name")
                continue
            if bundle_rules.unportable_component(ent.name):
                leave(rel, "unportable_name")
                continue
            folded = unicodedata.normalize("NFC", rel).casefold()
            if folded in seen_fold:
                leave(rel, "unportable_name")
                continue
            try:
                st = os.lstat(ent.path)                  # never ent.stat(): zeros for st_ino/st_dev/st_nlink on Windows (N8)
            except OSError:
                leave(rel, "special_file")
                continue
            if stat.S_ISLNK(st.st_mode):
                leave(rel, "link")
                continue
            if stat.S_ISDIR(st.st_mode):
                if st.st_dev != root_st.st_dev:
                    leave(rel, "mount")
                    continue
                part = dpart
                if ent.name in GENERATED:
                    if not parts.generated:
                        leave(rel, "generated")
                        continue
                    part = "generated"
                if part == "files" and rel_parts == ("docs", "kb"):
                    if not parts.kb:
                        continue
                    part = "kb"
                seen_fold.add(folded)
                stack.append((ent.path, rel_parts, part))
                continue
            if not stat.S_ISREG(st.st_mode):
                leave(rel, "special_file")
                continue
            if st.st_nlink > 1:
                leave(rel, "hard_link")
                continue
            if rel_parts != (manifest.FILE,) and ent.name.casefold() == manifest.FILE:
                try:
                    fd = _open_nofollow(ent.path)
                except OSError:
                    leave(rel, "link")
                    continue
                try:
                    if _nested_manifest(fd, st.st_size):
                        leave(rel, "nested_manifest")
                        continue
                finally:
                    os.close(fd)
            seen_fold.add(folded)
            fpart = dpart if dpart != "files" else _part_of(rel_parts)
            w.files.append(_File(rel=rel, abs=ent.path, size=st.st_size, dev=st.st_dev, ino=st.st_ino, mtime=st.st_mtime,
                                 mode=st.st_mode, part=fpart))
            w.part_bytes[fpart] = w.part_bytes.get(fpart, 0) + st.st_size
    w.files.sort(key=lambda f: f.rel)
    w.left_out.sort(key=lambda r: r["path"])
    return w


def _check_totals(walks: list[_Walk], extra_bytes: int = 0, extra_entries: int = 0, history_bytes: int = 0) -> None:
    total = sum(f.size for w in walks for f in w.files) + extra_bytes
    entries = sum(len(w.files) for w in walks) + extra_entries + 1
    if total > LIMITS["max_uncompressed_bytes"] or entries > LIMITS["max_entries"]:
        sizes: dict[str, int] = {}
        for w in walks:
            for k, v in w.part_bytes.items():
                sizes[k] = sizes.get(k, 0) + v
        if history_bytes:
            sizes["git_history"] = history_bytes
        optional = {k: v for k, v in sizes.items() if k in ("generated", "kb", "git_history") and v > 0}
        part = max(optional, key=optional.get) if optional else "files"
        raise BundleRefused("too_large", part=part)


# ── git history ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _head_branch(repo: Path) -> Optional[str]:
    r = _git(repo, "symbolic-ref", "-q", "HEAD", timeout=30)
    ref = r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else ""
    return ref if ref.startswith("refs/heads/") else None


def _has_commit(repo: Path, ref: str) -> bool:
    return _git(repo, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}", timeout=30).returncode == 0


def _history_repos(root: Path) -> list[tuple[str, Path, str]]:
    """(part path inside the bundle, repository, envelope key) for the project repository and a nested docs repository."""
    out = []
    if _is_repo(root):
        out.append((bundle_rules.HISTORY_BUNDLE, root, "head"))
        docs = root / "docs"
        if docs.is_dir() and not is_link(docs) and _is_repo(docs):
            out.append((bundle_rules.DOCS_HISTORY_BUNDLE, docs, "docs_head"))
    return out


def _history_usable(repo: Path, warnings: list[str]) -> Optional[str]:
    if changes.repo_problem(repo) is not None:
        warnings.append(UNREADABLE_WARNING)
        return None
    try:
        head = _head_branch(repo)
    except (changes.ChangesError, OSError, subprocess.TimeoutExpired):
        warnings.append(UNREADABLE_WARNING)
        return None
    if head is None:
        warnings.append(DETACHED_WARNING)
        return None
    if not _has_commit(repo, head):
        warnings.append(NO_COMMITS_WARNING)
        return None
    return head


def _create_history_bundle(repo: Path, target: Path) -> None:
    """``git bundle create --branches --tags`` (never ``--all``: remote-tracking refs, stash and notes stay home, REVIEW-0062 N4)
    and the self-check of its ref list."""
    r = _git(repo, "bundle", "create", str(target), "--branches", "--tags")
    if r.returncode != 0 or not target.is_file():
        raise BundleRefused("unreadable", detail="git could not write the history")
    heads = _git(repo, "bundle", "list-heads", str(target), timeout=60)
    if heads.returncode != 0:
        raise BundleRefused("unreadable", detail="git could not read its own history bundle")
    for line in heads.stdout.decode("utf-8", "replace").splitlines():
        ref = line.split(" ", 1)[1] if " " in line else ""
        if not (ref == "HEAD" or ref.startswith("refs/heads/") or ref.startswith("refs/tags/")):
            raise BundleRefused("unreadable", detail="the history bundle names a ref other than a branch or a tag")


# ── preview and build ────────────────────────────────────────────────────────────────────────────────────────────────────

def _manifest_bytes(man: dict[str, Any]) -> bytes:
    """``project.yaml`` as it travels: the ``tenant`` key removed, everything else as loaded."""
    return manifest.dumps({k: v for k, v in man.items() if k != "tenant"}).encode("utf-8")


def _ids(project_ids: list[str]) -> list[str]:
    ids = list(dict.fromkeys(project_ids))
    if not ids:
        raise BundleRefused("not_found")
    if len(ids) > LIMITS["max_projects"]:
        raise BundleRefused("too_many")
    return ids


def _classification(tenant_id: str, pid: str, man: dict[str, Any]) -> str:
    prot = registry.protection(tenant_id, pid)
    if prot and prot.get("classification") in manifest.CLASSES:
        return prot["classification"]
    return manifest.legacy_class(man)


def _parts_rows(w: _Walk, parts: Parts, history: dict[str, Any], history_note: Optional[str]) -> list[dict[str, Any]]:
    kb_present = any(f.part == "kb" for f in w.files)
    rows = [{"name": "files", "included": True},
            {"name": "kb", "included": bool(parts.kb and kb_present)},
            {"name": "git_history", "included": bool(history)},
            {"name": "videos_index", "included": any(f.part == "videos_index" for f in w.files)},
            {"name": "generated", "included": bool(parts.generated)}]
    if history_note and not history:
        rows[2]["note"] = history_note
    return rows


def _project_head(tenant_id: str, pid: str, man: dict[str, Any], e: dict[str, Any]) -> dict[str, Any]:
    return {"id": pid, "title": man.get("title") or pid, "template": man.get("template") or "",
            "role": man.get("role") or "", "classification": _classification(tenant_id, pid, man),
            "docs_mode": man.get("docs_mode", "nested"), "features": dict(man.get("features") or {})}


def preview(tenant_id: str, project_ids: list[str], parts: Parts, *, history_available: bool = True) -> dict[str, Any]:
    """What an export would contain - counts, bytes, ``left_out`` with reasons, commits, knowledge-base entries - without
    writing anything."""
    out = []
    warnings: list[str] = []
    for pid in _ids(project_ids):
        root, e, man = _project(tenant_id, pid)
        w = _walk(pid, root, parts)
        commits: Optional[int] = None
        history_ok = False
        if parts.git_history and history_available and _is_repo(root):
            head = _history_usable(root, warnings)
            if head is not None:
                history_ok = True
                r = _git(root, "rev-list", "--count", "HEAD", timeout=60)
                with contextlib.suppress(ValueError):
                    commits = int(r.stdout.decode().strip()) if r.returncode == 0 else None
        note = None if history_available else "unavailable"
        rows = _parts_rows(w, parts, {"x": 1} if history_ok else {}, note)
        kb_entries = sum(1 for f in w.files if f.part == "kb" and f.rel.endswith(".md"))
        nbytes = sum(len(_manifest_bytes(man)) if f.rel == manifest.FILE else f.size for f in w.files)
        out.append({**_project_head(tenant_id, pid, man, e), "parts": rows, "left_out": w.left_out,
                    "counts": {"files": len(w.files), "bytes": nbytes},
                    "commits": commits, "kb_entries": kb_entries})
    return {"projects": out, "notes": list(NOTES), "warnings": list(dict.fromkeys(warnings))}


def _read_into(zf: zipfile.ZipFile, name: str, f: _File, scanner: Optional[_secrets.StreamScanner],
               sniff_cb: Callable[[bytes], Optional[_secrets.StreamScanner]], progress_cb) -> tuple[Optional[str], int]:
    """Copy one file into the ZIP without following a link. Returns ``(sha256, size)`` or ``(None, 0)`` when the file turned
    out to be a link / changed since the walk (the entry is then not written)."""
    try:
        fd = _open_nofollow(f.abs)
    except OSError:
        return None, 0
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino, st.st_size) != (f.dev, f.ino, f.size) or not stat.S_ISREG(st.st_mode):
            return None, 0
        info = zipfile.ZipInfo(name, date_time=_zip_date(f.mtime))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = ((0o100755 if f.mode & 0o111 else 0o100644) & 0xFFFF) << 16
        info.file_size = f.size
        h = hashlib.sha256()
        n = 0
        first = True
        sc = scanner
        with zf.open(info, "w", force_zip64=f.size > (1 << 30)) as dst:
            while n < f.size:
                b = os.read(fd, min(_CHUNK, f.size - n))
                if not b:
                    break
                if first:
                    sc = sniff_cb(b[:_SNIFF])
                    first = False
                if sc is not None:
                    sc.feed(b)
                h.update(b)
                dst.write(b)
                n += len(b)
                progress_cb(len(b))
        if first:
            sc = sniff_cb(b"")
        if sc is not None:
            sc.close()
        return h.hexdigest(), n
    finally:
        os.close(fd)


def build(tenant_id: str, project_ids: list[str], parts: Parts, *, out: Path,
          progress: Optional[Callable[[int, int], None]] = None,
          history_scan: Optional[Callable[[Path], None]] = None) -> BuildResult:
    """Build the bundle into *out* (written through a private temp file in the same directory; nothing remains on a
    refusal). ``history_scan(repo)`` is the history gate (T-0410); it runs before anything is written."""
    out = Path(out)
    ids = _ids(project_ids)
    warnings: list[str] = []
    loaded = [(pid, *_project(tenant_id, pid)) for pid in ids]
    walks = [_walk(pid, root, parts) for pid, root, _e, _m in loaded]
    _check_totals(walks, extra_entries=0)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".bundle-", dir=str(out.parent)))
    with contextlib.suppress(OSError):
        os.chmod(work, 0o700)
    tmp_zip = work / "bundle.zip"
    done_files = [0, 0]

    def tick(nbytes: int) -> None:
        done_files[1] += nbytes
        if progress is not None:
            progress(done_files[0], done_files[1])

    try:
        # git history first: bundles are created (and, with the T-0410 gate, scanned) before the ZIP is opened
        histories: dict[str, list[tuple[str, Path, str, str]]] = {}
        if parts.git_history:
            for (pid, root, _e, _m) in loaded:
                rows = []
                for inner, repo, key in _history_repos(root):
                    head = _history_usable(repo, warnings)
                    if head is None:
                        if key == "head":
                            break
                        continue
                    if history_scan is not None:
                        history_scan(repo)
                    target = work / f"{pid}-{key}.bundle"
                    _create_history_bundle(repo, target)
                    rows.append((inner, target, key, head))
                histories[pid] = rows
        hist_bytes = sum(t.stat().st_size for rows in histories.values() for _i, t, _k, _h in rows)
        _check_totals(walks, extra_bytes=hist_bytes, extra_entries=sum(len(r) for r in histories.values()),
                      history_bytes=hist_bytes)
        envelope_projects = []
        fd = os.open(tmp_zip, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_BINARY, 0o600)
        with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for (pid, root, e, man), w in zip(loaded, walks):
                listed: list[dict[str, Any]] = []
                left = list(w.left_out)
                prefix = f"{PROJECTS_PREFIX}{pid}/"
                for f in w.files:
                    name = prefix + f.rel
                    if f.rel == manifest.FILE:
                        data = _manifest_bytes(man)
                        try:
                            _secrets.scan_bytes(data, f.rel)
                        except BundleCheckError:
                            raise BundleRefused("secret_shape", f.rel, project_id=pid) from None
                        info = zipfile.ZipInfo(name, date_time=_zip_date(f.mtime))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        info.external_attr = (0o100644 & 0xFFFF) << 16
                        zf.writestr(info, data)
                        listed.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
                        done_files[0] += 1
                        tick(len(data))
                        continue

                    def sniff(head: bytes, _rel=f.rel) -> Optional[_secrets.StreamScanner]:
                        return _secrets.StreamScanner(_rel) if is_text(head) else None
                    try:
                        sha, size = _read_into(zf, name, f, None, sniff, tick)
                    except BundleCheckError:
                        raise BundleRefused("secret_shape", f.rel, project_id=pid) from None
                    if sha is None:
                        left.append({"path": f.rel, "reason": "link"})      # swapped or changed since the walk: never read
                        continue
                    listed.append({"path": name, "sha256": sha, "size": size})
                    done_files[0] += 1
                git: dict[str, str] = {}
                for inner, target, key, head in histories.get(pid, []):
                    name = prefix + inner
                    h = hashlib.sha256()
                    size = 0
                    info = zipfile.ZipInfo(name, date_time=_zip_date(time.time()))
                    info.compress_type = zipfile.ZIP_STORED            # a pack is already compressed
                    info.external_attr = (0o100644 & 0xFFFF) << 16
                    info.file_size = target.stat().st_size
                    with open(target, "rb") as src, zf.open(info, "w", force_zip64=info.file_size > (1 << 30)) as dst:
                        while True:
                            b = src.read(_CHUNK)
                            if not b:
                                break
                            h.update(b)
                            dst.write(b)
                            size += len(b)
                            tick(len(b))
                    listed.append({"path": name, "sha256": h.hexdigest(), "size": size})
                    git[key] = head
                left.sort(key=lambda r: r["path"])
                proj = {**_project_head(tenant_id, pid, man, e), "export_id": new_ulid(),
                        "source_project_uid": registry.ensure_uid(tenant_id, pid),
                        "parts": _parts_rows(w, parts, git, None), "left_out": left, "files": listed,
                        "counts": {"files": len(listed), "bytes": sum(x["size"] for x in listed)}}
                if git:
                    proj["git"] = git
                envelope_projects.append(proj)
            envelope = {"format": FORMAT, "format_version": FORMAT_VERSION,
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "exporter": {"app": "corvinos", "version": _version()}, "origin": "unverified",
                        "projects": envelope_projects}
            raw_env = json.dumps(envelope, ensure_ascii=False, sort_keys=False, indent=1).encode("utf-8")
            if len(raw_env) > LIMITS["max_envelope_bytes"]:
                raise BundleRefused("too_large", part="files")
            info = zipfile.ZipInfo(ENVELOPE_NAME, date_time=_zip_date(time.time()))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 & 0xFFFF) << 16
            zf.writestr(info, raw_env)
        if tmp_zip.stat().st_size > LIMITS["max_compressed_bytes"]:
            sizes = {k: v for w in walks for k, v in w.part_bytes.items() if k in ("generated", "kb")}
            if hist_bytes:
                sizes["git_history"] = hist_bytes
            raise BundleRefused("too_large", part=max(sizes, key=sizes.get) if sizes else "files")
        os.replace(tmp_zip, out)
        with contextlib.suppress(OSError):
            os.chmod(out, 0o600)
        counts = {"files": sum(p["counts"]["files"] for p in envelope_projects),
                  "bytes": sum(p["counts"]["bytes"] for p in envelope_projects)}
        return BuildResult(path=out, envelope=envelope, counts=counts, warnings=list(dict.fromkeys(warnings)))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _version() -> str:
    try:
        from importlib.metadata import version  # noqa: PLC0415
        return version("corvinos")
    except Exception:  # noqa: BLE001
        return "0.0.0"
