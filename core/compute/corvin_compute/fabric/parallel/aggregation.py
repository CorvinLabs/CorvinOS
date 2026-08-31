"""Aggregator Protocol + implementations for parallel shard results (ADR-0026 §C).

Aggregators receive ArtifactManifest objects from completed shard workers and
combine them into a single result.

CRITICAL: FederatedAvgAggregator MUST NOT access DataCursor — only ArtifactManifest.
          This is a structural test requirement.

Strategies:
  - best:          pick manifest with best primary_metric
  - average:       average metric_value (and weight-average params if available)
  - vote:          majority vote on prediction outcome encoded in manifest tags
  - stack:         train a meta-learner (stub — meta training not in scope here)
  - federated_avg: FedAvg — aggregate model weights from manifests only
  - custom:        caller supplies aggregate(manifests) -> ArtifactManifest

MUST NOT import anthropic / openai / google.cloud.aiplatform.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from ..backends.protocol import ArtifactManifest

log = logging.getLogger(__name__)


@runtime_checkable
class Aggregator(Protocol):
    """Protocol for all Aggregator implementations."""

    strategy: str

    def aggregate(
        self, manifests: list[ArtifactManifest]
    ) -> ArtifactManifest: ...


class BestAggregator:
    """Pick the manifest with the highest primary_metric value."""

    strategy: str = "best"

    def __init__(self, *, minimize: bool = True) -> None:
        self._minimize = minimize

    def aggregate(self, manifests: list[ArtifactManifest]) -> ArtifactManifest:
        if not manifests:
            raise ValueError("BestAggregator.aggregate called with empty list")
        if self._minimize:
            return min(manifests, key=lambda m: m.metric_value)
        return max(manifests, key=lambda m: m.metric_value)


class AverageAggregator:
    """Average the primary metric values across all manifests.

    Returns a synthetic manifest with the same run_id as the first and
    metric_value = mean of all metric_values.
    """

    strategy: str = "average"

    def aggregate(self, manifests: list[ArtifactManifest]) -> ArtifactManifest:
        if not manifests:
            raise ValueError("AverageAggregator.aggregate called with empty list")
        avg_metric = sum(m.metric_value for m in manifests) / len(manifests)
        base = manifests[0]
        return ArtifactManifest(
            run_id=base.run_id,
            backend=base.backend,
            backend_version=base.backend_version,
            primary_metric=base.primary_metric,
            metric_value=avg_metric,
            artifact_path_hash=base.artifact_path_hash,
            artifact_size_b=sum(m.artifact_size_b for m in manifests),
            tags=["aggregated:average"] + base.tags,
            extra={"n_shards": len(manifests), "strategy": "average"},
        )


class VoteAggregator:
    """Majority vote — reads predicted class from manifest tags.

    Tags format: "predicted_class:<label>" — the majority label wins.
    """

    strategy: str = "vote"

    def aggregate(self, manifests: list[ArtifactManifest]) -> ArtifactManifest:
        if not manifests:
            raise ValueError("VoteAggregator.aggregate called with empty list")
        # Collect votes from tags
        votes: dict[str, int] = {}
        for m in manifests:
            for tag in m.tags:
                if tag.startswith("predicted_class:"):
                    label = tag.split(":", 1)[1]
                    votes[label] = votes.get(label, 0) + 1
        winner = max(votes, key=lambda k: votes[k]) if votes else "unknown"
        base = manifests[0]
        best_manifest = min(manifests, key=lambda m: m.metric_value)
        return ArtifactManifest(
            run_id=base.run_id,
            backend=base.backend,
            backend_version=base.backend_version,
            primary_metric=base.primary_metric,
            metric_value=best_manifest.metric_value,
            artifact_path_hash=best_manifest.artifact_path_hash,
            artifact_size_b=0,
            tags=[f"voted_class:{winner}", "aggregated:vote"],
            extra={"votes": votes, "n_shards": len(manifests), "strategy": "vote"},
        )


class StackAggregator:
    """Stub aggregator — in a real implementation would train a meta-learner.

    Returns the best manifest annotated with strategy=stack.
    """

    strategy: str = "stack"

    def aggregate(self, manifests: list[ArtifactManifest]) -> ArtifactManifest:
        if not manifests:
            raise ValueError("StackAggregator.aggregate called with empty list")
        best = min(manifests, key=lambda m: m.metric_value)
        return ArtifactManifest(
            run_id=best.run_id,
            backend=best.backend,
            backend_version=best.backend_version,
            primary_metric=best.primary_metric,
            metric_value=best.metric_value,
            artifact_path_hash=best.artifact_path_hash,
            artifact_size_b=best.artifact_size_b,
            tags=["aggregated:stack"] + best.tags,
            extra={"n_shards": len(manifests), "strategy": "stack"},
        )


class FederatedAvgAggregator:
    """FedAvg — privacy-preserving aggregation using only ArtifactManifest.

    STRUCTURAL INVARIANT: MUST NOT access DataCursor at any point.
    Only ArtifactManifest objects are visible to this aggregator.

    FedAvg in this implementation averages the metric_values and produces
    a synthetic manifest representing the federated model.  In a full
    implementation, model weight averaging would happen here; model weights
    are encoded as metadata in manifest.extra (not DataCursor).
    """

    strategy: str = "federated_avg"

    def aggregate(self, manifests: list[ArtifactManifest]) -> ArtifactManifest:
        if not manifests:
            raise ValueError("FederatedAvgAggregator.aggregate called with empty list")
        # FedAvg: weighted average (equal weights here)
        n = len(manifests)
        avg_metric = sum(m.metric_value for m in manifests) / n
        total_size = sum(m.artifact_size_b for m in manifests)
        base = manifests[0]
        # Aggregate any weight_vectors from extra (if provided by backends)
        avg_weights: dict[str, float] = {}
        all_weight_keys: set[str] = set()
        for m in manifests:
            wv = m.extra.get("weight_vector", {})
            all_weight_keys.update(wv.keys())
        for key in all_weight_keys:
            values = [m.extra.get("weight_vector", {}).get(key, 0.0) for m in manifests]
            avg_weights[key] = sum(values) / n
        return ArtifactManifest(
            run_id=base.run_id,
            backend=base.backend,
            backend_version=base.backend_version,
            primary_metric=base.primary_metric,
            metric_value=avg_metric,
            artifact_path_hash=ArtifactManifest.hash_path(
                f"{base.run_id}/federated_avg"
            ),
            artifact_size_b=total_size,
            tags=["aggregated:federated_avg"] + list({t for m in manifests for t in m.tags}),
            extra={
                "n_shards": n,
                "strategy": "federated_avg",
                "avg_weight_vector": avg_weights,
                # NOTE: no DataCursor reference — structural invariant
            },
        )


class CustomAggregator:
    """Aggregator that calls a user-provided function.

    ``fn`` receives list[ArtifactManifest] and returns ArtifactManifest.
    """

    strategy: str = "custom"

    def __init__(self, fn: Callable[[list[ArtifactManifest]], ArtifactManifest]) -> None:
        self._fn = fn

    def aggregate(self, manifests: list[ArtifactManifest]) -> ArtifactManifest:
        return self._fn(manifests)


def get_aggregator(strategy: str, **kwargs: Any) -> Aggregator:
    """Factory — return the aggregator for a given strategy name."""
    mapping: dict[str, type] = {
        "best": BestAggregator,
        "average": AverageAggregator,
        "vote": VoteAggregator,
        "stack": StackAggregator,
        "federated_avg": FederatedAvgAggregator,
    }
    if strategy not in mapping:
        raise ValueError(
            f"unknown aggregation strategy {strategy!r}; "
            f"valid: {sorted(mapping.keys())}"
        )
    return mapping[strategy](**{k: v for k, v in kwargs.items()
                                if k in _get_init_params(mapping[strategy])})


def _get_init_params(cls: type) -> set[str]:
    import inspect
    try:
        sig = inspect.signature(cls.__init__)
        return set(sig.parameters.keys()) - {"self"}
    except (TypeError, ValueError):
        return set()


__all__ = [
    "Aggregator",
    "BestAggregator",
    "AverageAggregator",
    "VoteAggregator",
    "StackAggregator",
    "FederatedAvgAggregator",
    "CustomAggregator",
    "get_aggregator",
]
