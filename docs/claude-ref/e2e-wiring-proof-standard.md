# E2E Wiring Proof — Standard Definition

**Reference:** `docs/claude-ref/e2e-wiring-proof-standard.md`  
**Last updated:** 2026-08-11  
**Applies to:** All implementation phases (Phase 1+)  
**Status:** Stable  

---

## Overview

This document defines the **E2E wiring proof** standard—a quality discipline for verifying that new code is not only correct, but actually reachable and exercised by real triggers in the running system.

**Why it exists:**
- A unit test proves a function returns the right value *when called*, not whether anything *calls* it.
- CorvinOS has shipped structural instances of this failure: plugin types that register but are never invoked, auth invariants unit-tested but reachable from zero live call sites.
- This standard makes that class of bug structurally harder to ship.

**Applies to:** Any new entry point—function, endpoint, route, CLI command, UI component, plugin, hook, bridge handler—intended to be reachable from outside its own file.

**Does not apply to:** Trivial edits (rename, typo), pure refactors with unchanged behavior and existing E2E coverage, config tuning, internal helpers already exercised by existing callers, code explicitly marked as dormant prototypes.

---

## The Standard: Two-Phase Gate

### Phase 1: Reachability Proof (Mandatory, Blocking)

**Goal:** Establish that at least one real call site exists, outside test files, traceable to a real trigger.

**Steps:**

1. **Search for the new symbol.** Grep the entire codebase:
   ```bash
   grep -rn "symbol_name\|route_path\|component_export" --include="*.py" --include="*.js" --include="*.ts" \
     | grep -v "/tests/" | grep -v "test_" | grep -v "__pycache__" | grep -v ".pyc"
   ```

2. **Find ≥1 call site that satisfies ALL of:**
   - **Outside the file where the symbol is defined** (not self-reference, not `__all__`).
   - **Outside test files** (no `test_*.py`, `conftest.py`, `.spec.ts`, `.test.js`).
   - **Traceable to a real trigger:**
     - Routing table (FastAPI `@app.get()`, Flask `@route`, etc.)
     - CLI argument parser registration (`click.command()`, `argparse`)
     - UI render tree (React `<Component>`, export in `index.ts`)
     - Plugin/extension-point registry (registration function call)
     - Cron/schedule config (e.g., `celery.beat.schedule`)
     - Message-bus subscription (e.g., event listener registration)
     - Module re-export in `__init__.py` that something else imports

3. **If zero call sites found:** **STOP.** This is not a warning to note—it is a **blocking finding.**
   - Do not proceed to Phase 2.
   - Dispatch `root-cause-by-layer`: Why is this unreachable?
     - Is the registration commented out?
     - Gated behind a feature flag that is never set to true?
     - Genuinely never wired into the codebase?
   - Fix the wiring. Do not lower the bar by writing a test for code nothing calls.

4. **Document the call site.** Note its file path and line number in your commit message or PR body.

**Example — what reachability looks like:**

```python
# ✅ GOOD: route explicitly registered
@app.post("/api/v1/new-endpoint")  # <— real trigger
def handle_new_endpoint():
    return new_feature_handler()  # <— reachable

# ❌ BAD: call site exists only in test file
def test_new_feature():
    result = new_feature_handler()  # <— not a real trigger

# ❌ BAD: registration is dead code
# @app.post("/api/v1/new-endpoint")  # <— commented out
def handle_new_endpoint():
    pass

# ❌ BAD: feature flag never turned on by default or operator
if EXPERIMENTAL_FEATURE_ON:  # <— flag ships as False
    @app.post("/api/v1/new-endpoint")
    def handle_new_endpoint():
        pass
```

---

### Phase 2: E2E Test (Mandatory, Only After Phase 1 Passes)

**Goal:** Write one E2E test that exercises the new code through its **real, user-facing entry point**—the same interface an operator or user would use.

**Core rule:** The test must NOT import the target function and call it directly. It must go through the real transport/interface boundary.

**Test layout by entry point type:**

| Entry Point Type | Real Interface to Use | Example |
|---|---|---|
| HTTP endpoint / REST route | Real HTTP request against a running server or test client | `requests.post()` or `client.post()` with a real route path |
| CLI command | Real subprocess invocation of the installed command | `subprocess.run(["corvin", "new-command", ...])` |
| UI component | Real browser interaction | Playwright: `.click()`, `.fill()`, `.goto()` on real URL |
| Plugin / extension point | Actual registry dispatch mechanism | Call the registry's `dispatch()` or `invoke()` method; don't call the plugin class directly |
| Bridge message handler | Real message through the adapter's ingestion path | Send a real message on the bridge; don't call the handler directly |
| MCP tool | MCP tool call via the real MCP server | `call_mcp_tool()` that goes through the server's JSON-RPC interface, not a direct Python call |

**Writing the E2E test:**

1. **Generate or extend one test** in an existing or new E2E test file (e.g., `test_e2e_new_feature.py`).

2. **Test must capture real execution evidence:**
   - HTTP: response status code, response body, response headers.
   - CLI: exit code, stdout, stderr.
   - UI: browser state after interaction (DOM selector, page title, console output).
   - Plugin: the returned value, side effects (audit log, database state).
   - Bridge: message delivered, state changed, audit event recorded.

3. **Test must fail if the code is unreachable or broken:**
   - If you comment out the registration/wiring, the test fails.
   - If you remove the function, the test fails.
   - If you break the logic, the test fails.

4. **Run the test and capture output.** A passing test is the proof.

**Example — what a real E2E test looks like:**

```python
# ✅ GOOD: real HTTP request
def test_new_api_endpoint_e2e():
    from fastapi.testclient import TestClient
    from my_app import app
    
    client = TestClient(app)
    response = client.post("/api/v1/new-endpoint", json={"data": "test"})
    
    assert response.status_code == 200
    assert response.json()["result"] == "expected_value"
    # Proof: the real route handler was invoked and returned the expected response.

# ❌ BAD: direct call, not E2E
def test_new_feature_direct():
    from my_module import new_feature_handler
    
    result = new_feature_handler({"data": "test"})
    
    assert result == "expected_value"
    # Not E2E: this doesn't prove the route handler is wired or the HTTP layer works.

# ✅ GOOD: real CLI subprocess
def test_new_cli_command_e2e():
    import subprocess
    
    result = subprocess.run(["corvin", "new-command", "--arg", "value"],
                           capture_output=True, text=True)
    
    assert result.returncode == 0
    assert "success" in result.stdout
    # Proof: the installed CLI command was invoked end-to-end.

# ✅ GOOD: real browser interaction
def test_new_ui_component_e2e(page):  # page fixture from Playwright
    page.goto("http://localhost:3000/new-page")
    
    button = page.locator("text=New Feature")
    button.click()
    
    response_text = page.locator("#result").text_content()
    assert response_text == "Feature works"
    # Proof: the component rendered, responded to interaction, and showed the result in the real browser.
```

---

## Infeasibility Exception

If the real entry point genuinely cannot be driven end-to-end (e.g., hardware-only code, unavailable external system):

1. Document the reason explicitly (never skip silently).
2. Phase 1 (reachability proof) **still applies unconditionally.**
3. State the exception in your commit message: `E2E proof infeasible: [reason]. Reachability verified at [file:line].`

**Example:**
```
Commit: add firmware-layer device-initialization hook

E2E proof infeasible: hardware-only, real device unavailable in CI.
Reachability verified: core/device/init.py:42 called by bootstrap_device() at
core/bootstrap.py:88, which runs on real hardware at install time.
```

---

## Multi-Surface Reachability

If a feature ships in two places (e.g., Console web-chat AND Discord bridge):

- Phase 1 must pass for **each** surface independently. Grep for both call sites.
- Phase 2 may write **one** E2E test if both surfaces use the same underlying handler (test once, proof applies to both).
- If surfaces have separate handlers, write **two** E2E tests (one per surface).

**Example:** If a new feature is wired into both Console (`web/handler.ts`) and Bridge (`adapter.py`):
- Reachability check: find call site in `web/handler.ts` AND call site in `adapter.py`.
- E2E test: write `test_e2e_console_new_feature()` and `test_e2e_bridge_new_feature()`.

---

## Packaged Artifact Reachability (Prerelease)

Before shipping a release (wheel, package, installer):

1. **Build the artifact** (e.g., `python -m build`, `npm run build`).
2. **List its contents** (e.g., `unzip -l dist/*.whl`, `ls -R dist/`).
3. **Verify the new feature's files are present:**
   - Module files, routes, CLI handlers, UI components—all expected files must be in the archive.
   - A packaging manifest is hand-maintained and drifts silently: never assume "the files are in the archive."

4. **Install into a fresh environment:**
   ```bash
   python -m venv /tmp/test-env
   source /tmp/test-env/bin/activate
   pip install dist/corvin-*.whl
   ```

5. **Run a smoke test against the installed package:**
   ```bash
   # Check that the feature reports itself available
   corvin show-features | grep new-feature
   # Should list the new feature as registered
   ```

6. **Run the Phase 2 E2E test against the installed environment.**
   - Test passes → feature is reachable in the shipped artifact.
   - Test fails → feature is missing from the archive (common: untracked files filtered by manifest, or import path drifted).

**Why this matters:**
- Unit tests pass against the source tree, where everything is available.
- A green test suite does not prove the artifact is complete.
- Before release: build the artifact, install it, test against the installed version, not the source tree.

---

## When This Standard Applies

**Mandatory gates:**

| Task | When E2E Proof Fires |
|---|---|
| New HTTP endpoint or route | Always |
| New CLI command or flag | Always |
| New UI page or interactive component | Always |
| New plugin, hook, or extension point | Always |
| New bridge message handler | Always |
| New scheduled job (cron, timer) | Always |
| New entry point in any form | Always |

**Optional (use your judgment):**

| Task | E2E Proof Applies? |
|---|---|
| Internal helper function with existing E2E coverage (existing caller's test exercises it) | No—existing test is sufficient |
| Pure refactor, no behavior change | No—existing E2E coverage still applies |
| Config value or threshold tuning | No |
| Bug fix with no new entry point | No—existing test is sufficient |
| Code explicitly marked "WIP prototype, not wired yet" | No—but update the status when you wire it |

---

## Burden and Payoff

**Burden:**
- Phase 1 reachability check: ~5–10 minutes (grep + grep again + grep to verify).
- Phase 2 E2E test: ~15–30 minutes for a simple feature (more for complex interactions).
- **Total:** ~30–40 minutes per entry point, once per commit.

**Payoff:**
- **No dead code shipped.** Zero instances of "registered but never called."
- **Faster debugging.** If a feature doesn't work, you know the code is wired; the bug is in logic, not routing.
- **Honest test coverage.** A test that doesn't exercise the real entry point doesn't count.
- **Cross-phase consistency.** Every phase uses the same standard; no hidden reachability assumptions between phases.

---

## Related Skills & Concepts

- **`assistant.e2e_wiring_proof`** — SkillForge skill, auto-injected on entry-point tasks (learned-experience, project scope).
- **CONCEPT-0008** — "Reachability review axis" in `Corvin-ADR/concepts/`; full narrative + evidence + alternatives.

---

## FAQ

**Q: Does this mean I have to write an HTTP test for every endpoint?**
A: Yes. "Real HTTP request" means you cannot mock the handler. But it is fast—`TestClient` from FastAPI or Django makes it simple. A test that imports the handler and calls it directly does not satisfy this gate.

**Q: What if the feature is only used internally, not by operators?**
A: If it is intended to be called from somewhere outside its own file, it must satisfy this gate. "Internal" means the code is not customer-facing, but it is still reachable. Reachability proof still applies.

**Q: Can I use mocks?**
A: No mocks of the component under test or its direct trigger. You can mock external dependencies (databases, APIs, file system) that the component calls. But the entry point itself and the dispatch mechanism must be real.

**Q: What if reachability proof fails and I can't fix it in time?**
A: Do not lower the bar. Either:
1. Fix the wiring (usually ~30 min for a simple feature).
2. Mark the feature as dormant/WIP, document that explicitly, and ship it under a feature flag that is OFF by default (fail-closed).
3. Do not ship unreachable code as "done."

**Q: Does this apply to my current code, or only new code?**
A: New code and changed entry points. Existing code without E2E coverage can be refactored into coverage incrementally; you don't need to retrofit every old endpoint at once. But any new endpoint must satisfy this gate before its commit lands.

---

**See also:**
- `CLAUDE.md` § "E2E Wiring Proof"
- `e2e-driven-iteration` skill (loop-driven-engineering)
- CONCEPT-0008 in `Corvin-ADR/concepts/` (full evidence trail)

