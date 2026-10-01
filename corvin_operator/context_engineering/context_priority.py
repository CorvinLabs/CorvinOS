"""Context Source Priority Resolver (ADR-2098).

Closes the memory-induced context-drift gap: the CEL memory stage
(``stages/memory.py``) runs ``MemoryLookup`` fresh on every turn's raw task
text, so a stale or duplicated memory fragment that happens to match the
current turn's words gets re-injected and rendered as an "authoritative"
fact every time — even when THIS session's dialogue already settled the
topic several turns ago. ADR-2098's design: tier-priority (session dialogue
> task plan > history > global memory) with topic-keyed dedup and a
session-scoped (never global) "already surfaced" marker.

This module implements the two mechanical, CEL-reachable halves of that
design (the parts a memory-lookup stage can actually act on without reading
the live dialogue transcript, which never reaches this layer):

1. **Intra-turn duplicate collapse** — two memory matches describing the same
   topic (e.g. two files both about "Phase 0") are collapsed to the single
   most-recently-modified one before rendering.
2. **Session-scoped repeat marking** — a topic already surfaced in an EARLIER
   turn of this session is tagged, never silently dropped (ADR-2098 §2:
   fail-closed toward visibility). ``render_brief_to_text`` uses the tag to
   reframe the line instead of repeating the same "authoritative new fact"
   framing turn after turn.

Store: a per-``(tenant, session)`` JSONL file under the tenant-scoped root
resolved via ``forge.paths.tenant_home`` — the same mechanism and the same
file shape as the ``anchor`` module's load-bearing-fact store (ADR-0407
amendment), deliberately mirrored here. Session-scoped only: nothing here is
ever written back into the global memory files, which is what keeps a NEW
session's view of memory unfiltered (ADR-2098 §4, Session-Independence).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("corvin.cel.context_priority")

_lock = threading.RLock()

# Topics already surfaced; capped so a very long session's store stays bounded.
CAP = 200


def topic_key(match: Any) -> str:
    """Normalize a memory match to a topic key for grouping/dedup.

    Title-based (falls back to filename stem): lowercased, non-alnum
    collapsed to a single space, trimmed. Two files titled
    "Phase 0 Kickoff" and "Phase 0 kickoff - notes" normalize to keys close
    enough to collide; exact-match only (no fuzzy similarity) — ADR-2098
    explicitly chooses visible-on-ambiguity over silent fuzzy merging, so this
    key is deliberately exact, not approximate.
    """
    raw = (getattr(match, "title", "") or getattr(match, "filename", "") or "").strip()
    if not raw:
        raw = Path(getattr(match, "source_file", "") or "").stem
    key = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
    return key or "untitled"


def collapse_duplicates(matches: list) -> "tuple[list, list[dict]]":
    """Collapse same-topic-key matches to the most-recently-modified one.

    Returns ``(kept_matches, merge_records)``; ``merge_records`` is one dict
    per collapsed group: ``{"topic_key", "kept_filename", "dropped_filenames"}``.
    Order of ``kept_matches`` follows the input order (stable); only exact
    topic-key collisions are merged, so a lone match is never touched.
    """
    groups: dict[str, list] = {}
    order: list[str] = []
    for m in matches:
        k = topic_key(m)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(m)

    kept: list = []
    merges: list[dict] = []
    for k in order:
        group = groups[k]
        if len(group) == 1:
            kept.append(group[0])
            continue
        # Most-recently-modified wins; stable tie-break on original order.
        winner = max(group, key=lambda m: (getattr(m, "timestamp", None) or 0))
        kept.append(winner)
        dropped = [m for m in group if m is not winner]
        merges.append({
            "topic_key": k,
            "kept_filename": getattr(winner, "filename", "?"),
            "dropped_filenames": [getattr(m, "filename", "?") for m in dropped],
        })
    return kept, merges


# ── Session-scoped "already surfaced" store (mirrors anchor.py's shape) ────

def _safe_key(session_key: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_key or "")).strip("_")
    if not s:
        return "_nosession"
    if len(s) > 100:
        digest = hashlib.sha1(str(session_key).encode("utf-8")).hexdigest()[:12]
        s = s[:80] + "_" + digest
    return s


def _store_path(tenant_id: str, session_key: str) -> Path:
    from corvin_operator.forge.forge.paths import tenant_home  # noqa: PLC0415
    return (Path(tenant_home(tenant_id)) / "cel_context_priority"
            / f"{_safe_key(session_key)}.json")


def load_seen_topics(tenant_id: str, session_key: str) -> "set[str]":
    """Topic keys already surfaced in an earlier turn of this session. Never raises."""
    try:
        p = _store_path(tenant_id, session_key)
        if not p.is_file():
            return set()
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("topics") or [])
    except Exception:  # noqa: BLE001 — a broken store must never break a turn
        return set()


def record_seen_topics(tenant_id: str, session_key: str, topics: "set[str]") -> None:
    """Merge ``topics`` into the session's seen-set and persist (capped, oldest
    dropped on overflow — insertion order is not tracked precisely, so overflow
    just truncates arbitrarily; CAP is generous enough that this is a safety
    bound, not a real eviction policy). Never raises."""
    if not topics:
        return
    try:
        p = _store_path(tenant_id, session_key)
        with _lock:
            existing = load_seen_topics(tenant_id, session_key)
            merged = existing | topics
            if len(merged) > CAP:
                merged = set(list(merged)[-CAP:])
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"topics": sorted(merged)}), encoding="utf-8")
            tmp.replace(p)
    except Exception:  # noqa: BLE001 — persistence is best-effort, never break a turn
        return


def clear_seen_topics(tenant_id: str, session_key: str) -> None:
    """Delete the session's seen-topics store. Never raises."""
    try:
        _store_path(tenant_id, session_key).unlink()
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001
        pass
