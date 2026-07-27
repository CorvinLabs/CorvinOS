"""Feature flags — ship dark by default (CLAUDE.md § Feature Flags).

Every new feature sits behind a named flag that is **off** on a fresh install
and **off** after an upgrade, so an existing CorvinOS install never changes
behavior until the operator flips the switch in Console → Settings → Features.

Resolution order for a flag (highest first):

  1. ``features.json``  — written by the Console Settings UI (per tenant)
  2. ``spec.features.<flag_id>`` in ``tenant.corvin.yaml`` — operator-managed
  3. the registry ``default`` — ALWAYS ``False``

Mirrors the ``delegation_budget.json`` precedence already used by
``chat_runtime._delegation_budget``: the YAML is the declarative base, the
JSON overlay is what the UI writes (so the UI never has to round-trip YAML
comments).

**Compliance guard.** Security and compliance mechanisms of the Compliance
Baseline (bot disclosure, audit hash-chain, consent gate, L10 path-gate, L44
house-rules, L34 flow guard, licensing gates) MUST NOT be flaggable — a
default-off switch on them is the same violation as an env kill-flag.
``_validate_registry`` refuses such an id at import time so the rule cannot be
broken by a later contributor adding an innocuous-looking entry.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge import paths as _forge_paths

__all__ = [
    "FeatureFlag",
    "REGISTRY",
    "WORKER_ENGINE_MODES",
    "WORKER_ENGINE_DEFAULT",
    "ProtectedMechanismError",
    "UnknownFlagError",
    "flag",
    "is_enabled",
    "describe_all",
    "set_enabled",
    "worker_engine_mode",
    "set_worker_engine_mode",
]

_FLAG_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")

# Substrings that mark a compliance/security mechanism. A flag id touching one
# of these is refused: those mechanisms stay always-on and non-disableable.
_PROTECTED_SUBSTRINGS = (
    "audit",
    "consent",
    "disclosure",
    "path_gate",
    "pathgate",
    "house_rules",
    "houserules",
    "flow_guard",
    "flowguard",
    "license",
    "licence",
    "compliance",
    "gdpr",
    "erasure",
)


class ProtectedMechanismError(ValueError):
    """Raised when a registry entry would flag a compliance mechanism."""


class UnknownFlagError(KeyError):
    """Raised when an unregistered flag id is read or written."""


@dataclass(frozen=True)
class FeatureFlag:
    """One registry entry.

    ``owner`` and ``target_release`` are mandatory: a flag is a temporary
    migration device, not permanent architecture. ``target_release`` names the
    release in which the flag either flips to default-on or the feature is
    removed.
    """

    id: str
    label: str
    description: str
    owner: str
    target_release: str
    default: bool = False
    # Feature areas the flag gates, for the Settings UI grouping only.
    tags: tuple[str, ...] = field(default_factory=tuple)


# ── The registry ──────────────────────────────────────────────────────────
#
# Add every new feature here. `default` is False — there is no legitimate
# reason for a new flag to ship True (CLAUDE.md § Feature Flags).
#
# NOT here: the worker-engine choice (native | acs | tde). It is a three-way
# selection, not a boolean, and lives in `worker_engine_mode()` below — one
# setting, one source of truth. A second boolean "TDE on/off" flag next to it
# would be a second truth about the same thing.
REGISTRY: tuple[FeatureFlag, ...] = (
    FeatureFlag(
        id="execution_context_badge",
        label="Execution context badge",
        description=(
            "Attach structured per-turn execution metadata (engine, model "
            "source, token counts, delegation mode) to each turn and show it "
            "in the chat UI."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("chat", "observability"),
    ),
    FeatureFlag(
        id="ccc_command_routing",
        label="Chat command control (entity extraction)",
        description=(
            "Extract entities from every turn and dispatch chat commands "
            "(\"create task …\", \"schedule …\") before the engine runs. Costs "
            "one extra extraction pass per turn; off means turns go straight "
            "to the engine."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("chat",),
    ),
    FeatureFlag(
        id="acs_context_sync",
        label="Delegation transcript sync",
        description=(
            "After a delegated run, replay the result into the session "
            "transcript with an extra `claude -p` call (ADR-0213) so the next "
            "turn resumes with the delegation in context. Costs one additional "
            "engine call per delegated turn."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("delegation",),
    ),
    FeatureFlag(
        id="bridge_big_data_delegation",
        label="Big-data delegation on messenger bridges",
        description=(
            "Let a big-data-shaped task from Discord/WhatsApp/Telegram run on "
            "the ACS worker fan-out instead of a single turn — the same "
            "carve-out the Console already has. Charges the shared "
            "agentic-compute pool. Off means bridges behave exactly as before: "
            "every task runs as one direct turn."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("delegation", "bridges"),
    ),
    FeatureFlag(
        id="plugin_health_monitoring",
        label="Plugin health monitoring",
        description=(
            "Poll every registered plugin's health_check() on a timer and export "
            "the results (including circuit-breaker state) for the Console and "
            "NerveFiber. Off means health is only evaluated when something asks "
            "for it — no background polling, no metrics endpoint. Circuit "
            "breakers themselves stay active either way; this flag only controls "
            "the polling and the reporting surface."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("plugins", "observability"),
    ),
    FeatureFlag(
        id="plugin_runtime_lifecycle",
        label="Runtime plugin install/enable",
        description=(
            "Allow plugins to be installed, enabled, reconfigured and removed at "
            "runtime against the per-tenant registry, instead of only at boot "
            "from spec.plugins.installed. Off means the registry is read-only at "
            "runtime and plugins load exactly as they do today."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("plugins",),
    ),
    FeatureFlag(
        id="plugin_trust_enforcement",
        label="Plugin provenance enforcement",
        description=(
            "Refuse to load third-party plugins that have no provenance: a plugin "
            "claiming origin=vetted without a valid Ed25519 signature from a "
            "PINNED maintainer key is refused (never quietly downgraded), and an "
            "origin=community plugin needs an explicit per-plugin operator "
            "approval, recorded as an audit event. Off means the verdict is still "
            "computed and shown, but nothing is refused — an existing install with "
            "community plugins keeps booting exactly as before. "
            "ADR-0249. Note this buys attribution, not containment: a loaded "
            "plugin still runs in-process with the audit writer."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("plugins", "security"),
    ),
    FeatureFlag(
        id="plugin_self_healing",
        label="Plugin self-healing",
        description=(
            "Let the health collector act on a repeatedly failing plugin: "
            "circuit-break it, soft-restart it, or disable it and degrade — "
            "reversible actions only, bounded per hour, every action audited. "
            "Off means unhealthy plugins are reported but never touched. "
            "ADR-0231 gates Stage 3 on Stage 2 being stable for a release, so "
            "this stays off until an operator has that evidence."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("plugins", "observability"),
    ),
    FeatureFlag(
        id="plugin_console_surface",
        label="Plugins page in the Console",
        description=(
            "Serve the /plugins REST routes and show the Plugins page with its "
            "schema-generated settings forms. Off means the routes 404 and the "
            "nav entry is absent; plugin state is then only reachable from the "
            "tenant config file."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("plugins", "console"),
    ),
    FeatureFlag(
        id="plugin_extension_points",
        label="Plugin extension points",
        description=(
            "Let a plugin override a named step of a bundled reference "
            "implementation (routing, model selection, workflow gating) through "
            "the extension-point bus, and let a replacement plugin take over a "
            "core component entirely. Off means the bundled defaults run and "
            "registered hooks are ignored. ADR-0237/0243."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("plugins",),
    ),
    FeatureFlag(
        id="admin_control_plane",
        label="Admin control plane API",
        description=(
            "Serve /api/admin/* — the UI-independent plugin administration API "
            "(list, enable, disable, configure, health). Off means those routes "
            "404 and plugin administration stays inside the Console's own "
            "surface. The compliance layer is never disableable through it. "
            "ADR-0239/0243."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("plugins", "console"),
    ),
    FeatureFlag(
        id="bridge_supervisor_plugins",
        label="Bridges as supervised plugins",
        description=(
            "Manage the Node bridge daemons (Discord, Slack, Telegram, "
            "WhatsApp, Signal, Teams, Email) as bundled-layer plugins with "
            "start/stop/health through the plugin registry, instead of only "
            "through bridge_manager.sh. Off means bridges are managed exactly as "
            "they are today. ADR-0238/0243."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("plugins", "bridges"),
    ),
    FeatureFlag(
        id="headless_api_mode",
        label="Headless API-only boot",
        description=(
            "Allow the platform to boot with no bridges and no web UI — core "
            "plus the HTTP API only, for API-first and container deployments. "
            "Off means the boot path is unchanged and the Console is always "
            "started. ADR-0241/0243."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("plugins", "console"),
    ),
    FeatureFlag(
        id="browser_automation",
        label="Browser automation",
        description=(
            "Allow `/browser <task>` to drive a real Chrome/Chromium instance "
            "from a turn. Off means the command reports that browsing is "
            "switched off instead of launching a browser."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("tools",),
    ),
    FeatureFlag(
        id="live_model_discovery",
        label="Live model discovery",
        description=(
            "Fetch the latest model list from Anthropic (and other providers) in "
            "real time instead of using the static catalog. Refreshes every 5 "
            "minutes in the background; if fetch fails, falls back to cached list. "
            "Console UI shows model status (online/offline) and refresh timestamp."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("models", "console"),
    ),
    FeatureFlag(
        id="plugin_builder_enabled",
        label="Plugin Builder",
        description=(
            "Enable the `/plugin-builder` console command: a 4-phase interview "
            "that classifies a plugin idea (MCP-Server | Skill | Hook | Provider "
            "| Integration | Custom) and generates an Idea Doc, Architecture "
            "Concept, ADR and Build Plan plus a code scaffold (ADR-0253). Off "
            "means the command returns a pointer message and no interview "
            "session, generation or scaffold write happens."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("plugins",),
    ),
)


def _validate_registry(entries: tuple[FeatureFlag, ...]) -> None:
    seen: set[str] = set()
    for entry in entries:
        if not _FLAG_ID_RE.match(entry.id):
            raise ValueError(f"invalid feature-flag id: {entry.id!r}")
        if entry.id in seen:
            raise ValueError(f"duplicate feature-flag id: {entry.id!r}")
        seen.add(entry.id)
        if entry.default:
            raise ValueError(
                f"feature flag {entry.id!r} must default to False — new "
                "features ship dark (CLAUDE.md § Feature Flags)"
            )
        for bad in _PROTECTED_SUBSTRINGS:
            if bad in entry.id:
                raise ProtectedMechanismError(
                    f"feature flag {entry.id!r} names a compliance/security "
                    f"mechanism ({bad!r}); those stay always-on and MUST NOT "
                    "be flaggable"
                )
        if not entry.owner or not entry.target_release:
            raise ValueError(
                f"feature flag {entry.id!r} needs an owner and a target_release"
            )


_validate_registry(REGISTRY)

_BY_ID: dict[str, FeatureFlag] = {f.id: f for f in REGISTRY}


# ── Worker engine selection (CLAUDE.md § Worker Engine Selection) ─────────

WORKER_ENGINE_MODES: tuple[str, ...] = ("native", "acs", "tde")
WORKER_ENGINE_DEFAULT = "native"


# ── Storage ───────────────────────────────────────────────────────────────

_OVERLAY_NAME = "features.json"
_LOCK = threading.Lock()
_spec_cache: dict[str, tuple[float, dict]] = {}


def _overlay_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / _OVERLAY_NAME


def _read_overlay(tenant_id: str) -> dict[str, Any]:
    try:
        raw = json.loads(_overlay_path(tenant_id).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt overlay → registry defaults
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_overlay(tenant_id: str, data: dict[str, Any]) -> None:
    path = _overlay_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


def _tenant_spec(tenant_id: str) -> dict:
    """Best-effort mtime-cached read of ``tenant.corvin.yaml::spec``.

    Deliberately a local copy of ``chat_runtime._tenant_spec`` rather than an
    import: ``chat_runtime`` imports THIS module, so importing back would be a
    cycle.
    """
    try:
        p = (_forge_paths.corvin_home() / "tenants" / tenant_id
             / "global" / "tenant.corvin.yaml")
        if not p.is_file():
            return {}
        mtime = p.stat().st_mtime
        with _LOCK:
            cached = _spec_cache.get(str(p))
            if cached and cached[0] == mtime:
                return cached[1]
        import yaml  # type: ignore[import-untyped]  # noqa: PLC0415
        raw = yaml.safe_load(p.read_text("utf-8")) or {}
        spec = raw.get("spec")
        spec = spec if isinstance(spec, dict) else {}
        with _LOCK:
            _spec_cache[str(p)] = (mtime, spec)
        return spec
    except Exception:  # noqa: BLE001 — unreadable config → registry defaults
        return {}


# ── Public API ────────────────────────────────────────────────────────────

def flag(flag_id: str) -> FeatureFlag:
    """Return the registry entry, or raise ``UnknownFlagError``."""
    try:
        return _BY_ID[flag_id]
    except KeyError as exc:
        raise UnknownFlagError(flag_id) from exc


def is_enabled(flag_id: str, tenant_id: str = "_default") -> bool:
    """Resolve a flag for a tenant. Unregistered id → ``False`` (fail-dark).

    Never raises: a feature check on a broken config must degrade to the
    pre-feature code path, not to an exception in the middle of a turn.
    """
    entry = _BY_ID.get(flag_id)
    if entry is None:
        return False
    overlay = _read_overlay(tenant_id).get("flags")
    if isinstance(overlay, dict) and flag_id in overlay:
        return bool(overlay[flag_id])
    spec_flags = _tenant_spec(tenant_id).get("features")
    if isinstance(spec_flags, dict) and flag_id in spec_flags:
        return bool(spec_flags[flag_id])
    return entry.default


def _source_of(flag_id: str, tenant_id: str) -> str:
    overlay = _read_overlay(tenant_id).get("flags")
    if isinstance(overlay, dict) and flag_id in overlay:
        return "console"
    spec_flags = _tenant_spec(tenant_id).get("features")
    if isinstance(spec_flags, dict) and flag_id in spec_flags:
        return "tenant_yaml"
    return "default"


def describe_all(tenant_id: str = "_default") -> list[dict[str, Any]]:
    """Registry + resolved state, for the Settings UI."""
    return [
        {
            "id": f.id,
            "label": f.label,
            "description": f.description,
            "owner": f.owner,
            "target_release": f.target_release,
            "tags": list(f.tags),
            "default": f.default,
            "enabled": is_enabled(f.id, tenant_id),
            "source": _source_of(f.id, tenant_id),
        }
        for f in REGISTRY
    ]


def set_enabled(flag_id: str, enabled: bool, tenant_id: str = "_default") -> bool:
    """Persist a flag state in the tenant's overlay. Returns the new state."""
    flag(flag_id)  # raises UnknownFlagError for anything unregistered
    with _LOCK:
        data = _read_overlay(tenant_id)
        flags = data.get("flags")
        if not isinstance(flags, dict):
            flags = {}
        flags[flag_id] = bool(enabled)
        data["flags"] = flags
        _write_overlay(tenant_id, data)
    return bool(enabled)


def worker_engine_mode(tenant_id: str = "_default") -> str:
    """Resolve the operator-selected worker engine: native | acs | tde.

    Precedence mirrors ``is_enabled``: overlay → ``spec.web_chat.worker_engine``
    → ``native``. An unknown value degrades to ``native`` rather than raising —
    a typo in the config must not route turns to an engine nobody selected.
    """
    overlay = _read_overlay(tenant_id).get("worker_engine")
    if isinstance(overlay, str) and overlay in WORKER_ENGINE_MODES:
        return overlay
    wc = _tenant_spec(tenant_id).get("web_chat")
    if isinstance(wc, dict):
        val = wc.get("worker_engine")
        if isinstance(val, str) and val in WORKER_ENGINE_MODES:
            return val
    return WORKER_ENGINE_DEFAULT


def set_worker_engine_mode(mode: str, tenant_id: str = "_default") -> str:
    """Persist the worker-engine selection. Returns the stored mode."""
    if mode not in WORKER_ENGINE_MODES:
        raise ValueError(
            f"unknown worker engine {mode!r} — expected one of "
            f"{', '.join(WORKER_ENGINE_MODES)}"
        )
    with _LOCK:
        data = _read_overlay(tenant_id)
        data["worker_engine"] = mode
        _write_overlay(tenant_id, data)
    return mode
