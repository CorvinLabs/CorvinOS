"""ideation.py — the --ideas co-ideation mode (ADR-0263 + LDD amendment)."""
from __future__ import annotations

from plugin_builder import ideation, session_store
from plugin_builder.interview import InterviewPhase

TENANT = "test-tenant-ideation"
KEY = "fp-ideation"


def setup_function(_fn):
    ideation.clear(TENANT, KEY)
    session_store.clear(TENANT, KEY)


def teardown_function(_fn):
    ideation.clear(TENANT, KEY)
    session_store.clear(TENANT, KEY)


def test_start_returns_the_ack_gate():
    reply = ideation.start(TENANT, KEY, idea_first=True)
    assert "?" in reply
    assert ideation._get(TENANT, KEY) is not None  # noqa: SLF001 — same-package test


def test_declining_the_ack_hands_off_to_a_plain_interview_immediately():
    ideation.start(TENANT, KEY, idea_first=True)
    reply = ideation.continue_active("no", tenant_id=TENANT, session_key=KEY)
    assert reply is not None
    assert ideation._get(TENANT, KEY) is None  # noqa: SLF001 — ideation session cleared
    handed_off = session_store.get(TENANT, KEY)
    assert handed_off is not None
    assert handed_off.idea_first is True


def test_accepting_the_ack_shows_grounded_proposals_with_sources(monkeypatch):
    from plugin_builder.ideation import GroundedProposal

    monkeypatch.setattr(
        ideation, "grounded_proposals",
        lambda seen, limit=2: (GroundedProposal("A fake idea.", "fake-source:1"),),
    )
    ideation.start(TENANT, KEY, idea_first=True)
    reply = ideation.continue_active("yes", tenant_id=TENANT, session_key=KEY)
    assert "fake-source:1" in reply
    assert "A fake idea." in reply


def test_numeric_pick_hands_off_the_chosen_proposal_text(monkeypatch):
    from plugin_builder.ideation import GroundedProposal

    monkeypatch.setattr(
        ideation, "grounded_proposals",
        lambda seen, limit=2: (
            GroundedProposal("Idea one text.", "src:1"),
            GroundedProposal("Idea two text.", "src:2"),
        ),
    )
    ideation.start(TENANT, KEY, idea_first=True)
    ideation.continue_active("yes", tenant_id=TENANT, session_key=KEY)
    ideation.continue_active("2", tenant_id=TENANT, session_key=KEY)

    handed_off = session_store.get(TENANT, KEY)
    assert handed_off is not None
    assert handed_off.phase == InterviewPhase.IDEA  # idea_text pre-filled, name still pending
    assert handed_off._answers.get("idea_text") == "Idea two text."  # noqa: SLF001


def test_free_text_contribution_converges_directly(monkeypatch):
    monkeypatch.setattr(ideation, "grounded_proposals", lambda seen, limit=2: ())
    ideation.start(TENANT, KEY, idea_first=True)
    ideation.continue_active("yes", tenant_id=TENANT, session_key=KEY)
    reply = ideation.continue_active(
        "Actually I want a plugin that summarizes my inbox.",
        tenant_id=TENANT, session_key=KEY,
    )
    assert reply is not None
    handed_off = session_store.get(TENANT, KEY)
    assert handed_off.phase == InterviewPhase.IDEA  # idea_text pre-filled, name still pending
    assert "summarizes my inbox" in handed_off._answers.get("idea_text", "")  # noqa: SLF001
    assert ideation._get(TENANT, KEY) is None  # noqa: SLF001


def test_more_exhausts_round_cap_and_exits_honestly(monkeypatch):
    monkeypatch.setattr(ideation, "grounded_proposals", lambda seen, limit=2: ())
    ideation.start(TENANT, KEY, idea_first=True)
    ideation.continue_active("yes", tenant_id=TENANT, session_key=KEY)
    for _ in range(ideation.ROUND_CAP):
        reply = ideation.continue_active("more", tenant_id=TENANT, session_key=KEY)
    assert "didn't land" in reply.lower() or "nichts konkretes" in reply.lower()
    assert ideation._get(TENANT, KEY) is None  # noqa: SLF001


def test_cancel_clears_the_session_and_writes_nothing():
    ideation.start(TENANT, KEY, idea_first=True)
    ideation.continue_active("yes", tenant_id=TENANT, session_key=KEY)
    reply = ideation.continue_active("cancel", tenant_id=TENANT, session_key=KEY)
    assert "cancel" in reply.lower() or "abgebrochen" in reply.lower()
    assert ideation._get(TENANT, KEY) is None  # noqa: SLF001
    assert session_store.get(TENANT, KEY) is None


def test_grounded_proposals_never_repeat_a_seen_source():
    first = ideation.grounded_proposals(frozenset())
    seen = frozenset(p.source for p in first)
    second = ideation.grounded_proposals(seen)
    assert not (seen & {p.source for p in second})


def test_grounded_proposals_always_cite_a_source():
    for p in ideation.grounded_proposals(frozenset(), limit=10):
        assert p.source
        assert p.text


def test_starting_ideas_mode_replaces_an_in_progress_interview_with_notice():
    """Regression: opening /plugin-builder, answering partway, then typing
    /plugin-builder --ideas used to silently abandon the interview — the two
    stores didn't know about each other (ADR-0262/0263 review round 1,
    Backend finding 4)."""
    import time

    from plugin_builder.interview import InterviewSession

    session_store._sessions[(TENANT, KEY)] = session_store._Entry(  # noqa: SLF001
        session=InterviewSession(session_id=f"{TENANT}:{KEY}"), last_touched=time.time(),
    )
    assert session_store.get(TENANT, KEY) is not None

    reply = ideation.start(TENANT, KEY, idea_first=True)
    assert "replaced" in reply.lower()
    assert session_store.get(TENANT, KEY) is None
    assert ideation._get(TENANT, KEY) is not None  # noqa: SLF001


def test_handoff_races_a_concurrent_plain_start_without_losing_either_session():
    """Round-4 regression: `_handoff_to_interview` (converging an --ideas
    dialogue) writes to session_store just like `ideation.start()` and
    `turn.command()` do — but was the one write path NOT covered by
    `session_store.cross_store_lock` when that lock was added in round 3.
    A plain /plugin-builder racing a converging --ideas session could get
    its freshly-started interview silently clobbered."""
    import threading

    from plugin_builder import turn

    session_store.clear(TENANT, KEY)
    ideation.clear(TENANT, KEY)
    try:
        ideation.start(TENANT, KEY, idea_first=True)
        ideation.continue_active("yes", tenant_id=TENANT, session_key=KEY)

        barrier = threading.Barrier(2)

        def converge():
            barrier.wait()
            ideation.continue_active("my own idea, converging now", tenant_id=TENANT, session_key=KEY)

        def plain_start():
            barrier.wait()
            turn.command("", tenant_id=TENANT, session_key=KEY)

        threads = [threading.Thread(target=converge), threading.Thread(target=plain_start)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Whichever won, the ideation store must be empty (converged-away or
        # replaced) and session_store must hold exactly one session — never
        # a silently dropped write from either side.
        assert not ideation.is_active(TENANT, KEY)
        assert session_store.get(TENANT, KEY) is not None
    finally:
        session_store.clear(TENANT, KEY)
        ideation.clear(TENANT, KEY)
