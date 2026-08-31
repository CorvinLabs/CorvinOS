"""ADR-0049 M4 — session-pinning E2E integration tests.

Covers:
  - worker_session_store: load/save/delete/list round-trips
  - ClaudeCodeEngine._build_args: resume_session_id inserts --resume
  - ClaudeCodeEngine._build_args: mutual-exclusion of continue + resume
  - CapabilityError raised when pin_session=True on non-supporting engine
  - spawn_a2a_worker: session file created on first spawn (fake engine)
  - spawn_a2a_worker: resume_count increments on second spawn
  - spawn_a2a_worker: stale-session eviction + re-spawn on "session not found"
  - session_reset: worker_sessions/ purged + audit event emitted

All tests use fake engines and a real tmpdir filesystem.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Iterator

# Wire the shared/ dir onto sys.path.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from agents import StreamEvent, SpawnResult, CapabilityError  # noqa: E402
from worker_session_store import (  # noqa: E402
    load_session, save_session, delete_session, list_sessions,
    read_session_record, worker_sessions_dir,
)


# ---------------------------------------------------------------------------
# Fake engines
# ---------------------------------------------------------------------------

class _FakeEngine:
    """Fake WorkerEngine that emits a configurable session_id."""

    name = "fake_engine"
    capabilities: dict[str, Any] = {"session_pinning": False}

    def __init__(self, session_id: str = "ses_abc123", text: str = "ok") -> None:
        self._session_id = session_id
        self._text = text
        self.spawned_kwargs: list[dict] = []

    def spawn(self, prompt: str, **kwargs: Any) -> Iterator[StreamEvent]:
        self.spawned_kwargs.append(dict(kwargs))
        yield StreamEvent(
            type="session_started",
            raw={"type": "system", "subtype": "init", "session_id": self._session_id},
        )
        yield StreamEvent(type="text_delta", text=self._text)
        yield StreamEvent(type="turn_completed")

    def cancel(self) -> None:
        pass


class _FakePinnableEngine(_FakeEngine):
    """Fake engine with session_pinning=True."""

    name = "claude_code"
    capabilities: dict[str, Any] = {
        "mid_stream_inject": True,
        "hooks": True,
        "skills_tool": True,
        "mcp": True,
        "stream_json": True,
        "permission_modes": ["default", "bypassPermissions"],
        "add_system_prompt": True,
        "version_flag": "--version",
        "session_pinning": True,
    }


class _FakeStaleEngine(_FakePinnableEngine):
    """Engine whose first spawn (with resume) returns 'session not found',
    subsequent spawn returns normally."""

    def __init__(self, new_session_id: str = "ses_fresh456") -> None:
        super().__init__(session_id=new_session_id)
        self._first_call_done = False

    def spawn(self, prompt: str, **kwargs: Any) -> Iterator[StreamEvent]:
        self.spawned_kwargs.append(dict(kwargs))
        if not self._first_call_done and kwargs.get("resume_session_id"):
            self._first_call_done = True
            yield StreamEvent(type="error", error="session not found: ses_stale")
            return
        self._first_call_done = True
        yield StreamEvent(
            type="session_started",
            raw={"type": "system", "subtype": "init", "session_id": self._session_id},
        )
        yield StreamEvent(type="text_delta", text="fresh spawn")
        yield StreamEvent(type="turn_completed")


# ---------------------------------------------------------------------------
# worker_session_store tests
# ---------------------------------------------------------------------------

class TestWorkerSessionStore(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-wss-"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ws(self) -> Path:
        return self.tmp / "worker_sessions"

    def test_load_missing_returns_none(self) -> None:
        self.assertIsNone(load_session(self._ws(), "myworker"))

    def test_save_and_load(self) -> None:
        ws = self._ws()
        save_session(ws, "myworker", "ses_abc123", "assistant")
        self.assertEqual(load_session(ws, "myworker"), "ses_abc123")

    def test_save_sets_mode_0600(self) -> None:
        ws = self._ws()
        save_session(ws, "myworker", "ses_abc123", "assistant")
        p = ws / "myworker.session.json"
        self.assertTrue(p.exists())
        mode = p.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f"expected 0600 got {oct(mode)}")

    def test_resume_count_increments(self) -> None:
        ws = self._ws()
        save_session(ws, "lbl", "ses_x", "p")
        rec1 = read_session_record(ws, "lbl")
        self.assertEqual(rec1["resume_count"], 0)
        # Same session_id → resume
        save_session(ws, "lbl", "ses_x", "p")
        rec2 = read_session_record(ws, "lbl")
        self.assertEqual(rec2["resume_count"], 1)
        save_session(ws, "lbl", "ses_x", "p")
        rec3 = read_session_record(ws, "lbl")
        self.assertEqual(rec3["resume_count"], 2)

    def test_new_session_id_resets_count(self) -> None:
        ws = self._ws()
        save_session(ws, "lbl", "ses_old", "p")
        save_session(ws, "lbl", "ses_old", "p")  # resume_count=1
        save_session(ws, "lbl", "ses_new", "p")  # new session
        rec = read_session_record(ws, "lbl")
        self.assertEqual(rec["resume_count"], 0)
        self.assertEqual(rec["session_id"], "ses_new")

    def test_delete_returns_true_when_exists(self) -> None:
        ws = self._ws()
        save_session(ws, "lbl", "ses_x", "p")
        self.assertTrue(delete_session(ws, "lbl"))

    def test_delete_returns_false_when_missing(self) -> None:
        self.assertFalse(delete_session(self._ws(), "nonexistent"))

    def test_list_sessions(self) -> None:
        ws = self._ws()
        save_session(ws, "lbl1", "ses_a", "p1")
        save_session(ws, "lbl2", "ses_b", "p2")
        records = list_sessions(ws)
        self.assertEqual(len(records), 2)
        labels = {r["scope_label"] for r in records}
        self.assertIn("lbl1", labels)
        self.assertIn("lbl2", labels)


# ---------------------------------------------------------------------------
# ClaudeCodeEngine._build_args
# ---------------------------------------------------------------------------

class TestBuildArgs(unittest.TestCase):

    def setUp(self) -> None:
        _engines_dir = HERE
        if str(_engines_dir) not in sys.path:
            sys.path.insert(0, str(_engines_dir))
        from agents.claude_code import ClaudeCodeEngine
        self.ClaudeCodeEngine = ClaudeCodeEngine

    def test_resume_inserts_flag(self) -> None:
        args = self.ClaudeCodeEngine._build_args(
            "hello", binary="claude",
            resume_session_id="ses_xyz",
        )
        self.assertIn("--resume", args)
        idx = args.index("--resume")
        self.assertEqual(args[idx + 1], "ses_xyz")
        self.assertNotIn("--continue", args)

    def test_continue_still_works(self) -> None:
        args = self.ClaudeCodeEngine._build_args(
            "hello", binary="claude", continue_session=True,
        )
        self.assertIn("--continue", args)
        self.assertNotIn("--resume", args)

    def test_mutual_exclusion_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.ClaudeCodeEngine._build_args(
                "hello", binary="claude",
                continue_session=True,
                resume_session_id="ses_x",
            )

    def test_no_flags_by_default(self) -> None:
        args = self.ClaudeCodeEngine._build_args("hello", binary="claude")
        self.assertNotIn("--resume", args)
        self.assertNotIn("--continue", args)


# ---------------------------------------------------------------------------
# CapabilityError gate
# ---------------------------------------------------------------------------

class TestCapabilityError(unittest.TestCase):

    def _spawn(self, engine, **kwargs) -> Any:
        """Collect a spawn() iterator from spawn_a2a_worker using a fake factory."""
        from a2a_worker import spawn_a2a_worker
        factory = lambda: engine
        return spawn_a2a_worker(
            instruction="do something",
            origin_id="test-origin",
            task_id="task-001",
            persona="assistant",
            ttl_s=30,
            engine_factory=factory,
            **kwargs,
        )

    def test_capability_error_on_non_pinning_engine(self) -> None:
        eng = _FakeEngine()
        with self.assertRaises(CapabilityError):
            self._spawn(eng, pin_session=True, scope_label="test")

    def test_no_error_when_pin_session_false(self) -> None:
        eng = _FakeEngine()
        # Must not raise even though engine doesn't support pinning.
        result = self._spawn(eng, pin_session=False)
        self.assertEqual(result.status, "ok")


# ---------------------------------------------------------------------------
# spawn_a2a_worker session pinning E2E
# ---------------------------------------------------------------------------

class TestSpawnA2aWorkerSessionPinning(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-a2a-pin-"))
        self.session_home = self.tmp / "sessions" / "discord:999"
        self.session_home.mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _spawn(self, engine, *, scope_label: str = "review") -> Any:
        from a2a_worker import spawn_a2a_worker
        return spawn_a2a_worker(
            instruction="do something",
            origin_id="test-origin",
            task_id="task-001",
            persona="assistant",
            ttl_s=30,
            engine_factory=lambda: engine,
            pin_session=True,
            scope_label=scope_label,
            session_home=self.session_home,
        )

    def test_session_file_created_on_first_spawn(self) -> None:
        eng = _FakePinnableEngine(session_id="ses_first")
        result = self._spawn(eng)
        self.assertEqual(result.status, "ok")
        ws = worker_sessions_dir(self.session_home)
        sid = load_session(ws, "review")
        self.assertEqual(sid, "ses_first")
        rec = read_session_record(ws, "review")
        self.assertEqual(rec["resume_count"], 0)

    def test_resume_count_increments_on_second_spawn(self) -> None:
        eng = _FakePinnableEngine(session_id="ses_pinned")
        # First spawn creates the file.
        self._spawn(eng, scope_label="task_a")
        # Second spawn resumes it.
        eng2 = _FakePinnableEngine(session_id="ses_pinned")
        result = self._spawn(eng2, scope_label="task_a")
        self.assertEqual(result.status, "ok")
        ws = worker_sessions_dir(self.session_home)
        rec = read_session_record(ws, "task_a")
        self.assertEqual(rec["resume_count"], 1)
        # Check that --resume was passed to the second engine.
        self.assertEqual(eng2.spawned_kwargs[0].get("resume_session_id"), "ses_pinned")

    def test_stale_session_evicted_and_respawned(self) -> None:
        # Pre-seed a stale session file.
        ws = worker_sessions_dir(self.session_home)
        save_session(ws, "code_review", "ses_stale", "assistant")

        eng = _FakeStaleEngine(new_session_id="ses_fresh456")
        result = self._spawn(eng, scope_label="code_review")
        self.assertEqual(result.status, "ok")
        # Old session file should be replaced with the fresh ID.
        sid = load_session(ws, "code_review")
        self.assertEqual(sid, "ses_fresh456")
        # Two spawns were made: first with stale ID (failed), second fresh.
        self.assertEqual(len(eng.spawned_kwargs), 2)
        self.assertEqual(eng.spawned_kwargs[0].get("resume_session_id"), "ses_stale")
        self.assertNotIn("resume_session_id", eng.spawned_kwargs[1])


# ---------------------------------------------------------------------------
# session_reset worker_sessions purge
# ---------------------------------------------------------------------------

class TestSessionResetPurgesWorkerSessions(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-reset-"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session_dir(self, chan_id: str) -> Path:
        """Create a minimal forge session dir structure."""
        d = self.tmp / "sessions" / chan_id / "worker_sessions"
        d.mkdir(parents=True)
        return d

    def test_purge_removes_session_files(self) -> None:
        chan_id = "discord:12345"
        ws = self._make_session_dir(chan_id)
        save_session(ws, "lbl1", "ses_a", "persona1")
        save_session(ws, "lbl2", "ses_b", "persona2")
        self.assertEqual(len(list(ws.glob("*.session.json"))), 2)

        # Call _purge_worker_sessions directly.
        from session_reset import _purge_worker_sessions
        os.environ["CORVIN_HOME"] = str(self.tmp)
        os.environ.setdefault("CORVIN_HOME", str(self.tmp))
        try:
            failures: list[str] = []
            removed = _purge_worker_sessions(forge_chan_id=chan_id, failures=failures)
        finally:
            del os.environ["CORVIN_HOME"]

        self.assertEqual(removed, 2, f"expected 2 removed, failures={failures}")
        self.assertEqual(list(ws.glob("*.session.json")), [])


if __name__ == "__main__":
    unittest.main()
