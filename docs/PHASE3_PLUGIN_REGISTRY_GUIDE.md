# Phase 3: Plugin Registry Consistency — Integration Guide

**Date:** 2026-09-26  
**Status:** COMPLETE  
**ADR:** [ADR-2067](../Corvin-ADR/decisions/ADR-2067-plugin-registry-consistency-phase3.md)

---

## Overview

Phase 3 implements **plugin registry consistency** to ensure all CorvinOS instances have identical plugin sets, versions, and checksums.

**Key Features:**
- Canonical plugin manifest (file-based, dev; S3/GCS-ready, prod)
- Hash verification (detect tampering)
- Dependency resolution (DAG, topological sort)
- Auto-remediation (install missing, update versions)
- Audit trail (GDPR Art. 30, 32)
- Phase 4 integration (Slack/PagerDuty alerts)

---

## Architecture

### Four Core Modules

```
┌─────────────────────────────────────────────────────┐
│         Phase 3: Plugin Registry Consistency        │
├─────────────────────────────────────────────────────┤
│                                                     │
│  1. Canonical Manifest ──────┐                    │
│     (canonical_manifest.py)  │                    │
│                              ├──> Registry Sync   │
│  2. Hash Verification ───────┤    (registry_sync) │
│     (hash_verification.py)   │                    │
│                              ├──> Drift Detection │
│  3. Dependency Resolver ─────┤    (drift_detector)│
│     (dependency_resolver.py) │                    │
│                              │    ┌──────────────┐│
│  4. Registry Sync ───────────┘    │ Phase 4:     ││
│     (registry_sync.py)            │ Monitoring   ││
│                                   │ + Alerting   ││
│                                   └──────────────┘│
│                                                    │
└─────────────────────────────────────────────────────┘
```

### Data Flow

```
Canonical Manifest (JSON)
  ↓
  └─→ CanonicalManifestManager
      ├─→ Load manifest
      ├─→ Write manifest (atomic)
      └─→ Verify integrity

Plugin Integrity Verification
  ↓
  └─→ PluginHashVerifier
      ├─→ Compute SHA256 hash (all files, sorted order)
      ├─→ Compare against canonical
      └─→ Report severity (CRITICAL/HIGH/MEDIUM/LOW)

Dependency Validation
  ↓
  └─→ DependencyResolver
      ├─→ Build dependency graph
      ├─→ Detect circular dependencies (fail-closed)
      ├─→ Topological sort (Kahn's algorithm)
      └─→ Compute installation/uninstall order

Auto-Remediation
  ↓
  └─→ PluginRegistrySynchronizer
      ├─→ Detect drifts (MISSING, VERSION_MISMATCH, CHECKSUM_MISMATCH)
      ├─→ Auto-remediate (safe cases)
      ├─→ Audit logging (GDPR)
      └─→ Phase 4 integration

Phase 4 Monitoring
  ↓
  └─→ DriftDetectionService
      ├─→ Poll every 30 seconds
      ├─→ Route to Slack (all severities)
      ├─→ Route to PagerDuty (CRITICAL only)
      └─→ Write to audit trail
```

---

## Module Details

### 1. Canonical Manifest (`canonical_manifest.py`)

**Purpose:** Central registry of plugin versions and checksums

**Key Classes:**
- `CanonicalManifest` — dataclass for manifest
- `PluginEntry` — dataclass for a single plugin
- `CanonicalManifestManager` — load/write/verify operations

**API:**
```python
from core.plugins.canonical_manifest import CanonicalManifestManager, PluginEntry

manager = CanonicalManifestManager()

# Load manifest
manifest = manager.load_manifest()  # ~250 lines of code

# Add plugin
plugin = PluginEntry(
    plugin_id="my_plugin",
    version="1.0.0",
    checksum="sha256_hash",
    dependencies=["dep_plugin"],
    boot_layer="bundled",
)
manager.add_plugin(plugin)

# Verify integrity
is_valid = manager.verify_manifest_integrity()
```

**Fail-Closed Semantics:**
- Missing manifest → empty list (safe default)
- Corrupt manifest → RuntimeError caught, empty list
- Tampering → manifest_hash mismatch detected

---

### 2. Hash Verification (`hash_verification.py`)

**Purpose:** Verify plugins have not been tampered with

**Key Classes:**
- `PluginHashVerifier` — verify integrity
- `VerificationResult` — result of verification
- `VerificationSeverity` — severity levels

**API:**
```python
from core.plugins.hash_verification import PluginHashVerifier, VerificationSeverity

verifier = PluginHashVerifier()

# Verify single plugin
result = verifier.verify_integrity("plugin_id")
assert result.is_valid
assert result.severity == VerificationSeverity.LOW

# Verify all plugins
results = verifier.verify_all_plugins()
summary = verifier.get_verification_summary(results)
print(f"Valid: {summary['valid']}/{summary['total_plugins']}")
```

**Severity Levels:**
- `CRITICAL` — Hash mismatch (tampering)
- `HIGH` — Version mismatch
- `MEDIUM` — Minor drift
- `LOW` — All valid

---

### 3. Dependency Resolution (`dependency_resolver.py`)

**Purpose:** Validate dependencies form DAG and compute installation order

**Key Classes:**
- `DependencyResolver` — resolve dependencies
- `DependencyGraph` — graph representation

**API:**
```python
from core.plugins.dependency_resolver import DependencyResolver

resolver = DependencyResolver()

# Build dependency graph (fail-closed on cycles)
graph = resolver.build_dependency_graph()

# Resolve installation order (dependencies first)
order = resolver.resolve_installation_order(["plugin_a", "plugin_b"])

# Resolve uninstall order (dependents first)
uninstall_order = resolver.resolve_uninstall_order(["plugin_a"])

# Validate DAG
is_valid, message = resolver.validate_dag()
```

**Invariants:**
- No circular dependencies allowed (RuntimeError on detection)
- All transitive dependencies included
- Installation order respects constraints
- Uninstall order ensures no dangling dependencies

---

### 4. Auto-Remediation (`registry_sync.py`)

**Purpose:** Detect drifts and auto-remediate safe cases

**Key Classes:**
- `PluginRegistrySynchronizer` — main synchronizer
- `PluginDrift` — drift representation
- `PluginInstaller` — installation helper

**API:**
```python
from core.plugins.registry_sync import PluginRegistrySynchronizer

sync = PluginRegistrySynchronizer(instance_id="my_instance")

# Detect drifts
drifts = sync.detect_plugin_drift()
for drift in drifts:
    print(f"{drift.plugin_id}: {drift.drift_type} (severity: {drift.severity})")

# Remediate (auto-install missing, auto-update versions)
remediation_count, messages = sync.remediate(dry_run=False)
print(f"Remediated: {remediation_count} plugins")

# Get local/canonical plugins
local = sync.get_local_plugins()  # {plugin_id: version}
canonical = sync.get_canonical_plugins()  # {plugin_id: version}
```

**Drift Types:**
- `MISSING` — Plugin not installed (auto-install, safe)
- `VERSION_MISMATCH` — Version differs (auto-update, safe)
- `CHECKSUM_MISMATCH` — Hash mismatch (manual review, tampering)

**Remediation:**
```
Missing → auto-install (dependency-respecting)
Version mismatch → auto-update (with rollback on failure)
Tampering (hash mismatch) → alert only (manual review)
```

---

## Phase 4 Integration

### Monitoring Loop

Phase 3 is integrated into Phase 4's 30-second monitoring loop:

```python
# core/monitoring/drift_detector.py

class DriftDetectionService:
    def monitor_all_instances(self):
        for instance_id in instances:
            # Phase 1: deployment state drifts
            phase1_drifts = deployment_manager.detect_drift(instance_id)
            
            # Phase 3: plugin registry drifts (NEW)
            if self.plugin_sync_enabled:
                plugin_drifts = self._detect_plugin_drifts(instance_id)
                for drift in plugin_drifts:
                    # Alert via Slack/PagerDuty
                    self.alert_with_severity(drift.severity, drift.message, instance_id)
```

### Alert Routing

Plugin drifts are routed via:
- **Slack:** All severities (INFO, HIGH, CRITICAL)
- **PagerDuty:** CRITICAL only (tampering, missing, cascading failures)
- **Audit Trail:** All drifts logged to security event trail

---

## Usage Examples

### Example 1: Detect Plugin Drifts

```python
from core.plugins.registry_sync import PluginRegistrySynchronizer

# Create synchronizer
sync = PluginRegistrySynchronizer(instance_id="prod_instance_1")

# Detect drifts
drifts = sync.detect_plugin_drift()

for drift in drifts:
    if drift.drift_type == "MISSING":
        print(f"⚠️  Missing: {drift.plugin_id}")
    elif drift.drift_type == "VERSION_MISMATCH":
        print(f"⚠️  Version mismatch: {drift.plugin_id} (expected {drift.expected_version}, got {drift.actual_version})")
    elif drift.drift_type == "CHECKSUM_MISMATCH":
        print(f"🚨 Tampering detected: {drift.plugin_id}")
```

### Example 2: Auto-Remediate Drifts

```python
from core.plugins.registry_sync import PluginRegistrySynchronizer

sync = PluginRegistrySynchronizer(instance_id="prod_instance_1")

# Dry run: see what would be remediated
remediation_count, messages = sync.remediate(dry_run=True)
print(f"Would remediate: {remediation_count} plugins")

# Actual remediation
remediation_count, messages = sync.remediate(dry_run=False)
print(f"Remediated: {remediation_count} plugins")
for message in messages:
    print(f"  {message}")
```

### Example 3: Verify Plugin Integrity

```python
from core.plugins.hash_verification import PluginHashVerifier

verifier = PluginHashVerifier()

# Verify all plugins
results = verifier.verify_all_plugins()

# Get summary
summary = verifier.get_verification_summary(results)
print(f"Total: {summary['total_plugins']}")
print(f"Valid: {summary['valid']}")
print(f"Invalid: {summary['invalid']}")
print(f"Critical: {summary['by_severity']['CRITICAL']}")

# Check specific results
for result in results:
    if not result.is_valid:
        print(f"⚠️  {result.plugin_id}: {result.reason}")
```

### Example 4: Resolve Dependencies

```python
from core.plugins.dependency_resolver import DependencyResolver

resolver = DependencyResolver()

# Resolve installation order for a plugin
plugins_to_install = ["plugin_a", "plugin_b"]
order = resolver.resolve_installation_order(plugins_to_install)
print(f"Install in this order: {order}")

# Resolve uninstall order
plugins_to_uninstall = ["plugin_a"]
uninstall_order = resolver.resolve_uninstall_order(plugins_to_uninstall)
print(f"Uninstall in this order: {uninstall_order}")
```

---

## Test Suite

### Test File: `tests/plugins/test_phase3_registry_sync.py`

**Test Cases (8+):**

| Test | Module | Purpose |
|---|---|---|
| `test_canonical_manifest_write_and_load` | Manifest | Atomic write/load |
| `test_canonical_manifest_integrity_verification` | Manifest | Hash verification |
| `test_canonical_manifest_get_and_add_plugin` | Manifest | CRUD operations |
| `test_plugin_hash_verification_valid` | Hash | Valid plugin check |
| `test_plugin_hash_verification_tampering_detected` | Hash | Tampering detection |
| `test_plugin_hash_verification_missing_plugin` | Hash | Missing plugin detection |
| `test_plugin_hash_verification_version_mismatch` | Hash | Version mismatch detection |
| `test_dependency_resolver_linear_chain` | Resolver | Linear dependencies |
| `test_dependency_resolver_multiple_dependencies` | Resolver | Multiple dependencies |
| `test_dependency_resolver_circular_detection` | Resolver | Circular detection |
| `test_dependency_resolver_dag_validation` | Resolver | DAG validation |
| `test_detect_plugin_drift_missing` | Sync | Missing detection |
| `test_remediate_missing_plugins` | Sync | Auto-install |
| `test_get_local_and_canonical_plugins` | Sync | CRUD |
| `test_plugin_install` | Installer | Installation |
| `test_plugin_update_with_backup` | Installer | Update with rollback |
| `test_multi_instance_sync` | Integration | Multi-instance scenario |
| `test_audit_logging_integration` | Integration | Audit trail |

**Running Tests:**
```bash
cd /home/shumway/projects/CorvinOS
pytest tests/plugins/test_phase3_registry_sync.py -v
```

---

## Configuration

### Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `CORVIN_HOME` | CorvinOS home directory | `~/.corvin` |
| `PLUGIN_MANIFEST_URL` | Canonical manifest location | `~/.corvin/plugins/canonical_manifest.json` |

### Manifest File

Location: `~/.corvin/plugins/canonical_manifest.json`

Example:
```json
{
  "schema_version": "1.0",
  "plugins": [
    {
      "plugin_id": "memory_plugin",
      "version": "1.2.0",
      "checksum": "abc123def456...",
      "dependencies": [],
      "boot_layer": "bundled",
      "timestamp": "2026-09-26T10:30:00Z"
    }
  ],
  "timestamp": "2026-09-26T10:30:00Z",
  "manifest_hash": "xyz789..."
}
```

---

## Compliance & Audit

### GDPR Compliance

| Article | Requirement | Satisfied |
|---|---|---|
| Art. 30 | Processing Record | ✅ All operations logged |
| Art. 32 | Security | ✅ Hash verification, audit trail |
| Art. 5 | Accountability | ✅ Immutable audit events |

### Audit Trail

All operations are logged to the security audit trail:
- Plugin installed
- Plugin updated
- Plugin remediation attempted
- Drift detected

Example audit event:
```json
{
  "event_type": "plugin_remediation_attempted",
  "instance_id": "prod_instance_1",
  "plugin_id": "my_plugin",
  "action": "INSTALL",
  "success": true,
  "message": "Installed my_plugin@1.0.0",
  "timestamp": "2026-09-26T10:30:00Z"
}
```

---

## Troubleshooting

### Issue: Manifest Not Found

**Symptom:** "Manifest not found, returning empty manifest"

**Resolution:**
1. Ensure `~/.corvin/plugins/` directory exists
2. Run initial manifest creation
3. Check `PLUGIN_MANIFEST_URL` environment variable

### Issue: Hash Mismatch (Tampering Detected)

**Symptom:** "Hash mismatch (tampering detected)" alert

**Resolution:**
1. **Verify audit trail:** Check who modified the plugin
2. **Restore from backup:** Re-install plugin from canonical manifest
3. **Investigate:** Determine if tampering was intentional or accidental

### Issue: Circular Dependency Error

**Symptom:** "Circular dependency detected in plugin graph"

**Resolution:**
1. Check manifest for circular dependencies
2. Update manifest to break cycle
3. Re-run dependency validation

---

## Next Steps

### Phase 3.1 (This Phase) ✅ COMPLETE
- Canonical manifest management
- Hash verification
- Dependency resolution
- Auto-remediation (basic)
- Phase 4 integration
- Test suite (8+ tests)

### Phase 3.2 (Future)
- Enhance auto-remediation (rollout strategies)
- Dashboard visualization (Vibe integration)
- Performance optimization (caching, parallel verification)

### Phase 3.3 (Future)
- Marketplace integration
- Plugin versioning policy
- Canary rollout strategies

---

## References

- **ADR:** [ADR-2067 — Plugin Registry Consistency](../Corvin-ADR/decisions/ADR-2067-plugin-registry-consistency-phase3.md)
- **Phase 1:** Deployment State Sync (ADR-0407)
- **Phase 2:** Config Management (ADR-2066)
- **Phase 4:** Drift Detection & Alerting (ADR-0409)
- **Audit:** ADR-0232/0233 — Boot Tripwire & Hash-Chain Integrity

---

**Document Status:** COMPLETE (2026-09-26)  
**Implementation Status:** COMPLETE (all modules, tests passing)  
**ADR Status:** PROPOSED (ready for review)
