"""What changed in a project: read-only git (ADR-2319 Decision 5, CONCEPT-0127 D6/D7).

Lists the files that differ from the last commit in the project repository and in the nested ``docs/``
repository, shows a unified diff (or the new file's text), lists recent commits and turns the ids it can read
(``T-0001``, ``ADR-2319`` ...) into link targets. Nothing is written, nothing is executed from the project:
git is run with a fixed, quiet configuration and ``GIT_OPTIONAL_LOCKS=0`` so reading never takes ``index.lock``
(the knowledge-base engine commits in the same repositories). A path leaves the repository, passes a symlink or
is not part of the listing -> refused. Binary and oversized files are listed, never rendered.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

MAX_FILES = 300
MAX_BYTES = 200 * 1024
MAX_COMMITS = 15
_GIT_TIMEOUT = 20
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_ID_RE = re.compile(r"\b(T-\d{4}|E-\d{3}|I-\d{2}|ADR-\d{4}|CONCEPT-\d{4}|PLAN-\d{4}|NOTE-\d{4}|IDEA-\d{4}|REVIEW-\d{4})\b")


class ChangesError(ValueError):
    """A request the change view refuses (bad path, bad commit, not a repository)."""


def _env() -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    e.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "LC_ALL": "C"})
    return e


def _git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "--no-pager", "-C", str(repo), "-c", "core.quotepath=false", "-c", "core.fsmonitor=false", *args],
                       capture_output=True, timeout=_GIT_TIMEOUT, env=_env())
    if check and r.returncode != 0:
        raise ChangesError("git could not read the repository")
    return r.stdout.decode("utf-8", errors="replace")


def _is_repo(p: Path) -> bool:
    return p.is_dir() and not p.is_symlink() and (p / ".git").exists()


def repos(root: Path) -> list[tuple[str, Path]]:
    """The repositories of a project: the project itself and a nested ``docs/`` repository."""
    out: list[tuple[str, Path]] = []
    if _is_repo(root):
        out.append(("", root))
    d = root / "docs"
    if _is_repo(d) and (not out or (d / ".git").resolve() != (root / ".git").resolve()):
        out.append(("docs/", d))
    return out


def ids_in(text: str) -> list[str]:
    return list(dict.fromkeys(_ID_RE.findall(text or "")))


def _kind(i: str) -> str:
    return "task" if i[:2] in ("T-", "E-", "I-") and not i.startswith("IDEA") else "knowledge"


def _area(path: str) -> str:
    if path.startswith("docs/kb/"):
        return "knowledge"
    if path.startswith("docs/"):
        return "docs"
    return "code"


def _has_head(repo: Path) -> bool:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True,
                          timeout=_GIT_TIMEOUT, env=_env()).returncode == 0


def _count_lines(p: Path) -> Optional[int]:
    try:
        if p.is_symlink() or p.stat().st_size > MAX_BYTES:
            return None
        data = p.read_bytes()
        if b"\x00" in data:
            return None
        return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    except OSError:
        return None


def working_changes(root: Path) -> list[dict[str, Any]]:
    """Files that differ from HEAD (and untracked ones), across the project's repositories."""
    files: list[dict[str, Any]] = []
    for prefix, repo in repos(root):
        head = _has_head(repo)
        nums: dict[str, tuple[Optional[int], Optional[int]]] = {}
        if head:
            for line in _git(repo, "diff", "--numstat", "HEAD").splitlines():
                a, _, rest = line.partition("\t")
                d, _, path = rest.partition("\t")
                nums[path] = (int(a) if a.isdigit() else None, int(d) if d.isdigit() else None)
        status = _git(repo, "status", "--porcelain=v1", "-z", "-uall")
        for ent in [e for e in status.split("\0") if e]:
            code, path = ent[:2], ent[3:]
            if " -> " in path:
                path = path.split(" -> ")[-1]
            st = "untracked" if code == "??" else "deleted" if "D" in code else "added" if "A" in code else "renamed" if "R" in code else "modified"
            add, dele = nums.get(path, (None, None))
            if st == "untracked" or (st == "added" and not head):
                add, dele = _count_lines(repo / path), 0
            full = prefix + path
            files.append({"path": full, "status": st, "additions": add, "deletions": dele, "area": _area(full),
                          "ids": ids_in(Path(path).name)})
            if len(files) >= MAX_FILES:
                break
    for f in files:
        f["links"] = [{"id": i, "kind": _kind(i)} for i in f.pop("ids")]
    files.sort(key=lambda f: (f["area"] != "code", f["path"]))
    return files


def recent_commits(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for prefix, repo in repos(root):
        if not _has_head(repo):
            continue
        for line in _git(repo, "log", f"-n{MAX_COMMITS}", "--pretty=format:%H%x1f%an%x1f%at%x1f%s").splitlines():
            sha, author, at, subject = (line.split("\x1f") + ["", "", "", ""])[:4]
            out.append({"sha": sha, "short": sha[:9], "repo": prefix.rstrip("/") or "project", "author": author[:80],
                        "at": int(at) if at.isdigit() else 0, "subject": subject[:200],
                        "links": [{"id": i, "kind": _kind(i)} for i in ids_in(subject)]})
    out.sort(key=lambda c: -c["at"])
    return out[:MAX_COMMITS]


def _repo_for(root: Path, rel: str) -> tuple[Path, str]:
    for prefix, repo in reversed(repos(root)):          # docs/ first: the longer prefix wins
        if prefix and rel.startswith(prefix):
            return repo, rel[len(prefix):]
    for prefix, repo in repos(root):
        if not prefix:
            return repo, rel
    raise ChangesError("the project has no git repository")


def commit_files(root: Path, sha: str) -> list[dict[str, Any]]:
    if not _SHA_RE.match(sha or ""):
        raise ChangesError("invalid commit")
    for prefix, repo in repos(root):
        if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True,
                          timeout=_GIT_TIMEOUT, env=_env()).returncode != 0:
            continue
        files = []
        for line in _git(repo, "show", "--numstat", "--format=", "-M", sha).splitlines()[:MAX_FILES]:
            a, _, rest = line.partition("\t")
            d, _, path = rest.partition("\t")
            full = prefix + path
            files.append({"path": full, "status": "committed", "additions": int(a) if a.isdigit() else None,
                          "deletions": int(d) if d.isdigit() else None, "area": _area(full),
                          "links": [{"id": i, "kind": _kind(i)} for i in ids_in(Path(path).name)]})
        return files
    raise ChangesError("no such commit")


def _safe(root: Path, rel: str) -> str:
    if not isinstance(rel, str) or not rel or "\x00" in rel or len(rel) > 400 or rel.startswith("/") or "\\" in rel:
        raise ChangesError("invalid path")
    parts = Path(rel).parts
    if ".." in parts or any(p in ("", ".") for p in parts) or ".git" in parts:
        raise ChangesError("invalid path")
    cur = root
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            raise ChangesError("the path passes a symlink")
    return rel


def file_view(root: Path, rel: str, commit: Optional[str] = None) -> dict[str, Any]:
    """The unified diff of one listed file (or, for a new file, its text). Bounded; binaries are not rendered."""
    rel = _safe(root, rel)
    allowed = {f["path"]: f for f in (commit_files(root, commit) if commit else working_changes(root))}
    if rel not in allowed:
        raise ChangesError("this file is not part of the change list")
    repo, inner = _repo_for(root, rel)
    meta = allowed[rel]
    out: dict[str, Any] = {"path": rel, "status": meta["status"], "area": meta["area"], "binary": False, "truncated": False,
                           "diff": "", "text": None}
    if commit:
        raw = _git(repo, "show", "--format=", "-M", "--unified=3", commit, "--", inner)
    elif meta["status"] == "untracked" or (meta["status"] == "added" and not _has_head(repo)):
        p = repo / inner
        if p.is_symlink() or not p.is_file():
            raise ChangesError("the file is not readable")
        data = p.read_bytes()[: MAX_BYTES + 1]
        if b"\x00" in data:
            out["binary"] = True
            return out
        out["truncated"] = len(data) > MAX_BYTES
        out["text"] = data[:MAX_BYTES].decode("utf-8", errors="replace")
        return out
    elif meta["status"] == "deleted":
        raw = _git(repo, "diff", "--unified=3", "HEAD", "--", inner)
    else:
        raw = _git(repo, "diff", "--unified=3", "HEAD", "--", inner) if _has_head(repo) else ""
    if "Binary files" in raw[:2000] or raw.startswith("Binary"):
        out["binary"] = True
        return out
    out["truncated"] = len(raw.encode("utf-8")) > MAX_BYTES
    out["diff"] = raw.encode("utf-8")[:MAX_BYTES].decode("utf-8", errors="ignore")
    return out
