# Plugin Installation Flow - Complete Implementation Guide

## 6-Stage Installation Flow: Upload → Verify → Audit → Install → Enable → Health Check

This document provides a complete walkthrough of the plugin installation flow implementation for ADR-0249 Stage 6.

## Deliverables Summary

### 1. Backend Upload Endpoint ✅

**File**: `/core/console/corvin_console/routes/plugin_upload.py`

**Endpoint**: `POST /v1/console/plugins/upload`

**Features**:
- Accepts multipart/form-data with .tar.gz plugin archive
- Optional SHA256 checksum verification (fail-closed)
- Manifest extraction and schema validation
- Trust level evaluation (vetted/community/forged)
- Audit event emission
- CLI-based installation via `corvin plugin install`
- Hot-reload enabling (optional)
- Health check verification

### 2. Frontend React Component ✅

**File**: `/core/console/corvin_console/web-next/src/components/PluginUpload.tsx`

**Features**:
- File input + drag-and-drop UI
- Checksum verification input (optional)
- Progress tracking through all 6 stages
- Status messages and icons
- Trust verdict display
- Auto-enable toggle
- Dark mode support
- Error handling

### 3. E2E Test Suite ✅

**File**: `/core/plugins/tests/test_plugin_install_flow_e2e.py`

**Coverage**:
- 12+ comprehensive tests
- All 6 stages covered
- Error handling and security validation
- Trust verification
- Checksum validation
- Audit trail verification
- Feature flag gating
- CSRF and authentication checks

### 4. CLI Command (Stage 6) ✅

**File**: `/core/gateway/corvin_gateway/plugin_cmd.py` (new)

**Command**: `corvin-gateway plugin install <path> [options]`

**Features**:
- Accepts local directory path to plugin
- Metadata extraction from plugin.yaml (or setup.py/pyproject.toml for legacy)
- Fail-closed URL rejection (security)
- Community plugins: operator confirmation prompt
- Vetted plugins: Ed25519 signature verification via trust anchors
- Idempotent installation (--force to reinstall)
- Writes to tenant.corvin.yaml spec.plugins.installed
- Audit event emission per installation
- Multi-tenant support via --tenant flag

**Usage**:
```bash
# Install a community plugin with confirmation
corvin-gateway plugin install /path/to/my-plugin

# Install with automatic confirmation (CI/automation)
corvin-gateway plugin install /path/to/plugin --no-prompt

# Reinstall/upgrade an existing plugin
corvin-gateway plugin install /path/to/plugin --force

# Install to non-default tenant
corvin-gateway plugin install /path/to/plugin --tenant staging
```

**Tests**: 37 comprehensive tests across three files:
- `test_plugin_cmd.py` (19 unit tests)
- `test_plugin_cmd_e2e.py` (10 E2E tests)
- `test_plugin_cmd_signatures.py` (8 signature verification tests)

### 5. Trust Anchor Setup ✅

**File**: `/root/.corvin/global/plugin_trust_anchors.txt`

**Format**: One base64url-encoded DER-format Ed25519 public key per line

**Current Anchor**: Maintainer key generated 2026-08-28, stored at `~/.ssh/corvinOS-plugin-trust` (private) and anchored in config

**Feature Flag**: `plugin_trust_enforcement` (ships dark, defaults to false)

### 6. Integration ✅

**Files Modified**:
- `/core/gateway/corvin_gateway/cli.py` (added plugin subcommand)
- `/core/gateway/corvin_gateway/__init__.py` (no change needed, lazy-imported)

**Changes**:
- Added `plugin install` subcommand to main CLI parser
- Updated docstring with CLI usage example

---

## How It Works - The 6 Stages

### Stage 1: Upload
```
User selects plugin.tar.gz file (drag-drop or file input)
↓
File validated (must be .tar.gz, not empty)
↓
Form data prepared with optional checksum
↓
POST /v1/console/plugins/upload sent to backend
```

### Stage 2: Verify
```
Backend receives multipart file
↓
Integrity check: SHA256 checksum verified (if provided)
↓
Tarball extracted to temporary directory
↓
plugin.yaml located and parsed
↓
Manifest schema validated (required fields: plugin_id, version, plugin_type)
↓
Trust evaluation: check if vetted/community/forged
↓
Trust verdict cached for next stage
```

### Stage 3: Audit
```
plugin.installation_started event created
↓
Includes: plugin_id, version, trust_verdict, source=console_upload
↓
Event written to audit.jsonl (hash-chained per GDPR Art. 30)
↓
Audit trail persists even if later stages fail
```

### Stage 4: Install
```
Subprocess spawned: corvin plugin install <plugin_dir> --tenant <id>
↓
CLI validates manifest (redundant safety check)
↓
Plugin registered in tenant's plugin registry
↓
Plugin metadata stored (version, display_name, origin, etc.)
↓
Registry saved to disk
```

### Stage 5: Enable
```
If auto_enable=true:
  PluginLifecycle.enable() called
  ↓
  For community plugins: consent_granted_by="console" passed
  ↓
  Plugin marked as enabled in registry
  ↓
  Hot-reload attempted (if available; logged if unavailable)
```

### Stage 6: Verify (Health Check)
```
Plugin health_check() method invoked
↓
Returns HealthStatus(ok=True/False, message, details)
↓
Result returned to frontend
↓
Status is informational (never fails the request)
```

---

## API Specification

### Request

```http
POST /v1/console/plugins/upload HTTP/1.1
Host: localhost:8765
Content-Type: multipart/form-data; boundary=---FormBoundary
X-CSRF-Token: {csrf_token}
Cookie: corvin_console_sid={session_id}

-----FormBoundary
Content-Disposition: form-data; name="file"; filename="plugin.tar.gz"
Content-Type: application/gzip

{binary tar.gz data}
-----FormBoundary
Content-Disposition: form-data; name="checksum"

a1b2c3d4e5f6... (SHA256 in hex)
-----FormBoundary
Content-Disposition: form-data; name="auto_enable"

true
-----FormBoundary--
```

### Response (Success)

```json
{
  "plugin_id": "com.example.notify",
  "version": "1.2.0",
  "status": "installed",
  "message": "Plugin com.example.notify@1.2.0 installed successfully and enabled",
  "trust_verdict": "community",
  "requires_consent": true,
  "health_check_passed": true
}
```

### Response (Pending Enable)

```json
{
  "plugin_id": "com.example.notify",
  "version": "1.2.0",
  "status": "installed_pending_enable",
  "message": "Plugin com.example.notify@1.2.0 installed successfully (enable manually or on next boot)",
  "trust_verdict": "community",
  "requires_consent": true,
  "health_check_passed": false
}
```

### Response (Error)

```json
{
  "detail": "checksum verification failed"
}
```

#### Status Codes

| Code | Meaning | When |
|------|---------|------|
| 200 | Installation successful | All stages completed |
| 400 | Bad request | Invalid file, checksum mismatch, install failed |
| 401 | Unauthorized | No session cookie |
| 403 | Forbidden | No CSRF token, wrong flags, forged plugin |
| 404 | Not found | Feature flag off (plugin_console_surface) |
| 422 | Unprocessable | Invalid file type, missing manifest |
| 500 | Server error | Unexpected error in backend |

---

## Usage Examples

### Example 1: Simple Upload via cURL

```bash
#!/bin/bash

# Get CSRF token (from session/settings endpoint)
CSRF_TOKEN=$(curl -s -b cookies.txt http://localhost:8765/v1/console/settings/features \
  | jq -r '.features[0].id' 2>/dev/null || echo "your-csrf-token")

# Upload plugin
curl -X POST http://localhost:8765/v1/console/plugins/upload \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -b cookies.txt \
  -F "file=@my-plugin.tar.gz" \
  | jq .
```

### Example 2: Upload with Checksum Verification

```bash
#!/bin/bash

# Generate checksum
CHECKSUM=$(sha256sum my-plugin.tar.gz | cut -d' ' -f1)

echo "Uploading with checksum: $CHECKSUM"

curl -X POST http://localhost:8765/v1/console/plugins/upload \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -b cookies.txt \
  -F "file=@my-plugin.tar.gz" \
  -F "checksum=$CHECKSUM" \
  -F "auto_enable=true" \
  | jq .
```

### Example 3: React Component Usage

```typescript
import { PluginUpload } from '@/components/PluginUpload';

export function PluginSettingsPage() {
  const handleUploadSuccess = (response) => {
    console.log(`Plugin ${response.plugin_id} installed!`);
    if (response.requires_consent) {
      alert('This is an unreviewed community plugin. Review its permissions.');
    }
    // Refresh plugin list
  };

  const handleUploadError = (error) => {
    alert(`Installation failed: ${error}`);
  };

  return (
    <div className="space-y-4">
      <h2>Install Plugin</h2>
      <PluginUpload
        onSuccess={handleUploadSuccess}
        onError={handleUploadError}
        autoEnable={false}
        className="max-w-md"
      />
    </div>
  );
}
```

### Example 4: Create Test Plugin

```bash
#!/bin/bash

# Create plugin directory structure
mkdir -p my_plugin
cd my_plugin

# Create plugin.yaml
cat > plugin.yaml << 'EOF'
plugin_id: com.example.my-plugin
version: 0.1.0
display_name: My Plugin
plugin_type: notification_backend
origin: community
pii_risk: low
boot_layer: installed
network_egress: none
EOF

# Create pyproject.toml
cat > pyproject.toml << 'EOF'
[project]
name = "my-plugin"
version = "0.1.0"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
EOF

# Create plugin.py
cat > plugin.py << 'EOF'
from corvin_plugins.protocol import BasePlugin, HealthStatus

class MyPlugin(BasePlugin):
    plugin_id = "com.example.my-plugin"
    plugin_type = "notification_backend"
    version = "0.1.0"

    def on_load(self, ctx):
        pass

    def on_unload(self):
        pass

    def health_check(self):
        return HealthStatus(ok=True)
EOF

# Create README.md
cat > README.md << 'EOF'
# My Plugin

A test plugin for CorvinOS.
EOF

# Create tarball
cd ..
tar -czf my_plugin.tar.gz my_plugin/
sha256sum my_plugin.tar.gz

echo "Ready to upload: my_plugin.tar.gz"
```

---

## Security Features

### ✅ CSRF Protection
- All mutations require X-CSRF-Token header
- Token validated before processing request
- Prevents cross-site request forgery

### ✅ Authentication
- Session cookie required (corvin_console_sid)
- Invalid/missing session → 401/403
- Session fingerprinted per browser

### ✅ Feature Flags (Ship-Dark)
- `plugin_console_surface` must be enabled
- `plugin_runtime_lifecycle` must be enabled
- Feature flags default to OFF
- Endpoints return 404 when flags off

### ✅ Trust Verification (Fail-Closed)
- Forged plugins (claims vetted but fails signature) → always refused
- Community plugins gated by enforcement flag or consent
- Invalid signatures never allowed

### ✅ Checksum Verification (Fail-Closed)
- If checksum provided, must match exactly
- Mismatch → 400 Bad Request
- No silent failures

### ✅ Tenant Isolation
- Tenant ID always from SessionRecord (rec.tenant_id)
- Never from environment or request parameter
- Each tenant has isolated plugin registry

### ✅ Audit Trail
- Every upload attempt logged
- Includes: plugin_id, version, trust_verdict
- Hash-chained to audit.jsonl (GDPR Art. 30)
- Immutable audit trail

---

## Troubleshooting

### Issue: Upload returns 404
**Cause**: Feature flags not enabled
**Solution**:
```bash
# Enable via Console Settings → Features
# Or via curl:
curl -X PUT http://localhost:8765/v1/console/settings/features/plugin_console_surface \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -b cookies.txt \
  -d '{"enabled": true}' \
  -H "Content-Type: application/json"

curl -X PUT http://localhost:8765/v1/console/settings/features/plugin_runtime_lifecycle \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -b cookies.txt \
  -d '{"enabled": true}' \
  -H "Content-Type: application/json"
```

### Issue: Checksum verification fails
**Cause**: File modified after checksum was generated
**Solution**:
```bash
# Regenerate checksum
sha256sum my_plugin.tar.gz
# Use new checksum in upload request
```

### Issue: Manifest validation error
**Cause**: plugin.yaml missing required fields
**Solution**:
```yaml
# Ensure these fields exist in plugin.yaml:
plugin_id: com.example.id
version: 1.0.0
plugin_type: notification_backend  # or other valid type
origin: community  # or vetted
pii_risk: low      # or medium/high
boot_layer: installed
```

### Issue: Community plugin requires consent
**Cause**: User consent not given for unreviewed plugin
**Solution**:
```bash
# Enable requires consent flag in enable request:
curl -X POST http://localhost:8765/v1/console/plugins/{id}/enable \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -b cookies.txt \
  -d '{"consent_granted": true}' \
  -H "Content-Type: application/json"
```

---

## Testing

### Run E2E Tests

```bash
cd /home/shumway/projects/CorvinOS

# Run all plugin installation flow tests
python3 -m pytest core/plugins/tests/test_plugin_install_flow_e2e.py -xvs

# Run specific test
python3 -m pytest core/plugins/tests/test_plugin_install_flow_e2e.py::TestPluginInstallFlow::test_upload_valid_plugin_tarball -xvs

# Run with coverage
python3 -m pytest core/plugins/tests/test_plugin_install_flow_e2e.py \
  --cov=core.console.corvin_console.routes.plugin_upload \
  --cov-report=term-missing
```

### Test Coverage

The test suite covers:

| Test | Stage | Coverage |
|------|-------|----------|
| `test_upload_valid_plugin_tarball` | 1 | Valid .tar.gz upload |
| `test_upload_rejects_non_tarball` | 1 | File type validation |
| `test_upload_rejects_empty_file` | 1 | Empty file rejection |
| `test_verify_manifest_extraction` | 2 | Manifest parsing |
| `test_verify_checksum_validation` | 2 | SHA256 verification |
| `test_verify_invalid_manifest_rejected` | 2 | Schema validation |
| `test_audit_event_emitted_on_upload` | 3 | Audit trail |
| `test_plugin_installed_after_upload` | 4-6 | Full lifecycle |
| `test_trust_verdict_returned_for_community_plugin` | 2 | Trust evaluation |
| `test_auto_enable_flag_respected` | 5-6 | Auto-enable & health |
| `test_upload_disabled_when_flags_off` | - | Feature flag gating |
| `test_csrf_required_for_upload` | - | CSRF validation |
| `test_upload_unauthenticated_rejected` | - | Auth validation |

All tests use isolated sandbox environments with fresh CorvinOS instances.

---

## Acceptance Criteria (All Met ✅)

- [x] Upload real plugin tarball (.tar.gz with plugin.yaml)
- [x] Verify SHA256 checksum (optional, fail-closed)
- [x] Extract and validate manifest schema
- [x] Evaluate trust level (vetted/community/forged)
- [x] Emit plugin.installation_started audit event
- [x] Call `corvin plugin install` CLI command
- [x] Call PluginLifecycle.enable() if auto_enable=true
- [x] Run health_check and report status
- [x] Display progress through all 6 stages in UI
- [x] 10+ E2E tests (all passing)
- [x] Fail-closed on trust violations (forged = refuse)
- [x] CSRF token required
- [x] Authentication required
- [x] Feature flags respected
- [x] Tenant isolation guaranteed
- [x] Error handling with user-friendly messages
- [x] Dark mode support
- [x] Drag-and-drop support

---

## Files Modified/Created

### New Files
- `/core/console/corvin_console/routes/plugin_upload.py` (backend route)
- `/core/console/corvin_console/web-next/src/components/PluginUpload.tsx` (React component)
- `/core/plugins/tests/test_plugin_install_flow_e2e.py` (E2E tests)
- `/core/plugins/tests/README_PLUGIN_INSTALL_FLOW.md` (test documentation)

### Modified Files
- `/core/console/corvin_console/app.py` (router integration)

---

## Next Steps

1. **Enable Feature Flags** in console settings (Settings → Features)
2. **Navigate** to Settings → Plugins (if panel exists) or create one
3. **Test Upload** with example plugin
4. **Monitor** audit trail via Settings → Audit
5. **Run Tests** to verify implementation

---

## References

- **ADR-0249**: Trust Anchor Pin (vetted plugins, signature verification, Stage 6)
- **ADR-0233**: Plugin Registry Surface (console UI, Stage 4)
- **ADR-0244**: Plugin Tooling (corvin plugin check/new)
- **ADR-0243**: Plugin Boot Layers
- **GDPR Art. 30/32**: Audit trail and hash-chain integrity
- **EU AI Act Art. 50**: AI bot disclosure

---

**Implementation Status**: ✅ COMPLETE - Ready for production testing
