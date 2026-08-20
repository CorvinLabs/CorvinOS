"""Bridge from the Skill-Creator to the SkillForge registry (ADR-0405).

Why a bridge and not a bare import
----------------------------------
``operator/skill-forge/`` is not guaranteed to be on ``sys.path``: it is
there for the console service (PYTHONPATH) but not for a plain ``pytest``
run or a CLI invocation of this package. A normal import is tried first and
a load from the explicit file location is the fallback, so the Skill-Creator
works in both.

Historical note: this package used to be named ``skill_forge`` too, which
collided with the registry package of the same name — whichever ``sys.path``
entry came first won, and the console route put THIS one first, so
``from skill_forge.multi_registry import ...`` failed process-wide for every
other consumer (skill_inject, promote.py, adapter.py). The package was
renamed to ``skill_creator``; do not name anything ``skill_forge`` here again.

What this buys
--------------
Writing a `.md` file into a directory is NOT promotion. The registry keeps a
manifest; ``SkillRegistry.list()`` reads the manifest, not the directory. A
skill that is only on disk is invisible to ``collect_active_skills()`` and can
never be injected into a turn. Going through ``registry.create()`` gets:

  * a manifest entry (so the skill is discoverable at all),
  * the fail-closed skill linter,
  * the plugin-slot mirror for ``user``/``project`` scope, which is what makes
    the skill visible to the next ``claude`` subprocess,
  * the hash-chained skill audit trail.

And the bootstrap grade closes the last gap: ``skill_inject`` drops any skill
with ``n_grades < 1 or mean_score <= 0``, so a freshly created skill sits
inert forever without one — it has no organic path to its own first grade,
because auto-grading only scores skills that were already injected.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Cap for a self-awarded grade, mirroring skill_inject's _AUTO_GRADE_CAP_MAX.
# A bootstrap seed must never look like earned usage.
BOOTSTRAP_GRADE = 0.3
BOOTSTRAP_NOTES = (
    "manual bootstrap seed by skill-creator — NOT earned usage; "
    "required so the injection gate (n_grades >= 1) can see the skill at all"
)

# Skills generated from an operator request are experience distilled into a
# reusable method — the registry's own vocabulary for that is
# "learned-experience" (VALID_TYPES in skill_forge/registry.py).
SKILL_TYPE = "learned-experience"

_PKG_MODULE = "_corvin_skillforge_pkg"
_REGISTRY_MODULE = _PKG_MODULE + ".registry"


class RegistryUnavailable(RuntimeError):
    """The SkillForge registry package could not be loaded."""


def _repo_root() -> Path:
    # operator/skill_creator/registry_bridge.py → <repo>
    return Path(__file__).resolve().parents[2]


def _load_registry_module():
    """Return the SkillForge registry module.

    Tries a normal import first; when ``operator/skill-forge`` is not on
    ``sys.path`` the package is loaded from its explicit file location under
    a private name, with ``submodule_search_locations`` set so the registry's
    own relative imports (``from .linter import lint``) resolve inside it.
    Deliberately does NOT mutate ``sys.path`` — a global side effect from an
    import helper is what caused the collision described above.
    """
    cached = sys.modules.get(_REGISTRY_MODULE)
    if cached is not None:
        return cached

    # Normal import when skill-forge is on sys.path (console service).
    try:
        import skill_forge.registry as _registry  # type: ignore  # noqa: PLC0415
        return _registry
    except ImportError:
        pass

    pkg_dir = _repo_root() / "operator" / "skill-forge" / "skill_forge"
    reg_path = pkg_dir / "registry.py"
    if not reg_path.is_file():
        raise RegistryUnavailable(f"SkillForge registry not found at {reg_path}")

    try:
        if _PKG_MODULE not in sys.modules:
            pkg_spec = importlib.util.spec_from_file_location(
                _PKG_MODULE, pkg_dir / "__init__.py",
                submodule_search_locations=[str(pkg_dir)],
            )
            if pkg_spec is None or pkg_spec.loader is None:
                raise RegistryUnavailable(f"cannot load package spec at {pkg_dir}")
            pkg = importlib.util.module_from_spec(pkg_spec)
            sys.modules[_PKG_MODULE] = pkg
            pkg_spec.loader.exec_module(pkg)

        reg_spec = importlib.util.spec_from_file_location(
            _REGISTRY_MODULE, reg_path,
        )
        if reg_spec is None or reg_spec.loader is None:
            raise RegistryUnavailable(f"cannot load module spec at {reg_path}")
        registry = importlib.util.module_from_spec(reg_spec)
        sys.modules[_REGISTRY_MODULE] = registry
        reg_spec.loader.exec_module(registry)
        return registry
    except RegistryUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(_REGISTRY_MODULE, None)
        raise RegistryUnavailable(f"could not load SkillForge registry: {exc}") from exc


def registry_for(root: Path):
    """Build a ``SkillRegistry`` rooted at ``<tenant-global>/skill-forge``."""
    module = _load_registry_module()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return module.SkillRegistry(root)


def promote_to_registry(
    root: Path,
    *,
    name: str,
    body_md: str,
    description: str,
    scope: str = "user",
    run_id: str = "",
    created_by: str = "skill-creator",
    grade: float = BOOTSTRAP_GRADE,
) -> dict[str, Any]:
    """Register a generated skill and seed its bootstrap grade.

    Returns a summary dict. Raises ``RegistryUnavailable`` when the registry
    cannot be loaded and propagates the registry's own errors (notably
    ``LinterError``) — promotion is fail-closed: a skill that cannot be
    registered must not be reported as promoted.
    """
    registry = registry_for(root)

    registry.create(
        name=name,
        type=SKILL_TYPE,
        body_md=body_md,
        description=description,
        scope=scope,
        overwrite=True,
        created_by=created_by,
    )

    graded = False
    try:
        registry.grade(name, run_id=run_id or "skill-creator-bootstrap",
                       score=grade, notes=BOOTSTRAP_NOTES)
        graded = True
    except Exception as exc:  # noqa: BLE001
        # A missing grade does not invalidate the registration, but it DOES
        # mean the skill stays below the injection gate — say so loudly.
        logger.error("Bootstrap grade for %s failed (%s) — skill will not be "
                     "injected until it is graded", name, exc)

    skill_dir = Path(root) / "skills" / name
    logger.info("Promoted %s to SkillForge registry at %s (graded=%s)",
                name, skill_dir, graded)
    return {
        "name": name,
        "scope": scope,
        "path": str(skill_dir / "SKILL.md"),
        "registry_root": str(root),
        "bootstrap_graded": graded,
        "injectable": graded,
    }


def read_skill(root: Path, name: str) -> Optional[dict[str, Any]]:
    """Return a registered skill's body + metadata, or None if unknown."""
    try:
        registry = registry_for(root)
    except RegistryUnavailable:
        return None
    spec = registry.get(name)
    if spec is None:
        return None
    body = registry.get_body(name)
    return {
        "name": spec.name,
        "type": getattr(spec, "type", ""),
        "description": getattr(spec, "description", ""),
        "scope": getattr(spec, "scope", ""),
        "created_by": getattr(spec, "created_by", ""),
        "sha256": getattr(spec, "sha256", ""),
        "grades": list(getattr(spec, "grades", []) or []),
        "n_grades": spec.n_grades,
        "mean_score": spec.mean_score,
        "injectable": spec.n_grades >= 1 and spec.mean_score > 0,
        "body": body or "",
    }


def list_skills(root: Path) -> list[dict[str, Any]]:
    """List every registered skill with its injection eligibility."""
    try:
        registry = registry_for(root)
    except RegistryUnavailable:
        return []
    out: list[dict[str, Any]] = []
    for spec in registry.list():
        out.append({
            "name": spec.name,
            "type": getattr(spec, "type", ""),
            "description": getattr(spec, "description", ""),
            "scope": getattr(spec, "scope", ""),
            "created_by": getattr(spec, "created_by", ""),
            "n_grades": spec.n_grades,
            "mean_score": round(spec.mean_score, 3),
            "injectable": spec.n_grades >= 1 and spec.mean_score > 0,
        })
    return out


def delete_skill(root: Path, name: str, *, reason: str = "") -> bool:
    """Remove a skill from the manifest, disk and plugin slot.

    Returns False when the skill was not registered. Deletion goes through
    the registry (never a bare `rmtree`) so the manifest, the engine plugin
    slot and the hash-chained skill audit all stay consistent — a directory
    removed behind the registry's back leaves a manifest entry pointing at
    nothing, and `SkillRegistry.list()` would keep reporting it.
    """
    try:
        registry = registry_for(root)
    except RegistryUnavailable:
        return False
    return bool(registry.delete(name, reason=reason or "deleted from console"))


def skill_body(root: Path, name: str) -> Optional[str]:
    """Raw SKILL.md of a registered skill, or None."""
    try:
        registry = registry_for(root)
    except RegistryUnavailable:
        return None
    return registry.get_body(name)


def strip_front_matter(body: str) -> str:
    """Return the markdown body without the registry's YAML front-matter.

    A refine round feeds the previous body back to the model as the starting
    point. Handing it the front-matter too invites the model to reproduce it,
    and `registry.create()` renders its own — two blocks make the skill
    unparseable for the engine that loads it.
    """
    text = (body or "").lstrip()
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4:].lstrip("\n")
