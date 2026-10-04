"""Frame templates for Video Producer (CONCEPT-0093 Phase 1).

Three composition patterns, replacing the old bare "text on solid color"
frame with icon + hierarchy + whitespace per the design principles in
CONCEPT-0093 (rule of thirds, 20% minimum whitespace, one accent per frame).

All three render onto a 1920x1080 canvas and return the output path they
were given — same contract create_title_frame() had, so existing render
scripts can swap one call for another without restructuring.
"""

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .icon_library import render_icon

BOLD_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REGULAR_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _fit_font(draw, text, max_width, font_path, start_size, min_size=32):
    size = start_size
    while size > min_size:
        try:
            font = ImageFont.truetype(font_path, size)
        except Exception:
            return ImageFont.load_default(), min_size
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return font, size
        size -= 4
    return ImageFont.truetype(font_path, min_size), min_size


def _paste_icon(img: Image.Image, icon_path: str, center_xy: tuple[int, int], size: int):
    icon = Image.open(icon_path).convert("RGBA").resize((size, size))
    x, y = center_xy[0] - size // 2, center_xy[1] - size // 2
    img.paste(icon, (x, y), icon)


def render_hero_frame(
    title: str,
    tagline: str,
    icon_name: Optional[str],
    output_path: str,
    bg_color=(11, 14, 20),
    primary_color=(59, 130, 246),
    text_color=(255, 255, 255),
    width=1920, height=1080,
) -> str:
    """Hero template: large icon above centered title + tagline below.

    Rule of thirds: icon center sits at ~38% height, title at ~55%, tagline
    at ~65% — not dead center, and with real vertical separation instead of
    stacking everything around one y-coordinate.
    """
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    max_w = int(width * 0.8)

    if icon_name:
        icon_path = render_icon(icon_name, size=320, color="#%02x%02x%02x" % primary_color)
        _paste_icon(img, icon_path, (width // 2, int(height * 0.36)), 220)

    title_font, _ = _fit_font(draw, title, max_w, BOLD_FONT, 96)
    bbox = draw.textbbox((0, 0), title, font=title_font)
    tx = (width - (bbox[2] - bbox[0])) // 2
    ty = int(height * 0.55)
    draw.text((tx, ty), title, fill=text_color, font=title_font)

    if tagline:
        tag_font, _ = _fit_font(draw, tagline, max_w, REGULAR_FONT, 44)
        bbox2 = draw.textbbox((0, 0), tagline, font=tag_font)
        tx2 = (width - (bbox2[2] - bbox2[0])) // 2
        ty2 = int(height * 0.68)
        draw.text((tx2, ty2), tagline, fill=primary_color, font=tag_font)

    img.save(output_path)
    return output_path


def render_content_frame(
    heading: str,
    body_lines: list[str],
    icon_name: Optional[str],
    output_path: str,
    bg_color=(11, 14, 20),
    primary_color=(59, 130, 246),
    text_color=(255, 255, 255),
    accent_color=(249, 115, 22),
    width=1920, height=1080,
) -> str:
    """Content template: icon in left third, heading + bullet lines in the
    right two thirds, with a vertical accent bar as the visual anchor
    between them (the 12-column grid CONCEPT-0093 calls for, simplified to
    a 1/3 + 2/3 split since PIL has no real grid layout engine)."""
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    left_col_center = width // 6
    accent_x = width // 3
    content_x = accent_x + 80
    max_w = width - content_x - 120

    if icon_name:
        icon_path = render_icon(icon_name, size=280, color="#%02x%02x%02x" % primary_color)
        _paste_icon(img, icon_path, (left_col_center, height // 2), 200)

    draw.rectangle([accent_x, height // 6, accent_x + 6, height - height // 6], fill=accent_color)

    heading_font, _ = _fit_font(draw, heading, max_w, BOLD_FONT, 72)
    hy = int(height * 0.28)
    draw.text((content_x, hy), heading, fill=text_color, font=heading_font)

    body_font = ImageFont.truetype(REGULAR_FONT, 40)
    by = hy + 110
    for line in body_lines:
        draw.text((content_x, by), f"•  {line}", fill=primary_color, font=body_font)
        by += 62

    img.save(output_path)
    return output_path


def render_emphasis_frame(
    stat: str,
    context: str,
    output_path: str,
    bg_color=(11, 14, 20),
    primary_color=(249, 115, 22),
    text_color=(255, 255, 255),
    width=1920, height=1080,
) -> str:
    """Emphasis template: one large stat (number/short phrase) with a small
    context line below — for highlighting a single measured fact, not a
    list (a bullet list fighting a hero number for attention is exactly
    the "dilution" CONCEPT-0093 warns against)."""
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    max_w = int(width * 0.85)

    stat_font, _ = _fit_font(draw, stat, max_w, BOLD_FONT, 160, min_size=60)
    bbox = draw.textbbox((0, 0), stat, font=stat_font)
    sx = (width - (bbox[2] - bbox[0])) // 2
    sy = (height - (bbox[3] - bbox[1])) // 2 - 40
    draw.text((sx, sy), stat, fill=primary_color, font=stat_font)

    if context:
        ctx_font, _ = _fit_font(draw, context, max_w, REGULAR_FONT, 48)
        bbox2 = draw.textbbox((0, 0), context, font=ctx_font)
        cx = (width - (bbox2[2] - bbox2[0])) // 2
        cy = sy + (bbox[3] - bbox[1]) + 60
        draw.text((cx, cy), context, fill=text_color, font=ctx_font)

    img.save(output_path)
    return output_path
