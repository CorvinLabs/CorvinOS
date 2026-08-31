# Vibe Engineering Console Implementation — Phase 1 Guide

**Status:** HANDOFF READY (Session 3)  
**Scope:** Dashboard + Navigation (3-column layout)  
**Estimate:** 2-3 hours (one focused dev session)  
**Quality Gate:** LDD k=1-3 validation before deploy

---

## REFERENCE ARCHITECTURE

**Existing Files (Read First):**
- `CONSOLE_REDESIGN_UNIFIED_CONCEPT.md` — Full UX spec (what to build)
- `core/console/corvin_console/web-next/src/pages/vibe-engineering.tsx` — Current implementation
- `core/console/corvin_console/routes/vibe_engineering.py` — Backend routes
- `core/context_pipeline/v2_context_preservation.py` — Live data source (Option B)

**Key Endpoints (Already Exist):**
- `/vibe-engineering/traces` — Get recent context-engineered turns
- `/vibe-engineering/explain/{hash}` — Get full brief text (audit trail)
- `/vibe-engineering/config` — Get/set tier policy

---

## IMPLEMENTATION STEPS

### **Step 1: Create Component Structure** (30 min)

**Create directory:**
```bash
mkdir -p core/console/corvin_console/web-next/src/pages/vibe-engineering/
```

**Create component files:**
1. `Dashboard.tsx` — Main 3-column view
2. `BrainStatus.tsx` — Column 1 (worker subsystems)
3. `ContextIntelligence.tsx` — Column 2 (pipeline layers)
4. `LearningHub.tsx` — Column 3 (talent + skills)
5. `BrainMonitor.tsx` — Secondary view (subsystem detail)
6. `SessionExplorer.tsx` — Secondary view (task history)
7. `hooks/useVibeData.ts` — Fetch live data from backend

---

### **Step 2: Implement Dashboard Component** (90 min)

**File: `Dashboard.tsx`**

```typescript
// Core structure:
// - Navigation tabs (Dashboard | Brain Monitor | Context | Learning | Sessions)
// - 3-column grid (responsive: 1/2/3 cols based on viewport)
// - Real-time updates (poll every 2-5s or WebSocket)
// - Click drill-downs to modals

// Data flow:
// useVibeData() → fetch /vibe-engineering/traces + /vibe-engineering/config
// State: currentTrace, contextLayers, talentData, workerStatus
// Render: <BrainStatus> <ContextIntelligence> <LearningHub>
```

**Key imports:**
```typescript
import { OriginalContext, PipelineContext } from "@/adapters/vibe";
import { Brain, Network, Sparkles, TrendingUp, AlertCircle } from "lucide-react";
```

**Column 1 (BrainStatus):**
- Active task title + elapsed time
- Worker status (4 subsystems): CostController, SafetyValidator, LoopEngineer, Orchestrator
- Decision queue (current decision + confidence)
- Last 5 decisions (scrollable)
- Visual: status dots 🟢🟡🟠, confidence bars

**Column 2 (ContextIntelligence):**
- ORIGINAL CONTEXT (immutable, locked icon)
  - Task description (truncated, click to expand)
  - User intent (truncated)
  - Hash integrity (✓ valid)
  - Click → modal with full text
  
- PIPELINE CONTEXT (live)
  - TIER_1, TIER_2, TIER_3 badges (count)
  - Entropy gauge (0-100%, red/yellow/green)
  - Last 3 additions (text, source, confidence)
  - Quality gate selector (TIER_1 | TIER_2 | TIER_3)

**Column 3 (LearningHub):**
- Talent score (0-100%, with sparkline)
- Recent feedback (last 3 events)
- Active skills (top 3, with grades)
- Learning rate (tasks completed, avg confidence)

---

### **Step 3: Implement Data Fetching** (45 min)

**File: `hooks/useVibeData.ts`**

```typescript
export function useVibeData(options = {}) {
  const [traces, setTraces] = useState<Turn[]>([]);
  const [config, setConfig] = useState<Config>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetch = async () => {
      const [tracesRes, configRes] = await Promise.all([
        fetch("/vibe-engineering/traces?limit=1"),
        fetch("/vibe-engineering/config"),
      ]);
      setTraces(await tracesRes.json());
      setConfig(await configRes.json());
      setLoading(false);
    };

    fetch();
    const interval = setInterval(fetch, 5000); // Refresh every 5s
    return () => clearInterval(interval);
  }, []);

  return { traces, config, loading };
}
```

---

### **Step 4: Wire Navigation** (30 min)

**Update: `core/console/corvin_console/app.py`**

Add to nav config:
```python
{
  "label": "Vibe Engineering",
  "href": "/console/vibe-engineering",
  "icon": "Sparkles",
  "children": [
    {"label": "Dashboard", "href": "/console/vibe-engineering"},
    {"label": "Brain Monitor", "href": "/console/vibe-engineering/brain"},
    {"label": "Context", "href": "/console/vibe-engineering/context"},
    {"label": "Learning", "href": "/console/vibe-engineering/learning"},
    {"label": "Sessions", "href": "/console/vibe-engineering/sessions"},
  ]
}
```

---

### **Step 5: Build & Deploy** (30 min)

```bash
cd core/console/corvin_console/web-next

# Clear caches (CRITICAL)
rm -rf dist/ node_modules/.vite/

# Build
npm run build

# Verify hashes changed
grep -o 'assets/[^"]*\.js' dist/index.html | head -3

# Restart console
# (User runs: /console app restart)

# Hard refresh in browser (CRITICAL)
# Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac)
```

---

## QUALITY GATES (LDD k=1-3)

### **k=1: Mechanical Implementation**
- [ ] All 3 columns render (no crashes)
- [ ] Data fetches from backend
- [ ] Responsive layout (mobile/tablet/desktop)
- [ ] Navigation works (tab switching)

### **k=2: Live Data Verification**
- [ ] Dashboard shows real Context Pipeline data (not mocked)
- [ ] Entropy gauge updates in real-time
- [ ] Original Context hash validates (✓ or ✗)
- [ ] Tier selector changes prompt instantly

### **k=3: Integration & Polish**
- [ ] Drill-down modals work (click card → detail)
- [ ] Dark mode renders correctly
- [ ] Accessibility: keyboard nav (Tab, Arrow keys)
- [ ] Performance: <500ms time-to-interact

---

## TESTING CHECKLIST

**Manual Test Plan:**
1. Open /console/vibe-engineering
2. Verify all 3 columns visible
3. Click Brain Status card → drill-down modal
4. Click "Show Prompt" → full prompt modal
5. Change quality gate (TIER_1 → TIER_2) → immediate update
6. Entropy gauge: verify colors (green <30%, yellow 30-60%, red >60%)
7. Tab to Brain Monitor view → subsystem detail
8. Refresh page → data persists
9. Hard refresh (Ctrl+Shift+R) → new bundle loads (verify hashes in network tab)

**Automated Tests:**
- E2E Playwright: navigate to /vibe-engineering, verify 3 columns render
- Component tests: BrainStatus, ContextIntelligence, LearningHub
- Hook tests: useVibeData fetch + polling

---

## PRODUCTION CHECKLIST

Before declaring "live":
- [ ] All 3 columns render correctly (no console errors)
- [ ] Data fetches from live backend (not cached)
- [ ] Responsive design works (tested on mobile/tablet/desktop)
- [ ] Dark mode verified
- [ ] Accessibility audit passed (WCAG 2.1 AA)
- [ ] Hard refresh in browser shows new bundle (hash verification)
- [ ] Operator can navigate to /vibe-engineering from Console nav
- [ ] All drill-down modals functional
- [ ] k=1-3 LDD gates passed

---

## KNOWN GOTCHAS

1. **Cache layers:** Console has 3 cache layers (vite pre-bundle, dist/, browser cache). MUST clear ALL before deploy.
2. **Hard refresh required:** User must do Ctrl+Shift+R after npm run build (Claude cannot do this).
3. **Data source:** Dashboard pulls from `/vibe-engineering/traces` — ensure this endpoint exists + returns real data.
4. **Real-time updates:** Use polling (5s interval) or WebSocket for live data refresh.
5. **Responsive design:** Test on actual mobile device, not just browser dev tools.

---

## FILES TO MODIFY

**Backend:**
- `core/console/corvin_console/app.py` — Add navigation entry
- `core/console/corvin_console/routes/vibe_engineering.py` — Add `/vibe/dashboard` route (if needed)

**Frontend:**
- `core/console/corvin_console/web-next/src/pages/vibe-engineering.tsx` — Replace with new Dashboard
- `core/console/corvin_console/web-next/src/pages/vibe-engineering/Dashboard.tsx` — NEW
- `core/console/corvin_console/web-next/src/pages/vibe-engineering/BrainStatus.tsx` — NEW
- `core/console/corvin_console/web-next/src/pages/vibe-engineering/ContextIntelligence.tsx` — NEW
- `core/console/corvin_console/web-next/src/pages/vibe-engineering/LearningHub.tsx` — NEW
- `core/console/corvin_console/web-next/src/pages/vibe-engineering/hooks/useVibeData.ts` — NEW
- `core/console/corvin_console/web-next/src/components/layout.tsx` — Add nav entry

---

## SUCCESS CRITERIA

✅ **Phase 1 DONE when:**
1. Dashboard live at /console/vibe-engineering
2. All 3 columns visible + responsive
3. Live data from Option B Context Pipeline
4. Entropy gauge working
5. Quality gate selector functional
6. Hard-refresh verified (new bundle loads)
7. No console errors
8. k=1-3 LDD gates passed

---

**READY FOR SESSION 3 IMPLEMENTATION** 🚀

This is a detailed blueprint. Follow it step-by-step, and Phase 1 will be production-ready.
