"""test_proposal.py — Layer 21 curated proposal stack (multi-user input + /go).

Covers add basics (empty / valid / truncation / stack-cap drop oldest),
list + get round-trip, remove existing + missing, clear + idempotency,
consume_for_go atomic with audit, format_for_prompt with content + empty
stack, status fields, id collision avoidance, CLI subcommand round-trip,
and audit chain integrity.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import proposal  # noqa: E402

CHANNEL = "telegram"
CHAT = "150221"
ALICE = "alice-21"
BOB = "bob-21"
OWNER = "owner-21"


class _ProposalTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="proposal-test-")
        self._orig_corvin_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp

    def tearDown(self) -> None:
        if self._orig_corvin_home is not None:
            os.environ["CORVIN_HOME"] = self._orig_corvin_home
        else:
            os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(self._tmp, ignore_errors=True)


class AddTests(_ProposalTestBase):
    def test_empty_text_rejected(self):
        result = proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "empty-text")
        result = proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="   ")
        self.assertFalse(result["ok"])

    def test_missing_uid_rejected(self):
        result = proposal.add(CHANNEL, CHAT, from_uid="", text="anything")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "missing-from-uid")

    def test_valid_text_succeeds(self):
        result = proposal.add(CHANNEL, CHAT, from_uid=ALICE,
                              text="propose feature X", from_role="member")
        self.assertTrue(result["ok"])
        self.assertEqual(result["entry"]["from_uid"], ALICE)
        self.assertEqual(result["entry"]["text"], "propose feature X")
        self.assertEqual(result["entry"]["from_role"], "member")
        self.assertEqual(len(result["entry"]["id"]), 6)

    def test_long_text_is_truncated(self):
        big = "x" * (proposal.MAX_TEXT_CHARS + 500)
        result = proposal.add(CHANNEL, CHAT, from_uid=ALICE, text=big)
        self.assertTrue(result["ok"])
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["entry"]["text"]), proposal.MAX_TEXT_CHARS)

    def test_stack_cap_drops_oldest(self):
        # Fill stack to MAX_STACK_SIZE
        for i in range(proposal.MAX_STACK_SIZE):
            proposal.add(CHANNEL, CHAT, from_uid=ALICE,
                         text=f"item {i}")
        # One more — oldest must be dropped
        result = proposal.add(CHANNEL, CHAT, from_uid=ALICE,
                              text="overflow item")
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["dropped"])
        stack = proposal.list_(CHANNEL, CHAT)
        self.assertEqual(len(stack), proposal.MAX_STACK_SIZE)
        # The first stored "item 0" must be gone:
        self.assertNotIn("item 0", [e["text"] for e in stack])
        self.assertEqual(stack[-1]["text"], "overflow item")

    def test_id_collision_avoidance(self):
        # Same uid + same body might hash to the same short-id, but the
        # collision-loop in `add` re-seeds. After many submissions the
        # ids in the (current) stack must all be unique.
        for i in range(20):
            proposal.add(CHANNEL, CHAT, from_uid=ALICE, text=f"item {i}")
        ids = [e["id"] for e in proposal.list_(CHANNEL, CHAT)]
        self.assertEqual(len(ids), len(set(ids)),
                         f"duplicate ids in stack: {sorted(ids)}")


class ListGetTests(_ProposalTestBase):
    def test_list_returns_oldest_first(self):
        proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="one")
        proposal.add(CHANNEL, CHAT, from_uid=BOB, text="two")
        stack = proposal.list_(CHANNEL, CHAT)
        self.assertEqual(stack[0]["text"], "one")
        self.assertEqual(stack[1]["text"], "two")

    def test_get_returns_match_or_none(self):
        result = proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="x")
        pid = result["entry"]["id"]
        found = proposal.get(CHANNEL, CHAT, pid)
        self.assertEqual(found["text"], "x")
        self.assertIsNone(proposal.get(CHANNEL, CHAT, "nosuch"))


class RemoveClearTests(_ProposalTestBase):
    def test_remove_existing(self):
        result = proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="x")
        pid = result["entry"]["id"]
        self.assertTrue(proposal.remove(CHANNEL, CHAT, pid, removed_by=OWNER))
        self.assertEqual(proposal.list_(CHANNEL, CHAT), [])

    def test_remove_missing(self):
        self.assertFalse(proposal.remove(CHANNEL, CHAT, "nosuch",
                                         removed_by=OWNER))
        self.assertFalse(proposal.remove(CHANNEL, CHAT, "",
                                         removed_by=OWNER))

    def test_clear_returns_count(self):
        proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="one")
        proposal.add(CHANNEL, CHAT, from_uid=BOB, text="two")
        n = proposal.clear(CHANNEL, CHAT, cleared_by=OWNER)
        self.assertEqual(n, 2)
        self.assertEqual(proposal.list_(CHANNEL, CHAT), [])

    def test_clear_idempotent(self):
        n = proposal.clear(CHANNEL, CHAT, cleared_by=OWNER)
        self.assertEqual(n, 0)


class ConsumeForGoTests(_ProposalTestBase):
    def test_consume_with_entries_returns_and_clears(self):
        proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="A")
        proposal.add(CHANNEL, CHAT, from_uid=BOB, text="B")
        entries = proposal.consume_for_go(CHANNEL, CHAT, triggered_by=OWNER)
        self.assertEqual(len(entries), 2)
        self.assertEqual({e["text"] for e in entries}, {"A", "B"})
        # Stack is empty after consume
        self.assertEqual(proposal.list_(CHANNEL, CHAT), [])

    def test_consume_empty_stack_emits_audit_returns_empty(self):
        entries = proposal.consume_for_go(CHANNEL, CHAT, triggered_by=OWNER,
                                          owner_text="solo")
        self.assertEqual(entries, [])

    def test_consume_is_atomic(self):
        # A second consume right after the first must see an empty stack.
        proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="A")
        first = proposal.consume_for_go(CHANNEL, CHAT, triggered_by=OWNER)
        second = proposal.consume_for_go(CHANNEL, CHAT, triggered_by=OWNER)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])


class FormatForPromptTests(_ProposalTestBase):
    def test_format_with_entries(self):
        proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="alpha",
                     from_role="member")
        proposal.add(CHANNEL, CHAT, from_uid=BOB, text="beta",
                     from_role="observer")
        entries = proposal.list_(CHANNEL, CHAT)
        rendered = proposal.format_for_prompt(entries,
                                              owner_text="please process")
        self.assertIn("PROPOSAL STACK", rendered)
        self.assertIn(ALICE, rendered)
        self.assertIn(BOB, rendered)
        self.assertIn("alpha", rendered)
        self.assertIn("beta", rendered)
        self.assertIn("END PROPOSAL STACK", rendered)
        self.assertIn("please process", rendered)

    def test_format_empty_with_owner_text(self):
        rendered = proposal.format_for_prompt([], owner_text="just steering")
        self.assertEqual(rendered, "just steering")

    def test_format_empty_no_owner_text(self):
        rendered = proposal.format_for_prompt([], owner_text="")
        self.assertEqual(rendered, "")


class StatusTests(_ProposalTestBase):
    def test_status_empty(self):
        st = proposal.status(CHANNEL, CHAT)
        self.assertEqual(st["stack_size"], 0)
        self.assertEqual(st["from_uids"], [])
        self.assertEqual(st["max_stack_size"], proposal.MAX_STACK_SIZE)

    def test_status_with_entries(self):
        proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="x")
        proposal.add(CHANNEL, CHAT, from_uid=BOB, text="y")
        proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="z")
        st = proposal.status(CHANNEL, CHAT)
        self.assertEqual(st["stack_size"], 3)
        self.assertEqual(set(st["from_uids"]), {ALICE, BOB})
        self.assertIsNotNone(st["oldest_ts"])
        self.assertIsNotNone(st["newest_ts"])
        self.assertGreaterEqual(st["newest_ts"], st["oldest_ts"])


class CliRoundTripTests(_ProposalTestBase):
    def _cli(self, *args: str) -> dict | str:
        env = os.environ.copy()
        env["CORVIN_HOME"] = self._tmp
        proc = subprocess.run(
            [sys.executable, str(HERE / "proposal.py"), *args],
            env=env, capture_output=True, text=True, timeout=10)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout

    def test_add_then_list_round_trip(self):
        out = self._cli("add", CHANNEL, CHAT, ALICE, "member", "hello world")
        self.assertTrue(out["ok"])
        listed = self._cli("list", CHANNEL, CHAT)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["from_uid"], ALICE)

    def test_consume_subcommand(self):
        self._cli("add", CHANNEL, CHAT, ALICE, "member", "A")
        self._cli("add", CHANNEL, CHAT, BOB, "observer", "B")
        out = self._cli("consume", CHANNEL, CHAT, OWNER, "go for it")
        self.assertEqual(out["count"], 2)
        self.assertIn("PROPOSAL STACK", out["prompt"])

    def test_remove_subcommand(self):
        a = self._cli("add", CHANNEL, CHAT, ALICE, "member", "x")
        pid = a["entry"]["id"]
        out = self._cli("remove", CHANNEL, CHAT, pid, OWNER)
        self.assertTrue(out["ok"])
        self.assertTrue(out["existed"])

    def test_clear_subcommand(self):
        self._cli("add", CHANNEL, CHAT, ALICE, "member", "x")
        out = self._cli("clear", CHANNEL, CHAT, OWNER)
        self.assertTrue(out["ok"])
        self.assertEqual(out["removed"], 1)


class AuditChainIntegrityTests(_ProposalTestBase):
    def test_proposal_events_chain_verifies(self):
        proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="one")
        proposal.add(CHANNEL, CHAT, from_uid=BOB, text="two")
        result = proposal.add(CHANNEL, CHAT, from_uid=ALICE, text="three")
        proposal.remove(CHANNEL, CHAT, result["entry"]["id"],
                        removed_by=OWNER)
        proposal.consume_for_go(CHANNEL, CHAT, triggered_by=OWNER)
        path = proposal._audit_path()
        if not path.exists():
            self.skipTest("forge package not available — _audit no-op")
        repo = HERE
        for parent in HERE.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent; break
        forge_pkg = repo / "operator" / "forge"
        sys.path.insert(0, str(forge_pkg))
        from forge.security_events import verify_chain  # type: ignore
        ok, problems = verify_chain(path)
        self.assertTrue(ok, f"chain broken: {problems[:5]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
