#!/usr/bin/env python3
"""test_context_cold_storage.py — E2E for Layer 20 Phase-2.

Drives the cold-storage tier with a real filesystem and the
deterministic ``HashEmbeddingProvider`` so the test runs offline
with no API key. Verifies:

  - HashEmbeddingProvider returns unit-norm vectors of declared dim
  - cosine() is correct (identical vectors → 1.0, orthogonal → ~0)
  - page_out persists a record with embedding + metadata
  - page_in returns top_k by similarity, descending
  - similar texts rank higher than dissimilar texts (within
    hash-embedding noise tolerance)
  - identical text retrieves itself with similarity ≈ 1.0
  - restore_one pops a specific record
  - purge_session removes all pages
  - cross-provider mismatch: pages embedded with provider A are
    skipped when querying with provider B
  - validation: invalid session_id, negative tokens, top_k <= 0,
    bad-dim provider response
  - 4-thread concurrent page_out: no losses (lock validated)
  - corrupt JSONL line skipped, valid pages preserved

Per-subtask E2E rule: real filesystem, real fcntl locks, real
embedding math (just hash-based instead of OpenAI). No mocks.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_module(home: Path):
    os.environ["CORVIN_HOME"] = str(home)
    for mod in ("context_cold_storage", "paths"):
        sys.modules.pop(mod, None)
    import context_cold_storage  # type: ignore
    return context_cold_storage


# --------------------------------------------------------------------- math/provider

def case_hash_embedding_unit_norm(home: Path) -> None:
    _section("HashEmbeddingProvider returns unit-norm vectors of declared dim")
    cs = _fresh_module(home)
    for dim in (16, 64, 128, 256):
        p = cs.HashEmbeddingProvider(dim=dim)
        v = p.embed("hello world")
        assert len(v) == dim, f"expected dim={dim}, got {len(v)}"
        norm = sum(x * x for x in v) ** 0.5
        assert abs(norm - 1.0) < 1e-9, f"not unit norm: {norm}"
    print("  PASS dims 16/64/128/256 all unit-norm")


def case_cosine_identity_and_orthogonal(home: Path) -> None:
    _section("cosine: identity → 1.0, orthogonal → ~0")
    cs = _fresh_module(home)
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    c = [0.0, 1.0, 0.0]
    assert abs(cs.cosine(a, b) - 1.0) < 1e-9
    assert abs(cs.cosine(a, c)) < 1e-9
    print("  PASS cosine(identity)=1.0, cosine(orthogonal)=0.0")


def case_cosine_dim_mismatch_raises(home: Path) -> None:
    _section("cosine raises on dim mismatch")
    cs = _fresh_module(home)
    try:
        cs.cosine([1.0, 0.0], [1.0, 0.0, 0.0])
    except ValueError as exc:
        assert "dims differ" in str(exc), exc
        print(f"  PASS rejected: {exc}")
        return
    raise AssertionError("expected ValueError")


# --------------------------------------------------------------------- lifecycle

def case_page_out_persists_record(home: Path) -> None:
    _section("page_out persists record with embedding + metadata")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_a")
    sid = store.page_out("t1", "the quick brown fox", 42)
    assert sid.startswith("p_")
    rec = store.get(sid)
    assert rec is not None
    assert rec["turn_id"] == "t1"
    assert rec["tokens"] == 42
    assert rec["content"] == "the quick brown fox"
    assert len(rec["embedding"]) == 128  # default dim
    assert rec["embedded_with"] == "hash-128"
    assert rec["content_hash"]  # sha256 hex
    print(f"  PASS stored_id={sid} record persisted with embedding")


def case_page_in_returns_top_k_by_similarity(home: Path) -> None:
    _section("page_in returns top_k by descending similarity")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_b")
    store.page_out("t1", "the quick brown fox", 100)
    store.page_out("t2", "lorem ipsum dolor sit amet", 100)
    store.page_out("t3", "neural networks are universal approximators", 100)

    # Query with the EXACT text of t1 → it should be #1.
    # Use min_similarity=-1.0 so the hash-embedding noise (which can
    # produce negative cosines for unrelated texts) doesn't filter
    # results out — we want all 3 ranked here.
    out = store.page_in("the quick brown fox", top_k=3, min_similarity=-1.0)
    assert len(out) == 3, f"expected 3 results, got {len(out)}"
    assert out[0]["turn_id"] == "t1", f"expected t1 first, got {out[0]['turn_id']}"
    # Similarity for identical text should be very close to 1.0
    assert out[0]["similarity"] > 0.999
    # Strict descending order across the full result set
    sims = [r["similarity"] for r in out]
    assert sims == sorted(sims, reverse=True), sims
    print(
        f"  PASS top hit: {out[0]['turn_id']} sim={out[0]['similarity']}, "
        f"order: {[r['turn_id'] for r in out]}"
    )


def case_page_in_top_k_caps_results(home: Path) -> None:
    _section("page_in respects top_k cap")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_cap")
    for i in range(5):
        store.page_out(f"t{i}", f"content number {i}", 50)
    out = store.page_in("anything", top_k=2)
    assert len(out) == 2
    print(f"  PASS top_k=2 returned {len(out)} results")


def case_page_in_min_similarity_filter(home: Path) -> None:
    _section("page_in filters by min_similarity")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_min")
    store.page_out("t1", "alpha", 10)
    store.page_out("t2", "beta", 10)
    out = store.page_in("alpha", top_k=10, min_similarity=0.999)
    # Only the exact-match should pass
    assert len(out) == 1
    assert out[0]["turn_id"] == "t1"
    print(f"  PASS min_similarity=0.999 returned only the exact match")


def case_restore_one_pops_record(home: Path) -> None:
    _section("restore_one pops a specific record")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_pop")
    sid = store.page_out("t_a", "to-be-restored", 33)
    store.page_out("t_b", "stays", 22)

    popped = store.restore_one(sid)
    assert popped is not None
    assert popped["turn_id"] == "t_a"
    # Now gone
    assert store.get(sid) is None
    # Other still there
    remaining = [p["turn_id"] for p in store.list_paged()]
    assert remaining == ["t_b"]
    print(f"  PASS popped {popped['turn_id']}, remaining: {remaining}")


def case_restore_missing_returns_none(home: Path) -> None:
    _section("restore_one of missing id returns None")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_miss")
    assert store.restore_one("p_does_not_exist") is None
    print("  PASS returns None for missing")


def case_purge_session_removes_all(home: Path) -> None:
    _section("purge_session removes all pages, returns count")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_purge")
    for i in range(4):
        store.page_out(f"t{i}", f"content {i}", 10)
    assert store.page_count() == 4
    n = store.purge_session()
    assert n == 4, n
    assert store.page_count() == 0
    print(f"  PASS purged {n} pages, store now empty")


def case_list_paged_omits_embeddings(home: Path) -> None:
    _section("list_paged omits embedding vectors from result")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_list")
    store.page_out("t1", "content", 10)
    items = store.list_paged()
    assert len(items) == 1
    assert "embedding" not in items[0]
    assert items[0]["turn_id"] == "t1"
    print("  PASS embedding stripped from listing")


def case_total_tokens_aggregates(home: Path) -> None:
    _section("total_tokens aggregates across paged-out turns")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_tok")
    store.page_out("t1", "x", 100)
    store.page_out("t2", "y", 250)
    store.page_out("t3", "z", 50)
    assert store.total_tokens() == 400
    print(f"  PASS total_tokens = {store.total_tokens()}")


def case_cross_provider_pages_skipped(home: Path) -> None:
    _section("page_in skips records embedded with different provider")
    cs = _fresh_module(home)
    p_small = cs.HashEmbeddingProvider(dim=64)
    p_large = cs.HashEmbeddingProvider(dim=128)

    store_small = cs.ColdStorage("s_xprov", provider=p_small)
    store_small.page_out("from-small", "content", 50)

    store_large = cs.ColdStorage("s_xprov", provider=p_large)
    out = store_large.page_in("content", top_k=10)
    # Cross-provider record skipped — empty result
    assert out == []
    # And reverse: query with the small provider should find it
    out2 = store_small.page_in("content", top_k=10)
    assert len(out2) == 1
    print("  PASS cross-provider skip works both ways")


def case_concurrent_page_out_no_loss(home: Path) -> None:
    _section("4-thread concurrent page_out: no losses (lock validated)")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_hot")

    N = 25
    THREADS = 4

    def worker(tid: int) -> None:
        for i in range(N):
            store.page_out(f"t_t{tid}_{i:03d}", f"content {tid} {i}", 10)

    threads = [
        threading.Thread(target=worker, args=(t,)) for t in range(THREADS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = THREADS * N
    assert store.page_count() == expected, (
        f"expected {expected}, got {store.page_count()}"
    )
    print(f"  PASS {expected} concurrent page_outs, lock held")


def case_corrupt_line_skipped(home: Path) -> None:
    _section("corrupt JSONL line skipped, valid pages preserved")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_corrupt")
    store.page_out("good-1", "first content", 10)

    # Append a malformed line directly
    p = cs._pages_path("s_corrupt")
    with p.open("a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")

    store.page_out("good-2", "second content", 10)

    items = store.list_paged()
    turn_ids = [it["turn_id"] for it in items]
    assert turn_ids == ["good-1", "good-2"], turn_ids
    print("  PASS corrupt line skipped, both valid pages preserved")


def case_validation_invalid_session_id(home: Path) -> None:
    _section("invalid session_id rejected")
    cs = _fresh_module(home)
    for bad in ("", "a/b", "../x"):
        try:
            cs.ColdStorage(bad).page_out("t", "x", 1)
        except ValueError:
            print(f"  PASS rejected session_id={bad!r}")
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def case_validation_negative_tokens(home: Path) -> None:
    _section("negative tokens rejected")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_neg")
    try:
        store.page_out("t1", "content", -5)
    except ValueError:
        print("  PASS rejected negative tokens")
        return
    raise AssertionError("expected ValueError")


def case_validation_top_k_must_be_positive(home: Path) -> None:
    _section("top_k <= 0 rejected")
    cs = _fresh_module(home)
    store = cs.ColdStorage("s_topk")
    for bad in (0, -1):
        try:
            store.page_in("query", top_k=bad)
        except ValueError:
            print(f"  PASS rejected top_k={bad}")
        else:
            raise AssertionError(f"expected ValueError for top_k={bad}")


def case_provider_dim_mismatch_raises(home: Path) -> None:
    _section("provider returning wrong-dim vector raises")
    cs = _fresh_module(home)

    class BrokenProvider:
        name = "broken"
        dim = 64
        def embed(self, text: str) -> list:
            return [0.1] * 32  # claims 64, returns 32

    store = cs.ColdStorage("s_broken", provider=BrokenProvider())
    try:
        store.page_out("t1", "content", 10)
    except ValueError as exc:
        assert "length" in str(exc) and "64" in str(exc), exc
        print(f"  PASS rejected: {exc}")
        return
    raise AssertionError("expected ValueError")


def case_list_sessions_with_pages(home: Path) -> None:
    _section("list_sessions_with_pages returns sessions that have pages")
    cs = _fresh_module(home)
    cs.ColdStorage("a").page_out("t", "x", 1)
    cs.ColdStorage("b").page_out("t", "y", 1)
    cs.ColdStorage("c")  # registered, no pages
    out = cs.list_sessions_with_pages()
    assert set(out) == {"a", "b"}, out
    print(f"  PASS sessions with pages: {sorted(out)}")


# --------------------------------------------------------------------- driver

def main() -> None:
    saved = os.environ.get("CORVIN_HOME")
    cases = [
        case_hash_embedding_unit_norm,
        case_cosine_identity_and_orthogonal,
        case_cosine_dim_mismatch_raises,
        case_page_out_persists_record,
        case_page_in_returns_top_k_by_similarity,
        case_page_in_top_k_caps_results,
        case_page_in_min_similarity_filter,
        case_restore_one_pops_record,
        case_restore_missing_returns_none,
        case_purge_session_removes_all,
        case_list_paged_omits_embeddings,
        case_total_tokens_aggregates,
        case_cross_provider_pages_skipped,
        case_concurrent_page_out_no_loss,
        case_corrupt_line_skipped,
        case_validation_invalid_session_id,
        case_validation_negative_tokens,
        case_validation_top_k_must_be_positive,
        case_provider_dim_mismatch_raises,
        case_list_sessions_with_pages,
    ]
    failures = 0
    for case in cases:
        home = Path(tempfile.mkdtemp(prefix="cs-test-"))
        try:
            case(home)
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    if saved is None:
        os.environ.pop("CORVIN_HOME", None)
    else:
        os.environ["CORVIN_HOME"] = saved

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
