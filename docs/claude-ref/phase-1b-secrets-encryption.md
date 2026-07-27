# Phase 1b: Encrypted Secrets Storage

**Status:** Complete & Production-Ready (2026-07-27)  
**Provider:** SecretsStore in provider_keys.py (Single Source of Truth)  
**Single Point of Integration:** operator/bridges/shared/provider_keys.py (channels: resolve_key, resolve_by_env_var)  
**Phase 2 Integration:** Tenant export/import with `--with-secrets` now includes encryption keys → secrets portable across machines

## Overview

Phase 1b adds encrypted at-rest storage for API keys and provider credentials using Fernet (AES-128-CBC) encryption. Secrets are stored in a tenant-scoped, encrypted secrets.enc file with per-tenant master keys.

**Single Source of Truth (SSOT):** provider_keys.py is extended with the SecretsStore class and remains the canonical entry point for all credential resolution. Tests verify that SecretsStore and all resolver functions return identical values.

## Architecture

### Three-Tier Credential Resolution

Every call to resolve_key/resolve_by_env_var checks three sources in strict precedence order:

```
1. Process environment variable (OPENAI_API_KEY=sk-... or CORVIN_STT_OPENAI_KEY=sk-...)
   ↓ (if not found)
2. Encrypted secrets.enc (Phase 1b) — Fernet-encrypted tenant store
   ↓ (if not found)
3. Service.env file (legacy, still supported)
```

**Rationale:** Process env overrides everything (allows testing, temporary overrides). Encrypted store wins over plaintext files (at-rest encryption > no encryption). Plaintext service.env is the fallback for backward compatibility.

### Directory Structure

```
~/.corvin/tenants/_default/
├── global/
│   ├── tenant.corvin.yaml          # Tenant configuration
│   └── secrets.enc                 # NEW: Encrypted secrets (0o600)
└── keys/
    └── tenant_master.key           # NEW: Encryption master key (0o600)
```

Each tenant has:
- **secrets.enc**: Versioned JSON envelope containing Fernet-encrypted credentials
- **tenant_master.key**: 32-byte Fernet key, readable by owner only

### Encryption Details

**Algorithm:** Fernet (symmetric encryption, AES-128-CBC with HMAC authentication)

**Key:** 32-byte random key per tenant, generated on first use

**File Format (secrets.enc):**

```json
{
  "version": "1.0",
  "encrypted_at": "2026-07-27T12:34:56Z",
  "algorithm": "AES-128-CBC (Fernet)",
  "key_id": "tenant_master__default",
  "payload": "gAAAAABlw2S..."  // base64-encoded ciphertext
}
```

**Design Notes:**
- Envelope is JSON (readable, inspectable for version/algorithm)
- Payload is base64-encoded for JSON transport
- Version field enables key rotation in future phases
- Each encryption includes a timestamp (audit trail)

## API Reference

### SecretsStore Class

Defined in `operator/bridges/shared/provider_keys.py`.

#### Constructor

```python
from provider_keys import SecretsStore

# Default tenant (_default)
store = SecretsStore()

# Specific tenant
store = SecretsStore(tenant_id="tenant-name")
```

#### Methods

**`encrypt_secrets(secrets_dict: dict) -> dict`**
- Encrypts a dict and writes to secrets.enc
- Returns the envelope dict
- Raises ValueError on encryption failure

**`decrypt_secrets() -> dict`**
- Loads and decrypts secrets.enc
- Returns empty dict if file doesn't exist
- Raises ValueError if key mismatch or corrupted

**`load_secret(key: str, default: str | None = None) -> str | None`**
- Loads a single secret by key (e.g., "ANTHROPIC_API_KEY")
- Returns default if key not found or store unreadable
- Does NOT raise (defensive against corrupted stores)

**`save_secret(key: str, value: str) -> None`**
- Saves or updates a single secret
- Encrypts and writes atomically
- Raises ValueError on failure

**`delete_secret(key: str) -> bool`**
- Deletes a secret
- Returns True if key existed, False if not found
- Removes secrets.enc file if last secret deleted
- Raises ValueError on write failure

**`list_secrets() -> list[str]`**
- Returns sorted list of all secret keys (not values)
- Returns empty list on error (defensive)

**`migrate_from_env(env_file: Path | None = None) -> dict`**
- Migrates secrets from legacy .env file to secrets.enc
- Moves original .env to .env.backup
- Returns dict of migrated secrets
- Raises ValueError on failure

### Integration with Resolvers

#### resolve_key(key_name: str) -> str | None

```python
from provider_keys import resolve_key

# Checks: env → secrets.enc → service.env
value = resolve_key("openai_api_key")
# Returns: "sk-org-123" (from whichever source has it first)
```

#### resolve_by_env_var(env_var: str) -> str | None

```python
from provider_keys import resolve_by_env_var

# For providers that only know credential_env names (ADR-0181)
value = resolve_by_env_var("OPENROUTER_API_KEY")
# Checks same three sources for the exact env var name
```

## CLI Interface

### corvin secrets set

```bash
corvin secrets set OPENAI_API_KEY "sk-org-..."
corvin secrets set OPENAI_API_KEY "sk-org-..." --tenant prod
```

### corvin secrets get

```bash
corvin secrets get OPENAI_API_KEY
sk-org-...

corvin secrets get NONEXISTENT
error: secret 'NONEXISTENT' not found
```

### corvin secrets delete

```bash
corvin secrets delete OPENAI_API_KEY
✓ Deleted secret 'OPENAI_API_KEY'
```

### corvin secrets list

```bash
corvin secrets list
Secrets in tenant _default:

  ANTHROPIC_API_KEY
  OPENAI_API_KEY
  OPENROUTER_API_KEY
```

### corvin secrets migrate

```bash
corvin secrets migrate
✓ Migrated 3 secrets from .env to tenant _default

# From specific tenant
corvin secrets migrate --tenant prod
```

## Security Guarantees

### At-Rest Encryption
- All secrets in secrets.enc are encrypted using Fernet
- Process env vars are never encrypted (out of scope)
- service.env remains plaintext (legacy, unchanged from pre-Phase-1b)

### Key Management
- Per-tenant master key in `~/.corvin/tenants/<tid>/keys/tenant_master.key`
- Permissions: 0o600 (readable by owner only)
- Generated on first use, persisted for all subsequent uses
- No key rotation mechanism today (Phase 2+)

### SSOT + Precedence
- All code paths (resolve_key, resolve_by_env_var, CLI) use SecretsStore via same API
- Three-tier precedence enforced: env > secrets.enc > service.env
- Tests verify all readers return identical values (test_secrets_ssot.py)

### No Bypass
- SecretsStore is the ONLY way to read/write encrypted secrets
- Direct file access to secrets.enc requires decryption (invalid ciphertext without key)
- Fallback to plaintext service.env is explicit and traced (debug logs)

## Upgrade Path

### From service.env

1. **Automatic:** No migration required. resolve_key still reads service.env if secrets.enc is empty.
2. **Voluntary:** Operator runs `corvin secrets migrate` to move existing keys to encrypted store.
3. **Partial:** Operator can save new keys via CLI or API; old keys in service.env continue to work (same precedence).

### From .env (Pre-Consolidation)

The legacy .env file is no longer read by resolve_key/resolve_by_env_var. To recover keys from .env:

```bash
corvin secrets migrate  # Migrates ~/.corvin/.env to tenant secrets.enc
```

## Testing Strategy

### Unit Tests (test_secrets_manager.py)

- **Basic:** encrypt/decrypt roundtrip, empty store, key generation
- **Single ops:** load/save/delete/list
- **Migration:** from .env, quote stripping, comment skipping
- **Errors:** key mismatch, corrupted files, missing payloads
- **Tenant isolation:** different tenants use separate stores
- **Integration:** SecretsStore + resolve_key/resolve_by_env_var agree

### SSOT Tests (test_secrets_ssot.py)

- **New:** `test_secrets_store_and_resolve_key_agree()` — both return same value
- **Precedence:** `test_secrets_store_precedence_with_service_env()` — secrets.enc wins over service.env
- **Existing:** All pre-Phase-1b tests still pass (backward compatibility)

### Coverage

- 27 unit tests in test_secrets_manager.py (all passing)
- 10 SSOT tests in test_secrets_ssot.py (all passing, +2 new for Phase 1b)
- No regression: existing resolve_key tests unchanged

## Threat Model & Non-Goals

### In Scope (Phase 1b)
- At-rest encryption for secrets in secrets.enc
- Per-tenant key isolation
- Secure key storage (0o600 file permissions)
- Backward compatibility with service.env

### Out of Scope
- Key rotation (Phase 2+)
- Multi-machine key distribution / backup
- Hardware security modules (HSM) integration
- Secrets in process memory (env vars stay plaintext by design)
- Console UI for secrets management (Phase 1c+)

## Known Limitations

### Key Destruction
If `~/.corvin/tenants/<tid>/keys/tenant_master.key` is lost, secrets.enc becomes unrecoverable (no key escrow, no HSM backup).

**Mitigation:** Operator should backup the key file as part of regular backup strategy.

### Single Master Key per Tenant
All secrets in a tenant share one master key. Compromise of the key compromises all secrets.

**Future:** Phase 2 might introduce per-secret envelope encryption or key derivation.

## ADR References

- **ADR-0007:** Multi-tenant architecture (Phase 1.2)
- **ADR-0181:** Provider model selection (engine_model_registry.yaml)
- Related to compliance baseline: GDPR Art. 32 (encryption), L16 (security hardening)

## Migration Guide

### For Operators: Migrate .env to Encrypted Storage

```bash
# 1. List current secrets (not values)
corvin secrets list

# 2. Migrate from .env
corvin secrets migrate
# Creates ~/.corvin/.env.backup
# Encrypts all .env secrets into secrets.enc

# 3. Verify migration
corvin secrets list
corvin secrets get ANTHROPIC_API_KEY

# 4. (Optional) Remove .env.backup after verification
rm ~/.corvin/.env.backup
```

### For Developers: Using SecretsStore

```python
from operator.bridges.shared.provider_keys import SecretsStore

# Load a secret (used by bridges, adapters, etc.)
store = SecretsStore()
api_key = store.load_secret("ANTHROPIC_API_KEY")
if api_key:
    use_key(api_key)
else:
    log.warning("ANTHROPIC_API_KEY not configured")

# Set via CLI (operator-facing)
# corvin secrets set ANTHROPIC_API_KEY sk-ant-...

# Or programmatically (rare)
store.save_secret("ANTHROPIC_API_KEY", "sk-ant-...")
```

## Changelog

### 2026-07-27 — Phase 1b Complete
- SecretsStore class added to provider_keys.py
- resolve_key() and resolve_by_env_var() updated to check secrets.enc
- CLI command: `corvin secrets {set|get|delete|list|migrate}`
- 27 unit tests + 2 SSOT tests, all passing
- Full backward compatibility with service.env
- Documentation: this file

## See Also

- [compliance-baseline.md](compliance-baseline.md) — GDPR Art. 32 (encryption)
- [layer-16-security.md](layer-16-security.md) — security hardening
- [portable-tenants.md](portable-tenants.md) — Phase 1c tenant plugins
