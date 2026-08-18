"""SkillForge stage (ADR-0283, P-D) — provision the worker with skills.

`effect=forge`, opt-in. Reads `scratch['needs'].skills` (from P-C) and binds them.
Per ADR-0283 R1 it does NOT re-select existing skills (the `skill` stage already
does that) — it only creates/binds what the task needs and no skill covers. Skills
take the skill-injection channel (`skills_to_bind`), NEVER `allowed_tools`.
Best-effort skill creation; a failure is fail-safe (the turn proceeds)."""
from __future__ import annotations

from .base import StageTelemetry
from .binding import SkillRef, MAX_BINDINGS
from .registry import register_stage


# NB (review R7): this used `MultiRegistry`, a class that does not exist — the real
# name is `MultiSkillRegistry`, and its constructor is keyword-only (`project_root=`,
# `channel_id=`), not a positional forge root. Every call raised ImportError into the
# stage's `except: pass`, so NO skill was ever written to disk and `_forged_skills`
# stayed empty — which also made the Gate-2 skill rollback a permanent no-op. Every
# test patched `_skill_create`, so nothing caught it. `SkillRegistry(root)` is the
# tenant-rooted, path-explicit twin of the `forge.registry.Registry(root)` call
# ToolForge already uses correctly, and it keeps the store under the CEL's own
# tenant home rather than the env-resolved session root.
def _skill_registry(tenant_id: str):
    import sys  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    # `skill_forge` lives under operator/skill-forge/, which is NOT on the path of
    # either host process (the console and the bridge only bootstrap operator/forge/
    # and operator/). Without this the import fails before the class name even
    # matters — the second layer of the same R7 defect. Mirrors the sys.path dance
    # license_gate.py already uses (project memory: "operator/ stdlib-Shadow-Falle" —
    # never make operator/ a package, extend sys.path instead).
    _sf_dir = str(Path(__file__).resolve().parents[2] / "skill-forge")
    if _sf_dir not in sys.path:
        sys.path.insert(0, _sf_dir)
    from forge.paths import tenant_home  # noqa: PLC0415
    from skill_forge.registry import SkillRegistry  # noqa: PLC0415
    root = Path(tenant_home(tenant_id)) / "skill-forge"
    root.mkdir(parents=True, exist_ok=True)
    return SkillRegistry(root)


def _skill_create(tenant_id: str, name: str, body: str) -> None:
    """Create a session-scoped learned-experience skill, in-process. Best-effort."""
    _skill_registry(tenant_id).create(
        name=name, type="learned-experience", body_md=body,
        description=(body[:120].replace("\n", " ").strip() or name),
        claim={}, scope="session", overwrite=True,
        created_by="context_engineering")


def _skill_exists(tenant_id: str, name: str) -> bool:
    """Does the tenant's SkillForge registry already carry this skill? Errors read
    as "no" — the create path is itself best-effort, so a probe failure must not
    turn into a silent skip."""
    try:
        return _skill_registry(tenant_id).get(name) is not None
    except Exception:  # noqa: BLE001
        return False


def uncreate_skills(tenant_id: str, names) -> None:
    """Roll back forged skills that Gate-2 rejected (ADR-0283 R3 / review R2 A4).
    Best-effort; a failed delete never breaks the turn."""
    try:
        reg = _skill_registry(tenant_id)
    except Exception:  # noqa: BLE001
        return
    for n in names or []:
        try:
            reg.delete(n, reason="cel_gate2_denied")
        except Exception:  # noqa: BLE001
            continue


class SkillForgeStage:
    id = "skillforge"
    requires: tuple = ("llm_synthesis",)
    effect = "forge"
    trust = "builtin"

    def run(self, bundle, ctx):
        needs = (bundle.scratch.get("needs") or {}).get("skills") or []
        bound: list = []
        skipped_shape = 0
        for s in needs[:MAX_BINDINGS]:
            # A skill without a BODY is an empty injection: `render_skill_bindings`
            # would emit a heading with nothing under it, and the artifact on disk
            # would hold `# cel_<title>` and no instruction. A title alone ("CSV
            # parsing", "error reporting") is what a model returns when asked which
            # skills are needed — it is a topic, not a skill (ADR-0283 amendment,
            # 2026-08-18). Require the text; count and skip the rest.
            if not isinstance(s, dict) or not s.get("name"):
                skipped_shape += 1
                continue
            if not str(s.get("body") or "").strip():
                skipped_shape += 1
                continue
            name = s.get("name")
            # The SkillRegistry contract is "alphanumeric + . + _" — a hyphen RAISES
            # ValueError there. Letting one through was a live defect (found
            # 2026-08-18 by an unmocked E2E): an LLM names skills the way skills are
            # named — `loop-driven-engineering`, `concept-gate`, `code-review` — so
            # in practice EVERY forged skill hit the `except: pass` below, reached no
            # disk, and never entered `_forged_skills`, which also made the Gate-2
            # skill rollback a no-op again (the ADR-0283 R7 defect, second edition).
            # Map the separator instead of filtering it: dropping it would fuse words
            # ("loopdrivenengineering"), and a name is an operator-facing identifier.
            safe = "".join(c if (c.isalnum() or c in "._") else "_"
                           for c in str(name))[:48]
            if not safe.strip("._"):
                continue
            # Namespace CEL-forged skills exactly like ToolForge namespaces forged
            # tools (review R3 finding A4, applied to skills in R6): `_skill_create`
            # writes with `overwrite=True`, so an LLM-proposed name taken from the
            # task ("notes", "meeting-summary") would silently CLOBBER the
            # operator's own session skill of that name. The prefix also keeps the
            # rollback set (`_forged_skills`) disjoint from operator-authored ids.
            safe = safe.lstrip("._")
            if not safe:
                continue   # a name of only separators would collapse every skill
                           # onto the bare prefix `cel_` (review R7)
            safe = "cel_" + safe
            body = str(s.get("body"))
            # PRE-EXISTING skills are bound, never re-created and never rolled back
            # (found 2026-08-18): `_skill_create` writes with overwrite=True, and the
            # rollback deletes by NAME, so a turn whose payload Gate-2 denies used to
            # delete the same-named artifact an EARLIER turn had legitimately forged
            # and bound. LLM-proposed names repeat across turns constantly, so this
            # was reachable in ordinary use, not a corner case. Roll back only what
            # this turn actually brought into existence.
            pre_existing = _skill_exists(ctx.tenant_id, safe)
            if not pre_existing:
                try:
                    _skill_create(ctx.tenant_id, safe, body)
                    # Track for rollback if Gate-2 rejects the payload (review R2 A4).
                    bundle.scratch.setdefault("_forged_skills", []).append(safe)
                except Exception:  # noqa: BLE001 — fail-safe; still bind the ref
                    pass
            bound.append(SkillRef(skill_id=safe, body=body))
        bundle.skills_to_bind.extend(bound)
        reason = None
        if not bound:
            reason = "no_forgeable_skill_needs" if skipped_shape else "no_skill_needs"
        return bundle, StageTelemetry(
            stage=self.id, status="ok", reason=reason,
            confidence_tier="high" if bound else "low",
            sources=[{"id": b.skill_id, "score": 1.0} for b in bound])


register_stage(SkillForgeStage())
