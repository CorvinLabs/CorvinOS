---
id: ADR-0327
status: proposed
depends_on: [ADR-0314, ADR-0321, ADR-0323]
related: [ADR-0322, ADR-0324, ADR-0325, ADR-0326]
supersedes: []
paths:
  - core/learning/operator_feedback.py
  - core/console/routes/feedback.py
docs:
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md
commits: []
---

# ADR-0327 — Operator Feedback Loop Integration

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Author:** Claude Code  

---

## Context

### Problem
Learning happens automatically (from tool/skill execution data), but **operators have no voice**. An operator might think:

- "This tool worked great but the cost estimate is wrong"
- "This skill is actually not useful despite high auto-grade"
- "I don't trust this tool; don't rank it so high"

**Impact:** Auto-grading and auto-ranking don't account for operator experience

### Gap
**Gap 7: Operator Feedback Loop Disconnected** — integrates human feedback into learning.

---

## Decision

### Conceptual Level
**Principle:** Operator feedback is a **first-class learning signal**. When an operator rates a tool or gives feedback, that signal should:
1. Be captured in learning events
2. Override/adjust auto-grades
3. Influence future ranking/selection

### Structural Level

**Feedback types:**
1. **Tool rating:** 1-5 stars (after execution)
2. **Skill rating:** 1-5 stars (on skill use)
3. **Strategy feedback:** "This strategy was great" / "This strategy was wrong"
4. **Free-text comments:** "Why did the tool fail?"

**Integration:**
- UI endpoints for feedback collection (Console, bridges)
- Learning events: OPERATOR_RATED_TOOL, OPERATOR_RATED_SKILL, etc
- Feedback adjuster: Adjusts skill grades based on operator ratings
- Feedback aggregator: Displays feedback history to operator

### Implementation Level

```python
class OperatorFeedbackHandler:
    """Handle operator feedback on tools/skills."""
    
    def __init__(self, hub: SubsystemHub):
        self.hub = hub
        self.event_emitter = hub.event_emitter
    
    async def rate_tool(
        self,
        tool_id: str,
        rating: int,  # 1-5
        feedback: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Record operator rating for a tool.
        
        Args:
            tool_id: Tool being rated
            rating: 1-5 stars
            feedback: Optional comment
            session_id: Session context
        """
        assert 1 <= rating <= 5, "rating must be 1-5"
        
        event = LearningEvent(
            event_type=LearningEventType.OPERATOR_RATED_TOOL,
            tenant_id=self.hub.tenant_id,
            instance_id=self.hub.instance_id,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            payload={
                "tool_id": tool_id,
                "rating": rating,
                "feedback": feedback or "",
            },
            tags=["operator_feedback"],
        )
        
        await self.event_emitter.emit(event)
    
    async def rate_skill(
        self,
        skill_id: str,
        rating: int,  # 1-5
        feedback: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Record operator rating for a skill."""
        assert 1 <= rating <= 5, "rating must be 1-5"
        
        event = LearningEvent(
            event_type=LearningEventType.OPERATOR_RATED_SKILL,
            tenant_id=self.hub.tenant_id,
            instance_id=self.hub.instance_id,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            payload={
                "skill_id": skill_id,
                "rating": rating,
                "feedback": feedback or "",
            },
            tags=["operator_feedback"],
        )
        
        await self.event_emitter.emit(event)


class FeedbackAdjuster:
    """Adjust skill grades based on operator feedback."""
    
    def __init__(self, skill_forge: SkillForgeSubsystem):
        self.skill_forge = skill_forge
    
    async def on_operator_rated_skill(self, event_name: str, event_data: dict):
        """Handle OPERATOR_RATED_SKILL event.
        
        Adjust skill grade based on operator rating:
        - 5 stars: +0.5 boost
        - 4 stars: +0.25 boost
        - 3 stars: no adjustment
        - 2 stars: -0.25 penalty
        - 1 star: -0.5 penalty
        """
        payload = event_data["payload"]
        skill_id = payload["skill_id"]
        rating = payload["rating"]
        
        # Map rating to grade adjustment
        adjustment_map = {
            5: 0.5,
            4: 0.25,
            3: 0.0,
            2: -0.25,
            1: -0.5,
        }
        
        grade_delta = adjustment_map.get(rating, 0.0)
        
        # Apply to skill
        await self.skill_forge._grade_skill(
            skill_id=skill_id,
            score_delta=grade_delta,
            reason=f"Operator feedback ({rating} stars)",
        )
    
    async def on_operator_rated_tool(self, event_name: str, event_data: dict):
        """Handle OPERATOR_RATED_TOOL event.
        
        Doesn't directly grade (tools aren't auto-graded like skills).
        But can adjust tool ranking weight or alert operator.
        """
        payload = event_data["payload"]
        tool_id = payload["tool_id"]
        rating = payload["rating"]
        feedback = payload.get("feedback", "")
        
        # Could store rating in tool metadata for future ranking
        # Or alert if rating < 3 (tool is problematic)
        
        if rating < 3:
            logger.warning(f"Tool {tool_id} rated poorly by operator: {feedback}")
```

**Console API endpoints:**

```python
# routes/feedback.py

@app.post("/api/feedback/rate-tool")
async def rate_tool(tool_id: str, rating: int, feedback: Optional[str] = None):
    """Rate a tool (1-5 stars)."""
    handler = OperatorFeedbackHandler(hub)
    await handler.rate_tool(
        tool_id=tool_id,
        rating=rating,
        feedback=feedback,
    )
    return {"ok": True}


@app.post("/api/feedback/rate-skill")
async def rate_skill(skill_id: str, rating: int, feedback: Optional[str] = None):
    """Rate a skill (1-5 stars)."""
    handler = OperatorFeedbackHandler(hub)
    await handler.rate_skill(
        skill_id=skill_id,
        rating=rating,
        feedback=feedback,
    )
    return {"ok": True}
```

---

## Consequences

### Positive
✅ **Operator input matters:** Feedback adjusts grades  
✅ **Auto-correction:** Operator can fix bad auto-grades  
✅ **Trust:** Operator sees their feedback is acted on  
✅ **Learning improvement:** More training signals (auto-data + human feedback)  

### Negative
⚠️ **UI integration required:** Need rating UI in Console/bridges  
⚠️ **Latency:** Feedback adjusts grades asynchronously (not immediate)  

### Risks & Mitigation

**Risk 1: One bad rating tanks a skill**
- Mitigation: Sample size threshold (ignore if < 10 total samples)
- Recommendation: Weight operator ratings less than auto-grades if needed

**Risk 2: Operator feedback is adversarial (intentional downrating)**
- Mitigation: Log all feedback; audit trail visible
- Recommendation: Add feedback moderation (future work)

**Risk 3: Feedback UI not built (Gate 7 incomplete)**
- Mitigation: Feedback handler is standalone; UI can be added later
- Fallback: Rate via CLI or API for testing

---

## Alternative Implementations

**Option A: Immediate feedback (synchronous)**
- Pro: Instant feedback visible
- Con: Blocks operator input (latency)

**Option B: Batch feedback (collect, process hourly)**
- Pro: Efficient aggregation
- Con: Operator doesn't see immediate effect

**Decision:** Asynchronous (current), non-blocking; feedback processed within minutes

---

## Implementation Plan

### Phase 5 (Final Phase): Operator Feedback (Days 37–40)
- [ ] Implement `OperatorFeedbackHandler` (event emission)
- [ ] Implement `FeedbackAdjuster` (skill grade adjustments)
- [ ] Console API endpoints (/api/feedback/rate-tool, /api/feedback/rate-skill)
- [ ] Unit tests (10+ cases): feedback emission, grade adjustment
- [ ] E2E test: Operator rates skill, grade adjusts
- [ ] Feature flag: `learning_gap_7_operator_feedback` (default: false)

### Deferred (Future Gates)
- [ ] UI components (star rating, feedback form) in Console
- [ ] Bridge integration (rating prompts in Slack/Discord)
- [ ] Feedback moderation (flag controversial feedback)
- [ ] Feedback aggregation dashboard

---

## Metrics & Success Criteria

### Phase 5 Success
- [ ] OPERATOR_RATED_TOOL and OPERATOR_RATED_SKILL events emitted
- [ ] FeedbackAdjuster adjusts skill grades per rating
- [ ] Audit trail logs all feedback
- [ ] E2E: Rate skill (5 stars) → grade adjusts +0.5

### Phase 6+ Unblocks
- Bridge operators can rate tools/skills inline (Gap 7b)
- Feedback analytics dashboard (observability)

---

## References

- ADR-0314: Learning Infrastructure
- ADR-0321: Tool Execution Events
- ADR-0323: Skill Attribution (integrates feedback into grading)
- ADR-0327b (Future): UI Components for Feedback Collection

---

**Status:** PROPOSED  
**Next:** Implement after Gaps 1–6 stabilize (final phase)

---

## Summary: All 7 Gaps

| Gap | Title | Status | Blockers | Unblocks |
|-----|-------|--------|----------|----------|
| 1 | Tool Execution Learning Events | PROPOSED (ADR-0321) | None | 2, 3, 4, 5, 6 |
| 2 | Tool Performance Ranking | PROPOSED (ADR-0322) | Gap 1, 4 | Tools selected data-driven |
| 3 | Skill Attribution | PROPOSED (ADR-0323) | Gap 1, 4 | Fair skill grading |
| 4 | Performance Aggregation | PROPOSED (ADR-0324) | Gap 1 | 2, 3, 5, 6 |
| 5 | Context Coherence | PROPOSED (ADR-0325) | Gap 1, 2 | Cross-session learning |
| 6 | Cost Learning | PROPOSED (ADR-0326) | Gap 1, 4 | Cost-optimized selection |
| 7 | Operator Feedback | PROPOSED (ADR-0327) | None | Human feedback integrated |

---

**Phase 1 (Foundation):** Gap 1  
**Phase 2 (Application):** Gaps 2, 3, 4  
**Phase 3 (Optimization):** Gaps 5, 6  
**Phase 4 (Feedback loop):** Gap 7  

**Total effort:** 5 engineers, 8 weeks (or 1 engineer, 40 weeks sequentially)
