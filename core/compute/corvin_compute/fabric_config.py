"""ADR-0026 tenant config extension — FabricConfig Pydantic model.

Added fields under spec.compute in tenant.corvin.yaml.
extra='forbid' (ADR-0007 Phase 3.1 schema strictness).
fabric_enabled: false is the guard — even with enabled: true,
all ADR-0026 MCP tools return FabricNotEnabled unless fabric_enabled: true.

MUST NOT import anthropic — CI AST lint enforced.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ParallelIntraBackend(BaseModel):
    """Intra-backend parallelism settings (per compute_plugin.yaml)."""

    model_config = {"extra": "forbid"}

    model: str = "thread"
    """Intra-backend parallelism model: thread | process | distributed | gpu."""

    max_workers: str | int = "auto"
    """Max worker count within a single backend instance.
    "auto" = nproc or GPU count."""

    gpu_aware: bool = False
    """Whether the backend can utilise GPU resources."""


class ParallelInterJob(BaseModel):
    """Inter-job parallelism settings (ShardManager compatibility)."""

    model_config = {"extra": "forbid"}

    compatible: bool = True
    """Whether the backend supports multiple parallel ShardManager instances.
    False (e.g. Spark backend) = ShardManager hands full cursor to one instance."""

    max_concurrent_instances: int = Field(default=8, ge=1, le=64)
    """Operator cap on concurrent parallel instances (when compatible=True)."""

    resource_per_instance: dict = Field(
        default_factory=lambda: {"cpu_cores": 2, "memory_gb": 4.0}
    )
    """Resource footprint per instance (used by ResourceManager slot calc)."""


class PluginParallel(BaseModel):
    """Parallel configuration block in a compute_plugin.yaml manifest."""

    model_config = {"extra": "forbid"}

    intra_backend: ParallelIntraBackend = Field(
        default_factory=ParallelIntraBackend
    )
    inter_job: ParallelInterJob = Field(default_factory=ParallelInterJob)


class FabricConfig(BaseModel):
    """Pydantic model for spec.compute in tenant.corvin.yaml.

    Includes all ADR-0013 (Layer 25) fields (unchanged) plus ADR-0026
    additions. The model is used for validation only; it is NOT persisted
    by this module — the authoritative source is the YAML file on disk.

    extra="forbid" enforces strict schema (ADR-0007 Phase 3.1).
    """

    model_config = {"extra": "forbid"}

    # ---- ADR-0013 inherited fields (unchanged) ----
    enabled: bool = True
    """Master switch: compute worker enabled by default per tenant (ADR-0013)."""

    max_parallel_iterations: int = Field(default=4, ge=1, le=16)
    """Max concurrent iterations in the ADR-0013 strategy loop."""

    max_concurrent_runs: int = Field(default=2, ge=1, le=8)
    """Max concurrent compute_run handles per tenant."""

    max_iterations_per_run: int = Field(default=200, ge=1, le=10000)
    """Hard cap on iterations per compute_run handle."""

    max_wall_clock_per_run_s: int = Field(default=600, ge=1, le=86400)
    """Wall-clock timeout per compute_run handle in seconds."""

    top_k_size: int = Field(default=5, ge=1, le=10)
    """How many top-k results to surface in compute_status."""

    disallow_llm_strategies: bool = False
    """True = block Oracle and any strategy classified as LLM-based."""

    strategies_allowed: list[str] = Field(
        default_factory=lambda: ["grid", "random", "bayesian"]
    )
    """Allowlist of ADR-0013 strategy names."""

    # ---- ADR-0026 additions ----
    fabric_enabled: bool = False
    """Master guard for ADR-0026 Compute Fabric. All Fabric MCP tools
    return FabricNotEnabled unless this is True."""

    allow_network_plugins: bool = False
    """Allow ComputeBackend plugins with sandbox.network: allow.
    Off by default — must be explicitly enabled by the operator."""

    max_parallel_workers: int = Field(default=4, ge=1, le=32)
    """Inter-job parallelism cap (ShardManager + ResourceManager)."""

    max_artifact_size_mb: int = Field(default=500, ge=10, le=10000)
    """Per-artifact size cap for the Model Registry artifact store."""

    oracle_enabled: bool = False
    """Permit the Async Gradient Oracle per tenant. Requires
    disallow_llm_strategies=False."""

    oracle_model: str | None = None
    """Oracle subprocess model name (None = claude -p default).
    MUST NOT be an API-key model name; subscription-native only."""

    backend_allowlist: list[str] = Field(default_factory=list)
    """Empty list = all discovered backends allowed.
    Non-empty = only listed backend names are accepted."""

    backend_denylist: list[str] = Field(default_factory=list)
    """Denied backend names. Wins over backend_allowlist."""

    aggregation_strategies_allowed: list[str] = Field(
        default_factory=lambda: ["best", "average", "vote", "stack"]
    )
    """Allowlist of Aggregator strategies.
    federated_avg and custom require explicit opt-in here."""

    negotiation_enabled: bool = True
    """Allow Backend Negotiation via Haiku-4.5 subprocess.
    False = explicit backend param is required."""

    # ---- Section D — DataSourceAdapter additions ----
    datasource_enabled: bool = False
    """Gate for ADR-0026 Section D (DataSourceAdapter). Off by default."""

    datasource_adapter_allowlist: list[str] = Field(default_factory=list)
    """Empty list = all discovered adapters allowed.
    Non-empty = only listed adapter names are accepted."""

    datasource_adapter_denylist: list[str] = Field(default_factory=list)
    """Denied adapter names. Wins over datasource_adapter_allowlist."""

    datasource_max_row_estimate: int = Field(default=0, ge=0)
    """Row estimate cap for untrusted sources. 0 = unlimited."""

    datasource_incremental_enabled: bool = True
    """Allow watermark-based incremental reads."""

    datasource_allow_network_adapters: bool = False
    """Allow DataSourceAdapter plugins with sandbox.network: allow.
    Same gate as allow_network_plugins."""

    datasource_residency_strict: bool = True
    """Reject datasource_register when source.region mismatches
    tenant.data_residency zone (ADR-0007 Phase 3.3).
    RECOMMENDED ON — turning off may violate GDPR Art. 45."""
