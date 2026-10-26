"""D6 (PLAN-0948 follow-up): the Data Hub tidies up after itself.

Data Hub skills pile up in two places and nothing used to take them away again:

* the tenant's SkillForge registry holds one ``assistant.datahub_<artifact_id>`` skill per artifact
  (``register.py``), even after its artifact was deleted or when it duplicates another one;
* the engine-facing plugin slot (``skills/dyn/assistant_datahub_<id>/``) mirrors every skill that was
  ever created. A mirror whose canonical skill is gone is read by the engine's skill loader and by the
  CEL skill stage but can never be injected, graded or regenerated.

This module lets the system decide, by rule, which of them go - and writes down what it removed.

Decision rules, evaluated in this order for every candidate (the first one that holds names the reason):

``orphan_mirror``   a slot mirror that NO tenant registry holds, older than ``ORPHAN_GRACE_S``. Besides the
                    current ``assistant_datahub_<hex id>`` this covers the legacy name-derived slugs
                    (``assistant_datahub_<what the operator typed>``, before review DH-2/DH-10): those are
                    only taken for Data Hub leftovers when their body carries the creator's template, and
                    their name is NEVER written to a record - it can be a patient's name - only a hash of it
``artifact_gone``   a registered skill whose artifact is deleted or missing from the artifact store
``duplicate``       several skills made from the same data (identical data profile; without a profile, an
                    identical body): the one with earned grades (then the newest) stays, the others go -
                    but never one that carries earned grades itself
``unused``          created more than ``UNUSED_DAYS`` ago, never offered to a turn and never rated, only
                    the bootstrap grade - and only once the daemon has watched for that whole window

Guards (these make the decision safe to take without a human):

* **audit first** - the intent is chained before anything is touched; a record that does not commit
  means the item is left alone;
* **a skill with an earned grade is never removed for being unused or a duplicate** - the operator or a
  user rated it, that is a signal this module may not overrule;
* **bulk guard** - a pass that would remove at least ``BULK_GUARD_MIN`` registered skills AND more than
  ``BULK_GUARD_FRACTION`` of them is refused as a whole (a wrong premise must not empty the registry);
  orphan mirrors are exempt, they hold nothing;
* **cap** - at most ``MAX_PER_PASS`` removals per pass;
* **removal list** - ``<tenant>/learning/datahub_prune/removed.jsonl`` (append-only: time, name, kind,
  reason, body hash prefix; never a body, a title or a description).

Records on the audit chain are content-free: ids, enums and a hash prefix.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from core.deployment import audit_sink

from .register import SCOPE, SKILL_PREFIX, _registry

logger = logging.getLogger(__name__)

audit_sink.register_events({
    "learning.datahub_pruned": ("skill_id", "kind", "reason", "stage", "outcome", "kept", "body_hash"),
    "learning.datahub_prune_refused": ("reason", "candidates", "registered"),
})

REASONS = ("orphan_mirror", "artifact_gone", "duplicate", "unused")
MIRROR_PREFIX = SKILL_PREFIX.replace(".", "_")           # the slot's undotted directory name
ORIGIN = "datahub_unified"

UNUSED_DAYS = 60
ORPHAN_GRACE_S = 600.0       # a create() writes the registry first and the mirror right after
MAX_PER_PASS = 500
BULK_GUARD_MIN = 20
BULK_GUARD_FRACTION = 0.5

_HEX = re.compile(r"^[a-f0-9]{1,32}$")
#: What every body made by ``UnifiedCreator`` carries - and a skill someone wrote by hand does not.
_CREATOR_TEMPLATE = ("**Generated:** Automated from data", "Based on analysis of")


@dataclass(frozen=True)
class Candidate:
    name: str            # the LOGGABLE skill id (dotted); for a legacy slug a hash, never the slug
    kind: str            # "mirror" | "skill"
    reason: str          # one of REASONS
    body_hash: str = ""
    kept: str = ""       # for a duplicate: the skill that stays
    slot: str = ""       # a legacy mirror's real directory name (in memory only, never logged)


@dataclass
class Plan:
    candidates: list[Candidate] = field(default_factory=list)
    registered: int = 0
    mirrors: int = 0
    unused_rule_active: bool = False

    def counts(self) -> dict[str, int]:
        out = {r: 0 for r in REASONS}
        for c in self.candidates:
            out[c.reason] += 1
        return out


@dataclass
class Result:
    removed: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    refused: str = ""
    dry_run: bool = False
    plan: Optional[Plan] = None
    verification: dict[str, Any] = field(default_factory=dict)


# ── helpers ──────────────────────────────────────────────────────────────────

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def _sf():
    """The skill_forge registry module (its sys.path is prepared by ``register._registry``)."""
    from skill_forge import registry as sf_registry  # noqa: PLC0415

    return sf_registry


def _slot_dir(tenant_id: str, slot_dir: Optional[Path]) -> Path:
    if slot_dir is not None:
        return Path(slot_dir)
    _registry(tenant_id)         # puts skill_forge on sys.path
    return _sf().plugin_slot_dir()


def _tenants_dir(tenant_id: str) -> Path:
    from core.paths.tenant import tenant_home

    return Path(tenant_home(tenant_id)).parent


def _all_registered_mirror_names(tenant_id: str) -> set[str]:
    """Slot names (undotted) of every skill ANY tenant registry holds, in any scope.

    A mirror is per repo, not per tenant: judging it against one tenant's registry would delete
    another tenant's live skill."""
    names: set[str] = set()
    base = _tenants_dir(tenant_id)
    patterns = ("*/skill-forge/skills/*", "*/global/skill-forge/skills/*", "*/sessions/*/skill-forge/skills/*")
    for pat in patterns:
        try:
            for p in base.glob(pat):
                if p.is_dir():
                    names.add(p.name.replace(".", "_"))
        except OSError:
            continue
    return names


def _artifact(tenant_id: str, artifact_id: str) -> Optional[dict]:
    from .regenerate import _artifacts_dir, _read

    if not artifact_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(artifact_id)):
        return None
    return _read(_artifacts_dir(tenant_id) / f"{artifact_id}.json")


_PROFILE = re.compile(r"Data profile \(computed from all \d+ rows.*", re.S)


def _dup_key(body: str) -> str:
    """Two skills are the same work when they describe the same data: the computed data profile (counts,
    ranges, most frequent values) decides. Only a skill WITHOUT a profile is compared by its whole body -
    never by title, row count or file type, which different datasets share."""
    m = _PROFILE.search(body)
    return _sha(m.group(0)) if m else _sha(body)


def _earned(spec: Any) -> int:
    """Grades that are not the capped bootstrap seed of this module's registration."""
    n = 0
    for g in list(getattr(spec, "grades", None) or []):
        run_id = str((g or {}).get("run_id", ""))
        notes = str((g or {}).get("notes", ""))
        if run_id == "datahub-bootstrap" or "NOT earned usage" in notes:
            continue
        n += 1
    return n


def _removed_log(tenant_id: str) -> Path:
    from core.paths.tenant import tenant_home

    return Path(tenant_home(tenant_id)) / "learning" / "datahub_prune" / "removed.jsonl"


def removed_list(tenant_id: str) -> list[dict[str, Any]]:
    """Everything the system has removed so far, oldest first."""
    path = _removed_log(tenant_id)
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _append_removed(tenant_id: str, row: dict[str, Any]) -> None:
    path = _removed_log(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


# ── phase 1: inventory and decision (read-only) ───────────────────────────────

def plan(
    tenant_id: str, *, now: Optional[float] = None, slot_dir: Optional[Path] = None,
    used_ids: Iterable[str] = (), observing_since: Optional[float] = None,
) -> Plan:
    """Decide which Data Hub skills go. Reads only; touches nothing."""
    now = time.time() if now is None else now
    used = {str(u) for u in used_ids}
    out = Plan()
    reg = _registry(tenant_id)
    slot = _slot_dir(tenant_id, slot_dir)

    # registered Data Hub skills of THIS tenant (the Data Hub writes the user scope only)
    live: list[tuple[str, Any, str, str]] = []   # (name, spec, body_hash, dup_key)
    for scope, spec in reg.list_with_scope():
        meta = getattr(spec, "meta", None) or {}
        if scope != SCOPE or not spec.name.startswith(SKILL_PREFIX) or meta.get("origin") != ORIGIN:
            continue
        body = reg.get_body(spec.name) or ""
        live.append((spec.name, spec, _sha(body), _dup_key(body)))
    out.registered = len(live)

    picked: set[str] = set()

    def pick(c: Candidate) -> None:
        if c.name not in picked:
            picked.add(c.name)
            out.candidates.append(c)

    # 1. orphan mirrors
    held = _all_registered_mirror_names(tenant_id)
    try:
        mirrors = sorted(p for p in slot.iterdir() if p.is_dir() and p.name.startswith(MIRROR_PREFIX))
    except OSError:
        mirrors = []
    for m in mirrors:
        suffix = m.name[len(MIRROR_PREFIX):]
        legacy = not _HEX.fullmatch(suffix)
        md = m / "SKILL.md"
        try:
            age = now - md.stat().st_mtime
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            age, text = ORPHAN_GRACE_S + 1, ""
        if legacy and not all(t in text for t in _CREATOR_TEMPLATE):
            continue                       # not made by the Data Hub creator: never ours to judge
        out.mirrors += 1
        if m.name in held or age < ORPHAN_GRACE_S:
            continue
        if legacy:
            pick(Candidate(f"{SKILL_PREFIX}legacy_{_sha(suffix)}", "mirror", "orphan_mirror", _sha(text),
                           slot=m.name))
        else:
            pick(Candidate(SKILL_PREFIX + suffix, "mirror", "orphan_mirror", _sha(text)))

    # 2. registered skills whose artifact is gone
    survivors: list[tuple[str, Any, str, str]] = []
    for name, spec, h, k in live:
        meta = getattr(spec, "meta", None) or {}
        art = _artifact(tenant_id, str(meta.get("artifact_id", "")))
        if art is None or str(art.get("status", "")).lower() == "deleted":
            pick(Candidate(name, "skill", "artifact_gone", h))
        else:
            survivors.append((name, spec, h, k))

    # 3. duplicates (same body): keep the best one, never remove a skill that carries earned grades
    groups: dict[str, list[tuple[str, Any, str, str]]] = {}
    for item in survivors:
        groups.setdefault(item[3], []).append(item)
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda it: (_earned(it[1]), float(getattr(it[1], "created_at", 0) or 0), it[0]),
                     reverse=True)
        keeper = members[0][0]
        for name, spec, h, _k in members[1:]:
            if _earned(spec) == 0:
                pick(Candidate(name, "skill", "duplicate", h, kept=keeper))

    # 4. unused: only after the daemon has watched for the whole window
    window = UNUSED_DAYS * 86400.0
    out.unused_rule_active = observing_since is not None and now - float(observing_since) >= window
    if out.unused_rule_active:
        for name, spec, h, _k in survivors:
            if name in picked or name in used or _earned(spec) > 0:
                continue
            created = float(getattr(spec, "created_at", 0) or 0)
            if created and now - created >= window:
                pick(Candidate(name, "skill", "unused", h))
    return out


# ── phase 2: removal (audited, guarded) ───────────────────────────────────────

def _intent(tenant_id: str, c: Candidate, stage: str, outcome: str = "") -> None:
    audit_sink.emit("learning.datahub_pruned", {
        "skill_id": c.name, "kind": c.kind, "reason": c.reason, "stage": stage, "outcome": outcome,
        "kept": c.kept, "body_hash": c.body_hash,
    }, tenant_id=tenant_id)


def apply(
    tenant_id: str, the_plan: Plan, *, dry_run: bool = False, slot_dir: Optional[Path] = None,
) -> Result:
    """Remove what the plan names. Audit first; a record that does not commit leaves the item alone."""
    res = Result(dry_run=dry_run, plan=the_plan)
    cands = list(the_plan.candidates)
    skills = [c for c in cands if c.kind == "skill"]
    if (len(skills) >= BULK_GUARD_MIN and the_plan.registered
            and len(skills) / the_plan.registered > BULK_GUARD_FRACTION):
        res.refused = "bulk_guard"
        if not dry_run:
            try:
                audit_sink.emit("learning.datahub_prune_refused", {
                    "reason": "bulk_guard", "candidates": len(skills), "registered": the_plan.registered,
                }, tenant_id=tenant_id, severity="WARNING")
            except audit_sink.AuditWriteFailed:
                logger.warning("datahub prune refusal did not commit")
        return res
    if dry_run:
        return res

    reg = _registry(tenant_id)
    slot = _slot_dir(tenant_id, slot_dir)
    sf = _sf()
    for c in cands[:MAX_PER_PASS]:
        try:
            _intent(tenant_id, c, "applying")
        except audit_sink.AuditWriteFailed:
            res.failed.append({"name": c.name, "why": "audit_not_committed"})
            continue
        try:
            if c.kind == "mirror":
                target = slot / (c.slot or sf._sanitize_slot_name(c.name))
                if not (target.name.startswith(MIRROR_PREFIX) and target.parent == slot):
                    raise ValueError("not a Data Hub mirror")
                _rm_tree(target)
                gone = not target.exists()
            else:
                reg.delete(c.name, scope=SCOPE, reason=f"datahub prune: {c.reason}")
                gone = reg.get_in_scope(c.name, SCOPE) is None
            if not gone:
                raise OSError("still present after removal")
        except Exception as exc:  # noqa: BLE001 - one failed removal never stops the pass
            res.failed.append({"name": c.name, "why": type(exc).__name__})
            try:
                _intent(tenant_id, c, "failed", type(exc).__name__)
            except audit_sink.AuditWriteFailed:
                pass
            continue
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "name": c.name, "kind": c.kind,
               "reason": c.reason, "body_hash": c.body_hash, "kept": c.kept}
        _append_removed(tenant_id, row)
        res.removed.append(row)
    return res


def _rm_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=False)


# ── phase 3: verification ─────────────────────────────────────────────────────

def verify(
    tenant_id: str, names: Iterable[str], *, slot_dir: Optional[Path] = None,
    slots: Optional[dict[str, str]] = None, scan_roots: Iterable[Path] = (),
    skip_dirs: Iterable[str] = ("node_modules", ".git", "dyn", "__pycache__", "dist"),
) -> dict[str, Any]:
    """After a removal: the names are gone from the registry and the slot, the registry still loads, and
    (when ``scan_roots`` is given) no text file under those roots still mentions them. ``slots`` maps a
    loggable name to the real mirror directory of a legacy slug."""
    names = sorted({str(n) for n in names})
    slots = slots or {}
    reg = _registry(tenant_id)
    slot = _slot_dir(tenant_id, slot_dir)
    sf = _sf()
    still_registered, still_mirrored = [], []
    for n in names:
        if reg.get(n) is not None:
            still_registered.append(n)
        if (slot / (slots.get(n) or sf._sanitize_slot_name(n))).exists():
            still_mirrored.append(n)
    try:
        reg.list()
        loads = True
    except Exception:  # noqa: BLE001
        loads = False
    refs: dict[str, list[str]] = {}
    roots = [Path(r) for r in scan_roots]
    if roots and names:
        ids = {n: n[len(SKILL_PREFIX):] for n in names}
        pat = re.compile(r"datahub_([a-f0-9]{1,32})")
        wanted = set(ids.values())
        skip = set(skip_dirs)
        for root in roots:
            for dp, dns, fns in os.walk(root):
                dns[:] = [d for d in dns if d not in skip]
                for f in fns:
                    if not f.endswith((".py", ".md", ".yaml", ".yml", ".json", ".ts", ".tsx", ".txt", ".jsonl")):
                        continue
                    fp = Path(dp) / f
                    try:
                        if fp.stat().st_size > 3_000_000:
                            continue
                        text = fp.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    for m in pat.finditer(text):
                        if m.group(1) in wanted:
                            refs.setdefault(SKILL_PREFIX + m.group(1), []).append(str(fp))
    return {"ok": not still_registered and not still_mirrored and loads and not refs,
            "still_registered": still_registered, "still_mirrored": still_mirrored,
            "registry_loads": loads, "references": {k: sorted(set(v)) for k, v in refs.items()}}


# ── one pass, as the learning host runs it ────────────────────────────────────

def run(
    tenant_id: str, *, now: Optional[float] = None, slot_dir: Optional[Path] = None,
    used_ids: Iterable[str] = (), observing_since: Optional[float] = None, dry_run: bool = False,
) -> Result:
    the_plan = plan(tenant_id, now=now, slot_dir=slot_dir, used_ids=used_ids, observing_since=observing_since)
    res = apply(tenant_id, the_plan, dry_run=dry_run, slot_dir=slot_dir)
    if res.removed:
        slots = {c.name: c.slot for c in the_plan.candidates if c.slot}
        res.verification = verify(tenant_id, [r["name"] for r in res.removed], slot_dir=slot_dir, slots=slots)
    return res


__all__ = ["Candidate", "Plan", "Result", "REASONS", "plan", "apply", "verify", "run", "removed_list",
           "UNUSED_DAYS", "ORPHAN_GRACE_S", "MAX_PER_PASS", "BULK_GUARD_MIN", "BULK_GUARD_FRACTION"]
