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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Registry path
# ---------------------------------------------------------------------------

_REGISTRY_FILE = (
    Path(__file__).resolve().parents[3]
    / "corvin_operator" / "bundle" / "config-templates" / "engine_model_registry.yaml"
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
    model_source: str = "static"     # static | ollama | openrouter | bedrock | vertex | foundry
    credential_env: str = ""
    kind: str = "cloud"              # local | cloud
    proxy_base_url: str = ""         # Anthropic-compatible endpoint for CC→provider routing
    #: ADR-0759 — how the engine REACHES this provider.
    #:   "api_key"  — redirect via ANTHROPIC_BASE_URL + the vault key (every
    #:                provider that existed before 2026-09-15).
    #:   "platform" — the engine speaks to it natively (Bedrock/Vertex/Foundry):
    #:                CorvinOS sets the enabling flag and forwards the platform
    #:                credential CHAIN, and MUST NOT set ANTHROPIC_BASE_URL /
    #:                ANTHROPIC_API_KEY. Setting them makes Claude Code speak
    #:                plain Anthropic to a SigV4 endpoint — every turn 403s.
    auth_mode: str = "api_key"
    #: Only meaningful for ``auth_mode == "platform"``. Keys: enable_var,
    #: region_env, default_region, host_template, runtime_host_template,
    #: project_env, resource_env, credential_vars.
    platform_env: dict = field(default_factory=dict)

    @property
    def is_platform(self) -> bool:
        return self.auth_mode == "platform"

    @property
    def egress_url(self) -> str:
        """The URL the L35 egress gate must validate for THIS provider.

        One expression, so the gate and the spawn can never disagree about where
        inference actually goes. A platform provider has no static ``base_url``
        (its host is region-derived), and gating on ``base_url`` alone therefore
        skipped the check entirely for Bedrock/Vertex — an allowlist that is
        silently not applied is worse than no allowlist, because it reads as
        enforced."""
        if self.proxy_base_url:
            return self.proxy_base_url
        if self.is_platform:
            return self.resolved_host_url()
        return self.base_url

    def resolved_region(self, env: "dict[str, str] | None" = None) -> str:
        """The region this platform provider actually addresses, from the first
        region env-var that is set, else the declared default."""
        src = env if env is not None else os.environ
        for name in self.platform_env.get("region_env") or []:
            val = (src.get(name) or "").strip()
            if val:
                return val
        return str(self.platform_env.get("default_region") or "")

    def resolved_host_url(self, env: "dict[str, str] | None" = None) -> str:
        """Region/resource-derived endpoint for a platform provider ("" if not
        derivable). The L35 egress gate validates THIS host, not a static
        placeholder — enforcement and actual egress must never disagree."""
        tpl = str(self.platform_env.get("host_template") or "")
        if not tpl:
            return self.base_url
        src = env if env is not None else os.environ
        fields = {"region": self.resolved_region(src)}
        if "{resource}" in tpl:
            resource = ""
            for name in self.platform_env.get("resource_env") or []:
                resource = (src.get(name) or "").strip()
                if resource:
                    break
            if not resource:
                return ""
            fields["resource"] = resource
        if "{region}" in tpl and not fields["region"]:
            return ""
        return tpl.format(**fields)


@dataclass
class EngineProviderSupport:
    """Which provider an engine can drive. ``native=False`` = via proxy/redirect."""
    provider: str
    native: bool = True
    note: str = ""


@dataclass
class LiveModelSource:
    """Where an engine's live model list comes from, and how it addresses ids.

    ``prefix`` is per ENGINE, not per provider: OpenCode addresses an Anthropic
    model as ``anthropic/claude-…`` while Claude Code takes the bare id, and both
    read the same cached catalogue.
    """
    provider: str
    prefix: str = ""


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
    #: Set when this engine's picker is topped up from the live catalogue.
    #: ``None`` = curated list only (Hermes, whose models are whatever Ollama has
    #: pulled locally, has no cloud catalogue to merge).
    live_models: Optional[LiveModelSource] = None

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
#: ADR-0885 step 2b — why the LAST _load_raw produced an empty registry, when it
#: did so because the YAML could not be read or parsed and nothing had ever
#: loaded. None when the last load succeeded (or a stale-good cache is being
#: served). Without this, a caller cannot tell "unreadable registry" from
#: "nothing declared": both are ``{}``.
_load_error: str | None = None


def registry_load_status() -> "tuple[bool, str | None]":
    """``(ok, error)`` for the registry as currently served.

    ``ok`` is False ONLY when the YAML failed to load and no earlier load ever
    succeeded — the one case in which ``load_registry()`` returns ``{}`` for a
    reason other than "the file declares nothing". A failure AFTER a good load
    keeps serving the stale-good cache (by design, see _load_raw) and reports
    ``(True, None)``. The status persists until a ``force_reload=True`` retries.
    """
    return (_load_error is None, _load_error)


def _load_raw(force_reload: bool) -> None:
    """Parse the YAML once into both the engine registry + provider caches.

    On ANY read/parse failure we do NOT clobber a previously-good cache (that
    would let a transient unreadable-file window during a force-reload silently
    wipe the registry for every other reader — review MEDIUM). We only fall back
    to empty when nothing has ever loaded. Both caches are committed atomically
    at the end, so a mid-parse error can never leave one populated + one None."""
    global _registry_cache, _providers_cache, _load_error  # noqa: PLW0603
    if _registry_cache is not None and _providers_cache is not None and not force_reload:
        return
    try:
        import yaml  # type: ignore[import-untyped]
        raw: dict[str, Any] = yaml.safe_load(_REGISTRY_FILE.read_text("utf-8")) or {}
        providers, result = _parse_raw(raw)
    except Exception as e:
        if _registry_cache is None:
            _registry_cache = {}
            # Nothing ever loaded: the empty registry is a FAILURE, say so.
            _load_error = f"{type(e).__name__}: {e}"[:200]
        if _providers_cache is None:
            _providers_cache = {}
        return
    _providers_cache = providers
    _registry_cache = result
    _load_error = None


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
                auth_mode=str(p.get("auth_mode") or "api_key"),
                platform_env=dict(p.get("platform_env") or {}),
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
        live_raw = entry.get("live_models")
        live = None
        if isinstance(live_raw, dict) and live_raw.get("provider"):
            live = LiveModelSource(
                provider=str(live_raw["provider"]),
                prefix=str(live_raw.get("prefix") or ""),
            )
        result[engine_id] = EngineModelSpec(
            engine_id=engine_id,
            label=str(entry.get("label") or engine_id),
            supports_os_turn=bool(entry.get("supports_os_turn", False)),
            supports_worker_turn=bool(entry.get("supports_worker_turn", False)),
            supports_task_type_steering=bool(entry.get("supports_task_type_steering", False)),
            os_models=_parse_models(entry.get("os_models")),
            worker_models=_parse_models(entry.get("worker_models")),
            supported_providers=_parse_providers(entry.get("supported_providers")),
            live_models=live,
        )
    return providers, result


def _merge_live_models(
    curated: dict[str, EngineModelSpec]
) -> dict[str, EngineModelSpec]:
    """Top up each engine's pickers from the cached live catalogue.

    ADDITIVE ONLY, and the three rules that word implies are each load-bearing:

    * **The curated entry wins.** A model already in the curated list is not
      re-added and does not lose its ``default`` flag. The curated list is the
      only list an install without an API key ever sees, so a merge that
      reordered it or moved the default would degrade the common case in order
      to serve the rare one.
    * **An empty role stays empty.** ``os_models: []`` means "this engine offers
      no model choice for that role" (Codex CLI). Filling it from the catalogue
      would invent a picker the engine cannot honour.
    * **The prefix is the engine's, not the provider's.** Same cached ids, two
      spellings: ``claude-…`` for Claude Code, ``anthropic/claude-…`` for
      OpenCode.

    Returns NEW spec objects — the curated cache is never mutated, so a catalogue
    that later goes empty cannot leave a merged model behind.
    """
    try:
        import model_catalog  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — no catalogue module ⇒ curated only
        return curated

    merged: dict[str, EngineModelSpec] = {}
    for engine_id, spec in curated.items():
        live = spec.live_models
        if live is None:
            merged[engine_id] = spec
            continue
        try:
            entries = model_catalog.catalog_models(live.provider) or []
        except Exception:  # noqa: BLE001 — a broken cache must not break the picker
            entries = []
        if not entries:
            merged[engine_id] = spec
            continue

        def _topped_up(curated_list: list[EngineModelEntry]) -> list[EngineModelEntry]:
            if not curated_list:
                return curated_list  # empty role stays empty
            known = {m.id for m in curated_list}
            extra = []
            for e in entries:
                mid = f"{live.prefix}{e.get('id', '')}"
                if not e.get("id") or mid in known:
                    continue
                known.add(mid)
                extra.append(EngineModelEntry(
                    id=mid, label=str(e.get("label") or mid), default=False,
                ))
            return curated_list + extra

        merged[engine_id] = replace(
            spec,
            os_models=_topped_up(spec.os_models),
            worker_models=_topped_up(spec.worker_models),
        )
    return merged


def load_registry(force_reload: bool = False) -> dict[str, EngineModelSpec]:
    """The engine model registry: curated YAML, topped up from the live catalogue.

    The YAML parse is cached; the MERGE is not, and deliberately so. The console
    refreshes the catalogue in a background thread, and a cached merge would mean
    a newly-shipped model only appears after a restart — which is the entire
    failure this path exists to prevent. Merging costs a dict walk over a handful
    of entries per call.
    """
    _load_raw(force_reload)
    return _merge_live_models(_registry_cache or {})


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
            "live_models": (
                None if spec.live_models is None
                else {"provider": spec.live_models.provider,
                      "prefix": spec.live_models.prefix}
            ),
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
            # ADR-0759 — the console must be able to tell a redirect provider
            # from a native platform one: they are configured, tested and
            # egress-gated differently, and rendering them identically is how
            # an operator ends up entering an API key for Bedrock.
            "auth_mode": p.auth_mode,
            "platform_env": {
                k: v for k, v in p.platform_env.items()
                # credential_vars names a credential CHAIN; the names are safe
                # (never values), and the console needs them to show what the
                # host must provide.
                if k in ("enable_var", "region_env", "default_region",
                         "project_env", "resource_env", "credential_vars",
                         "host_template", "runtime_host_template")
            },
            "resolved_host": p.resolved_host_url() if p.is_platform else p.base_url,
            "resolved_region": p.resolved_region() if p.is_platform else "",
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
    except Exception as e:
        import sys
        print(f"[WARN] _load_tenant_spec failed for '{tenant_id}': {e}", file=sys.stderr)
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

    if ps.is_platform:
        return platform_provider_env(ps)

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


def platform_provider_env(ps: "ProviderSpec",
                          source_env: "dict[str, str] | None" = None) -> dict:
    """ADR-0759 — spawn env for a NATIVE platform provider (Bedrock/Vertex/Foundry).

    Deliberately NOT symmetric with the api_key branch above:

    * No ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_API_KEY``. Claude Code on Bedrock
      signs SigV4 against a region endpoint it derives itself; a base-url
      redirect makes it speak plain Anthropic to that endpoint and every turn
      403s. Under-configuring here is recoverable, over-configuring is not.
    * The credential is a CHAIN, not one env var: an AWS profile, an IMDS/IRSA
      role, ``GOOGLE_APPLICATION_CREDENTIALS``, or an Azure federated token. We
      forward the names the platform's own SDK looks for and let it resolve
      them — which also means a host authenticating by instance role needs no
      env var at all and must NOT be reported as "credential missing".

    Returns only vars that are actually present in ``source_env`` (plus the
    enabling flag and the resolved region), so this never invents a credential.
    """
    src = source_env if source_env is not None else os.environ
    pe = ps.platform_env or {}
    out: dict[str, str] = {}
    enable_var = str(pe.get("enable_var") or "")
    if enable_var:
        out[enable_var] = "1"
    for name in pe.get("credential_vars") or []:
        val = src.get(name)
        if val:
            out[name] = val
    region = ps.resolved_region(src)
    if region:
        for name in pe.get("region_env") or []:
            out.setdefault(name, region)
    out["CORVIN_CC_PROVIDER"] = ps.id
    return out


#: Every env-var name any platform provider needs in a spawned engine process.
#: Consumed by the worker/manager spawn paths, whose secret-strip is a NAME
#: pattern (``ACCESS_KEY``/``SECRET``/``_TOKEN$``) that matches
#: ``AWS_SECRET_ACCESS_KEY``, ``AWS_SESSION_TOKEN`` and
#: ``GOOGLE_APPLICATION_CREDENTIALS`` — correct for an ordinary operator secret,
#: fatal for the ONLY credential a Bedrock/Vertex host has. Forwarding them is
#: not a weakening of that control: the OS turn already runs with them, so a
#: worker that cannot see them is strictly less capable than its own parent and
#: simply cannot authenticate at all.
def platform_credential_var_names() -> "set[str]":
    names: set[str] = set()
    for ps in load_providers().values():
        if not ps.is_platform:
            continue
        pe = ps.platform_env or {}
        enable = pe.get("enable_var")
        if enable:
            names.add(str(enable))
        for key in ("credential_vars", "region_env", "project_env", "resource_env"):
            for n in pe.get(key) or []:
                names.add(str(n))
    return names


def active_platform_provider(source_env: "dict[str, str] | None" = None) -> "ProviderSpec | None":
    """The platform provider this HOST is configured for, read from the enabling
    flag alone (``CLAUDE_CODE_USE_BEDROCK=1`` …). Independent of tenant config:
    it is a property of the machine's Claude Code install, and it is what makes
    a leftover OAuth credentials file the WRONG answer to "how is this host
    authenticated" (engine_detection ordering, ADR-0759)."""
    src = source_env if source_env is not None else os.environ
    for ps in load_providers().values():
        if not ps.is_platform:
            continue
        enable = str((ps.platform_env or {}).get("enable_var") or "")
        if enable and (src.get(enable) or "").strip() in ("1", "true", "True", "TRUE"):
            return ps
    return None


def resolve_engine_egress(tenant_id: str, engine_id: str) -> "ProviderSpec | None":
    """ADR-0181 M3 — the effective provider an engine egresses to for this tenant
    (or None to fall back to the engine's default host). The single source of
    truth for both the L35 egress-host check and the spawn env injection, so they
    can never disagree about where the engine actually sends inference."""
    pid = get_tenant_engine_provider(tenant_id, engine_id)
    if not pid:
        return None
    spec = load_providers().get(pid)
    if spec is None:
        return None
    # The effective egress target is `proxy_base_url or base_url` (see
    # resolve_engine_egress_host). Gate on the SAME expression so a proxy-only
    # provider (proxy_base_url set, base_url empty) still resolves — otherwise
    # L35 would validate the engine's default host while the adapter redirects
    # egress to the proxy host, silently bypassing the deny/forbid policy.
    # A platform provider (ADR-0759) has NO static base_url at all — its host is
    # region-derived — so it resolves on that derived host instead. Returning
    # None for it would have L35 validate api.anthropic.com while the engine
    # actually talks to bedrock-runtime.<region>.amazonaws.com.
    if spec.is_platform:
        return spec if spec.resolved_host_url() else None
    return spec if (spec.proxy_base_url or spec.base_url) else None


def resolve_engine_egress_host(tenant_id: str, engine_id: str) -> str | None:
    """The host an engine actually egresses to when a provider is assigned (the
    proxy host if configured, else the provider host). None → use the default."""
    spec = resolve_engine_egress(tenant_id, engine_id)
    if spec is None:
        return None
    from urllib.parse import urlparse
    target = spec.resolved_host_url() if spec.is_platform else (
        spec.proxy_base_url or spec.base_url)
    return urlparse(target).hostname or None


# ---------------------------------------------------------------------------
# Workload-based model tier routing (ADR-0043)
# ---------------------------------------------------------------------------

# Per-engine model tier definitions: maps engine→workload→model_id
# "fast" tier is used for CHAT workloads (fast, cheap)
# "full" tier is used for CODE workloads (capable, slow)
_MODEL_TIER_MAPPING: dict[str, dict[str, str]] = {
    # Keys MUST be real registry engine ids (see load_registry()). Earlier
    # revisions carried phantom entries ("gemini", "codex", "ollama_local")
    # with retired/nonexistent model ids that _model_is_valid waved through
    # because the engines were unknown to the registry — pruned 2026-07-18.
    # New engines register here only together with a registry entry.
    "claude_code": {
        "fast": "claude-haiku-4-5-20251001",
        "full": "claude-sonnet-5",
    },
}


def get_model_tier_mapping() -> dict[str, dict[str, str]]:
    """Return a deep copy of the model tier mapping (callers must not be
    able to mutate the module state through the return value)."""
    return {engine: dict(tiers) for engine, tiers in _MODEL_TIER_MAPPING.items()}


def model_is_registered(model_id: str | None, engine: str) -> bool:
    """Is ``model_id`` one of ``engine``'s registered models? FAIL-CLOSED.

    Lifted out of :func:`resolve_model_for_workload`'s inner scope on 2026-07-27
    so the ADR-0251 ``engine.model_selection`` call site can apply the SAME
    admissibility rule a plugin's answer must meet. Re-implementing it there
    would be two registry rules that agree until one is edited.

    Every failure path answers False: unknown engine, unreadable registry, empty
    id. A permissive default here let phantom tier-map entries return
    nonexistent models (adversarial review 2026-07-18) and would now also let a
    plugin name a model the operator never installed.
    """
    if not model_id:
        return False
    try:
        registry = load_registry()
        engine_spec = registry.get(engine)
        if not engine_spec:
            import sys
            print(f"[WARN] model_is_registered: engine '{engine}' not in registry "
                  f"— refusing model", file=sys.stderr)
            return False
        all_models = [m.id for m in engine_spec.os_models] + [
            m.id for m in engine_spec.worker_models
        ]
        is_valid = model_id in all_models
        if not is_valid:
            import sys
            print(f"[WARN] model_is_registered: model '{model_id}' not in engine "
                  f"'{engine}' registry", file=sys.stderr)
        return is_valid
    except Exception as e:
        import sys
        print(f"[WARN] model_is_registered: registry load failed ({e}) — refusing "
              f"model '{model_id}'", file=sys.stderr)
        return False


def resolve_model_for_workload(
    engine_id: str,
    workload_type: "str | object | None" = None,
    user_chosen_model: str | None = None,
    confidence: float | None = None,
    fast_chat_enabled: bool = False,
) -> str | None:
    """Resolve the actual model to use based on engine, workload classification,
    and user's chosen model.

    ADR-0043 Phase 1: Route CHAT workloads (high confidence) to the engine's fast tier,
    CODE to full tier, UNCERTAIN to user's choice (safe fallback).

    Args:
        engine_id: The engine identifier (e.g., "claude_code", "gemini")
        workload_type: Classification result (str or WorkloadType enum, or None).
                       If enum, extracted as .value first.
        user_chosen_model: The model the user pinned (if any)
        confidence: Classification confidence (0.0-1.0). Used to gate CHAT→fast routing.
        fast_chat_enabled: Whether fast-chat routing is enabled (feature flag).

    Returns:
        The resolved model ID string, or None if unable to determine.

    Logic:
        - If workload is "chat" AND confidence >= 0.7 AND fast_chat_enabled:
          use engine's "fast" tier (or user choice if unavailable)
        - If workload is "code": use user's choice (None → caller's tiers decide)
        - If workload is "uncertain" or unknown: use user's choice (safe fallback)
        - Model IDs are validated FAIL-CLOSED against load_registry();
          unknown engine / failed load / invalid id → user_choice
    """
    # Normalize workload type: handle both WorkloadType enum and string
    if workload_type is None:
        workload = "uncertain"
    elif isinstance(workload_type, str):
        # It's a string; use as-is after normalization
        workload = workload_type.lower().strip()
    elif hasattr(workload_type, "value"):
        # It's an enum; extract the .value
        workload = str(workload_type.value).lower().strip()
    else:
        # Unknown type; log and reject
        import sys
        print(f"[WARN] resolve_model_for_workload: invalid workload type (not str/enum): {type(workload_type)}", file=sys.stderr)
        return user_chosen_model

    # Safe fallback for unknown/uncertain workload
    if workload not in ("chat", "code", "uncertain"):
        import sys
        print(f"[WARN] resolve_model_for_workload: unknown workload type '{workload}', falling back to user_choice", file=sys.stderr)
        return user_chosen_model

    # Look up the engine's tier mapping
    tiers = _MODEL_TIER_MAPPING.get(engine_id)
    if tiers is None:
        # Unknown engine: use user's choice, no tier-based routing
        return user_chosen_model

    # Helper: validate that a model_id exists in the engine's registry.
    # FAIL-CLOSED: an unknown engine or a failed registry load returns False,
    # which makes the caller fall back to the user's chosen model. The old
    # permissive behaviour let phantom tier-map entries return nonexistent
    # models (adversarial review 2026-07-18).
    # Module-level since 2026-07-27 so the ADR-0251 model-selection call site
    # applies the same rule; the local name is kept so the reads below are
    # unchanged.
    _model_is_valid = model_is_registered

    # CHAT routing: use fast tier only if confidence is high and feature is enabled
    if workload == "chat":
        # Validate and clamp confidence to [0.0, 1.0]
        # Reject NaN/Infinity explicitly
        if confidence is None:
            conf = 0.0
        else:
            try:
                conf = float(confidence)
                # Reject NaN/Infinity AND out-of-range values. A confidence
                # of 5.0 is corrupt input, not "very confident" — clamping
                # it to 1.0 routed corrupt data toward the fast tier; the
                # safe direction for garbage is the user's model (0.0).
                if conf != conf or not (0.0 <= conf <= 1.0):
                    import sys
                    print(f"[WARN] resolve_model_for_workload: invalid confidence '{confidence}' (NaN/Inf/out-of-range), treating as 0.0", file=sys.stderr)
                    conf = 0.0
            except (ValueError, TypeError):
                conf = 0.0

        if fast_chat_enabled and conf >= 0.7:
            fast_model = tiers.get("fast")
            # Validate fast tier model; fallback to user choice if invalid
            if _model_is_valid(fast_model, engine_id):
                return fast_model
            else:
                return user_chosen_model
        else:
            # Not confident enough, or feature not enabled: use user choice
            return user_chosen_model

    # CODE routing: the user's choice, period. Returning the "full" tier
    # here hard-pinned Sonnet for every code-classified turn of un-pinned
    # users, silently bypassing the adaptive ADR-0112 selection downstream
    # (adversarial review 2026-07-18). None → caller's own tiers decide.
    elif workload == "code":
        return user_chosen_model

    else:
        # Shouldn't reach here, but be safe
        return user_chosen_model
