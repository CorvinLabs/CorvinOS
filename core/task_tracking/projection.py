"""Projection of an external source of truth into the task store (ADR-2205).

The knowledge base (Corvin-Knowledge) owns initiatives, epics and tasks; the task
store holds a PROJECTION of them, keyed by ``external_ref = "kb:<uid>"``. One way
only — nothing here reads a status back into the KB. A board move of a KB item is a
KB transition (``corvin_console.kb_projection.transition``), which then re-projects.

``apply`` is idempotent: an item whose projected fields already match is not
touched (no version bump, no audit record). It is also the self-heal: whatever
changed a projected field — a raw SQL write, a restored backup, a missed tick — the
next ``apply`` writes it back, and reports it as ``drift_healed``.

Fail-closed: a payload whose source check is red (``ok: false``) writes nothing
but one ``task_item.projection_blocked`` record.
"""
from __future__ import annotations

from typing import Any

from . import service, store
from .models import ItemCreate, ItemPatch

#: KB task status -> task-store status (the store has no cancelled; archived carries the reason).
STATUS_FROM_KB = {"open": "open", "in_progress": "in_progress", "blocked": "blocked",
                  "done": "complete", "cancelled": "archived"}
STATUS_TO_KB = {"open": "open", "in_progress": "in_progress", "blocked": "blocked",
                "complete": "done", "archived": "cancelled"}
PROJECTED = ("kind", "parent_id", "title", "status", "status_reason", "category", "labels")
_LABEL_OK = __import__("re").compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_LABELS = 12   # models._Fields.labels max_length


def _desired(it: dict[str, Any], ref_to_id: dict[str, str]) -> dict[str, Any]:
    status = STATUS_FROM_KB[it["status"]]
    reason = None
    if it["status"] == "blocked" and it.get("blocked_reason"):
        reason = str(it["blocked_reason"])[:500]
    elif it["status"] == "cancelled":
        reason = "cancelled in the knowledge base"
    labels = ["kb"]
    for x in it.get("labels") or []:        # same shape the store keeps (deduped, <= 12) — or it never converges
        if isinstance(x, str) and _LABEL_OK.match(x) and x not in labels and len(labels) < MAX_LABELS:
            labels.append(x)
    return dict(kind=it["kind"], parent_id=ref_to_id.get(it["parent_ref"]) if it.get("parent_ref") else None,
                title=it["title"][:200], status=status, status_reason=reason, category="kb",
                labels=labels)


def kb_items(tenant_id: str, namespace: str = "kb") -> dict[str, dict[str, Any]]:
    """Every item of *namespace*, deleted ones included, keyed by external_ref."""
    if not store.exists(tenant_id):
        return {}
    prefix = f"{namespace}:"
    with store.connect(tenant_id) as conn:
        rows = conn.execute("SELECT * FROM items WHERE tenant_id=? AND external_ref LIKE ?",
                            (tenant_id, prefix.replace("_", "\\_") + "%")).fetchall()
    return {r["external_ref"]: service._row(r) for r in rows}


def diff(tenant_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """What ``apply`` would change — the projection's loss signal. Empty = in sync."""
    have = kb_items(tenant_id, payload["namespace"])
    ref_to_id = {ref: row["id"] for ref, row in have.items()}
    out = []
    seen = set()
    for it in payload["items"]:
        seen.add(it["ref"])
        cur = have.get(it["ref"])
        if cur is None or cur["deleted_at"]:
            out.append(dict(ref=it["ref"], id=it["id"], problem="missing" if cur is None else "deleted"))
            continue
        want = _desired(it, ref_to_id)
        for k in PROJECTED:
            if cur.get(k) != want[k]:
                out.append(dict(ref=it["ref"], id=it["id"], problem="field", field=k,
                                expected=want[k], actual=cur.get(k)))
    for ref, cur in have.items():
        if ref not in seen and not cur["deleted_at"] and cur["status"] != "archived":
            out.append(dict(ref=ref, id=cur["title"], problem="orphan"))
    return out


def apply(tenant_id: str, payload: dict[str, Any], *, actor: str = service.KB_ACTOR,
          drift_healed: int = 0) -> dict[str, Any]:
    """*drift_healed*: the caller found a diff while the source had NOT changed —
    i.e. the store was written behind the KB's back — and this apply repairs it."""
    ns = payload.get("namespace")
    if ns != "kb":
        raise service.TaskTrackingError(f"unknown projection namespace {ns!r}")
    sha = (payload.get("sha") or "")[:12]
    if not payload.get("ok"):
        service._chain(tenant_id, "task_item.projection_blocked",
                       {"namespace": ns, "sha": sha, "blocking": int(payload.get("blocking") or 0),
                        "actor_kind": actor.split(":")[0]})
        return dict(state="blocked", sha=sha, blocking=payload.get("blocking"), failing=payload.get("failing"))

    have = kb_items(tenant_id, ns)
    ref_to_id = {ref: row["id"] for ref, row in have.items()}
    counts = dict(created=0, updated=0, archived=0, restored=0, unchanged=0)
    for it in payload["items"]:            # export order is parent-first
        want = _desired(it, ref_to_id)
        cur = have.get(it["ref"])
        if cur is None:
            body = ItemCreate(kind=want["kind"], title=want["title"], parent_id=want["parent_id"],
                              status=want["status"])
            row = service.create(tenant_id, body, actor=actor, source="kb",
                                 extra={"external_ref": it["ref"], "category": "kb", "labels": want["labels"],
                                        "status_reason": want["status_reason"]})
            ref_to_id[it["ref"]] = row["id"]
            counts["created"] += 1
            continue
        if cur["deleted_at"]:
            cur = service.restore(tenant_id, cur["id"], actor=actor)
            if cur["deleted_at"]:
                raise service.TaskTrackingError(f"could not restore {it['ref']}")
            counts["restored"] += 1
        changed = {k: want[k] for k in PROJECTED if cur.get(k) != want[k]}
        if not changed:
            counts["unchanged"] += 1
            continue
        service.update(tenant_id, cur["id"], ItemPatch(version=cur["version"], **changed), actor=actor)
        counts["updated"] += 1
    live = {it["ref"] for it in payload["items"]}
    for ref, cur in have.items():
        if ref in live or cur["deleted_at"] or cur["status"] == "archived":
            continue
        # removed from the KB: archived, never deleted — the history stays readable
        service.update(tenant_id, cur["id"], ItemPatch(version=cur["version"], status="archived",
                       status_reason=f"removed from the knowledge base @ {sha}"), actor=actor)
        counts["archived"] += 1
    written = counts["created"] + counts["updated"] + counts["archived"] + counts["restored"]
    if written:
        service._chain(tenant_id, "task_item.projection_applied",
                       {"namespace": ns, "sha": sha, **counts, "drift_healed": drift_healed,
                        "actor_kind": actor.split(":")[0]})
    remaining = diff(tenant_id, payload)
    return dict(state="ok" if not remaining else "diverged", sha=sha, **counts,
                drift_healed=drift_healed, remaining=remaining)
