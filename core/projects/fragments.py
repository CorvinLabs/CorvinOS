"""The declarative fragment/template engine (ADR-2315 Decisions 2-5).

A *catalogue* is a directory ``<src>/fragments/<id>/fragment.yaml`` (+ template files) and
``<src>/templates/<id>.yaml``. Fragments are DATA: no code from a template runs here. The only
active steps are a closed set of named actions the host implements (``git_init``, ``kb_scaffold``,
``kb_seed``, ``git_commit``); the engine only decides WHAT, in which order and whether it is still
needed.

``fragment.yaml``::

    id: kb                      # [a-z][a-z0-9_]*
    version: 1
    title: Knowledge base
    description: ...
    requires: [core]            # hard, transitive, acyclic
    suggests: [gates]           # a hint only
    requires_plugins: [corvin_knowledge]   # tenant plugin ids that must be enabled
    files:                      # kind: seed (written once, never overwritten),
      - {src: files/x.md, dst: docs/README.md, kind: seed}        # generated (engine-owned),
    claude: files/claude.md     # section of the generated CLAUDE.md (written only while the `claude` feature is on)
    gitignore: [".corvin/local/"]
    mcp: {server: {command: "{{paths.python}}", args: ["-m", "x"], env: {A: "{{paths.corvin_home}}"}}}
    kb: {initiative: "{{project.title}}", epic: "Setup"}     # (kb fragment) scaffold + seed
    seed_tasks: [{title: "...", dod: "..."}]                  # tasks of the epic, only with kb active
    actions: [git_init]

Plan first (no writes), then apply; apply twice is a no-op; repair is the same plan against an
existing project (seeds skipped, generated files rewritten, user blocks kept byte-identical).
"""
from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from .fsutil import atomic_write, ensure_inside

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
FILE_KINDS = ("seed", "generated", "local")
ACTIONS = frozenset({"git_init", "git_commit", "kb_scaffold", "kb_seed"})
PLACEHOLDERS = frozenset({"project.id", "project.title", "project.tenant", "paths.python", "paths.repo",
                          "paths.corvin_home", "paths.docs", "paths.kb_engine", "paths.pathsep"})
_PH_RE = re.compile(r"\{\{\s*([a-z_.]+)\s*\}\}")
USER_BEGIN, USER_END = "<!-- user:begin -->", "<!-- user:end -->"
GEN_MARK = "<!-- corvin:generated - edit only between the user markers -->"
GI_BEGIN, GI_END = "# corvin:begin", "# corvin:end"
MAX_FILE_BYTES = 256 * 1024
CLAUDE_MD = "CLAUDE.md"
LOCAL_DIR = ".corvin/local"


class FragmentError(ValueError):
    """The catalogue, a template or a request is invalid (-> HTTP 422 / a failed test)."""


# ---------------------------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Fragment:
    id: str
    version: int
    title: str
    description: str
    requires: tuple[str, ...]
    suggests: tuple[str, ...]
    requires_plugins: tuple[str, ...]
    files: tuple[dict[str, str], ...]
    claude: Optional[str]
    gitignore: tuple[str, ...]
    mcp: dict[str, Any]
    kb: dict[str, Any]
    seed_tasks: tuple[dict[str, str], ...]
    actions: tuple[str, ...]
    root: Path = field(compare=False, default=Path("."))


@dataclass(frozen=True)
class Template:
    id: str
    version: int
    title: str
    description: str
    features: tuple[str, ...]          # on by default
    offered: tuple[str, ...]           # every feature the wizard may show
    docs_mode: str = "nested"
    videos: dict[str, Any] = field(default_factory=dict, compare=False)
    role: str = ""                       # one of manifest.ROLES, or "" (a template without a job)
    classification: str = "internal"     # the template's default data class (L34 vocabulary)
    assistant_access: str = "read"       # the template's default for what the assistant may do
    outcome: str = ""                    # plain words: what this role typically produces


@dataclass
class Catalogue:
    fragments: dict[str, Fragment]
    templates: dict[str, Template]
    order: tuple[str, ...]             # catalogue (file-name) order: the tie-break of the topological sort
    source: Path = Path(".")


def _tup(v: Any, what: str) -> tuple:
    if v is None:
        return ()
    if not isinstance(v, list):
        raise FragmentError(f"{what} must be a list")
    return tuple(v)


def _load_yaml(p: Path) -> dict[str, Any]:
    if p.is_symlink() or not p.is_file():
        raise FragmentError(f"{p.name}: missing or a symlink")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise FragmentError(f"{p.name}: too large")
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise FragmentError(f"{p}: invalid YAML ({type(exc).__name__})") from None
    if not isinstance(d, dict):
        raise FragmentError(f"{p.name}: must be a mapping")
    return d


def _safe_rel(rel: str, what: str) -> str:
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\\" in rel or ".." in Path(rel).parts \
            or "\x00" in rel or re.match(r"^[A-Za-z]:", rel):
        raise FragmentError(f"{what}: {rel!r} is not a safe relative path")
    return rel


def load_catalogue(src: Path) -> Catalogue:
    src = Path(src)
    fdir, tdir = src / "fragments", src / "templates"
    if not fdir.is_dir():
        raise FragmentError("catalogue has no fragments/ directory")
    frags: dict[str, Fragment] = {}
    for d in sorted(p for p in fdir.iterdir() if p.is_dir() and not p.is_symlink()):
        raw = _load_yaml(d / "fragment.yaml")
        fid = raw.get("id")
        if not isinstance(fid, str) or not ID_RE.match(fid) or fid != d.name:
            raise FragmentError(f"{d.name}: id must equal the directory name and match {ID_RE.pattern}")
        files = []
        for f in _tup(raw.get("files"), f"{fid}.files"):
            if not isinstance(f, dict) or f.get("kind") not in FILE_KINDS:
                raise FragmentError(f"{fid}: every file needs a kind in {FILE_KINDS}")
            files.append({"src": _safe_rel(f.get("src"), f"{fid}.files.src"),
                          "dst": _safe_rel(f.get("dst"), f"{fid}.files.dst"), "kind": f["kind"]})
        claude = raw.get("claude")
        if claude is not None:
            _safe_rel(claude, f"{fid}.claude")
        actions = _tup(raw.get("actions"), f"{fid}.actions")
        bad = [a for a in actions if a not in ACTIONS]
        if bad:
            raise FragmentError(f"{fid}: unknown action(s) {bad}; the host implements {sorted(ACTIONS)}")
        tasks = []
        for t in _tup(raw.get("seed_tasks"), f"{fid}.seed_tasks"):
            if not isinstance(t, dict) or not isinstance(t.get("title"), str) or not isinstance(t.get("dod"), str):
                raise FragmentError(f"{fid}: a seed task needs a title and a dod")
            tasks.append({"title": t["title"], "dod": t["dod"]})
        mcp = raw.get("mcp") or {}
        if not isinstance(mcp, dict):
            raise FragmentError(f"{fid}.mcp must be a mapping")
        frag = Fragment(
            id=fid, version=int(raw.get("version", 1)), title=str(raw.get("title", fid)),
            description=str(raw.get("description", "")),
            requires=tuple(_tup(raw.get("requires"), f"{fid}.requires")),
            suggests=tuple(_tup(raw.get("suggests"), f"{fid}.suggests")),
            requires_plugins=tuple(_tup(raw.get("requires_plugins"), f"{fid}.requires_plugins")),
            files=tuple(files), claude=claude, gitignore=tuple(_tup(raw.get("gitignore"), f"{fid}.gitignore")),
            mcp=mcp, kb=raw.get("kb") or {}, seed_tasks=tuple(tasks), actions=tuple(actions), root=d)
        frags[fid] = frag
    templates: dict[str, Template] = {}
    if tdir.is_dir():
        for p in sorted(tdir.glob("*.yaml")):
            raw = _load_yaml(p)
            tid = raw.get("id")
            if not isinstance(tid, str) or tid != p.stem:
                raise FragmentError(f"{p.name}: id must equal the file name")
            feats = tuple(_tup(raw.get("features"), f"{tid}.features"))
            offered = tuple(_tup(raw.get("offered"), f"{tid}.offered")) or feats
            from . import manifest as _m  # noqa: PLC0415
            role = raw.get("role") or ""
            cls = raw.get("classification") or _m.DEFAULT_CLASS
            acc = raw.get("assistant_access") or _m.DEFAULT_ACCESS
            if (role and role not in _m.ROLES) or cls not in _m.CLASSES or acc not in _m.ACCESS:
                raise FragmentError(f"{tid}: role, classification or assistant_access is not in the closed lists")
            templates[tid] = Template(id=tid, version=int(raw.get("version", 1)), title=str(raw.get("title", tid)),
                                      role=str(role), classification=str(cls), assistant_access=str(acc),
                                      outcome=str(raw.get("outcome", ""))[:160],
                                      description=str(raw.get("description", "")), features=feats, offered=offered,
                                      docs_mode=str(raw.get("docs_mode", "nested")),
                                      videos=raw.get("videos") if isinstance(raw.get("videos"), dict) else {})
    return Catalogue(fragments=frags, templates=templates, order=tuple(frags), source=src)


# ---------------------------------------------------------------------------------------------
# dependency graph
# ---------------------------------------------------------------------------------------------

def validate_catalogue(cat: Catalogue) -> list[str]:
    """Every problem of the catalogue, or ``[]``: unknown ``requires``, cycles, templates and
    their offered features not requires-closed, unknown template features."""
    problems: list[str] = []
    for f in cat.fragments.values():
        for r in f.requires:
            if r not in cat.fragments:
                problems.append(f"fragment {f.id} requires unknown {r!r}")
        for s in f.suggests:
            if s not in cat.fragments:
                problems.append(f"fragment {f.id} suggests unknown {s!r}")
    state: dict[str, int] = {}

    def visit(n: str, stack: list[str]) -> None:
        if state.get(n) == 2 or n not in cat.fragments:
            return
        if state.get(n) == 1:
            problems.append("requires cycle: " + " -> ".join(stack[stack.index(n):] + [n]))
            return
        state[n] = 1
        for r in cat.fragments[n].requires:
            visit(r, stack + [n])
        state[n] = 2

    for n in cat.fragments:
        visit(n, [])
    if problems:
        return problems
    for t in cat.templates.values():
        for label, feats in (("features", t.features), ("offered", t.offered)):
            unknown = [x for x in feats if x not in cat.fragments]
            if unknown:
                problems.append(f"template {t.id} {label}: unknown {unknown}")
                continue
            missing = sorted(set(closure(cat, feats)) - set(feats))
            if missing:
                problems.append(f"template {t.id} {label} is not requires-closed: add {missing}")
        if "core" in cat.fragments and "core" not in t.features:
            problems.append(f"template {t.id} must include core")
        if not set(t.features) <= set(t.offered):
            problems.append(f"template {t.id}: default features not within offered")
    return problems


def closure(cat: Catalogue, selected: Any) -> list[str]:
    """*selected* plus everything it transitively requires, in dependency order (a dependency before
    its dependant; ties broken by catalogue order). Raises on unknown ids."""
    want: set[str] = set()

    def add(n: str) -> None:
        if n not in cat.fragments:
            raise FragmentError(f"unknown feature {n!r}")
        if n in want:
            return
        want.add(n)
        for r in cat.fragments[n].requires:
            add(r)

    for n in selected:
        add(n)
    out: list[str] = []
    done: set[str] = set()

    def emit(n: str) -> None:
        if n in done:
            return
        done.add(n)
        for r in cat.fragments[n].requires:
            emit(r)
        out.append(n)

    for n in cat.order:
        if n in want:
            emit(n)
    return out


def dependants(cat: Catalogue, active: Any, feature: str) -> list[str]:
    """Active features that (transitively) require *feature*: the cascade a switch-off would need."""
    act = set(active)
    out = []
    for n in cat.order:
        if n in act and n != feature and feature in closure(cat, [n]):
            out.append(n)
    return out


def suggestions(cat: Catalogue, active: Any) -> list[dict[str, str]]:
    act = set(active)
    return [{"feature": f, "suggests": s} for f in cat.order if f in act
            for s in cat.fragments[f].suggests if s not in act]


# ---------------------------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------------------------

@dataclass
class Op:
    kind: str                       # "write" | "delete" | "action"
    status: str                     # "new" | "changed" | "unchanged" | "skip"
    path: str = ""                  # project-relative, posix
    file_kind: str = ""             # seed | generated | local
    owner: str = ""                 # fragment id (or "engine")
    content: bytes = b""
    action: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    diff: str = ""
    reason: str = ""


@dataclass
class Plan:
    features: list[str]
    pulled: list[str]               # added because something requires them
    ops: list[Op]
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    plugins_needed: list[str] = field(default_factory=list)
    suggestions: list[dict[str, str]] = field(default_factory=list)

    @property
    def writes(self) -> list[Op]:
        return [o for o in self.ops if o.kind == "write"]

    def pending(self) -> list[Op]:
        return [o for o in self.ops if o.status in ("new", "changed")]

    def summary(self) -> dict[str, Any]:
        return {"features": self.features, "pulled": self.pulled, "warnings": self.warnings,
                "conflicts": self.conflicts, "plugins_needed": self.plugins_needed,
                "suggestions": self.suggestions,
                "files": [{"path": o.path, "owner": o.owner, "kind": o.file_kind, "status": o.status}
                          for o in self.writes],
                "actions": [{"action": o.action, "owner": o.owner, "status": o.status}
                            for o in self.ops if o.kind == "action"]}


def substitute(text: str, ctx: dict[str, str], where: str) -> str:
    def rep(m: "re.Match[str]") -> str:
        key = m.group(1)
        if key not in PLACEHOLDERS:
            raise FragmentError(f"{where}: unknown placeholder {{{{{key}}}}}")
        if key not in ctx:
            raise FragmentError(f"{where}: placeholder {{{{{key}}}}} has no value in this context")
        return ctx[key]
    return _PH_RE.sub(rep, text)


def _walk_sub(obj: Any, ctx: dict[str, str], where: str) -> Any:
    if isinstance(obj, str):
        return substitute(obj, ctx, where)
    if isinstance(obj, list):
        return [_walk_sub(x, ctx, where) for x in obj]
    if isinstance(obj, dict):
        return {k: _walk_sub(v, ctx, where) for k, v in obj.items()}
    return obj


def _read_bytes_regular(p: Path) -> Optional[bytes]:
    """The bytes of a regular file (never through a symlink), or None."""
    try:
        if p.is_symlink() or not p.is_file():
            return None
        return p.read_bytes()
    except OSError:
        return None


def _read_text(p: Path) -> Optional[str]:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return None


def _status_of(root: Path, rel: str, new: bytes, kind: str) -> tuple[str, str]:
    p = root / rel
    if p.is_symlink():
        return "skip", "target is a symlink"
    if not p.exists():
        return "new", ""
    if kind == "seed":
        return "skip", "exists (seed files are never overwritten)"
    try:
        old = p.read_bytes()
    except OSError:
        return "skip", "unreadable"
    if old == new:
        return "unchanged", ""
    try:
        d = "".join(difflib.unified_diff(old.decode("utf-8").splitlines(True), new.decode("utf-8").splitlines(True),
                                         f"a/{rel}", f"b/{rel}", n=2))
    except UnicodeDecodeError:
        d = "(binary change)"
    return "changed", d


def _user_block(existing: Optional[str]) -> str:
    """The text between the user markers of an existing file, or the default empty block."""
    if existing and USER_BEGIN in existing and USER_END in existing:
        a = existing.index(USER_BEGIN) + len(USER_BEGIN)
        b = existing.index(USER_END, a)
        return existing[a:b]
    return "\n\n"


def _compose_claude(cat: Catalogue, active: list[str], ctx: dict[str, str], root: Path,
                    user_text: Optional[str]) -> tuple[Optional[bytes], str]:
    title = ctx["project.title"]
    parts = [GEN_MARK, f"# {title} - conventions for Claude Code", ""]
    for fid in active:
        f = cat.fragments[fid]
        if f.claude:
            body = (f.root / f.claude).read_text(encoding="utf-8")
            parts.append(substitute(body, ctx, f"{fid}/{f.claude}").rstrip() + "\n")
    parts.append("## Your own rules\n")
    parts.append(f"{USER_BEGIN}{_user_block(user_text)}{USER_END}\n")
    return ("\n".join(parts)).encode("utf-8"), ""


def _compose_gitignore(cat: Catalogue, active: list[str], existing: Optional[str], extra: list[str]) -> bytes:
    lines: list[str] = []
    for fid in active:
        for g in cat.fragments[fid].gitignore:
            if g not in lines:
                lines.append(g)
    for g in extra:
        if g not in lines:
            lines.append(g)
    block = "\n".join([GI_BEGIN, *lines, GI_END]) + "\n"
    if existing and GI_BEGIN in existing and GI_END in existing:
        a = existing.index(GI_BEGIN)
        b = existing.index(GI_END, a) + len(GI_END)
        tail = existing[b:].lstrip("\n")
        head = existing[:a]
        return (head + block + (tail and "\n" + tail if tail else "")).encode("utf-8")
    return ((existing.rstrip("\n") + "\n\n" if existing else "") + block).encode("utf-8")


def build_plan(cat: Catalogue, root: Path, selected: Any, ctx: dict[str, str], *,
               docs_mode: str = "nested", mode: str = "create") -> Plan:
    """Compute what would be written. NOTHING is written. *mode* ``create`` expects an empty (or
    absent) *root*; ``repair`` adjusts an existing project (seeds kept, generated files rewritten)."""
    root = Path(root)
    pulled_all = closure(cat, selected)
    pulled = [x for x in pulled_all if x not in set(selected)]
    plan = Plan(features=pulled_all, pulled=pulled, ops=[])
    plan.suggestions = suggestions(cat, pulled_all)
    plan.plugins_needed = sorted({p for fid in pulled_all for p in cat.fragments[fid].requires_plugins})
    kb_on = "kb" in pulled_all
    local_extra: list[str] = []
    if docs_mode == "nested" and kb_on:
        local_extra.append("docs/")          # docs is its own repository: the project's repo must not record a gitlink

    if mode == "create" and root.exists() and root.is_dir() and any(root.iterdir()):
        plan.conflicts.append("the directory already exists and is not empty")

    for fid in pulled_all:
        for a in cat.fragments[fid].actions:
            if a == "git_init":
                has_git = (root / ".git").exists()
                plan.ops.append(Op(kind="action", status="skip" if has_git else "new", action=a, owner=fid,
                                   args={"docs_mode": docs_mode}, reason="already a repository" if has_git else ""))
    if kb_on:
        # BEFORE any file: the scaffold step refuses a docs/ that already holds something it did not create
        scaffolded = (root / "docs" / "kb" / "_meta" / "tenant_kb.json").is_file()
        plan.ops.append(Op(kind="action", status="skip" if scaffolded else "new", action="kb_scaffold", owner="kb",
                           args={"docs_mode": docs_mode}, reason="already scaffolded" if scaffolded else ""))
    for fid in pulled_all:
        f = cat.fragments[fid]
        where = f"fragment {fid}"
        for spec in f.files:
            data = (f.root / spec["src"])
            if data.is_symlink() or not data.is_file() or data.stat().st_size > MAX_FILE_BYTES:
                raise FragmentError(f"{where}: {spec['src']} is missing, a symlink or too large")
            content = substitute(data.read_text(encoding="utf-8"), ctx, f"{fid}/{spec['src']}").encode("utf-8")
            status, extra = _status_of(root, spec["dst"], content, spec["kind"])
            plan.ops.append(Op(kind="write", status=status, path=spec["dst"], file_kind=spec["kind"], owner=fid,
                               content=content, diff=extra if status == "changed" else "",
                               reason=extra if status == "skip" else ""))

    # composed files: CLAUDE.md, .gitignore, MCP block
    if "claude" in pulled_all and any(cat.fragments[f].claude for f in pulled_all):
        existing = _read_text(root / CLAUDE_MD)
        if existing is not None and GEN_MARK not in existing:
            plan.ops.append(Op(kind="write", status="skip", path=CLAUDE_MD, file_kind="generated", owner="engine",
                               reason="an existing CLAUDE.md that this engine did not generate is never overwritten"))
            plan.warnings.append("CLAUDE.md exists and is not generated: left untouched")
        else:
            body, _ = _compose_claude(cat, pulled_all, ctx, root, existing)
            st, df = _status_of(root, CLAUDE_MD, body or b"", "generated")
            plan.ops.append(Op(kind="write", status=st, path=CLAUDE_MD, file_kind="generated", owner="engine",
                               content=body or b"", diff=df if st == "changed" else ""))
    gi_existing = _read_text(root / ".gitignore")
    gi_body = _compose_gitignore(cat, pulled_all, gi_existing, local_extra + [f"{LOCAL_DIR}/", ".mcp.json"])
    st, df = _status_of(root, ".gitignore", gi_body, "generated")
    plan.ops.append(Op(kind="write", status=st, path=".gitignore", file_kind="generated", owner="engine",
                       content=gi_body, diff=df if st == "changed" else ""))
    servers: dict[str, Any] = {}
    for fid in pulled_all:
        for name, spec in cat.fragments[fid].mcp.items():
            servers[name] = _walk_sub(spec, ctx, f"fragment {fid} mcp {name}")
    # The MCP block (release review R1/R6): the engine owns `.corvin/local/mcp.json`; the root `.mcp.json` (for the user's own terminal)
    # is engine-owned only while it is byte-identical to that copy - a foreign one (an adopted repo's) is never overwritten, never deleted.
    local_rel = f"{LOCAL_DIR}/mcp.json"
    engine_copy = _read_bytes_regular(root / local_rel)
    root_copy = _read_bytes_regular(root / ".mcp.json")
    root_is_ours = root_copy is None or (engine_copy is not None and root_copy == engine_copy)
    if servers:
        mcp = (json.dumps({"mcpServers": servers}, indent=2, sort_keys=True) + "\n").encode("utf-8")
        st, df = _status_of(root, local_rel, mcp, "local")
        plan.ops.append(Op(kind="write", status=st, path=local_rel, file_kind="local", owner="engine", content=mcp,
                           diff=df if st == "changed" else ""))
        if root_is_ours:
            st, df = _status_of(root, ".mcp.json", mcp, "local")
            plan.ops.append(Op(kind="write", status=st, path=".mcp.json", file_kind="local", owner="engine", content=mcp,
                               diff=df if st == "changed" else ""))
        else:
            plan.ops.append(Op(kind="write", status="skip", path=".mcp.json", file_kind="local", owner="engine",
                               reason="an existing .mcp.json that this engine did not write is never overwritten"))
            plan.warnings.append(".mcp.json exists and is not the engine's: left untouched; chats use .corvin/local/mcp.json")
    else:
        # the feature that needed the block is off: drop the engine's copies, keep anything the user wrote
        if engine_copy is not None:
            plan.ops.append(Op(kind="delete", status="changed", path=local_rel, file_kind="local", owner="engine",
                               diff="(removed: no active feature needs an MCP server)"))
            if root_copy is not None and root_is_ours:
                plan.ops.append(Op(kind="delete", status="changed", path=".mcp.json", file_kind="local", owner="engine",
                                   diff="(removed: no active feature needs an MCP server)"))
    if kb_on:
        cmds = _kb_commands(ctx)
        st, df = _status_of(root, f"{LOCAL_DIR}/kb-commands.md", cmds, "local")
        plan.ops.append(Op(kind="write", status=st, path=f"{LOCAL_DIR}/kb-commands.md", file_kind="local",
                           owner="engine", content=cmds, diff=df if st == "changed" else ""))

    # host actions, in a fixed order after the files
    if kb_on:
        kbspec = next((cat.fragments[f].kb for f in pulled_all if cat.fragments[f].kb), {})
        seeded = (root / "docs" / "kb" / "initiatives").is_dir() and any((root / "docs" / "kb" / "initiatives").glob("*.md"))
        tasks = [t for fid in pulled_all for t in cat.fragments[fid].seed_tasks]
        plan.ops.append(Op(kind="action", status="skip" if seeded else "new", action="kb_seed", owner="kb",
                           args={"initiative": substitute(str(kbspec.get("initiative", "{{project.title}}")), ctx, "kb.initiative"),
                                 "epic": substitute(str(kbspec.get("epic", "Setup")), ctx, "kb.epic"),
                                 "tasks": [{"title": substitute(t["title"], ctx, "seed task"),
                                            "dod": substitute(t["dod"], ctx, "seed task")} for t in tasks]},
                           reason="already seeded" if seeded else ""))
    scaffold_new = any(o.action == "kb_scaffold" and o.status == "new" for o in plan.ops if o.kind == "action")
    if kb_on and docs_mode == "nested" and (scaffold_new or any(o.kind == "write" and o.path.startswith("docs/")
                                                              and o.status in ("new", "changed") for o in plan.ops)):
        plan.ops.append(Op(kind="action", status="new", action="git_commit", owner="engine",
                           args={"message": "docs: project scaffold", "dir": "docs"}))
    if any(o.action == "git_init" and o.status == "new" for o in plan.ops if o.kind == "action"):
        plan.ops.append(Op(kind="action", status="new", action="git_commit", owner="engine",
                           args={"message": "project: initial scaffold"}))
    return plan


def _kb_commands(ctx: dict[str, str]) -> bytes:
    eng, docs = ctx.get("paths.kb_engine", ""), ctx.get("paths.docs", "")
    return (f"# Knowledge-base commands (machine-local, generated - do not commit)\n\n"
            f"The path gate runs `kb.py` only from the pinned engine; this project's KB is `docs/`, selected with `--repo`.\n\n"
            f"```bash\nKB=\"python3 {eng} --repo {docs}\"\n"
            f"$KB find <words>\n$KB new idea --title \"...\"\n$KB new task --title \"...\" --epic E-001 --dod \"...\"\n"
            f"$KB task T-0001 in_progress|blocked|done|cancelled [--reason ...] [--dod ...]\n$KB check\n```\n").encode("utf-8")


# ---------------------------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------------------------

ActionRunner = Callable[[Op, dict[str, str]], None]


def apply_plan(plan: Plan, root: Path, ctx: dict[str, str], runner: Optional[ActionRunner] = None) -> dict[str, Any]:
    """Execute the pending ops of *plan*. File writes are atomic, never follow a symlink and stay
    inside *root*. Returns ``{written: [paths], actions: [names], skipped: n}``. A conflict in the
    plan refuses everything before the first write."""
    if plan.conflicts:
        raise FragmentError("plan has conflicts: " + "; ".join(plan.conflicts))
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    ran: list[str] = []
    for op in plan.ops:
        if op.status not in ("new", "changed"):
            continue
        if op.kind == "write":
            target = ensure_inside(root, root / op.path)
            atomic_write(target, op.content, mode=0o600 if op.file_kind == "local" else 0o644, root=root)
            written.append(op.path)
        elif op.kind == "delete":
            target = ensure_inside(root, root / op.path)          # never through a symlink, never outside the project
            if target.is_file() and not target.is_symlink():
                target.unlink()
            written.append(op.path)
        else:
            if runner is None:
                raise FragmentError(f"action {op.action} needs a runner")
            runner(op, ctx)
            ran.append(op.action)
    return {"written": written, "actions": ran, "skipped": sum(1 for o in plan.ops if o.status == "skip")}
