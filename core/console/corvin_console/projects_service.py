"""Projects: the console's service layer over ``core.projects`` (ADR-2315).

Everything that needs the host lives here (the audit chain, the tenant plugin registry, the bound
knowledge base's engine, subprocesses); ``core.projects`` stays pure. The HTTP surface is
``routes/projects.py``; the tenant always comes from the authenticated session.

Flow: ``plan_project`` (no write, audit ``project.plan_created``) -> ``create_project(plan_id)``
(re-validates everything, registers, applies, audit ``project.created`` requested/applied) ... and
``adopt``, ``set_features``, ``repair``, ``open_project``, ``remove_from_registry``, ``health``.
A project's files are the user's: nothing here deletes one, except a MANAGED project's folder moved to the trash on request
(``delete_project_files``) and removed for good after 30 days or on a typed permanent delete (ADR-2321 Decision 12).
"""
from __future__ import annotations

import contextlib
import contextvars
import errno
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict as _OrderedDict
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.paths.tenant import corvin_home
from core.projects import fragments as F
from core.projects import manifest, registry
from core.projects.fsutil import UnsafePath, dirent_is_junction, is_link, passes_link
from core.task_tracking import service as _tt

from . import kb_projection

TEMPLATES_INDEX_ID = "plugin:buildin-knowledge_management-project_templates"
TEMPLATES_PLUGIN_ID = "project_templates"
PLAN_TTL_S = 900.0
MAX_PLANS_PER_TENANT = 50


class ProjectsError(Exception):
    """Base of the errors the routes translate."""
    status = 400
    reason_class = "error"


class NotFound(ProjectsError):
    status = 404
    reason_class = "not_found"


class Conflict(ProjectsError):
    status = 409

    def __init__(self, message: str, reason_class: str = "conflict", **extra: Any):
        super().__init__(message)
        self.reason_class = reason_class
        self.extra = extra


class Unavailable(ProjectsError):
    status = 409
    reason_class = "catalogue_unavailable"


# ── environment: template source, plugin state, bound engine (replaceable in tests) ──────────

def plugin_state(tenant_id: str, registry_id: str) -> str:
    """``absent`` | ``installed`` | ``enabled`` for one plugin in the tenant registry (read on every call)."""
    try:
        from corvin_plugins.manifest import PluginError  # type: ignore[import-not-found]
        from corvin_plugins.state import TenantRegistry  # type: ignore[import-not-found]
    except ImportError:
        return "absent"
    try:
        rec = TenantRegistry.load(tenant_id=tenant_id).records.get(registry_id)
    except (PluginError, OSError):
        return "absent"
    if rec is None:
        return "absent"
    return "enabled" if rec.enabled else "installed"


def template_source(tenant_id: str) -> Path:
    """The ``src/`` of the template plugin, only when the TENANT enabled it (the tenant plugin state is
    the upper bound of every project feature, ADR-2315 Decision 3)."""
    state = plugin_state(tenant_id, TEMPLATES_PLUGIN_ID)
    if state != "enabled":
        raise Unavailable(f"the project templates plugin is {state} for this tenant - enable it in the Marketplace",
                          )
    try:
        from .routes import marketplace_resolve  # noqa: PLC0415

        src = marketplace_resolve.resolve_builtin_dir(TEMPLATES_INDEX_ID) / "src"
    except Exception as exc:  # noqa: BLE001
        raise Unavailable(f"the project templates plugin source was not found ({type(exc).__name__})") from None
    if not (src / "fragments").is_dir():
        raise Unavailable("the project templates plugin ships no fragments")
    return src


def compute_worker_reachable(tenant_id: str) -> Optional[bool]:
    """Can the tenant's compute worker be reached on its socket? ``None``: not determinable here.
    Read-only (one connect with a 1 s timeout, nothing is sent)."""
    try:
        from corvin_compute import endpoint  # type: ignore[import-not-found]
        from corvin_compute.cli import _default_socket_path  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - the compute package is optional in this process
        return None
    try:
        sock = endpoint.connect(_default_socket_path(corvin_home(), tenant_id), timeout_s=1.0)
    except (OSError, ValueError):
        return False
    try:
        sock.close()
    except OSError:
        pass
    return True


def external_warnings(tenant_id: str, features: list[str]) -> list[str]:
    """Prerequisites of a feature that live OUTSIDE the project and that the engine can only partly judge.
    Warnings, never a refusal: the user may start the worker after creating the project."""
    out: list[str] = []
    if "acs" in features:
        up = compute_worker_reachable(tenant_id)
        if up is False:
            out.append("agentic compute: the compute worker of this tenant is not reachable - start it "
                       f"(on Linux: systemctl --user start corvin-compute@{tenant_id}) before the first run")
        elif up is None:
            out.append("agentic compute: the compute worker could not be checked from here")
        out.append("agentic compute: the engine cannot verify the licence tier; Forge and compute need a tier that "
                   "allows them - check the first run")
    return out


def kb_init_script() -> Path:
    from .routes import marketplace_resolve  # noqa: PLC0415

    return marketplace_resolve.resolve_builtin_dir(kb_projection.KG_INDEX_ID) / "src" / "kb_init.py"


def bound_engine(tenant_id: str) -> Path:
    """The ``kb.py`` of the tenant's bound KB: the one engine the path gate pins (ADR-2316 Decision 8)."""
    repo = kb_projection.kb_repo(tenant_id)
    if repo is None or not kb_projection._is_kb(repo):
        raise Conflict("no knowledge base is bound to this tenant, so a project knowledge base has no engine",
                       "no_bound_kb")
    return repo / "scripts" / "kb.py"


def catalogue(tenant_id: str) -> F.Catalogue:
    try:
        cat = F.load_catalogue(template_source(tenant_id))
    except F.FragmentError as exc:
        raise Unavailable(f"the template catalogue is invalid: {exc}") from None
    problems = F.validate_catalogue(cat)
    if problems:
        raise Unavailable("the template catalogue is invalid: " + "; ".join(problems[:5]))
    return cat


# ── audit ────────────────────────────────────────────────────────────────────────────────────

def _audit(tenant_id: str, event: str, **details: Any) -> None:
    """Audit-FIRST and fail-closed: if the chain does not commit, nothing is done (-> 503)."""
    _tt._chain(tenant_id, event, {k: v for k, v in details.items() if v is not None})


#: REVIEW-0063 m7: the session a projects request runs for (set by the routes' session dependency, copied into the worker thread
#: by asyncio.to_thread); None outside a request (one shared key per tenant then)
REFUSAL_SESSION: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("projects_refusal_session", default=None)
REFUSAL_AUDIT_EVERY_S = 60.0
REFUSAL_MAX_KEYS = 4096


class RefusalGate:
    """``project.blocked`` at most once per (installation home, tenant, session, reason class) per minute - the browser's rule (ADR-2322 D11)
    applied to every refusal of the projects service. The FIRST refusal of a key is always recorded; the ones suppressed after
    it are counted and the next record of that key carries ``coalesced: n``, so the trail says how many there were."""

    def __init__(self, every_s: float = REFUSAL_AUDIT_EVERY_S, max_keys: int = REFUSAL_MAX_KEYS):
        self._lock = threading.Lock()
        self.every_s, self.max_keys = every_s, max_keys
        self._keys: "_OrderedDict[tuple[str, ...], tuple[float, int]]" = _OrderedDict()

    def admit(self, key: tuple[str, ...], now: Optional[float] = None) -> Optional[int]:
        """None: suppress (counted). An int: record now, carrying that many suppressed refusals."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hit = self._keys.pop(key, None)
            if hit is not None and now - hit[0] < self.every_s:
                self._keys[key] = (hit[0], hit[1] + 1)
                return None
            self._keys[key] = (now, 0)
            while len(self._keys) > self.max_keys:
                self._keys.popitem(last=False)
            return hit[1] if hit is not None else 0

    def reset(self) -> None:
        with self._lock:
            self._keys.clear()


REFUSAL_GATE = RefusalGate()


def _audit_refusal(tenant_id: str, reason_class: str, project_id: Optional[str] = None) -> None:
    """A refusal has no effect to protect: record it best-effort, at most once per session, reason class and minute
    (:class:`RefusalGate`, REVIEW-0063 m7)."""
    coalesced = REFUSAL_GATE.admit((str(corvin_home()), tenant_id, REFUSAL_SESSION.get() or "-", reason_class))
    if coalesced is None:
        return
    try:
        _tt._chain(tenant_id, "project.blocked", {"reason_class": reason_class,
                                                 **({"project_id": project_id} if project_id else {}),
                                                 **({"coalesced": coalesced} if coalesced else {})})
    except Exception:  # noqa: BLE001
        pass


def _bind_audit(tenant_id: str):
    """The audit-first hook of the workspace binding rules (ADR-2321 Decision 7): ``project.storage_changed`` with the outcome."""
    def _rec(outcome: str) -> None:
        _audit(tenant_id, "project.storage_changed", to="managed", outcome=outcome)
    return _rec


def _workspace(tenant_id: str) -> Path:
    """The tenant's managed root, created and bound on first use; a foreign workspace refuses (422 ``workspace_foreign``)."""
    try:
        root, _outcome = registry.ensure_managed_root(tenant_id, audit=_bind_audit(tenant_id))
    except registry.ProjectPathError as exc:
        _audit_refusal(tenant_id, exc.reason_class)
        raise
    return root


# ── plans (in memory, short-lived) ───────────────────────────────────────────────────────────

@dataclass
class PlanRecord:
    plan_id: str
    tenant_id: str
    template: str
    template_version: int
    project_id: str
    title: str
    features: list[str]
    location: Path
    docs_mode: str
    created: float = field(default_factory=time.monotonic)
    role: str = ""
    classification: str = manifest.DEFAULT_CLASS
    assistant_access: str = manifest.DEFAULT_ACCESS
    storage: str = "own"


_plans: dict[str, PlanRecord] = {}
_plans_lock = threading.Lock()
_plans_running: set[str] = set()


def _store_plan(rec: PlanRecord) -> None:
    now = time.monotonic()
    with _plans_lock:
        for k in [k for k, v in _plans.items() if now - v.created > PLAN_TTL_S]:
            del _plans[k]
        mine = sorted((v for v in _plans.values() if v.tenant_id == rec.tenant_id), key=lambda v: v.created)
        for old in mine[: max(0, len(mine) - MAX_PLANS_PER_TENANT + 1)]:
            _plans.pop(old.plan_id, None)
        _plans[rec.plan_id] = rec


def _take_plan(tenant_id: str, plan_id: str) -> PlanRecord:
    with _plans_lock:
        rec = _plans.get(plan_id)
        if rec is not None and rec.tenant_id == tenant_id and time.monotonic() - rec.created > PLAN_TTL_S:
            del _plans[plan_id]
            rec = None
        if rec is None or rec.tenant_id != tenant_id:
            raise NotFound("no such plan (it expires after 15 minutes) - plan again")
        if plan_id in _plans_running:
            # a double click / a client retry: the second create would write the same project.yaml and its rollback could delete
            # the FIRST create's manifest (adversarial review round 3)
            raise Conflict("this plan is being created right now", "in_progress")
        _plans_running.add(plan_id)
        return rec


_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "å": "aa", "đ": "d", "ł": "l"})


_held = threading.local()


def _project_locked(fn):
    """One writer per project at a time: every function that reads project.yaml, changes it and writes it back (or moves the folder,
    or appends to videos/INDEX.md) holds a per-project file lock, so a rename and a settings change running in parallel cannot undo
    each other (adversarial review 2026-11-10). Re-entrant within a thread (move -> repair)."""
    import functools  # noqa: PLC0415

    @functools.wraps(fn)
    def wrapper(tenant_id: str, project_id: str, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(project_id, str) or not manifest.PROJECT_ID_RE.match(project_id):
            return fn(tenant_id, project_id, *args, **kwargs)        # not an id: the function refuses it, no lock file is named after it
        key = (tenant_id, project_id)
        held = getattr(_held, "keys", None)
        if held is None:
            held = _held.keys = set()
        if key in held:
            return fn(tenant_id, project_id, *args, **kwargs)
        from core.projects.fsutil import file_lock  # noqa: PLC0415
        with file_lock(registry.registry_path(tenant_id).with_name(f"project-{project_id}.lock"), timeout=20.0):
            held.add(key)
            try:
                return fn(tenant_id, project_id, *args, **kwargs)
            finally:
                held.discard(key)
    return wrapper


def slug(title: str) -> str:
    """A kebab-case id from a title or folder name: German umlauts spelled out (Büro -> buero), other accents dropped
    (café -> cafe), never a route word and never a Windows device name (the id is the default folder name)."""
    import unicodedata  # noqa: PLC0415
    t = unicodedata.normalize("NFKD", (title or "").lower().translate(_TRANSLIT))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:28].strip("-")
    s = s or "project"
    if not manifest.is_new_id_allowed(s):                # a route word (review R9) or con/nul/aux/com1 ...
        s = f"{s[:20]}-project"
    return s


def _unique_id(tenant_id: str, base: str) -> str:
    taken = {e.get("id") for e in registry.entries(tenant_id)}
    if base not in taken:
        return base
    for n in range(2, 100):
        suffix = f"-{n}"
        cand = base[: 28 - len(suffix)].rstrip("-") + suffix
        if cand not in taken:
            return cand
    raise Conflict("could not derive a free project id")


def _refuse_foreign_kb(tenant_id: str, loc: Path) -> None:
    """A location that is, lies inside or contains ANY tenant's bound knowledge base (the default projects root is shared by all
    tenants; Corvin-Knowledge sits in it) is never a project: adopting it would write a project.yaml into that KB and hand the
    folder to this tenant's engine (adversarial review round 6)."""
    try:
        names = [d.name for d in (corvin_home() / "tenants").iterdir() if d.is_dir()]
    except OSError:
        names = []
    for name in {*names, tenant_id}:
        try:
            kb = kb_projection.kb_repo(name)
        except Exception:  # noqa: BLE001
            continue
        if kb is None:
            continue
        k = registry._resolve_forgiving(Path(kb))
        if loc == k or k in loc.parents or loc in k.parents:
            raise registry.ProjectPathError("the location is, or contains, a knowledge base; it cannot be a project")


def _context(tenant_id: str, project_id: str, title: str, root: Path) -> dict[str, str]:
    ctx = {"project.id": project_id, "project.title": title, "project.tenant": tenant_id,
           "paths.python": os.environ.get("CORVIN_PYTHON") or sys.executable,
           "paths.repo": str(kb_projection._REPO_ROOT),
           "paths.corvin_home": str(corvin_home()),
           "paths.docs": str(root / "docs"), "paths.pathsep": os.pathsep}
    try:
        ctx["paths.kb_engine"] = str(bound_engine(tenant_id))
    except Conflict:
        ctx["paths.kb_engine"] = ""
    return ctx


def _check_plugins(tenant_id: str, plan: F.Plan) -> list[dict[str, str]]:
    return [{"plugin": p, "state": plugin_state(tenant_id, p)} for p in plan.plugins_needed]


def _require_plugins(tenant_id: str, plan: F.Plan, project_id: Optional[str]) -> None:
    missing = [x for x in _check_plugins(tenant_id, plan) if x["state"] != "enabled"]
    if missing:
        _audit_refusal(tenant_id, "plugin_missing", project_id)
        raise Conflict("a tenant plugin the selected features need is not enabled: "
                       + ", ".join(f"{m['plugin']} ({m['state']})" for m in missing)
                       + ". A project can never enable a plugin; enable it in the Marketplace.",
                       "plugin_missing", plugins=missing)


# ── API operations ───────────────────────────────────────────────────────────────────────────

def list_templates(tenant_id: str) -> dict[str, Any]:
    try:
        cat = catalogue(tenant_id)
    except Unavailable as exc:
        return {"available": False, "reason": str(exc), "templates": [], "features": []}
    feats = []
    for fid in cat.order:
        f = cat.fragments[fid]
        feats.append({"id": fid, "title": f.title, "description": f.description, "requires": list(f.requires),
                      "suggests": list(f.suggests),
                      "plugins": [{"plugin": p, "state": plugin_state(tenant_id, p)} for p in f.requires_plugins]})
    return {"available": True, "projects_root": str(registry.projects_root(tenant_id)),
            "templates": [{"id": t.id, "version": t.version, "title": t.title, "description": t.description,
                           "features": list(t.features), "offered": list(t.offered), "docs_mode": t.docs_mode,
                           "role": t.role, "classification": t.classification,
                           "assistant_access": t.assistant_access, "outcome": t.outcome}
                          for t in cat.templates.values()],
            "features": feats}


def plan_project(tenant_id: str, *, template: str, title: str, project_id: Optional[str] = None,
                 location: Optional[str] = None, features: Optional[list[str]] = None,
                 docs_mode: Optional[str] = None, classification: Optional[str] = None,
                 assistant_access: Optional[str] = None, storage: Optional[str] = None) -> dict[str, Any]:
    if storage is not None and storage not in registry.STORAGE_KINDS:
        raise ProjectsError("storage must be managed or root")
    cat = catalogue(tenant_id)
    tpl = cat.templates.get(template)
    if tpl is None:
        raise NotFound(f"no such template {template!r}")
    title = (title or "").strip()
    if not title or len(title) > 120:
        raise ProjectsError("the title must be 1-120 characters")
    pid = project_id or _unique_id(tenant_id, slug(title))
    if not manifest.is_new_id_allowed(pid):
        raise ProjectsError("the project id must be lowercase kebab-case, 1-28 characters, and not a reserved word "
                            "or a Windows device name (con, nul, aux, prn, com1, lpt1 ...)")
    if registry.get(tenant_id, pid) is not None:
        raise Conflict("a project with this id already exists", "id_taken")
    if len(registry.entries(tenant_id)) >= registry.MAX_PROJECTS:
        raise Conflict(f"at most {registry.MAX_PROJECTS} projects per tenant", "limit")
    selected = list(features) if features is not None else list(tpl.features)
    outside = [f for f in selected if f not in tpl.offered]
    if outside:
        raise ProjectsError(f"features not offered by this template: {outside}")
    mode = docs_mode or tpl.docs_mode
    if mode not in manifest.DOCS_MODES:
        raise ProjectsError(f"docs_mode must be one of {manifest.DOCS_MODES}")
    cls = classification or tpl.classification
    acc = assistant_access or tpl.assistant_access
    if cls not in manifest.CLASSES:
        raise ProjectsError(f"classification must be one of {manifest.CLASSES}")
    if acc not in manifest.ACCESS:
        raise ProjectsError(f"assistant_access must be one of {manifest.ACCESS}")
    try:
        try:
            raw_loc, _kind = registry.default_location(tenant_id, pid, location, audit=_bind_audit(tenant_id), storage=storage)
        except registry.RegistryError as exc:
            if str(exc) != "root_required":
                raise
            _audit_refusal(tenant_id, "root_required", pid)
            raise Conflict(ROOT_REQUIRED, "root_required") from None
        loc = registry.validate_location(tenant_id, str(raw_loc), purpose="create")
        _refuse_foreign_kb(tenant_id, loc)
    except registry.ProjectPathError as exc:
        _audit_refusal(tenant_id, exc.reason_class, pid)
        raise
    storage = registry.storage_of(tenant_id, loc)
    ctx = _context(tenant_id, pid, title, loc)
    plan = F.build_plan(cat, loc, selected, ctx, docs_mode=mode, mode="create")
    if any(o.kind == "action" and o.action in ("git_init", "git_commit") for o in plan.ops) and shutil.which("git") is None:
        _audit_refusal(tenant_id, "git_missing", pid)
        raise Conflict("git is not installed on this machine; install git and plan again - nothing was written", "git_missing")
    plugins = _check_plugins(tenant_id, plan)
    rec = PlanRecord(plan_id=secrets.token_urlsafe(18), tenant_id=tenant_id, template=tpl.id,
                     template_version=tpl.version, project_id=pid, title=title, features=selected,
                     location=loc, docs_mode=mode, role=tpl.role, classification=cls, assistant_access=acc, storage=storage)
    _audit(tenant_id, "project.plan_created", project_id=pid, template=f"{tpl.id}@{tpl.version}",
           files=len(plan.writes), storage=storage)
    _store_plan(rec)
    if location:
        _record_recent(tenant_id, loc)                          # an explicit location the user chose (never the default place)
    summary = plan.summary()
    loc_warn = registry.location_warnings(loc) if storage == "own" else []
    summary["warnings"] = [*summary["warnings"], *external_warnings(tenant_id, plan.features), *(w["message"] for w in loc_warn)]
    summary["location_warnings"] = [w["class"] for w in loc_warn]
    return {"plan_id": rec.plan_id, "project_id": pid, "title": title, "template": f"{tpl.id}@{tpl.version}",
            "docs_mode": mode, "location": str(loc), "storage": storage, "role": tpl.role, "classification": cls,
            "assistant_access": acc, **summary, "plugins": plugins,
            "ready": not plan.conflicts and all(p["state"] == "enabled" for p in plugins),
            "expires_in_s": int(PLAN_TTL_S)}


def changes_error() -> type:
    from core.projects import changes  # noqa: PLC0415
    return changes.ChangesError


def _need_git() -> None:
    if shutil.which("git") is None:
        raise Conflict("git is not installed on this machine; install git and try again - nothing was changed", "git_missing")


def _git(root: Path, *args: str) -> None:
    from core.projects import changes  # noqa: PLC0415
    _need_git()
    if args and args[0] != "init":
        try:
            changes.require_own_repo(Path(root))
        except changes.ChangesError as exc:
            raise Conflict(str(exc), "repo_unsupported") from None
    try:
        r = subprocess.run(["git", "-C", str(root), "-c", "user.name=CorvinOS", "-c", "user.email=corvin@localhost",
                            "-c", "commit.gpgsign=false", *changes.neutral_git_args(Path(root)), *args],
                           capture_output=True, text=True, env=changes.neutral_git_env(Path(root), kb_projection._env("operator")), encoding="utf-8",
                           errors="replace", timeout=120)
    except changes.ChangesError as exc:
        raise Conflict(str(exc), "git_unguarded") from None
    except OSError as exc:
        raise Conflict(f"git could not be run ({type(exc).__name__})", "git_missing") from None
    if r.returncode != 0:
        raise ProjectsError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:200]}")
    if args and args[0] in ("init", "clone"):
        changes.forget_repo_cache()                  # the folder is a repository now: do not keep answering "not a repository"


def _sync_mcp(tenant_id: str, project_id: str, root: Path, active: Any) -> None:
    """Record the hash of the MCP file the engine just wrote, but only while an active feature declares MCP servers; otherwise clear it.
    A chat loads project MCP servers only against this record (release review round 2)."""
    import hashlib  # noqa: PLC0415
    try:
        cat = catalogue(tenant_id)
        needs = any(cat.fragments[f].mcp for f in F.closure(cat, sorted(active)) if f in cat.fragments)
    except Exception:  # noqa: BLE001
        needs = False
    f = Path(root) / ".corvin" / "local" / "mcp.json"
    sha = None
    if needs and not passes_link(Path(root), f) and f.is_file():
        from core.projects.fsutil import read_bytes_limited  # noqa: PLC0415
        try:
            sha = hashlib.sha256(read_bytes_limited(f)).hexdigest()
        except (OSError, ValueError):
            sha = None                                  # unreadable or oversized: no hash, so a chat never loads it
    registry.set_mcp(tenant_id=tenant_id, project_id=project_id, sha=sha)


def _refuse_linked_changes(where: Path) -> None:
    """Before ``git add -A``: git for Windows walks INTO a directory junction and would commit the files behind it into the project's
    history, where the change view could later show them. Every path git is about to stage (untracked, modified, deleted) must lie
    below ``where`` without a link on the way; otherwise nothing is committed (adversarial review 2026-11-10)."""
    from core.projects import changes  # noqa: PLC0415
    _need_git()
    try:
        changes.require_own_repo(Path(where))
    except changes.ChangesError as exc:
        raise Conflict(str(exc), "repo_unsupported") from None
    try:
        r = subprocess.run(["git", "-C", str(where), *changes.neutral_git_args(Path(where)), "ls-files", "-z", "--others",
                            "--exclude-standard", "--modified", "--deleted"],
                           capture_output=True, timeout=120, env=changes.neutral_git_env(Path(where), kb_projection._env("operator")))
    except changes.ChangesError as exc:
        raise Conflict(str(exc), "git_unguarded") from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Conflict(f"git could not list the changes ({type(exc).__name__})", "git_missing") from None
    if r.returncode != 0:
        return                                    # no repository yet / nothing tracked: `git add` reports its own error
    paths = sorted({x for x in r.stdout.decode("utf-8", errors="surrogateescape").split("\0") if x})
    for rel in paths:
        if passes_link(Path(where), Path(where) / rel):
            raise Conflict("the project contains a link (symlink or junction) with files behind it; remove the link (or add it to "
                           ".gitignore) - nothing was committed", "linked_changes")
    env = changes.neutral_git_env(Path(where), kb_projection._env("operator"))
    off = changes.neutralised_filters(env)
    if paths and off:
        # the console runs no filter program (LFS & co. are switched off): `git add` would commit such files raw instead of as the
        # pointers the repository expects (adversarial review round 4)
        try:
            ca = subprocess.run(["git", "-C", str(where), *changes.neutral_git_args(Path(where)), "check-attr", "-z", "--stdin", "filter"],
                                input=("\0".join(paths) + "\0").encode("utf-8", errors="surrogateescape"), capture_output=True, timeout=60, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Conflict(f"git could not check the file attributes ({type(exc).__name__})", "git_missing") from None
        f = ca.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        used = {f[i + 2] for i in range(0, len(f) - 2, 3) if f[i + 1] == "filter"}
        if used & off:
            raise Conflict(f"files to commit are stored through a git filter the console does not run ({', '.join(sorted(used & off))}, "
                           "e.g. Git LFS); commit them in your own terminal - nothing was committed", "git_filter")


def _project_kb_run(docs: Path, *args: str, **kwargs: Any):
    """``kb_projection._run`` on a PROJECT's docs/, after its knowledge-base config was judged: the engine follows what
    ``docs/kb/_meta/sources.yaml`` names (source folders, git refs, frozen snapshots), and that file is the assistant's or an adopted
    folder's to write - an unchecked run could execute a git filter, write into another folder or contact a network path
    (adversarial review round 6)."""
    from core.projects import changes  # noqa: PLC0415
    if passes_link(docs.parent, docs):
        raise Conflict("docs/ is a link (symlink or junction); the knowledge base is not read or changed through it", "linked_changes")
    try:
        changes.require_own_repo(docs)
        changes.require_own_repo(docs.parent)
    except changes.ChangesError as exc:
        raise Conflict(str(exc), "repo_unsupported") from None
    if not kb_projection.project_sources_contained(docs):
        raise Conflict("the project's knowledge-base config (docs/kb/_meta/sources.yaml) names a folder outside docs/, a git ref, a snapshot or a "
                       "link; the knowledge base is not read or changed until that is fixed", "kb_sources")
    return kb_projection._run(docs, *args, **kwargs)


def _runner(tenant_id: str, root: Path):
    def run(op: F.Op, ctx: dict[str, str]) -> None:
        docs = root / "docs"
        if op.action in ("kb_scaffold", "kb_seed") or (op.action == "git_commit" and op.args.get("dir")):
            if passes_link(root, docs):
                # the writes were skipped, but these actions run a program INSIDE docs/: through a junction that is somewhere else
                raise Conflict("docs/ is a link (symlink or junction); the knowledge base is not created or changed through it", "linked_changes")
        if op.action == "git_init":
            _git(root, "init", "-q")
            _git(root, "symbolic-ref", "HEAD", "refs/heads/main")
        elif op.action == "git_commit":
            where = root / op.args["dir"] if op.args.get("dir") else root
            _refuse_linked_changes(where)
            _git(where, "add", "-A")
            _git(where, "commit", "-q", "-m", op.args.get("message", "project: update"), "--allow-empty")
        elif op.action == "kb_scaffold":
            init = kb_init_script()
            args = [sys.executable, str(init), "--target", str(docs), "--no-engine"]
            if op.args.get("docs_mode") == "plain":
                args.append("--no-git")
            r = subprocess.run(args, capture_output=True, text=True, timeout=120, env=kb_projection._env("kb-init"),
                               encoding="utf-8", errors="replace")
            try:
                res = json.loads(r.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                res = {"status": "error", "error": (r.stderr or r.stdout)[-200:]}
            if res.get("status") not in ("created", "ok"):
                raise ProjectsError(f"knowledge base scaffold failed: {str(res.get('error') or res)[:200]}")
        elif op.action == "kb_seed":
            engine = bound_engine(tenant_id)

            def kb(*a: str) -> dict[str, Any]:
                rc, out = _project_kb_run(docs, *a, "--actor=operator", actor="operator", engine=engine, guard_git=True)
                if rc != 0 or not isinstance(out, dict):
                    raise ProjectsError(f"kb {a[0]} {a[1] if len(a) > 1 else ''} failed: {str(out)[:200]}")
                return out
            ini = kb("new", "initiative", f"--title={op.args['initiative']}")
            epic = kb("new", "epic", f"--title={op.args['epic']}", f"--initiative={ini['id']}")
            for t in op.args.get("tasks", []):
                kb("new", "task", f"--title={t['title']}", f"--epic={epic['id']}", f"--dod={t['dod']}")
        else:  # pragma: no cover - the engine only emits the closed set
            raise ProjectsError(f"unknown action {op.action!r}")
    return run


def _manifest_for(rec_tenant: str, pid: str, title: str, template: str, version: int, mode: str,
                  features: list[str], offered: list[str], videos: Optional[dict[str, Any]] = None,
                  role: str = "", classification: str = manifest.DEFAULT_CLASS,
                  assistant_access: str = manifest.DEFAULT_ACCESS) -> dict[str, Any]:
    on = set(features)
    man: dict[str, Any] = {"schema": manifest.SCHEMA, "id": pid, "title": title, "tenant": rec_tenant,
                           "template": f"{template}@{version}",
                           "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "docs_mode": mode, "classification": classification, "assistant_access": assistant_access,
                           "features": {f: (f in on) for f in dict.fromkeys([*offered, *features])}}
    if role:
        man["role"] = role
    if "videos" in on and videos:
        man["videos"] = dict(videos)                  # the template's defaults: where videos live, which languages
    if "directory" in on:
        man["directory"] = {"peers": []}              # the project's peer allowlist starts empty (nothing is sent)
    return man


def create_project(tenant_id: str, plan_id: str) -> dict[str, Any]:
    rec = _take_plan(tenant_id, plan_id)
    try:
        return _create_project(tenant_id, plan_id, rec)
    finally:
        with _plans_lock:
            _plans_running.discard(plan_id)


def _create_project(tenant_id: str, plan_id: str, rec: "PlanRecord") -> dict[str, Any]:
    cat = catalogue(tenant_id)
    tpl = cat.templates.get(rec.template)
    if tpl is None or tpl.version != rec.template_version:
        _audit_refusal(tenant_id, "template_changed", rec.project_id)
        raise Conflict("the template changed since this plan was made - plan again", "template_changed")
    try:
        if rec.storage == "managed":
            _workspace(tenant_id)                                           # the workspace must still be this installation's
        loc = registry.validate_location(tenant_id, str(rec.location), purpose="create")   # re-validate: the disk may have changed
    except registry.ProjectPathError as exc:
        _audit_refusal(tenant_id, exc.reason_class, rec.project_id)
        raise
    if registry.get(tenant_id, rec.project_id) is not None:
        raise Conflict("a project with this id already exists", "id_taken")
    ctx = _context(tenant_id, rec.project_id, rec.title, loc)
    plan = F.build_plan(cat, loc, rec.features, ctx, docs_mode=rec.docs_mode, mode="create")
    if plan.conflicts:
        _audit_refusal(tenant_id, "conflict", rec.project_id)
        raise Conflict("; ".join(plan.conflicts), "conflict")
    _require_plugins(tenant_id, plan, rec.project_id)
    if "kb" in plan.features:
        bound_engine(tenant_id)
    base = dict(project_id=rec.project_id, template=f"{tpl.id}@{tpl.version}", files=len(plan.writes),
                storage=registry.storage_of(tenant_id, loc))
    _audit(tenant_id, "project.created", outcome="requested", **base)          # audit-first
    outcome = "error"
    try:
        # REVIEW-0063 m4: made component by component without following a link, then checked to still be where the verdict
        # put it - a component swapped for a link after validate_location refuses (symlink) instead of creating the project there
        try:
            hloc = registry.make_location(loc)
        except registry.ProjectPathError as exc:
            _audit_refusal(tenant_id, exc.reason_class, rec.project_id)
            raise
        try:
            man = _manifest_for(tenant_id, rec.project_id, rec.title, tpl.id, tpl.version, rec.docs_mode,
                                plan.features, list(tpl.offered), tpl.videos, rec.role, rec.classification,
                                rec.assistant_access)
            manifest.write(loc, man)                                            # the truth first,
            try:
                if not registry.location_intact(loc, hloc):                     # re-checked right before it is recorded
                    _audit_refusal(tenant_id, "symlink", rec.project_id)
                    raise registry.ProjectPathError(registry.SENTENCES["symlink"], "symlink")
                registry.register(tenant_id, rec.project_id, loc, man["template"])  # then the index
            except BaseException:
                # nothing else was written yet: take the manifest (and the folders this call created) back, so planning again works
                try:
                    if hloc.fd is not None:
                        os.unlink(manifest.FILE, dir_fd=hloc.fd)                # in the folder that was made, never through a link
                    elif hloc.still_here():
                        (loc / manifest.FILE).unlink()
                except OSError:
                    pass
                hloc.rollback()
                raise
        finally:
            hloc.close()
        registry.set_protection(tenant_id, rec.project_id, rec.classification, rec.assistant_access)
        try:
            applied = F.apply_plan(plan, loc, {**ctx, "_root": str(loc)}, _runner(tenant_id, loc))
        except Exception as exc:  # noqa: BLE001 - a half-created project must not stay in the list (release review R4)
            registry.unregister(tenant_id, rec.project_id)
            raise Conflict(f"creating the project failed ({type(exc).__name__}); it was taken off the list and its folder "
                           f"{loc} was left as it is - remove it or add it again", "create_failed") from None
        _sync_mcp(tenant_id, rec.project_id, loc, plan.features)
        outcome = "applied"
    finally:
        try:
            _audit(tenant_id, "project.created", outcome=outcome, **base)
        except _tt.AuditUnavailable:
            pass
    with _plans_lock:
        _plans.pop(plan_id, None)
    return {"project": get_project(tenant_id, rec.project_id), "written": applied["written"], "actions": applied["actions"]}


def _detect_adopted(root: Path) -> tuple[str, dict[str, bool]]:
    """Read-only look at a foreign directory: which docs mode it has and which features it already
    carries. Only evidence on disk counts - nothing is guessed, nothing is written."""
    linked = passes_link(root, root / "docs")
    nested = not linked and (root / "docs" / ".git").exists()
    kb = not linked and not passes_link(root, root / "docs" / "kb" / "_meta") and (root / "docs" / "kb" / "_meta").is_dir()
    return ("nested" if nested else "plain"), ({"kb": True} if kb else {})


def adopt_project(tenant_id: str, location: str, *, project_id: Optional[str] = None,
                  title: Optional[str] = None) -> dict[str, Any]:
    """Register a directory. With a ``project.yaml`` it is registered as is. Without one (a
    repository the user already owns) exactly ONE file is written - ``project.yaml`` - and
    nothing else in the directory is touched (ADR-2315 Decision 6)."""
    try:
        # the input is normalised (and a network path refused) BEFORE the first filesystem access: an exists() on a UNC path
        # would wait for the SMB timeout; the manifest is looked for in the normalised local folder only
        loc = registry.validate_location(tenant_id, location, purpose="adopt")
        _refuse_foreign_kb(tenant_id, loc)
    except registry.ProjectPathError as exc:
        _audit_refusal(tenant_id, exc.reason_class)
        raise
    if (loc / manifest.FILE).exists():
        man = manifest.load(loc)
        if registry.get(tenant_id, man["id"]) is not None:
            raise Conflict("a project with this id is already registered", "id_taken")
        _audit(tenant_id, "project.adopted", project_id=man["id"], template=man.get("template"))
        registry.register(tenant_id, man["id"], loc, man.get("template"))        # writes nothing in the project
        registry.set_protection(tenant_id, man["id"], manifest.legacy_class(man), manifest.DEFAULT_ACCESS)   # a manifest may RAISE the class, never grant itself write access
        _record_recent(tenant_id, loc)
        return get_project(tenant_id, man["id"])
    ttl = (title or loc.name).strip()
    if not 0 < len(ttl) <= 120:
        raise ProjectsError("the title must be 1-120 characters")
    if project_id is not None and not manifest.is_new_id_allowed(project_id):
        raise ProjectsError("the project id must be lowercase kebab-case, 1-28 characters, and not a reserved word "
                            "or a Windows device name (con, nul, aux, prn, com1, lpt1 ...)")
    pid = project_id or _unique_id(tenant_id, slug(loc.name))
    if registry.get(tenant_id, pid) is not None:
        raise Conflict("a project with this id is already registered", "id_taken")
    if len(registry.entries(tenant_id)) >= registry.MAX_PROJECTS:
        raise Conflict(f"at most {registry.MAX_PROJECTS} projects per tenant", "limit")
    mode, feats = _detect_adopted(loc)
    data = {"schema": manifest.SCHEMA, "id": pid, "title": ttl, "tenant": tenant_id,
            "template": "adopted@1", "docs_mode": mode, "features": feats}
    manifest.validate(data)
    _audit(tenant_id, "project.adopted", project_id=pid, template="adopted@1", outcome="requested")
    manifest.write(loc, data)                                                    # the ONLY write
    try:
        registry.register(tenant_id, pid, loc, "adopted@1")
    except BaseException:
        # a parallel request won the id / the limit: the folder is left exactly as it was (no stray project.yaml)
        try:
            if manifest.load(loc).get("id") == pid:
                (loc / manifest.FILE).unlink()
        except (OSError, manifest.ManifestError):
            pass
        raise
    registry.set_protection(tenant_id, pid, manifest.DEFAULT_CLASS, manifest.DEFAULT_ACCESS)
    _record_recent(tenant_id, loc)
    return get_project(tenant_id, pid)


@_project_locked
def rename_project(tenant_id: str, project_id: str, title: str) -> dict[str, Any]:
    _refuse_builtin(project_id)
    """Change the title in ``project.yaml``. The id (the KB scope label, the registry key) never changes."""
    root, man = _load_project(tenant_id, project_id)
    ttl = (title or "").strip()
    if not 0 < len(ttl) <= 120:
        raise ProjectsError("the title must be 1-120 characters")
    _audit(tenant_id, "project.renamed", project_id=project_id)
    manifest.write(root, {**man, "title": ttl})
    return get_project(tenant_id, project_id)


CROSS_DEVICE = ("The new place is on another disk. Export the project and import it there, or copy the folder yourself and use "
                "Locate folder.")


@_project_locked
def move_project(tenant_id: str, project_id: str, location: str) -> dict[str, Any]:
    _refuse_builtin(project_id)
    """Move the directory to another place under this tenant's projects root and follow it in the
    index. Same filesystem only (an atomic rename); an existing target is never overwritten."""
    root, _man = _load_project(tenant_id, project_id)
    try:
        same = registry._resolve_forgiving(Path(registry.normalize_input(location))) == registry._resolve_forgiving(root)
    except (registry.ProjectPathError, OSError, ValueError):
        same = False
    if same:
        raise Conflict("the project is already in this folder (Windows does not tell folders apart by letter case)", "move_same")
    try:
        dest = registry.validate_location(tenant_id, location, purpose="move", project_id=project_id)  # absent or empty
    except registry.ProjectPathError as exc:
        _audit_refusal(tenant_id, exc.reason_class, project_id)
        raise
    if dest == root or root in dest.parents:
        raise Conflict("the target lies inside the project itself", "move_inside")
    if dest.exists() and any(dest.iterdir()):
        raise Conflict("the target is not empty", "target_exists")
    try:
        root_ident = (os.lstat(root).st_dev, os.lstat(root).st_ino)
    except OSError:
        raise Conflict("the directory could not be moved (it is gone); nothing changed", "move_failed") from None
    _audit(tenant_id, "project.moved", project_id=project_id, outcome="requested")
    hpar = None
    try:
        # REVIEW-0063 m4: the target's parent is made/opened without following a link and verified; the rename goes INTO that
        # directory's descriptor (POSIX), and the moved folder must be the project's folder at the judged place afterwards
        try:
            hpar = registry.make_location(dest.parent)
        except registry.ProjectPathError as exc:
            _audit_refusal(tenant_id, exc.reason_class, project_id)
            raise
        if hpar.fd is not None:
            with contextlib.suppress(FileNotFoundError):
                os.rmdir(dest.name, dir_fd=hpar.fd)                     # empty by the check above
        elif dest.exists():
            dest.rmdir()
        for attempt in range(8):
            try:
                if hpar.fd is not None:
                    os.rename(root, dest.name, dst_dir_fd=hpar.fd)
                else:
                    os.rename(root, dest)
                break
            except PermissionError:
                # Windows refuses to rename a folder another process uses as its working directory - the activity watcher's git
                # poll does for a moment: wait it out briefly; a lasting hold (an open file, a terminal) still fails cleanly
                if attempt == 7:
                    raise
                time.sleep(0.25)
        try:
            moved = os.stat(dest.name, dir_fd=hpar.fd, follow_symlinks=False) if hpar.fd is not None else os.lstat(dest)
            ok = (moved.st_dev, moved.st_ino) == root_ident and registry.location_intact(dest.parent, hpar) and \
                registry._same_place(dest)
        except OSError:
            ok = False
        if not ok:
            with contextlib.suppress(OSError):
                if hpar.fd is not None:
                    os.rename(dest.name, root, src_dir_fd=hpar.fd)
                else:
                    os.rename(dest, root)
            hpar.rollback()
            _audit_refusal(tenant_id, "symlink", project_id)
            raise registry.ProjectPathError(registry.SENTENCES["symlink"], "symlink")
    except OSError as exc:
        if exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17:      # ERROR_NOT_SAME_DEVICE: never copy across disks
            _audit_refusal(tenant_id, "cross_device", project_id)
            raise Conflict(CROSS_DEVICE, "cross_device") from None
        _audit_refusal(tenant_id, "move_failed", project_id)
        if hpar is not None:
            hpar.rollback()
        raise Conflict(f"the directory could not be moved ({type(exc).__name__}); nothing changed", "move_failed") from None
    finally:
        if hpar is not None:
            hpar.close()
    try:
        registry.relocate(tenant_id, project_id, dest)
    except Exception:  # noqa: BLE001 - the index did not follow: put the folder back (release review R10)
        try:
            os.rename(dest, root)
        except OSError:
            pass
        _audit_refusal(tenant_id, "move_failed", project_id)
        raise Conflict("the list could not be updated, so the folder was moved back; nothing changed", "move_failed") from None
    # From here on the move HAS happened (folder and list agree): every later step is best effort and is reported as a warning,
    # never as a failure that would make the user move it again (adversarial review 2026-11-10)
    warnings: list[str] = []
    from core.projects import attachment  # noqa: PLC0415
    try:
        for sid, att in attachment._load(tenant_id).items():                   # attached chats follow the folder
            if att["project_id"] == project_id:
                attachment.attach(tenant_id, sid, project_id, str(dest))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"the chats attached to this project could not be updated ({type(exc).__name__}); attach them again")
    try:
        _audit(tenant_id, "project.moved", project_id=project_id, outcome="applied")
    except _tt.AuditUnavailable:
        warnings.append("the audit chain was unavailable for the 'applied' record")
    _record_recent(tenant_id, dest)
    # machine-local files carry the old absolute path (ADR-2315 Decision 5): regenerate them now
    repaired: Optional[int] = None
    try:
        repaired = len(repair(tenant_id, project_id).get("written", []))
    except Exception:  # noqa: BLE001 - the move stands; the doctor will name what is out of sync
        repaired = None
    return {**get_project(tenant_id, project_id), "repaired_files": repaired, "warnings": warnings}


def _refuse_builtin(project_id: str) -> None:
    """Corvin itself is a project that nobody can change here (CONCEPT-0129)."""
    from core.projects import builtin  # noqa: PLC0415
    if builtin.is_builtin(project_id):
        raise Conflict("Corvin is built in: it cannot be removed, renamed, moved or reconfigured", "builtin")


def _entry(tenant_id: str, project_id: str) -> dict[str, Any]:
    from core.projects import builtin  # noqa: PLC0415
    if builtin.is_builtin(project_id):
        return builtin.entry(tenant_id)
    e = registry.get(tenant_id, project_id) if manifest.PROJECT_ID_RE.match(project_id or "") else None
    if e is None:
        raise NotFound("no such project")
    if registry.is_trashed(e):
        raise Conflict("the project is in the Trash; restore it from the Projects page first", "in_trash")
    return e


def _summary(tenant_id: str, e: dict[str, Any]) -> dict[str, Any]:
    if e.get("builtin"):
        from core.projects import builtin  # noqa: PLC0415
        root = Path(str(e.get("path") or ""))
        man = builtin.manifest(tenant_id)
        base = {"id": builtin.ID, "path": str(root), "template": "builtin@1", "added_at": None, "builtin": True,
                "storage": "builtin", "display_path": str(root)}
        if not str(e.get("path") or "") or not root.is_dir():
            return {**base, "state": "pending", "title": builtin.TITLE, "docs_mode": "plain", "features": {}, "active": [], "role": "",
                    "classification": "internal", "assistant_access": "read", "kb": False,
                    "reason": "Corvin's knowledge base has not been created yet; it appears after the Knowledge Graph plugin is enabled"}
        return {**base, "state": "ok", "title": builtin.TITLE, "docs_mode": "plain", "features": dict(man["features"]),
                "active": [f for f, on in man["features"].items() if on], "role": "", "classification": "internal",
                "assistant_access": "read", "kb": True}
    root = Path(str(e.get("path")))
    out: dict[str, Any] = {"id": e["id"], "path": str(root), "template": e.get("template") or "", "added_at": e.get("added_at"),
                           "storage": e.get("storage") or "own", "display_path": _display(tenant_id, root)}
    if e.get("pending"):
        # an import that is not applied yet (ADR-2323 Decision 13 step 2): listed, never opened, attached or projected
        return {**out, "state": "pending", "pending": e["pending"], "title": e["id"],
                "reason": PENDING_REASON.get(str(e["pending"]), "pending")}
    try:
        man = manifest.load(root)
    except manifest.ManifestError as exc:
        return {**out, "state": "missing" if not root.exists() else "invalid", "reason": str(exc)}
    if man.get("tenant") != tenant_id or man.get("id") != e["id"]:
        return {**out, "state": "invalid", "reason": "project.yaml does not match the registry entry"}
    return {**out, "state": "ok", "title": man.get("title", e["id"]), "docs_mode": man.get("docs_mode", "nested"),
            "features": man.get("features", {}), "active": manifest.active_features(man),
            "role": man.get("role") or "", **_protection_of(tenant_id, e["id"], man),
            "kb": not passes_link(root, root / "docs" / "kb" / "_meta") and (root / "docs" / "kb" / "_meta").is_dir()}


def _protection_of(tenant_id: str, project_id: str, man: dict[str, Any]) -> dict[str, str]:
    """The tenant's own record wins; project.yaml is a copy the assistant could have edited (release review R5)."""
    prot = registry.protection(tenant_id, project_id) or {}
    return {"classification": prot.get("classification") or manifest.legacy_class(man),     # only ever raised by project.yaml
            "assistant_access": prot.get("assistant_access") or manifest.DEFAULT_ACCESS}


def _builtin_first(tenant_id: str) -> dict[str, Any]:
    from core.projects import builtin  # noqa: PLC0415
    return _summary(tenant_id, builtin.entry(tenant_id))


def _display(tenant_id: str, p: Path) -> str:
    from core.paths import user_dirs  # noqa: PLC0415
    try:
        return user_dirs.display_path(p, managed_root=registry.known_managed_root(tenant_id), home=Path.home())
    except (OSError, ValueError, RuntimeError):
        return str(p)


def location_settings(tenant_id: str) -> dict[str, Any]:
    """Where new projects go and where the workspace is (ADR-2321 Decision 16). The workspace is created and bound here on first
    read (a lost ``settings.json`` recovers its key from the marker); a foreign workspace is reported, never used."""
    workspace = "ok"
    try:
        _workspace(tenant_id)
    except registry.WorkspaceForeign:
        workspace = "foreign"
    except (registry.ProjectPathError, OSError):
        workspace = "unavailable"
    st = registry.settings(tenant_id)
    return {"default_storage": st["default_storage"], "managed_root": st["managed_root"],
            "projects_root": str(registry.projects_root(tenant_id)), "projects_root_set": st["projects_root"] is not None,
            "workspace": workspace}


def _trashed_summary(e: dict[str, Any]) -> dict[str, Any]:
    """A trashed project as the scope lists see it: listed, never selectable ("In Trash")."""
    return {"id": e["id"], "path": "", "template": e.get("template") or "", "added_at": e.get("added_at"), "storage": "trashed",
            "state": "trashed", "title": e["id"], "reason": IN_TRASH, "trash_entry": e.get("trash_entry")}


def list_projects(tenant_id: str) -> dict[str, Any]:
    loc = location_settings(tenant_id)
    ents = registry.entries(tenant_id)
    trashed = []
    for e in (x for x in ents if registry.is_trashed(x)):
        s = _trashed_summary(e)
        side = _read_sidecar(Path(str(e.get("path") or "")).parent) if e.get("path") else None
        if side is not None:
            s["title"] = str(side.get("title") or e["id"])[:120]
        trashed.append(s)
    return {**loc,
            "projects": [_builtin_first(tenant_id), *[_summary(tenant_id, e) for e in ents if not registry.is_trashed(e)]],
            "trashed": trashed, "trash_count": trash_count(tenant_id)}


def get_project(tenant_id: str, project_id: str) -> dict[str, Any]:
    e = _entry(tenant_id, project_id)
    out = _summary(tenant_id, e)
    if out["state"] != "ok":
        return out
    if e.get("builtin"):
        return {**out, "directory_peers": [], "catalogue": {"available": False, "reason": "Corvin is built in; its features are fixed"}}
    # the Location section's hint-word warning (ADR-2321 Decision 15): only an own folder can carry one, a managed path is hint-free
    out["location_warnings"] = registry.location_warnings(Path(str(e["path"]))) if out.get("storage") == "own" else []
    try:
        man = manifest.load(Path(str(e["path"])))
        out["directory_peers"] = [str(x) for x in ((man.get("directory") or {}).get("peers") or [])]
    except (manifest.ManifestError, AttributeError, TypeError):
        out["directory_peers"] = []
    try:
        cat = catalogue(tenant_id)
    except Unavailable as exc:
        return {**out, "catalogue": {"available": False, "reason": str(exc)}}
    act = out["active"]
    feats = []
    for fid in cat.order:
        f = cat.fragments[fid]
        feats.append({"id": fid, "title": f.title, "description": f.description, "active": fid in act,
                      "requires": list(f.requires), "suggests": list(f.suggests),
                      "dependants": F.dependants(cat, act, fid) if fid in act else [],
                      "plugins": [{"plugin": p, "state": plugin_state(tenant_id, p)} for p in f.requires_plugins]})
    return {**out, "catalogue": {"available": True, "features": feats,
                                 "suggestions": F.suggestions(cat, act)}}


def _load_project(tenant_id: str, project_id: str) -> tuple[Path, dict[str, Any]]:
    e = _entry(tenant_id, project_id)
    root = Path(str(e["path"]))
    if e.get("builtin"):                                    # Corvin itself: synthesised, never read from a project.yaml
        from core.projects import builtin  # noqa: PLC0415
        if not str(e["path"]) or not root.is_dir():
            raise Conflict("Corvin's knowledge base has not been created yet", "builtin_missing")
        return root, builtin.manifest(tenant_id)
    try:
        man = manifest.load(root)
    except manifest.ManifestError as exc:
        raise Conflict(f"project.yaml is not usable: {exc}", "manifest_invalid") from None
    if man.get("tenant") != tenant_id or man.get("id") != project_id:
        raise Conflict("project.yaml does not match the registry entry", "manifest_invalid")
    return root, man


@_project_locked
def set_features(tenant_id: str, project_id: str, changes: dict[str, bool], *, accept_dependencies: bool = False,
                 cascade: bool = False) -> dict[str, Any]:
    _refuse_builtin(project_id)
    root, man = _load_project(tenant_id, project_id)
    cat = catalogue(tenant_id)
    unknown = [f for f in changes if f not in cat.fragments]
    if unknown:
        raise ProjectsError(f"unknown feature(s): {unknown}")
    if changes.get("core") is False:
        raise Conflict("core cannot be switched off", "core_required")
    active = set(manifest.active_features(man))
    want = set(active)
    for f, on in changes.items():
        (want.add if on else want.discard)(f)
    # switching off: a feature that an active one requires keeps its dependants unless cascaded
    off = [f for f, on in changes.items() if not on and f in active]
    cascade_list: list[str] = []
    for f in off:
        deps = [d for d in F.dependants(cat, want | {f}, f) if d in want]
        if deps and not cascade:
            raise Conflict(f"{f} is required by: {', '.join(deps)}", "has_dependants", feature=f, dependants=deps)
        cascade_list += deps
        for d in deps:
            want.discard(d)
    # switching on: requires are pulled in, only with consent
    closed = set(F.closure(cat, sorted(want)))
    pulled = sorted(closed - want)
    if pulled and not accept_dependencies:
        raise Conflict(f"switching these on also needs: {', '.join(pulled)}", "needs_dependencies", requires=pulled)
    want = closed
    added = sorted(want - active)
    removed = sorted(active - want)
    if not added and not removed:
        return get_project(tenant_id, project_id)
    ctx = _context(tenant_id, project_id, man.get("title", project_id), root)
    plan = F.build_plan(cat, root, sorted(want), ctx, docs_mode=man.get("docs_mode", "nested"), mode="repair")
    if added:
        _require_plugins(tenant_id, F.build_plan(cat, root, added, ctx, docs_mode=man.get("docs_mode", "nested"),
                                                 mode="repair"), project_id)
    if "kb" in added:
        bound_engine(tenant_id)
    if any(o.kind == "action" and o.action in ("git_init", "git_commit") for o in plan.pending()):
        _need_git()
    for f in [*added, *removed]:                                              # audit-first, one record each
        _audit(tenant_id, "project.feature_toggled", project_id=project_id, feature=f, to="on" if f in want else "off",
               template=man.get("template"))
    before = dict(man)
    man["features"] = {**man.get("features", {}), **{f: (f in want) for f in dict.fromkeys([*man.get("features", {}), *want, *active])}}
    manifest.write(root, man)
    try:
        F.apply_plan(plan, root, {**ctx, "_root": str(root)}, _runner(tenant_id, root))
    except Exception as exc:  # noqa: BLE001 - project.yaml must not claim a feature that was never applied
        manifest.write(root, before)
        if isinstance(exc, ProjectsError):
            raise
        raise Conflict(f"the change could not be applied ({type(exc).__name__}); project.yaml was left as it was", "apply_failed") from None
    _sync_mcp(tenant_id, project_id, root, want)
    out = get_project(tenant_id, project_id)
    out["warnings"] = external_warnings(tenant_id, sorted(want)) if "acs" in added else []
    return out


def _repair_marker(tenant_id: str, project_id: str, root: Path, *, dry_run: bool) -> Optional[str]:
    """A managed project whose workspace has no ``workspace.json``: repair writes it (and nothing else new). Audit-first."""
    e = registry.get(tenant_id, project_id) or {}
    mr = registry.known_managed_root(tenant_id)
    if (e.get("storage") or registry.storage_of(tenant_id, root)) != "managed" or mr is None or (mr / registry.MARKER).exists():
        return None
    if dry_run:
        return "pending"
    _audit(tenant_id, "project.repaired", project_id=project_id, files=1, storage="managed")
    _workspace(tenant_id)
    return "written"


@_project_locked
def repair(tenant_id: str, project_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    _refuse_builtin(project_id)
    e = _entry(tenant_id, project_id)
    if e.get("pending") == "import" and not _apply_running(tenant_id, project_id):
        # this call holds the project lock, so no apply of this id runs in any process: the entry is an orphan (T-0412)
        if dry_run:
            return {"dry_run": True, "pending": [{"path": "Remove the unfinished import", "status": "new", "diff": ""}]}
        return _remove_unfinished_import(tenant_id, project_id, e)
    root, man = _load_project(tenant_id, project_id)
    marker_written = _repair_marker(tenant_id, project_id, root, dry_run=dry_run)
    cat = catalogue(tenant_id)
    ctx = _context(tenant_id, project_id, man.get("title", project_id), root)
    plan = F.build_plan(cat, root, manifest.active_features(man), ctx, docs_mode=man.get("docs_mode", "nested"),
                        mode="repair")
    pending = plan.pending()
    summary = {**plan.summary(), "pending": [{"path": o.path or o.action, "status": o.status, "diff": o.diff}
                                             for o in pending], "dry_run": dry_run, "marker": marker_written}
    if dry_run or not pending:
        return summary
    _require_plugins(tenant_id, plan, project_id)
    if any(o.kind == "action" and o.action in ("git_init", "git_commit") for o in pending):
        _need_git()
    _audit(tenant_id, "project.repaired", project_id=project_id, template=man.get("template"), files=len(plan.pending()))
    try:
        res = F.apply_plan(plan, root, {**ctx, "_root": str(root)}, _runner(tenant_id, root))
    except (changes_error(), OSError, subprocess.TimeoutExpired) as exc:
        raise Conflict(f"repair stopped: {str(exc)[:160]}", "git_unguarded") from None
    _sync_mcp(tenant_id, project_id, root, manifest.active_features(man))
    return {**summary, "written": res["written"], "actions": res["actions"]}


def open_project(tenant_id: str, project_id: str) -> dict[str, Any]:
    e = _entry(tenant_id, project_id)
    summary = _summary(tenant_id, e)
    if summary["state"] != "ok":
        raise Conflict(f"the project is {summary['state']}: {summary.get('reason', '')}", "not_openable")
    _audit(tenant_id, "project.opened", project_id=project_id, template=summary.get("template") or None)
    path = summary["path"]
    commands = shell_commands(path)
    return {"project": summary, "command": commands["powershell"] if os.name == "nt" else commands["posix"], "commands": commands,
            "chat_attachment": True,
            "note": "Opening shows the command for your own terminal. To use the project in a console chat, attach it to that chat."}


#: PowerShell treats the typographic single quotes as quote characters too; each one is doubled like '
_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def shell_commands(path: str) -> dict[str, Optional[str]]:
    """``cd`` into *path* for PowerShell, cmd.exe and a POSIX shell, each quoted so that nothing in a folder name is evaluated:
    PowerShell gets a single-quoted literal path (``$( )``, ``$var`` and backticks are text there, unlike in double quotes), a POSIX
    shell a single-quoted word. cmd.exe expands ``%VAR%`` (and ``!VAR!``) even inside quotes and has no way to escape it there, so a
    name containing either gets no cmd command at all (None) instead of one that runs something else."""
    ps = "Set-Location -LiteralPath '" + "".join(ch * 2 if ch in _PS_QUOTES else ch for ch in path) + "'"
    posix = "cd -- '" + path.replace("'", "'\\''") + "'"
    cmd = None if any(ch in path for ch in '%!"\r\n') else f'cd /d "{path}"'
    return {"powershell": ps, "cmd": cmd, "posix": posix}


def remove_from_registry(tenant_id: str, project_id: str) -> dict[str, Any]:
    _refuse_builtin(project_id)
    _entry(tenant_id, project_id)
    _audit(tenant_id, "project.registry_removed", project_id=project_id)
    registry.unregister(tenant_id, project_id)
    from core.projects import attachment  # noqa: PLC0415
    attachment.detach_project(tenant_id, project_id)          # a removed project is attached to no chat (release review R2)
    return {"removed": project_id, "files_deleted": False}


# ── trash (ADR-2321 Decisions 6, 12; PLAN-0972 phase 5, T-0404) ─────────────────────────────────────────────────────────
# Only a MANAGED project's files can be deleted, and only to the trash: a same-disk rename into
# ``<managed_root>/.trash/<id>-<YYYYMMDDTHHMMSSZ>/project/`` next to the sidecar ``trash.json`` (0o600) of that entry. The
# entry directory is self-contained, so one rmtree removes it and a project file called ``trash.json`` is never overwritten.
# An ``own`` project has no delete path at all. Restore renames back when ``<managed_root>/<id>`` is free; permanent delete
# and the 30-day sweep remove an entry with ``shutil.rmtree`` on a path asserted to lie inside ``.trash/`` (no link on the
# way, fd-based on POSIX, a junction removed without descending on Windows).

TRASH_DIR = ".trash"
TRASH_TTL_S = 30 * 86400.0
TRASH_SWEEP_LIMIT = 10                       # entries removed per projector tick
TRASH_SCAN_LIMIT = 200                       # sidecars read per listing page / sweep tick (REVIEW-0063 m6: never a name window)
TRASH_NAME_CAP = 100_000                     # names of .trash looked at at all (a bound on memory, far above any real trash)
_TRASH_SIDECAR = "trash.json"
_TRASH_FILES = "project"
_TRASH_ENTRY_RE = re.compile(r"^([a-z0-9][a-z0-9-]{0,27})-(\d{8}T\d{6}Z)(?:-([2-9]|[1-9]\d))?$")
NOT_MANAGED = "Only projects in the Corvin workspace can be deleted here; this one lives in your own folder."
CONFIRM_MISMATCH = "Type the project id exactly to confirm."
IN_TRASH = "In Trash"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _trash_root(tenant_id: str, *, create: bool = False) -> Optional[Path]:
    """``<managed_root>/.trash`` of this tenant (0o700, never a link), or None when the tenant has no workspace yet."""
    mr = registry.known_managed_root(tenant_id)
    if mr is None:
        return None
    t = mr / TRASH_DIR
    if is_link(mr) or is_link(t):
        raise UnsafePath("the trash directory is a link")
    if create:
        t.mkdir(mode=0o700, exist_ok=True)
        if is_link(t):
            raise UnsafePath("the trash directory is a link")
        with contextlib.suppress(OSError):
            os.chmod(t, 0o700)
    return t


def _trash_entry_dir(tenant_id: str, entry: str) -> Path:
    """The entry's directory, asserted to lie directly inside ``.trash`` with no link on the way (422 ``path_rule`` otherwise,
    404 when there is no such entry)."""
    from core.projects.fsutil import ensure_inside  # noqa: PLC0415
    if not isinstance(entry, str) or not _TRASH_ENTRY_RE.match(entry):
        _audit_refusal(tenant_id, "path_rule")
        raise registry.ProjectPathError("not a trash entry", reason_class="path_rule")
    t = _trash_root(tenant_id)
    if t is None or not t.is_dir():
        raise NotFound("no such trash entry")
    d = t / entry
    try:
        d = ensure_inside(t, d)
    except UnsafePath:
        _audit_refusal(tenant_id, "path_rule")
        raise registry.ProjectPathError("the trash entry is a link", reason_class="path_rule") from None
    if not d.is_dir():
        raise NotFound("no such trash entry")
    return d


def _entry_sidecar(d: Path) -> Optional[dict[str, Any]]:
    """The sidecar of a trash entry, only when its ``id`` is the id the entry's NAME carries (REVIEW-0063 m8): a planted entry
    cannot restore, purge or be swept under another project's id."""
    m = _TRASH_ENTRY_RE.match(d.name)
    side = _read_sidecar(d)
    if m is None or side is None or side["id"] != m.group(1):
        return None
    return side


def _read_sidecar(d: Path) -> Optional[dict[str, Any]]:
    p = d / _TRASH_SIDECAR
    if is_link(p):
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not manifest.PROJECT_ID_RE.match(data["id"]):
        return None
    return data


def _write_sidecar(d: Path, data: dict[str, Any]) -> None:
    from core.projects.fsutil import atomic_write  # noqa: PLC0415
    atomic_write(d / _TRASH_SIDECAR, (json.dumps(data, sort_keys=True, indent=1) + "\n").encode("utf-8"), mode=0o600, owned=True)


def _trash_row(entry: str, side: dict[str, Any]) -> dict[str, Any]:
    ts = side.get("deleted_ts")
    ts = float(ts) if isinstance(ts, (int, float)) else None
    return {"entry": entry, "id": side["id"], "title": str(side.get("title") or side["id"])[:120],
            "deleted_at": side.get("deleted_at"), "restorable_until": _iso(ts + TRASH_TTL_S) if ts is not None else None,
            "pending": side.get("state") != "trashed"}


def _trash_names(tenant_id: str) -> list[tuple[str, Path]]:
    """``(name timestamp, entry dir)`` of every directory in this tenant's ``.trash`` whose NAME is a trash entry name, oldest
    first (REVIEW-0063 m6: only matching names count - junk names never fill a window; the order is the deletion time the
    name carries, not the directory's order). Links and junctions are skipped; no sidecar is read here. At most
    ``TRASH_NAME_CAP`` names are looked at."""
    try:
        t = _trash_root(tenant_id)
    except UnsafePath:
        return []
    if t is None or not t.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    try:
        with os.scandir(t) as it:
            for _n, e in zip(range(TRASH_NAME_CAP), it):
                m = _TRASH_ENTRY_RE.match(e.name)
                if m is None or e.is_symlink() or dirent_is_junction(e) or not e.is_dir(follow_symlinks=False):
                    continue
                out.append((m.group(2), Path(e.path)))
    except OSError:
        return out
    out.sort(key=lambda x: (x[0], x[1].name))
    return out


def _trash_scan(tenant_id: str, *, newest_first: bool = False, offset: int = 0,
                limit: int = TRASH_SCAN_LIMIT) -> list[tuple[Path, dict[str, Any]]]:
    """(entry dir, sidecar) of this tenant's trash in the order of the names' deletion time, the sidecars of at most *limit*
    names from *offset* read; an unreadable sidecar or one whose id is not the name's (:func:`_entry_sidecar`) is skipped."""
    names = _trash_names(tenant_id)
    if newest_first:
        names.reverse()
    out: list[tuple[Path, dict[str, Any]]] = []
    for _ts, d in names[max(offset, 0):max(offset, 0) + max(limit, 0)]:
        side = _entry_sidecar(d)
        if side is not None:
            out.append((d, side))
    return out


def trash_count(tenant_id: str) -> int:
    """How many entries the trash holds (matching names; no sidecar is read)."""
    return len(_trash_names(tenant_id))


def list_trash(tenant_id: str, *, offset: int = 0, limit: int = TRASH_SCAN_LIMIT) -> dict[str, Any]:
    """``GET /projects/-/trash[?offset=&limit=]``: newest first (by the deletion time in the entry name), one page of at most
    ``TRASH_SCAN_LIMIT`` entries; ``total`` counts every entry, ``next_offset`` is None on the last page."""
    offset = max(int(offset), 0)
    limit = min(max(int(limit), 1), TRASH_SCAN_LIMIT)
    total = trash_count(tenant_id)
    rows = [_trash_row(d.name, side) for d, side in _trash_scan(tenant_id, newest_first=True, offset=offset, limit=limit)]
    nxt = offset + limit if offset + limit < total else None
    return {"entries": rows, "ttl_days": int(TRASH_TTL_S // 86400), "total": total, "offset": offset, "limit": limit,
            "next_offset": nxt}


def _trash_name(t: Path, project_id: str, ts: float) -> str:
    base = f"{project_id}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(ts))}"
    if not (t / base).exists():
        return base
    for n in range(2, 100):
        if not (t / f"{base}-{n}").exists():
            return f"{base}-{n}"
    raise Conflict("too many deletions of this project in one second; try again", "busy")


def _rename_retrying(src: Path, dst: Path) -> None:
    for attempt in range(8):
        try:
            os.rename(src, dst)
            return
        except PermissionError:
            # Windows refuses to rename a folder another process uses as its working directory for a moment (see move_project)
            if attempt == 7:
                raise
            time.sleep(0.25)


@_project_locked
def delete_project_files(tenant_id: str, project_id: str, *, confirm_id: str) -> dict[str, Any]:
    """``DELETE /projects/{id}/files``: move a MANAGED project's folder to the trash (restorable for 30 days)."""
    _refuse_builtin(project_id)
    e = _entry(tenant_id, project_id)
    if (e.get("storage") or "own") != "managed" or e.get("pending"):
        _audit_refusal(tenant_id, "not_managed", project_id)
        raise Conflict(NOT_MANAGED, "not_managed")
    if confirm_id != project_id:
        _audit_refusal(tenant_id, "confirm_mismatch", project_id)
        raise Conflict(CONFIRM_MISMATCH, "confirm_mismatch")
    root = Path(str(e["path"]))
    mr = registry.known_managed_root(tenant_id)
    if mr is None or registry._resolve_forgiving(root).parent != registry._resolve_forgiving(mr) or is_link(root):
        _audit_refusal(tenant_id, "not_managed", project_id)
        raise Conflict(NOT_MANAGED, "not_managed")
    if not root.is_dir():
        raise Conflict("the project folder is missing; remove the project from the list instead", "missing")
    t = _trash_root(tenant_id, create=True)
    assert t is not None
    now = _now()
    name = _trash_name(t, project_id, now)
    title = project_id
    with contextlib.suppress(manifest.ManifestError, OSError):
        title = str(manifest.load(root).get("title") or project_id)[:120]
    _audit(tenant_id, "project.deleted", project_id=project_id, outcome="requested")
    d = t / name
    d.mkdir(mode=0o700)
    side = {"schema": 1, "id": project_id, "title": title, "deleted_at": _iso(now), "deleted_ts": now,
            "original_path": str(root), "state": "pending"}
    try:
        _write_sidecar(d, side)
        _rename_retrying(root, d / _TRASH_FILES)
    except OSError as exc:
        shutil.rmtree(d, ignore_errors=True)                         # only the sidecar and an empty entry: the folder did not move
        _audit_tenant_error(tenant_id, project_id)
        if exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17:
            _audit_refusal(tenant_id, "cross_device", project_id)
            raise Conflict(CROSS_DEVICE, "cross_device") from None
        raise Conflict(f"the folder could not be moved to the trash ({type(exc).__name__}); nothing changed", "move_failed") from None
    try:
        registry.set_trashed(tenant_id, project_id, d / _TRASH_FILES, name)
    except Exception:  # noqa: BLE001 - the list did not follow: put the folder back
        with contextlib.suppress(OSError):
            _rename_retrying(d / _TRASH_FILES, root)
            shutil.rmtree(d, ignore_errors=True)
        _audit_tenant_error(tenant_id, project_id)
        raise Conflict("the list could not be updated, so the folder was moved back; nothing changed", "move_failed") from None
    warnings: list[str] = []
    with contextlib.suppress(OSError):
        _write_sidecar(d, {**side, "state": "trashed"})
    from core.projects import attachment  # noqa: PLC0415
    try:
        attachment.detach_project(tenant_id, project_id)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"the chats attached to this project could not be detached ({type(exc).__name__})")
    try:
        _audit(tenant_id, "project.deleted", project_id=project_id, outcome="applied")
    except _tt.AuditUnavailable:
        warnings.append("the audit chain was unavailable for the 'applied' record")
    return {"project_id": project_id, "trash_entry": name, "deleted_at": side["deleted_at"],
            "restorable_until": _iso(now + TRASH_TTL_S), "warnings": warnings}


def _audit_tenant_error(tenant_id: str, project_id: str) -> None:
    with contextlib.suppress(Exception):
        _tt._chain(tenant_id, "project.deleted", {"project_id": project_id, "outcome": "error"})


def restore_project(tenant_id: str, entry: str) -> dict[str, Any]:
    """``POST /projects/-/trash/{entry}/restore``: rename the folder back to ``<managed_root>/<id>`` when that place is free."""
    d = _trash_entry_dir(tenant_id, entry)
    side = _entry_sidecar(d)
    if side is None:
        raise NotFound("no such trash entry")
    return _restore_locked(tenant_id, side["id"], entry)


@_project_locked
def _restore_locked(tenant_id: str, project_id: str, entry: str) -> dict[str, Any]:
    d = _trash_entry_dir(tenant_id, entry)                          # again under the lock
    side = _entry_sidecar(d)
    if side is None or side["id"] != project_id:
        raise NotFound("no such trash entry")
    files = d / _TRASH_FILES
    if is_link(files) or not files.is_dir():
        raise Conflict("this trash entry holds no project folder", "missing")
    e = registry.get(tenant_id, project_id)
    if e is not None and not registry.is_trashed(e):
        raise Conflict("another project already uses this id; remove it from the list first", "id_taken")
    if e is not None and e.get("trash_entry") != entry:
        # REVIEW-0063 m8: the list names ANOTHER trash entry for this id - this one is not that project's
        _audit_refusal(tenant_id, "trash_entry_mismatch", project_id)
        raise Conflict("another trash entry holds this project; restore that one", "id_taken")
    mr = registry.known_managed_root(tenant_id)
    assert mr is not None
    target = mr / project_id
    if target.exists() or is_link(target):
        _audit_refusal(tenant_id, "folder_not_empty", project_id)
        raise Conflict(f"A folder named {project_id} is already in the Corvin workspace; move it away, then restore.",
                       "folder_not_empty")
    _audit(tenant_id, "project.restored", project_id=project_id, outcome="requested")
    try:
        _rename_retrying(files, target)
    except OSError as exc:
        with contextlib.suppress(Exception):
            _tt._chain(tenant_id, "project.restored", {"project_id": project_id, "outcome": "error"})
        raise Conflict(f"the folder could not be restored ({type(exc).__name__}); nothing changed", "move_failed") from None
    try:
        registry.set_restored(tenant_id, project_id, target)
    except Exception:  # noqa: BLE001 - the list did not follow: put the folder back into the trash
        with contextlib.suppress(OSError):
            _rename_retrying(target, files)
        with contextlib.suppress(Exception):
            _tt._chain(tenant_id, "project.restored", {"project_id": project_id, "outcome": "error"})
        raise Conflict("the list could not be updated, so the folder stays in the trash; nothing changed", "move_failed") from None
    shutil.rmtree(d, ignore_errors=True)                            # the sidecar and the now empty entry
    warnings: list[str] = []
    try:
        _audit(tenant_id, "project.restored", project_id=project_id, outcome="applied")
    except _tt.AuditUnavailable:
        warnings.append("the audit chain was unavailable for the 'applied' record")
    _record_recent(tenant_id, target)
    repaired: Optional[int] = None
    try:
        repaired = len(repair(tenant_id, project_id).get("written", []))
    except Exception:  # noqa: BLE001 - the restore stands; the doctor names what is out of sync
        repaired = None
    return {**get_project(tenant_id, project_id), "repaired_files": repaired, "warnings": warnings}


def _remove_entry(tenant_id: str, d: Path, side: dict[str, Any]) -> None:
    """Audit-first permanent removal of one trash entry; never outside ``.trash``, never through a link."""
    from core.projects.fsutil import ensure_inside  # noqa: PLC0415
    t = _trash_root(tenant_id)
    assert t is not None
    d = ensure_inside(t, d)
    if d.parent != Path(os.path.abspath(t)) or is_link(d):
        raise UnsafePath("not a trash entry")
    pid = side["id"]
    _audit(tenant_id, "project.deleted", project_id=pid, outcome="purged")
    shutil.rmtree(d)
    e = registry.get(tenant_id, pid)
    if registry.is_trashed(e) and e.get("trash_entry") == d.name:
        registry.unregister(tenant_id, pid)


def purge_trash_entry(tenant_id: str, entry: str, *, confirm_id: str) -> dict[str, Any]:
    """``DELETE /projects/-/trash/{entry}``: remove one entry for good; the typed id must match."""
    d = _trash_entry_dir(tenant_id, entry)
    side = _entry_sidecar(d)
    if side is None:
        raise NotFound("no such trash entry")
    if confirm_id != side["id"]:
        _audit_refusal(tenant_id, "confirm_mismatch", side["id"])
        raise Conflict(CONFIRM_MISMATCH, "confirm_mismatch")
    return _purge_locked(tenant_id, side["id"], entry)


@_project_locked
def _purge_locked(tenant_id: str, project_id: str, entry: str) -> dict[str, Any]:
    d = _trash_entry_dir(tenant_id, entry)
    side = _entry_sidecar(d)
    if side is None or side["id"] != project_id:
        raise NotFound("no such trash entry")
    if side.get("state") != "trashed":
        raise Conflict("this entry is still being moved to the trash; try again in a moment", "pending")
    try:
        _remove_entry(tenant_id, d, side)
    except UnsafePath:
        _audit_refusal(tenant_id, "path_rule", project_id)
        raise registry.ProjectPathError("not a trash entry", reason_class="path_rule") from None
    return {"entry": entry, "purged": True}


def sweep_trash(tenant_id: str, *, busy: bool = False, now: Optional[float] = None) -> dict[str, Any]:
    """The projector tick's trash sweep: entries older than 30 days, at most ``TRASH_SWEEP_LIMIT`` per tick, skipped while the
    host is saturated; a pending entry (a delete that has not finished) is never swept."""
    if busy:
        return {"purged": 0, "skipped": "busy"}
    now = _now() if now is None else now
    purged = 0
    reads = 0
    # REVIEW-0063 m6: candidates are the names whose deletion time is past the TTL, oldest first, from the WHOLE trash; only the
    # sidecar reads are capped per tick (TRASH_SCAN_LIMIT), so the 30-day bound holds however many entries the trash holds
    cutoff = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now - TRASH_TTL_S))
    for name_ts, d in _trash_names(tenant_id):
        if purged >= TRASH_SWEEP_LIMIT or reads >= TRASH_SCAN_LIMIT or name_ts >= cutoff:
            break
        reads += 1
        side = _entry_sidecar(d)
        if side is None:
            continue
        ts = side.get("deleted_ts")
        if side.get("state") != "trashed" or not isinstance(ts, (int, float)) or now - float(ts) <= TRASH_TTL_S:
            continue
        try:
            with _project_lock_of(tenant_id, side["id"]):
                again = _entry_sidecar(d)
                if again is None or again.get("state") != "trashed":
                    continue
                _remove_entry(tenant_id, d, again)
            purged += 1
        except Exception as exc:  # noqa: BLE001 - one entry never stops the sweep; the next tick tries again
            import logging  # noqa: PLC0415
            logging.getLogger("corvin.projects").warning("trash sweep: an entry could not be removed (%s)", type(exc).__name__)
    return {"purged": purged}


@contextlib.contextmanager
def _project_lock_of(tenant_id: str, project_id: str):
    from core.projects.fsutil import file_lock  # noqa: PLC0415
    with file_lock(registry.registry_path(tenant_id).with_name(f"project-{project_id}.lock"), timeout=20.0):
        yield


def health(tenant_id: str, project_id: str) -> dict[str, Any]:
    """A checklist of REAL checks, each with its evidence - never a score (ADR-2315 D8)."""
    e = _entry(tenant_id, project_id)
    root = Path(str(e["path"]))
    if e.get("builtin"):                                    # Corvin itself: its knowledge base exists and is consistent
        out: list[dict[str, Any]] = []
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ok_dir = bool(str(e["path"])) and root.is_dir()
        out.append({"id": "path_exists", "ok": ok_dir, "detail": "Corvin's knowledge base exists" if ok_dir else "not created yet", "at": now})
        if ok_dir:
            try:
                rc, res = kb_projection._run(root, "check", actor="operator", engine=bound_engine(tenant_id), timeout=90)
                blocking = res.get("blocking") if isinstance(res, dict) else None
                out.append({"id": "kb_check", "ok": rc == 0 and blocking == 0, "detail": f"kb check: blocking {blocking}" if blocking is not None
                            else f"kb check failed: {str(res)[:120]}", "at": now})
            except (Conflict, subprocess.TimeoutExpired, OSError, ValueError) as exc:      # ChangesError is a ValueError
                out.append({"id": "kb_check", "ok": False, "detail": str(exc)[:160], "at": now})
        return {"project_id": project_id, "ok": all(c["ok"] for c in out), "checks": out}
    checks: list[dict[str, Any]] = []

    def add(cid: str, ok: bool, detail: str) -> None:
        checks.append({"id": cid, "ok": ok, "detail": detail, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    if e.get("pending") == "import":
        # ADR-2321 Decision 17: an import that is running, or one a crash left behind (no apply holds the lock)
        stale = _pending_import_unlocked(tenant_id, project_id)
        add("pending_import", not stale, "an import is being applied right now" if not stale else
            "the list entry is marked as an unfinished import and no import is running - Repair removes the unfinished import")
        left = [p.name for p in _bundle().leftovers(root.parent, project_id)]
        add("import_leftovers", not left, "no unfinished import beside the project" if not left else
            f"unfinished import folders beside the project: {left[:3]}")
        return {"project_id": project_id, "ok": all(c["ok"] for c in checks), "checks": checks,
                "repair": "Remove the unfinished import" if stale else None}
    add("path_exists", root.is_dir(), "the project directory exists" if root.is_dir() else "the directory is missing")
    if not root.is_dir():
        return {"project_id": project_id, "ok": False, "checks": checks}
    try:
        man = manifest.load(root)
        add("manifest", man.get("tenant") == tenant_id and man.get("id") == project_id, "project.yaml is valid and matches the registry")
    except manifest.ManifestError as exc:
        add("manifest", False, str(exc))
        return {"project_id": project_id, "ok": False, "checks": checks}
    _location_checks(tenant_id, project_id, e, root, add, checks)
    prot = registry.protection(tenant_id, project_id)
    if prot:
        same = prot.get("classification") == manifest.classification_of(man) and prot.get("assistant_access") == manifest.access_of(man)
        add("protection", same, "project.yaml matches the project's protection setting" if same else
            "project.yaml was changed by hand or by the assistant: the setting still applies (save the protection in Settings to rewrite the file)")
    active = manifest.active_features(man)
    try:
        cat = catalogue(tenant_id)
        closed = F.closure(cat, active)
        add("features_closed", set(closed) == set(active), "every active feature has its requirements active"
            if set(closed) == set(active) else f"missing requirements: {sorted(set(closed) - set(active))}")
        ctx = _context(tenant_id, project_id, man.get("title", project_id), root)
        plan = F.build_plan(cat, root, active, ctx, docs_mode=man.get("docs_mode", "nested"), mode="repair")
        drift = [o.path or o.action for o in plan.pending()]
        add("generated_files_in_sync", not drift, "generated and machine-local files match the active features"
            if not drift else f"out of sync (repair fixes): {drift[:6]}")
        miss = [x for x in _check_plugins(tenant_id, plan) if x["state"] != "enabled"]
        add("tenant_plugins", not miss, "every plugin the active features need is enabled"
            if not miss else "not enabled: " + ", ".join(m["plugin"] for m in miss))
    except Unavailable as exc:
        add("catalogue", False, str(exc))
    if "acs" in active:
        up = compute_worker_reachable(tenant_id)
        add("compute_worker", up is True, "the compute worker answers on its socket" if up is True
            else "the compute worker is not reachable" if up is False else "the compute worker could not be checked here")
    if "kb" in active:
        docs = root / "docs"
        if not (docs / "kb" / "_meta").is_dir():
            add("kb_check", False, "docs/kb is missing")
        else:
            try:
                rc, out = _project_kb_run(docs, "check", actor="operator", engine=bound_engine(tenant_id), timeout=90, guard_git=True)
                blocking = out.get("blocking") if isinstance(out, dict) else None
                add("kb_check", rc == 0 and blocking == 0, f"kb check: blocking {blocking}" if blocking is not None
                    else f"kb check failed: {str(out)[:120]}")
            except (Conflict, subprocess.TimeoutExpired, OSError, ValueError) as exc:        # ChangesError is a ValueError
                add("kb_check", False, str(exc)[:160])
    return {"project_id": project_id, "ok": all(c["ok"] for c in checks), "checks": checks}


_ENGINE_WALK_DEPTH = 4
_ENGINE_WALK_MAX = 5000


def _engine_copy(root: Path) -> Optional[str]:
    """An engine copy inside the project (ADR-2316 D8): an ``ENGINE.json`` or ``docs/scripts/kb.py``. Bounded walk, never
    following a link."""
    if (root / "docs" / "scripts" / "kb.py").is_file() and not passes_link(root, root / "docs" / "scripts" / "kb.py"):
        return "docs/scripts/kb.py"
    seen = 0
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for ent in it:
                    seen += 1
                    if seen > _ENGINE_WALK_MAX:
                        return None
                    if ent.name == "ENGINE.json" and ent.is_file(follow_symlinks=False):
                        return Path(ent.path).relative_to(root).as_posix()
                    if (depth < _ENGINE_WALK_DEPTH and ent.name not in (".git", "node_modules")
                            and ent.is_dir(follow_symlinks=False) and not dirent_is_junction(ent)):
                        stack.append((Path(ent.path), depth + 1))
        except OSError:
            continue
    return None


def _apply_running(tenant_id: str, project_id: str) -> bool:
    """True inside the apply of this very project (the apply's own repair step runs re-entrantly under its lock)."""
    return (tenant_id, project_id) in getattr(_applying, "keys", set())


_applying = threading.local()


def _pending_import_unlocked(tenant_id: str, project_id: str) -> bool:
    """True when no apply holds the project's lock (an import that is still running holds it)."""
    from core.projects.fsutil import LockBusy, file_lock  # noqa: PLC0415
    held = getattr(_held, "keys", set())
    if (tenant_id, project_id) in held:
        return False
    try:
        with file_lock(registry.registry_path(tenant_id).with_name(f"project-{project_id}.lock"), timeout=0.05):
            return True
    except LockBusy:
        return False


def _location_checks(tenant_id: str, project_id: str, e: dict[str, Any], root: Path, add, checks: list) -> None:
    """ADR-2321 Decision 17: storage_marker, path_under_base, no_engine_copy, import_leftovers, pending_import, path_gate_hint (a
    note) and hint_source."""
    from core.projects import hints  # noqa: PLC0415
    storage = e.get("storage") or registry.storage_of(tenant_id, root)
    mr = registry.known_managed_root(tenant_id)
    if storage == "managed":
        state = registry.workspace_state(tenant_id)
        marker = mr is not None and (mr / registry.MARKER).is_file()
        if not marker:
            add("storage_marker", False, "the workspace marker workspace.json is missing - Repair writes the workspace marker")
        elif state == "foreign":
            add("storage_marker", False, "the workspace marker names another installation or tenant - start a new workspace in Settings")
        else:
            add("storage_marker", True, "the workspace marker names this tenant, this workspace and this installation")
    actual = registry.storage_of(tenant_id, root, managed=mr) if mr is not None else "own"
    problems = []
    if storage == "managed" and actual != "managed":
        problems.append("the list says Corvin workspace but the folder lies outside it")
    if storage == "own" and actual == "managed":
        problems.append("the list says own folder but the folder lies in the Corvin workspace")
    stored = registry.settings(tenant_id)["projects_root"]
    if stored:
        try:
            registry.validate_location(tenant_id, stored, purpose="projects_root")
        except registry.ProjectPathError as exc:
            problems.append(f"the projects folder would be refused by the current location rule ({exc.reason_class})")
    add("path_under_base", not problems, "; ".join(problems) if problems else "the folder lies where its storage kind says")
    copy = _engine_copy(root)
    add("no_engine_copy", copy is None, "no knowledge-base engine copy inside the project" if copy is None else
        f"an engine copy ({copy}) is in the project - not repairable: remove ENGINE.json from the project; the tenant engine is used")
    left = [p.name for p in _bundle().leftovers(root.parent, project_id)]
    add("import_leftovers", not left, "no unfinished import beside the project" if not left else
        f"unfinished import folders beside the project: {left[:3]}")
    if e.get("pending") == "import":
        stale = _pending_import_unlocked(tenant_id, project_id)
        add("pending_import", not stale, "an import is being applied right now" if not stale else
            "the list entry is marked as an unfinished import and no import is running")
    else:
        add("pending_import", True, "no unfinished import is recorded")
    why = hints.available()
    add("hint_source", why is None, "the path gate's hint test is loadable" if why is None else
        f"{why} - managed creates are refused until it is fixed")
    if why is None:
        words = hints.hint_words(str(root))
        if words:
            checks.append({"id": "path_gate_hint", "ok": True, "note": True,
                           "detail": f"the path contains '{words[0]}': the assistant may refuse some shell commands here",
                           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


# ── videos (T-0361, CONCEPT-0126 §5.6) ───────────────────────────────────────────────────────

VIDEO_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
VIDEO_SECTIONS = ("Videoeigenschaften", "Inhalt", "Reproduktion", "Konfiguration", "Pipeline",
                  "Quellen im Codebase", "Bekannte Einschränkungen", "Verwandte ADRs/Konzepte")


def _videos_root(tenant_id: str, root: Path, man: dict[str, Any]) -> Path:
    """Where a project's videos go: ``videos.root`` of project.yaml, relative to the project or absolute.
    Refused inside CORVIN_HOME (the platform's own stores) and under the tenants directory."""
    cfg = man.get("videos") if isinstance(man.get("videos"), dict) else {}
    raw = str(cfg.get("root") or "../Corvin-Videos")
    if "\x00" in raw or len(raw) > 500:
        raise ProjectsError("videos.root is not a usable path")
    try:
        raw = registry.normalize_input(raw, "videos.root")     # a network or device path is refused before anything touches it
    except registry.ProjectPathError as exc:
        raise ProjectsError(str(exc)) from None
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = root / target
    target = Path(os.path.abspath(target))
    # lexical containment FIRST: resolving walks the path with exists() calls, which must never reach a folder outside the rule
    lex_project = Path(os.path.abspath(root))
    pr_given = registry.projects_root(tenant_id)
    lex_roots = {Path(os.path.abspath(pr_given))}
    if os.name == "nt" and str(lex_project).startswith("\\\\"):
        lex_roots.add(registry._resolve_forgiving(pr_given))    # a mapped drive: the project is stored in its UNC form
    if not (target == lex_project or lex_project in target.parents or any(r in target.parents for r in lex_roots)):
        raise ProjectsError("videos.root must lie inside the project or inside your projects folder")
    resolved = registry._resolve_forgiving(target)
    home = registry._resolve_forgiving(corvin_home())
    if resolved == home or home in resolved.parents or resolved in home.parents:
        raise ProjectsError("videos.root must not be inside the CorvinOS home")
    # project.yaml is a file the assistant (or an adopted folder) controls: the videos folder may only lie inside the project or
    # inside this tenant's projects folder, reached without a link, and never inside ANOTHER registered project - otherwise
    # "New video" would write into any folder the console can reach (adversarial review 2026-11-10)
    proot = registry._resolve_forgiving(root)
    pr = registry._resolve_forgiving(registry.projects_root(tenant_id))
    inside_project = resolved == proot or proot in resolved.parents
    inside_root = pr in resolved.parents or any(r in resolved.parents for r in lex_roots)
    if not (inside_project or inside_root):
        raise ProjectsError("videos.root must lie inside the project or inside your projects folder")
    if passes_link(Path(os.path.abspath(root)) if inside_project else Path(os.path.abspath(registry.projects_root(tenant_id))), target):
        raise ProjectsError("videos.root must not be, or lie below, a link (symlink or junction)")
    if not inside_project and registry.nesting_conflict(resolved, ignore=proot):
        raise ProjectsError("videos.root must not lie inside another project")
    return resolved


def _append_index_row(root: Path, index: Path, row: str) -> None:
    """Add one row to videos/INDEX.md the way the file is written: its encoding (UTF-8, UTF-8 with BOM, UTF-16), its line ending, and
    on a line of its own even when the last line has no newline (Notepad, VS Code). Atomic; an unreadable file is refused."""
    from core.projects.fsutil import atomic_write, read_text_any  # noqa: PLC0415
    from core.projects.fsutil import read_bytes_limited  # noqa: PLC0415
    try:
        raw = read_bytes_limited(index)
        text = read_text_any(index, limit=1024 * 1024)
    except (UnicodeError, ValueError):
        raise ProjectsError("videos/INDEX.md is not UTF-8 or UTF-16 text of at most 1 MB; save it as UTF-8 and try again") from None
    nl = "\r\n" if "\r\n" in text else "\n"
    if text and not text.endswith(("\n", "\r")):
        text += nl
    text += row + nl
    if raw.startswith(b"\xfe\xff"):
        data = b"\xfe\xff" + text.encode("utf-16-be")
    elif raw.startswith(b"\xff\xfe"):
        data = text.encode("utf-16")
    elif raw.startswith(b"\xef\xbb\xbf"):
        data = text.encode("utf-8-sig")
    else:
        data = text.encode("utf-8")
    atomic_write(index, data, root=root)


def _kb_initiative(root: Path) -> Optional[str]:
    """The id of the project's first initiative (the one the seed created), or None."""
    d = root / "docs" / "kb" / "initiatives"
    if passes_link(root, d):
        return None
    for p in sorted(d.glob("*.md")):
        if is_link(p):
            continue
        try:
            from core.projects.fsutil import read_text_any  # noqa: PLC0415
            m = re.search(r"^id:\s*(\S+)\s*$", read_text_any(p, limit=256 * 1024), re.M)
        except (OSError, ValueError):
            continue
        if m:
            return m.group(1)
    return None


@_project_locked
def new_video(tenant_id: str, project_id: str, name: str) -> dict[str, Any]:
    _refuse_builtin(project_id)
    """Create one video's folder (source/, build/, sources.md), a row in the project's videos/INDEX.md and,
    when the knowledge base is on, the epic VIDEO-<name> with the narration and render tasks per language.
    Audit-first. Nothing is overwritten: an existing folder is refused."""
    root, man = _load_project(tenant_id, project_id)
    if not manifest.active_features(man) or "videos" not in manifest.active_features(man):
        raise Conflict("the videos feature is off for this project - switch it on first", "feature_off", feature="videos")
    name = (name or "").strip()
    if not VIDEO_NAME_RE.match(name):
        raise ProjectsError("the video name must be lowercase kebab-case, 1-60 characters")
    cfg = man.get("videos") if isinstance(man.get("videos"), dict) else {}
    langs = [x for x in (cfg.get("languages") or ["de", "en"]) if isinstance(x, str)]
    vroot = _videos_root(tenant_id, root, man)
    vdir = vroot / name
    if vdir.exists() or is_link(vdir):
        raise Conflict("a video with this name already exists in the videos folder", "exists")
    kb_on = "kb" in manifest.active_features(man)
    if kb_on:
        bound_engine(tenant_id)            # refuse before anything is written when there is no engine for the KB
    _idx = root / "videos" / "INDEX.md"
    if not passes_link(root, _idx) and _idx.is_file():
        from core.projects.fsutil import read_text_any  # noqa: PLC0415
        try:
            read_text_any(_idx, limit=1024 * 1024)
        except (UnicodeError, ValueError, OSError):
            raise ProjectsError("videos/INDEX.md is not UTF-8 or UTF-16 text; save it as UTF-8 and try again") from None
    _audit(tenant_id, "project.video_created", project_id=project_id, outcome="requested", files=0)
    created: list[Path] = []
    epic_id: Optional[str] = None
    tasks = []
    try:
        vroot.mkdir(parents=True, exist_ok=True)
        vdir.mkdir()
        created.append(vdir)
        for sub in ("source", "build"):
            (vdir / sub).mkdir()
            created.append(vdir / sub)
            (vdir / sub / ".gitkeep").write_text("", encoding="utf-8")
            created.append(vdir / sub / ".gitkeep")
        tpl = root / "videos" / "TEMPLATE-sources.md"
        try:
            from core.projects.fsutil import read_text_any  # noqa: PLC0415
            text = read_text_any(tpl, limit=256 * 1024) if not passes_link(root, tpl) and tpl.is_file() else ""
        except (OSError, ValueError):
            raise ProjectsError("videos/TEMPLATE-sources.md could not be read as text (UTF-8/UTF-16, up to 256 KB); restore it with Repair") from None
        if [s for s in VIDEO_SECTIONS if f"## {s}" not in text]:
            raise ProjectsError("videos/TEMPLATE-sources.md is missing its eight sections; restore it with Repair")
        body = re.sub(r"^# sources\.md - .*$", f"# sources.md - {name}", text, count=1, flags=re.M)
        (vdir / "sources.md").write_text(body, encoding="utf-8")
        created.append(vdir / "sources.md")
        tasks: list[str] = []
        if kb_on:
            docs = root / "docs"
            ini = _kb_initiative(root)
            if ini is None:
                raise ProjectsError("the project has no initiative in its knowledge base to put the video's epic under")
            rc, out = _project_kb_run(docs, "new", "epic", f"--title=VIDEO-{name}", f"--initiative={ini}",
                                         "--actor=operator", actor="operator", engine=bound_engine(tenant_id))
            if rc != 0 or not isinstance(out, dict) or not out.get("id"):
                raise ProjectsError(f"the knowledge base refused the epic: {str(out)[:160]}")
            epic_id = str(out["id"])
            plan = []
            for lang in langs:
                plan.append((f"Narration {lang.upper()}", f"the {lang.upper()} narration exists and matches the storyboard"))
                plan.append((f"Render {lang.upper()}", f"the {lang.upper()} MP4 is rendered and its ffprobe values are recorded"))
            if "de" in langs and "en" in langs:
                plan.append(("Check numbers DE against EN", "every number and date in both languages agrees"))
            plan.append(("Acceptance and sources.md", "loudness about -16 LUFS, frames sampled, duration equals the sum of the segments, sources.md has all eight sections"))
            for title, dod in plan:
                rc, t = _project_kb_run(docs, "new", "task", f"--title={title}", f"--epic={epic_id}",
                                           f"--dod={dod}", "--actor=operator", actor="operator", engine=bound_engine(tenant_id))
                if rc != 0 or not isinstance(t, dict) or not t.get("id"):
                    raise ProjectsError(f"the knowledge base refused the task {title!r}: {str(t)[:160]}")
                tasks.append(str(t["id"]))
        # the INDEX row is the LAST write: a failure before it leaves no row that a retry would trip over
        index = root / "videos" / "INDEX.md"
        if not passes_link(root, index) and index.is_file():
            row = "| {} | {} | {} | `{}` | started |".format(
                name, "yes" if "de" in langs else "-", "yes" if "en" in langs else "-", vdir)
            _append_index_row(root, index, row)
    except BaseException as exc:
        # roll back only what this call created: nothing that existed before is removed
        for path in sorted(created, key=lambda p: len(p.parts), reverse=True):
            try:
                if is_link(path) and path.is_dir():
                    path.rmdir()                       # a junction is removed as a link, its target is never touched
                elif path.is_file() or is_link(path):
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            except OSError:
                pass
        _audit(tenant_id, "project.video_created", project_id=project_id, outcome="error", files=0)
        left = [p for p in created if p.exists() or is_link(p)]
        undo = "The video folder could not be removed completely; delete it by hand." if left else "The video folder was removed again."
        kept = f" The knowledge-base epic {epic_id} and {len(tasks)} task(s) stay; cancel them in the knowledge base." if epic_id else ""
        if isinstance(exc, ProjectsError):
            exc.args = (f"{exc} {undo}{kept}",)
            raise
        raise ProjectsError(f"the video could not be created ({type(exc).__name__}). {undo}{kept}") from None
    _audit(tenant_id, "project.video_created", project_id=project_id, outcome="applied", files=len(created))
    note = ("" if kb_on else
            "No knowledge base on this project: the folder and sources.md were created; no epic or tasks.")
    return {"video": {"name": name, "path": str(vdir), "languages": langs, "kb": kb_on,
                      "epic": epic_id, "tasks": tasks}, "note": note}


# ── structure, docs and videos of one project (T-0359) ──────────────────────────────────────

_STRUCTURE_SKIP = frozenset({".git", "__pycache__", "node_modules", ".venv"})
_STRUCTURE_MAX = 400


def structure(tenant_id: str, project_id: str, depth: int = 2) -> dict[str, Any]:
    """What is in the project folder, for the inspector's Structure, Docs and Videos tabs. Read-only; symlinks are
    listed but never followed; a directory listing stops at a fixed number of entries."""
    root, man = _load_project(tenant_id, project_id)
    depth = max(1, min(int(depth), 4))
    entries: list[dict[str, Any]] = []
    truncated = False

    def walk(d: Path, rel: str, level: int) -> None:
        nonlocal truncated
        try:
            children = _bounded_children(d)                                                   # a link is judged before it is stat-ed
        except OSError:
            return
        for c in children:
            if truncated:
                return
            if c.name in _STRUCTURE_SKIP:
                continue
            r = f"{rel}/{c.name}" if rel else c.name
            if len(entries) >= _STRUCTURE_MAX:
                truncated = True
                return
            if is_link(c):
                entries.append({"path": r, "kind": "symlink", "size": None})
                continue
            if c.is_dir():
                entries.append({"path": r, "kind": "dir", "size": None})
                if level < depth:
                    walk(c, r, level + 1)
            else:
                try:
                    size = c.stat().st_size
                except OSError:
                    size = None
                entries.append({"path": r, "kind": "file", "size": size})

    walk(root, "", 1)
    docs_dir = root / "docs" / "kb"
    kb_counts: dict[str, int] = {}

    def _count(d: Path) -> int:
        if passes_link(root, d) or not d.is_dir():
            return 0
        n = 0
        try:
            with os.scandir(d) as it:                   # bounded: a folder with a million files must not stall a worker
                for k, e in enumerate(it):
                    if k >= 20000:
                        break
                    if e.name.lower().endswith(".md") and not (e.is_symlink() or dirent_is_junction(e)):
                        n += 1
        except OSError:
            return 0
        return n

    if not passes_link(root, docs_dir) and docs_dir.is_dir():
        for sub in ("initiatives", "epics", "tasks"):
            kb_counts[sub] = _count(docs_dir / sub)
    for sub in ("concepts", "decisions", "ideas", "implementation-plans", "notes", "adversarial-reviews"):
        kb_counts[sub] = _count(root / "docs" / sub)
    try:
        vroot: Optional[Path] = _videos_root(tenant_id, root, man)
    except ProjectsError:
        vroot = None                    # a videos.root the rules refuse: the structure still shows; no row is looked up anywhere
    return {"project_id": project_id, "entries": entries, "truncated": truncated, "kb_counts": kb_counts,
            "videos": _video_rows(root, man, vroot), "videos_root": str(vroot) if vroot is not None else ""}


def _bounded_children(d: Path, limit: int = _STRUCTURE_MAX * 4) -> list[Path]:
    """The entries of *d*, directories first, but never more than *limit* are listed or stat-ed (a folder with a million files)."""
    out: list[tuple[bool, str, Path]] = []
    with os.scandir(d) as it:
        for k, e in enumerate(it):
            if k >= limit:
                break
            isd = e.is_dir(follow_symlinks=False) and not dirent_is_junction(e)
            out.append((not isd, e.name, Path(e.path)))
    out.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in out]


def _video_rows(root: Path, man: dict[str, Any], vroot: Optional[Path] = None) -> list[dict[str, Any]]:
    """The rows of videos/INDEX.md (one per video), each with whether its folder still exists. ``folder_exists`` is only ever
    looked up for a folder inside the project's videos folder: the index is a file the assistant can edit, and a row naming an
    arbitrary path must not turn the inspector into a probe of what exists on this machine."""
    index = root / "videos" / "INDEX.md"
    if passes_link(root, index) or not index.is_file():
        return []
    from core.projects.fsutil import read_text_any  # noqa: PLC0415
    try:
        lines = read_text_any(index, limit=1024 * 1024).splitlines()        # UTF-8 (BOM) or UTF-16, the encodings "New video" keeps
    except (OSError, UnicodeError, ValueError):
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| video "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        name, de, en, folder, state = cells[0], cells[1], cells[2], cells[3].strip("`"), cells[4]
        exists = False
        if folder and vroot is not None and "\x00" not in folder and len(folder) <= 500:
            try:
                # lexically inside the videos folder BEFORE any filesystem call: a row naming \\host\share must never make the
                # console open an SMB connection (it would hand the host account's NTLM hash to that host)
                # the videos folder is trusted (judged by _videos_root); a row is only looked up when it lies lexically inside it
                lex = Path(os.path.abspath(folder))
                if lex == vroot or vroot in lex.parents:
                    fp = registry._resolve_forgiving(lex)
                    exists = (fp == vroot or vroot in fp.parents) and not passes_link(vroot, lex) and fp.is_dir()
            except (OSError, ValueError):
                exists = False
        rows.append({"name": name, "de": de == "yes", "en": en == "yes", "folder": folder, "state": state, "folder_exists": exists})
    return rows


# ── chat attachment (T-0364, ADR-2317) ───────────────────────────────────────────────────────

def _require_chat(tenant_id: str, session_id: str) -> None:
    from . import chat_runtime  # noqa: PLC0415 - heavy import, only on this path
    from core.projects import attachment  # noqa: PLC0415
    if not attachment.SID_RE.match(session_id or "") or chat_runtime.get_session(tenant_id, session_id) is None:
        raise NotFound("no such chat")


def attach_chat(tenant_id: str, project_id: str, session_id: str) -> dict[str, Any]:
    """Attach the project to one chat of this tenant. The project is never the chat's cwd."""
    from core.projects import attachment  # noqa: PLC0415
    e = _entry(tenant_id, project_id)
    if _summary(tenant_id, e)["state"] != "ok":
        raise Conflict("only a project in state ok can be attached", "not_openable")
    _require_chat(tenant_id, session_id)
    root_, man = _load_project(tenant_id, project_id)
    refusal = _class_refusal(tenant_id, _protection_of(tenant_id, project_id, man)["classification"])
    if refusal:
        _audit_refusal(tenant_id, "classification", project_id)
        raise Conflict(refusal, "classification")
    loaded = attachment._load(tenant_id)
    if session_id not in loaded and len(loaded) >= attachment.MAX_ENTRIES:
        from . import chat_runtime  # noqa: PLC0415
        attachment.prune(tenant_id, lambda sid: chat_runtime.get_session(tenant_id, sid) is not None)
        if len(attachment._load(tenant_id)) >= attachment.MAX_ENTRIES:
            raise Conflict("too many chats have a project attached; detach some first", "attachment_full")
    _audit(tenant_id, "project.attached", project_id=project_id)
    attachment.attach(tenant_id, session_id, project_id, str(root_))
    return {"project_id": project_id, "session_id": session_id, "attached": True,
            "note": "Applies from the chat's next message. The assistant can follow the project's rules and use its tools. Its file tools "
                    "cannot change the project's files unless you allow editing in the project's settings (a shell command is not covered)."}


def detach_chat(tenant_id: str, session_id: str) -> dict[str, Any]:
    from core.projects import attachment  # noqa: PLC0415
    _require_chat(tenant_id, session_id)
    pid = attachment.attached(tenant_id, session_id)
    if pid is None:
        return {"session_id": session_id, "attached": False}
    _audit(tenant_id, "project.detached", project_id=pid)
    attachment.detach(tenant_id, session_id)
    return {"session_id": session_id, "attached": False, "project_id": pid}


def chat_attachment(tenant_id: str, session_id: str) -> dict[str, Any]:
    from core.projects import attachment  # noqa: PLC0415
    _require_chat(tenant_id, session_id)
    pid = attachment.attached(tenant_id, session_id)
    from core.projects import builtin  # noqa: PLC0415
    e = (builtin.entry(tenant_id) if builtin.is_builtin(pid) else registry.get(tenant_id, pid)) if pid else None
    if not e:
        return {"session_id": session_id, "project_id": None, "title": None, "withheld": None}
    summ = _summary(tenant_id, e)
    withheld = None                                    # attached, but not sent to the assistant now (release review R10)
    if summ.get("state") != "ok":
        withheld = "the project folder is missing or its project.yaml is not usable"
    else:
        att = attachment._entry(tenant_id, session_id) or {}
        if not att.get("path") or os.path.abspath(att["path"]) != os.path.abspath(str(e.get("path"))):
            withheld = "this chat was attached to a different folder with the same id; attach it again"
        else:
            withheld = _class_refusal(tenant_id, summ.get("classification", "internal"))
            if withheld is None:
                from . import chat_runtime  # noqa: PLC0415
                txt = attachment.project_rules_text(Path(str(e.get("path"))))
                if attachment.deny_base(os.path.abspath(str(e.get("path")))) is None:
                    withheld = ("its read-only rules cannot be written for this folder (a network folder, or a name containing [ ] * ?), "
                                "so it is not sent")
                elif txt is None:
                    withheld = "a rules file of the project (CLAUDE.md, .claude/...) is too large, a link or unreadable; it is not sent"
                elif txt and not chat_runtime.project_rules_allowed(tenant_id, txt):
                    withheld = "the project's CLAUDE.md may not go to the assistant's engine (data-flow rule)"
    return {"session_id": session_id, "project_id": pid, "title": summ.get("title"), "withheld": withheld}


def _class_refusal(tenant_id: str, classification: str) -> Optional[str]:
    """The L34 gate's refusal for sending a project of this class to the OS engine, or None. Fail closed."""
    from . import chat_runtime  # noqa: PLC0415
    return chat_runtime.project_class_refusal(tenant_id, classification)


# ── settings: classification and what the assistant may do (T-0369, ADR-2319) ───────────────

@_project_locked
def set_settings(tenant_id: str, project_id: str, *, classification: Optional[str] = None,
                 assistant_access: Optional[str] = None, confirm: bool = False) -> dict[str, Any]:
    _refuse_builtin(project_id)
    """Change the project's data class and/or the assistant's access. Raising access to ``write`` and
    LOWERING the class need ``confirm`` (the UI's confirming click); everything else applies at once."""
    root, man = _load_project(tenant_id, project_id)
    _cur = _protection_of(tenant_id, project_id, man)
    new_cls = classification if classification is not None else _cur["classification"]
    new_acc = assistant_access if assistant_access is not None else _cur["assistant_access"]
    if new_cls not in manifest.CLASSES:
        raise ProjectsError(f"classification must be one of {manifest.CLASSES}")
    if new_acc not in manifest.ACCESS:
        raise ProjectsError(f"assistant_access must be one of {manifest.ACCESS}")
    cur = _protection_of(tenant_id, project_id, man)
    old_cls, old_acc = cur["classification"], cur["assistant_access"]
    if (new_cls, new_acc) == (old_cls, old_acc):
        # nothing changes in the record; project.yaml is rewritten only if it no longer matches it (hand edit - the health check
        # "protection" tells the user to save the protection to fix exactly this)
        if manifest.classification_of(man) != new_cls or manifest.access_of(man) != new_acc:
            _audit(tenant_id, "project.settings_changed", project_id=project_id, classification=new_cls, access=new_acc)
            manifest.write(root, {**man, "classification": new_cls, "assistant_access": new_acc})
        return get_project(tenant_id, project_id)
    lowers = manifest.CLASSES.index(new_cls) < manifest.CLASSES.index(old_cls)
    raises = old_acc == "read" and new_acc == "write"
    if (lowers or raises) and not confirm:
        raise Conflict("this change lowers the protection of the project and needs confirmation", "confirmation_required",
                       lowers_classification=lowers, raises_access=raises)
    _audit(tenant_id, "project.settings_changed", project_id=project_id, classification=new_cls, access=new_acc)
    # project.yaml first: if it cannot be written (read-only, locked) NOTHING changed - the record that actually decides is untouched.
    # Only then the authoritative record; if that fails, project.yaml is put back.
    manifest.write(root, {**man, "classification": new_cls, "assistant_access": new_acc})
    try:
        registry.set_protection(tenant_id, project_id, new_cls, new_acc)              # the authoritative record
    except BaseException:
        try:
            manifest.write(root, man)
        except Exception:  # noqa: BLE001 - the registry still holds the old protection, which is what applies
            pass
        raise
    return get_project(tenant_id, project_id)


# ── changes (T-0370, ADR-2319 D5) ──────────────────────────────────────────────────────────

def project_changes(tenant_id: str, project_id: str, commit: Optional[str] = None) -> dict[str, Any]:
    from core.projects import changes  # noqa: PLC0415
    root, _man = _load_project(tenant_id, project_id)
    try:
        if commit:
            files, cut = changes.commit_files(root, commit), False
        else:
            files, cut = changes.working_changes_ex(root)
        return {"project_id": project_id, "commit": commit, "files": files, "commits": changes.recent_commits(root),
                "has_repository": bool(changes.repos(root)), "repository_note": changes.repository_note(root), "truncated": cut or len(files) >= changes.MAX_FILES}
    except changes.ChangesError as exc:
        raise ProjectsError(str(exc)) from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectsError(f"git could not read the repository ({type(exc).__name__})") from None


def project_change_file(tenant_id: str, project_id: str, path: str, commit: Optional[str] = None) -> dict[str, Any]:
    from core.projects import changes  # noqa: PLC0415
    root, _man = _load_project(tenant_id, project_id)
    try:
        return changes.file_view(root, path, commit)
    except changes.ChangesError as exc:
        raise ProjectsError(str(exc)) from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectsError(f"git could not read the repository ({type(exc).__name__})") from None


# ── where projects live (release review F1, ADR-2315 plan E7) ─────────────────────────────────

ROOT_REQUIRED = "Choose a folder of your own before switching to it."
NOT_FOREIGN = "This workspace belongs to this installation."
NOT_MISSING = "This project's folder is still where the list says; use Move to change it."


def _outside(tenant_id: str, root: Path) -> list[str]:
    return [e["id"] for e in registry.entries(tenant_id)
            if root not in registry._resolve_forgiving(Path(str(e.get("path")))).parents]


def set_root(tenant_id: str, path: str) -> dict[str, Any]:
    """``PUT /projects/root`` - the alias of ``set_default_storage("root", path)`` (ADR-2321 Decision 8, REVIEW-0061 M16): not a
    second settings writer. Audit-first ``project.root_changed`` as before, plus ``project.storage_changed`` only when
    ``default_storage`` flips to ``root`` (REVIEW-0062 N9). The path never goes into the audit; projects already outside the new
    folder are named and keep working - nothing is moved or dropped."""
    try:
        root = registry.validate_location(tenant_id, path, purpose="projects_root")
    except registry.ProjectPathError as exc:
        _audit_refusal(tenant_id, exc.reason_class)
        raise
    flips = registry.settings(tenant_id)["default_storage"] != "root"
    _audit(tenant_id, "project.root_changed", outcome="requested")
    if flips:
        _audit(tenant_id, "project.storage_changed", to="root", outcome="requested")
    root = registry.set_projects_root(tenant_id, str(root))
    registry.set_default_storage(tenant_id, "root")
    if flips:
        try:
            _audit(tenant_id, "project.storage_changed", to="root", outcome="applied")
        except _tt.AuditUnavailable:
            pass
    _record_recent(tenant_id, root)
    return {"projects_root": str(root), "outside": _outside(tenant_id, root)}


def set_default_storage(tenant_id: str, default_storage: str, projects_root: Optional[str] = None) -> dict[str, Any]:
    """``PUT /projects/settings/storage``: where NEW projects go. Nothing is moved (ADR-2321 Decision 5). Audit-first
    ``project.storage_changed`` requested -> applied; a bad root is the rule's 422; ``root`` without a folder is 409
    ``root_required``."""
    if default_storage not in registry.STORAGE_KINDS:
        raise ProjectsError("default_storage must be managed or root")
    root: Optional[Path] = None
    if projects_root is not None:
        try:
            root = registry.validate_location(tenant_id, projects_root, purpose="projects_root")
        except registry.ProjectPathError as exc:
            _audit_refusal(tenant_id, exc.reason_class)
            raise
    elif default_storage == "root" and registry.settings(tenant_id)["projects_root"] is None:
        _audit_refusal(tenant_id, "root_required")
        raise Conflict(ROOT_REQUIRED, "root_required")
    if default_storage == "managed":
        _workspace(tenant_id)
    _audit(tenant_id, "project.storage_changed", to=default_storage, outcome="requested")
    outcome = "error"
    try:
        if root is not None:
            registry.set_projects_root(tenant_id, str(root))
        try:
            registry.set_default_storage(tenant_id, default_storage)
        except registry.RegistryError as exc:
            if str(exc) == "root_required":
                raise Conflict(ROOT_REQUIRED, "root_required") from None
            raise
        outcome = "applied"
        if root is not None:
            _record_recent(tenant_id, root)
    finally:
        try:
            _audit(tenant_id, "project.storage_changed", to=default_storage, outcome=outcome)
        except _tt.AuditUnavailable:
            pass
    st = registry.settings(tenant_id)
    pr = registry.projects_root(tenant_id)
    return {"default_storage": st["default_storage"], "projects_root": st["projects_root"],
            "managed_root": st["managed_root"],
            "outside": _outside(tenant_id, registry._resolve_forgiving(pr)) if st["default_storage"] == "root" else []}


def rekey_workspace(tenant_id: str) -> dict[str, Any]:
    """"Start a new workspace for this installation" (binding rule (c)): only while the workspace is foreign."""
    if registry.workspace_state(tenant_id) != "foreign":
        _audit_refusal(tenant_id, "not_foreign")
        raise Conflict(NOT_FOREIGN, "not_foreign")
    root = registry.rekey_workspace(tenant_id, audit=_bind_audit(tenant_id))
    st = registry.settings(tenant_id)
    return {"default_storage": st["default_storage"], "projects_root": st["projects_root"], "managed_root": str(root),
            "outside": []}


@_project_locked
def relocate_project(tenant_id: str, project_id: str, location: str) -> dict[str, Any]:
    """``POST /projects/{id}/relocate`` ("Locate folder", ADR-2321 Decision 11): re-point the index at a folder that already holds
    this project's manifest. Only while the recorded path no longer holds it; moves nothing."""
    _refuse_builtin(project_id)
    e = _entry(tenant_id, project_id)
    old = Path(str(e.get("path")))
    try:
        man = manifest.load(old)
        still = man.get("id") == project_id and man.get("tenant") == tenant_id
    except manifest.ManifestError:
        still = False
    if still:
        _audit_refusal(tenant_id, "not_missing", project_id)
        raise Conflict(NOT_MISSING, "not_missing")
    try:
        dest = registry.validate_location(tenant_id, location, purpose="relocate", project_id=project_id)
    except registry.ProjectPathError as exc:
        _audit_refusal(tenant_id, exc.reason_class, project_id)
        raise
    _audit(tenant_id, "project.relocated", project_id=project_id, outcome="requested")
    registry.relocate(tenant_id, project_id, dest)
    warnings: list[str] = []
    from core.projects import attachment  # noqa: PLC0415
    try:
        for sid, att in attachment._load(tenant_id).items():
            if att["project_id"] == project_id:
                attachment.attach(tenant_id, sid, project_id, str(dest))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"the chats attached to this project could not be updated ({type(exc).__name__}); attach them again")
    try:
        _audit(tenant_id, "project.relocated", project_id=project_id, outcome="applied")
    except _tt.AuditUnavailable:
        warnings.append("the audit chain was unavailable for the 'applied' record")
    _record_recent(tenant_id, dest)
    repaired: Optional[int] = None
    try:
        repaired = len(repair(tenant_id, project_id).get("written", []))
    except Exception:  # noqa: BLE001 - the relink stands; the doctor names what is out of sync
        repaired = None
    return {**get_project(tenant_id, project_id), "repaired_files": repaired, "warnings": warnings}


# ── the folder browser's bounds (ADR-2322 Decisions 8 and 11, T-0391) ─────────────────────────────────────────────────────
# The listing itself is core.projects.browse (pure). What lives here is the admission the route applies before a listing runs on
# loop_health.browse_pool(): a per-session token bucket (fairness), non-blocking global and per-tenant slots (a hung mount can hold
# at most BROWSE_GLOBAL_SLOTS threads host-wide and BROWSE_TENANT_SLOTS per tenant), the 10 s cache of a late result, and the
# once-per-session-per-minute stamp of project.browse_refused. All in memory, all bounded, all thread-safe (the slots are released
# from the pool thread when the listing really returns).

from collections import OrderedDict as _OrderedDict  # noqa: E402

BROWSE_BUDGET_S = 2.0
BROWSE_GLOBAL_SLOTS = 4
BROWSE_TENANT_SLOTS = 2
BROWSE_RATE_PER_S = 10.0
BROWSE_BURST = 10.0
BROWSE_MAX_SESSIONS = 256
BROWSE_LATE_TTL_S = 10.0
BROWSE_LATE_MAX = 64
BROWSE_AUDIT_EVERY_S = 60.0
BROWSE_THROTTLED = "Too many folder requests at once; wait a moment."
#: the listing verdicts whose refusal is audited (besides path_rule, slow and throttled)
BROWSE_AUDITED_VERDICTS = frozenset({"corvin_home", "system_prefix", "symlink"})


class BrowseGate:
    """Admission and bookkeeping of the folder browser; one instance per process (:data:`BROWSE_GATE`)."""

    def __init__(self, *, global_slots: int = BROWSE_GLOBAL_SLOTS, tenant_slots: int = BROWSE_TENANT_SLOTS,
                 rate: float = BROWSE_RATE_PER_S, burst: float = BROWSE_BURST):
        self._lock = threading.Lock()
        self.global_slots, self.tenant_slots, self.rate, self.burst = global_slots, tenant_slots, rate, burst
        self._busy = 0
        self._busy_tenant: dict[str, int] = {}               # only tenants with a running listing: bounded by global_slots
        self._buckets: "_OrderedDict[str, tuple[float, float]]" = _OrderedDict()
        self._stamps: "_OrderedDict[str, float]" = _OrderedDict()
        self._late: "_OrderedDict[tuple, tuple[float, dict]]" = _OrderedDict()

    # per-session token bucket (fairness, never a global counter)
    def allow(self, sid: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._buckets.pop(sid, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            ok = tokens >= 1.0
            self._buckets[sid] = (tokens - 1.0 if ok else tokens, now)
            while len(self._buckets) > BROWSE_MAX_SESSIONS:
                self._buckets.popitem(last=False)
            return ok

    # non-blocking slots: per tenant first, then global
    def acquire(self, tenant_id: str) -> bool:
        with self._lock:
            if self._busy_tenant.get(tenant_id, 0) >= self.tenant_slots or self._busy >= self.global_slots:
                return False
            self._busy_tenant[tenant_id] = self._busy_tenant.get(tenant_id, 0) + 1
            self._busy += 1
            return True

    def release(self, tenant_id: str) -> None:
        with self._lock:
            n = self._busy_tenant.get(tenant_id, 0) - 1
            if n > 0:
                self._busy_tenant[tenant_id] = n
            else:
                self._busy_tenant.pop(tenant_id, None)
            self._busy = max(0, self._busy - 1)

    def busy(self) -> tuple[int, dict[str, int]]:
        with self._lock:
            return self._busy, dict(self._busy_tenant)

    # a result that arrived after its request timed out, served once within BROWSE_LATE_TTL_S
    def put_late(self, key: tuple, result: dict) -> None:
        with self._lock:
            self._late.pop(key, None)
            self._late[key] = (time.monotonic(), result)
            while len(self._late) > BROWSE_LATE_MAX:
                self._late.popitem(last=False)

    def take_late(self, key: tuple) -> Optional[dict]:
        with self._lock:
            hit = self._late.pop(key, None)
        if hit is None or time.monotonic() - hit[0] > BROWSE_LATE_TTL_S:
            return None
        return hit[1]

    # project.browse_refused at most once per session per minute
    def should_audit(self, sid: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            last = self._stamps.get(sid)
            if last is not None and now - last < BROWSE_AUDIT_EVERY_S:
                return False
            self._stamps.pop(sid, None)
            self._stamps[sid] = now
            while len(self._stamps) > BROWSE_MAX_SESSIONS:
                self._stamps.popitem(last=False)
            return True

    def reset(self) -> None:
        with self._lock:
            self._busy = 0
            self._busy_tenant.clear()
            self._buckets.clear()
            self._stamps.clear()
            self._late.clear()


BROWSE_GATE = BrowseGate()


def browse_refused(tenant_id: str, sid: str, reason_class: str) -> None:
    """``project.browse_refused`` (WARNING) with ``reason_class`` only - never a path or a name; at most one record per session
    per minute; best effort (a refusal has no effect to protect). Blocking: the route awaits it through ``asyncio.to_thread``."""
    if not BROWSE_GATE.should_audit(sid):
        return
    try:
        _tt._chain(tenant_id, "project.browse_refused", {"reason_class": reason_class})
    except Exception:  # noqa: BLE001
        pass


def _record_recent(tenant_id: str, path: Any) -> None:
    """A confirmed location goes into the picker's recent places (ADR-2322 Decision 5); best effort, never fails a mutation."""
    try:
        from core.projects import browse  # noqa: PLC0415
        browse.record_recent(tenant_id, str(path))
    except Exception:  # noqa: BLE001
        pass


# ── Share: export (ADR-2323 Decisions 4, 12, 15, 17; T-0397) ──────────────────────────────────────────────────────────────
# Class gate first (secret never, confidential only with the confirmation), then one of TWO export slots (429 at once, never
# waiting), then the walk sizes the export: <= 100 MiB is built in the request's worker thread (the route awaits it through
# asyncio.to_thread) and streamed; above it is a background job on loop_health.share_pool() with its files under
# <tenant_home>/projects/exports/<job_id>/ (0o700 dir, 0o600 files, 24 h TTL, swept by the projector tick and by every export
# request). A passphrase encrypts with gpg (symmetric AES256, passphrase on stdin, a private GNUPGHOME); the plain ZIP is
# deleted. Audit: project.exported requested (audit-first) / applied per project / error, project.export_refused.

EXPORT_SYNC_MAX_BYTES = 100 << 20
EXPORT_TTL_S = 24 * 3600.0
EXPORT_SLOTS = 2
_export_slots = threading.BoundedSemaphore(EXPORT_SLOTS)
_running_jobs: set[tuple[str, str]] = set()
_running_lock = threading.Lock()
#: REVIEW-0062 N11: the git_history part ships with its secret scan (T-0410: bundle.build runs bundle.history_gate on every
#: repository whose history it includes); default on (ADR-2323 Decision 8)
GIT_HISTORY_AVAILABLE = True
HISTORY_UNAVAILABLE = "Git history export is not available yet."
EXPORT_MESSAGES = {
    "classification": "Secret projects cannot be exported.",
    "confirm_required": "This project is confidential. Confirm that you want to export it.",
    "busy": "Two exports are already running; try again in a moment.",
    "gpg_missing": "A passphrase needs gpg, which is not installed on this machine. Export without one or install gpg.",
    "gpg_failed": "The bundle could not be encrypted; nothing was exported.",
}
_JOB_ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_JOB_FILE = "job.json"


def _now() -> float:
    """The share sweepers' clock (one seam, so a test can move it without moving the session clock)."""
    return time.time()


class Busy(ProjectsError):
    status = 429
    reason_class = "busy"


def _bundle():
    from core.projects import bundle  # noqa: PLC0415
    return bundle


def _share_root(tenant_id: str, kind: str) -> Path:
    from core.paths.tenant import tenant_home  # noqa: PLC0415
    from core.tenants import validate_tenant_id  # noqa: PLC0415
    return tenant_home(validate_tenant_id(tenant_id)) / "projects" / kind


def _private_dir(d: Path) -> Path:
    """Create *d* 0o700 (and its parents), never through a link."""
    for p in [*reversed(d.parents[:2]), d]:
        if is_link(p):
            raise UnsafePath("a share directory is a link")
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    if is_link(d):
        raise UnsafePath("a share directory is a link")
    with contextlib.suppress(OSError):
        os.chmod(d, 0o700)
    return d


def _write_job(d: Path, state: dict[str, Any]) -> None:
    from core.projects.fsutil import atomic_write  # noqa: PLC0415
    atomic_write(d / _JOB_FILE, (json.dumps(state, sort_keys=True) + "\n").encode("utf-8"), mode=0o600, owned=True)


def _read_job(d: Path) -> Optional[dict[str, Any]]:
    p = d / _JOB_FILE
    if is_link(d) or is_link(p):
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _sweep_dir(tenant_id: str, kind: str, ttl_s: float, now: float, limit: int = 200) -> int:
    """Remove this tenant's share directories older than *ttl_s* (bounded; never through a link; a job still running in this
    process is kept)."""
    root = _share_root(tenant_id, kind)
    if not root.is_dir() or is_link(root):
        return 0
    removed = 0
    try:
        with os.scandir(root) as it:
            ents = [e for _, e in zip(range(limit), it)]
    except OSError:
        return 0
    for e in ents:
        if e.is_symlink() or dirent_is_junction(e) or not e.is_dir(follow_symlinks=False):
            continue
        with _running_lock:
            if (tenant_id, e.name) in _running_jobs:
                continue
        st = _read_job(Path(e.path)) or {}
        created = st.get("created_ts")
        try:
            created = float(created) if created is not None else os.lstat(e.path).st_mtime
        except (OSError, TypeError, ValueError):
            continue
        if now - created > ttl_s:
            shutil.rmtree(e.path, ignore_errors=True)
            removed += 1
    return removed


def sweep_share(tenant_id: str, *, busy: bool = False, now: Optional[float] = None) -> dict[str, int]:
    """The projector tick's sweeper (ADR-2323 Decisions 12, 13): expired export jobs and import stagings of the tenant. Skipped
    while the host is saturated; bounded to 200 directories per kind and tick."""
    if busy:
        return {"exports": 0, "stagings": 0}
    now = _now() if now is None else now
    return {"exports": _sweep_dir(tenant_id, "exports", EXPORT_TTL_S, now),
            "stagings": _sweep_dir(tenant_id, "import_staging", IMPORT_STAGING_TTL_S, now)}


def _parts_of(body: Optional[dict[str, Any]]) -> Any:
    b = _bundle()
    body = body or {}
    return b.Parts(kb=bool(body.get("kb", True)), git_history=bool(body.get("git_history", True)),
                   generated=bool(body.get("generated", False)))


def _parts_str(rows: list[dict[str, Any]]) -> str:
    return ",".join(r["name"] for r in rows if r.get("included"))


def _export_refused(tenant_id: str, reason_class: str, project_id: Optional[str] = None) -> None:
    try:
        _tt._chain(tenant_id, "project.export_refused", {"reason_class": reason_class,
                                                        **({"project_id": project_id} if project_id else {})})
    except Exception:  # noqa: BLE001 - a refusal has no effect to protect
        pass


def _refusal_error(tenant_id: str, r: Any, project_id: Optional[str] = None) -> ProjectsError:
    """A ``bundle.BundleRefused`` as the service error the routes translate (and its audit record)."""
    _export_refused(tenant_id, r.reason_class, r.project_id or project_id)
    if r.reason_class == "not_found":
        return NotFound(r.message)
    extra = {"part": r.part} if r.part else {}
    if r.path:
        extra["path"] = r.path
    return Conflict(r.message, r.reason_class, **extra)


def _gate_projects(tenant_id: str, project_ids: list[str], confirm: bool) -> None:
    for pid in project_ids:
        if not isinstance(pid, str) or not manifest.PROJECT_ID_RE.match(pid):
            raise NotFound("no such project")
        e = registry.get(tenant_id, pid)
        if e is None or e.get("pending"):
            raise NotFound("no such project")
        prot = registry.protection(tenant_id, pid)
        cls = prot.get("classification") if prot else None
        if cls not in manifest.CLASSES:
            try:
                cls = manifest.legacy_class(manifest.load(Path(e["path"])))
            except (manifest.ManifestError, KeyError, TypeError):
                cls = manifest.DEFAULT_CLASS
        if cls == "secret":
            _export_refused(tenant_id, "classification", pid)
            raise Conflict(EXPORT_MESSAGES["classification"], "classification")
        if cls == "confidential" and not confirm:
            _export_refused(tenant_id, "confirm_required", pid)
            raise Conflict(EXPORT_MESSAGES["confirm_required"], "confirm_required")


def gpg_available() -> bool:
    return shutil.which("gpg") is not None


def export_preview(tenant_id: str, project_ids: list[str], parts: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The export dialog's numbers (``bundle.preview``) plus ``gpg``: whether a passphrase can be offered."""
    b = _bundle()
    for pid in project_ids:
        if not isinstance(pid, str) or not manifest.PROJECT_ID_RE.match(pid) or registry.get(tenant_id, pid) is None:
            raise NotFound("no such project")
    try:
        out = b.preview(tenant_id, project_ids, _parts_of(parts), history_available=GIT_HISTORY_AVAILABLE)
    except b.BundleRefused as r:
        if r.reason_class == "not_found":
            raise NotFound(r.message) from None
        raise Conflict(r.message, r.reason_class, **({"part": r.part} if r.part else {})) from None
    out["gpg"] = gpg_available()
    return out


def _encrypt(zpath: Path, passphrase: str) -> Path:
    gpg = shutil.which("gpg")
    if gpg is None:
        raise Conflict(EXPORT_MESSAGES["gpg_missing"], "gpg_missing")
    home = _private_dir(zpath.parent / "gnupg")
    out = zpath.with_name(zpath.name + ".gpg")
    try:
        r = subprocess.run([gpg, "--homedir", str(home), "--batch", "--yes", "--no-tty", "--pinentry-mode", "loopback",
                            "--symmetric", "--cipher-algo", "AES256", "--passphrase-fd", "0", "-o", str(out), str(zpath)],
                           input=passphrase.encode("utf-8"), capture_output=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired):
        r = None
    finally:
        shutil.rmtree(home, ignore_errors=True)
    try:
        zpath.unlink()
    except OSError:
        pass
    if r is None or r.returncode != 0 or not out.is_file():
        with contextlib.suppress(OSError):
            out.unlink()
        raise Conflict(EXPORT_MESSAGES["gpg_failed"], "gpg_failed")
    with contextlib.suppress(OSError):
        os.chmod(out, 0o600)
    return out


def _filename(project_ids: list[str]) -> str:
    day = time.strftime("%Y%m%d", time.gmtime())
    return f"corvin-project-{project_ids[0]}-{day}.zip" if len(project_ids) == 1 else f"corvin-projects-{day}.zip"


def _do_build(tenant_id: str, ids: list[str], parts: Any, jobdir: Path, passphrase: Optional[str],
              progress=None) -> tuple[Path, Any]:
    b = _bundle()
    out = jobdir / _filename(ids)
    res = b.build(tenant_id, ids, parts, out=out, progress=progress, **_history_gate())
    path = out
    if passphrase:
        path = _encrypt(out, passphrase)
    for p in res.envelope["projects"]:
        _audit(tenant_id, "project.exported", outcome="applied", project_id=p["id"], files=p["counts"]["files"],
               parts=_parts_str(p["parts"]))
    return path, res


def _history_gate() -> dict[str, Any]:
    """The history secret scan of T-0410 is bundle.build's own default gate (bundle.history_gate); nothing is overridden."""
    return {}


def export_projects(tenant_id: str, project_ids: list[str], parts: Optional[dict[str, Any]] = None, *, confirm: bool = False,
                    passphrase: Optional[str] = None) -> dict[str, Any]:
    """Export one project or a set. Answers ``{kind: "file", path, filename, media, warnings, cleanup}`` (<= 100 MiB, built
    here - the route runs this in a worker thread) or ``{kind: "job", job_id, warnings}`` (a background job)."""
    b = _bundle()
    ids = list(dict.fromkeys(project_ids or []))
    _sweep_dir(tenant_id, "exports", EXPORT_TTL_S, _now())                      # request-time sweep of the tenant's own jobs
    if not ids:
        raise NotFound("no such project")
    if len(ids) > b.LIMITS["max_projects"]:
        _export_refused(tenant_id, "too_many")
        raise Conflict(b.MESSAGES["too_many"], "too_many")
    _gate_projects(tenant_id, ids, confirm)
    if passphrase and not gpg_available():
        _export_refused(tenant_id, "gpg_missing")
        raise Conflict(EXPORT_MESSAGES["gpg_missing"], "gpg_missing")
    pr = _parts_of(parts)
    warnings: list[str] = []
    if pr.git_history and not GIT_HISTORY_AVAILABLE:
        warnings.append(HISTORY_UNAVAILABLE)
        pr = b.Parts(kb=pr.kb, git_history=False, generated=pr.generated)
    if not _export_slots.acquire(blocking=False):
        _export_refused(tenant_id, "busy")
        raise Busy(EXPORT_MESSAGES["busy"])
    handed_over = False
    try:
        try:
            pv = b.preview(tenant_id, ids, pr, history_available=GIT_HISTORY_AVAILABLE)
        except b.BundleRefused as r:
            raise _refusal_error(tenant_id, r) from None
        size = sum(p["counts"]["bytes"] for p in pv["projects"])
        rows = pv["projects"][0]["parts"] if len(ids) == 1 else []
        job_id = b.new_ulid()
        jobdir = _private_dir(_share_root(tenant_id, "exports") / job_id)
        base = {"files": sum(p["counts"]["files"] for p in pv["projects"]), "parts": _parts_str(rows) or "files",
                **({"project_id": ids[0]} if len(ids) == 1 else {})}
        state = {"state": "running", "created_ts": time.time(), "progress": {"files": 0, "bytes": 0}, "kind": "export"}
        _write_job(jobdir, state)
        try:
            _audit(tenant_id, "project.exported", outcome="requested", **base)              # audit-first
        except BaseException:
            shutil.rmtree(jobdir, ignore_errors=True)
            raise
        if size <= EXPORT_SYNC_MAX_BYTES:
            try:
                path, res = _do_build(tenant_id, ids, pr, jobdir, passphrase)
            except b.BundleRefused as r:
                shutil.rmtree(jobdir, ignore_errors=True)
                _exported_error(tenant_id, base)
                raise _refusal_error(tenant_id, r) from None
            except BaseException:
                shutil.rmtree(jobdir, ignore_errors=True)
                _exported_error(tenant_id, base)
                raise
            warnings += res.warnings
            return {"kind": "file", "path": path, "filename": path.name, "cleanup": jobdir, "warnings": warnings,
                    "media": "application/pgp-encrypted" if path.suffix == ".gpg" else "application/zip"}
        from . import loop_health  # noqa: PLC0415
        with _running_lock:
            _running_jobs.add((tenant_id, job_id))
        try:
            loop_health.share_pool().submit(_run_export_job, tenant_id, job_id, ids, pr, jobdir, passphrase, base, state)
        except RuntimeError:
            with _running_lock:
                _running_jobs.discard((tenant_id, job_id))
            shutil.rmtree(jobdir, ignore_errors=True)
            _exported_error(tenant_id, base)
            raise
        handed_over = True
        return {"kind": "job", "job_id": job_id, "warnings": warnings}
    finally:
        if not handed_over:
            _export_slots.release()


def _exported_error(tenant_id: str, base: dict[str, Any]) -> None:
    try:
        _audit(tenant_id, "project.exported", outcome="error", **base)
    except _tt.AuditUnavailable:
        pass


def _run_export_job(tenant_id: str, job_id: str, ids: list[str], parts: Any, jobdir: Path, passphrase: Optional[str],
                    base: dict[str, Any], state: dict[str, Any]) -> None:
    b = _bundle()
    last = [0.0]

    def progress(files: int, nbytes: int) -> None:
        now = time.monotonic()
        if now - last[0] >= 1.0:
            last[0] = now
            state["progress"] = {"files": files, "bytes": nbytes}
            with contextlib.suppress(OSError):
                _write_job(jobdir, state)
    try:
        try:
            path, res = _do_build(tenant_id, ids, parts, jobdir, passphrase, progress)
            state.update(state="done", file=path.name, warnings=res.warnings,
                         progress={"files": res.counts["files"], "bytes": res.counts["bytes"]},
                         media="application/pgp-encrypted" if path.suffix == ".gpg" else "application/zip")
        except b.BundleRefused as r:
            _exported_error(tenant_id, base)
            _export_refused(tenant_id, r.reason_class, r.project_id)
            state.update(state="error", reason_class=r.reason_class, message=r.message)
        except Conflict as exc:
            _exported_error(tenant_id, base)
            _export_refused(tenant_id, exc.reason_class)
            state.update(state="error", reason_class=exc.reason_class, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - the job reports, never raises into the pool
            _exported_error(tenant_id, base)
            state.update(state="error", reason_class="error", message=f"The export failed ({type(exc).__name__}).")
        if state["state"] != "done":
            for f in jobdir.iterdir():
                if f.name != _JOB_FILE:
                    shutil.rmtree(f, ignore_errors=True) if f.is_dir() and not is_link(f) else f.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            _write_job(jobdir, state)
    finally:
        passphrase = None
        with _running_lock:
            _running_jobs.discard((tenant_id, job_id))
        _export_slots.release()


def _job_dir(tenant_id: str, job_id: str) -> Path:
    if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
        raise NotFound("no such export")
    d = _share_root(tenant_id, "exports") / job_id
    if is_link(d) or not d.is_dir():
        raise NotFound("no such export")
    return d


def export_job(tenant_id: str, job_id: str) -> dict[str, Any]:
    d = _job_dir(tenant_id, job_id)
    st = _read_job(d)
    if st is None or st.get("kind") != "export":
        raise NotFound("no such export")
    out: dict[str, Any] = {"state": st.get("state"), "progress": st.get("progress") or {"files": 0, "bytes": 0}}
    if st.get("state") == "done":
        out["download_url"] = f"/v1/console/projects/-/exports/{job_id}/file"
        out["warnings"] = st.get("warnings") or []
    if st.get("state") == "error":
        out["reason_class"] = st.get("reason_class")
        out["message"] = st.get("message")
    return out


def export_job_file(tenant_id: str, job_id: str) -> dict[str, Any]:
    d = _job_dir(tenant_id, job_id)
    st = _read_job(d)
    if st is None or st.get("state") != "done" or not isinstance(st.get("file"), str):
        raise NotFound("no such export")
    p = d / Path(st["file"]).name
    if is_link(p) or not p.is_file():
        raise NotFound("no such export")
    return {"path": p, "filename": p.name, "media": st.get("media") or "application/zip"}


IMPORT_STAGING_TTL_S = 3600.0


# ── Share: import, first half - upload, staging, validate, report (ADR-2323 Decision 13; T-0399) ──────────────────────────
# The route streams the multipart body in chunks straight into <tenant_home>/projects/import_staging/<staging_id>/bundle.zip
# (dir 0o700, file 0o600, O_EXCL|O_NOFOLLOW) and aborts at 500 MiB + 1 byte (413). At most 5 stagings per tenant (the oldest
# is dropped), a host-wide budget of 2 GiB across tenants (429 staging_full, audited, never dropping another tenant's
# staging), TTL 1 h (projector tick + a request-time sweep of the tenant's own). bundle.validate runs on the staged file in
# one of two validate slots (429 busy at once). Nothing is written into the projects area.

IMPORT_MAX_BYTES = 500 << 20
IMPORT_STAGINGS_PER_TENANT = 5
IMPORT_HOST_BUDGET_BYTES = 2 << 30
#: REVIEW-0063 m11: one tenant's stagings (on disk + uploads in flight) may hold at most this - and never more than half the host
#: budget - so a single tenant cannot make every other tenant's upload staging_full for the TTL
IMPORT_TENANT_SHARE_BYTES = 1 << 30
IMPORT_MESSAGES = {
    "too_large": "The file is larger than 500 MB.",
    "staging_full": "Too many imports are waiting; finish or cancel one first.",
    "busy": "Two imports are being checked already; try again in a moment.",
    "no_file": "Choose one project bundle file to upload.",
}
CONFLICT_MESSAGES = {
    "id_taken": 'A project "{id}" already exists in this list.',
    "folder_not_empty": "That folder already exists and is not empty.",
    "same_source": "This was exported from a project in this list. It can only be imported as a copy.",
    "nested": "It lies inside another project ({id}).",
}
_STAGING_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_staging_lock = threading.Lock()
_staging_reserved: dict[tuple[str, str], int] = {}
_validate_slots = threading.BoundedSemaphore(2)


class ImportRejected(ProjectsError):
    status = 422

    def __init__(self, message: str, reason_class: str):
        super().__init__(message)
        self.reason_class = reason_class


class TooLarge(ProjectsError):
    status = 413
    reason_class = "too_large"


class StagingFull(ProjectsError):
    status = 429
    reason_class = "staging_full"


@dataclass
class StagingHandle:
    tenant_id: str
    staging_id: str
    dir: Path
    fd: int
    written: int = 0


def _import_rejected(tenant_id: str, reason_class: str) -> None:
    try:
        _tt._chain(tenant_id, "project.import_rejected", {"reason_class": reason_class})
    except Exception:  # noqa: BLE001 - a refusal has no effect to protect
        pass


def _host_staging_bytes() -> int:
    """Bytes held by every tenant's stagings on this host (bounded walk: at most 200 tenants x 5 stagings)."""
    total = 0
    tenants = corvin_home() / "tenants"
    try:
        with os.scandir(tenants) as it:
            tdirs = [e.path for _, e in zip(range(200), it) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return 0
    for t in tdirs:
        root = Path(t) / "projects" / "import_staging"
        if not root.is_dir() or is_link(root):
            continue
        try:
            with os.scandir(root) as it:
                for _, e in zip(range(50), it):
                    z = Path(e.path) / "bundle.zip"
                    if e.is_dir(follow_symlinks=False) and not is_link(z):
                        with contextlib.suppress(OSError):
                            total += os.lstat(z).st_size
        except OSError:
            continue
    return total


def _own_stagings(tenant_id: str) -> list[tuple[float, Path]]:
    root = _share_root(tenant_id, "import_staging")
    out: list[tuple[float, Path]] = []
    if not root.is_dir() or is_link(root):
        return out
    with os.scandir(root) as it:
        for _, e in zip(range(200), it):
            if e.is_dir(follow_symlinks=False) and _STAGING_ID_RE.match(e.name):
                st = _read_job(Path(e.path)) or {}
                try:
                    created = float(st.get("created_ts") or os.lstat(e.path).st_mtime)
                except (OSError, TypeError, ValueError):
                    created = 0.0
                out.append((created, Path(e.path)))
    return sorted(out)


def _in_flight_bytes() -> int:
    return sum(_staging_reserved.values())


def _tenant_share() -> int:
    """The bytes one tenant's stagings may hold: ``IMPORT_TENANT_SHARE_BYTES``, at most half the host budget (1 GiB today: one full
    500 MiB upload and a second one fit, the other half stays for the other tenants)."""
    return min(IMPORT_TENANT_SHARE_BYTES, IMPORT_HOST_BUDGET_BYTES // 2)


def _tenant_staging_bytes(tenant_id: str) -> int:
    """Bytes of this tenant's staged ``bundle.zip`` files (the uploads in flight included as far as they are written)."""
    total = 0
    for _created, d in _own_stagings(tenant_id):
        z = d / "bundle.zip"
        if not is_link(z):
            with contextlib.suppress(OSError):
                total += os.lstat(z).st_size
    return total


def _tenant_in_flight(tenant_id: str, *, but: Optional[str] = None) -> int:
    return sum(v for (t, sid), v in _staging_reserved.items() if t == tenant_id and sid != but)


def staging_open(tenant_id: str, declared: Optional[int] = None) -> StagingHandle:
    """Admit one upload: sweep the tenant's expired stagings, check the host budget (429 ``staging_full``, audited), drop the
    tenant's oldest beyond the cap, create the staging directory and the empty ``bundle.zip``."""
    _sweep_dir(tenant_id, "import_staging", IMPORT_STAGING_TTL_S, _now())
    with _staging_lock:
        need = min(declared or 0, IMPORT_MAX_BYTES)
        if _host_staging_bytes() + _in_flight_bytes() + need > IMPORT_HOST_BUDGET_BYTES or \
                _tenant_staging_bytes(tenant_id) + _tenant_in_flight(tenant_id) + need > _tenant_share():
            _import_rejected(tenant_id, "staging_full")
            raise StagingFull(IMPORT_MESSAGES["staging_full"])
        own = _own_stagings(tenant_id)
        while len(own) >= IMPORT_STAGINGS_PER_TENANT:
            _created, oldest = own.pop(0)
            with _running_lock:
                busy_one = (tenant_id, oldest.name) in _running_jobs
            if not busy_one:
                shutil.rmtree(oldest, ignore_errors=True)
        sid = secrets.token_hex(16)
        d = _private_dir(_share_root(tenant_id, "import_staging") / sid)
        _write_job(d, {"kind": "staging", "created_ts": time.time()})
        fd = os.open(d / "bundle.zip", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_BINARY", 0), 0o600)
        _staging_reserved[(tenant_id, sid)] = need
        with _running_lock:
            _running_jobs.add((tenant_id, sid))
    return StagingHandle(tenant_id=tenant_id, staging_id=sid, dir=d, fd=fd)


def staging_write(handle: StagingHandle, data: bytes) -> None:
    """Append one chunk; 413 beyond 500 MiB (the partial file is the caller's to abort), 429 beyond the host budget."""
    if handle.written + len(data) > IMPORT_MAX_BYTES:
        handle.written += len(data)
        raise TooLarge(IMPORT_MESSAGES["too_large"])
    view = memoryview(data)
    while view:
        n = os.write(handle.fd, view)
        view = view[n:]
    handle.written += len(data)
    key = (handle.tenant_id, handle.staging_id)
    with _staging_lock:
        if handle.written > _staging_reserved.get(key, 0):
            others = _in_flight_bytes() - _staging_reserved.get(key, 0)
            # the walk counts this staging's file (handle.written bytes) already; add what the other uploads reserved
            if _host_staging_bytes() + others > IMPORT_HOST_BUDGET_BYTES or \
                    _tenant_staging_bytes(handle.tenant_id) + _tenant_in_flight(handle.tenant_id, but=handle.staging_id) \
                    > _tenant_share():
                _import_rejected(handle.tenant_id, "staging_full")
                raise StagingFull(IMPORT_MESSAGES["staging_full"])
            _staging_reserved[key] = handle.written


def _release(handle: StagingHandle) -> None:
    with contextlib.suppress(OSError):
        os.close(handle.fd)
    with _staging_lock:
        _staging_reserved.pop((handle.tenant_id, handle.staging_id), None)
    with _running_lock:
        _running_jobs.discard((handle.tenant_id, handle.staging_id))


def staging_abort(handle: StagingHandle) -> None:
    _release(handle)
    shutil.rmtree(handle.dir, ignore_errors=True)


def staging_close(handle: StagingHandle) -> None:
    with contextlib.suppress(OSError):
        os.fsync(handle.fd)
    _release(handle)


def _known(tenant_id: str) -> Optional[dict[str, Any]]:
    try:
        cat = catalogue(tenant_id)
    except Exception:  # noqa: BLE001 - no catalogue: no staleness warnings
        return None
    return {"templates": {f"{t.id}@{t.version}" for t in cat.templates.values()} | set(cat.templates),
            "features": set(cat.fragments)}


def _staging_dir(tenant_id: str, staging_id: str) -> Path:
    if not isinstance(staging_id, str) or not _STAGING_ID_RE.match(staging_id):
        raise NotFound("no such import")
    d = _share_root(tenant_id, "import_staging") / staging_id
    if is_link(d) or not d.is_dir() or is_link(d / "bundle.zip"):
        raise NotFound("no such import")
    return d


def _nest_owner(tenant_id: str, loc: Path) -> Optional[str]:
    r = registry._resolve_forgiving(loc)                                   # noqa: SLF001 - the registry's own resolution
    for e in registry.entries(tenant_id):
        try:
            p = registry._resolve_forgiving(Path(str(e.get("path"))))        # noqa: SLF001
        except (OSError, ValueError):
            continue
        if r == p or p in r.parents or r in p.parents:
            return e.get("id")
    return None


def _default_place(tenant_id: str, pid: str) -> dict[str, str]:
    loc, _storage = registry.default_location(tenant_id, pid, audit=_bind_audit(tenant_id))
    return {"storage": registry.settings(tenant_id)["default_storage"], "path": str(registry._resolve_forgiving(loc))}  # noqa: SLF001


def _conflicts(tenant_id: str, card: dict[str, Any], *, location: Optional[str] = None,
               project_id: Optional[str] = None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    pid = project_id or card["id"]
    out: list[dict[str, Any]] = []
    if registry.get(tenant_id, pid) is not None:
        out.append({"kind": "id_taken", "message": CONFLICT_MESSAGES["id_taken"].format(id=pid),
                    "fix": {"suggested_id": _unique_id(tenant_id, pid)}})
    uid = card.get("source_project_uid")
    # "import as a copy": a new id resolves same_source (every import gets a new random uid), so the verdict for a new id no
    # longer lists it - the console's Import button follows the verdict (T-0402)
    if uid and pid == card["id"] and any(e.get("uid") == uid for e in registry.entries(tenant_id)):
        out.append({"kind": "same_source", "message": CONFLICT_MESSAGES["same_source"],
                    "fix": {"suggested_id": _unique_id(tenant_id, pid)}})
    if location:
        place = {"storage": "own", "path": location}
    else:
        # a taken id is never the import's id: the default place is the suggested id's (not the existing project's folder)
        place = _default_place(tenant_id, _unique_id(tenant_id, pid))
    try:
        registry.validate_location(tenant_id, place["path"], purpose="import")
    except registry.ProjectPathError as exc:
        rc = exc.reason_class
        if rc == "not_empty":
            out.append({"kind": "folder_not_empty", "message": CONFLICT_MESSAGES["folder_not_empty"],
                        "fix": {"choose_location": True}})
        elif rc == "nested":
            owner = _nest_owner(tenant_id, Path(place["path"])) or "another project"
            out.append({"kind": "nested", "message": CONFLICT_MESSAGES["nested"].format(id=owner),
                        "fix": {"choose_location": True}})
        else:
            out.append({"kind": rc, "message": str(exc), "fix": {"choose_location": True}})
    return out, place


def import_validate(tenant_id: str, staging_id: str) -> dict[str, Any]:
    """Validate the staged bundle (V1-V6) and answer the report with every project's default place and conflicts. A refusal
    deletes the staging and answers 422 ``{reason_class: <stage>, message}``."""
    d = _staging_dir(tenant_id, staging_id)
    if not _validate_slots.acquire(blocking=False):
        shutil.rmtree(d, ignore_errors=True)
        raise Busy(IMPORT_MESSAGES["busy"])
    key = (tenant_id, staging_id)
    with _running_lock:
        _running_jobs.add(key)
    try:
        rep = _bundle().validate(d / "bundle.zip", _known(tenant_id))
        if not rep["ok"]:
            shutil.rmtree(d, ignore_errors=True)
            _import_rejected(tenant_id, rep["reason_class"])
            raise ImportRejected(rep["message"], rep["reason_class"])
        cards = []
        for p in rep["projects"]:
            conflicts, place = _conflicts(tenant_id, p)
            cards.append({**p, "default_location": place, "conflicts": conflicts})
        report = {"staging_id": staging_id, "origin": "unverified", "exporter": rep["exporter"],
                  "created_at": rep["created_at"], "projects": cards}
        try:
            _audit(tenant_id, "project.import_validated", files=rep["counts"]["files"], outcome="validated")
        except _tt.AuditUnavailable:
            shutil.rmtree(d, ignore_errors=True)
            raise
        from core.projects.fsutil import atomic_write  # noqa: PLC0415
        atomic_write(d / "report.json", (json.dumps(report, sort_keys=True) + "\n").encode("utf-8"), mode=0o600, owned=True)
        return report
    finally:
        with _running_lock:
            _running_jobs.discard(key)
        _validate_slots.release()


def import_report(tenant_id: str, staging_id: str) -> dict[str, Any]:
    d = _staging_dir(tenant_id, staging_id)
    try:
        return json.loads((d / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise NotFound("no such import") from None


def import_verdict(tenant_id: str, staging_id: str, project: str, location: Optional[str] = None,
                   project_id: Optional[str] = None) -> dict[str, Any]:
    rep = import_report(tenant_id, staging_id)
    card = next((p for p in rep.get("projects", []) if p.get("id") == project), None)
    if card is None:
        raise NotFound("no such project in this import")
    if project_id is not None and not manifest.is_new_id_allowed(project_id):
        return {"conflicts": [{"kind": "invalid_id", "message": "id must be lowercase kebab-case, 1-28 characters",
                               "fix": {"suggested_id": _unique_id(tenant_id, slug(project_id))}}]}
    conflicts, place = _conflicts(tenant_id, card, location=location, project_id=project_id)
    return {"conflicts": conflicts, "location": place}


def import_cancel(tenant_id: str, staging_id: str) -> None:
    d = _staging_dir(tenant_id, staging_id)
    shutil.rmtree(d, ignore_errors=True)


# ── Share: import, second half - apply (ADR-2323 Decision 13; T-0400, T-0411, T-0412) and duplicate (Decision 14; T-0401) ─
# Propose then confirm: the client saw the report (validate) and confirms with an id, a place and the parts. The apply
# re-validates the staged file first ("nothing trusted before validation") and then, under the project's lock, in this
# order: (2) audit requested + a registry entry with pending="import" (the only state before the rename), (3) extraction into
# the sibling <parent>/<id>.importing-<8 hex> (O_EXCL|O_NOFOLLOW, never a link), (4) ONE os.rename onto the target, (5) the
# root project.yaml rewritten for this tenant, (6) the git step (bundle.import_git: never clone, never checkout), (7) a new
# random uid and the protection floor, (8) ENGINE.json removed and repair (which alone writes .mcp.json / .corvin/local and
# the MCP hash), (9) kb check, (10) pending cleared, audit applied. A failure in (1)-(4) leaves nothing (sibling and entry
# removed, audit error); a failure in (5)-(9) leaves a registered project whose doctor names the gap.

IMPORT_APPLY_SLOTS = 2
_apply_slots = threading.BoundedSemaphore(IMPORT_APPLY_SLOTS)
APPLY_MESSAGES = {
    "busy": "Two imports are being applied already; try again in a moment.",
    "project_required": "This bundle holds several projects; name the one to import.",
    "invalid_id": "id must be lowercase kebab-case, 1-28 characters",
    "incomplete": "The project was imported, but {step} failed; its doctor names what is missing.",
    "duplicate_secret": "Secret projects cannot be duplicated.",
    "removed": "The unfinished import was removed.",
}
PENDING_REASON = {"import": "Being imported"}


def _class_floor(envelope_class: str) -> str:
    """Decision 4: max(tenant default, envelope class) - the envelope's class is a floor, never a grant."""
    c = list(manifest.CLASSES)
    e = envelope_class if envelope_class in c else manifest.DEFAULT_CLASS
    return c[max(c.index(e), c.index(manifest.DEFAULT_CLASS))]


def _parts_choice(card: dict[str, Any], parts: Optional[dict[str, Any]]) -> tuple[bool, bool, str]:
    """(git history, knowledge base, the audited part list) - a part the bundle does not carry cannot be chosen."""
    want = parts or {}
    included = {r.get("name"): bool(r.get("included")) for r in card.get("parts") or []}
    hist = bool(card.get("git")) and included.get("git_history", False) and bool(want.get("git_history", True))
    kb = included.get("kb", False) and bool(want.get("kb", True))
    names = [n for n in ("files", "kb", "git_history", "videos_index", "generated")
             if included.get(n) and (n != "git_history" or hist) and (n != "kb" or kb)]
    return hist, kb, ",".join(names) or "files"


def _unresolved(tenant_id: str, card: dict[str, Any], pid: str, location: Optional[str]) -> None:
    """Step 1's conflict gate: 409 with the conflict's kind and message for every conflict the choices leave open."""
    conflicts, _place = _conflicts(tenant_id, card, location=location, project_id=pid)
    for c in conflicts:
        if c["kind"] == "same_source" and pid != card["id"]:
            continue                                        # "import as a copy": a new id resolves it (a new uid always)
        raise Conflict(c["message"], c["kind"])


def _target(tenant_id: str, pid: str, location: Optional[str]) -> Path:
    if location:
        return registry.validate_location(tenant_id, location, purpose="import")
    loc, _storage = registry.default_location(tenant_id, pid, audit=_bind_audit(tenant_id))
    return registry.validate_location(tenant_id, str(loc), purpose="import")


def _drop_pending(tenant_id: str, pid: str) -> None:
    e = registry.get(tenant_id, pid)
    if e is not None and e.get("pending") == "import":
        registry.unregister(tenant_id, pid)


def _place_tree(tmp: Path, loc: Path) -> None:
    """Step 4: ONE rename of the extracted sibling onto the target - never over content. An existing EMPTY folder (the
    location rule allows it) is replaced; a folder that appeared or filled meanwhile is 409 ``folder_not_empty``."""
    b = _bundle()
    if os.path.lexists(loc):
        if is_link(loc) or not loc.is_dir() or any(loc.iterdir()):
            raise b.ImportFailed("folder_not_empty", CONFLICT_MESSAGES["folder_not_empty"])
        if os.name == "nt":
            os.rmdir(loc)                                   # Windows renames onto nothing only
    try:
        os.rename(tmp, loc)
    except OSError as exc:
        if exc.errno in (errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR, errno.EISDIR, errno.EACCES) or os.path.lexists(loc):
            raise b.ImportFailed("folder_not_empty", CONFLICT_MESSAGES["folder_not_empty"]) from None
        raise


def _rewrite_manifest(tenant_id: str, pid: str, loc: Path, title: Optional[str], cls: str, access: str) -> dict[str, Any]:
    """Step 5: the root project.yaml (as the bundle carried it: no tenant) names this tenant, the new id and title, and the
    protection the registry holds (so the doctor's protection check agrees)."""
    from core.projects.fsutil import read_text_any  # noqa: PLC0415
    import yaml  # noqa: PLC0415
    raw = yaml.safe_load(read_text_any(loc / manifest.FILE, limit=64 * 1024))
    if not isinstance(raw, dict):
        raise manifest.ManifestError("project.yaml in the bundle is not a mapping")
    man = {**raw, "tenant": tenant_id, "id": pid, "title": (title or raw.get("title") or pid)[:120],
           "classification": cls, "assistant_access": access}
    manifest.write(loc, man)
    return manifest.load(loc)


def _git_init(where: Path) -> None:
    """The ``core`` fragment's git_init (``init -q`` + HEAD -> main), under ``neutral_git_args``."""
    _git(where, "init", "-q")
    _git(where, "symbolic-ref", "HEAD", "refs/heads/main")


def _remove_engine_copy(root: Path) -> None:
    copy = _engine_copy(root)
    if copy and copy.endswith("ENGINE.json"):
        from core.projects.fsutil import ensure_inside  # noqa: PLC0415
        p = ensure_inside(root, root / copy)
        if p.is_file() and not is_link(p):
            p.unlink()


def _kb_check_warning(tenant_id: str, root: Path) -> Optional[str]:
    try:
        rc, out = _project_kb_run(root / "docs", "check", actor="operator", engine=bound_engine(tenant_id), timeout=90, guard_git=True)
    except (Conflict, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        return f"The knowledge-base check could not run ({str(exc)[:120]}); see the project's doctor."
    blocking = out.get("blocking") if isinstance(out, dict) else None
    if rc != 0 or blocking != 0:
        return f"The knowledge-base check reports blocking {blocking}; see the project's doctor."
    return None


@_project_locked
def _apply_into(tenant_id: str, project_id: str, zip_path: Path, card: dict[str, Any], *, location: Optional[str],
                parts: Optional[dict[str, Any]], title: Optional[str], event: str,
                protection: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Decision 13 steps (1, after re-validation)-(10) for one card of a validated bundle, under the project lock of the NEW
    id. ``event``: ``project.imported`` or ``project.duplicated``; ``protection``: duplicate copies the source's record,
    import uses the floor rule."""
    keys = getattr(_applying, "keys", None)
    if keys is None:
        keys = _applying.keys = set()
    keys.add((tenant_id, project_id))
    try:
        return _apply_steps(tenant_id, project_id, zip_path, card, location=location, parts=parts, title=title, event=event,
                            protection=protection)
    finally:
        keys.discard((tenant_id, project_id))


def _apply_steps(tenant_id: str, project_id: str, zip_path: Path, card: dict[str, Any], *, location: Optional[str],
                 parts: Optional[dict[str, Any]], title: Optional[str], event: str,
                 protection: Optional[dict[str, str]]) -> dict[str, Any]:
    b = _bundle()
    pid = project_id
    if not manifest.is_new_id_allowed(pid):
        raise Conflict(APPLY_MESSAGES["invalid_id"], "invalid_id")
    _unresolved(tenant_id, card, pid, location)
    try:
        loc = _target(tenant_id, pid, location)
    except registry.ProjectPathError as exc:
        if exc.reason_class == "not_empty":
            raise Conflict(CONFLICT_MESSAGES["folder_not_empty"], "folder_not_empty") from None
        raise
    hist, kb, parts_str = _parts_choice(card, parts)
    cls = protection["classification"] if protection else _class_floor(card.get("classification", ""))
    access = protection["assistant_access"] if protection else manifest.DEFAULT_ACCESS
    base = {"project_id": pid, "files": int(card.get("counts", {}).get("files", 0)), "parts": parts_str}
    template = card.get("template") or ""
    # (2) audit-first, then the only state that exists before the rename
    _audit(tenant_id, event, outcome="requested", **base)
    tmp: Optional[Path] = None
    try:
        registry.register(tenant_id, pid, loc, template, pending="import")
        registry.make_location(loc.parent).close()        # REVIEW-0063 m4: made without following a link, then verified
        tmp = b.sibling(loc.parent, pid, b.IMPORTING)
        os.mkdir(tmp, 0o700)
        # (3) extraction, (4) the one rename
        b.extract_project(zip_path, card["id"], tmp, kb=kb, history=hist)
        _place_tree(tmp, loc)
        tmp = None
        if not registry._same_place(loc):                 # an ancestor became a link meanwhile: never recorded there
            raise registry.ProjectPathError(registry.SENTENCES["symlink"], "symlink")
    except BaseException as exc:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        with contextlib.suppress(Exception):
            _drop_pending(tenant_id, pid)
        with contextlib.suppress(Exception):
            _audit(tenant_id, event, outcome="error", reason_class=getattr(exc, "reason_class", None) or "error", **base)
        if isinstance(exc, b.ImportFailed):
            raise Conflict(exc.message, exc.reason_class) from None
        if isinstance(exc, registry.RegistryError):
            raise Conflict(str(exc), "registry") from None
        raise
    warnings: list[str] = list(card.get("warnings") or [])
    notes: list[str] = []
    step = "the project description"
    try:
        # (5) the manifest, (6) git, (7) uid + protection, (8) engine copy + repair, (9) kb check
        man = _rewrite_manifest(tenant_id, pid, loc, title, cls, access)
        step = "the git history"
        res = b.import_git(loc, card.get("git") if hist else None)
        notes += res.notes
        if hist and not res.ok:
            warnings.append(res.warning or b.GIT_IMPORT_WARNING)
        if not (loc / ".git").exists():
            _git_init(loc)
        docs = loc / "docs"
        if man.get("docs_mode", "nested") == "nested" and docs.is_dir() and not is_link(docs) and not os.path.lexists(docs / ".git"):
            _git_init(docs)
        if hist and card.get("git", {}).get("docs_head") and res.warning and b.GIT_IMPORT_WARNING not in warnings:
            warnings.append(b.GIT_IMPORT_WARNING)
        step = "the registry entry"
        registry.ensure_uid(tenant_id, pid)                 # a NEW random uid: never the bundle's source_project_uid
        registry.set_protection(tenant_id, pid, cls, access)
        step = "repair"
        _remove_engine_copy(loc)
        repair(tenant_id, pid)
        if "kb" in manifest.active_features(man):
            step = "the knowledge-base check"
            w = _kb_check_warning(tenant_id, loc)
            if w:
                warnings.append(w)
    except BaseException as exc:
        with contextlib.suppress(Exception):
            registry.set_pending(tenant_id, pid, None)
        with contextlib.suppress(Exception):
            _audit(tenant_id, event, outcome="error", reason_class="incomplete", **base)
        if not isinstance(exc, Exception):
            raise
        raise Conflict(APPLY_MESSAGES["incomplete"].format(step=step), "import_incomplete", project_id=pid) from None
    # (10)
    registry.set_pending(tenant_id, pid, None)
    with contextlib.suppress(_tt.AuditUnavailable):
        _audit(tenant_id, event, outcome="applied", **base)
    return {"project": get_project(tenant_id, pid), "warnings": warnings, "notes": notes, "origin": "unverified"}


def _mark_applied(d: Path, bundle_id: str, total: int) -> None:
    """Record the applied card in the staging; the staging is removed once every project of the bundle was applied."""
    st = _read_job(d) or {"kind": "staging", "created_ts": time.time()}
    done = sorted(set(st.get("applied") or []) | {bundle_id})
    if len(done) >= total:
        shutil.rmtree(d, ignore_errors=True)
        return
    st["applied"] = done
    with contextlib.suppress(OSError):
        _write_job(d, st)


def import_apply(tenant_id: str, staging_id: str, *, project: Optional[str] = None, project_id: Optional[str] = None,
                 location: Optional[str] = None, parts: Optional[dict[str, Any]] = None,
                 title: Optional[str] = None) -> dict[str, Any]:
    """Apply one project of a validated staging (Decision 13). ``project``: the bundle's id of the card (may be omitted for a
    one-project bundle); ``project_id``: the id it gets here (default: the bundle's). 201 with ``{project, warnings, notes}``."""
    d = _staging_dir(tenant_id, staging_id)
    if not _apply_slots.acquire(blocking=False):
        raise Busy(APPLY_MESSAGES["busy"])
    key = (tenant_id, staging_id)
    with _running_lock:
        _running_jobs.add(key)
    try:
        # (1) nothing trusted before validation: the staged file again, V1-V6
        rep = _bundle().validate(d / "bundle.zip", _known(tenant_id))
        if not rep["ok"]:
            shutil.rmtree(d, ignore_errors=True)
            _import_rejected(tenant_id, rep["reason_class"])
            raise ImportRejected(rep["message"], rep["reason_class"])
        cards = rep["projects"]
        if project is None:
            if len(cards) != 1:
                raise Conflict(APPLY_MESSAGES["project_required"], "project_required")
            card = cards[0]
        else:
            card = next((c for c in cards if c["id"] == project), None)
            if card is None:
                raise NotFound("no such project in this import")
        out = _apply_into(tenant_id, project_id or card["id"], d / "bundle.zip", card, location=location, parts=parts,
                          title=title, event="project.imported")
        _mark_applied(d, card["id"], len(cards))
        return out
    finally:
        with _running_lock:
            _running_jobs.discard(key)
        _apply_slots.release()


def _remove_unfinished_import(tenant_id: str, project_id: str, e: dict[str, Any]) -> dict[str, Any]:
    """Repair of a ``pending: "import"`` entry no apply holds (the caller holds the project lock): the siblings of the
    unfinished import are deleted (each inside its parent, never through a link), the entry dropped, audited
    ``project.imported outcome=error``. A folder at the target itself is never deleted."""
    from core.projects.fsutil import ensure_inside  # noqa: PLC0415
    b = _bundle()
    root = Path(str(e.get("path")))
    _audit(tenant_id, "project.imported", project_id=project_id, outcome="error", reason_class="unfinished")
    removed = 0
    for s in b.leftovers(root.parent, project_id, strict=True):
        p = ensure_inside(root.parent, s)
        if is_link(p):
            continue
        shutil.rmtree(p, ignore_errors=True)
        removed += 1
    registry.unregister(tenant_id, project_id)
    return {"removed_import": True, "removed_folders": removed, "message": APPLY_MESSAGES["removed"],
            "folder_kept": root.exists()}


def duplicate_project(tenant_id: str, project_id: str, *, new_id: str, location: Optional[str] = None,
                      parts: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Decision 14: export to a private staging plus apply in one call (no download). The staging is a bundle, so the export's
    scans run (secret shapes, the history scan); protection is the source's own record, chat attachments are not copied,
    the copy gets a new random uid. Audit ``project.duplicated`` requested/applied/error. The staging is always removed."""
    b = _bundle()
    _refuse_builtin(project_id)
    e = _entry(tenant_id, project_id)
    if e.get("pending"):
        raise NotFound("no such project")
    if not isinstance(new_id, str) or not manifest.is_new_id_allowed(new_id):
        raise Conflict(APPLY_MESSAGES["invalid_id"], "invalid_id")
    if registry.get(tenant_id, new_id) is not None:
        raise Conflict(CONFLICT_MESSAGES["id_taken"].format(id=new_id), "id_taken")
    _root, man = _load_project(tenant_id, project_id)
    prot = _protection_of(tenant_id, project_id, man)
    if prot["classification"] == "secret":
        _export_refused(tenant_id, "classification", project_id)
        raise Conflict(APPLY_MESSAGES["duplicate_secret"], "classification")
    if not _apply_slots.acquire(blocking=False):
        raise Busy(APPLY_MESSAGES["busy"])
    handle = None
    try:
        handle = staging_open(tenant_id)
        staging_close(handle)
        z = handle.dir / "bundle.zip"
        z.unlink()
        with _running_lock:
            _running_jobs.add((tenant_id, handle.staging_id))
        pr = _parts_of(parts)
        try:
            b.build(tenant_id, [project_id], pr, out=z, **_history_gate())
        except b.BundleRefused as r:
            raise _refusal_error(tenant_id, r, project_id) from None
        rep = b.validate(z, _known(tenant_id))
        if not rep["ok"]:
            raise Conflict(rep["message"], rep["reason_class"])
        card = rep["projects"][0]
        return _apply_into(tenant_id, new_id, z, card, location=location,
                           parts={"git_history": pr.git_history, "kb": pr.kb}, title=None, event="project.duplicated",
                           protection=prot)
    finally:
        if handle is not None:
            with _running_lock:
                _running_jobs.discard((tenant_id, handle.staging_id))
            shutil.rmtree(handle.dir, ignore_errors=True)
        _apply_slots.release()
