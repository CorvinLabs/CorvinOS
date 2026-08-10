"""Stage grade store + gate + self-improving loop (ADR-0285, P-F).

Reuses the SkillForge grade SHAPE (``{n_grades, mean_score}``) in its OWN store
(``ce_stage_grades.json`` under the tenant global dir) — NEVER the SkillForge
store (ADR-0277: "never describe it as reuse"). First-party (builtin) stages are
vetted and always default-eligible; any non-builtin/opt-in stage needs a passing
mean over a minimum sample before it may enter a DEFAULT pipeline. A brand-new
stage needs a bootstrap seed (≤ cap) so it is not inert forever; self-grading is
excluded. The loop attributes a turn's outcome to the stages that ran.

Community stages themselves remain P-G (no in-process isolation yet, ADR-0285 R2)
— this store governs default-pipeline ENTRY, not process isolation.
"""
from __future__ import annotations

import json
from pathlib import Path

_MIN_SAMPLE = 3
_DEFAULT_THRESHOLD = 0.5
_BOOTSTRAP_CAP = 0.3          # a seed grade may not exceed this (CONCEPT-0001)
# Only grades from these graders promote a stage into a DEFAULT pipeline (review
# R2 finding B2). __bootstrap__ seeds the henne-ei gate but is capped < threshold,
# so it counts toward n yet can never push the mean over on its own; __loop__ is
# the real signal (turn outcomes); operator is a manual override. A stage grading
# ITSELF under a spoofed non-self grader still cannot reach default eligibility.
_TRUSTED_GRADERS = {"__loop__", "__bootstrap__", "operator"}


def _store_path(tenant_id: str) -> Path:
    from forge.paths import tenant_global_dir  # noqa: PLC0415
    return Path(tenant_global_dir(tenant_id)) / "ce_stage_grades.json"


def _load(tenant_id: str) -> dict:
    try:
        p = _store_path(tenant_id)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _save(tenant_id: str, data: dict) -> None:
    p = _store_path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def grade_stage(tenant_id: str, stage_id: str, score: float, notes: str = "",
                grader: str = "") -> None:
    """Record a grade for a stage. Self-grading is structurally excluded, and an
    ANONYMOUS grade (empty grader) is rejected (review R2 finding B2 — the old
    guard `if grader and grader == stage_id` let a stage self-grade via the default
    empty grader). The grader is persisted so eligibility can trust-filter it."""
    if not grader or grader == stage_id:
        raise ValueError("a grade needs a non-self, non-empty grader")
    score = max(0.0, min(1.0, float(score)))
    data = _load(tenant_id)
    rec = data.setdefault(stage_id, {"grades": []})
    rec["grades"].append({"score": score, "notes": str(notes)[:200], "grader": grader})
    _save(tenant_id, data)


def get_grade(tenant_id: str, stage_id: str, *, trusted_only: bool = False) -> dict:
    """Aggregate a stage's grades. ``trusted_only`` counts only grades from a
    trusted grader (used by the default-eligibility gate) so a stage cannot promote
    itself with grades it authored under a spoofed grader name (review R2 B2)."""
    grades = _load(tenant_id).get(stage_id, {}).get("grades", [])
    if trusted_only:
        grades = [g for g in grades if g.get("grader") in _TRUSTED_GRADERS]
    n = len(grades)
    mean = (sum(g["score"] for g in grades) / n) if n else 0.0
    return {"n_grades": n, "mean_score": round(mean, 3)}


def bootstrap_seed(tenant_id: str, stage_id: str,
                   score: float = _BOOTSTRAP_CAP, notes: str = "manual bootstrap seed") -> None:
    """Seed a new stage's first grade (capped) so the gate isn't a henne-ei trap."""
    grade_stage(tenant_id, stage_id, min(score, _BOOTSTRAP_CAP), notes,
                grader="__bootstrap__")


def is_default_eligible(tenant_id: str, stage_id: str, builtin_ids) -> bool:
    """May this stage sit in a DEFAULT pipeline? Builtin (vetted) → always. Else it
    needs ≥ MIN_SAMPLE grades at ≥ THRESHOLD mean. (Opt-in use is always allowed —
    that is how a stage earns its grades; only default promotion is gated.)"""
    if stage_id in set(builtin_ids):
        return True
    g = get_grade(tenant_id, stage_id, trusted_only=True)  # only trusted grades promote
    return g["n_grades"] >= _MIN_SAMPLE and g["mean_score"] >= _DEFAULT_THRESHOLD


def record_turn_outcome(tenant_id: str, stage_ids, success: bool) -> None:
    """The self-improving loop (ADR-0269 Phase-4b): attribute a turn's outcome to
    the stages that ran. Advisory — the operator still disposes (ADR-0284)."""
    score = 1.0 if success else 0.0
    for sid in stage_ids:
        try:
            grade_stage(tenant_id, sid, score, notes="turn_outcome", grader="__loop__")
        except Exception:  # noqa: BLE001 — the loop never breaks a turn
            pass
