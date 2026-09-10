"""Real persistence for per-task-type model/provider selection (ADR-0641/0642).

Storage: ``<corvin_home>/tenants/<tenant_id>/global/model_selection_config.json``

Replaces the Phase-1 in-memory ``_MOCK_ENGINE_CONFIG``
(``core/console/corvin_console/routes/engine_api.py``) — same shape
(corvinOS/SIMPLE/MEDIUM/COMPLEX → selected_model/provider/alternatives),
actually written to disk, and actually READ by the shadow classifier
(``operator/bridges/shared/model_selector_shadow.py`` → ``ModelSelector(overrides=...)``)
so a saved console preference has a real, observable effect on the next
turn's classification instead of being cosmetic.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any, Optional

TASK_TYPES: tuple[str, ...] = ("corvinOS", "SIMPLE", "MEDIUM", "COMPLEX")

#: task_type (console vocabulary) -> complexity (ModelSelector vocabulary).
#: "corvinOS" has no classifier equivalent — task classification itself is
#: deterministic (feature_extractor.py, regex/keyword rules), not model-driven.
COMPLEXITY_BY_TASK_TYPE: dict[str, str] = {
    "SIMPLE": "simple",
    "MEDIUM": "medium",
    "COMPLEX": "complex",
}

_DEFAULTS: dict[str, dict[str, Any]] = {
    "corvinOS": {"selected_model": "claude-haiku-4-5-20251001", "provider": None, "alternatives": []},
    "SIMPLE": {"selected_model": "claude-haiku-4-5-20251001", "provider": None, "alternatives": ["claude-sonnet-5"]},
    "MEDIUM": {"selected_model": "claude-sonnet-5", "provider": None, "alternatives": ["claude-haiku-4-5-20251001", "claude-opus-5"]},
    "COMPLEX": {"selected_model": "claude-opus-5", "provider": None, "alternatives": ["claude-sonnet-5"]},
}


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    return Path.home() / ".corvin"


def _config_path(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "global" / "model_selection_config.json"


def load_config(tenant_id: str) -> dict[str, dict[str, Any]]:
    """Return {task_type: {selected_model, provider, alternatives}} — always
    all four TASK_TYPES, falling back to _DEFAULTS for anything unset/corrupt."""
    out = {k: dict(v) for k, v in _DEFAULTS.items()}
    path = _config_path(tenant_id)
    if not path.exists():
        return out
    try:
        raw = json.loads(path.read_text("utf-8"))
    except Exception:
        return out
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, dict):
        return out
    for task_type in TASK_TYPES:
        entry = models.get(task_type)
        if isinstance(entry, dict):
            out[task_type] = {
                "selected_model": entry.get("selected_model") or out[task_type]["selected_model"],
                "provider": entry.get("provider") or None,
                "alternatives": entry.get("alternatives") if isinstance(entry.get("alternatives"), list) else [],
            }
    return out


def save_config(tenant_id: str, models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Atomic write. *models* must already be validated by the caller (route)."""
    path = _config_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tenant_id": tenant_id,
        "models": models,
        "last_updated": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return payload


def classifier_overrides(tenant_id: str) -> dict[str, dict[str, Optional[str]]]:
    """Shape ``ModelSelector(overrides=...)`` expects: complexity-keyed, not
    task-type-keyed. "corvinOS" is never included — it has no complexity key."""
    cfg = load_config(tenant_id)
    out: dict[str, dict[str, Optional[str]]] = {}
    for task_type, complexity in COMPLEXITY_BY_TASK_TYPE.items():
        entry = cfg.get(task_type) or {}
        out[complexity] = {"provider": entry.get("provider"), "model": entry.get("selected_model")}
    return out
