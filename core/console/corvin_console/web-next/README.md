# corvin-console — web-next

Re-launched console frontend per **ADR-0037** — Vite + React + TypeScript +
Tailwind + shadcn-react.

The 17 backend route modules in `core/console/corvin_console/routes/`
are unchanged; this is the new SPA that consumes them.

## Layout

```
web-next/
├─ index.html              # Vite entry (FOUC-prevention script kept)
├─ package.json
├─ vite.config.ts          # base: "/console/", proxies /v1 → :8765 in dev
├─ tailwind.config.ts      # design tokens (off-white / deep-navy / brass)
├─ tsconfig.json
├─ components.json         # shadcn config
└─ src/
   ├─ App.tsx              # routes: /, /login, /app/...
   ├─ main.tsx             # React + TanStack-Query + Router root
   ├─ index.css            # theme tokens (light + dark)
   ├─ components/
   │  ├─ layout.tsx        # AppLayout (sidebar+topbar) + PublicLayout
   │  ├─ theme-toggle.tsx
   │  └─ ui/               # shadcn primitives (button, card, input, …)
   ├─ lib/
   │  ├─ api.ts            # fetch wrapper + typed endpoints
   │  ├─ auth.tsx          # session context (cookie-based)
   │  └─ utils.ts          # cn() + format helpers
   └─ pages/
      ├─ landing.tsx       # public hero + persona gallery + pillars
      ├─ login.tsx         # owner-token sign-in
      ├─ dashboard.tsx     # system + bridges + audit snapshot
      └─ coming-soon.tsx   # placeholder for Iteration 2/3 modules
```

## Local dev

```bash
cd core/console/corvin_console/web-next
npm install                # one-time
npm run dev                # http://127.0.0.1:5173 (proxies /v1 to :8765)
```

Make sure the gateway is up on `127.0.0.1:8765` for the proxy to resolve.

## Production build

```bash
npm install
npm run build              # → dist/
```

The FastAPI side serves `dist/` automatically when
`CORVIN_CONSOLE_UI=next` is set (see ADR-0037 § Backend integration).

## What's in

**Iteration 1 (PR #3)**
- Landing page (public): hero, five "pillars", persona gallery.
- Login page: owner-token entry.
- App layout: sidebar with all modules, topbar with status LED,
  theme toggle, tenant fingerprint.
- Dashboard: engine, STT chain, persona count, audit-chain size,
  bridges grid, today's events by severity, top-6 personas, compliance
  snapshot.

**Iteration 2 (PR #3 + #4)**
- Personas: card-grid + detail JSON editor, bundle→user copy, re-auth save.
- Bridges: 7-tab editor, secret masking, hot-reload hint, re-auth save.
- Voice: identity + 7-field Layer-12 audience editor, live DE/EN
  preview of the TTS-audience block, read-only TTS voice + STT chain
  snapshot, reset.
- Forge: tool list with promotion buttons (session→project→user),
  detail dialog with entry JSON + impl preview.
- SkillForge: skill list with grade-aware promotion buttons (force on
  project→user), detail dialog with body preview + grade history.
- Cowork: routing-mode overview, personas-in-rotation, persona-pinning
  pointer.
- LDD: master switch, 12 layer toggles with dependency hints, presets.
- Compliance: structural guarantees, hash-chain summary, role grants,
  audit tail with severity / event-prefix / limit filters and
  expandable details panes.

**Iteration 3 (PR #4 — v1 minimal)**
- Corvin Chat: messenger-style sidebar + chat pane, streaming bubbles
  with "thinking…" placeholder, tool-use cards, ⌘+Enter send,
  per-session WebSocket lifecycle.
- Voice in: MediaRecorder API → `POST /v1/console/voice/transcribe`
  via the same STT chain bridges use; transcript drops into the
  input textarea.
- Voice out: toggle in chat header → final assistant text →
  `POST /v1/console/voice/tts` → autoplayed audio.
- ⚠ v1 minimal: direct `claude -p` subprocess, no persona pinning,
  no audit hash-chain integration. See ADR-0037 § "Iteration 3a v1
  scope" for what's queued for the follow-up amendment.

**Iteration 4 (PR #3)**
- systemd-system unit (`multi-user.target`) + watchdog timer.
- `/etc/xdg/autostart/` desktop entry → opens the console on desktop
  login (suppress via `CORVIN_NO_AUTOSTART=1`).
- Hardened installer (`core/console/install-systemd.sh`) with
  `--user-mode`, `--no-autostart`, `--uninstall`.

## What's NOT yet in

- Full bridge-adapter integration for the web chat channel —
  ADR-0037 amendment queued (persona routing, hooks, path-gate,
  hash-chain audit, /btw inject, multi-engine).
- Per-chat persona pinning editor (today: edit the bridge's
  `chat_profiles` JSON in the Bridges tab as a workaround).
- Tenant-switcher in the topbar (single-tenant owner only; cross-tenant
  remains an corvin-admin concern per ADR-0014).

## Adding a panel — TWO registrations, not one

A panel is only reachable when **both** of these exist. Registering only the
first mounts the route but leaves it invisible: the operator sees the old
console and concludes the build is stale, which it is not.

| # | File | What it does | Symptom when missing |
|---|---|---|---|
| 1 | `src/panels/registry.tsx` (`PANELS`) | `panelRoutes()` mounts `/app/<route>` | route 404s inside the SPA |
| 2 | `src/components/layout.tsx` (`NAV_GROUPS`) | renders the sidebar entry | route works, but nothing links to it |

`ConsolePanel.nav` in the registry is **not** what draws the sidebar —
`NAV_GROUPS` is a separate hand-maintained list. Add the entry there too, with
the same `requiredFlag` the panel declares.

A `requiredFlag` is gated against the backend capability manifest
(`GET /v1/console/capabilities`), so the flag must ALSO be listed in
`GATED_FLAGS` in `core/console/corvin_console/routes/capabilities.py` — a flag
absent there resolves to `false` and the nav entry stays hidden.

### Fetching from a panel

Use `/v1/console/...` (the `BASE` in `src/lib/api.ts`) with
`credentials: 'include'`. A bare path like `/vibe-engineering/state` hits the
SPA mount and returns the `index.html` shell as a 404, which surfaces in the
panel as `API error: 404 404`.

## Deploying a change

`scripts/console-deploy.sh` (repo root) rebuilds and then proves the running host
serves what was just built — it prints `LIVE assets/index-<hash>.js` or exits 2 with
the mismatch. It never builds over `dist/` directly: vite empties its outDir first,
which would take `/console/` down for the length of the build, so it stages into
`dist.next/` and swaps (`dist.prev/` is the rollback copy).

```bash
scripts/console-deploy.sh                      # clean rebuild + verify
scripts/console-deploy.sh --fast               # incremental, esbuild minify (~24s)
scripts/console-deploy.sh --marker 'NewThing'  # also assert a string is bundled
```

Two things run it so nobody has to remember:

- **`corvin-console-watch.service`** (systemd --user) watches `src/` and redeploys on
  any change, from any editor. `systemctl --user status corvin-console-watch` ·
  `journalctl --user -u corvin-console-watch -f`.
- **`.claude/hooks/console_autodeploy.sh`** fires on Claude's own edits, and stands
  down when the watcher is active so nothing builds twice.

The open browser tab is the third cache. Turn on the **`console_auto_reload`** feature
flag (Settings → Features) and a tab brings itself onto each new build; while the
operator is typing it shows a "Reload now" banner instead, so input is never lost.
Off by default — see `src/hooks/use-build-freshness.ts`.

For live-reload while iterating, `npm run dev` still serves the SPA on :5173 with HMR,
proxying the API to the console on :8765.
