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

from .fsutil import is_link, passes_link

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
          ("protocol.allow", "never"),
          # git log/show start gpg for a signed commit when this is on (round 9)
          ("log.showSignature", "false"), ("commit.gpgSign", "false"))


_FIXED_NAMES = frozenset(k.lower() for k, _ in _FIXED)       # the scan lists its own -c overrides too; they must not be blanked again


def neutralised_filters(env: dict[str, str]) -> set[str]:
    """The filter drivers ``neutral_git_env`` switched off (their programs blanked) - e.g. ``lfs``."""
    out: set[str] = set()
    for n in range(int(env.get("GIT_CONFIG_COUNT", "0") or 0)):
        k = env.get(f"GIT_CONFIG_KEY_{n}", "")
        if k.lower().startswith("filter.") and k.lower().endswith((".clean", ".smudge", ".process")):
            out.add(k.split(".")[1])
    return out


def neutral_git_args(repo: Path) -> list[str]:
    """Fixed ``-c`` overrides every git call on an adopted folder carries: no fsmonitor, no hooks, submodules are never descended into
    (a nested repository's own config would otherwise run its filters - review round 4). The repository's own EXEC keys are
    neutralised by ``neutral_git_env`` (which also pins the git directory and the work tree)."""
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
        # the top repository is scanned exactly as it will be run: its own .git and work tree (a core.worktree elsewhere is ignored)
        if depth > 0 and not (is_link(r / ".git") or (r / ".git").exists()):
            return                       # an uninitialised submodule (a plain `git clone`, `submodule deinit`): nothing to read
        own = _owning_repo(r) if depth == 0 else None
        renv = dict(base, GIT_DIR=os.path.join(str(own), ".git"), GIT_WORK_TREE=str(own)) if own is not None else base
        if own is not None:
            _refuse_foreign_config(own / ".git", base)
        elif depth > 0:
            gd = _submodule_git_dir(r, root_git)
            if gd is None:
                raise ChangesError("a submodule's git directory lies outside the project's repository; the console refuses it")
            _refuse_foreign_config(gd, base)
        try:
            lst = subprocess.run(["git", "-C", str(r), *neutral_git_args(r), "config", "--list", "--includes", "-z"], capture_output=True, timeout=10, env=renv)
            entries = [e for e in lst.stdout.decode("utf-8", errors="surrogateescape").split("\0") if "\n" in e]
            subs = subprocess.run(["git", "-C", str(r), *neutral_git_args(r), "ls-files", "--stage", "--full-name", "-z"], capture_output=True, timeout=20, env=renv)
        except (OSError, subprocess.TimeoutExpired):
            # fail closed: a call that cannot be guarded is not made (an unreadable config must never mean an unguarded run - round 9)
            raise ChangesError("the repository's configuration could not be read in time") from None
        for e in entries:
            key = e.split("\n", 1)[0]
            if any(p.search(key.lower()) for p in _EXEC_KEYS) and not key.lower().endswith(".hookspath") and key.lower() not in _FIXED_NAMES and key not in seen:
                seen.add(key)
                items.append((key, "cat" if key.lower().endswith((".clean", ".smudge")) else ""))
        top: Optional[str] = None
        records = subs.stdout.decode("utf-8", errors="surrogateescape").split("\0") if subs else []
        for rec in records:
            # EVERY index entry is judged before git stats anything: an entry named //host/share/f is a path Windows opens over SMB
            # (the host account's NTLM hash goes to the host, and a poll stalls ~20 s) - adversarial review round 5
            if "\t" in rec and _unsafe_rel(rec.split("\t", 1)[1]):
                raise ChangesError("the repository's index names a path outside the repository (a network path, an absolute path or "
                                   "'..'); the console refuses it")
        for rec in records:
            if rec.startswith("160000 ") and "\t" in rec:
                if depth >= 3:        # deeper than the scan reads: refuse rather than run unguarded (review round 10)
                    raise ChangesError("the repository nests submodules deeper than the console will read")
                # the gitlink path is relative to the TOPLEVEL of the worktree git actually uses (core.worktree may redirect it), and a
                # symlink there is refused, never skipped: skipping left the child's own config unguarded (review round 14b)
                if top is None:
                    top = subprocess.run(["git", "-C", str(r), *neutral_git_args(r), "rev-parse", "--show-toplevel"], capture_output=True,
                                         timeout=10, env=renv).stdout.decode("utf-8", errors="surrogateescape").strip() or str(r)
                sub = rec.split("\t", 1)[1]
                child = Path(top) / sub
                if passes_link(Path(top), child):
                    raise ChangesError("a submodule path is a link (symlink or junction); the console refuses it")
                if child.is_dir():
                    collect(child, depth + 1)

    _own = _owning_repo(Path(repo))
    root_git = (_own / ".git") if _own is not None else None
    collect(Path(repo), 0)
    # a filter whose programs were blanked must not be `required` (Git for Windows ships Git LFS with filter.lfs.required=true in the
    # system config): git would refuse every status/diff/add touching such a file instead of reading it as stored (review round 4)
    drivers = {k.split(".")[1] for k, _ in items if k.lower().startswith("filter.") and k.count(".") >= 2}
    for name in sorted(drivers):
        key = f"filter.{name}.required"
        if key not in seen:
            seen.add(key)
            items.append((key, "false"))
    # the fixed set travels in the environment too, so a program that runs git for us (the knowledge-base engine) is guarded the same way
    items = list(_FIXED) + items
    env = dict(base)
    # The repository is <repo>/.git and the work tree is <repo>, whatever the repository's config says: a `core.worktree` there would
    # otherwise make git list and diff the files of any other folder (a command-line `-c core.worktree` does NOT override it; these
    # variables do). The pin is the folder whose .git is a real directory - repo itself, or the nearest parent for a folder that
    # is part of an enclosing repository (a project's plain-mode docs/) - so a legitimate engine write keeps working (review 2026-11-10).
    owner = _owning_repo(Path(repo))
    if owner is not None:
        env["GIT_DIR"] = os.path.join(str(owner), ".git")
        env["GIT_WORK_TREE"] = str(owner)
    else:
        env.pop("GIT_DIR", None)
        env.pop("GIT_WORK_TREE", None)
        # no repository of its own: git may look at this folder only, never up into an enclosing repository
        env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(os.path.abspath(repo))
    # no transport at all, whatever protocol.<name>.allow the repository sets (a per-protocol key outranks protocol.allow; this variable
    # outranks both), and no lazy fetch on any path - the commit path's env did not carry it before (review round 7)
    env["GIT_ALLOW_PROTOCOL"] = "none"
    env["GIT_NO_LAZY_FETCH"] = "1"
    # The same entries in GIT_CONFIG_PARAMETERS (the older channel `-c` uses): a program that sets GIT_CONFIG_COUNT for itself and so
    # replaces the entries above - the knowledge-base engine does when it commits - still inherits these (review round 11)
    def _sq(t: str) -> str:
        return "'" + t.replace("'", "'\\''") + "'"
    extra = " ".join(f"{_sq(k)}={_sq(v)}" for k, v in items)
    env["GIT_CONFIG_PARAMETERS"] = (env.get("GIT_CONFIG_PARAMETERS", "") + " " + extra).strip()
    env["GIT_CONFIG_COUNT"] = str(len(items))
    for n, (k, v) in enumerate(items):
        env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = k, v
    return env


def _owning_repo(repo: Path) -> Optional[Path]:
    """The repository a git call on *repo* must use, never one OUTSIDE the project: *repo* itself when its ``.git`` is a real
    directory, or - for a project's plain-mode ``docs/`` - the project root right above it. None otherwise (``git init`` of a new
    repository; a folder with no repository of its own): then ``GIT_CEILING_DIRECTORIES`` stops git from discovering an enclosing
    repository (a dotfiles repo in the home folder, a monorepo the projects root sits in) - adversarial review round 3."""
    cur = Path(os.path.abspath(repo))
    g = cur / ".git"
    if is_link(g):
        return None
    if g.is_dir():
        return cur if _is_repo(cur) else None
    if g.exists():
        return None                      # a .git FILE (an initialised submodule, a worktree): git resolves it; never the parent's repository
    parent = cur.parent
    if cur.name.lower() == "docs" and (parent / "project.yaml").is_file():
        # plain mode: docs/ is part of the PROJECT's repository - only when the parent really is a project root (a folder that merely
        # happens to be called "docs" inside the user's own repository must never be pinned to that repository)
        pg = parent / ".git"
        if not is_link(pg) and pg.is_dir() and _is_repo(parent):
            return parent
    return None


def _git(repo: Path, *args: str, check: bool = True, timeout: int = _GIT_TIMEOUT) -> str:
    r = subprocess.run(["git", "--no-pager", "-C", str(repo), "-c", "core.quotepath=false",
                        *neutral_git_args(repo), *args],
                       capture_output=True, timeout=timeout, env=neutral_git_env(repo, _env()))
    if check and r.returncode != 0:
        raise ChangesError("git could not read the repository")
    return r.stdout.decode("utf-8", errors="replace")


_repo_cache: dict[str, tuple[float, Optional[str]]] = {}
_REPO_CACHE_S = 20.0
_REPO_PROBLEM_S = 3.0


def repo_problem(p: Path) -> Optional[str]:
    """Why *p*'s git repository is not read by the console, or None when it is a repository of its own that can be read. A folder
    with no ``.git`` at all is "no repository", not a problem (None is returned only for a readable one; see :func:`_is_repo`).

    Read only what lives INSIDE the project: ``.git`` must be a real directory (not a link, not a ``gitdir:`` file - a git worktree
    checkout - whose target may be another tenant's repository), with no link anywhere in it and no ``commondir`` or object
    ``alternates`` (a ``--reference``/``--shared`` clone borrows objects from elsewhere). The answer is cached for a few seconds: the
    activity watcher asks on every poll (adversarial review round 5)."""
    import time  # noqa: PLC0415
    key = os.path.abspath(p)
    hit = _repo_cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _REPO_CACHE_S:
        return hit[1]
    out = _repo_problem(p)
    if len(_repo_cache) > 256:
        _repo_cache.clear()
    _repo_cache[key] = (now, out) if out is None else (now - _REPO_CACHE_S + _REPO_PROBLEM_S, out)    # a problem is re-checked within seconds
    return out


def forget_repo_cache() -> None:
    """After ``git init`` (or any change that makes a folder a repository): the cached "not a repository" is stale."""
    _repo_cache.clear()


def _repo_problem(p: Path) -> Optional[str]:
    if not p.is_dir() or is_link(p):
        return "not a folder"
    g = p / ".git"
    if is_link(g):
        return "its .git is a link (symlink or junction)"
    if g.is_file():
        return ("its .git is a file that points to a git directory elsewhere (a `git worktree` checkout or a separate git directory); "
                "the console only reads a repository whose .git folder is inside the project")
    if not g.is_dir():
        return "not a repository"
    try:
        if any(is_link(c) for c in g.iterdir()):
            return "its .git folder contains a link (symlink or junction)"
    except OSError:
        return "its .git folder could not be read"
    if (g / "commondir").exists():
        return "it shares its git directory with another repository (commondir)"
    if (g / "objects" / "info" / "alternates").exists() or is_link(g / "objects" / "info"):
        return "it borrows objects from another repository (a --reference or --shared clone); the console reads only self-contained repositories"
    walk = _git_dir_walk(g)
    if walk == "link":
        return "its .git folder contains a link (symlink or junction)"
    if walk == "large":
        return "its .git folder is too large for the console to verify"
    return None


def _git_capped(repo: Path, *args: str, cap: int = MAX_BYTES * 2, timeout: int = _GIT_TIMEOUT) -> str:
    """Like :func:`_git`, but reads at most *cap* bytes of output and then stops git: the diff of a hundreds-of-megabytes text file must
    not be held in memory (three times) before the 200 KB limit applies. A truncated diff is what the view shows anyway."""
    import threading  # noqa: PLC0415
    cmd = ["git", "--no-pager", "-C", str(repo), "-c", "core.quotepath=false", *neutral_git_args(repo), *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=neutral_git_env(repo, _env()))
    timer = threading.Timer(timeout, proc.kill)           # the read below blocks: a git that stalls without output is killed on time
    timer.start()
    truncated = False
    try:
        assert proc.stdout is not None
        data = proc.stdout.read(cap + 1)
        if len(data) > cap:
            truncated = True
            proc.kill()
    finally:
        try:
            proc.stdout.close()                          # type: ignore[union-attr]
        finally:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
            timer.cancel()
    if not truncated and proc.returncode != 0:
        raise ChangesError("git could not read the repository")
    return data[:cap].decode("utf-8", errors="replace")


def _is_repo(p: Path) -> bool:
    return repo_problem(p) is None


_GIT_WALK_MAX = 2_000_000
#: never part of what git reads for status/diff/log: large and the home of Git LFS' object cache
_GIT_WALK_SKIP = ("lfs",)
_UNC_VALUE = re.compile(r"^\s*(//|\\\\)")
_URL_KEY = re.compile(r"^(remote\..+\.(url|pushurl)|submodule\..+\.url|url\..+\.(insteadof|pushinsteadof))$")


def _refuse_foreign_config(gitdir: Path, base: dict[str, str]) -> None:
    """The repository's OWN config files (config, config.worktree), read without following includes: an ``include.path`` /
    ``includeIf`` or any value naming a network path (``//host/share``, ``\\\\host\\share``) would make git open that path - on
    Windows an SMB connection that hands the host account's NTLM hash to the host - so such a repository is refused."""
    for f in (gitdir / "config", gitdir / "config.worktree"):
        if not (is_link(f) or f.exists()):
            continue
        if is_link(f) or not f.is_file():
            raise ChangesError("the repository's config is not a regular file; the console refuses it")
        try:
            r = subprocess.run(["git", "config", "--file", str(f), "--no-includes", "--list", "-z"], capture_output=True, timeout=10,
                               env={k: v for k, v in base.items() if not k.startswith("GIT_")})
        except (OSError, subprocess.TimeoutExpired):
            raise ChangesError("the repository's configuration could not be read in time") from None
        if r.returncode != 0:
            raise ChangesError("the repository's configuration could not be read")
        for e in r.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
            if not e:
                continue
            key, _, val = e.partition("\n")
            k = key.lower()
            if k.startswith(("include.", "includeif.")):
                raise ChangesError("the repository's config includes other files; the console refuses it")
            if _URL_KEY.match(k):
                continue                 # a remote/submodule URL: never contacted (GIT_ALLOW_PROTOCOL=none, protocol.allow=never)
            if _is_network_path(val):
                raise ChangesError("the repository's config names a network path; the console refuses it")


def _unsafe_rel(sub: str) -> bool:
    """True when an index path is not a plain relative path inside the repository: empty, absolute, rooted (a leading slash or
    backslash, ``//host/share``), with a drive or ``:`` (also an alternate data stream), a NUL, or a ``..`` part."""
    if not sub or "\x00" in sub or sub.startswith(("/", "\\")) or (os.name == "nt" and ":" in sub):
        return True
    sp = Path(sub)
    return sp.is_absolute() or bool(sp.drive) or bool(sp.root) or ".." in sub.replace("\\", "/").split("/")


def _is_network_path(val: str) -> bool:
    """Every spelling Windows opens as a network or device path: ``//h/s``, ``\\\\h\\s``, mixed ``\\/h/s`` and ``/\\h\\s``, git's
    ``:(optional)`` prefix in front of any of them, ``\\\\?\\UNC\\...`` and the NT form ``\\??\\...``."""
    v = val.strip()
    if v.lower().startswith(":(optional)"):
        v = v[len(":(optional)"):].strip()
    v = v.replace("/", "\\")
    return v.startswith("\\\\") or v.startswith("\\??\\") or "\\\\?\\unc" in v.lower()


def _submodule_git_dir(child: Path, root_git: Optional[Path]) -> Optional[Path]:
    """The git directory of a committed submodule: its ``.git`` directory, or the ``gitdir:`` file's target - which must lie inside
    the project's own ``.git/modules`` (lexically, no network path). None = refuse."""
    g = child / ".git"
    if is_link(g):
        return None
    if g.is_dir():
        return g
    if not g.is_file() or root_git is None:
        return None
    try:
        from .fsutil import read_bytes_limited  # noqa: PLC0415
        line = read_bytes_limited(g, 4096).decode("utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return None
    if not line.startswith("gitdir:"):
        return None
    target = line[len("gitdir:"):].strip()
    if not target or _is_network_path(target):
        return None
    tp = Path(os.path.abspath(Path(target) if Path(target).is_absolute() else child / target))
    modules = Path(os.path.abspath(root_git / "modules"))
    if modules not in tp.parents or passes_link(Path(os.path.abspath(root_git)), tp):
        return None
    return tp


def _git_dir_walk(g: Path) -> str:
    """"ok", "link" (anything inside ``.git`` is a symlink or junction - a junction for ``refs/heads`` or ``objects/ab`` would make git
    read another repository's commits and blobs) or "large" (more entries than the walk reads). The Git LFS cache and packfiles'
    contents are skipped: they hold no references git follows."""
    seen = 0
    stack = [str(g)]
    top = os.path.abspath(str(g))
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    seen += 1
                    if seen > _GIT_WALK_MAX:
                        return "large"
                    if e.is_symlink() or e.is_junction():          # from the directory listing itself: no extra stat per entry
                        return "link"
                    if e.is_dir(follow_symlinks=False):
                        if d == top and e.name in _GIT_WALK_SKIP:
                            continue
                        stack.append(e.path)
        except OSError:
            return "link"
    return "ok"


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
    if not passes_link(root, d) and _is_repo(d) and (not out or (d / ".git").resolve() != (root / ".git").resolve()):
        out.append(("docs/", d))
    return out


def require_own_repo(path: Path) -> None:
    """Refuse to run git (or the KB engine, which commits) in *path* when it HAS a ``.git`` the console does not read: a junction, a
    ``gitdir:`` file, shared objects, links inside - git would otherwise follow it into another repository and write there
    (adversarial review round 7). A folder with no ``.git`` is fine (``git init``, plain docs/)."""
    g = path / ".git"
    if not (is_link(g) or g.exists()):
        return
    why = repo_problem(path)
    if why:
        # a cached "not a repository" can be stale (a subprocess such as kb_init created .git a moment ago): ask again, uncached
        why = _repo_problem(path)
        _repo_cache.pop(os.path.abspath(path), None)
    if why:
        raise ChangesError(f"git is not run here: {why}")


def repository_note(root: Path) -> Optional[str]:
    """For the change view: WHY a folder that has a ``.git`` is not listed (worktree checkout, shared objects, links, too large),
    or None when there is nothing to explain (no ``.git``, or a readable repository)."""
    for d, label in ((root, ""), (root / "docs", "docs/: ")):
        if not (is_link(d / ".git") or (d / ".git").exists()):
            continue
        why = repo_problem(d)
        if why and why != "not a repository":
            return label + why
    return None


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
        if is_link(p) or p.stat().st_size > MAX_BYTES:
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
        start = len(files)                              # this repository's entries are files[start:]
        head = _has_head(repo)
        nums: dict[str, tuple[Optional[int], Optional[int]]] = {}
        if head:
            for line in _git(repo, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--numstat", "HEAD", timeout=timeout).splitlines():
                a, _, rest = line.partition("\t")
                d, _, path = rest.partition("\t")
                nums[path] = (int(a) if a.isdigit() else None, int(d) if d.isdigit() else None)
        # `-z` prints a rename or copy as "XY new\0old\0": the old path is a SEPARATE field that must be consumed with its entry
        parts = [e for e in _git(repo, "status", "--porcelain=v1", "-z", "-uall", "--ignore-submodules=all", timeout=timeout).split("\0") if e]
        i = 0
        while i < len(parts):
            ent = parts[i]
            i += 1
            code, path = ent[:2], ent[3:]
            if code[0] in "RC" or code[1] in "RC":
                i += 1                                  # skip the old path
            if passes_link(repo, repo / path):
                # git for Windows walks INTO a directory junction and lists what lies behind it as untracked files of this
                # repository: those files are somewhere else and are neither listed nor read (a junction needs no admin right)
                continue
            st = "untracked" if code == "??" else "deleted" if "D" in code else "added" if "A" in code else "renamed" if "R" in code else "modified"
            add, dele = nums.get(path, (None, None))
            if st == "untracked" or (st == "added" and not head):
                add, dele = (_count_lines(repo / path) if count_lines else None), 0
            full = prefix + path
            files.append({"path": full, "status": st, "additions": add, "deletions": dele, "area": _area(full),
                          "ids": ids_in(Path(path).name)})
        files = _drop_lfs_false_modified(prefix, repo, files, start)
    files.sort(key=lambda f: (f["area"] != "code", f["path"]))
    truncated = len(files) > MAX_FILES
    files = files[:MAX_FILES]
    for f in files:
        f["links"] = [{"id": i, "kind": _kind(i)} for i in f.pop("ids")]
    return files, truncated


_LFS_OID = re.compile(rb"^oid sha256:([0-9a-f]{64})$", re.M)


_lfs_cache: dict[tuple[str, int, int, str], bool] = {}
_lfs_lock = __import__("threading").Lock()
_LFS_HASH_MAX = 512 * 1024 * 1024
_LFS_FILES_PER_CALL = 40
_LFS_BYTES_PER_CALL = 2 * 1024 * 1024 * 1024


def _lfs_pointers(repo: Path, env: dict[str, str], rels: list[str], ref: str = ":") -> dict[str, str]:
    """rel -> LFS oid of the pointer in the index, for every path whose index blob IS a small LFS pointer: ONE ``cat-file --batch-check``
    for the sizes, ONE ``--batch`` for the blobs of at most 1 KB (a hostile repository's huge blob is never read)."""
    rels = [r for r in rels if "\n" not in r and "\r" not in r]
    if not rels:
        return {}
    base = ["git", "-C", str(repo), *neutral_git_args(repo)]
    try:
        chk = subprocess.run([*base, "cat-file", "--batch-check"], input="".join(f"{ref}{r}\n" for r in rels).encode("utf-8", errors="surrogateescape"),
                             capture_output=True, timeout=_GIT_TIMEOUT, env=env).stdout.decode("utf-8", errors="replace").splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return {}
    small: list[str] = []
    for r, line in zip(rels, chk):
        parts = line.split()
        if len(parts) == 3 and parts[1] == "blob" and parts[2].isdigit() and int(parts[2]) <= 1024:
            small.append(r)
    if not small:
        return {}
    try:
        out = subprocess.run([*base, "cat-file", "--batch"], input="".join(f"{ref}{r}\n" for r in small).encode("utf-8", errors="surrogateescape"),
                             capture_output=True, timeout=_GIT_TIMEOUT, env=env).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    res: dict[str, str] = {}
    pos = 0
    for r in small:
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        head = out[pos:nl].split()
        if len(head) == 3 and head[1] == b"blob" and head[2].isdigit():
            size = int(head[2])
            m = _LFS_OID.search(out[nl + 1:nl + 1 + size])
            if m:
                res[r] = m.group(1).decode()
            pos = nl + 1 + size + 1
        else:
            pos = nl + 1
    return res


def _drop_lfs_false_modified(prefix: str, repo: Path, files: list[dict[str, Any]], start: int) -> list[dict[str, Any]]:
    """The console runs no filter program (Git LFS is switched off in its git calls), so a Git LFS file is compared RAW against the
    pointer in the index and shows as modified after any timestamp change (OneDrive, antivirus, touch). For such a file the real
    answer is cheap: sha256 of the working file against the ``oid`` in the index pointer. Same hash -> not a change. Applies to the
    entries ``files[start:]`` of ONE repository; bounded: at most 40 files and 2 GB hashed per call (the rest stay "modified"), pointers
    read in one batch, a verdict cached by path, mtime, size and oid (oldest evicted first)."""
    mine = [f for f in files[start:] if f["status"] == "modified"]
    if not mine:
        return files
    import hashlib  # noqa: PLC0415
    env = neutral_git_env(repo, _env())
    if "lfs" not in neutralised_filters(env):
        return files
    stdin = ("\0".join(f["path"][len(prefix):] for f in mine) + "\0").encode("utf-8", errors="surrogateescape")
    try:
        ca = subprocess.run(["git", "-C", str(repo), *neutral_git_args(repo), "check-attr", "-z", "--stdin", "filter"], input=stdin,
                            capture_output=True, timeout=_GIT_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return files
    f3 = ca.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    lfs = sorted({f3[i] for i in range(0, len(f3) - 2, 3) if f3[i + 1] == "filter" and f3[i + 2] == "lfs"})[:_LFS_FILES_PER_CALL]
    pointers = _lfs_pointers(repo, env, lfs)
    head_pointers = _lfs_pointers(repo, env, lfs, "HEAD:")           # a change already STAGED differs from HEAD and stays a change
    drop: set[str] = set()
    budget = _LFS_BYTES_PER_CALL
    for rel in lfs:
        oid = pointers.get(rel)
        full = repo / rel
        if oid is None or passes_link(repo, full):
            continue
        try:
            if not full.is_file():
                continue
            st = os.stat(full)
            if st.st_size > _LFS_HASH_MAX:
                continue                  # too big to hash on a poll (a hostile sparse file costs nothing to make): stays "modified"
            if head_pointers.get(rel) != oid:
                continue
            key = (os.path.abspath(full), st.st_mtime_ns, st.st_size, oid)
            with _lfs_lock:
                same = _lfs_cache.get(key)
            if same is None:
                if st.st_size > budget:
                    continue
                budget -= st.st_size
                h = hashlib.sha256()
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                same = h.hexdigest() == oid
                with _lfs_lock:
                    while len(_lfs_cache) >= 512:
                        _lfs_cache.pop(next(iter(_lfs_cache)), None)    # oldest first (dicts keep insertion order)
                    _lfs_cache[key] = same
            if same:
                drop.add(prefix + rel)
        except OSError:
            continue
    return [f for i, f in enumerate(files) if i < start or f["path"] not in drop] if drop else files


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
    if passes_link(root, root / rel):
        raise ChangesError("the path passes a link (symlink or junction)")
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
        raw = _git_capped(repo, "show", "--format=", "-M", "--no-ext-diff", "--no-textconv", "--unified=3", commit, "--", *spec)
    elif meta["status"] == "untracked" or (meta["status"] == "added" and not _has_head(repo)):
        p = repo / inner
        if passes_link(repo, p) or not p.is_file():
            raise ChangesError("the file is not readable")
        with open(p, "rb") as fh:
            data = fh.read(MAX_BYTES + 1)               # never the whole file: a video in the project is gigabytes
        if b"\x00" in data:
            out["binary"] = True
            return out
        out["truncated"] = len(data) > MAX_BYTES
        out["text"] = data[:MAX_BYTES].decode("utf-8", errors="replace")
        return out
    elif meta["status"] == "deleted":
        raw = _git_capped(repo, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--unified=3", "HEAD", "--", inner)
    else:
        raw = _git_capped(repo, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--unified=3", "HEAD", "--", inner) if _has_head(repo) else ""
    if "Binary files" in raw[:2000] or raw.startswith("Binary"):
        out["binary"] = True
        return out
    out["truncated"] = len(raw.encode("utf-8")) > MAX_BYTES
    out["diff"] = raw.encode("utf-8")[:MAX_BYTES].decode("utf-8", errors="ignore")
    return out
