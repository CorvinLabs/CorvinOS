"""MCP tool definitions for compute_* (ADR-0013 Phase 13.5).

This module is consumed by the Forge MCP server, NOT by the compute
worker. The Forge MCP server probes the worker socket and, on success,
appends these tool definitions to its tools/list output. Tool calls
get routed back to the worker via :class:`WorkerClient`.

The schemas are plain dicts, intentionally without Pydantic — the
worker re-validates server-side (mirror of the existing data_register
MCP surface in forge.corvin_data).
"""
from __future__ import annotations

from typing import Any

# --- Tool input-schemas (JSON-schema fragments) ---------------------------

_BUDGET_SCHEMA = {
    "type": "object",
    "properties": {
        "max_iterations":   {"type": "integer", "minimum": 1},
        "max_wall_clock_s": {"type": "integer", "minimum": 1},
        "convergence_eps":  {"type": "number"},
        "stall_after_n":    {"type": "integer", "minimum": 1},
    },
    "additionalProperties": False,
}

COMPUTE_RUN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_name":    {"type": "string"},
        "param_grid":   {"type": "object"},
        "loss_metric":  {"type": "string"},
        "strategy":     {"type": "string",
                         "enum": ["grid", "random", "bayesian"]},
        "budget":       _BUDGET_SCHEMA,
        "minimise":     {"type": "boolean"},
        "data_handle":  {"type": ["string", "null"]},
        "seed":         {"type": ["integer", "null"]},
        "top_k_size":   {"type": "integer", "minimum": 1, "maximum": 10},
        "sensitive_fields": {"type": "array", "items": {"type": "string"}},
        # ADR-0106 M5 — DSI v1 datasource names for this compute run.
        # Each name is resolved to a ConnectionManifest at worker spawn time.
        # Vault credentials are injected into the bwrap env at spawn.
        "datasources":  {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "List of DSI v1 connection names to make available inside "
                "the compute worker as ctx.datasources['name']. "
                "Each name must be registered via datasource_register."
            ),
        },
    },
    "required": ["tool_name", "param_grid", "loss_metric", "strategy"],
    "additionalProperties": False,
}

COMPUTE_STATUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "compute_handle": {"type": "string", "pattern": "^compute_[A-Za-z0-9_-]{22}$"},
    },
    "required": ["compute_handle"],
    "additionalProperties": False,
}

COMPUTE_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "compute_handle": {"type": "string",
                          "pattern": "^compute_[A-Za-z0-9_-]{22}$"},
        "wait_s":         {"type": "number", "minimum": 0, "maximum": 30},
    },
    "required": ["compute_handle"],
    "additionalProperties": False,
}

COMPUTE_ABORT_SCHEMA: dict[str, Any] = COMPUTE_STATUS_SCHEMA


def compute_tool_definitions() -> list[dict[str, Any]]:
    """Return the four MCP tool dicts the Forge server advertises."""
    return [
        {
            "name": "compute_run",
            "description": (
                "Submit an iterative compute run against an already-forged "
                "Forge tool. Returns IMMEDIATELY with a compute_handle; the "
                "worker drives the iteration loop out-of-LLM-loop. Poll "
                "via compute_status; read the final outcome via "
                "compute_result."
            ),
            "inputSchema": COMPUTE_RUN_SCHEMA,
        },
        {
            "name": "compute_status",
            "description": (
                "Poll a running compute run. Returns iterations_done, "
                "best_loss, eta, and a Top-K view with param fingerprints "
                "(NOT raw values)."
            ),
            "inputSchema": COMPUTE_STATUS_SCHEMA,
        },
        {
            "name": "compute_result",
            "description": (
                "Read the final outcome of a compute run. Blocks server-"
                "side up to wait_s seconds (capped at 30 s) when the run "
                "is still in progress."
            ),
            "inputSchema": COMPUTE_RESULT_SCHEMA,
        },
        {
            "name": "compute_abort",
            "description": (
                "Request a running compute run to terminate. The run "
                "drops to state=aborted on the next loop turn."
            ),
            "inputSchema": COMPUTE_ABORT_SCHEMA,
        },
    ]


COMPUTE_TOOL_NAMES = frozenset({
    "compute_run", "compute_status", "compute_result", "compute_abort",
})

# --------------------------------------------------------------------------
# ADR-0029 — Unified compute_submit / compute_gate tools
# --------------------------------------------------------------------------

_GATE_ACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action_type": {
            "type": "string",
            "enum": ["resume", "abort", "add_stage", "steer", "forge_noted",
                     "reallocate_budget", "re_run_manager"],
            "description": "The gate action to perform.",
        },
        "payload": {
            "type": "object",
            "description": "Action-specific data (stage spec, overrides, budget fractions, etc.).",
        },
    },
    "required": ["action_type"],
    "additionalProperties": False,
}

COMPUTE_SUBMIT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "engine": {
            "type": "string",
            "enum": ["flat", "pipeline", "hac"],
            "description": (
                "'flat': single tool, N iterations (ADR-0013). "
                "'pipeline': linear stages with forge gates (ADR-0027). "
                "'hac': sub-manager tree with composite loss and backprop gate (ADR-0028)."
            ),
        },
        "budget": _BUDGET_SCHEMA,
        "extra": {
            "type": "object",
            "description": (
                "Engine-specific spec. "
                "For 'flat': same fields as compute_run. "
                "For 'pipeline': {stages: [...], steering_gate: bool, steering_gate_timeout_s}. "
                "For 'hac': {sub_managers: [...], loss_weights: {...}, backprop_gate: bool, "
                "max_backprop_rounds, convergence_epsilon, convergence_window}."
            ),
        },
        "tenant_id": {"type": ["string", "null"]},
    },
    "required": ["engine", "budget"],
    "additionalProperties": False,
}

COMPUTE_GATE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "compute_handle": {
            "type": "string",
            "description": "The pipeline_ or hac_ job_id returned by compute_submit.",
        },
        "action": _GATE_ACTION_SCHEMA,
    },
    "required": ["compute_handle", "action"],
    "additionalProperties": False,
}

COMPUTE_ENGINE_TOOLS_NAMES = frozenset({
    "compute_submit", "compute_gate",
})


def compute_engine_tool_definitions() -> list[dict]:
    """ADR-0029 — unified tools, advertised alongside the legacy flat tools."""
    return [
        {
            "name": "compute_submit",
            "description": (
                "Submit a compute job to any engine (ADR-0029). "
                "engine='flat': same as compute_run but via the unified surface. "
                "engine='pipeline': start a multi-stage pipeline with forge gates (ADR-0027). "
                "engine='hac': start a hierarchical sub-manager tree with composite loss "
                "and a Backprop Gate (ADR-0028). "
                "Returns a compute_handle immediately; poll via compute_status."
            ),
            "inputSchema": COMPUTE_SUBMIT_SCHEMA,
        },
        {
            "name": "compute_gate",
            "description": (
                "Interact with a gate on a running pipeline or HAC job (ADR-0029). "
                "action_type='resume': continue to the next stage / backprop round. "
                "action_type='abort': stop the job. "
                "action_type='add_stage': append a new stage to a pipeline at this gate. "
                "action_type='steer': override the next stage's param_grid or strategy. "
                "action_type='forge_noted': record that a forge_tool call was made at this gate. "
                "action_type='reallocate_budget': move budget from one HAC sub-manager to another. "
                "action_type='re_run_manager': specify which sub-managers to re-run next round."
            ),
            "inputSchema": COMPUTE_GATE_SCHEMA,
        },
    ]

# --------------------------------------------------------------------------
# ADR-0026 — Compute Fabric MCP bridge extension
# --------------------------------------------------------------------------

FABRIC_TOOL_NAMES: frozenset[str] = frozenset({
    "compute_job_create",
    "compute_parallel_run",
    "compute_shard_plan",
    "compute_resource_status",
    "compute_plugin_list",
    "compute_plugin_enable",
    "compute_plugin_disable",
    "compute_backend_caps",
    "compute_artifact_list",
    "datasource_register",
    "datasource_list",
    "datasource_schema",
    "datasource_test",
    "datasource_unregister",
    "datasource_preview",
})

# Sentinel: returned by all Fabric MCP tools when fabric_enabled=False in
# tenant config.  The Forge MCP server checks fabric_enabled before routing
# any Fabric tool call and substitutes this sentinel on a mismatch.
FABRIC_NOT_ENABLED: dict[str, str] = {
    "status": "error",
    "error": "FabricNotEnabled",
    "message": (
        "fabric_enabled must be true in tenant config (spec.compute.fabric_enabled) "
        "to use Compute Fabric tools (ADR-0026)."
    ),
}

_SHARD_STRATEGY_ENUM = ["hash", "range", "stratified", "time_window"]
_AGG_STRATEGY_ENUM = [
    "best", "average", "vote", "stack", "federated_avg", "custom",
]


def fabric_tool_definitions() -> list[dict[str, Any]]:
    """Return MCP tool defs for all ADR-0026 Fabric tools.

    These are appended to the Forge MCP server's tools/list output only
    when the compute worker is reachable AND fabric_enabled=true in the
    tenant config.  Each tool returns FABRIC_NOT_ENABLED when
    fabric_enabled is false — callers MUST check that guard.
    """
    return [
        # ---- Fabric compute jobs ------------------------------------------
        {
            "name": "compute_job_create",
            "description": (
                "Submit a new Compute Fabric job (ADR-0026). Supports "
                "backend selection, inter-job parallelism (n_workers > 1), "
                "sharding strategies, aggregation, and the Async Gradient "
                "Oracle. Returns a job_id immediately; poll via "
                "compute_status."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Job type (e.g. 'train', 'fine_tune', 'transform').",
                    },
                    "model_spec": {
                        "type": "object",
                        "description": "Model specification dict (backend-specific).",
                    },
                    "data_ref": {
                        "type": "string",
                        "description": "Layer 24 data_handle (mutually exclusive with datasource).",
                    },
                    "backend": {
                        "type": "string",
                        "description": (
                            "Backend name (e.g. 'sklearn', 'xgboost'). "
                            "Omit to trigger Backend Negotiation."
                        ),
                    },
                    "hyperparams": {
                        "type": "object",
                        "description": "Initial hyperparameter values (backend-specific).",
                    },
                    "convergence": {
                        "type": "object",
                        "description": "ConvSpec: {metric, threshold, patience, max_epochs}.",
                    },
                    "oracle_enabled": {
                        "type": "boolean",
                        "default": False,
                        "description": "Activate the Async Gradient Oracle.",
                    },
                    "oracle_divergence_thr": {
                        "type": "number",
                        "default": 0.05,
                        "description": "Divergence threshold for multi-worker Oracle.",
                    },
                    "n_workers": {
                        "type": "integer",
                        "default": 1,
                        "description": "Number of parallel shard workers (>1 activates ShardManager).",
                    },
                    "shard_strategy": {
                        "type": "string",
                        "enum": _SHARD_STRATEGY_ENUM,
                        "description": "How to partition data across workers.",
                    },
                    "aggregation_strategy": {
                        "type": "string",
                        "enum": _AGG_STRATEGY_ENUM,
                        "description": "How to combine per-shard artifacts.",
                    },
                    "datasource": {
                        "type": "string",
                        "description": (
                            "Registered datasource name (mutually exclusive with data_ref). "
                            "Requires datasource_enabled=true in tenant config."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "FTS5-searchable tags for the Model Registry.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "compute_parallel_run",
            "description": (
                "Submit a parallel hyperparameter fan-out run "
                "(ADR-0026 Section C). Spawns n_workers independent jobs "
                "from a single job_spec and aggregates the results. "
                "Equivalent to calling compute_job_create n_workers times "
                "and waiting for the Aggregator — but managed by the Fabric."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_spec": {
                        "type": "object",
                        "description": "Base job specification (same shape as compute_job_create).",
                    },
                    "n_workers": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Number of parallel workers to spawn.",
                    },
                    "aggregation_strategy": {
                        "type": "string",
                        "enum": _AGG_STRATEGY_ENUM,
                        "description": "Aggregation strategy applied after all workers complete.",
                    },
                    "param_sampling": {
                        "type": "object",
                        "description": (
                            "Per-worker hyperparameter sampling spec. "
                            "Values can be a list (grid) or a string expression "
                            "like 'log_uniform(1e-4,1e-1)'."
                        ),
                    },
                },
                "required": ["job_spec", "n_workers", "aggregation_strategy"],
                "additionalProperties": False,
            },
        },
        {
            "name": "compute_shard_plan",
            "description": (
                "Plan how a data_ref would be sharded by the ShardManager "
                "(ADR-0026 Section C). Returns shard sizes, estimated "
                "wall-clock, available resource slots, and the recommended "
                "n_workers."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data_ref": {
                        "type": "string",
                        "description": "Layer 24 data_handle to shard-plan.",
                    },
                    "strategy": {
                        "type": "string",
                        "enum": _SHARD_STRATEGY_ENUM,
                        "description": "Sharding strategy.",
                    },
                    "n_shards": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Desired shard count; omit to let the Fabric recommend.",
                    },
                },
                "required": ["data_ref", "strategy"],
                "additionalProperties": False,
            },
        },
        {
            "name": "compute_resource_status",
            "description": (
                "Return current resource availability in the Compute Fabric "
                "(ADR-0026 Section C ResourceManager): "
                "available_cpu_cores, available_memory_gb, "
                "active_workers, queued_jobs, per_backend_max_slots."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        # ---- Backend plugin management ------------------------------------
        {
            "name": "compute_plugin_list",
            "description": (
                "List all discovered ComputeBackend plugins across the "
                "four-tier hierarchy (system / tenant / user / bundle). "
                "Returns [{name, version, status, capabilities}]."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "compute_plugin_enable",
            "description": (
                "Enable a ComputeBackend plugin for a tenant "
                "(ADR-0026 Section A). Owner-only. "
                "Emits compute.backend_plugin_enabled into the audit chain."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name as declared in compute_plugin.yaml.",
                    },
                    "tenant_id": {
                        "type": "string",
                        "description": "Target tenant ID (ADR-0007 five-scope model).",
                    },
                },
                "required": ["name", "tenant_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "compute_plugin_disable",
            "description": (
                "Disable a ComputeBackend plugin for a tenant "
                "(ADR-0026 Section A). Owner-only. "
                "Emits compute.backend_plugin_disabled into the audit chain."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name to disable.",
                    },
                    "tenant_id": {
                        "type": "string",
                        "description": "Target tenant ID.",
                    },
                },
                "required": ["name", "tenant_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "compute_backend_caps",
            "description": (
                "Return the full capability manifest for a named "
                "ComputeBackend plugin: capabilities list, parallel config, "
                "sandbox settings, and steering_map."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Backend name to inspect.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "compute_artifact_list",
            "description": (
                "Query the Model Registry (SQLite FTS5) for registered "
                "artifacts. Optional run_id filter. Returns "
                "[{artifact_id, model_type, metric, path_hash}] — "
                "raw artifact paths are never returned."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "Filter by specific run_id (omit = all runs).",
                    },
                },
                "additionalProperties": False,
            },
        },
        # ---- DataSourceAdapter tools (Section D) --------------------------
        {
            "name": "datasource_register",
            "description": (
                "Register a DataSourceAdapter datasource (ADR-0026 Section D). "
                "Triggers schema discovery, PII detection (Layer 24), and "
                "data-residency validation. Returns a schema snapshot; "
                "raw data never enters the LLM context."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Datasource name (must match the YAML manifest name).",
                    },
                    "manifest_yaml": {
                        "type": "string",
                        "description": (
                            "Optional inline YAML manifest. If absent, "
                            "reads from the on-disk datasource manifest file."
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "datasource_list",
            "description": (
                "List all registered datasources for the current tenant. "
                "Returns [{name, adapter, status, row_estimate, last_watermark}]."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "datasource_schema",
            "description": (
                "Return the PII-redacted schema snapshot for a registered "
                "datasource (same Layer 24/32 pipeline as datasource_register)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Datasource name.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "datasource_test",
            "description": (
                "Run a connectivity health check against a registered "
                "datasource. Returns {ok, latency_ms, error?}."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Datasource name to test.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "datasource_unregister",
            "description": (
                "Remove a datasource registration (manifest + checkpoint). "
                "Does NOT touch the upstream source system. Idempotent."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Datasource name to unregister.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "datasource_preview",
            "description": (
                "Return a PII-redacted sample from a registered datasource "
                "(Layer 24 pipeline). Maximum 20 rows. "
                "Emits datasource.preview_generated into the audit chain."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Datasource name.",
                    },
                    "n_rows": {
                        "type": "integer",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Number of sample rows (default 5, max 20).",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    ]
