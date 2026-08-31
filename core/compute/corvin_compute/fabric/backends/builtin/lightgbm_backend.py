"""LightGBM ComputeBackend — init_model= incremental boosting + native .txt (ADR-0026 §A).

MUST NOT import anthropic / openai / google.cloud.aiplatform.
"""
from __future__ import annotations

import dataclasses
import logging
import pickle
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
    import lightgbm as lgb  # type: ignore[import]
    LIGHTGBM_AVAILABLE = True
except ImportError:
    LIGHTGBM_AVAILABLE = False


@dataclasses.dataclass
class _LGBSession(BackendSession):
    booster: Any = None
    dataset: Any = None


class LightGBMBackend:
    """LightGBM backend — init_model= parameter for incremental boosting."""

    name: str = "lightgbm"
    version: str = "1.0.0"
    supports_partial_fit: bool = True
    supports_checkpointing: bool = True
    supports_distributed: bool = False
    inter_job_compatible: bool = True

    _STEERING_MAP: dict[str, str] = {
        "lr": "learning_rate",
        "max_depth": "max_depth",
        "num_leaves": "num_leaves",
        "subsample": "bagging_fraction",
        "colsample": "feature_fraction",
        "alpha": "reg_alpha",
        "lambda": "reg_lambda",
        "min_child": "min_child_samples",
    }

    def create_session(self, spec: JobSpec, cursor: DataCursor) -> _LGBSession:
        session = _LGBSession(
            run_id=spec.run_id,
            backend_name=self.name,
            shard_index=spec.shard_index,
            params=dict(spec.params),
        )
        if LIGHTGBM_AVAILABLE and cursor is not None and isinstance(cursor, dict):
            X = cursor.get("X")
            y = cursor.get("y")
            if X is not None and y is not None:
                session.dataset = lgb.Dataset(X, label=y)
        session._state["num_round_per_epoch"] = spec.params.get("num_round", 1)
        return session

    def train_epoch(self, session: _LGBSession) -> EpochMetrics:
        t0 = time.monotonic()
        session.current_epoch += 1

        if LIGHTGBM_AVAILABLE and session.dataset is not None:
            lgb_params = {
                self._STEERING_MAP.get(k, k): v
                for k, v in session.params.items()
                if k not in ("num_round",)
            }
            lgb_params.setdefault("verbosity", -1)
            callbacks = [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)]
            session.booster = lgb.train(
                lgb_params,
                session.dataset,
                num_boost_round=session._state.get("num_round_per_epoch", 1),
                init_model=session.booster,
                callbacks=callbacks,
            )
            metric_value = min(session.booster.best_score.get("training", {}).values(),
                               default=[0.5])[0] if hasattr(session.booster, "best_score") else 0.5
        else:
            metric_value = max(0.01, 1.0 / (session.current_epoch + 1))

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
        result: dict[str, Any] = {}
        defaults: dict[str, Any] = {
            "learning_rate": 0.1,
            "max_depth": -1,
            "num_leaves": 31,
            "bagging_fraction": 1.0,
            "feature_fraction": 1.0,
            "reg_alpha": 0.0,
            "reg_lambda": 0.0,
            "min_child_samples": 20,
        }
        for abstract_key, directive in vector.vector.items():
            native_key = self._STEERING_MAP.get(abstract_key, abstract_key)
            current = defaults.get(native_key, 0.1)
            result[native_key] = _apply_directive(current, directive)
        return BackendParams(params=result)

    def checkpoint(self, session: _LGBSession, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if LIGHTGBM_AVAILABLE and session.booster is not None:
            txt_path = path.with_suffix(".txt")
            session.booster.save_model(str(txt_path))
        else:
            with path.open("wb") as f:
                pickle.dump({"params": session.params, "epoch": session.current_epoch}, f)

    def restore(self, session: _LGBSession, path: Path) -> None:
        if LIGHTGBM_AVAILABLE:
            txt_path = path.with_suffix(".txt")
            if txt_path.exists():
                session.booster = lgb.Booster(model_file=str(txt_path))
                return
        with path.open("rb") as f:
            ckpt = pickle.load(f)  # noqa: S301
        session.params = ckpt.get("params", {})
        session.current_epoch = ckpt.get("epoch", 0)

    def finalize(self, session: _LGBSession) -> ArtifactManifest:
        best = min(session.history, key=lambda m: m.metric_value) if session.history else None
        metric_val = best.metric_value if best else 0.0
        artifact_path = f"/run/{session.run_id}/model.txt"
        return ArtifactManifest(
            run_id=session.run_id,
            backend=self.name,
            backend_version=self.version,
            primary_metric="loss",
            metric_value=metric_val,
            artifact_path_hash=ArtifactManifest.hash_path(artifact_path),
            artifact_size_b=0,
        )

    def cleanup(self, session: _LGBSession) -> None:
        session.booster = None
        session.dataset = None
        session._state.clear()


_BACKEND_CLASS = LightGBMBackend

__all__ = ["LightGBMBackend", "LIGHTGBM_AVAILABLE"]
