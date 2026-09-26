"""Keep the Task-Tracking SSOT current from git and agent sessions (ADR-2060).

    python -m corvin_console.task_tracking_git_sync [--tenant _default] [--dry-run]

Run by ``corvin-task-tracking-sync.timer`` every 5 min. For this install's
checkout it derives:

* one ``initiative`` per repository   — ``external_ref git:<repo>``
* one ``task`` per decision record referenced in a commit subject within the
  last 7 days                         — ``external_ref git:<repo>#ADR-NNNN``,
  titled from the record's heading; **status follows the record's own
  frontmatter status** (accepted → complete, rejected/superseded → archived,
  anything else or no file → in progress);
* **run links**: every such commit (``commit:<repo>:<sha12>``) and every
  interactive Claude Code session whose transcript shows it making one of
  those commits (``agent:<session-id>``).

Operator edits win: items are inserted insert-only (``import_items``), and a
field is patched only while nobody but the sync has updated, decided, deleted
or restored the item. After that the sync only adds new run links. A deleted
item is never re-created and never linked to.

Every write goes through ``core.task_tracking.service`` with actor ``sync:git``
— the same audit-first ``task_item.*`` chain records as a console write. A run
that changes nothing writes nothing. Host-level data: the default tenant only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import host_activity as ha

ACTOR = "sync:git"
SOURCE = "git"

_DONE = {"ACCEPTED", "IMPLEMENTED", "DEPLOYED", "COMPLETE", "COMPLETED", "DONE", "LIVE", "SHIPPED"}
_ARCHIVED = {"REJECTED", "SUPERSEDED", "DEPRECATED", "WITHDRAWN", "OBSOLETE", "ABANDONED"}
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
_TITLE_PREFIX_RE = re.compile(r"^ADR-\d+\s*[:—–-]*\s*")


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def container_ref(repo: str) -> str:
    return f"git:{repo}"


def adr_ref(repo: str, adr: str) -> str:
    return f"git:{repo}#{adr}"


def status_for(adr_status: str | None) -> str:
    word = (str(adr_status or "").strip().split() or [""])[0].upper().strip(".,;:()")
    if word in _DONE:
        return "complete"
    if word in _ARCHIVED:
        return "archived"
    return "in_progress"


def _read_record(path: Path) -> dict[str, Any] | None:
    from core.quality_gates.artifacts import parse_frontmatter  # noqa: PLC0415

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, body = parse_frontmatter(text)
    m = _HEADING_RE.search(body)
    title = _TITLE_PREFIX_RE.sub("", m.group(1)).strip() if m else ""
    return {"status": str(fm.get("status") or "").strip() or None, "title": title or None, "file": path.name}


def adr_meta(adr: str, root: Path | None) -> dict[str, Any] | None:
    """``{status, title, file, siblings}`` from the decision record's file, or None when absent.

    One number can be carried by more than one file (two naming schemes, plus
    duplicate numbering in the record repo). The record that decides is chosen so
    that a stale sibling can never mark work done: superseded/rejected siblings are
    ignored while a live one exists, and among live siblings the one that is NOT
    done wins — two live records that disagree read as open, and ``siblings``
    names every file so the description can say so."""
    if root is None:
        return None
    d = root / "decisions"
    num = adr.split("-", 1)[1]
    # Two naming schemes live side by side: ADR-NNNN-slug.md and (older) NNNN-slug.md.
    files = list(dict.fromkeys(
        f for pat in (f"{adr}-*.md", f"{adr}.md", f"{num}-*.md", f"{num}.md") for f in sorted(d.glob(pat))))
    records = [r for r in (_read_record(f) for f in files) if r is not None]
    if not records:
        return None

    def rank(i_r: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        i, r = i_r
        state = status_for(r["status"])
        return (state == "archived", state == "complete", i)

    chosen = min(enumerate(records), key=rank)[1]
    return {**chosen, "siblings": [r["file"] for r in records]}


def _adr_root() -> Path | None:
    try:
        from core.quality_gates.artifacts import resolve_adr_root  # noqa: PLC0415

        return resolve_adr_root()
    except Exception:  # noqa: BLE001 — no checkout = every item "record not found", never a crash
        return None


def _description(repo: str, adr: str, meta: dict[str, Any] | None) -> str:
    if meta is None:
        state = "The decision record was not found, so the status stays in progress."
    else:
        state = f"Status follows the decision record's own status (currently {meta['status'] or 'unset'})."
        if len(meta.get("siblings") or []) > 1:
            state += (f" {len(meta['siblings'])} files carry this number ({', '.join(meta['siblings'])}); "
                      f"status is read from {meta['file']}.")
    return (f"Work in {repo} whose commits reference {adr}. {state} Linked runs are the commits and the "
            "Claude Code sessions that made them. Edit this item and the sync stops changing it; "
            "new commits are still linked.")


def _task_fields(repo: str, adr: str, meta: dict[str, Any] | None) -> dict[str, Any]:
    """The record-derived fields of an ADR task: what both the in-window plan and
    the out-of-window refresh write."""
    status = status_for(meta["status"]) if meta else "in_progress"
    title = f"{adr} · {meta['title']}" if meta and meta.get("title") else adr
    return {"title": title[:200], "status": status, "description": _description(repo, adr, meta)}


def plan(tenant_id: str, *, now: float | None = None, repo: Path | None = None,
         sessions: list[dict[str, Any]] | None = None, adr_root: Path | None | bool = False) -> dict[str, Any]:
    """The desired state: item specs (parent-first) and run links per ADR ref."""
    now = time.time() if now is None else now
    repo = repo or ha.repo_root()
    slug = ha.repo_slug(repo)
    commits = ha.git_commits(repo, now=now)
    root = _adr_root() if adr_root is False else adr_root
    by_adr: dict[str, list[dict[str, Any]]] = {}
    for c in commits:
        for a in ha.adr_refs(c["subject"]):
            by_adr.setdefault(a, []).append(c)
    if sessions is None:
        sessions = ha.agent_sessions(now)
    made_by = ha.sessions_for_commits([c for cs in by_adr.values() for c in cs], sessions, repo)

    specs: list[dict[str, Any]] = []
    links: dict[str, set[tuple[str, str]]] = {}
    if not by_adr:
        return {"repo": slug, "specs": specs, "links": links, "commits": len(commits), "adr_root": root}
    specs.append({
        "external_ref": container_ref(slug), "kind": "initiative",
        "title": f"{slug} — engineering work (from git)"[:200], "status": "in_progress",
        "description": (f"One task per decision record referenced in {slug}'s commits over the last "
                        f"{ha.WINDOW_S // 86400} days, kept current by the git sync. Status follows each "
                        "record's own status; edit a task and the sync stops changing it."),
        "labels": ["git"], "completed_at": None,
    })
    for adr in sorted(by_adr, key=lambda a: -max(c["ts"] for c in by_adr[a])):
        cs = by_adr[adr]
        fields = _task_fields(slug, adr, adr_meta(adr, root))
        status = fields["status"]
        ref = adr_ref(slug, adr)
        specs.append({
            "external_ref": ref, "parent_ref": container_ref(slug), "kind": "task",
            **fields, "category": "adr", "labels": ["git"],
            "start_at": _iso(min(c["ts"] for c in cs)),
            "completed_at": _iso(max(c["ts"] for c in cs)) if status == "complete" else None,
        })
        want = links.setdefault(ref, set())
        for c in cs:
            want.add(("commit", f"commit:{slug}:{c['short']}"))
            for sid in made_by.get(c["sha"], []):
                want.add(("agent", f"agent:{sid}"))
    return {"repo": slug, "specs": specs, "links": links, "commits": len(commits), "adr_root": root}


_PATCHED = ("title", "status", "description", "category")


def _refresh_out_of_window(tenant_id: str, p: dict[str, Any], current: dict[str, dict[str, Any]],
                           out: dict[str, Any], *, dry_run: bool) -> None:
    """Keep ADR items whose commits left the window following their record.

    The plan only covers records referenced in the last ``WINDOW_S`` of commits;
    without this, an item froze at whatever its record said when its last commit
    aged out — a record accepted (or superseded) later never reached the item.
    Same ownership rule as the in-window patch: an operator-edited item is left alone."""
    from core.task_tracking import service  # noqa: PLC0415
    from core.task_tracking.models import ItemPatch  # noqa: PLC0415

    in_plan = {s["external_ref"] for s in p["specs"]}
    head = container_ref(p["repo"]) + "#"
    for ref, cur in current.items():
        if ref in in_plan or not ref.startswith(head) or cur["deleted_at"] or cur.get("category") != "adr":
            continue
        adr = ref[len(head):]
        fields = _task_fields(p["repo"], adr, adr_meta(adr, p.get("adr_root")))
        changed = {k: v for k, v in fields.items() if cur.get(k) != v}
        if not changed:
            continue
        if cur["foreign_edit"]:
            out["kept_operator_edits"] += 1
            continue
        out["refreshed"] += 1
        if not dry_run:
            service.update(tenant_id, cur["id"], ItemPatch(version=cur["version"], **changed), actor=ACTOR)


def run(tenant_id: str, *, now: float | None = None, dry_run: bool = False, **plan_kw: Any) -> dict[str, Any]:
    from core.task_tracking import service  # noqa: PLC0415
    from core.task_tracking.models import ItemPatch, RunLinkBody  # noqa: PLC0415

    if tenant_id != ha.HOST_TENANT:
        return {"skipped": f"host-level sync runs for tenant {ha.HOST_TENANT!r} only"}
    p = plan(tenant_id, now=now, **plan_kw)
    out: dict[str, Any] = {"repo": p["repo"], "commits": p["commits"], "planned": len(p["specs"]),
                           "inserted": 0, "updated": 0, "kept_operator_edits": 0, "linked": 0,
                           "refreshed": 0}
    prefix = container_ref(p["repo"])
    current = service.synced_items(tenant_id, prefix, actor=ACTOR)
    _refresh_out_of_window(tenant_id, p, current, out, dry_run=dry_run)
    if not p["specs"]:
        return out
    new_specs = [s for s in p["specs"] if s["external_ref"] not in current]
    if dry_run:
        out.update(inserted=len(new_specs), dry_run=True,
                   links=sum(len(v - (current.get(r, {}).get("run_refs") or set())) for r, v in p["links"].items()))
        return out
    if new_specs:
        res = service.import_items(tenant_id, new_specs, source=SOURCE, actor=ACTOR)
        out["inserted"] = res["inserted"]
        current = service.synced_items(tenant_id, prefix, actor=ACTOR)
    for spec in p["specs"]:
        cur = current.get(spec["external_ref"])
        if cur is None or cur["deleted_at"] or spec in new_specs:
            continue
        changed = {k: spec[k] for k in _PATCHED if k in spec and cur.get(k) != spec[k]}
        if not changed:
            continue
        if cur["foreign_edit"]:
            out["kept_operator_edits"] += 1
            continue
        service.update(tenant_id, cur["id"], ItemPatch(version=cur["version"], **changed), actor=ACTOR)
        out["updated"] += 1
    for ref, want in p["links"].items():
        cur = current.get(ref)
        if cur is None or cur["deleted_at"]:
            continue
        for run_type, run_ref in sorted(want - cur["run_refs"]):
            service.link_run(tenant_id, cur["id"], RunLinkBody(run_type=run_type, run_ref=run_ref), actor=ACTOR)
            out["linked"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", default=ha.HOST_TENANT)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args(argv)
    print(json.dumps(run(args.tenant, dry_run=args.dry_run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
