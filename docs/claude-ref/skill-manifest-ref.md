# Skill Manifest Schema Reference (ADR-0533)

> Load when: creating a new OS-Skill, validating manifest.yaml, or understanding skill metadata.

**Canonical Schema:** `core/skills/manifest_schema.yaml` (JSON Schema + YAML validation)

---

## Overview

Every OS-Skill includes a **manifest.yaml** that declares:
- Metadata (name, version, goal, description)
- Triggers (when to run)
- Input/output schemas (strict validation)
- Learning signals (how to score this skill)
- Dependencies (which skills must load first)
- Boot layer & origin (compliance, bundled, installed, community)

The manifest is **not just documentation** — it's a machine-readable contract used for:
- Schema validation (input/output checked against manifest)
- Trigger registration (framework knows when to invoke the skill)
- Learning integration (feedback sources, sanitization rules)
- Dependency resolution (DAG validation, composition safety)
- Canary deployment (gradual rollout with success criteria)

---

## Manifest Schema (Full)

```yaml
# REQUIRED FIELDS

# Metadata (required: all 8 fields)
name: os.delegation_router
version: "1.2.3"                    # Semantic: Major.Minor.Patch
goal: "Decide where to send a task: native OS, ACS, or TDE"
description: >
  Reads feedback from previous runs (latency, cost, quality),
  learns task patterns, adapts thresholds per tenant.
  Handles edge cases: quota exhaustion, latency spikes, partial failures.
author: "Corvin OS Team"
license: "Apache-2.0"
created_at: "2026-09-01T10:00:00Z"  # ISO 8601, UTC
updated_at: "2026-09-01T10:00:00Z"

# Triggers (required: ≥1)
triggers:
  # Trigger 1: Every decision point
  - name: before_delegation_decision
    event_type: decision_point
    phase: pre_routing
    condition: every_turn            # or: on_quota_exhausted, on_latency_spike
    async_allowed: false             # Must complete before decision taken
    timeout_ms: 5000                 # Hard timeout
    
  # Trigger 2: On backpressure
  - name: on_quota_exhausted
    event_type: system_event
    phase: backpressure
    condition: acs_quota_reached
    async_allowed: true              # Fire-and-forget
    timeout_ms: null                 # No timeout (async)

# Input Schema (required, JSON Schema)
input_schema:
  type: object
  required:
    - task_shape
    - context_size
    - tenant_id
  properties:
    task_shape:
      type: string
      enum: [small_code, big_data, prose, structured]
      description: "Classification of the task"
    context_size:
      type: integer
      minimum: 0
      maximum: 2000000
      description: "Context tokens available"
    tenant_id:
      type: string
      pattern: "^[a-z0-9_-]+$"
      description: "Tenant identifier"
    history_window:
      type: integer
      default: 100
      description: "How many past runs to consider"
    engine_preference:
      type: string
      enum: [native, acs, tde, any]
      default: "any"
      description: "Operator preference (skill overrides if needed)"
  additionalProperties: false        # STRICT: no unknown fields

# Output Schema (required, JSON Schema)
output_schema:
  type: object
  required:
    - decision
    - confidence
    - reasoning
  properties:
    decision:
      type: string
      enum: [native, acs, tde]
      description: "The routing decision"
    confidence:
      type: number
      minimum: 0.0
      maximum: 1.0
      description: "0-1 confidence in this decision"
    reasoning:
      type: string
      maxLength: 500
      description: "Audit trail: why this decision was made"
    metadata:
      type: object
      properties:
        model_used:
          type: string
          description: "Which model made the decision (for attribution)"
        execution_time_ms:
          type: integer
          description: "Skill execution time"
      additionalProperties: false
  additionalProperties: false        # STRICT: no unknown fields

# Learning Signal (required)
learning_signal:
  metrics:
    - latency_actual_vs_predicted    # How close we estimated latency
    - cost_per_token                 # Token cost efficiency
    - quality_score_outcome          # User satisfaction
  
  scoring_rule: "mde < 5%"           # Mean Directional Error < 5%
  
  feedback_sources:
    # Source 1: Turn completion
    - event_type: turn_completed
      extract:
        - latency
        - cost
        - quality_outcome
      required_fields:
        - task_shape                  # Must match input
        - decision
    
    # Source 2: User feedback (optional)
    - event_type: user_feedback
      extract:
        - thumbs_up_down
        - explicit_eval_score
      required_fields:
        - run_id
  
  sanitization:
    disallow_fields:
      - prompt                        # PII-prone
      - response
      - user_id
      - raw_content
    pii_patterns:                     # Regex to reject
      - email
      - phone
      - credit_card
      - social_security
    fail_closed: true                 # Drop feedback if suspicious

# OPTIONAL FIELDS

# Dependencies (optional, but recommended for composition)
depends_on:
  - name: os.context_adapter
    version: ">=1.0.0"                # Semantic version constraint
    version_constraint: semver
    required: true                    # Skill fails if unavailable
    call_pattern: once                # once, per_worker, on_demand
    call_budget_ms: 100               # Max time budget per call
    timeout_handling: fail_parent      # or: degrade_gracefully
  
  - name: os.workflow_optimizer
    version: ">=0.9.0"
    required: false                   # Skill degrades if unavailable
    call_budget_ms: 200
    timeout_handling: degrade_gracefully

# Boot Layer & Origin (required for compliance)
boot_layer: core                      # or: compliance, bundled, installed
origin: builtin                       # or: vetted, community, marketplace
priority: 10                          # Execution order (higher = earlier)

# Tenant Scope (required)
scope: local_development              # or: staging, production
tenant_override: false                # Can individual tenants use different version?

# Canary Deployment (optional, admin-set)
canary:
  enabled: false
  traffic_percent: 10                 # Route 10% traffic to this version
  duration_days: 7
  success_criteria:
    score_improvement_percent: 2      # Must beat baseline by 2%
    error_rate_max_percent: 1
  auto_rollback_on_failure: true

# State & Persistence (runtime, documented for reference)
state:
  runs_retention_days: 90
```

---

## Field Reference

### Metadata Fields

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `name` | string | ✅ | `os.delegation_router` |
| `version` | string (SemVer) | ✅ | `1.2.3` |
| `goal` | string | ✅ | `Decide where to send a task...` |
| `description` | string | ✅ | Multi-line description |
| `author` | string | ✅ | `Corvin OS Team` |
| `license` | string | ✅ | `Apache-2.0` |
| `created_at` | ISO 8601 | ✅ | `2026-09-01T10:00:00Z` |
| `updated_at` | ISO 8601 | ✅ | `2026-09-01T10:00:00Z` |

### Trigger Fields

| Field | Type | Required | Options |
|-------|------|----------|---------|
| `name` | string | ✅ | Unique identifier |
| `event_type` | string | ✅ | `decision_point`, `system_event`, `user_action` |
| `phase` | string | ✅ | `pre_routing`, `backpressure`, `post_execution` |
| `condition` | string | ✅ | `every_turn`, `on_quota_exhausted`, `on_latency_spike` |
| `async_allowed` | boolean | ✅ | `true` (fire-and-forget) or `false` (blocking) |
| `timeout_ms` | integer or null | ✅ | Milliseconds, or null for async (no timeout) |

### Input/Output Schema Fields

Both follow **JSON Schema** format (subset for safety):

| Constraint | Purpose |
|---|---|
| `type` | `object`, `string`, `number`, `integer`, `boolean`, `array` |
| `required` | List of required properties |
| `properties` | Object property definitions |
| `enum` | Allowed values (closed set) |
| `minimum`, `maximum` | Numeric bounds |
| `minLength`, `maxLength` | String length bounds |
| `pattern` | Regex for string validation |
| `default` | Default value if not provided |
| `additionalProperties: false` | STRICT: no unknown fields |

**Safety Rules:**
- ✅ Use `enum` for closed sets (e.g., `[native, acs, tde]`)
- ✅ Use `pattern` for validation (e.g., `^[a-z0-9_-]+$`)
- ✅ Use `additionalProperties: false` for strict schemas
- ❌ Never use `type: object` without `properties` (too loose)
- ❌ Never use regex without anchors (`^...$`)
- ❌ Never allow unbounded arrays (use `maxItems`)

### Learning Signal Fields

| Field | Type | Purpose |
|-------|------|---------|
| `metrics` | list[string] | What to measure (latency, cost, quality) |
| `scoring_rule` | string | Convergence criteria (e.g., `mde < 5%`) |
| `feedback_sources` | list[object] | Where feedback comes from |
| `sanitization` | object | PII filtering rules |

**Feedback Sources:**
- `event_type`: `turn_completed`, `user_feedback`, `system_metric`
- `extract`: Which fields to pull from the event
- `required_fields`: Must be present in every feedback event

**Sanitization:**
- `disallow_fields`: PII-prone field names (always filtered)
- `pii_patterns`: Regex patterns to reject (email, phone, credit_card, etc.)
- `fail_closed`: `true` = drop suspicious feedback, `false` = log warning and continue

### Dependency Fields

| Field | Type | Purpose |
|-------|------|---------|
| `name` | string | Skill identifier (e.g., `os.context_adapter`) |
| `version` | string | Semantic version constraint (e.g., `>=1.0.0`) |
| `version_constraint` | string | `exact`, `semver`, `range`, or `any` |
| `required` | boolean | `true` = fail if unavailable, `false` = degrade gracefully |
| `call_pattern` | string | `once`, `per_worker`, `on_demand` |
| `call_budget_ms` | integer | Time budget per call (summed if called N times) |
| `timeout_handling` | string | `fail_parent` or `degrade_gracefully` |

### Boot Layer & Origin Fields

| Field | Value | Meaning |
|-------|-------|---------|
| `boot_layer` | `compliance` | Mandatory, unbypassable (audit chain, consent, house-rules) |
| `boot_layer` | `core` | OS-internal decision-making (routing, context, workflow) |
| `boot_layer` | `bundled` | Shipped with CorvinOS, but can be replaced |
| `boot_layer` | `installed` | Installed by operator (plugins, marketplace) |
| `origin` | `builtin` | Shipped with CorvinOS |
| `origin` | `vetted` | Reviewed by Corvin Labs |
| `origin` | `community` | Community-contributed (lower trust) |
| `priority` | 1-100 | Execution order (higher = earlier) |

### Canary Deployment Fields

| Field | Type | Purpose |
|-------|------|---------|
| `enabled` | boolean | Enable canary deployment |
| `traffic_percent` | 1-100 | % of traffic routed to new version |
| `duration_days` | integer | How long to run canary |
| `success_criteria.score_improvement_percent` | number | Must beat baseline by X% |
| `success_criteria.error_rate_max_percent` | number | Max error rate for success |
| `auto_rollback_on_failure` | boolean | Auto-rollback if criteria not met |

---

## Examples

### Example 1: Delegation Router (Simple, Required Deps Only)

```yaml
name: os.delegation_router
version: "1.2.3"
goal: "Route tasks to native, ACS, or TDE"
description: "Learns task patterns and adapts routing per tenant"
author: "Corvin OS Team"
license: "Apache-2.0"
created_at: "2026-09-01T10:00:00Z"
updated_at: "2026-09-01T10:00:00Z"

triggers:
  - name: before_delegation_decision
    event_type: decision_point
    phase: pre_routing
    condition: every_turn
    async_allowed: false
    timeout_ms: 5000

input_schema:
  type: object
  required: [task_shape, context_size, tenant_id]
  properties:
    task_shape:
      type: string
      enum: [small_code, big_data, prose, structured]
    context_size:
      type: integer
      minimum: 0
      maximum: 2000000
    tenant_id:
      type: string
      pattern: "^[a-z0-9_-]+$"
  additionalProperties: false

output_schema:
  type: object
  required: [decision, confidence, reasoning]
  properties:
    decision:
      type: string
      enum: [native, acs, tde]
    confidence:
      type: number
      minimum: 0.0
      maximum: 1.0
    reasoning:
      type: string
      maxLength: 500
  additionalProperties: false

learning_signal:
  metrics:
    - latency_actual_vs_predicted
    - cost_per_token
    - quality_score_outcome
  scoring_rule: "mde < 5%"
  feedback_sources:
    - event_type: turn_completed
      extract: [latency, cost, quality_outcome]
      required_fields: [task_shape, decision]
  sanitization:
    disallow_fields: [prompt, response, user_id]
    pii_patterns: [email, phone, credit_card]
    fail_closed: true

boot_layer: core
origin: builtin
priority: 10
scope: local_development
tenant_override: false
```

### Example 2: Workflow Optimizer (Complex, With Composition)

```yaml
name: os.workflow_optimizer
version: "1.0.0"
goal: "Parallelize vs. serialize multi-worker tasks"
description: |
  Decides whether to run workers in parallel or serial.
  Learns from feedback: was latency better with parallelism?
  Considers: worker count, available quota, task interdependencies.
author: "Corvin OS Team"
license: "Apache-2.0"
created_at: "2026-09-05T14:00:00Z"
updated_at: "2026-09-05T14:00:00Z"

triggers:
  - name: before_workflow_dispatch
    event_type: decision_point
    phase: pre_execution
    condition: every_turn
    async_allowed: false
    timeout_ms: 8000

input_schema:
  type: object
  required: [worker_list, tenant_id, available_quota]
  properties:
    worker_list:
      type: array
      minItems: 1
      maxItems: 50
      items:
        type: object
        required: [task_id, task_type]
        properties:
          task_id:
            type: string
          task_type:
            type: string
            enum: [small_code, big_data, prose]
          context_size:
            type: integer
          dependencies:
            type: array
            items: {type: string}
            description: "List of task IDs this worker depends on"
      additionalProperties: false
    tenant_id:
      type: string
      pattern: "^[a-z0-9_-]+$"
    available_quota:
      type: integer
      minimum: 0
  additionalProperties: false

output_schema:
  type: object
  required: [orchestration_plan, confidence, reasoning]
  properties:
    orchestration_plan:
      type: array
      items:
        type: object
        required: [stage_num, worker_ids, parallelizable]
        properties:
          stage_num:
            type: integer
            minimum: 1
          worker_ids:
            type: array
            items: {type: string}
          parallelizable:
            type: boolean
        additionalProperties: false
      minItems: 1
    confidence:
      type: number
      minimum: 0.0
      maximum: 1.0
    reasoning:
      type: string
      maxLength: 1000
  additionalProperties: false

learning_signal:
  metrics:
    - total_latency_seconds
    - parallel_efficiency_percent
    - quota_utilization_percent
  scoring_rule: "latency reduction > 10%"
  feedback_sources:
    - event_type: workflow_completed
      extract: [total_latency, efficiency, quota_used]
      required_fields: [worker_count, parallelizable]
  sanitization:
    disallow_fields: [task_content, worker_output]
    pii_patterns: [email, phone]
    fail_closed: true

# COMPOSITION: This skill depends on delegation_router
depends_on:
  - name: os.delegation_router
    version: ">=1.0.0"
    required: true
    call_pattern: per_worker      # Called for each worker
    call_budget_ms: 50            # 50ms per worker * N workers
    timeout_handling: fail_parent
  
  - name: os.context_adapter
    version: ">=0.9.0"
    required: false
    call_budget_ms: 100
    timeout_handling: degrade_gracefully

boot_layer: core
origin: builtin
priority: 9                        # Runs after delegation_router (lower priority = later)
scope: local_development
tenant_override: false

# CANARY: Gradually roll out v1.0.0 to 10% of traffic
canary:
  enabled: true
  traffic_percent: 10
  duration_days: 7
  success_criteria:
    score_improvement_percent: 5    # Must improve latency by 5%
    error_rate_max_percent: 2
  auto_rollback_on_failure: true
```

### Example 3: Context Adapter (Soft Dependencies, Optional)

```yaml
name: os.context_adapter
version: "1.5.0"
goal: "Inject context optimized for agent type"
description: "Adapts context based on agent family (Claude, GPT, etc.)"
author: "Corvin OS Team"
license: "Apache-2.0"
created_at: "2026-08-15T09:00:00Z"
updated_at: "2026-09-06T15:30:00Z"

triggers:
  - name: before_agent_invocation
    event_type: decision_point
    phase: pre_prompt
    condition: every_turn
    async_allowed: false
    timeout_ms: 3000

input_schema:
  type: object
  required: [agent_type, base_context, tenant_id]
  properties:
    agent_type:
      type: string
      enum: [claude, gpt, other]
    base_context:
      type: object
      properties:
        task_description: {type: string}
        user_history: {type: array}
        system_state: {type: object}
      additionalProperties: false
    tenant_id:
      type: string
      pattern: "^[a-z0-9_-]+$"
    max_context_tokens:
      type: integer
      default: 50000
  additionalProperties: false

output_schema:
  type: object
  required: [adapted_context, confidence]
  properties:
    adapted_context:
      type: object
      properties:
        system_prompt: {type: string}
        examples: {type: array}
        constraints: {type: array}
      additionalProperties: false
    confidence:
      type: number
      minimum: 0.0
      maximum: 1.0
    tokens_saved:
      type: integer
      description: "Context tokens saved by adaptation"
  additionalProperties: false

learning_signal:
  metrics:
    - context_tokens_saved
    - agent_performance_delta
    - instruction_clarity_score
  scoring_rule: "saved_tokens > 10%"
  feedback_sources:
    - event_type: agent_completed
      extract: [tokens_used, quality_score, adaptation_impact]
      required_fields: [agent_type]
  sanitization:
    disallow_fields: [prompt, response, api_keys]
    pii_patterns: [email, phone, api_key]
    fail_closed: true

# NO DEPENDENCIES (standalone skill)
depends_on: []

boot_layer: bundled
origin: builtin
priority: 8
scope: production
tenant_override: true              # Tenants can use different version

# NO CANARY (already stable)
```

---

## Validation Checklist

Before committing a manifest.yaml, verify:

- [ ] All REQUIRED fields present and non-empty
- [ ] `version` follows Semantic Versioning (MAJOR.MINOR.PATCH)
- [ ] `input_schema` uses `additionalProperties: false` (strict)
- [ ] `output_schema` uses `additionalProperties: false` (strict)
- [ ] All enum fields are closed sets (not open strings)
- [ ] `learning_signal.sanitization.fail_closed: true`
- [ ] All PII-prone fields listed in `disallow_fields`
- [ ] `depends_on` fields match actual skill names in registry
- [ ] `timeout_ms` values are reasonable (≤ phase timeout from layer-44-os-skills.md)
- [ ] `call_budget_ms` sum for all calls ≤ skill timeout
- [ ] `boot_layer` is one of: compliance, core, bundled, installed
- [ ] `origin` is one of: builtin, vetted, community, marketplace
- [ ] If `canary.enabled: true`, success criteria are defined

---

## Must NOT do

- ❌ Use `type: object` without `properties`
- ❌ Use `additionalProperties: true` or omit it (always set to `false`)
- ❌ Use open-ended regex without anchors (e.g., `@` matches any email-ish string)
- ❌ List PII fields in feedback_sources.extract (always in disallow_fields)
- ❌ Set `fail_closed: false` in sanitization
- ❌ Declare non-existent dependencies
- ❌ Use version strings that don't follow Semantic Versioning
- ❌ Set `timeout_ms: 0` (use `null` for async)
- ❌ Omit `reason` in output for critical decisions

---

## References

→ **ADR-0533:** OS-Skill Manifest Schema & Versioning Strategy  
→ **ADR-0535:** OS-Skill Composition & Dependency Resolution  
→ **layer-44-os-skills.md:** Full OS-Skills architecture  
→ **JSON Schema Spec:** https://json-schema.org/ (reference for schemas)  
→ **Semantic Versioning:** https://semver.org/ (version format)

See `/home/shumway/projects/Corvin-Knowledge/decisions/ADR-0533-os-skill-manifest-and-versioning.md` for full ADR.
