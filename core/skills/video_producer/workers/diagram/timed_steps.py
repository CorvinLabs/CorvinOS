"""Build a compiler.py-compatible `steps` sequence from narration timing
marks (ADR-2212). No new diagram vocabulary — this only generates the
existing `steps` list plus `highlight` elements that compile_spec() already
understands, so collision resolution and every other compiler guarantee
apply unchanged.
"""
from __future__ import annotations


def build_timed_steps(
    base_elements: list[dict], timing_marks: list[tuple[float, str]]
) -> tuple[list[dict], list[list[str]]]:
    """Add one highlight element per timing mark and a matching cumulative
    `steps` entry, so step k reveals every non-highlight element plus a ring
    highlight on timing_marks[k]'s target.

    Args:
        base_elements: the spec's existing elements (boxes/arrows/etc.),
            already carrying ids that timing_marks reference.
        timing_marks: [(timestamp_s, element_id), ...] in ascending time
            order, e.g. from narration_markers.estimate_marker_timestamps().

    Returns:
        (elements, steps) — elements is base_elements plus one highlight
        element per mark; steps is the cumulative visibility list, step 0
        showing every base element with no highlight (so the diagram isn't
        empty before the first mark fires).

    Raises nothing: an empty timing_marks returns (base_elements, [all_ids])
    — the existing one-step-per-scene behavior, so scenes without markers
    are unaffected.
    """
    base_ids = [e["id"] for e in base_elements if "id" in e]

    if not timing_marks:
        return base_elements, [base_ids]

    elements = list(base_elements)
    steps: list[list[str]] = [list(base_ids)]  # step 0: everything, no highlight

    for i, (_timestamp, target_id) in enumerate(timing_marks):
        hl_id = f"_hl_mark_{i}"
        elements.append({
            "id": hl_id,
            "type": "highlight",
            "target": target_id,
            "style": "ring",
        })
        steps.append(list(base_ids) + [hl_id])

    return elements, steps
