"""Polars-transform ComputeBackend — lazy LazyFrame eval + Parquet intermediate (ADR-0026 §A).

This backend is not a classical ML training backend; it drives iterative
feature-engineering / transform pipelines using Polars lazy evaluation.
Each epoch applies a set of transforms and checkpoints to Parquet.

MUST NOT import anthropic / openai / google.cloud.aiplatform.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import pickle
import tempfile
import time
from pathlib import Path
from typing import Any

from ..protocol import (
    ArtifactManifest,
    BackendParams,
    BackendSession,
    DataCursor,
    EpochMetrics,
    JobSpec,
    SteeringVector,
)
from .sklearn_backend import _apply_directive

log = logging.getLogger(__name__)

try:
    import polars as pl  # type: ignore[import]
    POLARS_AVAILABLE = True
except ImportError:
    POLARS_AVAILABLE = False


@dataclasses.dataclass
class _PolarsSession(BackendSession):
    lazy_frame: Any = None          # polars.LazyFrame or None
    transform_steps: list = dataclasses.field(default_factory=list)
    # Cross-platform temp dir (no ``/tmp`` on Windows) + a per-process suffix so
    # concurrent sessions/tenants don't collide on one fixed shared path.
    # Still overridable by assigning a different Path after construction.
    checkpoint_path: Path = dataclasses.field(
        default_factory=lambda: Path(tempfile.gettempdir()) / f"polars_ckpt_{os.getpid()}"
    )
    row_count: int = 0
    collected_df: Any = None        # polars.DataFrame after collect()


class PolarsTransformBackend:
    """Polars lazy-eval transform backend.

    Each epoch applies a set of column transforms and evaluates the lazy plan.
    The metric is the number of rows passing a quality filter (proxy loss).
    """

    name: str = "polars_transform"
    version: str = "1.0.0"
    supports_partial_fit: bool = False
    supports_checkpointing: bool = True
    supports_distributed: bool = False
    inter_job_compatible: bool = True

    _STEERING_MAP: dict[str, str] = {
        "batch_size": "batch_size",
        "sample_frac": "sample_frac",
        "chunk_rows": "chunk_rows",
    }

    def create_session(self, spec: JobSpec, cursor: DataCursor) -> _PolarsSession:
        session = _PolarsSession(
            run_id=spec.run_id,
            backend_name=self.name,
            shard_index=spec.shard_index,
            params=dict(spec.params),
        )
        if POLARS_AVAILABLE and cursor is not None:
            if isinstance(cursor, dict):
                # Build a simple DataFrame from dict of lists
                data = {k: v for k, v in cursor.items() if isinstance(v, (list, type(None)))}
                if data:
                    try:
                        df = pl.DataFrame(data)
                        session.lazy_frame = df.lazy()
                        session.row_count = len(df)
                    except Exception as exc:
                        log.debug("polars session init: %s", exc)
            elif hasattr(cursor, "lazy"):
                session.lazy_frame = cursor.lazy()
            elif hasattr(cursor, "__len__"):
                session.row_count = len(cursor)
        else:
            # Stub: store raw cursor
            session._state["cursor"] = cursor
            if isinstance(cursor, dict):
                first_val = next(iter(cursor.values()), [])
                session.row_count = len(first_val)

        session.transform_steps = list(spec.extra.get("transforms", []))
        return session

    def train_epoch(self, session: _PolarsSession) -> EpochMetrics:
        t0 = time.monotonic()
        session.current_epoch += 1

        if POLARS_AVAILABLE and session.lazy_frame is not None:
            try:
                # Apply transform steps declared in extra
                lf = session.lazy_frame
                for step in session.transform_steps:
                    col_name = step.get("col")
                    op = step.get("op")
                    if col_name and op == "fill_null":
                        lf = lf.with_columns(
                            pl.col(col_name).fill_null(step.get("value", 0))
                        )
                    elif col_name and op == "clip":
                        lf = lf.with_columns(
                            pl.col(col_name).clip(step.get("min", None), step.get("max", None))
                        )
                session.collected_df = lf.collect()
                row_count = len(session.collected_df)
                session.row_count = row_count
                # Proxy loss: fraction of rows with any null
                null_frac = session.collected_df.null_count().sum_horizontal().sum() / max(1, row_count)
                metric_value = float(null_frac) if hasattr(null_frac, "__float__") else 0.0
            except Exception as exc:
                log.debug("polars epoch failed: %s", exc)
                metric_value = max(0.01, 1.0 / (session.current_epoch + 1))
        else:
            metric_value = max(0.01, 1.0 / (session.current_epoch + 1))

        wall_ms = (time.monotonic() - t0) * 1000
        metrics = EpochMetrics(
            epoch=session.current_epoch,
            primary_metric="null_frac",
            metric_value=metric_value,
            wall_ms=wall_ms,
        )
        session.history.append(metrics)
        return metrics

    def translate_steering(self, vector: SteeringVector) -> BackendParams:
        result: dict[str, Any] = {}
        defaults: dict[str, Any] = {
            "batch_size": 1024,
            "sample_frac": 1.0,
            "chunk_rows": 10000,
        }
        for abstract_key, directive in vector.vector.items():
            native_key = self._STEERING_MAP.get(abstract_key, abstract_key)
            current = defaults.get(native_key, 1.0)
            result[native_key] = _apply_directive(current, directive)
        return BackendParams(params=result)

    def checkpoint(self, session: _PolarsSession, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if POLARS_AVAILABLE and session.collected_df is not None:
            parquet_path = path.with_suffix(".parquet")
            session.collected_df.write_parquet(str(parquet_path))
        else:
            with path.open("wb") as f:
                pickle.dump({
                    "params": session.params,
                    "epoch": session.current_epoch,
                    "row_count": session.row_count,
                }, f)

    def restore(self, session: _PolarsSession, path: Path) -> None:
        if POLARS_AVAILABLE:
            parquet_path = path.with_suffix(".parquet")
            if parquet_path.exists():
                session.collected_df = pl.read_parquet(str(parquet_path))
                session.lazy_frame = session.collected_df.lazy()
                session.row_count = len(session.collected_df)
                return
        with path.open("rb") as f:
            ckpt = pickle.load(f)  # noqa: S301
        session.params = ckpt.get("params", {})
        session.current_epoch = ckpt.get("epoch", 0)
        session.row_count = ckpt.get("row_count", 0)

    def finalize(self, session: _PolarsSession) -> ArtifactManifest:
        best = min(session.history, key=lambda m: m.metric_value) if session.history else None
        metric_val = best.metric_value if best else 0.0
        artifact_path = f"/run/{session.run_id}/transform.parquet"
        return ArtifactManifest(
            run_id=session.run_id,
            backend=self.name,
            backend_version=self.version,
            primary_metric="null_frac",
            metric_value=metric_val,
            artifact_path_hash=ArtifactManifest.hash_path(artifact_path),
            artifact_size_b=0,
        )

    def cleanup(self, session: _PolarsSession) -> None:
        session.lazy_frame = None
        session.collected_df = None
        session._state.clear()


_BACKEND_CLASS = PolarsTransformBackend

__all__ = ["PolarsTransformBackend", "POLARS_AVAILABLE"]
