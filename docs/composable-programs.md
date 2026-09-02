# Composable Programs: Writing Skills That Call Other Skills

**One of Skills 2.0's superpowers:** Skills call other Skills. This enables **composable programs** — complex behavior built from simple, reusable units.

This guide explains composition patterns, dependency management, error handling, and real examples.

---

## Skills as Imports

Think of Skills like Python imports:

```python
# Python: import + call
from my_module import classify
result = classify(text)

# CorvinOS: skill + call_skill
skill = get_skill("classify_content")
result = skill.execute({"text": text})

# Composition: Chain Skills together
def content_router(request):
    classified = call_skill("classify_content", request)
    routed = call_skill("select_engine", classified)
    return routed
```

**Difference:** Skills are **versioned, learned, audited**. Each call leaves a trace.

---

## Composition Patterns

### Pattern 1: Linear Chain (A → B → C)

Skill A calls B, B calls C.

```python
@Skill.register
class Summarizer(Skill):
    skill_id = "demo.summarizer"
    version = "1.0.0"
    depends_on = ["clean_text", "extract_key_points", "format_summary"]
    
    def execute(self, request: dict) -> dict:
        text = request.get("text", "")
        
        # Step 1: Clean
        cleaned = self.call_skill("clean_text", {"text": text})
        
        # Step 2: Extract
        extracted = self.call_skill(
            "extract_key_points",
            {"text": cleaned["cleaned_text"]}
        )
        
        # Step 3: Format
        formatted = self.call_skill(
            "format_summary",
            {"points": extracted["points"]}
        )
        
        return formatted
```

**Audit trail (automatically traced):**
```
Task XYZ called Summarizer
  ├─ Summarizer called clean_text (10ms)
  ├─ clean_text called extract_key_points (15ms)
  └─ extract_key_points called format_summary (5ms)
  └─ Total: 30ms
```

### Pattern 2: Fan-Out (A → {B, C, D})

Skill A calls multiple Skills in parallel.

```python
@Skill.register
class ContentAnalyzer(Skill):
    skill_id = "demo.content_analyzer"
    version = "1.0.0"
    depends_on = ["classify_category", "analyze_sentiment", "extract_keywords"]
    
    def execute(self, request: dict) -> dict:
        text = request.get("text", "")
        
        # Parallel calls (or sequential, depending on framework)
        category = self.call_skill("classify_category", {"text": text})
        sentiment = self.call_skill("analyze_sentiment", {"text": text})
        keywords = self.call_skill("extract_keywords", {"text": text})
        
        return {
            "category": category["category"],
            "sentiment": sentiment["sentiment"],
            "keywords": keywords["keywords"],
            "confidence": min(
                category.get("confidence", 0),
                sentiment.get("confidence", 0),
                keywords.get("confidence", 0)
            )
        }
```

**Trait:** Confidence is weakest link (min of all).

### Pattern 3: Conditional (A → {B or C})

Skill A decides which Skill to call next.

```python
@Skill.register
class RequestRouter(Skill):
    skill_id = "demo.request_router"
    version = "1.0.0"
    depends_on = ["classify_request", "route_to_expert", "route_to_general"]
    
    def execute(self, request: dict) -> dict:
        # Classify the request
        classified = self.call_skill("classify_request", request)
        request_type = classified.get("type", "general")
        
        # Route based on type
        if request_type == "expert":
            return self.call_skill("route_to_expert", request)
        else:
            return self.call_skill("route_to_general", request)
```

**Note:** Both branches must be in `depends_on` (not just active path).

### Pattern 4: Recursive (A → A)

A Skill calls itself with different input (search depth limit).

```python
@Skill.register
class TreeSearch(Skill):
    skill_id = "demo.tree_search"
    version = "1.0.0"
    depends_on = ["tree_search"]  # Self-reference
    
    def execute(self, request: dict) -> dict:
        node = request.get("node")
        target = request.get("target")
        depth = request.get("depth", 0)
        max_depth = request.get("max_depth", 10)
        
        # Base case
        if node == target or depth >= max_depth:
            return {"found": node == target, "depth": depth}
        
        # Recursive case
        for child in node.children:
            result = self.call_skill(
                "tree_search",
                {
                    "node": child,
                    "target": target,
                    "depth": depth + 1,
                    "max_depth": max_depth
                }
            )
            if result["found"]:
                return result
        
        return {"found": False, "depth": depth}
```

**Safety:** Stack depth limited; recursion > max_depth raises error.

---

## Dependency Management

### Declaring Dependencies

Every Skill must declare what it calls:

```python
@Skill.register
class ComplexSkill(Skill):
    skill_id = "complex.analyzer"
    version = "1.0.0"
    depends_on = [
        "extract_features",      # Direct dependency
        "classify_category",     # Direct dependency
        "score_relevance"        # Direct dependency (only one called)
    ]
```

**Rule:** List ALL possible Skills this Skill might call, even if conditional.

### Dependency Resolution

On registration, system verifies:

```python
# Check 1: Do all dependencies exist?
register_skill(ComplexSkill())
# ✅ Pass: All three Skills exist

# Check 2: Are there circular dependencies?
# A depends on B
# B depends on C
# C depends on A  ← Circular! ERROR

# Check 3: What's the execution order?
resolve_dependencies("complex.analyzer", topo_sort=True)
# Returns: ["extract_features", "classify_category", "score_relevance"]
# (Order in which they might execute)
```

### Viewing Dependency Graph

```python
from corvin_skills.composition import get_dependency_graph

graph = get_dependency_graph("os.delegation_router")
# Returns:
# {
#   "os.delegation_router": {
#     "depends_on": ["classify_content", "estimate_complexity", "select_engine"],
#     "dependents": ["workflow_executor"]  # Who calls this Skill?
#   },
#   "classify_content": {
#     "depends_on": ["extract_features"],
#     "dependents": ["os.delegation_router"]
#   },
#   ...
# }
```

---

## Error Handling in Composition

### Failure Propagation (Fail-Closed)

If any called Skill fails, the entire composition fails:

```python
@Skill.register
class Pipeline(Skill):
    skill_id = "demo.pipeline"
    depends_on = ["step_a", "step_b", "step_c"]
    
    def execute(self, request: dict) -> dict:
        try:
            a = self.call_skill("step_a", request)
        except SkillExecutionError as e:
            # step_a failed
            raise SkillExecutionError(f"Pipeline failed at step_a: {e}")
        
        try:
            b = self.call_skill("step_b", a)
        except SkillExecutionError as e:
            # step_b failed
            raise SkillExecutionError(f"Pipeline failed at step_b: {e}")
        
        try:
            c = self.call_skill("step_c", b)
        except SkillExecutionError as e:
            # step_c failed
            raise SkillExecutionError(f"Pipeline failed at step_c: {e}")
        
        return c
```

**Outcome:** If step_b fails, entire pipeline fails; caller sees error.

### Fallback (Retry or Alternate Path)

```python
@Skill.register
class RobustRouter(Skill):
    skill_id = "demo.robust_router"
    depends_on = ["primary_classifier", "fallback_classifier"]
    
    def execute(self, request: dict) -> dict:
        # Try primary classifier
        try:
            result = self.call_skill("primary_classifier", request)
        except SkillExecutionError:
            # Primary failed; try fallback
            result = self.call_skill("fallback_classifier", request)
        
        return result
```

**Audit trail:**
```json
[
  {"event": "skill_executed", "skill_id": "primary_classifier", "status": "FAILED"},
  {"event": "skill_executed", "skill_id": "fallback_classifier", "status": "OK"}
]
```

### Graceful Degradation

```python
@Skill.register
class EnrichedResponse(Skill):
    skill_id = "demo.enriched_response"
    depends_on = ["basic_response", "enrich_with_context", "add_metadata"]
    
    def execute(self, request: dict) -> dict:
        # Always return something
        basic = self.call_skill("basic_response", request)
        
        # Try to enrich, but don't fail if it does
        try:
            enriched = self.call_skill("enrich_with_context", basic)
        except SkillExecutionError:
            # Enrichment failed; use basic response
            enriched = basic
        
        # Try to add metadata, but don't fail
        try:
            final = self.call_skill("add_metadata", enriched)
        except SkillExecutionError:
            # Metadata failed; use what we have
            final = enriched
        
        return final
```

**Guarantee:** Always returns a response (basic → basic+enriched → basic+enriched+metadata).

---

## Real Examples

### Example 1: Content Processing Pipeline

```python
@Skill.register
class DocumentProcessor(Skill):
    skill_id = "demo.document_processor"
    version = "1.0.0"
    depends_on = [
        "parse_document",
        "extract_metadata",
        "classify_content",
        "validate_compliance",
        "generate_summary"
    ]
    
    def execute(self, request: dict) -> dict:
        """Process a document end-to-end."""
        document = request.get("document")
        
        # Step 1: Parse
        parsed = self.call_skill("parse_document", {"document": document})
        
        # Step 2: Extract metadata
        metadata = self.call_skill(
            "extract_metadata",
            {"parsed": parsed["parsed_content"]}
        )
        
        # Step 3: Classify
        classified = self.call_skill(
            "classify_content",
            {**parsed, **metadata}
        )
        
        # Step 4: Validate compliance
        validated = self.call_skill(
            "validate_compliance",
            {**classified, "category": classified["category"]}
        )
        
        if not validated.get("compliant", False):
            raise SkillExecutionError(f"Compliance failed: {validated['reason']}")
        
        # Step 5: Summarize
        summary = self.call_skill(
            "generate_summary",
            {"parsed": parsed["parsed_content"], "metadata": metadata}
        )
        
        return {
            "document_id": metadata.get("id"),
            "category": classified["category"],
            "summary": summary["summary"],
            "confidence": min(
                classified.get("confidence", 0),
                summary.get("confidence", 0)
            )
        }
```

### Example 2: Multi-Model Ensemble

```python
@Skill.register
class EnsembleClassifier(Skill):
    skill_id = "demo.ensemble_classifier"
    version = "1.0.0"
    depends_on = [
        "model_lstm",
        "model_transformer",
        "model_svm",
        "aggregate_predictions"
    ]
    
    def execute(self, request: dict) -> dict:
        """Get predictions from 3 models, aggregate."""
        text = request.get("text", "")
        
        # Get predictions from each model (could be parallel)
        lstm_pred = self.call_skill("model_lstm", {"text": text})
        transformer_pred = self.call_skill("model_transformer", {"text": text})
        svm_pred = self.call_skill("model_svm", {"text": text})
        
        # Aggregate predictions
        aggregated = self.call_skill(
            "aggregate_predictions",
            {
                "predictions": [
                    lstm_pred["prediction"],
                    transformer_pred["prediction"],
                    svm_pred["prediction"]
                ],
                "confidences": [
                    lstm_pred.get("confidence", 0.5),
                    transformer_pred.get("confidence", 0.5),
                    svm_pred.get("confidence", 0.5)
                ]
            }
        )
        
        return aggregated
```

**Trait:** Final confidence is max (best among models).

---

## Testing Composition

### Unit Test: Composition Structure

```python
def test_compositor_dependencies():
    """Verify dependency declaration is correct."""
    skill = get_skill("demo.pipeline")
    
    # Check declared dependencies exist
    for dep in skill.depends_on:
        assert get_skill(dep), f"Dependency {dep} not found"
    
    # Check no circular dependencies
    from corvin_skills.composition import detect_cycles
    cycles = detect_cycles(skill.skill_id)
    assert len(cycles) == 0, f"Circular deps found: {cycles}"
```

### E2E Test: Composition Execution

```python
def test_pipeline_e2e():
    """Test the entire composition end-to-end."""
    from corvin_skills.registry import execute_skill
    
    result = execute_skill(
        "demo.pipeline",
        {"input": "test data"}
    )
    
    # Verify output structure
    assert "output" in result
    assert "confidence" in result
    assert result["confidence"] >= 0.0
    
    # Verify audit trail
    from corvin_skills.audit import get_execution_trace
    trace = get_execution_trace(result["trace_id"])
    
    # Check all Skills were called
    assert len(trace["skills_called"]) == 3
    assert trace["skills_called"][0]["skill_id"] == "step_a"
    assert trace["skills_called"][1]["skill_id"] == "step_b"
    assert trace["skills_called"][2]["skill_id"] == "step_c"
```

### Invariant: Total Latency

```python
def test_pipeline_latency():
    """Latency should be sum of all steps."""
    result = execute_skill("demo.pipeline", {...})
    
    # Sum of called Skills' latencies
    from corvin_skills.audit import get_execution_trace
    trace = get_execution_trace(result["trace_id"])
    
    total_skill_latency = sum(s["latency_ms"] for s in trace["skills_called"])
    
    # Should be close to pipeline latency (+ overhead)
    pipeline_latency = result["latency_ms"]
    overhead = pipeline_latency - total_skill_latency
    
    assert overhead < 10, f"Overhead too high: {overhead}ms"
```

---

## Best Practices

### 1. Declare All Dependencies

```python
# ✅ CORRECT
depends_on = ["classify", "route_if_complex", "route_if_simple"]

# ❌ WRONG (route_if_simple not declared)
depends_on = ["classify", "route_if_complex"]
```

### 2. Fail-Closed by Default

```python
# ✅ CORRECT: Fail if dependency fails
try:
    result = self.call_skill("dependency", input)
except SkillExecutionError:
    raise

# ⚠️ RISKY: Silently continue
try:
    result = self.call_skill("dependency", input)
except SkillExecutionError:
    result = {}  # Default; might hide errors
```

### 3. Trace Composition Latency

```python
# ✅ CORRECT: Each call logs latency
result = self.call_skill("step_a", input)  # Logged: 10ms
result = self.call_skill("step_b", result)  # Logged: 15ms

# Audit shows: Pipeline = 25ms (10 + 15 + overhead)

# ❌ WRONG: Batching calls hides latency breakdown
import concurrent.futures
with concurrent.futures.ThreadPoolExecutor() as executor:
    a = executor.submit(self.call_skill, "step_a", input)
    b = executor.submit(self.call_skill, "step_b", input)
# Audit only shows total; breakdown hidden
```

### 4. Version Composition Carefully

When changing composition (e.g., adding/removing a step):

- **Add step:** MINOR version bump (backward-compatible: new step at end)
- **Remove step:** MAJOR version bump (breaking: output format changes)
- **Reorder steps:** MAJOR version bump (breaking: latency + side effects)

---

## Debugging Composition Issues

### Check Dependency Chain

```bash
corvin skill trace os.delegation_router --task <task_id>
# Output:
# os.delegation_router
#   ├─ classify_content (10ms) ✅
#   ├─ estimate_complexity (15ms) ✅
#   └─ select_engine (5ms) ✅
# Total: 30ms
```

### Verify Audit Trail

```bash
corvin audit filter --skill "os.delegation_router|classify_content|estimate_complexity|select_engine" \
  --task <task_id>
# Shows all events in the call chain
```

### Check Circular Dependencies

```bash
corvin skill validate-dependencies
# Output: No cycles detected ✅
```

---

## See Also

- **[Skills System](skills-system.md)** — Core Skill concepts
- **[Skills API Reference](skills-api-reference.md)** — `call_skill()` signature
- **[Audit Trail](audit-trail.md)** — How composition is traced
- **[Learning Loop](learning-loop.md)** — How composite Skills improve

---

**Composition is where Skills shine. Simple units, combined cleverly, build complex intelligent systems. Every step audited. Every path traced. Every decision verifiable.**
