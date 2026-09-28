"""Autonomous Skill Forge (ADR-2094) — loss detection, candidate forging and
canary gate execution for the skills of one registry root.

Loss signal: a skill whose ORGANIC grades — the outcome grades written from
the user's follow-up turn (approval 0.9 / rephrase 0.3 / rejection 0.1) —
average below ``LOSS_THRESHOLD`` over at least ``LOSS_MIN_OUTCOMES`` grades
inside ``LOSS_WINDOW_S``. Usage auto-grades (capped at 0.3) and bootstrap
seeds say nothing about quality and are ignored.

Fork: the Skill-Creator refines the live body into a CANDIDATE (phases 1-4,
no promotion) and a canary starts at the first traffic step. The live skill
is untouched until an operator approves.

Tick: for every running canary the gate verdict (``canary.evaluate``) is
executed — escalate to the next traffic step, mark ready at the top step
(approval stays a human decision), or roll back. Then at most one new loss
signal is forked. The autopilot can be switched off per registry root; the
operator actions (fork, approve, defer, pause, resume, rollback) work either
way.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .registry_bridge import registry_for, skill_body, strip_front_matter

logger = logging.getLogger(__name__)

LOSS_THRESHOLD = 0.5
LOSS_MIN_OUTCOMES = 3
LOSS_WINDOW_S = 14 * 24 * 3600
#: No automatic re-fork of a skill whose last canary ended less than this ago.
COOLDOWN_S = 24 * 3600
TICK_INTERVAL_S = 600

_forks_in_flight: set[str] = set()
_forks_lock = threading.Lock()


def _canary_module():
    """``<registry package>.canary`` — the same package the registry module
    was loaded from. ``import_module`` and never a bare ``sys.modules`` read:
    a concurrent first import (two console requests on the thread pool) would
    otherwise hand one caller the partially initialised module."""
    import importlib  # noqa: PLC0415
    from .registry_bridge import _load_registry_module  # noqa: PLC0415
    pkg = _load_registry_module().__name__.rsplit(".", 1)[0]
    return importlib.import_module(f"{pkg}.canary")


def canary_store(root: Path) -> Any:
    return _canary_module().SkillCanary(Path(root))


# -- autopilot switch --------------------------------------------------------

def _settings_path(root: Path) -> Path:
    return Path(root) / "canary" / "autopilot.json"


def autopilot_settings(root: Path) -> dict:
    try:
        data = json.loads(_settings_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("enabled", True)
    return data


def _write_settings(root: Path, data: dict) -> None:
    path = _settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def set_autopilot(root: Path, enabled: bool, *, audit: Callable[[str, dict], Any]) -> dict:
    """Switch the autopilot; the switch is audited before it is written."""
    data = autopilot_settings(root)
    audit("skill.canary_autopilot_changed", {"enabled": bool(enabled),
                                              "from_enabled": bool(data["enabled"])})
    data["enabled"] = bool(enabled)
    data["changed_at"] = time.time()
    _write_settings(root, data)
    return data


# -- loss signal -------------------------------------------------------------

def _outcome_scores(spec: Any, now: float) -> list[float]:
    return [
        float(g.get("score", 0.0)) for g in (spec.grades or [])
        if str(g.get("notes", "")).startswith("outcome (")
        and now - float(g.get("ts", 0.0)) <= LOSS_WINDOW_S
    ]


def skills_overview(root: Path) -> list[dict]:
    """Every registered skill with its measurement and canary status."""
    now = time.time()
    reg = registry_for(root)
    store = canary_store(root)
    out = []
    for spec in reg.list():
        outcomes = _outcome_scores(spec, now)
        mean = round(sum(outcomes) / len(outcomes), 4) if outcomes else None
        state = store.state(spec.name)
        loss = bool(len(outcomes) >= LOSS_MIN_OUTCOMES and mean is not None
                    and mean < LOSS_THRESHOLD)
        out.append({
            "skill_id": spec.name,
            "description": spec.description,
            "outcome_n": len(outcomes),
            "outcome_mean": mean,
            "usage_n": sum(1 for g in (spec.grades or [])
                           if str(g.get("notes", "")).startswith("auto-grade")),
            "loss_signal": loss,
            "canary_status": (state or {}).get("status"),
            "canary_id": (state or {}).get("canary_id"),
        })
    out.sort(key=lambda r: (not r["loss_signal"], r["skill_id"]))
    return out


def loss_signals(root: Path) -> list[dict]:
    """Skills the autopilot may fork now (loss, no canary, out of cooldown)."""
    now = time.time()
    store = canary_store(root)
    signals = []
    for row in skills_overview(root):
        if not row["loss_signal"]:
            continue
        state = store.state(row["skill_id"])
        if state and state.get("status") in _canary_module().ACTIVE:
            continue
        if state and now - float(state.get("updated_at", 0)) < COOLDOWN_S:
            continue
        signals.append({
            "skill_id": row["skill_id"], "reason": "outcome_mean_below_threshold",
            "live_mean": row["outcome_mean"], "live_n": row["outcome_n"],
        })
    return signals


def refine_instruction(skill_id: str, trigger: dict) -> str:
    mean = trigger.get("live_mean")
    n = trigger.get("live_n")
    measured = (f" Over the last {n} rated uses it averaged {mean:.2f} on a 0-1 scale "
                f"(0.9 = user approved, 0.3 = user had to rephrase, 0.1 = user rejected)."
                if isinstance(mean, (int, float)) and n else "")
    return (f"Improve the skill {skill_id} so users get what they asked for on the "
            f"first attempt.{measured} Keep its purpose and name; make the method "
            f"more precise, remove steps that mislead, and add the checks that "
            f"would have caught a wrong answer.")


# -- fork --------------------------------------------------------------------

def fork(root: Path, skill_id: str, *, source: str, trigger: Optional[dict] = None,
         instruction: str = "", progress_cb: Optional[Callable] = None,
         orchestrator_factory: Optional[Callable[..., Any]] = None) -> dict:
    """Refine ``skill_id`` into a candidate and start its canary.

    Blocking (minutes on the Claude Code engine). One fork per skill at a time.
    """
    root = Path(root)
    key = f"{root}::{skill_id}"
    with _forks_lock:
        if key in _forks_in_flight:
            raise RuntimeError(f"a candidate for {skill_id} is already being forged")
        _forks_in_flight.add(key)
    try:
        store = canary_store(root)
        if store.active(skill_id) is not None:
            raise _canary_module().CanaryConflict(f"{skill_id} already has an active canary")
        body = skill_body(root, skill_id)
        if body is None:
            raise KeyError(f"skill not found: {skill_id}")
        base = {"name": skill_id, "body": strip_front_matter(body)}
        trigger = dict(trigger or {"reason": "operator_request"})
        # ADR-2095: a fork is a skill generation. Free tier: refuse up front
        # when no credit is left (before minutes of engine time), charge
        # once the candidate exists. Members: both are no-ops.
        from .registry_bridge import _load_registry_module  # noqa: PLC0415
        reg_mod = _load_registry_module()
        tenant_id, corvin_home = reg_mod._tenant_and_home(root)
        quota = reg_mod.skill_quota_status(tenant_id, corvin_home)
        if quota.get("remaining") == 0:
            raise reg_mod.SkillQuotaExceeded(tenant_id=tenant_id, limit=quota["limit"],
                                             used=quota["used"])
        if orchestrator_factory is None:
            from .skill_creator import SkillCreatorOrchestrator  # noqa: PLC0415
            orchestrator_factory = SkillCreatorOrchestrator
        orchestrator = orchestrator_factory(progress_cb=progress_cb, registry_root=str(root))
        candidate = asyncio.run(orchestrator.create_candidate(
            instruction or refine_instruction(skill_id, trigger), base))
        reg_mod.charge_skill_quota(tenant_id, corvin_home)
        return store.start(
            name=skill_id, candidate_body=candidate["body"], live_body=base["body"],
            source=source, trigger=trigger, quality=candidate.get("quality"),
            findings=candidate.get("findings"),
        )
    finally:
        with _forks_lock:
            _forks_in_flight.discard(key)


def forks_in_flight(root: Path) -> list[str]:
    prefix = f"{Path(root)}::"
    with _forks_lock:
        return sorted(k[len(prefix):] for k in _forks_in_flight if k.startswith(prefix))


# -- tick --------------------------------------------------------------------

def tick(root: Path, *, fork_fn: Callable[..., dict] = fork) -> dict:
    """One autopilot pass over ``root``. Returns what it did."""
    root = Path(root)
    settings = autopilot_settings(root)
    result: dict[str, Any] = {"ts": time.time(), "actions": []}
    if not settings["enabled"]:
        result["skipped"] = "autopilot_disabled"
        return result
    cm = _canary_module()
    store = canary_store(root)
    reg = None
    for state in store.list_states():
        if state.get("status") != "canary":
            continue
        name = state["skill"]
        verdict = cm.evaluate(state)
        try:
            if verdict["decision"] == "escalate":
                step = cm.next_traffic_step(int(state["traffic_percent"]))
                if step is not None:
                    store.set_traffic(name, step, reason_code="gates_passed")
                    result["actions"].append({"skill_id": name, "action": f"traffic_{step}"})
            elif verdict["decision"] == "ready":
                store.mark_ready(name)
                result["actions"].append({"skill_id": name, "action": "ready"})
            elif verdict["decision"] == "rollback":
                reg = reg or registry_for(root)
                store.rollback(name, reg, actor="autopilot", reason_code="gates_failed")
                result["actions"].append({"skill_id": name, "action": "rolled_back"})
        except Exception as exc:  # noqa: BLE001 — one canary must not stop the pass
            logger.warning("autopilot: %s on %s failed: %s", verdict["decision"], name, exc)
    signals = loss_signals(root)
    if signals and not forks_in_flight(root):
        sig = signals[0]
        try:
            fork_fn(root, sig["skill_id"], source="autopilot", trigger=sig)
            result["actions"].append({"skill_id": sig["skill_id"], "action": "forked"})
        except Exception as exc:  # noqa: BLE001
            logger.warning("autopilot: fork of %s failed: %s", sig["skill_id"], exc)
            result["actions"].append({"skill_id": sig["skill_id"], "action": "fork_failed",
                                      "error": type(exc).__name__})
    settings = autopilot_settings(root)
    settings["last_tick"] = result["ts"]
    settings["last_actions"] = result["actions"][-10:]
    _write_settings(root, settings)
    return result


_scheduler: Optional[threading.Thread] = None


def start_scheduler(roots: Callable[[], list[Path]], *, interval_s: float = TICK_INTERVAL_S) -> bool:
    """Run :func:`tick` over ``roots()`` every ``interval_s`` on a daemon
    thread. Idempotent per process; returns False when already running."""
    global _scheduler
    if _scheduler is not None and _scheduler.is_alive():
        return False

    def _loop() -> None:
        time.sleep(min(60.0, interval_s))
        while True:
            try:
                for root in roots():
                    try:
                        tick(root)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("autopilot tick for %s failed: %s", root, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("autopilot root discovery failed: %s", exc)
            time.sleep(interval_s)

    _scheduler = threading.Thread(target=_loop, name="skill-forge-autopilot", daemon=True)
    _scheduler.start()
    return True
