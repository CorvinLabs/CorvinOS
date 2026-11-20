#!/usr/bin/env python3
"""Pre-push term scan: refuse to publish forbidden terms to a public remote.

Git runs this hook as ``pre-push <remote-name> <remote-url>`` and feeds one
line per ref on stdin::

    <local ref> <local sha> <remote ref> <remote sha>

Two LOCAL files drive it; the repository never contains either of them:

* ``${CORVIN_PUBLIC_REMOTES_FILE:-$HOME/.config/corvin/public-remotes.txt}``
  one remote URL per line, matched as a substring OR as a Python regex
  (``#`` starts a comment). A push to a remote that matches no line is not
  scanned at all.
* ``${CORVIN_FORBIDDEN_TERMS_FILE:-$HOME/.config/corvin/public-forbidden-terms.txt}``
  one case-insensitive Python regex per line (``#`` comments, blank lines
  ignored). A term is reported by its LINE NUMBER in this file.

For a listed remote, every commit about to be pushed is scanned: the
added diff lines, the names of added/copied/renamed files and the commit
message. A hit prints ``refused: <file or "commit message"> matches term
#<n>`` and the push is refused (exit 1). The matched text and the term
itself are never printed, so the refusal message is safe to paste
anywhere. A missing or empty terms file, or a term that is not a valid
regex, refuses the push for a listed remote (fail closed). Deleting a
remote ref (all-zero local sha) is always allowed.

The same file also implements the tree scan (``scan-tree`` calls it with
``--scan-tree``): every tracked file name and file content (binaries
skipped, at most 2 MB read per file) is checked, each hit is printed as
``<path>: term #<n>``, followed by a count. Exit 1 on any hit, 0 when
clean, 2 when the terms file is missing, empty or invalid.

Install (opt-in, per clone): ``git config core.hooksPath scripts/git-hooks``
(or ``scripts/git-hooks/install.sh``). Stdlib only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ZERO_SHA_RE = re.compile(r"^0+$")
SCAN_CAP_BYTES = 2 * 1024 * 1024
BINARY_PROBE_BYTES = 8192


class TermsError(Exception):
    """The terms file is missing, empty or contains an invalid regex."""


def _config_path(env_name: str, default_name: str) -> Path:
    value = os.environ.get(env_name)
    if value:
        return Path(value).expanduser()
    return Path.home() / ".config" / "corvin" / default_name


def _meaningful_lines(path: Path) -> list[tuple[int, str]]:
    """Return (1-based line number, stripped text) for non-comment lines."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append((number, line))
    return out


def load_terms() -> list[tuple[int, re.Pattern[str]]]:
    path = _config_path("CORVIN_FORBIDDEN_TERMS_FILE", "public-forbidden-terms.txt")
    if not path.is_file():
        raise TermsError(f"terms file not found: {path}")
    terms: list[tuple[int, re.Pattern[str]]] = []
    for number, line in _meaningful_lines(path):
        try:
            terms.append((number, re.compile(line, re.IGNORECASE)))
        except re.error as exc:
            raise TermsError(f"term #{number} in {path} is not a valid regex ({exc.msg})") from None
    if not terms:
        raise TermsError(f"terms file is empty: {path}")
    return terms


def remote_is_listed(url: str) -> bool:
    path = _config_path("CORVIN_PUBLIC_REMOTES_FILE", "public-remotes.txt")
    if not path.is_file():
        return False
    for _number, line in _meaningful_lines(path):
        if line in url:
            return True
        try:
            if re.search(line, url):
                return True
        except re.error:
            pass
    return False


def matching_terms(text: str, terms: list[tuple[int, re.Pattern[str]]]) -> list[int]:
    return [number for number, pattern in terms if pattern.search(text)]


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- pre-push


def commits_to_push(local_sha: str, remote_sha: str, remote_name: str = "") -> list[str]:
    if not ZERO_SHA_RE.match(remote_sha):
        known = _git("cat-file", "-e", f"{remote_sha}^{{commit}}", check=False)
        if known.returncode == 0:
            result = _git("rev-list", f"{remote_sha}..{local_sha}")
            return _decode(result.stdout).split()
    # New branch (or a remote sha we do not have): everything not already on THIS
    # remote's tracking refs. Another remote's refs prove nothing: a commit that is
    # only on a private remote has never been scanned for the public one.
    # A push to a URL (not a configured remote) has no tracking refs of its own: scan everything reachable.
    configured = _decode(_git("remote", check=False).stdout).split()
    if remote_name in configured:
        result = _git("rev-list", local_sha, "--not", f"--remotes={remote_name}")
    else:
        result = _git("rev-list", local_sha)
    return _decode(result.stdout).split()


def _parents(sha: str) -> list[str]:
    out = _decode(_git("rev-list", "--parents", "-n", "1", sha).stdout).split()
    return out[1:]


def _diff_args(sha: str) -> list[str]:
    parents = _parents(sha)
    if parents:
        return ["diff", "--no-color", "--no-ext-diff", "-M", parents[0], sha]
    return ["diff-tree", "--no-color", "--no-ext-diff", "-M", "-r", "--root", sha]


def scan_commit(sha: str, terms: list[tuple[int, re.Pattern[str]]]) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    base = _diff_args(sha)

    message = _decode(_git("log", "-1", "--format=%B", sha).stdout)
    for number in matching_terms(message, terms):
        hits.append(("commit message", number))

    names = _decode(_git(*base, "--name-status", "-z", "--diff-filter=ACR").stdout)
    fields = [f for f in names.split("\0") if f != ""]
    if base[0] == "diff-tree" and fields and fields[0] == sha:
        fields = fields[1:]
    i = 0
    while i < len(fields):
        status = fields[i]
        if status[:1] in ("R", "C"):
            new_name = fields[i + 2] if i + 2 < len(fields) else ""
            i += 3
        else:
            new_name = fields[i + 1] if i + 1 < len(fields) else ""
            i += 2
        for number in matching_terms(new_name, terms):
            hits.append((new_name, number))

    patch = _decode(_git(*base, "-p", "--src-prefix=a/", "--dst-prefix=b/").stdout)
    current = "(unknown file)"
    in_hunk = False                                    # "+++ " is a header only BEFORE a file's first "@@"
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk and line.startswith("+++ "):
            target = line[4:]
            if target.startswith('"') and target.endswith('"'):
                target = target[1:-1]
            current = target[2:] if target.startswith("b/") else target
            continue
        if line.startswith("+"):
            for number in matching_terms(line[1:], terms):
                hits.append((current, number))
    return hits


def run_pre_push(argv: list[str]) -> int:
    remote_name = argv[0] if argv else ""
    remote_url = argv[1] if len(argv) > 1 else remote_name
    if not remote_is_listed(remote_url):
        return 0

    try:
        terms = load_terms()
    except TermsError as exc:
        print(f"refused: push to a listed public remote, but {exc}.", file=sys.stderr)
        print("  Create the terms file (one regex per line) or unlist the remote.", file=sys.stderr)
        return 1

    seen: set[str] = set()
    reported: set[tuple[str, int]] = set()
    refusals: list[str] = []
    for raw in sys.stdin.read().splitlines():
        parts = raw.split()
        if len(parts) != 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts
        if ZERO_SHA_RE.match(local_sha):
            continue  # deleting a remote ref publishes nothing
        for sha in commits_to_push(local_sha, remote_sha, remote_name):
            if sha in seen:
                continue
            seen.add(sha)
            for where, number in scan_commit(sha, terms):
                if (where, number) in reported:
                    continue
                reported.add((where, number))
                refusals.append(f"refused: {where} matches term #{number} (commit {sha[:10]})")

    if refusals:
        for line in refusals:
            print(line, file=sys.stderr)
        print(
            f"pre-push: {len(refusals)} forbidden-term hit(s) for a public remote; push refused.",
            file=sys.stderr,
        )
        return 1
    return 0


# --------------------------------------------------------------- scan-tree


def _read_capped(path: Path) -> bytes | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as fh:
            return fh.read(SCAN_CAP_BYTES)
    except OSError:
        return None


def run_scan_tree() -> int:
    try:
        terms = load_terms()
    except TermsError as exc:
        print(f"scan-tree: {exc}", file=sys.stderr)
        return 2

    top = _decode(_git("rev-parse", "--show-toplevel").stdout).strip()
    listing = _decode(_git("-C", top, "ls-files", "-z").stdout)
    hits = 0
    files_hit = 0
    for rel in (p for p in listing.split("\0") if p):
        numbers: set[int] = set(matching_terms(rel, terms))
        data = _read_capped(Path(top) / rel)
        if data is not None and b"\0" not in data[:BINARY_PROBE_BYTES]:
            numbers.update(matching_terms(_decode(data), terms))
        if numbers:
            files_hit += 1
            for number in sorted(numbers):
                print(f"{rel}: term #{number}")
                hits += 1
    print(f"scan-tree: {hits} hit(s) in {files_hit} file(s)")
    return 1 if hits else 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--scan-tree":
        return run_scan_tree()
    return run_pre_push(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
