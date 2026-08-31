# Plugin E2E Verification Framework — Developer Guide

## Overview

Das **Plugin E2E Verification Framework** (ADR-0464) bietet eine umfassende Vier-Ebenen-Testarchitektur für vollständige Plugin-Verifizierung:

```
TIER 4: SYSTEM-HEALTH     (Full platform: cross-tenant, config-drift, load-order, hot-reload)
   ↓
TIER 3: FEATURE-E2E        (Plugin workflows: install/uninstall, hooks, conflicts, state-corruption)
   ↓
TIER 2: INTEGRATION        (Subsystem interactions: registry, process-manager, health, CLI)
   ↓
TIER 1: UNIT               (Individual components: manifest, schema, API compat, context)
```

## Getting Started

### 1. Plugin Discovery

Entdecke alle Plugins automatisch:

```bash
cd /home/shumway/projects/CorvinOS
python tests/e2e/plugin_verification/plugin_scanner.py
```

**Output:** `tests/e2e/plugin_verification/test_inventory.json`
- Enthält: plugin_id, version, entry_point, dependencies, boot_layer, origin
- Zeigt: Test-Anforderungen pro Plugin, Coverage %, Lücken

### 2. Test Template Generation

Generiere Test-Skeletons für neue Plugins:

```bash
python tests/e2e/plugin_verification/test_generator.py
```

**Output:** `tests/e2e/plugin_verification/feature-e2e/generated/[plugin-name]/`
- `test_[plugin]_init_lifecycle.py` (Initialization & lifecycle)
- `test_[plugin]_features.py` (Core functionality)
- `test_[plugin]_hooks.py` (Hook behavior & conflicts)
- `test_[plugin]_integration.py` (System integration)
- `test_[plugin]_cleanup.py` (Unload & cleanup)

Entwickler füllen die Test-Bodies aus (ersetzen `pytest.skip` durch echte Tests).

### 3. Test Execution

**Quick Gate** (~5-15 min): Unit + Integration tests
```bash
pytest tests/unit/plugins/ tests/integration/plugins/ \
  -m "plugin_unit or plugin_integration" -v
```

**Feature Gate** (~20 min): Feature-level E2E
```bash
pytest tests/e2e/plugin_verification/feature-e2e/ \
  -m "plugin_feature_e2e" -v
```

**Full System Gate** (~30 min): System-health (nur auf main merge)
```bash
pytest tests/e2e/plugin_verification/system-health/ \
  -m "plugin_system_health" -v
```

**All Plugin Tests:**
```bash
pytest tests/ -m "plugin_unit or plugin_integration or plugin_feature_e2e or plugin_system_health" -v
```

## Test Checklist Per Plugin

Jedes Plugin muss folgendes bestehen:

### Mandatory (ALLE Plugins)

- **TIER-1 Unit:**
  - `test_manifest_parsing`: Manifest wird korrekt geparst
  - `test_validation_schemas`: Manifest hat alle erforderlichen Felder
  - `test_context_construction`: PluginContext wird korrekt gebaut

- **TIER-2 Integration:**
  - `test_registry_integration`: Plugin registriert sich korrekt
  - `test_manifest_validation_integration`: Manifest-Validierung in System-Kontext
  - `test_dependency_resolution`: Abhängigkeiten werden aufgelöst

- **TIER-3 Feature-E2E:**
  - `test_init_lifecycle`: on_load hook wird aufgerufen
  - `test_features`: Core-Features funktionieren
  - `test_hooks`: Hook-Registrierung und Ausführung
  - `test_integration`: Interop mit anderen Plugins
  - `test_cleanup`: on_unload hook, Ressourcen freigeben

### Risk-Based (HIGH-RISK Plugins: compliance, core)

```python
if plugin.boot_layer in ["compliance", "core"]:
    + test_load_order        # Dependencies vor Dependent
    + test_hot_reload        # State bleibt konsistent nach Reload
    + test_fault_injection   # Plugin-Crash wird isoliert
    + test_config_drift_detection  # Config-Änderungen erkannt
```

### Risk-Based (COMMUNITY Plugins: origin=community)

```python
if plugin.origin == "community":
    + test_sandbox           # Ressourcen-Sandbox funktioniert
    + test_resource_limits   # CPU/Memory/Disk-Limits durchgesetzt
    + test_cross_tenant_isolation  # Tenant A sieht nicht Tenant B's Plugins
```

## Critical Error Classes (Early Detection)

Framework erkennt automatisch folgende Fehlerklassen:

| Fehlerklasse | Erkennung | TIER | Marker |
|---|---|---|---|
| **Plugin-Init-Fehler** | Manifest, Schema, API-Kompatibilität | 1 | `@pytest.mark.plugin_validation` |
| **Hook-Konflikte** | Mehrere Plugins auf gleichen Hook | 3 | `@pytest.mark.plugin_conflict` |
| **State-Corruption** | Partial state, Zombie-Prozesse | 3 | `@pytest.mark.plugin_crash` |
| **Dependency-Konflikte** | Version-Mismatch, zirkulär | 2 | `@pytest.mark.plugin_dependency` |
| **Cross-Tenant-Leaks** | Tenant A sieht Tenant B's Daten | 4 | `@pytest.mark.plugin_isolation` |
| **Config-Drift** | Checksum-Mismatch, Schema-Fehler | 4 | `@pytest.mark.plugin_drift` |

## Test Fixtures (conftest.py)

### Isolation

```python
def test_something(isolated_plugin_env):
    """Fresh CORVIN_HOME, empty registry, redirected audit chain"""
    corvin_home = isolated_plugin_env["corvin_home"]
    registry = isolated_plugin_env["registry"]
```

### Conflict Detection

```python
def test_hook_conflicts(conflict_detector):
    """Detect hook conflicts, API mismatches"""
    conflict_detector.register_hook("on_task_start", "plugin-1")
    conflict_detector.register_hook("on_task_start", "plugin-2")
    conflict_detector.check_exclusive_hooks(["on_task_start"])
    conflict_detector.assert_no_conflicts()  # Raises if conflict found
```

### Config Drift

```python
def test_config_drift(config_drift_monitor):
    """Track config changes, detect drift"""
    config_drift_monitor.snapshot_config(Path("config.json"))
    # ... test modifies config ...
    if config_drift_monitor.detect_drift(Path("config.json")):
        print("Config was modified!")
    config_drift_monitor.assert_no_drift()
```

### Cross-Tenant Isolation

```python
def test_isolation(cross_tenant_validator):
    """Verify no cross-tenant data leaks"""
    cross_tenant_validator.record_read("_default", "plugin-a")
    cross_tenant_validator.record_read("_tenant2", "plugin-b")
    cross_tenant_validator.assert_no_cross_tenant_leaks()
```

## Pytest Markers

Alle Tests sind mit relevanten Markern annotiert:

```bash
# TIER-level markers
@pytest.mark.plugin_unit              # TIER-1
@pytest.mark.plugin_integration       # TIER-2
@pytest.mark.plugin_feature_e2e       # TIER-3
@pytest.mark.plugin_system_health     # TIER-4

# Error-class markers
@pytest.mark.plugin_validation        # Init-Fehler
@pytest.mark.plugin_conflict          # Hook-Konflikte
@pytest.mark.plugin_isolation         # Cross-Tenant-Leaks
@pytest.mark.plugin_dependency        # Dependency-Konflikte
@pytest.mark.plugin_drift             # Config-Drift
@pytest.mark.plugin_crash             # Crash-Recovery
```

Beispiel:

```python
@pytest.mark.plugin_feature_e2e
@pytest.mark.plugin_conflict
def test_hook_conflict_detection(conflict_detector):
    """TIER-3, Error-Class: Hook Conflicts"""
    pass
```

## CI/CD Integration

### GitHub Actions Gates

```yaml
# .github/workflows/test.yml

# GATE 1: Validation (~5 min) — run on every PR
- name: Plugin Validation Gate
  run: pytest -m "plugin_unit or plugin_validation" --maxfail=1

# GATE 2: Integration (~15 min) — run on every PR
- name: Plugin Integration Gate
  run: pytest -m "plugin_integration or plugin_isolation"

# GATE 3: Feature E2E (~20 min) — run on every PR
- name: Plugin Feature E2E Gate
  run: pytest -m "plugin_feature_e2e"

# GATE 4: System Health (~30 min) — run only on main merge
- name: Plugin System Health Gate
  if: github.ref == 'refs/heads/main'
  run: pytest -m "plugin_system_health"
```

**Gates sind fail-fast:** Wenn GATE 1 fehlschlägt, stopp die späteren Gates.

## Workflow für Neue Plugins

1. **Plugin erstellen** (Code + manifest.json)

2. **Inventory aktualisieren:**
   ```bash
   python tests/e2e/plugin_verification/plugin_scanner.py
   ```

3. **Test-Skeletons generieren:**
   ```bash
   python tests/e2e/plugin_verification/test_generator.py
   ```

4. **Test-Bodies implementieren:**
   - Öffne `tests/e2e/plugin_verification/feature-e2e/generated/[plugin-name]/`
   - Ersetze `pytest.skip()` durch echte Tests
   - Folge der Checklist für dein Plugin (mandatory + risk-based)

5. **Lokal testen:**
   ```bash
   pytest tests/e2e/plugin_verification/feature-e2e/test_[plugin]_*.py -v
   ```

6. **In CI/CD mergen:**
   - Alle Gates müssen grün sein
   - Pipeline validiert automatisch Test-Coverage

## Metrics & Dashboard

Laufe regelmäßig den Scanner:

```bash
# Täglicher Cron-Job
python tests/e2e/plugin_verification/plugin_scanner.py
```

**test_inventory.json** zeigt:
- Plugin-Count
- Test-Coverage % pro Plugin
- Fehlende Test-Kategorien
- Status: ✓ COMPLETE oder ⚠ INCOMPLETE

Beispiel-Output:
```json
{
  "plugin_count": 12,
  "total_coverage": 92.3,
  "plugins": {
    "console_plugin": {
      "plugin_id": "console_plugin",
      "boot_layer": "bundled",
      "test_gaps": [],
      "status": "✓ COMPLETE"
    },
    "marketplace_plugin": {
      "plugin_id": "marketplace_plugin",
      "boot_layer": "bundled",
      "test_gaps": ["test_hot_reload", "test_fault_injection"],
      "status": "⚠ INCOMPLETE"
    }
  }
}
```

## Troubleshooting

### Test schlägt fehl: "Plugin not found"

→ Lade die Plugin-Inventory neu:
```bash
python tests/e2e/plugin_verification/plugin_scanner.py
```

### Test ist flaky (manchmal grün, manchmal rot)

→ Verifiziere, dass `isolated_plugin_env` Fixture verwendet wird:
```python
def test_something(isolated_plugin_env):  # ← Required!
    registry = isolated_plugin_env["registry"]
```

### Config-Drift erkannt, aber Test sollte erfolgreich sein

→ Snapshot/Reset Config vor Test:
```python
def test_something(config_drift_monitor):
    config = Path("config.json")
    config_drift_monitor.snapshot_config(config)
    # ... test modifies config ...
    # Only actual unexpected drift should raise
```

## References

- **ADR:** `/home/shumway/projects/Corvin-ADR/decisions/ADR-0464-plugin-e2e-verification-framework.md`
- **Fixture Code:** `tests/e2e/plugin_verification/conftest.py`
- **Scanner:** `tests/e2e/plugin_verification/plugin_scanner.py`
- **Test Generator:** `tests/e2e/plugin_verification/test_generator.py`
- **Checklist:** `tests/e2e/plugin_verification/test_checklist.py`
