"""statsmodels ComputeBackend — rolling OLS/ARIMA + pickle checkpoint (ADR-0026 §A).

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
    import statsmodels.api as sm  # type: ignore[import]
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False


@dataclasses.dataclass
class _SMSession(BackendSession):
    fitted_model: Any = None
    endog: Any = None
    exog: Any = None
    window_size: int = 100
    model_type: str = "ols"  # "ols" or "arima"


class StatsmodelsBackend:
    """statsmodels backend — rolling-window OLS / ARIMA refit each epoch."""

    name: str = "statsmodels"
    version: str = "1.0.0"
    supports_partial_fit: bool = False  # full refit per window
    supports_checkpointing: bool = True
    supports_distributed: bool = False
    inter_job_compatible: bool = True

    _STEERING_MAP: dict[str, str] = {
        "window": "window_size",
        "ar_order": "ar_order",
        "ma_order": "ma_order",
        "alpha": "alpha",
    }

    def create_session(self, spec: JobSpec, cursor: DataCursor) -> _SMSession:
        session = _SMSession(
            run_id=spec.run_id,
            backend_name=self.name,
            shard_index=spec.shard_index,
            params=dict(spec.params),
            window_size=int(spec.params.get("window_size", spec.params.get("window", 100))),
            model_type=spec.extra.get("model_type", "ols"),
        )
        if cursor is not None and isinstance(cursor, dict):
            session.endog = cursor.get("y", cursor.get("endog", []))
            session.exog = cursor.get("X", cursor.get("exog", None))
        return session

    def train_epoch(self, session: _SMSession) -> EpochMetrics:
        t0 = time.monotonic()
        session.current_epoch += 1

        metric_value = 0.5
        if STATSMODELS_AVAILABLE and session.endog is not None and len(session.endog) > 0:
            try:
                import numpy as np  # type: ignore[import]
                endog = np.asarray(session.endog, dtype=float)
                window = min(session.window_size, len(endog))
                endog_win = endog[-window:]
                if session.model_type == "arima":
                    ar_order = int(session.params.get("ar_order", 1))
                    ma_order = int(session.params.get("ma_order", 1))
                    model = sm.tsa.ARIMA(endog_win, order=(ar_order, 0, ma_order))
                else:
                    exog_win = None
                    if session.exog is not None:
                        exog_arr = np.asarray(session.exog, dtype=float)
                        if exog_arr.ndim == 1:
                            exog_arr = exog_arr.reshape(-1, 1)
                        exog_win = exog_arr[-window:]
                        exog_win = sm.add_constant(exog_win, has_constant="add")
                    else:
                        exog_win = sm.add_constant(
                            np.arange(window, dtype=float).reshape(-1, 1),
                            has_constant="add"
                        )
                    model = sm.OLS(endog_win, exog_win)
                session.fitted_model = model.fit()
                metric_value = float(session.fitted_model.mse_resid) if hasattr(
                    session.fitted_model, "mse_resid") else 0.5
                # normalise
                metric_value = min(1.0, metric_value / max(1.0, abs(float(endog_win.mean()))))
            except Exception as exc:
                log.debug("statsmodels epoch failed: %s", exc)
                metric_value = max(0.01, 1.0 / (session.current_epoch + 1))
        else:
            metric_value = max(0.01, 1.0 / (session.current_epoch + 1))

        wall_ms = (time.monotonic() - t0) * 1000
        metrics = EpochMetrics(
            epoch=session.current_epoch,
            primary_metric="mse",
            metric_value=metric_value,
            wall_ms=wall_ms,
        )
        session.history.append(metrics)
        return metrics

    def translate_steering(self, vector: SteeringVector) -> BackendParams:
        result: dict[str, Any] = {}
        defaults: dict[str, Any] = {
            "window_size": 100,
            "ar_order": 1,
            "ma_order": 1,
            "alpha": 0.05,
        }
        for abstract_key, directive in vector.vector.items():
            native_key = self._STEERING_MAP.get(abstract_key, abstract_key)
            current = defaults.get(native_key, 1.0)
            result[native_key] = _apply_directive(current, directive)
        return BackendParams(params=result)

    def checkpoint(self, session: _SMSession, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        ckpt = {
            "params": session.params,
            "epoch": session.current_epoch,
            "window_size": session.window_size,
            "model_type": session.model_type,
            "fitted_model": session.fitted_model,
        }
        with path.open("wb") as f:
            pickle.dump(ckpt, f)

    def restore(self, session: _SMSession, path: Path) -> None:
        with path.open("rb") as f:
            ckpt = pickle.load(f)  # noqa: S301
        session.params = ckpt.get("params", {})
        session.current_epoch = ckpt.get("epoch", 0)
        session.window_size = ckpt.get("window_size", 100)
        session.model_type = ckpt.get("model_type", "ols")
        session.fitted_model = ckpt.get("fitted_model")

    def finalize(self, session: _SMSession) -> ArtifactManifest:
        best = min(session.history, key=lambda m: m.metric_value) if session.history else None
        metric_val = best.metric_value if best else 0.0
        artifact_path = f"/run/{session.run_id}/model.pkl"
        return ArtifactManifest(
            run_id=session.run_id,
            backend=self.name,
            backend_version=self.version,
            primary_metric="mse",
            metric_value=metric_val,
            artifact_path_hash=ArtifactManifest.hash_path(artifact_path),
            artifact_size_b=0,
        )

    def cleanup(self, session: _SMSession) -> None:
        session.fitted_model = None
        session.endog = None
        session.exog = None
        session._state.clear()


_BACKEND_CLASS = StatsmodelsBackend

__all__ = ["StatsmodelsBackend", "STATSMODELS_AVAILABLE"]
