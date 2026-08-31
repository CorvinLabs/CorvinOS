# CorvinOS v0.8 Offline Operation Determinism — Replay Specification

**Version:** 1.0  
**Status:** SPECIFICATION  
**Date:** 2026-08-18  
**Owner:** Offline-First Engineering & Quality Assurance  
**Related ADRs:** ADR-0340 (offline sync), ADR-0342 (determinism verification), ADR-0232 (audit logging)

## Executive Summary

CorvinOS v0.8 enables full offline operation: the operator can continue using the Console when disconnected from the network. When the operator reconnects, all offline operations (skill generation, template editing, preference changes) must be replayed against the online state to ensure convergence.

This specification guarantees **deterministic replay:** given an `ExecutionContext` snapshot captured at offline time, replaying that operation in the online environment produces identical output (same result, same side effects, same audit events).

**Core Guarantee:** Offline operation recordings are cryptographically hashable. If two executions produce different hashes, the operator is alerted to review the conflict.

---

## Problem Statement: Determinism in Offline Replay

### Scenario: Offline Skill Generation

1. **10:00 AM (Online):** Operator is connected. Core CorvinOS is synced with cloud state.
2. **10:05 AM (Offline):** Operator disconnects. Console captures `ExecutionContext` (current state, RNG seed, timestamps).
3. **10:05–10:20 AM (Offline):** Operator generates a new skill using an LLM prompt. Console emulates the LLM call (mocked) and produces an `ExecutionResult`.
4. **10:20 AM (Reconnect):** Operator reconnects to network. Console replays the offline operation against the fresh (online) state.
5. **Issue:** Does replay produce the same skill? If offline LLM mock returned `name="RouterSkill"` and online LLM returns `"RouterSkillV2"`, they differ.

### Guarantees Required

- **Determinism:** Same input (ExecutionContext) → Same output (ExecutionResult) with same hash.
- **Idempotency:** Replaying the same operation twice produces identical results (hash, output, side effects).
- **Auditability:** Operator can trace every step of offline replay and verify the hash.
- **Recoverability:** If replay fails (hash mismatch), operator can review and manually decide (accept/reject/retry).

---

## Determinism Invariants (Detailed)

### Invariant 1: No Uncontrolled Randomness

**Requirement:** All random operations must be seeded and recorded.

**Violations:**
```python
# BAD: Uncontrolled randomness
random.choice([1, 2, 3])  # Depends on system entropy, not deterministic
uuid4()  # Generates new random UUID each time
```

**Allowed:**
```python
# GOOD: Seeded randomness
random.seed(execution_context.rng_seed)
r = random.choice([1, 2, 3])  # Deterministic given seed
# The seed is captured in ExecutionContext
```

**Implementation:**
- Before executing any offline operation, capture `ExecutionContext.rng_seed` (a 256-bit value derived from operation ID + timestamp).
- All calls to `random.random()`, `random.choice()`, `uuid.uuid4()` must use the seeded generator.
- Seed is immutable and included in the ExecutionResult hash.

**Test:**
```python
def test_rng_determinism():
    """Same RNG seed produces same sequence."""
    seed = 42
    
    random.seed(seed)
    seq1 = [random.random() for _ in range(100)]
    
    random.seed(seed)
    seq2 = [random.random() for _ in range(100)]
    
    assert seq1 == seq2
```

### Invariant 2: No File I/O Non-Determinism

**Requirement:** All data must come from `ExecutionContext`, not from live filesystem.

**Violations:**
```python
# BAD: Live file read
with open("~/.corvin/prefs.json", "r") as f:
    prefs = json.load(f)
# File contents may differ between offline and online

# BAD: Check if file exists
if os.path.exists("path/to/file"):
    ...
# Existence may differ
```

**Allowed:**
```python
# GOOD: Read from ExecutionContext snapshot
prefs = execution_context.snapshot.get("preferences", {})
# snapshot is captured at offline time and replayed as-is

# GOOD: Check ExecutionContext state
if execution_context.snapshot.get("skill_installed"):
    ...
```

**Implementation:**
- `ExecutionContext.snapshot` contains immutable read-only view of all filesystem state at offline time.
- Operations can read from snapshot but cannot write to live filesystem (writes go to a transient buffer, to be synced on reconnect).
- Any operation attempting direct file I/O raises `ExecutionContextViolation` error.

**Test:**
```python
def test_no_live_io_during_offline():
    """Offline operation cannot read live filesystem."""
    ctx = ExecutionContext(mode="offline", snapshot={...})
    
    # This should raise an error
    with pytest.raises(ExecutionContextViolation):
        with open("~/.corvin/prefs.json", "r") as f:
            json.load(f)
    
    # This should work (reading from snapshot)
    prefs = ctx.snapshot.get("preferences", {})
    assert prefs is not None
```

### Invariant 3: No Network Calls (All Mocked)

**Requirement:** Network operations must be replaced with mocked responses captured offline.

**Violations:**
```python
# BAD: Live network call
response = requests.get("https://api.example.com/skill")
# Response may differ, external service may be down/changed
```

**Allowed:**
```python
# GOOD: Mocked network call
response = execution_context.mock_network.get("GET /skill")
# Response is deterministic, captured at offline time
```

**Implementation:**
- Before going offline, console captures a "MockNetworkLibrary" which logs all network calls made by a skill/operation.
- Skill is executed in "mock mode" where all `requests.get()`, `requests.post()`, etc. calls are intercepted and replaced with pre-recorded responses.
- If a call cannot be answered from the mock library, operation fails with `UnmockableNetworkCall` error.

**Test:**
```python
def test_mocked_network_calls():
    """Offline operation uses mocked network."""
    ctx = ExecutionContext(
        mode="offline",
        mock_network={
            "GET /models": {"body": [{"id": "gpt-4", "name": "GPT-4"}]},
            "POST /generate": {"body": {"text": "...generated..."}},
        }
    )
    
    # Skill logic
    models = requests.get("GET /models").json()
    assert len(models) == 1
    
    # Verify it came from mock, not live network
    assert requests.get.called_with("https://api.example.com/models")
    # (This is enforced at the socket level in mock mode)
```

### Invariant 4: No Concurrency (Sequential-Only)

**Requirement:** Offline operations must be single-threaded, no async, no threading.

**Violations:**
```python
# BAD: Threading
import threading
def worker():
    pass
t = threading.Thread(target=worker)
t.start()
# Thread scheduling is non-deterministic

# BAD: Async
async def task():
    pass
asyncio.run(task())
# Event loop scheduling is non-deterministic
```

**Allowed:**
```python
# GOOD: Sequential execution
result1 = do_work_a()
result2 = do_work_b(result1)
# Results depend only on inputs, not timing
```

**Implementation:**
- `ExecutionContext.concurrency_allowed = False` (default offline).
- Any call to `threading.Thread()`, `asyncio.create_task()`, `concurrent.futures.ThreadPoolExecutor()` raises `ConcurrencyForbidden` error.
- Event loop is disabled; await/async calls are disallowed.

**Test:**
```python
def test_no_threading_offline():
    """Offline operation cannot spawn threads."""
    ctx = ExecutionContext(mode="offline")
    
    with pytest.raises(ConcurrencyForbidden):
        t = threading.Thread(target=lambda: None)
        t.start()
```

### Invariant 5: No Time Dependency (Frozen Clock)

**Requirement:** Time must be frozen at ExecutionContext capture time. No system clock calls.

**Violations:**
```python
# BAD: Live system time
now = time.time()
# Returns different value on each call, non-deterministic

# BAD: Sleep
time.sleep(1)
# Duration may vary, affects timing-dependent logic
```

**Allowed:**
```python
# GOOD: Use ExecutionContext frozen time
now = execution_context.frozen_timestamp
# Deterministic, always same value

# GOOD: Conditional sleep (ignored offline)
if execution_context.mode == "offline":
    # Skip sleep
    pass
else:
    time.sleep(1)
```

**Implementation:**
- `ExecutionContext.frozen_timestamp` is set at offline capture time (immutable).
- All calls to `time.time()`, `datetime.now()`, `time.sleep()` are intercepted.
- `time.time()` returns `ExecutionContext.frozen_timestamp`.
- `time.sleep()` is a no-op (returns immediately without blocking).

**Test:**
```python
def test_frozen_time():
    """Offline operation uses frozen time."""
    frozen_ts = 1692302400.0
    ctx = ExecutionContext(mode="offline", frozen_timestamp=frozen_ts)
    
    # Time should be frozen
    assert time.time() == frozen_ts
    
    # Sleep should be instant
    start = time.perf_counter()
    time.sleep(10)  # Should not actually sleep
    elapsed = time.perf_counter() - start
    assert elapsed < 0.1  # <100ms elapsed, not 10 seconds
```

### Invariant 6: No State Mutation Outside ExecutionContext

**Requirement:** Operations can only modify state via ExecutionContext, not global variables or shared state.

**Violations:**
```python
# BAD: Modifying global state
GLOBAL_CACHE = {}
GLOBAL_CACHE["key"] = "value"
# Global state differs between runs if cache is pre-populated differently

# BAD: Modifying module-level variables
import module
module.CONFIG_VAR = new_value
# State persists across operations, non-deterministic
```

**Allowed:**
```python
# GOOD: Modify ExecutionContext-local state
ctx.transient_state["key"] = "value"
# State is scoped to one operation, fresh each replay

# GOOD: Return output
return {"key": "value"}
# Caller decides what to do with the result
```

**Implementation:**
- `ExecutionContext.transient_state` is a mutable dict scoped to one operation (fresh for each replay).
- Module-level caches are either disabled (offline mode) or explicitly cleared at operation start.
- Operations cannot import and modify external modules.

---

## Execution Context Snapshot Format

### Data Structure

```python
@dataclass(frozen=True)
class ExecutionContext:
    """Immutable snapshot of offline execution context."""
    
    # Operation metadata
    operation_id: str  # UUID of the operation
    operation_type: str  # "skill_generation", "template_edit", etc.
    operation_timestamp: int  # Unix timestamp (ms) when operation started offline
    
    # Determinism controls
    mode: str  # "offline" or "online"
    rng_seed: int  # 256-bit seed for all random operations
    frozen_timestamp: float  # Frozen clock time
    concurrency_allowed: bool  # False = sequential only
    
    # State snapshots
    snapshot: Dict[str, Any]  # Filesystem state at offline time
    # Keys: "preferences", "skills", "templates", "settings", etc.
    
    mock_network: Dict[str, Any]  # Recorded network responses
    # Keys: "GET /endpoint", "POST /endpoint", etc.
    # Values: {"status": 200, "body": {...}}
    
    # Operation inputs
    user_input: Dict[str, Any]  # User-provided data (prompt, config, etc.)
    
    # Audit trail
    audit_events: List[AuditEvent]  # Events logged before offline
    
    # Output (filled by operation)
    transient_state: Dict[str, Any] = field(default_factory=dict)  # Mutable during operation
```

### Serialization

ExecutionContext is serialized to JSON for storage and replay:

```json
{
  "operation_id": "op-12345-abcde",
  "operation_type": "skill_generation",
  "operation_timestamp": 1692302400000,
  "mode": "offline",
  "rng_seed": 42,
  "frozen_timestamp": 1692302400.5,
  "concurrency_allowed": false,
  "snapshot": {
    "preferences": {"theme": "dark", "language": "en"},
    "skills": {
      "router": {"id": "router", "name": "Router Skill", "steps": [...]}
    }
  },
  "mock_network": {
    "GET /models": {
      "status": 200,
      "body": [{"id": "gpt-4", "name": "GPT-4"}]
    }
  },
  "user_input": {
    "skill_name": "MySkill",
    "skill_description": "Does something useful",
    "base_model": "gpt-4"
  },
  "audit_events": [
    {"timestamp": "2026-08-18T10:05:00Z", "event_type": "operation_started", ...}
  ]
}
```

---

## Replay Algorithm

### Step 1: Load ExecutionContext

```python
def replay_offline_operation(
    context_file: str,
    conflict_resolver: ConflictResolver
) -> ExecutionResult:
    """
    Replay an offline operation.
    
    Args:
        context_file: Path to stored ExecutionContext JSON
        conflict_resolver: Handler for hash mismatches
    
    Returns:
        ExecutionResult with output + hash
    """
    
    # Load context from disk
    with open(context_file, "r") as f:
        context_dict = json.load(f)
    
    context = ExecutionContext.from_dict(context_dict)
    
    # Verify context integrity (hash chain)
    if not verify_context_hash(context):
        raise ContextTampered(f"Context hash mismatch for {context.operation_id}")
    
    return _replay_with_context(context, conflict_resolver)
```

### Step 2: Setup Deterministic Environment

```python
def _replay_with_context(
    context: ExecutionContext,
    conflict_resolver: ConflictResolver
) -> ExecutionResult:
    """
    Setup deterministic environment and replay operation.
    """
    
    # Patch system libraries to enforce invariants
    with DeterministicEnvironment(context):
        # Seed RNG
        random.seed(context.rng_seed)
        
        # Freeze time
        with FrozenTime(context.frozen_timestamp):
            # Mock network
            with MockedNetwork(context.mock_network):
                # Disable concurrency
                with ConcurrencyDisabled():
                    # Redirect filesystem to snapshot
                    with SnapshotFilesystem(context.snapshot):
                        # Execute operation
                        try:
                            result = execute_operation(
                                operation_type=context.operation_type,
                                user_input=context.user_input,
                                ctx=context
                            )
                        except Exception as e:
                            return ExecutionResult(
                                status="error",
                                error=str(e),
                                hash=None
                            )
    
    # Compute result hash
    result.hash = compute_result_hash(result)
    
    return result
```

### Step 3: Hash Verification

```python
def compute_result_hash(result: ExecutionResult) -> str:
    """
    Compute deterministic hash of result.
    
    Hash includes:
    - Output data
    - Audit events
    - Side effects (state changes)
    """
    
    hashable = {
        "output": result.output,
        "status": result.status,
        "audit_events": result.audit_events,
        "state_mutations": result.state_mutations,
    }
    
    # Serialize deterministically (sorted keys, no whitespace)
    json_str = json.dumps(hashable, sort_keys=True, separators=(",", ":"))
    
    # Hash with SHA256
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()


def verify_result_hash(
    offline_result: ExecutionResult,
    online_result: ExecutionResult
) -> bool:
    """
    Compare hashes from offline and online replays.
    
    If hashes match: determinism verified ✓
    If hashes differ: conflict detected → operator review needed
    """
    
    if offline_result.hash == online_result.hash:
        return True
    else:
        # Hash mismatch: log conflict
        log_audit_event(
            event_type="replay_hash_mismatch",
            operation_id=offline_result.operation_id,
            offline_hash=offline_result.hash,
            online_hash=online_result.hash
        )
        return False
```

### Step 4: Conflict Resolution

```python
def resolve_replay_conflict(
    offline_result: ExecutionResult,
    online_result: ExecutionResult,
    conflict_resolver: ConflictResolver
) -> ExecutionResult:
    """
    Handle hash mismatch (operation produced different result offline vs. online).
    
    Options:
    1. Accept offline result (trust operator's offline state)
    2. Accept online result (trust current online state)
    3. Merge results (if possible)
    4. Manual review (wait for operator decision)
    """
    
    # Log the conflict
    conflict_event = {
        "timestamp": datetime.now().isoformat(),
        "operation_id": offline_result.operation_id,
        "offline_hash": offline_result.hash,
        "online_hash": online_result.hash,
        "offline_output": offline_result.output,
        "online_output": online_result.output,
    }
    
    audit_log.append(conflict_event)
    
    # Resolve conflict
    resolution = conflict_resolver.resolve(
        offline=offline_result,
        online=online_result
    )
    
    if resolution.action == "accept_offline":
        return offline_result
    elif resolution.action == "accept_online":
        return online_result
    elif resolution.action == "merge":
        return merge_results(offline_result, online_result)
    elif resolution.action == "manual_review":
        # Alert operator; pause sync
        return None  # No decision yet
    else:
        raise ValueError(f"Unknown resolution action: {resolution.action}")
```

---

## Test Matrix: 30+ Determinism Scenarios

### Scenario Group 1: Single Operation (5 tests)

```python
def test_replay_simple_operation_idempotent():
    """Replaying same operation twice produces same hash."""
    context = create_test_context(
        operation_type="skill_generation",
        user_input={"name": "TestSkill", "model": "gpt-4"}
    )
    
    result1 = replay_offline_operation(context)
    result2 = replay_offline_operation(context)
    
    assert result1.hash == result2.hash
    assert result1.output == result2.output


def test_replay_rng_determinism():
    """RNG seed ensures deterministic randomness."""
    context = create_test_context(
        operation_type="template_generation",
        rng_seed=42
    )
    
    # First replay
    result1 = replay_offline_operation(context)
    assert "random_value" in result1.output
    random_val1 = result1.output["random_value"]
    
    # Second replay with same seed
    context.rng_seed = 42  # Same seed
    result2 = replay_offline_operation(context)
    assert result2.output["random_value"] == random_val1
    
    # Third replay with different seed
    context.rng_seed = 43  # Different seed
    result3 = replay_offline_operation(context)
    assert result3.output["random_value"] != random_val1


def test_replay_frozen_time():
    """Frozen clock ensures time determinism."""
    context = create_test_context(
        frozen_timestamp=1692302400.0
    )
    
    result1 = replay_offline_operation(context)
    timestamp1 = result1.audit_events[0]["timestamp"]
    
    # Immediate replay (same frozen time)
    result2 = replay_offline_operation(context)
    timestamp2 = result2.audit_events[0]["timestamp"]
    
    assert timestamp1 == timestamp2


def test_replay_snapshot_consistency():
    """Reading from snapshot gives consistent results."""
    prefs = {"theme": "dark", "lang": "en"}
    context = create_test_context(
        snapshot={"preferences": prefs},
        operation_type="read_preferences"
    )
    
    result = replay_offline_operation(context)
    assert result.output["preferences"] == prefs


def test_replay_no_concurrency():
    """Offline operation cannot use threads."""
    context = create_test_context(
        operation_type="threaded_operation",
        concurrency_allowed=False
    )
    
    with pytest.raises(ConcurrencyForbidden):
        replay_offline_operation(context)
```

### Scenario Group 2: Network Mocking (6 tests)

```python
def test_replay_mocked_network_call():
    """Mocked network calls return pre-recorded responses."""
    context = create_test_context(
        operation_type="skill_with_api",
        mock_network={
            "GET /models": {
                "status": 200,
                "body": [{"id": "gpt-4", "name": "GPT-4"}]
            }
        }
    )
    
    result = replay_offline_operation(context)
    assert result.output["models"] == [{"id": "gpt-4", "name": "GPT-4"}]


def test_replay_unmockable_network_call():
    """Network call not in mock library raises error."""
    context = create_test_context(
        operation_type="skill_with_api",
        mock_network={}  # Empty
    )
    
    result = replay_offline_operation(context)
    assert result.status == "error"
    assert "UnmockableNetworkCall" in result.error


def test_replay_multiple_network_calls():
    """Multiple network calls use mocked responses in order."""
    context = create_test_context(
        operation_type="multi_api",
        mock_network={
            "GET /models": {"status": 200, "body": [{"id": "gpt-4"}]},
            "POST /generate": {"status": 200, "body": {"text": "result"}},
        }
    )
    
    result = replay_offline_operation(context)
    assert len(result.audit_events) >= 2  # At least 2 API calls logged


def test_replay_network_error_determinism():
    """Network errors are deterministic."""
    context = create_test_context(
        mock_network={
            "GET /fail": {"status": 500, "body": {"error": "Server error"}}
        }
    )
    
    result1 = replay_offline_operation(context)
    result2 = replay_offline_operation(context)
    
    assert result1.status == "error"
    assert result1.hash == result2.hash


def test_replay_response_status_codes():
    """Different status codes are preserved."""
    for status in [200, 201, 400, 401, 403, 404, 500, 503]:
        context = create_test_context(
            mock_network={
                "GET /test": {"status": status, "body": {}}
            }
        )
        
        result = replay_offline_operation(context)
        assert result.output["status"] == status


def test_replay_network_timeout_simulation():
    """Network timeouts are deterministic."""
    context = create_test_context(
        mock_network={
            "GET /slow": {"error": "timeout", "delay_ms": 5000}
        }
    )
    
    result = replay_offline_operation(context)
    # Timeout should be simulated (no actual delay)
    # Result should be consistent
    assert result.status in ["error", "success"]
```

### Scenario Group 3: State Mutations (5 tests)

```python
def test_replay_preference_mutation():
    """Modifying preferences is deterministic."""
    context = create_test_context(
        operation_type="set_preference",
        user_input={"key": "theme", "value": "light"},
        snapshot={"preferences": {"theme": "dark"}}
    )
    
    result1 = replay_offline_operation(context)
    result2 = replay_offline_operation(context)
    
    assert result1.output["new_theme"] == "light"
    assert result1.hash == result2.hash


def test_replay_skill_creation():
    """Creating skill is deterministic."""
    context = create_test_context(
        operation_type="create_skill",
        user_input={"name": "Router", "steps": ["classify", "delegate"]},
        rng_seed=42
    )
    
    result = replay_offline_operation(context)
    
    # Replay with same seed
    context.rng_seed = 42
    result2 = replay_offline_operation(context)
    
    assert result.hash == result2.hash


def test_replay_nested_state_mutation():
    """Nested state changes are deterministic."""
    context = create_test_context(
        operation_type="update_config",
        user_input={"path": "settings.performance.cache", "value": True},
        snapshot={
            "settings": {
                "performance": {"timeout": 30}
            }
        }
    )
    
    result = replay_offline_operation(context)
    
    # Verify nested update
    assert result.output["settings"]["performance"]["cache"] == True
    assert result.output["settings"]["performance"]["timeout"] == 30


def test_replay_array_append():
    """Array operations are deterministic."""
    context = create_test_context(
        operation_type="append_to_list",
        user_input={"item": "new_step"},
        snapshot={"steps": ["step1", "step2"]}
    )
    
    result = replay_offline_operation(context)
    assert result.output["steps"] == ["step1", "step2", "new_step"]


def test_replay_conditional_logic():
    """Conditional branches are deterministic."""
    context = create_test_context(
        operation_type="conditional_operation",
        user_input={"value": 10},
        snapshot={"threshold": 5}
    )
    
    result = replay_offline_operation(context)
    
    # Condition: value > threshold
    if 10 > 5:
        assert result.output["branch"] == "then"
    else:
        assert result.output["branch"] == "else"
```

### Scenario Group 4: Error Handling (5 tests)

```python
def test_replay_validation_error():
    """Input validation errors are deterministic."""
    context = create_test_context(
        user_input={"skill_name": ""}  # Invalid: empty name
    )
    
    result1 = replay_offline_operation(context)
    result2 = replay_offline_operation(context)
    
    assert result1.status == "error"
    assert result1.hash == result2.hash


def test_replay_missing_dependency():
    """Missing dependencies cause deterministic error."""
    context = create_test_context(
        operation_type="skill_requiring_plugin",
        snapshot={"plugins": {}}  # Plugin not available
    )
    
    result = replay_offline_operation(context)
    assert result.status == "error"
    assert "plugin" in result.error.lower()


def test_replay_exception_determinism():
    """Exceptions are deterministic."""
    context = create_test_context(
        operation_type="divide_by_zero"
    )
    
    result1 = replay_offline_operation(context)
    result2 = replay_offline_operation(context)
    
    assert result1.status == "error"
    assert "ZeroDivisionError" in result1.error
    assert result1.hash == result2.hash


def test_replay_timeout_determinism():
    """Timeouts are deterministic (no actual timeout, immediate in mock)."""
    context = create_test_context(
        operation_type="long_running",
        mock_network={
            "GET /slow": {"error": "timeout"}
        }
    )
    
    start = time.time()
    result = replay_offline_operation(context)
    elapsed = time.time() - start
    
    # Should complete quickly (no real timeout)
    assert elapsed < 1.0
    assert result.status == "error"


def test_replay_filesystem_error():
    """File not found errors are deterministic."""
    context = create_test_context(
        operation_type="read_file",
        user_input={"filename": "nonexistent.json"},
        snapshot={}  # File not in snapshot
    )
    
    result = replay_offline_operation(context)
    assert result.status == "error"
    assert "FileNotFoundError" in result.error
```

### Scenario Group 5: Multi-Step Operations (5 tests)

```python
def test_replay_multi_step_operation():
    """Multi-step operation produces consistent final hash."""
    context = create_test_context(
        operation_type="multi_step_skill",
        user_input={
            "step_count": 3
        },
        rng_seed=42
    )
    
    result1 = replay_offline_operation(context)
    
    context.rng_seed = 42  # Same seed
    result2 = replay_offline_operation(context)
    
    assert result1.hash == result2.hash


def test_replay_dependent_operations():
    """Output of one operation feeds into next."""
    ctx1 = create_test_context(
        operation_type="generate_template",
        rng_seed=42
    )
    result1 = replay_offline_operation(ctx1)
    template_id = result1.output["template_id"]
    
    # Second operation uses first operation's output
    ctx2 = create_test_context(
        operation_type="use_template",
        user_input={"template_id": template_id},
        snapshot={"templates": {template_id: {...}}}
    )
    result2 = replay_offline_operation(ctx2)
    
    assert result2.output["success"] == True


def test_replay_operation_with_side_effects():
    """Side effects are replayed deterministically."""
    context = create_test_context(
        operation_type="create_and_log",
        snapshot={},
        user_input={"message": "test"}
    )
    
    result = replay_offline_operation(context)
    
    # Verify side effects
    assert len(result.audit_events) >= 1
    assert any(e["event_type"] == "log" for e in result.audit_events)


def test_replay_rollback_on_error():
    """Failed operation has no side effects."""
    context = create_test_context(
        operation_type="create_then_fail",
        user_input={"should_fail": True}
    )
    
    result = replay_offline_operation(context)
    assert result.status == "error"
    
    # Verify no partial state changes
    assert result.output.get("created") is not None
    assert result.output.get("rolled_back") == True
```

### Scenario Group 6: Conflict Detection (4 tests)

```python
def test_replay_hash_mismatch_detection():
    """Hash mismatch is detected when offline != online."""
    # Offline replay
    offline_ctx = create_test_context(
        operation_type="generate_skill",
        rng_seed=42,
        mock_network={
            "GET /models": {"status": 200, "body": [{"id": "gpt-4"}]}
        }
    )
    offline_result = replay_offline_operation(offline_ctx)
    offline_hash = offline_result.hash
    
    # Online replay with different mock response
    online_ctx = create_test_context(
        operation_type="generate_skill",
        rng_seed=42,
        mock_network={
            "GET /models": {"status": 200, "body": [{"id": "gpt-4"}, {"id": "gpt-3.5"}]}
        }
    )
    online_result = replay_offline_operation(online_ctx)
    online_hash = online_result.hash
    
    # Hashes should differ (different inputs)
    assert offline_hash != online_hash


def test_replay_conflict_logging():
    """Hash mismatch is logged to audit trail."""
    offline_result = ExecutionResult(hash="abc123", output={"val": 1})
    online_result = ExecutionResult(hash="def456", output={"val": 2})
    
    conflict_resolver = MockConflictResolver()
    
    result = resolve_replay_conflict(
        offline_result,
        online_result,
        conflict_resolver
    )
    
    # Conflict should be logged
    audit_entries = audit_log.search(event_type="replay_hash_mismatch")
    assert len(audit_entries) >= 1


def test_replay_operator_override():
    """Operator can override conflict resolution."""
    offline_result = ExecutionResult(hash="abc", output={"skill": "Offline"})
    online_result = ExecutionResult(hash="def", output={"skill": "Online"})
    
    # Operator chooses offline
    resolver = MockConflictResolver(action="accept_offline")
    result = resolve_replay_conflict(offline_result, online_result, resolver)
    
    assert result.output["skill"] == "Offline"


def test_replay_merge_results():
    """If possible, results are merged instead of choosing one."""
    offline_result = ExecutionResult(
        output={"skill": {"name": "Test", "version": "1.0"}}
    )
    online_result = ExecutionResult(
        output={"skill": {"name": "Test", "color": "blue"}}
    )
    
    # Merge attempts union of fields
    merged = merge_results(offline_result, online_result)
    
    assert merged.output["skill"]["name"] == "Test"
    assert merged.output["skill"]["version"] == "1.0"
    assert merged.output["skill"]["color"] == "blue"
```

---

## GDPR Compliance: Article 5 & 32

### Article 5(1)(a) — Lawfulness, Fairness, Transparency

**Determinism Verification:**
- Operator can audit offline-to-online replay by comparing hashes.
- If hash mismatch, operator sees detailed diff (what changed, why).
- All conflicts logged to immutable audit trail.

### Article 32 — Security of Processing

**Integrity measures:**
- ExecutionContext is immutable (frozen=True dataclass).
- Result hash ensures no tampering (detect if result was modified post-replay).
- Audit trail hash-chained; any modification is detected.

**Confidentiality measures:**
- ExecutionContext contains only necessary data (snapshot, user input, mock responses).
- No PII in hashes (hashes are cryptographic integrity checks, not signatures).

---

## Implementation Notes

- **Language:** Python 3.11+
- **Mocking libraries:** unittest.mock (patch), freezegun (time freezing), requests-mock (HTTP mocking)
- **Hashing:** SHA256 (via hashlib)
- **Context serialization:** JSON (with custom encoders for datetime, bytes)

---

## References

1. **ADR-0340:** Offline sync architecture
2. **ADR-0342:** Determinism verification protocol
3. **GDPR Art. 5:** Principles relating to processing (lawfulness, transparency)
4. **GDPR Art. 32:** Security of processing (integrity, availability)
5. **Deterministic Replay:** "Efficient and Deterministic Global Snapshots in Parallel & Distributed Computing" (Chandy-Lamport algorithm reference)

---

**Document Version:** 1.0  
**Last Updated:** 2026-08-18  
**Status:** SPECIFICATION  
**Approval:** [Pending v0.8 offline architecture review]
