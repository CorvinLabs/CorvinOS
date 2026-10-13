"""The operator's Console "Models" selection, read in ONE place (ADR-2090).

The Models page writes two stores:

* **Turn pins** — ``spec.engine_models.<engine>.{os_model,worker_model}`` in the
  tenant YAML (``PUT /settings/engine``).
* **Tier choices** — ``model_selection_config.json`` rows ``corvinOS`` / ``SIMPLE``
  / ``MEDIUM`` / ``COMPLEX`` (``PUT /v1/engine/config``).

Operator rule (2026-10-13): *the models selected in the Console are always the
ones used* - not a constant in a module, not the newest-of-family default. Before
this module every call site that needed "the cheap model" or "the worker model"
carried its own literal or its own half-reader, which is how Haiku 4.5 kept
serving helper calls, ACS workers and the Tier 3 default after the Console said
Haiku 5.5.

Contract of every function here:

* returns a model id the Claude Code CLI accepts (``provider/`` prefix stripped),
  or ``None`` when the operator saved nothing for that slot - the caller then
  falls back to its newest-of-family default, never to a frozen id;
* ignores a tier row whose provider is not Anthropic - the CLI cannot run an
  OpenRouter/Ollama id, and passing it would 404 every turn;
* never raises (a helper must not fail because a config file is unreadable) and
  reads the file on every call, so a Console change applies to the NEXT call
  with no restart.

Stdlib only at import time; the tenant config reader is imported lazily.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: Console tier row -> what it drives at runtime.
#:   corvinOS - CorvinOS' own helper steps (summaries, narration, critique, titles)
#:   SIMPLE   - the low OS-turn tier and the workload "fast" tier
#:   MEDIUM   - the default OS-turn tier and the workload "full" tier
#:   COMPLEX  - the top OS-turn tier (read by the classifier via classifier_overrides)
TIER_HELPER = "corvinOS"
TIER_LOW = "SIMPLE"
TIER_HIGH = "MEDIUM"

_NATIVE_PROVIDERS = {"", "anthropic", "claude", "claude_code", "native"}


def _tenant(tenant_id: str | None) -> str:
    return (tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default").strip() or "_default"


def _strip_provider(model: str) -> str:
    # Mirrors model_selector.normalise_pin step 2: a "provider/" prefix is never
    # valid CLI input.
    return model.split("/", 1)[1] if "/" in model else model


def _normalise(model: object, engine_id: str) -> str | None:
    if not isinstance(model, str) or not model.strip():
        return None
    try:
        try:
            from model_selector import normalise_pin  # type: ignore  # noqa: PLC0415
        except ImportError:
            from .model_selector import normalise_pin  # type: ignore  # noqa: PLC0415
        out = normalise_pin(model, engine_id)
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    return _strip_provider(model.strip()) or None


def _config_module():
    try:
        from core.models import model_selection_config as m  # type: ignore  # noqa: PLC0415
        return m
    except ImportError:
        repo = str(Path(__file__).resolve().parents[3])
        if repo not in sys.path:
            sys.path.insert(0, repo)
        from core.models import model_selection_config as m  # type: ignore  # noqa: PLC0415
        return m


def saved_tier_model(
    task_type: str, *, tenant_id: str | None = None, engine_id: str = "claude_code"
) -> str | None:
    """The model the operator SAVED for a Console tier row, or ``None``."""
    try:
        tid = _tenant(tenant_id)
        m = _config_module()
        if task_type not in m.saved_task_types(tid):
            return None
        entry = (m.load_config(tid) or {}).get(task_type) or {}
        provider = str(entry.get("provider") or "").strip().lower()
        sel = str(entry.get("selected_model") or "")
        if provider not in _NATIVE_PROVIDERS:
            return None
        # "openrouter/..." style ids carry their provider in the id instead.
        head = sel.split("/", 1)[0].strip().lower() if "/" in sel else ""
        if head and head not in _NATIVE_PROVIDERS:
            return None
        return _normalise(sel, engine_id)
    except Exception:  # noqa: BLE001
        return None


def pinned_model(
    role: str, *, tenant_id: str | None = None, engine_id: str = "claude_code"
) -> str | None:
    """The Console turn pin ``spec.engine_models.<engine>.<role>``
    (``os_model`` / ``worker_model``), or ``None``."""
    try:
        try:
            from engine_models import get_tenant_engine_model  # type: ignore  # noqa: PLC0415
        except ImportError:
            from .engine_models import get_tenant_engine_model  # type: ignore  # noqa: PLC0415
        return _normalise(get_tenant_engine_model(_tenant(tenant_id), engine_id, role), engine_id)
    except Exception:  # noqa: BLE001
        return None


def helper_model(tenant_id: str | None = None) -> str | None:
    """Console choice for CorvinOS' own helper steps (``corvinOS`` row)."""
    return saved_tier_model(TIER_HELPER, tenant_id=tenant_id)
