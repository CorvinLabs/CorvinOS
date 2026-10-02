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
     Edges: ``import``/``from`` statements (absolute, relative, and bare names
     resolved the way the bridges' ``sys.path`` inserts resolve them), plus any
     string literal naming a repo module (``"pkg.mod:Class"`` registries,
     ``importlib.import_module("…")``, file paths ending in ``.py``). Strings
     over-approximate reachability, so a module reported dead has no import
     path. "Imported" is not "used": a class reached only through a package
     ``__init__`` re-export counts as reachable here.
  2. Roots: every ``[project.scripts]`` target, every module/script a systemd
     unit or shell launcher in the repo starts, every builtin plugin the
     plugin bootstrap loads (``plugin.yaml`` → ``provider.py``/``plugin.py``),
     and the long-running hosts (bridge adapter, gateway app, console
     standalone). Importing a module also reaches its packages' ``__init__``.
  3. Contract implementations: subclasses of the registration bases listed in
     ``CONTRACTS`` and every module calling ``register_stage(``.
  4. Report each implementation whose module is not reachable from a root.

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
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "zero_caller_baseline.json"
SCAN_DIRS = ("core", "corvin_operator", "ops", "corvinOS", "scripts")
EXCLUDE_PARTS = {"tests", "test", "node_modules", ".venv", "venv", "__pycache__",
                 "worktrees", ".claude", "archived_v2", "dist", "build"}

#: Registration bases: a subclass is only useful once something registers it.
CONTRACTS = {
    "Subsystem": "Brain Hub subsystem (ADR-0347)",
    "BaseSkill": "OS skill, phase-1 base (ADR-0535)",
    "Skill": "OS skill (skill_registry_phase1, ADR-0532)",
    "ContextStage": "CEL stage (ADR-0277)",
    "Plugin": "plugin type",
    "BasePlugin": "plugin type (BasePlugin)",
}

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


class Index:
    def __init__(self, files: list[Path]):
        self.files = files
        self.file_set = set(files)
        # The parent of every TOP-LEVEL package is where that package imports
        # from (core/orchestration for `-m corvin_orchestration.mcp_server`).
        roots = set()
        for f in files:
            if f.name == "__init__.py" and not (f.parent.parent / "__init__.py").exists():
                rel = f.parent.parent.relative_to(REPO)
                if str(rel) != ".":
                    roots.add(str(rel))
        self.package_roots = tuple(sorted(roots))
        self.by_mod: dict[str, Path] = {}
        self.by_base: dict[str, list[Path]] = defaultdict(list)
        for f in files:
            m = module_name(f)
            self.by_mod[m] = f
            self.by_base[f.stem if f.stem != "__init__" else f.parent.name].append(f)
        # Dotted suffixes ("forge.security_events", "corvin_console.app"): the
        # repo puts many package parents on sys.path, so a module is importable
        # under every suffix of its repo path.
        self.by_suffix: dict[str, list[Path]] = defaultdict(list)
        for m, f in self.by_mod.items():
            parts = m.split(".")
            for i in range(len(parts)):
                self.by_suffix[".".join(parts[i:])].append(f)

    def resolve(self, dotted: str, src: Path | None = None) -> list[Path]:
        """Files ``import <dotted>`` can load from ``src``.

        Order: the source's own package (sibling); then modules whose dotted
        path is exact relative to the repo root or to one of PATH_ROOTS (the
        directories production code puts on ``sys.path``); then, only if the
        name is UNIQUE in the tree, that one file. An ambiguous bare name that
        none of these explains links nowhere — linking every same-named file
        let one live ``session_ledger`` keep any other ``session_ledger.py``
        "reachable" (review R3-6)."""
        if not dotted:
            return []
        rel = dotted.replace(".", "/")
        if src is not None:
            for c in (src.parent / (rel + ".py"), src.parent / rel / "__init__.py"):
                if c in self.file_set:
                    return [c]
        exact = []
        for root in ("",) + PATH_ROOTS + self.package_roots:
            base = REPO / root if root else REPO
            for c in (base / (rel + ".py"), base / rel / "__init__.py"):
                if c in self.file_set:
                    exact.append(c)
        if exact:
            return exact
        hits = list(self.by_suffix.get(dotted, []))
        return hits if len(hits) == 1 else []


#: Directories production code inserts into ``sys.path`` (measured 2026-10-02
#: from the sys.path.insert/append calls in core/, corvin_operator/, ops/).
PATH_ROOTS: tuple[str, ...] = (
    "corvin_operator", "corvin_operator/bridges/shared", "corvin_operator/forge",
    "corvin_operator/skill-forge", "corvin_operator/bridges/shared/agents",
    "core", "core/console", "core/plugins", "core/gateway", "core/compute",
    "core/console/corvin_console", "ops/launcher",
)


def _common(a: Path, b: Path) -> tuple:
    out = []
    for x, y in zip(a.parts, b.parts):
        if x != y:
            break
        out.append(x)
    return tuple(out) or (".",)


_MODSTR = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+(:[A-Za-z_]\w*)?$")


def edges_of(f: Path, idx: Index) -> set[Path]:
    try:
        tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    out: set[Path] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.update(idx.resolve(a.name, f))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = f.parent
                for _ in range(node.level - 1):
                    anchor = anchor.parent
                tgt = anchor / base.replace(".", "/") if base else anchor
                for c in (tgt.with_suffix(".py"), tgt / "__init__.py"):
                    if c.is_file():
                        out.add(c)
                for a in node.names:
                    for c in ((tgt / a.name).with_suffix(".py"), tgt / a.name / "__init__.py"):
                        if c.is_file():
                            out.add(c)
            else:
                out.update(idx.resolve(base, f))
                for a in node.names:
                    out.update(idx.resolve(f"{base}.{a.name}", f))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.strip()
            if len(s) > 200:
                continue
            if _MODSTR.match(s):
                out.update(idx.resolve(s.split(":")[0], f))
            elif s.endswith(".py") and "/" in s:
                cand = (REPO / s) if not s.startswith("/") else Path(s)
                if cand.is_file():
                    out.add(cand)
                else:
                    out.update(idx.by_base.get(Path(s).stem, [])[:2])
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


def roots(idx: Index) -> set[Path]:
    rs: set[Path] = set()
    for r in EXTRA_ROOTS:
        if (REPO / r).is_file():
            rs.add(REPO / r)
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    sect = pyproject.split("[project.scripts]", 1)[-1].split("\n[", 1)[0]
    for m in re.findall(r'=\s*"([\w.]+):\w+"', sect):
        rs.update(idx.resolve(m))
    for unit in REPO.rglob("*.service"):
        if EXCLUDE_PARTS & set(unit.relative_to(REPO).parts):
            continue
        for line in unit.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("ExecStart"):
                continue
            for m in re.findall(r"-m\s+([\w.]+)", line):
                rs.update(idx.resolve(m))
            for p in re.findall(r"([\w./%{}-]+\.py)\b", line):
                rs.update(idx.by_base.get(Path(p).stem, [])[:3])
    # Builtin plugins are loaded through their manifest, not imported: the real
    # loader (core/plugins/corvin_plugins/bootstrap.py::_builtin_plugin_dirs /
    # _load_builtin_class) walks for plugin.yaml and loads provider.py or
    # plugin.py by file path. A plugin.json entry_point is NOT loaded by it.
    for manifest in REPO.rglob("plugin.yaml"):
        if EXCLUDE_PARTS & set(manifest.relative_to(REPO).parts):
            continue
        for fname in ("provider.py", "plugin.py"):
            cand = manifest.parent / fname
            if cand in idx.file_set:
                rs.add(cand)
    # bridge.sh and other shell launchers start python files by path.
    for sh in list((REPO / "corvin_operator").rglob("*.sh")) + list((REPO / "ops").rglob("*.sh")):
        if EXCLUDE_PARTS & set(sh.relative_to(REPO).parts):
            continue
        for p in re.findall(r"([\w./${}-]+\.py)\b", sh.read_text(encoding="utf-8", errors="replace")):
            rs.update(idx.by_base.get(Path(p).stem, [])[:3])
    return rs


def reachable(idx: Index) -> set[Path]:
    graph = {f: edges_of(f, idx) for f in idx.files}
    seen = set(roots(idx))
    q = deque(seen)
    while q:
        for nxt in graph.get(q.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return seen


def _contract_classes(idx: Index) -> dict[str, str]:
    """Class name → contract, closed over subclassing: ``class B(A)`` where A
    implements a contract implements it too (review R3-18)."""
    edges: list[tuple[str, str]] = []
    for f in idx.files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for b in node.bases:
                    name = b.id if isinstance(b, ast.Name) else (
                        b.attr if isinstance(b, ast.Attribute) else
                        (b.value.id if isinstance(b, ast.Subscript) and isinstance(b.value, ast.Name) else None))
                    if name and name != node.name:
                        edges.append((node.name, name))
    known = {c: c for c in CONTRACTS}
    changed = True
    while changed:
        changed = False
        for child, base in edges:
            if base in known and child not in known:
                known[child] = known[base]
                changed = True
    return known


def implementations(idx: Index) -> list[dict]:
    contract_of = _contract_classes(idx)
    out = []
    for f in idx.files:
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for b in node.bases:
                    name = b.id if isinstance(b, ast.Name) else (
                        b.attr if isinstance(b, ast.Attribute) else
                        (b.value.id if isinstance(b, ast.Subscript) and isinstance(b.value, ast.Name) else None))
                    if name in contract_of and node.name != name and node.name not in CONTRACTS:
                        c = contract_of[name]
                        out.append({"file": str(f.relative_to(REPO)), "class": node.name,
                                    "contract": c, "kind": CONTRACTS[c]})
                        break
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
