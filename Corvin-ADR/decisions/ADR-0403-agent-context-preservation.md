---
id: ADR-0403
status: PROPOSED
depends_on: [ADR-0401, ADR-0300, ADR-0297]
relates_to: [ADR-0399 (context-pipeline-v2)]
paths:
  - core/agent/session_context.py
  - core/agent/memory_gating.py
docs:
  - docs/architecture/agent-context-preservation.md
---

# ADR-0403: Agent Context Preservation Protocol

## Problem

**Agent loses session context mid-session.** Corvin loads Memory, Memory overrides user's explicit request, task diverges from intent.

Example: User says "Week 3-4", Memory suggests "ADR-0267 (Week 1)", Corvin implements Week 1 instead.

## Root Cause

1. **No Priority Ordering:** Session intent and Memory context treated equally
2. **Silent Override:** Memory laden happens after session start, replaces intent
3. **No Conflict Declaration:** When Session ≠ Memory, no explicit signal to user

## Solution

**Two-Layer Agent Context Model (analogous to ADR-0401):**

### Layer 1: SessionContext (Immutable, Frozen)

```python
@dataclass(frozen=True)
class SessionContext:
    """User's current request - NEVER OVERWRITTEN BY MEMORY."""
    user_request: str              # "Week 3-4", "mach all das production ready"
    explicit_constraints: Dict     # "100% rollout, kein canary"
    task_scope: str                # "task orchestration", "adversarial review"
    session_timestamp: datetime
    
    def is_authoritative_on(self, topic: str) -> bool:
        """Session wins on any topic explicitly mentioned by user."""
        keywords = self.user_request.lower().split()
        return topic.lower() in keywords or any(
            topic.lower() in kw for kw in keywords
        )
```

**Invariant:** Frozen at session start. NO memory can override.

### Layer 2: MemoryContext (Additive, Argumentative)

```python
@dataclass
class MemoryContext:
    """Prior learnings - ONLY ARGUE, NEVER REPLACE."""
    related_adrs: List[str]        # ADR-0401, 0402 (supporting evidence)
    prior_findings: List[str]      # "was in audit" (context)
    architectural_patterns: List
    
    def can_augment(self, session_context: SessionContext, topic: str) -> bool:
        """Memory can ONLY augment if session doesn't own this topic."""
        return not session_context.is_authoritative_on(topic)
```

**Rule:** Memory is advisory. Arguments only. No replacement.

### Conflict Resolution

```python
async def resolve_context_conflict(session: SessionContext, 
                                    memory: MemoryContext,
                                    topic: str) -> str:
    """
    When Session and Memory diverge, declare conflict explicitly.
    User decides (or Session wins by default).
    """
    if session.is_authoritative_on(topic):
        # Session owns this topic
        reason = f"User explicitly mentioned: {topic}"
        return f"SESSION (authoritative): {session.user_request} [{reason}]"
    
    if memory.can_augment(session, topic):
        # Memory can argue
        return f"MEMORY (argues): {memory.prior_findings} [Session doesn't override]"
    
    # No opinion
    return "No context available for this topic"
```

## Implementation

1. **SessionContextLock** (~50 LoC): Freeze user intent at session start
2. **MemoryGate** (~40 LoC): Require authority check before using memory
3. **ConflictDeclaration** (~30 LoC): Explicit output when Session ≠ Memory
4. **Tests** (~60 LoC): Verify authority ordering, conflict detection

## Trade-offs

✅ Session intent preserved across session lifetime  
✅ Memory becomes evidence-based, not prescriptive  
✅ User always sees why Corvin made a choice  
❌ Slightly more verbose output (conflicts declared)  
❌ Memory has limited reach (only argumentative)

## Verification

- Unit tests: SessionContext authority detection
- Integration tests: Memory gating on actual Memory load
- E2E tests: Multi-turn session, user explicitly corrects course, session respects correction

## Related

- **ADR-0401:** Two-layer context at task level (Original frozen + Pipeline additive)
- **ADR-0300:** Dual Gate Context Pipeline (session-level context decisions)
- **ADR-0297:** PII Detection Fail Closed (Memory must never load PII into SessionContext)
