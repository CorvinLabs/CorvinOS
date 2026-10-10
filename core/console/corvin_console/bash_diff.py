"""Chat diff for file changes made through ``Bash`` (ADR-2241 amendment).

``Edit`` / ``MultiEdit`` / ``Write`` carry the CLI's own ``structuredPatch``; a
``Bash`` command (``sed -i``, a python heredoc, ``cat >``, ``mv``) carries
nothing, so its card showed no change. This module recovers the change by
looking at the files the command NAMES — before and after — and nothing else.

Two halves, one file, no shared mutable state between them:

* **Hook half** (``python bash_diff.py``, a Claude Code ``PreToolUse`` hook on
  ``Bash``): reads the hook payload from stdin, extracts the file paths the
  command mentions, and writes one atomic ``<tool_use_id>.json`` snapshot into
  ``$CORVIN_BASH_DIFF_DIR``. A PreToolUse hook finishes BEFORE the tool runs, so
  the snapshot cannot race the command — the reason for a hook rather than a
  snapshot taken when the stream event arrives (the CLI may already be
  executing by then). The hook never denies and never raises: it is
  observability, not a gate; any failure means "no diff", never a wrong one.
* **Console half** (:func:`diff_from_snapshot`): after the tool's result event,
  re-reads the same paths, diffs, and returns unified-diff lines in the shape
  ``chat_runtime._tool_result_diff`` produces. Runs through the same
  fail-closed credential gate and per-turn budget as the Edit/Write diff.

A snapshot file is written once by the hook and read once by the console
after the tool finished; each tool-use id has its own file, so concurrent
Bash calls never touch the same one. What is NOT covered, stated plainly: a
file the command changes without naming it (a glob, ``git checkout``, a build
tool's output), a file over ``MAX_FILE_BYTES``, a binary, a credential-shaped
file name, anything under ``.git`` / ``node_modules``. Two concurrent
commands (or another session) writing the SAME file inside one tool window
are attributed to both.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any

SNAP_ENV = "CORVIN_BASH_DIFF_DIR"
MAX_FILES = 12
MAX_FILE_BYTES = 256 * 1024
MAX_FILE_LINES = 4000          # difflib is quadratic in the worst case
MAX_COMMAND_CHARS = 64 * 1024  # a longer command is not scanned at all
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_SKIP_PARTS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "session_ledger", ".corvin-ledger", "cel_anchors",
    "pending_notifications",
})
# A file that is a credential store by NAME is never snapshotted, whatever its
# content: the snapshot is a copy on disk, and the credential gate only sees
# the diff afterwards.
_SKIP_NAME_RE = re.compile(
    r"(^\.env(\..*)?$|\.(pem|key|p12|pfx|jks|kdbx|sqlite3?|db|lock)$|^id_(rsa|dsa|ecdsa|ed25519)"
    r"|credential|secret|passwd|shadow|\.netrc$|\.npmrc$|\.pypirc$|token)", re.I)
# A path-looking string literal inside a script (python/node/shell heredoc).
_QUOTED_PATH_RE = re.compile(
    r"""["']((?:~|\.{1,2})?/?[\w.@+\-/]*[\w@+\-]\.[A-Za-z0-9]{1,8}|/[\w.@+\-/]+)["']""")


# ── path extraction (hook side) ───────────────────────────────────────────────

def _tokens(command: str) -> list[str]:
    # Shell operators are separators, not part of a name (`cat f.py)|wc`, `> g.txt;`).
    command = re.sub(r"[;&|()<>]", " ", command)
    try:
        toks = shlex.split(command, posix=True, comments=False)
    except ValueError:
        toks = command.split()
    out: list[str] = []
    for t in toks:
        t = t.split("=", 1)[1] if re.match(r"^[A-Za-z_-]+=", t) and "/" in t else t  # of=/x, --file=/x
        if t:
            out.append(t)
    return out


def candidate_paths(command: str, cwd: str) -> list[Path]:
    """Absolute paths the command names, in first-seen order, capped. A token
    qualifies when it exists as a regular file, or looks like a file name
    (has an extension or a slash) inside an existing directory — a file the
    command is about to create."""
    if not command or len(command) > MAX_COMMAND_CHARS:
        return []
    base = Path(cwd or ".")
    seen: dict[Path, None] = {}
    raw = _tokens(command) + _QUOTED_PATH_RE.findall(command)
    for tok in raw:
        if len(tok) > 1024 or "\n" in tok or "\x00" in tok or tok.startswith("-"):
            continue
        try:
            p = Path(os.path.expanduser(tok))
            p = p if p.is_absolute() else base / p
            p = Path(os.path.normpath(p))
        except (OSError, ValueError, RuntimeError):
            continue
        if _SKIP_PARTS & set(p.parts) or _SKIP_NAME_RE.search(p.name):
            continue
        try:
            if p.is_file():
                ok = True
            elif p.exists():           # a directory, device, socket …
                ok = False
            else:                      # to be created
                ok = ("." in p.name or "/" in tok) and p.parent.is_dir()
        except OSError:
            ok = False
        if ok:
            seen.setdefault(p, None)
        if len(seen) >= MAX_FILES:
            break
    return list(seen)


def _read_text(p: Path) -> str | None:
    """File text, or None when it is absent / too big / binary / unreadable.
    ``None`` is NOT "empty": callers tell absence apart via ``exists``."""
    try:
        if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            return None
        data = p.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _entry(p: Path) -> dict[str, Any]:
    text = _read_text(p)
    if text is not None:
        return {"state": "text", "sha": hashlib.sha256(text.encode()).hexdigest(), "text": text}
    try:
        exists = p.exists()
    except OSError:
        exists = True
    # Present but not diffable (too big / binary): remembered so a later
    # change is NOT mistaken for a creation.
    return {"state": "opaque" if exists else "absent"}


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".snap-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def take_snapshot(command: str, cwd: str, tool_use_id: str, snap_dir: str) -> int:
    """Write the pre-command snapshot. Returns the number of files recorded."""
    if not _ID_RE.match(tool_use_id or "") or not snap_dir:
        return 0
    d = Path(snap_dir)
    if not d.is_dir():
        return 0
    paths = candidate_paths(command, cwd)
    if not paths:
        return 0
    _atomic_write(d / f"{tool_use_id}.json",
                  {"cwd": cwd, "files": {str(p): _entry(p) for p in paths}})
    return len(paths)


def hook_main() -> int:
    """PreToolUse hook entry. Always exits 0 — it must never block a tool."""
    try:
        payload = json.load(sys.stdin)
        snap_dir = os.environ.get(SNAP_ENV, "")
        if snap_dir and payload.get("tool_name") == "Bash":
            take_snapshot(str((payload.get("tool_input") or {}).get("command") or ""),
                          str(payload.get("cwd") or os.getcwd()),
                          str(payload.get("tool_use_id") or ""), snap_dir)
    except Exception:  # noqa: BLE001 — observability only; no diff beats a wrong diff
        pass
    return 0


# ── console side ──────────────────────────────────────────────────────────────

_SNAP_PREFIX = "corvin-bashdiff-"
_STALE_S = 6 * 3600


def make_snap_dir() -> str:
    """A fresh per-turn snapshot directory (mode 0700). The caller removes it
    when the turn ends; ``sweep_stale`` reaps what a killed process left."""
    sweep_stale()
    return tempfile.mkdtemp(prefix=_SNAP_PREFIX)


def remove_snap_dir(snap_dir: str) -> None:
    import shutil
    if snap_dir and Path(snap_dir).name.startswith(_SNAP_PREFIX):
        shutil.rmtree(snap_dir, ignore_errors=True)


def sweep_stale(now: float | None = None) -> int:
    """Remove snapshot dirs older than ``_STALE_S`` (a turn never lasts that
    long). Best effort; returns how many were removed."""
    import time
    cutoff = (now if now is not None else time.time()) - _STALE_S
    n = 0
    try:
        for d in Path(tempfile.gettempdir()).glob(_SNAP_PREFIX + "*"):
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    remove_snap_dir(str(d))
                    n += 1
            except OSError:
                continue
    except OSError:
        pass
    return n


def hook_settings_json(python: str | None = None) -> str:
    """The ``--settings`` document that installs the snapshot hook on Bash."""
    py = python or sys.executable or "python3"
    cmd = f"{shlex.quote(py)} {shlex.quote(str(Path(__file__).resolve()))}"
    return json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": cmd, "timeout": 5}]}]}})


def _file_hunks(rel: str, before: str, after: str) -> list[str]:
    a, b = before.splitlines(), after.splitlines()
    if len(a) > MAX_FILE_LINES or len(b) > MAX_FILE_LINES:
        return []
    out: list[str] = []
    for line in difflib.unified_diff(a, b, n=3, lineterm=""):
        if line.startswith(("--- ", "+++ ")):
            continue
        if line.startswith("@@"):
            # "@@ -1,2 +1,2 @@" → "@@ <file> -1,2 +1,2 @@": still a hunk line
            # for the UI, now saying WHICH file (a Bash change can touch several).
            line = f"@@ {rel} " + line[3:]
        out.append(line)
    return out


def _display_path(path: str, cwd: str) -> str:
    """The path relative to the command's cwd when it is inside it, else absolute."""
    try:
        rel = os.path.relpath(path, cwd) if cwd else path
    except ValueError:
        return path
    return path if rel.startswith("..") else rel


def diff_from_snapshot(snap_dir: str, tool_use_id: str) -> list[str] | None:
    """Unified-diff lines for the files the command changed, or None when no
    snapshot exists / nothing changed. Blocking file IO — call off the loop."""
    if not _ID_RE.match(tool_use_id or ""):
        return None
    f = Path(snap_dir) / f"{tool_use_id}.json"
    try:
        snap = json.loads(f.read_text(encoding="utf-8"))
        files = snap["files"]
    except (OSError, ValueError, KeyError):
        return None
    finally:
        try:
            f.unlink()
        except OSError:
            pass
    cwd = str(snap.get("cwd") or "")
    lines: list[str] = []
    for path, before in files.items():
        p = Path(path)
        now = _entry(p)
        if before.get("state") == "opaque" or now["state"] == "opaque":
            continue
        if before.get("state") == "absent" and now["state"] == "absent":
            continue
        if before.get("sha") and before.get("sha") == now.get("sha"):
            continue
        rel = _display_path(path, cwd)
        b = before.get("text", "") if before.get("state") == "text" else ""
        a = now.get("text", "") if now["state"] == "text" else ""
        if not b and not a:
            continue
        hunks = _file_hunks(rel, b, a)
        if hunks:
            lines.extend(hunks)
        elif before.get("state") == "text" and now["state"] == "absent":
            lines.append(f"@@ {rel} deleted @@")
    return lines or None


if __name__ == "__main__":
    sys.exit(hook_main())
