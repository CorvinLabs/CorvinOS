"""Tests for Aggregator implementations — 30 test cases (ADR-0026 §C)."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute.fabric.backends.protocol import ArtifactManifest
from corvin_compute.fabric.parallel.aggregation import (
    AverageAggregator,
    BestAggregator,
    CustomAggregator,
    FederatedAvgAggregator,
    StackAggregator,
    VoteAggregator,
    get_aggregator,
)


def _manifest(
    run_id="r1",
    metric=0.5,
    backend="sklearn",
    tags=None,
    weight_vector=None,
) -> ArtifactManifest:
    extra = {}
    if weight_vector is not None:
        extra["weight_vector"] = weight_vector
    return ArtifactManifest(
        run_id=run_id,
        backend=backend,
        backend_version="1.0.0",
        primary_metric="loss",
        metric_value=metric,
        artifact_path_hash=ArtifactManifest.hash_path(f"/run/{run_id}/model"),
        tags=tags or [],
        extra=extra,
    )


# ---------------------------------------------------------------------------
# BestAggregator
# ---------------------------------------------------------------------------

class TestBestAggregator:
    def test_picks_lowest_metric_minimize(self):
        agg = BestAggregator(minimize=True)
        result = agg.aggregate([_manifest(metric=0.8), _manifest(metric=0.3), _manifest(metric=0.5)])
        assert result.metric_value == pytest.approx(0.3)

    def test_picks_highest_metric_maximize(self):
        agg = BestAggregator(minimize=False)
        result = agg.aggregate([_manifest(metric=0.8), _manifest(metric=0.3), _manifest(metric=0.5)])
        assert result.metric_value == pytest.approx(0.8)

    def test_single_manifest_returned(self):
        agg = BestAggregator()
        m = _manifest(metric=0.42)
        result = agg.aggregate([m])
        assert result.metric_value == pytest.approx(0.42)

    def test_empty_raises(self):
        agg = BestAggregator()
        with pytest.raises(ValueError):
            agg.aggregate([])

    def test_strategy_name(self):
        assert BestAggregator.strategy == "best"


# ---------------------------------------------------------------------------
# AverageAggregator
# ---------------------------------------------------------------------------

class TestAverageAggregator:
    def test_averages_metric_values(self):
        agg = AverageAggregator()
        result = agg.aggregate([
            _manifest(metric=0.2),
            _manifest(metric=0.4),
            _manifest(metric=0.6),
        ])
        assert result.metric_value == pytest.approx(0.4)

    def test_single_manifest_avg_unchanged(self):
        agg = AverageAggregator()
        result = agg.aggregate([_manifest(metric=0.77)])
        assert result.metric_value == pytest.approx(0.77)

    def test_includes_aggregated_tag(self):
        agg = AverageAggregator()
        result = agg.aggregate([_manifest(metric=0.5), _manifest(metric=0.3)])
        assert "aggregated:average" in result.tags

    def test_n_shards_in_extra(self):
        agg = AverageAggregator()
        result = agg.aggregate([_manifest(), _manifest(), _manifest()])
        assert result.extra.get("n_shards") == 3

    def test_empty_raises(self):
        agg = AverageAggregator()
        with pytest.raises(ValueError):
            agg.aggregate([])

    def test_strategy_name(self):
        assert AverageAggregator.strategy == "average"


# ---------------------------------------------------------------------------
# VoteAggregator
# ---------------------------------------------------------------------------

class TestVoteAggregator:
    def test_majority_vote_wins(self):
        agg = VoteAggregator()
        manifests = [
            _manifest(tags=["predicted_class:cat"]),
            _manifest(tags=["predicted_class:cat"]),
            _manifest(tags=["predicted_class:dog"]),
        ]
        result = agg.aggregate(manifests)
        assert "voted_class:cat" in result.tags

    def test_tie_returns_one_winner(self):
        agg = VoteAggregator()
        manifests = [
            _manifest(tags=["predicted_class:A"]),
            _manifest(tags=["predicted_class:B"]),
        ]
        result = agg.aggregate(manifests)
        # Should contain exactly one voted_class tag
        voted = [t for t in result.tags if t.startswith("voted_class:")]
        assert len(voted) == 1

    def test_no_predicted_class_tag_unknown(self):
        agg = VoteAggregator()
        result = agg.aggregate([_manifest(), _manifest()])
        assert "voted_class:unknown" in result.tags

    def test_empty_raises(self):
        agg = VoteAggregator()
        with pytest.raises(ValueError):
            agg.aggregate([])

    def test_strategy_name(self):
        assert VoteAggregator.strategy == "vote"


# ---------------------------------------------------------------------------
# StackAggregator
# ---------------------------------------------------------------------------

class TestStackAggregator:
    def test_returns_best_manifest(self):
        agg = StackAggregator()
        result = agg.aggregate([_manifest(metric=0.9), _manifest(metric=0.2)])
        assert result.metric_value == pytest.approx(0.2)

    def test_includes_aggregated_stack_tag(self):
        agg = StackAggregator()
        result = agg.aggregate([_manifest(), _manifest()])
        assert "aggregated:stack" in result.tags

    def test_empty_raises(self):
        agg = StackAggregator()
        with pytest.raises(ValueError):
            agg.aggregate([])


# ---------------------------------------------------------------------------
# FederatedAvgAggregator — MUST NOT access DataCursor
# ---------------------------------------------------------------------------

class TestFederatedAvgAggregator:
    def test_averages_metric_values(self):
        agg = FederatedAvgAggregator()
        result = agg.aggregate([
            _manifest(metric=0.2),
            _manifest(metric=0.4),
        ])
        assert result.metric_value == pytest.approx(0.3)

    def test_includes_federated_avg_tag(self):
        agg = FederatedAvgAggregator()
        result = agg.aggregate([_manifest(), _manifest()])
        assert "aggregated:federated_avg" in result.tags

    def test_n_shards_in_extra(self):
        agg = FederatedAvgAggregator()
        result = agg.aggregate([_manifest(), _manifest(), _manifest()])
        assert result.extra.get("n_shards") == 3

    def test_averages_weight_vectors(self):
        agg = FederatedAvgAggregator()
        m1 = _manifest(weight_vector={"w0": 1.0, "w1": 0.0})
        m2 = _manifest(weight_vector={"w0": 0.0, "w1": 1.0})
        result = agg.aggregate([m1, m2])
        avg_wv = result.extra.get("avg_weight_vector", {})
        assert avg_wv.get("w0") == pytest.approx(0.5)
        assert avg_wv.get("w1") == pytest.approx(0.5)

    def test_empty_raises(self):
        agg = FederatedAvgAggregator()
        with pytest.raises(ValueError):
            agg.aggregate([])

    def test_strategy_name(self):
        assert FederatedAvgAggregator.strategy == "federated_avg"

    def test_federated_avg_never_accesses_data_cursor_structurally(self):
        """AST check: FederatedAvgAggregator.aggregate must not reference DataCursor."""
        agg_src = (
            Path(__file__).parent.parent
            / "corvin_compute" / "fabric" / "parallel" / "aggregation.py"
        )
        source = agg_src.read_text()
        tree = ast.parse(source)

        # Find FederatedAvgAggregator class
        class_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "FederatedAvgAggregator":
                class_node = node
                break
        assert class_node is not None

        # Check no reference to DataCursor in class body
        for node in ast.walk(class_node):
            if isinstance(node, ast.Name) and node.id == "DataCursor":
                pytest.fail(
                    "FederatedAvgAggregator references DataCursor — structural violation"
                )
            if isinstance(node, ast.Attribute) and node.attr == "DataCursor":
                pytest.fail(
                    "FederatedAvgAggregator references DataCursor attribute — structural violation"
                )

    def test_federated_avg_result_has_hash_not_none(self):
        agg = FederatedAvgAggregator()
        result = agg.aggregate([_manifest(), _manifest()])
        assert result.artifact_path_hash is not None
        assert len(result.artifact_path_hash) == 16


# ---------------------------------------------------------------------------
# CustomAggregator
# ---------------------------------------------------------------------------

class TestCustomAggregator:
    def test_calls_provided_function(self):
        called = [False]

        def my_fn(manifests):
            called[0] = True
            return manifests[0]

        agg = CustomAggregator(fn=my_fn)
        result = agg.aggregate([_manifest()])
        assert called[0] is True

    def test_returns_function_result(self):
        custom_manifest = _manifest(metric=0.123)

        def my_fn(manifests):
            return custom_manifest

        agg = CustomAggregator(fn=my_fn)
        result = agg.aggregate([_manifest(), _manifest()])
        assert result.metric_value == pytest.approx(0.123)

    def test_strategy_name(self):
        agg = CustomAggregator(fn=lambda m: m[0])
        assert agg.strategy == "custom"


# ---------------------------------------------------------------------------
# get_aggregator factory
# ---------------------------------------------------------------------------

class TestGetAggregator:
    def test_get_best(self):
        agg = get_aggregator("best")
        assert isinstance(agg, BestAggregator)

    def test_get_average(self):
        agg = get_aggregator("average")
        assert isinstance(agg, AverageAggregator)

    def test_get_vote(self):
        agg = get_aggregator("vote")
        assert isinstance(agg, VoteAggregator)

    def test_get_stack(self):
        agg = get_aggregator("stack")
        assert isinstance(agg, StackAggregator)

    def test_get_federated_avg(self):
        agg = get_aggregator("federated_avg")
        assert isinstance(agg, FederatedAvgAggregator)

    def test_get_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown aggregation strategy"):
            get_aggregator("nonexistent_strategy_xyz")
