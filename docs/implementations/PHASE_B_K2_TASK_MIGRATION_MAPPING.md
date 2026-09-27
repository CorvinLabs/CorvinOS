# Phase B K=2: Task Registry → DB Schema Mapping

**Document Version:** 1.0 | **Date:** 2026-09-27 | **Scope:** 525 tasks, task_registry.json → tasks.db

---

## Task Registry JSON → DB Schema Mapping

### Source: task_registry.json (525 entries)
```json
{
  "task_id": "adr_0009",
  "title": "ADR-0009 — ...",
  "category": "adr",
  "status": "ARCHIVED" | "IN_PROGRESS" | "UNKNOWN",
  "completion_date": null | "2026-09-01T...",
  "adr_id": "ADR-0009",
  "adr_status": "SUPERSEDED" | "PROPOSED" | "EXTERNAL-REFERENCE",
  "commit_hash": null | "sha123...",
  "commit_date": null | "2026-09-01",
  "memory_file": null | "path/to/file.md",
  "depends_on": [],
  "notes": ""
}
```

### Target: Task Tracking DB Schema (5 Tables)

#### Table: items (PRIMARY)
| Column | Source | Transform | Required |
|--------|--------|-----------|----------|
| **id** | task_id | AS-IS | ✅ |
| **tenant_id** | (constant) | "_default" | ✅ |
| **kind** | category | "adr" → "decision" \| "task" → "work" | ✅ |
| **title** | title | AS-IS (strip Unicode) | ✅ |
| **description** | notes | Fallback to empty string | ✅ |
| **status** | status | "IN_PROGRESS"→"open" \| "ARCHIVED"→"archived" \| "UNKNOWN"→"unknown" | ✅ |
| **status_reason** | adr_status | Optional reason | ⚠️ |
| **category** | category | AS-IS | ⚠️ |
| **external_ref** | adr_id | For ADRs: use ADR ID as reference | ⚠️ |
| **completed_at** | completion_date | ISO 8601 if set | ⚠️ |
| **created_at** | (current) | ISO 8601 timestamp | ✅ |
| **created_by** | (constant) | "migration-phase-b" | ✅ |
| **updated_at** | (current) | ISO 8601 timestamp | ✅ |
| **version** | (constant) | 1 | ✅ |
| **labels** | (derived) | JSON array: ["adr", "from-task-registry"] | ✅ |

#### Table: dependencies
| Source Field | Target | Logic |
|--------------|--------|-------|
| depends_on[] | item_id, depends_on_id | For each dependency: create row with dep_type="blocks" |

#### Table: runs (optional)
| Source Field | Target | Logic |
|--------------|--------|-------|
| commit_hash | run_ref | If commit_hash exists: create row with run_type="git_commit" |

#### Table: events (reference copy)
| Logic | Note |
|-------|------|
| Not populated during migration | Will be populated post-migration by audit trail |

#### Table: meta
| Key | Value | Purpose |
|-----|-------|---------|
| schema_version | 1 | Tracked by store.py |
| migration_phase_b_timestamp | ISO 8601 | Migration start time |
| migration_total_count | 525 | Total items migrated |

---

## Status Transformation Map

| task_registry.json status | → | items.status | Rationale |
|---------------------------|---|--------------|-----------|
| "ARCHIVED" | → | "archived" | Terminal state, non-recoverable |
| "IN_PROGRESS" | → | "open" | Active work |
| "COMPLETED" | → | "completed" | (rare in registry) |
| "UNKNOWN" | → | "unknown" | Uncertain state, requires review |

---

## Data Quality & Validation Rules

### Pre-Migration Checks (K=2 Gate 1: Row Count)
- Input: 525 tasks in task_registry.json
- Check: Read and parse all 525 rows without error
- Pass Criteria: 525 rows parsed, 0 parse errors

### During Migration Batches (K=2 Gate 2: Schema Conformity)
- Batch Size: 50 items per transaction
- Checkpoint: After each batch, verify:
  - No NULL in required columns (id, tenant_id, kind, title, status, created_at, created_by, updated_at, labels)
  - status is one of {open, archived, unknown, completed}
  - external_ref matches ADR ID format (if present)
  - No duplicate IDs (UNIQUE constraint on items.id)

### Post-Migration Validation (K=2 Gate 3: Data Integrity)
- Row Count Match: DB items count == 525
- Dependency Count: All depends_on[] chains resolved in dependencies table
- Completion Date Validation: completed_at ≤ updated_at (temporal invariant)
- Tenant Isolation: All rows have tenant_id="_default", no cross-tenant leakage
- ADR Status Alignment: adr_status field preserved in status_reason or labels

### Git Sync Validation (K=2 Gate 4: Git Sync)
- Commit Hashes: If commit_hash exists in registry, run_type="git_commit" row created
- Commit Count: All 525 items checked; rows created for items with commit_hash
- Orphaned References: No item references commit_hash that doesn't exist in runs table

### ADR-0516 Compliance (K=2 Gate 5: ADR Compliance)
- task_registry.json: Remains read-only (0o444 permissions)
- DB Ownership: tasks.db owned by corvin (UID check)
- Audit Trail: Migration logged in audit.jsonl (core.task_tracking.store.events table)
- Tenant Isolation: All queries filtered by tenant_id, no bypass

---

## Rollback Strategy

### Rollback Points
1. **Pre-Migration Backup:** Copy tasks.db → tasks.db.backup-2026-09-27
2. **Batch Checkpoints:** After each 50-item batch:
   - If commit fails: roll back to pre-migration state
   - Log rollback reason (e.g., "Batch 5 schema violation")
3. **Full Restore:** Restore from backup if migration abort called

### Rollback Execution
```bash
# If migration fails
cp ~/.corvin/tenants/_default/global/task_tracking/tasks.db.backup-2026-09-27 \
   ~/.corvin/tenants/_default/global/task_tracking/tasks.db
# Re-run migration (idempotent: checks for existing IDs, skips duplicates)
```

---

## Expected Deliverables (Phase B K=2)

| Deliverable | Location | Status |
|-------------|----------|--------|
| Schema Mapping Doc | docs/implementations/PHASE_B_K2_TASK_MIGRATION_MAPPING.md | ✅ |
| Migration Script | scripts/migrate_task_registry_to_db.py | 🟡 Next |
| Batch Safety Tests | tests/integration/test_phase_b_batch_migration.py | 🟡 Next |
| Rollback Tests | tests/integration/test_phase_b_rollback_safety.py | 🟡 Next |
| K=2 Gate Validator | scripts/validate_k2_readiness_gates.py | 🟡 Next |

---

## Timeline Estimate

| Phase | Duration | Start |
|-------|----------|-------|
| Migration Execution | 1.5–2h | Now |
| Rollback Testing | 30–45m | After migration |
| K=2 Gate Validation | 30–45m | After rollback tests |
| **Total K=2** | **~3–4h** | **2026-09-27 02:00 UTC** |

---

## Sign-Off

- **Mapping Verified:** ✅ (schema matches ADR-2051 + store.py)
- **Rollback Strategy:** ✅ (backup + idempotent script)
- **ADR-0516 Compliance:** ✅ (tenant isolation, audit trail, read-only registry)
- **Ready for Phase B Execution:** ✅ Awaiting approval
