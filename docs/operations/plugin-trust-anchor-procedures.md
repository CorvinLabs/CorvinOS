# Plugin Trust Anchor Procedures (ADR-0249, Stage 6)

**Date:** 2026-08-28  
**Status:** Operational guide skeleton (awaiting maintainer key custody decision)  
**Audience:** Operator/Maintainer only

---

## Overview

The trust anchor system allows CorvinOS to distinguish between three plugin classes:

| Origin | Meaning | Install Flow |
|---|---|---|
| **builtin** | Ships with CorvinOS (in the wheel) | Trusted, no confirmation needed |
| **vetted** | Signed by the maintainer's Ed25519 key, pinned to a trust anchor | Trusted IF the signature is valid AND the key is pinned |
| **community** | Unreviewed third-party code | Requires explicit per-plugin operator approval at install time |

**The trust anchor is your key's public half.** It pins which Ed25519 key can sign `origin=vetted` plugins. Without it, nothing can reach `vetted` status — a self-signed signature verifies cryptographically but proves nothing about who produced it, so it stays `community` (the safe default).

---

## Key Generation (Maintainer Only)

**Prerequisites:**
- SSH or an Ed25519 key-generation tool (`ssh-keygen` on most systems)
- A secure, backed-up location for the private key
- No automated key rotation policy yet (ADR-0249 defers revocation)

**Steps:**

1. **Generate the keypair (one-time):**
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/corvin-plugins -C "corvin-plugins-maintainer@corvinlabs.com" -N ""
   ```

   This creates:
   - `~/.ssh/corvin-plugins` — private key (keep secret)
   - `~/.ssh/corvin-plugins.pub` — public key (openssh format, not used directly)

2. **Extract the DER public key and encode it for the anchor file:**
   ```bash
   # Convert OpenSSH public key to DER format
   ssh-keygen -e -m pkcs8 -f ~/.ssh/corvin-plugins.pub | \
     openssl pkey -pubin -outform DER | \
     base64 | tr -d '\n' > /tmp/corvin-anchor.txt
   
   echo "" >> /tmp/corvin-anchor.txt  # Newline at EOF
   ```

   Or, if using cryptography library directly (in Python):
   ```python
   from cryptography.hazmat.primitives.serialization import load_ssh_public_key, Encoding, PublicFormat
   import base64
   
   with open("~/.ssh/corvin-plugins.pub") as f:
       ssh_pubkey = f.read()
   
   pub_key = load_ssh_public_key(ssh_pubkey.encode())
   der = pub_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
   anchor = base64.urlsafe_b64encode(der).decode().rstrip("=")
   print(anchor)  # This is your trust anchor
   ```

3. **Store the anchor in the repository (PUBLIC):**
   ```bash
   mkdir -p ~/.corvin/global
   echo "# Corvin Labs maintainer Ed25519 key (2026-08)" > ~/.corvin/global/plugin_trust_anchors.txt
   echo "$ANCHOR_STRING" >> ~/.corvin/global/plugin_trust_anchors.txt
   ```

   The file is readable by all users; the private key is not.

4. **Verify the anchor is loadable:**
   ```bash
   python3 -c "
   from corvin_plugins.trust import load_trust_anchors
   from pathlib import Path
   anchors = load_trust_anchors(Path.home() / '.corvin')
   print(f'Loaded {len(anchors)} anchor(s)')
   print(f'First 20 chars: {anchors[0][:20]}...' if anchors else 'No anchors')
   "
   ```

---

## Signing a Plugin (Maintainer Only)

Once the key is in place, sign a plugin manifest **before** declaring it `origin=vetted`:

```bash
python3 -c "
import json
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from corvin_plugins.trust import manifest_signing_digest
import base64

# Load private key
with open(Path.home() / '.ssh/corvin-plugins') as f:
    priv_key_pem = f.read()

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
# Parse OpenSSH format (use ssh-keygen -p -m pem to convert if needed)
# For now, assume PEM format
priv_key = serialization.load_pem_private_key(
    priv_key_pem.encode(),
    password=None,
    backend=default_backend()
)

# Load manifest
manifest_path = Path('plugin.yaml')
import yaml
manifest_data = yaml.safe_load(manifest_path.read_text())

# Sign
sig_bytes = priv_key.sign(manifest_signing_digest(manifest_data))
pub_bytes = priv_key.public_key().public_bytes(
    serialization.Encoding.DER,
    serialization.PublicFormat.SubjectPublicKeyInfo
)

# Attach signature
manifest_data['signature'] = {
    'algorithm': 'ed25519',
    'public_key': base64.urlsafe_b64encode(pub_bytes).decode().rstrip('='),
    'value': base64.urlsafe_b64encode(sig_bytes).decode().rstrip('='),
}

# Write back
manifest_path.write_text(yaml.dump(manifest_data))
print('✓ Signed ' + manifest_path.name)
"
```

Then change the manifest's `origin:` from `community` to `vetted` and commit.

---

## Operator Install Flow

### Case 1: Community Plugin (Default)

```bash
$ corvin plugin install /path/to/plugin

Plugin 'my-cool-router' is unreviewed third-party code.
Installing it will run untested code in this process.

Approve installation of my-cool-router? [y/N] y
✓ Confirmed: installing my-cool-router
✓ Installed my-cool-router@1.0.0 to tenant _default
```

The decision is recorded in `~/.corvin/tenants/_default/global/plugin_consent.json`:

```json
{
  "approved": {
    "my-cool-router": {
      "approved": true,
      "operator": "...",
      "digest": "sha256:..."
    }
  }
}
```

### Case 2: Vetted Plugin (Signed by Maintainer)

```bash
$ corvin plugin install /path/to/vetted-plugin

✓ Trust verified: signature verified against a pinned anchor
✓ Installed vetted-plugin@2.0.0 to tenant _default
```

No prompt; the signature is proof enough.

### Case 3: Forged Plugin (Claims `vetted`, signature invalid or unpinned)

```bash
$ corvin plugin install /path/to/bad-plugin

error: Plugin 'bad-plugin' claims origin=vetted but fails trust verification:
       signature verified against a key that is not a pinned trust anchor
       Installation refused.
```

Refused immediately; no way to override (fail-closed per ADR-0249).

---

## CLI Command Reference (Stage 6)

The `corvin-gateway plugin install` command is the primary operator interface for plugin installation.

### Basic Syntax

```bash
python -m corvin_gateway.cli plugin install <path> [options]
```

### Options

| Flag | Description | Default |
|---|---|---|
| `<path>` | **Required.** Local directory containing `plugin.yaml` (or `setup.py`/`pyproject.toml` for legacy) | — |
| `--tenant TENANT_ID` | Tenant to install into | `_default` |
| `--force` | Reinstall if plugin already exists | false |
| `--no-prompt` | Skip operator confirmation for community plugins (for CI/automation) | false |

### Examples

**1. Install a community plugin (with confirmation prompt):**
```bash
python -m corvin_gateway.cli plugin install /path/to/my-plugin
```

**2. Install for CI/automation (non-interactive):**
```bash
python -m corvin_gateway.cli plugin install /path/to/plugin --no-prompt
```

**3. Upgrade/reinstall an existing plugin:**
```bash
python -m corvin_gateway.cli plugin install /path/to/plugin --force
```

**4. Install to a non-default tenant:**
```bash
python -m corvin_gateway.cli plugin install /path/to/plugin --tenant staging
```

### Plugin Metadata Discovery

The CLI looks for metadata in this order:

1. **`plugin.yaml`** (recommended, new ADR-0249 format)
   ```yaml
   id: com.example.my_plugin
   name: My Plugin
   version: 1.0.0
   origin: community          # or "vetted" or "builtin"
   boot_layer: installed      # or "bundled"
   class_path: my_pkg.backend:Handler
   config:
     key: value
   signature:
     algorithm: ed25519
     public_key: "MCowBQYD..." # base64url DER
     value: "..."              # base64url signature
   ```

2. **`setup.py`** (legacy fallback)
   ```python
   setup(name="plugin-name", version="1.0.0", ...)
   ```

3. **`pyproject.toml`** (legacy fallback)
   ```toml
   [project]
   name = "plugin-name"
   version = "1.0.0"
   ```

### Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success (plugin installed or already present) |
| `1` | Error (URL rejected, path not found, signature failed, confirmation denied, or config error) |

### Output Examples

**Success (community plugin with confirmation):**
```
Plugin requires operator confirmation:
  ID:       com.example.demo
  Name:     Demo Plugin
  Version:  1.0.0
  Origin:   community

This plugin is UNREVIEWED. Load it?
(yes/no): yes

✅ Plugin installed: com.example.demo v1.0.0
```

**Success (vetted plugin, automatic):**
```
✅ Plugin installed: com.example.vetted v2.0.0
```

**Failure (already installed, not forced):**
```
Plugin 'com.example.existing' already installed. Use --force to reinstall.
```

**Failure (URL rejected):**
```
Error: URL installation not supported. Provide a local directory path.
```

**Failure (vetted signature verification):**
```
Error: Plugin signature verification failed: vetted plugin missing required signature
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `"no trust anchors configured — nothing can be vetted"` (debug log) | Anchor file missing or empty | Place the anchor in `~/.corvin/global/plugin_trust_anchors.txt` |
| `"plugin signing key is not a pinned trust anchor"` (debug log) | Signature is valid but key doesn't match | Verify the plugin's `signature.public_key` against your stored anchor |
| `"Installation refused"` on a plugin you trust | Enforcement flag is ON (default-off) | Check `plugin_trust_enforcement` feature flag in `spec.features` |
| Community plugin asks for confirmation every install | Consent was not recorded | Operator said `y` but `plugin_consent.json` was not written; check permissions on `~/.corvin/tenants/*/global/` |

---

## Revocation (Not Yet Implemented)

ADR-0249 identifies revocation as a known gap: if the key is compromised, there is no built-in way to revoke old signatures. Future work (ADR-0249 § Deferred):

1. Maintain a separate CRL-style revocation list
2. Key rotation policy (next key = next version bump or timestamp cutoff)
3. Per-maintainer key expiration (e.g., annually)

For now, treat the private key as sensitive as the audit-chain writer.

---

## References

- **ADR-0249** — Plugin provenance and operator consent
- **ADR-0248** — `corvin plugin install` CLI command
- **trust.py** — Implementation details
- **`core/awpkg/awpkg/manifest.py`** — Related signing construction (base64url, SHA-256 digest)
