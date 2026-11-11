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
import sys
import time
from pathlib import Path
from typing import Any, NoReturn, Optional

from core.paths import user_dirs
from core.paths.tenant import corvin_home, tenant_home
from core.tenants import validate_tenant_id

from . import manifest
from .fsutil import DirHandle, LockBusy, UnsafePath, atomic_write, file_lock, is_link, make_dirs_nofollow, read_text_any

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


def default_location(tenant_id: str, project_id: str, explicit: Optional[str] = None, *, audit=None,
                     storage: Optional[str] = None) -> tuple[Path, str]:
    """ADR-2321 Decision 3, first match wins: (a) an explicit folder from the client; (b) ``default_storage: root`` ->
    ``<projects_root>/<id>``; (c) ``managed`` (the shipped default) -> ``<managed_root>/<id>``. Returns ``(path, storage)``.
    ``storage`` is the wizard's choice of place (step 2) and replaces the tenant default for this one create; ``root`` without
    a stored projects folder is ``RegistryError("root_required")``."""
    if explicit:
        return Path(explicit), "own"
    if storage is not None and storage not in STORAGE_KINDS:
        raise RegistryError("storage must be managed or root")
    if storage == "root" and not _stored_root(_read_settings_raw(tenant_id)):
        raise RegistryError("root_required")
    if (storage or settings(tenant_id)["default_storage"]) == "root":
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
    """Judge a new projects root: :func:`validate_location` with purpose ``projects_root`` (ADR-2321 Decision 10). Returns the
    resolved directory (it may not exist yet)."""
    return validate_location(tenant_id, path, purpose="projects_root")


#: settings.json: the projects root's resolved form when it was set - grandfathering holds only while it still resolves there
ROOT_RESOLVED_KEY = "projects_root_resolved"


def _same_place(judged: Path) -> bool:
    """*judged* (a resolved form returned by :func:`validate_location`) still resolves to itself: no component became a link."""
    now = _resolve_forgiving(judged)
    if os.name == "nt" and str(now).startswith("\\\\") and not str(judged).startswith("\\\\"):
        now = Path(os.path.abspath(judged))             # a mapped network drive keeps its letter (10a)
    return _fold(str(now)) == _fold(str(judged))


def make_location(resolved: Path) -> DirHandle:
    """Create a judged location (create, move target parent, projects root) without following a link (REVIEW-0063 m4): the
    folders are made component by component with ``fsutil.make_dirs_nofollow`` from the anchor of the RESOLVED form, then the
    path must still resolve to itself and name the directory that was made. Otherwise the folders this call made are removed
    and the verdict is ``symlink``. The caller closes the returned handle (and may :meth:`~DirHandle.rollback` it)."""
    try:
        h = make_dirs_nofollow(resolved)
    except UnsafePath:
        _refuse("symlink")
    if not _same_place(resolved) or not h.still_here():
        h.rollback()
        h.close()
        _refuse("symlink")
    return h


def location_intact(resolved: Path, h: DirHandle) -> bool:
    """Right before a judged location is recorded: it still resolves to itself and is still the directory *h* holds."""
    return _same_place(resolved) and h.still_here()


def set_projects_root(tenant_id: str, path: str) -> Path:
    root = validate_root(tenant_id, path)
    try:
        h = make_location(root)
    except ProjectPathError:
        raise
    except OSError as exc:
        # a drive that does not exist, a folder without write access (C:\Windows\...), a full disk: the rule, never a 500
        raise ProjectPathError(f"the projects folder could not be created ({type(exc).__name__}); choose a folder you can write to") from None
    try:
        try:
            os.chmod(root, 0o700) if not any(root.iterdir()) else None
        except OSError:
            pass
        if not location_intact(root, h):
            h.rollback()
            _refuse("symlink")
        # merges: default_storage, workspace_key, managed_root stay (M16); the resolved form binds the grandfathering (m5)
        write_settings(tenant_id, projects_root=str(root), **{ROOT_RESOLVED_KEY: str(_resolve_forgiving(root))})
    finally:
        h.close()
    return root


def _pin_root_resolution(tenant_id: str, stored: str, resolved: Path) -> None:
    """A projects root stored before its resolved form was recorded: record the current one (trust on first use), so a later
    re-link of an ancestor ends the grandfathering. Best effort and never waiting long: busy -> next time."""
    try:
        with file_lock(registry_path(tenant_id).with_name("registry.lock"), timeout=2.0):
            raw = _read_settings_raw(tenant_id)
            if _stored_root(raw) == stored and not isinstance(raw.get(ROOT_RESOLVED_KEY), str):
                _write_settings_locked(tenant_id, {ROOT_RESOLVED_KEY: str(resolved)})
    except (LockBusy, OSError, RegistryError):
        pass


def grandfather_root(tenant_id: str, *, pin: bool = True) -> Optional[tuple[Path, Path]]:
    """``(given, resolved)`` of the projects root below which checks 4, 6, 7, 8 and 10 judge only the components below the root
    (REVIEW-0062 N1), or None when the binding no longer holds (REVIEW-0063 m5): a STORED root must still resolve to the form
    recorded when it was set (``projects_root_resolved``; a legacy setting without one pins the current form on first use,
    ``pin=False`` for read-only callers); the default ``~/projects`` (nothing stored) must have no link in its given form
    (check 10's tolerated home ancestors and platform links aside). An ancestor re-linked after the root was set therefore
    ends the grandfathering instead of carrying it to the link's target."""
    raw = _read_settings_raw(tenant_id)
    stored = _stored_root(raw)
    pr_g = Path(os.path.abspath(projects_root(tenant_id)))
    pr_r = _resolve_forgiving(pr_g)
    if stored is not None:
        rec = raw.get(ROOT_RESOLVED_KEY)
        if isinstance(rec, str) and rec:
            return (pr_g, pr_r) if _fold(rec) == _fold(str(pr_r)) else None
        if pin:
            _pin_root_resolution(tenant_id, stored, pr_r)
        return pr_g, pr_r
    try:
        _check_links(pr_g, Path(pr_g.anchor))
    except ProjectPathError:
        return None
    return pr_g, pr_r


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


PURPOSES = ("create", "adopt", "projects_root", "move", "relocate", "import")

#: ADR-2321 Decision 15 - the fixed sentence of each reason class
SENTENCES = {
    "path_rule": "The path must be absolute, without '..'.",
    "top_level": "Top-level folders and home folders cannot be chosen — pick a folder inside your home or data directory.",
    "corvin_home": "This is Corvin's own data; projects cannot live here.",
    "system_prefix": "This is a system folder; projects cannot live here.",
    "protected": "This folder is in a protected place; choose another one.",
    "hidden": "Hidden folders and application data cannot be a project.",
    "symlink": "This is a link; choose the real folder.",
    "other_tenant": "This folder already belongs to another workspace.",
    "workspace_root": "Choose a project folder inside the Corvin workspace, not the workspace itself.",
    "nested": "This folder is inside another project, or contains one.",
    "not_empty": "This folder is not empty — choose an empty one or use New folder.",
    "missing": "This folder does not exist.",
    "wrong_manifest": "This folder does not hold this project.",
    "workspace_foreign": WORKSPACE_FOREIGN,
    "hint_word": "Commands that name this folder would be refused by the assistant's safety check; choose another id.",
    "hint_source": ("The assistant's safety-check word list could not be loaded, so no new folder can be made in the Corvin "
                    "workspace; run the project doctor, or choose your own folder."),
}


def _refuse(reason_class: str, message: Optional[str] = None) -> "NoReturn":
    raise ProjectPathError(message or SENTENCES[reason_class], reason_class)


def _fold(s: str) -> str:
    return s.casefold() if os.name == "nt" or sys.platform == "darwin" else s


def _under_eq(p: Path, root: Path) -> bool:
    """*p* equals or lies below *root* (case-folded where the filesystem folds case)."""
    a, b = [_fold(x) for x in p.parts], [_fold(x) for x in root.parts]
    return len(a) >= len(b) and a[: len(b)] == b


def _strictly_under(p: Path, root: Path) -> bool:
    return _under_eq(p, root) and len(p.parts) > len(root.parts)


def _rel_parts(p: Path, root: Path) -> tuple[str, ...]:
    return tuple(p.parts[len(root.parts):]) if _under_eq(p, root) else tuple(p.parts[1:])


def _forms(p: Path) -> list[Path]:
    """The given (absolute, lexical) form and the resolved form of the own home and similar anchors."""
    g = Path(os.path.abspath(p))
    r = _resolve_forgiving(g)
    return [g] if r == g else [g, r]


def _corvin_homes() -> list[Path]:
    """Every Corvin home the target may not be inside, above or equal to (Decision 10 check 5)."""
    homes = [corvin_home(), Path.home() / ".corvin"]
    env = os.environ.get("CORVIN_HOME", "").strip()
    if env:
        homes.append(Path(env).expanduser())
    out: list[Path] = []
    for h in homes:
        for f in _forms(h):
            if f not in out:
                out.append(f)
    return out


def _check_corvin_home(forms: list[Path]) -> None:
    homes = _corvin_homes()
    for f in forms:
        for h in homes:
            if _under_eq(f, h) or _under_eq(h, f):
                _refuse("corvin_home")
    # identity: a bind mount of a Corvin home at another path (st_dev/st_ino, REVIEW-0061 M7)
    existing_homes = [h for h in homes if os.path.isdir(h)]
    if not existing_homes:
        return
    cur = forms[-1]
    chain = [cur, *cur.parents]
    for a in chain:
        if not os.path.exists(a):
            continue
        for h in existing_homes:
            try:
                if os.path.samefile(a, h):
                    _refuse("corvin_home")
            except OSError:
                continue


def _home_forms() -> list[Path]:
    return _forms(Path(str(user_dirs.own_home())))


def _in_own_home(f: Path, homes: list[Path]) -> bool:
    return any(_under_eq(f, h) for h in homes)


def _is_user_home(f: Path) -> bool:
    roots = [Path(str(r)) for r in user_dirs.home_roots()]
    if f.parent in roots or any(_fold(str(f.parent)) == _fold(str(r)) for r in roots):
        return True
    return os.name != "nt" and str(f) == "/root"


def _check_top_level(forms: list[Path], homes: list[Path]) -> None:
    mounts = []
    try:
        mounts = [Path(d["path"]) for d in user_dirs.drives()]
    except Exception:  # noqa: BLE001 - drives() never raises; a monkeypatched one must not turn into a 500
        mounts = []
    for f in forms:
        if f == Path(f.anchor) or len(f.parts) < 3:
            _refuse("top_level")
        if _is_user_home(f):
            _refuse("top_level")
        if any(_strictly_under(h, f) or h == f for h in homes):
            _refuse("top_level")                                       # the own home or one of its ancestors
        if any(_fold(str(f)) == _fold(str(m)) for m in mounts):
            _refuse("top_level")                                       # a mount point itself (a folder below it is fine)


def _check_protected(forms: list[Path], homes: list[Path], parts_of) -> None:
    for f in forms:
        if _protected(parts_of(f)):
            _refuse("protected")
    roots = [Path(str(r)) for r in user_dirs.home_roots()]
    for f in forms:
        if _in_own_home(f, homes):
            continue
        for r in roots:
            if _strictly_under(f, r) and len(f.parts) > len(r.parts) + 1:
                _refuse("protected")                                   # inside another user's home


def _managed_parts(f: Path, parent: Optional[Path], own: Optional[Path]):
    """For a path under the managed parent: (inside own workspace, components to judge for 'hidden'), else None."""
    if parent is None or not _under_eq(f, parent):
        return None
    rel = _rel_parts(f, parent)
    if own is not None and rel and _fold(rel[0]) == _fold(own.name):
        rest = rel[1:]
        if rest and rest[0].startswith("."):
            rest = rest[1:]                                            # <managed_root>/.trash/...: check 9 answers it
        return True, rest
    return False, ()                                                   # another workspace: check 9 answers it


def _check_hidden(forms: list[Path], homes: list[Path], parent: Optional[Path], own: Optional[Path], parts_of) -> None:
    app = [Path(str(d)) for d in user_dirs.app_data_dirs()]
    for f in forms:
        m = _managed_parts(f, parent, own)
        if m is not None:
            if any(x.startswith(".") for x in m[1]):
                _refuse("hidden")
            continue
        if any(x.startswith(".") for x in parts_of(f)):
            _refuse("hidden")
        if any(_under_eq(f, a) for a in app):
            _refuse("hidden")


def _check_workspace(tenant_id: str, forms: list[Path], parent: Optional[Path], own: Optional[Path], resolved: Path) -> None:
    for f in forms:
        if parent is None:
            break
        if _fold(str(f)) == _fold(str(parent)):
            _refuse("other_tenant")
        if _strictly_under(f, parent):
            if own is None or not _under_eq(f, own):
                _refuse("other_tenant")                               # another tenant's or installation's workspace
            if f == own or _fold(str(f)) == _fold(str(own)):
                _refuse("workspace_root")
            rel = _rel_parts(f, own)
            if rel and rel[0].startswith("."):
                _refuse("workspace_root")                             # <managed_root>/.trash/...
    if _other_tenants_claiming(tenant_id, resolved):
        _refuse("other_tenant")


def _check_links(given: Path, start: Path) -> None:
    """Check 10: every component of the GIVEN form from *start* (exclusive) to the target, before anything follows it."""
    home_anc = set()
    for h in _home_forms()[:1]:
        home_anc = {str(a) for a in h.parents}
    tolerated = home_anc | {str(p) for p in user_dirs.platform_links()}
    cur = start
    for part in given.parts[len(start.parts):]:
        cur = cur / part
        if str(cur) in tolerated:
            continue
        if is_link(cur):
            _refuse("symlink")


def _ancestor_manifest(resolved: Path, skip: Optional[Path] = None) -> bool:
    for a in resolved.parents:
        if skip is not None and a == skip:
            continue                                          # the project being moved (into itself: the service answers it)
        m = a / manifest.FILE
        try:
            if m.is_file() and not is_link(m):
                manifest.load(a)
                return True
        except (manifest.ManifestError, OSError):
            continue
    return False


def validate_location(tenant_id: str, raw: str, *, purpose: Optional[str] = None, project_id: Optional[str] = None,
                      adopt: bool = False, unmanaged: bool = False, ignore: Optional[Path] = None) -> Path:
    """THE location rule (ADR-2321 Decision 10): judge a client-supplied path for a purpose and return the resolved directory.

    ``purpose`` in ``create``/``adopt``/``projects_root``/``move``/``relocate``/``import``; ``project_id`` names the project a
    ``move``/``relocate`` is about (its own folder is ignored by the nesting check; ``relocate`` needs its manifest). Every
    component of the given AND the resolved form is judged, from the anchor to the target; below a stored projects root the
    checks 4, 6, 7, 8 and 10 judge only the components below that root (grandfathering). A refusal is a
    :class:`ProjectPathError` with ``reason_class`` and the class's fixed sentence (or the kept sentence of a kept check).

    ``adopt``/``unmanaged``/``ignore`` are the pre-ADR-2321 keywords, kept ONLY so the regression tests of 6250cd0b6/65b587f2e
    run unchanged; no production caller uses them."""
    require_manifest: Optional[bool] = None
    if purpose is None:
        purpose = "adopt" if adopt else "create"
        if adopt:
            require_manifest = not unmanaged
    if purpose not in PURPOSES:
        raise ValueError(f"unknown purpose {purpose!r}")
    validate_tenant_id(tenant_id)
    what = "the projects folder" if purpose == "projects_root" else "the location"
    # 1. path_rule - nothing is stat-ed before this (a UNC path never starts an SMB handshake)
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw or len(raw) > 1000:
        _refuse("path_rule", f"{what} must be a non-empty path")
    norm = normalize_input(raw, what)
    expanded = Path(norm).expanduser()
    if not expanded.is_absolute() or ".." in expanded.parts:
        _refuse("path_rule")
    given = Path(os.path.abspath(expanded))
    # 2. windows_name (reported as path_rule)
    _check_windows_names(given, what)
    resolved = _resolve_forgiving(given)
    if os.name == "nt" and str(resolved).startswith("\\\\") and not str(given).startswith("\\\\"):
        resolved = given                       # a mapped network drive keeps the letter the user chose (10a)
    # 3. too_long (reported as path_rule)
    _check_length(resolved, what)
    forms = [given] if resolved == given else [given, resolved]
    homes = _home_forms()
    parent_p = Path(str(user_dirs.managed_parent()))
    own = known_managed_root(tenant_id)
    # grandfathering below a stored projects root (Decision 10, REVIEW-0062 N1)
    base_g: Optional[Path] = None
    if purpose != "projects_root":
        pr = projects_root(tenant_id)
        pr_g = Path(os.path.abspath(pr))
        pr_r = _resolve_forgiving(pr_g)
        if _strictly_under(given, pr_g) and _strictly_under(resolved, pr_r) and grandfather_root(tenant_id) is not None:
            base_g = pr_g
            if is_link(pr_g):
                _refuse("symlink", "the projects root must not be a link (symlink or junction)")
        if _fold(str(resolved)) == _fold(str(pr_r)) or _fold(str(given)) == _fold(str(pr_g)):
            _refuse("nested", "The projects folder itself cannot be a project; choose a folder inside it.")

    def parts_of(f: Path) -> tuple[str, ...]:
        if base_g is not None:
            return _rel_parts(f, base_g if f is given else _resolve_forgiving(base_g))
        if _in_own_home(f, homes):
            for h in homes:
                if _under_eq(f, h):
                    return _rel_parts(f, h)
        return tuple(f.parts[1:])

    # 4. top_level (the target itself)
    _check_top_level(forms, homes)
    # 5. corvin_home - always in full
    _check_corvin_home(forms)
    # 6. system_prefix
    if base_g is None and any(user_dirs.is_under_system_prefix(f) for f in forms):
        _refuse("system_prefix")
    # 7. protected (PROTECTED_COMPONENTS at any depth; another user's home)
    if base_g is None:
        _check_protected(forms, homes, lambda f: f.parts)
    else:
        for f in forms:
            if _protected(parts_of(f)):
                _refuse("protected")
    # 8. hidden (dot components below the home / anchor; application-data trees)
    if base_g is None:
        _check_hidden(forms, homes, parent_p, own, parts_of)
    elif any(x.startswith(".") for f in forms for x in parts_of(f)):
        _refuse("hidden")
    # 9. other_tenant / workspace_root
    _check_workspace(tenant_id, forms, parent_p, own, resolved)
    # 10. symlink (given form, before anything follows it)
    _check_links(given, base_g if base_g is not None else Path(given.anchor))
    # 11. nested
    if purpose != "projects_root":
        skip: Optional[Path] = ignore
        if project_id is not None:
            e = get(tenant_id, project_id)
            if e is not None and isinstance(e.get("path"), str):
                skip = _resolve_forgiving(Path(e["path"]))
        readopt = purpose == "adopt" and require_manifest is not False and any(
            p == resolved and t == tenant_id for t, _i, p in _registered_paths())
        if nesting_conflict(resolved, ignore=skip) and not readopt:
            _refuse("nested")
        if _ancestor_manifest(resolved, skip):
            _refuse("nested")
    # 12. the per-purpose check
    if purpose in ("create", "move", "import"):
        if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
            _refuse("not_empty")
    elif purpose == "adopt":
        if not resolved.is_dir():
            _refuse("missing")
        mf = resolved / manifest.FILE
        if is_link(mf):
            _refuse("symlink", "project.yaml must not be a link")
        has = mf.exists()
        if require_manifest is False and has:
            _refuse("other_tenant", "the directory already has a project.yaml")
        if require_manifest is True and not has:
            _refuse("wrong_manifest", "the directory has no valid project.yaml")
        if has:
            try:
                man = manifest.load(resolved)
            except manifest.ManifestError as exc:
                _refuse("wrong_manifest", f"the directory has no valid project.yaml ({exc})")
            if man.get("tenant") != tenant_id:
                _refuse("other_tenant")
    elif purpose == "relocate":
        if not resolved.is_dir():
            _refuse("missing")
        try:
            man = manifest.load(resolved)
        except manifest.ManifestError:
            _refuse("wrong_manifest")
        if man.get("tenant") != tenant_id or (project_id is not None and man.get("id") != project_id):
            _refuse("wrong_manifest")
    elif purpose == "projects_root":
        if resolved.exists() and not resolved.is_dir():
            _refuse("path_rule", "that path is a file, not a folder")
    # the path gate's hint words (ADR-2321 Decision 2): a NEW folder in the managed workspace must not match them
    if purpose in ("create", "move", "import") and own is not None and resolved.parent == _resolve_forgiving(own):
        from . import hints  # noqa: PLC0415
        try:
            bad = any(hints.looks_protected(str(f)) for f in forms)
        except hints.HintSourceUnavailable:
            # cannot tell (REVIEW-0064 #1): refused under its OWN class - the id is not at fault - until the doctor's
            # hint_source check passes; an own folder only warns and is never refused for this
            _refuse("hint_source")
        if bad:
            _refuse("hint_word")
    return resolved


HINT_WARNING = "The assistant may refuse some shell commands in a folder whose path contains '{word}'."


def location_warnings(path: "Path | str") -> list[dict[str, str]]:
    """Verdict notes that do not refuse: an own folder whose path the path gate's hint test matches (``hint_word``)."""
    from . import hints  # noqa: PLC0415
    try:
        words = hints.hint_words(str(path))
    except hints.HintSourceUnavailable:
        return []
    return [{"class": "hint_word", "message": HINT_WARNING.format(word=words[0])}] if words else []


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


PENDING_STATES = frozenset({"import"})


def register(tenant_id: str, project_id: str, path: Path, template: Optional[str], *, pending: Optional[str] = None) -> dict[str, Any]:
    """Add an entry. ``pending="import"`` (ADR-2323 Decision 13 step 2, REVIEW-0061 M12) is the only state an import writes
    before its tree is in place: ``rebuild``, the projector, the chat attachment and every scope list skip such an entry, and
    :func:`set_pending` clears it when the import is applied."""
    if pending is not None and pending not in PENDING_STATES:
        raise RegistryError("invalid pending state")
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
        if pending is not None:
            entry["pending"] = pending
        data["projects"] = lst + [entry]
        data.setdefault("schema", SCHEMA)
        _save(tenant_id, data)
    return entry


def set_pending(tenant_id: str, project_id: str, pending: Optional[str]) -> None:
    """Set or (``None``) clear an entry's ``pending`` state. Raises :class:`RegistryError` for an unknown project."""
    if pending is not None and pending not in PENDING_STATES:
        raise RegistryError("invalid pending state")
    with _lock(tenant_id):
        data = load(tenant_id)
        lst = [e for e in data.get("projects", []) if isinstance(e, dict)]
        hit = False
        for e in lst:
            if e.get("id") == project_id:
                hit = True
                if pending is None:
                    e.pop("pending", None)
                else:
                    e["pending"] = pending
        if not hit:
            raise RegistryError("no such project")
        data["projects"] = lst
        _save(tenant_id, data)


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
            validate_location(tenant_id, str(d), purpose="adopt")
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


TRASHED = "trashed"


def is_trashed(e: Optional[dict[str, Any]]) -> bool:
    """An entry whose folder is in the trash (ADR-2321 Decision 12): the projector, the chat attachment, every scope list and
    every per-project route skip it; only ``/projects/-/trash`` shows it."""
    return bool(e) and e.get("storage") == TRASHED


def set_trashed(tenant_id: str, project_id: str, trash_path: Path, entry: str) -> dict[str, Any]:
    """Point an entry at its folder in the trash: ``storage: trashed``, ``trash_entry``. The index only; moves nothing."""
    with _lock(tenant_id):
        data = load(tenant_id)
        lst = [e for e in data.get("projects", []) if isinstance(e, dict)]
        hit = None
        for e in lst:
            if e.get("id") == project_id:
                e["path"] = str(trash_path)
                e["storage"] = TRASHED
                e["trash_entry"] = entry
                hit = e
        if hit is None:
            raise RegistryError("no such project")
        data["projects"] = lst
        _save(tenant_id, data)
    return hit


def set_restored(tenant_id: str, project_id: str, path: Path) -> dict[str, Any]:
    """The reverse of :func:`set_trashed`: the entry points at *path* again, ``storage`` is recomputed (``managed``) and
    ``trash_entry`` is dropped. An entry that is missing (a registry rebuilt while the project was in the trash) is registered."""
    resolved = _resolve_forgiving(path)
    with _lock(tenant_id):
        data = load(tenant_id)
        lst = [e for e in data.get("projects", []) if isinstance(e, dict)]
        hit = None
        for e in lst:
            if e.get("id") == project_id:
                e["path"] = str(resolved)
                e["storage"] = storage_of(tenant_id, resolved)
                e.pop("trash_entry", None)
                hit = e
        if hit is not None:
            data["projects"] = lst
            _save(tenant_id, data)
            return hit
    man = manifest.load(resolved)
    return register(tenant_id, project_id, resolved, man.get("template"))


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


_UID_RE = re.compile(r"^[0-9a-f]{32}$")


def ensure_uid(tenant_id: str, project_id: str) -> str:
    """The project's random 128-bit ``uid`` (ADR-2323 Decision 7, REVIEW-0061 m6) - the bundle's ``source_project_uid``. Written
    into the registry entry the first time it is asked for (first export or duplicate); never derived from a path, a tenant or
    a user name. Raises :class:`RegistryError` for an unknown project."""
    import secrets as _secrets  # noqa: PLC0415
    with _lock(tenant_id):
        data = load(tenant_id)
        lst = [e for e in data.get("projects", []) if isinstance(e, dict)]
        for e in lst:
            if e.get("id") == project_id:
                uid = e.get("uid")
                if isinstance(uid, str) and _UID_RE.match(uid):
                    return uid
                e["uid"] = _secrets.token_hex(16)
                data["projects"] = lst
                _save(tenant_id, data)
                return e["uid"]
    raise RegistryError("no such project")


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
