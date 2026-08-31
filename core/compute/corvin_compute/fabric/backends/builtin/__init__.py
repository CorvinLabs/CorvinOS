"""Built-in compute backends bundled with Corvin (ADR-0026 §A)."""
from __future__ import annotations

from .sklearn_backend import SklearnBackend, SKLEARN_AVAILABLE
from .xgboost_backend import XGBoostBackend, XGBOOST_AVAILABLE
from .lightgbm_backend import LightGBMBackend, LIGHTGBM_AVAILABLE
from .statsmodels_backend import StatsmodelsBackend, STATSMODELS_AVAILABLE
from .polars_transform_backend import PolarsTransformBackend, POLARS_AVAILABLE

__all__ = [
    "SklearnBackend",
    "SKLEARN_AVAILABLE",
    "XGBoostBackend",
    "XGBOOST_AVAILABLE",
    "LightGBMBackend",
    "LIGHTGBM_AVAILABLE",
    "StatsmodelsBackend",
    "STATSMODELS_AVAILABLE",
    "PolarsTransformBackend",
    "POLARS_AVAILABLE",
]
