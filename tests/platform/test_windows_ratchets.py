"""Windows ratchets (PLAN-0969 WP-5, CONCEPT-0125): two defect classes that only break on Windows.

1. A session directory named ``<channel>:<chat>``: ``:`` is illegal in a Windows path component
   (WinError 123). It broke ACS runs and session memory on Windows 11. Session paths go through
   ``forge.paths.safe_session_subdir`` / ``fs_safe_component``.
2. A POSIX-only ``os`` function, ``signal`` constant or ``preexec_fn=`` outside a platform guard:
   an ``AttributeError`` / ``ValueError`` on Windows, often swallowed by a broad ``except``.

Both scan production code by AST (docstrings and comments never count). Each has an explicit
allowlist with a reason per entry; the allowlist may only shrink: an entry that no longer matches
fails too, so a fixed site cannot be quietly re-broken under its old exemption.
(The bare open-flag classes are pinned in ``tests/skills/test_windows_o_nofollow.py``.)
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("core", "corvin_operator", "corvinOS", "ops", "scripts")
SKIP_PARTS = {"tests", "node_modules", ".venv", "__pycache__", "worktrees", "templates"}

#: Functions of ``os`` that do not exist on Windows.
POSIX_OS = {"getuid", "geteuid", "getgid", "getegid", "getpgid", "getpgrp", "killpg", "setsid",
            "setpgrp", "fork", "forkpty", "uname", "getloadavg", "mkfifo", "chown", "lchown",
            "statvfs", "nice", "sync", "setuid", "setgid", "getgroups", "initgroups"}
#: Constants of ``signal`` that do not exist on Windows.
POSIX_SIGNALS = {"SIGKILL", "SIGHUP", "SIGUSR1", "SIGUSR2", "SIGALRM", "SIGCHLD", "SIGPIPE",
                 "SIGQUIT", "SIGTSTP", "SIGCONT", "SIGSTOP", "SIGWINCH", "SIGTTIN", "SIGTTOU"}
#: A platform guard within GUARD_WINDOW lines before or after the use.
GUARD = re.compile(
    r"hasattr\(|getattr\((os|signal)|sys\.platform|os\.name\b|platform\.system|_IS_WIN|IS_WINDOWS"
    r"|is_windows|_is_windows|_WINDOWS|_POSIX|IS_POSIX|start_new_session"
    r"|except\s*\(?[^)\n]*(AttributeError|ImportError|OSError|Exception)")
GUARD_WINDOW = 8

#: (file, symbol) -> why it may stay. Linux-only by construction, never reached on Windows.
POSIX_ALLOWLIST = {
    ("corvin_operator/bridges/shared/unit_sweep.py", "signal.SIGKILL"):
        "systemd cgroup-v2 sweep; sweep_unit_leftovers returns 0 unless this is the main process "
        "of a systemd service, which a Windows install never is",
    ("scripts/diagnostics/claude_strace_wrapper.py", "signal.SIGHUP"): "strace wrapper, Linux-only tool",
    ("scripts/diagnostics/claude_strace_wrapper.py", "signal.SIGQUIT"): "strace wrapper, Linux-only tool",
    ("scripts/diagnostics/claude_strace_wrapper.py", "signal.SIGUSR1"): "strace wrapper, Linux-only tool",
    ("scripts/diagnostics/claude_strace_wrapper.py", "signal.SIGUSR2"): "strace wrapper, Linux-only tool",
}

#: (file, line text fragment) -> why it may stay.
COLON_ALLOWLIST = {
    ("corvin_operator/bridges/shared/spg.py", 'f"{_safe(channel)}:{_safe(chat_key)}"'):
        "read-only lookup of a legacy POSIX directory behind .exists(); never created",
}


_CACHE: dict = {}


def _production_files():
    key = (str(REPO), tuple(ROOTS))
    if key not in _CACHE:
        _CACHE[key] = list(_scan_files())
    return _CACHE[key]


def _scan_files():
    for root in ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if set(path.parts) & SKIP_PARTS or path.name.startswith("test_"):
                continue
            try:
                src = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src)
            except (SyntaxError, ValueError):
                continue
            yield path.relative_to(REPO).as_posix(), src, tree


def _guarded(lines: list[str], lineno: int) -> bool:
    window = "\n".join(lines[max(0, lineno - 1 - GUARD_WINDOW): lineno + GUARD_WINDOW])
    return bool(GUARD.search(window))


def posix_only_uses() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for rel, src, tree in _production_files():
        lines = src.splitlines()
        for node in ast.walk(tree):
            sym = None
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "os" and node.attr in POSIX_OS:
                    sym = f"os.{node.attr}"
                elif node.value.id == "signal" and node.attr in POSIX_SIGNALS:
                    sym = f"signal.{node.attr}"
            elif isinstance(node, ast.keyword) and node.arg == "preexec_fn":
                if isinstance(node.value, ast.Constant) and node.value.value is None:
                    continue
                sym = "preexec_fn"
            if sym and not _guarded(lines, node.lineno if hasattr(node, "lineno") else node.value.lineno):
                found.add((rel, sym))
    return found


_COLON_JOIN = re.compile(r"\{[^{}]+\}:\{[^{}]+\}")


def colon_session_joins() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for rel, src, tree in _production_files():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                    and isinstance(node.right, ast.JoinedStr)):
                continue
            seg = ast.get_source_segment(src, node) or ""
            right = ast.get_source_segment(src, node.right) or ""
            if _COLON_JOIN.search(right) and "session" in seg.lower():
                found.add((rel, right.strip()))
    return found


def test_no_new_colon_named_session_directory():
    found = colon_session_joins()
    new = sorted(found - set(COLON_ALLOWLIST))
    assert not new, "':' in a session path component breaks Windows (WinError 123); use " \
                    "forge.paths.safe_session_subdir: " + ", ".join(f"{f}: {s}" for f, s in new)
    stale = sorted(set(COLON_ALLOWLIST) - found)
    assert not stale, f"allowlist entries no longer match, remove them: {stale}"


def test_no_new_unguarded_posix_only_call():
    found = posix_only_uses()
    new = sorted(found - set(POSIX_ALLOWLIST))
    assert not new, "POSIX-only os/signal use without a platform guard breaks Windows: " + \
                    ", ".join(f"{f} {s}" for f, s in new)
    stale = sorted(set(POSIX_ALLOWLIST) - found)
    assert not stale, f"allowlist entries no longer match, remove them: {stale}"


def test_ratchets_catch_a_planted_offender(tmp_path, monkeypatch):
    """Positive control: both scanners find the defect when it is there."""
    pkg = tmp_path / "core"
    pkg.mkdir()
    (pkg / "planted.py").write_text(
        "import os, signal\n"
        "from pathlib import Path\n"
        "def f(base: Path, channel, chat):\n"
        "    d = base / 'sessions' / f'{channel}:{chat}'\n"
        "    os.killpg(1, signal.SIGKILL)\n"
        "    return d\n", encoding="utf-8")
    me = sys.modules[__name__]
    monkeypatch.setattr(me, "REPO", tmp_path)
    monkeypatch.setattr(me, "ROOTS", ("core",))
    assert ("core/planted.py", "f'{channel}:{chat}'") in colon_session_joins()
    uses = posix_only_uses()
    assert ("core/planted.py", "os.killpg") in uses and ("core/planted.py", "signal.SIGKILL") in uses
