"""E2E: MultiSkillRegistry — shadowing + scope routing + audit-root."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skill_forge.multi_registry import MultiSkillRegistry  # noqa: E402


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
    "# trading.score_reviews\n\n"
    "Map a free-text review to 0..100 via brevity, sentiment, named features "
    "and reviewer history weighting. Combine via simple weighted sum.\n"
)


def _clean_env(td):
    for k in (
        "CORVIN_FORCE_SCOPE",
        "CORVIN_DEFAULT_SCOPE",
        "CORVIN_CHANNEL_ID",
        "CORVIN_TASK_ID",
    ):
        os.environ.pop(k, None)
    os.environ["CORVIN_HOME"] = td


def _fresh_task_id() -> str:
    """Use a UUID so reruns don't collide and tests stay isolated."""
    return f"sf-test-{uuid.uuid4().hex[:8]}"


def _cleanup_task(task_id: str) -> None:
    p = Path("/tmp/.corvin/tasks") / task_id
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


def test_shadowing():
    print("\n[shadowing — higher scope wins]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        tid = _fresh_task_id()
        mr = MultiSkillRegistry(channel_id="test-channel", task_id=tid)
        mr.create(scope="user", name="dbl", type="domain",
                  body_md=GOOD_BODY, description="user-version", claim={})
        mr.create(scope="session", name="dbl", type="domain",
                  body_md=GOOD_BODY, description="session-version", claim={})
        spec = mr.get("dbl")
        t("get returns session version", spec is not None
          and spec.description == "session-version")
        user_spec = mr.get_in_scope("dbl", "user")
        t("get_in_scope user still finds user", user_spec is not None
          and user_spec.description == "user-version")
        t("find_scope returns 'session'", mr.find_scope("dbl") == "session")
        names = [s.name for s in mr.list()]
        t("list() dedupes shadowed name", names.count("dbl") == 1)
        _cleanup_task(tid)


def test_audit_shared_at_scope_root():
    print("\n[audit lives at scope_root, sibling to forge/]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        mr = MultiSkillRegistry(channel_id="ch1")
        mr.create(scope="session", name="x", type="domain",
                  body_md=GOOD_BODY, description="d", claim={})
        # forge.scope.scope_root('session') points to corvin_home/sessions/ch1/forge
        # so audit must live at corvin_home/sessions/ch1/audit.jsonl
        audit = Path(td) / "sessions" / "ch1" / "audit.jsonl"
        t("audit at scope_root", audit.exists(), detail=str(audit))


def test_promote_gates():
    print("\n[promotion gates — task -> session -> project -> user]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        tid = _fresh_task_id()
        mr = MultiSkillRegistry(channel_id="ch", task_id=tid)
        mr.create(scope="task", name="p1", type="domain",
                  body_md=GOOD_BODY, description="d", claim={})
        # task->session without grade should fail
        from skill_forge.registry import PromotionGateError
        try:
            mr.promote("p1", to="session")
            t("ungraded task->session blocked", False)
        except PromotionGateError as e:
            t("ungraded task->session blocked", True, detail=str(e))
        # add a grade with score > 0
        mr.grade("p1", "r1", 0.7)
        spec = mr.promote("p1", to="session")
        t("graded task->session works", spec is not None
          and mr.find_scope("p1") == "session")

        # session->project: needs 3 grades & mean >= 0.5
        try:
            mr.promote("p1", to="project")
            t("session->project blocked (only 1 grade)", False)
        except PromotionGateError:
            t("session->project blocked (only 1 grade)", True)
        mr.grade("p1", "r2", 0.6)
        mr.grade("p1", "r3", 0.8)
        # mean now = (0.7+0.6+0.8)/3 = 0.7 ; n=3
        spec = mr.promote("p1", to="project")
        t("session->project works (n=3, mean>=0.5)",
          mr.find_scope("p1") == "project")

        # project->user without force should fail
        try:
            mr.promote("p1", to="user")
            t("project->user without force blocked", False)
        except PromotionGateError:
            t("project->user without force blocked", True)
        spec = mr.promote("p1", to="user", force=True)
        t("project->user with force works", mr.find_scope("p1") == "user")
        _cleanup_task(tid)


def test_promote_low_mean_blocked():
    print("\n[session->project blocked when mean < 0.5]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        tid = _fresh_task_id()
        mr = MultiSkillRegistry(channel_id="ch", task_id=tid)
        mr.create(scope="session", name="p2", type="domain",
                  body_md=GOOD_BODY, description="d", claim={})
        mr.grade("p2", "r1", 0.1)
        mr.grade("p2", "r2", 0.2)
        mr.grade("p2", "r3", 0.3)
        from skill_forge.registry import PromotionGateError
        try:
            mr.promote("p2", to="project")
            t("low-mean session->project blocked", False)
        except PromotionGateError as e:
            t("low-mean session->project blocked", "mean" in str(e),
              detail=str(e))
        _cleanup_task(tid)


def test_promote_score_exactly_zero_blocked():
    print("\n[task->session blocked when the only grade score is exactly 0.0]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        tid = _fresh_task_id()
        mr = MultiSkillRegistry(channel_id="ch", task_id=tid)
        mr.create(scope="task", name="p3", type="domain",
                  body_md=GOOD_BODY, description="d", claim={})
        # A real grade (not "no grades at all") but the score is exactly
        # 0.0 — must hit the `score <= 0` boundary, not the "no grades" path.
        mr.grade("p3", "r1", 0.0)
        from skill_forge.registry import PromotionGateError
        try:
            mr.promote("p3", to="session")
            t("score==0.0 blocks task->session", False)
            raise AssertionError(
                "promote() should have raised PromotionGateError "
                "for a single grade with score==0.0"
            )
        except PromotionGateError as e:
            t("score==0.0 blocks task->session", True, detail=str(e))
        assert mr.find_scope("p3") == "task", "must not have been promoted"
        _cleanup_task(tid)


def test_promote_n_grades_exactly_two_blocked():
    print("\n[session->project blocked when n_grades is exactly 2]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        tid = _fresh_task_id()
        mr = MultiSkillRegistry(channel_id="ch", task_id=tid)
        mr.create(scope="session", name="p4", type="domain",
                  body_md=GOOD_BODY, description="d", claim={})
        # mean is comfortably >= 0.5 — only the n_grades<3 edge is at play.
        mr.grade("p4", "r1", 1.0)
        mr.grade("p4", "r2", 1.0)
        from skill_forge.registry import PromotionGateError
        try:
            mr.promote("p4", to="project")
            t("n_grades==2 blocks session->project", False)
            raise AssertionError(
                "promote() should have raised PromotionGateError "
                "for n_grades==2 (< 3 required)"
            )
        except PromotionGateError as e:
            t("n_grades==2 blocks session->project", True, detail=str(e))
        assert mr.find_scope("p4") == "session", "must not have been promoted"
        _cleanup_task(tid)


def test_promote_mean_exactly_half_allowed():
    print("\n[session->project allowed when mean_score is exactly 0.5 (n=3)]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        tid = _fresh_task_id()
        mr = MultiSkillRegistry(channel_id="ch", task_id=tid)
        mr.create(scope="session", name="p5", type="domain",
                  body_md=GOOD_BODY, description="d", claim={})
        mr.grade("p5", "r1", 0.5)
        mr.grade("p5", "r2", 0.5)
        mr.grade("p5", "r3", 0.5)
        spec = mr.get("p5")
        assert abs(spec.mean_score - 0.5) < 1e-9, (
            f"fixture broken: expected mean==0.5 exactly, got {spec.mean_score}"
        )
        spec = mr.promote("p5", to="project")
        ok = spec is not None and mr.find_scope("p5") == "project"
        t("mean==0.5 (n=3) allows session->project", ok)
        assert ok, (
            "promote() should have SUCCEEDED for mean_score==0.5 exactly "
            "(gate requires mean_score >= 0.5, so equality must pass)"
        )
        _cleanup_task(tid)


def test_invalid_target_scope():
    print("\n[invalid target scope]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        mr = MultiSkillRegistry()
        mr.create(scope="user", name="z", type="domain",
                  body_md=GOOD_BODY, description="d", claim={})
        try:
            mr.promote("z", to="task")
            t("ValueError on to='task'", False)
        except ValueError:
            t("ValueError on to='task'", True)


def main() -> int:
    test_shadowing()
    test_audit_shared_at_scope_root()
    test_promote_gates()
    test_promote_low_mean_blocked()
    test_promote_score_exactly_zero_blocked()
    test_promote_n_grades_exactly_two_blocked()
    test_promote_mean_exactly_half_allowed()
    test_invalid_target_scope()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
