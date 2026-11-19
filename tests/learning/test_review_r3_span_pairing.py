"""Review 2026-11-19 round 3, R3-BR-6: a reused worker id does not lose a run.

``_drop_span_duplicates`` reconciles ``acs.engine_completed`` with the worker's
``engine.span.end``. Two iterations of worker ``w1`` in one run share one join id.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO, _REPO / "core" / "learning"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core.learning import model_selection_learner as msl  # noqa: E402

JOIN = "spn-run1-w1"


def _completion(tokens: int, join: str = JOIN) -> dict:
    return {"input_tokens": tokens, "output_tokens": tokens, "_span_join": join, "kind": "c"}


def _span(tokens: int, span_id: str = JOIN) -> dict:
    return {"input_tokens": tokens, "output_tokens": tokens, "span_id": span_id, "kind": "s"}


def test_join_id_is_shared_by_two_iterations_of_one_worker():
    det = {"run_id": "run1", "worker_id": "w1"}
    assert msl._acs_span_join(det) == msl._acs_span_join(dict(det)) == JOIN


def test_two_iterations_with_both_spans_count_twice():
    out = msl._drop_span_duplicates([_completion(5), _completion(7)], [_span(5), _span(7)])
    assert [e["kind"] for e in out] == ["s", "s"]
    assert sorted(e["input_tokens"] for e in out) == [5, 7]


def test_two_iterations_with_one_span_in_the_window_keep_both_runs():
    # Before: the dict held the one span, BOTH completions were dropped against it,
    # and two runs were counted as one.
    out = msl._drop_span_duplicates([_completion(5), _completion(7)], [_span(5)])
    assert len(out) == 2
    assert sorted(e["kind"] for e in out) == ["c", "s"]
    assert sorted(e["input_tokens"] for e in out) == [5, 7]
    assert all("_span_join" not in e for e in out)


def test_usage_is_not_traded_for_an_unpriced_span_per_iteration():
    # First span lost its token fields (audit floor), second one has them.
    out = msl._drop_span_duplicates([_completion(5), _completion(7)], [_span(0), _span(7)])
    assert len(out) == 2
    assert sorted(e["input_tokens"] for e in out) == [5, 7]
    assert sorted(e["kind"] for e in out) == ["c", "s"]


def test_both_spans_without_usage_keep_both_completions():
    out = msl._drop_span_duplicates([_completion(5), _completion(7)], [_span(0), _span(0)])
    assert [e["kind"] for e in out] == ["c", "c"]


def test_single_run_behaviour_is_unchanged():
    assert [e["kind"] for e in msl._drop_span_duplicates([_completion(5)], [_span(5)])] == ["s"]
    assert [e["kind"] for e in msl._drop_span_duplicates([_completion(5)], [_span(0)])] == ["c"]
    assert [e["kind"] for e in msl._drop_span_duplicates([_completion(5)], [])] == ["c"]
    assert [e["kind"] for e in msl._drop_span_duplicates(
        [_completion(5, join="")], [_span(5)])] == ["c", "s"]
    assert [e["kind"] for e in msl._drop_span_duplicates(
        [_completion(5)], [_span(9, span_id="spn-other")])] == ["c", "s"]
