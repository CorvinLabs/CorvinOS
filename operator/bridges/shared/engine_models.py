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
class ProviderSpec:
    """A model source + how to reach it (ADR-0181). ``credential_env`` is the env
    var NAME holding the API key (value lives in the L16 vault, never here).

    ``proxy_base_url`` (ADR-0181 M3) is the Anthropic-Messages-compatible endpoint
    used when routing an Anthropic-native engine (Claude Code) TO this provider.
    Empty ⇒ ``base_url`` is assumed Anthropic-compatible; a non-Anthropic provider
    (OpenAI/OpenRouter OpenAI-format) needs the operator to point this at a
    translating proxy (e.g. LiteLLM). Egress goes to the proxy host when set."""
    id: str
    label: str
    base_url: str = ""
    model_source: str = "static"     # static | ollama | openrouter
    credential_env: str = ""
    kind: str = "cloud"              # local | cloud
    proxy_base_url: str = ""         # Anthropic-compatible endpoint for CC→provider routing


@dataclass
class EngineProviderSupport:
    """Which provider an engine can drive. ``native=False`` = via proxy/redirect."""
    provider: str
    native: bool = True
    note: str = ""


@dataclass
class EngineModelSpec:
    engine_id: str
    label: str
    supports_os_turn: bool
    supports_worker_turn: bool
    supports_task_type_steering: bool
    os_models: list[EngineModelEntry] = field(default_factory=list)
    worker_models: list[EngineModelEntry] = field(default_factory=list)
    supported_providers: list[EngineProviderSupport] = field(default_factory=list)

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
_providers_cache: dict[str, ProviderSpec] | None = None


def _load_raw(force_reload: bool) -> None:
    """Parse the YAML once into both the engine registry + provider caches.

    On ANY read/parse failure we do NOT clobber a previously-good cache (that
    would let a transient unreadable-file window during a force-reload silently
    wipe the registry for every other reader — review MEDIUM). We only fall back
    to empty when nothing has ever loaded. Both caches are committed atomically
    at the end, so a mid-parse error can never leave one populated + one None."""
    global _registry_cache, _providers_cache  # noqa: PLW0603
    if _registry_cache is not None and _providers_cache is not None and not force_reload:
        return
    try:
        import yaml  # type: ignore[import-untyped]
        raw: dict[str, Any] = yaml.safe_load(_REGISTRY_FILE.read_text("utf-8")) or {}
        providers, result = _parse_raw(raw)
    except Exception:
        if _registry_cache is None:
            _registry_cache = {}
        if _providers_cache is None:
            _providers_cache = {}
        return
    _providers_cache = providers
    _registry_cache = result


def _parse_raw(raw: dict[str, Any]) -> "tuple[dict[str, ProviderSpec], dict[str, EngineModelSpec]]":
    # providers
    providers: dict[str, ProviderSpec] = {}
    for pid, p in (raw.get("providers") or {}).items():
        if isinstance(p, dict):
            providers[pid] = ProviderSpec(
                id=pid,
                label=str(p.get("label") or pid),
                base_url=str(p.get("base_url") or ""),
                model_source=str(p.get("model_source") or "static"),
                credential_env=str(p.get("credential_env") or ""),
                kind=str(p.get("kind") or "cloud"),
                proxy_base_url=str(p.get("proxy_base_url") or ""),
            )

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

    def _parse_providers(raw_list: Any) -> list[EngineProviderSupport]:
        out = []
        for item in (raw_list or []):
            if isinstance(item, dict) and item.get("provider"):
                out.append(EngineProviderSupport(
                    provider=str(item["provider"]),
                    native=bool(item.get("native", True)),
                    note=str(item.get("note") or ""),
                ))
        return out

    result: dict[str, EngineModelSpec] = {}
    for engine_id, entry in (raw.get("engines") or {}).items():
        if not isinstance(entry, dict):
            continue
        result[engine_id] = EngineModelSpec(
            engine_id=engine_id,
            label=str(entry.get("label") or engine_id),
            supports_os_turn=bool(entry.get("supports_os_turn", False)),
            supports_worker_turn=bool(entry.get("supports_worker_turn", False)),
            supports_task_type_steering=bool(entry.get("supports_task_type_steering", False)),
            os_models=_parse_models(entry.get("os_models")),
            worker_models=_parse_models(entry.get("worker_models")),
            supported_providers=_parse_providers(entry.get("supported_providers")),
        )
    return providers, result


def load_registry(force_reload: bool = False) -> dict[str, EngineModelSpec]:
    """Load the engine model registry from YAML. Cached after first load."""
    _load_raw(force_reload)
    return _registry_cache or {}


def load_providers(force_reload: bool = False) -> dict[str, ProviderSpec]:
    """Load the provider registry from YAML (ADR-0181). Cached after first load."""
    _load_raw(force_reload)
    return _providers_cache or {}


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
            "supported_providers": [
                {"provider": p.provider, "native": p.native, "note": p.note}
                for p in spec.supported_providers
            ],
        }
    return result


def providers_as_dict(force_reload: bool = False) -> dict[str, Any]:
    """Return the provider registry as JSON for the console API (ADR-0181).
    ``credential_env`` is the env-var NAME only — never a secret value."""
    return {
        pid: {
            "label": p.label, "base_url": p.base_url, "model_source": p.model_source,
            "credential_env": p.credential_env, "kind": p.kind,
            "proxy_base_url": p.proxy_base_url,
        }
        for pid, p in load_providers(force_reload=force_reload).items()
    }


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


def get_tenant_engine_provider(tenant_id: str, engine_id: str) -> str | None:
    """ADR-0181 — the provider assigned to <engine_id> for this tenant, or None."""
    spec = _load_tenant_spec(tenant_id)
    per_engine = (spec.get("engine_models") or {}).get(engine_id) or {}
    val = per_engine.get("provider")
    return val.strip() if isinstance(val, str) and val.strip() else None


def resolve_claude_code_provider_env(tenant_id: str) -> dict[str, str]:
    """ADR-0181 M3 — the ONE place that computes the ANTHROPIC_* redirect env
    for pointing the claude_code engine at a non-anthropic provider. Returns
    {} when claude_code should keep its default (native Anthropic) routing
    for this tenant.

    Every spawn site (adapter.py's OS-turn path, acs_runtime.py's ACS
    manager/worker paths) MUST call this instead of re-deriving the redirect
    itself. Before this consolidation (adversarial review, 2026-07-14),
    acs_runtime.py's own copy read the credential via a bare
    ``os.environ.get`` (missing a key an operator just saved through
    Settings -> API Keys until the daemon restarted) and never started the
    translating proxy for ollama/openrouter providers — it pointed
    ANTHROPIC_BASE_URL straight at their OpenAI-format ``base_url``, which
    claude_code cannot speak, breaking every ACS-delegated (manager/worker)
    turn for exactly the providers this function exists to support.

    The endpoint MUST speak the Anthropic Messages API: an operator-configured
    ``proxy_base_url`` (e.g. an externally-run LiteLLM) is honored first if
    set; otherwise, for a provider whose own API is OpenAI-format
    (ollama_local/ollama_cloud/openrouter — never anthropic-native), the
    built-in local translating proxy (anthropic_openai_bridge) is started on
    demand and used instead — no external proxy deployment required.
    """
    prov = get_tenant_engine_provider(tenant_id, "claude_code")
    if not prov or prov == "anthropic":
        return {}
    ps = load_providers().get(prov)
    if ps is None:
        return {}

    import sys
    _shared = str(Path(__file__).resolve().parent)
    if _shared not in sys.path:
        sys.path.insert(0, _shared)
    import provider_keys as _provider_keys  # type: ignore

    # Resolve through provider_keys (env, THEN service.env) rather than bare
    # os.environ — a key an operator just saved via Settings -> API Keys lands
    # in service.env immediately, but a long-running daemon's own os.environ
    # was only populated once, at process spawn; reading os.environ directly
    # would miss it until a restart.
    key = (_provider_keys.resolve_by_env_var(ps.credential_env) or ""
           if ps.credential_env else "")
    if ps.credential_env and not key:
        # A credential env-var is declared but not present — CC would be
        # redirected to the provider with a placeholder key and fail auth.
        # Surface the misconfig instead of failing silently. (Name only —
        # never the value — per the audit/PII red-line.)
        import logging
        logging.getLogger(__name__).warning(
            "[provider] %s: credential env %r is not set — claude_code will "
            "fail to authenticate against %s. Load the vault key into the "
            "process environment.", prov, ps.credential_env, ps.label,
        )

    base = ""
    if ps.proxy_base_url:
        base = ps.proxy_base_url
    elif ps.model_source in ("ollama", "openrouter"):
        try:
            from anthropic_openai_bridge import (  # type: ignore
                ProxyTarget, chat_completions_url_for, ensure_proxy)
            model = (
                get_tenant_engine_model(tenant_id, "claude_code", "os_model")
                or ("qwen3:8b" if ps.model_source == "ollama" else "")
            )
            if not model:
                # "auto" is not a valid OpenRouter model id (the real slug is
                # "openrouter/auto") — starting the proxy anyway would make
                # every turn fail with an opaque upstream 400 instead of a
                # clear error. Leave base unset so CC falls through to its
                # existing routing instead.
                import logging
                logging.getLogger(__name__).warning(
                    "[provider] %s: no model selected for claude_code and no "
                    "safe default exists for OpenRouter — pick a model on the "
                    "Engines page.", prov,
                )
            else:
                base = ensure_proxy(ProxyTarget(
                    chat_completions_url=chat_completions_url_for(
                        ps.base_url, ps.model_source),
                    api_key=key, model=model,
                    disable_reasoning=(ps.model_source == "ollama"),
                ))
        except Exception:  # noqa: BLE001 — never break the spawn
            import logging
            logging.getLogger(__name__).warning(
                "[provider] %s: failed to start the local translating proxy "
                "— falling back to base_url directly (will not speak the "
                "Anthropic API).", prov,
            )
            base = ps.base_url
    else:
        base = ps.base_url

    if not base:
        return {}
    return {
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_API_KEY": key or "provider",
        "ANTHROPIC_AUTH_TOKEN": key or "provider",
        "CORVIN_CC_PROVIDER": prov,
    }


def resolve_engine_egress(tenant_id: str, engine_id: str) -> "ProviderSpec | None":
    """ADR-0181 M3 — the effective provider an engine egresses to for this tenant
    (or None to fall back to the engine's default host). The single source of
    truth for both the L35 egress-host check and the spawn env injection, so they
    can never disagree about where the engine actually sends inference."""
    pid = get_tenant_engine_provider(tenant_id, engine_id)
    if not pid:
        return None
    spec = load_providers().get(pid)
    # The effective egress target is `proxy_base_url or base_url` (see
    # resolve_engine_egress_host). Gate on the SAME expression so a proxy-only
    # provider (proxy_base_url set, base_url empty) still resolves — otherwise
    # L35 would validate the engine's default host while the adapter redirects
    # egress to the proxy host, silently bypassing the deny/forbid policy.
    return spec if (spec and (spec.proxy_base_url or spec.base_url)) else None


def resolve_engine_egress_host(tenant_id: str, engine_id: str) -> str | None:
    """The host an engine actually egresses to when a provider is assigned (the
    proxy host if configured, else the provider host). None → use the default."""
    spec = resolve_engine_egress(tenant_id, engine_id)
    if spec is None:
        return None
    from urllib.parse import urlparse
    return urlparse(spec.proxy_base_url or spec.base_url).hostname or None
