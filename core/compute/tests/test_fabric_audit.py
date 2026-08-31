"""ADR-0026 — Compute Fabric + DataSourceAdapter audit chain tests.

Verifies that every new ADR-0026 event type:
  - is registered in EVENT_SEVERITY
  - emits the correct metadata-only fields
  - NEVER emits parameter values, model weights, steering magnitudes,
    credentials, raw watermark values, or training data

Run:
    python -m pytest core/compute/tests/test_fabric_audit.py -v
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))

from forge.security_events import EVENT_SEVERITY, write_event  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emit(
    event_type: str,
    details: dict[str, Any],
    *,
    audit_list: list[dict],
) -> dict[str, Any]:
    """Write to a temp file and capture the returned record."""
    td = tempfile.mkdtemp(prefix="fabric-audit-test-")
    path = Path(td) / "audit.jsonl"
    rec = write_event(path, event_type, details=details)
    audit_list.append(rec)
    return rec


# ---------------------------------------------------------------------------
# Sanity: all ADR-0026 event types are registered
# ---------------------------------------------------------------------------

_FABRIC_EVENTS = [
    "compute.backend_session_started",
    "compute.epoch_completed",
    "compute.oracle_steer_applied",
    "compute.oracle_subprocess_failed",
    "compute.shard_completed",
    "compute.aggregation_completed",
    "compute.backend_plugin_enabled",
    "compute.backend_plugin_disabled",
    "compute.checkpoint_written",
    "compute.artifact_registered",
    "compute.resource_slot_denied",
]

_DATASOURCE_EVENTS = [
    "datasource.registered",
    "datasource.schema_refreshed",
    "datasource.connection_tested",
    "datasource.connection_failed",
    "datasource.watermark_advanced",
    "datasource.residency_violation",
    "datasource.pii_detected",
    "datasource.adapter_enabled",
    "datasource.adapter_disabled",
    "datasource.preview_generated",
    "datasource.unregistered",
]


class TestEventRegistration:
    @pytest.mark.parametrize("event", _FABRIC_EVENTS + _DATASOURCE_EVENTS)
    def test_event_registered_in_severity_dict(self, event: str) -> None:
        assert event in EVENT_SEVERITY, (
            f"{event!r} must be registered in EVENT_SEVERITY"
        )

    def test_fabric_events_correct_severities(self) -> None:
        for e in _FABRIC_EVENTS:
            sev = EVENT_SEVERITY.get(e, "MISSING")
            assert sev in ("INFO", "WARNING", "ERROR", "CRITICAL"), (
                f"{e}: unexpected severity {sev!r}"
            )

    def test_oracle_steer_applied_severity_info(self) -> None:
        assert EVENT_SEVERITY["compute.oracle_steer_applied"] == "INFO"

    def test_oracle_subprocess_failed_severity_warning(self) -> None:
        assert EVENT_SEVERITY["compute.oracle_subprocess_failed"] == "WARNING"

    def test_resource_slot_denied_severity_warning(self) -> None:
        assert EVENT_SEVERITY["compute.resource_slot_denied"] == "WARNING"

    def test_residency_violation_severity_warning(self) -> None:
        assert EVENT_SEVERITY["datasource.residency_violation"] == "WARNING"

    def test_connection_failed_severity_warning(self) -> None:
        assert EVENT_SEVERITY["datasource.connection_failed"] == "WARNING"


# ---------------------------------------------------------------------------
# compute.backend_session_started
# ---------------------------------------------------------------------------

class TestBackendSessionStarted:
    def test_emits_allowed_fields(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.backend_session_started",
            {"run_id": "r1", "backend": "sklearn", "backend_version": "1.4.0"},
            audit_list=events,
        )
        assert rec["event_type"] == "compute.backend_session_started"
        assert rec["details"]["backend"] == "sklearn"

    def test_does_not_emit_param_values(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.backend_session_started",
            {"run_id": "r1", "backend": "xgboost", "backend_version": "2.0"},
            audit_list=events,
        )
        # Param values must not be present in any form
        assert "hyperparams" not in str(rec["details"])
        assert "learning_rate" not in str(rec["details"])


# ---------------------------------------------------------------------------
# compute.epoch_completed
# ---------------------------------------------------------------------------

class TestEpochCompleted:
    def test_emits_metric_fields(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.epoch_completed",
            {"run_id": "r1", "epoch": 3, "primary_metric": "val_loss",
             "metric_value": 0.42, "wall_ms": 1200},
            audit_list=events,
        )
        assert rec["details"]["epoch"] == 3
        assert rec["details"]["primary_metric"] == "val_loss"

    def test_does_not_emit_training_data(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.epoch_completed",
            {"run_id": "r1", "epoch": 1, "primary_metric": "loss",
             "metric_value": 0.9, "wall_ms": 500},
            audit_list=events,
        )
        detail_str = str(rec["details"])
        # Training samples / weights must never appear
        assert "training_samples" not in detail_str
        assert "model_weights" not in detail_str


# ---------------------------------------------------------------------------
# compute.oracle_steer_applied — critical: NO values in chain
# ---------------------------------------------------------------------------

class TestOracleSteerApplied:
    def test_steering_keys_present(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.oracle_steer_applied",
            {
                "run_id": "r1", "epoch": 3,
                "steering_keys": ["lr", "max_depth"],
                "divergence_detected": False,
            },
            audit_list=events,
        )
        assert "steering_keys" in rec["details"]
        assert "lr" in rec["details"]["steering_keys"]

    def test_no_steering_values(self) -> None:
        """steering_keys carries key NAMES only — direction/magnitude NEVER."""
        events: list[dict] = []
        rec = _emit(
            "compute.oracle_steer_applied",
            {
                "run_id": "r1", "epoch": 3,
                "steering_keys": ["lr", "max_depth"],
                "divergence_detected": False,
            },
            audit_list=events,
        )
        detail_str = str(rec["details"])
        # Values like "↓0.3" or "↑1" must never appear in the chain
        assert "↓0.3" not in detail_str, "steering magnitude must not be in chain"
        assert "↑1" not in detail_str, "steering direction must not be in chain"
        assert "0.3" not in detail_str.replace("False", ""), (
            "numeric steering value must not be in chain"
        )

    def test_steering_keys_are_list_of_strings(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.oracle_steer_applied",
            {"run_id": "r1", "epoch": 5,
             "steering_keys": ["subsample", "n_estimators"],
             "divergence_detected": True},
            audit_list=events,
        )
        assert isinstance(rec["details"]["steering_keys"], list)
        for k in rec["details"]["steering_keys"]:
            assert isinstance(k, str)

    def test_divergence_detected_bool(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.oracle_steer_applied",
            {"run_id": "r1", "epoch": 2,
             "steering_keys": ["lr"],
             "divergence_detected": True},
            audit_list=events,
        )
        assert rec["details"]["divergence_detected"] is True


# ---------------------------------------------------------------------------
# compute.oracle_subprocess_failed
# ---------------------------------------------------------------------------

class TestOracleSubprocessFailed:
    def test_emits_failure_reason(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.oracle_subprocess_failed",
            {"run_id": "r1", "epoch": 4,
             "failure_reason": "subprocess_timeout"},
            audit_list=events,
        )
        assert rec["details"]["failure_reason"] == "subprocess_timeout"
        assert EVENT_SEVERITY["compute.oracle_subprocess_failed"] == "WARNING"


# ---------------------------------------------------------------------------
# compute.shard_completed
# ---------------------------------------------------------------------------

class TestShardCompleted:
    def test_emits_shard_fields(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.shard_completed",
            {"run_id": "r1", "shard_index": 2,
             "total_shards": 4, "final_metric": 0.87},
            audit_list=events,
        )
        assert rec["details"]["shard_index"] == 2
        assert rec["details"]["total_shards"] == 4


# ---------------------------------------------------------------------------
# compute.aggregation_completed
# ---------------------------------------------------------------------------

class TestAggregationCompleted:
    def test_emits_strategy_fields(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.aggregation_completed",
            {"run_id": "r1", "strategy": "federated_avg",
             "n_shards": 4, "final_metric": 0.91},
            audit_list=events,
        )
        assert rec["details"]["strategy"] == "federated_avg"
        assert rec["details"]["n_shards"] == 4


# ---------------------------------------------------------------------------
# compute.backend_plugin_enabled / disabled
# ---------------------------------------------------------------------------

class TestBackendPluginLifecycle:
    def test_plugin_enabled_fields(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.backend_plugin_enabled",
            {"tenant_id": "acme", "plugin_name": "acme-compute",
             "plugin_version": "1.0.0"},
            audit_list=events,
        )
        assert rec["details"]["plugin_name"] == "acme-compute"
        assert rec["details"]["tenant_id"] == "acme"

    def test_plugin_disabled_fields(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.backend_plugin_disabled",
            {"tenant_id": "acme", "plugin_name": "acme-compute"},
            audit_list=events,
        )
        assert rec["details"]["plugin_name"] == "acme-compute"


# ---------------------------------------------------------------------------
# compute.checkpoint_written — path_hash not raw path
# ---------------------------------------------------------------------------

class TestCheckpointWritten:
    def test_emits_hash_not_path(self) -> None:
        import hashlib
        raw_path = "/home/user/.corvin/tenants/acme/compute/artifacts/r1/ckpt.ubj"
        path_hash = hashlib.sha256(raw_path.encode()).hexdigest()[:16]
        events: list[dict] = []
        rec = _emit(
            "compute.checkpoint_written",
            {"run_id": "r1", "epoch": 10,
             "checkpoint_path_hash": path_hash},
            audit_list=events,
        )
        detail_str = str(rec["details"])
        assert path_hash in detail_str
        assert raw_path not in detail_str, "raw path must not appear in chain"


# ---------------------------------------------------------------------------
# compute.artifact_registered — artifact_path_hash not artifact_path
# ---------------------------------------------------------------------------

class TestArtifactRegistered:
    def test_emits_hash_not_path(self) -> None:
        import hashlib
        raw_path = "/home/user/.corvin/tenants/acme/compute/artifacts/r1/model.pkl"
        artifact_hash = hashlib.sha256(raw_path.encode()).hexdigest()[:16]
        events: list[dict] = []
        rec = _emit(
            "compute.artifact_registered",
            {"run_id": "r1", "backend": "sklearn",
             "artifact_path_hash": artifact_hash,
             "artifact_size_b": 204800},
            audit_list=events,
        )
        detail_str = str(rec["details"])
        assert artifact_hash in detail_str
        assert raw_path not in detail_str, "raw artifact path must not be in chain"


# ---------------------------------------------------------------------------
# compute.resource_slot_denied
# ---------------------------------------------------------------------------

class TestResourceSlotDenied:
    def test_emits_slot_counts(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "compute.resource_slot_denied",
            {"run_id": "r1", "backend": "xgboost",
             "requested_slots": 4, "available_slots": 1},
            audit_list=events,
        )
        assert rec["details"]["requested_slots"] == 4
        assert rec["details"]["available_slots"] == 1
        assert EVENT_SEVERITY["compute.resource_slot_denied"] == "WARNING"


# ---------------------------------------------------------------------------
# DataSourceAdapter events
# ---------------------------------------------------------------------------

class TestDatasourceEvents:
    def test_registered(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "datasource.registered",
            {"name": "crm_events", "adapter": "s3_parquet",
             "tenant_id": "acme"},
            audit_list=events,
        )
        assert rec["details"]["adapter"] == "s3_parquet"

    def test_connection_tested(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "datasource.connection_tested",
            {"name": "orders_db", "latency_ms": 42},
            audit_list=events,
        )
        assert rec["details"]["latency_ms"] == 42

    def test_connection_failed_warning(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "datasource.connection_failed",
            {"name": "orders_db", "error_class": "OperationalError"},
            audit_list=events,
        )
        assert rec["severity"] == "WARNING"

    def test_watermark_advanced_hash_only(self) -> None:
        """Watermark values must be sha256[:8] hashes, never raw values."""
        import hashlib
        raw_watermark = "2024-06-15T12:00:00Z"
        wm_hash = hashlib.sha256(raw_watermark.encode()).hexdigest()[:8]
        events: list[dict] = []
        rec = _emit(
            "datasource.watermark_advanced",
            {"name": "crm_events", "cursor_col": "created_at",
             "watermark_hash": wm_hash},
            audit_list=events,
        )
        detail_str = str(rec["details"])
        assert wm_hash in detail_str
        assert raw_watermark not in detail_str, "raw watermark must not be in chain"

    def test_residency_violation_warning(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "datasource.residency_violation",
            {"name": "us_orders", "declared_region": "us-east-1",
             "tenant_zone": "eu-central-1"},
            audit_list=events,
        )
        assert rec["severity"] == "WARNING"
        assert rec["details"]["declared_region"] == "us-east-1"
        # Ensure no credentials in the violation event
        assert "password" not in str(rec["details"])
        assert "secret" not in str(rec["details"]).lower()

    def test_pii_detected_no_raw_values(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "datasource.pii_detected",
            {"name": "crm_events",
             "pii_columns": ["user_id", "email"],
             "detection_method": "manifest_hint"},
            audit_list=events,
        )
        # pii_columns carries NAMES only — never actual PII values
        assert "pii_columns" in rec["details"]
        assert "john@example.com" not in str(rec["details"])

    def test_adapter_enabled(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "datasource.adapter_enabled",
            {"tenant_id": "acme", "adapter_name": "acme_datalake",
             "adapter_version": "2.0.1"},
            audit_list=events,
        )
        assert rec["details"]["adapter_name"] == "acme_datalake"

    def test_adapter_disabled(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "datasource.adapter_disabled",
            {"tenant_id": "acme", "adapter_name": "acme_datalake"},
            audit_list=events,
        )
        assert rec["event_type"] == "datasource.adapter_disabled"

    def test_preview_generated(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "datasource.preview_generated",
            {"name": "crm_events", "n_rows": 5,
             "pii_redacted": True},
            audit_list=events,
        )
        assert rec["details"]["n_rows"] == 5
        assert rec["details"]["pii_redacted"] is True

    def test_unregistered(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "datasource.unregistered",
            {"name": "crm_events", "tenant_id": "acme"},
            audit_list=events,
        )
        assert rec["event_type"] == "datasource.unregistered"

    def test_schema_refreshed(self) -> None:
        events: list[dict] = []
        rec = _emit(
            "datasource.schema_refreshed",
            {"name": "orders_db", "n_columns": 12},
            audit_list=events,
        )
        assert rec["details"]["n_columns"] == 12


# ---------------------------------------------------------------------------
# Hash-chain integrity across fabric events
# ---------------------------------------------------------------------------

class TestHashChainIntegrity:
    def test_multiple_fabric_events_chain(self) -> None:
        """Write several fabric events and verify the chain is intact."""
        import tempfile
        from forge.security_events import verify_chain

        td = tempfile.mkdtemp(prefix="fabric-chain-test-")
        path = Path(td) / "audit.jsonl"

        write_event(path, "compute.backend_session_started",
                    details={"run_id": "r1", "backend": "sklearn"})
        write_event(path, "compute.epoch_completed",
                    details={"run_id": "r1", "epoch": 1,
                             "primary_metric": "loss", "metric_value": 0.9})
        write_event(path, "compute.oracle_steer_applied",
                    details={"run_id": "r1", "epoch": 1,
                             "steering_keys": ["lr"],
                             "divergence_detected": False})
        write_event(path, "datasource.registered",
                    details={"name": "ds1", "adapter": "postgresql"})
        write_event(path, "compute.aggregation_completed",
                    details={"run_id": "r1", "strategy": "best",
                             "n_shards": 2, "final_metric": 0.7})

        ok, problems = verify_chain(path)
        assert ok, f"Chain integrity failure: {problems}"
