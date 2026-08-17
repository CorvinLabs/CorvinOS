# TreeOfThoughts Implementation Plan

**Timeline:** 12 weeks (3 months), starting Week 1 of Q4 2026  
**Team:** 1 architect (design) + 2 engineers (implementation) + 1 QA  
**Budget:** ~600 engineering hours  
**Complexity:** HIGH (refactors 4 layers into 1, affects core learning loop)

---

## Phase Breakdown

### Phase 1: Core Data Model & Persistence (Weeks 1-2)

**Deliverables:**
- `core/learning/models.py`: TreeNode, LearningEvent, ConfidenceEvent dataclasses
- `core/learning/storage.py`: FileBackedEventStore (JSON, date-partitioned like audit logs)
- `core/learning/confidence.py`: Bayesian confidence update logic
- Tests: 20+ unit tests for confidence math

**Specification:**

```python
# core/learning/models.py

@dataclass(frozen=True)
class ConfidenceEvent:
    """Immutable record of a confidence change."""
    timestamp: ISO8601
    old_confidence: float
    new_confidence: float
    delta: float  # new - old
    event_type: str  # "used" | "failed" | "graded" | "refuted" | "decay"
    reason: str  # human-readable explanation
    context: dict  # task_id, user_id, metrics, etc.

@dataclass(frozen=True)
class TreeNode:
    id: str
    level: Literal["pattern", "method", "framework"]
    name: str
    body: str
    when: list[str]
    anti_when: list[str]
    children: list[str] = None
    e2e_tests: list[str] = None
    metrics: dict = None
    adr_link: str = None
    confidence: float = 0.5  # default
    confidence_history: list[ConfidenceEvent] = None
    operator_notes: list[tuple[ISO8601, str, str]] = None  # (date, author, text)
    created_at: ISO8601 = None
    modified_at: ISO8601 = None

class LearningEventStore:
    """Append-only event log, date-partitioned."""
    
    def append_event(self, subject_id: str, event: LearningEvent) -> None:
        # Write to ~/.corvin/learning/events/2026-08-17.jsonl
        # Update subject's confidence_history in-memory
        # Trigger confidence recalculation (async)
        pass
    
    def get_node(self, node_id: str) -> TreeNode:
        # Reconstruct from base def + all confidence events
        pass
    
    def get_events(self, node_id: str, after: ISO8601 = None) -> list[LearningEvent]:
        # For audit trail, operator notes
        pass
```

**Tests:**
```python
def test_confidence_update_bayesian():
    """New evidence blends with prior (70/30 split)."""
    node = TreeNode(id="pattern_x", confidence=0.5)
    event = LearningEvent(..., confidence_delta=+0.3)
    new_conf = update_confidence(node, event)
    assert new_conf == 0.5 * 0.7 + (0.5 + 0.3) * 0.3  # 0.59

def test_confidence_antipattern_penalty():
    """Using pattern in anti_when context → -0.3."""
    node = TreeNode(id="pattern_retry", anti_when=["auth_failures"])
    event = LearningEvent(..., event_type="antipattern_detected", 
                         reason="auth_failures")
    # Internally sets event.confidence_delta = -0.3
    new_conf = update_confidence(node, event)
    assert new_conf < node.confidence - 0.2  # strong penalty

def test_confidence_decay():
    """Unused pattern loses 0.1/day confidence."""
    node = TreeNode(id="pattern_unused", confidence=0.8)
    now = "2026-08-24"
    last_used = "2026-08-17"  # 7 days ago
    decayed = apply_decay(node.confidence, days=7, decay_rate=0.1)
    assert decayed == 0.8 - (7 * 0.1)  # 0.1

def test_hierarchical_aggregation():
    """Method confidence = weighted avg of children."""
    method = TreeNode(
        id="method_voice",
        children=["pattern_retry", "pattern_timeout", "pattern_fallback"],
        confidence=0.0  # to be computed
    )
    child_confs = {"pattern_retry": 0.8, "pattern_timeout": 0.85, "pattern_fallback": 0.9}
    agg_conf = aggregate_children(method, child_confs)
    assert agg_conf == (0.8 + 0.85 + 0.9) / 3  # 0.85
```

**Acceptance Criteria:**
- All 20+ confidence tests pass
- Storage roundtrip works (write event → read node with updated confidence)
- No data loss on concurrent appends (flock-based locking)

---

### Phase 2: Reachability Proof & E2E Integration (Weeks 3-4)

**Deliverables:**
- Scan existing `Concepts` repo and convert to `Framework` definitions (ADR-linked)
- Scan existing `Skills` and convert to `Pattern` definitions
- Create `@e2e_for(pattern_id)` decorator for E2E tests
- Metrics collector: latency, cost, success_rate, calls_in_production
- Decay monitor: warn if unused for >7 days

**Specification:**

```python
# core/learning/decorators.py

def e2e_for(pattern_id: str):
    """Mark a test as E2E proof for a pattern."""
    def decorator(test_func):
        test_func._e2e_for = pattern_id
        return test_func
    return decorator

# tests/test_voice_tts_retry.py
@e2e_for("pattern_retry_backoff_exponential")
def test_openai_tts_with_429_retry():
    """Prove retry-backoff works in production context."""
    # Real API call, not mock
    response = say.py("test.opus", "test", "de", "alloy", "openai")
    assert response.success
    assert response.retries >= 1

# core/learning/reachability.py

class ReachabilityMonitor:
    """Track E2E tests and production usage."""
    
    def scan_tests(self):
        """Find all @e2e_for(...) decorated tests."""
        # Scan tests/ for decorator
        # Map pattern_id → test file
        pass
    
    def track_production_call(self, pattern_id: str, metrics: dict):
        """Record: pattern was actually used in production."""
        # metrics = {latency_ms, cost_tokens, success, context}
        # Emit LearningEvent(event_type="used", ...)
        pass
    
    def check_coverage():
        """Ensure every pattern has E2E test + production usage."""
        for pattern in all_patterns:
            assert pattern.id in self.e2e_tests, f"No E2E for {pattern.id}"
            assert pattern.calls_in_production > 0, f"Never used: {pattern.id}"
```

**Conversion Template:**

```yaml
# OLD: docs/concepts/0008-retry-backoff.md (Concept)
# NEW: core/learning/patterns/retry_backoff.yaml

pattern:
  id: pattern_retry_backoff_exponential
  level: pattern
  name: Exponential Backoff Retry
  when:
    - "API rate-limits (429)"
    - "transient network errors"
    - "temporary timeouts"
  anti_when:
    - "user input validation"
    - "auth failures (would reveal password)"
    - "cache misses (would overload cache)"
  
  body: |
    for attempt in range(max_retries):
      try:
        return api_call()
      except RateLimitError:
        wait_time = 2 ** attempt
        time.sleep(wait_time)
    return fallback()
  
  e2e_tests:
    - tests/test_voice_tts_retry.py::test_openai_tts_with_429_retry
  
  adr_link: "ADR-0351"  # links to the decision that created this
  
  operator_notes:
    - date: 2026-08-17
      author: shumway
      text: "Fixed timeout bounds: was 3s/6s/12s (21s total, exceeded budget). Now 1s/2s/4s (7s, safe)."
```

**Metrics Collection:**

```python
# core/learning/metrics.py

@dataclass
class ExecutionMetrics:
    pattern_id: str
    latency_ms: float
    cost_tokens: int
    success: bool
    error_type: str | None
    context: dict  # {task_id, user_id, stage, outcome}

class MetricsCollector:
    def record(self, metrics: ExecutionMetrics):
        """Emit LearningEvent + update pattern metrics."""
        if metrics.success:
            event = LearningEvent(
                event_type="used",
                confidence_delta=+0.05,
                reason="succeeded in production"
            )
        else:
            event = LearningEvent(
                event_type="failed",
                confidence_delta=-0.1,
                reason=f"failed with {metrics.error_type}"
            )
        
        self.event_store.append_event(metrics.pattern_id, event)
```

**Acceptance Criteria:**
- All Concepts converted to Frameworks (3-5 frameworks)
- All Skills converted to Patterns (10-20 patterns)
- E2E test decorator working on 5+ tests
- Metrics collected for 3+ patterns in staging
- Decay warnings appear for unused patterns

---

### Phase 3: Active Learning Loop (Weeks 5-6)

**Deliverables:**
- Auto-emit LearningEvent after each Method execution
- Confidence decay monitor (daily batch job)
- Auto-suggest engine (when confidence drops below 0.5)
- Anti-pattern violation detector (context-aware)

**Specification:**

```python
# core/learning/active_loop.py

class ActiveLearningLoop:
    """Closed-loop: exec → metrics → confidence update."""
    
    async def execute_method(self, method_id: str, *args, **kwargs) -> Result:
        """
        1. Resolve Method (and its Patterns)
        2. Execute
        3. Collect metrics
        4. Emit LearningEvent
        5. Update confidence
        6. Auto-suggest if confidence drops
        """
        method = self.store.get_node(method_id)
        start = time.time()
        
        try:
            result = await self._run_method(method, *args, **kwargs)
            success = result.success
            error_type = None
        except Exception as e:
            success = False
            error_type = type(e).__name__
            result = Result(success=False, error=str(e))
        
        # Metrics
        latency_ms = (time.time() - start) * 1000
        cost_tokens = result.tokens_used or 0
        
        # Learning event
        delta = +0.1 if success else -0.15
        reason = "succeeded" if success else f"failed: {error_type}"
        
        event = LearningEvent(
            subject_id=method_id,
            event_type="used" if success else "failed",
            confidence_delta=delta,
            reason=reason,
            context={
                "task_id": kwargs.get("task_id"),
                "user_id": kwargs.get("user_id"),
                "latency_ms": latency_ms,
                "cost_tokens": cost_tokens,
                "error_type": error_type,
            }
        )
        
        self.store.append_event(method_id, event)
        
        # Check: did this method use any patterns in anti_when context?
        for pattern_id in method.children:
            pattern = self.store.get_node(pattern_id)
            if self._detect_antipattern_usage(pattern, kwargs):
                anti_event = LearningEvent(
                    subject_id=pattern_id,
                    event_type="antipattern_detected",
                    confidence_delta=-0.3,
                    reason=f"used in anti_when context: {kwargs}"
                )
                self.store.append_event(pattern_id, anti_event)
                self.console.warn(f"⚠️ Antipattern: {pattern_id} in wrong context")
        
        # Auto-suggest
        new_conf = self.store.get_node(method_id).confidence
        if new_conf < 0.5:
            suggestions = self._find_alternatives(method_id, confidence_threshold=0.7)
            if suggestions:
                self.console.suggest(
                    f"Confidence of {method_id} dropped to {new_conf:.2f}. "
                    f"Consider: {suggestions}"
                )
        
        return result

class ConfidenceDecayMonitor:
    """Daily batch: penalize unused patterns."""
    
    async def daily_decay(self):
        """For each pattern: if unused >7 days, confidence -= 0.1."""
        for pattern in self.store.all_nodes():
            last_event = self.store.get_events(pattern.id, limit=1)
            if not last_event:
                continue
            
            days_unused = (now() - last_event[0].timestamp).days
            if days_unused > 7:
                decay_amount = 0.1 * (days_unused // 7)  # 0.1 per week
                event = LearningEvent(
                    subject_id=pattern.id,
                    event_type="decay",
                    confidence_delta=-decay_amount,
                    reason=f"unused for {days_unused} days"
                )
                self.store.append_event(pattern.id, event)
```

**Anti-Pattern Detector:**

```python
# core/learning/antipattern.py

class AntiPatternDetector:
    """Context-aware: is this pattern being used wrong?"""
    
    def detect(self, pattern: TreeNode, usage_context: dict) -> bool:
        """Returns True if pattern is in anti_when context."""
        # Context might be: "user_input_validation" or "auth_flow"
        for anti in pattern.anti_when:
            if anti in usage_context.values():
                return True
        return False

# Example usage in say.py
detector = AntiPatternDetector()
if detector.detect(retry_pattern, context={"stage": "auth_login"}):
    # Emit antipattern violation event
    event = LearningEvent(..., event_type="antipattern_detected")
```

**Acceptance Criteria:**
- LearningEvent auto-emitted after 5+ Method executions
- Decay monitor runs daily, confidence drops for unused patterns
- Auto-suggest appears in console when confidence < 0.5
- Antipattern detection fires for 2+ test cases

---

### Phase 4: Console Dashboard (Weeks 7-8)

**Deliverables:**
- TreeOfThoughts dashboard UI (React component)
- Settings panel (confidence thresholds, decay rate, etc.)
- Inline grading ("👍 / 👎")
- Weekly report generator

**Specification:**

```tsx
// core/console/routes/learning.tsx

export function LearningDashboard() {
  const [tree, setTree] = useState(null)
  const [selectedNode, setSelectedNode] = useState(null)
  
  return (
    <div className="learning-dashboard">
      <h1>TreeOfThoughts</h1>
      
      {/* Confidence Tree */}
      <TreeView
        node={tree}
        onSelect={setSelectedNode}
        sortBy="confidence"
        filters={["confidence > 0.7", "used_in_7d"]}
      />
      
      {/* Detail Panel */}
      {selectedNode && (
        <DetailPanel node={selectedNode}>
          {/* Confidence gauge */}
          <ConfidenceGauge value={selectedNode.confidence} />
          
          {/* Inline grading */}
          <div className="feedback">
            <button onClick={() => grade(+1)}>👍 Worked well</button>
            <button onClick={() => grade(0)}>😐 Neutral</button>
            <button onClick={() => grade(-1)}>👎 Didn't work</button>
          </div>
          
          {/* Operator notes (append-only) */}
          <OperatorNotes notes={selectedNode.operator_notes} />
          
          {/* Metrics */}
          <MetricsCard metrics={selectedNode.metrics} />
          
          {/* ADR link */}
          {selectedNode.adr_link && (
            <a href={`/docs/adr/${selectedNode.adr_link}`}>
              See ADR: {selectedNode.adr_link}
            </a>
          )}
        </DetailPanel>
      )}
    </div>
  )
}
```

**Settings:**

```tsx
export function LearningSettings() {
  const [settings, setSettings] = useState({
    confidence_decay_days: 7,
    confidence_decay_rate: 0.1,
    auto_suggest_threshold: 0.5,
    antipattern_alert: true,
    antipattern_block: false,
    track_all_events: true,
  })
  
  return (
    <form>
      <label>
        Decay unused patterns after:
        <input type="number" value={settings.confidence_decay_days} />
        days
      </label>
      
      <label>
        Decay rate per period:
        <input type="number" step="0.05" value={settings.confidence_decay_rate} />
      </label>
      
      <label>
        Auto-suggest when confidence drops below:
        <input type="number" step="0.05" value={settings.auto_suggest_threshold} />
      </label>
      
      <label>
        <input type="checkbox" checked={settings.antipattern_alert} />
        Alert on antipattern usage
      </label>
      
      <label>
        <input type="checkbox" checked={settings.antipattern_block} />
        Block antipattern usage (hard-fail)
      </label>
      
      <button type="submit">Save Settings</button>
    </form>
  )
}
```

**Acceptance Criteria:**
- Dashboard loads in <2s
- Can view all 3 levels (drill-down)
- Inline grading emits LearningEvent
- Operator can edit settings
- Weekly report email works

---

### Phase 5: Operator Notes & Audit (Weeks 9-10)

**Deliverables:**
- Append-only operator notes UI
- Full audit trail export (CSV/JSON)
- ADR integration (link patterns → ADRs)

**Specification:**

```python
# core/learning/audit.py

class OperatorNoteManager:
    """Append-only, versioned operator notes."""
    
    def add_note(self, node_id: str, text: str, author: str):
        """Append immutable note to node's history."""
        note = (now(), author, text)
        # Stored in node.operator_notes list (append-only)
        # Never edited or deleted
        pass
    
    def export_audit_trail(self, start: ISO8601, end: ISO8601) -> CSV:
        """Export all LearningEvents + OperatorNotes for a date range."""
        # CSV columns: timestamp, node_id, event_type, confidence_delta,
        #              reason, context, author (if note), note_text
        pass
```

**Console UI:**

```tsx
export function OperatorNotes({ nodeId }) {
  const [notes, setNotes] = useState([])
  const [newNote, setNewNote] = useState("")
  
  const handleAddNote = async () => {
    await api.post(`/learning/${nodeId}/notes`, {
      text: newNote,
      author: user.id,
    })
    setNewNote("")
    // Refresh notes
  }
  
  return (
    <div className="operator-notes">
      <h3>Operator Notes (append-only)</h3>
      
      <div className="notes-list">
        {notes.map((note, i) => (
          <div key={i} className="note">
            <p className="meta">{note.date} • {note.author}</p>
            <p className="text">{note.text}</p>
            {/* NO EDIT/DELETE buttons — append-only */}
          </div>
        ))}
      </div>
      
      <textarea
        placeholder="Add a note (e.g., why this pattern is important, gotchas, ...)"
        value={newNote}
        onChange={e => setNewNote(e.target.value)}
      />
      <button onClick={handleAddNote}>Add Note</button>
    </div>
  )
}
```

**Acceptance Criteria:**
- Operator notes immutable (no edit/delete)
- Audit export contains all events
- ADR links functional
- Export includes context (task_id, user_id, metrics)

---

### Phase 6: Documentation & Migration (Weeks 11-12)

**Deliverables:**
- Migrate 5+ Concepts to Frameworks
- Migrate 10+ Skills to Patterns
- Rewrite ADR-0314 (Learning Infrastructure) to reference TreeOfThoughts
- docs-as-definition-of-done pass

**Acceptance Criteria:**
- All existing Concepts/Skills/Metaphers converted
- No orphaned references (all children have parents)
- Zero confidence regressions from old to new system
- Docs fully updated

---

## ADR References

Three ADRs will formalize this:

- **ADR-0365:** TreeOfThoughts: 3-Level Learning Hierarchy
- **ADR-0366:** Reachability Proof & E2E Integration
- **ADR-0367:** Console Dashboard & Active Learning Loop

---

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Storage grows unbounded (millions of events) | High | Medium | Implement Parquet archival after 6 months of events |
| Confidence decay math is wrong | Medium | High | Extensive unit tests + A/B test old vs new system |
| Antipattern detection too noisy (false positives) | High | Low | Add "override" mechanism, track false positives |
| Console dashboard slow (lots of nodes) | Medium | Medium | Implement pagination, lazy-load detail panels |
| Operator burden (maintaining notes) | Low | Low | Make notes truly optional; auto-generate from events if needed |

---

## Success Metrics

After 12 weeks:

✅ **Zero data loss** (immutable event log)  
✅ **Confidence accuracy** (patterns with high confidence have high success rate in production)  
✅ **Learning signal** (confidence changes when patterns succeed/fail)  
✅ **Adoption** (agents use TreeOfThoughts recommendations)  
✅ **Operator visibility** (can see why patterns have certain confidence)  
✅ **E2E coverage** (100% of patterns have E2E tests + production usage)

