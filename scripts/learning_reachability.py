#!/usr/bin/env python3
"""Learning reachability ratchet (ADR-2281 §3).

Lists every production module in the learning scopes that no entry point
reaches, using the SAME import graph as ``scripts/zero_caller_sweep.py``
(``Index(production_files())`` + ``reachable()``) — there is no second graph.
``zero_caller_sweep`` itself only reports registration-style contracts
(Subsystem, Skill, Stage, Plugin); this script applies its reachability to
plain modules, restricted to the learning scopes so the signal is not drowned
by the repo-wide backlog.

The baseline (``scripts/learning_reachability_baseline.json``) records the
unreachable modules known today. It only shrinks:

* a module that is unreachable and NOT in the baseline fails (a new dead
  learning module);
* a baseline entry whose module is now reachable or deleted fails too (stale:
  it would later mask the same path going dead again) until it is removed.

"Unreachable" has the sweep's meaning: no statically resolvable import path
from any entry point. Dynamic imports are invisible to it, so an entry is a
finding to verify, not a proof.

Usage:
  python3 -I scripts/learning_reachability.py            # report
  python3 -I scripts/learning_reachability.py --check    # exit 1 on new or stale
  python3 -I scripts/learning_reachability.py --json
  python3 -I scripts/learning_reachability.py --write-baseline
  python3 -I scripts/learning_reachability.py --repo DIR --baseline FILE --check
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zero_caller_sweep as zcs  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "learning_reachability_baseline.json"

#: The learning scopes named by ADR-2281 §3 (repo-relative directories).
SCOPES = (
    "core/learning",
    "core/skills/os_skills/flow_guard",
    "core/skills/os_skills/definition_of_done_verifier",
    "core/skills/os_skills/delegation_router",
    "core/skills/os_skills/workflow_optimizer_skill",
    "core/skills/feedback",
    "core/marketplace",
    "core/vibe",
    "core/vibe_engineering",
    "core/orchestration",
)


def _in_scope(rel: str) -> bool:
    return any(rel == s or rel.startswith(s + "/") for s in SCOPES)


def _explicit_edges(f: Path, idx) -> set[Path]:
    """What ``f`` names itself (``zcs.edges_of`` minus the implied parent ``__init__``)."""
    rel_files, mods, paths = zcs._refs_of(f, idx)
    out: set[Path] = set(rel_files)
    for m in mods:
        out.update(idx.resolve(m, f))
    for s in paths:
        out.update(idx.resolve_path(s, f.parent))
    out.discard(f)
    return {p for p in out if p in idx.file_set}


#: Package ``__init__`` files whose re-exports do not count as reachability
#: (LRN-19). Only ``core/learning`` today: applying the rule to every scoped
#: package (core/vibe_engineering, core/skills/feedback, the DoD verifier)
#: exposes 17 further modules that are loaded only through their package
#: ``__init__`` - a separate retire-or-keep decision per module, not a baseline
#: entry (review 2026-11-05).
REEXPORT_BLIND_INITS = ("core/learning/__init__.py",)


def _is_scoped_package_init(f: Path) -> bool:
    return f.name == "__init__.py" and f.relative_to(zcs.REPO).as_posix() in REEXPORT_BLIND_INITS


def _from_targets(f: Path, node: ast.ImportFrom, idx) -> list[Path]:
    """The module file(s) an ``ImportFrom`` names as its source module."""
    if node.level:
        tgt = zcs._relative_target(f, node)
        return [c for c in (tgt.with_suffix(".py"), tgt / "__init__.py") if c in idx.file_set]
    return list(idx.resolve(node.module or "", f)) if node.module else []


def _name_targets(f: Path, node: ast.ImportFrom, name: str, idx) -> list[Path]:
    """The file(s) ``from <node.module> import <name>`` loads: a submodule named
    ``name``, else the module the name is defined or re-exported in."""
    if node.level:
        tgt = zcs._relative_target(f, node) / name
        subs = [c for c in (tgt.with_suffix(".py"), tgt / "__init__.py") if c in idx.file_set]
    else:
        subs = list(idx.resolve(f"{node.module}.{name}", f)) if node.module else []
    return subs


def _reexports(init: Path, idx, cache: dict) -> dict[str, set[Path]]:
    """``name -> files`` for every name a package ``__init__`` imports (re-exports)."""
    if init in cache:
        return cache[init]
    out: dict[str, set[Path]] = {}
    tree = idx.tree(init)
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, ast.ImportFrom):
            src = set(_from_targets(init, node, idx))
            for a in node.names:
                files = set(_name_targets(init, node, a.name, idx)) or src
                out.setdefault(a.asname or a.name, set()).update(files)
        elif isinstance(node, ast.Import):
            for a in node.names:
                out.setdefault(a.asname or a.name.split(".")[0], set()).update(
                    idx.resolve(a.name, init))
    cache[init] = out
    return out


def _edges_split(f: Path, idx, cache: dict) -> tuple[set[Path], set[Path]]:
    """(edges to follow, scoped package inits reached without using their re-exports).

    ``from pkg import A`` where ``pkg/__init__.py`` is a scoped package init and ``A``
    is not a submodule follows only the file ``A`` is re-exported from; ``import pkg``
    and ``from pkg import *`` keep the whole ``__init__``; a module-path string reaches
    the ``__init__`` without its re-exports."""
    direct = _explicit_edges(f, idx)
    implied = zcs.edges_of(f, idx) - direct
    full: set[Path] = set()
    partial: set[Path] = set()
    named: set[Path] = set()
    tree = idx.tree(f)
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, ast.Import):
            for a in node.names:
                full.update(p for p in idx.resolve(a.name, f) if _is_scoped_package_init(p))
        elif isinstance(node, ast.ImportFrom):
            for p in _from_targets(f, node, idx):
                if not _is_scoped_package_init(p):
                    continue
                if any(a.name == "*" for a in node.names):
                    full.add(p)
                    continue
                partial.add(p)
                rex = _reexports(p, idx, cache)
                for a in node.names:
                    if not _name_targets(f, node, a.name, idx):
                        named.update(rex.get(a.name, ()))
    # A module-path STRING naming the package (``soft_dependencies = ["core.learning"]``
    # in the DoD verifier) reaches the ``__init__`` but uses none of its re-exports.
    held = {p for p in direct if _is_scoped_package_init(p) and p not in full}
    follow = (direct - held) | {n for n in named if n in idx.file_set}
    return follow, held | {p for p in implied if _is_scoped_package_init(p)} | (implied - follow)


def reachable_without_reexports(idx) -> set[Path]:
    """``zcs.reachable`` without counting package re-exports as reachability.

    A package ``__init__`` in :data:`REEXPORT_BLIND_INITS` is reachable when anything imports it
    or a submodule of it, but its OWN imports are followed only as far as they are
    used: ``from core.learning import LearningIntegration`` reaches
    ``integration.py`` (and what it imports), not every module the ``__init__``
    re-exports; ``import core.learning.event_store`` reaches none of them.
    ``core/learning/__init__.py`` re-exports integration, storage, active_loop,
    metrics, ... and the plain sweep therefore marked them all reachable on any
    ``core.learning.*`` import, so the ratchet could not see a dead learning module
    (review 2026-11-05 LRN-19). ``import pkg`` / ``from pkg import *`` keep
    following the whole ``__init__``. Same roots, edges and
    ``sys.path`` fixpoint as ``zcs.reachable`` otherwise (one graph)."""
    cache: dict = {}
    active: tuple[str, ...] = ()
    while True:
        idx.set_active_roots(active)
        start = set(zcs.roots(idx))
        seen = set(start)
        expanded: set[Path] = set()
        q = deque(start)
        while q:
            f = q.popleft()
            if f in expanded:
                continue
            expanded.add(f)
            follow, held = _edges_split(f, idx, cache)
            seen.update(held)
            for nxt in follow:
                if nxt not in seen or nxt not in expanded:
                    seen.add(nxt)
                    q.append(nxt)
            # a held package init that is not re-export blind keeps the plain
            # sweep's behaviour
            for h in held:
                if not _is_scoped_package_init(h) and h not in expanded:
                    q.append(h)
        grown = tuple(sorted(set(active).union(
            *(idx.inserts_by_file.get(f, ()) for f in seen))))
        if grown == active:
            return seen
        active = grown


def unreachable(repo: Path | None = None) -> tuple[int, list[str]]:
    """(number of scoped production modules, sorted unreachable ones).

    ``repo`` re-points the sweep at another tree (a sandbox copy in the
    ratchet's self-test); the sweep reads its root from ``zcs.REPO`` at call
    time, so it is set for the duration of the call and restored after."""
    saved = zcs.REPO
    if repo is not None:
        zcs.REPO = Path(repo).resolve()
    try:
        idx = zcs.Index(zcs.production_files())
        live = reachable_without_reexports(idx)
        scoped = [f for f in idx.files if _in_scope(f.relative_to(zcs.REPO).as_posix())]
        dead = sorted(f.relative_to(zcs.REPO).as_posix() for f in scoped if f not in live)
        return len(scoped), dead
    finally:
        zcs.REPO = saved


def compare(dead: list[str], baseline: list[str]) -> tuple[list[str], list[str]]:
    """(new: unreachable but not baselined, stale: baselined but not unreachable)."""
    base = set(baseline)
    return sorted(set(dead) - base), sorted(base - set(dead))


def load_baseline(path: Path = BASELINE) -> list[str]:
    if not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8"))["unreachable"])


def write_baseline(dead: list[str], path: Path = BASELINE) -> None:
    payload = {
        "_comment": ("Unreachable learning modules (ADR-2281 §3). Shrink-only: "
                     "remove an entry when its module is wired or deleted; never add "
                     "one to make a new dead module pass."),
        "unreachable": sorted(dead),
    }
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 on a new or stale entry")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--repo", type=Path, default=None, help="tree to sweep (default: this repo)")
    ap.add_argument("--baseline", type=Path, default=BASELINE)
    a = ap.parse_args(argv)
    n, dead = unreachable(a.repo)
    if a.write_baseline:
        write_baseline(dead, a.baseline)
        print(f"baseline written: {len(dead)} unreachable learning module(s) of {n}")
        return 0
    new, stale = compare(dead, load_baseline(a.baseline))
    if a.json:
        print(json.dumps({"scoped_modules": n, "unreachable": dead,
                          "new": new, "stale": stale}, indent=1))
    else:
        for d in dead:
            print(("NEW " if d in new else "    ") + d)
        for s in stale:
            print(f"STALE {s} (baselined, but reachable or deleted: remove it from the baseline)")
        print(f"{n} learning modules in scope, {len(dead)} unreachable "
              f"({len(new)} not in baseline, {len(stale)} stale baseline entries)")
    return 1 if (a.check and (new or stale)) else 0


if __name__ == "__main__":
    sys.exit(main())
