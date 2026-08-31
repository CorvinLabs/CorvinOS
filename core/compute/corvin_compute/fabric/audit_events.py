"""Fabric audit event allow-list (ADR-0026).

Every event emitted by the Compute Fabric must appear here.  The allow-list
is the structural defence: extra keys raise AuditFieldNotAllowed.

IMPORTANT: steering VALUES never enter the audit chain — only steering_keys
(list of key names).  Model weights, raw data, and parameter values are
similarly excluded.
"""
from __future__ import annotations

FABRIC_AUDIT_EVENTS: dict[str, set[str]] = {
    "compute.backend_session_started": {
        "run_id", "backend", "backend_version", "shard_index",
    },
    "compute.epoch_completed": {
        "run_id", "epoch", "primary_metric", "metric_value",
        "wall_ms", "shard_index",
    },
    "compute.oracle_steer_applied": {
        "run_id", "epoch", "steering_keys", "divergence_detected",
    },
    "compute.oracle_subprocess_failed": {
        "run_id", "epoch", "failure_reason",
    },
    "compute.shard_completed": {
        "run_id", "shard_index", "total_shards", "final_metric",
    },
    "compute.aggregation_completed": {
        "run_id", "strategy", "n_shards", "final_metric",
    },
    "compute.backend_plugin_enabled": {
        "tenant_id", "plugin_name", "plugin_version",
    },
    "compute.backend_plugin_disabled": {
        "tenant_id", "plugin_name",
    },
    "compute.checkpoint_written": {
        "run_id", "epoch", "checkpoint_path_hash",
    },
    "compute.artifact_registered": {
        "run_id", "backend", "artifact_path_hash", "artifact_size_b",
    },
    "compute.resource_slot_denied": {
        "run_id", "backend", "requested_slots", "available_slots",
    },
}

__all__ = ["FABRIC_AUDIT_EVENTS"]
