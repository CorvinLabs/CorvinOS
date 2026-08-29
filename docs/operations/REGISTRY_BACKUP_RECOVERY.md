# Registry Backup & Recovery SOP (ADR-0250)

**Status:** Production-Ready  
**Last Updated:** 2026-08-28  
**Scope:** Plugin registry (`registry.yaml`) atomic writes, backup, and corruption recovery

---

## Overview

CorvinOS maintains a per-tenant plugin registry (`registry.yaml`) that tracks installed plugins, their status, and configuration. This SOP documents the fail-safe backup and recovery mechanisms that ensure data integrity even in the face of:

- Process crashes mid-write
- Disk full conditions
- Corrupted registry files
- Power loss during mutations

**Key Guarantees:**

1. **Atomic writes** — Registry mutations are all-or-nothing (tempfile + fsync + rename)
2. **Automatic backups** — A `.yaml.bak` file is created before each mutation
3. **Automatic recovery** — Corrupted registries auto-restore from backup on next load
4. **Audit trail** — Every corruption detection and recovery is logged
5. **Fail-closed** — Corruption never silently corrupts data or downgrades to empty

---

## How It Works

### 1. Atomic Write Process

When `registry.save()` is called, the following atomic sequence executes:

```
┌─────────────────────────────────────────────────────┐
│ 1. Create backup (if current registry exists)      │
│    registry.yaml.bak ← copy of registry.yaml       │
└─────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────┐
│ 2. Write to temp file (in same dir as registry)    │
│    .registry-XXXXX.tmp ← new payload               │
│    - fwrite() the entire payload                   │
│    - fsync() to guarantee disk write               │
│    - chmod 0o600 (owner read/write only)           │
└─────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────┐
│ 3. Atomic rename (POSIX atomic, no middle state)   │
│    .registry-XXXXX.tmp → registry.yaml             │
│    os.replace() is atomic on all platforms         │
└─────────────────────────────────────────────────────┘
```

**Crash Scenarios:**

| Scenario | Result |
|----------|--------|
| Crash during backup | Backup incomplete, but main registry unaffected (old backup still exists) |
| Crash during temp write | Temp file left on disk (cleaned up on next start), main registry unchanged |
| Crash during rename | Atomic rename means either old or new registry exists, never partial |
| Disk full during write | Temp file creation fails before touching main registry |

### 2. Backup Lifecycle

```
Time → 

Before first save:
  registry.yaml        ✓ (empty or from previous run)
  registry.yaml.bak    ✗ (doesn't exist)

After first save:
  registry.yaml        ✓ (version 1)
  registry.yaml.bak    ✗ (no backup on first save — nothing to preserve)

After second save (mutation):
  registry.yaml        ✓ (version 2)
  registry.yaml.bak    ✓ (version 1 — backup of previous state)

After third save (another mutation):
  registry.yaml        ✓ (version 3)
  registry.yaml.bak    ✓ (version 2 — previous state, updated)

If corruption detected on load:
  registry.yaml        ✗ (corrupt)
  registry.yaml.bak    ✓ (valid, restored to main)
  → Main registry recovered
```

### 3. Auto-Recovery Flow

```
TenantRegistry.load(auto_recover=True) called
                       ↓
        ┌───────────────┴───────────────┐
        ↓                               ↓
  Registry exists?                 Return empty registry
        │ YES                           (first load)
        ↓
  Try to parse YAML
        │
    ┌───┴────────────────┐
    ↓                    ↓
  Valid              Corrupted (ParserError, etc.)
    │                    │
    ↓                    ↓ (auto_recover=True)
  Return loaded      Check for backup
    registry             │
                    ┌────┴────────────┐
                    ↓                 ↓
                  Exists?          No backup
                    │                 │
           ┌────────┴────────┐        ↓
           ↓                 ↓    Raise RegistryCorrupt
         Valid            Corrupted
           │                 │
           ↓                 ↓
      Restore backup   Raise RegistryCorrupt
           │           ("backup also corrupted")
           ↓
      Return recovered registry
```

---

## Operational Procedures

### A. Normal Operation (No Intervention Needed)

**For operators:** Backups are created automatically; nothing special is required.

- On every registry mutation (install, enable, disable, configure), a backup is created
- Backups are stored as `<tenant>/plugins/registry.yaml.bak`
- If corruption is detected, recovery is automatic on the next load
- Audit trail records all corruption events

### B. Manual Inspection

**To check if a backup exists:**

```bash
ls -la ~/.corvin/tenants/_default/plugins/
# Should show:
#   registry.yaml       (current)
#   registry.yaml.bak   (backup, if mutations have occurred)
#   .registry.lock      (inter-process lock, normal)
```

**To inspect backup contents:**

```bash
cat ~/.corvin/tenants/_default/plugins/registry.yaml.bak
```

**To verify backup is valid YAML:**

```bash
python3 -c "
import yaml
with open('~/.corvin/tenants/_default/plugins/registry.yaml.bak') as f:
    data = yaml.safe_load(f)
    print(f'Valid. Contains {len(data.get(\"plugins\", {}))} plugins.')
"
```

### C. Manual Recovery (If Auto-Recovery Failed)

**Scenario:** Corrupted registry, auto-recovery not available.

**Step 1: Verify corruption**

```bash
cat ~/.corvin/tenants/_default/plugins/registry.yaml
# Output: gibberish or parse error
```

**Step 2: Check for backup**

```bash
ls -la ~/.corvin/tenants/_default/plugins/registry.yaml.bak
# If not found, jump to "No Backup Available" section
```

**Step 3: Restore from backup**

```bash
# Atomic restore
cp ~/.corvin/tenants/_default/plugins/registry.yaml.bak \
   ~/.corvin/tenants/_default/plugins/registry.yaml

# Verify
cat ~/.corvin/tenants/_default/plugins/registry.yaml | head -5
```

**Step 4: Restart services**

```bash
# Restart the gateway (which reloads registry at boot)
systemctl restart corvin-gateway

# OR restart console (if running standalone)
systemctl --user restart corvin-console
```

**Step 5: Verify recovery in audit trail**

```bash
tail -n 20 ~/.corvin/audit.jsonl | grep "plugin.registry"
# Should show: plugin.registry_corruption_detected event
```

### D. No Backup Available

**Scenario:** Registry corrupted, no backup exists (first mutation failed mid-write).

**Step 1: Check recovery possibilities**

```bash
# Last modification time of registry
stat ~/.corvin/tenants/_default/plugins/registry.yaml | grep Modify

# Check git history (if in a git repo)
git log --oneline -- .corvin/tenants/_default/plugins/registry.yaml | head -3
```

**Step 2: Reconstruct from audit trail (if possible)**

Plugin installations are recorded in the audit trail. If the registry is beyond repair:

```bash
grep "plugin.installed\|plugin.enabled\|plugin.disabled" ~/.corvin/audit.jsonl \
  | tail -n 100 \
  | jq '.details | {plugin_id, enabled}'
```

This gives you the list of plugins that should be in the registry.

**Step 3: Manual reconstruction (last resort)**

If the audit trail is insufficient, manually recreate the registry:

```yaml
# ~/.corvin/tenants/_default/plugins/registry.yaml
spec:
  schema_version: '1.0'
plugins:
  # Re-add plugins by their installed_by audit events
  # (Consult your local backup strategy or plugin sources)
```

**Step 4: Notify the maintainer**

This scenario indicates a serious event (two-part failure: corruption + no backup). Open an issue with:

- Timestamp of the corruption
- Audit trail excerpt showing what was lost
- Recovery steps you took

---

## Audit Trail Recording

Every registry-related event is logged to the hash-chained audit trail:

### Corruption Detection

When `TenantRegistry.load()` detects corruption:

```json
{
  "event_type": "plugin.registry_corruption_detected",
  "tenant_id": "_default",
  "timestamp": "2026-08-28T14:35:22Z",
  "details": {
    "path": "/home/user/.corvin/tenants/_default/plugins/registry.yaml",
    "error": "ParserError",
    "recovery_attempted": true
  }
}
```

### Successful Recovery

When auto-recovery succeeds:

```json
{
  "event_type": "plugin.registry_corrupted_recovered",
  "tenant_id": "_default",
  "timestamp": "2026-08-28T14:35:23Z",
  "details": {
    "backup_path": "/home/user/.corvin/tenants/_default/plugins/registry.yaml.bak",
    "plugins_recovered": 5
  }
}
```

### Registry Mutations

Every enable/disable/install/uninstall records the change:

```json
{
  "event_type": "plugin.installed",
  "tenant_id": "_default",
  "timestamp": "2026-08-28T14:35:24Z",
  "details": {
    "plugin_id": "acme-notify",
    "origin": "vetted",
    "pii_risk": "low"
  }
}
```

---

## Testing & Validation

### Unit Tests

Registry atomic writes are covered by 18+ unit tests in `core/plugins/tests/test_registry_atomic_writes.py`:

```bash
python3 -m unittest core.plugins.tests.test_registry_atomic_writes -v
```

**Test Coverage:**

- **Atomic Writes** (4 tests): temp file cleanup, mode 0o600, idempotency, failure safety
- **Backup Creation** (3 tests): backup timing, permissions, rollover
- **Corruption Detection** (3 tests): malformed YAML, wrong type, invalid structure
- **Recovery** (5 tests): auto-recovery, no-backup fallback, corrupted-backup handling
- **Crash Simulation** (1 test): mid-write failure with backup
- **Multi-Tenant** (1 test): per-tenant backup isolation
- **Locking** (1 test): concurrent mutation safety
- **Audit Trail** (1 test): corruption event recording

### End-to-End Validation

**Test a crash scenario locally:**

```bash
#!/bin/bash
# Simulate process crash mid-write

CORVIN_HOME=~/.corvin
REGISTRY="$CORVIN_HOME/tenants/_default/plugins/registry.yaml"
BACKUP="$REGISTRY.bak"

# Create initial registry
python3 -c "
from corvin_plugins.state import TenantRegistry
reg = TenantRegistry(Path('$REGISTRY').expanduser())
reg.records['plugin1'] = _record('plugin1')
reg.save()
"

# Create backup
cp "$REGISTRY" "$BACKUP"

# Corrupt the registry mid-write (simulate)
echo '{ invalid yaml' > "$REGISTRY"

# Verify auto-recovery restores it
python3 -c "
from corvin_plugins.state import TenantRegistry
reg = TenantRegistry.load(auto_recover=True)
assert 'plugin1' in reg.records, 'Recovery failed!'
print('✓ Recovery successful')
"
```

---

## Troubleshooting

### Backup Not Created

**Problem:** `registry.yaml.bak` doesn't exist after a mutation.

**Cause:** Backup creation can fail silently (logged as warning) if the original registry is unreadable.

**Solution:**

```bash
# Check permissions
ls -la ~/.corvin/tenants/_default/plugins/registry.yaml
chmod 0o600 ~/.corvin/tenants/_default/plugins/registry.yaml

# Retry the mutation (e.g., enable a plugin via Console)
# Next mutation should create the backup
```

### Recovery Failed: Backup Also Corrupted

**Problem:** Both `registry.yaml` and `registry.yaml.bak` are corrupt.

**Likelihood:** Extremely rare (requires two independent corruptions).

**Recovery:**

1. Check system health (disk errors, power issues)
2. Use audit trail to reconstruct the registry (see Section D above)
3. Open an issue with details

### Permission Denied on Backup

**Problem:** `Permission denied` when accessing `.yaml.bak`.

**Cause:** File was created by a different user or process.

**Solution:**

```bash
# Check permissions
stat ~/.corvin/tenants/_default/plugins/registry.yaml.bak

# If not owner, fix it
sudo chown $USER ~/.corvin/tenants/_default/plugins/registry.yaml.bak
chmod 0o600 ~/.corvin/tenants/_default/plugins/registry.yaml.bak
```

---

## Implementation Details

### Code Locations

- **Main implementation:** `core/plugins/corvin_plugins/state.py`
  - `TenantRegistry.save()` — Atomic write with backup creation (line ~322)
  - `TenantRegistry.restore_from_backup()` — Recovery from backup (line ~367)
  - `TenantRegistry.load()` — Auto-recovery on corruption (line ~275)

- **Tests:** `core/plugins/tests/test_registry_atomic_writes.py`
  - 18 comprehensive tests covering all paths

- **Audit integration:** `core/plugins/corvin_plugins/state.py`
  - `_audit()` function emits hash-chained events (line ~374)

### Platform Support

| Platform | Atomic Rename | File Locking | Status |
|----------|---------------|--------------|--------|
| Linux    | ✓ (POSIX)     | fcntl        | Fully supported |
| macOS    | ✓ (POSIX)     | fcntl        | Fully supported |
| Windows  | ✓ (MoveFileEx)| None (shim)  | Supported (no file lock) |

**Note:** Windows does not support POSIX advisory locks, so inter-process locking relies on the file lock at `~/.corvin/tenants/_default/plugins/.registry.lock`. Intra-process locking uses `threading.RLock()`.

---

## Related ADRs & Concepts

- **ADR-0250 Part 1:** Registry cleanup — atomic writes + backup/recovery
- **ADR-0250 Part 2:** Validation + audit trail (Phase 2)
- **ADR-0007:** Multi-tenant architecture
- **CLAUDE.md § Compliance Baseline:** Audit trail integrity (GDPR Art. 30, 32)

---

## Support & Escalation

**For questions or issues:**

1. Check the troubleshooting section above
2. Review audit trail: `grep "plugin.registry" ~/.corvin/audit.jsonl`
3. Run unit tests: `python3 -m unittest core.plugins.tests.test_registry_atomic_writes`
4. Open an issue with:
   - Timestamp of the event
   - Audit trail excerpt
   - Output of `ls -la ~/.corvin/tenants/_default/plugins/`
   - System info (OS, Python version, available disk space)

---

**Last Updated:** 2026-08-28 | **Status:** Production-Ready
