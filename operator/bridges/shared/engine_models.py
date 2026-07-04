"""Per-engine model configuration — ADR-0119.

Provides:
  - Registry loading from engine_model_registry.yaml
  - EngineModelEntry / EngineModelSpec dataclasses
  - resolve_worker_model() — 6-step chain (persona → env → tenant → default)
  - resolve_os_model()    — 4-step chain (env-override → profile → tenant → adaptive)

Resolution chains are documented in ADR-0119.

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Registry path
# ---------------------------------------------------------------------------

_REGISTRY_FILE = (
    Path(__file__).resolve().parents[3]
    / "operator" / "bundle" / "config-templates" / "engine_model_registry.yaml"
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EngineModelEntry:
    id: str
    label: str
    default: bool = False


@dataclass
class EngineModelSpec:
    engine_id: str
    label: str
    supports_os_turn: bool
    supports_worker_turn: bool
    supports_task_type_steering: bool
    os_models: list[EngineModelEntry] = field(default_factory=list)
    worker_models: list[EngineModelEntry] = field(default_factory=list)

    def default_os_model(self) -> str | None:
        for m in self.os_models:
            if m.default:
                return m.id or None
        return self.os_models[0].id or None if self.os_models else None

    def default_worker_model(self) -> str | None:
        for m in self.worker_models:
            if m.default:
                return m.id or None
        return self.worker_models[0].id or None if self.worker_models else None


# ---------------------------------------------------------------------------
# Registry loading
# ---------------------------------------------------------------------------

_registry_cache: dict[str, EngineModelSpec] | None = None


def load_registry(force_reload: bool = False) -> dict[str, EngineModelSpec]:
    """Load the engine model registry from YAML. Cached after first load."""
    global _registry_cache  # noqa: PLW0603
    if _registry_cache is not None and not force_reload:
        return _registry_cache

    try:
        import yaml  # type: ignore[import-untyped]
        raw: dict[str, Any] = yaml.safe_load(_REGISTRY_FILE.read_text("utf-8")) or {}
    except Exception:
        _registry_cache = {}
        return _registry_cache

    result: dict[str, EngineModelSpec] = {}
    for engine_id, entry in (raw.get("engines") or {}).items():
        if not isinstance(entry, dict):
            continue

        def _parse_models(raw_list: Any) -> list[EngineModelEntry]:
            out = []
            for item in (raw_list or []):
                if isinstance(item, dict):
                    out.append(EngineModelEntry(
                        id=str(item.get("id") or ""),
                        label=str(item.get("label") or ""),
                        default=bool(item.get("default", False)),
                    ))
            return out

        result[engine_id] = EngineModelSpec(
            engine_id=engine_id,
            label=str(entry.get("label") or engine_id),
            supports_os_turn=bool(entry.get("supports_os_turn", False)),
            supports_worker_turn=bool(entry.get("supports_worker_turn", False)),
            supports_task_type_steering=bool(entry.get("supports_task_type_steering", False)),
            os_models=_parse_models(entry.get("os_models")),
            worker_models=_parse_models(entry.get("worker_models")),
        )

    _registry_cache = result
    return result


def registry_as_dict(force_reload: bool = False) -> dict[str, Any]:
    """Return the registry as a JSON-serialisable dict for the console API.

    ``force_reload=True`` re-reads the YAML from disk (bypassing the process
    cache) so a model-catalog update takes effect on a browser refresh, without
    restarting the console — the /registry route uses this."""
    result = {}
    for engine_id, spec in load_registry(force_reload=force_reload).items():
        result[engine_id] = {
            "label": spec.label,
            "supports_os_turn": spec.supports_os_turn,
            "supports_worker_turn": spec.supports_worker_turn,
            "supports_task_type_steering": spec.supports_task_type_steering,
            "os_models": [{"id": m.id, "label": m.label, "default": m.default} for m in spec.os_models],
            "worker_models": [{"id": m.id, "label": m.label, "default": m.default} for m in spec.worker_models],
        }
    return result


# ---------------------------------------------------------------------------
# Tenant YAML helper
# ---------------------------------------------------------------------------

def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    return Path.home() / ".corvin"


def _load_tenant_spec(tenant_id: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
        cfg = _corvin_home() / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
        if cfg.is_file():
            raw = yaml.safe_load(cfg.read_text("utf-8")) or {}
            return (raw.get("spec") or {})
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def get_tenant_engine_model(
    tenant_id: str,
    engine_id: str,
    role: str,  # "os_model" or "worker_model"
) -> str | None:
    """Read spec.engine_models.<engine_id>.<role> from tenant YAML.

    Returns a non-empty string if set, or None.
    """
    spec = _load_tenant_spec(tenant_id)
    engine_models = spec.get("engine_models") or {}
    per_engine = engine_models.get(engine_id) or {}
    val = per_engine.get(role)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None
