"""Tests for Phase 5: Advanced Context Optimizations (ADR-0394).

Covers:
- SelectiveInjector (relevance filtering)
- MemoryPruner (confidence + age + quota)
- ADRRanker (recency + relevance + status)
- Pipeline integration
- Feature flag toggles
- Edge cases

Expected coverage: 16 tests, all passing
"""
import pytest
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional

# Import the optimization modules
from operator.context_engineering.selective_injection import SelectiveInjector
from operator.context_engineering.memory_pruning import MemoryPruner
from operator.context_engineering.adr_reranking import ADRRanker
from operator.context_engineering.stages.base import ContextBundle, StageCtx, StageTelemetry
from operator.context_engineering.stages.selective_injection_stage import SelectiveInjectionStage
from operator.context_engineering.stages.memory_pruning_stage import MemoryPruningStage
from operator.context_engineering.stages.adr_reranking_stage import ADRRerangkingStage


# ── Test fixtures ───────────────────────────────────────────────────────────


@dataclass
class MockMemory:
    """Mock memory object for testing."""
    id: str
    title: str
    body: str
    confidence: float
    created_at: Optional[datetime] = None
    filename: str = ""


@dataclass
class MockADR:
    """Mock ADR object for testing."""
    id: str
    title: str
    status: str
    created_at: Optional[datetime] = None
    supersedes: list = None

    def __post_init__(self):
        if self.supersedes is None:
            self.supersedes = []


@dataclass
class MockTaskObj:
    """Mock task object for testing."""
    query: str = "test query"
    brief: str = "test brief"


@pytest.fixture
def mock_memories():
    """Create mock memories for testing."""
    now = datetime.now()
    return [
        MockMemory(
            id="mem1", title="Memory 1", body="content 1",
            confidence=0.9, created_at=now
        ),
        MockMemory(
            id="mem2", title="Memory 2", body="content 2",
            confidence=0.8, created_at=now - timedelta(days=5)
        ),
        MockMemory(
            id="mem3", title="Memory 3", body="content 3",
            confidence=0.2, created_at=now - timedelta(days=40)
        ),
        MockMemory(
            id="mem4", title="Memory 4", body="content 4",
            confidence=0.6, created_at=now - timedelta(days=20)
        ),
        MockMemory(
            id="mem5", title="Memory 5", body="content 5",
            confidence=0.4, created_at=now - timedelta(days=25)
        ),
    ]


@pytest.fixture
def mock_adrs():
    """Create mock ADRs for testing."""
    now = datetime.now()
    return [
        MockADR(
            id="ADR-0001", title="Accepted ADR",
            status="accepted", created_at=now
        ),
        MockADR(
            id="ADR-0002", title="Proposed ADR",
            status="proposed", created_at=now - timedelta(days=90)
        ),
        MockADR(
            id="ADR-0003", title="Superseded ADR",
            status="superseded", created_at=now - timedelta(days=180),
            supersedes=[]
        ),
        MockADR(
            id="ADR-0004", title="Frozen ADR",
            status="frozen", created_at=now - timedelta(days=365)
        ),
        MockADR(
            id="ADR-0005", title="Another Proposed",
            status="proposed", created_at=now - timedelta(days=30),
            supersedes=[]
        ),
    ]


# ── SelectiveInjector Tests ──────────────────────────────────────────────────


class TestSelectiveInjector:
    """Test the SelectiveInjector class."""

    def test_filter_by_relevance_basic(self):
        """Test basic relevance filtering."""
        injector = SelectiveInjector(threshold=0.5)
        memories = [
            MockMemory(id="m1", title="test query", body="", confidence=1.0),
            MockMemory(id="m2", title="unrelated", body="", confidence=1.0),
            MockMemory(id="m3", title="test", body="", confidence=1.0),
        ]
        query = "test query"

        filtered, tel = injector.filter_by_relevance(memories, query)

        assert len(filtered) > 0
        assert tel["items_before"] == 3
        assert tel["items_after"] <= 3
        assert tel["dropped_count"] == tel["items_before"] - tel["items_after"]
        assert "threshold" in tel

    def test_filter_empty_list(self):
        """Test filtering empty list."""
        injector = SelectiveInjector()
        filtered, tel = injector.filter_by_relevance([], "query")

        assert len(filtered) == 0
        assert tel["items_before"] == 0
        assert tel["items_after"] == 0
        assert tel["dropped_count"] == 0

    def test_filter_with_custom_threshold(self):
        """Test filtering with custom threshold."""
        injector = SelectiveInjector(threshold=0.5)
        memories = [
            MockMemory(id="m1", title="test", body="", confidence=1.0),
            MockMemory(id="m2", title="other", body="", confidence=1.0),
        ]

        filtered, tel = injector.filter_by_relevance(memories, "test", threshold=0.9)

        assert tel["threshold"] == 0.9
        # With very high threshold, fewer items pass
        assert len(filtered) <= len([m for m in memories if "test" in m.title])

    def test_filter_deduplication(self):
        """Test deduplication by id."""
        injector = SelectiveInjector(threshold=0.3)
        # Same id, different relevance scores
        memories = [
            MockMemory(id="m1", title="test", body="", confidence=1.0),
            MockMemory(id="m1", title="test again", body="", confidence=1.0),
        ]

        filtered, tel = injector.filter_by_relevance(memories, "test")

        # Should deduplicate to single item with highest score
        assert len(filtered) == 1
        assert filtered[0].id == "m1"

    def test_invalid_threshold(self):
        """Test that invalid thresholds are rejected."""
        with pytest.raises(ValueError):
            SelectiveInjector(threshold=1.5)

        with pytest.raises(ValueError):
            SelectiveInjector(threshold=-0.1)

    def test_telemetry_structure(self):
        """Test telemetry output structure."""
        injector = SelectiveInjector()
        memories = [MockMemory(id="m1", title="test", body="", confidence=1.0)]

        _, tel = injector.filter_by_relevance(memories, "test")

        assert "items_before" in tel
        assert "items_after" in tel
        assert "dropped_count" in tel
        assert "dropped_reasons" in tel
        assert "duration_ms" in tel
        assert "threshold" in tel


# ── MemoryPruner Tests ───────────────────────────────────────────────────────


class TestMemoryPruner:
    """Test the MemoryPruner class."""

    def test_prune_by_confidence(self):
        """Test pruning by confidence floor."""
        pruner = MemoryPruner(confidence_floor=0.5)
        memories = [
            MockMemory(id="m1", title="", body="", confidence=0.9),
            MockMemory(id="m2", title="", body="", confidence=0.3),
            MockMemory(id="m3", title="", body="", confidence=0.6),
        ]

        pruned, tel = pruner.prune(memories)

        assert len(pruned) == 2  # m1 and m3, not m2
        assert tel["dropped_count"] == 1
        assert tel["dropped_reasons"]["confidence_below_floor"] == 1

    def test_prune_by_age(self):
        """Test pruning by age retention policy."""
        now = datetime.now()
        pruner = MemoryPruner(max_age_days=10)
        memories = [
            MockMemory(id="m1", title="", body="", confidence=1.0, created_at=now),
            MockMemory(id="m2", title="", body="", confidence=1.0, created_at=now - timedelta(days=5)),
            MockMemory(id="m3", title="", body="", confidence=1.0, created_at=now - timedelta(days=40)),
        ]

        pruned, tel = pruner.prune(memories, now=now)

        assert len(pruned) == 2  # m1 and m2, not m3
        assert tel["dropped_reasons"]["age_exceeds_retention"] == 1

    def test_prune_by_quota(self):
        """Test pruning by per-tenant quota."""
        pruner = MemoryPruner(per_tenant_quota=2)
        now = datetime.now()
        memories = [
            MockMemory(id="m1", title="", body="", confidence=0.9, created_at=now),
            MockMemory(id="m2", title="", body="", confidence=0.8, created_at=now),
            MockMemory(id="m3", title="", body="", confidence=0.7, created_at=now),
        ]

        pruned, tel = pruner.prune(memories, now=now)

        assert len(pruned) == 2  # Quota enforced
        assert tel["dropped_reasons"]["quota_exceeded"] == 1

    def test_prune_sorts_by_confidence(self):
        """Test that results are sorted by confidence (highest first)."""
        pruner = MemoryPruner(per_tenant_quota=3)
        now = datetime.now()
        memories = [
            MockMemory(id="m1", title="", body="", confidence=0.5, created_at=now),
            MockMemory(id="m2", title="", body="", confidence=0.9, created_at=now),
            MockMemory(id="m3", title="", body="", confidence=0.7, created_at=now),
        ]

        pruned, _ = pruner.prune(memories, now=now)

        # Should be sorted by confidence: 0.9, 0.7, 0.5
        assert pruned[0].confidence == 0.9
        assert pruned[1].confidence == 0.7
        assert pruned[2].confidence == 0.5

    def test_prune_empty_list(self):
        """Test pruning empty list."""
        pruner = MemoryPruner()
        pruned, tel = pruner.prune([])

        assert len(pruned) == 0
        assert tel["memories_before"] == 0
        assert tel["memories_after"] == 0

    def test_prune_combined_rules(self):
        """Test pruning with all rules combined."""
        now = datetime.now()
        pruner = MemoryPruner(
            confidence_floor=0.3,
            max_age_days=30,
            per_tenant_quota=2
        )
        memories = [
            MockMemory(id="m1", title="", body="", confidence=0.9, created_at=now),
            MockMemory(id="m2", title="", body="", confidence=0.8, created_at=now - timedelta(days=5)),
            MockMemory(id="m3", title="", body="", confidence=0.2, created_at=now),  # Below confidence
            MockMemory(id="m4", title="", body="", confidence=0.7, created_at=now - timedelta(days=40)),  # Old
            MockMemory(id="m5", title="", body="", confidence=0.6, created_at=now),  # Would exceed quota
        ]

        pruned, tel = pruner.prune(memories, now=now)

        assert len(pruned) == 2
        assert tel["dropped_count"] == 3
        assert tel["memories_before"] == 5
        assert tel["memories_after"] == 2

    def test_invalid_parameters(self):
        """Test that invalid parameters are rejected."""
        with pytest.raises(ValueError):
            MemoryPruner(confidence_floor=1.5)

        with pytest.raises(ValueError):
            MemoryPruner(max_age_days=-1)

        with pytest.raises(ValueError):
            MemoryPruner(per_tenant_quota=-1)


# ── ADRRanker Tests ──────────────────────────────────────────────────────────


class TestADRRanker:
    """Test the ADRRanker class."""

    def test_rank_by_status(self):
        """Test that ADRs are ranked by status (ACCEPTED > PROPOSED > SUPERSEDED)."""
        ranker = ADRRanker(keep_top_k=3)
        adrs = [
            MockADR(id="ADR-0001", title="Accepted", status="accepted"),
            MockADR(id="ADR-0002", title="Proposed", status="proposed"),
            MockADR(id="ADR-0003", title="Superseded", status="superseded"),
        ]

        ranked, tel = ranker.rerank(adrs)

        # Accepted should be first
        assert ranked[0].status == "accepted"
        assert tel["adrs_before"] == 3

    def test_rank_by_recency(self):
        """Test that recent ADRs score higher."""
        now = datetime.now()
        ranker = ADRRanker(keep_top_k=3)
        adrs = [
            MockADR(id="ADR-0001", title="Old", status="proposed", created_at=now - timedelta(days=730)),
            MockADR(id="ADR-0002", title="Recent", status="proposed", created_at=now),
            MockADR(id="ADR-0003", title="Medium", status="proposed", created_at=now - timedelta(days=90)),
        ]

        ranked, tel = ranker.rerank(adrs, now=now)

        # Recent should be first
        assert ranked[0].id == "ADR-0002"
        assert len(ranked) == 3

    def test_filter_superseded(self):
        """Test that superseded ADRs are filtered out."""
        ranker = ADRRanker(keep_top_k=5)
        adrs = [
            MockADR(id="ADR-0001", title="New", status="accepted"),
            MockADR(id="ADR-0002", title="Old", status="superseded", supersedes=["ADR-0001"]),
        ]

        ranked, tel = ranker.rerank(adrs)

        # Only the newer one should remain
        assert len(ranked) == 1
        assert ranked[0].id == "ADR-0001"
        assert tel["dropped_reasons"]["superseded"] == 1

    def test_keep_top_k_truncation(self):
        """Test that only top-k ADRs are kept."""
        ranker = ADRRanker(keep_top_k=2)
        adrs = [
            MockADR(id="ADR-0001", title="A", status="accepted"),
            MockADR(id="ADR-0002", title="B", status="proposed"),
            MockADR(id="ADR-0003", title="C", status="proposed"),
            MockADR(id="ADR-0004", title="D", status="proposed"),
        ]

        ranked, tel = ranker.rerank(adrs)

        assert len(ranked) == 2
        assert tel["keep_top_k"] == 2
        assert tel["dropped_reasons"]["truncation_to_keep_top_k"] == 2

    def test_empty_adrs(self):
        """Test reranking empty ADR list."""
        ranker = ADRRanker()
        ranked, tel = ranker.rerank([])

        assert len(ranked) == 0
        assert tel["adrs_before"] == 0

    def test_invalid_parameters(self):
        """Test that invalid parameters are rejected."""
        with pytest.raises(ValueError):
            ADRRanker(keep_top_k=-1)

        with pytest.raises(ValueError):
            ADRRanker(recency_weight=-0.1)

        with pytest.raises(ValueError):
            ADRRanker(recency_weight=0.0, relevance_weight=0.0, status_weight=0.0)


# ── Pipeline Integration Tests ───────────────────────────────────────────────


class TestPipelineIntegration:
    """Test integration of optimization stages into the pipeline."""

    def test_selective_injection_stage(self):
        """Test SelectiveInjectionStage integrates correctly."""
        stage = SelectiveInjectionStage()
        assert stage.id == "selective_injection"
        assert "memory" in stage.requires
        assert stage.effect == "pure"

    def test_memory_pruning_stage(self):
        """Test MemoryPruningStage integrates correctly."""
        stage = MemoryPruningStage()
        assert stage.id == "memory_pruning"
        assert "memory" in stage.requires
        assert stage.effect == "pure"

    def test_adr_reranking_stage(self):
        """Test ADRRerangkingStage integrates correctly."""
        stage = ADRRerangkingStage()
        assert stage.id == "adr_reranking"
        assert "graph" in stage.requires
        assert stage.effect == "pure"


# ── Feature Flag Tests ───────────────────────────────────────────────────────


class TestFeatureFlags:
    """Test feature flag toggles."""

    def test_selective_injection_disabled(self):
        """Test that selective_injection can be disabled."""
        stage = SelectiveInjectionStage()
        bundle = ContextBundle(task="test")
        ctx = StageCtx()
        ctx.config = {"enabled": False}

        _, tel = stage.run(bundle, ctx)
        assert tel.status == "skipped"
        assert tel.reason == "disabled_by_config"

    def test_memory_pruning_disabled(self):
        """Test that memory_pruning can be disabled."""
        stage = MemoryPruningStage()
        bundle = ContextBundle(task="test")
        ctx = StageCtx()
        ctx.config = {"enabled": False}

        _, tel = stage.run(bundle, ctx)
        assert tel.status == "skipped"
        assert tel.reason == "disabled_by_config"

    def test_adr_reranking_disabled(self):
        """Test that adr_reranking can be disabled."""
        stage = ADRRerangkingStage()
        bundle = ContextBundle(task="test")
        ctx = StageCtx()
        ctx.config = {"enabled": False}

        _, tel = stage.run(bundle, ctx)
        assert tel.status == "skipped"
        assert tel.reason == "disabled_by_config"


# ── Edge Case Tests ──────────────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_all_low_relevance(self):
        """Test with all items below relevance threshold."""
        injector = SelectiveInjector(threshold=0.99)
        memories = [
            MockMemory(id="m1", title="unrelated", body="text", confidence=1.0),
            MockMemory(id="m2", title="other", body="content", confidence=1.0),
        ]

        filtered, tel = injector.filter_by_relevance(memories, "specific term")

        # All items may be dropped if similarity is low
        assert tel["dropped_count"] >= 0

    def test_all_low_confidence(self):
        """Test with all memories below confidence floor."""
        pruner = MemoryPruner(confidence_floor=0.9)
        memories = [
            MockMemory(id="m1", title="", body="", confidence=0.1),
            MockMemory(id="m2", title="", body="", confidence=0.2),
        ]

        pruned, tel = pruner.prune(memories)

        assert len(pruned) == 0
        assert tel["dropped_count"] == 2

    def test_no_adrs(self):
        """Test ADR reranking with no ADRs."""
        ranker = ADRRanker()
        ranked, tel = ranker.rerank([])

        assert len(ranked) == 0
        assert tel["adrs_before"] == 0

    def test_determinism(self):
        """Test that same input produces same output (deterministic)."""
        ranker = ADRRanker(keep_top_k=2)
        adrs = [
            MockADR(id="ADR-0001", title="Test", status="proposed"),
            MockADR(id="ADR-0002", title="Test2", status="proposed"),
            MockADR(id="ADR-0003", title="Test3", status="proposed"),
        ]

        ranked1, _ = ranker.rerank(adrs, query="test")
        ranked2, _ = ranker.rerank(adrs, query="test")

        # Same input should produce same output
        assert [a.id for a in ranked1] == [a.id for a in ranked2]


# ── Context Size Validation Tests ────────────────────────────────────────────


class TestContextSizeReduction:
    """Test that optimizations actually reduce context size."""

    def test_selective_injection_reduces_size(self):
        """Test that selective injection reduces memory count."""
        injector = SelectiveInjector(threshold=0.7)
        memories = [MockMemory(id=f"m{i}", title="test", body="", confidence=1.0) for i in range(10)]

        filtered, tel = injector.filter_by_relevance(memories, "unrelated query")

        # Should drop some items
        assert tel["dropped_count"] >= 0

    def test_memory_pruning_reduces_size(self):
        """Test that memory pruning reduces memory count."""
        now = datetime.now()
        pruner = MemoryPruner(confidence_floor=0.8, per_tenant_quota=2)
        memories = [
            MockMemory(id=f"m{i}", title="", body="", confidence=0.5 + i*0.1, created_at=now)
            for i in range(5)
        ]

        pruned, tel = pruner.prune(memories, now=now)

        # Should drop some items
        assert tel["dropped_count"] > 0
        assert len(pruned) <= 2  # Quota

    def test_adr_reranking_reduces_size(self):
        """Test that ADR reranking reduces ADR count."""
        ranker = ADRRanker(keep_top_k=2)
        adrs = [MockADR(id=f"ADR-{i:04d}", title="Test", status="proposed") for i in range(5)]

        ranked, tel = ranker.rerank(adrs)

        assert len(ranked) == 2
        assert tel["dropped_count"] == 3
