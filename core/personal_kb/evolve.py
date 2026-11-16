"""Bounded self-evolution (ADR-2327 items 9-12, CONCEPT-0134 outer loop).

Signals are the owner's own review actions on automatic entries (accept, edit, forget) and the
decoder's error classes. Nothing here reads another owner's data.

* Brake: more than half of the last 20 reviewed automatic entries forgotten or edited away
  -> :func:`policy.freeze` (tuning stops, policy tightens, capture continues).
* Capture tuning, every 20 review actions: a high forget share tightens (higher floor, lower cap);
  a very high accept share proposes a higher cap, which is loosening and waits for the owner.
* Decoder outer loop: the same error class in three different ideas tightens the decoder
  parameters (stricter template, one more adversarial pass, smaller task cap for "too big").
"""
from __future__ import annotations

import time
from typing import Any

from . import policy
from .store import PersonalStore

BRAKE_WINDOW = 20
BRAKE_SHARE = 0.5
TUNE_EVERY = 20
DECODER_REPEAT = 3


def record_review(store: PersonalStore, action: str, *, automatic: bool, now: float | None = None) -> None:
    """Remember one review action (accept | edit | forget) and react to it."""
    ts = time.time() if now is None else now
    if action not in ("accept", "edit", "forget"):
        return

    def _add(m: dict[str, Any]) -> None:
        log = list(m.get("review_log", []))
        log.append({"a": action, "auto": bool(automatic), "t": ts})
        m["review_log"] = log[-200:]
        m["review_since_tune"] = int(m.get("review_since_tune", 0)) + 1
    store.update_meta(_add)
    maybe_propose(store)


def _auto_window(store: PersonalStore) -> list[str]:
    return [e["a"] for e in store.meta.get("review_log", []) if e.get("auto")][-BRAKE_WINDOW:]


def maybe_propose(store: PersonalStore) -> str:
    """Returns what happened: "frozen" | "proposed" | "applied" | "none"."""
    if store.meta.get("frozen") or not store.meta.get("evolution", True):
        return "none"
    window = _auto_window(store)
    if len(window) >= BRAKE_WINDOW:
        bad = sum(1 for a in window if a in ("forget", "edit"))
        if bad / len(window) > BRAKE_SHARE:
            policy.freeze(store, "reject_rate")
            return "frozen"
    if int(store.meta.get("review_since_tune", 0)) < TUNE_EVERY:
        return "none"
    store.update_meta(lambda m: m.__setitem__("review_since_tune", 0))
    recent = [e["a"] for e in store.meta.get("review_log", []) if e.get("auto")][-TUNE_EVERY:]
    if not recent:
        return "none"
    cur = policy.current(store)
    forget_share = sum(1 for a in recent if a == "forget") / len(recent)
    accept_share = sum(1 for a in recent if a == "accept") / len(recent)
    changes: dict[str, Any] = {}
    if forget_share >= 0.4:
        changes["confidence_floor"] = min(0.95, round(float(cur["confidence_floor"]) + 0.05, 4))
        changes["daily_cap"] = max(1, int(cur["daily_cap"]) - 2)
    elif accept_share >= 0.9:
        changes["daily_cap"] = min(50, int(cur["daily_cap"]) + 2)
    if not changes:
        return "none"
    d = policy.propose(store, changes, cause="review_signal")
    return "proposed" if d.outcome == "pending_confirmation" else "applied" if d.outcome == "applied" else "none"


def record_decoder_error(store: PersonalStore, idea_uid: str, error_class: str) -> str:
    """Remember one decoder error class for one idea; three different ideas tighten the decoder."""
    def _add(m: dict[str, Any]) -> None:
        errs = [e for e in m.get("decoder_errors", []) if not (e["u"] == idea_uid and e["c"] == error_class)]
        errs.append({"u": idea_uid, "c": error_class})
        m["decoder_errors"] = errs[-300:]
    store.update_meta(_add)
    ideas = {e["u"] for e in store.meta.get("decoder_errors", []) if e["c"] == error_class}
    if len(ideas) < DECODER_REPEAT or store.meta.get("frozen"):
        return "none"
    cur = policy.current(store)
    changes: dict[str, Any] = {}
    if error_class.startswith("gate:") or error_class in ("unclear_goal", "missing_decision", "missing_step"):
        idx = policy.DECODER_TEMPLATES.index(cur["decoder_template_variant"])
        if idx > 0:
            changes["decoder_template_variant"] = policy.DECODER_TEMPLATES[idx - 1]
    if error_class.startswith("objection:"):
        if int(cur["decoder_adversarial_rounds"]) < 3:
            changes["decoder_adversarial_rounds"] = int(cur["decoder_adversarial_rounds"]) + 1
    if error_class == "too_big":
        changes["decoder_task_cap"] = max(3, int(cur["decoder_task_cap"]) - 2)
    if not changes:
        return "none"
    d = policy.propose(store, changes, cause="decoder_errors")
    if d.outcome in ("applied", "pending_confirmation"):
        store.update_meta(lambda m: m.__setitem__(
            "decoder_errors", [e for e in m.get("decoder_errors", []) if e["c"] != error_class]))
    return d.outcome
