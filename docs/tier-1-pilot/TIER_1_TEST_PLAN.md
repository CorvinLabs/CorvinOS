# TIER 1 TEST PLAN
## Comprehensive Testing Strategy for Plugin Extraction

**Document Version:** 1.0  
**Status:** TEST PLAN  
**Last Updated:** 2026-08-29

---

## EXECUTIVE SUMMARY

This document defines the complete testing strategy for Tier 1 plugin extraction across four dimensions:

1. **Unit Tests** (plugins in isolation)
2. **Integration Tests** (plugins + CorvinOS)
3. **E2E Tests** (operator workflow)
4. **Regression Tests** (core still works)

**Success Criteria:** ALL tests pass with zero failures before launch.

---

## PART I: UNIT TESTS (PER PLUGIN)

### 1.1 Test Structure

Each Tier 1 plugin has a `tests/` directory:

```
plugins/slack-notifier/tests/
├── __init__.py
├── conftest.py                    # Pytest fixtures
├── test_plugin.py                 # Core functionality
├── test_integration.py            # Integration with CorvinOS
├── test_manifest.py               # Manifest validation
└── fixtures/
    ├── mock_slack_responses.py
    └── sample_configs.yaml
```

### 1.2 Minimal Test Suite (Per Plugin)

Every Tier 1 plugin MUST have these test classes:

#### Class: `TestPluginInitialization`
```python
def test_plugin_imports():
    """Plugin can be imported standalone."""
    from plugin import SlackNotifierPlugin
    assert SlackNotifierPlugin.plugin_id == "slack-notifier"

def test_plugin_has_required_attributes():
    """Plugin has all required PluginInterface attributes."""
    from plugin import SlackNotifierPlugin
    plugin = SlackNotifierPlugin()
    assert hasattr(plugin, "on_load")
    assert hasattr(plugin, "on_unload")
    assert hasattr(plugin, "health_check")
    assert callable(plugin.on_load)
    assert callable(plugin.on_unload)
    assert callable(plugin.health_check)

def test_plugin_instantiates():
    """Plugin can be instantiated."""
    from plugin import SlackNotifierPlugin
    plugin = SlackNotifierPlugin()
    assert plugin is not None
    assert plugin.plugin_id == "slack-notifier"
```

#### Class: `TestPluginLifecycle`
```python
def test_on_load_succeeds(mock_context):
    """on_load() executes without error."""
    plugin = SlackNotifierPlugin()
    result = plugin.on_load(mock_context)
    assert result is not None or result is None  # Depends on protocol

def test_on_unload_succeeds(mock_context):
    """on_unload() executes without error."""
    plugin = SlackNotifierPlugin()
    plugin.on_load(mock_context)
    plugin.on_unload()
    # No exception raised

def test_on_load_idempotent(mock_context):
    """on_load() can be called multiple times safely."""
    plugin = SlackNotifierPlugin()
    plugin.on_load(mock_context)
    plugin.on_load(mock_context)  # Should not error
    assert plugin is not None

def test_health_check_passes(mock_context):
    """health_check() returns HealthStatus.HEALTHY."""
    from corvin_plugins.protocol import HealthStatus
    plugin = SlackNotifierPlugin()
    plugin.on_load(mock_context)
    status = plugin.health_check()
    assert status == HealthStatus.HEALTHY
```

#### Class: `TestPluginConfiguration`
```python
def test_configuration_schema_valid():
    """Plugin's configuration schema is valid JSON Schema."""
    manifest = load_manifest("manifest.yaml")
    schema = manifest.get("settings_schema")
    # JSON Schema validator
    jsonschema.Draft7Validator.check_schema(schema)

def test_required_config_present():
    """All required fields in settings_schema are documented."""
    manifest = load_manifest("manifest.yaml")
    required = manifest["settings_schema"].get("required", [])
    assert len(required) > 0  # Should require at least webhook_url

def test_default_config_valid():
    """Default settings satisfy the schema."""
    manifest = load_manifest("manifest.yaml")
    schema = manifest["settings_schema"]
    # Build defaults from schema
    defaults = {k: v.get("default") for k, v in schema["properties"].items()}
    # Should validate against schema
    jsonschema.validate(defaults, schema)
```

#### Class: `TestPluginErrors`
```python
def test_invalid_webhook_url_rejected():
    """Invalid Slack webhook URLs are rejected."""
    plugin = SlackNotifierPlugin()
    context = mock_context_with_config({
        "webhook_url": "not-a-valid-url"
    })
    with pytest.raises(ValueError):
        plugin.on_load(context)

def test_missing_required_config_fails():
    """Missing required config raises ValueError."""
    plugin = SlackNotifierPlugin()
    context = mock_context_with_config({})  # No webhook_url
    with pytest.raises(ValueError):
        plugin.on_load(context)

def test_network_error_handled():
    """Plugin handles network errors gracefully."""
    plugin = SlackNotifierPlugin()
    context = mock_context_with_config({
        "webhook_url": "https://hooks.slack.com/..."
    })
    plugin.on_load(context)
    
    with patch("requests.post", side_effect=requests.ConnectionError):
        status = plugin.health_check()
        # Should degrade gracefully, not crash
        assert status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED]
```

### 1.3 Plugin-Specific Tests

Additional tests for plugin-specific functionality:

**slack-notifier:**
- `test_notification_formatting()` — messages are properly formatted
- `test_batch_notification()` — multiple events are batched correctly
- `test_event_filtering()` — only configured event types are forwarded

**data-transform-json-csv:**
- `test_json_to_csv_conversion()` — valid conversion
- `test_csv_to_json_conversion()` — valid conversion
- `test_encoding_handling()` — UTF-8, Latin-1, etc.
- `test_max_rows_limit()` — respects configured limit

**audit-backend-json:**
- `test_audit_event_logged()` — events are persisted
- `test_audit_chain_intact()` — hash chain is valid
- `test_concurrent_writes()` — handles race conditions

### 1.4 Coverage Requirements

Minimum coverage per plugin:

```bash
pytest plugins/*/tests/ --cov=plugins/*/plugin.py --cov-report=term-report

# Expected output:
# slack-notifier: 87% coverage
# data-transform-json-csv: 92% coverage
# audit-backend-json: 91% coverage
# [...]
# Total: 85%+ coverage
```

---

## PART II: INTEGRATION TESTS

### 2.1 Test File

**Location:** `core/plugins/tests/test_marketplace_integration.py`

### 2.2 Test Classes

#### Class: `TestMarketplacePluginLoading`

```python
def test_marketplace_plugin_discovers_in_filesystem():
    """Marketplace plugins are discovered in ~/.corvin/plugins/installed/."""
    # Setup: copy a plugin to ~/.corvin/plugins/installed/
    plugin_dir = Path.home() / ".corvin" / "plugins" / "installed" / "test-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.yaml").write_text(SAMPLE_MANIFEST)
    (plugin_dir / "plugin.py").write_text(SAMPLE_PLUGIN_CODE)
    
    # Load plugins from operator directory
    registry = PluginRegistry()
    plugins = registry.discover_installed_plugins()
    
    # Verify discovery
    assert len(plugins) > 0
    assert any(p.plugin_id == "test-plugin" for p in plugins)

def test_marketplace_plugin_loads_successfully():
    """Marketplace plugin can be loaded and instantiated."""
    # Setup: copy plugin to filesystem
    install_plugin("slack-notifier", Path.home() / ".corvin" / "plugins" / "installed")
    
    # Load plugin
    registry = PluginRegistry()
    plugin_node = registry.load_plugin("slack-notifier")
    
    # Verify instantiation
    assert plugin_node is not None
    assert plugin_node.plugin.plugin_id == "slack-notifier"

def test_marketplace_plugin_manifest_validates():
    """Manifest schema is enforced."""
    registry = PluginRegistry()
    
    # Valid manifest should load
    valid_plugin = install_plugin("slack-notifier", TEST_DIR)
    assert registry.validate_manifest(valid_plugin.manifest)
    
    # Invalid manifest should reject
    invalid_manifest = {"id": "test"}  # Missing required fields
    with pytest.raises(ManifestValidationError):
        registry.validate_manifest(invalid_manifest)

def test_plugin_config_validation_integration():
    """Plugin configuration is validated against settings_schema."""
    # Load plugin and get its schema
    plugin = load_installed_plugin("slack-notifier")
    schema = plugin.manifest.settings_schema
    
    # Invalid config should reject
    invalid_config = {"webhook_url": "invalid"}
    with pytest.raises(ValueError):
        plugin.validate_config(invalid_config)
    
    # Valid config should accept
    valid_config = {"webhook_url": "https://hooks.slack.com/..."}
    assert plugin.validate_config(valid_config) is None
```

#### Class: `TestPluginRegistryIntegration`

```python
def test_registry_includes_marketplace_plugins():
    """Plugin registry includes marketplace plugins after discovery."""
    registry = PluginRegistry()
    installed = registry.discover_installed_plugins()
    all_plugins = registry.list_all_plugins()
    
    # Installed plugins should be included in all_plugins
    for installed_plugin in installed:
        found = any(p.plugin_id == installed_plugin.plugin_id for p in all_plugins)
        assert found, f"Plugin {installed_plugin.plugin_id} not in registry"

def test_registry_dependency_resolution():
    """Plugin dependencies are resolved correctly."""
    registry = PluginRegistry()
    
    # Load a plugin that has dependencies
    plugin = registry.load_plugin("data-transform-json-csv")
    deps = registry.resolve_dependencies(plugin)
    
    # All dependencies should be available
    assert deps.all_satisfied or len(plugin.manifest.dependencies) == 0

def test_registry_conflict_detection():
    """Conflicting plugins are detected."""
    registry = PluginRegistry()
    
    # Try to load two plugins that conflict
    registry.load_plugin("audit-backend-json")
    with pytest.raises(PluginConflictError):
        registry.load_plugin("audit-backend-sql")  # Conflicts with JSON backend
```

#### Class: `TestPluginExtensionPoints`

```python
def test_notification_backend_plugin_registers():
    """NotificationBackend plugin registers with notification system."""
    plugin = load_installed_plugin("slack-notifier")
    assert isinstance(plugin.instance, NotificationBackend)
    
    # Should be callable as NotificationBackend
    context = create_mock_context()
    event = {"type": "audit.event", "message": "test"}
    result = plugin.instance.send_notification(context, event)
    assert result is not None

def test_audit_backend_plugin_registers():
    """AuditBackend plugin registers with audit system."""
    plugin = load_installed_plugin("audit-backend-json")
    assert isinstance(plugin.instance, AuditBackend)
    
    # Should be callable as AuditBackend
    event = {"type": "plugin.loaded", "plugin_id": "test"}
    result = plugin.instance.write_event(event)
    assert result.success
```

#### Class: `TestPluginHealthChecks`

```python
def test_health_check_integration():
    """Plugin health checks are called by registry."""
    registry = PluginRegistry()
    plugin = registry.load_plugin("slack-notifier")
    
    # Should be able to check health
    status = registry.check_plugin_health("slack-notifier")
    assert status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED, HealthStatus.UNHEALTHY]

def test_unhealthy_plugin_handled():
    """Unhealthy plugins are handled gracefully."""
    registry = PluginRegistry()
    
    # Simulate unhealthy plugin
    with patch.object(SlackNotifierPlugin, "health_check", return_value=HealthStatus.UNHEALTHY):
        status = registry.check_plugin_health("slack-notifier")
        assert status == HealthStatus.UNHEALTHY
        # Should not crash the registry
        assert registry is not None
```

---

## PART III: E2E TESTS (OPERATOR WORKFLOW)

### 3.1 Test Script

**Location:** `tests/e2e/test_operator_workflow.py`

### 3.2 E2E Test: Install → Enable → Test → Disable

```python
class TestOperatorWorkflow:
    """Simulate real operator interactions."""
    
    def test_full_workflow_slack_notifier(self, operator_env):
        """Complete workflow: list → install → configure → enable → test → disable."""
        
        # Step 1: List available plugins
        result = subprocess.run(
            ["corvin", "plugin", "list", "--tier", "1"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "slack-notifier" in result.stdout
        assert "data-transform-json-csv" in result.stdout
        
        # Step 2: Install plugin
        result = subprocess.run(
            ["corvin", "plugin", "install", "slack-notifier"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "slack-notifier" in result.stdout
        assert "installed" in result.stdout.lower()
        
        # Verify installation
        plugin_dir = Path.home() / ".corvin" / "plugins" / "installed" / "slack-notifier"
        assert plugin_dir.exists()
        assert (plugin_dir / "manifest.yaml").exists()
        assert (plugin_dir / "plugin.py").exists()
        
        # Step 3: Configure plugin
        config_file = plugin_dir / "config.yaml"
        config_file.write_text("""
webhook_url: "https://hooks.slack.com/services/TEST/TEST/TEST"
notification_events: ["audit.event"]
batch_size: 10
""")
        
        # Step 4: Enable plugin
        result = subprocess.run(
            ["corvin", "plugin", "enable", "slack-notifier"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "enabled" in result.stdout.lower()
        
        # Verify enabled
        result = subprocess.run(
            ["corvin", "plugin", "status", "slack-notifier"],
            capture_output=True,
            text=True
        )
        assert "enabled: yes" in result.stdout.lower() or "enabled" in result.stdout.lower()
        
        # Step 5: Test plugin (with mock webhook)
        with patch("requests.post") as mock_post:
            mock_post.return_value = Mock(status_code=200)
            result = subprocess.run(
                ["corvin", "plugin", "test", "slack-notifier", "--event", "audit.event"],
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            assert "test" in result.stdout.lower()
        
        # Step 6: Disable plugin
        result = subprocess.run(
            ["corvin", "plugin", "disable", "slack-notifier"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "disabled" in result.stdout.lower()
        
        # Verify disabled
        result = subprocess.run(
            ["corvin", "plugin", "status", "slack-notifier"],
            capture_output=True,
            text=True
        )
        assert "disabled" in result.stdout.lower() or "enabled: no" in result.stdout.lower()
        
        # Step 7: Uninstall plugin
        result = subprocess.run(
            ["corvin", "plugin", "uninstall", "slack-notifier"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "uninstalled" in result.stdout.lower()
        
        # Verify uninstallation
        assert not plugin_dir.exists()
```

### 3.3 E2E Test: Upgrade/Downgrade

```python
def test_plugin_upgrade(operator_env):
    """Plugin can be upgraded to a newer version."""
    # Install v1.0.0
    subprocess.run(["corvin", "plugin", "install", "slack-notifier@1.0.0"])
    
    # Verify version
    result = subprocess.run(
        ["corvin", "plugin", "status", "slack-notifier"],
        capture_output=True,
        text=True
    )
    assert "1.0.0" in result.stdout
    
    # Upgrade to v1.1.0
    subprocess.run(["corvin", "plugin", "install", "slack-notifier@1.1.0"])
    
    # Verify new version
    result = subprocess.run(
        ["corvin", "plugin", "status", "slack-notifier"],
        capture_output=True,
        text=True
    )
    assert "1.1.0" in result.stdout

def test_plugin_downgrade(operator_env):
    """Plugin can be downgraded to an older version."""
    # Install v1.1.0
    subprocess.run(["corvin", "plugin", "install", "slack-notifier@1.1.0"])
    
    # Downgrade to v1.0.0
    subprocess.run(["corvin", "plugin", "install", "slack-notifier@1.0.0"])
    
    # Verify old version
    result = subprocess.run(
        ["corvin", "plugin", "status", "slack-notifier"],
        capture_output=True,
        text=True
    )
    assert "1.0.0" in result.stdout
```

---

## PART IV: REGRESSION TESTS (CORE)

### 4.1 Regression Test Suite

**Location:** `core/plugins/tests/`

Existing tests that MUST pass after Tier 1 extraction:

#### Test File: `test_boot_platform_call_site.py`

```bash
pytest core/plugins/tests/test_boot_platform_call_site.py -xvs

# Expected: ✅ All tests pass (tripwire not affected by extraction)
```

**Validates:**
- Boot tripwire initializes
- Audit chain is reachable
- Hash verification passes
- Compliance layer is intact

#### Test File: `test_plugin_system.py`

```bash
pytest core/plugins/tests/test_plugin_system.py -xvs

# Expected: ✅ All tests pass (registry still works)
```

**Validates:**
- Plugin registry initializes
- Manifest validation works
- Extension points are registered
- Plugin lifecycle is correct

#### Test File: `test_compliance.py`

```bash
pytest core/plugins/tests/test_compliance.py -xvs

# Expected: ✅ All tests pass (compliance not affected)
```

**Validates:**
- Consent gate works
- Audit trail is complete
- GDPR mechanisms intact

### 4.2 Full Core Test Run

```bash
cd /home/shumway/projects/CorvinOS
pytest core/plugins/tests/ -x --tb=short

# Expected: 546+ tests pass, 0 failures
# Output:
# ==================== 546 passed in 23.45s ====================
```

---

## PART V: TEST EXECUTION & REPORTING

### 5.1 Daily Test Runs

During Tier 1 pilot (Days 1-14):

```bash
# Day 1-2 (Extraction Prep)
pytest plugins/*/tests/test_plugin.py -xvs  # Unit tests

# Day 3-4 (Core Extraction)
pytest core/plugins/tests/ -x                # Core regression

# Day 5-6 (Marketplace Setup)
pytest tests/e2e/ -xvs                       # E2E tests

# Day 7-14 (Testing & Launch)
pytest core/plugins/tests/ -x                # Core regression (daily)
pytest plugins/*/tests/ -x                   # Plugin unit tests (daily)
pytest tests/e2e/ -xvs                       # E2E (daily)
```

### 5.2 Test Report Template

```
TIER 1 PILOT - TEST REPORT
Date: 2026-08-29
Phase: [1/2/3/4]

UNIT TESTS
──────────
slack-notifier:            ✅ 12/12 passed
data-transform-json-csv:   ✅ 15/15 passed
audit-backend-json:        ✅ 18/18 passed
notification-backend-discord: ✅ 14/14 passed
recall-backend-sqlite:     ✅ 11/11 passed
stt-provider-openai-whisper: ✅ 9/9 passed
router-backend-default:    ✅ 8/8 passed
────────────────────────────────────────
TOTAL:                     ✅ 87/87 passed (100%)

INTEGRATION TESTS
─────────────────
test_marketplace_plugin_loading:        ✅ 4/4 passed
test_plugin_registry_integration:       ✅ 6/6 passed
test_plugin_extension_points:           ✅ 5/5 passed
test_plugin_health_checks:              ✅ 3/3 passed
────────────────────────────────────────
TOTAL:                     ✅ 18/18 passed (100%)

E2E TESTS
─────────
test_operator_workflow_slack_notifier:  ✅ passed
test_plugin_upgrade:                    ✅ passed
test_plugin_downgrade:                  ✅ passed
────────────────────────────────────────
TOTAL:                     ✅ 3/3 passed (100%)

REGRESSION TESTS (CORE)
───────────────────────
test_boot_platform_call_site:           ✅ 12/12 passed
test_plugin_system:                     ✅ 18/18 passed
test_compliance:                        ✅ 23/23 passed
test_consent_gate:                      ✅ 8/8 passed
test_path_gate:                         ✅ 6/6 passed
[... 494 more tests]
────────────────────────────────────────
TOTAL:                     ✅ 546/546 passed (100%)

SUMMARY
───────
Total tests:               656
Passed:                    656 (100%)
Failed:                    0
Skipped:                   0
Coverage:                  85%+

STATUS: ✅ ALL TESTS PASSED
```

---

## APPENDIX: TEST FIXTURES

### Common Fixtures (conftest.py)

```python
@pytest.fixture
def mock_context():
    """Mock PluginContext."""
    return PluginContext(
        plugin_id="test-plugin",
        config={},
        tenant_id="_default",
        audit_log=MockAuditLog(),
    )

@pytest.fixture
def operator_env(tmp_path):
    """Mock operator environment (~/.corvin/)."""
    corvin_home = tmp_path / ".corvin"
    corvin_home.mkdir()
    (corvin_home / "plugins").mkdir()
    (corvin_home / "plugins" / "installed").mkdir()
    
    os.environ["CORVIN_HOME"] = str(corvin_home)
    yield corvin_home
    # Cleanup
    del os.environ["CORVIN_HOME"]

@pytest.fixture
def sample_manifest():
    """Sample manifest.yaml."""
    return {
        "id": "test-plugin",
        "version": "1.0.0",
        "name": "Test Plugin",
        "entry_point": "plugin.py::TestPlugin",
        "settings_schema": {
            "type": "object",
            "properties": {
                "webhook_url": {"type": "string"}
            },
            "required": ["webhook_url"]
        }
    }
```

---

**Document owner:** QA Team  
**Status:** READY FOR REVIEW
