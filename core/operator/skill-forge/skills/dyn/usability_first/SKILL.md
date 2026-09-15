---
name: usability_first
description: Usability-First discipline — apply to any user-facing surface (web UI, CLI output, Discord, error messages, onboarding). Ensures non-technical users can complete tasks without developer help. Covers plain language, single clear path, feedback, consistency, mobile, and accessibility baseline.
---

# Usability First — Simplicity & User-Friendliness discipline

Apply this discipline to any task that produces or modifies a user-facing surface: web UI, Discord messages, CLI output shown to end users, error messages, onboarding flows, labels, tooltips, help text, or any text a non-developer might read.

## The core principle

**The target user is NOT a developer.** They may be a small business owner, a team admin, or an end user who found Corvin through a recommendation. If they need to read documentation or ask a developer for help to complete a basic task, the design has failed.

Simplicity is not about fewer features — it is about making the right path so obvious that no explanation is needed.

---

## Checklist — run before shipping any user-facing change

### 1. Language — plain, not technical

- No internal jargon visible to users: no layer names (L16, L34), no acronyms (HMAC, bwrap, ADR), no UUIDs shown raw
- Error messages state **what went wrong** AND **what the user should do next** — never just "Error" or an HTTP status code
- Button text is a specific verb: "Save settings", "Connect account", "Delete conversation" — never just "Submit" or "OK"
- Confirmation dialogs explain the **consequence**, not just the action: "This will permanently delete all your conversations. You cannot undo this." — not "Are you sure?"
- Pick one word per concept and use it everywhere: either "conversation" or "session", never both

### 2. Flow — one clear path

- Each screen or step has **one obvious primary action** — if everything looks equally important, nothing stands out
- Advanced or rarely-needed options are collapsed, in a details section, or on a separate settings page — not in the default view
- No dead ends: every error state has a visible way forward ("Try again", "Go back", "Contact support")
- Empty states tell the user **what goes here and how to fill it** — not just a blank page or "No data found"

### 3. Feedback — the user always knows what is happening

- Anything taking more than 300 ms shows a loading indicator
- Every successful action is confirmed visibly: "Settings saved", "Account connected"
- Failures name the specific problem: "Could not connect — check your internet connection" not "Request failed"
- Destructive or irreversible actions require explicit confirmation before executing

### 4. Consistency — same patterns everywhere

- Button hierarchy: one primary action (filled, prominent) · secondary actions (outlined) · destructive actions (distinct warning style)
- Same navigation structure, same back-button behavior, same terminology across all pages and flows
- Icons always paired with a text label — icons alone are ambiguous for new users

### 5. Mobile-first

- Every screen works on a 375 px wide phone screen — test this before marking done
- Touch targets at least 44 × 44 px
- No horizontal scrolling for main content
- No hover-only interactions (hover does not exist on touch screens)

### 6. Accessibility baseline

- All images and icons have `alt` text or `aria-label`
- Color is never the **only** signal — use color plus icon or text together
- Form fields have visible labels, not just placeholder text (placeholder disappears on input)
- All interactive elements are reachable and operable by keyboard

---

## Anti-patterns — never ship these

| Anti-pattern | Why it fails |
|---|---|
| Error says only "Something went wrong" | User has no idea what to do next |
| Confirmation dialog says only "Are you sure?" | User does not know what they are confirming |
| Form with more than 5 visible fields | Overwhelming; use steps or collapsible sections |
| Button labeled "Submit" | Generic; use a specific action verb |
| Raw UUID or internal ID shown to user | Meaningless and scary; show a human-readable name |
| Empty page with no explanation | User thinks it is broken |
| Feature flags or dev options visible to standard users | Confusing clutter |
| Setting described in technical terms | Translate to what it does for the user |

---

## The first-timer test

Before marking a UI task done, ask:

*"Could someone who has never seen this product complete this task in under two minutes, without asking anyone for help?"*

- Yes → ship it
- No → find the blocking moment and fix it first; do not ship and "document around it"

---

## When this discipline fires

- Any new page, modal, or form in the web console
- Any new Discord command or its response format
- Any CLI output that a non-developer might read
- Any error message, notification, or status text
- Any onboarding, setup, or first-run flow
- Any settings UI or configuration surface

When the task is purely backend with zero user-visible output, this discipline does not apply — note that in one sentence and move on.
