#!/usr/bin/env python3
"""Zero-caller sweep for registration-style contracts (session-drift analysis
2026-10-02, §11).

Three separate efforts each built a "context bridge across sessions" — a Brain
Hub ``Subsystem``, an OS ``BaseSkill`` and an ADR-only proposal — and none of
them was reachable from anything that runs. A class that implements a
registration contract type-checks and unit-tests clean whether or not anything
ever registers it, so per-commit review cannot see the gap. This sweep can.

Method:
  1. Import graph over the production tree (tests, worktrees, venvs excluded).
     Edges: ``import``/``from`` statements and string literals naming a repo
     module (``"pkg.mod:Class"`` registries, ``importlib.import_module("…")``)
     or a repo file (``"…/x.py"``). A dotted name resolves against, in order:
     the importing file's own directory; the repo root and the packages the
     wheel maps to top level (``[tool.hatch.build.targets.wheel.sources]``);
     the directories the importing file itself puts on ``sys.path``; every
     directory a REACHABLE file puts on ``sys.path`` (derived from the AST of
     the ``sys.path.insert/append`` calls, iterated to a fixpoint with the
     walk — a root only a dead file inserts is on no process's path); then a
     unique dotted suffix. A name whose top level is the standard library or a
     third-party package pyproject declares never gets past the repo-root
     step, and its own-directory step counts only from a non-package (script)
     directory: ``import types`` is the stdlib, not some ``…/types.py``.
     At each step a name that matches more than one file links NOWHERE
     — Python would pick one by ``sys.path`` order, which is a property of the
     running process, and linking all of them let one live module keep every
     same-named file "reachable". A file path (in a shell launcher, a systemd
     unit or a string) resolves as written relative to its own file's
     directory, then the repo root, then by its most specific unique path
     suffix; an ambiguous one links nowhere.
  2. Roots: every ``[project.scripts]`` target, every module/script a systemd
     unit's ``ExecStart*`` (continuation lines joined) or a shell script
     anywhere in the repo starts — by path, or by ``-m pkg`` which also roots
     ``pkg/__main__.py`` (shell comments, echo/printf text and lines that are
     neither a python call, an assignment, ``exec``/``nohup`` nor the script
     itself as the command do not count) — every builtin plugin the plugin
     bootstrap loads (``plugin.yaml`` → ``provider.py``/``plugin.py``), and the
     long-running hosts (bridge adapter, gateway app, console standalone).
     Importing a module also reaches its packages' ``__init__``.
  3. Contract implementations: classes whose base resolves — through the
     file's own imports and aliases (``from … import Subsystem as _Base``) and
     through package re-exports — to a registration base named in
     ``CONTRACTS``, closed over subclassing by (file, class) identity, never by
     bare class name; every class whose body or in-repo bases define the
     plugin lifecycle shape (``plugin_id`` + ``on_load`` — what every plugin
     load path accepts; ``CorvinPlugin`` is a Protocol nobody subclasses);
     plus every module calling ``register_stage(``. A base the sweep cannot
     resolve counts only if its own name is a contract name.
  4. Report each implementation whose module is not reachable from a root.

What a result means. "Reachable" means a statically resolvable import/mention
path exists — not that anything instantiates the class ("imported" is not
"used"; a class reached only through a package ``__init__`` re-export counts).
"Dead" means the sweep found NO statically resolvable path: imports built at
run time (f-strings, concatenation, ``getattr``), ``sys.path`` entries it cannot
evaluate, and bare names that are ambiguous across the tree are invisible to
it, so a dead report is a finding to verify, not a proof. The gate errs toward
reporting: ambiguity drops an edge rather than inventing one.

Usage:
  python3 scripts/zero_caller_sweep.py               # report
  python3 scripts/zero_caller_sweep.py --json
  python3 scripts/zero_caller_sweep.py --check       # exit 1 on a dead one NOT in the baseline
  python3 scripts/zero_caller_sweep.py --write-baseline
The baseline (``scripts/zero_caller_baseline.json``) records today's known-dead
set, so the gate fails on the NEXT dead implementation without first requiring
the whole backlog to be fixed. Removing a baseline entry is the way to record
that one was wired or deleted.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "zero_caller_baseline.json"
SCAN_DIRS = ("core", "corvin_operator", "ops", "corvinOS", "scripts", "tools")
EXCLUDE_PARTS = {"tests", "test", "node_modules", ".venv", "venv", "__pycache__",
                 "worktrees", ".claude", "archived_v2", "dist", "build"}

#: Registration bases: a subclass is only useful once something registers it.
CONTRACTS = {
    "Subsystem": "Brain Hub subsystem (ADR-0347)",
    "BaseSkill": "OS skill, phase-1 base (ADR-0535)",
    "Skill": "OS skill (skill_registry_phase1, ADR-0532)",
    "ContextStage": "CEL stage (ADR-0277)",
    "CorvinPlugin": "plugin lifecycle (plugin_id + on_load, ADR-0030)",
}

#: The plugin contract is STRUCTURAL: ``corvin_plugins.protocol.CorvinPlugin`` is
#: a ``typing.Protocol`` nobody has to subclass, and every load path
#: (``bootstrap._load_builtin_class``, ``bootstrap_declared``,
#: ``bootstrap_global``) accepts a class by its ``plugin_id`` + ``on_load``
#: shape. A class whose body — or an in-repo base's body — defines both is
#: therefore a plugin implementation, whatever it subclasses.
PLUGIN_SHAPE = frozenset({"plugin_id", "on_load"})

#: Top-level names Python resolves OUTSIDE this repo: the standard library and
#: the import names of the third-party distributions pyproject declares. A bare
#: ``import random`` is the stdlib, never ``…/strategies/random.py`` — such a
#: name links into the repo only where Python itself would look there first
#: (see ``Index.resolve``). Deterministic on purpose: derived from pyproject,
#: not from whatever happens to be installed, so CI (pytest only) and a full
#: venv compute the same graph.
_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))
#: Distribution → import name where the two differ.
_DIST_IMPORT_NAMES = {
    "scikit_learn": "sklearn", "pyyaml": "yaml", "pyjwt": "jwt",
    "python_multipart": "multipart", "piper_tts": "piper", "edge_tts": "edge_tts",
    "python_telegram_bot": "telegram", "discord.py": "discord",
    "whatsapp_web.py": "whatsapp", "psycopg2_binary": "psycopg2",
    "faster_whisper": "faster_whisper", "ffmpeg_python": "ffmpeg",
    "google_api_python_client": "googleapiclient",
    "opentelemetry_api": "opentelemetry", "opentelemetry_sdk": "opentelemetry",
    "opentelemetry_exporter_otlp_proto_grpc": "opentelemetry",
    "opentelemetry_exporter_otlp_proto_http": "opentelemetry",
}
#: Importable in every environment this runs in without being declared:
#: ``packaging``/``pluggy`` come with pytest (the CI job installs it), ``grpc``
#: with the declared OTLP-gRPC exporter, ``pip``/``setuptools`` with the venv.
_TRANSITIVE_IMPORT_NAMES = frozenset({"packaging", "pluggy", "_pytest", "grpc",
                                      "pip", "setuptools", "pkg_resources"})


def third_party_names() -> frozenset[str]:
    """Import names of the distributions pyproject declares (runtime and every
    optional group), plus the transitive ones in ``_TRANSITIVE_IMPORT_NAMES``."""
    import tomllib   # py >= 3.11 (the CI job pins 3.11)
    try:
        proj = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    except (OSError, tomllib.TOMLDecodeError):
        return _TRANSITIVE_IMPORT_NAMES
    specs = list(proj.get("dependencies") or [])
    for group in (proj.get("optional-dependencies") or {}).values():
        specs.extend(group)
    out = set(_TRANSITIVE_IMPORT_NAMES)
    for spec in specs:
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
        if m:
            norm = m.group(1).lower().replace("-", "_")
            out.add(_DIST_IMPORT_NAMES.get(norm, norm))
    return frozenset(out)

#: Long-running hosts that are not console scripts.
EXTRA_ROOTS = (
    "corvin_operator/bridges/shared/adapter.py",
    "core/gateway/corvin_gateway/app.py",
    "core/console/corvin_console/standalone.py",
    "core/console/corvin_console/app.py",
)


def _is_test(p: Path) -> bool:
    return (p.name.startswith("test_") or p.name.endswith("_test.py")
            or p.name == "conftest.py")


def production_files() -> list[Path]:
    out = []
    for d in SCAN_DIRS:
        for f in (REPO / d).rglob("*.py"):
            rel = f.relative_to(REPO)
            if EXCLUDE_PARTS & set(rel.parts[:-1]) or _is_test(f):
                continue
            out.append(f)
    return sorted(out)


def module_name(f: Path) -> str:
    rel = f.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _parse(f: Path) -> ast.AST | None:
    try:
        return ast.parse(f.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return None


# ── sys.path inserts, evaluated statically ──────────────────────────────────

_MAX_VALUES = 64


class _PathEval:
    """Evaluate the argument of ``sys.path.insert/append`` to the set of
    absolute paths it can take. Handles ``__file__``, ``Path(…)``,
    ``.resolve()/.absolute()``, ``.parent``, ``.parents[N]``, ``/ "x"``,
    ``str()``/``os.fspath()``, ``os.path.dirname/abspath/realpath/normpath/
    join``, ``pathlib.Path`` under an alias, names assigned from those
    (module- or function-level, including ``for x in (a, b)`` and
    ``for x in p.parents``) and zero-argument helper functions of the same
    file that return one. Anything else evaluates to nothing and is skipped."""

    def __init__(self, f: Path, tree: ast.AST):
        self.file = str(f)
        self.env: dict[str, list[ast.AST]] = defaultdict(list)
        self.ancestors_of: dict[str, list[ast.AST]] = defaultdict(list)
        self.funcs: dict[str, list[ast.AST]] = defaultdict(list)
        self.path_ctors = {"Path", "PurePath", "PosixPath"}
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        self.env[t.id].append(n.value)
            elif isinstance(n, (ast.AnnAssign, ast.NamedExpr)) and isinstance(n.target, ast.Name) \
                    and n.value is not None:
                self.env[n.target.id].append(n.value)
            elif isinstance(n, (ast.For, ast.comprehension)) and isinstance(n.target, ast.Name):
                if isinstance(n.iter, (ast.Tuple, ast.List)):
                    self.env[n.target.id].extend(n.iter.elts)
                elif isinstance(n.iter, ast.Attribute) and n.iter.attr == "parents":
                    self.ancestors_of[n.target.id].append(n.iter.value)
            elif isinstance(n, ast.ImportFrom) and n.module == "pathlib":
                self.path_ctors |= {a.asname for a in n.names
                                    if a.asname and a.name in ("Path", "PurePath", "PosixPath")}
            elif isinstance(n, ast.FunctionDef) and not n.args.args and not n.args.posonlyargs:
                self.funcs[n.name].extend(r.value for r in ast.walk(n)
                                          if isinstance(r, ast.Return) and r.value is not None)
        self._busy: set[str] = set()

    def eval(self, n: ast.AST, depth: int = 0) -> set[str]:
        if depth > 12:
            return set()
        out = self._eval(n, depth + 1)
        return set(sorted(out)[:_MAX_VALUES])

    def _eval(self, n: ast.AST, d: int) -> set[str]:
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return {n.value}
        if isinstance(n, ast.Name):
            if n.id == "__file__":
                return {self.file}
            if n.id in self._busy:
                return set()
            self._busy.add(n.id)
            try:
                out: set[str] = set()
                for v in self.env.get(n.id, ()):
                    out |= self.eval(v, d)
                for v in self.ancestors_of.get(n.id, ()):
                    for a in self.eval(v, d):
                        while os.path.isabs(a) and os.path.dirname(a) != a:
                            a = os.path.dirname(a)
                            out.add(a)
                return out
            finally:
                self._busy.discard(n.id)
        if isinstance(n, ast.Attribute) and n.attr == "parent":
            return {os.path.dirname(v) for v in self.eval(n.value, d) if os.path.isabs(v)}
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute) \
                and n.value.attr == "parents" and isinstance(n.slice, ast.Constant) \
                and isinstance(n.slice.value, int):
            out = set()
            for v in self.eval(n.value.value, d):
                if os.path.isabs(v):
                    for _ in range(n.slice.value + 1):
                        v = os.path.dirname(v)
                    out.add(v)
            return out
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            return self._join([n.left, n.right], d)
        if isinstance(n, ast.Call):
            fn = n.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if name in self.path_ctors and n.args:
                return self._join(n.args, d)
            if isinstance(fn, ast.Name) and not n.args and fn.id in self.funcs \
                    and fn.id not in self._busy:
                self._busy.add(fn.id)
                try:
                    out = set()
                    for r in self.funcs[fn.id]:
                        out |= self.eval(r, d)
                    return out
                finally:
                    self._busy.discard(fn.id)
            if name in ("str", "fspath") and len(n.args) == 1:
                return self.eval(n.args[0], d)
            if name in ("resolve", "absolute", "expanduser") and isinstance(fn, ast.Attribute):
                return {os.path.normpath(v) for v in self.eval(fn.value, d)}
            if name in ("abspath", "realpath", "normpath") and len(n.args) == 1:
                return {os.path.normpath(v) for v in self.eval(n.args[0], d)}
            if name == "dirname" and len(n.args) == 1:
                return {os.path.dirname(v) for v in self.eval(n.args[0], d) if os.path.isabs(v)}
            if name == "join" and n.args and isinstance(fn, ast.Attribute) \
                    and isinstance(fn.value, ast.Attribute) and fn.value.attr == "path":
                return self._join(n.args, d)
            if name == "joinpath" and isinstance(fn, ast.Attribute):
                return self._join([fn.value, *n.args], d)
        return set()

    def _join(self, parts: list[ast.AST], d: int) -> set[str]:
        acc = self.eval(parts[0], d)
        for p in parts[1:]:
            nxt = self.eval(p, d)
            acc = {os.path.join(a, b) for a in acc for b in nxt}
            if not acc:
                return set()
        return acc


def sys_path_inserts(f: Path, tree: ast.AST) -> list[str]:
    """Repo-relative directories ``f`` puts on ``sys.path`` (statically
    evaluable ``sys.path.insert(i, x)`` / ``sys.path.append(x)`` calls)."""
    ev: _PathEval | None = None
    out: list[str] = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("insert", "append")
                and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "path"):
            continue
        arg = (n.args[1] if n.func.attr == "insert" and len(n.args) == 2 else
               n.args[0] if n.func.attr == "append" and len(n.args) == 1 else None)
        if arg is None:
            continue
        ev = ev or _PathEval(f, tree)
        for v in ev.eval(arg):
            if not os.path.isabs(v):
                continue
            p = Path(os.path.normpath(v))
            if p.is_relative_to(REPO) and p.is_dir():
                rel = str(p.relative_to(REPO))
                rel = "" if rel == "." else rel
                if rel not in out:
                    out.append(rel)
    return out


def wheel_mapped_packages() -> dict[str, str]:
    """Top-level import name → repo directory, from the wheel's ``sources``
    mapping (what an installed CorvinOS puts at the site-packages root)."""
    try:
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return {}
    sect = text.split("[tool.hatch.build.targets.wheel.sources]", 1)
    if len(sect) < 2:
        return {}
    body = sect[1].split("\n[", 1)[0]
    return {name: src for src, name in re.findall(r'^\s*"([^"]+)"\s*=\s*"([\w.]+)"', body, re.M)}


class Index:
    def __init__(self, files: list[Path]):
        self.files = files
        self.file_set = set(files)
        self._trees: dict[Path, ast.AST | None] = {}
        self.mapped = {name: REPO / d for name, d in wheel_mapped_packages().items()}
        self.inserts_by_file: dict[Path, tuple[str, ...]] = {}
        roots: list[str] = []
        for f in files:
            t = self.tree(f)
            if t is None:
                continue
            ins = tuple(sys_path_inserts(f, t))
            if ins:
                self.inserts_by_file[f] = ins
                roots.extend(r for r in ins if r not in roots)
        #: Every directory production code puts on ``sys.path`` (measured).
        self.path_roots = tuple(sorted(roots))
        #: The ``sys.path`` roots ``resolve`` honours for a foreign importer.
        #: Every measured one until ``reachable()`` narrows it to the roots of
        #: REACHABLE files — a directory only a dead file inserts is on no
        #: running process's ``sys.path`` (review R5-1).
        self.active_roots: tuple[str, ...] = self.path_roots
        #: Names Python finds outside the repo (stdlib + declared third party).
        self.external = _STDLIB | third_party_names()
        self._resolved: dict[tuple[str, Path | None], list[Path]] = {}
        self.refs: dict[Path, tuple[set[Path], list[str], list[str]]] = {}
        self.root_refs: tuple[set[Path], list[str], list[tuple[str, Path]]] | None = None
        #: Every trailing run of path components → the files whose repo path
        #: ends with it ("voice/scripts/voice_audit.py" → [that file]).
        self.by_path_suffix: dict[tuple[str, ...], list[Path]] = defaultdict(list)
        for f in files:
            parts = f.relative_to(REPO).parts
            for i in range(len(parts)):
                self.by_path_suffix[parts[i:]].append(f)
        self.by_suffix: dict[str, list[Path]] = defaultdict(list)
        for f in files:
            parts = module_name(f).split(".")
            for i in range(len(parts)):
                self.by_suffix[".".join(parts[i:])].append(f)

    def tree(self, f: Path) -> ast.AST | None:
        if f not in self._trees:
            self._trees[f] = _parse(f)
        return self._trees[f]

    def _at(self, base: Path, rel: str) -> list[Path]:
        return [c for c in (base / (rel + ".py"), base / rel / "__init__.py") if c in self.file_set]

    @staticmethod
    def _unique(hits: list[Path]) -> list[Path] | None:
        """None: no match here, try the next step. []: ambiguous, link nowhere."""
        hits = list(dict.fromkeys(hits))
        if not hits:
            return None
        return hits if len(hits) == 1 else []

    def set_active_roots(self, active: tuple[str, ...]) -> None:
        self.active_roots = tuple(active)
        self._resolved.clear()

    def resolve(self, dotted: str, src: Path | None = None) -> list[Path]:
        """Files ``import <dotted>`` loads from ``src`` — at most one."""
        key = (dotted, src)
        if key not in self._resolved:
            self._resolved[key] = self._resolve(dotted, src)
        return self._resolved[key]

    def _resolve(self, dotted: str, src: Path | None) -> list[Path]:
        """Steps (first that matches wins): ``src``'s own directory; the repo
        root and wheel-mapped top-level packages; the directories ``src``
        itself puts on ``sys.path``; any ``sys.path`` root a REACHABLE file
        inserts (``active_roots``); then the name if it is UNIQUE as a dotted
        suffix in the tree. A step with more than one match returns no edge —
        never all of them (review R3-6, R4-16).

        A name whose top level is the stdlib or a declared third-party package
        (``self.external``) stops after the second step, and its first step
        counts only from a directory that is not a package — the script
        directory Python puts at ``sys.path[0]``. Inside a package an absolute
        ``import random`` is the stdlib; the sys.path, insert and suffix steps
        would otherwise turn ``import types`` into some ``…/types.py`` (R5-1)."""
        if not dotted:
            return []
        rel = dotted.replace(".", "/")
        head, _, tail = dotted.partition(".")
        external = head in self.external
        if src is not None and not (external and (src.parent / "__init__.py") in self.file_set):
            sib = self._at(src.parent, rel)
            if sib:
                return sib[:1]
        fixed = self._at(REPO, rel)
        if head in self.mapped:
            fixed += self._at(self.mapped[head], tail) if tail else \
                [c for c in (self.mapped[head] / "__init__.py",) if c in self.file_set]
        steps = [fixed]
        if not external:
            if src is not None and src in self.inserts_by_file:
                steps.append([c for r in self.inserts_by_file[src] for c in self._at(REPO / r, rel)])
            steps.append([c for r in self.active_roots for c in self._at(REPO / r, rel)])
            steps.append(self.by_suffix.get(dotted, []))
        for hits in steps:
            u = self._unique(hits)
            if u is not None:
                return u
        return []

    def resolve_path(self, mention: str, base_dir: Path) -> list[Path]:
        """The file a ``…/x.py`` mention (shell launcher, systemd unit, string
        literal) names — at most one. As written relative to ``base_dir`` (the
        mentioning file's directory) then to the repo root; failing that, the
        most specific unique repo-path suffix (``$ROOT/../voice/x.py`` →
        ``voice/x.py``). Variables (``$X``, ``${X}``, ``%h``) are opaque, so
        everything up to the last one is dropped. Ambiguous ⇒ nothing (R4-17)."""
        parts = mention.split("/")
        var = [i for i, s in enumerate(parts) if re.search(r"[$%{}]", s)]
        tail = parts[var[-1] + 1:] if var else parts
        if not tail or not tail[-1].endswith(".py"):
            return []
        written = "/".join(tail)
        if not var and mention.startswith("/"):
            p = Path(os.path.normpath(mention))
            if p in self.file_set:
                return [p]
        elif written:
            for base in (base_dir, REPO):
                p = Path(os.path.normpath(base / written))
                if p in self.file_set:
                    return [p]
        clean = [s for s in os.path.normpath("/".join(tail)).split("/") if s not in ("", ".", "..")]
        for k in range(len(clean), 0, -1):
            suf = tuple(clean[-k:])
            hits = self.by_path_suffix.get(suf, [])
            if hits:
                return hits if len(hits) == 1 else []
        return []


_MODSTR = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+(:[A-Za-z_]\w*)?$")


def _relative_target(f: Path, node: ast.ImportFrom) -> Path:
    anchor = f.parent
    for _ in range(node.level - 1):
        anchor = anchor.parent
    base = node.module or ""
    return anchor / base.replace(".", "/") if base else anchor


def _refs_of(f: Path, idx: Index) -> tuple[set[Path], list[str], list[str]]:
    """What ``f`` names, before resolution: (relative-import files, dotted
    module names, ``…/x.py`` path mentions). Parsed once per Index — the
    ``sys.path`` fixpoint re-resolves these, it never re-walks the AST."""
    if f in idx.refs:
        return idx.refs[f]
    files: set[Path] = set()
    mods: list[str] = []
    paths: list[str] = []
    tree = idx.tree(f)
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                tgt = _relative_target(f, node)
                for c in (tgt.with_suffix(".py"), tgt / "__init__.py"):
                    if c.is_file():
                        files.add(c)
                for a in node.names:
                    for c in ((tgt / a.name).with_suffix(".py"), tgt / a.name / "__init__.py"):
                        if c.is_file():
                            files.add(c)
            else:
                mods.append(base)
                mods.extend(f"{base}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.strip()
            if len(s) > 200:
                continue
            if _MODSTR.match(s):
                mods.append(s.split(":")[0])
            elif s.endswith(".py") and "/" in s:
                paths.append(s)
    idx.refs[f] = (files, mods, paths)
    return idx.refs[f]


def edges_of(f: Path, idx: Index) -> set[Path]:
    rel_files, mods, paths = _refs_of(f, idx)
    out: set[Path] = set(rel_files)
    for m in mods:
        out.update(idx.resolve(m, f))
    for s in paths:
        out.update(idx.resolve_path(s, f.parent))
    # Importing a.b.c executes a/__init__.py and a/b/__init__.py first.
    for target in list(out):
        parent = target.parent
        while parent != REPO and parent.is_relative_to(REPO):
            init = parent / "__init__.py"
            if init in idx.file_set:
                out.add(init)
            parent = parent.parent
    out.discard(f)
    return {p for p in out if p in idx.file_set}


def _repo_walk(match) -> list[Path]:
    """Every repo file ``match(name)`` accepts, pruning ``EXCLUDE_PARTS`` and
    ``.git`` on the way down (an ``rglob`` walks ``node_modules`` first)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_PARTS and d != ".git")
        out.extend(Path(dirpath) / n for n in sorted(filenames) if match(n))
    return out


#: ``python … -m pkg.mod`` on a line that runs a Python interpreter.
_DASH_M = re.compile(r"(?:^|\s)-m\s+([A-Za-z_][\w.]*)")
_PY_LINE = re.compile(r"python|\$\{?\w*PY", re.I)
#: A shell line that can start a script without naming the interpreter: an
#: assignment the launch line expands later, ``exec``/``nohup``, or the path
#: itself as the command (shebang).
_SH_LAUNCH = re.compile(r"(?:(?:export\s+|local\s+|readonly\s+)?[A-Za-z_]\w*=|exec\b|nohup\b|[\w./${}-]+\.py(?:\s|$))")


def _root_refs(idx: Index) -> tuple[set[Path], list[str], list[tuple[str, Path]]]:
    """Entry points before resolution: (files, ``-m``/console-script modules,
    (path mention, its file's directory)). Collected once per Index."""
    if idx.root_refs is not None:
        return idx.root_refs
    files: set[Path] = {REPO / r for r in EXTRA_ROOTS if (REPO / r).is_file()}
    mods: list[str] = []
    paths: list[tuple[str, Path]] = []
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    sect = pyproject.split("[project.scripts]", 1)[-1].split("\n[", 1)[0]
    mods.extend(re.findall(r'=\s*"([\w.]+):\w+"', sect))
    for unit in _repo_walk(lambda n: n.endswith(".service")):
        # systemd joins a line ending in a backslash with the next one.
        text = re.sub(r"\\[ \t]*\n", " ", unit.read_text(encoding="utf-8", errors="replace"))
        for line in text.splitlines():
            if not line.lstrip().startswith("ExecStart"):
                continue
            mods.extend(_DASH_M.findall(line))
            paths.extend((p, unit.parent) for p in re.findall(r"([\w./%{}$-]+\.py)\b", line))
    # Builtin plugins are loaded through their manifest, not imported: the real
    # loader (core/plugins/corvin_plugins/bootstrap.py::_builtin_plugin_dirs /
    # _load_builtin_class) walks for plugin.yaml and loads provider.py or
    # plugin.py by file path. A plugin.json entry_point is NOT loaded by it.
    for manifest in _repo_walk(lambda n: n == "plugin.yaml"):
        for fname in ("provider.py", "plugin.py"):
            cand = manifest.parent / fname
            if cand in idx.file_set:
                files.add(cand)
    # Shell launchers anywhere in the repo start python files by path or by -m.
    # Comments and echo/printf text are not launches: a stale status script
    # NAMING a module in a comment kept that module's package alive (R5-3).
    for sh in _repo_walk(lambda n: n.endswith(".sh")):
        text = re.sub(r"\\[ \t]*\n", " ", sh.read_text(encoding="utf-8", errors="replace"))
        for line in text.splitlines():
            line = re.sub(r"(?:^|\s)#.*", "", line).strip()
            if not line or re.match(r"(?:echo|printf)\b", line):
                continue
            if _PY_LINE.search(line):
                mods.extend(_DASH_M.findall(line))
            elif not _SH_LAUNCH.match(line):
                continue   # a file list, a heredoc body, a test -f …
            paths.extend((p, sh.parent) for p in re.findall(r"([\w./${}-]+\.py)\b", line))
    idx.root_refs = (files, mods, paths)
    return idx.root_refs


def roots(idx: Index) -> set[Path]:
    files, mods, paths = _root_refs(idx)
    rs = set(files)
    for m in mods:
        for hit in idx.resolve(m):
            rs.add(hit)
            # ``python -m pkg`` runs pkg/__main__.py (after pkg/__init__.py).
            main = hit.parent / "__main__.py"
            if hit.name == "__init__.py" and main in idx.file_set:
                rs.add(main)
    for p, base in paths:
        rs.update(idx.resolve_path(p, base))
    return rs


def reachable(idx: Index) -> set[Path]:
    """Files reachable from ``roots``. ``sys.path`` roots are taken only from
    files already reachable, iterated to a fixpoint: start with none, add the
    inserts of every reachable file, re-walk until the set stops growing. The
    root set only grows, so this terminates; leaves ``idx.active_roots`` at
    the fixpoint for the contract pass that follows (review R5-1)."""
    active: tuple[str, ...] = ()
    while True:
        idx.set_active_roots(active)
        seen = set(roots(idx))
        q = deque(seen)
        while q:
            for nxt in edges_of(q.popleft(), idx):
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        grown = tuple(sorted(set(active).union(
            *(idx.inserts_by_file.get(f, ()) for f in seen))))
        if grown == active:
            return seen
        active = grown


# ── contract classes, by (file, class) identity ─────────────────────────────

ClassId = tuple[str, str]   # (repo-relative file, class name); file "*" = unresolved


class _Classes:
    """Per-file class definitions and import aliases, so a base expression
    resolves to the class it actually names — ``HealthMonitor`` from
    ``core.observability`` is not the Brain Hub ``HealthMonitor`` (R4-18), and
    ``Subsystem as _Base`` is still ``Subsystem`` (R4-19)."""

    def __init__(self, idx: Index):
        self.idx = idx
        self.defs: dict[Path, set[str]] = {}
        # local name → ("cls", [module files], attr, [submodule files])
        #            | ("mod", [module files])
        self.aliases: dict[Path, dict[str, tuple]] = {}
        for f in idx.files:
            t = idx.tree(f)
            if t is None:
                continue
            self.defs[f] = {n.name for n in ast.walk(t) if isinstance(n, ast.ClassDef)}
            al: dict[str, tuple] = {}
            for n in ast.walk(t):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        if a.asname:
                            al[a.asname] = ("mod", idx.resolve(a.name, f))
                        else:
                            head = a.name.split(".")[0]
                            al.setdefault(head, ("mod", idx.resolve(head, f)))
                elif isinstance(n, ast.ImportFrom):
                    if n.level:
                        tgt = _relative_target(f, n)
                        mods = [c for c in (tgt.with_suffix(".py"), tgt / "__init__.py")
                                if c in idx.file_set]
                    else:
                        mods = idx.resolve(n.module or "", f)
                    for a in n.names:
                        if a.name == "*":
                            continue
                        local = a.asname or a.name
                        if n.level:
                            sub = [c for c in ((tgt / a.name).with_suffix(".py"),
                                               tgt / a.name / "__init__.py") if c in idx.file_set]
                        else:
                            sub = idx.resolve(f"{n.module}.{a.name}", f) if n.module else []
                        # `from pkg import name` may bind a class defined in
                        # (or re-exported by) pkg, or the submodule pkg/name.py;
                        # keep both, the class lookup tries pkg first.
                        al[local] = ("cls", mods, a.name, sub)
            self.aliases[f] = al

    def lookup(self, f: Path, name: str, depth: int = 0) -> set[ClassId]:
        """The class(es) ``name`` denotes inside module ``f`` (follows
        re-exports through package ``__init__`` files)."""
        if depth > 8 or f not in self.defs:
            return set()
        if name in self.defs[f]:
            return {(str(f.relative_to(REPO)), name)}
        a = self.aliases[f].get(name)
        if a and a[0] == "cls":
            out: set[ClassId] = set()
            for m in a[1]:
                out |= self.lookup(m, a[2], depth + 1)
            return out
        return set()

    def bases_of(self, f: Path, node: ast.ClassDef) -> set[ClassId]:
        out: set[ClassId] = set()
        for b in node.bases:
            if isinstance(b, ast.Subscript):
                b = b.value
            ids: set[ClassId] = set()
            if isinstance(b, ast.Name):
                last = b.id
                ids = self.lookup(f, b.id)
            elif isinstance(b, ast.Attribute):
                last = b.attr
                chain = []
                v: ast.AST = b.value
                while isinstance(v, ast.Attribute):
                    chain.append(v.attr)
                    v = v.value
                if isinstance(v, ast.Name):
                    chain.append(v.id)
                    chain.reverse()
                    a = self.aliases.get(f, {}).get(chain[0])
                    mods: list[Path] = []
                    if a and a[0] == "mod" and len(chain) == 1:
                        mods = a[1]
                    elif a and a[0] == "cls" and len(chain) == 1:
                        mods = a[3]
                    else:
                        mods = self.idx.resolve(".".join(chain), f)
                    for m in mods:
                        ids |= self.lookup(m, b.attr)
            else:
                continue
            if not ids and last in CONTRACTS:
                ids = {("*", last)}   # unresolvable, but named like a contract base
            out |= ids
        return out


def _plugin_members(node: ast.ClassDef) -> set[str]:
    """Which of ``PLUGIN_SHAPE`` a class body defines: a method, a class
    attribute (assigned or annotated) or ``self.<name> = …`` in a method."""
    out: set[str] = set()
    for st in node.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(st.name)
            for n in ast.walk(st):
                tgts = n.targets if isinstance(n, ast.Assign) else \
                    [n.target] if isinstance(n, ast.AnnAssign) else []
                out.update(t.attr for t in tgts if isinstance(t, ast.Attribute)
                           and isinstance(t.value, ast.Name) and t.value.id == "self")
        elif isinstance(st, ast.Assign):
            out.update(t.id for t in st.targets if isinstance(t, ast.Name))
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            out.add(st.target.id)
    return out & PLUGIN_SHAPE


def _contract_classes(idx: Index) -> tuple[_Classes, dict[ClassId, str]]:
    """ClassId → contract, closed over subclassing by identity: ``class B(A)``
    implements a contract only if the ``A`` it imports does (R3-18, R4-18)."""
    cls = _Classes(idx)
    known: dict[ClassId, str] = {}
    edges: list[tuple[ClassId, ClassId]] = []
    shape: dict[ClassId, set[str]] = {}
    protocols: set[ClassId] = set()
    for f in idx.files:
        t = idx.tree(f)
        if t is None:
            continue
        rel = str(f.relative_to(REPO))
        for node in ast.walk(t):
            if isinstance(node, ast.ClassDef):
                me = (rel, node.name)
                if node.name in CONTRACTS:
                    known.setdefault(me, node.name)
                shape[me] = _plugin_members(node)
                if any((b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")) == "Protocol"
                       for b in node.bases):
                    protocols.add(me)
                for b in cls.bases_of(f, node):
                    if b != me:
                        edges.append((me, b))
    for c in CONTRACTS:
        known[("*", c)] = c
    # The plugin contract is the lifecycle SHAPE, inherited through in-repo
    # bases; a Protocol declaring the shape is a contract, not an implementation.
    changed = True
    while changed:
        changed = False
        for child, base in edges:
            extra = shape.get(base, set()) - shape.get(child, set())
            if extra and child in shape:
                shape[child] |= extra
                changed = True
    for me, members in shape.items():
        if PLUGIN_SHAPE <= members and me not in protocols:
            known.setdefault(me, "CorvinPlugin")
    changed = True
    while changed:
        changed = False
        for child, base in edges:
            if base in known and child not in known:
                known[child] = known[base]
                changed = True
    return cls, known


def implementations(idx: Index) -> list[dict]:
    cls, contract_of = _contract_classes(idx)
    out = []
    for f in idx.files:
        tree = idx.tree(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name not in CONTRACTS:
                hit = contract_of.get((str(f.relative_to(REPO)), node.name)) or next(
                    (contract_of[b] for b in sorted(cls.bases_of(f, node)) if b in contract_of), None)
                if hit:
                    out.append({"file": str(f.relative_to(REPO)), "class": node.name,
                                "contract": hit, "kind": CONTRACTS[hit]})
        if f.name != "registry.py" and any(
                isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "register_stage"
                for n in ast.walk(tree)):
            out.append({"file": str(f.relative_to(REPO)), "class": "(register_stage)",
                        "contract": "ContextStage", "kind": CONTRACTS["ContextStage"]})
    return out


def unscheduled_stages() -> list[dict]:
    """CEL stages that are registered (and so importable) but named by no
    SHIPPED pipeline list. A tenant's own ``tenant.corvin.yaml`` pipeline may
    still schedule one, so these are reported as their own kind."""
    cfg = REPO / "corvin_operator" / "context_engineering" / "stages" / "config.py"
    stages_dir = cfg.parent
    if not cfg.is_file():
        return []
    shipped = set(re.findall(r'"([a-z_0-9]+)"', cfg.read_text(encoding="utf-8")))
    out = []
    for f in sorted(stages_dir.glob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                sid = next((st.value.value for st in node.body
                            if isinstance(st, ast.Assign) and isinstance(st.value, ast.Constant)
                            and any(getattr(t, "id", "") == "id" for t in st.targets)
                            and isinstance(st.value.value, str)), None)
                if sid and sid not in shipped:
                    out.append({"file": str(f.relative_to(REPO)), "class": node.name,
                                "contract": "ContextStage",
                                "kind": f"CEL stage '{sid}' in no shipped pipeline"})
    return out


def sweep() -> dict:
    idx = Index(production_files())
    live = reachable(idx)
    impls = implementations(idx)
    dead = [i for i in impls if (REPO / i["file"]) not in live] + unscheduled_stages()
    return {"modules": len(idx.files), "reachable": len(live),
            "implementations": len(impls), "dead": sorted(dead, key=lambda d: (d["file"], d["class"]))}


def _key(d: dict) -> str:
    return f'{d["file"]}::{d["class"]}'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when a dead implementation is not in the baseline")
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args(argv)
    rep = sweep()
    if a.write_baseline:
        BASELINE.write_text(json.dumps(sorted(_key(d) for d in rep["dead"]), indent=1) + "\n",
                            encoding="utf-8")
        print(f"baseline written: {len(rep['dead'])} known-dead implementation(s)")
        return 0
    base = set(json.loads(BASELINE.read_text())) if BASELINE.is_file() else set()
    new = [d for d in rep["dead"] if _key(d) not in base]
    gone = sorted(base - {_key(d) for d in rep["dead"]})
    if a.json:
        print(json.dumps({**rep, "new": new, "fixed_since_baseline": gone}, indent=1))
    else:
        for d in rep["dead"]:
            flag = "NEW " if d in new else ""
            why = ("registered, scheduled by no shipped pipeline" if "pipeline" in d["kind"]
                   else "no path from any entry point")
            print(f"{flag}{d['file']}: {d['class']} ({d['kind']}) — {why}")
        print(f"{rep['modules']} modules, {rep['reachable']} reachable from entry points; "
              f"{rep['implementations']} contract implementations, {len(rep['dead'])} unreachable "
              f"({len(new)} not in baseline, {len(gone)} baseline entries now wired/removed)")
    # A stale baseline line (the implementation was wired or deleted) must be
    # removed too, or it would later mask the same class going dead again.
    return 1 if (a.check and (new or gone)) else 0


if __name__ == "__main__":
    sys.exit(main())
