"""Shared reportlab primitives — header / footer / styles / tables.

Every baseline report uses the same visual frame so the artefacts
look consistent in a regulator's hands.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table,
    TableStyle, KeepTogether,
)


# ── Branding tokens — track ADR-0017 Phase IV design tokens ───────────
#
# These ARE the print-side mirror of the SPA's CSS variables.
# Future Phase IV2 will let operators override these via branding.yaml.

BRAND_PRIMARY     = colors.HexColor("#2563eb")   # mirrors --accent (light)
BRAND_TEXT        = colors.HexColor("#14161b")
BRAND_DIM         = colors.HexColor("#5b606c")
BRAND_MUTE        = colors.HexColor("#8a8f9b")
BRAND_BORDER      = colors.HexColor("#d8dbe2")
BRAND_BG_ELEV     = colors.HexColor("#eef0f4")
BRAND_OK          = colors.HexColor("#16a34a")
BRAND_WARN        = colors.HexColor("#b45309")
BRAND_DANGER      = colors.HexColor("#dc2626")


@dataclass
class ReportMetadata:
    """Stamped onto every PDF for traceability."""
    title: str
    tenant_id: str
    period_start_ts: int
    period_end_ts: int
    generator_version: str
    generated_at_ts: int


def _fmt_ts(epoch: int | None, *, with_seconds: bool = True) -> str:
    if epoch is None:
        return "—"
    dt = datetime.datetime.fromtimestamp(int(epoch), tz=datetime.timezone.utc)
    if with_seconds:
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    return dt.strftime("%Y-%m-%d")


def _fmt_date(epoch: int | None) -> str:
    return _fmt_ts(epoch, with_seconds=False)


# ── Paragraph styles ──────────────────────────────────────────────────

def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "CorvinTitle", parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=22, leading=26,
            textColor=BRAND_TEXT,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "CorvinSubtitle", parent=base["Normal"],
            fontName="Helvetica", fontSize=11, leading=14,
            textColor=BRAND_DIM, spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "CorvinH2", parent=base["Heading2"],
            fontName="Helvetica-Bold", fontSize=14, leading=18,
            textColor=BRAND_PRIMARY,
            spaceBefore=14, spaceAfter=8,
        ),
        "h3": ParagraphStyle(
            "CorvinH3", parent=base["Heading3"],
            fontName="Helvetica-Bold", fontSize=11, leading=15,
            textColor=BRAND_TEXT,
            spaceBefore=8, spaceAfter=4,
            textTransform="uppercase",
        ),
        "body": ParagraphStyle(
            "CorvinBody", parent=base["BodyText"],
            fontName="Helvetica", fontSize=10, leading=14,
            textColor=BRAND_TEXT, spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "CorvinSmall", parent=base["BodyText"],
            fontName="Helvetica", fontSize=9, leading=12,
            textColor=BRAND_DIM,
        ),
        "code": ParagraphStyle(
            "CorvinCode", parent=base["BodyText"],
            fontName="Courier", fontSize=8.5, leading=11,
            textColor=BRAND_TEXT,
        ),
    }


# ── Page templates with header + footer ───────────────────────────────

def _header_footer(meta: ReportMetadata):
    def _on_page(canvas, doc):
        canvas.saveState()
        w, h = A4
        # Header bar
        canvas.setFillColor(BRAND_PRIMARY)
        canvas.rect(0, h - 6 * mm, w, 6 * mm, stroke=0, fill=1)
        # Brand stripe
        canvas.setFillColor(BRAND_TEXT)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(15 * mm, h - 13 * mm, "Corvin  ·  Compliance Report")
        canvas.setFillColor(BRAND_DIM)
        canvas.setFont("Helvetica", 8.5)
        canvas.drawRightString(
            w - 15 * mm, h - 13 * mm,
            f"Tenant {meta.tenant_id}  ·  {meta.title}",
        )
        # Hairline under header
        canvas.setStrokeColor(BRAND_BORDER)
        canvas.setLineWidth(0.4)
        canvas.line(15 * mm, h - 15 * mm, w - 15 * mm, h - 15 * mm)
        # Footer
        canvas.setFillColor(BRAND_MUTE)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(
            15 * mm, 12 * mm,
            f"Generated {_fmt_ts(meta.generated_at_ts)}  ·  "
            f"Corvin compliance-reports v{meta.generator_version}",
        )
        canvas.drawRightString(
            w - 15 * mm, 12 * mm,
            f"Page {canvas.getPageNumber()}",
        )
        canvas.line(15 * mm, 16 * mm, w - 15 * mm, 16 * mm)
        canvas.restoreState()
    return _on_page


def build_doc(output_path: Path, meta: ReportMetadata) -> tuple[BaseDocTemplate, dict[str, ParagraphStyle]]:
    """Create a BaseDocTemplate ready to ``.build([flowables])``."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=22 * mm, bottomMargin=22 * mm,
        title=meta.title,
        author="Corvin",
        subject=f"Compliance report for tenant {meta.tenant_id}",
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        leftPadding=0, rightPadding=0,
        topPadding=0, bottomPadding=0,
        id="normal",
    )
    doc.addPageTemplates([
        PageTemplate(id="default", frames=[frame], onPage=_header_footer(meta)),
    ])
    return doc, _styles()


# ── Cover-page flowables ──────────────────────────────────────────────

def cover_page(
    meta: ReportMetadata,
    *,
    intro_paragraphs: list[str],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    """A consistent cover block for every report."""
    out: list[Any] = []
    out.append(Spacer(1, 12 * mm))
    out.append(Paragraph(meta.title, styles["title"]))
    out.append(Paragraph(
        f"Tenant <b>{meta.tenant_id}</b>  &nbsp;·&nbsp;  "
        f"Period <b>{_fmt_date(meta.period_start_ts)}</b> "
        f"to <b>{_fmt_date(meta.period_end_ts)}</b>",
        styles["subtitle"],
    ))
    out.append(Spacer(1, 6 * mm))
    for p in intro_paragraphs:
        out.append(Paragraph(p, styles["body"]))
    out.append(Spacer(1, 10 * mm))
    return out


# ── Reusable table helper ─────────────────────────────────────────────

def styled_table(
    rows: list[list[str]],
    *,
    col_widths: list[float] | None = None,
    zebra: bool = True,
) -> Table:
    """Reusable table with brand-coloured header + optional zebra rows."""
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, 0), "LEFT"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), BRAND_TEXT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, BRAND_BORDER),
        ("BOX", (0, 0), (-1, -1), 0.3, BRAND_BORDER),
    ]
    if zebra:
        for i in range(1, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), BRAND_BG_ELEV))
    t.setStyle(TableStyle(style))
    return t


def section_heading(text: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    return Paragraph(text, styles["h2"])


def subsection(text: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    return Paragraph(text, styles["h3"])


def stat_box(
    label: str, value: str, styles: dict[str, ParagraphStyle],
) -> list[Any]:
    """Two-line key/value tile used in summary sections."""
    return [
        Paragraph(label.upper(), styles["h3"]),
        Paragraph(value, styles["body"]),
    ]


def integrity_banner(
    *, intact: bool, problems: list[str], styles: dict[str, ParagraphStyle],
) -> list[Any]:
    """Visual banner for the hash-chain integrity status."""
    if intact:
        bg = BRAND_OK
        msg = "✓  Audit-chain integrity verified — no tamper detected"
    else:
        bg = BRAND_DANGER
        msg = (
            f"✗  Audit-chain integrity FAILED — {len(problems)} problem(s). "
            "Investigate before publishing this report."
        )
    t = Table(
        [[msg]],
        colWidths=[180 * mm],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    out: list[Any] = [t]
    if problems:
        out.append(Spacer(1, 4 * mm))
        for p in problems[:10]:
            out.append(Paragraph(f"&bull; {p}", styles["small"]))
    return out


def signed_footer_block(
    *, last_hash: str | None, generator_version: str,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    """Hash-anchor block at the end of every report.

    The last-event hash from the audit chain is the cryptographic
    anchor — if anyone tampers with chain history after the report
    is generated, the hash here will no longer match the chain's
    actual last-event hash.
    """
    h = last_hash or "(empty chain)"
    out: list[Any] = []
    out.append(Spacer(1, 8 * mm))
    out.append(Paragraph("Hash-chain anchor", styles["h3"]))
    out.append(Paragraph(
        "This report is anchored against the audit chain's "
        "last-event hash at generation time. Any subsequent "
        "tampering with the chain would invalidate this anchor.",
        styles["small"],
    ))
    out.append(Spacer(1, 2 * mm))
    out.append(Paragraph(
        f"<font name=\"Courier\" size=\"8\">{h}</font>",
        styles["code"],
    ))
    out.append(Spacer(1, 4 * mm))
    out.append(Paragraph(
        f"Generated by Corvin compliance-reports v{generator_version}. "
        "Apache-2.0. Open Source.",
        styles["small"],
    ))
    return out


__all__ = [
    "ReportMetadata",
    "BRAND_PRIMARY", "BRAND_TEXT", "BRAND_DIM",
    "BRAND_OK", "BRAND_WARN", "BRAND_DANGER",
    "build_doc",
    "cover_page",
    "styled_table",
    "section_heading", "subsection", "stat_box",
    "integrity_banner",
    "signed_footer_block",
]
