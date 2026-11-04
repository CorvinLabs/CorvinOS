"""The chat's persistence paths, run in the Windows-like mode (see conftest.py).

Each test drives real code through its public entry point. A regression of either Windows defect
class (``:`` in a session path, a bare POSIX-only attribute) makes one of these fail on Linux CI,
before it reaches a Windows install.
"""
from __future__ import annotations

import json
import os
import signal
import sys


def test_the_mode_is_in_force(windows_like):
    assert not hasattr(os, "O_NOFOLLOW") and not hasattr(os, "killpg") and not hasattr(os, "fork")
    assert not hasattr(signal, "SIGKILL") and not hasattr(signal, "SIGALRM")
    import fcntl
    assert getattr(fcntl, "_corvin_win_shim", False)
    fcntl.flock(0, fcntl.LOCK_EX)  # the Windows stand-in; must not raise


def test_platform_probes_are_green(windows_like):
    from corvin_core.aco import platform_probes as pp

    results = pp.run_all(windows_like)
    assert all(r.ok for r in results), [r.to_dict() for r in results if not r.ok]


def test_session_memory_dir_has_no_colon(windows_like):
    import memory_bridge as mb

    root = mb._session_root("_default", "discord", "chat:with:colons")
    assert ":" not in root.name, root.name
    mem = root / ".claude" / "memory"
    mb._write_cc_memory_files(mem, "Käse", "", "")
    assert list(mem.glob("corvin_*.md"))


def test_conversation_recall_round_trip(windows_like):
    import conversation_recall as cr

    try:
        r = cr.index_turn("web", "c1", user_text="Mein Hund heißt Rex", assistant_text="Notiert.")
        assert r.get("ok") is True, r
        assert len(cr.recall("Rex", channel="web", chat_key="c1")) == 1
        assert cr.forget(channel="web", chat_key="c1") == 1
    finally:
        cr._close_all_connections()


def test_skill_feedback_history_append(windows_like):
    from core.skills.feedback import history_store

    history_store.append("_default", feedback_id="f1", timestamp="2026-11-04T00:00:00Z",
                         skill_id="s", subject_id="x", rating=1, category="c")
    raw = history_store._path("_default").read_bytes()
    assert json.loads(raw.splitlines()[0])["feedback_id"] == "f1" and b"\r\n" not in raw


def test_prompt_skill_version_catalog(windows_like, tmp_path):
    from core.skills.prompt_skill_versions import PromptVersionCatalog

    cat = PromptVersionCatalog(tmp_path / "versions")
    v = cat.record("demo", "body")
    assert cat.record("demo", "body") == v


def test_mode_leaves_nothing_behind():
    """Outside the fixture the real modules are back (this test runs after the others)."""
    import fcntl
    assert not getattr(fcntl, "_corvin_win_shim", False)
    assert hasattr(os, "O_NOFOLLOW") and hasattr(signal, "SIGKILL")
    assert getattr(sys.modules.get("memory_bridge"), "_is_windows", lambda: False)() is False
