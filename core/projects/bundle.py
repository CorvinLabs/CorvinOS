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
    g = d / bundle_rules.GIT_DIR
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


# ── the history gate (T-0410, ADR-2323 Decision 12) ─────────────────────────────────────────────────────────────────────

def scan_history(repo: Path, *, prefix: str = "", limit: int = 100) -> list[str]:
    """Every object of ``rev-list --objects --branches --tags`` - exactly the refs the bundle carries - read through ONE
    ``cat-file --batch`` process, both under ``neutral_git_args`` + ``neutral_git_env`` (no filter, textconv or hook can run).
    A tree entry or blob path that ``bundle_rules.history_secret_path`` names, or a blob <= 1 MiB with a credential shape,
    is a finding: its path (``prefix`` + path), never the commit or the match. Budget: ``max_history_objects`` objects and
    ``max_history_read_bytes`` read; beyond -> ``too_large`` naming the ``git_history`` part."""
    import threading  # noqa: PLC0415
    r = _git(repo, "rev-list", "--objects", "--branches", "--tags", timeout=_GIT_TIMEOUT)
    if r.returncode != 0:
        raise BundleRefused("unreadable", detail="git could not list the history")
    objs: list[tuple[str, Optional[str]]] = []
    for line in r.stdout.decode("utf-8", "surrogateescape").splitlines():
        sha, _sp, path = line.partition(" ")
        if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
            continue
        objs.append((sha, path if _sp else None))
        if len(objs) > LIMITS["max_history_objects"]:
            raise BundleRefused("too_large", part="git_history")
    findings: list[str] = []

    def found(path: str) -> None:
        shown = prefix + path
        if shown not in findings:
            findings.append(shown)

    proc = _git(repo, "cat-file", "--batch", popen=True)
    feed_err: list[BaseException] = []

    def feed() -> None:
        try:
            for sha, _p in objs:
                proc.stdin.write(sha.encode() + b"\n")
            proc.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            feed_err.append(exc)
    t = threading.Thread(target=feed, name="bundle-history-feed", daemon=True)
    t.start()
    read = 0
    out = proc.stdout
    try:
        for sha, path in objs:
            header = out.readline()
            parts = header.split()
            if len(parts) != 3 or parts[1] == b"missing":
                raise BundleRefused("unreadable", detail="git could not read an object of the history")
            kind, size = parts[1], int(parts[2])
            read += size
            if read > LIMITS["max_history_read_bytes"]:
                raise BundleRefused("too_large", part="git_history")
            if kind == b"blob" and size <= LIMITS["max_history_blob_scan_bytes"]:
                data = _read_exact(out, size)
                if path and bundle_rules.history_secret_path(path):
                    found(path)
                else:
                    try:
                        _secrets.scan_bytes(data, path or "")
                    except BundleCheckError:
                        found(path or "(unnamed blob)")
            elif kind == b"tree":
                data = _read_exact(out, size)
                base = (path + "/") if path else ""
                for name in _tree_names(data, len(sha) // 2):
                    if bundle_rules.history_secret_path(base + name):
                        found(base + name)
            else:
                if kind == b"blob" and path and bundle_rules.history_secret_path(path):
                    found(path)
                _skip(out, size)
            out.read(1)                                              # the LF after every object
            if len(findings) >= limit:
                break
    finally:
        with contextlib.suppress(OSError):
            proc.kill()
        proc.wait(timeout=30)
        t.join(timeout=5)
    return findings


def _read_exact(fh, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        b = fh.read(n - len(buf))
        if not b:
            raise BundleRefused("unreadable", detail="git stopped while reading the history")
        buf += b
    return bytes(buf)


def _skip(fh, n: int) -> None:
    while n > 0:
        b = fh.read(min(n, _CHUNK))
        if not b:
            raise BundleRefused("unreadable", detail="git stopped while reading the history")
        n -= len(b)


def _tree_names(data: bytes, idlen: int = 20) -> list[str]:
    """Entry names of a raw tree object: ``<mode> <name>\\0<binary id>`` repeated (the id is 20 bytes, 32 with SHA-256)."""
    names: list[str] = []
    i = 0
    while i < len(data):
        sp = data.find(b" ", i)
        nul = data.find(b"\0", sp + 1)
        if sp < 0 or nul < 0:
            break
        names.append(data[sp + 1:nul].decode("utf-8", "surrogateescape"))
        i = nul + 1 + idlen
    return names


def history_gate(repo: Path, prefix: str = "") -> None:
    hits = scan_history(repo, prefix=prefix, limit=1)
    if hits:
        raise BundleRefused("secret_in_history", hits[0])


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
        findings = 0
        history_ok = False
        if parts.git_history and history_available and _is_repo(root):
            head = _history_usable(root, warnings)
            if head is not None:
                history_ok = True
                r = _git(root, "rev-list", "--count", "HEAD", timeout=60)
                with contextlib.suppress(ValueError):
                    commits = int(r.stdout.decode().strip()) if r.returncode == 0 else None
                for _inner, repo, key in _history_repos(root):
                    try:
                        findings += len(scan_history(repo, prefix="docs/" if key == "docs_head" else ""))
                    except BundleRefused:
                        findings += 1
        note = None if history_available else "unavailable"
        rows = _parts_rows(w, parts, {"x": 1} if history_ok else {}, note)
        kb_entries = sum(1 for f in w.files if f.part == "kb" and f.rel.endswith(".md"))
        nbytes = sum(len(_manifest_bytes(man)) if f.rel == manifest.FILE else f.size for f in w.files)
        out.append({**_project_head(tenant_id, pid, man, e), "parts": rows, "left_out": w.left_out,
                    "counts": {"files": len(w.files), "bytes": nbytes},
                    "commits": commits, "kb_entries": kb_entries, "history_findings": findings})
    return {"projects": out, "notes": list(NOTES), "warnings": list(dict.fromkeys(warnings))}


class _Restow(Exception):
    """An entry deflated beyond the 100:1 rule of V1 (a sparse file, a run of zeros): the ZIP is written again with that entry
    stored, so an export always passes its own V1."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def _compresses_too_well(size: int, head: bytes) -> bool:
    """A large file whose first MiB deflates beyond 50:1 is stored, not deflated (a sparse file would break V1's 100:1)."""
    if size < LIMITS["ratio_check_min_bytes"] or not head:
        return False
    import zlib  # noqa: PLC0415
    return len(head) > 50 * len(zlib.compress(head, 1))


def _ratio_breach(size: int, packed: int) -> bool:
    return size >= LIMITS["ratio_check_min_bytes"] and size > LIMITS["max_compression_ratio"] * max(packed, 1)


def _read_into(zf: zipfile.ZipFile, name: str, f: _File, scanner: Optional[_secrets.StreamScanner],
               sniff_cb: Callable[[bytes], Optional[_secrets.StreamScanner]], progress_cb,
               stored: frozenset = frozenset()) -> tuple[Optional[str], int]:
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
        b0 = os.read(fd, min(_CHUNK, f.size)) if f.size else b""
        info = zipfile.ZipInfo(name, date_time=_zip_date(f.mtime))
        info.compress_type = zipfile.ZIP_STORED if name in stored or _compresses_too_well(f.size, b0) else zipfile.ZIP_DEFLATED
        info.external_attr = ((0o100755 if f.mode & 0o111 else 0o100644) & 0xFFFF) << 16
        info.file_size = f.size
        h = hashlib.sha256()
        n = 0
        first = True
        sc = scanner
        with zf.open(info, "w", force_zip64=f.size > (1 << 30)) as dst:
            while n < f.size:
                if b0:
                    b, b0 = b0, b""
                else:
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
        if info.compress_type == zipfile.ZIP_DEFLATED and _ratio_breach(n, info.compress_size):
            raise _Restow(name)
        return h.hexdigest(), n
    finally:
        os.close(fd)


def _write_zip(tenant_id, loaded, walks, parts: Parts, histories, tmp_zip: Path, stored: frozenset, tick, done_files) -> dict:
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
                    sha, size = _read_into(zf, name, f, None, sniff, tick, stored)
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
    return envelope


def build(tenant_id: str, project_ids: list[str], parts: Parts, *, out: Path,
          progress: Optional[Callable[[int, int], None]] = None,
          history_scan: Optional[Callable[[Path, str], None]] = None) -> BuildResult:
    """Build the bundle into *out* (written through a private temp file in the same directory; nothing remains on a
    refusal). The history gate (T-0410, :func:`history_gate`; ``history_scan`` replaces it in tests only) runs on every
    repository whose history is included, before anything is written."""
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
                    (history_scan or history_gate)(repo, "docs/" if key == "docs_head" else "")
                    target = work / f"{pid}-{key}.bundle"
                    _create_history_bundle(repo, target)
                    rows.append((inner, target, key, head))
                histories[pid] = rows
        hist_bytes = sum(t.stat().st_size for rows in histories.values() for _i, t, _k, _h in rows)
        _check_totals(walks, extra_bytes=hist_bytes, extra_entries=sum(len(r) for r in histories.values()),
                      history_bytes=hist_bytes)
        stored: set[str] = set()
        for _attempt in range(64):
            try:
                envelope = _write_zip(tenant_id, loaded, walks, parts, histories, tmp_zip, frozenset(stored), tick, done_files)
                break
            except _Restow as rs:
                stored.add(rs.name)
                with contextlib.suppress(OSError):
                    tmp_zip.unlink()
                done_files[0] = done_files[1] = 0
        else:
            raise BundleRefused("too_large", part="files")
        envelope_projects = envelope["projects"]
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


# ── validation V1-V6 (T-0398, ADR-2323 Decisions 2, 10, 17) ─────────────────────────────────────────────────────────────
#
# A pure function over the staged file: ``zipfile.ZipFile(path)`` on a seekable file, entries read in 1 MiB chunks through
# the budget, nothing extracted, nothing written. The first failing stage names itself (``reason_class`` = the stage) and
# never the content.

from core.bundle_common import names as _names  # noqa: E402
from core.bundle_common import ratio as _ratio  # noqa: E402

STAGES = ("container", "envelope", "integrity", "manifest", "staleness", "secrets")

#: V1's protected-entry predicate IS the export's never-list (REVIEW-0062 N3)
V1_PROTECTED = bundle_rules.protected_entry

V_MESSAGES = {
    "container": "This file is not a valid project bundle ({detail}).",
    "protected": "This file is not a valid project bundle (it contains a protected entry: {path}).",
    "envelope": "This file is not a valid project bundle ({detail}).",
    "integrity": "The bundle's contents do not match its manifest.",
    "manifest": "The project description in the bundle is invalid.",
    "nested": "The bundle contains a project inside the project; it was not imported.",
    "secrets": "{path} in the bundle looks like it contains a secret; the bundle was not imported.",
}

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_UID_RE = re.compile(r"^[0-9a-f]{32}$")
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_HEAD_RE = re.compile(r"^refs/heads/[A-Za-z0-9._/+-]{1,200}$")
_TOP_KEYS = {"format", "format_version", "created_at", "exporter", "origin", "projects"}
_PROJECT_KEYS = {"id", "title", "template", "role", "classification", "docs_mode", "features", "export_id",
                 "source_project_uid", "parts", "git", "left_out", "files", "counts"}
_PROJECT_REQUIRED = _PROJECT_KEYS - {"git"}


class _Fail(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


def _fail(stage: str, detail: str = "", *, key: Optional[str] = None, path: str = "") -> _Fail:
    return _Fail(stage, V_MESSAGES[key or stage].format(detail=detail, path=path))


def _limits_for_container() -> dict[str, int]:
    return dict(LIMITS)


def _eocd_entries(fh) -> int:
    """The entry count of the end-of-central-directory record, read BEFORE ``zipfile`` parses the central directory (a
    hostile directory of millions of entries must not be materialised first)."""
    try:
        end = zipfile._EndRecData(fh)          # noqa: SLF001 - stable since Python 2; the only way to read the count first
    except (OSError, ValueError, struct_error()):
        end = None
    if not end:
        raise _fail("container", "the file is not a readable ZIP archive")
    return int(end[zipfile._ECD_ENTRIES_TOTAL])  # noqa: SLF001


def struct_error():
    import struct  # noqa: PLC0415
    return struct.error


def _entry_rule_factory(protected_hit: dict[str, str]):
    seen_nfc: set[str] = set()

    def rule(info: zipfile.ZipInfo) -> Optional[tuple[str, str]]:
        name = info.filename
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            return "an entry uses an unsupported compression method", "unsupported compression"
        nfc = unicodedata.normalize("NFC", name.rstrip("/")).casefold()
        if nfc in seen_nfc:
            return "two entries have the same name after Unicode normalisation", "NFC duplicate"
        seen_nfc.add(nfc)
        parts = name.rstrip("/").split("/")
        if len(parts) > LIMITS["max_depth"]:
            return f"an entry path is nested deeper than {LIMITS['max_depth']} levels", "too deep"
        if any(bundle_rules.unportable_component(p) for p in parts):
            return ("an entry name is not valid on Windows (a device name, a reserved character or a trailing dot or space)",
                    "unportable name")
        if name == ENVELOPE_NAME:
            return None
        if parts[0] != PROJECTS_PREFIX.rstrip("/") or len(parts) < 2 or not manifest.PROJECT_ID_RE.match(parts[1]):
            return "an entry lies outside projects/<id>/", "outside projects/<id>/"
        if len(parts) == 2 and not info.is_dir():
            return "an entry lies outside projects/<id>/", "outside projects/<id>/"
        rel = parts[2:]
        if rel and (V1_PROTECTED(PurePosixPath(*rel)) is not None or bundle_rules.export_payload_problem(rel)):
            protected_hit["path"] = name.rstrip("/")
            return "protected entry", "protected entry"
        return None
    return rule


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: _ratio.Budget, sink: Callable[[bytes], None],
                stage: str = "integrity") -> None:
    try:
        with zf.open(info) as src:
            while True:
                b = src.read(_CHUNK)
                if not b:
                    break
                try:
                    budget.spend(len(b), info.filename)
                except BundleCheckError as exc:
                    raise _fail("container", exc.detail) from None
                sink(b)
    except _Fail:
        raise
    except BundleCheckError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, RuntimeError, NotImplementedError, ValueError,
            __import__("zlib").error):
        raise _fail(stage) from None


def _parse_envelope(raw: bytes) -> dict[str, Any]:
    def bad(detail: str) -> _Fail:
        return _fail("envelope", f"its manifest {detail}")
    try:
        env = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise bad("is not UTF-8 JSON") from None
    if not isinstance(env, dict) or set(env) != _TOP_KEYS:
        raise bad("has missing or unknown keys")
    if env["format"] != FORMAT or type(env["format_version"]) is not int or env["format_version"] != FORMAT_VERSION:
        raise bad("names another format or version")
    if env["origin"] != "unverified" or not isinstance(env["created_at"], str) or len(env["created_at"]) > 40:
        raise bad("has an invalid origin or date")
    ex = env["exporter"]
    if not isinstance(ex, dict) or set(ex) != {"app", "version"} or not all(isinstance(v, str) and len(v) <= 64 for v in ex.values()):
        raise bad("has an invalid exporter")
    projects = env["projects"]
    if not isinstance(projects, list) or not 1 <= len(projects) <= LIMITS["max_projects"]:
        raise bad(f"must list 1 to {LIMITS['max_projects']} projects")
    ids: set[str] = set()
    total_files = 0
    for p in projects:
        if not isinstance(p, dict) or not _PROJECT_REQUIRED <= set(p) or not set(p) <= _PROJECT_KEYS:
            raise bad("has a project with missing or unknown keys")
        pid = p["id"]
        if not isinstance(pid, str) or not manifest.PROJECT_ID_RE.match(pid) or pid in ids:
            raise bad("has an invalid or repeated project id")
        ids.add(pid)
        if not isinstance(p["title"], str) or not 0 < len(p["title"]) <= 120:
            raise bad("has an invalid title")
        if not isinstance(p["template"], str) or len(p["template"]) > 64 or not isinstance(p["role"], str) or len(p["role"]) > 32:
            raise bad("has an invalid template or role")
        if p["classification"] not in manifest.CLASSES or not isinstance(p["docs_mode"], str) or len(p["docs_mode"]) > 16:
            raise bad("has an invalid classification or docs mode")
        feats = p["features"]
        if not isinstance(feats, dict) or len(feats) > 64 or not all(
                isinstance(k, str) and manifest.FEATURE_ID_RE.match(k) and isinstance(v, bool) for k, v in feats.items()):
            raise bad("has invalid features")
        if not isinstance(p["export_id"], str) or not _ULID_RE.match(p["export_id"]):
            raise bad("has an invalid export id")
        if not isinstance(p["source_project_uid"], str) or not _UID_RE.match(p["source_project_uid"]):
            raise bad("has an invalid source id")
        parts = p["parts"]
        if not isinstance(parts, list) or len(parts) > len(PART_NAMES):
            raise bad("has invalid parts")
        seen_parts: dict[str, bool] = {}
        for row in parts:
            if (not isinstance(row, dict) or not {"name", "included"} <= set(row) <= {"name", "included", "note"}
                    or row["name"] not in PART_NAMES or row["name"] in seen_parts or type(row["included"]) is not bool
                    or ("note" in row and (not isinstance(row["note"], str) or len(row["note"]) > 120))):
                raise bad("has invalid parts")
            seen_parts[row["name"]] = row["included"]
        git = p.get("git")
        if seen_parts.get("git_history"):
            if (not isinstance(git, dict) or "head" not in git or not set(git) <= {"head", "docs_head"}
                    or not all(isinstance(v, str) and _HEAD_RE.match(v) and ".." not in v and not v.endswith((".lock", "/", "."))
                               for v in git.values())):
                raise bad("has git history without a valid branch")
        elif git is not None:
            raise bad("names git history that is not included")
        lo = p["left_out"]
        if not isinstance(lo, list) or len(lo) > LIMITS["max_entries"] or not all(
                isinstance(r, dict) and set(r) == {"path", "reason"} and isinstance(r["path"], str) and len(r["path"]) <= 1000
                and r["reason"] in LEFT_OUT_REASONS for r in lo):
            raise bad("has an invalid list of left-out files")
        files = p["files"]
        if not isinstance(files, list) or len(files) > LIMITS["max_entries"]:
            raise bad("has an invalid file list")
        prefix = f"{PROJECTS_PREFIX}{pid}/"
        nbytes = 0
        for f in files:
            if (not isinstance(f, dict) or set(f) != {"path", "sha256", "size"} or not isinstance(f["path"], str)
                    or not f["path"].startswith(prefix) or len(f["path"]) > 1000 or not isinstance(f["sha256"], str)
                    or not _SHA_RE.match(f["sha256"]) or type(f["size"]) is not int or f["size"] < 0):
                raise bad("has an invalid file entry")
            nbytes += f["size"]
        total_files += len(files)
        c = p["counts"]
        if (not isinstance(c, dict) or set(c) != {"files", "bytes"} or c.get("files") != len(files) or c.get("bytes") != nbytes):
            raise bad("has counts that do not match its file list")
        listed = {f["path"][len(prefix):] for f in files}
        for inner, key in ((bundle_rules.HISTORY_BUNDLE, "head"), (bundle_rules.DOCS_HISTORY_BUNDLE, "docs_head")):
            has_key = isinstance(git, dict) and key in git
            if (inner in listed) != has_key:
                raise bad("lists git history that does not match its parts")
    if total_files > LIMITS["max_entries"]:
        raise bad("lists too many files")
    return env


def _decode_yaml(data: bytes) -> Any:
    text = data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("utf-8-sig")
    if "\x00" in text:
        raise UnicodeError("NUL")
    return yaml.safe_load(text)


def _check_manifest(pid: str, data: Optional[bytes]) -> dict[str, Any]:
    if data is None or len(data) > _MANIFEST_MAX:
        raise _fail("manifest")
    try:
        doc = _decode_yaml(data)
    except Exception:  # noqa: BLE001 - not YAML / not text
        raise _fail("manifest") from None
    if not isinstance(doc, dict) or "tenant" in doc:
        raise _fail("manifest")
    try:
        manifest.validate({**doc, "tenant": "_default"})
    except manifest.ManifestError:
        raise _fail("manifest") from None
    if doc.get("id") != pid:
        raise _fail("manifest")
    return doc


def _staleness(doc: dict[str, Any], known: Optional[Any]) -> list[str]:
    if not known:
        return []
    warns = []
    tpl = doc.get("template")
    templates = set(known.get("templates") or ())
    if tpl and tpl not in templates and str(tpl).split("@", 1)[0] not in templates:
        warns.append(f"The template {tpl} is not available on this installation; the project imports without it.")
    feats = set(known.get("features") or ())
    for f, on in (doc.get("features") or {}).items():
        if on and f not in feats:
            warns.append(f"The feature {f} is not available on this installation.")
    return warns


def validate(path: Path, known: Optional[Any] = None) -> dict[str, Any]:
    """V1-V6 over the staged file. ``known``: ``{"templates": {...}, "features": {...}}`` of the target (None: no staleness
    warnings). Returns the BundleReport ``{ok, stage?, reason_class?, message, projects, exporter, created_at, origin}``."""
    try:
        return _validate(Path(path), known)
    except _Fail as f:
        return {"ok": False, "stage": f.stage, "reason_class": f.stage, "message": f.message}


def _validate(path: Path, known: Optional[Any]) -> dict[str, Any]:
    # V1 container
    try:
        fh = open(path, "rb")                                  # noqa: SIM115 - closed below
    except OSError:
        raise _fail("container", "the file could not be read") from None
    with fh:
        if os.fstat(fh.fileno()).st_size > LIMITS["max_compressed_bytes"]:
            raise _fail("container", "the file is larger than 500 MB")
        if _eocd_entries(fh) > LIMITS["max_entries"]:
            raise _fail("container", "too many entries")
        fh.seek(0)
        try:
            zf = zipfile.ZipFile(fh)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError, NotImplementedError):
            raise _fail("container", "the file is not a readable ZIP archive") from None
        with zf:
            hit: dict[str, str] = {}
            try:
                infos = _names.check_container(zf, where="bundle", limits=_limits_for_container(),
                                               entry_rule=_entry_rule_factory(hit))
            except BundleCheckError as exc:
                if exc.detail == "protected entry" and hit.get("path"):
                    raise _fail("container", key="protected", path=hit["path"][:1000]) from None
                raise _fail("container", exc.detail) from None
            by_name = {i.filename: i for i in infos}
            # V2 envelope
            env_info = by_name.get(ENVELOPE_NAME)
            if env_info is None:
                raise _fail("envelope", "its manifest project-bundle.json is missing")
            if env_info.file_size > LIMITS["max_envelope_bytes"]:
                raise _fail("envelope", "its manifest is larger than 4 MB")
            budget = _ratio.Budget(LIMITS["max_uncompressed_bytes"], limits=LIMITS)
            raw = bytearray()
            _read_entry(zf, env_info, budget, raw.extend, stage="envelope")
            env = _parse_envelope(bytes(raw))
            env_ids = {p["id"] for p in env["projects"]}
            dir_ids = {n.split("/")[1] for n in by_name if n.startswith(PROJECTS_PREFIX)}
            dir_ids |= {i.filename.split("/")[1] for i in zf.infolist() if i.is_dir() and i.filename.count("/") >= 2}
            if not dir_ids <= env_ids or not env_ids <= {n.split("/")[1] for n in by_name if n.startswith(PROJECTS_PREFIX)}:
                raise _fail("envelope", "a project's id does not match its directory")
            # V3 integrity (one pass; manifests kept for V4, the first secret finding kept for V6)
            listed: dict[str, tuple[str, int]] = {}
            folded: set[str] = set()
            for p in env["projects"]:
                for f in p["files"]:
                    if f["path"].casefold() in folded:
                        raise _fail("integrity")
                    folded.add(f["path"].casefold())
                    listed[f["path"]] = (f["sha256"], f["size"])
            present = set(by_name) - {ENVELOPE_NAME}
            if present != set(listed):
                raise _fail("integrity")
            manifests: dict[str, bytes] = {}
            nested: list[str] = []
            finding: list[str] = []
            git_payloads = {f"{PROJECTS_PREFIX}{pid}/{inner}" for pid in env_ids for inner in bundle_rules.GIT_PAYLOADS}
            for info in sorted((by_name[n] for n in listed), key=lambda i: i.header_offset):
                sha, size = listed[info.filename]
                if info.file_size != size:
                    raise _fail("integrity")
                h = hashlib.sha256()
                state = {"n": 0, "scanner": None, "first": True}
                is_yaml = info.filename.rsplit("/", 1)[-1].casefold() == manifest.FILE
                keep = bytearray() if is_yaml else None
                scan_this = not finding and info.filename not in git_payloads

                def sink(b: bytes, _st=state, _keep=keep, _name=info.filename) -> None:
                    h.update(b)
                    _st["n"] += len(b)
                    if _keep is not None and len(_keep) <= _MANIFEST_MAX:
                        _keep.extend(b[: _MANIFEST_MAX + 1 - len(_keep)])
                    if scan_this and not finding:
                        if _st["first"]:
                            _st["scanner"] = _secrets.StreamScanner(_name) if is_text(b[:_SNIFF]) else None
                            _st["first"] = False
                        sc = _st["scanner"]
                        if sc is not None:
                            try:
                                sc.feed(b)
                            except BundleCheckError:
                                finding.append(_name)
                                _st["scanner"] = None
                _read_entry(zf, info, budget, sink)
                if state["n"] != size or h.hexdigest() != sha:
                    raise _fail("integrity")
                sc = state["scanner"]
                if scan_this and not finding and state["first"]:
                    sc = _secrets.StreamScanner(info.filename)      # an empty file: scanned as empty text
                if sc is not None and not finding:
                    try:
                        sc.close()
                    except BundleCheckError:
                        finding.append(info.filename)
                if keep is not None:
                    parts = info.filename.split("/")
                    if len(parts) == 3:
                        manifests[parts[1]] = bytes(keep)
                    elif len(keep) <= _MANIFEST_MAX and looks_like_manifest(bytes(keep)):
                        nested.append(info.filename)
            # V4 manifest
            reports = []
            for p in env["projects"]:
                doc = _check_manifest(p["id"], manifests.get(p["id"]))
                reports.append((p, doc))
            if nested:
                raise _fail("manifest", key="nested")
            # V5 staleness (warnings only)
            out_projects = []
            for p, doc in reports:
                out_projects.append({
                    "id": p["id"], "title": p["title"], "role": p["role"], "classification": p["classification"],
                    "docs_mode": doc.get("docs_mode", "nested"), "template": doc.get("template") or "",
                    "features": dict(doc.get("features") or {}), "counts": dict(p["counts"]), "parts": list(p["parts"]),
                    "git": dict(p.get("git") or {}), "left_out": list(p["left_out"]),
                    "warnings": _staleness(doc, known), "source_project_uid": p["source_project_uid"]})
            # V6 secrets (the envelope itself too)
            if not finding:
                try:
                    _secrets.scan_bytes(bytes(raw), ENVELOPE_NAME)
                except BundleCheckError:
                    finding.append(ENVELOPE_NAME)
            if finding:
                raise _fail("secrets", path=finding[0][:1000])
            return {"ok": True, "message": "", "origin": "unverified", "exporter": dict(env["exporter"]),
                    "created_at": env["created_at"], "projects": out_projects,
                    "counts": {"files": len(listed), "bytes": sum(s for _h, s in listed.values())}}
