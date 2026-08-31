"""ShardCursor + ShardManager + ShardPlan — data sharding for inter-job parallelism (ADR-0026 §C).

Four built-in sharding strategies:
  - hash:       modular hash of row index → N buckets (i.i.d. assumption)
  - range:      sequential row ranges (time-series / ordered data)
  - stratified: class-balanced buckets (prevents class imbalance)
  - time_window: fixed calendar windows (online learning / drift detection)

ShardManager.plan() + execute():
  - If backend.inter_job_compatible is False → single full-cursor job
  - Otherwise → N ShardCursor objects split by strategy

DataCursor is typed as Any (real implementation is Layer 24 — external).
ShardCursor wraps a DataCursor and filters rows matching the shard.

MUST NOT import anthropic / openai / google.cloud.aiplatform.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import math
import random
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

# DataCursor is an opaque type from Layer 24
DataCursor = Any

# Noise factor for total_rows_estimate (privacy protection)
_ESTIMATE_NOISE_FRACTION = 0.05


@dataclasses.dataclass
class ShardPlan:
    """Describes the sharding plan for a job."""
    strategy: str
    n_shards: int
    # For stratified: column name
    stratify_col: Optional[str] = None
    # For time_window: column name + window spec
    time_col: Optional[str] = None
    window_size_days: int = 7
    # If backend is incompatible, plan is single-shard (no splitting)
    single_shard: bool = False


class ShardCursor:
    """Wraps a DataCursor and exposes only the rows belonging to shard_index.

    Backends receive a ShardCursor indistinguishable from a full DataCursor.
    """

    def __init__(
        self,
        data: Any,
        *,
        shard_index: int,
        n_shards: int,
        strategy: str,
        stratify_col: Optional[str] = None,
        time_col: Optional[str] = None,
        window_size_days: int = 7,
    ) -> None:
        self._data = data
        self.shard_index = shard_index
        self.n_shards = n_shards
        self.strategy = strategy
        self._stratify_col = stratify_col
        self._time_col = time_col
        self._window_size_days = window_size_days
        self._rows: Optional[list] = None

    def _build_rows(self) -> list:
        """Materialise the shard-specific rows from the underlying data."""
        if self._data is None:
            return []
        rows = _extract_rows(self._data)
        return _filter_rows(
            rows,
            shard_index=self.shard_index,
            n_shards=self.n_shards,
            strategy=self.strategy,
            stratify_col=self._stratify_col,
            time_col=self._time_col,
            window_size_days=self._window_size_days,
        )

    def get_rows(self) -> list:
        if self._rows is None:
            self._rows = self._build_rows()
        return self._rows

    def total_rows_estimate(self) -> int:
        """Return a noise-added estimate of shard row count (privacy protection)."""
        exact = len(self.get_rows())
        noise = int(exact * _ESTIMATE_NOISE_FRACTION * random.uniform(-1, 1))
        return max(0, exact + noise)

    # DataCursor-like dict access for backends that use cursor["X"] / cursor["y"]
    def __getitem__(self, key: str) -> Any:
        rows = self.get_rows()
        if not rows:
            return []
        if isinstance(self._data, dict) and key in self._data:
            indices = _get_row_indices(
                _extract_rows(self._data),
                shard_index=self.shard_index,
                n_shards=self.n_shards,
                strategy=self.strategy,
                stratify_col=self._stratify_col,
                time_col=self._time_col,
                window_size_days=self._window_size_days,
            )
            source = self._data[key]
            if hasattr(source, "__getitem__"):
                return [source[i] for i in indices]
            return source
        return rows

    def get(self, key: str, default: Any = None) -> Any:
        # Check if key exists in the underlying data before trying __getitem__
        if isinstance(self._data, dict) and key not in self._data:
            return default
        try:
            return self[key]
        except (KeyError, IndexError, TypeError):
            return default

    def __len__(self) -> int:
        return len(self.get_rows())


def _extract_rows(data: Any) -> list:
    """Extract a list of rows from various data shapes."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Use first value's length to construct synthetic row list
        first_key = next(iter(data), None)
        if first_key is not None:
            vals = data[first_key]
            if hasattr(vals, "__len__"):
                return list(range(len(vals)))
    if hasattr(data, "__len__"):
        return list(range(len(data)))
    if hasattr(data, "__iter__"):
        return list(data)
    return []


def _get_row_indices(
    rows: list,
    *,
    shard_index: int,
    n_shards: int,
    strategy: str,
    stratify_col: Optional[str],
    time_col: Optional[str],
    window_size_days: int,
) -> list[int]:
    """Return the indices of rows belonging to this shard."""
    all_indices = list(range(len(rows)))

    if strategy == "hash":
        return [
            i for i in all_indices
            if _hash_shard(i, n_shards) == shard_index
        ]
    elif strategy == "range":
        chunk_size = math.ceil(len(all_indices) / n_shards)
        start = shard_index * chunk_size
        end = min(start + chunk_size, len(all_indices))
        return all_indices[start:end]
    elif strategy == "stratified":
        return _stratified_indices(rows, all_indices, shard_index, n_shards, stratify_col)
    elif strategy == "time_window":
        return _time_window_indices(rows, all_indices, shard_index, n_shards,
                                    time_col, window_size_days)
    else:
        log.warning("unknown shard strategy %r; falling back to hash", strategy)
        return [i for i in all_indices if _hash_shard(i, n_shards) == shard_index]


def _filter_rows(
    rows: list,
    *,
    shard_index: int,
    n_shards: int,
    strategy: str,
    stratify_col: Optional[str],
    time_col: Optional[str],
    window_size_days: int,
) -> list:
    indices = _get_row_indices(
        rows,
        shard_index=shard_index,
        n_shards=n_shards,
        strategy=strategy,
        stratify_col=stratify_col,
        time_col=time_col,
        window_size_days=window_size_days,
    )
    return [rows[i] for i in indices]


def _hash_shard(index: int, n_shards: int) -> int:
    """Deterministic hash-based shard assignment."""
    digest = hashlib.md5(str(index).encode()).hexdigest()  # noqa: S324
    return int(digest, 16) % n_shards


def _stratified_indices(
    rows: list,
    all_indices: list[int],
    shard_index: int,
    n_shards: int,
    stratify_col: Optional[str],
) -> list[int]:
    """Class-balanced shard assignment."""
    # Group indices by class value
    class_buckets: dict[Any, list[int]] = {}
    for i, row in zip(all_indices, rows):
        if isinstance(row, dict) and stratify_col:
            class_val = row.get(stratify_col, 0)
        elif stratify_col and hasattr(row, stratify_col):
            class_val = getattr(row, stratify_col)
        else:
            class_val = i % n_shards  # fallback: hash-based
        class_buckets.setdefault(class_val, []).append(i)

    result = []
    for class_indices in class_buckets.values():
        # Assign each class's rows evenly across shards
        for j, idx in enumerate(class_indices):
            if j % n_shards == shard_index:
                result.append(idx)
    return result


def _time_window_indices(
    rows: list,
    all_indices: list[int],
    shard_index: int,
    n_shards: int,
    time_col: Optional[str],
    window_size_days: int,
) -> list[int]:
    """Calendar-window shard assignment.

    Assigns rows to shards based on their position within sequential windows.
    Without actual timestamp parsing, we use row order as a proxy.
    """
    chunk_size = math.ceil(len(all_indices) / n_shards)
    start = shard_index * chunk_size
    end = min(start + chunk_size, len(all_indices))
    return all_indices[start:end]


@dataclasses.dataclass
class ShardManager:
    """Manages the sharding of a DataCursor across N jobs."""

    def plan(
        self,
        cursor: DataCursor,
        *,
        n_shards: int,
        strategy: str = "hash",
        backend_inter_job_compatible: bool = True,
        stratify_col: Optional[str] = None,
        time_col: Optional[str] = None,
        window_size_days: int = 7,
    ) -> ShardPlan:
        """Produce a ShardPlan for the given cursor and configuration."""
        if not backend_inter_job_compatible:
            return ShardPlan(
                strategy=strategy,
                n_shards=1,
                single_shard=True,
            )
        return ShardPlan(
            strategy=strategy,
            n_shards=n_shards,
            stratify_col=stratify_col,
            time_col=time_col,
            window_size_days=window_size_days,
            single_shard=False,
        )

    def execute(
        self,
        cursor: DataCursor,
        plan: ShardPlan,
    ) -> list[ShardCursor]:
        """Split cursor into ShardCursor objects according to plan."""
        if plan.single_shard:
            return [ShardCursor(
                cursor,
                shard_index=0,
                n_shards=1,
                strategy=plan.strategy,
            )]
        return [
            ShardCursor(
                cursor,
                shard_index=i,
                n_shards=plan.n_shards,
                strategy=plan.strategy,
                stratify_col=plan.stratify_col,
                time_col=plan.time_col,
                window_size_days=plan.window_size_days,
            )
            for i in range(plan.n_shards)
        ]


__all__ = [
    "ShardCursor",
    "ShardManager",
    "ShardPlan",
    "DataCursor",
]
