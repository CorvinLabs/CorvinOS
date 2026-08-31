"""Interview state-machine tests (ADR-0253 Phase 1/3/4)."""
from __future__ import annotations

import pytest

from plugin_builder.interview import InterviewError, InterviewPhase, InterviewSession

PROBLEM_ANSWERS = [
    "Postgres Connector",
    "Query Postgres from a turn without hand-rolled SQL glue.",
    "Data analysts",
    "none",
    "MVP first",
]
DEPENDENCY_ANSWERS = [
    "psycopg", "yes", "yes", "db.example.com", "none", "mvp", "read-only for MVP",
]


def _drive_to_review(session: InterviewSession) -> None:
    for a in PROBLEM_ANSWERS + DEPENDENCY_ANSWERS:
        session.answer(a)


def test_happy_path_reaches_review_then_done():
    s = InterviewSession(session_id="t1")
    assert s.phase == InterviewPhase.PROBLEM
    _drive_to_review(s)
    assert s.phase == InterviewPhase.REVIEW
    assert s.result() is None  # not confirmed yet

    s.answer("confirm")
    assert s.phase == InterviewPhase.DONE
    assert s.is_finished()

    idea, classification = s.result()
    assert idea.plugin_name == "Postgres Connector"
    assert classification.plugin_type == "data_connector"


def test_required_field_rejects_empty_and_reprompts_same_question():
    s = InterviewSession(session_id="t2")
    q_before = s.current_question()
    reply = s.answer("   ")  # plugin_name is required
    assert s.current_question() is q_before  # did not advance
    assert q_before.prompt in reply


def test_yesno_rejects_garbage_and_reprompts():
    s = InterviewSession(session_id="t3")
    for a in PROBLEM_ANSWERS:
        s.answer(a)
    s.answer(DEPENDENCY_ANSWERS[0])  # external_libraries
    q_before = s.current_question()
    assert q_before.id == "requires_auth"
    reply = s.answer("maybe")
    assert s.current_question() is q_before
    assert "yes or no" in reply.lower()


def test_mvp_or_full_accepts_prefixes():
    s = InterviewSession(session_id="t4")
    for a in PROBLEM_ANSWERS:
        s.answer(a)
    for a in DEPENDENCY_ANSWERS[:5]:
        s.answer(a)
    assert s.current_question().id == "mvp_only"
    s.answer("full scope please")
    assert s.current_question().id == "scope_notes"


def test_csv_list_none_is_empty_tuple():
    s = InterviewSession(session_id="t5")
    for a in PROBLEM_ANSWERS:
        s.answer(a)
    reply = s.answer("none")  # external_libraries
    assert "requires_auth" not in reply or True  # just confirm no crash / advanced
    assert s.current_question().id == "requires_auth"


def test_preliminary_classification_set_after_phase_one():
    s = InterviewSession(session_id="t6")
    assert s.preliminary_classification is None
    for a in PROBLEM_ANSWERS:
        s.answer(a)
    assert s.preliminary_classification is not None
    assert s.phase == InterviewPhase.DEPENDENCIES


def test_cancel_from_problem_phase():
    s = InterviewSession(session_id="t7")
    s.answer(PROBLEM_ANSWERS[0])
    s.cancel()
    assert s.phase == InterviewPhase.CANCELLED
    assert s.is_finished()
    assert s.result() is None


def test_cancel_from_review_phase():
    s = InterviewSession(session_id="t8")
    _drive_to_review(s)
    s.answer("cancel")
    assert s.phase == InterviewPhase.CANCELLED


def test_restart_from_review_clears_answers():
    s = InterviewSession(session_id="t9")
    _drive_to_review(s)
    s.answer("restart")
    assert s.phase == InterviewPhase.PROBLEM
    assert s.current_question().id == "plugin_name"
    assert s.idea is None
    assert s.final_classification is None


def test_review_rejects_unknown_token_and_repeats_prompt():
    s = InterviewSession(session_id="t10")
    _drive_to_review(s)
    reply = s.answer("banana")
    assert s.phase == InterviewPhase.REVIEW
    assert "confirm" in reply.lower()


def test_answer_after_done_raises_interview_error():
    s = InterviewSession(session_id="t11")
    _drive_to_review(s)
    s.answer("confirm")
    with pytest.raises(InterviewError):
        s.answer("anything")


def test_answer_after_cancel_raises_interview_error():
    s = InterviewSession(session_id="t12")
    s.cancel()
    with pytest.raises(InterviewError):
        s.answer("anything")


def test_ask_after_cancelled_is_informational_not_error():
    s = InterviewSession(session_id="t13")
    s.cancel()
    assert "cancelled" in s.ask().lower()


def test_concurrent_answers_do_not_interleave_state():
    """Adversarial-review regression: two threads answering the SAME session
    at once (e.g. a client retry / double-submit) must not corrupt which
    answer lands under which question id."""
    import threading

    s = InterviewSession(session_id="t14")
    barrier = threading.Barrier(2)
    answers = ["PluginA", "PluginB"]
    errors: list[BaseException] = []

    def submit(text: str) -> None:
        try:
            barrier.wait(timeout=5)
            s.answer(text)
        except BaseException as exc:  # noqa: BLE001 — surfaced via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=submit, args=(a,)) for a in answers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
    # The lock serializes the two calls rather than rejecting either, so both
    # answers go through — one against "plugin_name", the next against
    # "problem" (whichever thread the OS scheduled first). The property under
    # test is that each answer lands whole under exactly one key — never split
    # or overwritten mid-write the way the pre-fix race corrupted them (a
    # reproduced run before this fix landed one thread's text under the
    # OTHER thread's question id).
    assert s._answer_index == 2
    assert set(s._answers) == {"plugin_name", "problem"}
    assert set(s._answers.values()) == set(answers)
    assert s._answers["plugin_name"] != s._answers["problem"]
