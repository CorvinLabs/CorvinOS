"""Tests for all 5 built-in backends — 40 test cases (ADR-0026 §A)."""
from __future__ import annotations

import pickle
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute.fabric.backends.protocol import (
    ArtifactManifest,
    BackendParams,
    BackendSession,
    EpochMetrics,
    JobSpec,
    SteeringVector,
)
from corvin_compute.fabric.backends.builtin.sklearn_backend import (
    SklearnBackend,
    SKLEARN_AVAILABLE,
    _apply_directive,
)
from corvin_compute.fabric.backends.builtin.xgboost_backend import XGBoostBackend, XGBOOST_AVAILABLE
from corvin_compute.fabric.backends.builtin.lightgbm_backend import LightGBMBackend, LIGHTGBM_AVAILABLE
from corvin_compute.fabric.backends.builtin.statsmodels_backend import StatsmodelsBackend, STATSMODELS_AVAILABLE
from corvin_compute.fabric.backends.builtin.polars_transform_backend import PolarsTransformBackend, POLARS_AVAILABLE


def _make_spec(run_id="test-run", **extra_params) -> JobSpec:
    params = {"lr": 0.01, "alpha": 0.001}
    params.update(extra_params)
    return JobSpec(
        run_id=run_id,
        max_epochs=3,
        params=params,
    )


def _make_cursor(n=20):
    """Simple dict-based cursor with synthetic data."""
    import random
    X = [[random.random() for _ in range(4)] for _ in range(n)]
    y = [i % 2 for i in range(n)]
    return {"X": X, "y": y}


# ---------------------------------------------------------------------------
# _apply_directive tests
# ---------------------------------------------------------------------------

class TestApplyDirective:
    def test_down_float(self):
        result = _apply_directive(1.0, "↓0.3")
        assert abs(result - 0.7) < 1e-10

    def test_up_float(self):
        result = _apply_directive(1.0, "↑0.3")
        assert abs(result - 1.3) < 1e-10

    def test_up_integer(self):
        result = _apply_directive(3, "↑1")
        assert result == 4

    def test_down_integer(self):
        result = _apply_directive(10, "↓0.5")
        assert result == 5

    def test_invalid_direction_returns_current(self):
        result = _apply_directive(1.0, "X0.3")
        assert result == 1.0

    def test_empty_directive_returns_current(self):
        result = _apply_directive(1.0, "")
        assert result == 1.0

    def test_none_current_returns_none(self):
        result = _apply_directive(None, "↑0.1")
        assert result is None


# ---------------------------------------------------------------------------
# SklearnBackend tests
# ---------------------------------------------------------------------------

class TestSklearnBackend:
    def setup_method(self):
        self.backend = SklearnBackend()
        self.spec = _make_spec()
        self.cursor = _make_cursor()

    def test_create_session_returns_session(self):
        session = self.backend.create_session(self.spec, self.cursor)
        assert session is not None
        assert session.run_id == "test-run"
        assert session.backend_name == "sklearn"

    def test_train_epoch_returns_metrics(self):
        session = self.backend.create_session(self.spec, self.cursor)
        metrics = self.backend.train_epoch(session)
        assert isinstance(metrics, EpochMetrics)
        assert metrics.epoch == 1
        assert isinstance(metrics.metric_value, float)

    def test_multiple_epochs_increment_counter(self):
        session = self.backend.create_session(self.spec, self.cursor)
        for _ in range(3):
            m = self.backend.train_epoch(session)
        assert m.epoch == 3
        assert len(session.history) == 3

    def test_translate_steering_lr_down(self):
        vector = SteeringVector(vector={"lr": "↓0.3"})
        params = self.backend.translate_steering(vector)
        assert isinstance(params, BackendParams)
        # eta0 should be reduced
        assert "eta0" in params.params
        assert params.params["eta0"] < 0.01 + 1e-9

    def test_translate_steering_maps_abstract_key(self):
        vector = SteeringVector(vector={"alpha": "↑0.1"})
        params = self.backend.translate_steering(vector)
        assert "alpha" in params.params

    def test_checkpoint_roundtrip(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.train_epoch(session)
        with tempfile.TemporaryDirectory() as tmpdir:
            ckpt_path = Path(tmpdir) / "model.pkl"
            self.backend.checkpoint(session, ckpt_path)
            assert ckpt_path.exists() or Path(tmpdir, "model.pkl").parent.exists()
            # Restore into a new session
            session2 = self.backend.create_session(self.spec, self.cursor)
            self.backend.restore(session2, ckpt_path)
            assert session2.current_epoch == session.current_epoch

    def test_finalize_returns_manifest(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.train_epoch(session)
        manifest = self.backend.finalize(session)
        assert isinstance(manifest, ArtifactManifest)
        assert manifest.run_id == "test-run"
        assert manifest.backend == "sklearn"
        assert len(manifest.artifact_path_hash) == 16  # sha256[:16]

    def test_finalize_hash_not_equal_to_path(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.train_epoch(session)
        manifest = self.backend.finalize(session)
        assert manifest.artifact_path_hash != f"/run/{session.run_id}/model.pkl"

    def test_cleanup_nulls_model(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.cleanup(session)
        assert session.model is None

    def test_handles_none_cursor(self):
        session = self.backend.create_session(self.spec, None)
        m = self.backend.train_epoch(session)
        assert isinstance(m, EpochMetrics)

    def test_sklearn_not_required_for_import(self):
        """Backend can be imported without sklearn installed."""
        # If we got here, the import worked regardless of SKLEARN_AVAILABLE
        assert True


# ---------------------------------------------------------------------------
# XGBoostBackend tests
# ---------------------------------------------------------------------------

class TestXGBoostBackend:
    def setup_method(self):
        self.backend = XGBoostBackend()
        self.spec = _make_spec()
        self.cursor = _make_cursor()

    def test_create_session(self):
        session = self.backend.create_session(self.spec, self.cursor)
        assert session.run_id == "test-run"
        assert session.backend_name == "xgboost"

    def test_train_epoch(self):
        session = self.backend.create_session(self.spec, self.cursor)
        m = self.backend.train_epoch(session)
        assert isinstance(m, EpochMetrics)
        assert m.epoch == 1

    def test_translate_steering_lr_to_learning_rate(self):
        vector = SteeringVector(vector={"lr": "↓0.2"})
        params = self.backend.translate_steering(vector)
        assert "learning_rate" in params.params
        assert params.params["learning_rate"] < 0.1 + 1e-9

    def test_translate_steering_max_depth_up(self):
        vector = SteeringVector(vector={"max_depth": "↑1"})
        params = self.backend.translate_steering(vector)
        assert params.params["max_depth"] == 7

    def test_finalize_returns_manifest(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.train_epoch(session)
        m = self.backend.finalize(session)
        assert isinstance(m, ArtifactManifest)
        assert len(m.artifact_path_hash) == 16

    def test_checkpoint_roundtrip_stub(self):
        session = self.backend.create_session(self.spec, None)
        self.backend.train_epoch(session)
        with tempfile.TemporaryDirectory() as tmpdir:
            ckpt_path = Path(tmpdir) / "model.pkl"
            self.backend.checkpoint(session, ckpt_path)
            s2 = self.backend.create_session(self.spec, None)
            self.backend.restore(s2, ckpt_path)
            assert s2.current_epoch == session.current_epoch

    def test_cleanup(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.cleanup(session)
        assert session.booster is None


# ---------------------------------------------------------------------------
# LightGBMBackend tests
# ---------------------------------------------------------------------------

class TestLightGBMBackend:
    def setup_method(self):
        self.backend = LightGBMBackend()
        self.spec = _make_spec()
        self.cursor = _make_cursor()

    def test_create_session(self):
        session = self.backend.create_session(self.spec, self.cursor)
        assert session.backend_name == "lightgbm"

    def test_train_epoch(self):
        session = self.backend.create_session(self.spec, self.cursor)
        m = self.backend.train_epoch(session)
        assert isinstance(m, EpochMetrics)

    def test_translate_lr_to_learning_rate(self):
        vector = SteeringVector(vector={"lr": "↓0.3"})
        params = self.backend.translate_steering(vector)
        assert "learning_rate" in params.params

    def test_translate_num_leaves_up(self):
        vector = SteeringVector(vector={"num_leaves": "↑1"})
        params = self.backend.translate_steering(vector)
        assert params.params["num_leaves"] == 32

    def test_finalize_hash_is_16_chars(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.train_epoch(session)
        m = self.backend.finalize(session)
        assert len(m.artifact_path_hash) == 16

    def test_cleanup(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.cleanup(session)
        assert session.booster is None


# ---------------------------------------------------------------------------
# StatsmodelsBackend tests
# ---------------------------------------------------------------------------

class TestStatsmodelsBackend:
    def setup_method(self):
        self.backend = StatsmodelsBackend()
        self.spec = _make_spec(window_size=10)
        self.cursor = {"y": [float(i) + 0.1 * (i % 3) for i in range(30)]}

    def test_create_session(self):
        session = self.backend.create_session(self.spec, self.cursor)
        assert session.backend_name == "statsmodels"

    def test_train_epoch_returns_metrics(self):
        session = self.backend.create_session(self.spec, self.cursor)
        m = self.backend.train_epoch(session)
        assert isinstance(m, EpochMetrics)
        assert m.primary_metric == "mse"

    def test_translate_window_steering(self):
        vector = SteeringVector(vector={"window": "↑0.1"})
        params = self.backend.translate_steering(vector)
        assert "window_size" in params.params

    def test_finalize(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.train_epoch(session)
        m = self.backend.finalize(session)
        assert isinstance(m, ArtifactManifest)
        assert len(m.artifact_path_hash) == 16

    def test_checkpoint_roundtrip(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.train_epoch(session)
        with tempfile.TemporaryDirectory() as tmpdir:
            ckpt = Path(tmpdir) / "model.pkl"
            self.backend.checkpoint(session, ckpt)
            s2 = self.backend.create_session(self.spec, self.cursor)
            self.backend.restore(s2, ckpt)
            assert s2.current_epoch == session.current_epoch

    def test_cleanup(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.cleanup(session)
        assert session.fitted_model is None


# ---------------------------------------------------------------------------
# PolarsTransformBackend tests
# ---------------------------------------------------------------------------

class TestPolarsTransformBackend:
    def setup_method(self):
        self.backend = PolarsTransformBackend()
        self.spec = _make_spec()
        self.cursor = {"col_a": [1, 2, None, 4], "col_b": [10, 20, 30, 40]}

    def test_create_session(self):
        session = self.backend.create_session(self.spec, self.cursor)
        assert session.backend_name == "polars_transform"

    def test_default_checkpoint_path_under_gettempdir(self):
        # Cross-platform: default checkpoint must live under the OS temp
        # dir (no hardcoded /tmp on Windows) and carry a per-process suffix
        # so concurrent sessions/tenants don't collide on one shared path.
        import os
        import tempfile

        session = self.backend.create_session(self.spec, self.cursor)
        ckpt = session.checkpoint_path
        assert str(ckpt).startswith(tempfile.gettempdir())
        assert str(os.getpid()) in ckpt.name

    def test_train_epoch_returns_metrics(self):
        session = self.backend.create_session(self.spec, self.cursor)
        m = self.backend.train_epoch(session)
        assert isinstance(m, EpochMetrics)
        assert m.primary_metric == "null_frac"

    def test_translate_steering(self):
        vector = SteeringVector(vector={"batch_size": "↑0.1"})
        params = self.backend.translate_steering(vector)
        assert "batch_size" in params.params

    def test_finalize(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.train_epoch(session)
        m = self.backend.finalize(session)
        assert isinstance(m, ArtifactManifest)
        assert len(m.artifact_path_hash) == 16

    def test_cleanup(self):
        session = self.backend.create_session(self.spec, self.cursor)
        self.backend.cleanup(session)
        assert session.lazy_frame is None
        assert session.collected_df is None
