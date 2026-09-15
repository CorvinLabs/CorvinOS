"""Tests for social_feed.py — Layer 39 CorvinFed."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))


def _reload_social():
    for mod in list(sys.modules.keys()):
        if mod.startswith("social_"):
            del sys.modules[mod]


def _make_post(post_id: str, actor_id: str, issued_at: float = None, **kwargs) -> dict:
    """Build a minimal valid post envelope dict for store_post."""
    now = issued_at if issued_at is not None else time.time()
    base = {
        "post_id": post_id,
        "actor_id": actor_id,
        "post_type": kwargs.get("post_type", "status"),
        "content": kwargs.get("content", f"content for {post_id}"),
        "visibility": kwargs.get("visibility", "public"),
        "in_reply_to": kwargs.get("in_reply_to"),
        "boost_of": kwargs.get("boost_of"),
        "issued_at": now,
        "is_ai": True,
        "tags": kwargs.get("tags", []),
        "attachments": kwargs.get("attachments", []),
        "signature": "deadbeef",
        "key_id": "test-key-id",
    }
    return base


class TestSocialFeedStoreBasicCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        import social_feed
        self.store = social_feed.SocialFeedStore()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_store_and_get_post(self):
        post = _make_post("p1", "actor-A", content="Hello CorvinFed")
        self.store.store_post(post)
        result = self.store.get_post("p1")
        self.assertIsNotNone(result)
        self.assertEqual(result["post_id"], "p1")
        self.assertEqual(result["actor_id"], "actor-A")

    def test_store_own_post(self):
        """is_own=True triggers sanitize_post_content — content is stored RAW (FIX-1/FIX-2)."""
        post = _make_post("p2", "actor-B", content="Own post content")
        self.store.store_post(post, is_own=True)
        result = self.store.get_post("p2")
        self.assertIsNotNone(result)
        # FIX-1/FIX-2: sanitize_post_content now returns raw content — no framing in DB.
        self.assertNotIn("<social_post_", result["content"])
        self.assertIn("Own post content", result["content"])

    def test_delete_post_returns_true(self):
        post = _make_post("p3", "actor-A")
        self.store.store_post(post)
        deleted = self.store.delete_post("p3")
        self.assertTrue(deleted)

    def test_delete_nonexistent_returns_false(self):
        deleted = self.store.delete_post("nonexistent-post-id")
        self.assertFalse(deleted)

    def test_get_nonexistent_returns_none(self):
        result = self.store.get_post("does-not-exist")
        self.assertIsNone(result)


class TestGetPostFramed(unittest.TestCase):
    """Tests for get_post_framed() — FIX-1/FIX-2."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        import social_feed
        self.store = social_feed.SocialFeedStore()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_post_framed_returns_framing(self):
        post = _make_post("framed-1", "actor-A", content="Hello world")
        self.store.store_post(post)
        result = self.store.get_post_framed("framed-1", fence_token="testtoken")
        self.assertIsNotNone(result)
        self.assertIn("<social_post_testtoken", result["content"])
        self.assertIn("Hello world", result["content"])

    def test_get_post_framed_returns_none_for_missing(self):
        result = self.store.get_post_framed("does-not-exist")
        self.assertIsNone(result)

    def test_get_post_raw_has_no_framing(self):
        """get_post() must return raw content without framing."""
        post = _make_post("raw-1", "actor-A", content="Raw content")
        self.store.store_post(post)
        result = self.store.get_post("raw-1")
        self.assertNotIn("<social_post_", result["content"])

    def test_get_post_framed_auto_token(self):
        """frame_for_llm generates a token if none provided."""
        post = _make_post("framed-auto", "actor-A", content="Auto token")
        self.store.store_post(post)
        result = self.store.get_post_framed("framed-auto")
        self.assertIsNotNone(result)
        self.assertIn("<social_post_", result["content"])


class TestListPosts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        import social_feed
        self.store = social_feed.SocialFeedStore()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_posts_empty(self):
        posts = self.store.list_posts()
        self.assertEqual(posts, [])

    def test_list_posts_returns_most_recent_first(self):
        base = time.time()
        self.store.store_post(_make_post("pa", "actor-A", issued_at=base + 10))
        self.store.store_post(_make_post("pb", "actor-A", issued_at=base + 20))
        self.store.store_post(_make_post("pc", "actor-A", issued_at=base + 30))
        posts = self.store.list_posts()
        ids = [p["post_id"] for p in posts]
        self.assertEqual(ids, ["pc", "pb", "pa"])

    def test_list_posts_limit(self):
        base = time.time()
        for i in range(5):
            self.store.store_post(_make_post(f"lp{i}", "actor-A", issued_at=base + i))
        posts = self.store.list_posts(limit=2)
        self.assertEqual(len(posts), 2)

    def test_list_posts_since(self):
        base = time.time()
        self.store.store_post(_make_post("s1", "actor-A", issued_at=base + 1))
        self.store.store_post(_make_post("s2", "actor-A", issued_at=base + 2))
        self.store.store_post(_make_post("s3", "actor-A", issued_at=base + 3))
        # since=base+1 → only s2 and s3 (issued_at > base+1)
        posts = self.store.list_posts(since=base + 1)
        ids = {p["post_id"] for p in posts}
        self.assertIn("s2", ids)
        self.assertIn("s3", ids)
        self.assertNotIn("s1", ids)

    def test_list_posts_by_actor_id(self):
        base = time.time()
        self.store.store_post(_make_post("a1", "actor-X", issued_at=base + 1))
        self.store.store_post(_make_post("a2", "actor-X", issued_at=base + 2))
        self.store.store_post(_make_post("a3", "actor-Y", issued_at=base + 3))
        posts = self.store.list_posts(actor_id="actor-X")
        self.assertEqual(len(posts), 2)
        for p in posts:
            self.assertEqual(p["actor_id"], "actor-X")


class TestTimeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        import social_feed
        self.store = social_feed.SocialFeedStore()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_timeline_includes_own_and_received(self):
        base = time.time()
        own = _make_post("own1", "local-actor", issued_at=base + 1)
        received = _make_post("recv1", "remote-actor", issued_at=base + 2)
        self.store.store_post(own)
        self.store.store_post(received)
        tl = self.store.timeline()
        ids = {p["post_id"] for p in tl}
        self.assertIn("own1", ids)
        self.assertIn("recv1", ids)


class TestTrendingByBoostCount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        import social_feed
        self.store = social_feed.SocialFeedStore()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_trending_by_boost_no_posts(self):
        result = self.store.trending_by_boost_count()
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    def test_trending_by_boost_counts_correctly(self):
        base = time.time()
        # Two original posts
        self.store.store_post(_make_post("orig1", "actor-A", issued_at=base))
        self.store.store_post(_make_post("orig2", "actor-A", issued_at=base))
        # P1 boosted 3x, P2 boosted 1x
        for i in range(3):
            self.store.store_post(
                _make_post(f"boost1_{i}", "actor-B", issued_at=base + i + 1,
                           post_type="boost", boost_of="orig1")
            )
        self.store.store_post(
            _make_post("boost2_0", "actor-C", issued_at=base + 1,
                       post_type="boost", boost_of="orig2")
        )
        trending = self.store.trending_by_boost_count(window_hours=24)
        self.assertGreater(len(trending), 0)
        self.assertEqual(trending[0]["post_id"], "orig1")

    def test_trending_no_ml(self):
        """trending_by_boost_count must not import any ML library; result must be a list."""
        result = self.store.trending_by_boost_count()
        self.assertIsInstance(result, list)
        # Inspect the source of social_feed to confirm no ML import
        import social_feed
        import inspect
        source = inspect.getsource(social_feed)
        for ml_lib in ("sklearn", "torch", "tensorflow", "keras", "numpy", "scipy"):
            self.assertNotIn(ml_lib, source,
                             msg=f"ML library {ml_lib!r} found in social_feed source")


class TestFTSSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        import social_feed
        self.store = social_feed.SocialFeedStore()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fts_search_finds_post(self):
        post = _make_post("fts1", "actor-A", content="CorvinFed rocks the federation world")
        self.store.store_post(post)
        results = self.store.search("CorvinFed")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["post_id"], "fts1")

    def test_fts_search_no_match(self):
        post = _make_post("fts2", "actor-A", content="Normal content without special words")
        self.store.store_post(post)
        results = self.store.search("ZZZNOMATCH999")
        self.assertEqual(results, [])


class TestBoostCount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        import social_feed
        self.store = social_feed.SocialFeedStore()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_boost_count_zero(self):
        post = _make_post("bc_orig", "actor-A")
        self.store.store_post(post)
        count = self.store.boost_count("bc_orig")
        self.assertEqual(count, 0)

    def test_boost_count_after_boost_posts(self):
        base = time.time()
        self.store.store_post(_make_post("bc_orig2", "actor-A", issued_at=base))
        self.store.store_post(
            _make_post("bc_b1", "actor-B", issued_at=base + 1,
                       post_type="boost", boost_of="bc_orig2")
        )
        self.store.store_post(
            _make_post("bc_b2", "actor-C", issued_at=base + 2,
                       post_type="boost", boost_of="bc_orig2")
        )
        count = self.store.boost_count("bc_orig2")
        self.assertEqual(count, 2)


class TestDeleteActorPosts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        import social_feed
        self.store = social_feed.SocialFeedStore()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_delete_actor_posts_clears_all(self):
        base = time.time()
        for i in range(3):
            self.store.store_post(_make_post(f"da{i}", "actor-del", issued_at=base + i))
        deleted = self.store.delete_actor_posts("actor-del")
        self.assertEqual(deleted, 3)
        remaining = self.store.list_posts(actor_id="actor-del")
        self.assertEqual(remaining, [])


class TestPublishPost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_publish_post_returns_envelope(self):
        import social_feed
        envelope = social_feed.publish_post("Hello CorvinFed!")
        self.assertIn("post_id", envelope)
        self.assertIn("actor_id", envelope)
        self.assertIn("signature", envelope)
        self.assertIn("is_ai", envelope)

    def test_publish_post_requires_consent(self):
        import social_feed
        import social_consent
        social_consent.leave()
        with self.assertRaises(social_feed.ConsentRequired):
            social_feed.publish_post("Should fail without consent")

    def test_publish_post_stores_in_db(self):
        import social_feed
        social_feed.publish_post("Stored post content")
        store = social_feed.SocialFeedStore()
        posts = store.list_posts()
        self.assertEqual(len(posts), 1)

    def test_publish_post_is_ai_always_true(self):
        import social_feed
        envelope = social_feed.publish_post("AI disclosure test")
        self.assertTrue(envelope["is_ai"])


class TestRetractPost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_retract_post_deletes_from_db(self):
        import social_feed
        envelope = social_feed.publish_post("Post to retract")
        post_id = envelope["post_id"]
        social_feed.retract_post(post_id)
        store = social_feed.SocialFeedStore()
        posts = store.list_posts()
        self.assertEqual(len(posts), 0)

    def test_retract_post_wrong_actor_fails(self):
        import social_feed
        # Inject a post from a *different* actor directly into the store
        store = social_feed.SocialFeedStore()
        foreign_post = _make_post("foreign-p", "foreign-actor-id", content="Not mine")
        store.store_post(foreign_post)
        # Now try to retract it via the module-level retract_post
        # (which loads the local actor_id and will see a mismatch)
        with self.assertRaises(social_feed.FeedError):
            social_feed.retract_post("foreign-p")


if __name__ == "__main__":
    unittest.main()
