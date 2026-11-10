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

import functools
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from core.paths.tenant import corvin_home, tenant_home
from core.tenants import validate_tenant_id

from . import manifest
from .fsutil import atomic_write, file_lock, is_link, read_text_any

SCHEMA = 1
#: components that must never be (part of) a project root: version-control internals and the
#: platform's own protected stores (path gate / session ledger / CEL anchors)
PROTECTED_COMPONENTS = frozenset({".git", ".corvin", "session_ledger", ".corvin-ledger", "cel_anchors",
                                  "pending_notifications", "tenants"})


def _protected(parts) -> bool:
    """Case-insensitive: on NTFS `.GIT` IS the git directory and `Tenants` the tenants folder."""
    return any(str(part).lower() in PROTECTED_COMPONENTS for part in parts)
MAX_PROJECTS = 200


class ProjectPathError(ValueError):
    """A client-supplied location is not allowed (-> HTTP 422); the message names the rule, never a path."""


class RegistryError(RuntimeError):
    """The registry is unreadable or an entry conflicts (-> HTTP 409/500)."""


_WIN_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_MAX_PATH = 260
_FILE_BUDGET = 95


def _read_json(p: Path) -> Any:
    """JSON as Windows editors save it (UTF-8 with or without BOM, UTF-16 with BOM)."""
    return json.loads(read_text_any(p))


@functools.lru_cache(maxsize=1)
def long_paths_enabled() -> bool:
    """Windows: whether this process may use paths of 260 characters and more (``LongPathsEnabled``). Elsewhere always True."""
    if os.name != "nt":
        return True
    try:
        import ctypes  # noqa: PLC0415

        return bool(ctypes.WinDLL("ntdll").RtlAreLongPathsEnabled())
    except (OSError, AttributeError):
        return False


def normalize_input(raw: str, what: str = "the location") -> str:
    r"""Windows spellings of a local path reduced to the plain one BEFORE anything touches the filesystem: ``\\?\C:\x`` and
    ``\\.\C:\x`` become ``C:\x``; a network (UNC) or device path is refused here, so a request never waits for an SMB timeout.
    Elsewhere the input is returned unchanged."""
    if os.name != "nt":
        return raw
    t = raw.strip().replace("/", "\\")
    if t[:4] in ("\\\\?\\", "\\\\.\\"):
        rest = t[4:]
        if re.match(r"^[A-Za-z]:\\", rest):
            return rest
        if rest[:4].upper() == "UNC\\":
            raise ProjectPathError(f"{what} must be on a local drive; network (UNC) paths are not supported - map the share to a drive letter")
        raise ProjectPathError(f"{what} must be a folder on a drive, not a device path")
    if t.startswith("\\\\"):
        raise ProjectPathError(f"{what} must be on a local drive; network (UNC) paths are not supported - map the share to a drive letter")
    return raw.strip()


def _check_windows_names(p: Path, what: str) -> None:
    """Names Windows cannot create or silently changes: device names (``con``, ``nul.txt`` ...), a trailing dot or space,
    and the characters ``< > : " | ? *``. Refused with the rule, so the user never sees "does not exist" for a name Windows ate."""
    if os.name != "nt":
        return
    cur = Path(p.parts[0])
    for part in p.parts[1:]:
        cur = cur / part
        if _WIN_BAD_CHARS.search(part):
            raise ProjectPathError(f'{what} contains a character Windows does not allow in a name (< > : " | ? *)')
        if part != part.rstrip(". "):
            raise ProjectPathError(f"{what} contains a name that ends in a dot or a space, which Windows removes")
        # an EXISTING folder with such a name evidently works (Windows 11 opens `aux`, `con.d` ...); only a name to be created is refused
        if part.split(".", 1)[0].rstrip(" ").lower() in manifest.WINDOWS_DEVICE_NAMES and not os.path.lexists("\\\\?\\" + str(cur)):
            raise ProjectPathError(f"{what} contains a reserved Windows device name (con, nul, aux, prn, com1, lpt1 ...)")


def _check_length(p: Path, what: str) -> None:
    # project.yaml and the files below must stay reachable; with long paths off the whole folder would read as "missing"
    # budget: the longest file the engine writes below a project - docs\kb\tasks\T-0001-<slug up to 60>.md is about 85 characters,
    # the atomic-write temp names .project.yaml.XXXXXXXX less (adversarial review rounds 3 and 6)
    if not long_paths_enabled() and len(str(p)) + _FILE_BUDGET >= _MAX_PATH:
        raise ProjectPathError(f"{what} is too long for Windows (260 characters with long paths switched off); choose a shorter path "
                               "or enable long paths (LongPathsEnabled)")


def registry_path(tenant_id: str) -> Path:
    return tenant_home(validate_tenant_id(tenant_id)) / "projects" / "registry.json"


def _empty() -> dict[str, Any]:
    return {"schema": SCHEMA, "projects": []}


def load(tenant_id: str) -> dict[str, Any]:
    p = registry_path(tenant_id)
    try:
        data = _read_json(p)
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
    atomic_write(p, (json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8"), mode=0o600, owned=True)


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
        raw = _read_json(settings_path(tenant_id)).get("projects_root")
    except (OSError, ValueError, AttributeError):
        raw = None
    if isinstance(raw, str) and raw.strip():
        try:
            raw = normalize_input(raw, "the projects folder")      # a value saved before normalisation (\\?\C:\...) still compares right
        except ProjectPathError:
            return Path.home() / "projects"                        # a stored network root is not used; the default stays safe
        return Path(raw).expanduser()
    return Path.home() / "projects"


def validate_root(tenant_id: str, path: str) -> Path:
    """Judge a new projects root. Absolute, no '..', not '/', not in or above the CorvinOS home, no protected component, not a file.
    Returns the resolved directory (it may not exist yet)."""
    validate_tenant_id(tenant_id)
    if not isinstance(path, str) or not path.strip() or "\x00" in path or len(path) > 1000:
        raise ProjectPathError("the projects folder must be a path")
    given = Path(normalize_input(path, "the projects folder")).expanduser()
    if not given.is_absolute() or ".." in given.parts:
        raise ProjectPathError("the projects folder must be an absolute path without '..'")
    root = _resolve_forgiving(given)
    if os.name == "nt" and str(root).startswith("\\\\") and not str(given).startswith("\\\\"):
        # a mapped network drive (Y:\...) resolves to its UNC form; the setting keeps the drive letter the user chose, which is
        # what every later comparison and the UNC refusal expect (adversarial review 2026-11-10)
        root = Path(os.path.abspath(given))
    if root == Path(root.anchor) or len(root.parts) < 3:
        raise ProjectPathError("choose a folder below your home or data directory, not a top-level one")
    home = _resolve_forgiving(corvin_home())
    if root == home or home in root.parents or root in home.parents:
        raise ProjectPathError("the projects folder must not be inside, or contain, the CorvinOS home")
    if _protected(root.parts):
        raise ProjectPathError("the projects folder contains a protected path component")
    _check_windows_names(given, "the projects folder")
    _check_length(root, "the projects folder")
    if is_link(root):
        raise ProjectPathError("the projects folder must not be a link (symlink or junction)")
    if root.exists() and not root.is_dir():
        raise ProjectPathError("that path is a file, not a folder")
    return root


def set_projects_root(tenant_id: str, path: str) -> Path:
    root = validate_root(tenant_id, path)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # a drive that does not exist, a folder without write access (C:\Windows\...), a full disk: the rule, never a 500
        raise ProjectPathError(f"the projects folder could not be created ({type(exc).__name__}); choose a folder you can write to") from None
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
            data = _read_json(reg)
        except (OSError, ValueError):
            continue
        for e in data.get("projects", []) if isinstance(data, dict) else []:
            try:
                if isinstance(e, dict) and _resolve_forgiving(Path(str(e.get("path")))) == resolved:
                    return True
            except (OSError, ValueError):
                continue
    return False


def _registered_paths() -> list[tuple[str, str, Path]]:
    """(tenant, id, resolved path) of every registered project of every tenant."""
    out: list[tuple[str, str, Path]] = []
    tdir = corvin_home() / "tenants"
    try:
        tenants = [d for d in tdir.iterdir() if d.is_dir() and not is_link(d)]
    except OSError:
        return out
    for d in tenants:
        try:
            data = _read_json(d / "projects" / "registry.json")
        except (OSError, ValueError):
            continue
        for e in (data.get("projects") or []) if isinstance(data, dict) else []:
            if isinstance(e, dict) and isinstance(e.get("path"), str):
                out.append((d.name, str(e.get("id", "")), _resolve_forgiving(Path(e["path"]))))
    return out


def nesting_conflict(resolved: Path, *, ignore: Optional[Path] = None) -> bool:
    """True when ``resolved`` lies inside, or contains, a registered project of ANY tenant (release review R3: a project inside
    another one could read and, with write access, change the outer one's files). ``ignore`` = the project being moved."""
    for _t, _i, p in _registered_paths():
        if ignore is not None and p == ignore:
            continue
        if p == resolved or p in resolved.parents or resolved in p.parents:
            return True
    return False


def validate_location(tenant_id: str, raw: str, *, adopt: bool = False, unmanaged: bool = False,
                      ignore: Optional[Path] = None) -> Path:
    """Judge a client-supplied project location. Returns the resolved directory.

    ``adopt=False`` (create): the directory must not exist or be empty. ``adopt=True``: it must
    exist and hold a ``project.yaml`` naming THIS tenant."""
    validate_tenant_id(tenant_id)
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw or len(raw) > 1000:
        raise ProjectPathError("the location must be a non-empty path")
    given = Path(normalize_input(raw)).expanduser()
    if ".." in given.parts:
        raise ProjectPathError("the location must not contain '..'")
    if not given.is_absolute():
        raise ProjectPathError("the location must be an absolute path")
    _check_windows_names(given, "the location")
    resolved = _resolve_forgiving(given)
    root = _resolve_forgiving(projects_root(tenant_id))
    if resolved == root or root not in resolved.parents:
        raise ProjectPathError("the location must be inside this tenant's projects root")
    home = _resolve_forgiving(corvin_home())
    if resolved == home or home in resolved.parents or resolved in home.parents:
        raise ProjectPathError("the location must not be inside the CorvinOS home")
    if _protected(resolved.relative_to(root).parts):
        raise ProjectPathError("the location contains a protected path component")
    if any(part.startswith(".") for part in resolved.relative_to(root).parts[:1]):
        raise ProjectPathError("a project directory must not be hidden")
    _check_length(resolved, "the location")
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
        if is_link(walk):
            raise ProjectPathError("the location must not be, or lie below, a link (symlink or junction)")
    if is_link(base):
        raise ProjectPathError("the projects root must not be a link (symlink or junction)")
    if _other_tenants_claiming(tenant_id, resolved):
        raise ProjectPathError("this directory already belongs to another tenant")
    if nesting_conflict(resolved, ignore=ignore) and not (adopt and not unmanaged and any(p == resolved and t == tenant_id
                                                                                         for t, _i, p in _registered_paths())):
        raise ProjectPathError("the location lies inside another project, or contains one")
    if adopt and unmanaged:
        # a directory that is not a project yet: it must exist and must NOT carry a foreign manifest
        if not resolved.is_dir():
            raise ProjectPathError("the directory does not exist")
        if (resolved / manifest.FILE).exists() or is_link(resolved / manifest.FILE):
            raise ProjectPathError("the directory already has a project.yaml")
    elif adopt:
        if not resolved.is_dir():
            raise ProjectPathError("the directory does not exist")
        try:
            man = manifest.load(resolved)
        except manifest.ManifestError as exc:
            # the reason (not UTF-8, not YAML, a missing field) is the rule's text, never a path
            raise ProjectPathError(f"the directory has no valid project.yaml ({exc})") from None
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
    if not manifest.PROJECT_ID_RE.match(project_id) or project_id in manifest.RESERVED_IDS:
        raise ProjectPathError("invalid project id")
    resolved = _resolve_forgiving(path)
    with _lock(tenant_id):
        data = load(tenant_id)
        lst = [e for e in data.get("projects", []) if isinstance(e, dict)]
        if any(e.get("id") == project_id for e in lst):
            raise RegistryError("a project with this id already exists")
        if any(_resolve_forgiving(Path(str(e.get("path")))) == resolved for e in lst):
            raise RegistryError("this directory is already registered")
        if nesting_conflict(resolved):                      # again under the lock: two requests cannot both nest
            raise RegistryError("the location lies inside another project, or contains one")
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
        children = sorted(d for d in root.iterdir() if d.is_dir() and not is_link(d))
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
            set_protection(tenant_id, man["id"], manifest.legacy_class(man), manifest.DEFAULT_ACCESS)   # the class may only be RAISED by project.yaml; access never
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


def protection(tenant_id: str, project_id: str) -> Optional[dict[str, str]]:
    """The tenant's own record of a project's data class and assistant access (authoritative; project.yaml is a copy the assistant
    could edit). None for an entry written before this field existed."""
    e = get(tenant_id, project_id)
    if not e or not isinstance(e.get("protection"), dict):
        return None
    p = e["protection"]
    return {k: p[k] for k in ("classification", "assistant_access") if isinstance(p.get(k), str)}


def set_protection(tenant_id: str, project_id: str, classification: str, assistant_access: str) -> None:
    if classification not in manifest.CLASSES or assistant_access not in manifest.ACCESS:
        raise RegistryError("invalid protection")
    with _lock(tenant_id):
        data = load(tenant_id)
        lst = [e for e in data.get("projects", []) if isinstance(e, dict)]
        for e in lst:
            if e.get("id") == project_id:
                e["protection"] = {"classification": classification, "assistant_access": assistant_access}
        data["projects"] = lst
        _save(tenant_id, data)


def set_mcp(*, tenant_id: str, project_id: str, sha: Optional[str]) -> None:
    """Record the sha256 of the MCP file the ENGINE wrote while an MCP feature is on (None = none active). A chat loads a project's MCP
    servers only when the file on disk still has this hash: a file that came with an adopted folder, or went stale, never runs."""
    with _lock(tenant_id):
        data = load(tenant_id)
        lst = [e for e in data.get("projects", []) if isinstance(e, dict)]
        for e in lst:
            if e.get("id") == project_id:
                if sha:
                    e["mcp_sha"] = sha
                else:
                    e.pop("mcp_sha", None)
        data["projects"] = lst
        _save(tenant_id, data)


def mcp_sha(*, tenant_id: str, project_id: str) -> Optional[str]:
    e = get(tenant_id, project_id)
    v = e.get("mcp_sha") if e else None
    return v if isinstance(v, str) and len(v) == 64 else None
