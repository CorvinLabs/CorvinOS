"""S7 — auto-grade skills that the LLM actually used in a bridge turn.

Fictional task: a 'csv_diff_workflow' skill is injected into the prompt
of a bridge turn. The user asked Claude to compare two CSVs; Claude's
output mentions the skill by name AND paraphrases its first instruction.
The auto-grader should:

  - bump the skill's grade list with score=0.7 once
  - record `notes` showing whether the match was on name or body snippet
  - leave un-mentioned skills alone

Negative case: the same skill is in the active set, but Claude's output
talks about something else entirely → no auto-grade.

Opt-out: the chat profile carries inject_skills=false → no grades
written even if the output mentions every skill.

Run as: python3 operator/bridges/shared/test_skill_auto_grade.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "skill-forge"))
sys.path.insert(0, str(REPO / "operator" / "forge"))


# Sandbox CORVIN_HOME + plugin slot BEFORE importing skill_inject so the
# skill-forge backend writes into our tmpdir, not the user's real ~/.corvin.
_TD = tempfile.mkdtemp(prefix="auto-grade-")
_SLOT = tempfile.mkdtemp(prefix="auto-grade-slot-")
os.environ["CORVIN_HOME"] = _TD
os.environ["CORVIN_FORCE_SCOPE"] = "user"
os.environ["CORVIN_PLUGIN_SLOT_DIR"] = _SLOT
os.environ["CORVIN_PROJECT_ROOT"] = ""   # suppress project-scope skill discovery

import skill_inject  # noqa: E402
from skill_forge.multi_registry import MultiSkillRegistry  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


SKILL_BODY = (
    "# CSV diff workflow\n\n"
    "Use this skill to compute deterministic diffs between two CSV files.\n\n"
    "## Steps\n\n"
    "1. Load both files via pandas.read_csv with the same dtype map.\n"
    "2. Sort by the primary key.\n"
    "3. Emit a row-wise diff as a markdown table.\n"
)


def _create_eligible_skill(reg: MultiSkillRegistry, name: str) -> None:
    """Create + seed grade so the skill counts as eligible for injection
    AND for auto-grade (mean_score > 0)."""
    reg.create(
        name=name,
        type="learned-experience",
        description="deterministic CSV diff",
        body_md=SKILL_BODY,
        claim={"summary": "Two CSVs can be diffed by sorting on a key."},
        scope="user",
    )
    reg.grade(name, "seed-run", 0.6, notes="seed grade")


def main() -> int:
    print("[skill auto-grade — fictional bridge turn for csv_diff_workflow]")

    reg = MultiSkillRegistry(channel_id=None, project_root=None)
    _create_eligible_skill(reg, "csv_diff_workflow")

    spec_before = reg.list_with_scope()[0][1]
    grades_before = spec_before.n_grades
    t("skill seeded with 1 grade", grades_before == 1, detail=f"n_grades={grades_before}")

    profile: dict = {}  # default profile (inject_skills=true by default)

    # --- Positive: name match -----------------------------------------------
    output = (
        "I used the csv_diff_workflow skill to compare both files. "
        "Sorted by id and emitted a diff table."
    )
    res = skill_inject.auto_grade_from_output(
        channel_id=None,
        profile=profile,
        output_text=output,
        run_id="turn-1",
        project_root=None,
        score=0.7,
    )
    t("returned exactly 1 graded skill",
      len(res) == 1,
      detail=f"got={[r.get('name') for r in res]}")
    t("matched on name (not body)",
      res and res[0].get("matched") == "name")

    spec_after_pos = next(
        (s for sc, s in reg.list_with_scope() if s.name == "csv_diff_workflow"),
        None,
    )
    t("skill now has 2 grades",
      spec_after_pos is not None and spec_after_pos.n_grades == grades_before + 1,
      detail=f"n_grades={spec_after_pos.n_grades if spec_after_pos else None}")
    # Auto-grade is hard-capped at 0.3 (skill_inject._AUTO_GRADE_CAP_MAX).
    # Caller passed score=0.7 but the lib clamps it down to 0.3.
    # mean = (0.6 + 0.3) / 2 = 0.45
    t("mean score updated towards CAPPED auto-grade score",
      spec_after_pos is not None
      and abs(spec_after_pos.mean_score - 0.45) < 1e-9,
      detail=f"mean={spec_after_pos.mean_score if spec_after_pos else None}")

    # --- Positive: body snippet match (paraphrased / no name) --------------
    # Use the first ~80 chars of the (front-matter-stripped) body literally.
    body_snippet = SKILL_BODY[:80].strip()
    output_b = (
        "Here is what I did:\n\n" + body_snippet +
        "\nThen I emitted the result."
    )
    res_b = skill_inject.auto_grade_from_output(
        channel_id=None,
        profile=profile,
        output_text=output_b,
        run_id="turn-2",
        project_root=None,
        score=0.7,
    )
    # The body matcher fires only when the name didn't already match —
    # check that we get exactly one entry that matched on EITHER. The
    # snippet contains "csv_diff" anyway via the heading, so name-match is
    # the most likely winner; both cases are valid auto-grade results.
    t("body snippet output yields a grade",
      len(res_b) == 1)

    # --- Negative: no mention -----------------------------------------------
    output_neg = (
        "I read the file with open() and counted lines. "
        "Returned the count as JSON. Done."
    )
    res_neg = skill_inject.auto_grade_from_output(
        channel_id=None,
        profile=profile,
        output_text=output_neg,
        run_id="turn-3",
        project_root=None,
        score=0.7,
    )
    t("no grade when output does not mention the skill",
      len(res_neg) == 0,
      detail=f"unexpected: {res_neg!r}")

    # --- Opt-out via profile.inject_skills=false ----------------------------
    res_opt = skill_inject.auto_grade_from_output(
        channel_id=None,
        profile={"inject_skills": False},
        output_text=output,  # mentions the skill by name
        run_id="turn-4",
        project_root=None,
    )
    t("inject_skills=false suppresses auto-grade",
      len(res_opt) == 0)

    # --- Empty output -------------------------------------------------------
    res_empty = skill_inject.auto_grade_from_output(
        channel_id=None, profile=profile, output_text="",
        run_id="turn-5", project_root=None,
    )
    t("empty output yields no grades",
      len(res_empty) == 0)

    # --- Negation filter (X3) ----------------------------------------------
    # English negation BEFORE the mention → no grade.
    res_neg_en1 = skill_inject.auto_grade_from_output(
        channel_id=None, profile=profile,
        output_text=("I won't use csv_diff_workflow because the files "
                     "are JSON, not CSV. Used jq instead."),
        run_id="turn-neg-en1", project_root=None,
    )
    t("'won't use <skill>' before name → no grade",
      len(res_neg_en1) == 0,
      detail=f"unexpected: {res_neg_en1!r}")

    res_neg_en2 = skill_inject.auto_grade_from_output(
        channel_id=None, profile=profile,
        output_text=("Decided to skip csv_diff_workflow this time and "
                     "compare line counts manually instead."),
        run_id="turn-neg-en2", project_root=None,
    )
    t("'skip <skill>' before name → no grade",
      len(res_neg_en2) == 0,
      detail=f"unexpected: {res_neg_en2!r}")

    # German negation BEFORE the mention → no grade.
    res_neg_de1 = skill_inject.auto_grade_from_output(
        channel_id=None, profile=profile,
        output_text=("Ich werde nicht csv_diff_workflow verwenden, "
                     "stattdessen mache ich es händisch."),
        run_id="turn-neg-de1", project_root=None,
    )
    t("'nicht <skill>' before name → no grade",
      len(res_neg_de1) == 0,
      detail=f"unexpected: {res_neg_de1!r}")

    # German negation AFTER the mention → no grade.
    res_neg_de2 = skill_inject.auto_grade_from_output(
        channel_id=None, profile=profile,
        output_text=("Ich werde csv_diff_workflow nicht anwenden, "
                     "weil die Daten JSON sind."),
        run_id="turn-neg-de2", project_root=None,
    )
    t("'<skill> nicht' after name → no grade",
      len(res_neg_de2) == 0,
      detail=f"unexpected: {res_neg_de2!r}")

    # Mixed: one negated mention + one clean mention → grade (positive wins).
    res_mixed = skill_inject.auto_grade_from_output(
        channel_id=None, profile=profile,
        output_text=("First I thought I won't use csv_diff_workflow. "
                     "Then I changed my mind. I used csv_diff_workflow "
                     "and it worked great. Sorted by id, emitted diff."),
        run_id="turn-mixed", project_root=None,
    )
    t("mixed (negated + clean mention) → still grade",
      len(res_mixed) == 1,
      detail=f"got={res_mixed!r}")

    # Output too short → no grade even with the name.
    res_short = skill_inject.auto_grade_from_output(
        channel_id=None, profile=profile,
        output_text="csv_diff_workflow",
        run_id="turn-short", project_root=None,
    )
    t("very short output (< 40 chars) → no grade",
      len(res_short) == 0,
      detail=f"unexpected: {res_short!r}")

    # --- Same run_id idempotency check -------------------------------------
    # Auto-grade on the same run_id should still write — registry stores
    # grades as an append-only list keyed by run_id. We don't dedup here
    # (that's the registry's job, and today it does not). Just confirm the
    # call doesn't crash and produces a result entry.
    res_dup = skill_inject.auto_grade_from_output(
        channel_id=None, profile=profile, output_text=output,
        run_id="turn-1", project_root=None,
    )
    t("duplicate run_id call still completes",
      len(res_dup) == 1)

    # Final sanity: turn-2 + turn-1-dup + initial pos call → at least 4 grades
    spec_final = next(
        (s for sc, s in reg.list_with_scope() if s.name == "csv_diff_workflow"),
        None,
    )
    t("final n_grades >= 4 (seed + 3 auto)",
      spec_final is not None and spec_final.n_grades >= 4,
      detail=f"n_grades={spec_final.n_grades if spec_final else None}")

    # --- Score-Cap structural check ----------------------------------------
    # A caller that passes score=0.95 must NOT push the skill above 0.3.
    # Use a fresh skill so we have a clean grade ledger to inspect.
    reg.create(
        name="cap.probe",
        type="learned-experience",
        description="capacity probe for hard-cap",
        body_md=("# cap.probe\n\nThis skill is just here to test the hard-cap "
                 "on auto-grade scores. It mentions cap.probe by name once.\n"),
        claim={"summary": "cap test"},
        scope="user",
    )
    reg.grade("cap.probe", "seed-cap", 0.6, notes="seed")
    skill_inject.auto_grade_from_output(
        channel_id=None,
        profile={},
        output_text=("Used cap.probe to test the cap behaviour. Returned a "
                     "structured response. Filed under capacity testing."),
        run_id="turn-cap-1",
        project_root=None,
        score=0.95,   # caller asks for 0.95, lib must clamp to 0.3
    )
    cap_spec = next(
        (s for sc, s in reg.list_with_scope() if s.name == "cap.probe"),
        None,
    )
    # mean should be (0.6 + 0.3) / 2 = 0.45 — clamped, NOT (0.6 + 0.95)/2.
    t("auto-grade score=0.95 is hard-capped to 0.3",
      cap_spec is not None and abs(cap_spec.mean_score - 0.45) < 1e-9,
      detail=f"mean={cap_spec.mean_score if cap_spec else None}")
    t("default auto-grade score equals the cap (0.3)",
      skill_inject._DEFAULT_AUTO_GRADE_SCORE == skill_inject._AUTO_GRADE_CAP_MAX
      == 0.3)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
