"""One-time import of ``initiatives.json`` into the Task-Tracking SSOT (ADR-2056 §6).

    python -m corvin_console.task_tracking_import [--tenant _default] [--apply]

Dry run by default (prints what would be inserted). Insert-only and idempotent:
every item carries ``external_ref = initiatives.json#<path>``, and a ref that is
already in the store is skipped — never updated, because after the import the
SSOT is authoritative. The file itself is not touched.

Mapping::

    initiative                 → kind=initiative
    task group ("P0 — …")      → kind=epic under the initiative
    task                       → kind=task (status/progress as the board derived them,
                                 i.e. evidence-derived where evidence exists)
    precondition               → kind=task, category=precondition
    checkpoint                 → kind=task, category=checkpoint
    gate                       → kind=task, category=gate, approval_state from decision
      gate criterion           → kind=subtask, category=criterion
    initiative.blocked_by gate → dependency initiative → gate
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Any

from . import initiatives as board_mod

SOURCE = "initiatives.json"
_TASK_STATUS = {"pending": "open", "running": "in_progress", "done": "complete", "blocked": "blocked"}
_CHECK_STATUS = {"ok": "complete", "fail": "blocked", "pending": "open"}
_DECISION = {"go": "approved", "no_go": "rejected", "pending": "pending"}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or "group"


def ref(*parts: str) -> str:
    return f"{SOURCE}#" + "/".join(parts)


def _title(text: Any, fallback: str) -> str:
    t = str(text or "").strip() or fallback
    return t[:200]


def _rollup_status(statuses: list[str]) -> str:
    if statuses and all(s == "complete" for s in statuses):
        return "complete"
    if any(s in ("in_progress", "complete") for s in statuses):
        return "in_progress"
    if statuses and all(s == "blocked" for s in statuses):
        return "blocked"
    return "open"


def plan(tenant_id: str, *, now: float | None = None) -> list[dict[str, Any]]:
    """Import specs, parent-first. Empty when there is no file."""
    now = time.time() if now is None else now
    raw, _path = board_mod._load_raw(tenant_id)
    derived = {i["id"]: i for i in board_mod.board(tenant_id, now=now)["initiatives"]}
    out: list[dict[str, Any]] = []
    for ini_raw in raw["initiatives"]:
        iid = str(ini_raw.get("id") or "")
        if not iid:
            continue
        ini = derived.get(iid)
        if ini is None:
            continue
        iref = ref(iid)
        task_specs: list[dict[str, Any]] = []
        groups: dict[str, str] = {}
        for t in ini["tasks"]:
            parent = iref
            if t.get("group"):
                g = t["group"]
                if g not in groups:
                    # Two names can slug alike ("P0 — x" / "P0 - x"); suffix
                    # rather than silently merge them into one epic.
                    base, n = _slug(g), 1
                    slug = base
                    while ref(iid, "group", slug) in groups.values():
                        n += 1
                        slug = f"{base}-{n}"
                    groups[g] = ref(iid, "group", slug)
                parent = groups[g]
            task_specs.append({
                "external_ref": ref(iid, "task", t["id"]), "parent_ref": parent, "kind": "task",
                "title": _title(t["title"], f"Task {t['id']}"),
                "description": t.get("note"), "status": _TASK_STATUS.get(t["status"], "open"),
                "progress": int(t.get("progress") or 0), "deadline": t.get("due"),
                # Unknown stays unknown: never stamp an import time as a completion date.
                "completed_at": (t.get("completed_at") or None) if t["status"] == "done" else None,
                "start_at": ini_raw.get("start"),
            })
        pre_specs = [{
            "external_ref": ref(iid, "precondition", str(n)), "parent_ref": iref, "kind": "task",
            "category": "precondition", "title": _title(c.get("label"), f"Precondition {n + 1}"),
            "description": c.get("detail"), "status": _CHECK_STATUS.get(c.get("state"), "open"),
        } for n, c in enumerate(ini["preconditions"])]
        cp_specs = []
        for n, cp in enumerate(ini_raw.get("checkpoints") or []):
            at = cp.get("at")
            # A checkpoint is a dated marker, not work: once its time has passed it
            # is complete AT that time (a fact from the file, not the import time).
            # The store keeps markers out of the KPI counts and the overdue flag.
            at_ts = board_mod._parse_ts(at, "checkpoint.at") if at else None
            past = at_ts is not None and at_ts <= now
            cp_specs.append({
                "external_ref": ref(iid, "checkpoint", str(n)), "parent_ref": iref, "kind": "task",
                "category": "checkpoint", "title": _title(cp.get("label"), f"Checkpoint {n + 1}"),
                "deadline": at, "status": "complete" if past else "open",
                "completed_at": at if past else None,
            })
        gate_specs = []
        for g in ini["gates"]:
            gref = ref(iid, "gate", g["id"])
            approval = _DECISION.get(g["decision"], "pending")
            notes = "\n".join(x for x in (
                f"Go → {g['on_go']}" if g.get("on_go") else "",
                f"No-go → {g['on_no_go']}" if g.get("on_no_go") else "") if x)
            gate_specs.append({
                "external_ref": gref, "parent_ref": iref, "kind": "task", "category": "gate",
                "title": _title(g["title"], f"Gate {g['id']}"), "description": notes or None,
                "deadline": g.get("at"), "approval_state": approval, "priority": "high",
                "status": {"approved": "complete", "rejected": "blocked"}.get(approval, "open"),
                "completed_at": g.get("at") if approval == "approved" else None,
            })
            for n, c in enumerate(g["criteria"]):
                gate_specs.append({
                    "external_ref": ref(iid, "gate", g["id"], "criterion", str(n)), "parent_ref": gref,
                    "kind": "subtask", "category": "criterion",
                    "title": _title(c.get("label"), f"Criterion {n + 1}"), "description": c.get("detail"),
                    "status": _CHECK_STATUS.get(c.get("state"), "open"),
                })
        work_status = [s["status"] for s in task_specs]
        if ini["outcome"] == "completed":
            ini_status = "complete"
        elif ini["outcome"] == "cancelled":
            ini_status = "archived"
        elif ini["status"] == "blocked":
            ini_status = "blocked"
        else:
            ini_status = _rollup_status(work_status)
        desc = "\n".join(x for x in (ini_raw.get("description") or "",
                                    f"Cadence: {ini_raw['cadence']}" if ini_raw.get("cadence") else "") if x)
        label = str(ini_raw.get("label") or "").strip()
        out.append({
            "external_ref": iref, "kind": "initiative",
            "title": _title(f"{label} — {ini['title']}" if label else ini["title"], iid),
            "description": desc or None, "start_at": ini_raw.get("start"), "deadline": ini_raw.get("deadline"),
            "status": ini_status,
            "completed_at": ini.get("finished_at") if ini_status == "complete" else None,
            "depends_on_refs": ([ref(ini["blocked_by"]["initiative"], "gate", ini["blocked_by"]["gate"])]
                                if ini.get("blocked_by") else []),
        })
        for g, gref in groups.items():
            members = [s["status"] for s in task_specs if s["parent_ref"] == gref]
            st = _rollup_status(members)
            out.append({"external_ref": gref, "parent_ref": iref, "kind": "epic", "title": _title(g, "Group"),
                        "status": st, "start_at": ini_raw.get("start")})
        out.extend(task_specs)
        out.extend(pre_specs)
        out.extend(cp_specs)
        out.extend(gate_specs)
    for spec in out:
        spec.setdefault("completed_at", None)  # explicit: the store must not stamp one
    return out


def run(tenant_id: str, *, actor: str = "importer", sid_fingerprint: str | None = None,
        now: float | None = None) -> dict[str, Any]:
    from core.task_tracking import service  # noqa: PLC0415

    specs = plan(tenant_id, now=now)
    if not specs:
        return {"inserted": 0, "skipped": 0, "planned": 0}
    res = service.import_items(tenant_id, specs, source="initiatives_json", actor=actor,
                               sid_fingerprint=sid_fingerprint)
    return {**res, "planned": len(specs)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", default="_default")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args(argv)
    if not args.apply:
        specs = plan(args.tenant)
        for s in specs:
            print(f"{s['kind']:10} {s.get('status', 'open'):11} {s['external_ref']}  {s['title']}")
        print(f"\n{len(specs)} items planned — dry run, nothing written (use --apply)")
        return 0
    print(json.dumps(run(args.tenant)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
