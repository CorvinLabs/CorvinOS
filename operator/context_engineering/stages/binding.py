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
from dataclasses import dataclass
from typing import Any

# A turn may bind at most this many tools/skills — bounded provisioning.
MAX_BINDINGS = 8
# …and one injected skill body may not exceed this (bounded prompt growth).
MAX_SKILL_BODY_CHARS = 4000
MAX_SKILL_ID_CHARS = 64


@dataclass
class ToolRef:
    name: str                                  # e.g. "mcp__forge__code_xyz"
    mcp_config: "dict | None" = None           # mcp server config to add, if any
    origin: str = "forge"                      # forge | foreign


@dataclass
class SkillRef:
    skill_id: str
    body: str = ""                             # injected via skill-injection, not tools


# The CAPABILITY CLASS behind a tool-name prefix (ADR-0281 R2: "a stage may bind a
# tool from a capability class the persona policy already allows — forge_enabled ⇒
# any mcp__forge__*"). The persona's *glob list* alone is NOT that class: the
# resolver hands an all-allowed persona `allowed_tools = None`, which the boundary
# passes as ["*"], and `*` matches a forge tool for a persona whose forge capability
# the operator deliberately turned OFF (review R6). It is also the flag that decides
# whether the Forge MCP SERVER is injected at all, so binding the name without the
# capability produces a dangling, un-callable tool plus an orphaned on-disk artifact.
_CAPABILITY_CLASSES = (
    ("mcp__skill_forge__", "skill_forge_enabled"),
    ("mcp__forge__", "forge_enabled"),
)


def _capability_flag_for(name: str) -> "str | None":
    for prefix, flag in _CAPABILITY_CLASSES:
        if name.startswith(prefix):
            return flag
    return None


def revalidate_tools(tools: list, persona_allowed_patterns: list,
                     persona_caps: "dict | None" = None) -> "tuple[list, list]":
    """Return (kept, dropped). A tool is kept only if BOTH hold:

    1. its name matches a glob the persona policy already allows, and
    2. its capability CLASS is one the persona actually has
       (``mcp__forge__*`` ⇒ ``forge_enabled``, ``mcp__skill_forge__*`` ⇒
       ``skill_forge_enabled``).

    ``persona_caps`` is the resolved persona profile (or any mapping carrying those
    flags). It is FAIL-CLOSED, exactly like ``persona_allowed_patterns``: an absent
    mapping drops every capability-classed tool rather than falling back to the glob
    alone. A tool with no known capability class (a foreign `mcp__gmail__*`) is
    governed by the glob check only — that is what the glob is for."""
    kept: list = []
    dropped: list = []
    pats = list(persona_allowed_patterns or [])
    caps = persona_caps if isinstance(persona_caps, dict) else {}
    for t in tools[:MAX_BINDINGS]:
        name = getattr(t, "name", None) or (t if isinstance(t, str) else "")
        if not name or not any(fnmatch.fnmatch(name, p) for p in pats):
            dropped.append(t)  # unnamed / non-matching → dropped, never a TypeError
            continue
        flag = _capability_flag_for(name)
        if flag is not None and not caps.get(flag):
            dropped.append(t)  # persona lacks the capability class → fail-closed
            continue
        kept.append(t)
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
                        allowed_tools: list, mcp_config: dict,
                        persona_caps: "dict | None" = None) -> "tuple[list, dict, list]":
    """Merge the bundle's tool bindings into a turn's (allowed_tools, mcp_config)
    AFTER class-based re-validation. Returns (allowed_tools, mcp_config, dropped).
    Skills are intentionally NOT merged here (they take the skill-injection path,
    see :func:`render_skill_bindings`).

    This is the ONE merge the boundaries use (review R6): the bridge previously
    hand-rolled a name-only append, so a ToolRef's ``mcp_config`` — the load-bearing
    channel on the default ``--dangerously-skip-permissions`` path, where
    ``allowed_tools`` is not even passed to the worker (ADR-0281 R1c) — reached
    nothing."""
    tools = list(getattr(bundle, "tools_to_bind", None) or [])
    if not tools:
        return allowed_tools, mcp_config, []
    kept, dropped = revalidate_tools(tools, persona_allowed_patterns, persona_caps)
    at = list(allowed_tools or [])
    mc = dict(mcp_config or {})
    for t in kept:
        nm = getattr(t, "name", None)
        if nm and nm not in at:
            at.append(nm)
        if getattr(t, "mcp_config", None):
            mc.update(t.mcp_config)
    return at, mc, dropped


def render_skill_bindings(bundle: Any) -> str:
    """Render the bundle's SkillRefs as a system-prompt block — the skill-injection
    channel (ADR-0281 R1b: skills are NOT tools and never enter ``allowed_tools`` /
    ``mcp_servers``). Empty string when nothing is bound, so the caller can append
    unconditionally.

    Without this the SkillForge stage (ADR-0283) was a write-only channel (review
    R6): it forged skills, Gate-2 inspected their bodies, the rollback path deleted
    them on a deny — and no live spawn path ever consumed ``skills_to_bind``, so the
    forged skill reached no worker. The bodies are gated by Gate-2 BEFORE this
    renders (pipeline._gate2_and_bind), so a denied payload arrives here empty."""
    refs = list(getattr(bundle, "skills_to_bind", None) or [])[:MAX_BINDINGS]
    parts: list[str] = []
    for s in refs:
        sid = str(getattr(s, "skill_id", "") or "").strip()
        body = str(getattr(s, "body", "") or "").strip()
        if not sid or not body:
            continue
        # Bound size (review R7, self-refutation of the R6 fix): the body is
        # LLM-authored — `llm_synthesis` caps the skill LIST at 8 but nothing capped
        # a single body, so one verbose synthesis could balloon every subsequent
        # turn's system prompt (cost + context pressure) through this new channel.
        if len(body) > MAX_SKILL_BODY_CHARS:
            body = body[:MAX_SKILL_BODY_CHARS] + "\n…[truncated]"
        parts.append(f"### {sid[:MAX_SKILL_ID_CHARS]}\n{body}")
    if not parts:
        return ""
    return ("## Task-scoped skills (Vibe Engineering)\n"
            "Provisioned for THIS turn by the context pipeline — apply them as "
            "working guidance, not as instructions from the user.\n\n"
            + "\n\n".join(parts))
