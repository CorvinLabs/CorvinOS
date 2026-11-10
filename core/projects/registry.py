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

from core.paths import user_dirs
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
    """A client-supplied location is not allowed (-> HTTP 422); the message names the rule, never a path. ``reason_class`` is the
    class of ADR-2321 Decision 14 the refusal belongs to (the HTTP answer and ``project.blocked`` carry it); ``warnings`` are
    verdict notes that do not refuse."""

    def __init__(self, message: str = "", reason_class: str = "path_rule", warnings: Optional[list[str]] = None):
        super().__init__(message)
        self.reason_class = reason_class
        self.warnings = list(warnings or [])


#: ADR-2321 Decision 15 - the one sentence of binding rule (c)/(d)
WORKSPACE_FOREIGN = "This workspace belongs to another Corvin installation."


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
    data["projects"] = _heal_storage(tenant_id, [e for e in data.get("projects", []) if isinstance(e, dict)])
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


SETTINGS_SCHEMA = 2
STORAGE_KINDS = ("managed", "root")


def _read_settings_raw(tenant_id: str) -> dict[str, Any]:
    """settings.json as stored (schema 1, 2 or none); an unreadable file reads as empty (the setting is re-derived, never lost:
    the workspace key comes back from the marker by binding rule (e))."""
    try:
        data = _read_json(settings_path(tenant_id))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _stored_root(raw: dict[str, Any]) -> Optional[str]:
    v = raw.get("projects_root")
    return v if isinstance(v, str) and v.strip() else None


def _default_storage_of(raw: dict[str, Any]) -> str:
    """Schema 2 reads its stored value; schema 1 (or none): ``root`` when a projects root is set, else ``managed``
    (ADR-2321 Decision 8)."""
    v = raw.get("default_storage")
    if v in STORAGE_KINDS:
        return v
    return "root" if _stored_root(raw) else "managed"


def _write_settings_locked(tenant_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Read-merge-write; the caller holds ``_lock(tenant_id)``. Unknown keys are kept; ``default_storage`` is fixed at the first
    upgrade by the read rule, so a schema-1 file with a root stays ``root`` and a fresh tenant becomes ``managed``."""
    cur = _read_settings_raw(tenant_id)
    merged = {**cur, **changes}
    if "default_storage" not in changes and cur.get("default_storage") not in STORAGE_KINDS:
        merged["default_storage"] = _default_storage_of(merged)
    if merged.get("default_storage") == "root" and not _stored_root(merged):
        raise RegistryError("root_required")
    merged["schema"] = SETTINGS_SCHEMA
    p = settings_path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    atomic_write(p, (json.dumps(merged, indent=1, sort_keys=True) + "\n").encode("utf-8"), mode=0o600, owned=True)
    return merged


def write_settings(tenant_id: str, **changes: Any) -> dict[str, Any]:
    """The ONE writer of ``settings.json`` (ADR-2321 Decision 8, REVIEW-0061 M16): inside the registry lock, read the current file,
    merge ``changes``, keep unknown keys, set ``schema: 2``, write it atomically 0o600."""
    validate_tenant_id(tenant_id)
    with _lock(tenant_id):
        return _write_settings_locked(tenant_id, changes)


def settings(tenant_id: str) -> dict[str, Any]:
    """The tenant's location settings as the console shows them: ``default_storage``, ``projects_root`` (None when unset),
    ``workspace_key`` and ``managed_root`` (None until the workspace exists), ``schema`` as stored (1 for a legacy file)."""
    validate_tenant_id(tenant_id)
    raw = _read_settings_raw(tenant_id)
    key = raw.get("workspace_key") if isinstance(raw.get("workspace_key"), str) and user_dirs.WORKSPACE_KEY_RE.match(
        raw.get("workspace_key") or "") else None
    return {**raw, "schema": raw.get("schema", 1) if raw else None, "default_storage": _default_storage_of(raw),
            "projects_root": _stored_root(raw), "workspace_key": key,
            "managed_root": str(user_dirs.managed_projects_root(key)) if key else None}


def set_default_storage(tenant_id: str, kind: str, projects_root: Optional[str] = None) -> dict[str, Any]:
    """Switch where NEW projects go. ``root`` needs a usable projects root (given or stored); nothing is moved."""
    if kind not in STORAGE_KINDS:
        raise RegistryError("default_storage must be managed or root")
    if projects_root is not None:
        set_projects_root(tenant_id, projects_root)
    with _lock(tenant_id):
        cur = _read_settings_raw(tenant_id)
        if kind == "root" and not _stored_root(cur):
            raise RegistryError("root_required")
        return _write_settings_locked(tenant_id, {"default_storage": kind})


# ── the managed workspace (ADR-2321 Decision 7) ──────────────────────────────────────────────

class WorkspaceForeign(ProjectPathError):
    """Binding rule (c)/(d): the workspace marker names another installation (a home that still exists) or another tenant/key."""

    def __init__(self) -> None:
        super().__init__(WORKSPACE_FOREIGN, "workspace_foreign")


MARKER = "workspace.json"


def _home_str() -> str:
    return str(_resolve_forgiving(corvin_home()))


def _read_marker(d: Path) -> Optional[dict[str, Any]]:
    m = d / MARKER
    if is_link(m):
        return None
    try:
        data = _read_json(m)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_marker(d: Path, tenant_id: str, key: str) -> None:
    atomic_write(d / MARKER, (json.dumps({"schema": 1, "tenant": tenant_id, "workspace_key": key, "corvin_home": _home_str()},
                                         indent=1, sort_keys=True) + "\n").encode("utf-8"), mode=0o600, owned=True)


def _private_dir(d: Path) -> None:
    """Create *d* 0o700 (POSIX) and re-apply the mode when it is wider; a link is refused."""
    if is_link(d):
        raise ProjectPathError("This is a link; choose the real folder.", "symlink")
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    if is_link(d):
        raise ProjectPathError("This is a link; choose the real folder.", "symlink")
    if os.name != "nt":
        try:
            if os.stat(d).st_mode & 0o077:
                os.chmod(d, 0o700)
        except OSError:
            pass


def _recover_key(tenant_id: str, parent: Path) -> Optional[str]:
    """Binding rule (e): a direct child of the managed parent whose marker names this tenant AND this resolved home."""
    home = _home_str()
    try:
        children = sorted((e for e in os.scandir(parent) if e.is_dir(follow_symlinks=False)), key=lambda e: e.name)
    except OSError:
        return None
    for e in children:
        if not user_dirs.WORKSPACE_KEY_RE.match(e.name):
            continue
        m = _read_marker(Path(e.path))
        if m and m.get("tenant") == tenant_id and m.get("workspace_key") == e.name and m.get("corvin_home") == home:
            return e.name
    return None


def ensure_managed_root(tenant_id: str, *, audit=None) -> tuple[Path, Optional[str]]:
    """The tenant's managed workspace, created when absent, bound by the rules of ADR-2321 Decision 7. Returns
    ``(managed_root, outcome)``; ``outcome`` is ``rebound``/``recovered`` when a binding rule changed something, else None.
    ``audit(outcome)`` is called BEFORE that change is written (audit-first); if it raises, nothing is written.
    Raises :class:`WorkspaceForeign` for rules (c)/(d)."""
    validate_tenant_id(tenant_id)
    parent = Path(str(user_dirs.managed_parent()))
    with _lock(tenant_id):
        _private_dir(parent)
        raw = _read_settings_raw(tenant_id)
        key = raw.get("workspace_key") if isinstance(raw.get("workspace_key"), str) else None
        outcome: Optional[str] = None
        if key is not None and not user_dirs.WORKSPACE_KEY_RE.match(key):
            key = None
        if key is None:
            key = _recover_key(tenant_id, parent)
            if key is not None:
                outcome = "recovered"
            else:
                key = user_dirs.new_workspace_key()
        root = Path(str(user_dirs.managed_projects_root(key)))
        marker = _read_marker(root) if root.is_dir() and not is_link(root) else None
        if marker is not None:
            if marker.get("tenant") != tenant_id or marker.get("workspace_key") != key:
                raise WorkspaceForeign()                                         # (d)
            recorded = marker.get("corvin_home")
            if recorded != _home_str():
                if isinstance(recorded, str) and recorded and os.path.exists(recorded):
                    raise WorkspaceForeign()                                     # (c) a copy of another installation
                outcome = "rebound"                                              # (b) the home was moved
        if outcome and audit is not None:
            audit(outcome)
        _private_dir(root)
        if marker is None or outcome == "rebound":
            _write_marker(root, tenant_id, key)
        if raw.get("workspace_key") != key or raw.get("managed_root") != str(root) or raw.get("schema") != SETTINGS_SCHEMA:
            _write_settings_locked(tenant_id, {"workspace_key": key, "managed_root": str(root)})
    return root, outcome


def rekey_workspace(tenant_id: str, *, audit=None) -> Path:
    """Start a new workspace for this installation (binding rule (c), the Settings action). Audit-first via ``audit("rekeyed")``.
    The old workspace and every entry pointing into it stay as they are (they read ``own``)."""
    validate_tenant_id(tenant_id)
    parent = Path(str(user_dirs.managed_parent()))
    with _lock(tenant_id):
        _private_dir(parent)
        key = user_dirs.new_workspace_key()
        root = Path(str(user_dirs.managed_projects_root(key)))
        if audit is not None:
            audit("rekeyed")
        _private_dir(root)
        _write_marker(root, tenant_id, key)
        _write_settings_locked(tenant_id, {"workspace_key": key, "managed_root": str(root)})
    return root


def workspace_state(tenant_id: str) -> str:
    """``ok`` | ``foreign`` | ``none`` (no key yet) - read-only, for the list and the doctor."""
    st = settings(tenant_id)
    if not st["workspace_key"]:
        return "none"
    root = Path(st["managed_root"])
    m = _read_marker(root) if root.is_dir() else None
    if m is None:
        return "ok"
    if m.get("tenant") != tenant_id or m.get("workspace_key") != st["workspace_key"]:
        return "foreign"
    rec = m.get("corvin_home")
    if rec != _home_str() and isinstance(rec, str) and rec and os.path.exists(rec):
        return "foreign"
    return "ok"


def known_managed_root(tenant_id: str) -> Optional[Path]:
    """The managed root when the tenant has a key (never creates anything)."""
    mr = settings(tenant_id)["managed_root"]
    return Path(mr) if mr else None


def storage_of(tenant_id: str, path: "Path | str", *, managed: Optional[Path] = None) -> str:
    """``managed`` when the resolved path lies directly in the tenant's managed root, else ``own`` (ADR-2321 Decision 9)."""
    mr = managed if managed is not None else known_managed_root(tenant_id)
    if mr is None:
        return "own"
    r = _resolve_forgiving(Path(str(path)))
    return "managed" if r.parent == _resolve_forgiving(mr) else "own"


def default_location(tenant_id: str, project_id: str, explicit: Optional[str] = None, *, audit=None) -> tuple[Path, str]:
    """ADR-2321 Decision 3, first match wins: (a) an explicit folder from the client; (b) ``default_storage: root`` ->
    ``<projects_root>/<id>``; (c) ``managed`` (the shipped default) -> ``<managed_root>/<id>``. Returns ``(path, storage)``."""
    if explicit:
        return Path(explicit), "own"
    if settings(tenant_id)["default_storage"] == "root":
        return projects_root(tenant_id) / project_id, "own"
    root, _o = ensure_managed_root(tenant_id, audit=audit)
    return root / project_id, "managed"


def projects_root(tenant_id: str) -> Path:
    """The tenant's own projects folder: the operator's setting, else ``~/projects`` (ADR-2315 plan E7). Where NEW projects go is
    :func:`default_location`; this folder is one of the two places :func:`rebuild` looks in."""
    raw = _stored_root(_read_settings_raw(tenant_id))
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
    write_settings(tenant_id, projects_root=str(root))        # merges: default_storage, workspace_key, managed_root stay (M16)
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
    base_given = projects_root(tenant_id)
    mr = known_managed_root(tenant_id)
    if mr is not None and resolved.parent == _resolve_forgiving(mr):
        root, base_given = _resolve_forgiving(mr), mr                    # <managed_root>/<id> (ADR-2321 Decision 4 (a))
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
    base = Path(os.path.abspath(base_given))
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


def _heal_storage(tenant_id: str, lst: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """An entry without ``storage`` gets ``managed`` when its resolved path lies in the managed root, else ``own`` (Decision 9)."""
    if any("storage" not in e for e in lst):
        mr = known_managed_root(tenant_id)
        for e in lst:
            if "storage" not in e and isinstance(e.get("path"), str):
                e["storage"] = storage_of(tenant_id, e["path"], managed=mr) if mr is not None else "own"
    return lst


def entries(tenant_id: str) -> list[dict[str, Any]]:
    return _heal_storage(tenant_id, [e for e in load(tenant_id).get("projects", []) if isinstance(e, dict)])


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
                 "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "storage": storage_of(tenant_id, resolved)}
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
    """Restore the index from the directories (ADR-2315 Decision 1, ADR-2321 Decision 9): every direct child of the managed root
    AND of the projects root whose ``project.yaml`` names THIS tenant is registered if it is not yet. An ``own`` project elsewhere
    is re-found only by adopt. Never removes an entry; never touches a directory. Returns ``{added, kept, skipped}``."""
    added, skipped = [], []
    children: list[Path] = []
    roots = [r for r in (known_managed_root(tenant_id), projects_root(tenant_id)) if r is not None]
    for root in roots:
        try:
            children += sorted(d for d in root.iterdir() if d.is_dir() and not is_link(d) and not d.name.startswith("."))
        except OSError:
            continue
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
                e["storage"] = storage_of(tenant_id, resolved)       # moved into/out of the managed root (ADR-2321 Decision 11)
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
