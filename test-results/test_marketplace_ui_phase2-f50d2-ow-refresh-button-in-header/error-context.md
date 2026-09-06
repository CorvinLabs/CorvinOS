# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: test_marketplace_ui_phase2.spec.ts >> Marketplace UI Phase 2 >> should show refresh button in header
- Location: tests/e2e/test_marketplace_ui_phase2.spec.ts:382:7

# Error details

```
TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.
Call log:
  - waiting for locator('[data-testid="plugin-center-tab-marketplace"]') to be visible
    - waiting for navigation to finish...
    - navigated to "http://127.0.0.1:8765/console/"

```

# Page snapshot

```yaml
- generic [ref=f1e3]:
  - complementary [ref=f1e4]:
    - link "Corvin Operator Console" [ref=f1e5] [cursor=pointer]:
      - /url: /console
      - generic [ref=f1e11]:
        - generic [ref=f1e12]: Corvin
        - generic [ref=f1e13]: Operator Console
    - navigation [ref=f1e14]:
      - generic [ref=f1e15]:
        - link "Chat" [ref=f1e16] [cursor=pointer]:
          - /url: /console/app/chat
        - link "Dashboard" [ref=f1e20] [cursor=pointer]:
          - /url: /console/app/dashboard
      - generic [ref=f1e27]:
        - button "Vibe Engineering" [ref=f1e28] [cursor=pointer]
        - link "Vibe Dashboard" [ref=f1e32] [cursor=pointer]:
          - /url: /console/app/vibe-engineering
      - generic [ref=f1e37]:
        - button "Observability" [ref=f1e38] [cursor=pointer]
        - link "Learning Dashboard" [ref=f1e42] [cursor=pointer]:
          - /url: /console/app/learning-dashboard
      - generic [ref=f1e46]:
        - generic [ref=f1e47]: Messaging
        - generic [ref=f1e48]:
          - link "Channels" [ref=f1e49] [cursor=pointer]:
            - /url: /console/app/bridges
          - link "Profile" [ref=f1e55] [cursor=pointer]:
            - /url: /console/app/voice
          - link "People" [ref=f1e57] [cursor=pointer]:
            - /url: /console/app/people
      - generic [ref=f1e64]:
        - generic [ref=f1e65]: Assistant
        - generic [ref=f1e66]:
          - link "AI Engine" [ref=f1e67] [cursor=pointer]:
            - /url: /console/app/engines
          - link "Browser" [ref=f1e71] [cursor=pointer]:
            - /url: /console/app/browser
          - link "Personas" [ref=f1e75] [cursor=pointer]:
            - /url: /console/app/personas
          - link "Memory" [ref=f1e78] [cursor=pointer]:
            - /url: /console/app/memory
          - link "Files" [ref=f1e81] [cursor=pointer]:
            - /url: /console/app/files
      - generic [ref=f1e85]:
        - button "Build" [ref=f1e86] [cursor=pointer]
        - generic [ref=f1e89]:
          - link "Workflows" [ref=f1e90] [cursor=pointer]:
            - /url: /console/app/workflows
          - link "Pipelines" [ref=f1e95] [cursor=pointer]:
            - /url: /console/app/flows
          - link "Agentic Compute" [ref=f1e101] [cursor=pointer]:
            - /url: /console/app/compute
          - link "Tools" [ref=f1e105] [cursor=pointer]:
            - /url: /console/app/forge
          - link "Skills" [ref=f1e110] [cursor=pointer]:
            - /url: /console/app/skills
          - link "OS Skills" [ref=f1e113] [cursor=pointer]:
            - /url: /console/app/os-skills
          - link "Packages" [ref=f1e118] [cursor=pointer]:
            - /url: /console/app/packages
          - link "Agents" [ref=f1e123] [cursor=pointer]:
            - /url: /console/app/agents
          - link "Plugins & Extensions" [ref=f1e127] [cursor=pointer]:
            - /url: /console/app/plugin-center
          - link "Marketplace" [ref=f1e131] [cursor=pointer]:
            - /url: /console/app/marketplace
      - generic [ref=f1e137]:
        - button "Network" [ref=f1e138] [cursor=pointer]
        - generic [ref=f1e141]:
          - link "Agent Hub" [ref=f1e142] [cursor=pointer]:
            - /url: /console/app/agent-hub
          - link "CorvinSpace" [ref=f1e148] [cursor=pointer]:
            - /url: /console/app/space
          - link "Organisations" [ref=f1e152] [cursor=pointer]:
            - /url: /console/app/orgs
          - link "Connectors" [ref=f1e157] [cursor=pointer]:
            - /url: /console/app/connectors
          - link "Sync Monitor" [ref=f1e160] [cursor=pointer]:
            - /url: /console/app/sync-monitor
          - link "Webhooks" [ref=f1e166] [cursor=pointer]:
            - /url: /console/app/webhooks
      - generic [ref=f1e172]:
        - button "Data" [ref=f1e173] [cursor=pointer]
        - generic [ref=f1e176]:
          - link "Databases" [ref=f1e177] [cursor=pointer]:
            - /url: /console/app/data-sources
          - link "Knowledge" [ref=f1e181] [cursor=pointer]:
            - /url: /console/app/rag
          - link "Knowledge Hub" [ref=f1e186] [cursor=pointer]:
            - /url: /console/app/rag-hub
          - link "Add Provider" [ref=f1e192] [cursor=pointer]:
            - /url: /console/app/custom-provider
      - button "System" [ref=f1e197] [cursor=pointer]
      - generic [ref=f1e201]:
        - generic [ref=f1e202]: Your panels
        - link "Task Graph — Redesigned" [ref=f1e204] [cursor=pointer]:
          - /url: /console/app/task-graph-redesigned
    - link "Licence Free" [ref=f1e207] [cursor=pointer]:
      - /url: /console/app/license
      - generic [ref=f1e208]: Licence
      - generic [ref=f1e209]: Free
    - generic [ref=f1e210]:
      - generic [ref=f1e211]:
        - generic [ref=f1e212]: _default
        - generic [ref=f1e213]: owner
      - button "Log out" [ref=f1e214] [cursor=pointer]
  - generic [ref=f1e215]:
    - banner [ref=f1e216]:
      - generic [ref=f1e217]: _default
      - generic [ref=f1e219]:
        - link "Claude Code" [ref=f1e220] [cursor=pointer]:
          - /url: /console/app/engines
        - button "Corvin Assistant" [ref=f1e224] [cursor=pointer]:
          - generic [ref=f1e230]: Assistant
        - 'button "Theme: Dark theme (click to switch)" [ref=f1e231] [cursor=pointer]'
    - main [ref=f1e232]:
      - generic [ref=f1e233]:
        - main [ref=f1e234]:
          - generic [ref=f1e235]:
            - generic [ref=f1e236]:
              - generic [ref=f1e237]: NordTech Solutions GmbH — KI-Kommandozentrale
              - generic [ref=f1e238]:
                - generic [ref=f1e239]: web:HoeyobVc6I
                - generic [ref=f1e240]: 1 turns
            - generic [ref=f1e241]:
              - button "Audit" [ref=f1e242] [cursor=pointer]
              - button "Claude Code" [ref=f1e244] [cursor=pointer]
              - button "Recap the whole session" [ref=f1e250] [cursor=pointer]
              - button "Voice on" [ref=f1e251] [cursor=pointer]
              - button "Open session artifacts folder" [ref=f1e252] [cursor=pointer]
          - generic [ref=f1e254]:
            - paragraph [ref=f1e258]: "Hallo! Ich bin das NordTech KI-System. Beantworte bitte auf Deutsch: In welchem Bundesland liegt Berlin?"
            - generic [ref=f1e269]:
              - paragraph [ref=f1e270]: Berlin ist selbst ein Bundesland — es ist eine sogenannte Stadtstadt und gehört nicht zu einem anderen Bundesland. Berlin ist eine der 16 Bundesländer der Bundesrepublik Deutschland und fungiert gleichzeitig als Hauptstadt.
              - paragraph [ref=f1e271]: "(Zur Klarstellung: Falls die Frage ironisch gemeint war, dass ich ein \"NordTech\"-System bin — ich bin Claude von Anthropic, kein NordTech-System, und antworte auf Deutsch, weil das deine Spracheinstellung in diesem Projekt ist.)"
            - paragraph [ref=f1e275]: "Danke! Noch eine kurze Folgefrage: Nenne mir genau eine bekannte Sehenswürdigkeit in der Stadt, von der du gerade gesprochen hast."
          - generic [ref=f1e281]:
            - button "Claude Code" [ref=f1e282] [cursor=pointer]
            - button "auto" [ref=f1e285] [cursor=pointer]
            - button "Voice on" [ref=f1e288] [cursor=pointer]
            - generic [ref=f1e293]: type / for commands
          - generic [ref=f1e295]:
            - button "Attach files" [ref=f1e296]
            - generic [ref=f1e297]:
              - textbox "Message Corvin… (hold Space to speak)" [ref=f1e299]
              - generic [ref=f1e300]:
                - button "Attach files (CSV, PDF, images, …)" [ref=f1e301] [cursor=pointer]
                - button "Start recording (or hold Space)" [ref=f1e302] [cursor=pointer]
                - button "Send (Enter · Shift+Enter for newline)" [disabled]
        - complementary [ref=f1e303]:
          - generic [ref=f1e304]:
            - generic [ref=f1e305]: Chats
            - button "New" [ref=f1e306] [cursor=pointer]
          - generic [ref=f1e307]:
            - generic [ref=f1e308]:
              - button "NordTech Solutions GmbH — KI-Kommandozentrale 1 turn · Sep 06, 2026, 02:46 AM" [ref=f1e309] [cursor=pointer]:
                - generic [ref=f1e310]: NordTech Solutions GmbH — KI-Kommandozentrale
                - generic [ref=f1e312]:
                  - generic [ref=f1e313]: 1 turn
                  - generic [ref=f1e314]: ·
                  - generic [ref=f1e315]: Sep 06, 2026, 02:46 AM
              - button "Rename session" [ref=f1e316] [cursor=pointer]
              - button "Delete session" [ref=f1e320] [cursor=pointer]
            - generic [ref=f1e324]:
              - button "NordTech Solutions GmbH — KI-Kommandozentrale 6 turns · Sep 06, 2026, 02:27 AM" [ref=f1e325] [cursor=pointer]:
                - generic [ref=f1e326]: NordTech Solutions GmbH — KI-Kommandozentrale
                - generic [ref=f1e328]:
                  - generic [ref=f1e329]: 6 turns
                  - generic [ref=f1e330]: ·
                  - generic [ref=f1e331]: Sep 06, 2026, 02:27 AM
              - button "Rename session" [ref=f1e332] [cursor=pointer]
              - button "Delete session" [ref=f1e336] [cursor=pointer]
            - generic [ref=f1e340]:
              - button "New Chat 0 turns · Sep 06, 2026, 02:23 AM" [ref=f1e341] [cursor=pointer]:
                - generic [ref=f1e342]: New Chat
                - generic [ref=f1e344]:
                  - generic [ref=f1e345]: 0 turns
                  - generic [ref=f1e346]: ·
                  - generic [ref=f1e347]: Sep 06, 2026, 02:23 AM
              - button "Rename session" [ref=f1e348] [cursor=pointer]
              - button "Delete session" [ref=f1e352] [cursor=pointer]
            - generic [ref=f1e356]:
              - button "New Chat 0 turns · Sep 06, 2026, 02:23 AM" [ref=f1e357] [cursor=pointer]:
                - generic [ref=f1e358]: New Chat
                - generic [ref=f1e360]:
                  - generic [ref=f1e361]: 0 turns
                  - generic [ref=f1e362]: ·
                  - generic [ref=f1e363]: Sep 06, 2026, 02:23 AM
              - button "Rename session" [ref=f1e364] [cursor=pointer]
              - button "Delete session" [ref=f1e368] [cursor=pointer]
            - generic [ref=f1e372]:
              - button "NordTech — Task Engine E2E 0 turns · Sep 06, 2026, 02:22 AM" [ref=f1e373] [cursor=pointer]:
                - generic [ref=f1e374]: NordTech — Task Engine E2E
                - generic [ref=f1e376]:
                  - generic [ref=f1e377]: 0 turns
                  - generic [ref=f1e378]: ·
                  - generic [ref=f1e379]: Sep 06, 2026, 02:22 AM
              - button "Rename session" [ref=f1e380] [cursor=pointer]
              - button "Delete session" [ref=f1e384] [cursor=pointer]
            - generic [ref=f1e388]:
              - button "mach mal eine tiefgehende… 2 turns · Aug 30, 2026, 11:28 AM" [ref=f1e389] [cursor=pointer]:
                - generic [ref=f1e390]: mach mal eine tiefgehende…
                - generic [ref=f1e392]:
                  - generic [ref=f1e393]: 2 turns
                  - generic [ref=f1e394]: ·
                  - generic [ref=f1e395]: Aug 30, 2026, 11:28 AM
              - button "Rename session" [ref=f1e396] [cursor=pointer]
              - button "Delete session" [ref=f1e400] [cursor=pointer]
            - generic [ref=f1e404]:
              - button "ich bekomme ständig diese… 1 turn · Aug 30, 2026, 09:42 AM" [ref=f1e405] [cursor=pointer]:
                - generic [ref=f1e406]: ich bekomme ständig diese…
                - generic [ref=f1e408]:
                  - generic [ref=f1e409]: 1 turn
                  - generic [ref=f1e410]: ·
                  - generic [ref=f1e411]: Aug 30, 2026, 09:42 AM
              - button "Rename session" [ref=f1e412] [cursor=pointer]
              - button "Delete session" [ref=f1e416] [cursor=pointer]
            - generic [ref=f1e420]:
              - button "das neue frontend wird… 1 turn · Aug 26, 2026, 04:27 PM" [ref=f1e421] [cursor=pointer]:
                - generic [ref=f1e422]: das neue frontend wird…
                - generic [ref=f1e424]:
                  - generic [ref=f1e425]: 1 turn
                  - generic [ref=f1e426]: ·
                  - generic [ref=f1e427]: Aug 26, 2026, 04:27 PM
              - button "Rename session" [ref=f1e428] [cursor=pointer]
              - button "Delete session" [ref=f1e432] [cursor=pointer]
            - generic [ref=f1e436]:
              - button "räume das repo auf… 1 turn · Aug 25, 2026, 01:09 AM" [ref=f1e437] [cursor=pointer]:
                - generic [ref=f1e438]: räume das repo auf…
                - generic [ref=f1e440]:
                  - generic [ref=f1e441]: 1 turn
                  - generic [ref=f1e442]: ·
                  - generic [ref=f1e443]: Aug 25, 2026, 01:09 AM
              - button "Rename session" [ref=f1e444] [cursor=pointer]
              - button "Delete session" [ref=f1e448] [cursor=pointer]
            - generic [ref=f1e452]:
              - button "nutze diesen skill assistant.corvin_end_to_end_ldd… 1 turn · Aug 25, 2026, 01:05 AM" [ref=f1e453] [cursor=pointer]:
                - generic [ref=f1e454]: nutze diesen skill assistant.corvin_end_to_end_ldd…
                - generic [ref=f1e456]:
                  - generic [ref=f1e457]: 1 turn
                  - generic [ref=f1e458]: ·
                  - generic [ref=f1e459]: Aug 25, 2026, 01:05 AM
              - button "Rename session" [ref=f1e460] [cursor=pointer]
              - button "Delete session" [ref=f1e464] [cursor=pointer]
            - generic [ref=f1e468]:
              - button "nutze den skill assistant.corvinos_panel_design… 3 turns · Aug 25, 2026, 12:35 AM" [ref=f1e469] [cursor=pointer]:
                - generic [ref=f1e470]: nutze den skill assistant.corvinos_panel_design…
                - generic [ref=f1e472]:
                  - generic [ref=f1e473]: 3 turns
                  - generic [ref=f1e474]: ·
                  - generic [ref=f1e475]: Aug 25, 2026, 12:35 AM
              - button "Rename session" [ref=f1e476] [cursor=pointer]
              - button "Delete session" [ref=f1e480] [cursor=pointer]
            - generic [ref=f1e484]:
              - button "es gibt eine neue… 3 turns · Aug 24, 2026, 10:45 PM" [ref=f1e485] [cursor=pointer]:
                - generic [ref=f1e486]: es gibt eine neue…
                - generic [ref=f1e488]:
                  - generic [ref=f1e489]: 3 turns
                  - generic [ref=f1e490]: ·
                  - generic [ref=f1e491]: Aug 24, 2026, 10:45 PM
              - button "Rename session" [ref=f1e492] [cursor=pointer]
              - button "Delete session" [ref=f1e496] [cursor=pointer]
            - generic [ref=f1e500]:
              - button "erzeuge in http://127.0.0.1:8765/console/app/skills mit… 6 turns · Aug 20, 2026, 12:41 PM" [ref=f1e501] [cursor=pointer]:
                - generic [ref=f1e502]: erzeuge in http://127.0.0.1:8765/console/app/skills mit…
                - generic [ref=f1e504]:
                  - generic [ref=f1e505]: 6 turns
                  - generic [ref=f1e506]: ·
                  - generic [ref=f1e507]: Aug 20, 2026, 12:41 PM
              - button "Rename session" [ref=f1e508] [cursor=pointer]
              - button "Delete session" [ref=f1e512] [cursor=pointer]
          - generic [ref=f1e516]: Chat sessions are scoped to this browser. Switch sessions using the list above.
```

# Test source

```ts
  1   | /**
  2   |  * E2E Tests for Console Marketplace UI (Phase 2)
  3   |  *
  4   |  * Tests the complete marketplace workflow:
  5   |  * - Browsing marketplace extensions
  6   |  * - Searching and filtering by category
  7   |  * - Installing extensions
  8   |  * - Error handling
  9   |  * - Progress tracking
  10  |  * - State synchronization with plugins registry
  11  |  */
  12  | 
  13  | import { test, expect } from '@playwright/test'
  14  | 
  15  | const CONSOLE_URL = 'http://127.0.0.1:8765/console'
  16  | 
  17  | test.describe('Marketplace UI Phase 2', () => {
  18  |   test.beforeEach(async ({ page }) => {
  19  |     await page.goto(`${CONSOLE_URL}/#/app/plugin-center?tab=marketplace`)
> 20  |     await page.waitForSelector('[data-testid="plugin-center-tab-marketplace"]', {
      |                ^ TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.
  21  |       timeout: 5000,
  22  |     })
  23  |   })
  24  | 
  25  |   test('should load marketplace index on mount', async ({ page }) => {
  26  |     // Check that the browse view is visible
  27  |     await expect(page.locator('input[placeholder*="Search"]')).toBeVisible()
  28  | 
  29  |     // Verify grid of extensions is rendered
  30  |     const extensions = page.locator('[class*="grid"] > [class*="rounded"]')
  31  |     const count = await extensions.count()
  32  |     expect(count).toBeGreaterThanOrEqual(0)
  33  |   })
  34  | 
  35  |   test('should display extension metadata correctly', async ({ page }) => {
  36  |     // Wait for extensions to load
  37  |     await page.waitForTimeout(500)
  38  | 
  39  |     // Get first extension card
  40  |     const firstCard = page.locator('[class*="grid"] > div').first()
  41  |     await expect(firstCard).toBeVisible()
  42  | 
  43  |     // Verify card contains expected fields
  44  |     const title = firstCard.locator('h3')
  45  |     const version = firstCard.locator('p:has-text("v")')
  46  |     const category = firstCard.locator('span[class*="px-2"]')
  47  | 
  48  |     await expect(title).toBeVisible()
  49  |     await expect(version).toBeVisible()
  50  |     await expect(category).toBeVisible()
  51  |   })
  52  | 
  53  |   test('should open extension detail modal on click', async ({ page }) => {
  54  |     // Wait for extensions
  55  |     await page.waitForTimeout(500)
  56  | 
  57  |     // Click first extension card
  58  |     const firstCard = page.locator('[class*="grid"] > div').first()
  59  |     await firstCard.click()
  60  | 
  61  |     // Verify modal appears with extension details
  62  |     const modal = page.locator('[class*="fixed"][class*="inset-0"]')
  63  |     await expect(modal).toBeVisible()
  64  | 
  65  |     // Verify modal content
  66  |     await expect(modal.locator('h2')).toBeVisible()
  67  |     await expect(modal.locator('text=Description')).toBeVisible()
  68  |   })
  69  | 
  70  |   test('should close modal on X button click', async ({ page }) => {
  71  |     // Open modal
  72  |     await page.waitForTimeout(500)
  73  |     const firstCard = page.locator('[class*="grid"] > div').first()
  74  |     await firstCard.click()
  75  | 
  76  |     // Wait for modal
  77  |     const closeButton = page.locator('button:has-text("✕")')
  78  |     await expect(closeButton).toBeVisible()
  79  | 
  80  |     // Click close button
  81  |     await closeButton.click()
  82  | 
  83  |     // Verify modal is hidden
  84  |     const modal = page.locator('[class*="fixed"][class*="inset-0"][class*="bg-black"]')
  85  |     await expect(modal).toBeHidden({ timeout: 1000 })
  86  |   })
  87  | 
  88  |   test('should search extensions by name', async ({ page }) => {
  89  |     const searchInput = page.locator('input[placeholder*="Search"]')
  90  |     await expect(searchInput).toBeVisible()
  91  | 
  92  |     // Type search term
  93  |     await searchInput.fill('auth')
  94  | 
  95  |     // Wait for filtering
  96  |     await page.waitForTimeout(300)
  97  | 
  98  |     // Verify results are filtered (should show fewer or zero results)
  99  |     const extensions = page.locator('[class*="grid"] > div')
  100 |     const count = await extensions.count()
  101 |     expect(count).toBeGreaterThanOrEqual(0)
  102 |   })
  103 | 
  104 |   test('should filter extensions by category', async ({ page }) => {
  105 |     // Wait for category buttons to be visible
  106 |     await page.waitForTimeout(500)
  107 | 
  108 |     // Get all category buttons (skip the "All" button at index 0)
  109 |     const categoryButtons = page.locator('button:has-text(/^[A-Z]/)').locator(
  110 |       'not(:text-is("All"))'
  111 |     )
  112 |     const categoryCount = await categoryButtons.count()
  113 | 
  114 |     if (categoryCount > 0) {
  115 |       // Click first category
  116 |       const firstCategory = categoryButtons.first()
  117 |       const categoryText = await firstCategory.textContent()
  118 | 
  119 |       await firstCategory.click()
  120 |       await page.waitForTimeout(300)
```