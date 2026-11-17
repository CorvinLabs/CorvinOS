"""The capture and decoder policy: bounded, versioned, undoable (ADR-2327 items 9-12).

Only parameters on :data:`ALLOWLIST` exist, each with hard bounds and a direction that counts as
"loosening" (capturing more, checking less). Every change is audit-first and writes a new encrypted
version. Rules:

* An automatic change that loosens ANY parameter waits as ``pending_confirmation`` (whole change).
* A change that only tightens applies directly.
* An automatic revert (the brake) never loosens: per parameter the more conservative value wins.
* An owner edit or an owner rollback inside the allowlist is itself the confirmation and applies exactly.
* A pending change records the version it was computed from; confirming it is refused when the
  current version differs or tuning is frozen.

Encryption, keys, consent, binding, erasure, audit, the non-evolvable filters and the private-surface
rule are not parameters and cannot be reached from here (Level 3).
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

from . import audit
from .store import PersonalStore

EXTRACTOR_VARIANTS = ("v0_strict", "v1", "v2_broad")          # ordered: more permissive later
DECODER_TEMPLATES = ("t0_strict", "t1", "t2_light")             # ordered: stricter first


@dataclass(frozen=True)
class ParamSpec:
    kind: str                 # "float" | "int" | "bool" | "enum"
    default: Any
    lo: Any = None
    hi: Any = None
    loosen: str = "higher"    # "higher" | "lower" | "on" | "later" (enum index)
    choices: tuple[str, ...] = ()


ALLOWLIST: dict[str, ParamSpec] = {
    "confidence_floor": ParamSpec("float", 0.7, 0.5, 0.95, "lower"),
    "daily_cap": ParamSpec("int", 20, 1, 50, "higher"),
    "context_ttl_days": ParamSpec("int", 180, 30, 365, "higher"),
    "type_enabled.preference": ParamSpec("bool", True, loosen="on"),
    "type_enabled.context": ParamSpec("bool", True, loosen="on"),
    "type_enabled.idea": ParamSpec("bool", True, loosen="on"),
    "extractor_variant": ParamSpec("enum", "v1", loosen="later", choices=EXTRACTOR_VARIANTS),
    "decoder_adversarial_rounds": ParamSpec("int", 1, 0, 3, "lower"),
    "decoder_task_cap": ParamSpec("int", 7, 3, 15, "higher"),
    "decoder_template_variant": ParamSpec("enum", "t1", loosen="later", choices=DECODER_TEMPLATES),
}


class PolicyRefused(ValueError):
    pass


def defaults() -> dict[str, Any]:
    return {k: s.default for k, s in ALLOWLIST.items()}


def current(store: PersonalStore) -> dict[str, Any]:
    n = int(store.meta.get("policy_current", 0))
    pol = defaults()
    if n > 0:
        stored = store.read_policy_version(n)
        pol.update({k: v for k, v in stored.items() if k in ALLOWLIST})
    return pol


def current_version(store: PersonalStore) -> int:
    return int(store.meta.get("policy_current", 0))


def _check(name: str, value: Any) -> Any:
    spec = ALLOWLIST.get(name)
    if spec is None:
        raise PolicyRefused("unknown parameter")
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise PolicyRefused("expected true or false")
        return value
    if spec.kind == "enum":
        if value not in spec.choices:
            raise PolicyRefused("unknown choice")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyRefused("expected a number")
    v = int(value) if spec.kind == "int" else round(float(value), 4)
    if spec.kind == "int" and v != value:
        raise PolicyRefused("expected a whole number")
    if not (spec.lo <= v <= spec.hi):
        raise PolicyRefused("value out of range")
    return v


def _rank(name: str, value: Any) -> float:
    """A number that grows in the loosening direction."""
    spec = ALLOWLIST[name]
    if spec.kind == "bool":
        return 1.0 if value else 0.0
    if spec.kind == "enum":
        return float(spec.choices.index(value))
    return float(value) if spec.loosen == "higher" else -float(value)


def loosens(name: str, old: Any, new: Any) -> bool:
    return _rank(name, new) > _rank(name, old)


def conservative(name: str, a: Any, b: Any) -> Any:
    return a if _rank(name, a) <= _rank(name, b) else b


def _deltas(cur: dict[str, Any], target: dict[str, Any]) -> tuple[str, str]:
    names = sorted(k for k in ALLOWLIST if cur.get(k) != target.get(k))
    parts = []
    for k in names:
        a, b = cur.get(k), target.get(k)
        if isinstance(a, (int, float)) and not isinstance(a, bool):
            parts.append(f"{k}:{b - a:+g}")
        else:
            parts.append(f"{k}:{'loosen' if loosens(k, a, b) else 'tighten'}")
    return ",".join(names)[:400], ",".join(parts)[:400]


def _apply(store: PersonalStore, target: dict[str, Any], *, event: str, cause: str) -> int:
    cur = current(store)
    params, deltas = _deltas(cur, target)
    audit.emit(event, tenant_id=store.tenant_id, owner_hash=store.owner_hash,
               policy_version=current_version(store) + 1, params=params, deltas=deltas, cause_code=cause)
    return store.write_policy_version(target)


def _refuse(store: PersonalStore, params: str, cause: str, msg: str) -> None:
    audit.emit("personal_kb.policy_refused", tenant_id=store.tenant_id, owner_hash=store.owner_hash,
               policy_version=current_version(store), params=params[:400], cause_code=cause)
    raise PolicyRefused(msg)


def _validated_target(store: PersonalStore, changes: dict[str, Any], cause: str) -> dict[str, Any]:
    target = current(store)
    for name, value in (changes or {}).items():
        try:
            target[name] = _check(name, value)
        except PolicyRefused as exc:
            # only an allowlisted name reaches the audit record: an arbitrary request key is never written there
            _refuse(store, str(name) if name in ALLOWLIST else "unknown", cause, str(exc))
    return target


@dataclass
class Decision:
    outcome: str              # "applied" | "pending_confirmation" | "unchanged"
    version: int
    change_id: str | None = None


def owner_edit(store: PersonalStore, changes: dict[str, Any]) -> Decision:
    target = _validated_target(store, changes, "owner_edit")
    if target == current(store):
        return Decision("unchanged", current_version(store))
    n = _apply(store, target, event="personal_kb.policy_updated", cause="owner_edit")
    return Decision("applied", n)


def propose(store: PersonalStore, changes: dict[str, Any], *, cause: str, require_confirmation: bool = False) -> Decision:
    """An automatic change (Level 1). Refused while frozen; loosening waits for the owner, and so does every change with
    ``require_confirmation`` (the decoder's prompt-level knobs, ADR-2329 item 6: no controller beat doing nothing)."""
    if store.meta.get("frozen"):
        _refuse(store, ",".join(sorted(changes or {})), cause, "tuning is frozen")
    cur = current(store)
    target = _validated_target(store, changes, cause)
    if target == cur:
        return Decision("unchanged", current_version(store))
    if require_confirmation or any(loosens(k, cur[k], target[k]) for k in ALLOWLIST):
        change_id = secrets.token_hex(6)

        def _merge(m: dict[str, Any]) -> None:
            # One pending slot: a second suggestion joins the first instead of replacing it (before, a proposal for more
            # critic rounds silently dropped a waiting stricter template; ADR-2329 review).
            old = m.get("policy_pending") or {}
            base = current_version(store)
            merged = dict(target)
            causes = [cause]
            if old and int(old.get("base_version", -1)) == base:
                for k, v in (old.get("target") or {}).items():
                    if k in ALLOWLIST and v != cur.get(k) and k not in changes:
                        merged[k] = v
                causes = sorted(set(str(old.get("cause", "")).split("+")) | {cause} - {""})
            m["policy_pending"] = {"change_id": change_id, "base_version": base, "target": merged,
                                   "cause": "+".join(causes)[:80], "at": time.time()}
        store.update_meta(_merge)
        return Decision("pending_confirmation", current_version(store), change_id)
    n = _apply(store, target, event="personal_kb.policy_updated", cause=cause)
    return Decision("applied", n)


def confirm(store: PersonalStore, change_id: str) -> Decision:
    pending = store.meta.get("policy_pending") or {}
    if not pending or pending.get("change_id") != change_id:
        raise PolicyRefused("no such pending change")
    if store.meta.get("frozen") or int(pending.get("base_version", -1)) != current_version(store):
        store.update_meta(lambda m: m.__setitem__("policy_pending", None))
        _refuse(store, "pending", "stale_confirmation", "the policy changed since this suggestion; it was dropped")
    target = {k: _check(k, v) for k, v in pending["target"].items() if k in ALLOWLIST}
    cur = current(store)
    params, deltas = _deltas(cur, target)
    audit.emit("personal_kb.policy_confirmed", tenant_id=store.tenant_id, owner_hash=store.owner_hash,
               policy_version=current_version(store) + 1, params=params, deltas=deltas,
               cause_code=str(pending.get("cause", "auto"))[:40])
    n = store.write_policy_version(target)
    store.update_meta(lambda m: m.__setitem__("policy_pending", None))
    return Decision("applied", n)


def dismiss(store: PersonalStore, change_id: str) -> None:
    pending = store.meta.get("policy_pending") or {}
    if pending.get("change_id") == change_id:
        store.update_meta(lambda m: m.__setitem__("policy_pending", None))


def rollback(store: PersonalStore, to_version: int, *, automatic: bool) -> Decision:
    if not (1 <= int(to_version) <= int(store.manifest.get("policy_versions", 0))):
        raise PolicyRefused("no such policy version")
    old = defaults()
    old.update({k: v for k, v in store.read_policy_version(int(to_version)).items() if k in ALLOWLIST})
    cur = current(store)
    target = {k: conservative(k, cur[k], old[k]) for k in ALLOWLIST} if automatic else old
    if target == cur:
        return Decision("unchanged", current_version(store))
    n = _apply(store, target, event="personal_kb.policy_rolled_back",
               cause="brake" if automatic else "owner_rollback")
    return Decision("applied", n)


def freeze(store: PersonalStore, reason: str) -> None:
    """The brake: stop tuning, tighten toward the version before the current one (never loosen)."""
    audit.emit("personal_kb.learning_frozen", tenant_id=store.tenant_id, owner_hash=store.owner_hash,
               reason_code=reason[:40])
    store.update_meta(lambda m: (m.__setitem__("frozen", True), m.__setitem__("policy_pending", None)))
    n = current_version(store)
    if n >= 2:
        rollback(store, n - 1, automatic=True)
    elif n == 1:
        cur = current(store)
        target = {k: conservative(k, cur[k], ALLOWLIST[k].default) for k in ALLOWLIST}
        if target != cur:
            _apply(store, target, event="personal_kb.policy_rolled_back", cause="brake")


def unfreeze(store: PersonalStore) -> None:
    audit.emit("personal_kb.policy_unfrozen", tenant_id=store.tenant_id, owner_hash=store.owner_hash,
               policy_version=current_version(store), cause_code="owner")
    store.update_meta(lambda m: (m.__setitem__("frozen", False), m.__setitem__("review_log", [])))
