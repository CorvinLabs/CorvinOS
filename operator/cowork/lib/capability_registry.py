#!/usr/bin/env python3
"""capability_registry.py — ADR-0190 Capability Registry.

Single source of truth mapping each user-facing CorvinOS capability to its
persona-gating flag, MCP tool names, and a description for the self-aware
capability map (see ``capability_map.py``). Consumed by ``resolver.py``
(persona wiring) and enforced by
``operator/cowork/test/test_capability_registry_matches_reality.py``.

Two invariants this file exists to protect (ADR-0190 "What NOT to Do"):

  1. A capability marked ``status="wired"`` MUST actually be callable —
     its ``tool_names`` must be real tools some MCP server advertises.
  2. The capability map (what Corvin tells a user it can do) is GENERATED
     from this file, never hand-written prose — so it cannot silently
     drift the way the pre-2026-07-11 IBC domain / RS256 design did.

Adding a new chat-reachable capability in a future milestone means adding
an entry HERE first — the CI enforcement test will fail if a new MCP tool
appears in any ``mcp_server.py`` without a matching registry entry, and
will fail if a ``status="wired"`` entry's tools don't actually exist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Status = Literal["wired", "planned", "deprecated"]


@dataclass(frozen=True)
class Capability:
    id: str
    """Stable identifier, e.g. "forge.tools". Never reused across entries."""

    domain: str
    """Display group shown in the capability map, e.g. "Forge (Tool Generation)"."""

    status: Status

    one_liner: str = ""
    """Shown in the capability map when status == "wired". Required for
    wired entries (enforced by _validate below)."""

    not_yet_note: str = ""
    """Shown instead of one_liner when status != "wired" — must name the
    tracking milestone so "planned" never means "silently forgotten"."""

    persona_flag: str | None = None
    """Profile flag gating this capability's attachment (e.g.
    "forge_enabled"). None means always-on for personas that declare the
    MCP server directly (e.g. Playwright, ImageGen) — not resolver-gated."""

    mcp_server: str | None = None
    """Dotted module path or external package name of the MCP server."""

    tool_names: tuple[str, ...] = field(default_factory=tuple)
    """Exact MCP tool names this capability exposes."""

    gate_fn: str | None = None
    """Dotted path to the license/L34/L35/consent gate this capability's
    tool call MUST reuse (never reimplement). None only for capabilities
    with no additional gate beyond the persona flag itself."""

    service_fn: str | None = None
    """Dotted path to the underlying service/library entry point the MCP
    tool wraps — the thing to keep in sync when that code moves."""

    deep_dive_doc: str | None = None
    """docs/capabilities/<id>.md — read on demand, not injected eagerly."""

    test_file: str | None = None
    """Path (relative to repo root) proving this entry is real."""

    def __post_init__(self) -> None:
        if self.status == "wired":
            if not self.one_liner:
                raise ValueError(f"Capability {self.id!r}: status='wired' requires one_liner")
            if not self.tool_names:
                raise ValueError(f"Capability {self.id!r}: status='wired' requires tool_names")
        else:
            if not self.not_yet_note:
                raise ValueError(
                    f"Capability {self.id!r}: status={self.status!r} requires not_yet_note "
                    "(ADR-0190 — never let a non-wired capability go undisclosed)"
                )


# ── Wired — already chat-reachable today (verified against the ADR-0190 audit) ──

CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="forge.tools",
        domain="Forge (Tool Generation)",
        status="wired",
        one_liner="Forge a new sandboxed, deterministic tool from a description, run it, or promote it to a wider scope.",
        persona_flag="forge_enabled",
        mcp_server="forge.mcp_server",
        tool_names=("mcp__forge__forge_tool", "mcp__forge__forge_promote",
                    "mcp__forge__forge_list", "mcp__forge__forge_exec"),
        service_fn="operator.forge.forge.mcp_server",
        test_file="operator/forge/tests/test_mcp_server.py",
    ),
    Capability(
        id="forge.data",
        domain="Data (local snapshots)",
        status="wired",
        one_liner="Register a local data file (CSV/TSV/JSON/JSONL/Parquet) or reference an already-configured connection for a compute run.",
        persona_flag="forge_enabled",
        mcp_server="forge.mcp_server",
        tool_names=("mcp__forge__data_register", "mcp__forge__data_snapshot", "mcp__forge__data_unregister"),
        service_fn="operator.forge.forge.corvin_data.mcp_handlers",
    ),
    Capability(
        id="forge.artifacts",
        domain="Artifacts (Layer 33)",
        status="wired",
        one_liner="List, search, retrieve, or pin session artifacts (files produced by earlier turns/tools).",
        persona_flag="forge_enabled",
        mcp_server="forge.mcp_server",
        tool_names=(
            "mcp__forge__artifact_list", "mcp__forge__artifact_search", "mcp__forge__artifact_get",
            "mcp__forge__artifact_extract", "mcp__forge__artifact_register", "mcp__forge__artifact_pin",
        ),
    ),
    Capability(
        id="compute.flat",
        domain="Agentic Compute",
        status="wired",
        one_liner="Run a parameter sweep (grid/random/bayesian) over an already-forged tool, out of the LLM loop; check status, fetch the result, or abort.",
        persona_flag="forge_enabled",
        mcp_server="forge.mcp_server",
        tool_names=(
            "mcp__forge__compute_run", "mcp__forge__compute_status",
            "mcp__forge__compute_result", "mcp__forge__compute_abort",
        ),
        gate_fn="operator.forge.forge.mcp_server._check_compute_access",
        service_fn="core.compute.corvin_compute.mcp_bridge.compute_tool_definitions",
    ),
    Capability(
        id="skill_forge.skills",
        domain="SkillForge (Skill Generation)",
        status="wired",
        one_liner="Create, grade, promote, look up, or diff reusable markdown skills that get prompt-injected into future turns.",
        persona_flag="skill_forge_enabled",
        mcp_server="skill_forge.mcp_server",
        tool_names=(
            "mcp__skill_forge__skill_create", "mcp__skill_forge__skill_promote",
            "mcp__skill_forge__skill_grade", "mcp__skill_forge__skill_list",
            "mcp__skill_forge__skill_get", "mcp__skill_forge__skill_purge",
            "mcp__skill_forge__skill_diff",
        ),
        service_fn="operator.skill_forge.skill_forge.mcp_server",
    ),
    Capability(
        id="delegate.workers",
        domain="Delegation",
        status="wired",
        one_liner="Delegate a sub-task to a fresh, isolated Claude Code / Codex / OpenCode / Hermes / Copilot worker.",
        persona_flag="delegate_enabled",
        mcp_server="corvin_delegate.mcp_server",
        tool_names=(
            "mcp__corvin_delegate__delegate_claude_code", "mcp__corvin_delegate__delegate_codex",
            "mcp__corvin_delegate__delegate_opencode", "mcp__corvin_delegate__delegate_hermes",
            "mcp__corvin_delegate__delegate_copilot",
        ),
        service_fn="core.delegate.corvin_delegate.mcp_server",
        test_file="core/delegate/tests/test_mcp_server.py",
    ),
    Capability(
        id="browser.automation",
        domain="Browser Automation",
        status="wired",
        one_liner="Drive a real Chromium browser — navigate, fill, click, read — with a live view an operator can watch.",
        persona_flag=None,  # persona-hardcoded (assistant/research), not resolver-gated
        mcp_server="playwright (external npx @playwright/mcp)",
        tool_names=("mcp__playwright__*",),
    ),
    Capability(
        id="image.generation",
        domain="Image Generation",
        status="wired",
        one_liner=(
            "Generate an image from a text prompt — zero-config free tier by "
            "default, auto-upgrades to the user's own OpenAI key when configured."
        ),
        # ADR-0191: first-class mcp_manager catalog entry (seeded on boot,
        # attached via the catalog/spawn path) — replaced the persona-hardcoded
        # npx imagegen-mcp-server, which was BYOK-only, broken without a key,
        # and invisible to L34/L35 governance.
        persona_flag=None,
        mcp_server="mcp_manager catalog: imagegen-zero-config",
        tool_names=("mcp__imagegen-zero-config__generate_image",),
    ),

    # ── Planned (ADR-0190 M7) — not yet chat-reachable. Registered so
    # the capability map can disclose "not available yet" honestly instead
    # of staying silent, which reads as "doesn't exist" — worse than a
    # clear "coming soon, here's why" for anyone planning around it.
    # (M2-M6 below this comment used to live here as "planned" — all now
    # flipped to "wired" further up; M8 is a research-spike milestone with
    # no new user-facing capability of its own, so it has no registry entry.)
    Capability(
        id="license.status",
        domain="License",
        status="planned",
        not_yet_note=(
            "Reading your current license tier / feature limits from chat "
            "(e.g. 'what's my compute quota today'). Today this is console-"
            "only (Settings -> License). Tracked: ADR-0190 M7."
        ),
        service_fn="operator.license.validator.active_tier",
    ),
    Capability(
        id="rag.query",
        domain="Knowledge Base (RAG)",
        status="planned",
        not_yet_note=(
            "Querying the tenant's ingested-document knowledge base from "
            "chat as a distinct tool call (retrieval today happens only as "
            "an implicit context-injection step, not as something you can "
            "invoke directly and inspect). Tracked: ADR-0190 M7."
        ),
        service_fn="operator.bridges.shared.rag_query_engine.RAGQueryEngine",
    ),
    Capability(
        id="a2a.peers",
        domain="A2A (instance-to-instance)",
        status="planned",
        not_yet_note=(
            "Listing/inspecting configured A2A peer pairings and friendship "
            "state from chat (distinct from a2a.send/a2a_list_endpoints, "
            "which only see YOUR OWN sender-side endpoint configs, not the "
            "richer bidirectional pairing state). Today this is console-only "
            "(the /remote-trigger/pair/friendship/connections REST route). "
            "Tracked: ADR-0190 M7."
        ),
        service_fn="core.console.corvin_console.routes.a2a_pair",
    ),
    Capability(
        id="pipes.sessions",
        domain="Inter-Session Pipes (Layer 18)",
        status="planned",
        not_yet_note=(
            "Piping one persona session's output into another's input via "
            "named pipes. A fully-built MCP server exists (core/pipe/"
            "mcp_server.py: pipe_create/write/read/subscribe/...) but has "
            "NO resolver injector and is referenced nowhere — genuinely "
            "orphaned. ADR-0190 M8 recommendation: wire it (persona "
            "composition is a real use case) or formally remove it; not "
            "decided yet. Registered here so the CI reverse-check knows the "
            "orphan is tracked instead of silently invisible."
        ),
        # Inert metadata while status="planned": the intended server key +
        # tool names, so the CI reverse-check (every tool advertised by any
        # mcp_server.py must be registry-tracked) can account for the file.
        mcp_server="core.pipe.mcp_server",
        tool_names=(
            "mcp__corvin_pipe__pipe_create", "mcp__corvin_pipe__pipe_write",
            "mcp__corvin_pipe__pipe_read", "mcp__corvin_pipe__pipe_list",
            "mcp__corvin_pipe__pipe_remove", "mcp__corvin_pipe__pipe_get_meta",
            "mcp__corvin_pipe__pipe_subscribe", "mcp__corvin_pipe__pipe_unsubscribe",
            "mcp__corvin_pipe__pipe_queue_depth",
        ),
        service_fn="core.pipe.mcp_server",
    ),

    Capability(
        id="compute.pipeline",
        domain="Agentic Compute",
        status="wired",
        one_liner="Submit a multi-stage pipeline or hierarchical (HAC) compute job with forge/backprop gates; steer, resume, or abort it mid-run via compute_gate.",
        persona_flag="forge_enabled",
        mcp_server="forge.mcp_server",
        tool_names=("mcp__forge__compute_submit", "mcp__forge__compute_gate"),
        gate_fn="operator.forge.forge.mcp_server._check_compute_access",
        service_fn="core.compute.corvin_compute.mcp_bridge.compute_engine_tool_definitions",
        test_file="operator/forge/tests/test_compute_engine_tools.py",
    ),
    Capability(
        id="data.sources",
        domain="Data Sources",
        status="wired",
        one_liner="Register a typed database/warehouse connection (Postgres, MySQL, Snowflake, BigQuery, S3, ...); adapter access follows your license tier (Free: local_file only).",
        persona_flag="forge_enabled",
        mcp_server="forge.mcp_server",
        tool_names=("mcp__forge__datasource_connect",),
        gate_fn="operator.forge.forge.mcp_server._lic_get_limit",
        service_fn="core.compute.corvin_compute.fabric.datasources.registry.DataSourceRegistry",
        test_file="operator/forge/tests/test_datasource_connect.py",
    ),
    Capability(
        id="a2a.send",
        domain="A2A (instance-to-instance)",
        status="wired",
        one_liner="Send a signed task instruction to an already-paired CorvinOS instance, or list configured endpoints.",
        persona_flag="orchestration_enabled",
        mcp_server="corvin_orchestration.mcp_server",
        tool_names=("mcp__corvin_orchestration__a2a_send", "mcp__corvin_orchestration__a2a_list_endpoints"),
        service_fn="operator.bridges.shared.remote_trigger_sender.RemoteTriggerSender.send",
        test_file="core/orchestration/tests/test_mcp_server.py",
    ),
    Capability(
        id="workflows.awp",
        domain="Workflows",
        status="wired",
        one_liner="Run, resume, or list paused AWP DAG-workflows (code/merge/route/ask_human nodes) already registered under Settings -> Workflows.",
        persona_flag="orchestration_enabled",
        mcp_server="corvin_orchestration.mcp_server",
        tool_names=(
            "mcp__corvin_orchestration__workflow_run",
            "mcp__corvin_orchestration__workflow_resume",
            "mcp__corvin_orchestration__workflow_list_paused",
        ),
        service_fn="core.workflows.corvin_workflows.runner.DAGRunner",
        test_file="core/orchestration/tests/test_mcp_server.py",
    ),
    # NOTE (ADR-0190 M6 scope note, not shown to the LLM): the NEW acs_delegate
    # MCP tool below is wired and calls run_acs_workflow() directly — zero
    # regression risk to the existing console feature. The SEPARATE,
    # originally-scoped refactor of the console's own chat_runtime.py ACS
    # bypass onto this same run_acs_workflow() path was deliberately DEFERRED
    # — that bypass is a live, working console feature relying on
    # run_acs_workflow()-unsupported kwargs (manager_model/worker_model/
    # session_debug_log) and a different run-directory convention; refactoring
    # it needs its own carefully-tested pass, not a rushed side-effect here.
    Capability(
        id="compute.delegation_loop",
        domain="Agentic Compute",
        status="wired",
        one_liner="Delegate an open-ended task to the Autonomous Compute Shell's manager/worker loop (ADR-0104) — distinct from a fixed workflow_run DAG.",
        persona_flag="orchestration_enabled",
        mcp_server="corvin_orchestration.mcp_server",
        tool_names=("mcp__corvin_orchestration__acs_delegate",),
        service_fn="operator.bridges.shared.acs_engine_adapter.run_acs_workflow",
        test_file="core/orchestration/tests/test_mcp_server.py",
    ),
)


def capabilities_by_status(status: Status) -> tuple[Capability, ...]:
    return tuple(c for c in CAPABILITIES if c.status == status)


def get(capability_id: str) -> Capability | None:
    for c in CAPABILITIES:
        if c.id == capability_id:
            return c
    return None


def all_tool_names() -> frozenset[str]:
    """Every concrete (non-wildcard) tool name across all wired capabilities."""
    out: set[str] = set()
    for c in capabilities_by_status("wired"):
        for t in c.tool_names:
            if not t.endswith("*"):
                out.add(t)
    return frozenset(out)
