"""Tests for GET /chat/sessions/{sid}/chain-dual-track (ADR-0118).

Focus: COMPLETENESS + CORRECTNESS of the dual-track reconstruction.
  - every os_turn.* event for the session is returned (no trailing-byte
    truncation drops a session's events once the chain grows large);
  - os_turn.tool_called events keep their tool_name / seq projection so the
    swimlane is self-describing;
  - delegation grouping pairs OS-side delegation.* with worker A2A.* by id;
  - cross-session events are filtered out; metadata-only projection holds.
"""
from __future__ import annotations

import json
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

import corvin_console.routes.chain_dual_track as dt  # noqa: E402


def _auth(tenant_id: str = "_default"):
    r = MagicMock()
    r.tenant_id = tenant_id
    return r


class ChainDualTrackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-dual-track-"))
        # corvin_home()/global/forge/audit.jsonl is the OS-event source.
        self.forge_dir = self.tmp / "global" / "forge"
        self.forge_dir.mkdir(parents=True)
        self.audit = self.forge_dir / "audit.jsonl"
        self.chat_key = "web:sess-abc"
        self.sid = "sess-abc"

    def _write(self, events: list[dict]) -> None:
        self.audit.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    def _call(self) -> dict:
        # tenant_global_dir → a dir with no extra audit files (only the global
        # forge file matters for these OS-side tests).
        empty = self.tmp / "tenant-empty"
        empty.mkdir(exist_ok=True)
        with (
            patch.object(dt._forge_paths, "corvin_home", return_value=self.tmp),
            patch.object(dt._forge_paths, "tenant_global_dir", return_value=empty),
        ):
            return dt.get_chain_dual_track(sid=self.sid, rec=_auth())

    # ── completeness ────────────────────────────────────────────────────────

    def test_all_os_turn_events_returned(self) -> None:
        now = time.time()
        evs = [{"event_type": "os_turn.started", "ts": now,
                "details": {"turn_id": "t1", "chat_key": self.chat_key, "persona": "assistant"}}]
        for i in range(1, 16):
            evs.append({"event_type": "os_turn.tool_called", "ts": now + i * 0.1,
                        "details": {"turn_id": "t1", "chat_key": self.chat_key,
                                    "tool_name": f"Tool{i}", "seq": i}})
        evs.append({"event_type": "os_turn.completed", "ts": now + 5,
                    "details": {"turn_id": "t1", "chat_key": self.chat_key,
                                "tools_called": 15, "exit_code": 0, "duration_ms": 5000}})
        self._write(evs)
        res = self._call()
        # 1 started + 15 tool_called + 1 completed = 17, all present, none dropped.
        self.assertEqual(len(res["os_only_events"]), 17)
        kinds = [e["event_type"] for e in res["os_only_events"]]
        self.assertEqual(kinds.count("os_turn.tool_called"), 15)

    def test_tool_name_and_seq_preserved(self) -> None:
        now = time.time()
        self._write([
            {"event_type": "os_turn.tool_called", "ts": now,
             "details": {"turn_id": "t1", "chat_key": self.chat_key, "tool_name": "Bash", "seq": 3}},
        ])
        res = self._call()
        d = res["os_only_events"][0]["details"]
        self.assertEqual(d["tool_name"], "Bash")
        self.assertEqual(d["seq"], 3)

    def test_no_trailing_truncation_for_large_chain(self) -> None:
        # Pad the chain with >1 MB of unrelated-but-valid events BEFORE the
        # session's events, so a 512 KB trailing read would have dropped them.
        now = time.time()
        pad = [{"event_type": "console.noise", "ts": now,
                "details": {"chat_key": "web:other", "blob": "x" * 200}} for _ in range(6000)]
        session = [
            {"event_type": "os_turn.started", "ts": now + 100,
             "details": {"turn_id": "t1", "chat_key": self.chat_key, "persona": "assistant"}},
            {"event_type": "os_turn.completed", "ts": now + 101,
             "details": {"turn_id": "t1", "chat_key": self.chat_key, "exit_code": 0}},
        ]
        # session events come FIRST, then >1 MB of padding after → only a full
        # read recovers them.
        self._write(session + pad)
        self.assertGreater(self.audit.stat().st_size, 1_000_000)
        res = self._call()
        self.assertEqual(len(res["os_only_events"]), 2)

    # ── correctness ─────────────────────────────────────────────────────────

    def test_cross_session_events_filtered(self) -> None:
        now = time.time()
        self._write([
            {"event_type": "os_turn.started", "ts": now,
             "details": {"turn_id": "t1", "chat_key": self.chat_key, "persona": "assistant"}},
            {"event_type": "os_turn.started", "ts": now,
             "details": {"turn_id": "t2", "chat_key": "web:someone-else", "persona": "assistant"}},
        ])
        res = self._call()
        self.assertEqual(len(res["os_only_events"]), 1)
        self.assertEqual(res["os_only_events"][0]["details"]["turn_id"], "t1")

    def test_delegation_groups_pair_os_and_worker(self) -> None:
        now = time.time()
        self._write([
            {"event_type": "delegation.started", "ts": now,
             "details": {"delegation_id": "d1", "chat_key": self.chat_key, "target_engine": "hermes"}},
            {"event_type": "A2A.engine_spawned", "ts": now + 0.2,
             "details": {"task_id": "d1", "engine_id": "hermes", "chat_key": self.chat_key}},
            {"event_type": "A2A.result_filtered", "ts": now + 0.3,
             "details": {"task_id": "d1", "filter_pass_count": 2, "filter_reject_count": 0}},
            {"event_type": "delegation.ended", "ts": now + 0.5,
             "details": {"delegation_id": "d1", "chat_key": self.chat_key, "duration_ms": 300}},
        ])
        res = self._call()
        self.assertEqual(len(res["delegations"]), 1)
        grp = res["delegations"][0]
        self.assertEqual(grp["delegation_id"], "d1")
        self.assertEqual(grp["engine"], "hermes")
        self.assertEqual(len(grp["os_events"]), 2)      # started + ended
        self.assertEqual(len(grp["worker_events"]), 2)  # spawned + filtered

    def test_metadata_only_projection(self) -> None:
        now = time.time()
        self._write([
            {"event_type": "os_turn.tool_called", "ts": now,
             "details": {"turn_id": "t1", "chat_key": self.chat_key, "tool_name": "Read",
                         "seq": 1, "secret_payload": "DO NOT LEAK", "raw_prompt": "private"}},
        ])
        res = self._call()
        d = res["os_only_events"][0]["details"]
        self.assertNotIn("secret_payload", d)
        self.assertNotIn("raw_prompt", d)
        self.assertIn("tool_name", d)


class ChainVerifiedFieldTests(unittest.TestCase):
    """Adversarial review finding: the "chain DNA ✓/✗" badge was previously
    inferred purely by scanning for an event-type STRING already present in
    the raw JSONL — not a real hash-chain re-verification. Anyone able to
    write/edit a line into audit.jsonl could forge the badge. `chain_verified`
    now performs a real `security_events.verify_chain` walk."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-dual-track-verify-"))
        self.forge_dir = self.tmp / "global" / "forge"
        self.forge_dir.mkdir(parents=True)
        self.audit = self.forge_dir / "audit.jsonl"
        self.sid = "sess-abc"

    def _call(self) -> dict:
        empty = self.tmp / "tenant-empty"
        empty.mkdir(exist_ok=True)
        with (
            patch.object(dt._forge_paths, "corvin_home", return_value=self.tmp),
            patch.object(dt._forge_paths, "tenant_global_dir", return_value=empty),
        ):
            return dt.get_chain_dual_track(sid=self.sid, rec=_auth())

    def test_intact_real_hash_chain_reports_verified(self) -> None:
        from forge import security_events as _sec

        _sec.write_event(self.audit, "os_turn.started", details={"turn_id": "t1", "chat_key": "web:sess-abc"})
        _sec.write_event(self.audit, "os_turn.completed", details={"turn_id": "t1", "chat_key": "web:sess-abc"})
        res = self._call()
        self.assertTrue(res["chain_verified"])

    def test_tampered_hash_chain_reports_not_verified(self) -> None:
        from forge import security_events as _sec

        _sec.write_event(self.audit, "os_turn.started", details={"turn_id": "t1", "chat_key": "web:sess-abc"})
        _sec.write_event(self.audit, "os_turn.completed", details={"turn_id": "t1", "chat_key": "web:sess-abc"})
        lines = self.audit.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        rec["details"]["turn_id"] = "TAMPERED"
        lines[0] = json.dumps(rec)
        self.audit.write_text("\n".join(lines) + "\n", encoding="utf-8")

        res = self._call()
        self.assertFalse(res["chain_verified"], (
            "a tampered audit.jsonl must not report chain_verified=True — "
            "genesis_match readings pulled from it are not trustworthy"
        ))

    def test_no_hash_field_events_are_not_falsely_marked_broken(self) -> None:
        # Pre-chain-style events (no `hash` field) are legitimate per
        # verify_chain's own contract (hash_chain=False writes) and must not
        # be reported as a broken chain.
        self.audit.write_text(json.dumps({
            "event_type": "os_turn.started", "ts": time.time(),
            "details": {"turn_id": "t1", "chat_key": "web:sess-abc"},
        }) + "\n", encoding="utf-8")
        res = self._call()
        self.assertTrue(res["chain_verified"])


if __name__ == "__main__":
    unittest.main()
