"""Unit tests for marker-driven highlight timing (ADR-2212)."""
import pytest

from core.skills.video_producer.workers.narration_markers import (
    MarkerOffset,
    estimate_marker_timestamps,
    split_markers,
)
from core.skills.video_producer.workers.diagram.timed_steps import build_timed_steps
from core.skills.video_producer.workers.diagram.compiler import compile_spec


def test_split_markers_strips_syntax_and_records_offsets():
    clean, offsets = split_markers("Hello [[hl:box1]] world.")
    assert clean == "Hello  world."
    assert offsets == [MarkerOffset(char_offset=6, element_id="box1")]


def test_split_markers_no_markers_returns_text_unchanged():
    clean, offsets = split_markers("Plain text, no markers here.")
    assert clean == "Plain text, no markers here."
    assert offsets == []


def test_split_markers_multiple_in_order():
    clean, offsets = split_markers("A [[hl:x]] B [[hl:y]] C")
    assert clean == "A  B  C"
    assert [o.element_id for o in offsets] == ["x", "y"]
    assert offsets[0].char_offset < offsets[1].char_offset


def test_estimate_marker_timestamps_proportional():
    offsets = [MarkerOffset(char_offset=0, element_id="a"), MarkerOffset(char_offset=50, element_id="b")]
    marks = estimate_marker_timestamps(offsets, clean_text_len=100, audio_duration_s=10.0)
    assert marks == [(0.0, "a"), (5.0, "b")]


def test_estimate_marker_timestamps_empty_offsets_returns_empty():
    assert estimate_marker_timestamps([], clean_text_len=100, audio_duration_s=10.0) == []


def test_estimate_marker_timestamps_zero_duration_returns_empty():
    offsets = [MarkerOffset(char_offset=0, element_id="a")]
    assert estimate_marker_timestamps(offsets, clean_text_len=100, audio_duration_s=0.0) == []


def test_build_timed_steps_no_marks_falls_back_to_single_step():
    base = [{"id": "x", "type": "box", "label": "X", "at": [0, 0]}]
    elements, steps = build_timed_steps(base, [])
    assert elements == base
    assert steps == [["x"]]


def test_build_timed_steps_adds_one_highlight_per_mark():
    base = [{"id": "x", "type": "box", "label": "X", "at": [0, 0]}]
    elements, steps = build_timed_steps(base, [(1.0, "x"), (2.0, "x")])
    assert len(elements) == 3  # base box + 2 highlight elements
    assert len(steps) == 3  # step 0 (no highlight) + one per mark
    highlight_elements = [e for e in elements if e["type"] == "highlight"]
    assert len(highlight_elements) == 2
    assert all(e["target"] == "x" for e in highlight_elements)


def test_build_timed_steps_output_compiles():
    base = [
        {"id": "a", "type": "box", "label": "A", "at": [100, 100]},
        {"id": "b", "type": "box", "label": "B", "right_of": "a", "gap": 60},
    ]
    elements, steps = build_timed_steps(base, [(1.0, "a"), (2.0, "b")])
    compiled = compile_spec({"elements": elements, "steps": steps})
    assert compiled.step_count == 3
    # first step has no highlight div, later steps do
    assert 'class="highlight-ring' not in compiled.html_by_step[0]
    assert 'class="highlight-ring' in compiled.html_by_step[1]
    assert 'class="highlight-ring' in compiled.html_by_step[2]


def test_build_timed_steps_unknown_target_raises_specerror_on_compile():
    """A marker referencing an id not in base_elements must fail at compile
    time via the existing reference-validation path, not silently render."""
    from core.skills.video_producer.workers.diagram.compiler import SpecError

    base = [{"id": "a", "type": "box", "label": "A", "at": [100, 100]}]
    elements, steps = build_timed_steps(base, [(1.0, "nonexistent")])
    with pytest.raises(SpecError, match="unknown box"):
        compile_spec({"elements": elements, "steps": steps})
