"""Compiles a declarative diagram spec (YAML/dict) into a self-contained HTML
string, rendered later by Playwright into PNG frame(s).

Spec shape (deliberately close to the never-built `paint.py` concept
documented in assistant_didactic_slide_video/SKILL.md):

    theme: corvin-dark
    canvas: {w: 1920, h: 1080}
    elements:
      - id: maestro
        type: box
        label: "os.video_producer"
        at: [760, 120]          # absolute position (px), OR:
        # below: other_id / right_of: other_id / near: other_id
        # gap: 120               # px, used with below/right_of/near
        icon: cpu                 # optional, see icons.py
        variant: accent           # optional: accent | surface (default)
      - id: arrow1
        type: arrow
        from: maestro.s            # "<id>.<anchor>", anchor in n/e/s/w/c
        to: worker1.n
        label: "Phase 2"           # optional
      - id: grid1
        type: grid
        at: [0, 0]
        size: [1920, 1080]
        spacing: 60                 # px between lines
      - id: group1
        type: brace
        spans: [worker1, worker2]   # bounding box spans these element ids
        side: bottom                # top | bottom | left | right
        label: "Phase 4 workers"
      - id: hl1
        type: highlight
        target: maestro             # id of the box to draw attention to
        style: ring                 # ring (dashed outline) | glow (soft shadow)
    steps:                         # optional; omit for a static diagram
      - [maestro]
      - [maestro, worker1, arrow1]

Each step is a cumulative set of *visible* element ids — frame k shows
exactly `steps[k]`. This mirrors the "Frame k shows steps[0..k]" rule from
the same SKILL.md so specs stay interchangeable between the two formats.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from html import escape

from .icons import ICONS, icon_svg
from .themes import THEMES, get_theme

BOX_W_DEFAULT = 300
BOX_H_DEFAULT = 110
CHAR_W_PX = 11  # rough label-width heuristic for auto-sizing


class SpecError(ValueError):
    """The diagram spec is not buildable — fix the YAML."""


@dataclass
class ResolvedBox:
    id: str
    x: float
    y: float
    w: float
    h: float
    label: str
    icon: str | None
    variant: str

    def anchor(self, side: str) -> tuple[float, float]:
        cx, cy = self.x + self.w / 2, self.y + self.h / 2
        return {
            "n": (cx, self.y),
            "s": (cx, self.y + self.h),
            "e": (self.x + self.w, cy),
            "w": (self.x, cy),
            "c": (cx, cy),
        }[side]


@dataclass
class Compiled:
    html_by_step: list[str] = field(default_factory=list)
    step_count: int = 1
    layout_warnings: list[str] = field(default_factory=list)


def _box_size(label: str) -> tuple[float, float]:
    w = max(BOX_W_DEFAULT, len(label) * CHAR_W_PX + 80)
    return w, BOX_H_DEFAULT


def _resolve_positions(elements: list[dict]) -> dict[str, ResolvedBox]:
    resolved: dict[str, ResolvedBox] = {}
    for el in elements:
        if el.get("type") != "box":
            continue
        eid = el.get("id")
        if not eid:
            raise SpecError(f"box element missing 'id': {el!r}")
        label = str(el.get("label", eid))
        w, h = _box_size(label)

        if "at" in el:
            x, y = el["at"]
        elif "below" in el or "right_of" in el or "near" in el:
            ref_key = "below" if "below" in el else ("right_of" if "right_of" in el else "near")
            ref_id = el[ref_key]
            if ref_id not in resolved:
                raise SpecError(
                    f"element {eid!r} references {ref_key}={ref_id!r}, "
                    f"but {ref_id!r} is not yet resolved (declare it earlier in 'elements')"
                )
            ref = resolved[ref_id]
            gap = float(el.get("gap", 80))
            if ref_key == "below":
                x, y = ref.x, ref.y + ref.h + gap
            elif ref_key == "right_of":
                x, y = ref.x + ref.w + gap, ref.y
            else:  # near: default to below-right
                x, y = ref.x + ref.w + gap, ref.y + ref.h + gap
        else:
            raise SpecError(f"box {eid!r} has no position (need 'at', 'below', 'right_of', or 'near')")

        resolved[eid] = ResolvedBox(
            id=eid, x=float(x), y=float(y), w=w, h=h,
            label=label, icon=el.get("icon"), variant=el.get("variant", "surface"),
        )
    return resolved


def _rects_overlap(a: ResolvedBox, b: ResolvedBox, margin: float) -> float:
    """Return the overlap depth (>0 means overlapping by that many px on the
    smaller axis), or 0 if the two boxes (expanded by `margin` on each side)
    don't intersect at all."""
    overlap_x = min(a.x + a.w, b.x + b.w) - max(a.x, b.x) + margin
    overlap_y = min(a.y + a.h, b.y + b.h) - max(a.y, b.y) + margin
    if overlap_x <= 0 or overlap_y <= 0:
        return 0.0
    return min(overlap_x, overlap_y)


def _resolve_collisions(
    boxes: dict[str, ResolvedBox], min_gap: float = 24.0, max_iterations: int = 64
) -> list[str]:
    """Push overlapping boxes apart along their axis of least overlap.

    Diagram specs place boxes via absolute coordinates or relative to an
    already-resolved sibling (`below`/`right_of`/`near` + `gap`); neither path
    checks against boxes placed earlier, so two independently-positioned
    elements (e.g. two `at:` boxes, or a `near:` box sized wider than its
    `gap` accounts for) can end up overlapping with no warning before this.
    This is a physics-style separation pass: each iteration finds the worst
    overlap and splits it 50/50 between the two boxes, repeating until no
    pair overlaps by more than `min_gap` or `max_iterations` is hit (bounded
    so a pathological spec can't hang the compile).

    Returns human-readable warnings (one per resolved pair) for the caller to
    surface — collisions are fixed automatically, not fatal, but a diagram
    that needed fixing is worth knowing about.
    """
    ids = list(boxes.keys())
    warnings: list[str] = []
    for _ in range(max_iterations):
        worst: tuple[float, str, str] | None = None
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = boxes[ids[i]], boxes[ids[j]]
                depth = _rects_overlap(a, b, min_gap)
                if depth > 0 and (worst is None or depth > worst[0]):
                    worst = (depth, ids[i], ids[j])
        if worst is None:
            break
        depth, id_a, id_b = worst
        a, b = boxes[id_a], boxes[id_b]
        overlap_x = min(a.x + a.w, b.x + b.w) - max(a.x, b.x) + min_gap
        overlap_y = min(a.y + a.h, b.y + b.h) - max(a.y, b.y) + min_gap
        push = depth / 2
        if overlap_x < overlap_y:
            if a.x <= b.x:
                a.x -= push
                b.x += push
            else:
                a.x += push
                b.x -= push
        else:
            if a.y <= b.y:
                a.y -= push
                b.y += push
            else:
                a.y += push
                b.y -= push
        warnings.append(f"diagram layout: resolved overlap between {id_a!r} and {id_b!r}")
    return warnings


def _parse_anchor_ref(ref: str) -> tuple[str, str]:
    if "." not in ref:
        raise SpecError(f"arrow endpoint {ref!r} must be '<id>.<anchor>' (anchor in n/e/s/w/c)")
    eid, anchor = ref.rsplit(".", 1)
    if anchor not in ("n", "e", "s", "w", "c"):
        raise SpecError(f"arrow endpoint {ref!r} has invalid anchor {anchor!r} (use n/e/s/w/c)")
    return eid, anchor


def _render_boxes_html(boxes: dict[str, ResolvedBox], theme: dict[str, str], visible: set[str]) -> str:
    out = []
    for b in boxes.values():
        if b.id not in visible:
            continue
        bg = theme["accent"] if b.variant == "accent" else theme["surface"]
        fg = theme["bg"] if b.variant == "accent" else theme["text"]
        border = theme["accent"] if b.variant == "accent" else theme["surface_border"]
        icon_html = ""
        if b.icon:
            icon_color = theme["bg"] if b.variant == "accent" else theme["accent"]
            icon_html = f'<div class="icon">{icon_svg(b.icon, size=30, color=icon_color)}</div>'
        out.append(
            f'<div class="box fade-in" style="left:{b.x}px;top:{b.y}px;width:{b.w}px;height:{b.h}px;'
            f'background:{bg};color:{fg};border-color:{border};">'
            f'{icon_html}<div class="label">{escape(b.label)}</div></div>'
        )
    return "\n".join(out)


def _render_grids_svg(grids: list[dict], theme: dict[str, str], visible: set[str]) -> str:
    out = []
    for g in grids:
        if g["id"] not in visible:
            continue
        x0, y0 = g.get("at", [0, 0])
        w, h = g.get("size", [1920, 1080])
        spacing = float(g.get("spacing", 60))
        opacity = g.get("opacity", 0.12)
        color = theme["surface_border"]
        lines = []
        x = x0
        while x <= x0 + w:
            lines.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y0 + h}"/>')
            x += spacing
        y = y0
        while y <= y0 + h:
            lines.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + w}" y2="{y}"/>')
            y += spacing
        out.append(
            f'<g class="fade-in" stroke="{color}" stroke-width="1" opacity="{opacity}">'
            + "".join(lines) + "</g>"
        )
    return "\n".join(out)


def _render_braces_svg(
    braces: list[dict], boxes: dict[str, ResolvedBox], theme: dict[str, str], visible: set[str]
) -> str:
    out = []
    labels = []
    for br in braces:
        if br["id"] not in visible:
            continue
        span_ids = br.get("spans")
        if not span_ids:
            raise SpecError(f"brace {br['id']!r} needs 'spans' (a list of element ids)")
        unknown = [sid for sid in span_ids if sid not in boxes]
        if unknown:
            raise SpecError(f"brace {br['id']!r} references unknown box ids: {unknown}")
        spanned = [boxes[sid] for sid in span_ids]
        x1 = min(b.x for b in spanned)
        y1 = min(b.y for b in spanned)
        x2 = max(b.x + b.w for b in spanned)
        y2 = max(b.y + b.h for b in spanned)
        side = br.get("side", "bottom")
        depth = float(br.get("depth", 26))
        color = theme["accent"]

        if side in ("bottom", "top"):
            y = y2 + 14 if side == "bottom" else y1 - 14
            dy = depth if side == "bottom" else -depth
            mid = (x1 + x2) / 2
            path = (
                f"M{x1},{y} C{x1 + (mid - x1) * 0.5},{y} {mid - 18},{y + dy * 0.6} {mid},{y + dy} "
                f"C{mid + 18},{y + dy * 0.6} {x2 - (x2 - mid) * 0.5},{y} {x2},{y}"
            )
            label_x, label_y = mid, y + dy + (22 if side == "bottom" else -14)
        else:  # left / right
            x = x2 + 14 if side == "right" else x1 - 14
            dx = depth if side == "right" else -depth
            mid = (y1 + y2) / 2
            path = (
                f"M{x},{y1} C{x},{y1 + (mid - y1) * 0.5} {x + dx * 0.6},{mid - 18} {x + dx},{mid} "
                f"C{x + dx * 0.6},{mid + 18} {x},{y2 - (y2 - mid) * 0.5} {x},{y2}"
            )
            label_x, label_y = x + dx + (14 if side == "right" else -14), mid

        out.append(f'<path d="{path}" stroke="{color}" stroke-width="2.5" fill="none" class="fade-in"/>')
        if br.get("label"):
            anchor = "middle" if side in ("bottom", "top") else ("start" if side == "right" else "end")
            labels.append(
                f'<text x="{label_x}" y="{label_y}" fill="{color}" font-size="18" font-weight="700" '
                f'text-anchor="{anchor}" class="fade-in">{escape(br["label"])}</text>'
            )
    return "\n".join(out) + "\n".join(labels)


def _render_highlights_html(
    highlights: list[dict], boxes: dict[str, ResolvedBox], theme: dict[str, str], visible: set[str]
) -> str:
    out = []
    for hl in highlights:
        if hl["id"] not in visible:
            continue
        target_id = hl.get("target")
        if target_id not in boxes:
            raise SpecError(f"highlight {hl['id']!r} references unknown box {target_id!r}")
        b = boxes[target_id]
        style = hl.get("style", "ring")
        pad = 10
        color = hl.get("color", theme["accent"])
        if style == "ring":
            out.append(
                f'<div class="highlight-ring fade-in" style="left:{b.x - pad}px;top:{b.y - pad}px;'
                f'width:{b.w + pad * 2}px;height:{b.h + pad * 2}px;border-color:{color};"></div>'
            )
        elif style == "glow":
            out.append(
                f'<div class="highlight-glow fade-in" style="left:{b.x - pad}px;top:{b.y - pad}px;'
                f'width:{b.w + pad * 2}px;height:{b.h + pad * 2}px;'
                f'box-shadow:0 0 36px 6px {color};border-radius:20px;"></div>'
            )
        else:
            raise SpecError(f"highlight {hl['id']!r} has unknown style {style!r} (use 'ring' or 'glow')")
    return "\n".join(out)


def _render_arrows_svg(
    arrows: list[dict], boxes: dict[str, ResolvedBox], theme: dict[str, str], visible: set[str]
) -> str:
    paths = []
    labels = []
    for a in arrows:
        if a["id"] not in visible:
            continue
        from_id, from_anchor = _parse_anchor_ref(a["from"])
        to_id, to_anchor = _parse_anchor_ref(a["to"])
        for ref_id in (from_id, to_id):
            if ref_id not in boxes:
                raise SpecError(f"arrow {a['id']!r} references unknown box {ref_id!r}")
        x1, y1 = boxes[from_id].anchor(from_anchor)
        x2, y2 = boxes[to_id].anchor(to_anchor)
        color = theme["teal"]
        paths.append(
            f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{color}" stroke-width="3" '
            f'fill="none" marker-end="url(#arrowhead)" class="fade-in"/>'
        )
        if a.get("label"):
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - 10
            labels.append(
                f'<text x="{mx}" y="{my}" fill="{theme["text_muted"]}" font-size="20" '
                f'text-anchor="middle" class="fade-in">{escape(a["label"])}</text>'
            )
    marker = (
        f'<defs><marker id="arrowhead" markerWidth="10" markerHeight="10" refX="8" refY="3" '
        f'orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="{theme["teal"]}"/></marker></defs>'
    )
    return marker + "\n".join(paths) + "\n".join(labels)


def _page_shell(theme: dict[str, str], canvas_w: int, canvas_h: int, body: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: {canvas_w}px; height: {canvas_h}px; background: {theme["bg"]}; overflow: hidden; }}
  .canvas {{ position: relative; width: {canvas_w}px; height: {canvas_h}px; font-family: {theme["font"]}; }}
  .box {{
    position: absolute; border-radius: 14px; border: 2px solid;
    display: flex; align-items: center; gap: 14px; padding: 0 22px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.35);
  }}
  .box .label {{ font-size: 22px; font-weight: 700; line-height: 1.25; }}
  .box .icon {{ flex-shrink: 0; display: flex; }}
  svg.overlay {{ position: absolute; left: 0; top: 0; width: {canvas_w}px; height: {canvas_h}px; }}
  .fade-in {{ opacity: 1; }}
  .highlight-ring {{ position: absolute; border: 3px dashed; border-radius: 20px; pointer-events: none; }}
  .highlight-glow {{ position: absolute; pointer-events: none; }}
</style></head>
<body><div class="canvas">
{body}
</div></body></html>"""


# ── Input validation ─────────────────────────────────────────────────────────
#
# A spec is UNTRUSTED (it may come from an LLM-written storyboard or an uploaded
# YAML) and its values end up inside HTML attributes that Chromium renders. So
# every field is checked HERE, once, before any HTML exists: ids against a
# charset, numbers to finite floats in a range, colours against #RRGGBB, enums
# against their sets. Downstream code only ever interpolates normalised values.

ELEMENT_TYPES = frozenset({"box", "arrow", "grid", "brace", "highlight"})
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_ELEMENTS = 400
MAX_STEPS = 200
MAX_LABEL = 200
MAX_GRID_LINES = 2000
CANVAS_W = (64, 3840)
CANVAS_H = (64, 2160)


def _num(value, lo: float, hi: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(f"{what} must be a number, got {type(value).__name__}")
    f = float(value)
    if not math.isfinite(f) or not lo <= f <= hi:
        raise SpecError(f"{what} must be between {lo:g} and {hi:g}, got {value!r}")
    return f


def _pair(value, lo: float, hi: float, what: str) -> list:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SpecError(f"{what} must be a list of two numbers")
    return [_num(value[0], lo, hi, what), _num(value[1], lo, hi, what)]


def _text(value, what: str) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{what} must be a string")
    if len(value) > MAX_LABEL:
        raise SpecError(f"{what} is longer than {MAX_LABEL} characters")
    return value


def _choice(value, allowed, what: str) -> str:
    if value not in allowed:
        raise SpecError(f"{what} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _ref(value, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise SpecError(f"{what} must be an element id ([A-Za-z0-9_-], 1-64 chars)")
    return value


def normalize_spec(spec) -> dict:
    """Validate an untrusted spec and return a normalised copy (raises SpecError)."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be a mapping")
    theme = spec.get("theme", "corvin-dark")
    _choice(theme, THEMES, "theme")
    canvas = spec.get("canvas", {"w": 1920, "h": 1080})
    if not isinstance(canvas, dict) or "w" not in canvas or "h" not in canvas:
        raise SpecError("canvas must be a mapping with 'w' and 'h'")
    cw, ch = canvas["w"], canvas["h"]
    for v, (lo, hi), name in ((cw, CANVAS_W, "canvas.w"), (ch, CANVAS_H, "canvas.h")):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise SpecError(f"{name} must be an integer between {lo} and {hi}")
    span = float(max(cw, ch))

    elements = spec.get("elements")
    if not isinstance(elements, list) or not elements:
        raise SpecError("spec has no 'elements' list")
    if len(elements) > MAX_ELEMENTS:
        raise SpecError(f"more than {MAX_ELEMENTS} elements")

    out_elements: list = []
    seen: set = set()
    for el in elements:
        if not isinstance(el, dict):
            raise SpecError("every element must be a mapping")
        eid = _ref(el.get("id"), "element id")
        if eid in seen:
            raise SpecError(f"duplicate element id {eid!r}")
        seen.add(eid)
        etype = _choice(el.get("type"), ELEMENT_TYPES, f"type of {eid!r}")
        n = {"id": eid, "type": etype}
        if "label" in el:
            n["label"] = _text(el["label"], f"label of {eid!r}")

        if etype == "box":
            if "at" in el:
                n["at"] = _pair(el["at"], -span, 2 * span, f"at of {eid!r}")
            else:
                keys = [k for k in ("below", "right_of", "near") if k in el]
                if len(keys) != 1:
                    raise SpecError(f"box {eid!r} needs exactly one of 'at', 'below', 'right_of', 'near'")
                n[keys[0]] = _ref(el[keys[0]], f"{keys[0]} of {eid!r}")
                n["gap"] = _num(el.get("gap", 80), 0, 2 * span, f"gap of {eid!r}")
            if "icon" in el:
                n["icon"] = _choice(el["icon"], ICONS, f"icon of {eid!r}")
            n["variant"] = _choice(el.get("variant", "surface"), {"surface", "accent"}, f"variant of {eid!r}")
        elif etype == "arrow":
            for end in ("from", "to"):
                ref = el.get(end)
                if not isinstance(ref, str) or "." not in ref:
                    raise SpecError(f"arrow {eid!r} needs '{end}: <id>.<anchor>'")
                rid, anchor = ref.rsplit(".", 1)
                _ref(rid, f"{end} of {eid!r}")
                _choice(anchor, {"n", "e", "s", "w", "c"}, f"anchor of {eid!r}.{end}")
                n[end] = ref
        elif etype == "grid":
            n["at"] = _pair(el.get("at", [0, 0]), -span, 2 * span, f"at of {eid!r}")
            n["size"] = _pair(el.get("size", [cw, ch]), 1, 2 * span, f"size of {eid!r}")
            n["spacing"] = _num(el.get("spacing", 60), 8, span, f"spacing of {eid!r}")
            n["opacity"] = _num(el.get("opacity", 0.12), 0, 1, f"opacity of {eid!r}")
            lines = (n["size"][0] / n["spacing"]) + (n["size"][1] / n["spacing"])
            if lines > MAX_GRID_LINES:
                raise SpecError(f"grid {eid!r} would draw {lines:.0f} lines (max {MAX_GRID_LINES})")
        elif etype == "brace":
            spans = el.get("spans")
            if not isinstance(spans, list) or not spans:
                raise SpecError(f"brace {eid!r} needs 'spans' (a list of element ids)")
            n["spans"] = [_ref(x, f"spans of {eid!r}") for x in spans]
            n["side"] = _choice(el.get("side", "bottom"), {"top", "bottom", "left", "right"}, f"side of {eid!r}")
            n["depth"] = _num(el.get("depth", 26), 4, 200, f"depth of {eid!r}")
        else:  # highlight
            n["target"] = _ref(el.get("target"), f"target of {eid!r}")
            n["style"] = _choice(el.get("style", "ring"), {"ring", "glow"}, f"style of {eid!r}")
            if "color" in el:
                c = el["color"]
                if not isinstance(c, str) or not _HEX_RE.match(c):
                    raise SpecError(f"color of {eid!r} must be #RRGGBB")
                n["color"] = c
        out_elements.append(n)

    box_ids = {e["id"] for e in out_elements if e["type"] == "box"}
    for e in out_elements:  # references are checked for EVERY element, visible or not
        refs = []
        if e["type"] == "arrow":
            refs = [e["from"].rsplit(".", 1)[0], e["to"].rsplit(".", 1)[0]]
        elif e["type"] == "brace":
            refs = e["spans"]
        elif e["type"] == "highlight":
            refs = [e["target"]]
        unknown = [r for r in refs if r not in box_ids]
        if unknown:
            raise SpecError(f"{e['type']} {e['id']!r} references unknown box ids: {unknown}")

    steps = spec.get("steps")
    if steps is not None:
        if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
            raise SpecError(f"steps must be a non-empty list of at most {MAX_STEPS} lists")
        for st in steps:
            if not isinstance(st, list) or not all(isinstance(x, str) for x in st):
                raise SpecError("every step must be a list of element ids")
            unknown = sorted(set(st) - seen)
            if unknown:
                raise SpecError(f"steps reference unknown element ids: {unknown}")

    return {"theme": theme, "canvas": {"w": cw, "h": ch}, "elements": out_elements, "steps": steps}


def compile_spec(spec: dict) -> Compiled:
    spec = normalize_spec(spec)
    theme = get_theme(spec["theme"])
    canvas = spec["canvas"]
    elements = spec["elements"]

    boxes = _resolve_positions(elements)
    layout_warnings = _resolve_collisions(boxes)
    arrows = [el for el in elements if el.get("type") == "arrow"]
    grids = [el for el in elements if el.get("type") == "grid"]
    braces = [el for el in elements if el.get("type") == "brace"]
    highlights = [el for el in elements if el.get("type") == "highlight"]
    for group, kind in ((arrows, "arrow"), (grids, "grid"), (braces, "brace"), (highlights, "highlight")):
        for el in group:
            if "id" not in el:
                raise SpecError(f"{kind} element missing 'id': {el!r}")

    all_ids = (
        list(boxes) + [a["id"] for a in arrows] + [g["id"] for g in grids]
        + [b["id"] for b in braces] + [h["id"] for h in highlights]
    )
    steps = spec.get("steps") or [all_ids]  # no steps -> one static frame showing everything

    html_by_step = []
    for step_ids in steps:
        unknown = set(step_ids) - set(all_ids)
        if unknown:
            raise SpecError(f"steps reference unknown element ids: {sorted(unknown)}")
        visible = set(step_ids)
        grid_svg = _render_grids_svg(grids, theme, visible)
        boxes_html = _render_boxes_html(boxes, theme, visible)
        highlights_html = _render_highlights_html(highlights, boxes, theme, visible)
        arrows_svg = _render_arrows_svg(arrows, boxes, theme, visible)
        braces_svg = _render_braces_svg(braces, boxes, theme, visible)
        body = (
            f'<svg class="overlay">{grid_svg}</svg>'
            + boxes_html + highlights_html
            + f'<svg class="overlay">{arrows_svg}{braces_svg}</svg>'
        )
        html_by_step.append(_page_shell(theme, canvas["w"], canvas["h"], body))

    return Compiled(html_by_step=html_by_step, step_count=len(html_by_step), layout_warnings=layout_warnings)
