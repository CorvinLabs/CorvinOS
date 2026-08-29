# Plugin Installation Flow (ADR-0249 Stage 6)

Complete implementation of the plugin installation lifecycle with 6 stages: Upload → Verify → Audit → Install → Enable → Health Check.

## Implementation Overview

### 1. Backend Components

#### `/core/console/corvin_console/routes/plugin_upload.py`

Implements the complete plugin installation flow:

```python
POST /v1/console/plugins/upload
Content-Type: multipart/form-data

Request:
  file: .tar.gz plugin archive (binary)
  checksum: SHA256 checksum (optional)
  auto_enable: boolean (default: false)

Response:
  {
    "plugin_id": "string",
    "version": "string",
    "status": "installed" | "installed_pending_enable" | "error",
    "message": "string",
    "trust_verdict": "vetted" | "community" | "forged" | null,
    "requires_consent": boolean,
    "health_check_passed": boolean | null
  }
```

**Stages Implemented:**

1. **Upload** (Stage 1):
   - Accept multipart file upload
   - Validate file type (.tar.gz, .tgz)
   - Check file size > 0

2. **Verify** (Stage 2):
   - Verify SHA256 checksum (optional, fail-closed)
   - Extract tarball to temp directory
   - Validate plugin.yaml exists and is parseable
   - Validate manifest schema (required fields)
   - Evaluate trust level per ADR-0249 (vetted/community/forged)

3. **Audit** (Stage 3):
   - Emit `plugin.installation_started` event via `console_audit`
   - Include plugin_id, version, trust_verdict in audit details

4. **Install** (Stage 4):
   - Call `corvin plugin install <path>` CLI command
   - Pass `--tenant` ID
   - Pass `--yes` flag (console already gated trust)
   - Capture output and errors

5. **Enable** (Stage 5):
   - Call `PluginLifecycle.enable()` if auto_enable=true
   - Pass consent_granted_by="console" for community plugins
   - Hot-reload optional (logs warning if unavailable)

6. **Verify** (Stage 6):
   - Call `health_check()` on the plugin
   - Return health_check_passed status
   - Never fail the request (health check is informational)

**Error Handling (Fail-Closed):**

- Invalid file format → 422 Unprocessable Entity
- Checksum mismatch → 400 Bad Request
- Missing/invalid manifest → 422 Unprocessable Entity
- Trust verification failure (forged) → 403 Forbidden
- Installation error → 400 Bad Request
- Missing flags → 403 Forbidden
- No authentication → 401/403
- No CSRF token → 401/403

**Security:**

- CSRF token required (all mutations)
- Authentication required
- Feature flags gated (plugin_console_surface + plugin_runtime_lifecycle)
- Trust verification fail-closed (forged = never allowed)
- Tenant isolation (always use rec.tenant_id)
- Audit trail for every installation

### 2. Frontend Components

#### `/core/console/corvin_console/web-next/src/components/PluginUpload.tsx`

React component for plugin upload UI:

**Features:**
- File input with label
- Drag-and-drop area
- File validation (.tar.gz)
- Optional SHA256 checksum input
- Progress tracking through all 6 stages
- Status display with icons
- Trust verdict display
- Auto-enable toggle
- Error handling with user-friendly messages

**Props:**
```typescript
interface PluginUploadProps {
  onSuccess?: (response: UploadResponse) => void;
  onError?: (error: string) => void;
  autoEnable?: boolean;
  className?: string;
}
```

**Stage Progress:**
- idle → uploading → verifying → installing → enabling → health_check → complete/error
- Progress bar shows percentage (10% per stage)
- Status messages update for each stage
- Icons indicate status (spinner, check, alert)

**Styling:**
- Dark mode support
- Responsive layout
- Tailwind CSS classes
- Accessible form controls

### 3. E2E Tests

#### `/core/plugins/tests/test_plugin_install_flow_e2e.py`

Comprehensive test suite with 12+ tests covering:

**Stage 1: Upload**
- `test_upload_valid_plugin_tarball`: Upload valid .tar.gz
- `test_upload_rejects_non_tarball`: Reject non-.tar.gz files
- `test_upload_rejects_empty_file`: Reject empty uploads

**Stage 2: Verify**
- `test_verify_manifest_extraction`: Extract and validate plugin.yaml
- `test_verify_checksum_validation`: Validate SHA256 checksum
- `test_verify_invalid_manifest_rejected`: Reject tarball without plugin.yaml

**Stage 3: Audit**
- `test_audit_event_emitted_on_upload`: Verify audit.jsonl entry created

**Stages 4-6: Install + Enable + Health Check**
- `test_plugin_installed_after_upload`: Full flow end-to-end
- `test_trust_verdict_returned_for_community_plugin`: Trust level verdict
- `test_auto_enable_flag_respected`: Auto-enable and health check

**Error Handling**
- `test_upload_disabled_when_flags_off`: Fail-closed when flags off (404/403)
- `test_csrf_required_for_upload`: CSRF validation
- `test_upload_unauthenticated_rejected`: Authentication validation

**Test Utilities:**
- `_create_test_plugin_tarball()`: Generate valid test plugin
- `_compute_sha256()`: Calculate checksums
- `_sandbox()`: Isolated test environment

### 4. Integration

**Console App** (`corvin_console/app.py`):
```python
# Import plugin_upload route
from .routes import plugin_upload as plugin_upload_route

# Mount router
router.include_router(plugin_upload_route.router, tags=["console-plugins"])
```

The upload endpoint is automatically gated by the existing `require_surface_csrf` dependency, which checks:
- `plugin_console_surface` flag
- `plugin_runtime_lifecycle` flag
- CSRF token validity
- Session authentication

## ADR-0249 Compliance

✅ **Trust Verification (fail-closed)**:
- Vetted plugins with invalid signature → 403 Forbidden (never allowed)
- Community plugins gated by enforcement flag or consent
- Forged verdict = never installed

✅ **Checksum Verification (optional, fail-closed)**:
- If provided, must match or installation fails
- If not provided, installation proceeds (optional feature)

✅ **Manifest Validation (fail-closed)**:
- Required fields: plugin_id, version, plugin_type
- Schema validation via corvin_plugins.manifest
- Invalid manifest → 422 Unprocessable Entity

✅ **Audit Trail**:
- `plugin.installation_started` event emitted (Stage 3)
- Includes: plugin_id, version, trust_verdict
- Appended to audit.jsonl (hash-chained per GDPR Art. 30)

✅ **Operator Consent** (community plugins):
- Marked requires_consent=true in response
- Console can display consent notice
- Enable operation requires consent_granted_by="console"

✅ **Tenant Isolation**:
- Tenant ID always from SessionRecord (rec.tenant_id)
- Never from env var or request body
- Registry operations scoped to tenant

✅ **Security**:
- CSRF token required
- Authentication required
- Feature flags gated (ship-dark by default)
- Fail-closed on all errors

## Running the Tests

```bash
# Unit test for upload functionality
python3 -m pytest core/plugins/tests/test_plugin_install_flow_e2e.py::TestPluginInstallFlow::test_upload_valid_plugin_tarball -xvs

# All upload stage tests
python3 -m pytest core/plugins/tests/test_plugin_install_flow_e2e.py::TestPluginInstallFlow::test_upload* -xvs

# All verification tests
python3 -m pytest core/plugins/tests/test_plugin_install_flow_e2e.py::TestPluginInstallFlow::test_verify* -xvs

# All tests
python3 -m pytest core/plugins/tests/test_plugin_install_flow_e2e.py -xvs

# With coverage
python3 -m pytest core/plugins/tests/test_plugin_install_flow_e2e.py --cov=core.console.corvin_console.routes.plugin_upload --cov-report=term-missing
```

## Test Fixtures

**Test Plugin Tarball:**
- Valid .tar.gz with plugin.yaml
- Includes pyproject.toml (entry point)
- Configurable plugin_id, version, origin, network_egress
- Can be created inline via `_create_test_plugin_tarball()`

**Isolated Environment:**
- Temporary CORVIN_HOME per test
- Independent audit trail
- Plugin registry per test
- Session authentication fixtures
- CSRF token generation

## Example Usage

### CLI (Existing)
```bash
corvin plugin install ./path/to/plugin --tenant _default
```

### Console UI (New)
1. Navigate to Settings → Plugins
2. Click "Upload Plugin" button
3. Drag-drop .tar.gz file or click to browse
4. (Optional) Enter SHA256 checksum
5. (Optional) Check "Auto-enable after installation"
6. Watch progress through all 6 stages
7. See trust verdict and health check result
8. Plugin appears in registry, ready to enable

### API (New)
```bash
curl -X POST http://localhost:8765/v1/console/plugins/upload \
  -H "X-CSRF-Token: <token>" \
  -H "Cookie: corvin_console_sid=<sid>" \
  -F "file=@plugin.tar.gz" \
  -F "checksum=<sha256>" \
  -F "auto_enable=true"
```

## Design Decisions

**Why 6 Stages?**
- Separates concerns (upload vs. verify vs. install)
- Provides clear progress reporting to user
- Enables audit trail granularity
- Matches CLI `corvin plugin install` workflow

**Why fail-closed on checksum?**
- Optional verification (not required)
- If provided, must be correct
- Mismatch detected before attempting install
- No silent failures

**Why emit audit before install?**
- Installation can have side effects
- Audit trail captures intent, not just outcome
- Helps with compliance reporting
- Easier debugging if install fails

**Why health_check is informational?**
- Plugin may not load immediately (depends on bootstrap order)
- Health check can fail for transient reasons (resources)
- Never refuse successful installation due to health
- UI shows the status but doesn't block

**Why auto_enable is optional?**
- Operator review recommended for community plugins
- Consent model requires visible opt-in
- Staged enables are safer (can disable if issues)
- Default-off (ship-dark) principle

## Future Enhancements

1. **Batch Upload**: Multiple plugins in one request
2. **Plugin Repository**: Index of pre-built plugins
3. **Auto-Update**: Check for newer versions
4. **Rollback**: Revert to previous version
5. **Dependency Resolution**: Install dependencies first
6. **Signature Verification**: Full cryptographic signing (ADR-0249 implementation)

## References

- **ADR-0249**: Trust Anchor Pin (vetted plugins, signature verification)
- **ADR-0233**: Plugin Registry Surface (console UI)
- **ADR-0244**: Plugin Tooling (corvin plugin check/new)
- **ADR-0243**: Plugin Boot Layers (installed, compliance)
- **GDPR Art. 30/32**: Audit trail and integrity
- **EU AI Act Art. 50**: Bot disclosure

## Acceptance Criteria

✅ Upload real plugin tarball (.tar.gz with plugin.yaml)
✅ Extract and verify manifest schema
✅ Calculate and verify SHA256 checksum (optional)
✅ Emit plugin.installation_started audit event
✅ Call `corvin plugin install` CLI
✅ Mark for hot-reload or next boot
✅ Run health_check and report status
✅ Display all 6 stages in UI with progress
✅ 10+ E2E tests (all passing)
✅ Fail-closed on trust violations
✅ CSRF + authentication required
✅ Feature flags respected
✅ Tenant isolation guaranteed

All acceptance criteria met ✅
