"""Skill canary — serve a candidate SKILL.md body to a share of turns and
grade the two variants apart (ADR-2094).

A canary belongs to ONE registered skill in ONE registry root (the scope root
that holds ``skills/``). While it is active, the injection path
(``skill_inject.collect_active_skills``) asks :meth:`SkillCanary.variant_for`
which body a chat gets; the grading path (``auto_grade_from_output``,
``grade_from_user_followup``) asks :meth:`SkillCanary.served_variant` which
body that chat GOT, and records the grade against that variant. Candidate
grades never enter the registry's grade list — the live skill's mean stays a
measurement of the live body.

Split: sticky per chat. ``bucket = sha256(canary_id:channel_id) % 100`` and a
chat is served the candidate while ``bucket < traffic_percent``. The variant a
chat was actually served is recorded in a per-canary ledger keyed by a hash of
the channel id (never the id itself), so a traffic change between a turn and
its follow-up grade cannot misattribute that grade. A turn without a channel
id is always served the live body.

Serving surfaces: only the injection path splits. The engine plugin slot the
registry writes (Claude Code loads it as a file) keeps the LIVE body until an
approval — a file cannot be split per turn.

Lifecycle (every transition is written to the tenant audit chain FIRST; a
failed chain write refuses the transition):

    canary ──pause──▶ paused ──resume──▶ canary
      │ └──gates pass at the top step──▶ ready
      ├──approve (canary|paused|ready)──▶ approved ──rollback──▶ rolled_back
      ├──defer──▶ deferred
      └──rollback──▶ rolled_back
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

ACTIVE = ("canary", "paused", "ready")
FINAL = ("approved", "deferred", "rolled_back")
TRAFFIC_STEPS = (10, 25, 50)
#: Outcome grades per variant before a gate may decide anything.
MIN_SAMPLES = 5
#: A candidate this far below the reference mean is rolled back.
ROLLBACK_MARGIN = 0.15
#: Grades from the follow-up turn (approval / rejection / rephrase) are the
#: only ones that measure quality. Auto-grades only say "the skill was used"
#: and are capped at 0.3, so they are counted, never averaged.
OUTCOME = "outcome"
USAGE = "usage"
_SERVED_KEEP = 2000  # ledger entries kept per canary

CANARY_EVENTS: dict[str, frozenset[str]] = {
    "skill.canary_started": frozenset({
        "canary_id", "traffic_percent", "source", "candidate_sha", "live_sha",
        "quality", "trigger_reason", "trigger_mean", "trigger_n",
    }),
    "skill.canary_traffic_changed": frozenset({
        "canary_id", "traffic_percent", "from_traffic_percent", "reason_code",
    }),
    "skill.canary_status_changed": frozenset({
        "canary_id", "status", "from_status", "reason_code", "actor",
    }),
    "skill.canary_graded": frozenset({
        "canary_id", "variant", "kind", "score", "run_id",
    }),
    "skill.canary_autopilot_changed": frozenset({"enabled", "from_enabled"}),
}


def audit_writer(audit_path: Path) -> Callable[[str, dict], dict]:
    """Public handle on the chain writer the canary uses (for the autopilot
    switch, which is audited by the same rules)."""
    return _default_audit_writer(Path(audit_path))

_lock = threading.RLock()


class CanaryError(Exception):
    """A canary operation that cannot be carried out."""


class CanaryConflict(CanaryError):
    """No active canary where one is required, or one where none may be."""


def _valid_name(name: str) -> str:
    if (not name or len(name) > 128 or ".." in name or "/" in name
            or not all(c.isalnum() or c in "._" for c in name)):
        raise CanaryError(f"invalid skill name: {name!r}")
    return name


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _channel_key(channel_id: str) -> str:
    return hashlib.sha256(channel_id.encode("utf-8")).hexdigest()[:16]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def variant_stats(state: dict) -> dict[str, dict[str, Any]]:
    """Per-variant counts: ``outcome_n`` / ``outcome_mean`` (the measurement)
    and ``usage_n`` (turns in which the skill was demonstrably used)."""
    out: dict[str, dict[str, Any]] = {}
    for variant in ("live", "candidate"):
        grades = [g for g in state.get("grades", []) if g.get("variant") == variant]
        outcome = [float(g["score"]) for g in grades if g.get("kind") == OUTCOME]
        out[variant] = {
            "outcome_n": len(outcome),
            "outcome_mean": _mean(outcome),
            "usage_n": sum(1 for g in grades if g.get("kind") == USAGE),
            "served_n": int(state.get("served", {}).get(variant, 0)),
        }
    return out


def reference_mean(state: dict) -> tuple[float | None, str]:
    """What the candidate is measured against: the live variant's outcome mean
    during the canary once it has ``MIN_SAMPLES``, else the live skill's
    outcome mean from before the canary (recorded at start), else nothing."""
    live = variant_stats(state)["live"]
    if live["outcome_n"] >= MIN_SAMPLES:
        return live["outcome_mean"], "live_during_canary"
    base = (state.get("trigger") or {}).get("live_mean")
    if isinstance(base, (int, float)):
        return float(base), "live_before_canary"
    return None, "none"


def evaluate(state: dict) -> dict[str, str]:
    """The gate verdict for an active canary — pure, shown in the console and
    executed by the autopilot.

    ``hold``      not enough outcome grades yet, or candidate between the bars
    ``escalate``  candidate >= reference; move to the next traffic step
    ``ready``     candidate >= reference at the top step; awaits approval
    ``rollback``  candidate below reference by more than ROLLBACK_MARGIN
    """
    if state.get("status") != "canary":
        return {"decision": "hold", "reason": f"canary is {state.get('status')}"}
    cand = variant_stats(state)["candidate"]
    if cand["outcome_n"] < MIN_SAMPLES:
        return {"decision": "hold",
                "reason": f"{cand['outcome_n']}/{MIN_SAMPLES} candidate outcome grades"}
    ref, source = reference_mean(state)
    if ref is None:
        return {"decision": "hold", "reason": "no reference measurement for the live skill"}
    mean = float(cand["outcome_mean"])
    if mean < ref - ROLLBACK_MARGIN:
        return {"decision": "rollback",
                "reason": f"candidate {mean:.2f} < reference {ref:.2f} - {ROLLBACK_MARGIN}"}
    if mean >= ref:
        if int(state.get("traffic_percent", 0)) >= TRAFFIC_STEPS[-1]:
            return {"decision": "ready",
                    "reason": f"candidate {mean:.2f} >= reference {ref:.2f} ({source})"}
        return {"decision": "escalate",
                "reason": f"candidate {mean:.2f} >= reference {ref:.2f} ({source})"}
    return {"decision": "hold",
            "reason": f"candidate {mean:.2f} below reference {ref:.2f}, within margin"}


def next_traffic_step(current: int) -> int | None:
    for step in TRAFFIC_STEPS:
        if step > current:
            return step
    return None


def _default_audit_writer(audit_path: Path) -> Callable[[str, dict], dict]:
    """Write through the registry's own chain writer (``forge.security_events``)
    and register the canary events' allowlists with that same module."""
    from .registry import _write_event  # noqa: PLC0415

    if _write_event is None:
        def _refuse(event_type: str, details: dict) -> dict:
            raise CanaryError("audit chain writer unavailable — canary change refused")
        return _refuse
    se = sys.modules.get(getattr(_write_event, "__module__", ""))
    register = getattr(se, "register_event_allowlist", None)
    severity = getattr(se, "EVENT_SEVERITY", None)
    for event_type, fields in CANARY_EVENTS.items():
        if callable(register):
            register(event_type, fields | {"skill"})
        if isinstance(severity, dict):
            severity.setdefault(event_type, "INFO")

    def _write(event_type: str, details: dict) -> dict:
        return _write_event(audit_path, event_type, severity="INFO",
                            tool=str(details.get("skill", "")), details=details)
    return _write


class SkillCanary:
    """Canary state for the skills of one registry root."""

    def __init__(self, registry_root: Path | str, *,
                 audit_path: Path | None = None,
                 audit: Callable[[str, dict], dict] | None = None) -> None:
        self.root = Path(registry_root)
        self.dir = self.root / "canary"
        self._audit_path = audit_path
        self._audit_fn = audit

    # -- storage -----------------------------------------------------------

    def _skill_dir(self, name: str) -> Path:
        return self.dir / _valid_name(name)

    def _load(self, name: str) -> dict | None:
        path = self._skill_dir(name) / "state.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None

    def _save(self, name: str, state: dict) -> None:
        state["updated_at"] = time.time()
        _atomic_write(self._skill_dir(name) / "state.json",
                      json.dumps(state, indent=2, sort_keys=True) + "\n")

    def _audit(self, event_type: str, details: dict) -> dict:
        if self._audit_fn is None:
            if self._audit_path is None:
                from .registry import SkillRegistry  # noqa: PLC0415
                self._audit_path = SkillRegistry(self.root).audit_path()
            self._audit_fn = _default_audit_writer(self._audit_path)
        try:
            record = self._audit_fn(event_type, details)
        except CanaryError:
            raise
        except Exception as exc:  # noqa: BLE001 — audit-first: no record, no change
            raise CanaryError(f"audit write failed ({type(exc).__name__}) — change refused") from exc
        return record if isinstance(record, dict) else {}

    def _event(self, state: dict, kind: str, record: dict, **detail: Any) -> None:
        state.setdefault("events", []).append({
            "ts": time.time(), "type": kind,
            "hash": str(record.get("hash") or ""), **detail,
        })

    # -- read --------------------------------------------------------------

    def state(self, name: str) -> dict | None:
        return self._load(name)

    def active(self, name: str) -> dict | None:
        try:
            state = self._load(name)
        except CanaryError:
            return None
        return state if state and state.get("status") in ACTIVE else None

    def list_states(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        out = []
        for d in self.dir.iterdir():
            if d.is_dir():
                st = self._load(d.name)
                if st:
                    out.append(st)
        out.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
        return out

    def candidate_body(self, name: str) -> str | None:
        try:
            return (self._skill_dir(name) / "candidate.md").read_text(encoding="utf-8")
        except OSError:
            return None

    # -- serving -----------------------------------------------------------

    def variant_for(self, name: str, channel_id: str | None) -> str:
        """Which body this chat is served now; records the choice."""
        state = self.active(name)
        if state is None or state.get("status") == "paused" or not channel_id:
            return "live"
        digest = hashlib.sha256(f"{state['canary_id']}:{channel_id}".encode()).hexdigest()
        variant = "candidate" if int(digest[:8], 16) % 100 < int(state.get("traffic_percent", 0)) else "live"
        if variant == "candidate" and self.candidate_body(name) is None:
            variant = "live"
        with _lock:
            path = self._skill_dir(name) / "served.json"
            try:
                ledger = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                ledger = {}
            ledger[_channel_key(channel_id)] = {"variant": variant, "ts": time.time()}
            if len(ledger) > _SERVED_KEEP:
                keep = sorted(ledger.items(), key=lambda kv: kv[1].get("ts", 0))[-_SERVED_KEEP:]
                ledger = dict(keep)
            _atomic_write(path, json.dumps(ledger) + "\n")
            fresh = self._load(name) or state
            served = fresh.setdefault("served", {})
            served[variant] = int(served.get(variant, 0)) + 1
            self._save(name, fresh)
        return variant

    def served_variant(self, name: str, channel_id: str | None) -> str | None:
        """The variant this chat was last served during the active canary."""
        if not channel_id or self.active(name) is None:
            return None
        try:
            ledger = json.loads((self._skill_dir(name) / "served.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        entry = ledger.get(_channel_key(channel_id))
        return entry.get("variant") if isinstance(entry, dict) else None

    def record_grade(self, name: str, *, variant: str, score: float,
                     run_id: str, kind: str) -> dict:
        if variant not in ("live", "candidate") or kind not in (OUTCOME, USAGE):
            raise CanaryError(f"bad grade variant/kind: {variant}/{kind}")
        with _lock:
            state = self.active(name)
            if state is None:
                raise CanaryConflict(f"no active canary for {name}")
            record = self._audit("skill.canary_graded", {
                "skill": name, "canary_id": state["canary_id"], "variant": variant,
                "kind": kind, "score": float(score), "run_id": str(run_id)[:64],
            })
            state.setdefault("grades", []).append({
                "variant": variant, "kind": kind, "score": float(score),
                "run_id": str(run_id)[:64], "ts": time.time(),
                "hash": str(record.get("hash") or ""),
            })
            self._save(name, state)
            return state

    # -- lifecycle ---------------------------------------------------------

    def start(self, *, name: str, candidate_body: str, live_body: str,
              traffic_percent: int = TRAFFIC_STEPS[0], source: str = "operator",
              trigger: dict | None = None, quality: float | None = None,
              findings: list[dict] | None = None, run_id: str = "") -> dict:
        if not candidate_body.strip():
            raise CanaryError("empty candidate body")
        if not 0 < int(traffic_percent) <= TRAFFIC_STEPS[-1]:
            raise CanaryError(f"traffic_percent must be in (0, {TRAFFIC_STEPS[-1]}]")
        trigger = dict(trigger or {})
        with _lock:
            if self.active(name) is not None:
                raise CanaryConflict(f"{name} already has an active canary")
            canary_id = uuid.uuid4().hex[:12]
            record = self._audit("skill.canary_started", {
                "skill": name, "canary_id": canary_id,
                "traffic_percent": int(traffic_percent), "source": source,
                "candidate_sha": _sha(candidate_body), "live_sha": _sha(live_body),
                "quality": quality, "trigger_reason": str(trigger.get("reason", ""))[:64],
                "trigger_mean": trigger.get("live_mean"), "trigger_n": trigger.get("live_n"),
            })
            d = self._skill_dir(name)
            d.mkdir(parents=True, exist_ok=True)
            _atomic_write(d / "candidate.md", candidate_body)
            _atomic_write(d / "live_at_start.md", live_body)
            try:
                (d / "served.json").unlink()
            except FileNotFoundError:
                pass
            now = time.time()
            state = {
                "canary_id": canary_id, "skill": name, "status": "canary",
                "traffic_percent": int(traffic_percent), "source": source,
                "trigger": trigger, "quality": quality,
                "findings": list(findings or [])[:20], "run_id": run_id,
                "candidate_sha": _sha(candidate_body), "live_sha": _sha(live_body),
                "created_at": now, "grades": [], "served": {}, "events": [],
            }
            self._event(state, "started", record, traffic_percent=int(traffic_percent),
                        source=source)
            self._save(name, state)
            return state

    def _transition(self, name: str, *, to: str, allowed_from: tuple[str, ...],
                    reason_code: str, actor: str, extra: Callable[[dict], None] | None = None) -> dict:
        with _lock:
            state = self._load(name)
            if state is None or state.get("status") not in allowed_from:
                raise CanaryConflict(
                    f"{name}: no canary in {'/'.join(allowed_from)} "
                    f"(is {state.get('status') if state else 'none'})")
            record = self._audit("skill.canary_status_changed", {
                "skill": name, "canary_id": state["canary_id"], "status": to,
                "from_status": state["status"], "reason_code": reason_code[:64],
                "actor": actor[:32],
            })
            if extra is not None:
                extra(state)
            self._event(state, to, record, from_status=state["status"],
                        reason=reason_code, actor=actor)
            state["status"] = to
            self._save(name, state)
            return state

    def set_traffic(self, name: str, percent: int, *, reason_code: str) -> dict:
        percent = int(percent)
        if not 0 < percent <= TRAFFIC_STEPS[-1]:
            raise CanaryError(f"traffic_percent must be in (0, {TRAFFIC_STEPS[-1]}]")
        with _lock:
            state = self.active(name)
            if state is None or state.get("status") != "canary":
                raise CanaryConflict(f"{name}: no running canary")
            previous = int(state.get("traffic_percent", 0))
            record = self._audit("skill.canary_traffic_changed", {
                "skill": name, "canary_id": state["canary_id"],
                "traffic_percent": percent, "from_traffic_percent": previous,
                "reason_code": reason_code[:64],
            })
            state["traffic_percent"] = percent
            self._event(state, "traffic", record, traffic_percent=percent,
                        from_traffic_percent=previous, reason=reason_code)
            self._save(name, state)
            return state

    def pause(self, name: str, *, actor: str = "operator") -> dict:
        return self._transition(name, to="paused", allowed_from=("canary", "ready"),
                                reason_code="paused", actor=actor)

    def resume(self, name: str, *, actor: str = "operator") -> dict:
        return self._transition(name, to="canary", allowed_from=("paused",),
                                reason_code="resumed", actor=actor)

    def mark_ready(self, name: str, *, reason_code: str = "gates_passed") -> dict:
        return self._transition(name, to="ready", allowed_from=("canary",),
                                reason_code=reason_code, actor="autopilot")

    def defer(self, name: str, *, reason_code: str = "deferred", actor: str = "operator") -> dict:
        return self._transition(name, to="deferred", allowed_from=ACTIVE,
                                reason_code=reason_code, actor=actor)

    def approve(self, name: str, registry: Any, *, actor: str = "operator") -> dict:
        """Make the candidate the live body. The skill's registry grades are
        replaced by the candidate's own grades from the canary (the evidence
        FOR the new body); the previous body and grades are kept for rollback."""
        candidate = self.candidate_body(name)
        spec = registry.get(name)
        if candidate is None or spec is None:
            raise CanaryConflict(f"{name}: candidate or live skill missing")
        with _lock:
            state = self._load(name)
            if state is None or state.get("status") not in ACTIVE:
                raise CanaryConflict(f"{name}: no active canary to approve")
            live_body = _strip_front_matter(registry.get_body(name) or "")
            _atomic_write(self._skill_dir(name) / "previous.md", live_body)
            state["previous"] = {
                "grades": list(spec.grades), "description": spec.description,
            }
            self._save(name, state)
            new_grades = [
                {"run_id": g["run_id"], "score": g["score"], "ts": g["ts"],
                 "notes": f"{'outcome' if g['kind'] == OUTCOME else 'usage'} "
                          f"(canary {state['canary_id']})"}
                for g in state.get("grades", []) if g.get("variant") == "candidate"
            ]
            if not any(g["score"] > 0 for g in new_grades):
                new_grades.append({"run_id": f"canary-{state['canary_id']}", "score": 0.3,
                                   "ts": time.time(),
                                   "notes": "approval seed — NOT earned usage; keeps the "
                                            "approved body above the injection gate"})

            def _apply(st: dict) -> None:
                registry.create(
                    name=name, type=spec.type, body_md=candidate,
                    description=spec.description, claim=spec.claim, scope=spec.scope,
                    overwrite=True, created_by=spec.created_by or "skill-canary",
                    meta=spec.meta, quota_exempt=True,
                )
                registry.set_grades(name, new_grades)
                st["approved_at"] = time.time()

            return self._transition(name, to="approved", allowed_from=ACTIVE,
                                    reason_code="approved", actor=actor, extra=_apply)

    def rollback(self, name: str, registry: Any, *, actor: str = "operator",
                 reason_code: str = "rollback") -> dict:
        """Active canary: stop serving the candidate. Approved canary: restore
        the body and grades the skill had before the approval."""
        state = self._load(name)
        if state is None:
            raise CanaryConflict(f"{name}: no canary")
        if state.get("status") in ACTIVE:
            return self._transition(name, to="rolled_back", allowed_from=ACTIVE,
                                    reason_code=reason_code, actor=actor)
        if state.get("status") != "approved":
            raise CanaryConflict(f"{name}: canary is {state.get('status')} — nothing to roll back")
        try:
            previous = (self._skill_dir(name) / "previous.md").read_text(encoding="utf-8")
        except OSError as exc:
            raise CanaryConflict(f"{name}: previous body missing — cannot roll back") from exc
        spec = registry.get(name)
        if spec is None:
            raise CanaryConflict(f"{name}: skill no longer registered")
        prev = state.get("previous") or {}

        def _apply(st: dict) -> None:
            registry.create(
                name=name, type=spec.type, body_md=previous,
                description=prev.get("description") or spec.description,
                claim=spec.claim, scope=spec.scope, overwrite=True,
                created_by=spec.created_by or "skill-canary", meta=spec.meta, quota_exempt=True,
            )
            registry.set_grades(name, list(prev.get("grades") or []))
            st["rolled_back_at"] = time.time()

        return self._transition(name, to="rolled_back", allowed_from=("approved",),
                                reason_code=reason_code, actor=actor, extra=_apply)


def _strip_front_matter(text: str) -> str:
    text = text.lstrip()
    if not text.startswith("---"):
        return text
    rest = text[3:]
    nl = rest.find("\n")
    if nl < 0:
        return text
    rest = rest[nl + 1:]
    end = rest.find("\n---")
    if end < 0:
        return text
    after = rest[end + 4:]
    nl2 = after.find("\n")
    return "" if nl2 < 0 else after[nl2 + 1:]
