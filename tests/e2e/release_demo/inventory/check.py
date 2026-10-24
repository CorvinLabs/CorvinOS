"""Consistency gate for the release-readiness inventory (W-0b).

Fails (exit 1) when:
  drift     the committed inventory.json is not what generate.py produces from the source now;
  unmapped  an inventory entry has no entry in assertion_map.json (a new page, deep link or
            button-tab must be decided on explicitly);
  stale     assertion_map.json names an id the inventory no longer has;
  no reason a mapping is "pending" without a reason.

A mapping is {"status": "mapped", "assertion": "<gate predicate id>"} or
{"status": "pending", "reason": "<why it is not mapped yet>"}. This package maps nothing
(panel assertions are W-6); every entry is therefore pending with that reason.

Usage: python3 tests/e2e/release_demo/inventory/check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import generate  # noqa: E402

DEFAULT_INVENTORY = HERE / "inventory.json"
DEFAULT_MAP = HERE / "assertion_map.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check(repo_root: Path, inventory_path: Path | None = None, map_path: Path | None = None) -> tuple[list[str], dict]:
    inventory_path = inventory_path or DEFAULT_INVENTORY
    map_path = map_path or DEFAULT_MAP
    errors: list[str] = []

    if not inventory_path.exists():
        errors.append(f"drift: {inventory_path.name} is missing; run generate.py")
        committed = {"entries": []}
    else:
        committed = _load(inventory_path)
        generated = generate.build_inventory(repo_root)
        if generate.render(committed) != generate.render(generated):
            errors.append(f"drift: {inventory_path.name} differs from the generator output; run generate.py and commit it")

    mappings: dict = {}
    if map_path.exists():
        mappings = _load(map_path).get("mappings", {})
    else:
        errors.append(f"unmapped: {map_path.name} is missing; every inventory entry needs a mapping")

    entry_ids = [e["id"] for e in committed.get("entries", [])]

    # Consumers (capture file names, gate rows) key on the explicit slug; it must be present,
    # well-formed and unique even if someone edits inventory.json by hand.
    for e in committed.get("entries", []):
        s = e.get("slug")
        if not s or not generate.SLUG_RE.match(s):
            errors.append(f"slug: {e['id']} has no well-formed slug ({s!r})")
    seen: dict[str, str] = {}
    for e in committed.get("entries", []):
        s = e.get("slug")
        if s and s in seen:
            errors.append(f"slug: {s!r} is shared by {seen[s]} and {e['id']}")
        elif s:
            seen[s] = e["id"]
    for eid in entry_ids:
        if eid not in mappings:
            errors.append(f"unmapped: {eid} has no assertion mapping in {map_path.name}")
    for mid in sorted(mappings):
        if mid not in entry_ids:
            errors.append(f"stale: mapping {mid} is not in the inventory")
            continue
        m = mappings[mid]
        status = m.get("status")
        if status == "mapped":
            if not m.get("assertion"):
                errors.append(f"mapped without assertion: {mid}")
        elif status == "pending":
            if not str(m.get("reason", "")).strip():
                errors.append(f"pending with no reason: {mid}")
        else:
            errors.append(f"bad status {status!r} for {mid}")

    pending = sum(1 for mid in entry_ids if mappings.get(mid, {}).get("status") == "pending")
    summary = {"entries": len(entry_ids), "mapped": len(entry_ids) - pending, "pending": pending}
    return errors, summary


def main() -> int:
    repo = HERE.parents[3]
    errors, summary = check(repo)
    if errors:
        for e in errors:
            print(f"FAIL {e}")
        print(f"inventory check: {len(errors)} problem(s); entries={summary['entries']}")
        return 1
    print(f"inventory check: OK entries={summary['entries']} mapped={summary['mapped']} pending={summary['pending']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
