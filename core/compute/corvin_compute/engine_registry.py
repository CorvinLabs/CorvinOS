"""ComputeEngine registry (ADR-0029)."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from .engine_protocol import ComputeEngine, UnknownJobId

if TYPE_CHECKING:
    pass


class ComputeEngineRegistry:
    def __init__(self) -> None:
        self._engines: dict[str, ComputeEngine] = {}
        self._lock = threading.Lock()

    def register(self, engine: ComputeEngine) -> None:
        with self._lock:
            # Guard: a different engine already claiming the same prefix would
            # cause get_by_job_id to return the wrong engine for live job IDs.
            for existing in self._engines.values():
                if (
                    existing.engine_id != engine.engine_id
                    and existing.job_id_prefix == engine.job_id_prefix
                ):
                    raise ValueError(
                        f"prefix conflict: engine {engine.engine_id!r} claims "
                        f"prefix {engine.job_id_prefix!r} already held by "
                        f"{existing.engine_id!r}"
                    )
            self._engines[engine.engine_id] = engine

    def unregister(self, engine_id: str) -> None:
        with self._lock:
            self._engines.pop(engine_id, None)

    def get(self, engine_id: str) -> ComputeEngine:
        with self._lock:
            try:
                return self._engines[engine_id]
            except KeyError:
                raise KeyError(f"no engine registered with id {engine_id!r}") from None

    def get_by_job_id(self, job_id: str) -> ComputeEngine:
        with self._lock:
            for engine in self._engines.values():
                if job_id.startswith(engine.job_id_prefix):
                    return engine
        raise UnknownJobId(job_id)

    def engines_for_tenant(
        self,
        tenant_id: str,
        allowed_engine_ids: list[str] | None = None,
    ) -> list[ComputeEngine]:
        with self._lock:
            engines = list(self._engines.values())
        if allowed_engine_ids is not None:
            engines = [e for e in engines if e.engine_id in allowed_engine_ids]
        return engines

    def discover(self) -> list[str]:
        with self._lock:
            return list(self._engines.keys())

    @property
    def default_engine_id(self) -> str | None:
        with self._lock:
            return next(iter(self._engines), None)


# Module-level singleton — avoids circular imports if callers use the
# convenience functions instead of holding a registry reference directly.
_default_registry = ComputeEngineRegistry()


def register(engine: ComputeEngine) -> None:
    _default_registry.register(engine)


def get(engine_id: str) -> ComputeEngine:
    return _default_registry.get(engine_id)


def get_by_job_id(job_id: str) -> ComputeEngine:
    return _default_registry.get_by_job_id(job_id)


def get_registry() -> ComputeEngineRegistry:
    return _default_registry
