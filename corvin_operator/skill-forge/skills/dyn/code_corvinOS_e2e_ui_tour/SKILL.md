---
name: code_corvinOS_e2e_ui_tour
description: Phase 5-6 of CorvinOS E2E: Playwright full-UI discovery tour — every page loaded, every tab/button/dropdown clicked, mobile viewport check, per-engine model config tested. Called from code.corvinOS_e2e_fresh_install.
---

---
name: code.corvinOS_e2e_ui_tour
type: domain
description: "Phase 5-6: Playwright full-UI discovery tour — every page, every interactive element, mobile viewport, per-engine model config. Called from code.corvinOS_e2e_fresh_install."
references: []
---

# CorvinOS E2E — Full WebUI Discovery Tour (Phases 5–6)

Part of the `code.corvinOS_e2e_fresh_install` suite. Assumes Phase 4 (login + engine selection) is already complete.

## Phase 5 — Navigation and Page Load Check

For EVERY page listed below, Playwright must:
1. Navigate to the URL
2. Wait for the primary heading/title selector to be visible
3. Screenshot `<page_name>_load.png`
4. Record any JS errors (none is the baseline)
5. Assert no blank white sections (no zero-height containers that should have content)

Required pages:
`/app/dashboard`, `/app/engines`, `/app/engine-control`, `/app/personas`,
`/app/tasks`, `/app/forge`, `/app/skill-forge`, `/app/settings`,
`/app/audit`, `/app/compliance`, `/app/people`, `/app/data-sources`,
`/app/cowork`, `/app/license`

**Mobile viewport check (390 × 844):**
On each page: sidebar must collapse, hamburger menu present and functional, no horizontal scrollbar.

---

## Phase 6 — Full Interactive Discovery (every element)

Act as a user exploring each page for the first time. Click systematically.

### `/app/engines`
- Click each of the 5 engine cards → card gets selected visual state
- For Hermes card: Ollama reachability badge visible (reachable or "not running")
- Open **Per-Engine Model Configuration** section → change Claude Code worker model to `claude-opus-4-8` → Save → reload → assert `claude-opus-4-8` is shown selected
- Screenshot `engines_model_config.png`

### `/app/engine-control`
- Click each engine column header in capability matrix → ECI panel updates for that engine
- Verify matrix has ≥5 capability rows and ≥3 engine columns with non-empty cells
- Screenshot `engine_control_matrix.png`

### `/app/personas`
- Click each visible persona card → detail view opens showing name, description, permission_mode
- Edit a non-critical field (e.g. description suffix) in `assistant` → Save → toast appears within 3 s → reload → change persisted
- Create new persona: name `e2e_test_persona`, permission_mode `default` → Save → appears in list
- Delete `e2e_test_persona` → disappears from list
- Screenshot `personas_create_delete.png`

### `/app/forge`
- Click "Create tool" (or equivalent) → fill: name `e2e.ui_smoke`, description `UI smoke test`, minimal body
- Save → tool appears in list → click it → detail view opens
- Screenshot `forge_list.png`
- Delete the tool → disappears

### `/app/skill-forge`
- Create skill: name `e2e.ui_smoke_skill`, body `When asked to greet, say hello.`
- Appears in list → click → body visible
- Submit skill with empty body → inline validation error appears, NOT a generic 500
- Delete the skill
- Screenshot `skill_forge_validation.png`

### `/app/settings`
- Click every tab visible (General, Security, Voice, Integrations, etc.)
- Each tab renders without blank area or 500
- Change one non-critical setting (e.g. rate_limit_per_hour if present) → Save → reload → persisted
- Screenshot `settings_all_tabs.png`

### `/app/audit`
- At least 5 audit events visible with timestamps and event types
- Filter by event type (if filter UI exists) → filtered results change
- Spot-check 3 rendered event rows: no email addresses, no API tokens, no file paths in detail column
- Screenshot `audit_filtered.png`

### `/app/compliance`
- Summary section visible with ≥3 compliance mechanism rows
- No missing/broken icons in the compliance table
- Screenshot `compliance_summary.png`

### `/app/data-sources`
- Page loads with "Add data source" button (or equivalent) visible
- Screenshot `data_sources_empty.png`

### `/app/people`
- User/role list renders, current user visible with their role
- Screenshot `people_roles.png`

---

## Discovery Quality Signals

For each page, record:
- **Time-to-interactive** (ms from navigate to first heading visible)
- **JS errors count**
- **Empty-state quality**: if a list page has no items, is the empty state clear and actionable?
- **Mobile breakpoint**: does layout collapse correctly?

Add each UX friction point to the report as HIGH/MEDIUM/LOW with description.
