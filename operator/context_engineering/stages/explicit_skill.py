"""ExplicitSkillStage — honor a user-named EXISTING on-disk skill on the console CEL path.

The bug (session web:9gCJXQnmhy): a user explicitly names an existing on-disk skill
in the active persona's namespace, but the console CEL pipeline has NO route that
injects an EXISTING skill's BODY. ``SkillStage`` only sets ``brief.recommended_skills``
(titles); ``SkillForgeStage`` only FORGES brand-new LLM-proposed skills and, per
ADR-0283 R1, never re-selects an existing one. And ``render_skill_bindings`` — the
console's only skill-injection channel — reads ``bundle.skills_to_bind`` ONLY. So a
user explicitly naming an ungraded on-disk skill had no route into the console prompt.

This stage closes that gap. It parses explicitly-named skill ids from the task text,
resolves the active persona's namespace (FAIL-CLOSED — an unresolved persona never
opens the gate to every namespace), loads the matching on-disk skill BODY EVEN IF
UNGRADED (an explicit request is the strongest relevance signal and outranks the AUTO
grade gate, which only governs relevance-based auto-injection), and appends a SkillRef
to ``skills_to_bind`` so it flows through Gate-2 + ``render_skill_bindings`` into the
console system prompt.

POST-gate (``effect="forge"``) ON PURPOSE: ``skills_to_bind`` must never be populated
by a PURE pre-Gate-1 stage — a Gate-1-denied turn would otherwise still render the body
(``_cel_render_skills`` runs regardless of a gate1 denial). As a deferred forge stage it
runs only AFTER Gate-1 approved the task, and Gate-2 still inspects every body it adds.

Parse / namespace / diagnostics / caps are REUSED from the bridge's
``operator/bridges/shared/skill_inject.py`` (one choke point — no third copy of the
regex). Fail-safe throughout: any error degrades to "no explicit skill bound", never
breaks the turn.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .base import StageTelemetry
from .binding import SkillRef, MAX_BINDINGS
from .registry import register_stage


# ── Optional shared-helper import (bridge skill_inject) ─────────────────────────
# Mirrors core/delegate/.../skill_context.py: add operator/bridges/shared to sys.path
# and import skill_inject. Optional — a host without it simply cannot honor explicit
# requests (the stage no-ops), never raises.
def _load_helpers():
    try:
        shared = Path(__file__).resolve().parents[2] / "bridges" / "shared"
        if shared.is_dir() and str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        import skill_inject as _si  # type: ignore  # noqa: PLC0415
        return _si
    except Exception:  # noqa: BLE001 — helpers optional; degrade to no-op
        return None


def _skill_registry(tenant_id: str):
    """Tenant-native MultiSkillRegistry across the task→session→project→user ladder.
    Mirrors skillforge.py's sys.path dance (operator/skill-forge is on neither host's
    path by default). Returns None on any import failure (fail-safe)."""
    try:
        _sf_dir = str(Path(__file__).resolve().parents[2] / "skill-forge")
        if _sf_dir not in sys.path:
            sys.path.insert(0, _sf_dir)
        _forge_dir = str(Path(__file__).resolve().parents[2] / "forge")
        if _forge_dir not in sys.path:
            sys.path.insert(0, _forge_dir)
        from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415
        return MultiSkillRegistry(tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        return None


def honor_explicit_skill_requests(bundle, ctx) -> int:
    """Add EXPLICITLY-requested existing on-disk skills to ``bundle.skills_to_bind``.

    Returns the number of skills bound. Reuses the bridge's parse/namespace/diagnostic
    choke point; applies the SAME fail-closed namespace gate, the SAME per-turn cap
    (``_MAX_EXPLICIT_HONORED`` == MAX_BINDINGS), and the SAME loud content-free
    diagnostics. Best-effort: any error returns the count bound so far, never raises."""
    si = _load_helpers()
    if si is None:
        return 0
    task_text = getattr(bundle, "task", "") or ""
    requested = si._parse_requested_skills(task_text)
    if not requested:
        return 0
    reg = _skill_registry(getattr(ctx, "tenant_id", "_default") or "_default")
    if reg is None:
        # Registry unavailable but the user still named a skill — don't stay silent.
        low = task_text.lower()
        if any(v in low for v in si._SKILL_REQUEST_VOCAB):
            for req in requested:
                si._diag_excluded_request(req, "registry_unavailable")
        return 0

    try:
        scoped = reg.list_with_scope()
    except Exception:  # noqa: BLE001
        scoped = []
    by_name_ci = {getattr(spec, "name", "").lower(): spec for _scope, spec in scoped}

    ns_prefix = si._persona_namespace(getattr(ctx, "persona", "") or None)
    low = task_text.lower()
    has_vocab = any(v in low for v in si._SKILL_REQUEST_VOCAB)
    # A cap that matches render_skill_bindings' own MAX_BINDINGS; prefer the bridge's
    # constant (single source) but never exceed the render cap.
    cap = min(getattr(si, "_MAX_EXPLICIT_HONORED", MAX_BINDINGS), MAX_BINDINGS)

    already = {str(getattr(s, "skill_id", "") or "").lower()
               for s in (bundle.skills_to_bind or [])}
    honored = 0
    for req in requested:
        spec = by_name_ci.get(req)
        exists = spec is not None
        # Dotted PROSE (file names, "e.g.") only counts as a request when it either
        # resolves to a real skill or the message carries explicit skill vocabulary.
        if not exists and not has_vocab:
            continue
        # Namespace gate FIRST, FAIL-CLOSED: an unresolved persona (ns_prefix None)
        # never opens the gate to every namespace.
        if ns_prefix is None:
            si._diag_excluded_request(req, "persona_unresolved")
            continue
        req_ns = req.split(".", 1)[0] if "." in req else ""
        if req_ns != ns_prefix:
            si._diag_excluded_request(req, "wrong_namespace")
            continue
        if not exists:
            si._diag_excluded_request(req, "not_found")
            continue
        name = getattr(spec, "name", "") or ""
        if name.lower() in already:
            continue  # already bound this turn (e.g. a forged twin)
        # Cap the count: a task naming 20 valid skills must not balloon the prompt.
        if honored >= cap:
            si._diag_excluded_request(req, "capped")
            continue
        try:
            body = reg.get_body(name) or ""
            body = si._strip_front_matter(body).strip()
        except Exception:  # noqa: BLE001
            body = ""
        if not body:
            si._diag_excluded_request(req, "empty_body")
            continue
        bundle.skills_to_bind.append(SkillRef(skill_id=name, body=body))
        already.add(name.lower())
        honored += 1
    return honored


class ExplicitSkillStage:
    id = "explicit_skill"
    requires: tuple = ()
    # POST-gate: skills_to_bind must not be populated before Gate-1 (a gate1-denied
    # turn still renders skills_to_bind). effect="forge" defers this stage so it runs
    # only after Gate-1 approves; Gate-2 still inspects the bodies it adds.
    effect = "forge"
    trust = "builtin"

    def run(self, bundle, ctx):
        bound = 0
        try:
            bound = honor_explicit_skill_requests(bundle, ctx)
        except Exception:  # noqa: BLE001 — explicit-honor must never break the turn
            bound = 0
        return bundle, StageTelemetry(
            stage=self.id, status="ok",
            reason=None if bound else "no_explicit_skill_request",
            confidence_tier="high" if bound else "low",
            sources=[{"id": s.skill_id, "score": 1.0}
                     for s in (bundle.skills_to_bind or [])
                     if getattr(s, "skill_id", "")][:MAX_BINDINGS])


register_stage(ExplicitSkillStage())
