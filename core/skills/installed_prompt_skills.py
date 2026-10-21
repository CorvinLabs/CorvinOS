"""Imported skills become SkillForge prompt skills (G9, Skills 2.0).

A Forge-bundle / single-skill import stores the package under
``<corvin_home>/skills_installed/<id>/<version>/``. Nothing booted that store, so an
imported skill was reported "installed" and never took part in a turn.

This module is the ONE bridge from that store into the machinery that already decides
injection: the tenant's SkillForge registry (scope ``user``). Nothing new decides what
reaches a prompt:

* the SkillForge **linter** runs fail-closed on the body (prompt-injection patterns);
* the name is ``installed.<id>`` — an import can never shadow a curated skill, and an
  existing skill under that name is never overwritten by another origin;
* the skill starts **ungraded**, so the injector's grade gate keeps it out of automatic
  injection; it runs when the operator asks for it by name or after real grades;
* ``prompt_skill_adapter`` then registers it as ``prompt.installed.<id>`` (audited).

Every outcome is returned as a short, path-free reason — the import UI shows it instead
of a bare "installed".
"""
from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NAME_PREFIX = "installed."
SCOPE = "user"
ORIGIN = "forge-bundle-import"
#: A SKILL.md larger than this is refused rather than read into memory.
MAX_BODY_BYTES = 256 * 1024
#: Boot catch-up never activates more than this many skills at once (each one spends
#: a free-tier authoring credit, so a large backlog must not drain a day's quota).
BOOT_CATCHUP_LIMIT = 3

_UNSAFE = re.compile(r"[^A-Za-z0-9._]")
_SAFE_PART = re.compile(r"[A-Za-z0-9._+-]{1,128}")


@dataclass(frozen=True)
class Activation:
    skill_id: str
    version: str
    #: ``activated`` | ``updated`` | ``unchanged`` | ``refused`` | ``unavailable``
    status: str
    forge_name: str = ""
    reason: str = ""

    @property
    def active(self) -> bool:
        return self.status in ("activated", "updated", "unchanged")

    def describe(self) -> str:
        """One path-free sentence for the import result."""
        if self.status in ("activated", "updated"):
            return (f"available as prompt skill '{self.forge_name}' (ungraded: it is used when "
                    f"requested by name or once it has earned grades)")
        if self.status == "unchanged":
            return f"already available as prompt skill '{self.forge_name}'"
        return f"stored but not active as a prompt skill: {self.reason}"


def forge_name_for(skill_id: str) -> str:
    """``installed.<id>`` with every character SkillForge rejects replaced by ``_``."""
    core = re.sub(r"\.{2,}", ".", _UNSAFE.sub("_", skill_id)).strip(".")
    return (NAME_PREFIX + core)[:128].rstrip(".")


def _ensure_skill_forge_importable() -> None:
    base = Path(__file__).resolve().parents[2] / "corvin_operator"
    for d in (base / "skill-forge", base / "forge"):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))


def _frontmatter_description(text: str) -> str:
    from .prompt_skill_adapter import _frontmatter_description as _fd  # noqa: PLC0415

    return _fd(text)


def activate_installed_skill(skill_id: str, version: str, body_md: str, *,
                             tenant_id: str) -> Activation:
    """Make one installed skill's SKILL.md body a SkillForge ``user``-scope skill.

    Never raises: the install already happened and must not be reported as failed
    because the prompt side said no.
    """
    name = forge_name_for(skill_id)
    if len(body_md.encode("utf-8", "replace")) > MAX_BODY_BYTES:
        return Activation(skill_id, version, "refused", name, "SKILL.md is larger than 256 KiB")
    if not body_md.strip():
        return Activation(skill_id, version, "refused", name, "SKILL.md is empty")
    try:
        _ensure_skill_forge_importable()
        from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415
        from skill_forge.registry import (  # noqa: PLC0415
            LinterError, NamespaceDenied, SkillQuotaExceeded)
    except Exception as exc:  # noqa: BLE001
        logger.info("skill forge unavailable for %s (%s)", skill_id, type(exc).__name__)
        return Activation(skill_id, version, "unavailable", name, "SkillForge is not available on this install")

    description = _frontmatter_description(body_md) or skill_id
    meta: Dict[str, Any] = {"origin": ORIGIN, "source_id": skill_id, "source_version": version}
    try:
        reg = MultiSkillRegistry(tenant_id=tenant_id)
        existing_scope = reg.find_scope(name)
        if existing_scope is not None:
            spec = reg.get_in_scope(name, existing_scope)
            if spec is None or (spec.meta or {}).get("origin") != ORIGIN:
                return Activation(skill_id, version, "refused", name,
                                  "a skill with this name already exists and was not imported")
            if (spec.meta or {}).get("source_version") == version:
                return Activation(skill_id, version, "unchanged", name)
            # A re-sync of an imported skill is not new authoring: no quota, and the synced
            # version is recorded so the next boot reads "unchanged".
            reg.update_body(name, scope=existing_scope, body_md=body_md, description=description,
                            quota_exempt=True, meta={"source_version": version})
            return Activation(skill_id, version, "updated", name)
        reg.create(scope=SCOPE, name=name, type="domain", body_md=body_md,
                   description=description, overwrite=False,
                   created_by=ORIGIN, meta=meta)
        return Activation(skill_id, version, "activated", name)
    except LinterError as exc:
        # The linter's messages name the matched pattern, not the body.
        first = str(exc.violations[0]) if exc.violations else "lint error"
        return Activation(skill_id, version, "refused", name, f"the SkillForge linter refused the body ({first[:120]})")
    except SkillQuotaExceeded:
        return Activation(skill_id, version, "refused", name, "today's skill-authoring quota is used up; it is retried at the next start")
    except NamespaceDenied:
        return Activation(skill_id, version, "refused", name, "the namespace gate refused the name")
    except Exception as exc:  # noqa: BLE001
        logger.warning("installed skill %s not activated (%s)", skill_id, type(exc).__name__)
        return Activation(skill_id, version, "refused", name, f"unexpected error ({type(exc).__name__})")


def _installed_root() -> Path:
    _ensure_skill_forge_importable()
    from forge import paths as _forge_paths  # noqa: PLC0415

    return _forge_paths.corvin_home() / "skills_installed"


def _registry_entries(root: Path) -> Dict[str, List[Dict[str, Any]]]:
    try:
        data = json.loads((root / "skills_registry.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_body(root: Path, skill_id: str, version: str) -> Optional[str]:
    """The top-level SKILL.md of an installed package, or None. Symlinks are never followed."""
    if not (_SAFE_PART.fullmatch(skill_id) and _SAFE_PART.fullmatch(version)) or ".." in skill_id + version:
        return None
    path = root / skill_id / version / skill_id / "SKILL.md"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BODY_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def activate_missing_installed_skills(tenant_id: str, *, limit: int = BOOT_CATCHUP_LIMIT) -> List[Activation]:
    """Boot catch-up: activate installed skills the tenant's SkillForge does not hold yet.

    Only VERIFIED registry entries are considered, newest version last in the entry list.
    Idempotent: a skill already present at that version costs nothing.
    """
    out: List[Activation] = []
    try:
        root = _installed_root()
        entries = _registry_entries(root)
    except Exception as exc:  # noqa: BLE001
        logger.info("installed skills not scanned (%s)", type(exc).__name__)
        return out
    for skill_id, versions in sorted(entries.items()):
        if len([a for a in out if a.status in ("activated", "updated")]) >= limit:
            break
        if not isinstance(versions, list) or not versions:
            continue
        entry = versions[-1]
        if not isinstance(entry, dict) or entry.get("verified") is not True:
            continue
        version = str(entry.get("version", ""))
        body = _read_body(root, skill_id, version) if version else None
        if body is None:
            continue
        out.append(activate_installed_skill(skill_id, version, body, tenant_id=tenant_id))
    return out


__all__ = ["Activation", "activate_installed_skill", "activate_missing_installed_skills",
           "forge_name_for", "NAME_PREFIX", "SCOPE", "ORIGIN"]
