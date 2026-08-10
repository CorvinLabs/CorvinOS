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

LIVE SINCE P-G (ADR-0289). ``is_default_eligible`` is called by
``config.resolve_pipeline`` for any pipeline the operator did NOT author, and
community stages — the non-builtin subject this gate was written for — now exist
because they run in the subprocess sandbox. Was "dormant until P-G" (review R3
finding B1) up to 2026-08-11.

``record_turn_outcome`` is STILL without a production caller: the
outcome-feedback loop (ADR-0269 Phase-4b) that would attribute a turn's success
to the stages that ran is not wired. Grades therefore accrue only from explicit
operator grading today — which is also the only kind that promotes.
"""
from __future__ import annotations

import json
from pathlib import Path

_MIN_SAMPLE = 3
_DEFAULT_THRESHOLD = 0.5
_BOOTSTRAP_CAP = 0.3          # a seed grade may not exceed this (CONCEPT-0001)
# DEFAULT-pipeline promotion requires EXPLICIT operator grades (review R2 C3/C4:
# "operator disposes", ADR-0284). __loop__ (turn outcomes) and __bootstrap__ (the
# henne-ei seed) are ADVISORY only — counting them would auto-promote an opt-in
# stage by mere usage (2×bootstrap@0.3 + 1×loop@1.0 = 0.53 ≥ threshold), with no
# human intent. So only `operator` grades count toward default-eligibility.
_PROMOTING_GRADERS = {"operator"}


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
    # Atomic write (review R2 finding C5): a naked write_text can be torn by a
    # crash / concurrent writer, and _load's bare except then SILENTLY discards
    # every grade. Write a temp file + os.replace (atomic on POSIX + Windows).
    import os  # noqa: PLC0415
    p = _store_path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, p)


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


def get_grade(tenant_id: str, stage_id: str, *, promoting_only: bool = False) -> dict:
    """Aggregate a stage's grades. ``promoting_only`` counts ONLY explicit operator
    grades (used by the default-eligibility gate) so neither a spoofed grader name
    nor mere loop/bootstrap usage can promote a stage (review R2 B2/C3/C4)."""
    grades = _load(tenant_id).get(stage_id, {}).get("grades", [])
    if promoting_only:
        grades = [g for g in grades if g.get("grader") in _PROMOTING_GRADERS]
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
    g = get_grade(tenant_id, stage_id, promoting_only=True)  # only operator grades promote
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
