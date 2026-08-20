#!/usr/bin/env python3
"""Seed / purge fictional skills in a tenant's SkillForge registry (ADR-0405).

Test infrastructure for the live Playwright suite. Deliberately NOT an HTTP
endpoint: the Skill-Creator has no create-without-generating route by design
(creating a skill means running the phases), and adding a fixture endpoint to
the production API to make a test convenient is how test-only surface ends up
shipped.

Uses `registry_bridge` — the same code path the generator promotes through —
so a seeded skill is indistinguishable from a generated one to every reader.

Usage:
    seed_skills.py seed  --json '[{"name": ..., "description": ..., "body": ..., "grade": 0.3}]'
    seed_skills.py purge --prefix assistant.e2efixture_

`grade` is optional: omit it (or pass null) to leave the skill UNGRADED, which
is what puts it below skill_inject's eligibility gate — the "inert" state the
UI has to render honestly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[7]
sys.path.insert(0, str(_REPO / "operator"))

from skill_creator.registry_bridge import (  # noqa: E402
    delete_skill,
    list_skills,
    registry_for,
)


def registry_root(tenant_id: str) -> Path:
    """`<tenant_home>/skill-forge` — the root skill_inject actually reads."""
    home = Path(os.environ.get("CORVIN_HOME") or (_REPO / ".corvin"))
    return home / "tenants" / tenant_id / "skill-forge"


def seed(root: Path, skills: list[dict]) -> list[str]:
    registry = registry_for(root)
    created: list[str] = []
    for entry in skills:
        name = entry["name"]
        registry.create(
            name=name,
            type=entry.get("type", "learned-experience"),
            body_md=entry["body"],
            description=entry["description"],
            scope=entry.get("scope", "user"),
            overwrite=True,
            created_by="e2e-fixture",
        )
        grade = entry.get("grade")
        if grade is not None:
            registry.grade(name, run_id="e2e-fixture", score=float(grade),
                           notes="e2e fixture seed — not earned usage")
        created.append(name)
    return created


def purge(root: Path, prefix: str) -> list[str]:
    removed = []
    for skill in list_skills(root):
        if skill["name"].startswith(prefix):
            if delete_skill(root, skill["name"], reason="e2e fixture purge"):
                removed.append(skill["name"])
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("seed", "purge", "list"))
    ap.add_argument("--tenant", default=os.environ.get("CORVIN_TENANT_ID", "_default"))
    ap.add_argument("--json", default="[]")
    ap.add_argument("--prefix", default="assistant.e2efixture_")
    args = ap.parse_args()

    root = registry_root(args.tenant)

    if args.action == "seed":
        out = seed(root, json.loads(args.json))
    elif args.action == "purge":
        out = purge(root, args.prefix)
    else:
        out = [s["name"] for s in list_skills(root)]

    print(json.dumps({"ok": True, "root": str(root), "skills": out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
