"""Phase 2 E2E tests: on-demand resolution, caching, hash validation, latency, DAG cycles.

Every test drives the real modules against real files on disk and inspects the
real audit sink - nothing under test is mocked.
"""

import os
import statistics
import tempfile
import threading
import time

import pytest

from core.context.reference_graph import (
    CRGBuilder,
    ContextBuildError,
    CycleError,
    DanglingDependencyError,
    ReferenceGraph,
    ReferenceHashMismatchError,
    ReferenceLoader,
    ReferenceNotInDigestError,
    ReferenceUnavailableError,
)
from core.context.reference_graph.audit import clear_audit_events, get_audit_events


@pytest.fixture
def ctx_dir():
    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for name, body in {
            "user-profile.md": b"# User Profile\nname: Alice\nrole: admin\n",
            "session-history.md": b"# Session History\nTask 1: Completed\nTask 2: Pending\n",
            "preferences.json": b'{"theme": "dark", "language": "de"}',
        }.items():
            path = os.path.join(tmp, name)
            with open(path, "wb") as fh:
                fh.write(body)
            files[name] = path
        yield files


def _events(event_type: str):
    return [e for e in get_audit_events() if e["event_type"] == event_type]


class TestReferenceLoaderE2E:
    def test_resolve_on_demand_validates_hash_and_audits(self, ctx_dir):
        clear_audit_events()
        b = CRGBuilder(tenant_id="tenant-a")
        b.add_reference(ctx_dir["user-profile.md"], summary="profile")
        b.add_reference(ctx_dir["session-history.md"], summary="history")
        digest = b.build()

        loader = ReferenceLoader(digest)
        content = loader.resolve(ctx_dir["user-profile.md"])
        assert content == b"# User Profile\nname: Alice\nrole: admin\n"

        # Lookup by hash id also works and yields identical bytes
        ref = digest.references[0]
        assert loader.resolve("sha256:" + ref.hash_sha256) == content

        ev = _events("context_reference_resolved")
        assert ev, "context_reference_resolved must be emitted"
        assert ev[0]["status"] == "ok"
        assert ev[0]["hash_expected"] == ev[0]["hash_actual"] == ref.hash_sha256
        assert ev[0]["tenant_id"] == "tenant-a"
        assert ev[0]["lom"]

        # A path outside the digest is refused (fail-closed) and audited
        with pytest.raises(ReferenceNotInDigestError):
            loader.resolve(ctx_dir["preferences.json"])
        assert any(e.get("error") == "not_in_digest" for e in _events("context_reference_resolved"))

    def test_cache_hit_is_served_from_memory(self, ctx_dir):
        clear_audit_events()
        b = CRGBuilder()
        b.add_reference(ctx_dir["user-profile.md"])
        digest = b.build()
        loader = ReferenceLoader(digest)
        path = ctx_dir["user-profile.md"]

        first = loader.resolve_detailed(path)
        assert first.cache_hit is False
        # Delete the file: a cache hit must not touch the disk at all
        os.remove(path)
        second = loader.resolve_detailed(path)
        assert second.cache_hit is True
        assert second.content == first.content

        stats = loader.stats()
        assert stats.hits == 1 and stats.misses == 1 and stats.errors == 0
        statuses = [e["status"] for e in _events("context_reference_resolved")]
        assert statuses == ["ok", "cache_hit"]

        # After clearing the cache the missing file is an error, not a fallback
        loader.clear_cache()
        with pytest.raises(ReferenceUnavailableError):
            loader.resolve(path)

    def test_hash_mismatch_fails_closed_and_is_not_cached(self, ctx_dir):
        clear_audit_events()
        b = CRGBuilder()
        b.add_reference(ctx_dir["user-profile.md"])
        digest = b.build()
        loader = ReferenceLoader(digest)
        path = ctx_dir["user-profile.md"]

        # Same-length tamper -> only the hash can catch it
        with open(path, "wb") as fh:
            fh.write(b"# User Profile\nname: Bobby\nrole: admin\n")

        with pytest.raises(ReferenceHashMismatchError) as exc:
            loader.resolve(path)
        assert exc.value.reason == "hash_mismatch"
        assert not loader.is_cached(path)
        assert loader.stats().errors == 1

        mm = _events("context_reference_hash_mismatch")
        assert len(mm) == 1
        assert mm[0]["action"] == "reference_not_loaded"
        assert mm[0]["hash_expected"] != mm[0]["hash_actual"]
        assert _events("context_reference_resolved")[-1]["status"] == "error"

        # Size change is also caught (size_mismatch reason)
        with open(path, "ab") as fh:
            fh.write(b"extra")
        with pytest.raises(ReferenceHashMismatchError) as exc2:
            loader.resolve(path)
        assert exc2.value.reason == "size_mismatch"

    def test_latency_p95_under_50ms(self, ctx_dir):
        clear_audit_events()
        b = CRGBuilder()
        for p in ctx_dir.values():
            b.add_reference(p)
        digest = b.build()
        loader = ReferenceLoader(digest)
        paths = list(ctx_dir.values())

        samples = []
        for i in range(300):
            t0 = time.perf_counter()
            loader.resolve(paths[i % len(paths)])
            samples.append((time.perf_counter() - t0) * 1000)
        p95 = statistics.quantiles(samples, n=20)[18]
        assert p95 < 50.0, f"p95 latency {p95:.2f}ms exceeds 50ms"
        assert loader.stats().hits == 297 and loader.stats().misses == 3

    def test_concurrent_resolution_is_atomic(self, ctx_dir):
        clear_audit_events()
        b = CRGBuilder()
        b.add_reference(ctx_dir["session-history.md"])
        digest = b.build()
        loader = ReferenceLoader(digest)
        path = ctx_dir["session-history.md"]
        expected = open(path, "rb").read()

        results, errors = [], []

        def worker():
            try:
                for _ in range(50):
                    results.append(loader.resolve(path))
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(results) == 400 and all(r == expected for r in results)
        st = loader.stats()
        assert st.hits + st.misses == 400
        assert st.cached_bytes == len(expected)  # inserted exactly once

    def test_cycle_detection_blocks_build_and_resolution(self, ctx_dir):
        clear_audit_events()
        a, bpath, c = ctx_dir["user-profile.md"], ctx_dir["session-history.md"], ctx_dir["preferences.json"]

        # A -> B -> C -> A is rejected at build time, audited as builder error
        b = CRGBuilder()
        b.add_reference(a, depends_on=[bpath])
        b.add_reference(bpath, depends_on=[c])
        b.add_reference(c, depends_on=[a])
        with pytest.raises(ContextBuildError) as exc:
            b.build()
        assert exc.value.reason == "circular_references_detected"
        assert a in (exc.value.details or "")
        assert _events("context_builder_error")

        # Dangling dependency is also fail-closed
        b2 = CRGBuilder()
        b2.add_reference(a, depends_on=[bpath])
        with pytest.raises(ContextBuildError) as exc2:
            b2.build()
        assert exc2.value.reason == "dangling_dependency"

        # Valid DAG: A -> B -> C resolves dependencies-first
        b3 = CRGBuilder()
        b3.add_reference(a, depends_on=[bpath])
        b3.add_reference(bpath, depends_on=[c])
        b3.add_reference(c)
        digest = b3.build()
        assert _events("context_reference_dag_validated")[-1]["edge_count"] == 2
        loader = ReferenceLoader(digest)
        chain = loader.resolve_with_dependencies(a)
        assert [r.reference.file_path for r in chain] == [c, bpath, a]
        assert _events("context_reference_dependencies_resolved")[-1]["dependency_count"] == 2

    def test_dag_iterative_dfs_survives_deep_chain_and_reports_cycle_path(self):
        g = ReferenceGraph()
        n = 20000  # far beyond the default recursion limit
        for i in range(n):
            g.add_node(f"n{i}")
        for i in range(n - 1):
            g.add_edge(f"n{i}", f"n{i+1}")
        assert g.validate().ok
        g.add_edge(f"n{n-1}", "n0")  # close the loop
        v = g.validate()
        assert not v.ok and v.cycle is not None
        assert v.cycle[0] == v.cycle[-1] == "n0"
        with pytest.raises(CycleError):
            g.topological_order()
        # self-loop and dangling
        g2 = ReferenceGraph()
        g2.add_node("x")
        g2.add_edge("x", "x")
        assert g2.find_cycle() == ("x", "x")
        g3 = ReferenceGraph()
        g3.add_edge("x", "ghost")
        with pytest.raises(DanglingDependencyError):
            g3.topological_order()
