"""The server-side folder browser's pure core (ADR-2322 Decisions 2, 3, 5-10; T-0390).

No FastAPI import, no audit call: the route (``corvin_console.routes.projects``, T-0391) adapts it and owns the budget, the
throttle and the audit. Stdlib only, Python 3.11-safe (links through ``fsutil.dirent_is_junction``/``fsutil.is_link``, never the
3.12-only junction or drive-listing APIs).

What a listing returns: directories only - never a file name, size, count, mtime or content. What it never enters (answers
``entries: []`` with the reason as its verdict): a path at or below a Corvin home, a system prefix, the managed parent or another
workspace below it, another tenant's registered project, and any path with a linked component. The verdict for a path that CAN be
entered is :func:`verdict` - ``registry.validate_location`` caught into ``{ok, reason_class, message}``, never a second rule.

The picker store ``<tenant_home>/projects/picker.json`` (0o600) holds the last 8 confirmed locations (``recent``, written by the
mutations that accept a location, never by the browser) and at most 10 ``favourites``.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Optional

from core.paths import user_dirs
from core.paths.tenant import tenant_home
from core.tenants import validate_tenant_id

from . import manifest, registry
from .fsutil import atomic_write, dirent_is_junction, file_lock, is_link

PAGE_SIZE = 200
#: directory entries examined per listing at most (a folder with more is answered ``truncated``)
MAX_SCAN = 20000
MAX_RECENT = 8
MAX_FAVOURITES = 10
MAX_INPUT = 1000

#: the classes for which a listing is not entered (``entries: []``)
BLOCKING = ("symlink", "corvin_home", "system_prefix", "other_tenant")

SLOW = "This folder did not answer in time (network share?). Try again or choose another place."
OK_NEW = "Can be used — a new folder will be created when you continue."
OK = "Can be used"
FAVOURITES_MAX_SENTENCE = "At most 10 favourites."
MANAGED_ROW = "Corvin workspaces"

_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_DIRECTORY = 0x10


class BrowseInputError(ValueError):
    """The request names no usable path (-> HTTP 422 ``path_rule``); the message is the rule's sentence, never the path."""

    def __init__(self, message: str, reason_class: str = "path_rule"):
        super().__init__(message)
        self.reason_class = reason_class


# ── the input rule (checks 1-3 of ADR-2321 Decision 10) ──────────────────────────────────────────

def check_input(raw: Any, purpose: Any = "create", project_id: Optional[str] = None) -> str:
    """Pure (no filesystem call): type, NUL, length, ``normalize_input`` (UNC/device refused), absolute, no ``..``; the purpose
    vocabulary; ``project_id`` for move/relocate. Returns the normalised absolute path string. Nothing is expanded (no ``~``)."""
    if purpose not in registry.PURPOSES:
        raise BrowseInputError(registry.SENTENCES["path_rule"])
    if purpose in ("move", "relocate") and not project_id:
        raise BrowseInputError(registry.SENTENCES["path_rule"])
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw or len(raw) > MAX_INPUT:
        raise BrowseInputError(registry.SENTENCES["path_rule"])
    try:
        norm = registry.normalize_input(raw)
    except registry.ProjectPathError as exc:
        raise BrowseInputError(str(exc)) from None
    p = Path(norm)
    if not p.is_absolute() or ".." in p.parts:
        raise BrowseInputError(registry.SENTENCES["path_rule"])
    return os.path.abspath(norm)


def _check_names(given: Path) -> None:
    """Checks 2 and 3 (Windows names, length) - the only part of the input rule that may touch the filesystem (``lexists`` of a
    device-named component on Windows), so it runs after :func:`check_input`."""
    try:
        registry._check_windows_names(given, "the location")
        registry._check_length(given, "the location")
    except registry.ProjectPathError as exc:
        raise BrowseInputError(str(exc)) from None


# ── shared state of one call ─────────────────────────────────────────────────────────────────────

class _Ctx:
    """What every check of one listing needs, read once."""

    def __init__(self, tenant_id: str):
        self.tenant = tenant_id
        self.homes = registry._corvin_homes()
        self.home_ids = set()
        for h in self.homes:
            try:
                st = os.stat(h)
                self.home_ids.add((st.st_dev, st.st_ino))
            except (OSError, ValueError):
                continue
        self.parent = Path(str(user_dirs.managed_parent()))
        self.own = registry.known_managed_root(tenant_id)
        self.registered = registry._registered_paths()
        self.own_home = [Path(str(h)) for h in registry._home_forms()]
        self.app = [Path(str(d)) for d in user_dirs.app_data_dirs()]
        self.home_roots = [Path(str(r)) for r in user_dirs.home_roots()]
        try:
            self.mounts = [Path(d["path"]) for d in user_dirs.drives()]
        except Exception:  # noqa: BLE001 - drives() never raises; a monkeypatched one must not fail a listing
            self.mounts = []
        self.base = None                                          # the grandfathered stored projects root (given form)
        raw = registry._stored_root(registry._read_settings_raw(tenant_id))
        if raw:
            try:
                self.base = Path(os.path.abspath(registry.projects_root(tenant_id)))
            except (OSError, ValueError):
                self.base = None


def _under(p: Path, root: Path) -> bool:
    return registry._under_eq(p, root)


def _corvin_home_block(forms: list[Path], ctx: Optional[_Ctx] = None) -> bool:
    """At or below a Corvin home - lexically, or by identity of the target or an ancestor (a second path to the home). An
    ANCESTOR of a home is listed (the home appears as a row in it)."""
    homes = ctx.homes if ctx is not None else registry._corvin_homes()
    for f in forms:
        if any(_under(f, h) for h in homes):
            return True
    ids = ctx.home_ids if ctx is not None else set()
    if ids:
        f = forms[-1]
        for a in (f, *f.parents):
            try:
                st = os.stat(a)
            except (OSError, ValueError):
                continue
            if (st.st_dev, st.st_ino) in ids:
                return True
    return False


def _below_base(ctx: _Ctx, given: Path, resolved: Path) -> bool:
    if ctx.base is None:
        return False
    return registry._strictly_under(given, ctx.base) and registry._strictly_under(resolved, registry._resolve_forgiving(ctx.base))


def _other_tenant_block(ctx: _Ctx, forms: list[Path]) -> bool:
    """The managed parent itself, a workspace below it other than the own one, or at/below another tenant's registered project."""
    for f in forms:
        if registry._fold(str(f)) == registry._fold(str(ctx.parent)):
            return True
        if registry._strictly_under(f, ctx.parent) and (ctx.own is None or not _under(f, ctx.own)):
            return True
    resolved = forms[-1]
    for t, _i, p in ctx.registered:
        if t != ctx.tenant and (p == resolved or p in resolved.parents):
            return True
    return False


def _link_block(ctx: _Ctx, given: Path, resolved: Path) -> bool:
    start = ctx.base if _below_base(ctx, given, resolved) else Path(given.anchor)
    try:
        registry._check_links(given, start)
    except registry.ProjectPathError:
        return True
    return False


def _block(ctx: _Ctx, given: Path, resolved: Path) -> Optional[str]:
    """The reason a listing is not entered, in the order of ADR-2322 Decisions 3 and 7."""
    forms = [given] if resolved == given else [given, resolved]
    if _link_block(ctx, given, resolved):
        return "symlink"
    if _corvin_home_block(forms, ctx):
        return "corvin_home"
    if not _below_base(ctx, given, resolved) and any(user_dirs.is_under_system_prefix(f) for f in forms):
        return "system_prefix"
    if _other_tenant_block(ctx, forms):
        return "other_tenant"
    return None


# ── the verdict ──────────────────────────────────────────────────────────────────────────────────

def verdict(tenant_id: str, path: str, purpose: str, project_id: Optional[str] = None) -> dict[str, Any]:
    """``registry.validate_location`` for *path* and *purpose*, caught into ``{ok, reason_class, message[, warnings, notes]}``."""
    try:
        resolved = registry.validate_location(tenant_id, path, purpose=purpose, project_id=project_id)
    except registry.ProjectPathError as exc:
        return {"ok": False, "reason_class": exc.reason_class, "message": str(exc)}
    except (OSError, registry.RegistryError) as exc:
        return {"ok": False, "reason_class": "filesystem",
                "message": f"This folder could not be checked ({type(exc).__name__})."}
    out: dict[str, Any] = {"ok": True, "message": OK_NEW if purpose in ("create", "move", "import") else OK}
    own = registry.known_managed_root(tenant_id)
    if own is None or resolved.parent != registry._resolve_forgiving(own):
        notes = registry.location_warnings(resolved)
        if notes:
            out["warnings"] = [w["class"] for w in notes]
            out["notes"] = notes                             # the sentence per class (the hint word named), for the picker
    return out


def _blocked_verdict(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason_class": reason, "message": registry.SENTENCES[reason]}


# ── entries ──────────────────────────────────────────────────────────────────────────────────────

def _entry_is_dir(e: "os.DirEntry[str]", link: bool) -> bool:
    """A directory, or a link to one. A plain entry is judged without following anything; for a link only its target's TYPE is
    asked (Windows: the link's own directory attribute, nothing followed; POSIX: one ``stat``) - never listed or entered."""
    try:
        if not link:
            return e.is_dir(follow_symlinks=False)
        if os.name == "nt":
            attrs = getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0)
            return bool(attrs & _FILE_ATTRIBUTE_DIRECTORY)
        return stat.S_ISDIR(os.stat(e.path).st_mode)
    except (OSError, ValueError):
        return False


def _is_hidden(e: "os.DirEntry[str]") -> bool:
    if e.name.startswith("."):
        return True
    if os.name == "nt":
        try:
            return bool(getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & _FILE_ATTRIBUTE_HIDDEN)
        except OSError:
            return False
    return False


def _entry_refusal(ctx: _Ctx, p: Path, e: "os.DirEntry[str]", link: bool, hidden: bool, here_in_project: bool) -> Optional[str]:
    """The advisory refusal of one row (ADR-2322 Decision 5); the verdict of the folder itself stays :func:`verdict`."""
    if link:
        return "symlink"
    if any(_under(p, h) for h in ctx.homes):
        return "corvin_home"
    if ctx.home_ids:
        try:
            st = e.stat(follow_symlinks=False)
            if (st.st_dev, st.st_ino) in ctx.home_ids:
                return "corvin_home"
        except OSError:
            pass
    if not (ctx.base is not None and registry._strictly_under(p, ctx.base)) and user_dirs.is_under_system_prefix(p):
        return "system_prefix"
    if registry._strictly_under(p, ctx.parent) and (ctx.own is None or not _under(p, ctx.own)):
        return "other_tenant"
    for t, _i, rp in ctx.registered:
        if t != ctx.tenant and rp == p:
            return "other_tenant"
    if e.name.casefold() in registry.PROTECTED_COMPONENTS:
        return "protected"
    in_own_home = any(_under(p, h) for h in ctx.own_home)
    if not in_own_home and any(registry._strictly_under(p, r) and len(p.parts) > len(r.parts) + 1 for r in ctx.home_roots):
        return "protected"
    if hidden or any(_under(p, a) for a in ctx.app) and not (ctx.own is not None and (_under(p, ctx.own) or _under(ctx.own, p))):
        return "hidden"
    if here_in_project or any(t == ctx.tenant and (rp == p or p in rp.parents) for t, _i, rp in ctx.registered):
        return "nested"
    if p == Path(p.anchor) or len(p.parts) < 3 or registry._is_user_home(p) or any(
            registry._strictly_under(h, p) or h == p for h in ctx.own_home) or any(
            registry._fold(str(p)) == registry._fold(str(m)) for m in ctx.mounts):
        return "top_level"
    return None


def _project_of(ctx: _Ctx, p: Path) -> dict[str, Any]:
    for t, pid, rp in ctx.registered:
        if rp != p:
            continue
        if t != ctx.tenant:
            return {"state": "other"}                        # existence only: never an id or a title
        title = pid
        try:
            title = manifest.load(p).get("title") or pid
        except (manifest.ManifestError, OSError):
            pass
        return {"state": "mine", "id": pid, "title": title}
    return {"state": "none"}


def _sort_key(name: str) -> tuple[str, str]:
    return (name.casefold(), name)


def _entries(ctx: _Ctx, here: Path, *, hidden: bool, page: Optional[str]) -> tuple[list[dict[str, Any]], bool, Optional[str], bool]:
    """(entries, truncated, next_page, readable) of *here*. ``here`` passed every block, so it holds no linked component."""
    under_managed = ctx.own is not None and _under(here, ctx.own)
    resolved_here = registry._resolve_forgiving(here)
    here_in_project = any(t == ctx.tenant and (rp == resolved_here or rp in resolved_here.parents) for t, _i, rp in ctx.registered)
    rows: list[dict[str, Any]] = []
    scanned = 0
    over = False
    try:
        it = os.scandir(here)
    except (FileNotFoundError, NotADirectoryError):
        return [], False, None, True
    except (PermissionError, OSError, ValueError):
        return [], False, None, False
    with it:
        for e in it:
            scanned += 1
            if scanned > MAX_SCAN:
                over = True
                break
            try:
                link = e.is_symlink() or dirent_is_junction(e)
            except OSError:
                continue
            if not _entry_is_dir(e, link):
                continue                                     # files (and links to files) are never named
            p = Path(e.path)
            is_hidden = _is_hidden(e)
            if registry._fold(str(p)) == registry._fold(str(ctx.parent)) and not link:
                row = {"name": MANAGED_ROW, "path": str(ctx.own) if ctx.own is not None else str(p), "kind": "dir",
                       "hidden": False, "symlink": False,
                       "readable": ctx.own is not None and os.access(ctx.own, os.R_OK | os.X_OK),
                       "project": {"state": "none"}}
                if ctx.own is None:
                    row["refusal"] = "other_tenant"
                rows.append(row)
                continue
            if is_hidden and (not hidden or under_managed):
                continue
            row = {"name": e.name, "path": str(p), "kind": "dir", "hidden": is_hidden, "symlink": link,
                   "readable": (not link) and os.access(p, os.R_OK | os.X_OK),
                   "project": {"state": "none"} if link else _project_of(ctx, p)}
            ref = _entry_refusal(ctx, p, e, link, is_hidden, here_in_project)
            if ref:
                row["refusal"] = ref
            rows.append(row)
    rows.sort(key=lambda r: _sort_key(r["name"]))
    if page:
        k = _sort_key(page)
        rows = [r for r in rows if _sort_key(r["name"]) > k]
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    nxt = rows[-1]["name"] if more and rows else None
    return rows, more or over, nxt, True


# ── the listing ──────────────────────────────────────────────────────────────────────────────────

_UNSET: Any = object()


def frame(tenant_id: str, path: str, managed_root: Any = _UNSET) -> dict[str, Any]:
    """The pure part of a listing: ``path``, ``display``, ``parent``, ``breadcrumbs``. With ``managed_root`` given (``None``
    included) nothing is read at all - the route answers a ``slow`` listing on the event loop with it."""
    given = Path(path)
    crumbs = []
    cur = Path(given.anchor)
    crumbs.append({"label": given.anchor or str(given), "path": str(cur)})
    for part in given.parts[1:]:
        cur = cur / part
        crumbs.append({"label": part, "path": str(cur)})
    own = registry.settings(tenant_id)["managed_root"] if managed_root is _UNSET else managed_root
    try:
        display = user_dirs.display_path(given, managed_root=own, home=user_dirs.own_home())
    except (OSError, ValueError):
        display = str(given)
    return {"path": str(given), "display": display, "parent": str(given.parent) if given.parent != given else None,
            "breadcrumbs": crumbs}


def listing(tenant_id: str, path: str, purpose: str, project_id: Optional[str] = None, hidden: bool = False,
            page: Optional[str] = None) -> dict[str, Any]:
    """One page of *path*'s directories with the verdict for *path* and *purpose* (ADR-2322 Decision 5 shape, plus ``path`` per
    entry - where the row leads; the managed parent's row leads to the own workspace - and ``readable`` of the folder itself)."""
    validate_tenant_id(tenant_id)
    norm = check_input(path, purpose, project_id)            # nothing is stat-ed before this
    if page is not None and (not isinstance(page, str) or len(page) > 255 or "\x00" in page):
        raise BrowseInputError(registry.SENTENCES["path_rule"])
    given = Path(norm)
    _check_names(given)
    resolved = registry._resolve_forgiving(given)
    if os.name == "nt" and str(resolved).startswith("\\\\") and not str(given).startswith("\\\\"):
        resolved = given                                     # a mapped network drive keeps its letter (ADR-2321 10a)
    ctx = _Ctx(tenant_id)
    out = frame(tenant_id, str(given))
    reason = _block(ctx, given, resolved)
    if reason is not None:
        return {**out, "readable": False, "entries": [], "verdict": _blocked_verdict(reason), "truncated": False}
    rows, truncated, nxt, readable = _entries(ctx, given, hidden=bool(hidden), page=page)
    res = {**out, "readable": readable, "entries": rows, "verdict": verdict(tenant_id, str(given), purpose, project_id),
           "truncated": truncated}
    if nxt is not None:
        res["next_page"] = nxt
    return res


# ── places ───────────────────────────────────────────────────────────────────────────────────────

def _probe(kind: str, label: str, path: str, note: str = "") -> dict[str, Any]:
    return user_dirs._place(kind, label, user_dirs._P(path), note)


def places(tenant_id: str) -> list[dict[str, Any]]:
    """Managed workspace, projects folder (when set), the user's places, drives, recent and favourites - in that order."""
    validate_tenant_id(tenant_id)
    st = registry.settings(tenant_id)
    out: list[dict[str, Any]] = []
    if st["managed_root"]:
        out.append(_probe("managed", "Corvin workspace", st["managed_root"]))
    if st["projects_root"]:
        try:
            out.append(_probe("root", "Projects folder", str(registry.projects_root(tenant_id))))
        except (OSError, ValueError):
            pass
    out.extend(user_dirs.user_places())
    out.extend(user_dirs.drives())
    pk = load_picker(tenant_id)
    for p in pk["recent"]:
        out.append(_probe("recent", Path(p).name or p, p))
    for p in pk["favourites"]:
        out.append(_probe("favourite", Path(p).name or p, p))
    return out


# ── the picker store ─────────────────────────────────────────────────────────────────────────────

def picker_path(tenant_id: str) -> Path:
    return tenant_home(validate_tenant_id(tenant_id)) / "projects" / "picker.json"


def _clean(lst: Any, limit: int) -> list[str]:
    out: list[str] = []
    for v in lst if isinstance(lst, list) else []:
        if isinstance(v, str) and v and len(v) <= MAX_INPUT and "\x00" not in v and v not in out:
            out.append(v)
    return out[:limit]


def load_picker(tenant_id: str) -> dict[str, list[str]]:
    try:
        data = json.loads(picker_path(tenant_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"recent": _clean(data.get("recent"), MAX_RECENT), "favourites": _clean(data.get("favourites"), MAX_FAVOURITES)}


def _save(tenant_id: str, data: dict[str, list[str]]) -> None:
    p = picker_path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    atomic_write(p, (json.dumps({"schema": 1, **data}, indent=1) + "\n").encode("utf-8"), mode=0o600, owned=True)


def _update(tenant_id: str, fn) -> dict[str, list[str]]:
    with file_lock(picker_path(tenant_id).with_name("picker.lock")):
        data = load_picker(tenant_id)
        fn(data)
        _save(tenant_id, data)
        return data


def _same(a: str, b: str) -> bool:
    return os.path.normcase(a) == os.path.normcase(b)


def record_recent(tenant_id: str, path: str) -> None:
    """A confirmed location (called by the mutations on success, never by the browser): first, deduplicated, at most 8."""
    if not isinstance(path, str) or not path:
        return

    def _f(d):
        d["recent"] = [path, *[p for p in d["recent"] if not _same(p, path)]][:MAX_RECENT]
    _update(tenant_id, _f)


def set_favourites(tenant_id: str, paths: list[str]) -> dict[str, list[str]]:
    """Replace the favourites. At most 10; each judged by the input rule (a favourite is a place, not a verdict)."""
    if not isinstance(paths, list) or len(paths) > MAX_FAVOURITES:
        raise BrowseInputError(FAVOURITES_MAX_SENTENCE)
    clean: list[str] = []
    for p in paths:
        n = check_input(p)
        if not any(_same(n, c) for c in clean):
            clean.append(n)

    def _f(d):
        d["favourites"] = clean
    return _update(tenant_id, _f)


def clear_recent(tenant_id: str) -> dict[str, list[str]]:
    def _f(d):
        d["recent"] = []
    return _update(tenant_id, _f)


__all__ = ["BrowseInputError", "check_input", "frame", "listing", "verdict", "places", "picker_path", "load_picker",
           "record_recent", "set_favourites", "clear_recent", "SLOW", "PAGE_SIZE", "BLOCKING"]

if sys.version_info < (3, 11):  # pragma: no cover
    raise ImportError("core.projects.browse needs Python 3.11 or newer")
