"""Tests for router_embedding.py — anchor cache + cosine routing.

Uses the fake embedding (deterministic hash) so no real API call.
Run: python3 operator/bridges/shared/test_router_embedding.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Sandbox + fake embeddings before import.
_SANDBOX = tempfile.mkdtemp(prefix="router_emb_test_")
os.environ["XDG_CACHE_HOME"] = _SANDBOX
os.environ["ROUTER_EMBED_FAKE"] = "1"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import router_embedding as re_mod  # noqa: E402


def _persona(name: str, anchors: list[str], *, exclude: bool = False) -> dict:
    return {"name": name, "routing_anchors": anchors, "routing_exclude": exclude}


class CosineTests(unittest.TestCase):
    def test_self_similarity_is_one(self):
        v = re_mod._embed("hello")
        self.assertAlmostEqual(re_mod._cosine(v, v), 1.0, places=4)

    def test_empty_returns_zero(self):
        self.assertEqual(re_mod._cosine([], [1.0]), 0.0)
        self.assertEqual(re_mod._cosine([1.0], []), 0.0)


class CacheTests(unittest.TestCase):
    def setUp(self):
        if re_mod._CACHE_FILE.exists():
            re_mod._CACHE_FILE.unlink()

    def test_first_call_creates_cache(self):
        personas = [_persona("a", ["one", "two"]), _persona("b", ["three"])]
        emb = re_mod._ensure_anchors(personas)
        self.assertIn("a", emb)
        self.assertIn("b", emb)
        self.assertEqual(len(emb["a"]), 2)
        self.assertTrue(re_mod._CACHE_FILE.exists())

    def test_unchanged_personas_reuse_cache(self):
        personas = [_persona("x", ["alpha", "beta"])]
        first = re_mod._ensure_anchors(personas)
        # Patch _embed to fail loudly if it gets called — second pass must be cached.
        original = re_mod._embed
        re_mod._embed = lambda _: (_ for _ in ()).throw(RuntimeError("should not embed"))
        try:
            second = re_mod._ensure_anchors(personas)
        finally:
            re_mod._embed = original
        self.assertEqual(first, second)

    def test_changed_anchors_invalidate(self):
        personas = [_persona("p", ["foo", "bar"])]
        first = re_mod._ensure_anchors(personas)
        # Change the anchors → the entry must be re-embedded.
        personas[0]["routing_anchors"] = ["foo", "baz"]
        second = re_mod._ensure_anchors(personas)
        self.assertNotEqual(first["p"], second["p"])

    def test_routing_exclude_keeps_persona_out(self):
        personas = [
            _persona("good", ["a"]),
            _persona("bad",  ["a"], exclude=True),
        ]
        emb = re_mod._ensure_anchors(personas)
        self.assertIn("good", emb)
        self.assertNotIn("bad", emb)


class RouteTests(unittest.TestCase):
    def setUp(self):
        if re_mod._CACHE_FILE.exists():
            re_mod._CACHE_FILE.unlink()

    def test_empty_text(self):
        self.assertIsNone(re_mod.route("", [_persona("a", ["x"])]))
        self.assertIsNone(re_mod.route("   ", [_persona("a", ["x"])]))

    def test_no_personas(self):
        self.assertIsNone(re_mod.route("hi", []))

    def test_no_anchors_anywhere(self):
        # personas without anchors → router has nothing to compare against.
        result = re_mod.route("hi", [{"name": "a"}])
        self.assertIsNone(result)

    def test_exact_anchor_match(self):
        # In FAKE mode: same input → same hash-derived vector → cosine = 1.
        personas = [
            _persona("alpha", ["the alpha thing"]),
            _persona("beta",  ["the beta thing"]),
        ]
        result = re_mod.route("the alpha thing", personas)
        self.assertIsNotNone(result)
        self.assertEqual(result["persona"], "alpha")
        self.assertGreater(result["confidence"], 0.99)

    def test_below_threshold_returns_none(self):
        # In FAKE mode, distinct inputs almost always score 0-ish, but to
        # be safe pin the threshold so high that nothing wins.
        personas = [_persona("alpha", ["something"])]
        result = re_mod.route("totally different", personas, threshold=0.99)
        self.assertIsNone(result)

    def test_returned_shape(self):
        personas = [
            _persona("a", ["x"]),
            _persona("b", ["y"]),
        ]
        # Use a phrase identical to one anchor so we know which wins.
        result = re_mod.route("x", personas, threshold=0.9)
        self.assertIsNotNone(result)
        self.assertEqual(set(result.keys()), {"persona", "confidence", "why"})
        self.assertEqual(result["persona"], "a")
        self.assertIn("top-sim", result["why"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
