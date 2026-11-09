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
    e.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "LC_ALL": "C",
              "GIT_NO_LAZY_FETCH": "1"})          # git >= 2.44; protocol.allow=never below is the guard for older gits)
    return e


_EXEC_KEYS = (re.compile(r"^filter\..+\.(clean|smudge|process)$"),
              # generic: any key whose NAME says it holds a program (command, cmd, program, hook, helper, pager, editor, textconv, driver,
              # gitproxy, fsmonitor, askpass ...) is blanked, so a key the list above never heard of is still closed (review rounds 4-5)
              re.compile(r"(command|cmd|program|hook|hooks|helper|pager|editor|textconv|driver|gitproxy|fsmonitor|askpass|process|uploadpack|receivepack|vcs)$"))


_FIXED = (("core.fsmonitor", "false"), ("core.hooksPath", os.devnull), ("diff.ignoreSubmodules", "all"), ("status.submoduleSummary", "false"),
          ("submodule.recurse", "false"), ("diff.external", ""), ("core.pager", ""), ("core.sshCommand", ""), ("core.askPass", ""),
          ("gc.auto", "0"), ("maintenance.auto", "false"), ("gc.recentObjectsHook", ""),
          # a partial clone fetches a missing object ON DEMAND from a remote the repository names (uploadpack / ext:: URL): no transport (review round 6)
          ("protocol.allow", "never"))


def neutral_git_args(repo: Path) -> list[str]:
    """Fixed ``-c`` overrides every git call on an adopted folder carries: no fsmonitor, no hooks, submodules are never descended into
    (a nested repository's own config would otherwise run its filters - review round 4). The repository's own EXEC keys are
    neutralised by ``neutral_git_env``."""
    out: list[str] = []
    for k, v in _FIXED:
        out += ["-c", f"{k}={v}"]
    return out


def neutral_git_env(repo: Path, base: dict[str, str]) -> dict[str, str]:
    """``base`` plus ``GIT_CONFIG_COUNT/KEY_n/VALUE_n`` entries (command scope, they outrank every config file) that switch off every
    command the repository's own configuration - including include.path targets and config.worktree - could make git run: filter and
    diff drivers become the identity / nothing. Key and value travel separately, so a driver NAME containing ``=`` cannot split a
    ``-c key=value`` argument into something else (review round 4). A repository whose config cannot be read keeps only the fixed set."""
    items: list[tuple[str, str]] = []
    seen: set[str] = set()

    def collect(r: Path, depth: int) -> None:
        # git descends into a committed nested repository (add -A, status, diff) and reads ITS config too, so the keys of every gitlink
        # are neutralised as well; the command-scope entries below apply to those child processes (review round 4)
        try:
            lst = subprocess.run(["git", "-C", str(r), *neutral_git_args(r), "config", "--list", "--includes", "-z"], capture_output=True, timeout=10, env=base)
            entries = [e for e in lst.stdout.decode("utf-8", errors="replace").split("\0") if "\n" in e]
            subs = subprocess.run(["git", "-C", str(r), *neutral_git_args(r), "ls-files", "--stage", "-z"], capture_output=True, timeout=20, env=base) if depth < 3 else None
        except (OSError, subprocess.TimeoutExpired):
            return
        for e in entries:
            key = e.split("\n", 1)[0]
            if any(p.search(key.lower()) for p in _EXEC_KEYS) and not key.lower().endswith(".hookspath") and key not in seen:
                seen.add(key)
                items.append((key, "cat" if key.lower().endswith((".clean", ".smudge")) else ""))
        for rec in (subs.stdout.decode("utf-8", errors="replace").split("\0") if subs else []):
            if rec.startswith("160000 ") and "\t" in rec:
                child = r / rec.split("\t", 1)[1]
                if child.is_dir() and not child.is_symlink():
                    collect(child, depth + 1)

    collect(Path(repo), 0)
    env = dict(base)
    # no transport at all, whatever protocol.<name>.allow the repository sets (a per-protocol key outranks protocol.allow; this variable
    # outranks both), and no lazy fetch on any path - the commit path's env did not carry it before (review round 7)
    env["GIT_ALLOW_PROTOCOL"] = "none"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_CONFIG_COUNT"] = str(len(items))
    for n, (k, v) in enumerate(items):
        env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = k, v
    return env


def _git(repo: Path, *args: str, check: bool = True, timeout: int = _GIT_TIMEOUT) -> str:
    r = subprocess.run(["git", "--no-pager", "-C", str(repo), "-c", "core.quotepath=false",
                        *neutral_git_args(repo), *args],
                       capture_output=True, timeout=timeout, env=neutral_git_env(repo, _env()))
    if check and r.returncode != 0:
        raise ChangesError("git could not read the repository")
    return r.stdout.decode("utf-8", errors="replace")


def _is_repo(p: Path) -> bool:
    return p.is_dir() and not p.is_symlink() and (p / ".git").exists()


def repos(root: Path) -> list[tuple[str, Path]]:
    """The repositories of a project: the project itself and a nested ``docs/`` repository. None when git is not installed
    (the change view then says "not a git repository" instead of failing - release review R4)."""
    import shutil  # noqa: PLC0415
    out: list[tuple[str, Path]] = []
    if shutil.which("git") is None:
        return out
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
    return subprocess.run(["git", "-C", str(repo), *neutral_git_args(repo), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True,
                          timeout=_GIT_TIMEOUT, env=neutral_git_env(repo, _env())).returncode == 0


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
    return working_changes_ex(root)[0]


def working_changes_ex(root: Path, *, count_lines: bool = True, timeout: int = _GIT_TIMEOUT) -> tuple[list[dict[str, Any]], bool]:
    """Like :func:`working_changes`, plus a ``truncated`` flag (the list was cut at ``MAX_FILES``). ``count_lines=False`` skips reading
    untracked files for their line count (the live watcher polls often and must stay cheap). The cut is applied AFTER sorting by path, so
    the same input always yields the same list."""
    files: list[dict[str, Any]] = []
    for prefix, repo in repos(root):
        head = _has_head(repo)
        nums: dict[str, tuple[Optional[int], Optional[int]]] = {}
        if head:
            for line in _git(repo, "diff", "--no-ext-diff", "--no-textconv", "--numstat", "HEAD", timeout=timeout).splitlines():
                a, _, rest = line.partition("\t")
                d, _, path = rest.partition("\t")
                nums[path] = (int(a) if a.isdigit() else None, int(d) if d.isdigit() else None)
        # `-z` prints a rename or copy as "XY new\0old\0": the old path is a SEPARATE field that must be consumed with its entry
        parts = [e for e in _git(repo, "status", "--porcelain=v1", "-z", "-uall", timeout=timeout).split("\0") if e]
        i = 0
        while i < len(parts):
            ent = parts[i]
            i += 1
            code, path = ent[:2], ent[3:]
            if code[0] in "RC" or code[1] in "RC":
                i += 1                                  # skip the old path
            st = "untracked" if code == "??" else "deleted" if "D" in code else "added" if "A" in code else "renamed" if "R" in code else "modified"
            add, dele = nums.get(path, (None, None))
            if st == "untracked" or (st == "added" and not head):
                add, dele = (_count_lines(repo / path) if count_lines else None), 0
            full = prefix + path
            files.append({"path": full, "status": st, "additions": add, "deletions": dele, "area": _area(full),
                          "ids": ids_in(Path(path).name)})
    files.sort(key=lambda f: (f["area"] != "code", f["path"]))
    truncated = len(files) > MAX_FILES
    files = files[:MAX_FILES]
    for f in files:
        f["links"] = [{"id": i, "kind": _kind(i)} for i in f.pop("ids")]
    return files, truncated


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
        if subprocess.run(["git", "-C", str(repo), *neutral_git_args(repo), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True,
                          timeout=_GIT_TIMEOUT, env=neutral_git_env(repo, _env())).returncode != 0:
            continue
        files = []
        # `-z`: "add\tdel\tpath\0", and a rename "add\tdel\t\0old\0new\0" (without -z git prints "{old => new}", which no
        # pathspec matches - release review R8)
        parts = _git(repo, "show", "--numstat", "-z", "--format=", "-M", sha).split("\0")
        entries: list[tuple[str, str, str]] = []
        i = 0
        while i < len(parts) and len(entries) < MAX_FILES:
            head = parts[i].lstrip("\n")
            i += 1
            if not head:
                continue
            a, _, rest = head.partition("\t")
            d, _, path = rest.partition("\t")
            old = ""
            if path == "":                                         # a rename: the next two fields are old and new
                old = parts[i] if i < len(parts) else ""
                path = parts[i + 1] if i + 1 < len(parts) else ""
                i += 2
            entries.append((a, d, path, old))
        for a, d, path, old in entries:
            full = prefix + path
            files.append({"path": full, "status": "committed", "additions": int(a) if a.isdigit() else None,
                          "deletions": int(d) if d.isdigit() else None, "area": _area(full), "old_path": (prefix + old) if old else None,
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
        old = meta.get("old_path")
        spec = [inner] + ([old[len(rel) - len(inner):] if old.startswith(rel[: len(rel) - len(inner)]) else old] if old else [])
        raw = _git(repo, "show", "--format=", "-M", "--no-ext-diff", "--no-textconv", "--unified=3", commit, "--", *spec)
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
        raw = _git(repo, "diff", "--no-ext-diff", "--no-textconv", "--unified=3", "HEAD", "--", inner)
    else:
        raw = _git(repo, "diff", "--no-ext-diff", "--no-textconv", "--unified=3", "HEAD", "--", inner) if _has_head(repo) else ""
    if "Binary files" in raw[:2000] or raw.startswith("Binary"):
        out["binary"] = True
        return out
    out["truncated"] = len(raw.encode("utf-8")) > MAX_BYTES
    out["diff"] = raw.encode("utf-8")[:MAX_BYTES].decode("utf-8", errors="ignore")
    return out
