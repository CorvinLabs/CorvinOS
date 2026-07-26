# Phase 1 Implementation Plan: Audit + Auth Pluginification — SUPERSEDED

**Status:** **Superseded** by [`../implementation/PLUGIN_SYSTEM_IMPLEMENTATION_PLAN.md`](../implementation/PLUGIN_SYSTEM_IMPLEMENTATION_PLAN.md)
and [ADR-0233](../../../Corvin-ADR/decisions/0233-plugin-system-consolidation.md) —
same objections as the sprint variant: a 3–4 engineer staffing model, and a Phase-1
objective ("Extract Audit logging from L16", "Extract User Management from L18-21") that
conflicts with ADR-0232's mandatory core. Backends are **additive**: core keeps writing
its own hash-chained `audit.jsonl`, a backend receives a copy, and a boot tripwire fails
closed if the core writer is unreachable. A `user_backend` returning `None` or raising
means deny, never guest.

**Retained for:** the `AuditBackend` / `UserBackend` protocol sketches, which carried
over (minus the LDAP/OIDC scaffolds) into Phase 1 of the replacement plan.

## Original content (historical)

**Date:** 2026-07-26
**Phase:** 1 (Critical Path: Audit + User Management)
**Duration:** 3-4 months (12-16 weeks)
**Team Size:** 3-4 engineers

---

## Phase 1 Objectives

✅ Extract Audit logging from L16 into pluggable `AuditBackendPlugin`  
✅ Extract User Management from L18-21 into pluggable `UserBackendPlugin`  
✅ Implement circuit breaker for audit failures (core continues)  
✅ Enable multi-auth support (LDAP, OIDC, local, guest)  
✅ Per-tenant audit chains (true multi-tenancy)  
✅ Maintain 100% backwards compatibility  
✅ Ship with 56+ E2E tests  

---

## Week 1-3: Protocol Design & Templates

### Week 1: AuditBackend Protocol Design

**Owner:** Engineer A  
**Tasks:**
1. Read `core/plugins/corvin_plugins/protocol.py` (understand structure)
2. Design `AuditBackend` protocol:
   ```python
   @runtime_checkable
   class AuditBackend(Protocol):
       """Pluggable audit trail interface (L16)."""
       
       def log_event(
           self,
           event_type: str,
           details: dict,
           *,
           tenant_id: str = "_default",
           user_id: str | None = None,
           hash_chain: str | None = None,
       ) -> None:
           """Log audit event (fire-and-forget or queued)."""
           ...
       
       def verify_chain(self) -> bool:
           """Verify hash-chain integrity."""
           ...
       
       def enforce_retention(self, max_age_days: int) -> dict:
           """Delete events older than X days. Returns {'deleted': count}."""
           ...
   ```

3. Add to `KNOWN_PLUGIN_TYPES`
4. Write protocol tests (5-7 tests):
   - ✅ Valid audit log
   - ✅ Chain verification
   - ✅ Retention enforcement
   - ✅ Error handling (what if log_event fails?)
   - ✅ Multiple events in sequence

**Deliverable:** `protocol.py` updated + 7 passing tests

---

### Week 1-2: UserBackend Protocol Design

**Owner:** Engineer B  
**Tasks:**
1. Design `UserBackend` protocol:
   ```python
   @runtime_checkable
   class UserBackend(Protocol):
       """Pluggable user authentication & management (L18-21)."""
       
       async def authenticate(
           self,
           credentials: dict,
       ) -> dict | None:
           """Authenticate user. Returns {'user_id': str, 'roles': [str]} or None."""
           ...
       
       async def get_user(self, user_id: str) -> dict | None:
           """Get user details by ID."""
           ...
       
       async def enforce_quota(
           self,
           user_id: str,
           resource: str,  # "tokens", "compute_minutes", "api_calls"
       ) -> None:
           """Check user limits. Raises QuotaExceededError if over limit."""
           ...
       
       async def list_users(self) -> list[dict]:
           """Return all users (for admin UI)."""
           ...
   ```

2. Add to `KNOWN_PLUGIN_TYPES`
3. Write protocol tests (8-10 tests):
   - ✅ Successful auth
   - ✅ Failed auth (bad password)
   - ✅ User not found
   - ✅ Quota enforcement
   - ✅ List users
   - ✅ Async error handling

**Deliverable:** `protocol.py` updated + 10 passing tests

---

### Week 2: Plugin Templates

**Owner:** Engineer C  
**Tasks:**
1. Create `core/plugins/templates/audit_backend_plugin.py`
   ```python
   from corvin_plugins.protocol import AuditBackend
   
   class ExampleAuditBackendPlugin(AuditBackend):
       """Template for audit backend plugins."""
       
       def log_event(self, event_type: str, details: dict, **kwargs) -> None:
           print(f"[AUDIT] {event_type}: {details}")
       
       def verify_chain(self) -> bool:
           return True  # Placeholder
       
       def enforce_retention(self, max_age_days: int) -> dict:
           return {'deleted': 0}  # Placeholder
   ```

2. Create `core/plugins/templates/user_backend_plugin.py`
   ```python
   from corvin_plugins.protocol import UserBackend
   
   class ExampleUserBackendPlugin(UserBackend):
       """Template for user backend plugins."""
       
       async def authenticate(self, credentials: dict) -> dict | None:
           # Check credentials in database/LDAP/etc
           return None  # Placeholder
       
       async def get_user(self, user_id: str) -> dict | None:
           return None  # Placeholder
       
       async def enforce_quota(self, user_id: str, resource: str) -> None:
           pass  # Placeholder
       
       async def list_users(self) -> list[dict]:
           return []  # Placeholder
   ```

3. Add templates to test suite

**Deliverable:** 2 templates + documentation

---

## Week 4-8: Built-in Plugin Implementations

### Week 4: Default Audit Backend

**Owner:** Engineer A  
**Tasks:**
1. Extract audit logic from `core/audit/` (identify all files):
   - `event_logger.py` → `log_event()` method
   - `hash_chain.py` → `verify_chain()` method
   - `retention.py` → `enforce_retention()` method

2. Create `core/plugins/corvin_plugins/providers/audit_backend.py`:
   ```python
   from corvin_plugins.protocol import AuditBackend, PluginContext
   
   class DefaultAuditBackendPlugin(AuditBackend):
       """File-based audit backend (production default)."""
       
       plugin_id = "audit-backend-default"
       plugin_type = "audit_backend"
       version = "1.0.0"
       
       def __init__(self):
           self.audit_file = None
           self.hash_chain = None
       
       def on_load(self, ctx: PluginContext) -> None:
           """Initialize audit subsystem."""
           audit_path = ctx.corvin_home / "audit.jsonl"
           self.audit_file = open(audit_path, 'a')
           # Load hash chain
       
       def log_event(self, event_type: str, details: dict, **kwargs) -> None:
           """Log to audit.jsonl with hash-chain."""
           # Extract logic from core/audit/event_logger.py
           event = {
               "timestamp": datetime.utcnow().isoformat(),
               "event_type": event_type,
               "details": details,
               "hash_chain": self.compute_hash_chain(),
           }
           self.audit_file.write(json.dumps(event) + "\n")
           self.audit_file.flush()
       
       def verify_chain(self) -> bool:
           """Verify hash-chain integrity."""
           # Extract logic from core/audit/hash_chain.py
           ...
       
       def enforce_retention(self, max_age_days: int) -> dict:
           """Delete audit entries older than X days (GDPR)."""
           # Extract logic from core/audit/retention.py
           ...
   ```

3. Write 8-10 integration tests:
   - ✅ Log event → file
   - ✅ Hash chain computes correctly
   - ✅ Retention deletes old events
   - ✅ Plugin disable → graceful queue
   - ✅ Plugin re-enable → replay queued events

**Deliverable:** Built-in audit plugin + tests (8-10 passing)

---

### Week 5-6: User Backends

**Owner:** Engineer B + C  

#### Subweek 5a: Local Auth Backend
**Tasks:**
1. Extract logic from `core/auth/` (identify all files)
2. Create `core/plugins/corvin_plugins/providers/user_backend_local.py`:
   ```python
   class LocalUserBackendPlugin(UserBackend):
       """Local password-file authentication (default)."""
       
       plugin_id = "user-backend-local"
       plugin_type = "user_backend"
       version = "1.0.0"
       
       async def authenticate(self, credentials: dict) -> dict | None:
           # Check username + password hash from local file
           # Return {'user_id': '...', 'roles': [...]}
           ...
   ```

3. Write unit tests (6-8 tests):
   - ✅ Correct password
   - ✅ Wrong password
   - ✅ User not found
   - ✅ Role assignment
   - ✅ Quota tracking

**Deliverable:** Local auth + 6 tests

#### Subweek 5b: LDAP Backend (Optional)
**Tasks:**
1. Create `core/plugins/corvin_plugins/providers/user_backend_ldap.py`
2. Mock LDAP server for tests
3. Write unit tests (6-8 tests)

**Deliverable:** LDAP auth + 6 tests (optional for Phase 1)

#### Subweek 6: OIDC Backend (Optional)
**Tasks:**
1. Create `core/plugins/corvin_plugins/providers/user_backend_oidc.py`
2. Mock OIDC server for tests
3. Write unit tests

**Deliverable:** OIDC auth + 6 tests (optional for Phase 1)

---

## Week 9-12: Core Integration & Circuit Breaker

### Week 9: Circuit Breaker Pattern

**Owner:** Engineer D  
**Tasks:**
1. Design circuit breaker wrapper (prevent audit crashes from blocking core):
   ```python
   class PluginCircuitBreaker:
       """Fail-safe wrapper for plugin calls."""
       
       def __init__(self, plugin: CorvinPlugin, timeout_s: float = 5.0):
           self.plugin = plugin
           self.timeout_s = timeout_s
           self.failures = 0
           self.max_failures = 3
           self.state = "closed"  # closed | open | half_open
       
       async def call(self, method_name: str, *args, **kwargs):
           """Call plugin method with circuit breaker."""
           if self.state == "open":
               # Circuit is open — queue event, don't call
               self.queue_operation(method_name, args, kwargs)
               return self.fallback_result()
           
           try:
               # Try to call with timeout
               return await asyncio.wait_for(
                   getattr(self.plugin, method_name)(*args, **kwargs),
                   timeout=self.timeout_s
               )
           except asyncio.TimeoutError:
               self.trip()  # Open circuit
               self.queue_operation(method_name, args, kwargs)
               return self.fallback_result()
           except Exception as e:
               self.failures += 1
               if self.failures >= self.max_failures:
                   self.trip()
               raise
       
       def trip(self):
           """Open circuit. Log the event."""
           self.state = "open"
           logger.warning(f"circuit_breaker opened for {self.plugin.plugin_id}")
       
       def reset(self):
           """Close circuit. Resume normal operation."""
           self.state = "closed"
           self.failures = 0
   ```

2. Write 5-7 unit tests:
   - ✅ Normal call succeeds
   - ✅ Timeout → circuit opens
   - ✅ Circuit open → queue operation
   - ✅ Circuit recovery
   - ✅ Multiple timeouts → circuit stays open

**Deliverable:** Circuit breaker + 7 tests

---

### Week 10: Wiring in Core Boot

**Owner:** Engineer A  
**Tasks:**
1. Modify `core/boot.py` or `core/app.py` to:
   ```python
   # Load plugin system
   from corvin_plugins import registry, PluginContext
   
   def init_plugins(tenant_id: str = "_default") -> None:
       """Initialize all built-in plugins."""
       
       # 1. Create audit backend plugin
       audit_plugin = DefaultAuditBackendPlugin()
       ctx = PluginContext(
           plugin_id="audit-backend-default",
           tenant_id=tenant_id,
           corvin_home=Path.home() / ".corvin",
           config={},
           audit_emit=lambda e, d: print(f"{e}: {d}"),  # Temp
       )
       registry.register(audit_plugin, ctx)
       
       # 2. Create user backend plugin
       auth_config = load_config()["auth_provider"]  # "local" | "ldap" | "oidc"
       if auth_config == "local":
           user_plugin = LocalUserBackendPlugin()
       elif auth_config == "ldap":
           user_plugin = LDAPUserBackendPlugin()
       else:
           user_plugin = LocalUserBackendPlugin()  # Default
       
       ctx = PluginContext(...)
       registry.register(user_plugin, ctx)
   ```

2. Inject into FastAPI context:
   ```python
   app.state.audit_plugin = registry.get("audit-backend-default")
   app.state.user_plugin = registry.get(auth_config)
   ```

3. Update audit calls:
   ```python
   # Old: audit.log_event(...)
   # New: app.state.audit_plugin.log_event(...)
   ```

4. Write integration tests (5-7):
   - ✅ Plugins boot correctly
   - ✅ Audit plugin callable from core
   - ✅ User plugin callable from core
   - ✅ Both plugins together
   - ✅ Graceful shutdown

**Deliverable:** Core boot wiring + 7 tests

---

### Week 11-12: E2E Testing

**Owner:** Engineer C + D  
**Tasks:**
1. Write E2E tests (10-15 tests):
   - ✅ Full lifecycle: boot → log event → disable audit → continue
   - ✅ User login: local auth → LDAP auth → OIDC auth
   - ✅ Multi-tenant: audit chains separate
   - ✅ Circuit breaker: audit timeout → core continues
   - ✅ Quota enforcement: user hits limit
   - ✅ Retention: old events auto-delete
   - ✅ Graceful degradation: disable auth plugin → guest mode
   - ✅ Health check: all plugins report status
   - ✅ Plugin hot-reload: disable audit → re-enable without restart

2. Add to CI pipeline
3. Document test scenarios

**Deliverable:** 15+ E2E tests (all passing)

---

## Week 13-14: Documentation & Rollout

### Week 13: Operator Documentation

**Owner:** Engineer A + B  
**Tasks:**
1. Write migration guide:
   - "Converting CorvinOS to Plugin Architecture"
   - Step-by-step for ops teams
   - Environment variables → config YAML mapping

2. Write plugin author guide:
   - "How to Write Custom Audit Backends"
   - "How to Write Custom User Backends"
   - Template + examples

3. Write troubleshooting:
   - "Audit plugin not logging"
   - "User authentication fails"
   - "How to debug plugin issues"

**Deliverable:** 3 documentation pages

---

### Week 14: Beta Rollout

**Owner:** Engineer D (release manager)  
**Tasks:**
1. Tag release `v0.11.0-beta1` (Audit + Auth plugins)
2. Deploy to internal test environment
3. Run 1-week smoke test
4. Gather feedback
5. Fix any issues
6. Tag `v0.11.0` (stable)

---

## Week 15-16: Retrospective & Phase 2 Planning

**Owner:** All  
**Tasks:**
1. Post-mortem: What went well? What was hard?
2. Update roadmap based on learnings
3. Plan Phase 2 (Compute + Router plugins)
4. Schedule Phase 2 kickoff

---

## Success Criteria

### Code Quality
- ✅ 56+ unit tests (all passing)
- ✅ 15+ E2E tests (all passing)
- ✅ 90%+ code coverage
- ✅ No security issues in code review
- ✅ Backwards compatible (old config still works)

### Performance
- ✅ Audit logging latency: <5ms (p99)
- ✅ Auth latency: <100ms (p99)
- ✅ Circuit breaker recovery: <30s
- ✅ No memory leaks (sustained 24h test)

### Operability
- ✅ Clear error messages (no cryptic failures)
- ✅ Health check shows plugin status
- ✅ Can switch auth backend without restart
- ✅ Documentation covers all scenarios

### Security/Compliance
- ✅ Audit trail immutable (hash-chain verified)
- ✅ No PII in audit events
- ✅ Per-tenant audit isolation
- ✅ GDPR retention automated

---

## Team Roles & Responsibilities

| Engineer | Weeks | Tasks | 
|----------|-------|-------|
| **A** | 1-8, 13 | AuditBackend protocol + plugin + wiring + docs |
| **B** | 1-7, 13 | UserBackend protocol + LDAP + docs |
| **C** | 2-3, 11-12 | Templates + E2E testing |
| **D** | 9-10, 12, 14-16 | Circuit breaker + core wiring + rollout |

---

## Dependencies & Blockers

### Must-Have
- ✅ Plugin system v1 complete (Phases 1-2b already done)
- ✅ Protocol.py extensible (can add new plugin types)
- ⚠️ Audit code base readable (need to extract logic)
- ⚠️ Auth code base readable (need to extract logic)

### Nice-to-Have
- Database for audit trail (optional; file-based works)
- LDAP server for testing (can mock)
- OIDC server for testing (can mock)

### Risks to Mitigate
- **Risk:** Audit plugin crashes → audit trail stops
  - **Mitigation:** Circuit breaker + in-memory queue
  
- **Risk:** Auth plugin slow → login timeouts
  - **Mitigation:** Timeout (5s) + fallback to cached user

- **Risk:** Breaking change for existing operators
  - **Mitigation:** Backwards-compat mode (env vars → plugin config)

---

## Budget & Resources

| Item | Cost | Notes |
|------|------|-------|
| **Engineer Time** | 50 engineer-weeks | 3-4 engineers × 13-16 weeks |
| **Infrastructure** | Minimal | No new servers needed |
| **Testing** | Included | 20+ tests written in-process |
| **Documentation** | Included | 3 operator guides |

---

## Next Steps (Ready to Execute)

### This Week (2026-07-26)
- [ ] Approve Phase 1 plan (this document)
- [ ] Assign engineers A, B, C, D
- [ ] Kick off Week 1 (protocol design)

### Week 1 (2026-07-29)
- [ ] Engineer A starts AuditBackend protocol
- [ ] Engineer B starts UserBackend protocol
- [ ] Engineer C prepares templates
- [ ] Daily standup (Mon-Fri, 9am)

### Week 2 (2026-08-05)
- [ ] Protocol design reviews
- [ ] Circuit breaker design
- [ ] Begin built-in plugin implementation

---

## Approval Checklist

- [ ] Tech lead: Approve architecture
- [ ] Security: Audit + Auth critical path approved
- [ ] PM: Timeline acceptable
- [ ] Ops: Documentation plan accepted
- [ ] All engineers: Ready to commit

---

**Phase 1 Ready to Execute. Let's compartmentalize CorvinOS.** ⚓

---

## Appendix: Command Reference

### Run Phase 1 Tests
```bash
cd /home/shumway/projects/CorvinOS
pytest core/plugins/tests/ -v  # Protocol + plugin tests
pytest -m integration  # E2E tests
```

### Check Plugin Status
```bash
python -c "from corvin_plugins import discover; print(discover())"
```

### Deploy Phase 1 Release
```bash
git tag v0.11.0
git push origin v0.11.0
python -m build
twine upload dist/*
```
