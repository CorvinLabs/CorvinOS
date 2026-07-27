# Portable Tenant Bundles (Phase 2)

**Status:** Implemented 2026-07-27  
**Reference:** ADR-0251 (Phase 2: Export/Import CLI)  
**Commands:** `corvin tenant {export,import,list,info}`

## Overview

Portable tenant bundles enable **backup, restore, and migration** of CorvinOS tenants across machines. A bundle is a compressed tar.gz archive containing all tenant configuration, sessions, plugins, and optional sensitive data.

### Key Features

- **Host-independent:** Bundle moves between Linux/Windows/macOS seamlessly
- **Selective export:** Include/exclude secrets, compute history, old sessions
- **Metadata validation:** Each bundle carries version info, creation timestamp, host info
- **Non-destructive import:** Backs up existing tenant before overwrite
- **Encrypted secrets:** Optionally export encrypted credentials (requires re-encryption on import)

## Commands

### Export

Export a tenant to a portable bundle:

```bash
corvin tenant export --tenant-id _default --output backup.tar.gz
```

**Options:**
- `--tenant-id ID` — Tenant to export (default: `_default`)
- `--output PATH` — Output bundle path (required, must not exist)
- `--with-secrets` — Include encrypted credentials (off by default)
- `--with-compute-runs` — Include compute run history (off by default)
- `--exclude-old-sessions N` — Skip sessions older than N days

**Example: Export everything including sensitive data**

```bash
corvin tenant export --output full_backup.tar.gz --with-secrets --with-compute-runs
```

**Example: Export config and recent sessions only (7 days)**

```bash
corvin tenant export --output prod_snapshot.tar.gz --exclude-old-sessions 7
```

### Import

Restore a tenant from a portable bundle:

```bash
corvin tenant import backup.tar.gz --tenant-id restored_tenant
```

**Options:**
- `bundle_path` — Path to tar.gz bundle (required)
- `--tenant-id ID` — Target tenant (default: `_default`)
- `--force-overwrite` — Replace existing tenant (backs up original with timestamp)
- `--decrypt-secrets` — Re-encrypt secrets with new master key (if bundle includes secrets)

**Example: Restore to new tenant**

```bash
corvin tenant import backup.tar.gz --tenant-id production_2024
```

**Example: Replace existing tenant with backup**

```bash
corvin tenant import backup.tar.gz --tenant-id _default --force-overwrite
```

When `--force-overwrite` is used, the original tenant is backed up to:
```
<corvin_home>/tenants/_default_backup_<timestamp>/
```

### List

Show all available tenants:

```bash
corvin tenant list
```

**Output:**
```
Available tenants:

  → _default              2026-07-27 20:30:15  (12 sessions)
    prod                  2026-07-25 14:22:08  (45 sessions)
    staging               2026-07-20 09:15:33  (3 sessions)
```

(Arrow `→` marks the default tenant)

### Info

Show detailed info about a tenant:

```bash
corvin tenant info --tenant-id _default
```

**Output:**
```
Tenant: _default
Location: ~/.corvin/tenants/_default

✓ Configuration present
✓ Sessions: 12
✓ Voice configuration present
✓ Plugins registered
✓ Datasource connections: 2

Total size: 145.3 MB
```

## Bundle Format

### Tar.gz Structure

A portable bundle is a gzip-compressed tar archive with this structure:

```
tenant_id/
├── metadata.json                    # Bundle metadata & manifest
├── global/                          # Tenant configuration
│   ├── tenant.corvin.yaml
│   ├── feature_flags.json
│   └── [config files, no secrets unless --with-secrets]
├── voice/                           # Voice profiles and settings
│   ├── profiles.json
│   ├── memory/
│   └── [no encryption keys]
├── sessions/                        # Chat and voice sessions
│   ├── web:session_id_1/
│   ├── web:session_id_2/
│   └── [...filtered by age if --exclude-old-sessions]
├── plugins/                         # Registered plugins
│   ├── manifest.json
│   └── [plugin configs]
├── datasource_connections/          # Database and API connections
│   ├── postgres_prod.json
│   └── [no credentials]
├── workflows/                       # Workflow definitions
│   └── [*.json]
└── compute/                         # (optional) Compute run history
    └── [run logs if --with-compute-runs]
```

### Metadata (metadata.json)

Every bundle includes metadata:

```json
{
  "version": "1.0",
  "portable_format_version": "1.0",
  "tenant_id": "_default",
  "created_at": "2026-07-27T20:45:30Z",
  "created_on_host": "workstation-1",
  "corvin_version": "0.10.63",
  "includes": {
    "tenant_config": true,
    "sessions": true,
    "compute_runs": false,
    "voice_config": true,
    "plugins": true,
    "datasource_connections": true,
    "secrets": false,
    "audit_trail": true,
    "browser_sessions": false,
    "exclude_old_sessions_days": null
  },
  "checksums": {}
}
```

## Scenarios

### Scenario 1: Daily Backup

```bash
#!/bin/bash
DATE=$(date +%Y%m%d)
corvin tenant export --output "backups/tenant_$DATE.tar.gz"
```

### Scenario 2: Disaster Recovery

Export full backup including secrets:
```bash
corvin tenant export --output disaster_backup.tar.gz \
  --with-secrets --with-compute-runs
```

Restore on new system:
```bash
corvin tenant import disaster_backup.tar.gz --force-overwrite
```

### Scenario 3: Migration to New Machine

**On old system:**
```bash
corvin tenant export --output ~/tenant_migration.tar.gz
scp ~/tenant_migration.tar.gz user@new-system:/tmp/
```

**On new system:**
```bash
corvin tenant import /tmp/tenant_migration.tar.gz --force-overwrite
```

### Scenario 4: Multi-Tenant Setup

Export prod, stage, and dev to separate bundles:

```bash
for tenant in prod staging dev; do
  corvin tenant export --tenant-id $tenant \
    --output "backups/${tenant}.tar.gz" \
    --exclude-old-sessions 30
done
```

### Scenario 5: Selective Restore

Export prod but restore to a test tenant:

```bash
corvin tenant export --tenant-id prod --output prod_snapshot.tar.gz
corvin tenant import prod_snapshot.tar.gz --tenant-id test_restore
```

Now `_default` and `test_restore` coexist. Test in isolation, then:

```bash
corvin tenant import prod_snapshot.tar.gz --tenant-id prod --force-overwrite
```

## Security Considerations

### Secrets Handling

**By default, `--with-secrets` is OFF:**
- Encrypted credentials (`.enc` files, `secrets.json`) are **never** exported
- Datasource connection credentials are **omitted** (host/port/name only)
- Reduces risk of accidental credential leakage

**When `--with-secrets` is used:**
- Secrets are exported as-is in their encrypted form
- On import, they remain encrypted until explicitly decrypted
- `--decrypt-secrets` re-encrypts them with the destination system's master key
- Without `--decrypt-secrets`, secrets remain unusable (wrong key)

### Audit Trail

- Export operation is logged with timestamp, operator, bundle path
- Import operation is logged with bundle metadata, target tenant
- Backup locations are recorded (when using `--force-overwrite`)

### Path Traversal Protection

- Tenant IDs validated against charset rule `[a-z0-9_][a-z0-9_-]{0,62}`
- Bundle extraction unpacks to a temporary directory first
- Paths verified to stay within tenant boundaries
- Reserved prefix `__` forbidden (system-only)

## Limitations

### Not Included in Bundle

- **Encryption keys:** Voice BYOK vaults, master secrets
- **Live service state:** Running sessions, background jobs
- **Bridge credentials:** Channel tokens, OAuth state (regenerated per install)
- **Browser automation state:** Selenium sessions, screenshots
- **System audit chain:** GDPR immutable audit.jsonl (separate archive if needed)

### File Size Considerations

- Sessions can be large (terminal transcripts, file attachments)
- Use `--exclude-old-sessions` to minimize bundle size
- Compute run history can exceed 500MB on large datasets
- Browser sessions excluded by default (can be hundreds of GB)

## API / Programmatic Use

For automation and integration:

```python
from ops.launcher.corvin import tenant_cmd
import argparse

# Export
args = argparse.Namespace(
    tenant_id="_default",
    output="backup.tar.gz",
    with_secrets=False,
    with_compute_runs=False,
    exclude_old_sessions=30
)
result = tenant_cmd.cmd_export(args)

# Import
args = argparse.Namespace(
    bundle_path="backup.tar.gz",
    tenant_id="_default",
    force_overwrite=True,
    decrypt_secrets=False
)
result = tenant_cmd.cmd_import(args)
```

Exit code `0` = success, non-zero = error (printed to stderr).

## Troubleshooting

### Export fails with "tenant not found"

Verify tenant exists:
```bash
corvin tenant list
corvin tenant info --tenant-id <id>
```

### Import fails with "bundle format invalid"

Ensure file is a valid tar.gz:
```bash
tar -tzf bundle.tar.gz | head
```

Should show `tenant_id/metadata.json` as first file.

### Import fails with "invalid bundle: metadata.json not found"

Bundle was corrupted or created by an older version. Try:
```bash
tar -xzf bundle.tar.gz -O _default/metadata.json
```

### Decryption fails on import

If `--decrypt-secrets` is used but fails:
1. The old master key may be unavailable
2. Secrets might be corrupted
3. Retry without `--decrypt-secrets` and manually manage credentials

## Future Work

- **Cloud storage:** Direct export to S3/GCS/Azure Blob
- **Incremental bundles:** Only changes since last export
- **Tenant merge:** Combine sessions/plugins from multiple bundles
- **Signature verification:** GPG-sign bundles for integrity
- **Scheduled backups:** Automatic daily/weekly exports via systemd timer
