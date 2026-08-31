"""PipelineEngine package — ADR-0027 Adaptive Compute Pipelines."""
from .engine import PipelineEngine, register_pipeline_engine
from .manifest import PipelineManifest, StageSpec, PipelineStore, new_pipeline_id
from .coordinator import PipelineCoordinator

__all__ = [
    "PipelineEngine",
    "register_pipeline_engine",
    "PipelineManifest",
    "StageSpec",
    "PipelineStore",
    "PipelineCoordinator",
    "new_pipeline_id",
]
