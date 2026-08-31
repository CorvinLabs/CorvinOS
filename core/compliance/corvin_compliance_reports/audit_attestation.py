"""Audit-Chain Integrity Attestation — third baseline report.

A short, dense PDF that runs ``forge.security_events.verify_chain``
and prints:

  - Pass/fail verdict
  - First + last event hashes (the chain anchors)
  - Total event count + severity histogram
  - Event-type histogram (top N)
  - Operator signature line for Wirtschaftsprüfer stamping

Apache-2.0. Auditors / Wirtschaftsprüfer print + stamp this report
as the cryptographic anchor of every other compliance report.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer

from . import __version__, audit_query, templates


def _intro_paragraphs() -> list[str]:
    return [
        "This attestation certifies the integrity of the Corvin "
        "<b>hash-chained audit log</b> for the named tenant and time "
        "window. The verification runs the canonical "
        "<font face=\"Courier\">forge.security_events.verify_chain</font> "
        "algorithm — same code used by the daily <font face=\"Courier\">"
        "voice-audit verify</font> systemd timer.",
        "Any line in the audit log that has been altered, removed or "
        "inserted after-the-fact would break the SHA-256 hash chain "
        "and surface as a problem in the appendix.",
        "<i>This document is the cryptographic anchor of every other "
        "Corvin compliance report covering the same period.</i>",
    ]


def generate(
    *,
    tenant_id: str,
    start_ts: int,
    end_ts: int,
    output_path: Path,
    chain_path: Path | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    meta = templates.ReportMetadata(
        title="Audit-Chain Integrity Attestation",
        tenant_id=tenant_id,
        period_start_ts=start_ts,
        period_end_ts=end_ts,
        generator_version=__version__,
        generated_at_ts=now,
    )

    stats = audit_query.compute_stats(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        chain_path=chain_path,
    )

    doc, styles = templates.build_doc(output_path, meta)
    story: list[Any] = []
    story.extend(templates.cover_page(
        meta, intro_paragraphs=_intro_paragraphs(), styles=styles,
    ))

    # Banner result up front — most important info first.
    story.extend(templates.integrity_banner(
        intact=stats.chain_intact,
        problems=stats.chain_problems,
        styles=styles,
    ))
    story.append(Spacer(1, 8 * mm))

    # ── Anchor block (first/last hash)
    story.append(templates.section_heading("Chain anchors", styles))
    anchor_rows = [
        ["Position", "Timestamp (UTC)", "SHA-256 hash"],
        [
            "First event",
            templates._fmt_ts(stats.first_event_ts),
            stats.first_event_hash or "—",
        ],
        [
            "Last event",
            templates._fmt_ts(stats.last_event_ts),
            stats.last_event_hash or "—",
        ],
    ]
    story.append(templates.styled_table(
        anchor_rows,
        col_widths=[28 * mm, 50 * mm, 102 * mm],
    ))
    story.append(Spacer(1, 6 * mm))

    # ── Severity histogram
    story.append(templates.section_heading(
        "Event distribution — by severity", styles,
    ))
    sev_rows = [["Severity", "Count"]]
    for sev, count in sorted(
        stats.by_severity.items(), key=lambda kv: -kv[1],
    ):
        sev_rows.append([sev, str(count)])
    story.append(templates.styled_table(
        sev_rows, col_widths=[120 * mm, 60 * mm],
    ))
    story.append(Spacer(1, 6 * mm))

    # ── Top event types
    story.append(templates.section_heading(
        "Event distribution — top 30 event types", styles,
    ))
    type_rows = [["Event type", "Count"]]
    for et, count in sorted(
        stats.by_event_type.items(), key=lambda kv: -kv[1],
    )[:30]:
        type_rows.append([et, str(count)])
    if len(type_rows) > 1:
        story.append(templates.styled_table(
            type_rows, col_widths=[120 * mm, 60 * mm],
        ))
    else:
        story.append(Paragraph(
            "<i>No events recorded in the selected period.</i>",
            styles["body"],
        ))
    story.append(Spacer(1, 6 * mm))

    # ── Operator signature line (auditor's stamp)
    story.append(templates.section_heading(
        "Auditor signature", styles,
    ))
    story.append(Paragraph(
        "Signed by:",
        styles["small"],
    ))
    story.append(Spacer(1, 16 * mm))
    sig_rows = [
        ["Name", "Role / Function", "Signature & Date"],
        ["", "", ""],
    ]
    story.append(templates.styled_table(
        sig_rows,
        col_widths=[60 * mm, 50 * mm, 70 * mm],
    ))

    # ── Hash anchor footer
    story.extend(templates.signed_footer_block(
        last_hash=stats.last_event_hash,
        generator_version=__version__,
        styles=styles,
    ))

    doc.build(story)

    return {
        "report_type": "audit_chain_attestation",
        "output_path": str(output_path),
        "chain_intact": stats.chain_intact,
        "chain_problems_count": len(stats.chain_problems),
        "total_events": stats.total_events,
        "first_event_ts": stats.first_event_ts,
        "last_event_ts": stats.last_event_ts,
        "anchor_hash": stats.last_event_hash,
        "tenant_id": tenant_id,
        "period_start_ts": start_ts,
        "period_end_ts": end_ts,
        "generated_at_ts": now,
        "generator_version": __version__,
    }


__all__ = ["generate"]
