# Audit-Trail Integration Spec — Tenant-Native Skills v0.3

**Status:** Specification (Phase 0.2)  
**Compliance:** GDPR Art. 30, 32 (Processing Record)  

---

## Overview

Every Skill operation (create, update, export, promote, delete) must be audit-logged and hash-chained.

This spec defines:
- **Who writes** audit entries (SkillManager vs SkillForge)
- **When** entries are written (pre/post operation)
- **What** is logged (event schema)
- **How** hash-chain is maintained
- **Validation** via daily cron

---

## Event Types

| Event | Trigger | Logged By | Data |
|-------|---------|-----------|------|
| `skill_created` | Operator creates skill via `/api/skills/create` | SkillManager | skill_id, scope, version |
| `skill_modified` | Skill body/meta updated | SkillManager | skill_id, changes (list) |
| `skill_deleted` | Skill removed | SkillManager | skill_id, scope, reason |
| `skill_exported` | Exported to GitHub | ExportManager | skill_id, repo, branch, commit_sha |
| `skill_promoted` | _local/ → _shared/ | SkillManager | old_scope, new_scope, version_change |
| `skill_imported` | Imported from GitHub | ImportManager | skill_id, source_repo, conflict_resolution |
| `skill_dependency_added` | Dependency added to skill | SkillManager | skill_id, dep_id, dep_version |
| `skill_enabled` | Skill toggled on | PreferenceManager | skill_id, tenant_id |
| `skill_disabled` | Skill toggled off | PreferenceManager | skill_id, tenant_id |
| `skill_sync_started` | GitHub sync begins | SyncManager | sync_type (push/pull) |
| `skill_sync_completed` | GitHub sync ends | SyncManager | sync_type, status (success/failed), count |
| `config_updated` | tenant.corvin.yaml changed | ConfigManager | config_keys_modified (list) |
| `migration_started` | Migration from ~/.claude/ begins | MigrationManager | from_path, to_path, skill_count |
| `migration_completed` | Migration finishes | MigrationManager | status (success/rollback), skill_count |

---

## Event Schema

```json
{
  "timestamp": "2026-08-19T10:30:00Z",
  "event_type": "skill_exported",
  "tenant_id": "_default",
  "actor": "shumway",
  "session_id": "voice/discord/1501315335750684803",
  
  "details": {
    "skill_id": "academic-paper-generation",
    "scope": "_shared",
    "repo": "github:shumway/dotcloud-backup",
    "branch": "main",
    "commit_sha": "abc123def456",
    "export_status": "success"
  },
  
  "prior_hash": "sha256:xyz...",  # Previous entry's hash
  "event_hash": "sha256:abc...",  # THIS entry's hash (for chaining)
  "sequence_number": 1247
}
```

**Hash Calculation:**
```python
import hashlib

def calculate_event_hash(event: dict) -> str:
    """Calculate hash of this event (for chaining)"""
    # Exclude hash fields from calculation
    hashable_event = {k: v for k, v in event.items() 
                      if k not in ["prior_hash", "event_hash", "sequence_number"]}
    
    json_str = json.dumps(hashable_event, sort_keys=True)
    return "sha256:" + hashlib.sha256(json_str.encode()).hexdigest()
```

---

## Write Semantics

### **When to write:**

1. **PRE-OPERATION:** Attempt to write audit entry BEFORE the operation
   - If audit write fails: STOP, don't proceed with operation
   - Example: `skill_exported` logged before GitHub push

2. **POST-OPERATION:** Write after operation completes
   - If operation fails: still log with status=failed
   - Example: `skill_sync_completed` with status

### **Atomic Operations:**

```python
class AuditedSkillManager:
    def export_skill(skill_id: str, repo: str) -> ExportResult:
        """Export with audit guarantees"""
        
        # Step 1: Write PRE event
        pre_event = {
            "event_type": "skill_export_started",
            "skill_id": skill_id,
            "status": "in_progress"
        }
        audit_id = write_audit_trail(pre_event)  # Fails fast if audit broken
        
        # Step 2: Do the export
        try:
            export_result = github_exporter.export(skill_id, repo)
            
            # Step 3: Write POST event (success)
            post_event = {
                "event_type": "skill_exported",
                "skill_id": skill_id,
                "status": "success",
                "commit_sha": export_result.commit_sha
            }
            write_audit_trail(post_event)
            return export_result
        
        except Exception as e:
            # Write FAILURE event
            failure_event = {
                "event_type": "skill_exported",
                "skill_id": skill_id,
                "status": "failed",
                "error": str(e)
            }
            write_audit_trail(failure_event)
            raise  # Propagate error
```

---

## Hash-Chain Validation

### **Daily Validation (Cron Job)**

```python
def daily_audit_chain_validation(tenant_id: str) -> ValidationResult:
    """Run daily at 02:00 UTC"""
    
    audit_path = f"~/.corvin/tenants/{tenant_id}/audit.jsonl"
    
    with open(audit_path) as f:
        entries = [json.loads(line) for line in f]
    
    errors = []
    
    for i, entry in enumerate(entries):
        # Check sequence numbers are monotonic
        if i > 0 and entry["sequence_number"] != entries[i-1]["sequence_number"] + 1:
            errors.append(f"Sequence break at {i}: {entry['sequence_number']}")
        
        # Check hash chain (current.event_hash == next.prior_hash)
        if i < len(entries) - 1:
            current_hash = entry["event_hash"]
            next_prior_hash = entries[i+1]["prior_hash"]
            
            if current_hash != next_prior_hash:
                errors.append(f"Hash chain break at {i}->{i+1}")
        
        # Check event_hash is correct
        computed_hash = calculate_event_hash(entry)
        if computed_hash != entry["event_hash"]:
            errors.append(f"Event hash mismatch at {i}")
    
    if errors:
        # Alert: audit trail is corrupted
        alert_operator(f"Audit trail corruption detected: {errors}")
        return ValidationResult(valid=False, errors=errors)
    
    audit_log("audit_validation_passed", len(entries))
    return ValidationResult(valid=True, entries_validated=len(entries))
```

### **Emergency Recovery**

If audit trail is corrupted:

```python
def recovery_from_corrupted_audit() -> RecoveryResult:
    """Last-resort recovery (uses git log + manifest timestamps)"""
    
    # Fallback: reconstruct from Git History + Manifest Timestamps
    events = []
    
    # 1. Get all commits affecting skills/
    git_log = subprocess.run(
        ["git", "log", "--oneline", "--", "skills/"],
        capture_output=True
    ).stdout.decode().split("\n")
    
    for commit_line in git_log:
        commit_sha = commit_line.split()[0]
        # Map commit to audit event
        # (skill creation, modification, deletion)
    
    # 2. Use manifest.json timestamps
    for skill_dir in list_all_skills():
        manifest = load_manifest(skill_dir)
        # Fill in timeline from manifest.created, manifest.last_modified
    
    # 3. Write recovery audit log
    recovery_event = {
        "event_type": "audit_trail_recovered",
        "recovery_method": "git_log + manifest_timestamps",
        "entries_recovered": len(events)
    }
    write_audit_trail(recovery_event)
    
    return RecoveryResult(entries_recovered=len(events))
```

---

## Tenant Isolation

All audit operations must filter by `tenant_id`:

```python
def write_audit_trail(event: dict, tenant_id: str):
    """Write to tenant-specific audit file"""
    audit_path = f"~/.corvin/tenants/{tenant_id}/audit.jsonl"
    
    # Load last entry (for hash chaining)
    last_entry = get_last_audit_entry(audit_path)
    
    # Build new event
    event["tenant_id"] = tenant_id
    event["sequence_number"] = last_entry.sequence_number + 1 if last_entry else 1
    event["prior_hash"] = last_entry.event_hash if last_entry else "sha256:0"
    event["timestamp"] = datetime.now(timezone.utc).isoformat()
    
    # Calculate hash
    event["event_hash"] = calculate_event_hash(event)
    
    # Append to file (atomic)
    with open(audit_path, "a") as f:
        f.write(json.dumps(event) + "\n")
        f.flush()
        os.fsync(f.fileno())  # Ensure disk write
```

---

## Failure Scenarios

| Scenario | Behavior | Recovery |
|----------|----------|----------|
| **Audit write fails (disk full)** | Abort operation. Error to operator. | Clear space, retry. |
| **Audit file corrupted** | Daily cron detects. Alert operator. | Use git log fallback. |
| **Network drops during export** | POST event marked "failed". | Operator retries export. |
| **Operator deletes audit.jsonl** | Data is lost (no recovery). | Restore from backup. |
| **Hash chain breaks at entry 500** | Cron finds it. Entries 501+ are suspect. | Investigate + fix manually or rollback to backup. |

---

## Ownership

| Component | Owner | Responsibility |
|-----------|-------|---|
| **SkillManager** | Core Platform | Log skill CRUD operations |
| **ExportManager** | GitHub Integration | Log export/import operations |
| **PreferenceManager** | Config System | Log preference changes |
| **SyncManager** | GitHub Integration | Log sync start/completion |
| **ConfigManager** | Config System | Log config changes |
| **MigrationManager** | Core Platform | Log migration events |
| **ValidationCron** | Ops/Infrastructure | Daily chain validation |

---

## Performance Targets

- Write latency: <10ms per event (disk I/O)
- Daily validation: <100ms for 10,000 entries
- Recovery time (if needed): <5 min

---

## Testing

- ✅ Unit: audit write/read, hash calculation, chain validation
- ✅ Integration: audit write + operation atomicity
- ✅ E2E: full migration flow with audit trail
- ✅ Failure: disk full, corrupted file, network drops

---

## Sign-Off

**Required before Phase 1:**

- [ ] Audit schema finalized
- [ ] All event types documented
- [ ] Hash-chain algorithm tested
- [ ] Validation cron spec'd
- [ ] Tenant isolation verified

