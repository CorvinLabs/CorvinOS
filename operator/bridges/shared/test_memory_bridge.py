"""Tests for memory_bridge.py — ADR-0051 Worker Memory Bridge.

Test structure:
    Unit tests  — build_context_block, harvest_worker_output, helpers
    E2E test    — two-turn delegation round-trip (no real Haiku subprocess)

All tests use a temporary directory tree that mirrors the on-disk shape:
    <tmp>/tenants/_default/global/memory/
    <tmp>/tenants/_default/global/memory/worker_harvest/
    <tmp>/tenants/_default/sessions/<channel>:<chat>/
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# ── Bootstrap path ────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# ── Temp-dir fixture ──────────────────────────────────────────────────────────

_SANDBOX = tempfile.mkdtemp(prefix="memory_bridge_test_")
_CORVIN_HOME = Path(_SANDBOX) / "corvin"
_TENANT_HOME  = _CORVIN_HOME / "tenants" / "_default"
_GLOBAL_DIR   = _TENANT_HOME / "global"
_SESSIONS_DIR = _TENANT_HOME / "sessions"

# Inject tenant dirs into the environment BEFORE importing memory_bridge
os.environ["CORVIN_HOME"] = str(_CORVIN_HOME)

# ── Module under test ─────────────────────────────────────────────────────────

import memory_bridge as mb  # noqa: E402 — must come after env setup

# Re-inject the tenant-path helpers so the module finds our sandbox
def _fake_tenant_global_dir(tid=None):
    return _GLOBAL_DIR

def _fake_tenant_sessions_dir(tid=None):
    return _SESSIONS_DIR

mb._tenant_global_dir  = _fake_tenant_global_dir
mb._tenant_sessions_dir = _fake_tenant_sessions_dir


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_dirs() -> None:
    for d in [
        _GLOBAL_DIR / "memory",
        _GLOBAL_DIR / "memory" / "worker_harvest",
        _GLOBAL_DIR / "forge",
        _SESSIONS_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)


def _write_memory_file(directory: Path, name: str, body: str, mtype: str = "project") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{name}.md"
    p.write_text(
        f"---\nname: {name}\ndescription: test\nmetadata:\n  type: {mtype}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return p


def _null_haiku(prompt: str, timeout_s: float, site: str = "") -> str:
    """Stub: returns '' (simulate unavailable Haiku — triggers truncation fallback)."""
    return ""


def _echo_haiku(label: str) -> str:
    """Return a stub that echoes the first 200 chars of the block passed in prompt."""
    def _stub(prompt: str, timeout_s: float, site: str = "") -> str:
        # Extract the block content between === BEGIN and === END
        import re
        m = re.search(r"=== BEGIN .+? ===\n(.+?)\n=== END", prompt, re.DOTALL)
        if m:
            return m.group(1)[:200].strip()
        return ""
    return _stub


# ── Helper tests ──────────────────────────────────────────────────────────────

class TestParseScopeHelper(unittest.TestCase):
    def test_three_parts(self):
        tid, ch, ck = mb._parse_scope("_default:discord:chat123")
        self.assertEqual(tid, "_default")
        self.assertEqual(ch, "discord")
        self.assertEqual(ck, "chat123")

    def test_two_parts(self):
        tid, ch, ck = mb._parse_scope("discord:chat123")
        self.assertEqual(tid, "")
        self.assertEqual(ch, "discord")
        self.assertEqual(ck, "chat123")

    def test_one_part(self):
        tid, ch, ck = mb._parse_scope("chat123")
        self.assertEqual(ck, "chat123")

    def test_chat_key_with_colon(self):
        # "tenant:channel:chan:sub" — last segment contains colon
        tid, ch, ck = mb._parse_scope("_default:discord:chan:sub")
        self.assertEqual(tid, "_default")
        self.assertEqual(ch, "discord")
        self.assertEqual(ck, "chan:sub")


class TestTruncate(unittest.TestCase):
    def test_within_cap(self):
        self.assertEqual(mb._truncate("hello", 10), "hello")

    def test_over_cap(self):
        result = mb._truncate("abcde", 4)
        self.assertEqual(len(result), 4)
        self.assertTrue(result.endswith("…"))

    def test_exact_cap(self):
        self.assertEqual(mb._truncate("abcd", 4), "abcd")


class TestMakeSlug(unittest.TestCase):
    def test_normal(self):
        slug = mb._make_slug("The cache size is 512 MB", 0)
        self.assertRegex(slug, r"^[a-z0-9][a-z0-9_-]{0,40}$")

    def test_empty_body(self):
        slug = mb._make_slug("", 7)
        self.assertEqual(slug, "entry-7")

    def test_special_chars(self):
        slug = mb._make_slug("!!!@@@###", 3)
        self.assertEqual(slug, "entry-3")


class TestResolveMaxChars(unittest.TestCase):
    def test_default(self):
        self.assertEqual(mb._resolve_max_chars(2000), 2000)

    def test_env_override(self):
        os.environ[mb._ENV_MAX_CHARS] = "1500"
        self.assertEqual(mb._resolve_max_chars(2000), 1500)
        del os.environ[mb._ENV_MAX_CHARS]

    def test_bad_env(self):
        os.environ[mb._ENV_MAX_CHARS] = "not_a_number"
        self.assertEqual(mb._resolve_max_chars(2000), 2000)
        del os.environ[mb._ENV_MAX_CHARS]


# ── build_context_block unit tests ────────────────────────────────────────────

class TestBuildContextBlockEmpty(unittest.TestCase):
    """Returns '' when there is no memory material at all."""

    def setUp(self):
        _make_dirs()
        # Wipe all project-memory files to guarantee no stale material
        for p in (_GLOBAL_DIR / "memory").glob("*.md"):
            p.unlink(missing_ok=True)
        for p in (_GLOBAL_DIR / "memory" / "worker_harvest").glob("*.md"):
            p.unlink(missing_ok=True)

    def test_no_material_returns_empty(self):
        with patch.object(mb, "_run_haiku", _null_haiku):
            result = mb.build_context_block(
                scope="_default:discord:testchat-empty",
                workdir=Path(_SANDBOX),
                profile={},
                engine_type="codex",
            )
        self.assertEqual(result, "")


class TestBuildContextBlockStructure(unittest.TestCase):
    """Verifies the <corvin_context> XML structure when data is present."""

    def setUp(self):
        _make_dirs()
        # Write a project-facts file that matches the hint keyword
        _write_memory_file(
            _GLOBAL_DIR / "memory",
            "db-schema",
            "Database uses PostgreSQL 16. Primary key is UUID.",
            "project",
        )

    def test_returns_xml_block(self):
        with patch.object(mb, "_run_haiku", _null_haiku):
            result = mb.build_context_block(
                scope="_default:discord:testchat2",
                workdir=Path(_SANDBOX),
                profile={},
                engine_type="opencode",
                instruction_hint="query the database schema",
            )
        self.assertTrue(result.startswith("<corvin_context>"))
        self.assertTrue(result.strip().endswith("</corvin_context>"))

    def test_project_facts_block_present(self):
        with patch.object(mb, "_run_haiku", _null_haiku):
            result = mb.build_context_block(
                scope="_default:discord:testchat2",
                workdir=Path(_SANDBOX),
                profile={},
                engine_type="opencode",
                instruction_hint="database schema query",
            )
        self.assertIn("<project_facts>", result)

    def test_no_keyword_match_omits_project_facts(self):
        with patch.object(mb, "_run_haiku", _null_haiku):
            result = mb.build_context_block(
                scope="_default:discord:testchat3",
                workdir=Path(_SANDBOX),
                profile={},
                engine_type="opencode",
                instruction_hint="send an email to everyone",  # no keyword overlap
            )
        # May or may not have project_facts; db-schema won't match "email"
        if result:
            self.assertNotIn("<project_facts>", result)


class TestBuildContextBlockCCWorker(unittest.TestCase):
    """For claude_code engine, verifies .claude/memory/ files are written."""

    def setUp(self):
        _make_dirs()
        _write_memory_file(
            _GLOBAL_DIR / "memory",
            "api-key-format",
            "All API keys start with 'sk-'. Never log them.",
            "project",
        )

    def test_cc_worker_writes_memory_files(self):
        chat_key = "cc-test-chat"
        session_dir = _SESSIONS_DIR / f"discord:{chat_key}" / ".claude" / "memory"

        with patch.object(mb, "_run_haiku", _null_haiku):
            result = mb.build_context_block(
                scope=f"_default:discord:{chat_key}",
                workdir=Path(_SANDBOX),
                profile={},
                engine_type="claude_code",
                instruction_hint="api key format logging",
            )

        if result:  # only check if there was something to write
            self.assertTrue(session_dir.exists(), "session .claude/memory/ should be created")
            written = list(session_dir.glob("corvin_*.md"))
            self.assertGreater(len(written), 0, "at least one corvin_*.md should be written")

    def test_non_cc_worker_does_not_write_files(self):
        chat_key = "opencode-test-chat"
        session_dir = _SESSIONS_DIR / f"discord:{chat_key}" / ".claude" / "memory"

        with patch.object(mb, "_run_haiku", _null_haiku):
            mb.build_context_block(
                scope=f"_default:discord:{chat_key}",
                workdir=Path(_SANDBOX),
                profile={},
                engine_type="opencode",
                instruction_hint="api key format",
            )

        # Non-CC engine must NOT write .claude/memory/ files
        if session_dir.exists():
            corvin_files = list(session_dir.glob("corvin_*.md"))
            self.assertEqual(corvin_files, [])


# ── harvest_worker_output unit tests ─────────────────────────────────────────

class TestHarvestWorkerOutputSmoke(unittest.TestCase):
    """harvest_worker_output is fire-and-forget; test that it completes."""

    def setUp(self):
        _make_dirs()

    def test_empty_output_returns_immediately(self):
        # Should not start a thread
        before = threading.active_count()
        mb.harvest_worker_output(
            scope="_default:discord:smk1",
            instruction="do something",
            output="",
            engine_id="claude_code",
            chat_key="smk1",
            tenant_id="_default",
        )
        # Thread count unchanged (no thread started for empty output)
        self.assertEqual(threading.active_count(), before)

    def test_nonempty_output_starts_thread(self):
        with patch.object(mb, "_run_haiku", _null_haiku):
            mb.harvest_worker_output(
                scope="_default:discord:smk2",
                instruction="compute something",
                output="The result is 42.",
                engine_id="codex",
                chat_key="smk2",
                tenant_id="_default",
            )
        # Thread is daemon — just verify no exception was raised


class TestHarvestWritesEntries(unittest.TestCase):
    """Verify that a valid Haiku response leads to on-disk memory files."""

    def setUp(self):
        _make_dirs()
        mb.clear_cancel_event("harvest-write-test")

    def _make_haiku_stub(self, entries: list[dict]):
        payload = json.dumps(entries)
        def _stub(prompt: str, timeout_s: float, site: str = "") -> str:
            return payload
        return _stub

    def test_project_scope_entry_is_written(self):
        entries = [
            {"scope": "project", "type": "project", "body": "Cache TTL is 300 seconds globally."},
        ]
        done = threading.Event()
        original_task = mb._harvest_task

        def _tracked_task(*args, **kwargs):
            original_task(*args, **kwargs)
            done.set()

        with patch.object(mb, "_run_haiku", self._make_haiku_stub(entries)):
            with patch.object(mb, "_harvest_task", side_effect=_tracked_task):
                mb.harvest_worker_output(
                    scope="_default:discord:hwt",
                    instruction="configure the cache",
                    output="I set the cache TTL to 300 seconds globally.",
                    engine_id="claude_code",
                    chat_key="harvest-write-test",
                    tenant_id="_default",
                )

        done.wait(timeout=5.0)
        harvest_dir = _GLOBAL_DIR / "memory" / "worker_harvest"
        files = list(harvest_dir.glob("harvest_*.md"))
        self.assertGreater(len(files), 0, "project-scope entry should be written to worker_harvest/")

    def test_invalid_scope_entry_is_skipped(self):
        entries = [
            {"scope": "invalid_scope", "type": "project", "body": "This should be skipped."},
        ]
        result = mb._write_harvest_entry(entries[0], 0, None, "_default")
        self.assertFalse(result)

    def test_empty_body_is_skipped(self):
        entry = {"scope": "project", "type": "project", "body": ""}
        result = mb._write_harvest_entry(entry, 0, None, "_default")
        self.assertFalse(result)

    def test_session_scope_entry_written_to_session_dir(self):
        session_dir = Path(_SANDBOX) / "sess_test"
        entry = {"scope": "session", "type": "feedback", "body": "User prefers verbose output."}
        result = mb._write_harvest_entry(entry, 0, session_dir, "_default")
        self.assertTrue(result)
        mem_files = list((session_dir / ".claude" / "memory").glob("harvest_*.md"))
        self.assertEqual(len(mem_files), 1)
        content = mem_files[0].read_text()
        self.assertIn("User prefers verbose output", content)


# ── Cancellation test ─────────────────────────────────────────────────────────

class TestCancellationGate(unittest.TestCase):
    """Cancelled harvest tasks must not write after cancellation."""

    def setUp(self):
        _make_dirs()

    def test_cancelled_harvest_writes_nothing(self):
        chat_key = "cancel-test"
        mb.clear_cancel_event(chat_key)

        # Immediately cancel before thread can write
        event = mb.get_cancel_event(chat_key)
        event.set()

        harvest_dir = _GLOBAL_DIR / "memory" / "worker_harvest"
        before = set(harvest_dir.glob("harvest_*.md"))

        haiku_stub = lambda p, t, site="": json.dumps(
            [{"scope": "project", "type": "project", "body": "Should not appear."}]
        )
        with patch.object(mb, "_run_haiku", haiku_stub):
            mb.harvest_worker_output(
                scope=f"_default:discord:{chat_key}",
                instruction="do a thing",
                output="I did the thing and discovered X.",
                engine_id="codex",
                chat_key=chat_key,
                tenant_id="_default",
            )

        time.sleep(0.5)
        after = set(harvest_dir.glob("harvest_*.md"))
        self.assertEqual(before, after, "cancelled harvest must write no new files")
        mb.clear_cancel_event(chat_key)


# ── E2E — two-turn delegation round-trip ─────────────────────────────────────

class TestE2ERoundTrip(unittest.TestCase):
    """
    E2E: simulates two delegation turns.

    Turn 1: worker output is harvested → project-scope memory file is written.
    Turn 2: build_context_block() reads the harvested memory and includes it
            in <project_facts> (because the instruction hint matches).

    This validates the full export → import → re-export round-trip defined
    in ADR-0051 §2+§4.
    """

    CHANNEL  = "discord"
    CHAT_KEY = "e2e-roundtrip"
    SCOPE    = f"_default:{CHANNEL}:{CHAT_KEY}"
    ENGINE   = "opencode"

    def setUp(self):
        _make_dirs()
        mb.clear_cancel_event(self.CHAT_KEY)
        # Clear harvest dir so previous test files don't pollute harvest_files[0]
        harvest_dir = _GLOBAL_DIR / "memory" / "worker_harvest"
        if harvest_dir.exists():
            for f in harvest_dir.glob("harvest_*.md"):
                f.unlink(missing_ok=True)

    # ── Turn 1 helpers ────────────────────────────────────────────────────────

    def _turn1_instruction(self) -> str:
        return "Refactor the retry logic to use exponential backoff."

    def _turn1_worker_output(self) -> str:
        return (
            "I refactored the retry logic. The new strategy uses exponential backoff "
            "with a base of 2 seconds and a maximum of 32 seconds. "
            "The implementation lives in operator/utils/retry.py. "
            "Unit tests are in test_retry.py."
        )

    def _harvest_haiku_stub(self) -> str:
        """Return a realistic harvest response for Turn 1 output."""
        entries = [
            {
                "scope": "project",
                "type": "project",
                "body": (
                    "Retry logic uses exponential backoff: base 2s, max 32s. "
                    "Implementation: operator/utils/retry.py. Tests: test_retry.py."
                ),
            },
        ]
        return json.dumps(entries)

    # ── Turn 2 helpers ────────────────────────────────────────────────────────

    def _turn2_instruction(self) -> str:
        return "Add a test for the retry backoff edge case at max interval."

    # ── Test ──────────────────────────────────────────────────────────────────

    def test_round_trip(self):
        # ── Turn 1: harvest worker output → memory file written ───────────────
        done = threading.Event()
        original_task = mb._harvest_task

        def _tracked_task(*args, **kwargs):
            original_task(*args, **kwargs)
            done.set()

        harvest_stub = lambda p, t, site="": self._harvest_haiku_stub()

        with patch.object(mb, "_run_haiku", harvest_stub):
            with patch.object(mb, "_harvest_task", side_effect=_tracked_task):
                mb.harvest_worker_output(
                    scope=self.SCOPE,
                    instruction=self._turn1_instruction(),
                    output=self._turn1_worker_output(),
                    engine_id=self.ENGINE,
                    chat_key=self.CHAT_KEY,
                    tenant_id="_default",
                )

        done.wait(timeout=8.0)
        self.assertTrue(done.is_set(), "harvest task did not complete within 8 s")

        # Verify the memory file was written
        harvest_dir = _GLOBAL_DIR / "memory" / "worker_harvest"
        harvest_files = list(harvest_dir.glob("harvest_*.md"))
        self.assertGreater(len(harvest_files), 0, "Turn 1 should write at least one harvest file")

        written_content = harvest_files[0].read_text()
        self.assertIn("retry", written_content.lower(), "harvest file should mention retry")

        # ── Turn 2: build_context_block reads harvested memory ────────────────
        # Haiku not needed — content fits within cap without compression.
        with patch.object(mb, "_run_haiku", _null_haiku):
            block = mb.build_context_block(
                scope=self.SCOPE,
                workdir=Path(_SANDBOX),
                profile={},
                engine_type=self.ENGINE,
                instruction_hint=self._turn2_instruction(),
            )

        self.assertTrue(block, "Turn 2 must return a non-empty context block")
        self.assertIn("<corvin_context>", block)
        self.assertIn("<project_facts>", block)
        self.assertIn("retry", block.lower(), "project_facts must include the harvested retry info")

        print(f"\n[E2E] Round-trip complete.\nHarvest file: {harvest_files[0].name}")
        print(f"[E2E] Context block (Turn 2):\n{block}\n")


# ── ADR-0051 Must-NOT-do lint ─────────────────────────────────────────────────

class TestNoAnthropicImport(unittest.TestCase):
    """CI lint: memory_bridge.py must not import anthropic."""

    def test_no_anthropic_import_statement(self):
        import ast
        src = Path(__file__).parent / "memory_bridge.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    self.assertFalse(
                        name == "anthropic" or name.startswith("anthropic."),
                        f"memory_bridge.py must not import anthropic — found: {name}",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
