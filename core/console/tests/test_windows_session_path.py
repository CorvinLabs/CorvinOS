"""Regression: a console chat session dir is ``web:<sid>`` — the ``:`` is illegal
in a Windows filename, so create_session's mkdir raised
``NotADirectoryError: [WinError 267]`` and NO chat could be created on a fresh
Windows install. fs_safe_component neutralises it on Windows while staying a
POSIX no-op (Linux/macOS byte-identical, no migration).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from forge import paths as P  # type: ignore

_WIN_ILLEGAL = '<>:"/\\|?*'


def test_windows_branch_removes_all_illegal_chars():
    out = P.fs_safe_component(f'web:{"".join(_WIN_ILLEGAL)}abc', windows=True)
    assert not any(c in out for c in _WIN_ILLEGAL), out
    # the colon specifically becomes an underscore
    assert P.fs_safe_component("web:FNjkP6tz_7Hq", windows=True) == "web_FNjkP6tz_7Hq"


def test_posix_branch_is_noop_for_colon():
    # Existing Linux/macOS installs MUST keep web:<sid> (back-compat, no orphan).
    assert P.fs_safe_component("web:FNjkP6tz_7Hq", windows=False) == "web:FNjkP6tz_7Hq"
    # only / and NUL are illegal on POSIX
    assert P.fs_safe_component("a/b", windows=False) == "a_b"


def test_traversal_and_reserved_neutralised():
    assert P.fs_safe_component("..", windows=True) not in ("..", ".", "")
    assert P.fs_safe_component("../../etc", windows=True).count("/") == 0
    assert P.fs_safe_component("CON", windows=True).startswith("_")
    assert P.fs_safe_component("nul.txt", windows=True).startswith("_")
    # trailing dot/space stripped (Windows)
    assert not P.fs_safe_component("name. ", windows=True).endswith((" ", "."))


def test_sanitised_name_is_actually_mkdir_able():
    # The Windows-safe name must be a valid single component everywhere.
    with tempfile.TemporaryDirectory() as td:
        safe = P.fs_safe_component("web:abc-123_XYZ", windows=True)
        d = Path(td) / safe
        d.mkdir(parents=True)
        assert d.is_dir() and ":" not in safe


def test_workdir_creatable_with_forced_windows_sanitizer():
    # End-to-end shape: tenant_sessions_dir / fs_safe_component("web:<sid>")
    # mkdirs cleanly with the Windows sanitizer (the create_session crash path).
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "sessions"
        comp = P.fs_safe_component("web:FNjkP6tz_7HqFoDTpP703A", windows=True)
        wd = base / comp
        wd.mkdir(parents=True, exist_ok=True)   # would be WinError 267 with ':'
        assert wd.is_dir() and ":" not in comp


def test_workdir_roundtrip_writer_equals_reader():
    # The single _workdir builder → writer and every reader agree (no drift).
    os.environ["CORVIN_HOME"] = ""  # use default resolution; just compare equality
    sys.path.insert(0, str(_REPO / "core" / "console"))
    for n in list(sys.modules):
        if n.startswith("corvin_console.chat_runtime"):
            sys.modules.pop(n, None)
    from corvin_console import chat_runtime as cr  # type: ignore
    a = cr._workdir("_default", "FNjkP6tz_7HqFoDTpP703A")
    b = cr._workdir("_default", "FNjkP6tz_7HqFoDTpP703A")
    assert a == b
    # On POSIX the name keeps the colon (back-compat); on Windows it must not.
    leaf = a.name
    if os.name == "nt":
        assert ":" not in leaf
    else:
        assert leaf == "web:FNjkP6tz_7HqFoDTpP703A"


def test_safe_session_subdir_prefers_existing_legacy():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        legacy = base / "web:sid1"
        # simulate a POSIX install that already has the colon dir
        try:
            legacy.mkdir()
        except OSError:
            return  # Windows can't create it → nothing to prefer; skip
        got = P.safe_session_subdir(base, "web:sid1", windows=False)
        assert got == legacy


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
