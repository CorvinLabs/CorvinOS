"""The per-tenant project registry: an INDEX, never the truth (ADR-2315 Decisions 1 and 6).

``<tenant_home>/projects/registry.json`` (file 0o600, directory 0o700):

    {"schema": 1, "projects": [{"id", "path", "template", "added_at"}]}

and, beside it, ``settings.json`` with the operator's ``projects_root`` (so that losing the index
never loses the place :func:`rebuild` has to look in).

The truth is each directory's ``project.yaml``; :func:`rebuild` restores the index from them. The
console's project projector (``corvin_console.kb_projection.project_kbs``) reads this same shape.

The two operations that take a path from a client - planning/creating and adopting - go through
:func:`validate_location`: only places under the tenant's ``projects_root``, no ``..``, nothing
inside ``CORVIN_HOME`` or another tenant's home, no protected component, and one directory belongs
to exactly one tenant.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from core.paths.tenant import corvin_home, tenant_home
from core.tenants import validate_tenant_id

from . import manifest
from .fsutil import atomic_write, file_lock

SCHEMA = 1
#: components that must never be (part of) a project root: version-control internals and the
#: platform's own protected stores (path gate / session ledger / CEL anchors)
PROTECTED_COMPONENTS = frozenset({".git", ".corvin", "session_ledger", ".corvin-ledger", "cel_anchors",
                                  "pending_notifications", "tenants"})
MAX_PROJECTS = 200


class ProjectPathError(ValueError):
    """A client-supplied location is not allowed (-> HTTP 422); the message names the rule, never a path."""


class RegistryError(RuntimeError):
    """The registry is unreadable or an entry conflicts (-> HTTP 409/500)."""


def registry_path(tenant_id: str) -> Path:
    return tenant_home(validate_tenant_id(tenant_id)) / "projects" / "registry.json"


def _empty() -> dict[str, Any]:
    return {"schema": SCHEMA, "projects": []}


def load(tenant_id: str) -> dict[str, Any]:
    p = registry_path(tenant_id)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError):
        raise RegistryError("the project registry is unreadable") from None
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        raise RegistryError("the project registry has an unexpected shape")
    return data


def _save(tenant_id: str, data: dict[str, Any]) -> None:
    p = registry_path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    atomic_write(p, (json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8"), mode=0o600)


def _lock(tenant_id: str):
    return file_lock(registry_path(tenant_id).with_name("registry.lock"))


def settings_path(tenant_id: str) -> Path:
    """The operator's setting lives NEXT TO the index, not in it: the index is rebuilt from the
    directories (:func:`rebuild`), the setting is what tells it where to look."""
    return registry_path(tenant_id).with_name("settings.json")


def projects_root(tenant_id: str) -> Path:
    """The tenant's allowlisted parent of all project directories: the operator's setting, else
    ``~/projects`` (ADR-2315 plan E7: one root per tenant, an operator setting)."""
    try:
        raw = json.loads(settings_path(tenant_id).read_text(encoding="utf-8")).get("projects_root")
    except (OSError, ValueError, AttributeError):
        raw = None
    return Path(raw).expanduser() if isinstance(raw, str) and raw.strip() else Path.home() / "projects"


def validate_root(tenant_id: str, path: str) -> Path:
    """Judge a new projects root. Absolute, no '..', not '/', not in or above the CorvinOS home, no protected component, not a file.
    Returns the resolved directory (it may not exist yet)."""
    validate_tenant_id(tenant_id)
    if not isinstance(path, str) or not path.strip() or "\x00" in path or len(path) > 1000:
        raise ProjectPathError("the projects folder must be a path")
    given = Path(path.strip()).expanduser()
    if not given.is_absolute() or ".." in given.parts:
        raise ProjectPathError("the projects folder must be an absolute path without '..'")
    root = _resolve_forgiving(given)
    if root == Path(root.anchor) or len(root.parts) < 3:
        raise ProjectPathError("choose a folder below your home or data directory, not a top-level one")
    home = _resolve_forgiving(corvin_home())
    if root == home or home in root.parents or root in home.parents:
        raise ProjectPathError("the projects folder must not be inside, or contain, the CorvinOS home")
    if any(part in PROTECTED_COMPONENTS for part in root.parts):
        raise ProjectPathError("the projects folder contains a protected path component")
    if root.exists() and not root.is_dir():
        raise ProjectPathError("that path is a file, not a folder")
    return root


def set_projects_root(tenant_id: str, path: str) -> Path:
    root = validate_root(tenant_id, path)
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700) if not any(root.iterdir()) else None
    except OSError:
        pass
    p = settings_path(tenant_id)
    with _lock(tenant_id):
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p.parent, 0o700)
        except OSError:
            pass
        atomic_write(p, (json.dumps({"projects_root": str(root)}, indent=1) + "\n").encode("utf-8"), mode=0o600)
    return root


def _resolve_forgiving(p: Path) -> Path:
    """resolve() a path whose last components may not exist yet (create): the deepest existing
    ancestor is resolved strictly (symlinks followed), the rest is appended literally."""
    p = Path(os.path.abspath(p))
    tail: list[str] = []
    cur = p
    while not cur.exists() and cur != cur.parent:
        tail.append(cur.name)
        cur = cur.parent
    return cur.resolve().joinpath(*reversed(tail))


def _other_tenants_claiming(tenant_id: str, resolved: Path) -> bool:
    """True when another tenant's registry lists this directory (existence only, no content leaks)."""
    tenants_dir = corvin_home() / "tenants"
    try:
        names = [d.name for d in tenants_dir.iterdir() if d.is_dir() and d.name != tenant_id]
    except OSError:
        return False
    for name in names:
        reg = tenants_dir / name / "projects" / "registry.json"
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for e in data.get("projects", []) if isinstance(data, dict) else []:
            try:
                if isinstance(e, dict) and _resolve_forgiving(Path(str(e.get("path")))) == resolved:
                    return True
            except (OSError, ValueError):
                continue
    return False


def validate_location(tenant_id: str, raw: str, *, adopt: bool = False, unmanaged: bool = False) -> Path:
    """Judge a client-supplied project location. Returns the resolved directory.

    ``adopt=False`` (create): the directory must not exist or be empty. ``adopt=True``: it must
    exist and hold a ``project.yaml`` naming THIS tenant."""
    validate_tenant_id(tenant_id)
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw or len(raw) > 1000:
        raise ProjectPathError("the location must be a non-empty path")
    given = Path(raw).expanduser()
    if ".." in given.parts:
        raise ProjectPathError("the location must not contain '..'")
    if not given.is_absolute():
        raise ProjectPathError("the location must be an absolute path")
    resolved = _resolve_forgiving(given)
    root = _resolve_forgiving(projects_root(tenant_id))
    if resolved == root or root not in resolved.parents:
        raise ProjectPathError("the location must be inside this tenant's projects root")
    home = _resolve_forgiving(corvin_home())
    if resolved == home or home in resolved.parents or resolved in home.parents:
        raise ProjectPathError("the location must not be inside the CorvinOS home")
    if any(part in PROTECTED_COMPONENTS for part in resolved.relative_to(root).parts):
        raise ProjectPathError("the location contains a protected path component")
    if any(part.startswith(".") for part in resolved.relative_to(root).parts[:1]):
        raise ProjectPathError("a project directory must not be hidden")
    # no symlink anywhere between the projects root and the target, judged on the path AS GIVEN
    # (resolve() would silently follow it to a place that looks harmless)
    cur = Path(os.path.abspath(given))
    base = Path(os.path.abspath(projects_root(tenant_id)))
    try:
        parts = cur.relative_to(base).parts
    except ValueError:
        parts = ()          # given lies outside the root as written; the resolved check above decides
    walk = base
    for part in parts:
        walk = walk / part
        if walk.is_symlink():
            raise ProjectPathError("the location must not be, or lie below, a symlink")
    if base.is_symlink():
        raise ProjectPathError("the projects root must not be a symlink")
    if _other_tenants_claiming(tenant_id, resolved):
        raise ProjectPathError("this directory already belongs to another tenant")
    if adopt and unmanaged:
        # a directory that is not a project yet: it must exist and must NOT carry a foreign manifest
        if not resolved.is_dir():
            raise ProjectPathError("the directory does not exist")
        if (resolved / manifest.FILE).exists() or (resolved / manifest.FILE).is_symlink():
            raise ProjectPathError("the directory already has a project.yaml")
    elif adopt:
        if not resolved.is_dir():
            raise ProjectPathError("the directory does not exist")
        try:
            man = manifest.load(resolved)
        except manifest.ManifestError:
            raise ProjectPathError("the directory has no valid project.yaml") from None
        if man.get("tenant") != tenant_id:
            raise ProjectPathError("project.yaml names another tenant")
    else:
        if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
            raise ProjectPathError("the directory already exists and is not empty")
    return resolved


def entries(tenant_id: str) -> list[dict[str, Any]]:
    return [e for e in load(tenant_id).get("projects", []) if isinstance(e, dict)]


def get(tenant_id: str, project_id: str) -> Optional[dict[str, Any]]:
    for e in entries(tenant_id):
        if e.get("id") == project_id:
            return e
    return None


def register(tenant_id: str, project_id: str, path: Path, template: Optional[str]) -> dict[str, Any]:
    if not manifest.PROJECT_ID_RE.match(project_id):
        raise ProjectPathError("invalid project id")
    resolved = _resolve_forgiving(path)
    with _lock(tenant_id):
        data = load(tenant_id)
        lst = [e for e in data.get("projects", []) if isinstance(e, dict)]
        if any(e.get("id") == project_id for e in lst):
            raise RegistryError("a project with this id already exists")
        if any(_resolve_forgiving(Path(str(e.get("path")))) == resolved for e in lst):
            raise RegistryError("this directory is already registered")
        if len(lst) >= MAX_PROJECTS:
            raise RegistryError(f"at most {MAX_PROJECTS} projects per tenant")
        entry = {"id": project_id, "path": str(resolved), "template": template or "",
                 "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        data["projects"] = lst + [entry]
        data.setdefault("schema", SCHEMA)
        _save(tenant_id, data)
    return entry


def unregister(tenant_id: str, project_id: str) -> bool:
    with _lock(tenant_id):
        data = load(tenant_id)
        lst = [e for e in data.get("projects", []) if isinstance(e, dict)]
        keep = [e for e in lst if e.get("id") != project_id]
        if len(keep) == len(lst):
            return False
        data["projects"] = keep
        _save(tenant_id, data)
    return True


def rebuild(tenant_id: str) -> dict[str, Any]:
    """Restore the index from the directories (ADR-2315 Decision 1): every direct child of the
    projects root whose ``project.yaml`` names THIS tenant is registered if it is not yet. Never
    removes an entry; never touches a directory. Returns ``{added, kept, skipped}``."""
    root = projects_root(tenant_id)
    added, skipped = [], []
    try:
        children = sorted(d for d in root.iterdir() if d.is_dir() and not d.is_symlink())
    except OSError:
        return {"added": [], "kept": len(entries(tenant_id)), "skipped": []}
    for d in children:
        try:
            man = manifest.load(d)
        except manifest.ManifestError:
            continue
        if man.get("tenant") != tenant_id:
            skipped.append(man.get("id", d.name))
            continue
        if get(tenant_id, man["id"]) is not None:
            continue
        try:
            validate_location(tenant_id, str(d), adopt=True)
            register(tenant_id, man["id"], d, man.get("template"))
            added.append(man["id"])
        except (ProjectPathError, RegistryError):
            skipped.append(man["id"])
    return {"added": added, "kept": len(entries(tenant_id)) - len(added), "skipped": skipped}


def relocate(tenant_id: str, project_id: str, new_path: Path) -> dict[str, Any]:
    """Point an entry at the directory the project now lives in. The index only; moves nothing."""
    resolved = _resolve_forgiving(new_path)
    with _lock(tenant_id):
        data = load(tenant_id)
        lst = [e for e in data.get("projects", []) if isinstance(e, dict)]
        if any(e.get("id") != project_id and _resolve_forgiving(Path(str(e.get("path")))) == resolved for e in lst):
            raise RegistryError("this directory is already registered")
        hit = None
        for e in lst:
            if e.get("id") == project_id:
                e["path"] = str(resolved)
                hit = e
        if hit is None:
            raise RegistryError("no such project")
        data["projects"] = lst
        _save(tenant_id, data)
    return hit
