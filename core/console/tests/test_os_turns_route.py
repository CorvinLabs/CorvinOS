"""Tests for GET /chat/sessions/{sid}/os-turns.

Verifies that the route correctly reads os_turn.* events from the L16 audit
chain, filters by chat_key, groups by turn_id, and returns metadata-only
output (EU AI Act Art. 12/13, GDPR Art. 5).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


def _write_audit(path: Path, events: list[dict]) -> None:
    lines = [json.dumps(ev) for ev in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_session_stub(chat_key: str, workdir: Path):
    s = MagicMock()
    s.chat_key = chat_key
    s.workdir = workdir
    return s


def _make_auth_record(tenant_id: str = "_default"):
    r = MagicMock()
    r.tenant_id = tenant_id
    return r


import corvin_console.routes.chat as chat_routes  # noqa: E402


class OsTurnsRouteTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-os-turns-"))
        # Route reads corvin_home() / "global" / "forge" / "audit.jsonl"
        self.audit_dir = self.tmp / "global" / "forge"
        self.audit_dir.mkdir(parents=True)
        self.audit_path = self.audit_dir / "audit.jsonl"
        self.workdir = self.tmp / "session-workdir"
        self.workdir.mkdir()
        self.chat_key = "web:test-session-1"
        self.session_stub = _make_session_stub(self.chat_key, self.workdir)
        self.auth_rec = _make_auth_record()
        self.sid = "test-session-1"

    def _call(self, limit: int = 20) -> dict:
        with (
            patch("corvin_console.routes.chat.chat_runtime.get_session",
                  return_value=self.session_stub),
            patch("forge.paths.corvin_home", return_value=self.tmp),
        ):
            return chat_routes.list_session_os_turns(
                sid=self.sid,
                rec=self.auth_rec,
                limit=limit,
            )

    def test_empty_audit_returns_zero_turns(self) -> None:
        self.audit_path.write_text("", encoding="utf-8")
        result = self._call()
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["turns"], [])
        self.assertEqual(result["chat_key"], self.chat_key)

    def test_missing_audit_file_returns_zero_turns(self) -> None:
        if self.audit_path.exists():
            self.audit_path.unlink()
        result = self._call()
        self.assertEqual(result["count"], 0)

    def test_single_complete_turn_reconstructed(self) -> None:
        now = time.time()
        turn_id = "ot_aabbcc112233"
        _write_audit(self.audit_path, [
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "persona": "assistant",
                    "model": "claude-sonnet-4-6",
                },
            },
            {
                "event_type": "os_turn.tool_called",
                "ts": now + 0.5,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "tool_name": "mcp__imagegen__generate_image",
                    "seq": 1,
                },
            },
            {
                "event_type": "os_turn.completed",
                "ts": now + 2.0,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "duration_ms": 2000,
                    "tools_called": 1,
                    "exit_code": 0,
                    "timed_out": False,
                    "model": "claude-sonnet-4-6",
                },
            },
        ])
        result = self._call()
        self.assertEqual(result["count"], 1)
        turn = result["turns"][0]
        self.assertEqual(turn["turn_id"], turn_id)
        self.assertEqual(turn["persona"], "assistant")
        self.assertTrue(turn["completed"])
        self.assertEqual(turn["tools_called"], 1)
        self.assertEqual(turn["duration_ms"], 2000)
        self.assertEqual(len(turn["tools"]), 1)
        self.assertEqual(turn["tools"][0]["name"], "mcp__imagegen__generate_image")

    def test_other_chat_key_events_excluded(self) -> None:
        now = time.time()
        own_turn = "ot_own111"
        other_turn = "ot_other222"
        _write_audit(self.audit_path, [
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {
                    "turn_id": own_turn,
                    "chat_key": self.chat_key,
                    "persona": "assistant",
                    "model": "claude-haiku-4-5-20251001",
                },
            },
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {
                    "turn_id": other_turn,
                    "chat_key": "web:other-session-xyz",
                    "persona": "coder",
                    "model": "claude-haiku-4-5-20251001",
                },
            },
            {
                "event_type": "os_turn.completed",
                "ts": now + 1.0,
                "details": {
                    "turn_id": own_turn,
                    "chat_key": self.chat_key,
                    "duration_ms": 1000,
                    "tools_called": 0,
                    "exit_code": 0,
                    "timed_out": False,
                },
            },
        ])
        result = self._call()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["turns"][0]["turn_id"], own_turn)

    def test_incomplete_turn_shows_completed_false(self) -> None:
        now = time.time()
        turn_id = "ot_incomplete99"
        _write_audit(self.audit_path, [
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "persona": "assistant",
                    "model": "claude-sonnet-4-6",
                },
            },
        ])
        result = self._call()
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["turns"][0]["completed"])

    def test_multiple_turns_returned_most_recent_first(self) -> None:
        base = time.time()
        events = []
        turn_ids = [f"ot_turn{i:02d}" for i in range(3)]
        for i, tid in enumerate(turn_ids):
            events.extend([
                {
                    "event_type": "os_turn.started",
                    "ts": base + i * 5.0,
                    "details": {
                        "turn_id": tid,
                        "chat_key": self.chat_key,
                        "persona": "assistant",
                        "model": "claude-haiku-4-5-20251001",
                    },
                },
                {
                    "event_type": "os_turn.completed",
                    "ts": base + i * 5.0 + 1.0,
                    "details": {
                        "turn_id": tid,
                        "chat_key": self.chat_key,
                        "duration_ms": 1000,
                        "tools_called": 0,
                        "exit_code": 0,
                        "timed_out": False,
                    },
                },
            ])
        _write_audit(self.audit_path, events)
        result = self._call()
        self.assertEqual(result["count"], 3)
        # most-recent first: last turn_id appears first
        self.assertEqual(result["turns"][0]["turn_id"], turn_ids[-1])
        self.assertEqual(result["turns"][-1]["turn_id"], turn_ids[0])

    def test_no_prompt_or_output_in_response(self) -> None:
        """GDPR Art. 5 — audit metadata only, never prompt or tool output."""
        now = time.time()
        turn_id = "ot_gdpr_check"
        _write_audit(self.audit_path, [
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "persona": "assistant",
                    "model": "claude-sonnet-4-6",
                    "prompt_text": "SHOULD_NOT_APPEAR",
                },
            },
            {
                "event_type": "os_turn.completed",
                "ts": now + 1.0,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "duration_ms": 500,
                    "tools_called": 0,
                    "exit_code": 0,
                    "timed_out": False,
                    "output_text": "SHOULD_NOT_APPEAR",
                },
            },
        ])
        result = self._call()
        result_str = json.dumps(result)
        self.assertNotIn("SHOULD_NOT_APPEAR", result_str)

    def test_limit_caps_results(self) -> None:
        now = time.time()
        events = []
        for i in range(25):
            tid = f"ot_limit{i:03d}"
            events.append({
                "event_type": "os_turn.started",
                "ts": now + i,
                "details": {
                    "turn_id": tid,
                    "chat_key": self.chat_key,
                    "persona": "assistant",
                    "model": "claude-haiku-4-5-20251001",
                },
            })
        _write_audit(self.audit_path, events)
        result = self._call(limit=20)
        self.assertLessEqual(result["count"], 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
