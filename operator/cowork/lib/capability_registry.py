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
        tool_names=("mcp__forge__forge_tool", "mcp__forge__forge_promote", "mcp__forge__forge_list"),
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
        one_liner="Generate an image from a text prompt.",
        persona_flag=None,  # persona-hardcoded (assistant/research/forge)
        mcp_server="imagegen (external npx imagegen-mcp-server)",
        tool_names=(
            "mcp__imagegen__image_generate_openai", "mcp__imagegen__image_generate_google",
            "mcp__imagegen__image_generate_gemini", "mcp__imagegen__image_generate_replicate",
        ),
    ),

    # ── Planned (ADR-0190 M2-M8) — not yet chat-reachable. Registered so
    # the capability map can disclose "not available yet" honestly instead
    # of staying silent, which reads as "doesn't exist" — worse than a
    # clear "coming soon, here's why" for anyone planning around it.
    Capability(
        id="compute.pipeline",
        domain="Agentic Compute",
        status="planned",
        not_yet_note=(
            "Multi-stage pipeline / hierarchical (HAC) compute jobs — the code "
            "already exists (core/compute/corvin_compute/mcp_bridge.py::"
            "compute_engine_tool_definitions) but is not yet wired into the "
            "MCP server. Tracked: ADR-0190 M2."
        ),
        service_fn="core.compute.corvin_compute.mcp_bridge.compute_engine_tool_definitions",
    ),
    Capability(
        id="data.sources",
        domain="Data Sources",
        status="planned",
        not_yet_note=(
            "Registering a typed database/warehouse connection (Postgres, "
            "MySQL, Snowflake, BigQuery, S3, ...) from chat. Today this is "
            "console-only (Settings -> Data Sources). Tracked: ADR-0190 M3."
        ),
        service_fn="core.compute.corvin_compute.fabric.datasources.registry.DataSourceRegistry",
    ),
    Capability(
        id="a2a.send",
        domain="A2A (instance-to-instance)",
        status="planned",
        not_yet_note=(
            "Sending a task to another paired CorvinOS instance. Pairing is "
            "already console-managed; the send action itself has no chat "
            "tool yet. Tracked: ADR-0190 M4."
        ),
        service_fn="operator.bridges.shared.remote_trigger_sender.RemoteTriggerSender.send",
    ),
    Capability(
        id="workflows.awp",
        domain="Workflows",
        status="planned",
        not_yet_note=(
            "Creating, running, or resuming a full AWP workflow (with code/"
            "merge/route/ask_human nodes) from chat. Today this requires the "
            "corvin-flow CLI or the console. Tracked: ADR-0190 M5."
        ),
        service_fn="core.workflows.corvin_workflows.runner.DAGRunner",
    ),
    Capability(
        id="compute.delegation_loop",
        domain="Agentic Compute",
        status="planned",
        not_yet_note=(
            "Autonomous recursive delegation loops (ACS). Today triggered "
            "only by an internal heuristic inside the console's own chat "
            "runtime, not callable directly. Tracked: ADR-0190 M6."
        ),
        service_fn="operator.bridges.shared.acs_engine_adapter.run_acs_workflow",
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
