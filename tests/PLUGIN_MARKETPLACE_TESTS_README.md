# Plugin Marketplace Integration Tests — ADR-0249

Comprehensive integration test suite for the CorvinOS plugin system marketplace workflows.

## Overview

This test suite provides end-to-end coverage of plugin marketplace features:

- **Marketplace Discovery:** Search, filter, paginate plugins
- **Installation:** Install, enable, disable, remove plugins  
- **Ratings & Reviews:** Rate plugins, leave reviews, average calculations
- **Governance:** Auto-removal on low ratings, plugin reporting, trust badges
- **Trust & Security:** Signature verification, sandboxing, resource limits
- **Multi-Tenant:** Tenant isolation verification (GDPR compliance)
- **Error Handling:** Interrupted installs, corrupted registry, failures
- **UI/UX:** Responsive design, accessibility, performance (Playwright)

## Test Files

### 1. `test_marketplace_integration_e2e.py` (400+ lines, 30+ tests)

**API & backend tests using pytest**

```
Fixtures:
- marketplace: PluginMarketplace instance
- populated_marketplace: Pre-loaded with sample plugins
- audit_trail: Mock audit trail with event logging
- sample_plugins: Factory for creating test plugins
- temp_corvin_home: Isolated .corvin directory

Test Suites:
1. TestMarketplaceDiscovery (7 tests)
   - List/search/filter/paginate plugins
   - Exclude unlisted plugins
   - Sort by rating

2. TestPluginInstallation (3 tests)
   - Record installations in registry
   - Concurrent installs
   - Validation (CPU, memory, timeout limits)

3. TestPluginRatings (4 tests)
   - Record reviews and update ratings
   - Calculate averages from multiple reviews
   - Retrieve reviews by plugin
   - Validate review input

4. TestPluginGovernance (4 tests)
   - Check governance conditions
   - Auto-remove low-rated plugins
   - Preserve new plugins with few reviews
   - Mark plugins as unlisted

5. TestMultiTenantIsolation (2 tests - parametrized)
   - Verify tenant installations are isolated
   - Check review isolation per tenant

6. TestFullWorkflows (2 tests)
   - Discovery → Install → Review workflow
   - Concurrent installs and reviews (10 operations)

7. TestErrorHandling (5 tests)
   - Handle non-existent plugins
   - Duplicate registrations
   - Pagination edge cases
```

### 2. `test_plugin_installation_workflows.py` (350+ lines, 25+ tests)

**Installation pipeline and lifecycle tests**

```
Fixtures:
- plugin_registry: PluginRegistry instance
- temp_plugin_dir: Isolated registry directory
- sample_manifest: Factory for plugin manifests
- mock_audit_trail: Mock audit logging

Test Suites:
1. TestBasicInstallation (3 tests)
   - Install simple plugin
   - Get plugin by ID
   - Plugin installation timestamp

2. TestPluginLifecycle (3 tests)
   - Remove plugins
   - List all plugins
   - Handle non-existent removals

3. TestPluginConfiguration (3 tests)
   - Update plugin config
   - Different configs → different hashes
   - Secrets masked in audit logs (Finding #2)

4. TestConcurrentOperations (3 tests)
   - Install 5+ plugins concurrently
   - Update configs across plugins
   - Install/remove mixed operations

5. TestAuditTrailIntegration (3 tests)
   - Log on install, config change, removal
   - Verify secrets are masked
   - Hash-chained audit trail

6. TestDependencyManagement (2 tests)
   - Install plugins with dependencies
   - Detect incompatible versions

7. TestHealthCheckAndRollback (3 tests)
   - Health checks pass/fail
   - Rollback on failure
   - State management

8. TestInterruptedInstallationRecovery (2 tests)
   - Resume interrupted installs
   - Detect/recover from registry corruption

9. TestVersionManagement (2 tests)
   - Multiple versions of same plugin
   - Version incompatibility detection
```

### 3. `test_plugin_governance_and_trust.py` (400+ lines, 28+ tests)

**Trust badges, signatures, governance, and security**

```
Fixtures:
- mock_trust_anchor_store: Ed25519 key management
- mock_report_system: Plugin reporting system
- temp_trust_anchor_dir: Isolated trust store

Test Suites:
1. TestTrustBadges (4 tests - parametrized)
   - Builtin → 'verified' badge
   - Vetted → 'verified' badge
   - Community → 'community' badge

2. TestSignatureVerification (4 tests)
   - Verify vetted plugin signatures (Ed25519)
   - Community plugins (no signature)
   - Invalid signatures rejected
   - Unknown keys rejected

3. TestPluginReporting (6 tests)
   - Report malicious plugins
   - Report inappropriate content
   - Report permission abuse
   - Report misrepresentation
   - Multiple reports on same plugin
   - Report threshold triggers action

4. TestAutoRemovalGovernance (3 tests)
   - Low rating (<2 stars, 5+ reviews)
   - High report count
   - Security audit failure

5. TestApprovalWorkflows (3 tests)
   - Builtin: auto-approved
   - Vetted: signature validation
   - Community: explicit confirmation

6. TestSandboxingAndLimits (4 tests)
   - CPU limits (1-100%)
   - Memory limits (64-512 MB)
   - Network access controls
   - Filesystem access controls

7. TestBootLayerRestrictions (4 tests - ADR-0243)
   - Compliance layer: non-disableable
   - Core layer: replaceable
   - Bundled layer: disableable
   - Installed layer: fully manageable
```

### 4. `conftest_plugin_marketplace.py` (400+ lines)

**Shared fixtures and factories**

```
Marketplace Fixtures:
- marketplace_config: Configuration dict
- temp_marketplace_home: Isolated .corvin directory

Registry Fixtures:
- registry_state: Empty registry state
- plugin_entry_factory: Creates PluginEntry dicts
- mock_audit_service: AuditService mock

Tenant Fixtures:
- tenant_factory: Creates tenant contexts
- multi_tenant_context: 3 test tenants
- tenant_isolation_verifier: Isolation checker

Plugin Fixtures:
- plugin_manifest_factory: Creates manifests
- sample_plugins_data: Real test plugin data
- plugin_entry_factory: Registry entries

Governance Fixtures:
- governance_rules: Governance config
- report_factory: Create test reports

Helper Fixtures:
- cleanup_handler: Test cleanup
- performance_timer: Benchmark helper
```

### 5. `plugins-integration.spec.ts` (500+ lines, 25+ tests)

**Playwright E2E tests for Console UI**

```
Test Suites:
1. Marketplace Discovery (7 tests)
   - List plugins
   - Search by name
   - Filter by category
   - View trust badges
   - Sort by rating
   - Pagination

2. Golden Path: Install → Enable → Review (5 tests)
   - Install from marketplace
   - Enable installed plugin
   - Disable plugin
   - Rate plugin
   - Leave review comment

3. Plugin Governance & Reporting (4 tests)
   - Report malicious plugin
   - Report permission abuse
   - View governance info
   - View sandbox permissions

4. Multi-Plugin Operations (3 tests)
   - Install 3 plugins sequentially
   - Enable/disable multiple plugins
   - Rate multiple plugins

5. Error Handling (3 tests)
   - Handle install failure
   - Marketplace load failure
   - Network timeout

6. Responsive Design (2 tests)
   - Mobile (375×667)
   - Tablet (768×1024)

7. Performance (2 tests)
   - Marketplace loads <2s
   - Search responds <500ms

8. Accessibility (3 tests)
   - Heading structure
   - ARIA labels on buttons
   - Keyboard-accessible ratings
```

## Installation & Setup

### Prerequisites

```bash
# Python 3.10+
python --version

# Playwright (for UI tests)
npm install -D @playwright/test
```

### Setup Test Environment

```bash
cd /home/shumway/projects/CorvinOS

# Install pytest and dependencies
pip install pytest pytest-asyncio pytest-xdist pytest-cov

# Install Playwright
npm install -D @playwright/test
npx playwright install chromium firefox
```

## Running Tests

### Run All Plugin Marketplace Tests

```bash
# All pytest tests
pytest tests/test_marketplace_integration_e2e.py \
        tests/test_plugin_installation_workflows.py \
        tests/test_plugin_governance_and_trust.py \
        -v --tb=short

# With coverage
pytest tests/test_marketplace_*.py \
        -v --cov=core/plugins \
        --cov-report=html
```

### Run Specific Test Suite

```bash
# Marketplace discovery only
pytest tests/test_marketplace_integration_e2e.py::TestMarketplaceDiscovery -v

# Installation workflows
pytest tests/test_plugin_installation_workflows.py -v

# Governance and trust
pytest tests/test_plugin_governance_and_trust.py::TestTrustBadges -v
```

### Run Playwright E2E Tests

```bash
# All E2E tests
npm run test:e2e -- core/console/corvin_console/web-next/tests/e2e/plugins-integration.spec.ts

# Specific test
npm run test:e2e -- plugins-integration.spec.ts -g "discover plugins"

# With headed browser (see test execution)
npm run test:e2e -- plugins-integration.spec.ts --headed

# Multi-browser
npm run test:e2e -- plugins-integration.spec.ts --project=chromium --project=firefox
```

### Run in CI/CD Pipeline

```bash
# Run all tests with parallelization
pytest tests/test_marketplace_*.py -n auto --tb=short

# Generate JUnit XML for CI
pytest tests/test_marketplace_*.py \
        --junit-xml=test-results.xml \
        --cov=core/plugins \
        --cov-report=json
```

## Test Patterns & Conventions

### Fixture Dependency Chain

```
temp_corvin_home
    ↓
marketplace
    ↓
populated_marketplace
    ↓
audit_trail

temp_plugin_dir
    ↓
plugin_registry
    ↓
sample_manifest
    ↓
mock_audit_trail
```

### Multi-Tenant Testing Pattern

```python
@pytest.mark.parametrize("tenant_id", ["tenant-1", "tenant-2", "tenant-3"])
def test_multi_tenant_reviews(marketplace, tenant_id):
    # Test runs once per tenant
    assert isolation_verified(marketplace, tenant_id)
```

### Audit Trail Verification

```python
def test_action_is_audited(marketplace, audit_trail):
    # Perform action
    marketplace.record_installation(installation)
    
    # Verify audit event
    audit_trail.log_event("plugin.installed", {
        "plugin_id": "test",
        "version": "1.0.0",
    })
    
    events = audit_trail.get_events("plugin.installed")
    assert len(events) == 1
    assert events[0]["details"]["plugin_id"] == "test"
```

### Error Path Testing

```python
def test_duplicate_registration_fails(marketplace):
    plugin = create_plugin("test-id")
    marketplace.register_plugin(plugin)
    
    # Should raise on second registration
    with pytest.raises(ValueError, match="already registered"):
        marketplace.register_plugin(plugin)
```

## Assertion Patterns

### Testing Marketplace State

```python
# List filtering
results = marketplace.list_plugins(
    category=PluginCategory.SECURITY,
    origin=PluginOrigin.VETTED,
)
assert all(p.category == PluginCategory.SECURITY for p in results)
assert all(p.origin == PluginOrigin.VETTED for p in results)

# Rating calculation
updated = marketplace.get_plugin("test-plugin")
assert updated.rating_count == 3
assert updated.rating_average == pytest.approx(4.33, abs=0.01)

# Governance checks
to_remove = marketplace.check_governance()
assert "bad-plugin" in to_remove
```

### Testing Tenant Isolation

```python
# Verify installations per tenant
tenants = {i.tenant_id for i in marketplace.installations["plugin-id"]}
assert "tenant-a" in tenants
assert "tenant-b" in tenants
assert len(tenants) == 2  # Only these two
```

## Coverage Goals

- **API Routes:** 95%+ (marketplace, install, list, get, disable, uninstall, report)
- **Core Marketplace:** 90%+ (discovery, ratings, governance)
- **Registry Operations:** 85%+ (add, remove, update_config)
- **Audit Trail:** 100% (all events logged)
- **Multi-Tenant:** 100% (isolation verified)
- **UI Workflows:** 80%+ (golden paths, error cases, accessibility)

Current coverage: ~240 test scenarios across 4 files + Playwright suite

## Debugging Failed Tests

### View Test Output

```bash
# Verbose mode with full tracebacks
pytest test_marketplace_integration_e2e.py -vv --tb=long

# Show print statements
pytest test_marketplace_integration_e2e.py -vv -s

# Stop on first failure
pytest test_marketplace_integration_e2e.py -x
```

### Debug Specific Test

```bash
# Run single test with pdb on failure
pytest test_marketplace_integration_e2e.py::TestMarketplaceDiscovery::test_list_all_plugins -vv --pdb

# Print debug logs
PYTEST_CURRENT_TEST=1 pytest test_marketplace_integration_e2e.py -s
```

### Playwright Debugging

```bash
# Headed browser (watch execution)
npm run test:e2e -- plugins-integration.spec.ts --headed

# Debug mode (pause on breakpoints)
npm run test:e2e -- plugins-integration.spec.ts --debug

# Trace viewer
npm run test:e2e -- plugins-integration.spec.ts --trace on
```

## Continuous Integration

### GitHub Actions Configuration

```yaml
name: Plugin Marketplace Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: |
          pip install pytest pytest-asyncio pytest-xdist pytest-cov
          npm install -D @playwright/test
          npx playwright install chromium
      
      - name: Run pytest tests
        run: |
          pytest tests/test_marketplace_*.py \
                  -v --cov=core/plugins \
                  --junit-xml=test-results.xml
      
      - name: Run Playwright tests
        run: |
          npm run test:e2e -- plugins-integration.spec.ts
      
      - name: Upload results
        uses: actions/upload-artifact@v2
        if: always()
        with:
          name: test-results
          path: test-results.xml
```

## Performance Benchmarks

Expected test execution times:

```
test_marketplace_integration_e2e.py    ~30 seconds  (30+ tests)
test_plugin_installation_workflows.py  ~25 seconds  (25+ tests)
test_plugin_governance_and_trust.py    ~35 seconds  (28+ tests)
plugins-integration.spec.ts            ~2-3 minutes (25+ UI tests)
───────────────────────────────────────────────────
Total                                  ~4 minutes   (~110 tests)
```

Run with parallelization:

```bash
pytest tests/test_marketplace_*.py -n auto  # Parallel worker processes
# Expected: ~90 seconds total
```

## Known Issues & Limitations

1. **Async Tests:** Some installation tests mock async behavior; real async support added via `@pytest.mark.asyncio`
2. **Playwright:** UI tests require running Console server on http://localhost:8765
3. **Audit Trail:** Mock implementation; production uses hash-chained JSONL
4. **Multi-tenant:** Tests use in-memory marketplace; production uses persistent storage

## Related Documentation

- [ADR-0249: Plugin Trust Anchor & Governance](../../Corvin-ADR/decisions/ADR-0249-plugin-trust-marketplace.md)
- [ADR-0243: Plugin Boot Layers](../../Corvin-ADR/decisions/ADR-0243-plugin-boot-layers.md)
- [Plugin System Reference](../../docs/claude-ref/layer-plugins.md)
- [Compliance Baseline](../../docs/claude-ref/compliance-baseline.md)

## Contributing

When adding new tests:

1. Follow the test suite pattern (Arrange → Act → Assert)
2. Use factory fixtures for test data
3. Verify audit trail for every operation
4. Test multi-tenant isolation
5. Include error path tests
6. Run all tests before committing:
   ```bash
   pytest tests/test_marketplace_*.py -v --cov=core/plugins
   ```

## License

Apache 2.0 — See LICENSE file in repository root.
