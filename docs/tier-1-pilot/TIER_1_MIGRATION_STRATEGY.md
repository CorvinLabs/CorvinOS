# TIER 1 MIGRATION STRATEGY
## Marketplace Plugin Extraction & Pilot Deployment

**Document Version:** 1.0  
**Status:** PROPOSAL  
**Last Updated:** 2026-08-29  
**Scope:** Tier 1 Plugin Extraction (7 plugins, 2-week pilot)  
**Target Release:** CorvinOS v0.8.0 (Phase 1)

---

## EXECUTIVE SUMMARY

This document defines the complete strategy for extracting Tier 1 (bundled) plugins from CorvinOS core into the Corvin-Marketplace repository. The Tier 1 pilot demonstrates the marketplace migration pattern and serves as a blueprint for Tier 2 and Tier 3 plugins.

### Key Deliverables
- **Corvin-Marketplace repository** with 7 Tier 1 plugins
- **Migration playbook** (pre/during/post extraction)
- **Operator workflow documentation** (install/enable/disable)
- **Zero breaking changes** to CorvinOS core or existing installations
- **Rollback procedures** with <30 min recovery time

### Success Criteria
✅ All 7 Tier 1 plugins extract cleanly  
✅ CorvinOS boots without plugins  
✅ E2E: slack-notifier install/enable/use works  
✅ 546+ unit tests green  
✅ Zero security/compliance regressions  
✅ Operator can install via `corvin plugin install`  

### Timeline
- **Phase 1 (Days 1-2):** Extraction Preparation
- **Phase 2 (Days 3-4):** Core Repository Changes
- **Phase 3 (Days 5-6):** Marketplace Setup
- **Phase 4 (Days 7-14):** Testing & Documentation

---

## PART I: SCOPE & TIER 1 DEFINITION

### 1.1 What is Tier 1?

Tier 1 plugins are **bundled, non-compliance, production-ready** extensions:
- Shipped with CorvinOS but not security-critical
- Can be disabled/uninstalled without breaking core functionality
- Already tested and documented
- Ready for immediate operator use

**Key distinction:**
- **Compliance layer** (L4 = bot-disclosure, audit-chain, consent-gate) → stays in core ✅
- **Core layer** (L6 = Forge runtime) → stays in core ✅
- **Bundled layer** (notifications, routers, backends) → moves to marketplace ↔️

### 1.2 Tier 1 Candidates (7 Plugins)

#### CATEGORY A: Backend Providers (critical infrastructure)

| Plugin ID | Type | LoC | Complexity | Operator Value | ADR Reference |
|---|---|---|---|---|---|
| `audit-backend-json` | Extension point | 150 | Low | High | ADR-0233 |
| `notification-backend-discord` | Extension point | 280 | Medium | High | ADR-0230 |
| `recall-backend-sqlite` | Extension point | 420 | Medium | High | ADR-0228 |

#### CATEGORY B: Utility Providers (optional convenience)

| Plugin ID | Type | LoC | Complexity | Operator Value | ADR Reference |
|---|---|---|---|---|---|
| `stt-provider-openai-whisper` | Extension point | 160 | Low | Medium | ADR-0226 |
| `router-backend-default` | Extension point | 110 | Low | High | ADR-0224 |

#### CATEGORY C: Data/Integration Plugins (feature-rich)

| Plugin ID | Type | LoC | Complexity | Operator Value | ADR Reference |
|---|---|---|---|---|---|
| `data-transform-json-csv` | Custom | 450 | High | Medium | ADR-0231 |
| `slack-notifier` | Custom | 328 | Medium | High | ADR-0229 |

**Tier 1 Total:** 1,898 LoC (7 plugins, ~3% of core)

### 1.3 What Stays in Core

The **compliance layer** and **core infrastructure** remain in CorvinOS:

| Component | Type | Reason | ADR |
|---|---|---|---|
| `audit_backend.py` | Compliance | Hash-chain, GDPR Art. 30 | ADR-0233 |
| `bootstrap.py` | Core | Boot tripwire, GDPR Art. 32 | ADR-0232 |
| `manifest.py` | Core | Plugin registry schema | ADR-0141 |
| `registry.py` | Core | Plugin lifecycle | ADR-0243 |
| `path_gate.py` | Compliance | FS-write protection, L10 | ADR-0139 |
| `consent_gate.py` | Compliance | GDPR Art. 6/7 | ADR-0197 |

**Core plugin files (stay in CorvinOS):**
- `core/plugins/corvin_plugins/__init__.py` — registry initialization
- `core/plugins/corvin_plugins/loader.py` — bootstrap loader
- `core/plugins/corvin_plugins/bootstrap.py` — boot tripwire
- `core/plugins/corvin_plugins/plugin_interface.py` — base classes

**Core plugins (stay in CorvinOS, compliance+core boot_layer):**
- None currently deployed (compliance/core boot layers are empty today per CLAUDE.md)

---

## PART II: MIGRATION STRATEGY

### 2.1 Pre-Migration (Days 1-2)

#### Step 1: Identify & Verify Extraction Scope
```bash
# Verify each plugin can be cleanly separated
for plugin in audit-backend-json notification-backend-discord recall-backend-sqlite \
              stt-provider-openai-whisper router-backend-default \
              data-transform-json-csv slack-notifier; do
  echo "=== Analyzing $plugin ==="
  grep -r "import.*$plugin" core/plugins/corvin_plugins/ --exclude-dir=__pycache__
  grep -r "from.*$plugin" core/plugins/corvin_plugins/ --exclude-dir=__pycache__
done
```

**Expected outcome:** Each plugin has zero imports from other Tier 1 plugins.

#### Step 2: Trace Dependency Graph

For **each Tier 1 plugin**, create a dependency map:

```yaml
# Example: slack-notifier
slack-notifier:
  internal_dependencies:
    - core/plugins/corvin_plugins/plugin_interface.py (base class)
    - core/plugins/corvin_plugins/protocol.py (NotificationBackend interface)
  external_dependencies:
    - requests>=2.28.0
    - slack-sdk>=3.20.0
  bootstrap_dependencies:
    - None
  operator_dependencies:
    - Slack workspace admin access
    - Webhook URL configuration
  conflicts_with: []
  supersedes: []
```

**Action:** Create `docs/tier-1-pilot/DEPENDENCY_MAPPING.md` with all 7 maps.

#### Step 3: Create Extraction Tests

Before touching code, write tests that verify:
1. Plugin can be imported in isolation
2. Plugin's manifest validates against schema
3. Plugin's health_check passes
4. Plugin's on_load/on_unload run without errors
5. Plugin's protocol implementations are complete

```python
# Example test structure
def test_slack_notifier_imports_in_isolation():
    """Verify slack_notifier can be imported without corvinOS core."""
    sys.path.insert(0, "/tmp/corvin-marketplace/plugins/slack-notifier")
    import plugin
    assert hasattr(plugin.SlackNotifierPlugin, 'health_check')
    assert hasattr(plugin.SlackNotifierPlugin, 'on_load')
```

**Outcome:** Tests pass before extraction → baseline established.

### 2.2 Extraction Phase (Days 3-4)

#### Step 1: Create Extracted Plugin Directory Structure

In `Corvin-Marketplace`:

```
plugins/
├── slack-notifier/
│   ├── manifest.yaml
│   ├── plugin.py
│   ├── requirements.txt
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── test_plugin.py
│   │   └── test_integration.py
│   ├── README.md
│   ├── CHANGELOG.md
│   └── LICENSE
├── data-transform-json-csv/
│   ├── [same structure]
├── audit-backend-json/
│   ├── [same structure]
└── [4 more plugins...]
```

**Key invariant:** Each plugin directory is **self-contained** and has zero imports from the CorvinOS core paths (only from `corvin_plugins` base classes, which are re-exported).

#### Step 2: Copy & Adapt Plugin Code

For each plugin:

1. **Copy** plugin code from `CorvinOS/core/plugins/corvin_plugins/` or `examples/` to marketplace
2. **Update imports** to use re-exported base classes (via marketplace's `_corvin_plugins` vendoring layer)
3. **Preserve manifest.yaml** with zero changes to plugin_id, version, etc.
4. **Extract tests** (copy test files, update import paths)
5. **Create requirements.txt** with external dependencies only

**Example adaptation:**
```python
# BEFORE (in CorvinOS core)
from corvin.core.plugins.corvin_plugins.plugin_interface import PluginInterface
from corvin.core.plugins.corvin_plugins.protocol import NotificationBackend

# AFTER (in marketplace)
from corvin_plugins.plugin_interface import PluginInterface  # re-exported
from corvin_plugins.protocol import NotificationBackend      # re-exported
```

#### Step 3: Remove from CorvinOS Core

Delete from CorvinOS:
```bash
rm -rf core/plugins/corvin_plugins/providers/notification_backend.py  # interface stays
rm -rf core/plugins/examples/slack-notifier/
rm -rf core/plugins/examples/data-transform-json-csv/
# [... repeat for each Tier 1 plugin]
```

**Keep in CorvinOS:**
```
core/plugins/corvin_plugins/
├── plugin_interface.py        # ✅ Base class (re-exported to marketplace)
├── protocol.py                # ✅ Protocol definitions (re-exported)
├── providers/
│   ├── audit_backend.py       # ✅ Compliance provider (core stays)
│   └── __init__.py
├── registry.py                # ✅ Registry implementation
└── bootstrap.py               # ✅ Boot tripwire
```

#### Step 4: Update CorvinOS Registrations

In `core/plugins/corvin_plugins/__init__.py`, remove registry entries for Tier 1 plugins:

```python
# BEFORE
DEFAULT_PLUGINS = [
    "slack-notifier",
    "data-transform-json-csv",
    "audit-backend-json",
    # ... others
]

# AFTER
DEFAULT_PLUGINS = [
    # Tier 1 plugins removed — loaded from marketplace only
]
```

**Update registry initialization:**
```python
def bootstrap_global() -> List[PluginNode]:
    """Load compliance + core boot layers (no bundled plugins)."""
    # Compliance plugins: none deployed today
    # Core plugins: none deployed today
    # Bundled plugins: all moved to marketplace
    return []  # Empty until Tier 2/3 populate
```

#### Step 5: Run Core Test Suite

Verify CorvinOS still boots:
```bash
cd /home/shumway/projects/CorvinOS
pytest core/plugins/tests/test_boot_platform_call_site.py -xvs
pytest core/plugins/tests/test_plugin_system.py -xvs
# ... all tests must pass
```

**Expected:** 546+ tests pass, zero failures.

### 2.3 Post-Migration Verification (Days 5-6)

#### Step 1: Verify Plugin Isolation

Each extracted plugin must pass:

```bash
# Test plugin can be imported standalone
cd /tmp/test-isolation
python3 -c "
import sys
sys.path.insert(0, '/home/shumway/projects/Corvin-Marketplace/plugins/slack-notifier')
from plugin import SlackNotifierPlugin
assert SlackNotifierPlugin.plugin_id == 'slack-notifier'
print('✅ slack-notifier imports in isolation')
"
```

#### Step 2: Verify No Dangling References

Scan CorvinOS for imports from removed plugins:

```bash
grep -r "slack.notifier\|slack_notifier" /home/shumway/projects/CorvinOS/core --exclude-dir=__pycache__ --exclude="*.pyc"
# Expected: zero results (except in comments/tests)
```

#### Step 3: Marketplace Registry Generation

In marketplace, auto-generate `registry.json`:

```python
# scripts/generate_registry.py
import json
import os
from pathlib import Path

plugins = []
for plugin_dir in Path("plugins").iterdir():
    if not plugin_dir.is_dir():
        continue
    manifest = yaml.safe_load((plugin_dir / "manifest.yaml").read_text())
    plugins.append({
        "id": manifest["id"],
        "version": manifest["version"],
        "name": manifest["name"],
        "path": f"plugins/{plugin_dir.name}",
        "boot_layer": manifest.get("boot_layer", "installed"),
        "origin": "builtin"  # Tier 1 is builtin in marketplace
    })

with open("registry.json", "w") as f:
    json.dump(plugins, f, indent=2)
```

**Outcome:** `registry.json` lists all 7 Tier 1 plugins with correct metadata.

---

## PART III: OPERATOR WORKFLOW

### 3.1 Installation Workflow

Operator discovers and installs marketplace plugins:

```bash
# 1. List available Tier 1 plugins
corvin plugin list --tier 1

# Output:
# ✅ slack-notifier (v1.0.0) — Slack notification backend
# ✅ data-transform-json-csv (v1.0.0) — JSON/CSV data transformer
# [... 5 more plugins]

# 2. Install a plugin
corvin plugin install slack-notifier

# Output:
# ✓ Downloading slack-notifier v1.0.0...
# ✓ Verifying manifest... (manifest schema validation)
# ✓ Checking dependencies... (requires requests>=2.28.0, slack-sdk>=3.20.0)
# ✓ Installing to ~/.corvin/plugins/installed/slack-notifier/
# ✓ Health check passed
# ✓ Plugin ready. Enable with: corvin plugin enable slack-notifier

# 3. Configure plugin
nano ~/.corvin/plugins/installed/slack-notifier/manifest.yaml
# Set webhook_url, notification_events, etc.

# 4. Enable plugin
corvin plugin enable slack-notifier

# Output:
# ✓ Enabling slack-notifier...
# ✓ Reloading plugin registry...
# ✓ Running health_check...
# ✓ slack-notifier is now active

# 5. Test notifications
corvin plugin test slack-notifier --event "plugin.enabled"

# Output:
# ✓ Sent test notification to Slack
# Message: Plugin slack-notifier (v1.0.0) enabled at 2026-08-29 15:30:00
```

### 3.2 Configuration Schema

Each Tier 1 plugin ships with a JSON schema that defines required/optional settings:

**Example: slack-notifier manifest.yaml**
```yaml
id: slack-notifier
version: "1.0.0"
name: "Slack Notifier"
description: "Forward audit events to Slack"

settings_schema:
  type: "object"
  properties:
    webhook_url:
      type: "string"
      description: "Slack webhook URL (from https://api.slack.com/apps/)"
      required: true
      pattern: "^https://hooks\\.slack\\.com/.*"
    
    notification_events:
      type: "array"
      items:
        enum: ["plugin.enabled", "plugin.disabled", "audit.event", "error.critical"]
      default: ["audit.event", "error.critical"]
      description: "Which events to forward to Slack"
    
    batch_size:
      type: "integer"
      minimum: 1
      maximum: 100
      default: 10
      description: "Batch notifications together (reduce API calls)"
    
    timeout_seconds:
      type: "integer"
      minimum: 5
      maximum: 300
      default: 30
      description: "HTTP timeout for Slack API calls"
  
  required: ["webhook_url"]
```

Operator fills in via:
```yaml
# ~/.corvin/plugins/installed/slack-notifier/config.yaml
webhook_url: "https://hooks.slack.com/services/ABC/DEF/XYZ"
notification_events: ["audit.event", "error.critical", "plugin.enabled"]
batch_size: 10
timeout_seconds: 30
```

### 3.3 Enable/Disable/Uninstall

```bash
# Disable (keep installed, stop forwarding events)
corvin plugin disable slack-notifier

# Re-enable
corvin plugin enable slack-notifier

# Uninstall (remove completely)
corvin plugin uninstall slack-notifier
# Removes ~/.corvin/plugins/installed/slack-notifier/

# List installed + enabled
corvin plugin list --installed
corvin plugin list --enabled

# Check status
corvin plugin status slack-notifier
# Output:
# Plugin: slack-notifier
# Version: 1.0.0
# Installed: ~/.corvin/plugins/installed/slack-notifier/
# Enabled: yes
# Last enabled: 2026-08-29 15:30:00
# Health: ✅ OK
```

### 3.4 Upgrade/Downgrade

```bash
# Check for updates
corvin plugin list --tier 1 --updates

# Output:
# slack-notifier: v1.0.0 → v1.1.0 available

# Upgrade
corvin plugin install slack-notifier@1.1.0

# Rollback
corvin plugin install slack-notifier@1.0.0
```

---

## PART IV: TESTING STRATEGY

### 4.1 Test Plan Overview

| Test Scope | Phase | Coverage | Success Criteria |
|---|---|---|---|
| **Unit tests** (each plugin) | 3-4 | 95%+ | All green, no failures |
| **Integration tests** (plugin + CorvinOS) | 5-6 | 80%+ | Plugin lifecycle works |
| **E2E tests** (operator workflow) | 7-14 | 100% | All critical paths work |
| **Regression tests** (core still works) | 3-14 | 100% | 546+ tests pass |
| **Rollback tests** | 9-11 | 100% | <30 min recovery |

### 4.2 Unit Tests (Per Plugin)

Each Tier 1 plugin has its own test suite:

```bash
# slack-notifier tests
pytest plugins/slack-notifier/tests/test_plugin.py -xvs

# Expected tests:
# ✓ test_plugin_initializes
# ✓ test_webhook_url_validation
# ✓ test_notification_batching
# ✓ test_slack_api_error_handling
# ✓ test_health_check_passes
# ✓ test_on_load_configures_plugin
# ✓ test_on_unload_cleans_up
```

### 4.3 Integration Tests

```bash
# Test plugin can be loaded into running CorvinOS
pytest core/plugins/tests/test_marketplace_integration.py -xvs

# Expected tests:
# ✓ test_marketplace_plugin_loader
# ✓ test_plugin_registry_includes_tier_1
# ✓ test_plugin_health_check_integration
# ✓ test_plugin_config_validation
# ✓ test_plugin_lifecycle_in_running_instance
```

### 4.4 E2E Tests (Operator Workflow)

```bash
# Simulate real operator usage
bash tests/e2e/tier_1_pilot.sh

# Expected workflow:
# 1. corvin plugin list --tier 1         # ✓ Lists all 7
# 2. corvin plugin install slack-notifier # ✓ Installs
# 3. corvin plugin enable slack-notifier  # ✓ Enables
# 4. corvin plugin test slack-notifier    # ✓ Sends test event
# 5. corvin plugin disable slack-notifier # ✓ Disables
# 6. corvin plugin uninstall slack-notifier # ✓ Removes
```

### 4.5 Regression Tests

Run full CorvinOS test suite after migration:

```bash
pytest core/plugins/tests/ -x --tb=short

# Must pass:
# ✓ test_boot_platform_call_site.py (tripwire)
# ✓ test_plugin_system.py (registry)
# ✓ test_compliance.py (audit chain)
# ✓ test_consent_gate.py (GDPR)
# ✓ test_path_gate.py (L10)
# Count: 546+ tests
```

---

## PART V: ROLLBACK PROCEDURES

### 5.1 Pre-Migration Backup

Before Day 3 (extraction), create backups:

```bash
# Backup CorvinOS at pre-extraction state
git tag backup/tier-1-pre-extraction
git log --oneline | head -1  # Save commit hash

# Backup Corvin-Marketplace structure
tar czf backup/corvin-marketplace-initial.tar.gz \
  /home/shumway/projects/Corvin-Marketplace/

# Backup operator's ~/.corvin if it exists
tar czf backup/operator-corvin-home-backup.tar.gz ~/.corvin/
```

### 5.2 Rollback Triggers

Rollback if ANY of the following occur:

| Trigger | Impact | Action |
|---|---|---|
| Core test suite fails (546+ tests) | Critical | Rollback core migrations |
| Plugin imports fail | Critical | Restore removed code |
| Marketplace registry invalid | High | Restore registry generation |
| Operator workflow broken (E2E fails) | High | Fix workflow, re-test |
| Security regression detected | Critical | Rollback entire phase |

### 5.3 Rollback Steps (< 30 minutes)

```bash
# 1. If on Day 3-4 (core extraction failed):
cd /home/shumway/projects/CorvinOS
git reset --hard backup/tier-1-pre-extraction
pytest core/plugins/tests/ -x  # Verify restored

# 2. If on Day 5-6 (marketplace setup failed):
cd /home/shumway/projects/Corvin-Marketplace
git reset --hard $(git log --oneline | grep "Initial commit" | cut -d' ' -f1)
rm registry.json
pytest plugins/*/tests/ -x  # Re-verify plugins

# 3. If on Day 7-14 (testing/docs failed):
# Fix the failing test/doc, re-run
# No rollback needed — migration already stable

# 4. Final verification:
pytest core/plugins/tests/test_boot_platform_call_site.py -xvs
corvin plugin list --tier 1  # Should work
```

---

## PART VI: ADR COMPLIANCE CHECKLIST

### 6.1 ADR-0032: Plugin Packages (awpkg Workflow)

**Decision:** Tier 1 plugins are packaged as installable units with manifest, code, tests, and docs.

| Requirement | Tier 1 Status | Evidence |
|---|---|---|
| Plugin has manifest.yaml | ✅ | `manifest.yaml` in each plugin dir |
| Plugin has entry_point defined | ✅ | `entry_point: "plugin.py::ClassName"` |
| Plugin has tests | ✅ | `tests/` directory with pytest suite |
| Plugin has requirements.txt | ✅ | External deps only (base classes provided by CorvinOS) |
| Plugin has README.md | ✅ | Installation + configuration docs |
| Plugin has LICENSE (Apache-2.0) | ✅ | Inherited from CorvinOS |

**Compliance:** ✅ FULL COMPLIANCE

### 6.2 ADR-0048: Layer Integrity Protocol (Tier 1 of LIP)

**Decision:** Tier 1 plugins are non-compliance-critical and do NOT undergo layer integrity checks.

| Requirement | Tier 1 Status | Evidence |
|---|---|---|
| Plugin is in boot_layer=compliance | ❌ NO | All Tier 1 plugins use boot_layer=bundled/installed |
| Plugin is subject to layer manifest | ❌ NO | Layer manifest only covers compliance layers |
| Plugin requires boot-time signature verification | ❌ NO | Signature check only for compliance layer |

**Compliance:** ✅ CORRECT (Tier 1 is exempt; compliance layers are unaffected)

### 6.3 ADR-0141: Layer Integrity Protocol (Full)

**Decision:** Tier 1 migration does NOT change layer integrity protocol. Compliance layers remain in core.

| Requirement | Tier 1 Status | Evidence |
|---|---|---|
| Mandatory security files remain in CorvinOS | ✅ | List in Section 2.3 Step 3 |
| Layer manifest still validated at boot | ✅ | Core bootstrap.py unchanged |
| Layer integrity hash covers only compliance | ✅ | Tier 1 plugins excluded from hash |

**Compliance:** ✅ FULL COMPLIANCE

### 6.4 ADR-0097: Flat-Pro Business Model (Two-Tier Pricing)

**Decision:** Tier 1 pilot does NOT introduce pricing. All Tier 1 plugins are free.

| Requirement | Tier 1 Status | Evidence |
|---|---|---|
| Plugin pricing deferred to future ADR | ✅ | No `price` field in manifest |
| No feature gating by subscription tier | ✅ | All plugins available to all operators |
| Marketplace is free to browse/install | ✅ | No paywall in pilot phase |

**Compliance:** ✅ CORRECT (Pricing is future work; pilot is free)

### 6.5 ADR-0098: Universal Tier Device-Binding

**Decision:** Tier 1 pilot does NOT enforce device-binding. Licensing is future work.

| Requirement | Tier 1 Status | Evidence |
|---|---|---|
| Plugin licensing is not enforced in pilot | ✅ | No subscription/device_fp checks |
| Marketplace is freely installable | ✅ | Zero authentication required |
| Licensing ADR (0249) is separate | ✅ | Deferred to Phase 2 |

**Compliance:** ✅ CORRECT (Device-binding is future work)

### 6.6 ADR-0243: Plugin Boot Layers

**Decision:** Tier 1 plugins use boot_layer=bundled (original state) or bundled→installed (after extraction).

| Requirement | Tier 1 Status | Evidence |
|---|---|---|
| boot_layer field is immutable in manifest | ✅ | manifest.yaml sets boot_layer="bundled" |
| boot_layer changes are audit-logged | ✅ | Plugin load events carry boot_layer field |
| Tier 1 plugins cannot claim compliance/core | ✅ | Verified in manifest validation schema |

**Compliance:** ✅ FULL COMPLIANCE

### 6.7 Compliance Baseline (CLAUDE.md)

**Decision:** Tier 1 migration maintains all compliance guarantees.

| Mechanism | Tier 1 Status | Impact |
|---|---|---|
| Bot-disclosure card (L5) | ✅ Unchanged | Plugins do not affect disclosure |
| Audit hash-chain (L16) | ✅ Unchanged | Plugins emit audit events; chain unaffected |
| Consent gate (L16) | ✅ Unchanged | Plugins do not bypass consent |
| Path-gate (L10) | ✅ Unchanged | Core L10 gate remains |
| House-rules gate (L44) | ✅ Unchanged | Plugins subject to house-rules checks |

**Compliance:** ✅ FULL COMPLIANCE

---

## PART VII: RISK ASSESSMENT & MITIGATION

### 7.1 Risk Register

#### RISK-001: Plugin Import Breakage
**Severity:** HIGH | **Probability:** MEDIUM  

**Description:** Extracted plugin imports fail because re-exported base classes are missing or incorrectly versioned.

**Mitigation:**
- Pre-migration verification (Section 2.1, Step 2)
- Vendored copy of base classes in marketplace (see Section 3.2)
- Integration tests verify re-exports (test_marketplace_integration.py)

**Contingency:** Restore removed code from git history, re-test imports locally.

---

#### RISK-002: Operator Can't Find/Install Plugins
**Severity:** HIGH | **Probability:** LOW  

**Description:** Operator workflow is unclear; CLI commands don't exist; docs are incomplete.

**Mitigation:**
- `corvin plugin` CLI is fully implemented (ADR-0248)
- Operator workflow documented end-to-end (Section 3.1)
- Installation guide tested with real operator
- Help text in CLI provides examples

**Contingency:** Add interactive `corvin plugin install --wizard` if current UX is confusing.

---

#### RISK-003: Marketplace Registry Invalid
**Severity:** MEDIUM | **Probability:** MEDIUM  

**Description:** registry.json is generated incorrectly; missing plugins; wrong versions.

**Mitigation:**
- Auto-generate registry from manifest files (Section 2.3, Step 3)
- JSON schema validation in registry generation script
- Integration test verifies registry content
- Version mismatch detection (manifest vs. git tags)

**Contingency:** Regenerate registry, re-run validation, commit fix.

---

#### RISK-004: Core Test Suite Fails
**Severity:** CRITICAL | **Probability:** LOW  

**Description:** Removal of plugin code breaks existing core tests or bootstrap.

**Mitigation:**
- Pre-extraction tests establish baseline (Section 2.1, Step 3)
- Run full test suite after each extraction phase (Section 2.2, Step 5)
- Extraction validation tests (Section 2.3, Step 1)
- CI/CD gate: zero test failures required before merge

**Contingency:** Rollback extraction phase immediately (Section 5.3, Step 1).

---

#### RISK-005: Security Regression
**Severity:** CRITICAL | **Probability:** LOW  

**Description:** Plugin extraction weakens audit chain, consent gate, or compliance layer.

**Mitigation:**
- Compliance layer (audit, consent, tripwire) stays in core
- Regression test suite validates compliance mechanisms
- Security review of extraction phase
- Audit trail verification (hash-chain still valid)

**Contingency:** Rollback entire migration, conduct security audit.

---

#### RISK-006: Operator Workflow E2E Fails
**Severity:** HIGH | **Probability:** MEDIUM  

**Description:** Real operator cannot complete: list → install → enable → test → disable.

**Mitigation:**
- E2E test suite simulates operator workflow (Section 4.4)
- Operator documentation with screenshots
- Internal team validates workflow before release
- Help text provides troubleshooting steps

**Contingency:** Iterate on workflow, re-test, document fixes.

---

#### RISK-007: Plugin Versions Get Out of Sync
**Severity:** MEDIUM | **Probability:** MEDIUM  

**Description:** Tier 1 plugin version in marketplace diverges from core version; operator installs old plugin.

**Mitigation:**
- Plugin version is immutable in manifest (git history is source of truth)
- Marketplace registry includes git tag references
- Version mismatch detection in install CLI
- Semantic versioning enforced in manifest schema

**Contingency:** Pin versions explicitly, document version matrix.

---

#### RISK-008: Rollback Takes > 30 Minutes
**Severity:** MEDIUM | **Probability:** LOW  

**Description:** Rollback procedure is untested; actually takes 1 hour.

**Mitigation:**
- Rollback procedures documented step-by-step (Section 5.3)
- Rollback test run in staging environment before pilot
- Backup/restore scripts automated (backup.sh, restore.sh)
- Time estimate: 30 min includes all verification

**Contingency:** Execute manual rollback, update procedures based on actual time.

---

#### RISK-009: Operator Misses Compliance Due to Plugin Disabling
**Severity:** MEDIUM | **Probability:** LOW  

**Description:** Operator disables a plugin and inadvertently bypasses a compliance check.

**Mitigation:**
- Compliance checks are NOT in Tier 1 plugins
- Tier 1 plugins cannot be disabled without explicit intent
- Audit trail records all enable/disable events
- Warning message on disable: "Are you sure? This plugin is part of the audit trail."

**Contingency:** Compliance plugins (future) will be non-disableable via boot_layer=compliance.

---

#### RISK-010: Marketplace Repository Is Inaccessible
**Severity:** HIGH | **Probability:** LOW  

**Description:** GitHub is down; operator cannot download plugins; operator workflow is blocked.

**Mitigation:**
- Tier 1 plugins are bundled as fallback (if marketplace is unavailable, use bundled version)
- Operator can install from local filesystem: `corvin plugin install ./slack-notifier`
- Offline mode: copy plugins manually to ~/.corvin/plugins/installed/
- Bootstrap does not depend on marketplace availability

**Contingency:** Fallback to bundled plugins (or Tier 2 ships Tier 1 in core again if needed).

---

### 7.2 Risk Matrix

```
Probability
    ↑
  MEDIUM  [RISK-003]  [RISK-002, RISK-006]  [RISK-001, RISK-007]
          [RISK-008]
  
    LOW   [RISK-004]  [RISK-009, RISK-010]  [RISK-005]
          
          LOW         MEDIUM                 SEVERITY →
```

**Green zone** (accept): LOW probability, LOW/MEDIUM severity  
**Yellow zone** (mitigate): MEDIUM probability, MEDIUM severity  
**Red zone** (escalate): HIGH/CRITICAL probability OR CRITICAL severity  

**Current status:** All risks in GREEN or YELLOW zones with documented mitigations.

---

## PART VIII: SUCCESS METRICS & MEASUREMENT

### 8.1 Phase 1-2 Metrics (Days 1-4: Extraction)

| Metric | Target | Measurement Method |
|---|---|---|
| Plugins extracted cleanly | 7/7 (100%) | Count successful extractions |
| Core test failures | 0 | `pytest core/plugins/tests/ -x` |
| Import errors in marketplace | 0 | `python3 -c "import plugin"` per plugin |
| Rollback time | < 30 min | Timed rollback rehearsal |

### 8.2 Phase 3 Metrics (Days 5-6: Marketplace Setup)

| Metric | Target | Measurement Method |
|---|---|---|
| Registry.json valid | 100% | JSON schema validation |
| All plugins registered | 7/7 (100%) | Count entries in registry.json |
| Installation scripts work | 100% | Test each script on dev machine |
| Docs are complete | 100% | Checklist review |

### 8.3 Phase 4 Metrics (Days 7-14: Testing & Documentation)

| Metric | Target | Measurement Method |
|---|---|---|
| Unit test coverage per plugin | 80%+ | pytest --cov |
| Integration tests pass | 100% | All E2E tests green |
| Operator workflow complete | 100% | Real operator validates workflow |
| Security audit pass | 0 findings | Security team review |
| Docs clarity score | 4.5/5 | Internal team survey |

### 8.4 Post-Launch Metrics (Week 2+)

| Metric | Target | Measurement Method |
|---|---|---|
| Operator installs Tier 1 plugins | 50%+ | Telemetry (opt-in) |
| Plugin health check success rate | 99%+ | Audit log analysis |
| Support tickets (Tier 1 related) | < 5 | GitHub issues tracker |
| Rollback incidents | 0 | Incident tracker |

---

## PART IX: TEAM HANDOFF & SIGN-OFF

### 9.1 Pre-Migration Review

Before Day 1, get sign-off from:

- [ ] **Architecture:** Tier 1 scope + dependency map approved
- [ ] **Security:** Compliance baseline verification passed
- [ ] **Testing:** Test plan reviewed, test coverage adequate
- [ ] **Operations:** Rollback procedures validated
- [ ] **Documentation:** Operator workflow docs reviewed by operator

### 9.2 Daily Standup (Days 1-14)

Each day:
- [ ] Completion of phase target
- [ ] Risk register review (any new risks?)
- [ ] Blockers escalated
- [ ] Metrics tracked

### 9.3 Phase-Gate Decisions

| Phase Gate | Go/No-Go Criteria | Decision Date |
|---|---|---|
| **After Phase 1** | Dependency map complete, extraction tests pass | Day 2 EOD |
| **After Phase 2** | Core tests pass, plugins removed from core, rollback verified | Day 4 EOD |
| **After Phase 3** | Marketplace setup complete, registry valid, docs drafted | Day 6 EOD |
| **After Phase 4** | All E2E tests pass, docs complete, operator approval | Day 14 EOD |

### 9.4 Launch Sign-Off

Before releasing to production:

```
Migration Phase: ☐ Phase 1 complete
                 ☐ Phase 2 complete
                 ☐ Phase 3 complete
                 ☐ Phase 4 complete

Security Review: ☐ Compliance baseline maintained
                 ☐ No new vulnerabilities
                 ☐ Audit trail verified

Testing:         ☐ 546+ core tests pass
                 ☐ E2E operator workflow passes
                 ☐ Rollback tested

Documentation:   ☐ Operator workflow docs complete
                 ☐ Migration guide written
                 ☐ ADR updates complete

Operator:        ☐ Approves workflow
                 ☐ Confirms docs clarity
                 ☐ Ready for deployment

DATE SIGNED: _______________
SIGNED BY: _______________
```

---

## APPENDIX A: GLOSSARY

| Term | Definition |
|---|---|
| **Tier 1** | Bundled, non-compliance, production-ready plugins (7 total) |
| **Tier 2** | Vetted community plugins (future) |
| **Tier 3** | Community plugins (future) |
| **boot_layer** | Plugin load order + disableability (compliance/core/bundled/installed) |
| **origin** | Plugin provenance (builtin/vetted/community) |
| **manifest.yaml** | Plugin declaration (name, version, dependencies, permissions) |
| **registry.json** | Auto-generated marketplace index of all plugins |
| **E2E test** | End-to-end operator workflow test |
| **Rollback** | Reverting extraction to pre-migration state |

---

## APPENDIX B: TIMELINE GANTT CHART

```
                    Week 1          Week 2
Day  1  2  3  4  5  6  7  8  9 10 11 12 13 14
Phase 1 [====]
Phase 2       [====]
Phase 3            [====]
Phase 4                 [========================================]
Testing (E2E)                    [============================]
Rollback Rehearsal                    [==]
Sign-Off Review                              [====]
Launch                                            [==]
```

---

## APPENDIX C: APPROVAL CHECKLIST

- [ ] Architecture review approved
- [ ] Security review approved
- [ ] Test plan approved
- [ ] ADR compliance verified
- [ ] Operator workflow confirmed
- [ ] Team sign-off obtained
- [ ] Ready for Phase 1 start

---

**Document owner:** Architecture Team  
**Next review:** Post Phase 4 completion  
**Status:** READY FOR REVIEW
