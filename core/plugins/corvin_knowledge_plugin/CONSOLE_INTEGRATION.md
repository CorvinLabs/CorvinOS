# Corvin-Knowledge Console Integration Guide

**Plugin:** Corvin-Knowledge v1.0.0  
**Type:** Knowledge Management  
**Console Support:** ✅ Auto-Discovery + Auto-Installation  
**Dark Mode:** ✅ Supported

---

## 📊 Marketplace Auto-Discovery

The plugin is automatically discoverable in the Corvin Console Marketplace via `plugin.json`:

```json
{
  "marketplace": {
    "auto_discover": true,
    "auto_register": true,
    "console_panels": [
      {
        "id": "corvin-knowledge-graph",
        "name": "Knowledge Graph Explorer",
        "panel_type": "react-component"
      }
    ]
  }
}
```

**Installation in Console:**
```bash
# Method 1: Marketplace UI (automatic)
1. Open Corvin Console
2. Go to Plugins/Marketplace
3. Search "corvin-knowledge"
4. Click "Install"

# Method 2: CLI
corvin-cli plugin install corvin-knowledge@1.0.0

# Method 3: Direct URL
corvin-cli plugin install https://github.com/CorvinLabs/Corvin-Knowledge/releases/download/v1.0.0/corvin-knowledge-plugin.zip
```

---

## 🎨 React Panels

### Graph Explorer Panel

**Location:** `/console/plugins/corvin-knowledge/graph`  
**Component:** `GraphPanel` (main UI)

**Features:**
- Interactive graph visualization (vis-network library)
- Click entities to view details
- Double-click to focus on graph
- Hover for entity preview
- Dark mode auto-detection

**Data Flow:**
```
GraphPanel
├── GraphVisualization (entity click handlers)
│   ├── Network graph (vis.js)
│   └── EntityDetailsPanel (side panel)
└── Settings access (tab)
```

### Settings Panel

**Location:** Integrated in GraphPanel (Settings tab)  
**Component:** `SettingsPanel`

**Configurable:**
- Repository path (`repo_path`)
- Remote URL (`remote_url`)
- Auto-sync on query toggle
- Consistency level (strict/warn/ignore)
- Sync actions (pull/push/both)

**Persisted to:** `~/.claude/plugins/corvin-knowledge.json`

---

## 🔌 API Endpoints

All endpoints are RESTful and work with the console's built-in authentication.

### GET `/api/v1/console/plugins/corvin-knowledge/config`

Fetch current configuration.

**Response:**
```json
{
  "repo_path": "~/.corvin-knowledge/",
  "remote_url": "https://github.com/CorvinLabs/Corvin-Knowledge.git",
  "auto_sync_on_query": true,
  "consistency_level": "warn"
}
```

### GET `/api/v1/console/plugins/corvin-knowledge/graph`

Fetch graph data (entities + relations).

**Response:**
```json
{
  "entities": [
    {
      "id": "ADR-0568",
      "type": "decision",
      "title": "Skill Contract Schema",
      "status": "accepted",
      "tags": ["skills", "contract"]
    }
  ],
  "relations": [
    {
      "from_id": "ADR-0568",
      "to_id": "ADR-0532",
      "relation": "depends_on"
    }
  ]
}
```

### POST `/api/v1/console/plugins/corvin-knowledge/config`

Update configuration.

**Request:**
```json
{
  "repo_path": "~/.corvin-knowledge/",
  "remote_url": "https://github.com/CorvinLabs/Corvin-Knowledge.git",
  "auto_sync_on_query": true,
  "consistency_level": "strict"
}
```

### POST `/api/v1/console/plugins/corvin-knowledge/sync`

Trigger sync operation.

**Request:**
```json
{
  "sync_type": "pull" | "push" | "both"
}
```

**Response:**
```json
{
  "status": "success" | "error" | "conflict",
  "message": "Sync completed",
  "conflicts": [] // if any
}
```

### POST `/api/v1/console/plugins/corvin-knowledge/init`

Called on plugin installation.

### POST `/api/v1/console/plugins/corvin-knowledge/cleanup`

Called on plugin uninstallation.

---

## 🎨 Dark Mode Support

The panel automatically detects Corvin Console's dark mode preference via CSS custom properties.

**Light Mode Variables:**
```css
--color-background: #ffffff;
--color-border: #e0e0e0;
--color-text: #333333;
--color-primary: #2196F3;
```

**Dark Mode Variables:**
```css
--color-background: #1e1e1e;
--color-border: #404040;
--color-text: #e0e0e0;
--color-primary: #42A5F5;
```

Automatically applied via:
```typescript
useEffect(() => {
  const darkModeQuery = window.matchMedia('(prefers-color-scheme: dark)');
  setIsDarkMode(darkModeQuery.matches);
}, []);
```

---

## 📦 Installation/Uninstallation

### Install

```bash
# Via marketplace
corvin-cli plugin install corvin-knowledge

# Triggers:
# 1. Download ZIP from GitHub Releases
# 2. Extract to ~/.corvin/plugins/corvin-knowledge/
# 3. Call POST /api/.../init (creates default config)
# 4. Register panel in console (sidebar + menu)
# 5. Inject CSS variables for dark mode
```

### Uninstall

```bash
# Via marketplace or CLI
corvin-cli plugin uninstall corvin-knowledge

# Triggers:
# 1. Call POST /api/.../cleanup (removes config)
# 2. Remove from ~/.corvin/plugins/corvin-knowledge/
# 3. Unregister panel from console
# 4. Clean up any persisted state
```

---

## 🧪 Testing Panel Integration

### Local Test

```bash
# 1. Run console dev server
npm run dev:console

# 2. Install plugin locally
corvin-cli plugin install ./core/plugins/corvin-knowledge/

# 3. Navigate to plugin
# Console should automatically add:
# - Sidebar menu item
# - Settings in preferences
# - Graph panel at /console/plugins/corvin-knowledge/graph
```

### E2E Test (Headless)

```bash
# Test panel loading via Playwright
npm run test:e2e -- test/console/plugin-integration.spec.ts

# Verifies:
# - Panel loads at correct route
# - API endpoints respond
# - Graph renders without errors
# - Settings persist across page reloads
# - Uninstall removes panel cleanly
```

---

## 🚀 Next Steps

1. **Install via Marketplace** — corvin-cli plugin install corvin-knowledge
2. **Open Graph Panel** — Navigate to Plugins → Knowledge Graph Explorer
3. **Configure Repository** — Settings → Set repo path + remote URL
4. **Sync Data** — Click "Sync" to pull latest knowledge graph
5. **Explore** — Click entities to view details, double-click to focus

---

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| Panel not showing in marketplace | Restart console (`npm run dev:console`) |
| Graph not loading | Check repo configuration in Settings |
| Sync fails | Verify repo path + remote URL + network access |
| Dark mode not working | Clear browser cache (Cmd+Shift+Delete) |

---

## 📚 Related

- **Video Producer Panel** — Similar integration pattern (reference design)
- **ADR-0352** — Console as Plugin (architectural decision)
- **ADR-0471** — Console Marketplace API v2 (full API spec)

---

**Build Date:** 2026-09-18  
**Status:** Production Ready  
**Tested Platforms:** macOS, Linux, Windows
