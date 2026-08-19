---
id: ADR-0325
status: proposed
depends_on: [ADR-0314, ADR-0321, ADR-0322]
related: [ADR-0324, ADR-0326, ADR-0327]
supersedes: []
paths:
  - core/learning/context_coherence.py
  - core/orchestration/subsystems/context_bridge.py
docs:
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md
commits: []
---

# ADR-0325 — Context Coherence for Cross-Session Learning

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Author:** Claude Code  

---

## Context

### Problem
Learning signals (tool success, skill performance) are scoped to a single session. When a session ends and a new one begins, **learned context is lost**:

- Tool A worked great in Session 1; Session 2 doesn't know this
- Skill B was ranked #1 for "code" tasks; next session forgets
- Cost estimates from Session 1 don't improve Session 2's decisions

**Impact:** No convergence across sessions; each session is isolated

### Gap
**Gap 5: Context Coherence Not Applied** — prevents learning from carrying forward across sessions.

---

## Decision

### Conceptual Level
**Principle:** Learning persists across sessions. When resuming a session or starting a new one with similar context, **inherited strategies/tools/skills should be ranked higher** because they worked before.

### Structural Level

**Context Coherence Model:**
1. **Parent session:** Previous session with similar task context
2. **Inheritance:** Propagate top-ranked tools/skills from parent
3. **Conflict resolution:** If parent recommends Tool A but current session suggests Tool B, use score-weighted blend
4. **Freshness:** Older parent sessions (>24 hours) are deprioritized

**Data structure:**
```python
@dataclass(frozen=True)
class SessionContext:
    session_id: str
    parent_session_id: Optional[str]  # Previous session
    task_type: str
    task_id: Optional[str]
    checkpoint_id: Optional[str]  # Resume point
    inherited_tools: list[str]  # Tools from parent
    inherited_skills: list[str]  # Skills from parent
    created_at: datetime
    last_checkpoint: Optional[datetime]
```

**Integration:**
- On session start: Query for parent sessions (same task_type, recent)
- On tool selection: Blend parent recommendations with current ranking
- On skill grading: Boost skills that were successful in parent session

### Implementation Level

```python
class ContextCoherenceManager:
    """Manages learning coherence across sessions."""
    
    def __init__(self, event_store: EventStore):
        self.event_store = event_store
    
    async def find_parent_session(
        self,
        task_type: str,
        tenant_id: str = "_default",
        max_age_hours: int = 24,
    ) -> Optional[str]:
        """Find most recent parent session with same task_type."""
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        
        # Query for SESSION_CREATED events with matching task_type
        events = await self.event_store.query_events(
            event_type=LearningEventType.SESSION_CREATED,
            tenant_id=tenant_id,
            filter_fn=lambda e: (
                e["payload"].get("task_type") == task_type and
                datetime.fromisoformat(e["timestamp"]) >= cutoff
            ),
        )
        
        if not events:
            return None
        
        # Return most recent
        latest = max(events, key=lambda e: e["timestamp"])
        return latest["payload"]["session_id"]
    
    async def get_inherited_tools(
        self,
        parent_session_id: str,
        limit: int = 5,
    ) -> list[str]:
        """Get top tools from parent session."""
        # Query for TOOL_EXECUTED events in parent session
        # Rank by success rate
        # Return top-N tool IDs
        pass
    
    async def get_inherited_skills(
        self,
        parent_session_id: str,
        limit: int = 5,
    ) -> list[str]:
        """Get top skills from parent session."""
        # Query for SKILL_USED events in parent session
        # Rank by success rate
        # Return top-N skill IDs
        pass
    
    async def blend_tool_rankings(
        self,
        current_ranked: list[RankedTool],
        inherited_tools: list[str],
        parent_weight: float = 0.3,
    ) -> list[RankedTool]:
        """Blend current and parent tool rankings.
        
        Args:
            current_ranked: Tools ranked by current session data
            inherited_tools: Tools from parent session
            parent_weight: How much to weight parent (0.0-1.0)
        
        Returns:
            Reranked tools with parent preference boosted
        """
        # For each current ranked tool:
        #   If in inherited_tools: boost score by (1 - parent_weight) * boost_factor
        #   Else: keep score as-is
        # Re-sort
        
        boosted = []
        for tool in current_ranked:
            if tool.tool_id in inherited_tools:
                new_score = tool.score + (1 - parent_weight) * 0.2  # +0.2 boost
            else:
                new_score = tool.score
            
            boosted.append(tool.with_score_update(score=new_score))
        
        # Re-rank
        boosted.sort(key=lambda t: t.score, reverse=True)
        return boosted
```

---

## Consequences

### Positive
✅ **Cross-session learning:** Tools/skills improve over time across sessions  
✅ **Coherence:** Similar task contexts converge on same solutions  
✅ **Operator continuity:** "This tool worked last time" is recoverable  

### Negative
⚠️ **Stale context:** Parent session might be outdated (handled: max_age_hours)  
⚠️ **False coherence:** Tool good in Session 1 might be bad in Session 2 (handled: tunable parent_weight)  

### Risks & Mitigation

**Risk 1: Parent session too old (24+ hours)**
- Mitigation: max_age_hours parameter; operators can tune
- Default: 24 hours (conservative)

**Risk 2: Wrong parent session matched (different subtask types)**
- Mitigation: Match on task_type + optional error_class
- Recommendation: Add error_class filtering for finer granularity

**Risk 3: Parent recommendation conflicts with current data**
- Mitigation: parent_weight (0.3 default) = 30% inheritance boost
- Example: Current ranks [A, B], Parent recommends [B, C] → boost B by 0.2

---

## Implementation Plan

### Phase 4 (Parallel with Gaps 4/6): Context Coherence (Days 33–36)
- [ ] Implement `ContextCoherenceManager`
- [ ] Implement `find_parent_session()`, `get_inherited_tools()`, `get_inherited_skills()`
- [ ] Implement ranking blend logic
- [ ] Integration with ToolRankingManager (boost parent tools)
- [ ] Unit tests (8+ cases): parent finding, tool blending, age filtering
- [ ] Feature flag: `learning_gap_5_context_coherence` (default: false)

---

## References

- ADR-0314: Learning Infrastructure
- ADR-0322: Tool Ranking (accepts blended rankings)
- ADR-0323: Skill Attribution (can use inherited skills)

---

**Status:** PROPOSED  
**Next:** Implement after Gap 4 stabilizes
