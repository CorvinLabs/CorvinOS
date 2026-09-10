# Context Filtering (L10 Refinement) — ADR-0528

## Overview

**Purpose:** Reduce injected context noise by ~30% → improve routing accuracy → faster turns, lower cost.

**Pattern:** Score context blocks via static rules → filter low-relevance → log decisions → audit-ready.

**Status:** ✅ Phase 2a (Static Scoring) COMPLETE — Tier-4 tested, 0 security findings

---

## Quick Start

```python
from core.skills.os_skills.context_filter import (
    ContextBlock, filter_context, ContextCategory
)

# 1. Define context blocks (from L10 snapshot)
blocks = [
    ContextBlock(
        id="task_summary",
        content="User is debugging auth flow",
        size_tokens=20,
        category=ContextCategory.TASK_HISTORY,
        timestamp=datetime.utcnow(),
    ),
    ContextBlock(
        id="session_metadata",
        content="Session: 2h old, browser: Chrome",
        size_tokens=10,
        category=ContextCategory.SESSION_STATE,
        timestamp=datetime.utcnow(),
    ),
]

# 2. Filter
included, decisions = filter_context(blocks)

# 3. Pass filtered blocks to routing (L5)
for block in included:
    routing_input["context"] = block.content

# 4. Audit decisions (logged to audit.jsonl)
for decision in decisions:
    audit_event = ContextFilterAuditEvent.from_decision(decision, tenant_id="_default")
    # audit_backend.write_event(audit_event)
```

---

## Scoring Rules (Static)

Context blocks are scored by category:

| Category | Default Score | When to Use |
|---|---|---|
| **TASK_HISTORY** | 0.9 | Task description, current goal, progress |
| **SYSTEM_MESSAGES** | 0.95 | System prompt, instructions (always include) |
| **CONVERSATION_RECALL** | 0.8 | Prior turns in same task |
| **USER_PROFILE** | 0.7 | User preferences, style, expertise |
| **SESSION_STATE** | 0.5 | Session metadata, timestamps (often noise) |

**Threshold:** Default 0.7 (blocks scoring ≥0.7 are included)

**Example:**
- Task history (0.9) → included ✅
- Session state (0.5) → filtered ❌
- User profile (0.7) → included ✅

---

## Filtering Decision Tree

```
1. Score block via static rules
   ├─ IF score >= threshold (0.7)
   │  └─ ACTION: INCLUDE
   │
   ├─ IF score < threshold & size > 500 tokens
   │  └─ OPTIONAL: Ask LLM (Phase 2b)
   │
   └─ IF score < threshold & size <= 500 tokens
      └─ ACTION: INCLUDE (small blocks are safe)

2. FALLBACK: If all blocks filtered
   └─ ACTION: Include largest block (never empty prompt)

3. AUDIT: Log every decision
   └─ context_filtered event with score, action, reason
```

---

## Determinism & Predictability

**Static scoring is deterministic:** Same input → same output, always.

```python
# These produce identical results:
filtered_1, _ = filter_context(blocks)
filtered_2, _ = filter_context(blocks)
assert filtered_1 == filtered_2  # ✅ True
```

**Audit trail is complete:** Every decision is logged with reason.

```python
decision.reason  # "score 0.9 >= threshold 0.7"
decision.reason  # "small block (50 tokens), safe to include"
decision.reason  # "all blocks filtered; fallback: include largest"
```

---

## Configuration

Use custom scores or threshold:

```python
config = FilterConfig(
    static_scores={
        "task_history": 0.8,       # Adjust scores
        "user_profile": 0.6,
    },
    threshold=0.65,                # Adjust threshold
    include_fallback=True,         # Keep fallback enabled
)

included, decisions = filter_context(blocks, config)
```

---

## PII Safety

**Rule:** No PII should pass through filtered context.

**Validation:**
```python
from core.skills.os_skills.context_filter import validate_no_pii

for block in included:
    is_safe, pii_fields = validate_no_pii({"content": block.content})
    if not is_safe:
        # In production: fail-closed (discard context)
        log.warning(f"PII detected: {pii_fields}")
```

**PII patterns detected:**
- `email`, `phone`, `password`, `ssn`, `credit_card`, `secret`, `token`, `api_key`

---

## Audit Integration

Every filter decision produces an audit event:

```python
# Decision
decision = FilterDecision(
    block_id="task_history",
    score=0.9,
    action="include",
    reason="score 0.9 >= threshold 0.7",
)

# Convert to audit event (immutable)
audit_event = ContextFilterAuditEvent.from_decision(decision, tenant_id="_default")
# {
#     "event_type": "context_filtered",
#     "tenant_id": "_default",
#     "block_id": "task_history",
#     "score": 0.9,
#     "action": "include",
#     "reason": "...",
#     "timestamp": "2026-09-10T12:34:56Z",
# }

# In production, audit_backend.write_event(audit_event) logs to audit.jsonl
```

---

## Testing Strategy

| Test Class | Cases | Purpose |
|---|---|---|
| **test_context_filter_static.py** | 12 | Scoring logic, thresholds, fallback |
| **test_context_filter_integration.py** | 10 | Pipeline integration, audit readiness |
| **test_context_filter_e2e.py** | 5 | Real routing scenarios, consistency |
| **test_context_filter_adversarial.py** | 15 | Security (injection, PII, poison), edge cases |
| **Total** | **42** | 0 CRITICAL/HIGH findings |

**Run all:**
```bash
pytest core/skills/tests/test_context_filter*.py -v
```

---

## Phase 2b: LLM Fallback (Coming)

**Phase 2b will add:** Optional LLM classifier for uncertain blocks (score < 0.7, size > 500 tokens).

```python
# Phase 2b signature (not yet wired):
def filter_context(
    blocks: List[ContextBlock],
    config: FilterConfig = None,
    lm_classify_fn=None,  # Phase 2b: LLM classifier
) -> Tuple[List[ContextBlock], List[FilterDecision]]:
    ...
```

---

## Must NOT Do

- ❌ Don't filter critical context (task_history) without strong signal
- ❌ Don't leak PII into audit logs
- ❌ Don't silently drop context (audit every decision)
- ❌ Don't skip fallback (prompt must never be empty)
- ❌ Don't use non-deterministic scoring in Phase 2a

---

## Related

- **ADR-0528:** Context Filter Architecture (full design)
- **ADR-0650:** Language Priority Resolver (upstream: language detection)
- **ADR-0314:** Learning Infrastructure (downstream: Phase 2b feedback loop)
- **ADR-0555/0556/0557:** Hybrid Context Model (L10 layer)

---

**Last Updated:** 2026-09-10  
**Status:** Phase 2a ✅ (Static) | Phase 2b 🚀 (LLM fallback)
