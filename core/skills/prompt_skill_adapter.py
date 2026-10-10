"""ADR-2175 — prompt skills as Skills 2.0 programs.

A SkillForge / bundle SKILL.md has no code, but "inject this text into the turn
or not" is a decision, and a decision belongs in the registry: audited, attributed
(LoM), failure-counted, DAG-checked. ONE generic ``PromptSkill`` registers every
prompt skill as ``prompt.<name>``; nothing is rewritten by hand.

* **Version** = the content hash (``0.0.0+<sha8>``): a prompt skill has no version
  field, only ``sha256``. A changed hash re-registers the skill and is audited again.
* **``skill.migrated``** is chained once per (id, hash) the registry has not seen —
  never the body, never free text: ids, a source label, 16 hex of the hash.
* ``learn=False``: one execution per injected skill per turn would flood the
  learning store with events no optimizer consumes (the ``os.capabilities`` lesson).
  Every execution is still AUDITED.

``sync_prompt_skills`` is idempotent and thread-safe. Boot calls it
(``skills.boot.boot_skills``); the injector calls it per turn (T-0104) because
which SkillForge skills exist depends on the channel/project scope of that turn.
"""
from __future__ import annotations

import hashlib
import logging
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .skill_registry_phase1 import (
    Skill,
    SkillMetadata,
    SkillOrigin,
    SkillTier,
    SkillsRegistry,
    _utc_now_iso,
)

logger = logging.getLogger(__name__)

PROMPT_PREFIX = "prompt."
_SYNC_LOCK = threading.RLock()
_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PromptSkillSource:
    """One prompt skill as found on disk. ``scope`` is ``bundle`` or a SkillForge scope."""

    name: str
    description: str
    sha256: str
    scope: str


def prompt_skill_id(name: str) -> str:
    return f"{PROMPT_PREFIX}{name}"


def prompt_skill_version(sha256: str) -> str:
    return f"0.0.0+{sha256[:8]}" if sha256 else "0.0.0+unhashed"


class PromptSkill(Skill):
    """``execute`` answers "inject" for the turn; the body never leaves the source.

    Selection (grade gate, relevance, cap) stays with the injector for now — this
    skill records that the injection DECISION happened, under this version.
    """

    def __init__(self, source: PromptSkillSource):
        self.source = source
        bundle = source.scope == "bundle"
        super().__init__(SkillMetadata(
            id=prompt_skill_id(source.name),
            name=source.name,
            description=(source.description or source.name)[:200],
            version=prompt_skill_version(source.sha256),
            origin=SkillOrigin.BUILTIN if bundle else SkillOrigin.COMMUNITY,
            owner="bundle" if bundle else "skill-forge",
            tags=["prompt", source.scope],
            learn=False,
            tier=SkillTier.INSTALLED,
        ))

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        return {"decision": "inject", "mode": self.source.scope}


@dataclass
class SyncResult:
    registered: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    unchanged: int = 0
    failed: List[str] = field(default_factory=list)
    audit_failures: int = 0


def sync_prompt_skills(registry: SkillsRegistry, sources: Iterable[PromptSkillSource],
                       *, tenant_id: str) -> SyncResult:
    """Make the registry hold exactly the given prompt skills at their current hashes."""
    result = SyncResult()
    backend = getattr(registry, "audit_backend", None)
    with _SYNC_LOCK:
        failures_before = int(getattr(backend, "write_failures", 0))
        for src in sources:
            sid = prompt_skill_id(src.name)
            version = prompt_skill_version(src.sha256)
            existing = registry.get(sid)
            if existing is not None and existing.metadata.version == version:
                result.unchanged += 1
                continue
            try:
                if existing is not None:
                    registry.unregister(sid)
                registry.register(PromptSkill(src))
            except Exception as exc:  # noqa: BLE001 — one bad skill never blocks the rest
                logger.warning("prompt skill %s not registered (%s)", sid, type(exc).__name__)
                result.failed.append(sid)
                continue
            action = "updated" if existing is not None else "registered"
            (result.updated if existing is not None else result.registered).append(sid)
            registry._write_audit({
                "event_type": "SKILL_MIGRATED",
                "skill_id": sid,
                "skill_version": version,
                "source": src.scope,
                "action": action,
                "content_hash": src.sha256[:16],
                "timestamp": _utc_now_iso(),
                "tenant_id": tenant_id,
            })
        result.audit_failures = int(getattr(backend, "write_failures", 0)) - failures_before
    if result.audit_failures:
        logger.error("%d skill.migrated record(s) NOT chained", result.audit_failures)
    return result


# ── sources ──────────────────────────────────────────────────────────────────

def _frontmatter_description(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    for line in text[3:end].splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def bundle_sources() -> List[PromptSkillSource]:
    """Every SKILL.md of the shipped bundle (``corvin_operator/bundle/skills/**``)."""
    out: List[PromptSkillSource] = []
    base = _REPO_ROOT / "corvin_operator" / "bundle" / "skills"
    if not base.is_dir():  # wheel layouts without the bundle: nothing to register
        return out
    for md in sorted(base.rglob("SKILL.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        name = md.parent.name
        out.append(PromptSkillSource(
            name=name, description=_frontmatter_description(text) or name,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), scope="bundle"))
    return out


def skillforge_sources(tenant_id: str, *, channel_id: Optional[str] = None,
                       project_root: Optional[Path] = None) -> List[PromptSkillSource]:
    """The tenant's SkillForge skills visible from this scope (best-effort, never raises)."""
    try:
        base = _REPO_ROOT / "corvin_operator"
        for d in (base / "skill-forge", base / "forge"):
            if str(d) not in sys.path:
                sys.path.insert(0, str(d))
        from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415

        reg = MultiSkillRegistry(tenant_id=tenant_id, channel_id=channel_id, project_root=project_root)
        return [PromptSkillSource(name=spec.name, description=spec.description or spec.name,
                                  sha256=spec.sha256 or "", scope=scope)
                for scope, spec in reg.list_with_scope()]
    except Exception as exc:  # noqa: BLE001
        logger.info("skillforge prompt skills unavailable (%s)", type(exc).__name__)
        return []


def collect_prompt_sources(tenant_id: str, **scope: Any) -> List[PromptSkillSource]:
    """Bundle skills win over a same-named SkillForge skill (the injector's rule)."""
    bundle = bundle_sources()
    taken = {s.name for s in bundle}
    return bundle + [s for s in skillforge_sources(tenant_id, **scope) if s.name not in taken]


__all__ = ["PromptSkill", "PromptSkillSource", "SyncResult", "collect_prompt_sources",
           "prompt_skill_id", "prompt_skill_version", "sync_prompt_skills"]
