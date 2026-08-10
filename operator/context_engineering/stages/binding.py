"""Dual-channel worker provisioning — the bind guards (ADR-0281, CONCEPT-0006 §9/§10).

A stage may add ToolRefs / SkillRefs to the ContextBundle; the boundary feeds them
to the worker via the RIGHT channels — tools through the resolver
(`allowed_tools` + `mcp_config`, which IS the tool authority boundary), skills
through the skill-injection path (NEVER `allowed_tools`).

Two load-bearing guards live here (the reason P-B is compliance-critical even
before a producer exists in P-D):

* `revalidate_tools` — CLASS-based (ADR-0281 R2): a stage may only bind a tool
  from a capability class the persona policy ALREADY allows (e.g. a `forge_enabled`
  persona ⇒ any `mcp__forge__*`). A forged tool is an instance of an allowed class,
  not a new grant. Anything outside the persona's allowed patterns is dropped +
  reported (bind ≠ authorise). For forged tools the Forge SANDBOX is the real
  guard; this re-check guards FOREIGN tools (`mcp__gmail__*` …).
* `strip_for_remote` — by construction (ADR-0279): a remote / isolated spawn
  (A2A inbound, ACS worker fan-out) carries NO local bindings. Never send a local
  capability across the trust / isolation boundary.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

# A turn may bind at most this many tools/skills — bounded provisioning.
MAX_BINDINGS = 8


@dataclass
class ToolRef:
    name: str                                  # e.g. "mcp__forge__code_xyz"
    mcp_config: "dict | None" = None           # mcp server config to add, if any
    origin: str = "forge"                      # forge | foreign


@dataclass
class SkillRef:
    skill_id: str
    body: str = ""                             # injected via skill-injection, not tools


def revalidate_tools(tools: list, persona_allowed_patterns: list) -> "tuple[list, list]":
    """Return (kept, dropped). Keep a tool only if its name matches a pattern the
    persona policy already allows — class-based, so a freshly forged tool of an
    allowed class survives while a foreign tool the persona can't call is dropped."""
    kept: list = []
    dropped: list = []
    pats = list(persona_allowed_patterns or [])
    for t in tools[:MAX_BINDINGS]:
        name = getattr(t, "name", t)
        if any(fnmatch.fnmatch(name, p) for p in pats):
            kept.append(t)
        else:
            dropped.append(t)
    # anything past the cap is dropped too (bounded provisioning)
    dropped.extend(tools[MAX_BINDINGS:])
    return kept, dropped


def strip_for_remote(bundle: Any) -> bool:
    """Empty the binding channels before a remote/isolated spawn (ADR-0279). Returns
    True if anything was stripped (the caller audits it). text_sections are NOT
    touched — only the capability channels."""
    had = bool(getattr(bundle, "tools_to_bind", None) or getattr(bundle, "skills_to_bind", None))
    bundle.tools_to_bind = []
    bundle.skills_to_bind = []
    return had


def apply_tool_bindings(bundle: Any, persona_allowed_patterns: list,
                        allowed_tools: list, mcp_config: dict) -> "tuple[list, dict, list]":
    """Merge the bundle's tool bindings into a turn's (allowed_tools, mcp_config)
    AFTER class-based re-validation. Returns (allowed_tools, mcp_config, dropped).
    Skills are intentionally NOT merged here (they take the skill-injection path)."""
    tools = list(getattr(bundle, "tools_to_bind", None) or [])
    if not tools:
        return allowed_tools, mcp_config, []
    kept, dropped = revalidate_tools(tools, persona_allowed_patterns)
    at = list(allowed_tools or [])
    mc = dict(mcp_config or {})
    for t in kept:
        if t.name not in at:
            at.append(t.name)
        if t.mcp_config:
            mc.update(t.mcp_config)
    return at, mc, dropped
