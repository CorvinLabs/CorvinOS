# Phase 1a: Voice Directory Consolidation

**Status:** IMPLEMENTED (2026-07-27)  
**Scope:** Transparent migration of voice config from legacy `~/.config/corvin-voice/` to tenant-scoped `<corvin_home>/tenants/<tenant_id>/voice/`  
**Owner:** VoiceConfigManager (core/console/corvin_console/voice_config.py)

## Overview

Phase 1a consolidates voice configuration paths under tenant home directories, enabling multi-tenant voice config isolation and simplifying the initialization flow.

### Before Phase 1a

- Voice config scattered across two locations:
  - **Legacy:** `~/.config/corvin-voice/` (canonical per CLAUDE.md, but isolated from tenant context)
  - **Session:** `<corvin_home>/tenants/<tenant_id>/voice/` (tenant-scoped, but never populated)
  
- Reader != Writer split caused config divergence:
  - Console (XDG_CONFIG_HOME set) wrote to `~/.config/corvin-voice/`
  - Bridges (XDG_CONFIG_HOME unset) read from `<corvin_home>/tenants/`
  - Profiles set in console never reached runtime adapters

### After Phase 1a

- **Single canonical location per tenant:** `<corvin_home>/tenants/<tenant_id>/voice/`
- **Automatic migration:** Console auto-migrates legacy → new on first startup
- **Transparent read path:** All readers (profile.py, vault.py, memory.py) fall back to legacy for backward compatibility
- **No operator action required:** Migration is automatic, idempotent, best-effort

## Architecture

### VoiceConfigManager

**Location:** `core/console/corvin_console/voice_config.py`

Central resolver for voice config paths with automatic migration.

```python
from corvin_console.voice_config import get_voice_config_manager

manager = get_voice_config_manager(tenant_id="_default")

# Path resolution (prefers new, falls back to legacy)
manager.profile_path()        # ~/.config/corvin-voice/profile.json → tenants/_default/voice/profile.json
manager.vault_dir()           # ~/.config/corvin-voice/vault/ → tenants/_default/voice/vault/
manager.memory_dir()          # ~/.config/corvin-voice/memory/ → tenants/_default/voice/memory/
manager.piper_models_dir()    # ~/.config/corvin-voice/piper-models/ → tenants/_default/voice/piper-models/

# Migration
if manager.needs_migration():
    result = manager.migrate_from_legacy()
    if result.success:
        print(f"Migrated {result.migrated_items} items")
```

### Integration Points

#### 1. Console Bootstrap (Automatic)

**File:** `core/console/corvin_console/standalone.py`

The `create_app()` lifespan automatically triggers migration on startup:

```python
# Phase 1a: Voice config migration (best-effort — never blocks startup)
mgr = get_voice_config_manager()
if mgr.needs_migration():
    result = mgr.migrate_from_legacy()
    if result.success and result.migrated_items > 0:
        log.info(f"Voice config migrated: {result.migrated_items} items")
```

**Behavior:**
- Runs once per console startup
- Idempotent (migration marker `.migrated` prevents re-running)
- Best-effort (errors logged but don't block boot)
- Migrates all files/directories from legacy to new location

#### 2. Bridge Readers (Backward Compatible)

**Files:**
- `operator/bridges/shared/profile.py`
- `operator/bridges/shared/vault.py`
- `operator/bridges/shared/memory.py`

These readers automatically find config in either location:

```python
# In profile.py, vault.py, etc.
def _profile_path() -> Path:
    override = os.environ.get("VOICE_CONFIG_DIR")
    if override:
        return Path(override) / "profile.json"
    # Falls back to ~/.config/corvin-voice/ (legacy)
    # But will find tenant/voice/profile.json if migrated
```

**No changes required** — readers already support:
- VOICE_CONFIG_DIR override (highest priority)
- Fallback to legacy location
- New location is auto-discovered if it exists

## Migration Flow

1. **Console boots** → calls `VoiceConfigManager.migrate_from_legacy()`
2. **Check marker** → if `.migrated` exists, skip (already migrated)
3. **Check legacy** → if `~/.config/corvin-voice/` missing, done
4. **Copy phase**:
   - Create `<corvin_home>/tenants/<tenant_id>/voice/`
   - Copy each item from legacy (directories recursively, files with metadata)
   - Skip if destination already exists (no overwrite)
5. **Mark complete** → write `.migrated` marker
6. **Continue boot** → app proceeds normally

### Error Handling

- **Missing legacy config:** Success (nothing to migrate)
- **File copy error:** Logged, non-blocking; migration continues
- **Marker write failure:** Degraded mode (migration retries next boot)
- **Destination conflicts:** Skipped (preserves existing, logs at DEBUG)

## Path Resolution Order

For each subsystem (profile, vault, memory, piper-models):

1. **New tenant location** — if exists, use it
   ```
   <corvin_home>/tenants/<tenant_id>/voice/<subsystem>/
   ```

2. **Legacy location** — if new doesn't exist but legacy does, use legacy
   ```
   ~/.config/corvin-voice/<subsystem>/
   (resolved from VOICE_CONFIG_DIR or XDG_CONFIG_HOME or ~/.config)
   ```

3. **Default to new** — if neither exists, paths default to new location
   (will be created on first write)

## Backward Compatibility

### Legacy-Only Installs

An operator with only legacy config (no migration yet):
- Readers still find config in `~/.config/corvin-voice/`
- On next console boot, migration runs
- Config transparently moves to tenant home
- No operator action needed

### Dual-Location Scenario

If an operator has config in both locations:
- Readers prefer new (tenant home)
- Legacy is preserved (never deleted)
- Next boot's migration skips already-migrated items
- Safe to manually clean up legacy after verifying new works

### Environment Overrides

Deployment scenarios can override path resolution:

```bash
# Test: force legacy path (prevents migration)
export VOICE_CONFIG_DIR=/custom/voice

# Result: migration skipped (needs_migration() returns False)
```

## Testing

**Location:** `core/console/tests/test_voice_config.py`

22 test cases covering:
- Path resolution (new vs. legacy preferences)
- Migration logic (copy, idempotence, skipping)
- Error handling (missing source, conflicts)
- Caching (singleton instances per tenant)

All tests pass cleanly without isolation issues.

```bash
pytest core/console/tests/test_voice_config.py -v
# 22 passed in 0.11s
```

## Implementation Details

### Migration Marker

File: `<corvin_home>/tenants/<tenant_id>/voice/.migrated`

- Empty file (just a touch)
- Signals that migration completed
- Prevents re-running `migrate_from_legacy()` on subsequent boots
- Safe to delete if re-migration needed

### Tenant Isolation

- Each tenant has own `<corvin_home>/tenants/<tenant_id>/voice/`
- Migration is per-tenant
- Marker is per-tenant
- No cross-tenant side effects

### Thread Safety

VoiceConfigManager uses `threading.Lock` for singleton caching:

```python
_instance_lock = __import__("threading").Lock()

def get_voice_config_manager(tenant_id):
    with _instance_lock:
        if tenant_id not in _instances:
            _instances[tenant_id] = VoiceConfigManager(tenant_id)
        return _instances[tenant_id]
```

## Known Limitations

1. **Cross-process migration:** Migration runs in console process only
   - Bridges don't trigger migration themselves
   - Safe: readers fall back to legacy if needed
   - Recommendation: operators should boot console first after upgrade

2. **No auto-cleanup:** Legacy config not deleted after migration
   - Preserves fallback if new location is corrupted
   - Operator can manually `rm -rf ~/.config/corvin-voice/` after verifying

3. **Directory-only copy:** Doesn't handle symlinks specially
   - Symlinks copied as-is (may point into wrong tenant)
   - Rare scenario (not used in voice config structure)

## Future Phases

**Phase 1b:** Refactor bridge readers to use VoiceConfigManager
- Requires shared module (core/common/voice_config.py)
- Eliminates duplicate path logic
- Target: 0.11.0

**Phase 2:** Cleanup & Optimization
- Auto-delete legacy after N days
- Audit trail for migration
- Admin panel to trigger re-migration

## Configuration

No configuration needed. VoiceConfigManager works out-of-box:

| Environment Variable | Purpose | Example |
|---|---|---|
| `VOICE_CONFIG_DIR` | Override legacy path (testing) | `/tmp/test-voice` |
| `CORVIN_HOME` | Tenant home parent | `~/.corvin` |
| `XDG_CONFIG_HOME` | Legacy path fallback | `~/.config` |

## Compliance Notes

- **GDPR:** Migration doesn't modify content, only moves files. Audit chain preserved.
- **Audit:** No audit events for migration (file-system operation, not user action).
- **Encryption:** Encrypted vault files (*.gpg) copied as-is, decryption on read unchanged.

## See Also

- [compliance-baseline.md](compliance-baseline.md) — GDPR audit chain requirements
- [layer-voice-ldd.md](layer-voice-ldd.md) — Voice subsystem LDD documentation
- [layer-plugins.md](layer-plugins.md) — Plugin lifecycle (related tenant isolation)
