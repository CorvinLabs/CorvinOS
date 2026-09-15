---
name: code_corvinOS_e2e_fresh_install
description: Orchestrator: fresh pip install → bootstrap → Claude Code engine selection → calls e2e_ui_tour and e2e_integration skills → teardown + report with screenshots + visual verification of every result.
---

---
name: code.corvinOS_e2e_fresh_install
type: domain
description: "Orchestrator: fresh pip install → bootstrap → Claude Code engine selection → e2e_ui_tour + e2e_integration → teardown + full report. Global screenshot + visual-verification rule applies to every step."
references: []
---

# CorvinOS — E2E Fresh-Install Orchestrator

Companion skills: `code.corvinOS_e2e_ui_tour` (Phase 5-6) and `code.corvinOS_e2e_integration` (Phase 7-13).
Run all three together in order. Never skip a phase silently — write `SKIPPED — <reason>` in the report.

---

## GLOBAL RULE: Screenshot + Visual Verification

**This rule applies to every single step in every phase, without exception.**

1. After every action that produces a visible result — page load, button click, form save, AI response, command output — **take a screenshot immediately**.
2. Name it descriptively: `phase<N>_<action>.png`, e.g. `phase4_login_success.png`.
3. **Visually inspect** the screenshot. Ask: does this look like a working, correct result?
   - Expected: content visible, correct data shown, no error banners, no blank areas that should have content, colors/layout intact
   - Flag any visual anomaly as a **VISUAL finding** in the report, even if the code assertion passed
4. All screenshots go to `$TESTDIR/outputs/screenshots/` and are listed in the final report.
5. When the result is an AI response in chat: screenshot the entire response bubble, then compare both the visual appearance AND the text content against what was expected.

The goal is that someone reviewing the screenshots alone (without reading the assertions) can confirm the test passed.

---

## Guiding principle

You are a first-time operator who just ran `pip install corvinOS`. At every step:
*"Would a user with no prior knowledge get through this without help?"*
Nothing is mocked. Real AI responses, real adapters, real databases.

---

## Phase 0 — Isolation

```bash
TESTDIR=$(mktemp -d /tmp/corvin-e2e-XXXX)
python3 -m venv "$TESTDIR/venv"
source "$TESTDIR/venv/bin/activate"
export CORVIN_HOME="$TESTDIR/.corvin"
export CORVIN_TENANT_ID=_default
export CORVIN_CONSOLE_PORT=18765
mkdir -p "$TESTDIR/outputs/screenshots"
```

Screenshot `phase0_isolation.png` of terminal with the TESTDIR and venv path visible.
Record: Python version, CORVIN_HOME.

---

## Phase 1 — Installation

```bash
pip install -e ".[console]" --quiet
```

**STOP if any of these fail:**
- `corvin --version` exits 0, prints semver. Screenshot output.
- `python -c "from corvin_console import app"` succeeds
- `python -c "from corvin_console.routes import engine"` succeeds
- `python -c "from operator.bridges.shared import adapter"` succeeds

Screenshot `phase1_version.png` (terminal showing version string).
**UX check:** Without ANTHROPIC_API_KEY, run `corvin --help`. Output must name the exact env var. Screenshot `phase1_ux_missing_key.png`. Visual check: is the error message clear and actionable?

---

## Phase 2 — Bootstrap and Self-Test

```bash
python -m corvin_console.main &
CONSOLE_PID=$!
for i in $(seq 1 20); do
  curl -sf http://localhost:18765/health && break; sleep 0.5
done
```

Screenshot `phase2_health.png` (curl output showing `{"ok": true}`).
**Checks:**
- `/health` returns `{"ok": true}` — STOP + screenshot if 5xx
- `bridge.sh doctor --quick 2>&1` — screenshot output, record CRITICAL/WARNING lines
- `voice-audit verify` exits 0 — CRITICAL if non-zero, screenshot output
- `$CORVIN_HOME/tenants/_default/global/audit.jsonl` exists, ≥1 line

---

## Phase 3 — Playwright Setup

```bash
pip install playwright --quiet && playwright install chromium --quiet
```

All tests: `http://localhost:18765`, 15 s timeout, video recording on.
Capture JS console errors per page into `js_errors.log`.

---

## Phase 4 — Login + Engine Selection

Screenshot every sub-step:
- `phase4_redirect.png` — `GET /` must redirect to `/app/login`
- `phase4_login_error.png` — wrong credentials → error visible within 3 s
- `phase4_dashboard.png` — successful login → `/app/dashboard` within 5 s

**Engine Selection (immediately after login):**
1. Navigate to `/app/engines`
2. Click **Claude Code** engine card → selected state
3. Click **Save engine**
4. Reload → assert Claude Code shows active
5. Screenshot `phase4_engine_selected.png`. Visual check: Claude Code card has the selected/active highlight, not another engine.

---

## Phase 14 — Teardown and Report

```bash
kill $CONSOLE_PID 2>/dev/null; deactivate
```

Save `./outputs/e2e_report_<timestamp>.md`:

```markdown
# CorvinOS E2E Report — <timestamp>
Python: __   Install: __s   Total: __s   First AI response: __s

## Phase Results
| Phase | Name                       | Result    | Visual | Notes |
|-------|----------------------------|-----------|--------|-------|
| 0     | Isolation                  | PASS/FAIL | OK/NOK |       |
| 1     | Installation               | PASS/FAIL | OK/NOK |       |
| 2     | Bootstrap                  | PASS/FAIL | OK/NOK |       |
| 4     | Login + Engine Selection   | PASS/FAIL | OK/NOK |       |
| 5     | Navigation Tour            | PASS/FAIL | OK/NOK |       |
| 6     | Full UI Discovery          | PASS/FAIL | OK/NOK | N issues |
| 7     | Synthetic Chat Tasks       | PASS/FAIL | OK/NOK | T1-T5 |
| 8     | Adapter Integration        | PASS/SKIP | OK/NOK |       |
| 9     | SQLite Data Connector      | PASS/FAIL | OK/NOK |       |
| 10    | Forge Tool Execution       | PASS/FAIL | OK/NOK |       |
| 11    | Skill Injection Round-Trip | PASS/FAIL | OK/NOK |       |
| 12    | A2A Remote Trigger         | PASS/SKIP | OK/NOK |       |
| 13    | Compliance Spot-Check      | PASS/FAIL | OK/NOK |       |

## Synthetic Task Output Comparison
| Task | Expected | Actual | Time ms | Assert | Visual |
|------|----------|--------|---------|--------|--------|

## Visual Findings (anomalies seen in screenshots)
- phase____.png: <description of what was unexpected>

## UX Findings (new-user friction)
- HIGH / MEDIUM / LOW: <description>

## JS Errors | CRITICAL Self-Test Items
...

## All Screenshots
(full list with filename → what it shows)

## Time-to-Working
Install: __s  Bootstrap: __s  First login: __s  First AI response: __s
```

All files in `./outputs/` — auto-attached to Discord reply.

---

## STOP conditions

| Condition | Action |
|-----------|--------|
| Phase 1 import fails | Stop, screenshot + traceback |
| Phase 2 /health 5xx | Stop, screenshot + last 100 log lines |
| Login page missing | Stop, screenshot what was visible |
| voice-audit non-zero | CRITICAL, screenshot, continue |
| PII in audit.jsonl | CRITICAL, continue |
| Skill injection fails | CRITICAL, screenshot, continue |
