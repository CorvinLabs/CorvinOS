"""D1 (PLAN-0948): a Data Hub SKILL artifact becomes a registered, attributable skill.

``UnifiedCreator`` produces a markdown artifact. Until now that artifact sat in the console's
artifact store and nothing could inject, grade or attribute it. Here it is written into the tenant's
SkillForge registry as a ``learned-experience`` skill whose ``meta`` carries the **data sources** it was
derived from, so the rest of the loop has a subject:

* the injector (``skill_inject``) can offer it to a turn like any other skill,
* an execution or a piece of feedback about it can be attributed to ``datahub:<artifact_id>``,
* a changed source or a weak consensus can regenerate it IN PLACE (``regenerate_skill_artifact``,
  grades and meta are preserved by ``MultiSkillRegistry.update_body``).

The bootstrap grade is the capped, disclosed seed every freshly minted skill needs before the injection
gate can see it (CLAUDE.md, Concept Gate); it is not earned usage.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SKILL_PREFIX = "assistant.datahub_"
PERSONA = "assistant"
SCOPE = "user"
CREATED_BY = "datahub-unified"
BOOTSTRAP_GRADE = 0.3
BOOTSTRAP_NOTES = (
    "datahub bootstrap seed - NOT earned usage; the injection gate needs n_grades >= 1 to see the skill"
)


def source_id(artifact_id: str) -> str:
    """The attribution id of one artifact's data source (content-free, stable)."""
    return f"datahub:{artifact_id}"


def skill_name_for(artifact_name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", artifact_name.lower()).strip("_") or "artifact"
    return (SKILL_PREFIX + slug)[:128]


@dataclass(frozen=True)
class Registered:
    name: str
    data_sources: tuple[str, ...]
    injectable: bool
    created: bool


def _registry(tenant_id: str):
    """The tenant's MultiSkillRegistry acting for the ``assistant`` persona (namespace gate)."""
    base = Path(__file__).resolve().parents[4] / "corvin_operator"
    import sys

    for d in (base / "skill-forge", base / "forge"):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
    from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415

    return MultiSkillRegistry(tenant_id=tenant_id, caller_persona=PERSONA)


def register_skill_artifact(
    tenant_id: str, *, artifact_id: str, name: str, description: str, body: str,
) -> Registered:
    """Write the artifact as a SkillForge skill (or refresh it), attributed to its data source."""
    reg = _registry(tenant_id)
    skill = skill_name_for(name)
    sources = [source_id(artifact_id)]
    meta = {"data_sources": sources, "origin": "datahub_unified", "artifact_id": artifact_id}
    existing = reg.get(skill)
    created = existing is None
    if created:
        spec = reg.create(scope=SCOPE, name=skill, type="learned-experience", body_md=body,
                          description=(description or name)[:300], created_by=CREATED_BY, meta=meta)
    else:
        spec = reg.update_body(skill, scope=SCOPE, body_md=body, description=(description or name)[:300])
    injectable = False
    if not list(getattr(spec, "grades", None) or []):
        try:
            spec = reg.grade(skill, run_id="datahub-bootstrap", score=BOOTSTRAP_GRADE,
                             notes=BOOTSTRAP_NOTES, organic=False)
        except Exception as exc:  # noqa: BLE001 - the skill exists; it just is not injectable yet
            logger.warning("datahub skill %s: bootstrap grade failed (%s)", skill, type(exc).__name__)
    injectable = spec.n_grades >= 1 and spec.mean_score > 0
    return Registered(name=skill, data_sources=tuple(sources), injectable=injectable, created=created)


def data_sources_of(spec: Any) -> list[str]:
    """The attribution ids a SkillSpec carries (empty for every ordinary skill)."""
    from core.background.learning_inbox import sources_of

    return sources_of(spec)


__all__ = ["Registered", "register_skill_artifact", "skill_name_for", "source_id", "data_sources_of"]
