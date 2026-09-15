"""Layer 39 CorvinFed — unit tests for social_registry.py.

Run: python3 test_social_registry.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))


class TestSocialRegistry(unittest.TestCase):
    """Tests for SocialRegistry CRUD, follow protocol, block/unblock, rate limits."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        # Purge any cached social_ module state
        for mod in list(sys.modules.keys()):
            if mod.startswith("social_"):
                del sys.modules[mod]
        # Ensure the social dir exists by joining first
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        from social_registry import SocialRegistry
        self.reg = SocialRegistry()

    def tearDown(self) -> None:
        # Close the connection before removing the directory
        try:
            self.reg.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)
        for mod in list(sys.modules.keys()):
            if mod.startswith("social_"):
                del sys.modules[mod]

    # ── upsert and get_actor ──────────────────────────────────────────────────

    def test_upsert_and_get(self) -> None:
        """upsert an actor, get_actor returns it with correct fields."""
        self.reg.upsert_actor(
            actor_id="actor-001",
            inbox_url="https://example.com/inbox",
            public_key_hex="deadbeef" * 8,
            relationship="follower",
            display_name="Alice",
            compliance_zone="eu",
            is_ai=True,
        )
        actor = self.reg.get_actor("actor-001")
        self.assertIsNotNone(actor)
        self.assertEqual(actor["actor_id"], "actor-001")
        self.assertEqual(actor["inbox_url"], "https://example.com/inbox")
        self.assertEqual(actor["relationship"], "follower")
        self.assertEqual(actor["display_name"], "Alice")
        self.assertEqual(actor["compliance_zone"], "eu")
        self.assertEqual(bool(actor["is_ai"]), True)

    def test_upsert_updates_existing(self) -> None:
        """upsert twice with different display_name -> get_actor returns updated name."""
        self.reg.upsert_actor(
            actor_id="actor-002",
            inbox_url="https://example.com/inbox",
            public_key_hex="deadbeef" * 8,
            relationship="follower",
            display_name="Bob",
        )
        self.reg.upsert_actor(
            actor_id="actor-002",
            inbox_url="https://example.com/inbox",
            public_key_hex="deadbeef" * 8,
            relationship="follower",
            display_name="Robert",
        )
        actor = self.reg.get_actor("actor-002")
        self.assertIsNotNone(actor)
        self.assertEqual(actor["display_name"], "Robert")

    def test_get_nonexistent_returns_none(self) -> None:
        """Unknown actor_id -> None."""
        result = self.reg.get_actor("no-such-actor")
        self.assertIsNone(result)

    # ── relationships ─────────────────────────────────────────────────────────

    def test_list_actors_by_relationship(self) -> None:
        """upsert follower + following -> list_actors('follower') returns 1."""
        self.reg.upsert_actor(
            actor_id="follower-01",
            inbox_url="https://a.example/inbox",
            public_key_hex="aa" * 32,
            relationship="follower",
        )
        self.reg.upsert_actor(
            actor_id="following-01",
            inbox_url="https://b.example/inbox",
            public_key_hex="bb" * 32,
            relationship="following",
        )
        followers = self.reg.list_actors("follower")
        self.assertEqual(len(followers), 1)
        self.assertEqual(followers[0]["actor_id"], "follower-01")

    def test_update_relationship(self) -> None:
        """upsert as follower, update to mutual -> get_actor shows mutual."""
        self.reg.upsert_actor(
            actor_id="actor-003",
            inbox_url="https://c.example/inbox",
            public_key_hex="cc" * 32,
            relationship="follower",
        )
        updated = self.reg.update_relationship("actor-003", "mutual")
        self.assertTrue(updated)
        actor = self.reg.get_actor("actor-003")
        self.assertEqual(actor["relationship"], "mutual")

    def test_delete_actor(self) -> None:
        """upsert, delete -> get_actor returns None."""
        self.reg.upsert_actor(
            actor_id="actor-004",
            inbox_url="https://d.example/inbox",
            public_key_hex="dd" * 32,
            relationship="following",
        )
        deleted = self.reg.delete_actor("actor-004")
        self.assertTrue(deleted)
        self.assertIsNone(self.reg.get_actor("actor-004"))

    # ── follow protocol ───────────────────────────────────────────────────────

    def test_accept_follow_adds_to_registry(self) -> None:
        """accept_follow(actor_id, inbox_url, pub_key) -> actor with relationship=follower."""
        result = self.reg.accept_follow(
            actor_id="actor-005",
            inbox_url="https://e.example/inbox",
            public_key_hex="ee" * 32,
            compliance_zone="eu",
        )
        self.assertTrue(result)
        actor = self.reg.get_actor("actor-005")
        self.assertIsNotNone(actor)
        self.assertEqual(actor["relationship"], "follower")

    def test_accept_follow_blocked_actor_returns_false(self) -> None:
        """Block actor first, then accept_follow -> returns False (fail-silent)."""
        self.reg.block("actor-blocked")
        result = self.reg.accept_follow(
            actor_id="actor-blocked",
            inbox_url="https://blocked.example/inbox",
            public_key_hex="ff" * 32,
        )
        self.assertFalse(result)
        # Relationship must remain blocked
        actor = self.reg.get_actor("actor-blocked")
        self.assertEqual(actor["relationship"], "blocked")

    def test_accept_follow_compliance_zone_eu_rejects_us(self) -> None:
        """Set CORVIN_DATA_RESIDENCY=eu, actor compliance_zone='us' -> returns False."""
        os.environ["CORVIN_DATA_RESIDENCY"] = "eu"
        os.environ.pop("CORVIN_SOCIAL_ALLOW_NON_EU", None)
        result = self.reg.accept_follow(
            actor_id="actor-us",
            inbox_url="https://us.example/inbox",
            public_key_hex="11" * 32,
            compliance_zone="us",
        )
        self.assertFalse(result)

    def test_accept_follow_compliance_zone_eu_accepts_eu(self) -> None:
        """Actor compliance_zone='eu' -> accept_follow returns True."""
        os.environ["CORVIN_DATA_RESIDENCY"] = "eu"
        os.environ.pop("CORVIN_SOCIAL_ALLOW_NON_EU", None)
        result = self.reg.accept_follow(
            actor_id="actor-eu",
            inbox_url="https://eu.example/inbox",
            public_key_hex="22" * 32,
            compliance_zone="eu",
        )
        self.assertTrue(result)
        actor = self.reg.get_actor("actor-eu")
        self.assertIsNotNone(actor)

    def test_add_following(self) -> None:
        """add_following sets relationship=following."""
        self.reg.add_following(
            actor_id="actor-follow-out",
            inbox_url="https://f.example/inbox",
            public_key_hex="33" * 32,
        )
        actor = self.reg.get_actor("actor-follow-out")
        self.assertIsNotNone(actor)
        self.assertEqual(actor["relationship"], "following")

    # ── block/unblock ─────────────────────────────────────────────────────────

    def test_block_sets_relationship(self) -> None:
        """block(actor_id) -> get_actor.relationship == 'blocked'."""
        self.reg.upsert_actor(
            actor_id="actor-007",
            inbox_url="https://g.example/inbox",
            public_key_hex="44" * 32,
            relationship="follower",
        )
        self.reg.block("actor-007")
        actor = self.reg.get_actor("actor-007")
        self.assertEqual(actor["relationship"], "blocked")

    def test_unblock_removes_actor(self) -> None:
        """block then unblock -> relationship changed (former_follower) or actor gone."""
        self.reg.block("actor-008")
        result = self.reg.unblock("actor-008")
        self.assertTrue(result)
        actor = self.reg.get_actor("actor-008")
        # After unblock, actor may be None (if deleted) or have relationship != blocked
        if actor is not None:
            self.assertNotEqual(actor["relationship"], "blocked")

    def test_is_blocked_true(self) -> None:
        """After block -> is_blocked returns True."""
        self.reg.block("actor-009")
        self.assertTrue(self.reg.is_blocked("actor-009"))

    def test_is_blocked_false(self) -> None:
        """Non-blocked actor -> is_blocked returns False."""
        self.reg.upsert_actor(
            actor_id="actor-010",
            inbox_url="https://h.example/inbox",
            public_key_hex="55" * 32,
            relationship="follower",
        )
        self.assertFalse(self.reg.is_blocked("actor-010"))

    # ── rate limits ───────────────────────────────────────────────────────────

    def test_rate_limit_allows_within_limit(self) -> None:
        """First post -> check_and_record_post returns True."""
        result = self.reg.check_and_record_post("actor-rl-01", per_actor_limit=100)
        self.assertTrue(result)

    def test_rate_limit_blocks_over_limit(self) -> None:
        """Call 101 times with per_actor_limit=100 -> 101st returns False."""
        actor_id = "actor-rl-02"
        limit = 100
        # Fill up to limit
        for _ in range(limit):
            self.reg.check_and_record_post(actor_id, per_actor_limit=limit, global_limit=10000)
        # 101st must fail
        result = self.reg.check_and_record_post(actor_id, per_actor_limit=limit, global_limit=10000)
        self.assertFalse(result)

    # ── followers/following counts ────────────────────────────────────────────

    def test_follower_count(self) -> None:
        """accept 2 follows -> follower_count() == 2."""
        self.reg.accept_follow(
            actor_id="f1",
            inbox_url="https://f1.example/inbox",
            public_key_hex="a1" * 32,
            compliance_zone="eu",
        )
        self.reg.accept_follow(
            actor_id="f2",
            inbox_url="https://f2.example/inbox",
            public_key_hex="a2" * 32,
            compliance_zone="eu",
        )
        self.assertEqual(self.reg.follower_count(), 2)

    def test_following_count(self) -> None:
        """add 1 following -> following_count() == 1."""
        self.reg.add_following(
            actor_id="g1",
            inbox_url="https://g1.example/inbox",
            public_key_hex="b1" * 32,
        )
        self.assertEqual(self.reg.following_count(), 1)

    # ── purge ─────────────────────────────────────────────────────────────────

    def test_purge_all(self) -> None:
        """add 3 actors, purge_all -> 0 actors."""
        for i in range(3):
            self.reg.upsert_actor(
                actor_id=f"purge-{i}",
                inbox_url="https://example.com/inbox",
                public_key_hex="cc" * 32,
                relationship="follower",
            )
        self.reg.purge_all()
        actors = self.reg.list_actors()
        self.assertEqual(len(actors), 0)


if __name__ == "__main__":
    unittest.main()
