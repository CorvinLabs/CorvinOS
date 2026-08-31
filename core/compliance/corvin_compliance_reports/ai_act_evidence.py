"""EU AI Act Art. 50 — Active-Disclosure Evidence Report.

Art. 50 requires AI-system operators to inform users they are
interacting with an AI. This report produces a regulator-defensible
PDF showing, for a given tenant and time window:

  - Every disclosure card shown (`disclosure.shown` event)
  - Every user response (`disclosure.action` — joined/passed/left)
  - Every consent grant / revoke / expiry / share-admit / drop
  - The hash-chain anchor proving non-tampering

Apache-2.0. The Enterprise plugin adds premium variants (scheduled
generation, custom layouts, WORM archival); this baseline is
unconditionally free per ADR-0017's transparency invariant.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Spacer

from . import __version__, audit_query, templates


# Event types we surface in this report.
_DISCLOSURE_EVENTS = (
    "disclosure.shown",
    "disclosure.action",
    "disclosure.joined",
)
_CONSENT_EVENTS = (
    "consent.granted",
    "consent.revoked",
    "consent.expired",
    "consent.observer_dropped",
    "consent.share_admitted",
    "consent.consume_drift",
)
_READ_ONLY_EVENTS = (
    "bridge.read_only_drop",
)


def _intro_paragraphs() -> list[str]:
    return [
        "This report documents the operator's compliance with "
        "<b>EU AI Act 2026 Article 50</b>, which requires active "
        "disclosure when a person interacts with an AI system.",
        "The events listed below are read directly from the "
        "tamper-evident audit chain at "
        "<font face=\"Courier\">&lt;corvin_home&gt;/global/forge/audit.jsonl</font>, "
        "anchored by the hash printed on the final page.",
        "<i>The absence of an event in this listing constitutes "
        "absence of the corresponding disclosure or consent "
        "action.</i>",
    ]


def _disclosure_rows(events: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = [["Timestamp (UTC)", "Channel", "Chat", "User ID", "Action"]]
    for ev in events:
        d = ev.get("details", {}) or {}
        action = ev.get("event_type", "").replace("disclosure.", "")
        if action == "action":
            action = str(d.get("action", "action"))
        rows.append([
            templates._fmt_ts(ev.get("ts")),
            str(d.get("channel", ""))[:20],
            str(d.get("chat_key", ""))[:24],
            str(d.get("uid", ""))[:20],
            action,
        ])
    return rows


def _consent_rows(events: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = [["Timestamp (UTC)", "Channel", "Chat", "User ID", "Event", "Reason / Mode"]]
    for ev in events:
        d = ev.get("details", {}) or {}
        et = ev.get("event_type", "").replace("consent.", "")
        rows.append([
            templates._fmt_ts(ev.get("ts")),
            str(d.get("channel", ""))[:20],
            str(d.get("chat_key", ""))[:24],
            str(d.get("uid", ""))[:20],
            et,
            str(d.get("mode", "") or d.get("reason", "") or "")[:24],
        ])
    return rows


def _read_only_rows(events: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = [["Timestamp (UTC)", "Channel", "Chat", "User ID", "First drop?"]]
    for ev in events:
        d = ev.get("details", {}) or {}
        rows.append([
            templates._fmt_ts(ev.get("ts")),
            str(d.get("channel", ""))[:20],
            str(d.get("chat_key", ""))[:24],
            str(d.get("uid", ""))[:20],
            "yes" if d.get("first_drop") else "no",
        ])
    return rows


def generate(
    *,
    tenant_id: str,
    start_ts: int,
    end_ts: int,
    output_path: Path,
    chain_path: Path | None = None,
) -> dict[str, Any]:
    """Generate the report at ``output_path``. Returns metadata dict."""
    now = int(time.time())
    meta = templates.ReportMetadata(
        title="EU AI Act Art. 50 — Active-Disclosure Evidence",
        tenant_id=tenant_id,
        period_start_ts=start_ts,
        period_end_ts=end_ts,
        generator_version=__version__,
        generated_at_ts=now,
    )

    disclosure_events = audit_query.collect_events(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        event_types=_DISCLOSURE_EVENTS, chain_path=chain_path,
    )
    consent_events = audit_query.collect_events(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        event_types=_CONSENT_EVENTS, chain_path=chain_path,
    )
    read_only_events = audit_query.collect_events(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        event_types=_READ_ONLY_EVENTS, chain_path=chain_path,
    )
    stats = audit_query.compute_stats(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        chain_path=chain_path,
    )

    doc, styles = templates.build_doc(output_path, meta)
    story: list[Any] = []

    # ── Cover
    story.extend(templates.cover_page(
        meta, intro_paragraphs=_intro_paragraphs(), styles=styles,
    ))

    # ── Executive summary
    story.append(templates.section_heading("Executive summary", styles))
    summary = [
        ["Disclosure events shown", str(len(disclosure_events))],
        ["Consent events", str(len(consent_events))],
        ["Read-only senders silently dropped", str(len(read_only_events))],
        ["Total chain events in period", str(stats.total_events)],
        ["Hash-chain intact", "yes" if stats.chain_intact else "NO — see appendix"],
    ]
    story.append(templates.styled_table(
        [["Metric", "Value"]] + summary,
        col_widths=[110 * mm, 70 * mm],
    ))
    story.append(Spacer(1, 6 * mm))

    # ── Integrity banner
    story.extend(templates.integrity_banner(
        intact=stats.chain_intact, problems=stats.chain_problems, styles=styles,
    ))
    story.append(Spacer(1, 8 * mm))

    # ── Disclosure events section
    story.append(PageBreak())
    story.append(templates.section_heading(
        "Disclosure events", styles,
    ))
    story.append(Paragraph(
        "Each row records one instance of the bot-disclosure card "
        "being shown to a user, or the user's subsequent "
        "<i>join</i> / <i>pass</i> / <i>leave</i> response. Identity "
        "is bound to the bridge platform's user-ID.",
        styles["small"],
    ))
    story.append(Spacer(1, 4 * mm))
    if disclosure_events:
        story.append(templates.styled_table(
            _disclosure_rows(disclosure_events),
            col_widths=[40 * mm, 25 * mm, 35 * mm, 35 * mm, 45 * mm],
        ))
    else:
        story.append(Paragraph(
            "<i>No disclosure events recorded in the selected period.</i>",
            styles["body"],
        ))

    # ── Consent events
    story.append(PageBreak())
    story.append(templates.section_heading("Consent events", styles))
    story.append(Paragraph(
        "Consent grants, revocations, expiries, one-shot share-admits, "
        "and read-only-sender drops on disabled consent. Together "
        "they prove DSGVO Art. 6 + 7 compliance for observer-transcript "
        "processing.",
        styles["small"],
    ))
    story.append(Spacer(1, 4 * mm))
    if consent_events:
        story.append(templates.styled_table(
            _consent_rows(consent_events),
            col_widths=[40 * mm, 22 * mm, 32 * mm, 32 * mm, 28 * mm, 26 * mm],
        ))
    else:
        story.append(Paragraph(
            "<i>No consent events recorded in the selected period.</i>",
            styles["body"],
        ))

    # ── Read-only drops
    story.append(PageBreak())
    story.append(templates.section_heading(
        "Read-only sender drops (Layer 16 Phase 2)", styles,
    ))
    story.append(Paragraph(
        "Messages from senders in the <i>read_only</i> capability list "
        "are silently dropped before they reach the LLM. The first drop "
        "per (chat, user) is shown to the user as a polite ACK; "
        "subsequent drops are silent. Every drop is recorded here.",
        styles["small"],
    ))
    story.append(Spacer(1, 4 * mm))
    if read_only_events:
        story.append(templates.styled_table(
            _read_only_rows(read_only_events),
            col_widths=[45 * mm, 30 * mm, 40 * mm, 40 * mm, 25 * mm],
        ))
    else:
        story.append(Paragraph(
            "<i>No read-only drops recorded in the selected period.</i>",
            styles["body"],
        ))

    # ── Hash-anchor block
    story.extend(templates.signed_footer_block(
        last_hash=stats.last_event_hash,
        generator_version=__version__,
        styles=styles,
    ))

    doc.build(story)

    return {
        "report_type": "ai_act_art_50",
        "output_path": str(output_path),
        "page_count_estimate": 1 + (
            len(disclosure_events) // 30
            + len(consent_events) // 30
            + len(read_only_events) // 30
        ),
        "disclosure_events": len(disclosure_events),
        "consent_events": len(consent_events),
        "read_only_drops": len(read_only_events),
        "total_chain_events": stats.total_events,
        "chain_intact": stats.chain_intact,
        "anchor_hash": stats.last_event_hash,
        "tenant_id": tenant_id,
        "period_start_ts": start_ts,
        "period_end_ts": end_ts,
        "generated_at_ts": now,
        "generator_version": __version__,
    }


__all__ = ["generate"]
