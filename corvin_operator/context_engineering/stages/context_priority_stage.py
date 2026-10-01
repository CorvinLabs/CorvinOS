"""Context Source Priority Resolver stage (ADR-2098).

Runs right after ``memory`` (the root stage that populates
``bundle.brief.memory_context.matches``) and before anything renders those
matches: collapses same-topic duplicate matches, and tags matches whose topic
was already surfaced in an EARLIER turn of this session so
``render_brief_to_text`` can reframe them instead of repeating the same
"authoritative new fact" framing turn after turn (ADR-2098 §1–§2).

Session-scoped only (ADR-2098 §4): the "already surfaced" marker lives in a
per-(tenant, session) store under the tenant home, never in the global
memory files — a brand-new session sees global memory exactly as it does
today.
"""
from __future__ import annotations

import logging
import uuid

from .base import StageTelemetry
from .registry import register_stage
from ..context_priority import (
    collapse_duplicates, load_seen_topics, record_seen_topics, topic_key,
)

_log = logging.getLogger("corvin.cel.context_priority")


def _audit_duplicate_merge(tenant_id: str, merge: dict) -> None:
    """Hash-chain one duplicate-collapse decision (ADR-2098 audit_events).
    Best-effort: a write failure is logged, never raised into the turn."""
    try:
        from forge.paths import tenant_audit_chain  # noqa: PLC0415
        from forge.security_events import write_event  # noqa: PLC0415
        write_event(
            tenant_audit_chain(tenant_id), "context.duplicate_memory_merged",
            tool="context_engineering",
            details={
                "context_id": uuid.uuid4().hex[:16],
                "tenant_id": tenant_id,
                "topic_key": merge["topic_key"],
                "kept_filename": merge["kept_filename"],
                "merged_count": len(merge["dropped_filenames"]),
            },
        )
    except Exception as exc:  # noqa: BLE001 — advisory stage: surface, never break the turn
        _log.error("context_priority: duplicate-merge AUDIT-WRITE FAILED (tenant %s): %s "
                   "— record NOT persisted to the hash chain", tenant_id,
                   type(exc).__name__)


class ContextPriorityStage:
    """CEL stage: collapse duplicate memory matches, tag session repeats."""
    id = "context_priority"
    requires: tuple = ("memory",)
    effect = "pure"
    trust = "builtin"

    def run(self, bundle, ctx):
        tel = StageTelemetry(stage=self.id, status="ok")
        try:
            brief = getattr(bundle, "brief", None)
            if brief is None:
                tel.status, tel.reason = "skipped", "no_brief"
                return bundle, tel
            mc = getattr(brief, "memory_context", None)
            matches = getattr(mc, "matches", None) if mc else None
            if not matches:
                tel.status, tel.reason = "ok", "no_matches"
                return bundle, tel

            tenant_id = getattr(ctx, "tenant_id", "") or "_default"
            session_key = getattr(ctx, "session_id", "") or ""

            kept, merges = collapse_duplicates(matches)
            mc.matches = kept
            for merge in merges:
                _audit_duplicate_merge(tenant_id, merge)

            repeat_topics: set = set()
            if session_key:
                seen = load_seen_topics(tenant_id, session_key)
                current_topics = {topic_key(m) for m in kept}
                repeat_topics = current_topics & seen
                record_seen_topics(tenant_id, session_key, current_topics)

            # Mirrors the anchor module's established pattern of attaching a
            # dynamic, non-dataclass-declared field to the brief (pipeline.py
            # `brief.anchor_facts = facts`) — render_brief_to_text reads it.
            try:
                brief.memory_repeat_topics = repeat_topics
            except Exception:  # noqa: BLE001 — a frozen/odd brief just skips the tag
                pass

            tel.sources = [
                {"id": "duplicate_merged_count",
                 "score": sum(len(m["dropped_filenames"]) for m in merges)},
                {"id": "session_repeat_count", "score": len(repeat_topics)},
            ]
        except Exception as e:  # noqa: BLE001 — one stage never breaks the turn
            tel.status, tel.error = "failed", str(e)[:120]
            _log.exception("context_priority stage failed")
        return bundle, tel


register_stage(ContextPriorityStage())
