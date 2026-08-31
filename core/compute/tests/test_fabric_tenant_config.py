"""ADR-0026 — FabricConfig Pydantic model tests.

Tests the spec.compute tenant config extension introduced by ADR-0026.
extra='forbid' strictness, field range validation, and default values.

Run:
    python -m pytest core/compute/tests/test_fabric_tenant_config.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from pydantic import ValidationError  # noqa: E402

from corvin_compute.fabric_config import (  # noqa: E402
    FabricConfig,
    ParallelInterJob,
    ParallelIntraBackend,
    PluginParallel,
)


# ---------------------------------------------------------------------------
# FabricConfig default values
# ---------------------------------------------------------------------------

class TestFabricConfigDefaults:
    def test_fabric_enabled_defaults_false(self) -> None:
        cfg = FabricConfig()
        assert cfg.fabric_enabled is False

    def test_oracle_enabled_defaults_false(self) -> None:
        cfg = FabricConfig()
        assert cfg.oracle_enabled is False

    def test_datasource_enabled_defaults_false(self) -> None:
        cfg = FabricConfig()
        assert cfg.datasource_enabled is False

    def test_enabled_defaults_true(self) -> None:
        """ADR-0013 master switch is on by default (compute enabled for all tenants)."""
        cfg = FabricConfig()
        assert cfg.enabled is True

    def test_datasource_residency_strict_defaults_true(self) -> None:
        cfg = FabricConfig()
        assert cfg.datasource_residency_strict is True

    def test_allow_network_plugins_defaults_false(self) -> None:
        cfg = FabricConfig()
        assert cfg.allow_network_plugins is False

    def test_datasource_allow_network_adapters_defaults_false(self) -> None:
        cfg = FabricConfig()
        assert cfg.datasource_allow_network_adapters is False

    def test_negotiation_enabled_defaults_true(self) -> None:
        cfg = FabricConfig()
        assert cfg.negotiation_enabled is True

    def test_datasource_incremental_enabled_defaults_true(self) -> None:
        cfg = FabricConfig()
        assert cfg.datasource_incremental_enabled is True

    def test_oracle_model_defaults_none(self) -> None:
        cfg = FabricConfig()
        assert cfg.oracle_model is None


# ---------------------------------------------------------------------------
# Explicit field setting
# ---------------------------------------------------------------------------

class TestFabricConfigExplicit:
    def test_fabric_enabled_true(self) -> None:
        cfg = FabricConfig(fabric_enabled=True)
        assert cfg.fabric_enabled is True

    def test_oracle_enabled_with_fabric_enabled(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, oracle_enabled=True)
        assert cfg.oracle_enabled is True

    def test_datasource_enabled_with_fabric_enabled(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, datasource_enabled=True)
        assert cfg.datasource_enabled is True


# ---------------------------------------------------------------------------
# extra='forbid' strictness (ADR-0007 Phase 3.1)
# ---------------------------------------------------------------------------

class TestFabricConfigExtraForbid:
    def test_extra_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            FabricConfig(extra_unknown_field=True)  # type: ignore[call-arg]

    def test_model_config_extra_forbid(self) -> None:
        assert FabricConfig.model_config["extra"] == "forbid"

    def test_extra_misspelled_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            FabricConfig(fabrik_enabled=True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Field range validation
# ---------------------------------------------------------------------------

class TestFabricConfigRanges:
    def test_max_parallel_workers_min_1(self) -> None:
        with pytest.raises(ValidationError):
            FabricConfig(max_parallel_workers=0)

    def test_max_parallel_workers_max_32(self) -> None:
        with pytest.raises(ValidationError):
            FabricConfig(max_parallel_workers=33)

    def test_max_parallel_workers_boundary_1(self) -> None:
        cfg = FabricConfig(max_parallel_workers=1)
        assert cfg.max_parallel_workers == 1

    def test_max_parallel_workers_boundary_32(self) -> None:
        cfg = FabricConfig(max_parallel_workers=32)
        assert cfg.max_parallel_workers == 32

    def test_max_artifact_size_mb_min_10(self) -> None:
        with pytest.raises(ValidationError):
            FabricConfig(max_artifact_size_mb=9)

    def test_max_artifact_size_mb_max_10000(self) -> None:
        with pytest.raises(ValidationError):
            FabricConfig(max_artifact_size_mb=10001)

    def test_max_artifact_size_mb_boundary_10(self) -> None:
        cfg = FabricConfig(max_artifact_size_mb=10)
        assert cfg.max_artifact_size_mb == 10

    def test_max_artifact_size_mb_boundary_10000(self) -> None:
        cfg = FabricConfig(max_artifact_size_mb=10000)
        assert cfg.max_artifact_size_mb == 10000

    def test_datasource_max_row_estimate_min_0(self) -> None:
        with pytest.raises(ValidationError):
            FabricConfig(datasource_max_row_estimate=-1)

    def test_datasource_max_row_estimate_zero_ok(self) -> None:
        cfg = FabricConfig(datasource_max_row_estimate=0)
        assert cfg.datasource_max_row_estimate == 0


# ---------------------------------------------------------------------------
# Structural: backend_denylist wins over backend_allowlist (documented)
# ---------------------------------------------------------------------------

class TestFabricConfigBackendPolicy:
    def test_backend_denylist_and_allowlist_coexist(self) -> None:
        """FabricConfig stores both; the enforcement is in BackendRegistry,
        but the model must accept both fields simultaneously."""
        cfg = FabricConfig(
            backend_allowlist=["sklearn", "xgboost"],
            backend_denylist=["xgboost"],
        )
        assert "sklearn" in cfg.backend_allowlist
        assert "xgboost" in cfg.backend_denylist
        # Structural test: denylist wins is documented; both present is valid.
        assert "xgboost" in cfg.backend_denylist

    def test_empty_backend_lists_default(self) -> None:
        cfg = FabricConfig()
        assert cfg.backend_allowlist == []
        assert cfg.backend_denylist == []


# ---------------------------------------------------------------------------
# Sub-model tests
# ---------------------------------------------------------------------------

class TestParallelSubModels:
    def test_parallel_intra_backend_defaults(self) -> None:
        m = ParallelIntraBackend()
        assert m.model == "thread"
        assert m.max_workers == "auto"
        assert m.gpu_aware is False

    def test_parallel_inter_job_defaults(self) -> None:
        m = ParallelInterJob()
        assert m.compatible is True
        assert m.max_concurrent_instances == 8

    def test_parallel_inter_job_max_concurrent_instances_range(self) -> None:
        with pytest.raises(ValidationError):
            ParallelInterJob(max_concurrent_instances=0)
        with pytest.raises(ValidationError):
            ParallelInterJob(max_concurrent_instances=65)

    def test_plugin_parallel_defaults(self) -> None:
        pp = PluginParallel()
        assert isinstance(pp.intra_backend, ParallelIntraBackend)
        assert isinstance(pp.inter_job, ParallelInterJob)
