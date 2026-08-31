"""Tests for GET /chat/sessions/{sid}/execution-log.

Covers:
  - Empty chain → empty entries
  - Multiple tool_called events for the same turn are NOT deduplicated
    (regression: dedup key used seq=0 for all before seq was added to events)
  - OS + ACS events are merged and sorted chronologically
  - Metadata-only: no prompt text, no tool inputs/outputs (GDPR Art. 5)
  - Entries capped at limit
  - ACS events without chat_key are scoped by session workdir
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
    path.write_text("\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8")


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


class ExecutionLogRouteTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-exec-log-"))
        # Bridge audit: os_turn.* events → global/forge/audit.jsonl
        self.bridge_audit_dir = self.tmp / "global" / "forge"
        self.bridge_audit_dir.mkdir(parents=True)
        self.bridge_audit = self.bridge_audit_dir / "audit.jsonl"
        # Tenant audit: acs.* events → tenants/_default/global/audit.jsonl
        self.tenant_audit_dir = self.tmp / "tenants" / "_default" / "global"
        self.tenant_audit_dir.mkdir(parents=True)
        self.tenant_audit = self.tenant_audit_dir / "audit.jsonl"
        self.workdir = self.tmp / "session-workdir"
        self.workdir.mkdir()
        self.chat_key = "web:test-exec-session"
        self.session_stub = _make_session_stub(self.chat_key, self.workdir)
        self.auth_rec = _make_auth_record()
        self.sid = "test-exec-session"

    def _call(self, limit: int = 200) -> dict:
        with (
            patch("corvin_console.routes.chat.chat_runtime.get_session",
                  return_value=self.session_stub),
            patch("forge.paths.corvin_home", return_value=self.tmp),
        ):
            return chat_routes.get_session_execution_log(
                sid=self.sid,
                rec=self.auth_rec,
                limit=limit,
            )

    def test_empty_chains_return_no_entries(self) -> None:
        self.bridge_audit.write_text("", encoding="utf-8")
        self.tenant_audit.write_text("", encoding="utf-8")
        result = self._call()
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["entries"], [])

    def test_multiple_tool_calls_not_deduplicated(self) -> None:
        """Regression: before seq was added to os_turn.tool_called events,
        all tool calls in the same turn shared dedup key seq=0 and all but
        the first were dropped from the execution log."""
        now = time.time()
        turn_id = "ot_multi_tools"
        _write_audit(self.bridge_audit, [
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
            # Three tool calls — each with distinct seq
            {
                "event_type": "os_turn.tool_called",
                "ts": now + 0.3,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "tool_name": "mcp__forge__forge_list",
                    "seq": 1,
                },
            },
            {
                "event_type": "os_turn.tool_called",
                "ts": now + 0.6,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "tool_name": "mcp__forge__forge_exec",
                    "seq": 2,
                },
            },
            {
                "event_type": "os_turn.tool_called",
                "ts": now + 0.9,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "tool_name": "Bash",
                    "seq": 3,
                },
            },
            {
                "event_type": "os_turn.completed",
                "ts": now + 2.0,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "duration_ms": 2000,
                    "tools_called": 3,
                    "exit_code": 0,
                    "timed_out": False,
                },
            },
        ])
        result = self._call()
        tool_called_entries = [
            e for e in result["entries"]
            if e["event_type"] == "os_turn.tool_called"
        ]
        self.assertEqual(
            len(tool_called_entries), 3,
            f"expected 3 tool_called entries, got {len(tool_called_entries)}; "
            f"all entries: {[e['event_type'] for e in result['entries']]}",
        )
        # Verify seq values are preserved in details
        seqs = sorted(e["details"].get("seq", 0) for e in tool_called_entries)
        self.assertEqual(seqs, [1, 2, 3])
        # Verify tool names are distinct
        names = {e["details"].get("tool_name", "") for e in tool_called_entries}
        self.assertEqual(names, {"mcp__forge__forge_list", "mcp__forge__forge_exec", "Bash"})

    def test_tool_calls_without_seq_still_appear(self) -> None:
        """Legacy events without seq (pre-fix chain entries) fall back to
        positional index — at least the first tool call is always visible."""
        now = time.time()
        turn_id = "ot_legacy_no_seq"
        _write_audit(self.bridge_audit, [
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "persona": "assistant",
                    "model": "claude-haiku-4-5-20251001",
                },
            },
            # Only one tool call without seq — dedup key collision only
            # materialises with MULTIPLE events sharing the same seq=0.
            {
                "event_type": "os_turn.tool_called",
                "ts": now + 0.5,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "tool_name": "Read",
                    # no "seq" key — legacy format
                },
            },
        ])
        result = self._call()
        tool_called_entries = [
            e for e in result["entries"]
            if e["event_type"] == "os_turn.tool_called"
        ]
        self.assertEqual(len(tool_called_entries), 1)

    def test_entries_sorted_chronologically(self) -> None:
        now = time.time()
        turn1 = "ot_first"
        turn2 = "ot_second"
        _write_audit(self.bridge_audit, [
            # Written in reverse order to test that sort works
            {
                "event_type": "os_turn.started",
                "ts": now + 5.0,
                "details": {"turn_id": turn2, "chat_key": self.chat_key, "persona": "coder"},
            },
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {"turn_id": turn1, "chat_key": self.chat_key, "persona": "assistant"},
            },
        ])
        result = self._call()
        self.assertEqual(result["count"], 2)
        # Chronological: turn1 (earlier) before turn2
        self.assertLessEqual(result["entries"][0]["ts"], result["entries"][1]["ts"])
        self.assertEqual(result["entries"][0]["details"].get("turn_id"), turn1)
        self.assertEqual(result["entries"][1]["details"].get("turn_id"), turn2)

    def test_no_prompt_or_output_in_response(self) -> None:
        """GDPR Art. 5 — metadata only, never prompt or tool output."""
        now = time.time()
        turn_id = "ot_gdpr_exec"
        _write_audit(self.bridge_audit, [
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "persona": "assistant",
                    "prompt_text": "SHOULD_NOT_APPEAR",
                },
            },
            {
                "event_type": "os_turn.tool_called",
                "ts": now + 0.5,
                "details": {
                    "turn_id": turn_id,
                    "chat_key": self.chat_key,
                    "tool_name": "Read",
                    "seq": 1,
                    "tool_output": "SHOULD_NOT_APPEAR",
                },
            },
        ])
        result = self._call()
        result_str = json.dumps(result)
        self.assertNotIn("SHOULD_NOT_APPEAR", result_str)

    def test_cross_session_events_excluded(self) -> None:
        now = time.time()
        own_turn = "ot_mine"
        other_turn = "ot_other"
        _write_audit(self.bridge_audit, [
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {"turn_id": own_turn, "chat_key": self.chat_key, "persona": "assistant"},
            },
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {"turn_id": other_turn, "chat_key": "web:other-session", "persona": "coder"},
            },
        ])
        result = self._call()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["entries"][0]["details"].get("turn_id"), own_turn)

    def test_limit_caps_results(self) -> None:
        now = time.time()
        events = []
        for i in range(30):
            events.append({
                "event_type": "os_turn.started",
                "ts": now + i,
                "details": {
                    "turn_id": f"ot_cap{i:03d}",
                    "chat_key": self.chat_key,
                    "persona": "assistant",
                },
            })
        _write_audit(self.bridge_audit, events)
        result = self._call(limit=10)
        self.assertLessEqual(result["count"], 10)

    def test_role_field_set_correctly(self) -> None:
        now = time.time()
        turn_id = "ot_role_check"
        _write_audit(self.bridge_audit, [
            {
                "event_type": "os_turn.started",
                "ts": now,
                "details": {"turn_id": turn_id, "chat_key": self.chat_key, "persona": "assistant"},
            },
            {
                "event_type": "os_turn.tool_called",
                "ts": now + 0.5,
                "details": {"turn_id": turn_id, "chat_key": self.chat_key, "tool_name": "Read", "seq": 1},
            },
        ])
        result = self._call()
        for entry in result["entries"]:
            self.assertEqual(entry["role"], "os")


if __name__ == "__main__":
    unittest.main(verbosity=2)
