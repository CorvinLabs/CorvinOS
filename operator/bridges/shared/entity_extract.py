"""entity_extract.py — CCC entity recognition for Chat Command Center (ADR-0168 M1).

Two-pass extraction over a chat prompt:
  Pass 1 — Domain-prefix forced routing ("ATS: ...", "A2A: ...", "/create ...")
  Pass 2 — Keyword + NER cluster matching + slot filler

Mirrors the pattern of ato_classify.py:
  - Pure Python, no network calls, no anthropic import
  - Importable in-process (no subprocess)
  - Returns an EntityPlan dataclass
  - Must emit ccc.entity_extracted audit event (caller's responsibility)

MUST NOT import anthropic.
MUST NOT make any network call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── Entity type constants ────────────────────────────────────────────────────

ENTITY_NONE       = "none"         # no entity detected — regular LLM turn
ENTITY_ATS_TASK   = "ats_task"
ENTITY_WORKFLOW   = "workflow"
ENTITY_A2A        = "a2a_session"
ENTITY_FORGE      = "forge_tool"
ENTITY_SKILL      = "skill"
ENTITY_AUDIT      = "audit_query"  # read-only
ENTITY_ERASURE    = "erasure_request"
ENTITY_VAULT      = "vault_entry"
ENTITY_ENGINE     = "worker_engine"
ENTITY_RAG        = "rag_source"

# ── L34 confidential entity set — SINGLE SOURCE OF TRUTH ──────────────────────
# Entity types whose CCC action-card payloads carry user PII / secrets and must
# be stripped to {entity_id, status} before fan-out. Previously duplicated in
# ccc_pubsub.py and chat_runtime.py (and entity_extract omitted a2a_session) —
# a drift-prone load-bearing compliance control. Import this constant; do not
# re-declare it (security review 2026-06-27, C5).
CONFIDENTIAL_ENTITY_TYPES: frozenset[str] = frozenset({
    ENTITY_ERASURE,   # erasure_request — GDPR Art. 17 subject reference
    ENTITY_VAULT,     # vault_entry — BYOK secret name
    ENTITY_A2A,       # a2a_session — remote agent / instance identity
})

# ── Regex patterns — Pass 2 NER ──────────────────────────────────────────────

# NOTE: ats_task is the ONLY entity route with a real side effect
# (TaskManager.create_task). The bare words "task"/"aufgabe" matched in almost
# every conversation ("help me with this task…") and two hits crossed the 0.60
# is_actionable gate → phantom tasks + ccc:<tenant> quota exhaustion. Mirror the
# _ENGINE_SIGNALS hardening (commit 5da3164): require an explicit/compound form.
# Natural-language task creation goes through the Pass-1 forced prefixes
# ("/create task", "ATS: …"), which are unaffected (security review 2026-06-27).
_ATS_SIGNALS = re.compile(
    r"\b(ATS|ats[\s_-]task|ADR-0080|task[\s_-]engine)\b", re.I)

_WORKFLOW_SIGNALS = re.compile(
    r"\b(workflow|AWPKG|awpkg|flow|pipeline|prozess)\b", re.I)

_A2A_SIGNALS = re.compile(
    r"\b(A2A|agent[\s_-]to[\s_-]agent|mesh|remote[\s_-]trigger|a2a[\s_-]session)\b", re.I)

_FORGE_SIGNALS = re.compile(
    r"\b(forge|forge[\s_-]tool|tool[\s_-]erstell|werkzeug)\b", re.I)

_SKILL_SIGNALS = re.compile(
    r"\b(skill|skillforge|skill[\s_-]forge|fähigkeit)\b", re.I)

_AUDIT_SIGNALS = re.compile(
    r"\b(audit|audit[\s_-]chain|audit[\s_-]log|hash[\s_-]chain|prüfpfad)\b", re.I)

_ERASURE_SIGNALS = re.compile(
    r"\b(erasure|lösch\w*|erase|GDPR[\s_-]art\.?\s*17|Art\.?\s*17|recht[\s_-]auf[\s_-]löschung)\b",
    re.I)

_VAULT_SIGNALS = re.compile(
    r"\b(vault|verschlüssel\w*|encrypt\w*|geheimnis|secret[\s_-]vault)\b", re.I)

_ENGINE_SIGNALS = re.compile(
    # "agent" alone is too broad (matches in every Corvin conversation).
    # Require compound forms: worker-engine, hermes, or explicit layer ref.
    r"\b(engine|worker[\s_-]engine|hermes|L22|workerengine|agent[\s_-]engine|switch[\s_-](?:engine|model))\b",
    re.I,
)

_RAG_SIGNALS = re.compile(
    r"\b(RAG|wissensbasis|dokument\w*|document\w*|knowledge[\s_-]base|rag[\s_-]source)\b", re.I)

# ── Slot filler — NER for common parameters ──────────────────────────────────

# Temporal: “alle 5 Minuten”, “every 5 minutes”, “jede Stunde” (number optional → 1)
_CRON_EVERY_N_MIN = re.compile(
    r"(?:alle|every|jede[rn]?)\s+(?:(\d+)\s+)?(?:min\w*|minute\w*)", re.I)
_CRON_EVERY_N_HOUR = re.compile(
    r"(?:alle|every|jede[rn]?)\s+(?:(\d+)\s+)?(?:stunde\w*|hour\w*)", re.I)

# Name: quoted string or kebab-case token following action verb.
# Character class covers ASCII “ (U+0022) + curly quotes U+201C/D/E.
_NAME_QUOTED = re.compile(
    r'["\u201c\u201d\u201e]([\w\s._-]{1,64})["\u201c\u201d\u201e]')
_NAME_UNQUOTED = re.compile(
    r"(?:namens?|named?|heißt?|called?)\s+([\w._-]{2,48})", re.I)

# Target service/tenant
_TARGET = re.compile(
    r"(?:für|for|on|gegen|against|targeting?)\s+(?:den\s+|die\s+|das\s+)?([\w._-]{2,48})", re.I)

# uid / user reference for erasure
_UID = re.compile(r"\buid\s*[=:]\s*([\w@._-]{1,128})\b", re.I)

# ── Domain-prefix patterns — Pass 1 ──────────────────────────────────────────

_PREFIX_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^ATS\s*:", re.I),      ENTITY_ATS_TASK),
    (re.compile(r"^A2A\s*:", re.I),      ENTITY_A2A),
    (re.compile(r"^Workflow\s*:", re.I), ENTITY_WORKFLOW),
    (re.compile(r"^Forge\s*:", re.I),    ENTITY_FORGE),
    (re.compile(r"^Skill\s*:", re.I),    ENTITY_SKILL),
    (re.compile(r"^Audit\s*:", re.I),    ENTITY_AUDIT),
    (re.compile(r"^RAG\s*:", re.I),      ENTITY_RAG),
    (re.compile(r"^/create\s+workflow\b", re.I),  ENTITY_WORKFLOW),
    (re.compile(r"^/create\s+task\b",    re.I),   ENTITY_ATS_TASK),
    (re.compile(r"^/create\s+skill\b",   re.I),   ENTITY_SKILL),
    (re.compile(r"^/create\s+tool\b",    re.I),   ENTITY_FORGE),
    (re.compile(r"^/erase\b",            re.I),   ENTITY_ERASURE),
    (re.compile(r"^/audit\b",            re.I),   ENTITY_AUDIT),
    (re.compile(r"^/list\b",             re.I),   ENTITY_NONE),  # handled separately
    (re.compile(r"^/stop\b",             re.I),   ENTITY_NONE),  # action on existing entity
]

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class EntityPlan:
    entity_type: str                          # one of ENTITY_* constants
    confidence: float                         # 0.0 – 1.0
    slots: dict[str, Any] = field(default_factory=dict)  # extracted params
    forced: bool = False                       # True when Pass 1 (prefix/slash) matched
    raw_text: str = ""                         # first 200 chars of prompt (no PII in audit)

    @property
    def is_actionable(self) -> bool:
        """True when the plan should be dispatched to the command router."""
        return self.entity_type != ENTITY_NONE and self.confidence >= 0.60


# ── Slot extraction helpers ───────────────────────────────────────────────────

def _extract_slots(text: str, entity_type: str) -> dict[str, Any]:
    slots: dict[str, Any] = {}

    # Name
    if m := _NAME_QUOTED.search(text):
        slots["name"] = m.group(1).strip()
    elif m := _NAME_UNQUOTED.search(text):
        slots["name"] = m.group(1).strip()

    # Schedule (cron)
    if m := _CRON_EVERY_N_MIN.search(text):
        n = int(m.group(1) or 1)
        slots["schedule"] = f"*/{n} * * * *"
    elif m := _CRON_EVERY_N_HOUR.search(text):
        n = int(m.group(1) or 1)
        slots["schedule"] = f"0 */{n} * * *"

    # Target service
    if m := _TARGET.search(text):
        slots["target"] = m.group(1).strip()

    # UID for erasure
    if entity_type == ENTITY_ERASURE:
        if m := _UID.search(text):
            slots["subject_id"] = m.group(1)

    # Background mode
    if re.search(r"\bhintergrund\b|\bbackground\b|\brecurring\b|\bscheduled?\b", text, re.I):
        slots["execution_mode"] = "background"

    return slots


# ── Main extract function ─────────────────────────────────────────────────────

def extract(
    prompt: str,
    *,
    min_confidence: float = 0.40,
) -> EntityPlan:
    """Extract the primary entity intent from *prompt*.

    Args:
        prompt: Raw chat message (≤4000 chars used).
        min_confidence: Minimum confidence to return a non-NONE plan.
                        Callers may lower this for soft hints.

    Returns:
        EntityPlan — always returns something (entity_type=ENTITY_NONE when
        no intent detected above the threshold).
    """
    text = prompt[:4000].strip()
    # raw_snippet is a fingerprint for non-PII audit context only.
    # Erasure and vault entities likely contain UIDs/emails — never store snippet.
    _PII_ENTITY_TYPES = CONFIDENTIAL_ENTITY_TYPES
    raw_snippet = text[:200]

    # ── Pass 1: domain-prefix / slash-command forced routing ─────────────────
    for pattern, etype in _PREFIX_MAP:
        if pattern.match(text):
            slots = _extract_slots(text, etype)
            return EntityPlan(
                entity_type=etype,
                confidence=1.0,
                slots=slots,
                forced=True,
                raw_text="" if etype in _PII_ENTITY_TYPES else raw_snippet,
            )

    # ── Pass 2: keyword-cluster NER ──────────────────────────────────────────
    scores: dict[str, float] = {
        ENTITY_ATS_TASK:  min(1.0, len(_ATS_SIGNALS.findall(text))     * 0.40),
        ENTITY_WORKFLOW:  min(1.0, len(_WORKFLOW_SIGNALS.findall(text)) * 0.40),
        ENTITY_A2A:       min(1.0, len(_A2A_SIGNALS.findall(text))      * 0.50),
        ENTITY_FORGE:     min(1.0, len(_FORGE_SIGNALS.findall(text))    * 0.50),
        ENTITY_SKILL:     min(1.0, len(_SKILL_SIGNALS.findall(text))    * 0.50),
        ENTITY_AUDIT:     min(1.0, len(_AUDIT_SIGNALS.findall(text))    * 0.40),
        ENTITY_ERASURE:   min(1.0, len(_ERASURE_SIGNALS.findall(text))  * 0.50),
        ENTITY_VAULT:     min(1.0, len(_VAULT_SIGNALS.findall(text))    * 0.50),
        ENTITY_ENGINE:    min(1.0, len(_ENGINE_SIGNALS.findall(text))   * 0.40),
        ENTITY_RAG:       min(1.0, len(_RAG_SIGNALS.findall(text))      * 0.40),
    }

    best_type = max(scores, key=lambda k: scores[k])
    best_score = round(scores[best_type], 3)

    if best_score < min_confidence:
        return EntityPlan(
            entity_type=ENTITY_NONE,
            confidence=0.0,
            raw_text=raw_snippet,
        )

    slots = _extract_slots(text, best_type)
    return EntityPlan(
        entity_type=best_type,
        confidence=best_score,
        slots=slots,
        forced=False,
        raw_text="" if best_type in _PII_ENTITY_TYPES else raw_snippet,
    )
