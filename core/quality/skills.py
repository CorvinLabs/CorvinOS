"""
SkillForge integration: Create reusable gate skills.

Each gate becomes a callable skill that enforces lineage + quality constraints.
"""

from typing import Dict
from .gates.gates import IdeaGate, ConceptGate, ADRGate, ImplementationGate


# Skill templates (markdown bodies injected into future turns)

IDEA_GATE_SKILL = """# idea-gate

Gate for raw ideas: minimal validation.

**Rules:**
- Must have a name
- Status must be draft or proposed
- Tags are optional but recommended (for discovery)
- No upstream required (it's a root)

**When to use:**
- Before creating a Concept from an Idea
- When brainstorming new problems or insights
- To validate that Ideas are well-formed before promotion

**Example:**

```
idea = Idea(
  id='IDEA-0042',
  name='distributed-consensus-for-microservices',
  status=Status.PROPOSED,
  tags=['consensus', 'distributed-systems'],
  inspiration_context='Observed lack of consensus protocols...'
)
gate = IdeaGate()
result = gate.validate(idea)
assert result.is_pass()
```

**Validation:**
- ✓ Name present and non-empty
- ✓ Status in [draft, proposed]
- ⚠ Recommend: tags (for discovery)
- ⚠ Recommend: inspiration_context (trace source)

**Verdict:** PASS (no issues) | WARN (issues + warnings) | FAIL (>=1 issue)
"""

CONCEPT_GATE_SKILL = """# concept-gate

Gate for reusable concepts (patterns).

**Rules:**
- Must have upstream Idea (blocking)
- Must have a name
- Status must be draft | proposed | approved
- Tags required (pattern name, keywords) — minimum 2
- Validates upstream Idea exists in repository

**When to use:**
- Before creating an ADR from a Concept
- When extracting a reusable pattern from an Idea
- To prevent orphaned Concepts (no parent Idea)

**Example:**

```
concept = Concept(
  id='CONCEPT-0008',
  name='raft-consensus',
  upstream='IDEA-0042',
  tags=['raft', 'consensus', 'algorithm'],
  status=Status.PROPOSED,
)
gate = ConceptGate()
result = gate.validate(concept, drawer_manager)
assert result.is_pass()
```

**Validation:**
- ✓ Name present
- ✓ Status in [draft, proposed, approved]
- ✓ Upstream Idea exists (blocking)
- ✓ Tags present (minimum 2)
- ⚠ Warn if tags < 2

**Verdict:** PASS | WARN | FAIL
"""

ADR_GATE_SKILL = """# adr-gate

Enhanced gate for Architecture Decision Records.

**Rules (extends ADR-0264):**
- Must have upstream Concept (new)
- Decision section required
- Status progression: proposed → approved → active
- Tags recommended (security, performance, api)

**When to use:**
- Before creating an Implementation Plan from an ADR
- When documenting architectural decisions
- To enforce that ADRs are grounded in Concepts

**Example:**

```
adr = ADR(
  id='ADR-0321',
  name='use-raft-for-distributed-consensus',
  upstream='CONCEPT-0008',
  tags=['raft', 'decision'],
  status=Status.PROPOSED,
)
gate = ADRGate()
result = gate.validate(adr, drawer_manager)
assert result.is_pass()
```

**Validation:**
- ✓ Name (title) present
- ✓ Status in [proposed, approved, active]
- ✓ Upstream Concept exists (blocking)
- ⚠ Recommend: tags

**Verdict:** PASS | WARN | FAIL
"""

IMPLEMENTATION_GATE_SKILL = """# implementation-gate

Blocking gate for deployment plans.

**Rules:**
- Must have upstream ADR (blocking)
- Must have ≥1 deployment step (blocking)
- Must have success criteria (blocking)
- Status: approved | active | superseded
- Rollback procedure recommended
- Rollout sequence recommended

**When to use:**
- Before deploying any change to production
- When validating that Plans are complete
- To prevent incomplete deployments

**Example:**

```
plan = ImplementationPlan(
  id='IMPL-0001',
  name='raft-rollout-plan',
  upstream='ADR-0321',
  deployment_steps=['Stage 1: Deploy to staging', ...],
  success_criteria='Replication works, failover < 500ms',
  rollback_procedure='Revert to v0.9',
  status=Status.APPROVED,
)
gate = ImplementationGate()
result = gate.validate(plan, drawer_manager)
assert result.is_pass()
```

**Validation:**
- ✓ Upstream ADR exists (blocking)
- ✓ Deployment steps present (≥1, blocking)
- ✓ Success criteria defined (blocking)
- ✓ Status in [approved, active, superseded]
- ⚠ Recommend: rollback procedure
- ⚠ Recommend: rollout sequence

**Verdict:** PASS | WARN | FAIL (any missing → FAIL)
"""

PIPELINE_AUDIT_SKILL = """# pipeline-audit

Full-pipeline audit: orphan detection, cycle detection, lineage validation.

**Checks:**
1. All artifacts pass their respective gates
2. No orphaned artifacts (Concepts/ADRs/Plans without upstream)
3. No circular dependencies (Idea → Concept → ADR → Plan)
4. All upstream links resolvable

**When to use:**
- Before committing a batch of artifacts
- Periodic validation (daily/weekly)
- After bulk-import (Phase 3c)

**Example:**

```
audit = PipelineAudit()
results = audit.audit_all(drawer_manager)

failures = [r for r in results if r.verdict == GateVerdict.FAIL]
if failures:
  for r in failures:
    print(f'{r.artifact_id}: {r.issues}')
```

**Outputs:**
- Gate validation results (one per artifact)
- Orphan list (artifacts with no upstream)
- Cycle list (circular dependencies)

**Verdict:** PASS if no orphans/cycles | FAIL if found
"""

SKILL_CATALOG = {
    'idea-gate': {
        'name': 'idea-gate',
        'description': 'Gate for raw ideas: minimal validation',
        'type': 'learned-experience',
        'scope': 'project',
        'body': IDEA_GATE_SKILL,
    },
    'concept-gate': {
        'name': 'concept-gate',
        'description': 'Gate for reusable concepts: requires upstream Idea',
        'type': 'learned-experience',
        'scope': 'project',
        'body': CONCEPT_GATE_SKILL,
    },
    'adr-gate': {
        'name': 'adr-gate',
        'description': 'Enhanced gate for ADRs: requires upstream Concept',
        'type': 'learned-experience',
        'scope': 'project',
        'body': ADR_GATE_SKILL,
    },
    'implementation-gate': {
        'name': 'implementation-gate',
        'description': 'Blocking gate for deployment plans: requires ADR + steps + criteria',
        'type': 'learned-experience',
        'scope': 'project',
        'body': IMPLEMENTATION_GATE_SKILL,
    },
    'pipeline-audit': {
        'name': 'pipeline-audit',
        'description': 'Full-pipeline audit: orphan detection, cycle detection, lineage validation',
        'type': 'learned-experience',
        'scope': 'project',
        'body': PIPELINE_AUDIT_SKILL,
    },
}


def get_skill_catalog() -> Dict[str, dict]:
    """Get all available skills."""
    return SKILL_CATALOG


def get_skill(skill_name: str) -> Dict[str, str]:
    """Get a specific skill."""
    return SKILL_CATALOG.get(skill_name)


def list_skills() -> list:
    """List all skill names."""
    return list(SKILL_CATALOG.keys())
