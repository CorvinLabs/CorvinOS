"""FabricConfig — top-level configuration model for ADR-0026 Compute Fabric.

All fields have safe defaults (disabled by default per CLAUDE.md opt-in rule).
"""
from __future__ import annotations

import dataclasses
from typing import Optional


@dataclasses.dataclass
class OracleConfig:
    """Configuration for the Async Gradient Oracle (Section B)."""
    enabled: bool = False
    timeout_s: float = 30.0
    max_consecutive_failures: int = 3
    queue_max_size: int = 8
    divergence_threshold: float = 0.05
    # subprocess command — split into list for asyncio.create_subprocess_exec
    subprocess_cmd: list[str] = dataclasses.field(
        default_factory=lambda: ["claude", "-p", "--max-turns", "1", "--tools", ""]
    )


@dataclasses.dataclass
class ParallelConfig:
    """Configuration for inter-job parallelism (Section C)."""
    enabled: bool = False
    max_concurrent_jobs: int = 4
    # cpu cores per slot
    cpu_per_slot: int = 1
    # memory MiB per slot
    mem_mib_per_slot: int = 512
    default_aggregation_strategy: str = "best"


@dataclasses.dataclass
class RegistryConfig:
    """Configuration for the SQLite model registry."""
    db_path: Optional[str] = None  # None → in-memory for tests


@dataclasses.dataclass
class FabricConfig:
    """Top-level configuration for the Corvin Compute Fabric (ADR-0026).

    All sub-systems are disabled by default; operator enables per tenant.
    """
    fabric_enabled: bool = False
    oracle: OracleConfig = dataclasses.field(default_factory=OracleConfig)
    parallel: ParallelConfig = dataclasses.field(default_factory=ParallelConfig)
    registry: RegistryConfig = dataclasses.field(default_factory=RegistryConfig)
    # Path to plugin discovery roots (system, tenant, user are appended at runtime)
    extra_plugin_paths: list[str] = dataclasses.field(default_factory=list)
    # If True, oracle is classified as LLM strategy and disabled when
    # tenant sets disallow_llm_strategies: true
    disallow_llm_strategies: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "FabricConfig":
        oracle_d = d.get("oracle", {})
        parallel_d = d.get("parallel", {})
        registry_d = d.get("registry", {})
        return cls(
            fabric_enabled=d.get("fabric_enabled", False),
            oracle=OracleConfig(**{k: v for k, v in oracle_d.items()
                                   if k in OracleConfig.__dataclass_fields__}),
            parallel=ParallelConfig(**{k: v for k, v in parallel_d.items()
                                       if k in ParallelConfig.__dataclass_fields__}),
            registry=RegistryConfig(**{k: v for k, v in registry_d.items()
                                       if k in RegistryConfig.__dataclass_fields__}),
            extra_plugin_paths=d.get("extra_plugin_paths", []),
            disallow_llm_strategies=d.get("disallow_llm_strategies", False),
        )


__all__ = [
    "FabricConfig",
    "OracleConfig",
    "ParallelConfig",
    "RegistryConfig",
]
