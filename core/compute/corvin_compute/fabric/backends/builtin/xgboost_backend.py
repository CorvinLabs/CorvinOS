"""XGBoost ComputeBackend — DMatrix streaming + native .ubj checkpoint (ADR-0026 §A).

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
    import xgboost as xgb  # type: ignore[import]
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False


@dataclasses.dataclass
class _XGBSession(BackendSession):
    booster: Any = None
    dtrain: Any = None


class XGBoostBackend:
    """XGBoost backend — DMatrix streaming, xgb_model=prev for continuation."""

    name: str = "xgboost"
    version: str = "1.0.0"
    supports_partial_fit: bool = True
    supports_checkpointing: bool = True
    supports_distributed: bool = False
    inter_job_compatible: bool = True

    _STEERING_MAP: dict[str, str] = {
        "lr": "learning_rate",
        "max_depth": "max_depth",
        "subsample": "subsample",
        "colsample": "colsample_bytree",
        "alpha": "reg_alpha",
        "lambda": "reg_lambda",
    }

    def create_session(self, spec: JobSpec, cursor: DataCursor) -> _XGBSession:
        session = _XGBSession(
            run_id=spec.run_id,
            backend_name=self.name,
            shard_index=spec.shard_index,
            params=dict(spec.params),
        )
        if XGBOOST_AVAILABLE and cursor is not None:
            X = cursor.get("X") if isinstance(cursor, dict) else None
            y = cursor.get("y") if isinstance(cursor, dict) else None
            if X is not None and y is not None:
                session.dtrain = xgb.DMatrix(X, label=y)
        session._state["num_round_per_epoch"] = spec.params.get("num_round", 1)
        return session

    def train_epoch(self, session: _XGBSession) -> EpochMetrics:
        t0 = time.monotonic()
        session.current_epoch += 1

        if XGBOOST_AVAILABLE and session.dtrain is not None:
            xgb_params = {
                self._STEERING_MAP.get(k, k): v
                for k, v in session.params.items()
            }
            xgb_params.setdefault("verbosity", 0)
            results: dict = {}
            session.booster = xgb.train(
                xgb_params,
                session.dtrain,
                num_boost_round=session._state.get("num_round_per_epoch", 1),
                xgb_model=session.booster,
                evals_result=results,
                verbose_eval=False,
            )
            # Extract loss
            try:
                eval_data = list(results.values())
                metric_value = float(list(eval_data[0].values())[0][-1]) if eval_data else 0.5
            except (IndexError, KeyError, TypeError):
                metric_value = 0.5
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
        for abstract_key, directive in vector.vector.items():
            native_key = self._STEERING_MAP.get(abstract_key, abstract_key)
            current = self._current_value(native_key, abstract_key)
            result[native_key] = _apply_directive(current, directive)
        return BackendParams(params=result)

    def _current_value(self, native_key: str, abstract_key: str) -> Any:
        defaults: dict[str, Any] = {
            "learning_rate": 0.1,
            "max_depth": 6,
            "subsample": 1.0,
            "colsample_bytree": 1.0,
            "reg_alpha": 0.0,
            "reg_lambda": 1.0,
        }
        return defaults.get(native_key, 0.1)

    def checkpoint(self, session: _XGBSession, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if XGBOOST_AVAILABLE and session.booster is not None:
            ubj_path = path.with_suffix(".ubj")
            session.booster.save_model(str(ubj_path))
        else:
            with path.open("wb") as f:
                pickle.dump({"params": session.params, "epoch": session.current_epoch}, f)

    def restore(self, session: _XGBSession, path: Path) -> None:
        if XGBOOST_AVAILABLE:
            ubj_path = path.with_suffix(".ubj")
            if ubj_path.exists():
                session.booster = xgb.Booster()
                session.booster.load_model(str(ubj_path))
                return
        with path.open("rb") as f:
            ckpt = pickle.load(f)  # noqa: S301
        session.params = ckpt.get("params", {})
        session.current_epoch = ckpt.get("epoch", 0)

    def finalize(self, session: _XGBSession) -> ArtifactManifest:
        best = min(session.history, key=lambda m: m.metric_value) if session.history else None
        metric_val = best.metric_value if best else 0.0
        artifact_path = f"/run/{session.run_id}/model.ubj"
        return ArtifactManifest(
            run_id=session.run_id,
            backend=self.name,
            backend_version=self.version,
            primary_metric="loss",
            metric_value=metric_val,
            artifact_path_hash=ArtifactManifest.hash_path(artifact_path),
            artifact_size_b=0,
        )

    def cleanup(self, session: _XGBSession) -> None:
        session.booster = None
        session.dtrain = None
        session._state.clear()


_BACKEND_CLASS = XGBoostBackend

__all__ = ["XGBoostBackend", "XGBOOST_AVAILABLE"]
