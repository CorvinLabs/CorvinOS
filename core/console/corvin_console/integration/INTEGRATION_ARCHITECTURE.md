# Console Integration Layer Architecture

**Status:** 🟢 IMPLEMENTED (2026-09-19)  
**Scope:** Console UI ↔ Registry API ↔ Tenant-Skill-Architecture ↔ Plugin-Builder  
**Load-Bearing Rules:** ADR-0511, ADR-0405, ADR-0007, ADR-0232/0233

---

## Overview

The integration layer serves as the **central data flow controller** connecting four subsystems:

1. **Console UI** (`core/console/corvin_console/routes/`)
   - FastAPI endpoints for user interactions
   - Session & CSRF validation
   - Audit trail generation

2. **Registry API** (SkillForge, Plugin Registry)
   - `corvin_operator/skill-forge/` (skill lifecycle)
   - `core/plugins/corvin_plugins/` (plugin lifecycle)
   - Manifests & configuration

3. **Tenant-Skill-Architecture** (Multi-tenant data persistence)
   - `~/.corvin/tenants/<tenant_id>/` (tenant home)
   - `registry/` subdirectory (plugin/skill records)
   - Append-only audit trail per tenant

4. **Plugin-Builder** (`core/plugins/plugin_builder/`)
   - Interview phase (ideation)
   - Generation phase (source code)
   - Deployment phase (via integration layer)

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Console UI (Browser)                       │
└───────────────────┬───────────────────────────────────────────────┬─┘
                    │                                               │
           POST /api/v1/integration/plugins/{id}/install   GET /api/v1/integration/skills
                    │                                               │
         ┌──────────▼───────────────────────────────────────────────▼──┐
         │        FastAPI Routes (integration_api.py)                  │
         │  install_plugin()  promote_skill()  record_grade()          │
         │  deploy_generated_plugin()  validate_data_flow()            │
         └──────────┬───────────────────────────────────────────────┬──┘
                    │                                               │
         ┌──────────▼───────────────────────────────────────────────▼──────┐
         │      RegistryIntegrationBridge (Main Coordinator)              │
         │  - Tenant isolation enforcement                                │
         │  - Data flow event recording                                   │
         │  - Cross-subsystem routing                                     │
         │  - Error handling & rollback                                   │
         └──┬───────────────┬──────────────────┬────────────────┬─────┬──┘
            │               │                  │                │     │
    ┌───────▼────┐  ┌──────▼──────┐  ┌────────▼────────┐  ┌───▼──┐  │
    │  Tenant    │  │   Skill     │  │ PluginBuilder  │  │Data  │  │
    │  Registry  │  │  Registry   │  │   Adapter      │  │Flow  │  │
    │  Adapter   │  │  Adapter    │  │                │  │Valid-│  │
    └───────┬────┘  └──────┬──────┘  └────────┬────────┘  └───┬──┘  │
            │              │                   │               │     │
    ┌───────▼──────────────▼───────────────────▼───────────────▼─────▼──┐
    │                Tenant-Scoped Storage (GDPR-Safe)                   │
    │  ~/.corvin/tenants/<tenant_id>/registry/                           │
    │  ├── installed_plugins.jsonl (append-only)                         │
    │  ├── installed_skills.jsonl (append-only)                          │
    │  └── config.json (registry configuration)                          │
    └──────────────────────────────────────────────────────────────────┘
            │              │                   │
    ┌───────▼──────────────▼───────────────────▼──────────────────────┐
    │              External Subsystems (Read-Only or Via Bridge)       │
    │  ├── SkillForge Registry (~/.corvin/tenants/<tenant>/skill-forge)│
    │  ├── Plugin Registry (core/plugins/corvin_plugins/)              │
    │  └── Plugin-Builder (core/plugins/plugin_builder/)               │
    └────────────────────────────────────────────────────────────────┘
```

---

## Key Design Principles

### 1. Fail-Closed (ADR-0232/0233 Boot Tripwire)

Every operation defaults to denying access unless explicitly validated:

```python
# ✅ GOOD: Validate tenant_id first, fail if invalid
validate_tenant_id(tenant_id)  # Raises ValueError if invalid
adapter = TenantRegistryAdapter(tenant_id)  # Now safe

# ❌ BAD: Try to use tenant_id without validation
adapter = TenantRegistryAdapter(tenant_id)  # May allow "../../../etc/passwd"
```

### 2. Tenant Isolation (GDPR Art. 5, 6, 32)

All data access is scoped by `tenant_id`. Cross-tenant leakage is prevented:

```python
# Every operation validates tenant match
if record.tenant_id != self.tenant_id:
    raise ValueError("Cross-tenant operation blocked")

# All queries filtered by tenant_id (implicit in path construction)
path = tenant_paths.tenant_home(tenant_id) / "registry"
```

### 3. Append-Only Audit Trail

Every state change is immutable and hash-linked:

```python
# Installation records are frozen (dataclass(frozen=True))
@dataclass(frozen=True)
class RegistryInstallRecord:
    ...  # No modification after creation

# Written to append-only JSONL file
with open(installed_plugins_file, "a") as f:
    f.write(json.dumps(record.to_dict()) + "\n")  # Append only
```

### 4. Immutable Data Models

All data models are frozen dataclasses to prevent accidental mutation:

```python
@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    # ... no __setattr__ allowed

manifest.version = "2.0"  # ❌ AttributeError: frozen dataclass
```

### 5. E2E Wiring Proof

Every integration path is testable end-to-end through real API endpoints:

```python
# ✅ Test goes through real HTTP request
@router.post("/api/v1/integration/plugins/{id}/install")
def install_plugin(...):
    bridge = RegistryIntegrationBridge(tenant_id)
    success, msg = bridge.install_plugin(plugin_id)
    return ...

# ✅ Test uses the real endpoint
def test_install_plugin_e2e():
    response = client.post("/api/v1/integration/plugins/test/install")
    assert response.status_code == 200
```

---

## Data Flow Walkthrough

### Plugin Installation Flow

```
1. Console UI (Browser)
   └─ Click "Install Plugin" button
      └─ POST /api/v1/integration/plugins/plugin-x/install

2. API Route (integration_api.py::install_plugin)
   └─ Validate request
   └─ Create RegistryIntegrationBridge(tenant_id)
   └─ Call bridge.install_plugin(plugin_id)
   └─ Emit data flow event: "plugin_install_requested"

3. RegistryIntegrationBridge
   └─ Validate plugin not already installed
   └─ Create RegistryInstallRecord(...)
   └─ Call adapter.record_installation(record)
   └─ Emit data flow event: "plugin_install_completed"
   └─ Validate data flow integrity
   └─ Return success response

4. TenantRegistryAdapter
   └─ Construct tenant-scoped path
   └─ Open ~/.corvin/tenants/<tenant_id>/registry/installed_plugins.jsonl
   └─ Append RegistryInstallRecord as JSON line
   └─ Ensure file mode is 0o600 (GDPR compliance)

5. Return to Console UI
   └─ Display success message
   └─ Refresh plugin list
   └─ User sees plugin as "installed"
```

### Skill Promotion Flow

```
1. Skill-Creator (skill_creator_api.py)
   └─ POST /api/v1/integration/skills/promote
   └─ Send skill name, body, description

2. API Route (integration_api.py::promote_skill)
   └─ Validate scope enum
   └─ Create RegistryIntegrationBridge(tenant_id)
   └─ Call bridge.promote_skill(...)
   └─ Emit data flow event: "skill_promote_requested"

3. RegistryIntegrationBridge
   └─ Call skill_adapter.promote_skill_to_registry(...)
   └─ Emit data flow event: "skill_promote_completed"

4. SkillRegistryAdapter
   └─ Import registry_bridge (bridges into SkillForge)
   └─ Call registry.create(name, body_md, scope)
   └─ Get back SkillManifest with skill_id
   └─ Create RegistryInstallRecord(target_type="skill")
   └─ Call adapter.record_skill_installation(record)
   └─ Record skill grade (bootstrap: 0.3)

5. TenantRegistryAdapter
   └─ Append to ~/.corvin/tenants/<tenant_id>/registry/installed_skills.jsonl
   └─ Ensure immutability & tenant isolation

6. Return to Skill-Creator
   └─ Display success with skill_id
   └─ Skill is now available for injection
```

### Plugin-Builder Deployment Flow

```
1. Plugin-Builder (plugin_builder/turn.py)
   └─ Generate plugin artifacts
   └─ Create plugin directory structure
   └─ Call RegistryIntegrationBridge.deploy_plugin_from_builder(path)

2. RegistryIntegrationBridge
   └─ Call plugin_builder_adapter.deploy_plugin(path, tenant_id)
   └─ Emit data flow event: "plugin_deploy_initiated"

3. PluginBuilderAdapter
   └─ Call validate_generated_plugin(path)
   └─ Check plugin.json exists and is valid
   └─ Check src/, tests/, README.md present
   └─ If valid, proceed to deployment
   └─ If invalid, return errors (fail-closed)

4. TenantRegistryAdapter
   └─ Load plugin.json manifest
   └─ Create RegistryInstallRecord with manifest metadata
   └─ Call record_installation(record)
   └─ Append to installed_plugins.jsonl

5. Return to Plugin-Builder
   └─ Display deployment status
   └─ Plugin is now available to tenant
```

---

## API Endpoints

### Plugin Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/integration/plugins` | List installed plugins |
| POST | `/api/v1/integration/plugins/{id}/install` | Install a plugin |
| POST | `/api/v1/integration/plugins/{id}/uninstall` | Uninstall a plugin |
| PATCH | `/api/v1/integration/plugins/{id}/enable` | Enable a plugin |
| PATCH | `/api/v1/integration/plugins/{id}/disable` | Disable a plugin |

### Skill Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/integration/skills` | List installed skills |
| POST | `/api/v1/integration/skills/promote` | Promote a skill to registry |
| POST | `/api/v1/integration/skills/{id}/grade` | Record skill feedback grade |

### Registry Status & Validation

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/integration/status` | Get registry status |
| GET | `/api/v1/integration/stats` | Get detailed statistics |
| GET | `/api/v1/integration/data-flow/events` | Get data flow events (dev) |
| POST | `/api/v1/integration/data-flow/validate` | Validate data flow integrity |

### Plugin-Builder Deployment

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/integration/deploy-plugin` | Deploy generated plugin |

---

## Data Validation

The integration layer performs **multi-level validation**:

### Level 1: Request Validation
```python
# FastAPI Pydantic models
class PluginInstallRequest(BaseModel):
    plugin_id: str  # Required
    source_tier: str  # Enum validation
```

### Level 2: Tenant Isolation
```python
# Explicit tenant_id validation
validate_tenant_id(tenant_id)  # Raises ValueError if invalid

# Path construction is scoped
path = tenant_paths.tenant_home(tenant_id) / "registry"
```

### Level 3: Data Flow Validation
```python
# Hash-chain integrity
for i in range(1, len(events)):
    assert events[i].prev_hash == events[i-1].payload_hash

# Status transitions
assert curr_status in valid_transitions.get(prev_status)

# Tenant consistency
for event in events:
    assert event.tenant_id == bridge.tenant_id
```

### Level 4: Schema Validation
```python
# Plugin manifest validation
required_fields = ["plugin_id", "name", "version", "tier", "category"]
for field in required_fields:
    assert field in manifest
```

---

## Error Handling

All errors are **fail-closed** (deny by default):

```python
# ✅ Invalid tenant_id → Exception (not silent fallback)
try:
    validate_tenant_id(tenant_id)
except ValueError:
    raise HTTPException(status_code=400, detail="Invalid tenant")

# ✅ Cross-tenant operation → Exception (not silent filter)
if record.tenant_id != self.tenant_id:
    raise ValueError("Cross-tenant operation blocked")

# ✅ Missing manifest → Deployment fails (not silent skip)
if not manifest_file.exists():
    errors.append("plugin.json not found")
    return False, errors
```

---

## Audit Trail

Every operation is logged to the audit chain:

```python
# 1. Data flow event recorded
self._record_data_flow_event(
    event_type="plugin_install_requested",
    source_component="console_ui",
    target_component="registry_integration",
    status="initiated",
)

# 2. Installation record appended (immutable)
with open(installed_plugins_file, "a") as f:
    f.write(json.dumps(record.to_dict()) + "\n")

# 3. Console audit event recorded
console_audit.action_performed(
    tenant_id=tenant_id,
    action="plugin.install",
    target_kind="plugin",
    target_id=plugin_id,
)
```

---

## Testing Strategy

### E2E Wiring Tests
```python
def test_plugin_install_success(registry_bridge):
    """Test that plugin installation flows end-to-end."""
    success, message = registry_bridge.install_plugin("test-plugin")
    assert success is True
    installed = registry_bridge.list_installed_plugins()
    assert any(p["target_id"] == "test-plugin" for p in installed)
```

### Tenant Isolation Tests
```python
def test_cross_tenant_isolation():
    """Test that tenants cannot see each other's data."""
    bridge1.install_plugin("plugin-a")
    bridge2.install_plugin("plugin-b")
    installed1 = bridge1.list_installed_plugins()
    assert not any(p["target_id"] == "plugin-b" for p in installed1)
```

### Data Flow Validation Tests
```python
def test_data_flow_tenant_isolation(registry_bridge):
    """Test that all events are tenant-scoped."""
    registry_bridge.install_plugin("plugin1")
    is_valid, errors = registry_bridge.validate_data_flow()
    assert is_valid is True
```

### Immutability Tests
```python
def test_installation_records_immutable():
    """Test that RegistryInstallRecord is frozen."""
    record = RegistryInstallRecord(...)
    with pytest.raises(AttributeError):
        record.deployment_status = DeploymentStatus.FAILED
```

---

## Integration Checklist (for new features)

When adding a new integration path (e.g., new plugin type):

- [ ] **Dialectical Reasoning:** Run `/dialectical-reasoning` to surface design choices
- [ ] **Data Model:** Create immutable dataclass with `@dataclass(frozen=True)`
- [ ] **Tenant Isolation:** Every operation validates tenant_id, paths are scoped
- [ ] **Audit Trail:** Every state change logged to data flow events
- [ ] **Append-Only:** No modifications to existing records, only appends
- [ ] **Error Handling:** Fail-closed on validation errors
- [ ] **E2E Test:** Write test that goes through real API endpoint
- [ ] **Data Flow Validation:** Test hash chain, status transitions, tenant isolation
- [ ] **ADR:** Document the design choice in Corvin-ADR (if structural)
- [ ] **Load-Bearing Check:** Ensure no bypass of validation, tenant isolation, or audit

---

## Must NOT Do

❌ **Don't bypass tenant_id validation:** Every operation must validate tenant_id  
❌ **Don't modify existing records:** Only append, never update/delete  
❌ **Don't leak PII to logs:** Use only non-identifying data in audit events  
❌ **Don't fail-open on validation:** Invalid operations → exception, not silent fallback  
❌ **Don't commit to tenant registry without audit:** Data flow event must precede storage  
❌ **Don't assume paths are safe:** Always use tenant_paths.* helpers  
❌ **Don't add feature flags to disable isolation:** Tenant isolation is non-negotiable  

---

## References

- **ADR-0511:** Marketplace architecture (plugin discovery, installation)
- **ADR-0405:** Skill-Creator → SkillForge registry bridge
- **ADR-0007:** Multi-tenant axis (tenant scoping rules)
- **ADR-0232/0233:** Audit-first design, boot tripwire
- **ADR-0314:** Learning infrastructure (feedback loop integration)
- **ADR-0532–0535:** OS-Skills architecture (Skills as control plane)
- **GDPR Art. 5, 6, 32:** Integrity, legality, security (implemented via tenant isolation + audit)

---

## Related Files

- `core/console/corvin_console/integration/registry_integration.py` — Main module
- `core/console/corvin_console/routes/integration_api.py` — FastAPI endpoints
- `tests/integration/test_registry_integration_e2e.py` — Comprehensive tests
- `core/paths/tenant.py` — Tenant path resolution (GDPR-safe)
- `corvin_operator/skill_creator/registry_bridge.py` — SkillForge integration bridge
- `core/plugins/plugin_builder/turn.py` — Plugin builder integration point
