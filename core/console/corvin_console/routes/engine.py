"""Console engine-selector settings — ADR-0067 M2.4.

Exposes GET/PUT /settings/engine so operators can choose the tenant-level
default engine (Claude Code vs Hermes) without editing JSON files.

Endpoints
---------
  GET  /settings/engine        → {default_engine, hermes_model, valid_engines, ollama_reachable}
  PUT  /settings/engine        → body {default_engine, hermes_model} → saves to tenant YAML
  GET  /settings/engine/health → {ollama_reachable, model_count, base_url_hash}

Settings are stored in tenant.corvin.yaml::spec.default_engine and
spec.hermes_model. These are read by the adapter's call_claude_streaming()
dispatch as the tenant-level default (Resolution order per ADR-0067:
per-chat profile.default_engine → tenant spec.default_engine → "claude_code").

Security invariants:
  - Ollama base URL is NEVER returned in responses (16-hex SHA-256 prefix only)
  - Engine values validated against VALID_ENGINES allow-list
  - All mutations require CSRF + session
  - audit console.engine_setting_updated emitted on every successful PUT

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any

import yaml  # type: ignore[import-not-found]
from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

# Module logger. Without this, the 5 `_log.*` calls in detect_engines raised
# NameError — and on a WHEEL install the very first `_log.debug` (source-tree
# import failed → trying wheel) crashed, cascading into the except handler's own
# `_log.warning` → unhandled NameError → /settings/engine/detect 500 on EVERY
# call. That broke the engine/setup UI on every fresh pip install.
_log = logging.getLogger(__name__)

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

# ---------------------------------------------------------------------------
# Engine display metadata registry
# ---------------------------------------------------------------------------
# Single source of truth for display names, descriptions, and capability
# hints shown in the console UI.
#
# Adding a new engine (e.g. DeepSeek, Gemini, vLLM):
#   1. Register the engine_id in engine_switch.py VALID_ENGINES + ENGINE_ALIASES
#   2. Optionally add an entry here for a richer description.
#      If absent, a generic fallback entry is generated automatically.
#   → The Web UI will render it on the next page load with NO code changes.

_ENGINE_METADATA: dict[str, dict] = {
    "claude_code": {
        "label": "Claude Code",
        "description": "Full-featured: /btw, hooks, skills, forge MCP, all permission modes. Best for complex reasoning and code tasks.",
        "local": False,
        "requires": "Anthropic API key (claude login)",
        "model_placeholder": "(default)",
        "model_examples": "Leave blank for Claude default",
        "model_aliases": [],
        "os_capable": True,
    },
    "codex_cli": {
        "label": "Codex CLI",
        "description": "Optimised for isolated code-generation runs. Stream-JSON output, no hooks or skills. Fast and focused.",
        "local": False,
        "requires": "OpenAI API key",
        "model_placeholder": "(default)",
        "model_examples": "Leave blank for Codex default",
        "model_aliases": [],
        "os_capable": False,
    },
    "opencode": {
        "label": "OpenCode",
        "description": "Provider-agnostic: Claude, OpenAI, Google, or local Ollama. Flexible backend without single-provider lock-in.",
        "local": False,
        "requires": "Provider key (Anthropic / OpenAI / Ollama…)",
        "model_placeholder": "ollama/qwen3:8b",
        "model_examples": "ollama/qwen3:8b · ollama-cloud/qwen3-coder-next · anthropic/claude-sonnet-5",
        "model_aliases": [],
        "os_capable": True,
    },
    "hermes": {
        "label": "Hermes",
        "description": "Fully-local via Ollama. Zero cloud egress. CONFIDENTIAL-capable (L34). No API key needed.",
        "local": True,
        "requires": "Ollama running locally",
        "model_placeholder": "hermes-balanced",
        "model_examples": "hermes-fast · hermes-balanced · hermes-capable · hermes-large",
        "model_aliases": ["hermes-fast", "hermes-balanced", "hermes-capable", "hermes-large"],
        "os_capable": True,
    },
    # ADR-0071 — GitHub Copilot CLI (github/copilot-cli, `copilot -p`).
    # Worker-only: lacks /btw live inject, hooks, skills for OS-turn use.
    # Zero incremental cost for GitHub Copilot Business/Enterprise licensees.
    "copilot": {
        "label": "GitHub Copilot",
        "description": "AI coding assistant via GitHub Copilot CLI. Zero incremental cost for Copilot Business/Enterprise licensees. Pass model='shell', 'git', or 'gh' for task-type steering, or blank for general chat.",
        "local": False,
        "requires": "GitHub Copilot subscription + copilot binary (github/copilot-cli)",
        "model_placeholder": "shell",
        "model_examples": "shell · git · gh (task-type steering) — blank for general chat",
        "model_aliases": ["shell", "git", "gh"],
        "os_capable": False,  # delegation-only; lacks live /btw, hooks, skills
    },
}

# Fallback metadata for any engine_id not listed above.
def _engine_meta_fallback(engine_id: str) -> dict:
    return {
        "label": engine_id.replace("_", " ").title(),
        "description": f"Custom engine — add metadata in routes/engine.py _ENGINE_METADATA['{engine_id}'].",
        "local": False,
        "requires": "See engine documentation",
        "model_placeholder": "",
        "model_examples": "",
        "model_aliases": [],
        "os_capable": False,
    }


def _engine_catalog() -> list[dict]:
    """Return the full engine catalog derived from engine_switch.VALID_ENGINES.

    New engines registered in engine_switch.py appear automatically here
    with fallback metadata — no code change needed in this file.
    """
    import engine_switch as _es  # noqa: PLC0415
    catalog = []
    seen: set[str] = set()
    for engine_id in _es.VALID_ENGINES:
        if engine_id in seen:
            continue
        seen.add(engine_id)
        meta = dict(_ENGINE_METADATA.get(engine_id) or _engine_meta_fallback(engine_id))
        meta["id"] = engine_id
        catalog.append(meta)
    return catalog


# Engines available as OS engine (run the OS-turn directly).
# Codex is excluded — it is delegation-only.
VALID_CONSOLE_ENGINES: tuple[str, ...] = tuple(
    e["id"] for e in _engine_catalog() if e.get("os_capable")
)

HERMES_MODEL_ALIASES: tuple[str, ...] = (
    "hermes-fast", "hermes-balanced", "hermes-capable", "hermes-large",
)

_TENANT_YAML_FILENAME = "tenant.corvin.yaml"
_OLLAMA_PROBE_TIMEOUT = 2.0

router = APIRouter(prefix="/settings/engine", tags=["console-engine"])


# ---------------------------------------------------------------------------
# Tenant YAML helpers
# ---------------------------------------------------------------------------

def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    return Path.home() / ".corvin"


def _tenant_id(tenant_id: str | None = None) -> str:
    """Return the provided tenant_id, or "_default" if None. Do NOT fall back to env var."""
    return tenant_id or "_default"


def _tenant_yaml_path(tenant_id: str) -> Path:
    # Must match adapter.py::_tenant_yaml_path — config lives in global/ subdir
    return _corvin_home() / "tenants" / tenant_id / "global" / _TENANT_YAML_FILENAME


def _load_tenant_yaml(tenant_id: str) -> dict[str, Any]:
    path = _tenant_yaml_path(tenant_id)
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _save_tenant_yaml(tenant_id: str, data: dict[str, Any]) -> None:
    path = _tenant_yaml_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via .tmp
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, default_flow_style=False))
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Ollama health probe
# ---------------------------------------------------------------------------

def _base_url_hash(base_url: str) -> str:
    """16-hex SHA-256 prefix — NEVER return the full URL."""
    return hashlib.sha256(base_url.encode()).hexdigest()[:16]


def _probe_ollama() -> dict[str, Any]:
    base_url = (
        os.environ.get("CORVIN_OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or "http://localhost:11434"
    ).rstrip("/")
    try:
        with urllib.request.urlopen(
            f"{base_url}/api/tags", timeout=_OLLAMA_PROBE_TIMEOUT
        ) as resp:
            data = json.loads(resp.read())
        model_count = len(data.get("models") or [])
        return {
            "ollama_reachable": True,
            "model_count": model_count,
            "base_url_hash": _base_url_hash(base_url),
        }
    except (urllib.error.URLError, OSError):
        return {
            "ollama_reachable": False,
            "model_count": 0,
            "base_url_hash": _base_url_hash(base_url),
        }
    except Exception:
        return {
            "ollama_reachable": False,
            "model_count": 0,
            "base_url_hash": _base_url_hash(base_url),
        }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class EngineModelConfig(BaseModel):
    model_config = {"extra": "forbid"}
    
    """Per-engine model configuration for OS-turn and worker-turn."""
    os_model: str | None = Field(None, description="OS-turn model override; null = adaptive default")
    worker_model: str | None = Field(None, description="Worker-turn model override; null = global default")
    provider: str | None = Field(
        None, description="ADR-0181 model provider id (anthropic/openai/ollama_local/"
                          "ollama_cloud/openrouter); null = engine's native provider")


class EngineSettingResponse(BaseModel):
    # OS engine (which engine IS Corvin)
    default_engine: str | None = Field(
        None,
        description="Tenant-level OS engine ('claude_code', 'opencode', 'hermes', or null for system default)",
    )
    hermes_model: str | None = Field(
        None,
        description="Hermes model alias for the OS engine (e.g. 'hermes-balanced') or null",
    )
    valid_engines: list[str] = Field(
        default_factory=list,
        description="OS engines available in the console selector",
    )
    ollama_reachable: bool = Field(False, description="Whether Ollama is reachable right now")
    # Worker engine (what the OS delegates sub-tasks to)
    default_worker_engine: str | None = Field(
        None,
        description="Tenant-level default delegation worker engine ('claude_code', 'codex_cli', 'opencode', 'hermes', 'copilot', or null = OS decides)",
    )
    default_worker_model: str | None = Field(
        None,
        description="Model for the worker engine (e.g. 'hermes-fast', 'ollama/qwen3:8b') or null",
    )
    valid_worker_engines: list[str] = Field(
        default_factory=list,
        description="Worker engines available in the console selector",
    )
    # Per-engine model config (ADR-0119)
    engine_models: dict[str, EngineModelConfig] = Field(
        default_factory=dict,
        description="Per-engine OS/worker model overrides — keyed by engine_id",
    )
    # Delegation flag — exposed so the Audit panel can explain empty ACS graph
    delegation_enabled: bool = Field(
        False,
        description="True when web_chat.delegation_enabled is set; enables the ACS Workflow Graph",
    )
    # ADR-0181 — compliance advisories raised while saving cloud-model assignments.
    compliance_warnings: list[str] = Field(
        default_factory=list,
        description="L34/L35 advisories for cloud-model assignments (non-blocking; the "
                    "spawn gate is the hard enforcement)",
    )


class EngineSettingUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    
    default_engine: str | None = Field(
        None,
        description="New OS engine; null clears the override (falls back to claude_code)",
    )
    hermes_model: str | None = Field(
        None,
        description="Hermes model alias; null clears",
    )
    default_worker_engine: str | None = Field(
        None,
        description="New worker engine for delegation; null clears (OS orchestrator decides)",
    )
    default_worker_model: str | None = Field(
        None,
        description="Model for the worker engine; null clears",
    )
    # Per-engine model config (ADR-0119)
    engine_models: dict[str, EngineModelConfig] | None = Field(
        None,
        description="Per-engine OS/worker model overrides to save; null leaves existing config unchanged",
    )


class EngineHealthResponse(BaseModel):
    ollama_reachable: bool
    model_count: int
    base_url_hash: str = Field(
        description="16-hex SHA-256 prefix of the Ollama base URL — never the full URL",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=EngineSettingResponse)
def get_engine_setting(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> EngineSettingResponse:
    """Return the current tenant-level engine settings (OS + worker).

    ADR-0007: tenant_id from SessionRecord, never env var.
    """
    data = _load_tenant_yaml(_rec.tenant_id)
    spec = data.get("spec") or {}
    health = _probe_ollama()
    # Deserialise per-engine model config (ADR-0119)
    raw_em = spec.get("engine_models") or {}
    engine_models = {
        eid: EngineModelConfig(
            os_model=cfg.get("os_model") or None,
            worker_model=cfg.get("worker_model") or None,
            provider=cfg.get("provider") or None,
        )
        for eid, cfg in raw_em.items()
        if isinstance(cfg, dict)
    }
    _wc = spec.get("web_chat") or {}
    return EngineSettingResponse(
        default_engine=spec.get("default_engine") or None,
        hermes_model=spec.get("hermes_model") or None,
        valid_engines=list(VALID_CONSOLE_ENGINES),
        ollama_reachable=health["ollama_reachable"],
        default_worker_engine=spec.get("default_worker_engine") or None,
        default_worker_model=spec.get("default_worker_model") or None,
        valid_worker_engines=[e["id"] for e in _engine_catalog()],
        engine_models=engine_models,
        delegation_enabled=bool(_wc.get("delegation_enabled", False)),
    )


@router.put("", response_model=EngineSettingResponse)
def put_engine_setting(
    body: EngineSettingUpdate,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> EngineSettingResponse:
    """Update the tenant-level default engine.

    Writes to tenant.corvin.yaml::spec.default_engine.
    The adapter reads this on the next turn — no restart needed.

    ADR-0007: tenant_id from SessionRecord, never env var.
    """
    # Validate engine value
    if body.default_engine is not None and body.default_engine not in VALID_CONSOLE_ENGINES:
        console_audit.action_failed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="engine.setting.update",
            target_kind="engine_setting",
            target_id="default_engine",
            reason="invalid_engine_value",
        )
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"default_engine {body.default_engine!r} is not valid. "
                f"Choose from: {list(VALID_CONSOLE_ENGINES)}"
            ),
        )
    # Validate hermes model alias
    if body.hermes_model is not None and body.hermes_model not in HERMES_MODEL_ALIASES:
        console_audit.action_failed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="engine.setting.update",
            target_kind="engine_setting",
            target_id="hermes_model",
            reason="invalid_model_alias",
        )
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"hermes_model {body.hermes_model!r} is not a known alias. "
                f"Choose from: {list(HERMES_MODEL_ALIASES)}"
            ),
        )
    # Validate worker engine value
    if body.default_worker_engine is not None:
        from engine_switch import VALID_ENGINES  # noqa: PLC0415
        _valid_worker = list(VALID_ENGINES.keys()) if isinstance(VALID_ENGINES, dict) else list(VALID_ENGINES)
        if body.default_worker_engine not in _valid_worker:
            console_audit.action_failed(
                tenant_id=_rec.tenant_id,
                sid_fingerprint=_rec.sid_fingerprint,
                action="engine.setting.update",
                target_kind="engine_setting",
                target_id="default_worker_engine",
                reason="invalid_worker_engine_value",
            )
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"default_worker_engine {body.default_worker_engine!r} is not valid. "
                    f"Choose from: {_valid_worker}"
                ),
            )

    data = _load_tenant_yaml(_rec.tenant_id)
    if "spec" not in data or not isinstance(data.get("spec"), dict):
        data["spec"] = {}

    if body.default_engine is None:
        data["spec"].pop("default_engine", None)
    else:
        data["spec"]["default_engine"] = body.default_engine

    if body.hermes_model is None:
        data["spec"].pop("hermes_model", None)
    else:
        data["spec"]["hermes_model"] = body.hermes_model

    # Worker engine fields
    if body.default_worker_engine is None:
        data["spec"].pop("default_worker_engine", None)
    else:
        data["spec"]["default_worker_engine"] = body.default_worker_engine
    if body.default_worker_model is None:
        data["spec"].pop("default_worker_model", None)
    else:
        data["spec"]["default_worker_model"] = body.default_worker_model

    # Per-engine model config (ADR-0119) — only written when body provides the field
    if body.engine_models is not None:
        # ADR-0181: validate any provider against the registry (+ the engine's
        # supported_providers) so a garbage provider can't persist silently.
        try:
            from engine_models import load_providers, load_registry  # type: ignore[import]
            _known_providers = load_providers()
            _registry = load_registry()
        except Exception:  # noqa: BLE001
            _known_providers, _registry = {}, {}
        serialised: dict[str, Any] = {}
        for eid, cfg in body.engine_models.items():
            if cfg.provider:
                if cfg.provider not in _known_providers:
                    raise HTTPException(
                        status_code=400,
                        detail=f"unknown provider '{cfg.provider}' for engine '{eid}'")
                _supported = {p.provider for p in getattr(_registry.get(eid), "supported_providers", [])}
                if _supported and cfg.provider not in _supported:
                    raise HTTPException(
                        status_code=400,
                        detail=f"engine '{eid}' does not support provider '{cfg.provider}'")
            entry: dict[str, Any] = {}
            if cfg.os_model:
                entry["os_model"] = cfg.os_model
            if cfg.worker_model:
                entry["worker_model"] = cfg.worker_model
            if cfg.provider:
                entry["provider"] = cfg.provider
            if entry:
                serialised[eid] = entry
        if serialised:
            data["spec"]["engine_models"] = serialised
        else:
            data["spec"].pop("engine_models", None)

    # Ensure delegation is enabled when the engine is first configured so the
    # WDAT Worker Graph is populated on fresh installs without requiring a
    # manual tenant.corvin.yaml edit.  Only sets the key when absent — an
    # explicit false in an existing config is preserved.
    _wc = data["spec"].get("web_chat")
    if not isinstance(_wc, dict):
        data["spec"]["web_chat"] = {"delegation_enabled": True}
    elif "delegation_enabled" not in _wc:
        _wc["delegation_enabled"] = True

    _save_tenant_yaml(_rec.tenant_id, data)

    try:
        console_audit.action_performed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="engine_setting_updated",
            target_kind="engine",
            target_id=body.default_engine or "cleared",
        )
    except Exception:  # noqa: BLE001
        pass

    # Re-load to get the full saved state (engine_models may be partial update)
    saved_spec = (_load_tenant_yaml(_rec.tenant_id).get("spec") or {})
    raw_em = saved_spec.get("engine_models") or {}
    engine_models = {
        eid: EngineModelConfig(
            os_model=cfg.get("os_model") or None,
            worker_model=cfg.get("worker_model") or None,
            provider=cfg.get("provider") or None,
        )
        for eid, cfg in raw_em.items()
        if isinstance(cfg, dict)
    }

    health = _probe_ollama()
    _saved_wc = saved_spec.get("web_chat") or {}
    return EngineSettingResponse(
        default_engine=body.default_engine,
        hermes_model=body.hermes_model,
        valid_engines=list(VALID_CONSOLE_ENGINES),
        ollama_reachable=health["ollama_reachable"],
        default_worker_engine=body.default_worker_engine,
        default_worker_model=body.default_worker_model,
        valid_worker_engines=[e["id"] for e in _engine_catalog()],
        engine_models=engine_models,
        delegation_enabled=bool(_saved_wc.get("delegation_enabled", False)),
        compliance_warnings=_assess_model_compliance(engine_models, _rec.tenant_id),
    )


@router.get("/health", response_model=EngineHealthResponse)
def get_engine_health(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> EngineHealthResponse:
    """Probe Ollama availability. Returns model count and base-URL hash (never full URL)."""
    health = _probe_ollama()
    return EngineHealthResponse(**health)


@router.get("/catalog")
def get_engine_catalog(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> list[dict]:
    """Return all registered engines with display metadata.

    Derived from engine_switch.VALID_ENGINES — automatically includes
    any new engine added there without requiring changes in this file.
    Each entry: {id, label, description, local, requires,
                 model_placeholder, model_examples, model_aliases, os_capable}
    """
    return _engine_catalog()


@router.get("/registry")
def get_engine_model_registry(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """Return the engine model registry — available model choices per engine.

    ADR-0119: the registry defines selectable OS-turn and worker-turn models
    for each engine. The UI uses this to populate model dropdowns.

    Response shape per engine_id:
      {
        "label": "Claude Code",
        "supports_os_turn": true,
        "supports_worker_turn": true,
        "supports_task_type_steering": false,
        "os_models":     [{"id": "...", "label": "...", "default": bool}, ...],
        "worker_models": [{"id": "...", "label": "...", "default": bool}, ...]
      }
    """
    try:
        from engine_models import registry_as_dict  # type: ignore[import]
        # force_reload → always serve the current model catalog so a registry
        # edit or package upgrade shows up on a browser refresh (no server
        # restart needed; the module otherwise caches the registry for the
        # process lifetime, which hid freshly-added models like Sonnet 5).
        return registry_as_dict(force_reload=True)
    except Exception:  # noqa: BLE001
        return {}


def _egress_denied(base_url: str, tenant_id: str) -> str | None:
    """L35: return a block message if the active egress policy denies this host,
    else None (allow). Fail-OPEN on operational error / disabled policy — matches
    the adapter's egress semantics — but honours an EXPLICIT policy denial."""
    try:
        from urllib.parse import urlparse
        from egress_gate import load_egress_gate_for_tenant  # type: ignore[import]
        host = urlparse(base_url).hostname or ""
        gate = load_egress_gate_for_tenant(tenant_id or "_default")
        if gate is None or not host:
            return None
        try:
            gate.validate_or_raise(host)
            return None
        except Exception as e:  # noqa: BLE001 — explicit denial
            return (f"Egress to '{host}' is blocked by the L35 policy — add it to "
                    f"allowed_hosts to fetch this provider's models. ({str(e)[:120]})")
    except Exception:  # noqa: BLE001 — gate unavailable → don't break the model list
        return None


def _assess_model_compliance(engine_models: dict, tenant_id: str) -> list[str]:
    """ADR-0181 — non-blocking advisories when an engine is pointed at a CLOUD
    provider. HONEST about the runtime: the ``provider`` field records the choice
    and drives the model picker/live-fetch; cloud egress happens both when the
    engine runs a cloud MODEL STRING at spawn (e.g. OpenCode's 'provider/model'
    routing) and via cross-engine PROXY routing (e.g. Claude Code -> OpenRouter,
    ADR-0181 M3, wired since 2026-07-04) — either way the pre-spawn L34/L35 gates
    apply. An engine that doesn't natively speak the target's API format (Claude
    Code -> OpenRouter/Ollama) is translated by the built-in local proxy
    (anthropic_openai_bridge, 2026-07-14), auto-started on demand — no operator
    proxy deployment required unless they want to override it with their own."""
    warnings: list[str] = []
    try:
        from engine_models import load_providers  # type: ignore[import]
        providers = load_providers()
    except Exception:  # noqa: BLE001
        return warnings
    for eid, cfg in (engine_models or {}).items():
        pid = getattr(cfg, "provider", None)
        spec = providers.get(pid) if pid else None
        if spec is None or spec.kind != "cloud":
            continue
        denied = _egress_denied(spec.base_url, tenant_id)
        if denied:
            warnings.append(f"{eid}: {denied}")
        warnings.append(
            f"{eid}: '{spec.label}' is a cloud provider. Store your API key under "
            f"{spec.credential_env or 'the provider env var'} in Settings -> API Keys. Cloud "
            f"egress occurs when the engine runs a cloud model or is proxy-routed to this "
            f"provider at spawn, where L34/L35 apply.")
    return warnings


@router.get("/providers")
def get_engine_providers(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """ADR-0181 — model providers (Anthropic, OpenAI, Ollama local/cloud,
    OpenRouter). ``credential_env`` is the env-var NAME only, never a secret."""
    try:
        from engine_models import providers_as_dict  # type: ignore[import]
        return providers_as_dict(force_reload=True)
    except Exception:  # noqa: BLE001
        return {}


@router.get("/models")
def get_provider_models(
    provider: str,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """ADR-0181 — live-fetch the models a provider offers right now (Ollama
    /api/tags, OpenRouter /models). Cloud providers are network egress: the host
    must pass the L35 gate before we reach out to it."""
    try:
        from engine_models import load_providers  # type: ignore[import]
        from engine_providers import fetch_models  # type: ignore[import]
    except Exception as exc:  # noqa: BLE001
        return {"provider": provider, "reachable": False, "models": [],
                "error": f"module error: {exc}"}
    spec = load_providers(force_reload=True).get(provider)
    if spec is None:
        return {"provider": provider, "reachable": False, "models": [],
                "error": f"unknown provider '{provider}'"}
    if spec.kind == "cloud" and spec.base_url:
        denied = _egress_denied(spec.base_url, getattr(_rec, "tenant_id", "_default"))
        if denied:
            return {"provider": provider, "reachable": False, "models": [], "error": denied}
    return fetch_models(provider, base_url=spec.base_url,
                        model_source=spec.model_source, credential_env=spec.credential_env)


@router.get("/detect")
def detect_engines(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """Probe all engines and return their installation/authentication state.

    ADR-0125 — Zero-Config Engine Onboarding.

    Response shape:
        {
          "results": [
            {
              "engine_id": "claude_code",
              "installed": true,
              "authenticated": true,
              "credential_source": "subscription",  // or "env_var"/"config_file"/"none"/null
              "version": "1.0.56",
              "models": [],          // non-empty for hermes only
              "detail": "Authenticated via Claude subscription (OAuth)"
            },
            ...
          ],
          "recommended_engine": "claude_code",   // best ready engine, or null
          "needs_bootstrap": false               // true when no engine is authenticated
        }

    Detection results are NOT written to the audit chain — they contain no
    secrets, PII, or credential values. credential_source is an enum string only.
    """
    try:
        try:
            # Source-tree mode: sys.path injection points at operator/bridges/shared/
            from engine_detection import detect_all, recommended_engine  # type: ignore[import]
            _log.debug("Loaded engine_detection from source tree")
        except ImportError:
            # Wheel install mode: bundled alongside the console package (pyproject.toml)
            _log.debug("Source-tree import failed, trying wheel mode")
            from corvin_console.engine_detection import detect_all, recommended_engine  # type: ignore[import]
            _log.debug("Loaded engine_detection from wheel bundle")

        # Run detection with timeout wrapper to catch hung probes (e.g., Hermes)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(detect_all)
            try:
                results = future.result(timeout=15)  # 15s total timeout (detect_all's 10s + buffer)
            except concurrent.futures.TimeoutExpired:
                _log.warning("engine detect_all timed out (likely Hermes hung)")
                results = []
        rec_engine = recommended_engine(results)
        needs_bootstrap = rec_engine is None

        serialised = [
            {
                "engine_id": r.engine_id,
                "installed": r.installed,
                "authenticated": r.authenticated,
                "credential_source": r.credential_source,
                "version": r.version,
                "models": r.models,
                "detail": r.detail,
            }
            for r in results
        ]
        return {
            "results": serialised,
            "recommended_engine": rec_engine,
            "needs_bootstrap": needs_bootstrap,
        }
    except Exception as exc:  # noqa: BLE001
        # Fail gracefully — detection errors must not break the console.
        # Log server-side (may contain local paths); return opaque code to client.
        # L16 compliance: never leak exception details to HTTP response.
        _log.warning(f"engine detect_all failed: {type(exc).__name__}", exc_info=True)
        return {
            "results": [],
            "recommended_engine": None,
            "needs_bootstrap": True,
            "error": "detection_failed",
        }


# ── Hermes bootstrap: async start → poll status (ADR-0125) ────────────────────
# The model pull (qwen3:8b ≈ 5 GB) takes minutes. Modelling it as a single
# blocking HTTP request fails: the SPA aborts on its default 30 s fetch timeout
# while the download is still running ("Auto-Setup läuft nicht zuende"). Instead
# we run the pull in a daemon thread and expose a status endpoint the SPA polls.
# State is process-local (single-node console); a new POST while one is running
# is a no-op that returns the in-flight state.
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAP_STATE: dict[str, Any] = {"state": "idle"}  # state: idle|running|done|error


def _import_bootstrap_hermes():
    try:
        try:
            from hermes_bootstrap import bootstrap_hermes  # type: ignore[import]
        except ImportError:
            from corvin_console.hermes_bootstrap import bootstrap_hermes  # type: ignore[import]
        return bootstrap_hermes
    except ImportError:
        return None


def _run_bootstrap_job(tenant_id: str) -> None:
    """Worker thread: pull the model, configure Hermes, record terminal state.

    Never raises — all outcomes land in ``_BOOTSTRAP_STATE``.
    """
    def _set_phase(msg: str) -> None:
        with _BOOTSTRAP_LOCK:
            _BOOTSTRAP_STATE["phase"] = msg

    try:
        bootstrap_hermes = _import_bootstrap_hermes()
        if bootstrap_hermes is None:
            with _BOOTSTRAP_LOCK:
                _BOOTSTRAP_STATE.update(
                    {"state": "error", "result": {"error": "hermes_bootstrap module not available"}}
                )
            return

        result = bootstrap_hermes(progress=_set_phase)

        # Point the tenant's OS engine at the model that was just pulled, so
        # "configured model" == "installed model" and Hermes is the default in
        # one shot (a RAM-modest box pulls qwen3:1.7b).
        if result.get("model_pulled"):
            _MODEL_TO_ALIAS = {"qwen3:1.7b": "hermes-fast", "qwen3:8b": "hermes-balanced"}
            alias = _MODEL_TO_ALIAS.get(str(result.get("model_selected", "")), "hermes-balanced")
            try:
                data = _load_tenant_yaml(tenant_id)
                if not isinstance(data.get("spec"), dict):
                    data["spec"] = {}
                data["spec"]["default_engine"] = "hermes"
                data["spec"]["hermes_model"] = alias
                _save_tenant_yaml(tenant_id, data)
                result["engine_configured"] = True
                result["hermes_model"] = alias
            except Exception:  # noqa: BLE001
                result["engine_configured"] = False

        with _BOOTSTRAP_LOCK:
            _BOOTSTRAP_STATE.update(
                {"state": "done" if result.get("model_pulled") else "error", "result": result}
            )
    except Exception as exc:  # noqa: BLE001
        with _BOOTSTRAP_LOCK:
            _BOOTSTRAP_STATE.update(
                {"state": "error", "result": {"error": f"Unexpected bootstrap error: {exc}"}}
            )


@router.post("/bootstrap")
def bootstrap_hermes_engine(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> dict:
    """Start the Hermes (Ollama) bootstrap in the background and return at once.

    ADR-0125 — installs Ollama (if missing) + pulls the RAM-appropriate qwen3
    model. The actual work runs in a daemon thread; poll GET /bootstrap/status
    for progress and the terminal result. A POST while a job is already running
    returns the in-flight state without starting a second pull.

    Audit event: engine.hermes_bootstrap_requested (target_kind=engine, target_id=hermes).
    """
    if _import_bootstrap_hermes() is None:
        raise HTTPException(
            status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
            detail="hermes_bootstrap module not available",
        )

    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAP_STATE.get("state") == "running":
            return dict(_BOOTSTRAP_STATE)
        _BOOTSTRAP_STATE.clear()
        _BOOTSTRAP_STATE.update({"state": "running", "phase": "Starting…"})

    try:
        console_audit.action_performed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="hermes_bootstrap_requested",
            target_kind="engine",
            target_id="hermes",
        )
    except Exception:  # noqa: BLE001
        pass

    threading.Thread(
        target=_run_bootstrap_job, args=(_rec.tenant_id,), daemon=True,
    ).start()

    with _BOOTSTRAP_LOCK:
        return dict(_BOOTSTRAP_STATE)


@router.get("/bootstrap/status")
def bootstrap_hermes_status(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """Return the current Hermes-bootstrap job state for the SPA to poll.

    Shape: {"state": "idle|running|done|error", "phase"?: str, "result"?: {...}}
    """
    with _BOOTSTRAP_LOCK:
        return dict(_BOOTSTRAP_STATE)


@router.get("/capabilities")
def get_engine_capabilities(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """Return the full capability matrix for all registered engines.

    ADR-0069 M5 — Engine-Agnostic OS Shell capability matrix.

    Response shape:
        {
          "engines": {
            "<engine_id>": {
              "capabilities": {...},          # WorkerEngine.capabilities dict
              "command_manifest": {           # ECI EngineCommandManifest (if present)
                "mid_stream_inject": "...",
                "cancel": "...",
                "compact": "...",
                "native_commands": {"<cmd>": {"description": "...", "usage": "..."}}
              },
              "eaos_gaps": ["<gap1>", ...]    # capabilities not yet bridged by EAOS
            }
          },
          "eaos_milestones": {                # ADR-0069 milestone status
            "M1": "done", "M2": "done", ...
          }
        }

    The capability gaps list names capabilities that are structurally
    impossible or not yet implemented for that engine (e.g. "mid_stream_inject"
    for HTTP-only engines, "plan_mode" for non-CC engines).
    """
    matrix: dict[str, Any] = {}
    try:
        from engine_registry import get_engine  # type: ignore[import]
        from engine_switch import VALID_ENGINES  # type: ignore[import]
        # VALID_ENGINES may be a dict (id→class) or a tuple/list of ids
        engine_ids = (
            list(VALID_ENGINES.keys())
            if isinstance(VALID_ENGINES, dict)
            else list(VALID_ENGINES)
        )
    except Exception:  # ImportError, AttributeError, or any runtime failure
        engine_ids = ["claude_code", "hermes", "codex_cli", "opencode", "copilot"]

    _STRUCTURAL_GAPS: dict[str, list[str]] = {
        "hermes":    ["mid_stream_inject_live", "plan_mode", "context_compaction", "session_pinning"],
        "codex_cli": ["mid_stream_inject", "plan_mode", "context_compaction", "session_pinning", "skills"],
        "opencode":  ["mid_stream_inject", "plan_mode", "context_compaction", "session_pinning"],
        "copilot":   ["mid_stream_inject", "plan_mode", "context_compaction", "session_pinning", "skills", "streaming", "hooks"],
        "claude_code": [],
    }

    for eid in engine_ids:
        try:
            from engine_registry import get_engine as _ge  # type: ignore[import]
            eng = _ge(eid)
        except Exception:  # noqa: BLE001
            eng = None

        caps = {}
        manifest_dict: dict[str, Any] | None = None
        if eng is not None:
            caps = dict(getattr(eng, "capabilities", {}) or {})
            raw_manifest = getattr(eng, "command_manifest", None)
            if raw_manifest is not None:
                native = {
                    cmd: {"description": spec.description, "usage": spec.usage}
                    for cmd, spec in (getattr(raw_manifest, "native_commands", {}) or {}).items()
                }
                manifest_dict = {
                    "mid_stream_inject": raw_manifest.mid_stream_inject,
                    "cancel": raw_manifest.cancel,
                    "compact": raw_manifest.compact,
                    "native_commands": native,
                }

        matrix[eid] = {
            "capabilities": caps,
            "command_manifest": manifest_dict,
            "eaos_gaps": _STRUCTURAL_GAPS.get(eid, []),
        }

    return {
        "engines": matrix,
        "eaos_milestones": {
            "M1": "done",    # TEB wired into Forge MCP server
            "M2": "done",    # FCB translation layer + Hermes tool-use loop
            "M3": "done",    # SkillCompiler (system-prompt path for all engines)
            "M4": "done",    # /btw buffered mode for HTTP engines
            "M5": "done",    # this endpoint
            "M6": "done",    # ECI core — EngineCommandManifest + CommandDispatcher
            "M7": "pending", # ECI Sidecar protocol (loopback HTTP, future)
        },
    }


# ---------------------------------------------------------------------------
# ADR-0126 — Claude Code Local Backend (Ollama redirect)
# ---------------------------------------------------------------------------

import re as _re  # noqa: E402 (needed below; re already imported at top via stdlib)

_CC_LOCAL_URL_RE = _re.compile(
    r"^https?://[a-zA-Z0-9._:\[\]-]+(:\d+)?(/.*)?$"
)
_CC_LOCAL_MODEL_RE = _re.compile(r"^[a-zA-Z0-9._:/\[\]-]{1,128}$")
_CC_LOCAL_PROBE_TIMEOUT = 2.0


def _probe_ollama_at(base_url: str) -> dict[str, Any]:
    """Probe Ollama model list at base_url; return {reachable, available_models}.

    Always probes <scheme>://<host>:<port>/api/tags (the Ollama model-list endpoint)
    regardless of any path in base_url — so http://localhost:11434/v1 (the
    Anthropic-compat API prefix) probes http://localhost:11434/api/tags correctly.
    """
    import urllib.parse as _urlparse
    parsed = _urlparse.urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    try:
        with urllib.request.urlopen(f"{origin}/api/tags", timeout=_CC_LOCAL_PROBE_TIMEOUT) as resp:
            data = json.loads(resp.read())
        models = [m["name"] for m in (data.get("models") or [])]
        return {"reachable": True, "available_models": models}
    except Exception:  # noqa: BLE001
        return {"reachable": False, "available_models": []}


def _validate_cc_local_url(url: str) -> str | None:
    """Return None when url is valid; return an error string when invalid."""
    if not url or len(url) > 256:
        return "base_url must be a non-empty string ≤ 256 characters"
    if not _CC_LOCAL_URL_RE.match(url):
        return "base_url must start with http:// or https:// and contain only safe URL characters"
    return None


def _validate_cc_local_model(name: str, field: str) -> str | None:
    if not name:
        return None  # blank = use Ollama default
    if not _CC_LOCAL_MODEL_RE.match(name):
        return f"{field}: model name may only contain [a-zA-Z0-9._:/\\[\\]-] (max 128 chars)"
    return None


class ClaudeLocalConfig(BaseModel):
    enabled: bool = False
    base_url: str = Field("http://localhost:11434", description="Ollama base URL (http/https)")
    sonnet_model: str = Field("", description="Model for Sonnet tier; blank = Ollama default")
    haiku_model: str = Field("", description="Model for Haiku tier; blank = Ollama default")
    opus_model: str = Field("", description="Model for Opus tier; blank = Ollama default")


class ClaudeLocalResponse(BaseModel):
    enabled: bool
    base_url: str = Field(description="Configured URL (returned for display — not a secret)")
    sonnet_model: str
    haiku_model: str
    opus_model: str
    ollama_reachable: bool
    available_models: list[str] = Field(default_factory=list)


def _load_cc_local_cfg(tenant_id: str) -> dict[str, Any]:
    data = _load_tenant_yaml(tenant_id)
    return (data.get("spec") or {}).get("claude_code_local") or {}


@router.get("/claude-local", response_model=ClaudeLocalResponse)
def get_claude_local(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> ClaudeLocalResponse:
    """Return the tenant's Claude Code Local Backend configuration (ADR-0126 M2).

    The base_url is returned in full for display purposes (it is a network address,
    not a secret — no API key or credential is stored here).
    Probe result is synchronous but capped at 2 s.
    """
    cfg = _load_cc_local_cfg(_rec.tenant_id)
    base_url = cfg.get("base_url") or "http://localhost:11434"
    probe = _probe_ollama_at(base_url)
    return ClaudeLocalResponse(
        enabled=bool(cfg.get("enabled")),
        base_url=base_url,
        sonnet_model=cfg.get("sonnet_model") or "",
        haiku_model=cfg.get("haiku_model") or "",
        opus_model=cfg.get("opus_model") or "",
        ollama_reachable=probe["reachable"],
        available_models=probe["available_models"],
    )


@router.put("/claude-local", response_model=ClaudeLocalResponse)
def put_claude_local(
    body: ClaudeLocalConfig,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> ClaudeLocalResponse:
    """Save the Claude Code Local Backend configuration (ADR-0126 M2).

    Validates base_url and model names at write time.
    Emits a compliance audit event — no config values in the event details.
    """
    # Validate inputs — always validate base_url and models, even when disabled.
    # Storing an unvalidated URL while disabled would allow SSRF: the GET handler
    # calls _probe_ollama_at(base_url) regardless of the enabled flag.
    url_err = _validate_cc_local_url(body.base_url)
    if url_err:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=url_err)
    for field_name, val in [
        ("sonnet_model", body.sonnet_model),
        ("haiku_model", body.haiku_model),
        ("opus_model", body.opus_model),
    ]:
        model_err = _validate_cc_local_model(val, field_name)
        if model_err:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=model_err
            )

    data = _load_tenant_yaml(_rec.tenant_id)
    if "spec" not in data or not isinstance(data.get("spec"), dict):
        data["spec"] = {}

    data["spec"]["claude_code_local"] = {
        "enabled": body.enabled,
        "base_url": body.base_url,
        "sonnet_model": body.sonnet_model,
        "haiku_model": body.haiku_model,
        "opus_model": body.opus_model,
    }
    _save_tenant_yaml(_rec.tenant_id, data)

    # Audit — action type varies by toggle direction (no config details in chain)
    action = (
        "claude_local_enabled" if body.enabled else "claude_local_disabled"
    )
    try:
        console_audit.action_performed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action=action,
            target_kind="engine",
            target_id="claude_code",
        )
    except Exception:  # noqa: BLE001
        pass

    probe = _probe_ollama_at(body.base_url) if body.enabled else {"reachable": False, "available_models": []}
    return ClaudeLocalResponse(
        enabled=body.enabled,
        base_url=body.base_url,
        sonnet_model=body.sonnet_model,
        haiku_model=body.haiku_model,
        opus_model=body.opus_model,
        ollama_reachable=probe["reachable"],
        available_models=probe["available_models"],
    )
