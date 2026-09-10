"""Real, cross-process persistence for ConfidenceOptimizer (ADR-0644).

``ConfidenceOptimizer``'s own ``store`` constructor param defaults to a bare
``{}`` — in-memory only, invisible across process boundaries AND lost on
every restart. That is fatal for this specific system: the shadow
classification that produces ``(task_type, model)`` recommendations runs in
the bridge daemon (``operator/bridges/shared/adapter.py``, its own process),
while the console that reads confidence via ``get_optimizer()``
(``core/console/corvin_console/routes/model_selection_analytics.py``) runs
in ``corvin_gateway.app`` — a SEPARATE process. Two independent in-memory
singletons never see each other's writes; the console would always read 0
samples no matter how many real turns the bridge processed.

Storage: ``<corvin_home>/tenants/<tenant_id>/global/model_confidence_stats.json``
— one file per tenant (ADR-0007 isolation), a dict of
``"model_stats:{task_type}:{model}:{tenant_id}" -> {"stats": {...ModelStats
fields...}, "confidence_history": [last <=CONVERGENCE_WINDOW confidence
values]}``. Every write is atomic (write-temp + replace). Reads are
read-through-disk on every access — no cross-process staleness — which is
fine at this call volume (once per real turn's outcome, not a hot loop).
"""
from __future__ import annotations

import json
import os
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Iterator


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    return Path.home() / ".corvin"


def _stats_path(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "global" / "model_confidence_stats.json"


def _tenant_from_stat_key(stat_key: str) -> str | None:
    """``"model_stats:{task_type}:{model}:{tenant_id}"`` -> tenant_id.

    Tenant ids never contain ``:`` (ADR-0007 validate_tenant_id), so the
    final colon-segment is always the tenant id regardless of what the model
    id itself contains (e.g. an OpenRouter id like "z-ai/glm-4.6" has no
    colon, but be defensive rather than assume)."""
    if not stat_key.startswith("model_stats:"):
        return None
    parts = stat_key.split(":")
    return parts[-1] if len(parts) >= 4 else None


def _load_file(tenant_id: str) -> dict[str, Any]:
    path = _stats_path(tenant_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001 — corrupt/partial file → start fresh, never crash
        return {}


def _save_file(tenant_id: str, data: dict[str, Any]) -> None:
    path = _stats_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_entries(tenant_id: str) -> list[tuple[str, str]]:
    """Return every (task_type, model) pair with persisted stats for this
    tenant — the real enumeration the analytics summary needs. The
    optimizer's own ``_stats_cache`` is process-local and lazily populated
    only for keys someone already asked about; it is NOT a substitute for
    this when listing "everything this tenant has learned so far"."""
    out: list[tuple[str, str]] = []
    for stat_key in _load_file(tenant_id):
        if not stat_key.startswith("model_stats:"):
            continue
        # model ids can themselves contain ":" (e.g. an Ollama tag like
        # "mistral:7b") — task_type is always parts[1], tenant_id is always
        # the last segment (never contains ":", ADR-0007), so the model is
        # everything in between, rejoined.
        parts = stat_key.split(":")
        if len(parts) >= 4:
            out.append((parts[1], ":".join(parts[2:-1])))
    return out


def load_confidence_history(stat_key: str) -> list[float]:
    tenant_id = _tenant_from_stat_key(stat_key)
    if tenant_id is None:
        return []
    entry = _load_file(tenant_id).get(stat_key) or {}
    history = entry.get("confidence_history")
    return list(history) if isinstance(history, list) else []


def save_confidence_history(stat_key: str, history: list[float]) -> None:
    tenant_id = _tenant_from_stat_key(stat_key)
    if tenant_id is None:
        return
    data = _load_file(tenant_id)
    entry = data.get(stat_key) or {}
    entry["confidence_history"] = history
    data[stat_key] = entry
    _save_file(tenant_id, data)


class PersistentConfidenceStore(MutableMapping):
    """Dict-like ``store`` for :class:`ConfidenceOptimizer` — every
    read/write goes straight through to the per-tenant JSON file, so any
    process calling :func:`get_optimizer` sees the same, durable state."""

    def __getitem__(self, stat_key: str) -> dict[str, Any]:
        tenant_id = _tenant_from_stat_key(stat_key)
        if tenant_id is None:
            raise KeyError(stat_key)
        data = _load_file(tenant_id)
        if stat_key not in data:
            raise KeyError(stat_key)
        return data[stat_key].get("stats", {})

    def __setitem__(self, stat_key: str, value: dict[str, Any]) -> None:
        tenant_id = _tenant_from_stat_key(stat_key)
        if tenant_id is None:
            return
        data = _load_file(tenant_id)
        entry = data.get(stat_key) or {}
        entry["stats"] = value
        data[stat_key] = entry
        _save_file(tenant_id, data)

    def __delitem__(self, stat_key: str) -> None:
        tenant_id = _tenant_from_stat_key(stat_key)
        if tenant_id is None:
            return
        data = _load_file(tenant_id)
        data.pop(stat_key, None)
        _save_file(tenant_id, data)

    def __contains__(self, stat_key: object) -> bool:
        if not isinstance(stat_key, str):
            return False
        tenant_id = _tenant_from_stat_key(stat_key)
        if tenant_id is None:
            return False
        return stat_key in _load_file(tenant_id)

    def __iter__(self) -> Iterator[str]:
        # ConfidenceOptimizer.reset_learning only ever iterates filtered by a
        # known tenant_id substring — scanning every tenant dir the same way
        # keeps this correct without a caller needing a tenant_id up front.
        home = _corvin_home() / "tenants"
        if not home.is_dir():
            return iter(())
        keys: list[str] = []
        for tenant_dir in home.iterdir():
            data = _load_file(tenant_dir.name)
            keys.extend(data.keys())
        return iter(keys)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def keys(self):  # noqa: D102 — MutableMapping's default is correct but explicit is clearer here
        return list(iter(self))
