"""CEL Decision Record — audit-complete, PII-safe trail (ADR-0278).

Two layers, emitted once per context-engineered turn at the ``build_brief``
boundary:

* **Layer A** — a CONTENT-FREE event (``cel.decision``) written through the ONE
  canonical hash-chained audit writer (``forge.security_events.write_event``),
  so it inherits the chain's durability, daily-verify and L37 retention. IDs,
  per-source scores, all stages (incl. ``not_run``) and the brief's SHA-256 —
  **never** task text, memory passages, or the brief body. EU AI Act Art. 12.
* **Layer B** — the full rendered brief text, written to a session-scoped
  sidecar ``cel-briefs/<sha256>.txt`` keyed by the Layer-A hash. Erasable
  (GDPR Art. 17 — see erasure_handlers WebChatHandler, which now owns
  ``cel-briefs/``) and L37-retained. Served only to an authorised auditor.

An auditor re-hashes Layer B and checks it against Layer A: tamper-evidence
WITHOUT putting PII in the immutable chain. If Layer B was lawfully erased, the
hash resolves to nothing — itself honest audit evidence.

Best-effort at the call site: a write failure is surfaced by the audit layer, it
never breaks the turn.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# The full conceptual CEL pipeline. Stages the live build_brief does not run are
# recorded as `not_run` so an auditor sees completeness, not a gap (ADR-0278 gap 1).
_CONCEPTUAL_STAGES = ["memory", "graph", "skill", "approach_synthesis", "blocker_id"]
# All five stages run now (approach_synthesis + blocker_id are deterministic,
# ADR-0275). A stage absent from the trace is a genuine miss, not "inactive".
_INACTIVE_STAGES: set = set()

# Keys whose presence would mean raw text leaked into the content-free Layer A.
_FORBIDDEN_TEXT_KEYS = {"task", "brief", "content", "text", "summary", "prompt",
                        "passage", "body", "preview", "task_preview"}
_MAX_STR = 80  # any string value longer than this is treated as potential content
_MAX_ID = 76   # source ids / error slugs are truncated to this (< _MAX_STR) so a
               # benign long title never trips the content-free tripwire (finding #1)


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_record(trace: dict, brief_text: str, *, turn_id: str,
                 session_id: str = "", tenant_id: str = "_default") -> dict:
    """Build the CONTENT-FREE Layer-A record from the (already content-free) trace.

    Adds `not_run` entries for inactive conceptual stages, the brief hash, and a
    small aggregate. Carries NO text — only ids, scores, statuses, the hash.
    """
    seen = {s.get("stage"): s for s in trace.get("stages", [])}
    stages: list[dict[str, Any]] = []
    ok = 0
    top_score = 0.0

    def _content_free_entry(name: str, s: dict) -> dict:
        # Truncate source ids + error slugs to a content-free-safe length BEFORE
        # the record is assembled — a benign long memory title / error path must
        # NOT trip assert_content_free and cause the whole audit record to be
        # dropped (finding #1: this was silently voiding Layer A / P-0).
        safe_sources = [
            {"id": str(x.get("id", ""))[:_MAX_ID], "score": x.get("score")}
            for x in s.get("sources", []) if isinstance(x, dict)]
        e: dict[str, Any] = {
            "stage": name, "status": s.get("status", "ok"),
            "confidence_tier": s.get("confidence_tier"),
            "duration_ms": s.get("duration_ms"),
            "sources": safe_sources,   # {id, score} — the causal "why this source"
        }
        if s.get("reason"):
            e["reason"] = str(s["reason"])[:_MAX_ID]
        if s.get("error"):
            e["error_slug"] = str(s["error"])[:_MAX_ID]
        return e

    def _accumulate(s: dict) -> None:
        nonlocal top_score
        for src in s.get("sources", []):
            if isinstance(src, dict):
                top_score = max(top_score, float(src.get("score", 0.0) or 0.0))

    for name in _CONCEPTUAL_STAGES:
        s = seen.get(name)
        if s is None:
            reason = "stage_inactive" if name in _INACTIVE_STAGES else "not_reached"
            stages.append({"stage": name, "status": "not_run", "reason": reason})
            continue
        if s.get("status") == "ok":
            ok += 1
        stages.append(_content_free_entry(name, s))
        _accumulate(s)

    # The ACTIVE pipeline runs egress/forge stages (llm_synthesis, toolforge,
    # skillforge) NOT in _CONCEPTUAL_STAGES. They MUST appear in the hash-chained
    # record — the cloud-egress + tool-forge decisions are exactly what an EU AI
    # Act auditor needs to see (review R2 finding C1: they were silently dropped).
    for name, s in seen.items():
        if not name or name in _CONCEPTUAL_STAGES:
            continue
        if s.get("status") == "ok":
            ok += 1
        stages.append(_content_free_entry(name, s))
        _accumulate(s)

    # Reflect the ACTUAL feature that ran + any gate denial (review R2 finding C3:
    # the record hardcoded flag=vibe_engineering and only read `degraded`, so an
    # active-brain turn a house-rules gate denied left no trace of the active brain
    # or the denial).
    active = any(sid in seen for sid in ("llm_synthesis", "toolforge", "skillforge"))
    rec: dict[str, Any] = {
        "turn_id": turn_id,
        "session_id": session_id,
        "tenant_id": tenant_id,           # reserved audit key, always kept
        "flag": "vibe_engineering_active" if active else "vibe_engineering",
        "metered": True,
        "degraded": trace.get("degraded"),
        "stages": stages,
        "stages_ok": ok,
        "top_score": round(top_score, 3),
        "brief_sha256": _sha256_text(brief_text) if brief_text else None,
        "brief_bytes": len(brief_text.encode("utf-8")) if brief_text else 0,
    }
    for k in ("gate1_denied", "gate2_denied"):
        if trace.get(k):
            rec[k] = str(trace[k])[:_MAX_ID]
    if trace.get("forged_rolled_back"):
        rb = trace["forged_rolled_back"]
        rec["forged_rolled_back"] = {
            "tools": [str(t)[:_MAX_ID] for t in (rb.get("tools") or [])],
            "skills": [str(s)[:_MAX_ID] for s in (rb.get("skills") or [])]}
    return rec


def assert_content_free(record: dict) -> None:
    """Defence-in-depth: raise if a text-shaped field slipped into Layer A. The
    audit chain scrubs too, but this fails LOUD in tests and never lets a content
    leak reach the immutable chain silently."""
    def _walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in _FORBIDDEN_TEXT_KEYS:
                    raise ValueError(f"content-free violation: key '{path}{k}'")
                _walk(v, f"{path}{k}.")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _walk(v, f"{path}{i}.")
        elif isinstance(obj, str):
            # brief_sha256 is a 64-hex digest — allowed; anything else long = suspect
            if len(obj) > _MAX_STR:
                raise ValueError(f"content-free violation: long string at '{path}'")
    _walk(record)


def _layer_a_path(tenant_id: str):
    from forge.paths import tenant_global_dir  # noqa: PLC0415
    return Path(tenant_global_dir(tenant_id)) / "forge" / "audit.jsonl"


def emit(trace: dict, brief_text: str, *, turn_id: str, tenant_id: str = "_default",
         workdir: Any = None, session_id: str = "") -> "dict | None":
    """Write Layer A (hash-chained, content-free) + Layer B (erasable sidecar).

    Returns the written Layer-A record, or None on any failure (never raises into
    the turn). Layer B is written first so the hash in Layer A always resolves to
    real content that already exists on disk.
    """
    try:
        record = build_record(trace, brief_text, turn_id=turn_id,
                              session_id=session_id, tenant_id=tenant_id)
        assert_content_free(record)  # fail-loud before the immutable write
    except Exception as e:  # noqa: BLE001 — a broken record is a code bug: surface it
        _log.error("CEL Decision Record build failed (turn %s): %s", turn_id, e)
        return None

    # Layer B — full brief text, erasable sidecar keyed by the hash. A miss here
    # only loses the CONTENT (the content-free Layer-A record still stands), but
    # it is logged, not silently dropped.
    sidecar: "Path | None" = None
    if brief_text and workdir and record.get("brief_sha256"):
        try:
            d = Path(workdir) / "cel-briefs"
            d.mkdir(parents=True, exist_ok=True)
            sidecar = d / f"{record['brief_sha256']}.txt"
            sidecar.write_text(brief_text, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            sidecar = None
            _log.warning("CEL Layer-B brief sidecar write failed (turn %s): %s",
                         turn_id, e)

    # Layer A — the canonical hash-chained audit record. ADR-0278 durability (P-0):
    # a write failure is SURFACED to the ops/L16 stream, NEVER a silent drop. The
    # turn still runs (ADR-0276 degrade-not-block), but the failure is visible.
    try:
        from forge.security_events import write_event  # noqa: PLC0415
        write_event(_layer_a_path(tenant_id), "cel.decision",
                    tool="context_engineering", details=record)
        return record
    except Exception as e:  # noqa: BLE001
        _log.error("CEL Decision Record AUDIT-WRITE FAILED (turn %s, tenant %s): %s "
                   "— record NOT persisted to the hash chain", turn_id, tenant_id, e)
        # Roll back the orphaned Layer-B sidecar (review R2 finding D5): with no
        # Layer-A hash referencing it, the brief text would linger un-audited until
        # the erasure handler runs. Best-effort unlink.
        if sidecar is not None:
            try:
                sidecar.unlink()
            except Exception:  # noqa: BLE001
                pass
        return None
