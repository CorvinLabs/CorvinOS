# Phase 1a: Voice Directory Consolidation

**Status:** IMPLEMENTED (2026-07-27) · **AMENDED 2026-09-07** (migration allow-list + legacy-source isolation)  
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
- Migrates **only the allow-listed configuration artefacts** (see below) — never
  runtime, audit or build state

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
   - Copy each **allow-listed** item from legacy (directories recursively, files
     with metadata); everything else is left in place and reported in
     `MigrationResult.warnings`
   - Skip if destination already exists (no overwrite)
5. **Mark complete** → write `.migrated` marker
6. **Continue boot** → app proceeds normally

### Error Handling

- **Missing legacy config:** Success (nothing to migrate)
- **File copy error:** Logged, non-blocking; migration continues
- **Marker write failure:** Degraded mode (migration retries next boot)
- **Destination conflicts:** Skipped (preserves existing, logs at DEBUG)

## Migration Allow-List (2026-09-07 — load-bearing)

`migrate_from_legacy()` used to `copytree` **every** subdirectory of the legacy
directory. `~/.config/corvin-voice/` is not a pure config directory: it is also
the Layer-16 audit anchor-key directory, a virtualenv, and a pid/lock/log store.
On the maintainer's host it measured **1.4 GB / 275,414 files**, of which
**268,220** were `mac_active_chains/` markers — and all of it was copied into
every freshly initialised voice home (see "Legacy source isolation" below for
why that fired in tests).

The migration now carries an explicit **allow-list**, declared in
`core/console/corvin_console/voice_config.py`:

| Constant | Contents |
|---|---|
| `MIGRATABLE_FILES` | `profile.json` · `config.json` · `secrets.json` · `service.env` · `.env` · `license.jwt` |
| `MIGRATABLE_DIRS` | `vault/` · `memory/` · `piper-models/` · `whisper-models/` |

Everything else stays in the legacy directory and is reported as a warning.
Deliberate exclusions and why:

| Excluded | Reason |
|---|---|
| `audit.jsonl`, `audit_anchor.key`, `audit_mac_active`, `audit_manifest_mac_active` | Layer-16 audit chain + its anchor key. An audit chain is evidence and is **never** duplicated to a second location (GDPR Art. 30/32). |
| `mac_active_chains/`, `chain_ids/`, `chain_tails/`, `manifest_mac_active_dirs/` | Out-of-tree audit anchors keyed to the anchor key's own directory — meaningless anywhere else, and the bulk of the 1.4 GB. |
| `forge/` | Holds a second `audit.jsonl` hash chain plus generated tools/skills. |
| `google/`, `venv/` | Python virtualenvs (147 MB) — build artefacts, not config. |
| `whatsapp/` | pid files. |
| `maintainer.key`, `maintainer.env` | Host-level maintainer credentials, not tenant voice config; duplicating secrets is the wrong direction. |
| `current.pgid`, `tts.lock`, `*.log`, `*.bak-*`, `.*_setup_complete` | Runtime state and markers. |

**Allow-list, not deny-list — deliberate.** A deny-list fails open: the next
runtime directory dropped into `~/.config/corvin-voice/` would silently be
copied again, which is exactly how this bug arose. An allow-list fails closed:
the worst case is "a new config file is not carried over", which is visible,
recoverable, and already covered by the legacy read-fallback.

## Legacy Source Isolation (2026-09-07 — load-bearing)

`~/.config/corvin-voice/` is **user-global**; `<corvin_home>` is **per-install**.
The two used to be paired implicitly, so any process that pointed `CORVIN_HOME`
at a throwaway directory — every test that builds the console app does exactly
that — resolved its legacy source to the operator's real `~/.config/corvin-voice`,
reported `needs_migration() == True`, and copied 1.4 GB into `/tmp`. That alone
made `core/console/tests/test_adapter_ensured_at_boot.py` take 37 s.

`VoiceConfigManager.legacy_source_allowed()` now decides whether the legacy
directory may be read from at all:

| Situation | Legacy source admissible? |
|---|---|
| `VOICE_CONFIG_DIR` set (explicit) | **Yes** — always; a deliberate act by the operator or a test |
| `CORVIN_HOME` unset, or equal to the ambient home | **Yes** — the real operator upgrade path |
| `CORVIN_HOME` redirected elsewhere, no `VOICE_CONFIG_DIR` | **No** — an isolated home never inherits ambient user config |

"Ambient home" is what `corvin_home()` resolves to with `CORVIN_HOME` unset: the
repo-local `.corvin` in a source checkout, else `~/.corvin`
(`VoiceConfigManager._ambient_corvin_home()`).

When the source is not admissible, `has_legacy_config()`, `needs_migration()`
and every legacy read-fallback (`profile_path()`, `vault_dir()`, `memory_dir()`,
`piper_models_dir()`) behave as if the legacy directory did not exist.

### Operator note — pruning the dead `mac_active_chains/` markers

The migration no longer copies them, but on a host that ran the pre-F-A14 writer
the markers are still on disk (268,220 files / ~1.1 GB of block allocation on the
maintainer's machine). They are still **read** by
`forge.security_events._chain_had_mac()`, so a blind `rm -rf` would turn a
mac-stripped chain into a "never had a mac" chain and weaken the Layer-16
strip detector. Prune selectively instead — keep every genesis-keyed (`g-*`)
marker and every path-keyed marker whose chain file still exists; a path-keyed
marker for a path that no longer exists can never be consulted:

```bash
python3 - <<'EOF'   # add --apply as the first argv to actually delete
import hashlib, os, sys
from pathlib import Path
MK = Path.home() / ".config" / "corvin-voice" / "mac_active_chains"
ROOTS = [Path.home() / ".corvin",                       # add any other
         Path.home() / "projects" / "CorvinOS" / ".corvin",   # CORVIN_HOME roots
         Path.home() / ".config" / "corvin-voice"]
names = set(os.listdir(MK))
keep = {n for n in names if n.startswith("g-")}
for r in ROOTS:
    if r.is_dir():
        for c in r.rglob("*.jsonl"):
            keep.add(hashlib.sha256(os.path.abspath(str(c)).encode()).hexdigest()[:32])
dead = sorted(names - keep)
print(f"markers={len(names)} keep={len(names & keep)} dead={len(dead)} "
      f"frees~{len(dead) * 4096 / 2**30:.2f} GiB")
if "--apply" in sys.argv:
    for n in dead:
        os.unlink(MK / n)
    print("pruned", len(dead))
EOF

# then reclaim the ~15 MB directory inode itself
cd ~/.config/corvin-voice \
  && cp -a mac_active_chains mac_active_chains.new \
  && rm -rf mac_active_chains && mv mac_active_chains.new mac_active_chains
```

Note the writer can still mint a *path-keyed* marker for a chain whose genesis
cannot be read yet, so the directory can regrow slowly; the F-A14 tmp-chain
guard in `security_events._skip_out_of_tree_markers()` is what stops the bulk.

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

27 test cases covering:
- Path resolution (new vs. legacy preferences)
- Migration logic (copy, idempotence, skipping)
- Migration allow-list (`TestMigrationAllowList`) — a real operator upgrade still
  carries config/vault/models; audit chain, anchor key, markers, virtualenv, pid
  files and logs are never copied
- Legacy source isolation (`TestLegacySourceIsolation`) — a redirected
  `CORVIN_HOME` never reaches into the ambient `~/.config/corvin-voice`
- Error handling (missing source, conflicts)
- Caching (singleton instances per tenant)

All tests pass cleanly without isolation issues.

```bash
pytest core/console/tests/test_voice_config.py -v
# 27 passed in 0.13s
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
