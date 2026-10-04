"""Unit tests (tier 1-2, no Playwright) for the grid/brace/highlight vocabulary
added on top of box/arrow in compiler.py. Fast gates before the Playwright-
backed E2E test (test_diagram_renderer_e2e.py) spends real browser time.
"""
import pytest

from core.skills.video_producer.workers.diagram.compiler import (
    SpecError,
    _rects_overlap,
    _resolve_collisions,
    _resolve_positions,
    compile_spec,
    normalize_spec,
)

BOX_X = {"id": "x", "type": "box", "label": "X", "at": [0, 0]}


def test_grid_renders_without_error():
    spec = {"elements": [{"id": "g1", "type": "grid", "at": [0, 0], "size": [400, 300], "spacing": 50}]}
    compiled = compile_spec(spec)
    assert "stroke" in compiled.html_by_step[0]


def test_brace_spans_two_boxes():
    spec = {
        "elements": [
            BOX_X,
            {"id": "y", "type": "box", "label": "Y", "right_of": "x", "gap": 40},
            {"id": "br", "type": "brace", "spans": ["x", "y"], "side": "bottom", "label": "group"},
        ]
    }
    compiled = compile_spec(spec)
    assert "group" in compiled.html_by_step[0]


def test_brace_without_spans_raises_specerror():
    spec = {"elements": [{"id": "br", "type": "brace", "side": "bottom"}]}
    with pytest.raises(SpecError, match="needs 'spans'"):
        compile_spec(spec)


def test_brace_unknown_span_id_raises_specerror():
    spec = {"elements": [BOX_X, {"id": "br", "type": "brace", "spans": ["x", "nope"]}]}
    with pytest.raises(SpecError, match="unknown box ids"):
        compile_spec(spec)


@pytest.mark.parametrize("style", ["ring", "glow"])
def test_highlight_styles_render(style):
    spec = {"elements": [BOX_X, {"id": "h1", "type": "highlight", "target": "x", "style": style}]}
    compiled = compile_spec(spec)
    assert f'class="highlight-{style}' in compiled.html_by_step[0]


def test_highlight_unknown_target_raises_specerror():
    spec = {"elements": [BOX_X, {"id": "h1", "type": "highlight", "target": "nope"}]}
    with pytest.raises(SpecError, match="unknown box ids: \\['nope'\\]"):
        compile_spec(spec)


def test_highlight_unknown_style_raises_specerror():
    spec = {"elements": [BOX_X, {"id": "h1", "type": "highlight", "target": "x", "style": "sparkle"}]}
    with pytest.raises(SpecError, match="style of 'h1' must be one of"):
        compile_spec(spec)


def test_steps_can_reveal_grid_brace_highlight_progressively():
    spec = {
        "elements": [
            {"id": "g1", "type": "grid", "at": [0, 0], "size": [200, 200], "spacing": 50},
            BOX_X,
            {"id": "y", "type": "box", "label": "Y", "right_of": "x", "gap": 40},
            {"id": "br", "type": "brace", "spans": ["x", "y"]},
            {"id": "h1", "type": "highlight", "target": "x"},
        ],
        "steps": [["g1", "x"], ["g1", "x", "y", "br"], ["g1", "x", "y", "br", "h1"]],
    }
    compiled = compile_spec(spec)
    assert compiled.step_count == 3
    # the .highlight-ring CSS rule is static in every page's <style> block;
    # what must vary by step is an actual rendered div instance.
    assert 'class="highlight-ring' not in compiled.html_by_step[0]
    assert 'class="highlight-ring' in compiled.html_by_step[2]


def test_overlapping_boxes_are_pushed_apart():
    """Two boxes placed at absolute coordinates that overlap must not overlap
    after compile_spec runs — this is the ADR-2211 collision-resolution pass."""
    spec = {
        "elements": [
            {"id": "a", "type": "box", "label": "A", "at": [100, 100]},
            {"id": "b", "type": "box", "label": "B", "at": [150, 120]},  # overlaps "a"
        ]
    }
    normalized = normalize_spec(spec)
    boxes = _resolve_positions(normalized["elements"])
    assert _rects_overlap(boxes["a"], boxes["b"], 24.0) > 0, "fixture must start overlapping"

    warnings = _resolve_collisions(boxes)

    assert warnings, "collision must be reported"
    assert _rects_overlap(boxes["a"], boxes["b"], 24.0) == 0.0


def test_non_overlapping_boxes_produce_no_warnings_and_are_not_moved():
    spec = {
        "elements": [
            {"id": "a", "type": "box", "label": "A", "at": [100, 100]},
            {"id": "b", "type": "box", "label": "B", "right_of": "a", "gap": 80},
        ]
    }
    normalized = normalize_spec(spec)
    boxes = _resolve_positions(normalized["elements"])
    original = {bid: (b.x, b.y) for bid, b in boxes.items()}

    warnings = _resolve_collisions(boxes)

    assert warnings == []
    assert {bid: (b.x, b.y) for bid, b in boxes.items()} == original


def test_compile_spec_exposes_layout_warnings_for_overlapping_spec():
    spec = {
        "elements": [
            {"id": "a", "type": "box", "label": "A", "at": [100, 100]},
            {"id": "b", "type": "box", "label": "B", "at": [150, 120]},
        ]
    }
    compiled = compile_spec(spec)
    assert any("a" in w and "b" in w for w in compiled.layout_warnings)


def test_compile_spec_clean_layout_has_no_warnings():
    spec = {"elements": [BOX_X]}
    compiled = compile_spec(spec)
    assert compiled.layout_warnings == []
