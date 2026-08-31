"""scikit-learn ComputeBackend — partial_fit + joblib checkpoint (ADR-0026 §A).

Uses conditional import so the backend can be imported without scikit-learn
installed (tests work with mock data).

MUST NOT import anthropic / openai / google.cloud.aiplatform.
"""
from __future__ import annotations

import dataclasses
import logging
import pickle
import time
from pathlib import Path
from typing import Any, Optional

from ..protocol import (
    ArtifactManifest,
    BackendParams,
    BackendSession,
    ComputeBackend,
    DataCursor,
    EpochMetrics,
    JobSpec,
    SteeringVector,
)

log = logging.getLogger(__name__)

try:
    import sklearn  # type: ignore[import]
    from sklearn.linear_model import SGDClassifier, SGDRegressor  # type: ignore[import]
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


@dataclasses.dataclass
class _SklearnSession(BackendSession):
    """sklearn-specific session state."""
    model: Any = None
    X_chunks: list = dataclasses.field(default_factory=list)
    y_chunks: list = dataclasses.field(default_factory=list)


class SklearnBackend:
    """scikit-learn backend using partial_fit for incremental learning."""

    name: str = "sklearn"
    version: str = "1.0.0"
    supports_partial_fit: bool = True
    supports_checkpointing: bool = True
    supports_distributed: bool = False
    inter_job_compatible: bool = True

    # Steering map: abstract key → sklearn-native param name
    _STEERING_MAP: dict[str, str] = {
        "lr": "eta0",
        "alpha": "alpha",
        "max_iter": "max_iter",
        "tol": "tol",
    }

    def create_session(self, spec: JobSpec, cursor: DataCursor) -> _SklearnSession:
        session = _SklearnSession(
            run_id=spec.run_id,
            backend_name=self.name,
            shard_index=spec.shard_index,
            params=dict(spec.params),
        )
        if SKLEARN_AVAILABLE:
            model_type = spec.extra.get("model_type", "classifier")
            eta0 = float(spec.params.get("lr", spec.params.get("eta0", 0.01)))
            alpha = float(spec.params.get("alpha", 0.0001))
            if model_type == "regressor":
                session.model = SGDRegressor(eta0=eta0, alpha=alpha, max_iter=1)
            else:
                session.model = SGDClassifier(eta0=eta0, alpha=alpha, max_iter=1)
        else:
            # Stub model for testing without sklearn
            session.model = _StubModel(spec.params.copy())

        # Load data from cursor
        if cursor is not None:
            if isinstance(cursor, dict):
                session.X_chunks = [cursor.get("X", [])]
                session.y_chunks = [cursor.get("y", [])]
            elif hasattr(cursor, "__iter__"):
                session._state["cursor"] = cursor
        return session

    def train_epoch(self, session: _SklearnSession) -> EpochMetrics:
        t0 = time.monotonic()
        session.current_epoch += 1

        if SKLEARN_AVAILABLE and hasattr(session.model, "partial_fit"):
            for X, y in zip(session.X_chunks, session.y_chunks):
                if len(X) > 0:
                    try:
                        import numpy as np  # type: ignore[import]
                        X_arr = np.array(X, dtype=float)
                        y_arr = np.array(y)
                        classes = np.unique(y_arr)
                        # SGDClassifier requires classes on first call
                        if hasattr(session.model, "classes_"):
                            session.model.partial_fit(X_arr, y_arr)
                        else:
                            session.model.partial_fit(X_arr, y_arr, classes=classes)
                    except Exception:
                        # Fallback: try without numpy conversion
                        session.model.partial_fit(X, y)
            # Use loss as a proxy metric
            try:
                metric_value = float(getattr(session.model, "loss_", 0.5))
            except Exception:
                metric_value = 0.5
        else:
            # Stub: simulate decreasing loss
            metric_value = max(0.01, 1.0 / (session.current_epoch + 1))
            if hasattr(session.model, "train_epoch"):
                session.model.train_epoch()

        wall_ms = (time.monotonic() - t0) * 1000
        metrics = EpochMetrics(
            epoch=session.current_epoch,
            primary_metric="loss",
            metric_value=metric_value,
            wall_ms=wall_ms,
        )
        session.history.append(metrics)
        return metrics

    def translate_steering(self, vector: SteeringVector) -> BackendParams:
        """Map abstract Oracle steering to sklearn-native params."""
        result: dict[str, Any] = {}
        for abstract_key, directive in vector.vector.items():
            native_key = self._STEERING_MAP.get(abstract_key, abstract_key)
            current = self._get_current_param_value(native_key)
            result[native_key] = _apply_directive(current, directive)
        return BackendParams(params=result)

    def _get_current_param_value(self, key: str) -> Any:
        return 0.01  # default if unknown

    def checkpoint(self, session: _SklearnSession, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        ckpt = {
            "model": session.model,
            "params": session.params,
            "epoch": session.current_epoch,
        }
        if SKLEARN_AVAILABLE:
            try:
                import joblib  # type: ignore[import]
                joblib.dump(ckpt, path)
                return
            except ImportError:
                pass
        with path.open("wb") as f:
            pickle.dump(ckpt, f)

    def restore(self, session: _SklearnSession, path: Path) -> None:
        if SKLEARN_AVAILABLE:
            try:
                import joblib  # type: ignore[import]
                ckpt = joblib.load(path)
            except ImportError:
                with path.open("rb") as f:
                    ckpt = pickle.load(f)  # noqa: S301
        else:
            with path.open("rb") as f:
                ckpt = pickle.load(f)  # noqa: S301
        session.model = ckpt["model"]
        session.params = ckpt["params"]
        session.current_epoch = ckpt["epoch"]

    def finalize(self, session: _SklearnSession) -> ArtifactManifest:
        best = min(session.history, key=lambda m: m.metric_value) if session.history else None
        metric_val = best.metric_value if best else 0.0
        # Hash of a canonical artifact path (we don't store the path itself)
        artifact_path = f"/run/{session.run_id}/model.pkl"
        return ArtifactManifest(
            run_id=session.run_id,
            backend=self.name,
            backend_version=self.version,
            primary_metric="loss",
            metric_value=metric_val,
            artifact_path_hash=ArtifactManifest.hash_path(artifact_path),
            artifact_size_b=0,
        )

    def cleanup(self, session: _SklearnSession) -> None:
        session.model = None
        session.X_chunks.clear()
        session.y_chunks.clear()
        session._state.clear()


class _StubModel:
    """Minimal stub used when sklearn is not installed."""
    def __init__(self, params: dict) -> None:
        self.params = params
        self._epoch = 0

    def train_epoch(self) -> None:
        self._epoch += 1


def _apply_directive(current: Any, directive: str) -> Any:
    """Apply a steering directive string to a current parameter value.

    "↓0.3" → multiply by (1 - 0.3) = 0.7
    "↑0.3" → multiply by (1 + 0.3) = 1.3
    "↑1"   → add 1 (integer increment)
    """
    if not directive:
        return current
    direction = directive[0]
    try:
        magnitude_str = directive[1:]
        magnitude = float(magnitude_str)
    except (ValueError, IndexError):
        return current

    if current is None:
        return None
    try:
        if direction == "↓":
            if isinstance(current, int):
                return max(0, int(current * (1.0 - magnitude)))
            return current * (1.0 - magnitude)
        elif direction == "↑":
            if isinstance(current, int):
                return current + int(magnitude)
            return current * (1.0 + magnitude)
    except (TypeError, ValueError):
        return current
    return current


# Marker for BackendRegistry auto-discovery
_BACKEND_CLASS = SklearnBackend

__all__ = ["SklearnBackend", "SKLEARN_AVAILABLE", "_apply_directive"]
