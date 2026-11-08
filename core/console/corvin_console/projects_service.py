"""Projects: the console's service layer over ``core.projects`` (ADR-2315).

Everything that needs the host lives here (the audit chain, the tenant plugin registry, the bound
knowledge base's engine, subprocesses); ``core.projects`` stays pure. The HTTP surface is
``routes/projects.py``; the tenant always comes from the authenticated session.

Flow: ``plan_project`` (no write, audit ``project.plan_created``) -> ``create_project(plan_id)``
(re-validates everything, registers, applies, audit ``project.created`` requested/applied) ... and
``adopt``, ``set_features``, ``repair``, ``open_project``, ``remove_from_registry``, ``health``.
A project's files are the user's: nothing here deletes one.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.paths.tenant import corvin_home
from core.projects import fragments as F
from core.projects import manifest, registry
from core.projects.fsutil import UnsafePath
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


def _audit_refusal(tenant_id: str, reason_class: str, project_id: Optional[str] = None) -> None:
    """A refusal has no effect to protect: record it best-effort."""
    try:
        _tt._chain(tenant_id, "project.blocked", {"reason_class": reason_class,
                                                 **({"project_id": project_id} if project_id else {})})
    except Exception:  # noqa: BLE001
        pass


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


_plans: dict[str, PlanRecord] = {}
_plans_lock = threading.Lock()


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
        return rec


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:28].strip("-")
    return s or "project"


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
                 assistant_access: Optional[str] = None) -> dict[str, Any]:
    cat = catalogue(tenant_id)
    tpl = cat.templates.get(template)
    if tpl is None:
        raise NotFound(f"no such template {template!r}")
    title = (title or "").strip()
    if not title or len(title) > 120:
        raise ProjectsError("the title must be 1-120 characters")
    pid = project_id or _unique_id(tenant_id, slug(title))
    if not manifest.PROJECT_ID_RE.match(pid):
        raise ProjectsError("the project id must be lowercase kebab-case, 1-28 characters")
    if registry.get(tenant_id, pid) is not None:
        raise Conflict("a project with this id already exists", "id_taken")
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
    raw_loc = location or str(registry.projects_root(tenant_id) / pid)
    try:
        loc = registry.validate_location(tenant_id, raw_loc)
    except registry.ProjectPathError:
        _audit_refusal(tenant_id, "path_rule", pid)
        raise
    ctx = _context(tenant_id, pid, title, loc)
    plan = F.build_plan(cat, loc, selected, ctx, docs_mode=mode, mode="create")
    plugins = _check_plugins(tenant_id, plan)
    rec = PlanRecord(plan_id=secrets.token_urlsafe(18), tenant_id=tenant_id, template=tpl.id,
                     template_version=tpl.version, project_id=pid, title=title, features=selected,
                     location=loc, docs_mode=mode, role=tpl.role, classification=cls, assistant_access=acc)
    _audit(tenant_id, "project.plan_created", project_id=pid, template=f"{tpl.id}@{tpl.version}",
           files=len(plan.writes))
    _store_plan(rec)
    summary = plan.summary()
    summary["warnings"] = [*summary["warnings"], *external_warnings(tenant_id, plan.features)]
    return {"plan_id": rec.plan_id, "project_id": pid, "title": title, "template": f"{tpl.id}@{tpl.version}",
            "docs_mode": mode, "location": str(loc), "role": tpl.role, "classification": cls,
            "assistant_access": acc, **summary, "plugins": plugins,
            "ready": not plan.conflicts and all(p["state"] == "enabled" for p in plugins),
            "expires_in_s": int(PLAN_TTL_S)}


def _git(root: Path, *args: str) -> None:
    r = subprocess.run(["git", "-C", str(root), "-c", "user.name=CorvinOS", "-c", "user.email=corvin@localhost",
                        "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgsign=false", *args],
                       capture_output=True, text=True, env=kb_projection._env("operator"), encoding="utf-8",
                       errors="replace", timeout=120)
    if r.returncode != 0:
        raise ProjectsError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:200]}")


def _runner(tenant_id: str, root: Path):
    def run(op: F.Op, ctx: dict[str, str]) -> None:
        docs = root / "docs"
        if op.action == "git_init":
            _git(root, "init", "-q")
            _git(root, "symbolic-ref", "HEAD", "refs/heads/main")
        elif op.action == "git_commit":
            where = root / op.args["dir"] if op.args.get("dir") else root
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
                rc, out = kb_projection._run(docs, *a, "--actor=operator", actor="operator", engine=engine)
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
    cat = catalogue(tenant_id)
    tpl = cat.templates.get(rec.template)
    if tpl is None or tpl.version != rec.template_version:
        _audit_refusal(tenant_id, "template_changed", rec.project_id)
        raise Conflict("the template changed since this plan was made - plan again", "template_changed")
    try:
        loc = registry.validate_location(tenant_id, str(rec.location))      # re-validate: the disk may have changed
    except registry.ProjectPathError:
        _audit_refusal(tenant_id, "path_rule", rec.project_id)
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
    base = dict(project_id=rec.project_id, template=f"{tpl.id}@{tpl.version}", files=len(plan.writes))
    _audit(tenant_id, "project.created", outcome="requested", **base)          # audit-first
    outcome = "error"
    try:
        loc.mkdir(parents=True, exist_ok=True)
        man = _manifest_for(tenant_id, rec.project_id, rec.title, tpl.id, tpl.version, rec.docs_mode,
                            plan.features, list(tpl.offered), tpl.videos, rec.role, rec.classification,
                            rec.assistant_access)
        manifest.write(loc, man)                                                # the truth first,
        registry.register(tenant_id, rec.project_id, loc, man["template"])      # then the index
        applied = F.apply_plan(plan, loc, {**ctx, "_root": str(loc)}, _runner(tenant_id, loc))
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
    nested = (root / "docs" / ".git").exists()
    kb = (root / "docs" / "kb" / "_meta").is_dir()
    return ("nested" if nested else "plain"), ({"kb": True} if kb else {})


def adopt_project(tenant_id: str, location: str, *, project_id: Optional[str] = None,
                  title: Optional[str] = None) -> dict[str, Any]:
    """Register a directory. With a ``project.yaml`` it is registered as is. Without one (a
    repository the user already owns) exactly ONE file is written - ``project.yaml`` - and
    nothing else in the directory is touched (ADR-2315 Decision 6)."""
    try:
        loc = registry.validate_location(tenant_id, location, adopt=True,
                                         unmanaged=not (Path(location) / manifest.FILE).exists())
    except registry.ProjectPathError:
        _audit_refusal(tenant_id, "path_rule")
        raise
    if (loc / manifest.FILE).exists():
        man = manifest.load(loc)
        if registry.get(tenant_id, man["id"]) is not None:
            raise Conflict("a project with this id is already registered", "id_taken")
        _audit(tenant_id, "project.adopted", project_id=man["id"], template=man.get("template"))
        registry.register(tenant_id, man["id"], loc, man.get("template"))        # writes nothing in the project
        return get_project(tenant_id, man["id"])
    ttl = (title or loc.name).strip()
    if not 0 < len(ttl) <= 120:
        raise ProjectsError("the title must be 1-120 characters")
    if project_id is not None and not manifest.PROJECT_ID_RE.match(project_id):
        raise ProjectsError("the project id must be lowercase kebab-case, 1-28 characters")
    pid = project_id or _unique_id(tenant_id, slug(loc.name))
    if registry.get(tenant_id, pid) is not None:
        raise Conflict("a project with this id is already registered", "id_taken")
    mode, feats = _detect_adopted(loc)
    data = {"schema": manifest.SCHEMA, "id": pid, "title": ttl, "tenant": tenant_id,
            "template": "adopted@1", "docs_mode": mode, "features": feats}
    manifest.validate(data)
    _audit(tenant_id, "project.adopted", project_id=pid, template="adopted@1", outcome="requested")
    manifest.write(loc, data)                                                    # the ONLY write
    registry.register(tenant_id, pid, loc, "adopted@1")
    return get_project(tenant_id, pid)


def rename_project(tenant_id: str, project_id: str, title: str) -> dict[str, Any]:
    """Change the title in ``project.yaml``. The id (the KB scope label, the registry key) never changes."""
    root, man = _load_project(tenant_id, project_id)
    ttl = (title or "").strip()
    if not 0 < len(ttl) <= 120:
        raise ProjectsError("the title must be 1-120 characters")
    _audit(tenant_id, "project.renamed", project_id=project_id)
    manifest.write(root, {**man, "title": ttl})
    return get_project(tenant_id, project_id)


def move_project(tenant_id: str, project_id: str, location: str) -> dict[str, Any]:
    """Move the directory to another place under this tenant's projects root and follow it in the
    index. Same filesystem only (an atomic rename); an existing target is never overwritten."""
    root, _man = _load_project(tenant_id, project_id)
    try:
        dest = registry.validate_location(tenant_id, location)          # create rules: absent or empty
    except registry.ProjectPathError:
        _audit_refusal(tenant_id, "path_rule", project_id)
        raise
    if dest == root or root in dest.parents:
        raise Conflict("the target lies inside the project itself", "move_inside")
    if dest.exists() and any(dest.iterdir()):
        raise Conflict("the target is not empty", "target_exists")
    _audit(tenant_id, "project.moved", project_id=project_id, outcome="requested")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.rmdir()                                                # empty by the check above
        os.rename(root, dest)
    except OSError as exc:
        raise Conflict(f"the directory could not be moved ({type(exc).__name__}); nothing changed", "move_failed") from None
    registry.relocate(tenant_id, project_id, dest)
    # machine-local files carry the old absolute path (ADR-2315 Decision 5): regenerate them now
    repaired: Optional[int] = None
    try:
        repaired = len(repair(tenant_id, project_id).get("written", []))
    except (ProjectsError, F.FragmentError):
        repaired = None                     # the move stands; the doctor will name what is out of sync
    return {**get_project(tenant_id, project_id), "repaired_files": repaired}


def _entry(tenant_id: str, project_id: str) -> dict[str, Any]:
    e = registry.get(tenant_id, project_id) if manifest.PROJECT_ID_RE.match(project_id or "") else None
    if e is None:
        raise NotFound("no such project")
    return e


def _summary(tenant_id: str, e: dict[str, Any]) -> dict[str, Any]:
    root = Path(str(e.get("path")))
    out: dict[str, Any] = {"id": e["id"], "path": str(root), "template": e.get("template") or "", "added_at": e.get("added_at")}
    try:
        man = manifest.load(root)
    except manifest.ManifestError as exc:
        return {**out, "state": "missing" if not root.exists() else "invalid", "reason": str(exc)}
    if man.get("tenant") != tenant_id or man.get("id") != e["id"]:
        return {**out, "state": "invalid", "reason": "project.yaml does not match the registry entry"}
    return {**out, "state": "ok", "title": man.get("title", e["id"]), "docs_mode": man.get("docs_mode", "nested"),
            "features": man.get("features", {}), "active": manifest.active_features(man),
            "role": man.get("role") or "", "classification": manifest.classification_of(man),
            "assistant_access": manifest.access_of(man), "kb": (root / "docs" / "kb" / "_meta").is_dir()}


def list_projects(tenant_id: str) -> dict[str, Any]:
    return {"projects_root": str(registry.projects_root(tenant_id)),
            "projects": [_summary(tenant_id, e) for e in registry.entries(tenant_id)]}


def get_project(tenant_id: str, project_id: str) -> dict[str, Any]:
    e = _entry(tenant_id, project_id)
    out = _summary(tenant_id, e)
    if out["state"] != "ok":
        return out
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
    try:
        man = manifest.load(root)
    except manifest.ManifestError as exc:
        raise Conflict(f"project.yaml is not usable: {exc}", "manifest_invalid") from None
    if man.get("tenant") != tenant_id or man.get("id") != project_id:
        raise Conflict("project.yaml does not match the registry entry", "manifest_invalid")
    return root, man


def set_features(tenant_id: str, project_id: str, changes: dict[str, bool], *, accept_dependencies: bool = False,
                 cascade: bool = False) -> dict[str, Any]:
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
    for f in [*added, *removed]:                                              # audit-first, one record each
        _audit(tenant_id, "project.feature_toggled", project_id=project_id, feature=f, to="on" if f in want else "off",
               template=man.get("template"))
    man["features"] = {**man.get("features", {}), **{f: (f in want) for f in dict.fromkeys([*man.get("features", {}), *want, *active])}}
    manifest.write(root, man)
    F.apply_plan(plan, root, {**ctx, "_root": str(root)}, _runner(tenant_id, root))
    out = get_project(tenant_id, project_id)
    out["warnings"] = external_warnings(tenant_id, sorted(want)) if "acs" in added else []
    return out


def repair(tenant_id: str, project_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    root, man = _load_project(tenant_id, project_id)
    cat = catalogue(tenant_id)
    ctx = _context(tenant_id, project_id, man.get("title", project_id), root)
    plan = F.build_plan(cat, root, manifest.active_features(man), ctx, docs_mode=man.get("docs_mode", "nested"),
                        mode="repair")
    pending = plan.pending()
    summary = {**plan.summary(), "pending": [{"path": o.path or o.action, "status": o.status, "diff": o.diff}
                                             for o in pending], "dry_run": dry_run}
    if dry_run or not pending:
        return summary
    _require_plugins(tenant_id, plan, project_id)
    _audit(tenant_id, "project.repaired", project_id=project_id, template=man.get("template"), files=len(plan.pending()))
    res = F.apply_plan(plan, root, {**ctx, "_root": str(root)}, _runner(tenant_id, root))
    return {**summary, "written": res["written"], "actions": res["actions"]}


def open_project(tenant_id: str, project_id: str) -> dict[str, Any]:
    e = _entry(tenant_id, project_id)
    summary = _summary(tenant_id, e)
    if summary["state"] != "ok":
        raise Conflict(f"the project is {summary['state']}: {summary.get('reason', '')}", "not_openable")
    _audit(tenant_id, "project.opened", project_id=project_id, template=summary.get("template") or None)
    path = summary["path"]
    return {"project": summary, "command": f'cd "{path}"', "chat_attachment": True,
            "note": "Opening shows the command for your own terminal. To use the project in a console chat, attach it to that chat."}


def remove_from_registry(tenant_id: str, project_id: str) -> dict[str, Any]:
    _entry(tenant_id, project_id)
    _audit(tenant_id, "project.registry_removed", project_id=project_id)
    registry.unregister(tenant_id, project_id)
    return {"removed": project_id, "files_deleted": False}


def health(tenant_id: str, project_id: str) -> dict[str, Any]:
    """A checklist of REAL checks, each with its evidence - never a score (ADR-2315 D8)."""
    e = _entry(tenant_id, project_id)
    root = Path(str(e["path"]))
    checks: list[dict[str, Any]] = []

    def add(cid: str, ok: bool, detail: str) -> None:
        checks.append({"id": cid, "ok": ok, "detail": detail, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    add("path_exists", root.is_dir(), "the project directory exists" if root.is_dir() else "the directory is missing")
    if not root.is_dir():
        return {"project_id": project_id, "ok": False, "checks": checks}
    try:
        man = manifest.load(root)
        add("manifest", man.get("tenant") == tenant_id and man.get("id") == project_id, "project.yaml is valid and matches the registry")
    except manifest.ManifestError as exc:
        add("manifest", False, str(exc))
        return {"project_id": project_id, "ok": False, "checks": checks}
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
                rc, out = kb_projection._run(docs, "check", actor="operator", engine=bound_engine(tenant_id), timeout=90)
                blocking = out.get("blocking") if isinstance(out, dict) else None
                add("kb_check", rc == 0 and blocking == 0, f"kb check: blocking {blocking}" if blocking is not None
                    else f"kb check failed: {str(out)[:120]}")
            except (Conflict, subprocess.TimeoutExpired) as exc:
                add("kb_check", False, str(exc)[:160])
    return {"project_id": project_id, "ok": all(c["ok"] for c in checks), "checks": checks}


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
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = root / target
    resolved = registry._resolve_forgiving(target)
    home = registry._resolve_forgiving(corvin_home())
    if resolved == home or home in resolved.parents or resolved in home.parents:
        raise ProjectsError("videos.root must not be inside the CorvinOS home")
    return resolved


def _kb_initiative(root: Path) -> Optional[str]:
    """The id of the project's first initiative (the one the seed created), or None."""
    for p in sorted((root / "docs" / "kb" / "initiatives").glob("*.md")):
        m = re.search(r"^id:\s*(\S+)\s*$", p.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1)
    return None


def new_video(tenant_id: str, project_id: str, name: str) -> dict[str, Any]:
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
    if vdir.exists() or vdir.is_symlink():
        raise Conflict("a video with this name already exists in the videos folder", "exists")
    kb_on = "kb" in manifest.active_features(man)
    if kb_on:
        bound_engine(tenant_id)            # refuse before anything is written when there is no engine for the KB
    _audit(tenant_id, "project.video_created", project_id=project_id, outcome="requested", files=0)
    created: list[Path] = []
    try:
        vroot.mkdir(parents=True, exist_ok=True)
        vdir.mkdir()
        created.append(vdir)
        for sub in ("source", "build"):
            (vdir / sub).mkdir()
            created.append(vdir / sub)
            (vdir / sub / ".gitkeep").write_text("", encoding="utf-8")
        tpl = root / "videos" / "TEMPLATE-sources.md"
        text = tpl.read_text(encoding="utf-8") if tpl.is_file() else ""
        if [s for s in VIDEO_SECTIONS if f"## {s}" not in text]:
            raise ProjectsError("videos/TEMPLATE-sources.md is missing its eight sections; restore it with Repair")
        body = re.sub(r"^# sources\.md - .*$", f"# sources.md - {name}", text, count=1, flags=re.M)
        (vdir / "sources.md").write_text(body, encoding="utf-8")
        created.append(vdir / "sources.md")
        index = root / "videos" / "INDEX.md"
        if index.is_file() and not index.is_symlink():
            row = "| {} | {} | {} | `{}` | started |\n".format(
                name, "yes" if "de" in langs else "-", "yes" if "en" in langs else "-", vdir)
            with index.open("a", encoding="utf-8") as fh:
                fh.write(row)
        epic_id: Optional[str] = None
        tasks: list[str] = []
        if kb_on:
            docs = root / "docs"
            ini = _kb_initiative(root)
            if ini is None:
                raise ProjectsError("the project has no initiative in its knowledge base to put the video's epic under")
            rc, out = kb_projection._run(docs, "new", "epic", f"--title=VIDEO-{name}", f"--initiative={ini}",
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
                rc, t = kb_projection._run(docs, "new", "task", f"--title={title}", f"--epic={epic_id}",
                                           f"--dod={dod}", "--actor=operator", actor="operator", engine=bound_engine(tenant_id))
                if rc != 0 or not isinstance(t, dict) or not t.get("id"):
                    raise ProjectsError(f"the knowledge base refused the task {title!r}: {str(t)[:160]}")
                tasks.append(str(t["id"]))
    except BaseException as exc:
        # roll back only what this call created: nothing that existed before is removed
        for path in sorted(created, key=lambda p: len(p.parts), reverse=True):
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            except OSError:
                pass
        _audit(tenant_id, "project.video_created", project_id=project_id, outcome="error", files=0)
        if isinstance(exc, ProjectsError):
            raise
        raise ProjectsError(f"the video could not be created ({type(exc).__name__}); what was written was removed") from None
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
            children = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name))
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
            if c.is_symlink():
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
    if docs_dir.is_dir():
        for sub in ("initiatives", "epics", "tasks"):
            kb_counts[sub] = len(list((docs_dir / sub).glob("*.md"))) if (docs_dir / sub).is_dir() else 0
    for sub in ("concepts", "decisions", "ideas", "implementation-plans", "notes", "adversarial-reviews"):
        kb_counts[sub] = len(list((root / "docs" / sub).glob("*.md"))) if (root / "docs" / sub).is_dir() else 0
    return {"project_id": project_id, "entries": entries, "truncated": truncated, "kb_counts": kb_counts,
            "videos": _video_rows(root, man), "videos_root": str(_videos_root(tenant_id, root, man))}


def _video_rows(root: Path, man: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows of videos/INDEX.md (one per video), each with whether its folder still exists."""
    index = root / "videos" / "INDEX.md"
    if not index.is_file() or index.is_symlink():
        return []
    rows: list[dict[str, Any]] = []
    for line in index.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| video "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        name, de, en, folder, state = cells[0], cells[1], cells[2], cells[3].strip("`"), cells[4]
        rows.append({"name": name, "de": de == "yes", "en": en == "yes", "folder": folder,
                     "state": state, "folder_exists": bool(folder) and Path(folder).is_dir()})
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
    _, man = _load_project(tenant_id, project_id)
    refusal = _class_refusal(tenant_id, manifest.classification_of(man))
    if refusal:
        _audit_refusal(tenant_id, "classification", project_id)
        raise Conflict(refusal, "classification")
    _audit(tenant_id, "project.attached", project_id=project_id)
    attachment.attach(tenant_id, session_id, project_id)
    return {"project_id": project_id, "session_id": session_id, "attached": True,
            "note": "Applies from the chat's next message. The project's files are read-only for the assistant; its rules and tools are available."}


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
    e = registry.get(tenant_id, pid) if pid else None
    return {"session_id": session_id, "project_id": pid if e else None,
            "title": _summary(tenant_id, e).get("title") if e else None}


def _class_refusal(tenant_id: str, classification: str) -> Optional[str]:
    """The L34 gate's refusal for sending a project of this class to the OS engine, or None. Fail closed."""
    from . import chat_runtime  # noqa: PLC0415
    return chat_runtime.project_class_refusal(tenant_id, classification)


# ── settings: classification and what the assistant may do (T-0369, ADR-2319) ───────────────

def set_settings(tenant_id: str, project_id: str, *, classification: Optional[str] = None,
                 assistant_access: Optional[str] = None, confirm: bool = False) -> dict[str, Any]:
    """Change the project's data class and/or the assistant's access. Raising access to ``write`` and
    LOWERING the class need ``confirm`` (the UI's confirming click); everything else applies at once."""
    root, man = _load_project(tenant_id, project_id)
    new_cls = classification if classification is not None else manifest.classification_of(man)
    new_acc = assistant_access if assistant_access is not None else manifest.access_of(man)
    if new_cls not in manifest.CLASSES:
        raise ProjectsError(f"classification must be one of {manifest.CLASSES}")
    if new_acc not in manifest.ACCESS:
        raise ProjectsError(f"assistant_access must be one of {manifest.ACCESS}")
    old_cls, old_acc = manifest.classification_of(man), manifest.access_of(man)
    if (new_cls, new_acc) == (old_cls, old_acc):
        return get_project(tenant_id, project_id)
    lowers = manifest.CLASSES.index(new_cls) < manifest.CLASSES.index(old_cls)
    raises = old_acc == "read" and new_acc == "write"
    if (lowers or raises) and not confirm:
        raise Conflict("this change lowers the protection of the project and needs confirmation", "confirmation_required",
                       lowers_classification=lowers, raises_access=raises)
    _audit(tenant_id, "project.settings_changed", project_id=project_id, classification=new_cls, access=new_acc)
    manifest.write(root, {**man, "classification": new_cls, "assistant_access": new_acc})
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
                "has_repository": bool(changes.repos(root)), "truncated": cut or len(files) >= changes.MAX_FILES}
    except changes.ChangesError as exc:
        raise ProjectsError(str(exc)) from None


def project_change_file(tenant_id: str, project_id: str, path: str, commit: Optional[str] = None) -> dict[str, Any]:
    from core.projects import changes  # noqa: PLC0415
    root, _man = _load_project(tenant_id, project_id)
    try:
        return changes.file_view(root, path, commit)
    except changes.ChangesError as exc:
        raise ProjectsError(str(exc)) from None


# ── where projects live (release review F1, ADR-2315 plan E7) ─────────────────────────────────

def set_root(tenant_id: str, path: str) -> dict[str, Any]:
    """Change the tenant's projects folder. Audit-first; the path itself never goes into the audit. Projects already outside the new
    folder are named and keep working - nothing is moved or dropped."""
    try:
        root = registry.validate_root(tenant_id, path)
    except registry.ProjectPathError:
        _audit_refusal(tenant_id, "path_rule")
        raise
    _audit(tenant_id, "project.root_changed", outcome="requested")
    root = registry.set_projects_root(tenant_id, str(root))
    outside = [e["id"] for e in registry.entries(tenant_id)
               if root not in registry._resolve_forgiving(Path(str(e.get("path")))).parents]
    return {"projects_root": str(root), "outside": outside}
