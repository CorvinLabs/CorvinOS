"""Which project a board card belongs to (REVIEW-0082 N30): only a KB card has one; its reference names it, a card projected before
references carried the project is read from its label, a manual card never belongs to a project."""
from core.task_tracking.projection import project_of_card


def test_a_kb_card_is_named_by_its_reference():
    assert project_of_card({"external_ref": "kb:01ABC@alpha", "labels": ["kb", "prj-alpha"]}) == "alpha"


def test_a_card_projected_before_references_carried_the_project_keeps_it():
    # live install 2026-11-11: project "t" had three cards "kb:<uid>" + "prj-t" from 2026-11-08
    assert project_of_card({"external_ref": "kb:01ABC", "labels": ["kb", "prj-t"]}) == "t"


def test_a_bound_kb_card_and_a_manual_card_have_none():
    assert project_of_card({"external_ref": "kb:01ABC", "labels": ["kb"]}) is None
    assert project_of_card({"external_ref": None, "labels": ["prj-alpha"]}) is None
    assert project_of_card({"external_ref": "github:1", "labels": ["prj-alpha"]}) is None
