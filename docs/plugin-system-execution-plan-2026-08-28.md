# Plugin System Completion — Autonomous Execution Plan
**Date:** 2026-08-28  
**Status:** Active  
**Author:** Claude Code  
**Audience:** Solo maintainer + autonomous Claude Code sessions  
**Source of Truth:** `docs/implementation/PLUGIN_SYSTEM_ACTIVATION_PLAN.md` (2026-07-27)

---

## EXECUTIVE SUMMARY

### Current State
The plugin system is **92% complete** as of 2026-07-27. Nineteen ADRs (0231–0249) specify the architecture; implementation exists for all mechanisms. What remains is **proving reachability** — wiring call sites, closing E2E spine gaps, and depositing the trust anchor key.

### What Remains (Concrete Deliverables)
| Item | Est. Effort | Blocker | Owner |
|---|---|---|---|
| **Stage 6:** `corvin plugin install` + trust anchor key | 1 week | Maintainer key custody | Autonomous |
| **E2/E3 rows:** Per-stage E2E lifecycle tests | 1 week | Stages must be complete | Autonomous |
| **ADR-0250 Part 2:** Registry keying migration | Deferred | Architectural; decoupled from activation | Future |
| **Marketplace installer** (`install <name>` URL/search) | Deferred | Separate project (ADR-0233 D3) | Future |
| **Template modules** (`stt_provider`, `data_connector`) | Deferred | Requires ADR-0245 design | Future |

### Critical Path
```
Now ──→ Stage 6 (1w) ──→ E2/E3 rows (1w) ──→ Production gate (Stage 0 + coverage.yml ✅)
   
Blocker: Maintainer Ed25519 key custody (Stage 6 blocking item)
```

### Success Criteria (Go/No-Go)
1. **Stage 0 gate:** GREEN (all 950+ core/plugins tests in CI)
2. **E1 harness:** GREEN (`test_lifecycle_e2e.py` passes)
3. **E2/E3 scenarios:** GREEN (per-stage rows passing)
4. **Docs:** Synced (`CLAUDE.md`, `layer-plugins.md`, `surface_map.py` accurate)
5. **Smoke test:** `corvin plugin install <path> && gateway boot && hook fires` works end-to-end

### Risks (3 Technical Debt Items)
1. **Trust anchor loss:** No recovery path if private key is lost (Stage 6 dependent).
2. **E2E console boot flakiness:** Isolated E2E harness has history of hanging (E3 droppable if unstable).
3. **Plugin supervisor crash race:** A dead daemon can cascade; `bridge_manager.py` wraps mitigate risk.

---

## MILESTONES WITH DEPENDENCIES

### PRE-STAGE: Verify Foundation (Parallel, 0.5 d)

**Deliverables:**
- Confirm `core/plugins/tests/` is in `coverage.yml` (fix: 2026-07-27 + CI registration)
- Verify ADR-0250/0251 status transitions (Proposed → Accepted): DONE ✅
- Run full plugin test suite locally: `pytest core/plugins/tests/` (950+ tests)
- Confirm `voice-audit verify` passes on test fixture

**Acceptance Criteria:**
- ✅ All 950+ core/plugins tests pass locally
- ✅ CI includes `core/plugins/tests/` in coverage.yml
- ✅ ADRs 0250/0251 status = ACCEPTED (merged in Corvin-ADR)
- ✅ Audit chain tripwire is reached on boot (Track F verified)

**Dependencies:** None (Foundation)

---

### MILESTONE 1: Stage 6 — CLI `install` Command + Trust Anchor (1 week)

**Motivation:** Close G7 (CLI is absent) and G8 (no trust anchor). These are the final activation blockers.

**Part 1.1: Implement `corvin plugin install <path>` (3 days)**

**Deliverable:**
- New CLI command: `corvin plugin install <local_path>`
  - Reads plugin metadata from `<path>/setup.py` or `pyproject.toml` + `plugin.yaml`
  - Extracts `plugin_id`, `version`, `plugin_type`, `class_path`, `origin`
  - Writes entry to `<corvin_home>/tenants/<tenant_id>/global/tenant.corvin.yaml` under `spec.plugins.installed`
  - For `origin=community`: requires operator confirmation (yes/no prompt)
  - For `origin=vetted`: requires trust anchor key in `~/.corvin/global/plugin_trust_anchors.txt`; refuses if missing
  - Prints confirmation + the `auto_discover_entry_points` step from generated README
  - Guards: URL arguments rejected with clear message; unsupported origins rejected

**Code locations:**
- `core/cli/plugin_cmd.py` — add `install` subcommand
- `core/plugins/corvin_plugins/trust.py` — use existing `verify_origin_claim()`
- `core/plugins/corvin_plugins/metadata.py` — read plugin metadata from entry point

**Tests (10+ unit tests):**
- ✅ URL argument rejected (`--help` shows `<path>` not `<url>`)
- ✅ Community plugin without confirmation does not load (audit event written)
- ✅ Vetted claim with unpinned key is **refused**, not downgraded
- ✅ Local path installs and registers in `tenant.corvin.yaml` (declarative pass)
- ✅ Confirmation audit event carries operator identifier, no PII
- ✅ Duplicate install is idempotent (second run is no-op with message)

**Acceptance Criteria:**
- ✅ `corvin plugin install <path>` works end-to-end (creates entry in config)
- ✅ Community plugins require explicit confirmation (audit trail)
- ✅ Vetted claims checked against trust anchor
- ✅ Help text is clear; no URL support mentioned
- ✅ E1 test harness uses this path (not just declarative `spec.plugins.installed`)

**Estimated time:** 3 days  
**Blocked by:** None (independent)

---

**Part 1.2: Deposit Trust Anchor Key + Documentation (2 days)**

**Action items (Maintainer-only decision):**
1. Generate Ed25519 keypair:
   ```bash
   # Maintainer runs this once, offline or in secure environment
   ssh-keygen -t ed25519 -N "" -f ~/.corvin/global/plugin_trust_anchors.txt -C "corvinOS-plugin-trust"
   # Creates:
   #   ~/.corvin/global/plugin_trust_anchors.txt (private key)
   #   ~/.corvin/global/plugin_trust_anchors.txt.pub (public key)
   chmod 600 ~/.corvin/global/plugin_trust_anchors.txt
   ```

2. Document custody procedure in:
   - `docs/operations/plugin-trust-anchor-procedures.md` (new)
     - Where key is stored and backup strategy
     - Recovery procedure if key is lost (none today; ADR-0249 defers revocation)
     - Signing SOP for new vetted plugins
   - `CLAUDE.md` § "Plugin Trust Anchor" (update)

3. Publicly commit `plugin_trust_anchors.txt.pub` to repo (add to `.gitignore` exception)

4. `corvin plugin sign <path>` (future, NOT in this plan — signing is maintainer-only for now)

**Deliverable:**
- Key custody documented in `docs/operations/plugin-trust-anchor-procedures.md`
- Public key deposited in repo
- CLAUDE.md updated with key location and backup plan
- All references to "no trust anchor" in code/tests updated to assume key exists

**Tests (5 unit tests):**
- ✅ `trust.py` reads key from `~/.corvin/global/plugin_trust_anchors.txt`
- ✅ Verification fails gracefully if key file missing (audit event)
- ✅ Malformed key is caught at startup (tripwire, not silently skipped)
- ✅ Key format is Ed25519 SSH (not PEM or other format)

**Acceptance Criteria:**
- ✅ Maintainer has generated key and stored securely
- ✅ Public key is in repo
- ✅ Custody procedure documented and reviewed
- ✅ No override/escape hatch for missing key (fail-closed)

**Estimated time:** 2 days  
**Blocked by:** Maintainer Ed25519 key generation (cannot be automated)

---

### MILESTONE 2: Stage 6 E2E Spine Row — Full Lifecycle Test (1 week)

**Motivation:** Prove `corvin plugin install` works end-to-end: new plugin → install → boot → hook fires → audit chain intact.

**Deliverable:**
- Add row to `_STAGE_ROWS_OWED` in `core/plugins/tests/test_lifecycle_e2e.py`
- Scenario: `community_plugin_without_confirmation_denied` + `vetted_plugin_accepted`
  - Create a community plugin with `corvin plugin new`
  - Attempt to install without confirmation → refusal, audit event written
  - Create a vetted plugin, sign it, install it → success
  - Boot the gateway → plugin loads, hook fires
  - Verify audit chain: `voice-audit verify` still passes
  - Confirm the hook actually fired (e.g., a model selection hook changed the model)

**Code:**
- `core/plugins/tests/test_lifecycle_e2e.py` — add `test_stage_6_install_and_trust_anchor_e2e()`
- Fixtures: isolated tmp `CORVIN_HOME`, mock trust anchor key in `pytest.fixture`

**Tests (3 scenarios):**
- ✅ Community plugin install blocked without confirmation (audit event)
- ✅ Vetted plugin installs and loads successfully
- ✅ Hook fires on a real turn (not just `on_load()`)

**Acceptance Criteria:**
- ✅ E2 row for Stage 6 is in `_STAGE_ROWS_OWED` and passing
- ✅ `voice-audit verify` passes after plugin runs
- ✅ Audit event for refusal/confirmation is in chain
- ✅ Hook return value is used (model selection changed, or gate denial honored)

**Estimated time:** 5 days  
**Blocked by:** Milestone 1 (Stage 6 CLI + key)

---

### MILESTONE 3: E3 — Un-mocked Playwright Tests (1 week, droppable if unstable)

**Motivation:** Prove the Console's plugin panel works against a real gateway (not route interception).

**Deliverable:**
- Run `core/console/web-next/tests/e2e/plugins.spec.ts` against real gateway
- Use isolated-E2E-console harness (from `test_lifecycle_e2e.py`)
- Scenarios:
  - List plugins (Playwright visit `/console/plugins`)
  - Filter by scope (builtin / installed)
  - Enable/disable a plugin (toggle, E2E verifies `plugin_health_monitoring` flag)
  - Promote a skill (if skill system is integrated; TBD)

**Code:**
- `core/console/web-next/tests/e2e/plugins.spec.ts` — update to use real gateway
- Fixtures: use pytest + subprocess to spin up gateway; hand URL to Playwright

**Tests:**
- ✅ Panel loads without route interception
- ✅ Plugin list reflects installed plugins
- ✅ Enable/disable works end-to-end (registry updated, not just UI)
- ✅ Health monitoring flag toggles visibility of health column

**Acceptance Criteria:**
- ✅ E3 row passes with real gateway
- ✅ Console does not hang on boot (if it does, E3 is dropped and noted here)
- ✅ All plugin E2E tests pass against real server (not mocked)

**Estimated time:** 5-7 days (high variability based on console boot stability)  
**Blocked by:** Milestone 2 (Stage 6 E2E first)  
**Drop condition:** If isolated E2E console hang persists after 3 iterations, drop E3 and mark as "droppable per plan" — do not force a failing test to pass

---

### MILESTONE 4: ADR-0250 Part 2 — Registry Keying Migration (Deferred, 2-3 weeks if/when started)

**Status:** DEFERRED by design. This is a separate architectural project.

**Trigger:** "When single-tenant proves stable and refusal gate has one release of production use" (PLUGIN_SYSTEM_ACTIVATION_PLAN § 2).

**Scope (NOT in this milestone, here for reference):**
- Key all eight registries (`audit_backend`, `user_backend`, `stt_provider`, etc.) by `tenant_id`
- Thread `tenant_id` through `PluginContext` (instead of module globals)
- Update `bootstrap.build_context()` to build per-tenant registry sets
- Migrate `set_active(provider)` → `set_active_for_tenant(tenant_id, provider)` at provider object level; module-level convenience functions gain required tenant argument
- D1 (refusal gate) stays as backstop; tests prove every registry is tenant-keyed

**When to start:** ADR-0250 Part 2 is a migration with its own gate and its own `voice-audit verify` before/after. It should not start until:
1. Part 1 (refusal) has one release of production use
2. No cross-tenant incidents reported
3. Maintainer explicitly decides to proceed

**Dependencies:** Part 1 must be stable + production-proven

---

### MILESTONE 5: Deferred Items Roadmap (Separate Projects)

These are **explicitly out of scope** for this plan. They are listed here so they are not accidentally bundled into later stages.

| Item | Deferred by | Trigger to Build | Owner |
|---|---|---|---|
| **Marketplace installer** (`install <name>` URL/search/list) | ADR-0233 D3, ADR-0248 | Separate project; requires plugin registry backend + web UI | TBD |
| **Templates for `worker_engine` + `bridge_channel`** | ADR-0245 (design question) | L22 registration API needed first; `bridge_channel_registry` class needs to be written | Future |
| **Templates for `stt_provider` + `data_connector`** | ADR-0246 | Once L23/L24 consumers exist (currently consumed by nothing) | Future |
| **Process isolation / sandboxing** | ADR-0249 § "Not decided here" | If plugin safety becomes a blocker; requires subprocess or container | Future |
| **Signature revocation** | ADR-0249 (known gap) | After production use; honesty requires a separate channel | Future |
| **Directory move (Phase 7)** | Dropped from this plan § 2 | Risk high, benefit cosmetic; deferred indefinitely | Future |

**Note:** ADR-0246 (templates) become *unblocked* once L23/L24 gain consumers, but they are still not activated until someone needs them. A scaffold that manufactures silence is worse than no scaffold.

---

## AUTONOMOUS EXECUTION STRUCTURE

### Phase 1: Milestone 1 (Stage 6 CLI) — 3 days

**Task 1.1: Implement `corvin plugin install` subcommand**
- **Description:** Add to `core/cli/plugin_cmd.py`; wire into `argparse`
- **Acceptance Criteria:**
  - `corvin plugin install <path>` creates entry in `tenant.corvin.yaml`
  - URL arguments rejected with clear message
  - Help text shows only `<path>`, no URL examples
  - Entry is picked up on next gateway boot
- **Dependencies:** None
- **Est. Time:** 2 days
- **Tests to add:** 10 unit tests (see Milestone 1.1 above)

**Task 1.2: Implement community plugin confirmation gate**
- **Description:** Prompt operator for `origin=community` plugins; record audit event
- **Acceptance Criteria:**
  - Confirmation prompt appears (yes/no)
  - Refusal: plugin not added to config, audit event written
  - Acceptance: plugin added, audit event written with operator id
  - No PII in audit event (operator identifier only, which audit chain already has)
- **Dependencies:** Task 1.1
- **Est. Time:** 1 day
- **Tests:** 3 unit tests

**Task 1.3: Deposit trust anchor key (Maintainer decision point)**
- **Description:** Maintainer generates Ed25519 key offline and commits public half
- **Acceptance Criteria:**
  - Private key at `~/.corvin/global/plugin_trust_anchors.txt` (600 perms)
  - Public key at `~/.corvin/global/plugin_trust_anchors.txt.pub` (in repo)
  - Custody procedure documented in `docs/operations/plugin-trust-anchor-procedures.md`
  - CLAUDE.md updated with location + backup strategy
- **Dependencies:** None (parallel; but must complete before E2E tests)
- **Est. Time:** 0.5 days (exclusive owner decision, cannot be parallelized)
- **Note:** This is a BLOCKER for E2E; cannot proceed without actual key

**Task 1.4: Implement vetted plugin signature verification**
- **Description:** Wire `trust.verify_origin_claim()` into install command
- **Acceptance Criteria:**
  - Vetted claim without key in `plugin_trust_anchors.txt.pub` is **refused**, not downgraded
  - Audit event written on refusal (plugin id, reason, timestamp)
  - Valid signature accepted, invalid signature refused
- **Dependencies:** Task 1.3 (key must exist)
- **Est. Time:** 1 day
- **Tests:** 3 unit tests

---

### Phase 2: Milestone 2 (Stage 6 E2E) — 5 days

**Task 2.1: Create E2E scenario for community plugin refusal**
- **Description:** Add to `test_lifecycle_e2e.py`; test full lifecycle with confirmation gate
- **Acceptance Criteria:**
  - Community plugin installation without confirmation is refused
  - Refusal audit event is in chain
  - `voice-audit verify` passes after refusal
- **Dependencies:** Milestone 1 complete
- **Est. Time:** 2 days
- **Flag-gating:** None (no feature flag for trust enforcement; it is a compliance gate)

**Task 2.2: Create E2E scenario for vetted plugin acceptance**
- **Description:** Install a vetted plugin, boot gateway, hook fires
- **Acceptance Criteria:**
  - Plugin loads successfully
  - Hook fires on real turn (e.g., model selection hook changes model)
  - Audit chain remains intact (`voice-audit verify` passes)
- **Dependencies:** Milestone 1 complete + Task 2.1
- **Est. Time:** 2 days
- **Tests:** Same harness as E1; add row to `_STAGE_ROWS_OWED`

**Task 2.3: Guard test for Stage 6 completion**
- **Description:** Assert `_STAGE_ROWS_OWED` includes Stage 6
- **Acceptance Criteria:**
  - Test fails until Stage 6 E2 row is passing
  - Merging a stage without E2E row fails CI
- **Dependencies:** Task 2.1 + Task 2.2
- **Est. Time:** 0.5 days

---

### Phase 3: Milestone 3 (E3 — Un-mocked Playwright) — 5-7 days (droppable)

**Task 3.1: Refactor `plugins.spec.ts` to use real gateway**
- **Description:** Replace route interception with actual HTTP calls
- **Acceptance Criteria:**
  - Playwright connects to real gateway via isolated-E2E harness
  - No route mocks; all plugin operations go through gateway API
  - List, filter, enable/disable work end-to-end
- **Dependencies:** Milestone 1 complete (plugins CLI functional)
- **Est. Time:** 3 days
- **Drop condition:** If isolated E2E console boot hangs >30s consistently, drop E3

**Task 3.2: Add multi-browser coverage (if E3 not dropped)**
- **Description:** Run E3 against Chrome, Firefox, Safari (Playwright matrix)
- **Acceptance Criteria:**
  - All browsers pass the same test scenarios
  - Mobile viewport (375px) tested
- **Dependencies:** Task 3.1 passing
- **Est. Time:** 2 days
- **Drop condition:** If browser matrix takes >1 day to stabilize, defer to v0.12 release

**Task 3.3: Guard test for E3 completion (or drop)**
- **Description:** Either assert E3 row passes, or mark as "intentionally droppable"
- **Acceptance Criteria:**
  - Either: E3 all green
  - Or: Documented in `_STAGE_ROWS_OWED` with comment "E3 dropped — console boot unstable, re-evaluate v0.12"
- **Dependencies:** Task 3.1 or explicit drop decision
- **Est. Time:** 0.5 days

---

### Verification Phase: Production Gate (1 day)

**Task V.1: Stage 0 gate (call-site assertions)**
- **Description:** Verify Stage 0's gate is green in CI
- **Acceptance Criteria:**
  - All 950+ `core/plugins/tests/` pass in CI
  - `core/plugins/tests/` in `coverage.yml` ✅ (already done 2026-07-27)
  - Every `KNOWN_EXTENSION_POINTS` has a production call site
  - Every `surface_map` entry with `consumed_by is not None` is actually consumed
- **Est. Time:** 0.5 days (CI confirmation only)

**Task V.2: Docs sync**
- **Description:** Verify all changed files have updated docs/ADRs
- **Acceptance Criteria:**
  - `CLAUDE.md` § Plugin System Block updated
  - `docs/claude-ref/layer-plugins.md` updated
  - `docs/extending.md` mentions `corvin plugin install`
  - `surface_map.py` docstrings accurate
  - ADRs 0250/0251 links correct
- **Est. Time:** 0.5 days

**Task V.3: Smoke test**
- **Description:** Full end-to-end on a clean install
- **Acceptance Criteria:**
  - `corvin plugin new my-test-plugin` works
  - `corvin plugin install ./my-test-plugin` works
  - Gateway boots without error
  - Plugin's hook fires (verified via audit event)
  - `voice-audit verify` passes
- **Est. Time:** 0.5 days (manual)

---

## MEASURED SUCCESS CRITERIA

### Gate 1: Stage 0 — Call-site Reachability (MUST PASS)
All four checks RED until each stage is complete:

```python
# test_extension_point_call_sites.py
def test_engine_engine_selection_call_site():
    """Assert engine.engine_selection is invoked from delegation_policy."""
    assert "invoke(" in load_code("delegation_policy.py")
    # RED until Stage 3 call site is wired

def test_all_consumed_types_are_really_consumed():
    """Assert surface_map consumed_by entries are actually called."""
    for entry in surface_map():
        if entry.consumed_by:
            assert f"get_active()" in load_code(entry.consumed_by)
    # RED until Stages 3–4 complete

def test_all_unconsumed_have_dead_reason():
    """Assert every unconsumed entry has a dead_reason."""
    for entry in surface_map():
        if entry.consumed_by is None:
            assert entry.dead_reason is not None
    # GREEN (metadata check)

def test_stage_rows_owed_are_satisfied():
    """Assert _STAGE_ROWS_OWED has passing test rows."""
    assert len(_STAGE_ROWS_OWED) == 0  # All rows closed
    # RED until E1, E2, E3 complete
```

**Current state:** ✅ GREEN after stages 0–5 (2026-07-27)  
**Final state (this plan):** ✅ GREEN after Stage 6 + E2/E3

---

### Gate 2: E2E Spine — Production Scenarios
Each stage MUST have an end-to-end row that goes through the real bootstrap path:

| Stage | E2E Scenario | Must Verify | Status |
|---|---|---|---|
| E1 (existing) | Audit backend loads, fires, chain intact | Real `audit_backend.fanout()` called | ✅ DONE |
| E2–3 (Stages 3–4) | Extension point hook fires, model selection changed | Hook return value used, audit event | ◑ NOT YET |
| E2–5 (Stage 5) | Bridge supervisor health monitored, dead daemon unhealthy | Supervisor wraps `bridge_manager.py`, no restart | ◑ NOT YET |
| E2–6 (Stage 6) | Community plugin refused, vetted accepted, hook fires | Confirmation audit event, trust verification | ◑ NOT YET |
| E3 (console) | Un-mocked plugin panel, enable/disable real | Gateway serves real routes, not intercepted | ◑ NOT YET (droppable) |

**Current:** E1 only  
**Final:** E1 + E2–6 (E3 droppable per stability)

---

### Gate 3: Docs/Code Sync
| File | Current (2026-07-27) | Must be updated | Check |
|---|---|---|---|
| `CLAUDE.md` § Plugin block | Exists; mentions "no `install` command" | Remove that caveat; mention key custody | § 7 |
| `layer-plugins.md` | "do not install multi-tenant provider" | Update to "enforce refusal" | § 6 |
| `docs/extending.md` | Mentions `corvin plugin types` | Add section for `corvin plugin install` | § 6 |
| `surface_map.py` docstrings | Three rows with wrong `dead_reason` | Correct user_backend / worker_engine / bridge_channel | ✅ DONE |
| ADR-0250/0251 links | No links in CLAUDE.md | Add references | § 2 |

**Gate:** `scripts/docs-sync-check.sh` passes (if script exists) OR manual review confirms all changed code has updated docs

---

### Gate 4: Operator Smoke Test (Manual, 1 instance)
```bash
#!/bin/bash
set -e

# 1. Create a test plugin
mkdir -p /tmp/test-plugin
cd /tmp/test-plugin
corvin plugin new test-audit-sink
cd test-audit-sink

# 2. Implement a trivial audit backend
cat >> test_audit_sink/backend.py <<'EOF'
from corvin_plugins.protocol import PluginContext
class TestAuditSink:
    plugin_id = "com.example.test-audit-sink"
    plugin_type = "audit_backend"
    version = "1.0.0"
    def on_load(self, ctx: PluginContext):
        ctx.audit_backend.set_active(self)
    def fanout(self, event, details):
        print(f"[TEST-AUDIT] {event.type}")
EOF

# 3. Install
corvin plugin install /tmp/test-plugin/test-audit-sink

# 4. Boot gateway (headless)
export CORVIN_HOME=/tmp/corvin-test-home
corvin-gateway &
sleep 5

# 5. Verify hook fires
curl -s http://localhost:8765/v1/health | grep -q '"status": "ok"'
echo "PASS: Gateway booted, health OK"

# 6. Verify audit chain
voice-audit verify
echo "PASS: Audit chain intact"

kill %1
```

**Success:** All steps pass, no errors.

---

## RESOURCE & BLOCKERS

### Blocker 1: Maintainer Ed25519 Key Custody (Critical Path)

**Impact:** Blocks Milestone 1.3 and all Stage 6 validation.

**Required action:**
1. Maintainer generates key (offline or secure environment)
2. Stores private half securely (suggest: `pass` or encrypted USB)
3. Commits public half to repo
4. Documents custody in `docs/operations/plugin-trust-anchor-procedures.md`

**Timeline:** Must complete before Task 1.3 starts.  
**Mitigation if lost:** None today (ADR-0249 defers revocation). Recovery strategy is future work.

**Dependency chain:**
```
Maintainer generates key → Task 1.3 → Tasks 2.1–2.2 → Gate 1 passes
```

---

### Blocker 2: CI Registration of `core/plugins/tests/` (Already Fixed ✅)

**Status:** FIXED 2026-07-27  
**Verification:** Confirm `coverage.yml` lists `core/plugins/tests/` under test paths.  
**If not fixed:** Add in first commit of this plan.

---

### Blocker 3: E2E Console Boot Stability (Medium Risk)

**Issue:** Isolated E2E console has history of hanging in boot (issue reference: CLAUDE.md).

**Impact:** E3 (Milestone 3) may be unstable; droppable per plan.

**Mitigation:**
- Budget E3 at 5–7 days with explicit drop condition
- If console boot >30s consistently, mark E3 as dropped + document
- Do NOT force E3 to pass; a green test that boots nothing is worse than an honest drop

**Escalation:** If E3 cannot be fixed in 3 iterations, proceed to Gate 1 without E3 (it is explicitly droppable in this plan).

---

### Resource: Solo Maintainer + Async Claude Code Sessions

**Available capacity:**
- Estimated 2–3 weeks for Stages 6 + E2/E3 (Milestones 1–3)
- Cannot be parallelized: Stage 6 CLI must precede E2E

**Sequencing:**
```
Week 1: Milestone 1 (Stage 6 CLI) — 3 days
        + Maintainer key gen (parallel, 0.5 d)
Week 2: Milestone 2 (E2 rows) — 5 days
Week 3: Milestone 3 (E3) — 5–7 days OR drop + verification gate
```

**Sessions budget:**
- Each Milestone: 2–3 Claude Code sessions (6–12 hours per session, est.)
- Refutation rounds: 1 per Milestone (find defects in own code)
- Pre-commit: `bash operator/bridges/run-all-tests.sh` (budgets >15 min per commit)

---

### Resource: CI/CD

**Already in place (2026-07-27):**
- ✅ `core/plugins/tests/` in `coverage.yml`
- ✅ `pytest` runs all 950+ tests
- ✅ ADRs 0250/0251 in Corvin-ADR (ACCEPTED)

**No new CI setup required** (tests are already gated).

---

## DEFERRED ITEMS — NOT IN THIS PLAN

| Item | Why deferred | Trigger to build | Est. effort |
|---|---|---|---|
| **Marketplace installer** | Separate project; needs registry backend + web UI | New GitHub repo + design | 2–3 weeks |
| **`corvin plugin sign` (CLI)** | Maintainer-only; no use case for automation yet | When first third-party vetted plugin needed | 2 days |
| **ADR-0250 Part 2** (registry keying) | Architectural migration; decoupled from activation | After 1 release of Part 1 production use | 2–3 weeks |
| **Template modules** (worker_engine, bridge_channel) | ADR-0245 design questions unsolved | After L22/L23/L24 design settled | 1 week each |
| **Process isolation** | ADR-0249 explicitly deferred | If plugin containment becomes blocker | TBD |
| **Signature revocation** | Known gap; requires new channel design | After first security incident or policy change | 1 week |

**Rationale:** These are decision-class or design-class items, not implementation-class. Starting them now would create dependencies on items outside this session's control.

---

## EXECUTION — STARTING NOW

### Week 1 (Days 1–5): Milestone 1 — Stage 6 CLI

**Monday (Day 1):**
- [ ] Confirm `core/plugins/tests/` in `coverage.yml` ✅ (should be done)
- [ ] Review `core/cli/plugin_cmd.py` structure
- [ ] Design `install` subcommand interface (Task 1.1)
- [ ] Create test file `core/cli/tests/test_plugin_install.py`

**Tuesday–Wednesday (Days 2–3):**
- [ ] Implement Task 1.1: `corvin plugin install <path>` (10 tests)
- [ ] Implement Task 1.2: community plugin confirmation gate (3 tests)
- [ ] Pre-commit: `bash operator/bridges/run-all-tests.sh`

**Thursday (Day 4):**
- [ ] **MAINTAINER DECISION POINT:** Generate Ed25519 key (Task 1.3, ~30 min offline)
- [ ] Commit public key to repo + document custody
- [ ] Review CLAUDE.md § Plugin block for updates needed

**Friday (Day 5):**
- [ ] Implement Task 1.4: trust verification in install (3 tests)
- [ ] Manual smoke test: `corvin plugin install` works
- [ ] Create PR for code review

---

### Week 2 (Days 6–10): Milestone 2 — E2E Spine

**Monday–Friday (Days 6–10):**
- [ ] Task 2.1: E2E scenario for community plugin refusal (2 days)
- [ ] Task 2.2: E2E scenario for vetted plugin acceptance (2 days)
- [ ] Task 2.3: Guard test for Stage 6 completion (0.5 days)
- [ ] Run full suite: `pytest core/plugins/tests/ -v` (all green)
- [ ] Verify audit chain: `voice-audit verify` on test fixture

---

### Week 3 (Days 11–17): Milestone 3 — E3 (or drop)

**Option A: E3 succeeds**
- [ ] Task 3.1: Un-mock `plugins.spec.ts` (3 days)
- [ ] Task 3.2: Multi-browser coverage (2 days)
- [ ] Task 3.3: Guard test for E3 (0.5 days)

**Option B: E3 dropped (if console boot unstable)**
- [ ] Mark `_STAGE_ROWS_OWED` with comment "E3 dropped — console boot, re-evaluate v0.12"
- [ ] Document in this plan under "Drop condition"
- [ ] Proceed to Gate 1 verification

---

### Gate Verification (End of Week 3)

**Verification Phase (1 day):**
- [ ] Task V.1: Stage 0 gate GREEN in CI (call-site assertions)
- [ ] Task V.2: Docs sync verified (CLAUDE.md, layer-plugins.md, surface_map.py)
- [ ] Task V.3: Manual smoke test (`corvin plugin new` → `install` → boot → hook fires)

**Final checklist:**
- ✅ Stage 0 gate: GREEN
- ✅ E1 existing: GREEN
- ✅ E2 rows: GREEN (Stages 3–4, 5–6)
- ✅ E3: GREEN OR droppable with documented reason
- ✅ Docs: Synced (CLAUDE.md, ADR links, surface_map.py)
- ✅ CI: `core/plugins/tests/` in coverage.yml
- ✅ ADRs: 0250/0251 ACCEPTED (already are)

**Decision:** If all GREEN, plugin system activation is COMPLETE.

---

## SUMMARY TABLE

| Milestone | Effort | Blocker | Owner | Status |
|---|---|---|---|---|
| Pre-stage (foundation) | 0.5 d | None | Autonomous | ✅ READY |
| **Stage 6 CLI** | 3 d | Maintainer key | Autonomous (blocker: maintainer) | ⏳ PENDING KEY |
| **Stage 6 E2E** | 5 d | Stage 6 CLI | Autonomous | ⏳ PENDING |
| **E3 Console** | 5–7 d | Stage 6 E2E | Autonomous | ⏳ PENDING (droppable) |
| Verification gate | 1 d | All above | Autonomous | ⏳ PENDING |
| **TOTAL** | **~1 week** (async) | **Maintainer key** | **Autonomous** | **Go/no-go decision point: 2026-09-11** |

---

## NEXT STEPS (Start Immediately)

1. **Maintainer:** Confirm you can generate Ed25519 key and decide custody location by EOW
2. **Autonomous:** Confirm `core/plugins/tests/` in `coverage.yml` + CI passing
3. **Autonomous:** Start Week 1 (Milestone 1) on Monday; blocker is key custody only
4. **Weekly:** Sync on Milestone completion + any blockers

**Success metric:** Plugin system activation complete by 2026-09-11 (3 weeks), with Stage 0 gate GREEN and production smoke test passing.

---

**Document version:** 2026-08-28  
**Last updated:** 2026-08-28  
**Status:** ACTIVE — Ready for autonomous execution
