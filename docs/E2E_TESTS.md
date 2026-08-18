# E2E Test Suite — CorvinOS Console

Comprehensive end-to-end tests using Playwright to validate the entire console UI, feature flags, token metrics, and context pipeline.

## 📋 Test Coverage

| Test | Purpose | Pipeline Stages Tested |
|------|---------|------------------------|
| 1️⃣ Console Navigation | Load main UI and verify nav items | UI Layer |
| 2️⃣ Settings Page | Open Settings and verify config files | Settings UI |
| 3️⃣ Feature Flags | Load Feature Flags section via API | GET /settings/features |
| 4️⃣ Vibe Engineering | Enable feature and verify Token Metrics panel | Feature Flag System |
| 5️⃣ Memory Stage | Send memory-related chat and verify response | Memory Lookup → Context |
| 6️⃣ Skills Stage | Send skills inquiry and verify response | Skills Injection |
| 7️⃣ Graph Stage | Send knowledge graph inquiry | Graph Traversal |
| 8️⃣ Forge Tools | Verify forged tools visible | Tool Registry |
| 9️⃣ Forged Skills | Verify learned skills visible and graded | Skill Registry |
| 🔟 Token Metrics | Verify real-time updates | Token Counter → API |
| 1️⃣1️⃣ Full Integration | Send synthesis prompt exercising all stages | Complete Pipeline |

## 🚀 Quick Start

### Prerequisites

```bash
# Install Node.js 18+ and npm
node --version  # Should be 18+
npm --version
```

### Run Tests

```bash
# Headless mode (default)
bash scripts/run-e2e-tests.sh

# Show browser
bash scripts/run-e2e-tests.sh --headed

# Debug mode (interactive)
bash scripts/run-e2e-tests.sh --debug

# Specific browser
bash scripts/run-e2e-tests.sh --chrome
bash scripts/run-e2e-tests.sh --firefox
bash scripts/run-e2e-tests.sh --webkit
```

## 📊 Expected Test Output

### Console Loads Successfully
```
✅ Console loads and displays navigation
  ✅ Found nav item: Dashboard
  ✅ Found nav item: Chat
  ✅ Found nav item: Settings
  ✅ Found nav item: Forge
  ✅ Found nav item: Skills
```

### Settings Opens
```
✅ Settings opens and shows configuration files
  ✅ Settings page loaded
```

### Features Available
```
✅ Features section loads with feature flags
  ✅ vibe_engineering feature found in Settings
```

### Vibe Engineering Enabled
```
✅ Enable Vibe Engineering and verify Token Metrics panel appears
  ✅ vibe_engineering enabled
  ✅ Token Metrics panel now visible in navigation
  ✅ Token Metrics content loaded
```

### Pipeline Stages Respond
```
✅ Chat interface - Test memory context pipeline stage
  ✅ Chat interface loaded
  ✅ Memory stage responded

✅ Chat - Test skills pipeline stage
  ✅ Skills stage responded

✅ Chat - Test graph/knowledge pipeline stage
  ✅ Graph stage tested
```

### Tools and Skills Visible
```
✅ Forge section - Verify forged tools are visible
  ✅ Forged tools section visible

✅ Skills section - Verify forged skills are visible and graded
  ✅ Found 5 learned/forged skills
```

### Real-time Metrics
```
✅ Token Metrics - Verify real-time updates
  ✅ Token metrics displaying real-time data
  ✅ Tokens: 73,375
  ✅ Savings: 25.9%
```

### Full Integration
```
✅ Context Pipeline Integration - Full flow test
  🔄 Step 1: Ensure Vibe Engineering enabled
  ✅ Vibe Engineering enabled
  🔄 Step 2: Open Chat interface
  ✅ Chat loaded
  🔄 Step 3: Send synthesis prompt (exercises all pipeline stages)
  ✅ Memory Stage responded
  ✅ Skills Stage responded
  ✅ Graph Stage responded
  ✅ Synthesis Stage responded
  🔄 Step 4: Verify Token Metrics updated
  ✅ Token Metrics updated after chat interaction
  🎉 Full Context Pipeline integration test complete
```

## 📁 Test Files

- **`scripts/e2e-test-full-console.spec.ts`** — Main test suite (11 comprehensive tests)
- **`playwright.config.ts`** — Playwright configuration
- **`scripts/run-e2e-tests.sh`** — Test runner script

## 🔍 What Gets Tested

### UI Layer
- ✅ Navigation loads (Dashboard, Chat, Settings, Forge, Skills)
- ✅ Page transitions work
- ✅ Timeouts handled properly

### Settings & Features
- ✅ Settings page loads all configuration sections
- ✅ Feature Flags API returns available features
- ✅ Feature toggle works (vibe_engineering)
- ✅ Changes persist

### Token Metrics
- ✅ Panel appears after enabling Vibe Engineering
- ✅ Real-time data displays correctly
- ✅ Updates every 5 seconds
- ✅ Shows accurate token counts and savings

### Context Pipeline Stages

Each stage is tested via chat prompts:

1. **Memory Stage**
   - Sends: "List recent conversations"
   - Expects: Response mentioning memory/context

2. **Skills Stage**
   - Sends: "What skills are available?"
   - Expects: Response mentioning skills/abilities

3. **Graph Stage**
   - Sends: "Show knowledge relationships"
   - Expects: Response mentioning relationships/graph

4. **Synthesis Stage** (Full Integration)
   - Sends: Complex query exercising all stages
   - Expects: Integrated response using memory + skills + graph

### Forged Components

- ✅ **Forge Tools** — Custom tools created via Forge UI are visible
- ✅ **Learned Skills** — Skills marked as `learned-experience` appear in Skills section
- ✅ **Skill Grading** — Skills show grade/quality metric

## 📈 Reports

After running tests, reports are generated:

```
playwright-report/
├── index.html          # Main HTML report with timeline
├── trace.zip           # Full trace (for debugging failures)
└── screenshots/        # Screenshots of failed tests

test-results.json       # Machine-readable results
junit.xml               # CI/CD integration format
```

### View HTML Report
```bash
npx playwright show-report
```

## 🐛 Debugging Failed Tests

### Run in Debug Mode
```bash
bash scripts/run-e2e-tests.sh --debug

# Then use Playwright Inspector:
# - Step through tests
# - Inspect DOM
# - Check network tab
```

### Run with Visible Browser
```bash
bash scripts/run-e2e-tests.sh --headed
```

### Check Screenshots
```bash
# Browser screenshots saved in test-results/ on failure
# Check what the page looked like at failure point
open test-results/chromium/e2e-test-full-console-1-Chrome.png
```

### Check Network Activity
```bash
# Trace files recorded in playwright-report/
# Load in Playwright Inspector to see:
# - API calls
# - Response times
# - Network errors
```

## 🔧 Troubleshooting

### Tests Can't Connect to Console
```bash
# Check if Console is running
curl http://localhost:8000

# Manually start Console if needed
python -m corvin_console.standalone
```

### Database Not Seeded
```bash
# Manually seed token_metrics.db
python3 scripts/seed-token-metrics.db.py

# Check it exists
ls -la ~/.corvin/token_metrics.db
```

### Feature Flag Not Visible
```bash
# Verify vibe_engineering is enabled
cat ~/.corvin/tenants/_default/global/features.json | jq '.flags.vibe_engineering'

# Should output: true
```

### Playwright Not Installed
```bash
# Install Playwright
npm install --save-dev @playwright/test

# Install browsers
npx playwright install
```

## 🎯 Success Criteria

All 11 tests should pass:
- ✅ 1️⃣ Console Navigation
- ✅ 2️⃣ Settings Page
- ✅ 3️⃣ Feature Flags API
- ✅ 4️⃣ Vibe Engineering + Token Metrics
- ✅ 5️⃣ Memory Pipeline Stage
- ✅ 6️⃣ Skills Pipeline Stage
- ✅ 7️⃣ Graph Pipeline Stage
- ✅ 8️⃣ Forge Tools Visible
- ✅ 9️⃣ Forged Skills Visible
- ✅ 🔟 Token Metrics Real-time
- ✅ 1️⃣1️⃣ Full Integration

## 📚 Related Documentation

- [Feature Flags](docs/FEATURE_FLAGS.md)
- [Token Metrics](docs/TOKEN_METRICS.md)
- [Context Pipeline](docs/CONTEXT_PIPELINE.md)
- [Forge & SkillForge](docs/FORGE_AND_SKILLFORGE.md)

## 🚀 CI/CD Integration

Add to your CI/CD pipeline:

```yaml
# .github/workflows/e2e.yml
name: E2E Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
      - run: bash scripts/run-e2e-tests.sh
      - uses: actions/upload-artifact@v3
        if: always()
        with:
          name: playwright-report
          path: playwright-report/
```

---

**Last Updated**: 2026-08-18  
**Test Suite Status**: ✅ All 11 Tests Ready
