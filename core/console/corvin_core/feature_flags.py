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
    "recovery_command",
    "set_enabled",
    "worker_engine_mode",
    "set_worker_engine_mode",
    "tier_of",
    "can_promote_to",
    "migrate_flags_to_alpha",
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

    ``release_tier`` (ADR-0286): semantic maturity classification for automatic
    graduation. alpha → beta → stable → production. Starts as alpha; promotes
    automatically based on metrics. Demotes automatically on error spike.
    """

    id: str
    label: str
    description: str
    owner: str
    target_release: str
    default: bool = False
    # Feature areas the flag gates, for the Settings UI grouping only.
    tags: tuple[str, ...] = field(default_factory=tuple)
    # A SELF-LOCKING flag removes the surface that can switch it back off.
    # `headless_api_mode` is the archetype: on, it unmounts /console/, so the
    # Settings panel that flipped it no longer exists. Such a flag is not a
    # rollout switch, it is a deployment mode, and it needs two things a normal
    # flag does not: an explicit confirmation before it is turned ON, and an
    # off-ramp that does not go through the Console. Both hang off this field
    # so the UI never hard-codes a flag id — see `recovery_command()` and
    # `corvin config set features.<id> false`.
    self_locking: bool = False
    # Release tier for automatic promotion (ADR-0286, ADR-0288)
    release_tier: str = "alpha"  # alpha | beta | stable | production
    # Timestamp when tier was set/promoted (for metrics tracking)
    released_date: str | None = None
    # Maintainer who promoted to current tier (for audit trail)
    promoted_by: str | None = None


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
        label="Execution context badge (capture only — no badge yet)",
        description=(
            "INCOMPLETE, and this toggle currently changes nothing — audited "
            "2026-07-28. Per-turn execution metadata (engine, model source, "
            "token counts, delegation mode) IS captured and persisted into "
            "turns.jsonl on every console turn, and the Audit view and the "
            "turn filter in /chat/turns both read it. What does not exist is "
            "the badge: no console component renders execution_context, and "
            "on the messenger bridges execution_context_renderer.js plus its "
            "six daemon call sites never fire because adapter.py never puts "
            "an execution_context key on an outbox payload. Nothing reads "
            "this flag id either — grep it. Flipping it on or off is a no-op "
            "on both surfaces. Left registered rather than deleted because "
            "the capture half is real and shipped; the flag becomes live when "
            "a renderer does. Do NOT add a second setting for the same thing: "
            "the bridge renderer's `show_execution_context` key is exactly "
            "that mistake, and it is dead for the same reason."
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
        id="tde_shadow_measurement",
        label="TDE shadow measurement",
        description=(
            "ADR-0222: after a NATIVE turn, run a detached shadow measurement — "
            "the tool-less {direct, tier, tde} arms — so the decision gate gets "
            "real per-band evidence WITHOUT ever showing the user a TDE answer. "
            "The native answer is only the trigger, never a measured arm. Also "
            "requires TDE_MEASUREMENT_ENABLED=1 and passing the sample rate. The "
            "TDE arm is a real fan-out and books the shared compute pool (self-"
            "limiting: an exhausted pool drops the sample). Costs extra compute "
            "per sampled turn — enable only during a measurement week."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("delegation", "measurement"),
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
        id="bridge_worker_engine_parity",
        label="Worker-engine parity on messenger bridges",
        description=(
            "Route a bridge turn (Discord/Telegram/Slack/WhatsApp/etc.) through "
            "the SAME native/acs/tde decision the console uses — the operator's "
            "worker_engine mode, an explicit /delegate override, and the "
            "console's own triage heuristic — instead of only the narrow "
            "big-data-shaped carve-out. Makes spec.engine_models.<engine_id>."
            "worker_model reachable on bridges for ordinary conversation, not "
            "only big-data prompts. TDE stays unreachable via THIS flag "
            "(mode=tde degrades to native here) — real TDE execution on bridges "
            "is the separate opt-in bridge_tde_execution flag (ADR-0221/0222). "
            "Off means bridges behave exactly as before: only "
            "bridge_big_data_delegation's narrow carve-out applies. ADR-0255."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("delegation", "bridges"),
    ),
    FeatureFlag(
        id="bridge_tde_execution",
        label="TDE execution on messenger bridges (measured opt-in)",
        description=(
            "Single-operator measured test (TDE_ROBUST_USABLE_PLAN Step 4, "
            "ADR-0221/0222): lift the bridge TDE freeze for THIS tenant. When on, "
            "a bridge turn with worker_engine=tde actually runs the Tiered "
            "Delegation Engine (via the shared TDE core) instead of degrading to "
            "native, and — if TDE_MEASUREMENT_ENABLED=1 — a detached background "
            "thread measures each run against the tool-less {direct, tier} "
            "baselines (measurement.jsonl) on your REAL messenger tasks, feeding "
            "the ADR-0222 gate (corvin tde gate). Robust: ANY TDE failure or an "
            "exhausted shared pool degrades to the native turn. Each TDE turn "
            "books the shared compute pool. This flag alone unlocks TDE (not the "
            "broader ACS parity); enable only for a deliberate measurement run."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("delegation", "measurement", "bridges"),
    ),
    FeatureFlag(
        id="delegation_badge",
        label="Delegation transparency badge",
        description=(
            "Show which delegation ran a task on BOTH surfaces: the console "
            "engine chip and a compact text-suffix on messenger-bridge replies "
            "(e.g. '⚙ TDE · tiered', '⚙ ACS · loop', '⚙ native'), so it is always "
            "traceable how a task was delegated. Uses the shared "
            "execution_context.format_delegation_badge() so both surfaces read "
            "identically. Off = no badge (unchanged)."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("delegation", "ux"),
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
        id="a2a_relay_fallback",
        label="A2A encrypted relay fallback",
        description=(
            "ADR-0258 Stage 3: when a direct A2A send()/ping() to a paired peer "
            "fails (peer behind CGNAT, hotel/airport WiFi, roaming with no "
            "direct route — the case Stage 1's LAN reconnect and Stage 2's "
            "mesh-VPN detection cannot cover), retry the SAME signed envelope "
            "through a configured relay instead of failing immediately. The "
            "envelope is AES-256-GCM encrypted with a key derived from the "
            "pairing's own hmac_key before it ever reaches the relay — the "
            "relay is a dumb pipe and cannot read routed content even if "
            "fully compromised, but it CAN see routing metadata (which kid "
            "talks to which, timing, volume). This is a genuine trust-model "
            "change (a third party — even a blind one — now sits in the path "
            "for some messages), which is why it ships dark: off means every "
            "send()/ping() behaves byte-identically to before this stage "
            "existed, even with a relay URL configured. Turning this on with "
            "no relay URL set (Settings -> A2A -> Relay URL) is a no-op, not "
            "an error."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("a2a",),
    ),
    FeatureFlag(
        id="a2a_lan_bind",
        label="Bind A2A/Console to all network interfaces (0.0.0.0)",
        description=(
            "Off (default): corvin serve / the login-autostart / the opt-in "
            "always-on service all bind 127.0.0.1 only — the loopback binding "
            "IS the security boundary for the A2A receiver (/v1/a2a/receive, "
            "/v1/a2a/ping, /v1/a2a/friendship-ack all live on the same port) "
            "and the Console API. On: the NEXT server start binds 0.0.0.0 "
            "instead, so a paired peer on the same LAN can reach this instance "
            "directly (Stage 1, no relay needed) without hand-editing a "
            "--host flag or a systemd/Task-Scheduler unit. This is a genuine "
            "trust-model change — anything on the local network segment can "
            "then reach the A2A endpoints and, unless a firewall rule is also "
            "added, attempt to pair. Does NOT take effect on an already-"
            "running process — restart corvin serve (or the autostart "
            "service) after flipping this for it to bind the new interface. "
            "An explicit --host CLI flag on corvin serve always overrides "
            "this flag in either direction."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("a2a", "network"),
    ),
    FeatureFlag(
        id="headless_api_mode",
        label="Headless API-only boot",
        description=(
            "Allow the platform to boot with no bridges and no web UI — core "
            "plus the HTTP API only, for API-first and container deployments. "
            "Off means the boot path is unchanged and the Console is always "
            "started. ADR-0241/0243. SELF-LOCKING: while this is on there is no "
            "/console/ to switch it back off from. The off-ramp is the CLI — "
            "`corvin config set features.headless_api_mode false`, then restart "
            "the service."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("plugins", "console"),
        self_locking=True,
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
        release_tier="beta",
    ),
    FeatureFlag(
        id="plugin_builder_idea_first_interview",
        label="Plugin Builder: idea-first interview",
        description=(
            "Replace the fixed 5+6-question interview with one open idea "
            "question plus a short-name question; the safety-relevant "
            "Dependencies fields (external libs, auth, network egress, "
            "egress hosts) are inferred from that free text where possible, "
            "and only the unresolved ones are asked (ADR-0262). Also detects "
            "and pins the session's language from the first answer. Off "
            "means the original ADR-0253 question sequence, unchanged. "
            "Requires `plugin_builder_enabled`."
        ),
        owner="maintainer",
        target_release="0.13.x",
        tags=("plugins",),
    ),
    FeatureFlag(
        id="plugin_builder_checkpoint_review",
        label="Plugin Builder: checkpoint review",
        description=(
            "Insert a Zwischenstand checkpoint between generating the Idea/"
            "Architecture/ADR/Build-Plan docs and writing the code scaffold: "
            "a text and voice summary of the docs and the classification "
            "(risk flags carried verbatim) is shown, and a second explicit "
            "confirmation is required before any code is written (ADR-0262). "
            "Off means `confirm` writes docs and scaffold together in one "
            "step, as ADR-0253 always did. Requires `plugin_builder_enabled`."
        ),
        owner="maintainer",
        target_release="0.13.x",
        tags=("plugins",),
    ),
    FeatureFlag(
        id="plugin_builder_generate_e2e_tests",
        label="Plugin Builder: generate E2E tests",
        description=(
            "Generate edge-case tests and, for a Provider plugin type the "
            "live Extension-Surface Map (ADR-0245) marks as consumed, a real "
            "registration wiring test alongside the scaffold — an honest "
            "`pytest.mark.skip` naming the live reason for a type nothing "
            "invokes yet, never a fabricated pass (ADR-0262). Off means a "
            "scaffold with no generated tests, as ADR-0253 always produced. "
            "Requires `plugin_builder_enabled`."
        ),
        owner="maintainer",
        target_release="0.13.x",
        tags=("plugins",),
    ),
    FeatureFlag(
        id="plugin_builder_ideas_mode",
        label="Plugin Builder: --ideas co-ideation mode",
        description=(
            "Enable `/plugin-builder --ideas`: a bounded, moderated dialogue "
            "where CorvinOS proposes plugin ideas grounded in real signals "
            "(Extension-Surface Map gaps, Marketplace category sparsity, "
            "what the user already said) instead of the user arriving with "
            "one already formed (ADR-0263). Converges into the SAME idea-"
            "first interview flow `plugin_builder_idea_first_interview` "
            "governs for a plain `/plugin-builder` call — but always uses "
            "that flow regardless of that flag's own on/off state, since "
            "--ideas has no other flow to hand its converged idea into. "
            "Writes nothing to disk on its own. Off means `--ideas` is not "
            "recognized. Requires `plugin_builder_enabled`."
        ),
        owner="maintainer",
        target_release="0.13.x",
        tags=("plugins",),
    ),
    FeatureFlag(
        id="package_marketplace_ui",
        label="Package Marketplace UI redesign",
        description=(
            "Show the redesigned Package Marketplace interface with package "
            "cards, search, and details modal. The new UI provides a modern, "
            "user-friendly way to discover, view, and manage installed packages "
            "with license, dependencies, and permissions information. Off means "
            "the marketplace page is not accessible."
        ),
        owner="maintainer",
        target_release="0.10.x",
        tags=("console", "packages"),
    ),
    FeatureFlag(
        id="model_catalog_auto_refresh",
        label="Auto-refresh the live model catalogue",
        description=(
            "When the Settings → AI Engines page loads, refresh the cached model "
            "list for cloud providers in the background, so a newly released "
            "model appears in the pickers on its own instead of waiting for a "
            "package upgrade (ADR-0181). Gated: a fresh cache never egresses, a "
            "failed attempt is not retried for an hour (a failed fetch writes "
            "nothing, so without the floor it would refetch on every page load), "
            "an in-flight refresh is never duplicated, an L35-denied host is "
            "never contacted, and a broken refresh can never take the Engines "
            "page down. Off (default) means the catalogue only changes on an "
            "explicit per-provider fetch or a package upgrade — no page load "
            "ever reaches the network."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("console", "engine", "egress"),
    ),
    FeatureFlag(
        id="vibe_engineering",
        label="Vibe Engineering — CEL brief on live turns",
        description=(
            "Run the consolidated Context Engineering pipeline (memory → graph → "
            "skill) via a single build_brief() BEFORE the pre-spawn gates and "
            "inject the resulting brief into the OS-turn system prompt "
            "(ADR-0275 P-1). Off means turns are assembled exactly as before, "
            "with no CEL brief — a quiet, unchanged path. The brief only shapes "
            "the prompt; the L34/L44/L35 gates still inspect the task text "
            "(invariant I1)."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("chat", "context-engineering"),
        release_tier="beta",
    ),
    FeatureFlag(
        id="vibe_engineering_active",
        label="Vibe Engineering — ACTIVE brain (LLM synthesis + ToolForge/SkillForge)",
        description=(
            "On top of vibe_engineering: run the FULL Context Brain on live turns — "
            "an LLM synthesis stage that assembles the single best worker prompt, "
            "plus ToolForge and SkillForge stages that provision the worker with "
            "forged tools/skills, not just a text brief (ADR-0282/0283). Every "
            "egress/forge stage runs POST-gate behind the two-gate enforcer: Gate-1 "
            "on the task, then egress cloud LLM synthesis, then Gate-2 re-inspects "
            "the synthesised prompt + forged tool names through the SAME L44 "
            "classifier before the spawn, and bound tools are class-re-validated "
            "against the persona's own allow-list (bind ≠ authorise). Needs cloud "
            "egress: under a zero-egress residency policy (L35) it degrades to the "
            "deterministic brief. Off (default) means only the deterministic "
            "memory→graph→skill brief runs — no cloud LLM call, nothing forged. "
            "Reaches BOTH live turn surfaces: the messenger bridges "
            "(adapter._resolve_spawn_inputs, sync) and the Console web-chat "
            "(chat_runtime.stream_turn, async — the blocking synthesis subprocess "
            "runs on asyncio.to_thread so it never stalls the event loop). Until "
            "review R6 only the bridge did, so an operator who authored an "
            "egress/forge pipeline in the Console's own Context Pipeline editor got "
            "those stages recorded 'deferred' on every Console turn and never run."
        ),
        owner="maintainer",
        target_release="0.13.x",
        tags=("chat", "context-engineering", "forge"),
        release_tier="alpha",
    ),
    FeatureFlag(
        id="auto_load_github_repo",
        label="Auto-load GitHub repo in Cross-Device Learning",
        description=(
            "Automatically populate the GitHub repository URL from the merged-state "
            "config when opening the Cross-Device Learning dashboard. On means the "
            "repo URL is pre-filled when the Settings panel loads; off means the "
            "field starts empty and the user must manually enter it each time "
            "(ADR-0275)."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("multi-instance", "console"),
        release_tier="beta",
    ),
    FeatureFlag(
        id="validator_factory_enabled",
        label="Input Validator Factory (ADR-0296)",
        description=(
            "Enable centralized, pluggable input validation with deny-by-default "
            "behavior. When on, all user input is validated through the "
            "ValidatorFactory before reaching business logic, with fail-closed "
            "behavior on validation errors. Tenant-isolated validation via "
            "keyword-only tenant_id parameter. Supports built-in validators "
            "(string, integer, email, URL, peer_id, flag_id, UUID) and composite "
            "validators (AND/OR/NOT). Invalid input returns 400 Bad Request with "
            "non-specific error codes for security."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("validation", "security", "compliance"),
        release_tier="alpha",
    ),
    FeatureFlag(
        id="file_permissions_enabled",
        label="File Permission Hardener (ADR-0295)",
        description=(
            "Enable fine-grained file-write protection with fail-closed semantics. "
            "When on, the FilePermissionManager enforces per-path permission rules, "
            "tenant-scoped file access (GDPR Art. 32), and audit trail integration. "
            "Protected paths (audit logs, vaults, instance keys, license) are "
            "always protected. Custom rules support whitelists, deny patterns, and "
            "permission inheritance (directory → children). Deny rules take priority "
            "over allow rules for safety. All checks are audit-logged. Off means "
            "file operations follow the current path-gate rules only (L10)."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("security", "compliance", "file-access"),
        release_tier="alpha",
    ),
    FeatureFlag(
        id="dual_gate_pipeline_enabled",
        label="Dual-Gate Pipeline: Input Validation (ADR-0300)",
        description=(
            "Enable Gate 2a of the dual-gate context pipeline: input validation. "
            "When on, all operations with input_data must pass the ValidatorFactory "
            "checks before execution (ADR-0296). Off means validation is skipped "
            "and the operation proceeds after capability check. Fail-closed: "
            "invalid input → ValidationGateError, audited, denied. Validator "
            "configuration lives in PipelineContext.validator_rules. Tenant-scoped "
            "validation with tenant_id required. GDPR Art. 32: input integrity is "
            "load-bearing for data protection."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("security", "validation", "pipeline"),
        release_tier="alpha",
    ),
    FeatureFlag(
        id="dual_gate_pii_detection_enabled",
        label="Dual-Gate Pipeline: PII Detection (ADR-0300)",
        description=(
            "Enable Gate 2b of the dual-gate context pipeline: PII detection. "
            "When on, all operations with input_data are scanned for PII patterns "
            "(email, phone, SSN, credit card, etc.) using the PIIDetector (ADR-0297). "
            "Off means PII scanning is skipped. Fail-closed: PII detected → "
            "PIIDetectionError, audited, denied. Unknown patterns default to SAFE "
            "(not rejected). Tenant-scoped detection with tenant_id required. "
            "GDPR Art. 5, 32: PII protection is load-bearing."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("security", "pii", "pipeline", "compliance"),
        release_tier="alpha",
    ),
    FeatureFlag(
        id="dual_gate_queue_integrity_enabled",
        label="Dual-Gate Pipeline: Queue Integrity Check (ADR-0300)",
        description=(
            "Enable Gate 2c of the dual-gate context pipeline: queue integrity check. "
            "When on, operations must pass the QueueIntegrityMonitor health check "
            "before execution (ADR-0298). Off means queue integrity checks are "
            "skipped. Fail-closed: queue unhealthy → QueueIntegrityError, audited, "
            "denied. Monitors for hash-chain breaks, timestamp disorders, duplicate "
            "event IDs, and disk I/O errors. Tenant-scoped monitoring with audit "
            "trail integration. GDPR Art. 30, 32: audit trail integrity is load-bearing."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("security", "compliance", "audit", "pipeline"),
        release_tier="alpha",
    ),
    FeatureFlag(
        id="queue_corruption_detection_enabled",
        label="Queue Corruption Detection & Recovery (ADR-0298)",
        description=(
            "Enable automatic detection and recovery from audit queue corruption "
            "(ADR-0298). When on, the QueueIntegrityMonitor detects hash-chain "
            "breaks, timestamp disorders, duplicate event IDs, and disk I/O errors, "
            "then attempts automatic repair (marking corrupted records, extracting "
            "tail for recovery). Tenant-scoped monitoring with audit trail "
            "integration. Fail-closed: corruption detection never silently drops "
            "records, only marks them as CORRUPTED. Repairs are atomic and "
            "non-destructive. Off means queue loads but bypasses corruption "
            "detection (existing chain verification still runs). GDPR Art. 30, 32: "
            "audit trail integrity is load-bearing."
        ),
        owner="maintainer",
        target_release="0.11.x",
        tags=("security", "compliance", "audit"),
        release_tier="alpha",
    ),
    FeatureFlag(
        id="frontend_forge",
        label="FrontendForge (in-browser panel authoring)",
        description=(
            "Show the FrontendForge page in the Console — an in-browser editor for "
            "authoring external Console panels with a live, sandboxed preview "
            "(ADR-0364). Operator-only: off by default, so a normal install does not "
            "surface it. Turning it on adds the nav entry and route; it does not "
            "change how existing panels are served."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("console", "plugins", "ui"),
        release_tier="alpha",
    ),
    FeatureFlag(
        id="console_web_surface_plugin",
        label="Console as web_surface plugin",
        description=(
            "Declare the Console itself as a bundled web_surface plugin (ADR-0356) so "
            "the plugin loader loads it and it appears in the loaded-surfaces list. "
            "Off by default (ship-dark): the Console is still served the existing "
            "hard-wired way, so a normal install is unchanged. This is the first step "
            "toward the Console being a replaceable UI surface."
        ),
        owner="maintainer",
        target_release="0.12.x",
        tags=("console", "plugins"),
        release_tier="alpha",
    ),
    FeatureFlag(
        id="outcome_feedback_loop",
        label="Outcome-feedback loop (self-improving CEL)",
        description=(
            "After each console turn, attribute the turn's outcome to the CEL stages "
            "that ran (ADR-0269 Phase-4b / ADR-0369-sibling G4). Off by default "
            "(ship-dark): a normal install records no outcome. The grades written are "
            "ADVISORY (grader='__loop__', non-promoting) — they never change which "
            "stage is default-eligible; only an explicit operator grade does that."
        ),
        owner="maintainer",
        target_release="0.13.x",
        tags=("vibe-engineering", "learning"),
        release_tier="alpha",
    ),
    FeatureFlag(
        id="cross_device_sync",
        label="Cross-device tenant sync",
        description=(
            "Sync this tenant's learnable state (CEL stage grades, learning-event "
            "JSONL, skills, memory) across the operator's own instances through a Git "
            "remote, using the type-specific merge engine (ADR-0369). Off by default "
            "(ship-dark): a normal install never syncs. Turning it on requires an "
            "explicit consent step, a configured remote + PAT (in the Vault), and "
            "mandatory GPG encryption of the payload before push — learning state can "
            "carry end-user-derived PII, so the push is a real GDPR egress event."
        ),
        owner="maintainer",
        target_release="0.13.x",
        tags=("cross-device", "learning"),
        release_tier="alpha",
    ),
)


def _validate_registry(entries: tuple[FeatureFlag, ...]) -> None:
    seen: set[str] = set()
    allowed_tiers = ("alpha", "beta", "stable", "production")
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
        # Validate release_tier (ADR-0286)
        if entry.release_tier not in allowed_tiers:
            raise ValueError(
                f"feature flag {entry.id!r} has invalid release_tier {entry.release_tier!r}; "
                f"must be one of {allowed_tiers}"
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


def _coerce_flag(value: object) -> bool:
    """Interpret a stored flag value as on/off, fail-DARK.

    ``bool(value)`` is wrong for the config layer: ``bool("false")`` is ``True``,
    so a hand-edited (or quoted-YAML) ``headless_api_mode: "false"`` turned the
    feature ON — violating "absent/invalid = off, never on because unset"
    (2026-07-30 review finding D2). Only a real ``True`` or an explicit truthy
    string counts as on; everything else (incl. any other string, None, numbers
    other than 1) is off.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "on"}
    if isinstance(value, (int, float)):
        return value == 1
    return False


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
        return _coerce_flag(overlay[flag_id])
    spec_flags = _tenant_spec(tenant_id).get("features")
    if isinstance(spec_flags, dict) and flag_id in spec_flags:
        return _coerce_flag(spec_flags[flag_id])
    return entry.default


def _source_of(flag_id: str, tenant_id: str) -> str:
    overlay = _read_overlay(tenant_id).get("flags")
    if isinstance(overlay, dict) and flag_id in overlay:
        return "console"
    spec_flags = _tenant_spec(tenant_id).get("features")
    if isinstance(spec_flags, dict) and flag_id in spec_flags:
        return "tenant_yaml"
    return "default"


def recovery_command(flag_id: str) -> str:
    """The Console-independent way to switch ``flag_id`` back off.

    Single source of truth for the string: the Settings UI prints it verbatim
    in the confirmation dialog, ``corvin config set`` prints it back after a
    self-locking flag is turned on, and the tests assert on it. If the CLI
    surface is ever renamed, this is the one place that changes.
    """
    flag(flag_id)  # unregistered id → UnknownFlagError, never a bogus command
    return f"corvin config set features.{flag_id} false"


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
            "self_locking": f.self_locking,
            # Only meaningful for a self-locking flag; None keeps the UI from
            # offering a "recovery" command for a flag that never traps anyone.
            "recovery_command": (
                f"corvin config set features.{f.id} false" if f.self_locking else None
            ),
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


# ── Tier management (ADR-0286, ADR-0288) ──────────────────────────────────

_TIER_PROGRESSION = ("alpha", "beta", "stable", "production")


def tier_of(flag_id: str) -> str:
    """Return the current release_tier of a flag."""
    return flag(flag_id).release_tier


def can_promote_to(flag_id: str, target_tier: str) -> bool:
    """Check if a flag can progress to the next tier (not used for auto-promotion gate).

    This is an informational check for the CLI/dashboard. Real gating happens in
    the promotion daemon (ADR-0288) with metrics-based thresholds.
    """
    if target_tier not in _TIER_PROGRESSION:
        return False
    current = tier_of(flag_id)
    current_idx = _TIER_PROGRESSION.index(current)
    target_idx = _TIER_PROGRESSION.index(target_tier)
    return target_idx > current_idx  # Can only progress forward


def migrate_flags_to_alpha() -> dict[str, str]:
    """One-time migration: ensure all flags in registry have a release_tier.

    Returns dict of flag_id → tier (for audit trail).

    Safe to re-run; idempotent.
    """
    result = {}
    for entry in REGISTRY:
        result[entry.id] = entry.release_tier
    return result
