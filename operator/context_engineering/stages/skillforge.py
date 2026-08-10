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


def _skill_create(tenant_id: str, name: str, body: str) -> None:
    """Create a task-scoped learned-experience skill, in-process. Best-effort."""
    from skill_forge.multi_registry import MultiRegistry  # noqa: PLC0415
    from forge.paths import tenant_home  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    root = Path(tenant_home(tenant_id)) / "skill-forge"
    MultiRegistry(root).create(scope="session", name=name, type="learned-experience",
                               body_md=body, description=body[:120], claim={},
                               overwrite=True)


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
            body = (s.get("body") if isinstance(s, dict) else "") or f"# {safe}\n"
            try:
                _skill_create(ctx.tenant_id, safe, body)
            except Exception:  # noqa: BLE001 — fail-safe; still bind the ref
                pass
            bound.append(SkillRef(skill_id=safe, body=body))
        bundle.skills_to_bind.extend(bound)
        return bundle, StageTelemetry(
            stage=self.id, status="ok",
            confidence_tier="high" if bound else "low",
            sources=[{"id": b.skill_id, "score": 1.0} for b in bound])


register_stage(SkillForgeStage())
