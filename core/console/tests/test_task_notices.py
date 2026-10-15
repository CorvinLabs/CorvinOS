"""task_notices: fixed templates, closed fields, fail-closed on id-shaped tokens (PLAN-0958 P2)."""
from __future__ import annotations

import re

import pytest

from corvin_console import task_notices as tn

ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}|\b\d{10,}\b|\b[0-9a-fA-F]{16,}\b")


def test_every_kind_class_and_language_renders_without_ids():
    for lang in ("en", "de"):
        for kind in tn.KINDS:
            for cls in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"):
                text = tn.render(kind, lang=lang, failure_class=cls, label="Quarterly report", attempt=1, max_attempts=2)
                assert "Quarterly report" in text and not ID.search(text)


def test_label_with_ids_is_scrubbed_not_leaked():
    label = tn.task_label("fix 3f2a9c1e-1111-2222-3333-444455556666 and chat 1234567890123 'quoted'")
    assert not ID.search(label) and "'" not in label and "fix" in label
    assert len(tn.task_label("x" * 500)) <= tn.LABEL_MAX
    assert tn.task_label("   ") == "your request"


def test_unknown_kind_or_class_fails_closed():
    with pytest.raises(tn.NoticeError):
        tn.render("nope", failure_class="C1")
    with pytest.raises(tn.NoticeError):
        tn.render("gave_up", failure_class="C99")


def test_attempt_arithmetic_and_german():
    assert "0 left" in tn.render("gave_up", failure_class="C1", label="a", attempt=2, max_attempts=2)
    assert "1 left" in tn.render("interrupted_resuming", failure_class="C1", label="a", attempt=1, max_attempts=2)
    assert "Neustart" in tn.render("interrupted_resuming", lang="de", failure_class="C1", label="a")
