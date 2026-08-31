"""GDPR Art. 30 — Records of Processing Activities (RoPA).

Art. 30 requires data controllers to maintain a register of every
processing activity. This report reconstructs the RoPA directly
from the audit chain, listing — per tenant, per time window:

  - Engines used + compliance zones
  - Data handles registered + PII classes detected
  - Voice transcripts (METADATA ONLY — never content per Layer 23)
  - User-model distillation + forget actions

Apache-2.0. Baseline RoPA stays free per ADR-0017 transparency
invariant. The Enterprise plugin offers premium scheduling +
custom-template variants.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, Spacer

from . import __version__, audit_query, templates


_ENGINE_EVENTS = (
    "gateway.run_created",
    "gateway.run_status_changed",
    "gateway.engine_denied",
    "gateway.zone_denied",
)
# Layer 29 — delegation (worker-engine spawns via delegate_* tools).
# Art. 30 requires documenting every processing activity; delegation is a
# distinct activity: the OS-turn engine spawns a separate WorkerEngine that
# processes the task sub-delegation.
_DELEGATION_EVENTS = (
    "delegation.started",
    "delegation.ended",
    "delegation.error",
    "delegate.invoked",
    "delegate.completed",
    "delegate.failed",
)
# Layer 38 — A2A (agent-to-agent remote trigger / task envelope protocol).
# Art. 30 requires documenting cross-system data flows; A2A carries task
# instructions + optional binary attachments between Corvin instances.
_A2A_EVENTS = (
    "A2A.envelope_received",
    "A2A.envelope_sent",
    "A2A.engine_spawned",
    "A2A.result_filtered",
    "A2A.response_signed",
    "A2A.request_rejected",
    "A2A.response_rejected",
)
_DATA_EVENTS = (
    "data.registered",
    "data.snapshot_generated",
    "data.pii_detected",
    "data.unregistered",
    "data.policy_violated",
    "data.snapshot_oversized",
)
_VOICE_EVENTS = (
    "voice.transcribed",
    "voice.transcribe_failed",
)
_MEMORY_EVENTS = (
    "memory.turn_indexed",
    "memory.recall_query",
    "memory.user_model_distilled",
    "memory.user_model_forgotten",
)


def _intro_paragraphs() -> list[str]:
    return [
        "This report constitutes the operator's <b>Article 30 GDPR "
        "Record of Processing Activities</b> reconstructed from the "
        "Corvin audit chain.",
        "Each section lists distinct processing activities, the data "
        "categories involved, and aggregate counts. Where Layer 23 "
        "(voice transcribe), Layer 24 (data snapshot), or Layer 28 "
        "(conversation recall) processed personal data, the audit "
        "chain records <b>metadata only</b> — actual content never "
        "lands in the chain per Corvin's structural data-"
        "minimisation invariant.",
        "<i>Auditors may cross-reference any row against the "
        "tamper-evident hash chain anchored on the final page.</i>",
    ]


def _aggregate_engines(events: list[dict[str, Any]]) -> list[list[str]]:
    """One row per (engine, zone) pair with run counts."""
    buckets: dict[tuple[str, str], int] = {}
    for ev in events:
        d = ev.get("details", {}) or {}
        engine = str(d.get("engine", "") or d.get("engine_id", ""))
        zone = str(d.get("compliance_zone", "") or d.get("zone", "") or "—")
        if not engine:
            continue
        buckets[(engine, zone)] = buckets.get((engine, zone), 0) + 1
    rows: list[list[str]] = [["Engine", "Compliance zone", "Events"]]
    for (engine, zone), count in sorted(buckets.items()):
        rows.append([engine, zone, str(count)])
    return rows


def _aggregate_data_handles(events: list[dict[str, Any]]) -> list[list[str]]:
    """One row per data handle with format + PII summary."""
    handles: dict[str, dict[str, Any]] = {}
    for ev in events:
        d = ev.get("details", {}) or {}
        handle = str(d.get("data_handle", ""))
        if not handle:
            continue
        info = handles.setdefault(handle, {
            "format": "", "size_b": 0, "pii_classes": set(), "states": set(),
        })
        if d.get("format"):
            info["format"] = str(d.get("format"))
        if d.get("size_b"):
            try:
                info["size_b"] = int(d.get("size_b") or 0)
            except (ValueError, TypeError):
                info["size_b"] = 0
        pii_list = d.get("classes") or {}
        if isinstance(pii_list, dict):
            for cls in pii_list.keys():
                info["pii_classes"].add(str(cls))
        info["states"].add(ev.get("event_type", "").replace("data.", ""))
    rows: list[list[str]] = [["Data handle", "Format", "PII classes", "States"]]
    for h, info in sorted(handles.items()):
        pii = ", ".join(sorted(info["pii_classes"])) or "—"
        rows.append([
            h[:30],
            info["format"] or "—",
            pii[:32],
            ", ".join(sorted(info["states"]))[:24],
        ])
    return rows


def _aggregate_voice(events: list[dict[str, Any]]) -> list[list[str]]:
    """Per-provider voice transcribe counts (metadata only)."""
    by_provider: dict[str, dict[str, int]] = {}
    total_seconds = 0
    for ev in events:
        d = ev.get("details", {}) or {}
        prov = str(d.get("provider", "unknown"))
        bucket = by_provider.setdefault(prov, {"ok": 0, "failed": 0, "audio_s": 0})
        if ev.get("event_type") == "voice.transcribed":
            bucket["ok"] += 1
            try:
                audio_s = int(d.get("audio_s", 0) or 0)
            except (ValueError, TypeError):
                audio_s = 0
            bucket["audio_s"] += audio_s
            total_seconds += audio_s
        else:
            bucket["failed"] += 1
    rows: list[list[str]] = [
        ["Provider", "Successful", "Failed", "Total audio (s)"]
    ]
    for prov, b in sorted(by_provider.items()):
        rows.append([prov, str(b["ok"]), str(b["failed"]), str(b["audio_s"])])
    return rows


def _aggregate_memory(events: list[dict[str, Any]]) -> list[list[str]]:
    """Memory-layer activity per (channel, chat) — metadata only."""
    by_chat: dict[tuple[str, str], dict[str, int]] = {}
    for ev in events:
        d = ev.get("details", {}) or {}
        key = (str(d.get("channel", "")), str(d.get("chat_key", "")))
        bucket = by_chat.setdefault(key, {
            "turns": 0, "queries": 0, "distills": 0, "forgets": 0,
        })
        et = ev.get("event_type", "")
        if et == "memory.turn_indexed":
            bucket["turns"] += 1
        elif et == "memory.recall_query":
            bucket["queries"] += 1
        elif et == "memory.user_model_distilled":
            bucket["distills"] += 1
        elif et == "memory.user_model_forgotten":
            bucket["forgets"] += 1
    rows: list[list[str]] = [
        ["Channel", "Chat", "Turns indexed", "Recalls", "Distills", "Forgets"]
    ]
    for (ch, ck), b in sorted(by_chat.items()):
        rows.append([
            ch[:18], ck[:24],
            str(b["turns"]), str(b["queries"]),
            str(b["distills"]), str(b["forgets"]),
        ])
    return rows


def _aggregate_delegation(events: list[dict[str, Any]]) -> list[list[str]]:
    """Delegation activity by (engine, persona) — metadata only.

    Each delegation is a distinct processing activity under Art. 30:
    the OS engine spawns a WorkerEngine, which processes the delegated
    task in a separate subprocess. No task text, no output content.
    """
    by_engine: dict[tuple[str, str], dict[str, int]] = {}
    for ev in events:
        d = ev.get("details", {}) or {}
        engine = str(d.get("engine_id", "") or d.get("target_engine", "") or "unknown")
        persona = str(d.get("persona", "") or "—")
        key = (engine, persona)
        bucket = by_engine.setdefault(key, {"started": 0, "completed": 0, "errors": 0})
        et = ev.get("event_type", "")
        if et in ("delegation.started", "delegate.invoked"):
            bucket["started"] += 1
        elif et in ("delegation.ended", "delegate.completed"):
            bucket["completed"] += 1
        elif et in ("delegation.error", "delegate.failed"):
            bucket["errors"] += 1
    rows: list[list[str]] = [["Target engine", "Persona", "Started", "Completed", "Errors"]]
    for (eng, per), b in sorted(by_engine.items()):
        rows.append([eng[:30], per[:24], str(b["started"]), str(b["completed"]), str(b["errors"])])
    return rows


def _aggregate_a2a(events: list[dict[str, Any]]) -> list[list[str]]:
    """A2A (agent-to-agent) activity by (origin_id/endpoint_id, direction).

    Each A2A envelope is a cross-system data flow per Art. 30: task
    instructions (and optional binary attachments) travel between Corvin
    instances. Only metadata is logged (task_id_prefix, origin/endpoint
    identifiers, outcome). No instruction content, no attachment bytes.
    """
    received, sent, spawned, rejected = 0, 0, 0, 0
    by_peer: dict[str, dict[str, int]] = {}
    for ev in events:
        d = ev.get("details", {}) or {}
        et = ev.get("event_type", "")
        peer = str(d.get("origin_id", "") or d.get("endpoint_id", "") or "unknown")
        bucket = by_peer.setdefault(peer, {"received": 0, "sent": 0, "spawned": 0, "rejected": 0})
        if et == "A2A.envelope_received":
            bucket["received"] += 1
            received += 1
        elif et == "A2A.envelope_sent":
            bucket["sent"] += 1
            sent += 1
        elif et == "A2A.engine_spawned":
            bucket["spawned"] += 1
            spawned += 1
        elif et in ("A2A.request_rejected", "A2A.response_rejected"):
            bucket["rejected"] += 1
            rejected += 1
    rows: list[list[str]] = [["Peer (origin/endpoint)", "Received", "Sent", "Spawned", "Rejected"]]
    for peer, b in sorted(by_peer.items()):
        rows.append([peer[:32], str(b["received"]), str(b["sent"]), str(b["spawned"]), str(b["rejected"])])
    if not by_peer:
        rows.append(["—", "0", "0", "0", "0"])
    return rows


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
        title="GDPR Art. 30 — Records of Processing Activities",
        tenant_id=tenant_id,
        period_start_ts=start_ts,
        period_end_ts=end_ts,
        generator_version=__version__,
        generated_at_ts=now,
    )

    engine_events = audit_query.collect_events(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        event_types=_ENGINE_EVENTS, chain_path=chain_path,
    )
    data_events = audit_query.collect_events(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        event_types=_DATA_EVENTS, chain_path=chain_path,
    )
    voice_events = audit_query.collect_events(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        event_types=_VOICE_EVENTS, chain_path=chain_path,
    )
    memory_events = audit_query.collect_events(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        event_types=_MEMORY_EVENTS, chain_path=chain_path,
    )
    delegation_events = audit_query.collect_events(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        event_types=_DELEGATION_EVENTS, chain_path=chain_path,
    )
    a2a_events = audit_query.collect_events(
        tenant_id=tenant_id, start_ts=start_ts, end_ts=end_ts,
        event_types=_A2A_EVENTS, chain_path=chain_path,
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

    # Integrity banner up front
    story.extend(templates.integrity_banner(
        intact=stats.chain_intact,
        problems=stats.chain_problems,
        styles=styles,
    ))
    story.append(Spacer(1, 8 * mm))

    # Section 1 — Engine usage
    story.append(PageBreak())
    story.append(templates.section_heading(
        "1.  Engine usage & compliance zones", styles,
    ))
    story.append(Paragraph(
        "Per ADR-0007 Phase 3.2/3.3, every LLM run is routed through "
        "an allow-listed engine in an allow-listed zone. The table "
        "below aggregates run-events by (engine, zone).",
        styles["small"],
    ))
    story.append(Spacer(1, 4 * mm))
    if len(engine_events) > 0:
        story.append(templates.styled_table(
            _aggregate_engines(engine_events),
            col_widths=[80 * mm, 70 * mm, 30 * mm],
        ))
    else:
        story.append(Paragraph(
            "<i>No engine runs in the selected period.</i>",
            styles["body"],
        ))

    # Section 2 — Data handles
    story.append(PageBreak())
    story.append(templates.section_heading(
        "2.  Data handles & PII detection", styles,
    ))
    story.append(Paragraph(
        "Layer 24 large-data snapshot layer registers each dataset "
        "with a stable handle; PII classes detected at registration "
        "are listed here. Snapshots routed through redaction never "
        "expose raw values to the LLM (audit chain carries metadata "
        "only).",
        styles["small"],
    ))
    story.append(Spacer(1, 4 * mm))
    if data_events:
        story.append(templates.styled_table(
            _aggregate_data_handles(data_events),
            col_widths=[55 * mm, 25 * mm, 60 * mm, 40 * mm],
        ))
    else:
        story.append(Paragraph(
            "<i>No data-handle activity in the selected period.</i>",
            styles["body"],
        ))

    # Section 3 — Voice transcripts (metadata only)
    story.append(PageBreak())
    story.append(templates.section_heading(
        "3.  Voice transcription — metadata only", styles,
    ))
    story.append(Paragraph(
        "Layer 23 STT pipeline produces transcripts for processing "
        "but the audit chain records <b>only metadata</b> "
        "(provider, language, duration, char count) — never the "
        "transcript content. DSGVO Art. 5(1)(c) data minimisation.",
        styles["small"],
    ))
    story.append(Spacer(1, 4 * mm))
    if voice_events:
        story.append(templates.styled_table(
            _aggregate_voice(voice_events),
            col_widths=[60 * mm, 35 * mm, 35 * mm, 50 * mm],
        ))
    else:
        story.append(Paragraph(
            "<i>No voice transcriptions in the selected period.</i>",
            styles["body"],
        ))

    # Section 4 — Memory layer
    story.append(PageBreak())
    story.append(templates.section_heading(
        "4.  Conversation memory & user-model activity", styles,
    ))
    story.append(Paragraph(
        "Layer 28 indexes redacted turn-pairs into per-tenant SQLite "
        "FTS5 stores. User-models are distilled periodically. Every "
        "forget request (GDPR Art. 17) is logged.",
        styles["small"],
    ))
    story.append(Spacer(1, 4 * mm))
    if memory_events:
        story.append(templates.styled_table(
            _aggregate_memory(memory_events),
            col_widths=[28 * mm, 40 * mm, 28 * mm, 22 * mm, 24 * mm, 24 * mm],
        ))
    else:
        story.append(Paragraph(
            "<i>No memory-layer activity in the selected period.</i>",
            styles["body"],
        ))

    # Section 5 — Delegation (Layer 29, worker-engine spawns)
    story.append(PageBreak())
    story.append(templates.section_heading(
        "5.  Worker-engine delegation — Layer 29", styles,
    ))
    story.append(Paragraph(
        "Layer 29 OS-turn delegation spawns a separate WorkerEngine "
        "(Claude, Codex, OpenCode, Hermes, or Copilot) to process a "
        "sub-task. Each spawn is a distinct processing activity under "
        "Art. 30: the delegating engine passes only a task description "
        "(no user-context PII) and receives only structured output. "
        "Metadata only — no task text, no output content "
        "(GDPR Art. 5 data minimisation, L29 output cap 64 KB).",
        styles["small"],
    ))
    story.append(Spacer(1, 4 * mm))
    if delegation_events:
        story.append(templates.styled_table(
            _aggregate_delegation(delegation_events),
            col_widths=[55 * mm, 40 * mm, 25 * mm, 30 * mm, 25 * mm],
        ))
    else:
        story.append(Paragraph(
            "<i>No worker-engine delegation activity in the selected period.</i>",
            styles["body"],
        ))

    # Section 6 — A2A remote trigger (Layer 38, cross-instance data flow)
    story.append(PageBreak())
    story.append(templates.section_heading(
        "6.  Agent-to-agent (A2A) remote trigger — Layer 38", styles,
    ))
    story.append(Paragraph(
        "Layer 38 A2A carries signed TaskEnvelopes between Corvin "
        "instances via HMAC-SHA256-authenticated HTTP. Each envelope "
        "constitutes a cross-system data flow under Art. 30: task "
        "instructions (≤16 KB) and optional binary attachments "
        "(≤1 MiB) travel to a remote peer. Instruction content and "
        "attachment bytes are NEVER in the audit chain — only "
        "metadata (task_id prefix, peer identifier, outcome, "
        "attachments count per ADR-0048/ADR-0077).",
        styles["small"],
    ))
    story.append(Spacer(1, 4 * mm))
    if a2a_events:
        story.append(templates.styled_table(
            _aggregate_a2a(a2a_events),
            col_widths=[58 * mm, 28 * mm, 25 * mm, 28 * mm, 28 * mm],
        ))
    else:
        story.append(Paragraph(
            "<i>No A2A remote-trigger activity in the selected period.</i>",
            styles["body"],
        ))

    # Hash anchor
    story.extend(templates.signed_footer_block(
        last_hash=stats.last_event_hash,
        generator_version=__version__,
        styles=styles,
    ))

    doc.build(story)

    return {
        "report_type": "gdpr_art_30_ropa",
        "output_path": str(output_path),
        "engine_run_events": len(engine_events),
        "data_handle_events": len(data_events),
        "voice_events": len(voice_events),
        "memory_events": len(memory_events),
        "delegation_events": len(delegation_events),
        "a2a_events": len(a2a_events),
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
