# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: github-integration.spec.ts >> GitHub Integration E2E >> should preserve URL navigation
- Location: tests/e2e/github-integration.spec.ts:207:7

# Error details

```
Error: expect(received).toContain(expected) // indexOf

Expected substring: "/app/audit"
Received string:    "http://127.0.0.1:8765/console/login"
```

# Page snapshot

```yaml
- generic [ref=f2e3]:
  - complementary [ref=f2e4]:
    - link "Corvin Operator Console" [ref=f2e5] [cursor=pointer]:
      - /url: /console
      - generic [ref=f2e11]:
        - generic [ref=f2e12]: Corvin
        - generic [ref=f2e13]: Operator Console
    - navigation [ref=f2e14]:
      - generic [ref=f2e15]:
        - link "Chat" [ref=f2e16] [cursor=pointer]:
          - /url: /console/app/chat
        - link "Dashboard" [ref=f2e20] [cursor=pointer]:
          - /url: /console/app/dashboard
      - generic [ref=f2e27]:
        - button "Vibe Engineering" [ref=f2e28] [cursor=pointer]
        - link "Vibe Dashboard" [ref=f2e32] [cursor=pointer]:
          - /url: /console/app/vibe-engineering
      - generic [ref=f2e37]:
        - button "Observability" [ref=f2e38] [cursor=pointer]
        - link "Learning Dashboard" [ref=f2e42] [cursor=pointer]:
          - /url: /console/app/learning-dashboard
      - generic [ref=f2e46]:
        - generic [ref=f2e47]: Messaging
        - generic [ref=f2e48]:
          - link "Channels" [ref=f2e49] [cursor=pointer]:
            - /url: /console/app/bridges
          - link "Profile" [ref=f2e55] [cursor=pointer]:
            - /url: /console/app/voice
          - link "People" [ref=f2e57] [cursor=pointer]:
            - /url: /console/app/people
      - generic [ref=f2e64]:
        - generic [ref=f2e65]: Assistant
        - generic [ref=f2e66]:
          - link "AI Engine" [ref=f2e67] [cursor=pointer]:
            - /url: /console/app/engines
          - link "Browser" [ref=f2e71] [cursor=pointer]:
            - /url: /console/app/browser
          - link "Personas" [ref=f2e75] [cursor=pointer]:
            - /url: /console/app/personas
          - link "Memory" [ref=f2e78] [cursor=pointer]:
            - /url: /console/app/memory
          - link "Files" [ref=f2e81] [cursor=pointer]:
            - /url: /console/app/files
      - generic [ref=f2e85]:
        - button "Build" [ref=f2e86] [cursor=pointer]
        - generic [ref=f2e89]:
          - link "Workflows" [ref=f2e90] [cursor=pointer]:
            - /url: /console/app/workflows
          - link "Pipelines" [ref=f2e95] [cursor=pointer]:
            - /url: /console/app/flows
          - link "Agentic Compute" [ref=f2e101] [cursor=pointer]:
            - /url: /console/app/compute
          - link "Tools" [ref=f2e105] [cursor=pointer]:
            - /url: /console/app/forge
          - link "Skills" [ref=f2e110] [cursor=pointer]:
            - /url: /console/app/skills
          - link "OS Skills" [ref=f2e113] [cursor=pointer]:
            - /url: /console/app/os-skills
          - link "Packages" [ref=f2e118] [cursor=pointer]:
            - /url: /console/app/packages
          - link "Agents" [ref=f2e123] [cursor=pointer]:
            - /url: /console/app/agents
          - link "Plugins & Extensions" [ref=f2e127] [cursor=pointer]:
            - /url: /console/app/plugin-center
          - link "Marketplace" [ref=f2e131] [cursor=pointer]:
            - /url: /console/app/marketplace
      - generic [ref=f2e137]:
        - button "Network" [ref=f2e138] [cursor=pointer]
        - generic [ref=f2e141]:
          - link "Agent Hub" [ref=f2e142] [cursor=pointer]:
            - /url: /console/app/agent-hub
          - link "CorvinSpace" [ref=f2e148] [cursor=pointer]:
            - /url: /console/app/space
          - link "Organisations" [ref=f2e152] [cursor=pointer]:
            - /url: /console/app/orgs
          - link "Connectors" [ref=f2e157] [cursor=pointer]:
            - /url: /console/app/connectors
          - link "Sync Monitor" [ref=f2e160] [cursor=pointer]:
            - /url: /console/app/sync-monitor
          - link "Webhooks" [ref=f2e166] [cursor=pointer]:
            - /url: /console/app/webhooks
      - generic [ref=f2e172]:
        - button "Data" [ref=f2e173] [cursor=pointer]
        - generic [ref=f2e176]:
          - link "Databases" [ref=f2e177] [cursor=pointer]:
            - /url: /console/app/data-sources
          - link "Knowledge" [ref=f2e181] [cursor=pointer]:
            - /url: /console/app/rag
          - link "Knowledge Hub" [ref=f2e186] [cursor=pointer]:
            - /url: /console/app/rag-hub
          - link "Add Provider" [ref=f2e192] [cursor=pointer]:
            - /url: /console/app/custom-provider
      - button "System" [ref=f2e197] [cursor=pointer]
      - generic [ref=f2e201]:
        - generic [ref=f2e202]: Your panels
        - link "Task Graph — Redesigned" [ref=f2e204] [cursor=pointer]:
          - /url: /console/app/task-graph-redesigned
    - generic [ref=f2e207]:
      - generic [ref=f2e208]:
        - generic [ref=f2e209]: _default
        - generic [ref=f2e210]: owner
      - button "Log out" [ref=f2e211] [cursor=pointer]
  - generic [ref=f2e212]:
    - banner [ref=f2e213]:
      - generic [ref=f2e214]: _default
      - generic [ref=f2e216]:
        - link "Claude Code" [ref=f2e217] [cursor=pointer]:
          - /url: /console/app/engines
        - button "Corvin Assistant" [ref=f2e221] [cursor=pointer]:
          - generic [ref=f2e227]: Assistant
        - 'button "Theme: Dark theme (click to switch)" [ref=f2e228] [cursor=pointer]'
    - main [ref=f2e229]
```

# Test source

```ts
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
  127 |     const stats = page.locator('text=Total Events')
  128 |     await expect(stats).toBeVisible()
  129 |   })
  130 | 
  131 |   test('should have chain verification', async ({ page }) => {
  132 |     await page.goto(`${CONSOLE_BASE}/app/audit`)
  133 | 
  134 |     // Should have verify button
  135 |     const verifyButton = page.locator('button:has-text("Verify Chain")')
  136 |     await expect(verifyButton).toBeVisible()
  137 |   })
  138 | 
  139 |   test('should navigate to releases', async ({ page }) => {
  140 |     await page.goto(`${CONSOLE_BASE}/app/releases`)
  141 | 
  142 |     // Should show release manager
  143 |     await expect(page.locator('h2')).toContainText('Release Manager')
  144 |   })
  145 | 
  146 |   test('should show version info', async ({ page }) => {
  147 |     await page.goto(`${CONSOLE_BASE}/app/releases`)
  148 | 
  149 |     // Should display version info
  150 |     const latestVersion = page.locator('text=Latest Version')
  151 |     await expect(latestVersion).toBeVisible()
  152 |   })
  153 | 
  154 |   test('should have create release button', async ({ page }) => {
  155 |     await page.goto(`${CONSOLE_BASE}/app/releases`)
  156 | 
  157 |     // Should have button
  158 |     const createButton = page.locator('button:has-text("New Release")')
  159 |     await expect(createButton).toBeVisible()
  160 |   })
  161 | 
  162 |   test('complete GitHub setup flow', async ({ page }) => {
  163 |     // 1. Go to GitHub settings
  164 |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  165 |     await expect(page.locator('h1')).toContainText('GitHub Integration')
  166 | 
  167 |     // 2. Enter GitHub URL
  168 |     const urlInput = page.locator('input[placeholder*="https://github.com"]')
  169 |     await urlInput.fill('https://github.com/veegee82/tenant-shumway')
  170 | 
  171 |     // 3. Verify URL is accepted
  172 |     const connectButton = page.locator('button:has-text("Connect Repository")')
  173 |     await expect(connectButton).toBeEnabled()
  174 | 
  175 |     // 4. Check sync monitor is accessible
  176 |     await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
  177 |     await expect(page.locator('h2')).toContainText('Sync Monitor')
  178 | 
  179 |     // 5. Check audit trail is accessible
  180 |     await page.goto(`${CONSOLE_BASE}/app/audit`)
  181 |     await expect(page.locator('h2')).toContainText('Audit')
  182 | 
  183 |     // 6. Check releases are accessible
  184 |     await page.goto(`${CONSOLE_BASE}/app/releases`)
  185 |     await expect(page.locator('h2')).toContainText('Release Manager')
  186 |   })
  187 | 
  188 |   test('should handle responsive layout on mobile', async ({ page }) => {
  189 |     await page.setViewportSize({ width: 375, height: 667 })
  190 | 
  191 |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  192 | 
  193 |     // Page should still be visible and functional
  194 |     await expect(page.locator('h2')).toContainText('GitHub Integration')
  195 |     await expect(page.locator('button:has-text("Connect Repository")')).toBeVisible()
  196 |   })
  197 | 
  198 |   test('should handle responsive layout on tablet', async ({ page }) => {
  199 |     await page.setViewportSize({ width: 768, height: 1024 })
  200 | 
  201 |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  202 | 
  203 |     // Page should be readable
  204 |     await expect(page.locator('h2')).toContainText('GitHub Integration')
  205 |   })
  206 | 
  207 |   test('should preserve URL navigation', async ({ page }) => {
  208 |     // Test navigation between sections
  209 |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  210 |     await expect(page.url()).toContain('/app/settings/github')
  211 | 
  212 |     await page.goto(`${CONSOLE_BASE}/app/audit`)
> 213 |     await expect(page.url()).toContain('/app/audit')
      |                              ^ Error: expect(received).toContain(expected) // indexOf
  214 | 
  215 |     await page.goto(`${CONSOLE_BASE}/app/releases`)
  216 |     await expect(page.url()).toContain('/app/releases')
  217 |   })
  218 | })
  219 | 
```