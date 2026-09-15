"""CEL prompt-assembly record — the causal chain from context bausteine to the
final worker prompt, for the console inspector (extends ADR-0278 Layer B).

This is the CONTENT side (Layer B), NOT the content-free Layer-A chain: it holds
the assembled prompt text + the structured sections it was built from, so an
authorised operator can trace how single retrieval bausteine become the final
prompt that enters the worker engine. Erasable (GDPR Art. 17): written under the
session workdir's ``cel-briefs/`` dir, which the WebChatHandler erasure path
already owns. Keyed by turn id. Best-effort — a write/read failure never breaks
the turn and simply yields no inspector detail.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SUFFIX = ".assembly.json"


def _safe(turn_id: str) -> str:
    return "".join(c for c in str(turn_id) if c.isalnum() or c in "_-")[:96]


def build_sections(bundle: Any) -> list[dict]:
    """Extract the structured 'bausteine' from a ContextBundle — each retrieval
    channel as a labelled section, the LLM synthesis, and the bound tools/skills.
    Mirrors render_brief_to_text's ordering so the waterfall matches the prompt.
    Accepts a full ContextBundle (active brain) OR a bare brief (passive path)."""
    br = getattr(bundle, "brief", None) or bundle
    out: list[dict] = []
    mc = getattr(br, "memory_context", None)
    matches = getattr(mc, "matches", []) if mc else []
    if matches:
        out.append({"kind": "memory", "label": "Relevant past memory",
                    "items": [getattr(m, "title", None) or getattr(m, "filename", "?")
                              for m in matches[:8]]})
    rel = getattr(br, "related_decisions", None) or []
    if rel:
        out.append({"kind": "adrs", "label": "Related decisions (ADRs)",
                    "items": [f"{getattr(d, 'decision_id', '?')}: {getattr(d, 'title', '')}"
                              for d in rel[:8]]})
    sk = getattr(br, "recommended_skills", None) or []
    if sk:
        out.append({"kind": "skills", "label": "Recommended skills",
                    "items": [getattr(s, "title", None) or getattr(s, "skill_id", "?")
                              for s in sk[:8]]})
    ap = getattr(br, "approach", None) or []
    if ap:
        out.append({"kind": "approach", "label": "Suggested focus",
                    "items": [str(a) for a in ap]})
    bl = getattr(br, "blockers", None) or []
    if bl:
        out.append({"kind": "blockers", "label": "Constraints / blockers",
                    "items": [str(b) for b in bl[:8]]})
    syn = getattr(bundle, "synthesised_prompt", None)
    if syn:
        out.append({"kind": "synthesis", "label": "LLM-synthesised prompt", "text": str(syn)})
    tools = getattr(bundle, "tools_to_bind", None) or []
    if tools:
        out.append({"kind": "tools", "label": "Forged tools bound",
                    "items": [getattr(t, "name", "?") for t in tools]})
    skills = getattr(bundle, "skills_to_bind", None) or []
    if skills:
        out.append({"kind": "forged_skills", "label": "Forged skills bound",
                    "items": [getattr(s, "skill_id", "?") for s in skills]})
    return out


def persist_assembly(workdir: Any, turn_id: str, *, sections: list,
                     cel_text: str, final_prompt: str,
                     forged_tools: "list | None" = None,
                     forged_skills: "list | None" = None) -> None:
    """Write the assembly record (Layer B, erasable). ``cel_text`` is the CEL block
    injected into the system prompt; ``final_prompt`` is the FULL assembled prompt
    that enters the worker engine. ``forged_*`` are the tool/skill ids to resolve
    to code on read. Best-effort."""
    try:
        d = Path(workdir) / "cel-briefs"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"turn_id": turn_id, "sections": sections or [],
               "cel_text": cel_text or "", "final_prompt": final_prompt or "",
               "forged_tools": list(forged_tools or []),
               "forged_skills": list(forged_skills or [])}
        (d / f"{_safe(turn_id)}{_SUFFIX}").write_text(
            json.dumps(rec, default=str), encoding="utf-8")
    except Exception:  # noqa: BLE001 — best-effort; never breaks the turn
        pass


def read_assembly(workdir: Any, turn_id: str) -> "dict | None":
    """Read a turn's assembly record, or None if absent/unreadable."""
    try:
        p = Path(workdir) / "cel-briefs" / f"{_safe(turn_id)}{_SUFFIX}"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:  # noqa: BLE001
        return None


def read_tool_code(tenant_id: str, name: str) -> "dict | None":
    """Resolve a forged tool name to its code + metadata from the Forge registry.
    Returns {name, description, code, deterministic} or None. Read-only."""
    try:
        from forge.paths import tenant_home  # noqa: PLC0415
        from forge.registry import Registry  # noqa: PLC0415
        reg = Registry(Path(tenant_home(tenant_id)) / "forge")
        spec = reg.get(name)
        if spec is None:
            return None
        code = ""
        try:
            code = Path(spec.impl_path).read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            code = ""
        return {"name": name, "description": getattr(spec, "description", ""),
                "code": code, "deterministic": bool(
                    (getattr(spec, "meta", None) or {}).get("deterministic"))}
    except Exception:  # noqa: BLE001
        return None


def read_skill_body(tenant_id: str, name: str) -> "dict | None":
    """Resolve a forged skill id to its markdown body from the SkillForge registry.
    Returns {skill_id, body} or None. Read-only."""
    try:
        # `MultiRegistry` does not exist — the class is `MultiSkillRegistry`, and its
        # constructor is keyword-only (project_root=/channel_id=), not a forge root.
        # The import raised into the `except` below on EVERY call, so the console's
        # forged-artifact drill-down showed zero skills for turns that really did
        # forge them (measured 2026-08-19). This is the SAME defect ADR-0283 R7 fixed
        # in stages/skillforge.py, left standing in this second call site — so use
        # the identical primitive: the tenant-rooted, path-explicit `SkillRegistry`.
        import sys  # noqa: PLC0415
        _sf = str(Path(__file__).resolve().parents[1] / "skill-forge")
        if _sf not in sys.path:      # not on the console/bridge path by default
            sys.path.insert(0, _sf)
        from skill_forge.registry import SkillRegistry  # noqa: PLC0415
        from forge.paths import tenant_home  # noqa: PLC0415
        reg = SkillRegistry(Path(tenant_home(tenant_id)) / "skill-forge")
        body = reg.get_body(name)
        if body is None:
            return None
        return {"skill_id": name, "body": body}
    except Exception:  # noqa: BLE001
        return None
