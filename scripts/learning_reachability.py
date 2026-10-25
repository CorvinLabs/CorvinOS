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
import json
import sys
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
        live = zcs.reachable(idx)
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
