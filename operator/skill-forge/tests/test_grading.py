"""E2E: grading semantics — append-only, mean correct, persisted to meta.json,
audit-event written per grade."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skill_forge.registry import SkillRegistry  # noqa: E402
from skill_forge.multi_registry import MultiSkillRegistry, PromotionGateError  # noqa: E402

# Ensure forge is importable for verify_chain
plugins_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(plugins_dir / "forge"))
from forge.security_events import verify_chain  # noqa: E402

# Sandbox the plugin-slot mirror so this test never touches the real
# operator/skill-forge/skills/dyn/ tree.
_SLOT_TMP = tempfile.mkdtemp(prefix="sf-slot-test-")
os.environ["CORVIN_PLUGIN_SLOT_DIR"] = _SLOT_TMP


PASS = 0
FAIL = 0


def t(label, ok, *, detail=""):
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


GOOD_BODY = (
    "# g\n\nA reasonable heuristic body, no injection, no secrets, "
    "fits well within the 8 KiB body limit.\n"
)


def test_grade_append_and_mean():
    print("\n[grade — append-only, mean correct]")
    with tempfile.TemporaryDirectory() as td:
        r = SkillRegistry(Path(td) / "skill-forge")
        r.create(name="x", type="domain", body_md=GOOD_BODY,
                 description="d", claim={})
        for i, score in enumerate([0.0, 0.4, 0.8, 1.0]):
            r.grade("x", run_id=f"r{i}", score=score, notes=f"run{i}")
        spec = r.get("x")
        t("4 grades", spec.n_grades == 4)
        t("mean = 0.55", abs(spec.mean_score - 0.55) < 1e-6,
          detail=f"got {spec.mean_score}")


def test_grade_clamp_validation():
    print("\n[grade — score range enforced]")
    with tempfile.TemporaryDirectory() as td:
        r = SkillRegistry(Path(td) / "skill-forge")
        r.create(name="y", type="domain", body_md=GOOD_BODY,
                 description="d", claim={})
        for bad in (-0.1, 1.1, 5.0):
            try:
                r.grade("y", "r", bad)
                t(f"score={bad} accepted (BUG)", False)
            except ValueError:
                t(f"score={bad} rejected", True)


def test_grade_rejects_nan():
    """Pinned, non-conditional: grade() MUST reject NaN, not just
    'incidentally' via the chained-comparison form. A future refactor
    from `if not (0.0 <= score <= 1.0)` to `if score < 0.0 or score > 1.0`
    would silently let NaN through (both comparisons are False for NaN),
    corrupting mean_score with NaN and defeating the promotion gate below."""
    print("\n[grade — NaN explicitly rejected, not laundered]")
    with tempfile.TemporaryDirectory() as td:
        r = SkillRegistry(Path(td) / "skill-forge")
        r.create(name="nan_y", type="domain", body_md=GOOD_BODY,
                 description="d", claim={})
        try:
            r.grade("nan_y", "r", float("nan"))
            t("NaN score rejected", False, detail="grade() accepted NaN")
        except ValueError:
            t("NaN score rejected", True)
        spec = r.get("nan_y")
        t("no grade was stored", spec.n_grades == 0,
          detail=f"n_grades={spec.n_grades}")


def _clean_scope_env(td):
    for k in (
        "CORVIN_FORCE_SCOPE",
        "CORVIN_DEFAULT_SCOPE",
        "CORVIN_CHANNEL_ID",
        "CORVIN_TASK_ID",
    ):
        os.environ.pop(k, None)
    os.environ["CORVIN_HOME"] = td


def test_enforce_gate_rejects_nan_mean_score():
    """Regression for the fail-closed session->project promotion gate:
    even though grade() itself blocks NaN today, a NaN could still reach
    storage through another path (direct meta.json edit, legacy-data
    migration, a future grade() regression). _enforce_gate must not let
    a non-finite mean_score silently satisfy `mean_score >= 0.5` (NaN
    comparisons are always False, so an unguarded `< 0.5` check would
    let a corrupted skill sail through the gate)."""
    print("\n[_enforce_gate — NaN mean_score must not pass the quality gate]")
    with tempfile.TemporaryDirectory() as td:
        _clean_scope_env(td)
        tid = f"sf-test-{uuid.uuid4().hex[:8]}"
        mr = MultiSkillRegistry(channel_id="ch-nan", task_id=tid)
        mr.create(scope="session", name="nan_p", type="domain",
                  body_md=GOOD_BODY, description="d", claim={})
        # Bypass grade()'s validation entirely — simulate corrupted/legacy
        # data landing directly in storage, which is exactly the scenario
        # the gate must defend against regardless of grade()'s own checks.
        sess_registry = mr._registry("session")
        data = sess_registry._load()
        data["nan_p"]["grades"] = [
            {"run_id": f"r{i}", "score": float("nan"), "ts": 0.0, "notes": ""}
            for i in range(3)
        ]
        sess_registry._save(data)
        spec = sess_registry.get("nan_p")
        t("mean_score is NaN", spec.mean_score != spec.mean_score,
          detail=f"mean_score={spec.mean_score}")
        try:
            mr._enforce_gate(spec, "session", "project", force=False)
            t("gate rejects NaN mean_score", False,
              detail="_enforce_gate raised nothing for a NaN mean_score")
        except PromotionGateError as e:
            t("gate rejects NaN mean_score", True, detail=str(e))


def test_grade_unknown_skill():
    print("\n[grade on unknown name → KeyError]")
    with tempfile.TemporaryDirectory() as td:
        r = SkillRegistry(Path(td) / "skill-forge")
        try:
            r.grade("nope", "r", 0.5)
            t("KeyError raised", False)
        except KeyError:
            t("KeyError raised", True)


def test_grade_audit_chain():
    print("\n[every grade logs an audit event, chain intact]")
    with tempfile.TemporaryDirectory() as td:
        r = SkillRegistry(Path(td) / "skill-forge")
        r.create(name="z", type="domain", body_md=GOOD_BODY,
                 description="d", claim={})
        r.grade("z", "r1", 0.6)
        r.grade("z", "r2", 0.8)
        audit = r.audit_path()
        events = [json.loads(l) for l in audit.read_text().splitlines() if l.strip()]
        actions = [e["event_type"] for e in events]
        t("create + 2 grade events present",
          actions.count("skill.create") == 1 and actions.count("skill.grade") == 2,
          detail=str(actions))
        ok, problems = verify_chain(audit)
        t("chain verifies", ok, detail=str(problems))


def test_grade_meta_json_synced():
    print("\n[meta.json grades stay in sync with manifest]")
    with tempfile.TemporaryDirectory() as td:
        r = SkillRegistry(Path(td) / "skill-forge")
        r.create(name="m", type="domain", body_md=GOOD_BODY,
                 description="d", claim={})
        r.grade("m", "r1", 0.3, notes="needs work")
        meta_path = Path(td) / "skill-forge" / "skills" / "m" / "meta.json"
        meta = json.loads(meta_path.read_text())
        t("meta.json contains grade",
          len(meta["grades"]) == 1 and meta["grades"][0]["run_id"] == "r1")
        t("notes round-trip", meta["grades"][0]["notes"] == "needs work")


def main() -> int:
    test_grade_append_and_mean()
    test_grade_clamp_validation()
    test_grade_rejects_nan()
    test_grade_unknown_skill()
    test_grade_audit_chain()
    test_grade_meta_json_synced()
    test_enforce_gate_rejects_nan_mean_score()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
