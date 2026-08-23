# Vibe Engineering Platform: Phase 4-5 Specification & ADR Draft

**Status:** Design Ready (Phase 1-3 foundation established via IDEA-0001, CEL built but not wired)  
**Scope:** Phase 4 (Wire CEL + Talent UI) + Phase 5 (Pluggable stages + Graph visualization)  
**Timeline:** 3-4 weeks (Phase 4: 1.5w, Phase 5: 2w)  
**Persona:** Coder (pragmatic, implementation-focused, testable code)

---

## Context: What Already Exists

**Backend (CEL — Context Engineering Layer):**
- ✅ `operator/context_engineering/` (ADR-0269) — modular stages: memory_lookup, graph_traversal, skill_injection, adr_loader
- ✅ Talent Score (`talent_score.py`) — accuracy, learning_rate, variety, efficiency metrics
- ✅ Learning Queue (`learning_queue.py`) — feedback event loop
- ✅ Uncertainty Tiers (HIGH/MEDIUM/LOW/UNCERTAIN) — confidence badges

**Frontend (Console):**
- ✅ Routes scaffolded (`routes/talent.py`, `routes/context_pipeline.py`)
- ✅ Pages scaffolded (`pages/talent.tsx`, `pages/context_pipeline.tsx`)
- ✅ Mock data wired (placeholder Talent scores)
- ❌ **NOT wired to real CEL telemetry** ← This is Phase 4 work
- ❌ **Graph visualization NOT implemented** ← This is Phase 5 work

---

## Phase 4: Wire CEL + Talent UI (1.5 weeks)

### 4a: Real Telemetry → Talent Score (3 days)

**Goal:** Replace mock Talent data with real CEL metrics

**Code Skeleton:**

```python
# operator/telemetry/talent_aggregator.py

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
from typing import List, Dict

@dataclass
class TalentMetrics:
    """Aggregated talent scores from CEL events."""
    accuracy: float          # % of confident decisions that were correct
    learning_rate: float    # Tasks where feedback improved next decision
    variety: float          # Range of task types handled
    efficiency: float       # Context tokens used vs benefit gained
    timestamp: str
    period: str  # '7d', '30d', 'all'

class TalentAggregator:
    """Real-time aggregation of Talent metrics from CEL audit trail."""
    
    def __init__(self, audit_path: Path = None):
        if audit_path is None:
            audit_path = Path.home() / ".corvin" / "audit.jsonl"
        self.audit_path = audit_path
    
    def compute_accuracy(self, days: int = 7) -> float:
        """Compute P(prediction correct) from CEL feedback events."""
        events = self._load_events(days)
        
        correct = sum(
            1 for e in events 
            if e.get("event_type") == "routing_feedback" 
            and e.get("operator_feedback") == "correct"
        )
        total = sum(
            1 for e in events 
            if e.get("event_type") == "routing_feedback"
        )
        
        return (correct / total * 100) if total > 0 else 0.0
    
    def compute_learning_rate(self, days: int = 7) -> float:
        """Measure how often operator feedback improves next decision."""
        events = self._load_events(days)
        
        # Find pairs: (feedback_event, next_routing_for_similar_task)
        # If next routing scores higher → learning happened
        learning_events = 0
        for i, e in enumerate(events):
            if e.get("event_type") == "routing_feedback" and e.get("operator_feedback") == "incorrect":
                # Look for next event on similar task
                for j in range(i+1, min(i+10, len(events))):
                    if self._is_similar_task(events[i], events[j]):
                        next_confidence = events[j].get("confidence", 0)
                        prev_confidence = e.get("confidence", 0)
                        if next_confidence > prev_confidence:
                            learning_events += 1
                        break
        
        return (learning_events / len(events) * 100) if events else 0.0
    
    def compute_variety(self, days: int = 7) -> float:
        """Task type diversity (0-100)."""
        events = self._load_events(days)
        task_types = set(
            e.get("task_type") for e in events 
            if e.get("event_type") == "routing_decision"
        )
        # Max 10 task types in scope
        return min(100.0, len(task_types) * 10)
    
    def compute_efficiency(self, days: int = 7) -> float:
        """Context tokens used per unit of decision quality."""
        events = self._load_events(days)
        
        total_tokens = sum(
            e.get("context_tokens", 0) for e in events
            if e.get("event_type") == "context_engineering"
        )
        correct_decisions = sum(
            1 for e in events
            if e.get("operator_feedback") == "correct"
        )
        
        if correct_decisions == 0:
            return 0.0
        
        # Lower tokens/correct = higher efficiency (normalize to 0-100)
        efficiency = 100 - min(100, (total_tokens / correct_decisions) / 10)
        return max(0, efficiency)
    
    def aggregate(self, days: int = 7) -> TalentMetrics:
        """Compute complete Talent scorecard."""
        return TalentMetrics(
            accuracy=self.compute_accuracy(days),
            learning_rate=self.compute_learning_rate(days),
            variety=self.compute_variety(days),
            efficiency=self.compute_efficiency(days),
            timestamp=datetime.utcnow().isoformat(),
            period=f"{days}d",
        )
    
    def _load_events(self, days: int) -> List[Dict]:
        """Load audit events from last N days."""
        if not self.audit_path.exists():
            return []
        
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        events = []
        with open(self.audit_path) as f:
            for line in f:
                try:
                    event = json.loads(line)
                    ts = datetime.fromisoformat(event.get("timestamp", ""))
                    if ts >= cutoff:
                        events.append(event)
                except (json.JSONDecodeError, ValueError):
                    pass
        
        return events
    
    def _is_similar_task(self, e1: Dict, e2: Dict) -> bool:
        """Check if two events are similar tasks (for learning rate)."""
        return e1.get("task_type") == e2.get("task_type")
```

**Integration Points:**
- Route: `GET /api/talent/metrics?days=7` → returns aggregated scores
- Frontend: `talent.tsx` calls endpoint, displays real metrics
- Audit trail: CEL events must include `context_tokens`, `operator_feedback`, `task_type`

**Tests:**
- Unit: `test_talent_aggregator.py` (compute_accuracy, compute_learning_rate, etc.)
- Integration: Real audit.jsonl mock data → verify scores calculated correctly
- E2E: Console Talent page loads, displays real scores (not mocks)

---

### 4b: Context Pipeline UI Skeleton (3 days)

**Goal:** Surface CEL stages visibly in console (collapsible cards per stage)

**React Component Skeleton:**

```typescript
// operator/console/pages/context_pipeline.tsx

import React, { useState } from 'react'
import { Card, Badge, Skeleton, Tabs } from '@/components/ui'

interface ContextStage {
  name: string              // 'memory_lookup', 'graph_traversal', 'skill_injection', etc.
  status: 'pending' | 'running' | 'done' | 'error'
  confidence: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNCERTAIN'
  tokens_in: number
  tokens_out: number
  sources: string[]         // ['memory#123', 'adr#0269', ...]
  output_preview: string    // First 200 chars of output
  duration_ms: number
}

interface ContextPipelineState {
  task_id: string
  stages: ContextStage[]
  total_tokens_in: number
  total_tokens_out: number
  live: boolean  // Is task still running?
}

export default function ContextPipelineTab() {
  const [pipelineState, setPipelineState] = useState<ContextPipelineState | null>(null)
  const [expandedStage, setExpandedStage] = useState<string | null>(null)

  React.useEffect(() => {
    // Poll for live pipeline updates
    const interval = setInterval(async () => {
      const res = await fetch('/api/context_pipeline/live')
      const data = await res.json()
      setPipelineState(data)
    }, 500)
    
    return () => clearInterval(interval)
  }, [])

  if (!pipelineState) {
    return <Skeleton className="w-full h-96" />
  }

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">Context Pipeline</h2>
      
      {/* Token flow summary */}
      <Card className="p-4 bg-gradient">
        <div className="flex justify-between">
          <div>
            <span className="text-sm text-muted">Input Tokens</span>
            <div className="text-2xl font-bold">{pipelineState.total_tokens_in}</div>
          </div>
          <div className="text-center">
            <span className="text-sm text-muted">Efficiency</span>
            <div className="text-2xl font-bold">
              {Math.round(
                (pipelineState.total_tokens_out / pipelineState.total_tokens_in) * 100
              )}%
            </div>
          </div>
          <div>
            <span className="text-sm text-muted">Output Tokens</span>
            <div className="text-2xl font-bold">{pipelineState.total_tokens_out}</div>
          </div>
        </div>
      </Card>

      {/* Pipeline stages as flow */}
      <div className="space-y-2">
        {pipelineState.stages.map((stage, idx) => (
          <div key={stage.name}>
            {/* Arrow between stages */}
            {idx > 0 && <div className="text-center text-muted text-lg">↓</div>}
            
            {/* Stage card */}
            <Card
              className="p-4 cursor-pointer hover:bg-accent"
              onClick={() => setExpandedStage(
                expandedStage === stage.name ? null : stage.name
              )}
            >
              <div className="flex items-center justify-between">
                <div className="flex-1">
                  <h3 className="font-semibold">{stage.name}</h3>
                  <p className="text-sm text-muted">{stage.output_preview}...</p>
                </div>
                
                <div className="flex gap-2 items-center">
                  <Badge variant={stage.status}>
                    {stage.status === 'running' ? '◉' : '✓'} {stage.status}
                  </Badge>
                  <Badge variant={stage.confidence}>
                    {stage.confidence}
                  </Badge>
                  <span className="text-sm text-muted">
                    {stage.tokens_in} → {stage.tokens_out} tokens ({stage.duration_ms}ms)
                  </span>
                </div>
              </div>

              {/* Expanded details */}
              {expandedStage === stage.name && (
                <div className="mt-4 pt-4 border-t space-y-2">
                  <div>
                    <h4 className="font-mono text-sm">Sources:</h4>
                    <ul className="text-sm text-muted">
                      {stage.sources.map(s => <li key={s}>• {s}</li>)}
                    </ul>
                  </div>
                  <div>
                    <h4 className="font-mono text-sm">Output:</h4>
                    <pre className="bg-muted p-2 text-xs overflow-auto">
                      {stage.output_preview}...
                    </pre>
                  </div>
                </div>
              )}
            </Card>
          </div>
        ))}
      </div>

      {/* Debug: show live updates */}
      {pipelineState.live && (
        <div className="text-sm text-amber-600">
          🔴 Pipeline running... (updating every 500ms)
        </div>
      )}
    </div>
  )
}
```

**Integration Points:**
- Backend API: `GET /api/context_pipeline/live` → returns current ContextPipelineState
- CEL must emit events to audit.jsonl with stage-level telemetry
- Console nav: Add "Context Pipeline" tab alongside "Talent"

**Tests:**
- Component tests (Jest/React Testing Library): stage cards render, expand/collapse works
- E2E (Playwright): Pipeline tab loads, shows stages in correct order, live updates work

---

## Phase 5: Graph Visualization + Pluggable Stages (2 weeks)

### 5a: Sankey/Flow Graph (1 week)

**Goal:** Visualize context flow as DAG (directed acyclic graph) — tokens + sources flowing through stages

**Tech Stack:**
- React-Flow (nodes/edges) or Recharts Sankey for token flow
- Real-time updates via WebSocket or polling

**Code Skeleton:**

```typescript
// operator/console/components/ContextFlowGraph.tsx

import React from 'react'
import { Sankey, Tooltip, Sink, Source, Node, Link } from 'recharts'

interface FlowNode {
  name: string
  value: number  // tokens
}

interface FlowLink {
  source: number
  target: number
  value: number  // tokens transferred
}

interface ContextFlowData {
  nodes: FlowNode[]
  links: FlowLink[]
}

export default function ContextFlowGraph({ data }: { data: ContextFlowData }) {
  return (
    <Sankey
      width={1000}
      height={600}
      data={{
        nodes: data.nodes,
        links: data.links.map(link => ({
          source: link.source,
          target: link.target,
          value: link.value,
        })),
      }}
      node={{ fill: '#8884d8', fillOpacity: 1 }}
      link={{ stroke: '#d084d8', strokeOpacity: 0.5 }}
    >
      <Tooltip />
    </Sankey>
  )
}
```

**Backend computation:**

```python
# operator/telemetry/context_flow_graph.py

from typing import List, Dict, Tuple

class ContextFlowGraph:
    """Build Sankey graph of token flow through CEL stages."""
    
    def __init__(self, audit_events: List[Dict]):
        self.events = audit_events
    
    def build_graph(self) -> Dict:
        """Extract nodes (stages) and links (token flow)."""
        stages_seen = {}
        links = []
        
        for event in self.events:
            if event.get("event_type") != "context_engineering":
                continue
            
            stage = event.get("stage_name")
            tokens_in = event.get("tokens_in", 0)
            tokens_out = event.get("tokens_out", 0)
            
            # Track stage
            if stage not in stages_seen:
                stages_seen[stage] = len(stages_seen)
            
            # Track flow: prev_stage → this_stage
            # (requires event to include prev_stage or we infer from order)
            if "prev_stage" in event:
                prev = event["prev_stage"]
                if prev not in stages_seen:
                    stages_seen[prev] = len(stages_seen)
                
                links.append({
                    "source": stages_seen[prev],
                    "target": stages_seen[stage],
                    "value": tokens_in,
                })
        
        return {
            "nodes": [
                {"name": stage} for stage in sorted(stages_seen.keys(), 
                                                     key=lambda s: stages_seen[s])
            ],
            "links": links,
        }
```

**Tests:**
- Unit: graph_builder computes correct nodes/links from mock events
- E2E: Context Flow tab displays Sankey, updates live as task runs

---

### 5b: Pluggable Context Stages (1 week)

**Goal:** Formalize plugin contract for custom CEL stages

**ADR-0272 Draft:**

```
# ADR-0272 — Pluggable Context Engineering Stages

## Decision
Define a `ContextStage` plugin contract so operators can implement custom
stages (e.g., codebase-grep, web-research, test-coverage analysis).

## Contract

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ContextBrief:
    """Context passed between stages."""
    task: str                    # Original task description
    normalized_metadata: Dict    # Task type, affected modules, etc.
    confidence: float            # Accumulated confidence (0-1)
    context_tokens: int
    collected_context: List[str] # [memory_link, adr, ...]

class ContextStage(ABC):
    """Plugin contract for custom CEL stages."""
    
    @property
    def name(self) -> str:
        """Stage identifier (e.g., 'codebase-grep')."""
        raise NotImplementedError
    
    @property
    def metadata(self) -> Dict:
        """Stage metadata: description, author, version."""
        raise NotImplementedError
    
    @abstractmethod
    async def run(self, brief_in: ContextBrief) -> ContextBrief:
        """Execute stage; transform context.
        
        Args:
            brief_in: Context from previous stage
        
        Returns:
            brief_out: Enhanced context
        
        Raises:
            StageError: If stage fails to produce output
        """
        pass
    
    @abstractmethod
    def telemetry(self) -> Dict:
        """Return execution metrics.
        
        Returns dict with:
        - tokens_in, tokens_out: Token usage
        - confidence: Confidence in output (HIGH/MEDIUM/LOW/UNCERTAIN)
        - sources: List of resources used (e.g., ['memory#123', 'file:main.py'])
        - duration_ms: Execution time
        """
        pass
```

## Registration

Register via plugin registry:

```yaml
# operator/context_engineering/custom_stages.yaml
plugins:
  - id: codebase-grep
    type: context-stage
    boot_layer: installed  # User-installed, not bundled
    enable: true
    module: custom_stages.CodebaseGrepStage
```

## Governance

- Stages are graded (SkillForge style): new stages start with grade 0
- Grades increase based on: does it improve overall accuracy, does it degrade?
- Low-grade stages can be disabled by operator or auto-disabled if harming accuracy
- Audit trail records all stage invocations + outputs (security boundary)
```

**Implementation Skeleton:**

```python
# operator/context_engineering/plugins/example_codebase_grep.py

import asyncio
from typing import List
from .base import ContextStage, ContextBrief

class CodebaseGrepStage(ContextStage):
    """Search codebase for relevant files by grep."""
    
    @property
    def name(self) -> str:
        return "codebase-grep"
    
    @property
    def metadata(self) -> Dict:
        return {
            "description": "Search codebase for files matching task keywords",
            "author": "community",
            "version": "0.1.0",
        }
    
    async def run(self, brief_in: ContextBrief) -> ContextBrief:
        """Grep for relevant files."""
        keywords = brief_in.task.split()[:5]  # Top 5 words
        
        # Run grep concurrently
        grep_results = []
        for kw in keywords:
            result = await self._grep_async(kw)
            grep_results.extend(result)
        
        # Add to context
        files_found = list(set(grep_results))[:10]  # Top 10 unique files
        brief_in.collected_context.extend([f"file:{f}" for f in files_found])
        brief_in.context_tokens += len(files_found) * 50  # Estimate tokens
        
        return brief_in
    
    async def _grep_async(self, keyword: str) -> List[str]:
        """Async grep wrapper."""
        proc = await asyncio.create_subprocess_exec(
            "grep", "-r", keyword, ".",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        
        files = [line.decode().split(":")[0] for line in stdout.split(b"\n") if line]
        return files
    
    def telemetry(self) -> Dict:
        return {
            "tokens_in": 0,
            "tokens_out": 500,  # Estimate
            "confidence": "MEDIUM",
            "sources": ["file:system"],
            "duration_ms": 250,
        }
```

**Tests:**
- Unit: CodebaseGrepStage.run() transforms brief correctly
- Integration: Stage registered, loaded via plugin system, invoked in CEL pipeline
- E2E: Console shows custom stage in Context Pipeline, can be enabled/disabled

---

## Success Criteria (Validation Plan)

### Phase 4 (Wire CEL + Talent UI)
- [ ] Real Talent metrics computed from audit.jsonl (accuracy, learning_rate, variety, efficiency)
- [ ] Talent page displays real scores, not mocks
- [ ] Context Pipeline UI renders with stage cards (collapsible)
- [ ] Live updates work: pipeline stages light up as task runs
- [ ] 12+ unit/integration/E2E tests (all green)
- [ ] Code review: 0 findings (security, correctness, performance)

### Phase 5 (Graph Visualization + Pluggable Stages)
- [ ] Sankey graph visualizes token flow through stages
- [ ] Custom stages can be registered + enabled
- [ ] Example stage (codebase-grep) works end-to-end
- [ ] Stage grading system wired (low grades auto-disabled)
- [ ] 15+ E2E tests with custom stage scenarios
- [ ] ADR-0272 approved (Pluggable Context Stages)

---

## Dependencies & Risks

### Known Dependencies
- **ADR-0269 (CEL):** Already implemented, just needs wiring
- **ADR-0217 (Big-Data Carve-Out):** Inform routing decisions in Context Pipeline
- **Audit Trail (ADR-0232):** Must emit stage-level events (currently module-level only)
- **Plugin Registry (ADR-0030):** Custom stages ride existing plugin system

### Risks
- **Risk 1:** Audit trail events missing stage-level telemetry → Phase 4a blocked
  - Mitigation: Audit schema update (1-2 days work)
- **Risk 2:** CEL stages not idempotent → live tracing breaks
  - Mitigation: Document idempotency requirement in ContextStage contract
- **Risk 3:** Custom stages degrade overall accuracy → needs auto-disable logic
  - Mitigation: Grade-based disable already in plugin system (ADR-0030)

---

## Next Steps (Implementation)

1. **Week 1 (Phase 4a):** Real Talent aggregator
   - Load audit.jsonl → compute accuracy, learning_rate, variety, efficiency
   - Wire to `GET /api/talent/metrics` endpoint
   - Update Talent.tsx to call endpoint

2. **Week 1.5 (Phase 4b):** Context Pipeline UI
   - Implement React component with collapsible stage cards
   - Add backend API: `GET /api/context_pipeline/live`
   - Wire CEL to emit stage-level events to audit.jsonl

3. **Week 2-2.5 (Phase 5a):** Sankey graph
   - Build token-flow graph from audit events
   - Render with Recharts Sankey
   - Add to Context Pipeline tab

4. **Week 3 (Phase 5b):** Pluggable stages
   - Define ContextStage contract
   - Implement example stage (codebase-grep)
   - Wire to plugin registry + grading system

---

## Code Quality Gates (LDD)

- ✅ **Tier 0:** Context (CEL exists, plugin system exists, audit trail exists)
- ✅ **Tier 1:** Lint/type (mypy, pylint on all code)
- ✅ **Tier 2:** Unit tests (12+ for Phase 4, 15+ for Phase 5)
- ✅ **Tier 3:** Integration (full CEL pipeline tested end-to-end)
- ✅ **Tier 4:** E2E (Playwright: Talent page loads, Context Pipeline renders, custom stage works)
- ✅ **ADR Gate:** ADR-0272 (Pluggable Stages) approved before coding

---

**Status:** Ready for Phase 4 kick-off (next session with fresh token budget).
