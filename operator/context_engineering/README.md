# Context Engineering Layer (CEL) — Phase 5.5

Memory-driven task enrichment for TaskEngine.

## Overview

The Context Engineering Layer (CEL) is Phase 5.5 of the TaskEngine pipeline (ADR-0269). It sits between Phase 4 (Enrich) and Phase 5 (Delegate), searching project memory files and returning ranked matches with confidence scores to enrich task routing decisions.

**Why?** Agents make better decisions when they know what happened in similar tasks before. CEL provides that context without requiring manual LLM research.

## Architecture

```
TaskEngine.route_task()
├─ Phase 0-4: Normalize → Classify → Filter → Validate → Enrich
├─ Phase 5.5: CEL (OPTIONAL, graceful degradation)
│   └─ Extract keywords → Search memory → Rank → Confidence
└─ Phase 5: Delegate (native/acs/tde)
```

## Usage

### Basic Usage

```python
from operator.context_engineering import MemoryLookup

# Create lookup instance
lookup = MemoryLookup()

# Search by keywords
matches = lookup.search(['voice', 'bug'], max_results=5)
for match in matches:
    print(f"{match.title} (confidence: {match.relevance_score:.2f})")
```

### With TaskEngine

```python
from operator.task_analysis.engine import TaskEngine

# Create engine (CEL enabled by default)
engine = TaskEngine(enable_cel=True)

# Route a task (Phase 5.5 runs automatically if CEL available)
result = engine.route_task("Fix bug in voice module")

# Access enriched context
if result.rich_task_brief:
    brief = result.rich_task_brief
    print(f"Memory matches: {len(brief.memory_context.matches)}")
    print(f"Confidence: {brief.memory_context.confidence:.2f}")
```

### Disable CEL

```python
# CEL is optional; disable if not needed
engine = TaskEngine(enable_cel=False)
```

## Components

### MemoryLookup

Searches memory files by keywords and returns ranked matches.

**Key Methods:**
- `search(keywords, max_results=5)` → `List[MemoryMatch]`
- `rank(matches)` → sorted by relevance (highest first)
- `enrich_task(enriched_task)` → `RichTaskBrief`

**Features:**
- TF-IDF-like relevance scoring (title: 2x weight, body: 1x)
- Age decay (files >30 days old penalized)
- LRU caching (30-min TTL, order-independent keys)
- Keyword extraction from task summary
- Graceful error handling

**Configuration:**
```python
lookup = MemoryLookup(
    memory_dir=Path.home() / ".claude" / "projects" / "CorvinOS" / "memory",
    cache_ttl_minutes=30
)
```

### RichTaskBrief

Output structure containing enriched task context.

**Fields:**
```python
@dataclass
class RichTaskBrief:
    raw_input: str                  # Original task input
    enriched_task: object           # EnrichedTask from Phase 4
    memory_context: MemoryContext   # Search results + confidence
    timestamp: datetime             # When enrichment occurred
    version: str                    # Protocol version ("0.1")
```

### MemoryMatch

Individual memory file match result.

**Fields:**
```python
@dataclass
class MemoryMatch:
    filename: str               # e.g. "voice-bug-incident.md"
    title: str                  # Extracted title
    relevance_score: float      # [0.0, 1.0], validated
    source_file: str            # Absolute path
    timestamp: datetime         # File modification time
    content_preview: str        # First 200 chars
```

### MemoryContext

Aggregated search results with metadata.

**Fields:**
```python
@dataclass
class MemoryContext:
    matches: List[MemoryMatch]          # Top N results
    search_queries: List[str]           # Keywords extracted
    confidence: float                   # Avg of match scores
    cache_hit: bool                     # Was result cached?
    search_duration_ms: float           # Wall-clock time
```

## Performance

### Latency

**Typical (3 keywords, 50 memory files):**
- Cache miss: ~2–5 ms
- Cache hit: ~0.5 ms
- Keyword extraction: ~0.1 ms
- Total enrichment: <10 ms (P95)

**Target:** P95 latency < 700ms (Phase 1 gate)

### Memory

- In-memory cache: ~100 KB per cached result
- File I/O: One stat() per scanned file
- No full-file reads for scoring (content limited to title + first 200 chars)

### Caching

- LRU cache with 30-minute TTL
- Order-independent cache keys (same keywords in any order = hit)
- Automatic expiry of stale entries

## Scoring Algorithm

### TF-IDF-Like Relevance

For each memory file against keywords:

1. **Title match:** keyword found in title → +2.0 weight
2. **Body match:** keyword found in body (not title) → +1.0 weight
3. **Normalize:** total_weight / len(keywords), clamped to [0.0, 1.0]

**Example:**
- Keywords: ["voice", "bug", "fix"]
- Title contains: "voice", "bug" → +2.0 + 2.0 = 4.0
- Body contains: "fix" → +1.0
- Total: 5.0 / 3 = 1.67 → clamped to 1.0 ✓

### Age Decay

Files older than 30 days are penalized:
- 30 days: 0.7x multiplier
- 60 days: 0.4x multiplier
- 90+ days: 0.1x multiplier

**Rationale:** Recent experiences are more relevant; stale memories less so.

## Keyword Extraction

From task's `normalized.summary`:

1. Split on whitespace, lowercase
2. Filter: words > 3 chars, not in stopwords set
3. Deduplicate (preserve order)
4. Limit to 10 keywords

**Stopwords:** "the", "this", "that", "with", "from", "and", "or", "is", "a", "be"

## Known Limitations

1. **Memory directory is static** — CEL does not sync memory files during operation. Changes are visible only after MemoryLookup restart.

2. **No ML ranking** — Uses simple TF-IDF. Phase 2+ can add embedding-based similarity.

3. **Text-only memory** — CEL searches `.md` files only. Other formats (PDFs, images) require Phase 2 extension.

4. **No active learning** — CEL does not record which memory matches were actually useful. Phase 2+ can add feedback loop.

5. **Keyword extraction is fragile** — Split on whitespace; no NLP. Hyphenated words and acronyms may not extract well.

## Testing

**Run all tests:**
```bash
python3 -m pytest operator/context_engineering/tests/ -xvs
```

**Coverage:**
```bash
python3 -m pytest operator/context_engineering/tests/ --cov=operator.context_engineering --cov-report=term-missing
```

**Test breakdown:**
- `test_memory_lookup.py`: 18 unit tests (search, rank, cache, keyword extraction)
- `test_enrichment.py`: 15 integration tests (full pipeline, latency, confidence)
- `test_engine_phase_5_5_cel.py`: 14 TaskEngine integration tests

**Current status:** 47 tests passing ✅

## Metrics & Observability

CEL integrates with TaskEngine's Prometheus metrics:

- `phase_timer(MetricsPhase.CEL)` — Phase 5.5 wall-clock time
- `ctx["memory_matches"]` — Number of matches found
- `ctx["cel_confidence"]` — Average confidence score

**Example Prometheus query:**
```promql
rate(task_engine_phase_duration_seconds_sum{phase="context_engineering"}[5m])
```

## Error Handling

CEL is **optional** — TaskEngine gracefully degrades if MemoryLookup is unavailable:

```python
if self.cel:
    try:
        rich_brief = self.cel.enrich_task(enriched)
    except Exception as e:
        logger.warning(f"CEL failed: {e}, continuing without it")
```

**Logs:**
- `INFO`: CEL enabled/disabled at startup
- `DEBUG`: Cache hit/miss, search operations
- `INFO`: Enrichment complete with match count + confidence
- `WARNING`: CEL initialization failed, continuing without it

## Debugging

### Check Memory Directory

```python
from pathlib import Path
memory_dir = Path.home() / ".claude" / "projects" / "CorvinOS" / "memory"
print(f"Memory files: {len(list(memory_dir.glob('*.md')))}")
for f in memory_dir.glob("*.md"):
    print(f"  {f.name}")
```

### Test a Search

```python
from operator.context_engineering import MemoryLookup

lookup = MemoryLookup()
matches = lookup.search(["voice", "bug"], max_results=10)

for m in matches:
    print(f"{m.filename}: {m.relevance_score:.2f} ({m.title})")
```

### Check Cache

```python
keywords = ["voice", "bug"]
cache_key = hash(tuple(sorted(keywords)))
if cache_key in lookup._search_cache:
    results, timestamp = lookup._search_cache[cache_key]
    age = (datetime.now() - timestamp).total_seconds()
    print(f"Cache: {len(results)} results (age: {age:.0f}s)")
```

## Next Steps (Phase 2+)

- **Graph Traversal:** Walk Classifier graphs to find related decisions
- **Skill Injection:** Inject relevant skills into agent context
- **Approach Synthesis:** Recommend approaches based on past experience
- **Blocker Identification:** Flag known obstacles
- **Feedback Loop:** Track which memory matches were actually useful

## References

- **ADR-0269:** Context Engineering Layer specification
- **ADR-0267:** TaskEngine architecture (Phases 0-5)
- **memory/**: MEMORY.md index + individual .md files
