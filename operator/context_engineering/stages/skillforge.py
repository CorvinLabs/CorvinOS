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
        for s in needs[:MAX_BINDINGS]:
            name = s.get("name") if isinstance(s, dict) else str(s)
            if not name:
                continue
            safe = "".join(c for c in str(name) if c.isalnum() or c in "._-")[:48]
            if not safe:
                continue
            # Namespace CEL-forged skills exactly like ToolForge namespaces forged
            # tools (review R3 finding A4, applied to skills in R6): `_skill_create`
            # writes with `overwrite=True`, so an LLM-proposed name taken from the
            # task ("notes", "meeting-summary") would silently CLOBBER the
            # operator's own session skill of that name. The prefix also keeps the
            # rollback set (`_forged_skills`) disjoint from operator-authored ids.
            safe = safe.lstrip("._-")
            if not safe:
                continue   # a name of only separators would collapse every skill
                           # onto the bare prefix `cel_` (review R7)
            safe = "cel_" + safe
            body = (s.get("body") if isinstance(s, dict) else "") or f"# {safe}\n"
            try:
                _skill_create(ctx.tenant_id, safe, body)
                # Track for rollback if Gate-2 rejects the payload (review R2 A4).
                bundle.scratch.setdefault("_forged_skills", []).append(safe)
            except Exception:  # noqa: BLE001 — fail-safe; still bind the ref
                pass
            bound.append(SkillRef(skill_id=safe, body=body))
        bundle.skills_to_bind.extend(bound)
        return bundle, StageTelemetry(
            stage=self.id, status="ok",
            confidence_tier="high" if bound else "low",
            sources=[{"id": b.skill_id, "score": 1.0} for b in bound])


register_stage(SkillForgeStage())
