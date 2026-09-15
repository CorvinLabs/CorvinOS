---
name: code_corvinOS_e2e_integration
description: Phase 7-13 of CorvinOS E2E: live chat synthetic tasks + output comparison, real Discord adapter smoke, SQLite data connector, Forge tool execution, Skill injection round-trip, A2A check, compliance PII scan. Called from code.corvinOS_e2e_fresh_install.
---

---
name: code.corvinOS_e2e_integration
type: domain
description: "Phase 7-13: live chat synthetic tasks + output comparison, Discord adapter smoke, SQLite data connector, Forge tool execution, Skill injection round-trip, A2A, compliance PII scan."
references: []
---

# CorvinOS E2E — Integration Tests (Phases 7–13)

Part of `code.corvinOS_e2e_fresh_install`. Assumes Phase 4 (login, Claude Code active) is complete.

**Screenshot rule (applies to ALL phases):** After every action that produces a visible result, take a screenshot and record its filename in the report. Visually inspect each screenshot: does it match what was expected? Flag any visual anomaly (missing content, wrong color, broken layout) as a finding even if the assertion passed.

---

## Phase 7 — Live Chat: Synthetic Tasks + Output Comparison

Open the main chat UI. Claude Code must be the active engine.
Send each task, wait ≤60 s for a response, compare output, screenshot after each.

| # | Prompt (send exactly) | Expected | Assert | Screenshot |
|---|-----------------------|----------|--------|------------|
| T1 | `What is 17 × 23? Reply with only the number.` | `391` | response.strip() == "391" | `chat_T1.png` |
| T2 | `List the 8 planets, one per line, no other text.` | 8 lines | line_count == 8 | `chat_T2.png` |
| T3 | `Write a Python function that returns the sum of a list. Code only.` | def + return | contains `def` and `return` | `chat_T3.png` |
| T4 | `Today's date in ISO 8601 only (YYYY-MM-DD).` | date | matches `\d{4}-\d{2}-\d{2}` | `chat_T4.png` |
| T5 | `Translate "hello world" to German. Translation only.` | Hallo Welt | icontains `hallo` | `chat_T5.png` |

Record per task: prompt, response text (first 200 chars), response time ms, PASS/FAIL.
**Visual check for each screenshot:** AI response area is populated, no spinner stuck, no error banner.

**Command tests (same session, screenshot each):**
- `/new` → session resets, chat clears. Screenshot `chat_new.png`. Visual check: messages gone.
- `/btw test injection` → no crash. Screenshot `chat_btw.png`.
- `/engine claude_code` → confirmation or silent. Screenshot `chat_engine.png`.
- `/quota` → quota count or "unlimited" visible. Screenshot `chat_quota.png`.
- `/role` → role string visible. Screenshot `chat_role.png`.

---

## Phase 8 — Real Adapter Integration (Discord)

```bash
DISCORD_TOKEN="${DISCORD_TOKEN:-dummy_smoke_token}" \
  timeout 15 python -m operator.bridges.discord.adapter --dry-run 2>&1 \
  | tee "$TESTDIR/discord_dryrun.log"
```

**Assert on log:**
- No `ImportError` or `ModuleNotFoundError`
- No CRITICAL self-test failure unrelated to the dummy token
- At least one of: `settings loaded`, `whitelist`, `bot-disclosure` appears
- Process exits 0 or 1 with a clean auth error (NOT an unhandled Python exception)

Screenshot of log tail (first 30 lines): `adapter_dryrun.png`.
**Visual check:** log shows structured startup messages, not a traceback.

**If DISCORD_TOKEN is a real token:**
- Start bridge for 30 s, inject test message via Discord API, assert AI response arrives within 45 s
- Assert first response from new user includes bot-disclosure card
- Send `/leave` to clean up
- Screenshot `adapter_live_response.png`. Visual check: response text present, no error embed.

---

## Phase 9 — SQLite Data Connector E2E

Create synthetic database:
```python
import sqlite3, pathlib, os
db = pathlib.Path(os.environ["TESTDIR"]) / "test_customers.sqlite"
conn = sqlite3.connect(db)
conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, revenue REAL)")
conn.executemany("INSERT INTO customers VALUES (?,?,?,?)", [
  (1,"Alice Corp","alice@example.invalid",125000),
  (2,"Bob GmbH","bob@example.invalid",89000),
  (3,"Carol Ltd","carol@example.invalid",210000),
])
conn.commit(); conn.close()
```

**Playwright on `/app/data-sources`:**
1. "Add data source" → fill name `e2e_test_db`, type `sqlite`, path = db path
2. "Test connection" → green indicator within 5 s. Screenshot `ds_connected.png`. Visual check: green/success state.
3. Save → entry in list. Screenshot `ds_list.png`.
4. If preview UI exists: open → ≥3 rows visible. Screenshot `ds_preview.png`. Visual check: table has data, no email raw text visible (L32 PII).
5. Delete → entry disappears. Screenshot `ds_deleted.png`. Visual check: list is empty or connector gone.

---

## Phase 10 — Forge Tool Real Execution

**Create tool `e2e.csv_summarizer`:**
- Input: `csv_text: string`
- Implementation: parse CSV → return `{row_count: int, columns: [str]}`
- Runtime: `python`

**Execute** with input:
```
name,score
Alice,95
Bob,87
Carol,92
```

**Assert:**
- `row_count == 3`
- `columns` contains `name` and `score`
- Output is valid JSON, not an error message

Screenshot `forge_execute_result.png`. Visual check: JSON result visible in output area, no error banner.
Record execution time ms.

---

## Phase 11 — Skill Injection Round-Trip

1. Create skill `e2e.ping_pong`: body = `When the user sends exactly "e2e-ping", reply with exactly "e2e-pong" and nothing else.`
2. Grade it with score 0.9 (triggers injection gate)
3. In live chat: send `e2e-ping`, wait ≤60 s
4. **Assert:** response.strip().lower() == "e2e-pong"
5. Screenshot `skill_injection_pass.png`. Visual check: response area shows "e2e-pong", no extra text.
6. Delete the skill
7. Send `e2e-ping` again → response must NOT be "e2e-pong"
8. Screenshot `skill_injection_removed.png`. Visual check: response is a normal AI reply.

If step 4 fails: CRITICAL finding. The skill injection pipeline is broken.

---

## Phase 12 — A2A Remote Trigger

If `$CORVIN_HOME/cowork/remote_origins/` contains ≥1 origin file:
- `GET /v1/console/remote-trigger/origins` → 200, ≥1 entry. Screenshot `a2a_origins.png`.
- `GET /v1/console/remote-trigger/log` → 200, JSON array. Screenshot `a2a_log.png`.
- Visual check: both pages show structured data, no 500 error page.

Else: `SKIPPED — no A2A origins in test tenant`.

---

## Phase 13 — Compliance Spot-Check

**Audit chain:**
```bash
voice-audit verify
```
Exit 0 required. If non-zero: CRITICAL — record exact output.

**PII scan in audit log:**
```bash
grep -rE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' \
  "$CORVIN_HOME/tenants/_default/global/audit.jsonl" | head -5
```
Any match = CRITICAL finding. Record matching line, redact the actual address.

**Consent gate (if consent API present):**
- POST to consent endpoint with unknown uid → must return `denied`, not auto-admit

Screenshot `compliance_audit_verify.png` (terminal output of voice-audit verify).
Visual check: "OK" or success indicator, no error lines.
