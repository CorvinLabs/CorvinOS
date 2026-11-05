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

``record_turn_outcome`` is wired as of G4 (ADR-0371): ``chat_runtime.stream_turn``
calls it after a completed console turn (native AND ACS-delegated; the ACS branch
returns early and calls it itself, with the run's real result as the signal), behind the default-off ``outcome_feedback_loop``
flag. Those grades are ADVISORY (``grader="__loop__"``) — only an explicit ``operator``
grade promotes. On a default install (flag off) grades still accrue only from operator
grading + the bootstrap seed. NOTE: the loop records ``success=True`` for every completed
turn (no richer signal yet), so with the flag on the auto-earned mean trends toward 1.0 —
treat it as "the stage participated in completed turns," not a quality score, until a real
success signal (user 👍/👎 or task outcome) feeds it.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

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
    from corvin_operator.forge.forge.paths import tenant_global_dir  # noqa: PLC0415
    return Path(tenant_global_dir(tenant_id)) / "ce_stage_grades.json"


class GradeStoreUnreadable(RuntimeError):
    """The grade store exists but does not parse (a torn write, a foreign writer). Never read
    as "no grades": the next grade would then replace every prior operator grade (review
    2026-11-05 LRN-11). Writers refuse; the eligibility gate reads it as not eligible."""


#: In-process half of the store lock; the file lock in ``_store_lock`` is the
#: cross-process half (msvcrt locks on Windows are per process, so threads need this).
_THREAD_LOCK = threading.Lock()


@contextmanager
def _store_lock(tenant_id: str) -> Iterator[None]:
    """Exclusive lock around one load-modify-save. The console and the bridge (and their
    threads) both grade: without it 4 threads x 50 grades kept 49 of 200 (LRN-11)."""
    p = _store_path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK:
        fh = open(p.with_name(p.name + ".lock"), "a+b")
        try:
            try:
                import fcntl  # noqa: PLC0415
            except ImportError:  # Windows
                import msvcrt  # noqa: PLC0415
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fh.close()  # closing drops the flock / msvcrt lock


def _load(tenant_id: str) -> dict:
    """The store's content; ``{}`` only when it does not exist. An unreadable store raises
    :class:`GradeStoreUnreadable` - fail-closed, never silently empty (LRN-11)."""
    p = _store_path(tenant_id)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise GradeStoreUnreadable(f"stage grade store unreadable ({type(exc).__name__})") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise GradeStoreUnreadable("stage grade store does not parse") from exc
    if not isinstance(data, dict):
        raise GradeStoreUnreadable("stage grade store is not an object")
    return data


def _save(tenant_id: str, data: dict) -> None:
    # Atomic write (review R2 finding C5) through a UNIQUE temp file: the old
    # ``.tmp.<pid>`` name was shared by every thread of one process, so two threads
    # raced on it (FileNotFoundError on replace, LRN-11). Callers hold _store_lock.
    p = _store_path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def grade_stage(tenant_id: str, stage_id: str, score: float, notes: str = "",
                grader: str = "") -> None:
    """Record a grade for a stage. Self-grading is structurally excluded, and an
    ANONYMOUS grade (empty grader) is rejected (review R2 finding B2 — the old
    guard `if grader and grader == stage_id` let a stage self-grade via the default
    empty grader). The grader is persisted so eligibility can trust-filter it.

    Raises :class:`GradeStoreUnreadable` when the existing store does not parse."""
    if not grader or grader == stage_id:
        raise ValueError("a grade needs a non-self, non-empty grader")
    score = max(0.0, min(1.0, float(score)))
    with _store_lock(tenant_id):
        data = _load(tenant_id)
        rec = data.setdefault(stage_id, {"grades": []})
        # ``ts`` dates the grade so the Learning Loops view can tell a live loop from a
        # stopped one. Grades written before this field existed stay undated and count
        # only toward the all-time total.
        rec["grades"].append({"score": score, "notes": str(notes)[:200], "grader": grader,
                              "ts": round(time.time(), 3)})
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
    try:
        g = get_grade(tenant_id, stage_id, promoting_only=True)  # only operator grades promote
    except GradeStoreUnreadable:
        return False  # fail-closed: an unreadable store promotes nothing (LRN-11)
    return g["n_grades"] >= _MIN_SAMPLE and g["mean_score"] >= _DEFAULT_THRESHOLD


def record_turn_outcome(tenant_id: str, stage_ids, success: bool) -> None:
    """The self-improving loop (ADR-0269 Phase-4b): attribute a turn's outcome to
    the stages that ran. Advisory — the operator still disposes (ADR-0284)."""
    score = 1.0 if success else 0.0
    for sid in stage_ids:
        try:
            grade_stage(tenant_id, sid, score, notes="turn_outcome", grader="__loop__")
        except Exception as exc:  # noqa: BLE001 — the loop never breaks a turn
            logger.warning("stage outcome grade for %s not recorded: %s", sid, exc)
