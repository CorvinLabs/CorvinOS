# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: github-integration.spec.ts >> GitHub Integration E2E >> should show disconnected state initially
- Location: tests/e2e/github-integration.spec.ts:24:7

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('text=Not connected')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for locator('text=Not connected')
    - waiting for navigation to finish...
    - navigated to "http://127.0.0.1:8765/console/"

```

```yaml
- complementary:
  - link "Corvin Operator Console":
    - /url: /console
  - navigation:
    - link "Chat":
      - /url: /console/app/chat
      - img
      - text: Chat
    - link "Dashboard":
      - /url: /console/app/dashboard
      - img
      - text: Dashboard
    - button "Vibe Engineering":
      - text: Vibe Engineering
      - img
    - link "Vibe Dashboard":
      - /url: /console/app/vibe-engineering
      - img
      - text: Vibe Dashboard
    - button "Observability":
      - text: Observability
      - img
    - link "Learning Dashboard":
      - /url: /console/app/learning-dashboard
      - img
      - text: Learning Dashboard
    - text: Messaging
    - link "Channels":
      - /url: /console/app/bridges
      - img
      - text: Channels
    - link "Profile":
      - /url: /console/app/voice
      - img
      - text: Profile
    - link "People":
      - /url: /console/app/people
      - img
      - text: People
    - text: Assistant
    - link "AI Engine":
      - /url: /console/app/engines
      - img
      - text: AI Engine
    - link "Browser":
      - /url: /console/app/browser
      - img
      - text: Browser
    - link "Personas":
      - /url: /console/app/personas
      - img
      - text: Personas
    - link "Memory":
      - /url: /console/app/memory
      - img
      - text: Memory
    - link "Files":
      - /url: /console/app/files
      - img
      - text: Files
    - button "Build":
      - text: Build
      - img
    - link "Workflows":
      - /url: /console/app/workflows
      - img
      - text: Workflows
    - link "Pipelines":
      - /url: /console/app/flows
      - img
      - text: Pipelines
    - link "Agentic Compute":
      - /url: /console/app/compute
      - img
      - text: Agentic Compute
    - link "Tools":
      - /url: /console/app/forge
      - img
      - text: Tools
    - link "Skills":
      - /url: /console/app/skills
      - img
      - text: Skills
    - link "OS Skills":
      - /url: /console/app/os-skills
      - img
      - text: OS Skills
    - link "Packages":
      - /url: /console/app/packages
      - img
      - text: Packages
    - link "Agents":
      - /url: /console/app/agents
      - img
      - text: Agents
    - link "Plugins & Extensions":
      - /url: /console/app/plugin-center
      - img
      - text: Plugins & Extensions
    - link "Marketplace":
      - /url: /console/app/marketplace
      - img
      - text: Marketplace
    - button "Network":
      - text: Network
      - img
    - link "Agent Hub":
      - /url: /console/app/agent-hub
      - img
      - text: Agent Hub
    - link "CorvinSpace":
      - /url: /console/app/space
      - img
      - text: CorvinSpace
    - link "Organisations":
      - /url: /console/app/orgs
      - img
      - text: Organisations
    - link "Connectors":
      - /url: /console/app/connectors
      - img
      - text: Connectors
    - link "Sync Monitor":
      - /url: /console/app/sync-monitor
      - img
      - text: Sync Monitor
    - link "Webhooks":
      - /url: /console/app/webhooks
      - img
      - text: Webhooks
    - button "Data":
      - text: Data
      - img
    - link "Databases":
      - /url: /console/app/data-sources
      - img
      - text: Databases
    - link "Knowledge":
      - /url: /console/app/rag
      - img
      - text: Knowledge
    - link "Knowledge Hub":
      - /url: /console/app/rag-hub
      - img
      - text: Knowledge Hub
    - link "Add Provider":
      - /url: /console/app/custom-provider
      - img
      - text: Add Provider
    - button "System":
      - text: System
      - img
    - text: Your panels
    - link "Task Graph — Redesigned":
      - /url: /console/app/task-graph-redesigned
      - img
      - text: Task Graph — Redesigned
  - link "Licence Free":
    - /url: /console/app/license
  - text: _default owner
  - button "Log out":
    - img
- banner:
  - text: _default
  - link "Claude Code":
    - /url: /console/app/engines
    - img
    - text: Claude Code
  - button "Corvin Assistant": Assistant
  - 'button "Theme: Dark theme (click to switch)"':
    - img
- main:
  - main:
    - text: NordTech Solutions GmbH — KI-Kommandozentrale web:W81tTu42-G 6 turns
    - button "Audit":
      - img
      - text: Audit
    - button "Claude Code":
      - img
      - text: Claude Code
      - img
    - button "Recap the whole session":
      - img
    - button "Voice on":
      - img
      - text: Voice on
    - button "Open session artifacts folder":
      - img
    - paragraph: "Hallo! Ich bin das NordTech KI-System. Beantworte bitte auf Deutsch: In welchem Bundesland liegt Berlin?"
    - img
    - img
    - paragraph:
      - text: Berlin ist selbst ein Bundesland. Berlin ist ein sogenannter Stadtstaat und einer der 16 Bundesländer der Bundesrepublik Deutschland. Es liegt nicht
      - emphasis: in
      - text: einem anderen Bundesland, sondern ist eine eigenständige Verwaltungseinheit im Nordosten Deutschlands.
    - button "Play":
      - img
    - text: 0:00
    - slider: "0"
    - text: 0:36
    - button "Mute":
      - img
    - slider: "1"
    - img
    - text: 717ab3abe9a47abd.mp3 voice 566.6 KB
    - link "Download file":
      - /url: /v1/console/chat/sessions/W81tTu42-GdqQXZowDIBaw/workdir/voice/717ab3abe9a47abd.mp3
      - img
    - paragraph: "Danke! Noch eine kurze Folgefrage: Nenne mir genau eine bekannte Sehenswürdigkeit in der Stadt, von der du gerade gesprochen hast."
    - img
    - img
    - paragraph: Das Brandenburger Tor ist eine der bekanntesten Sehenswürdigkeiten in Berlin. Es ist ein historisches Denkmal aus dem 18. Jahrhundert und eines der Wahrzeichen Deutschlands.
    - button "Play":
      - img
    - text: 0:00
    - slider: "0"
    - text: 0:17
    - button "Mute":
      - img
    - slider: "1"
    - img
    - text: 9287429e90f9250e.mp3 voice 276.4 KB
    - link "Download file":
      - /url: /v1/console/chat/sessions/W81tTu42-GdqQXZowDIBaw/workdir/voice/9287429e90f9250e.mp3
      - img
    - paragraph: "NordTech-Aufgabe #4721: Schreibe bitte eine Python-Funktion `calculate_mrr(subscriptions: list[dict]) -> float` die aus einer Liste von Abonnements mit dem Feld `monthly_amount` den Monthly Recurring Revenue berechnet. Kommentiere die Funktion auf Deutsch."
    - img
    - img
    - paragraph: "NordTech Q3-Analyse: Hier sind unsere monatlichen Umsatzzahlen in EUR: Jan=87400, Feb=91200, Mär=88750, Apr=95600, Mai=103200, Jun=98400, Jul=112000, Aug=108900, Sep=118500, Okt=124300, Nov=131000, Dez=145600. Bitte berechne: (1) Gesamtjahresumsatz, (2) Durchschnitt pro Monat, (3) stärkstes Wachstumsmonat, (4) Wachstumsrate Q1→Q4 in Prozent. Präsentiere die Ergebnisse strukturiert auf Deutsch."
    - img
    - img
    - paragraph: "NordTech HR-System: Erstelle bitte eine CSV-Datei mit 8 fiktiven Mitarbeitern für unser Berliner Büro. Felder: mitarbeiter_id,name,abteilung,eintrittsdatum,gehalt_eur. Abteilungen: Engineering, Product, Sales, Operations. Nutze realistische deutsche Namen und Gehälter (50k–120k EUR)."
    - img
    - img
    - paragraph: NordTech Berlin HR roster (8 employees, CSV format) — realistic German names, 50–120k EUR salary band, 2019–2026 hire dates, balanced across Engineering/Product/Sales/Operations.
    - text: "{ \"workflow_id\": \"web-chat-delegation\", \"run_id\": \"acs-web-1788654336-96e95b\", \"iteration\": 1, \"artifacts\": [ \"output/nordtech_employees.csv\" ], \"quality_score\": 0.98 }"
    - img
    - text: manifest.json live 0.2 KB
    - link "Download file":
      - /url: /v1/console/chat/sessions/W81tTu42-GdqQXZowDIBaw/workdir/acs/runs/acs-web-1788654336-96e95b/output/FINAL/manifest.json
      - img
    - button "Play":
      - img
    - text: 0:00
    - slider: "0"
    - text: 1:07
    - button "Mute":
      - img
    - slider: "1"
    - img
    - text: 00ba11b656c6de76.mp3 voice 1054.1 KB
    - link "Download file":
      - /url: /v1/console/chat/sessions/W81tTu42-GdqQXZowDIBaw/workdir/voice/00ba11b656c6de76.mp3
      - img
    - paragraph: "NordTech Sprint #23 Planning: Unser Engineering-Team (6 Personen, 85 SP Kapazität) soll folgende Epics priorisieren: (A) API-Authentifizierung OAuth2 [34 SP], (B) Dashboard-Performance-Optimierung [21 SP], (C) Automatisierte Backup-Pipeline [13 SP], (D) Kundenportal v2.0 Beta [55 SP], (E) Security-Audit-Remediation [8 SP]. Erstelle einen priorisierten Sprint-Backlog mit Begründung."
    - img
    - img
    - 'heading "NordTech Sprint #23 — Priorisierter Backlog (85 SP Kapazität)" [level=2]'
    - paragraph:
      - strong: "Sprint-Auswahl (76 SP):"
    - table:
      - rowgroup:
        - row "Rang Epic SP Begründung":
          - columnheader "Rang"
          - columnheader "Epic"
          - columnheader "SP"
          - columnheader "Begründung"
      - rowgroup:
        - 'row "1 (E) Security-Audit-Remediation 8 KRITISCH: Compliance-blocker, klein, Risk-Reduktion → sofort"':
          - cell "1"
          - cell "(E) Security-Audit-Remediation":
            - strong: (E) Security-Audit-Remediation
          - cell "8"
          - 'cell "KRITISCH: Compliance-blocker, klein, Risk-Reduktion → sofort"':
            - strong: KRITISCH
            - text: ": Compliance-blocker, klein, Risk-Reduktion → sofort"
        - 'row "2 (A) OAuth2-Authentifizierung 34 Foundational: blockiert (D) Kundenportal, Sicherheit, mittlere Komplexität → Woche 1–2"':
          - cell "2"
          - cell "(A) OAuth2-Authentifizierung":
            - strong: (A) OAuth2-Authentifizierung
          - cell "34"
          - 'cell "Foundational: blockiert (D) Kundenportal, Sicherheit, mittlere Komplexität → Woche 1–2"':
            - strong: Foundational
            - text: ": blockiert (D) Kundenportal, Sicherheit, mittlere Komplexität → Woche 1–2"
        - 'row "3 (C) Backup-Pipeline 13 Operational: Disaster-Recovery-Notwendigkeit, unabhängig, schnell → parallel zu A"':
          - cell "3"
          - cell "(C) Backup-Pipeline":
            - strong: (C) Backup-Pipeline
          - cell "13"
          - 'cell "Operational: Disaster-Recovery-Notwendigkeit, unabhängig, schnell → parallel zu A"':
            - strong: Operational
            - text: ": Disaster-Recovery-Notwendigkeit, unabhängig, schnell → parallel zu A"
        - 'row "4 (B) Dashboard-Performance 21 Customer Value: observability, aber nicht kritisch → Woche 3"':
          - cell "4"
          - cell "(B) Dashboard-Performance":
            - strong: (B) Dashboard-Performance
          - cell "21"
          - 'cell "Customer Value: observability, aber nicht kritisch → Woche 3"':
            - strong: Customer Value
            - text: ": observability, aber nicht kritisch → Woche 3"
    - paragraph:
      - strong: "Nicht im Sprint:"
    - list:
      - listitem:
        - strong: (D) Kundenportal v2.0 Beta
        - text: "[55 SP] →"
        - strong: "Backlog für Sprint #24"
        - text: "(benötigt OAuth2 aus Sprint #23 als Dependency)"
    - paragraph:
      - strong: "Rationale:"
    - list:
      - listitem:
        - strong: Risk First
        - text: "(E): Audit-Funde blockieren ggf. Produktion → zuerst"
      - listitem:
        - strong: Dependency Chain
        - text: "(A→D): OAuth2 ist Blocker für Portal → priorisieren"
      - listitem:
        - strong: Kapazitätspuffer
        - text: ": 76/85 SP (9 SP Reserve für Unvorhergesehenes)"
      - listitem:
        - strong: Parallelisierbar
        - text: ": (A) + (C) können verschiedene Teams nehmen"
    - paragraph: Soll ich die Story-Point-Schätzung pro Epic verfeinern oder Sprint-Assignments für die 6 Team-Mitglieder entwerfen?
    - button "Play":
      - img
    - text: 0:00
    - slider: "0"
    - text: 2:12
    - button "Mute":
      - img
    - slider: "1"
    - img
    - text: 77d65a2302422081.mp3 voice 2072.3 KB
    - link "Download file":
      - /url: /v1/console/chat/sessions/W81tTu42-GdqQXZowDIBaw/workdir/voice/77d65a2302422081.mp3
      - img
    - button "Claude Code":
      - img
      - text: Claude Code
    - button "auto":
      - img
      - text: auto
    - button "Voice on":
      - img
      - text: Voice on
    - text: type / for commands
    - button "Attach files"
    - textbox "Message Corvin… (hold Space to speak)"
    - button "Attach files (CSV, PDF, images, …)":
      - img
    - button "Start recording (or hold Space)":
      - img
    - button "Send (Enter · Shift+Enter for newline)" [disabled]:
      - img
  - complementary:
    - text: Chats
    - button "New":
      - img
      - text: New
    - button "NordTech Solutions GmbH — KI-Kommandozentrale 6 turns · Sep 06, 2026, 02:27 AM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "New Chat 0 turns · Sep 06, 2026, 02:23 AM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "New Chat 0 turns · Sep 06, 2026, 02:23 AM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "NordTech — Task Engine E2E 0 turns · Sep 06, 2026, 02:22 AM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "mach mal eine tiefgehende… 2 turns · Aug 30, 2026, 11:28 AM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "ich bekomme ständig diese… 1 turn · Aug 30, 2026, 09:42 AM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "das neue frontend wird… 1 turn · Aug 26, 2026, 04:27 PM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "räume das repo auf… 1 turn · Aug 25, 2026, 01:09 AM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "nutze diesen skill assistant.corvin_end_to_end_ldd… 1 turn · Aug 25, 2026, 01:05 AM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "nutze den skill assistant.corvinos_panel_design… 3 turns · Aug 25, 2026, 12:35 AM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "es gibt eine neue… 3 turns · Aug 24, 2026, 10:45 PM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - button "erzeuge in http://127.0.0.1:8765/console/app/skills mit… 6 turns · Aug 20, 2026, 12:41 PM"
    - button "Rename session":
      - img
    - button "Delete session":
      - img
    - text: Chat sessions are scoped to this browser. Switch sessions using the list above.
```

# Test source

```ts
  1   | /**
  2   |  * E2E Tests: Cross-Device-Learning GitHub Integration
  3   |  * Console: http://127.0.0.1:8765/console
  4   |  *
  5   |  * Tests complete flow:
  6   |  * 1. Navigate to GitHub settings
  7   |  * 2. Enter GitHub URL
  8   |  * 3. Verify connection
  9   |  * 4. Monitor live sync status
  10  |  * 5. View audit trail
  11  |  */
  12  | 
  13  | import { test, expect } from '@playwright/test'
  14  | 
  15  | const CONSOLE_BASE = 'http://127.0.0.1:8765/console'
  16  | 
  17  | test.describe('GitHub Integration E2E', () => {
  18  |   test('should navigate to GitHub settings page', async ({ page }) => {
  19  |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  20  |     await expect(page.locator('h2')).toContainText('GitHub Integration')
  21  |     await expect(page.locator('text=Connect your tenant to a GitHub repository')).toBeVisible()
  22  |   })
  23  | 
  24  |   test('should show disconnected state initially', async ({ page }) => {
  25  |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
> 26  |     await expect(page.locator('text=Not connected')).toBeVisible()
      |                                                      ^ Error: expect(locator).toBeVisible() failed
  27  |   })
  28  | 
  29  |   test('should validate GitHub URL format', async ({ page }) => {
  30  |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  31  | 
  32  |     const urlInput = page.locator('input[placeholder*="https://github.com"]')
  33  |     const connectButton = page.locator('button:has-text("Connect Repository")')
  34  | 
  35  |     // Invalid URL should disable button
  36  |     await urlInput.fill('https://gitlab.com/owner/repo')
  37  |     await expect(connectButton).toBeDisabled()
  38  | 
  39  |     // Valid URL should enable button
  40  |     await urlInput.fill('https://github.com/veegee82/tenant-shumway')
  41  |     await expect(connectButton).toBeEnabled()
  42  |   })
  43  | 
  44  |   test('should accept valid GitHub URLs', async ({ page }) => {
  45  |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  46  | 
  47  |     const urlInput = page.locator('input[placeholder*="https://github.com"]')
  48  |     const connectButton = page.locator('button:has-text("Connect Repository")')
  49  | 
  50  |     // Test valid formats
  51  |     const validUrls = [
  52  |       'https://github.com/owner/repo',
  53  |       'https://github.com/my-org/my-repo',
  54  |       'https://github.com/tenant-shumway/skills-backup',
  55  |     ]
  56  | 
  57  |     for (const url of validUrls) {
  58  |       await urlInput.fill(url)
  59  |       await expect(connectButton).toBeEnabled()
  60  |     }
  61  |   })
  62  | 
  63  |   test('should navigate to sync monitor', async ({ page }) => {
  64  |     await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
  65  | 
  66  |     // Should show monitor panel
  67  |     await expect(page.locator('h2')).toContainText('Sync Monitor')
  68  |     await expect(page.locator('text=Manage tenant-native skills')).toBeVisible()
  69  |   })
  70  | 
  71  |   test('should show worker status on monitor', async ({ page }) => {
  72  |     await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
  73  | 
  74  |     // Should display worker status
  75  |     const statusCard = page.locator('text=Status')
  76  |     await expect(statusCard).toBeVisible()
  77  |   })
  78  | 
  79  |   test('should allow worker control (start/stop)', async ({ page }) => {
  80  |     await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
  81  | 
  82  |     // Should have start/stop button
  83  |     const button = page.locator('button:has-text("Start Worker"), button:has-text("Stop Worker")')
  84  |     await expect(button).toBeVisible()
  85  |   })
  86  | 
  87  |   test('should display event log', async ({ page }) => {
  88  |     await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
  89  | 
  90  |     // Should have event log section
  91  |     const eventLog = page.locator('text=Sync Events')
  92  |     await expect(eventLog).toBeVisible()
  93  |   })
  94  | 
  95  |   test('should navigate to webhook config', async ({ page }) => {
  96  |     await page.goto(`${CONSOLE_BASE}/app/settings/github/webhooks`)
  97  | 
  98  |     // Should show webhook panel
  99  |     await expect(page.locator('h2')).toContainText('GitHub Webhooks')
  100 |     await expect(page.locator('text=Event-driven synchronization from GitHub')).toBeVisible()
  101 |   })
  102 | 
  103 |   test('should have webhook registration form', async ({ page }) => {
  104 |     await page.goto(`${CONSOLE_BASE}/app/settings/github/webhooks`)
  105 | 
  106 |     // Should show token input
  107 |     const tokenInput = page.locator('input[placeholder*="ghp_"]')
  108 |     await expect(tokenInput).toBeVisible()
  109 | 
  110 |     // Should show register button
  111 |     const registerButton = page.locator('button:has-text("Register Webhook")')
  112 |     await expect(registerButton).toBeVisible()
  113 |   })
  114 | 
  115 |   test('should navigate to audit trail', async ({ page }) => {
  116 |     await page.goto(`${CONSOLE_BASE}/app/audit`)
  117 | 
  118 |     // Should show audit panel
  119 |     await expect(page.locator('h2')).toContainText('Sync Audit Trail')
  120 |     await expect(page.locator('text=GDPR Art. 30, 32')).toBeVisible()
  121 |   })
  122 | 
  123 |   test('should show audit statistics', async ({ page }) => {
  124 |     await page.goto(`${CONSOLE_BASE}/app/audit`)
  125 | 
  126 |     // Should display stats
```