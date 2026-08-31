"""Idea-first interview + checkpoint phase (ADR-0262).

``test_interview.py`` already covers the legacy (flag-off) flow byte-for-byte
unchanged — this file is additive, for the new phases only.
"""
from __future__ import annotations

from plugin_builder.interview import InterviewPhase, InterviewSession


def test_default_construction_is_the_legacy_flow():
    """The regression contract: all three new flags default False, and the
    session starts exactly where ADR-0253 always started."""
    session = InterviewSession(session_id="s0")
    assert session.idea_first is False
    assert session.checkpoint_enabled is False
    assert session.phase == InterviewPhase.PROBLEM


def test_idea_first_starts_at_idea_phase():
    session = InterviewSession(session_id="s1", idea_first=True)
    assert session.phase == InterviewPhase.IDEA
    assert "idea" in session.ask().lower()


def test_idea_first_two_questions_then_confirm_gaps():
    session = InterviewSession(session_id="s2", idea_first=True)
    session.answer("It talks to api.example.com and needs an API key.")
    assert session.phase == InterviewPhase.IDEA  # plugin_name still pending
    session.answer("Notifier")
    # egress + auth were resolved from the free text — only external_libraries
    # should remain, since egress_hosts was also caught by the URL regex.
    assert session.phase == InterviewPhase.CONFIRM_GAPS
    remaining_ids = [q.id for q in (session._dynamic_questions or ())]
    assert remaining_ids == ["external_libraries"]


def test_fully_resolved_text_skips_confirm_gaps_entirely():
    session = InterviewSession(session_id="s3", idea_first=True)
    session.answer(
        "Talks to api.example.com, needs an API key, uses `requests`, "
        "no other dependencies."
    )
    session.answer("Everything Resolver")
    # Every DEPENDENCY_FIELDS entry was resolved -> straight to REVIEW.
    assert session.phase == InterviewPhase.REVIEW
    assert session.idea is not None
    assert session.idea.dependencies.requires_network_egress is True


def test_language_pins_from_first_idea_answer_and_stays_pinned():
    session = InterviewSession(session_id="s4", idea_first=True)
    session.answer("Ich möchte ein Plugin, das mein Postgres-Warehouse abfragt.")
    assert session.language.language == "de"
    de_prompt = session.ask()
    assert "Wie sollen wir" in de_prompt  # the German plugin_name translation
    session.answer("Warehouse-Beobachter")
    # Later free text (English) must not flip the pinned language.
    assert session.language.language == "de"


def test_checkpoint_disabled_review_confirm_goes_straight_to_done():
    session = InterviewSession(session_id="s5", idea_first=True, checkpoint_enabled=False)
    session.answer("A vague idea with no clear signals at all.")
    session.answer("Vague Plugin")
    while session.phase == InterviewPhase.CONFIRM_GAPS:
        session.answer("none")
    assert session.phase == InterviewPhase.REVIEW
    session.answer("confirm")
    assert session.phase == InterviewPhase.DONE
    assert session.result() is not None


def test_checkpoint_enabled_review_confirm_goes_to_checkpoint_first():
    session = InterviewSession(session_id="s6", idea_first=True, checkpoint_enabled=True)
    session.answer("A vague idea with no clear signals at all.")
    session.answer("Vague Plugin Two")
    while session.phase == InterviewPhase.CONFIRM_GAPS:
        session.answer("none")
    session.answer("confirm")
    assert session.phase == InterviewPhase.CHECKPOINT
    # result() must already be available here — the checkpoint step needs it
    # to write the review docs before any code exists.
    assert session.result() is not None
    session.answer("confirm")
    assert session.phase == InterviewPhase.DONE


def test_checkpoint_restart_resets_docs_written_flag():
    session = InterviewSession(session_id="s7", idea_first=True, checkpoint_enabled=True)
    session.answer("A vague idea.")
    session.answer("Restart Test")
    while session.phase == InterviewPhase.CONFIRM_GAPS:
        session.answer("none")
    session.answer("confirm")
    assert session.phase == InterviewPhase.CHECKPOINT
    session.checkpoint_docs_written = True  # simulate turn.py having written docs
    session.answer("restart")
    assert session.phase == InterviewPhase.IDEA
    assert session.checkpoint_docs_written is False


def test_checkpoint_cancel_reaches_cancelled():
    session = InterviewSession(session_id="s8", idea_first=True, checkpoint_enabled=True)
    session.answer("A vague idea.")
    session.answer("Cancel Test")
    while session.phase == InterviewPhase.CONFIRM_GAPS:
        session.answer("none")
    session.answer("confirm")
    session.answer("cancel")
    assert session.phase == InterviewPhase.CANCELLED
    assert session.is_finished()


def test_result_none_before_checkpoint_or_done():
    session = InterviewSession(session_id="s9", idea_first=True)
    assert session.result() is None
    session.answer("Some idea text.")
    assert session.result() is None  # still CONFIRM_GAPS/REVIEW, not finalized


def test_idea_text_over_length_cap_is_rejected_with_a_reprompt():
    """Defense-in-depth against the ADR-0262 review round 2 follow-up: even
    with classifier.py's per-token regex cap, an unbounded overall idea_text
    still costs real (if linear) CPU across many tokens — the system
    boundary itself (what a user can type) needs its own limit too."""
    from plugin_builder.interview import _MAX_IDEA_TEXT_LEN

    session = InterviewSession(session_id="s10", idea_first=True)
    too_long = "a " * (_MAX_IDEA_TEXT_LEN // 2 + 100)
    reply = session.answer(too_long)
    assert "long" in reply.lower()
    assert session.phase == InterviewPhase.IDEA  # did not advance
    assert session.current_question().id == "idea_text"  # still on the same question

    ok_reply = session.answer("A reasonably sized idea description.")
    assert session.phase != InterviewPhase.IDEA or "idea" not in ok_reply.lower()


def test_plugin_name_over_length_cap_is_rejected_with_a_reprompt():
    """Round-4 regression: idea_text got a length bound in round 2, but the
    IDEA phase's other free-text question (plugin_name) was missed — a
    multi-megabyte "name" was accepted whole and spliced into every
    generated doc + the code scaffold."""
    from plugin_builder.interview import _MAX_NAME_LEN

    session = InterviewSession(session_id="s11", idea_first=True)
    session.answer("A perfectly normal idea description.")
    assert session.phase == InterviewPhase.IDEA
    too_long_name = "x" * (_MAX_NAME_LEN + 1)
    reply = session.answer(too_long_name)
    assert "long" in reply.lower()
    assert session.current_question().id == "plugin_name"  # did not advance

    session.answer("Short Name")
    assert session.phase != InterviewPhase.IDEA


def test_classify_checkpoint_decision_recognizes_all_three_tokens():
    from plugin_builder.interview import classify_checkpoint_decision

    assert classify_checkpoint_decision("confirm") == "confirm"
    assert classify_checkpoint_decision("YES") == "confirm"
    assert classify_checkpoint_decision("cancel") == "cancel"
    assert classify_checkpoint_decision("restart") == "restart"
    assert classify_checkpoint_decision("something else entirely") is None
