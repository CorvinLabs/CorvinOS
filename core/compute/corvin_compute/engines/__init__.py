"""Built-in ComputeEngine implementations (ADR-0029)."""
from .flat import FlatEngine, register_flat_engine

__all__ = ["FlatEngine", "register_flat_engine"]
