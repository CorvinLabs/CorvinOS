# Corvin Console Development Guide

## Quick Start

### Option 1: Full Development Mode (Recommended)
Run both Vite (frontend HMR) + Uvicorn (backend auto-reload) together:

```bash
cd core/console  # from repo root
bash dev.sh
```

This will start:
- **Vite dev server** on http://localhost:5173 (instant hot reload for React/TypeScript)
- **Uvicorn** on http://localhost:8765 (auto-reload on Python changes)
- **Console UI** at http://localhost:8765/console

### Option 2: Manual Frontend-Only Development
If you only need the frontend to hot-reload:

```bash
cd core/console/corvin_console/web-next
npm run dev
```

Then in another terminal, start the backend:
```bash
cd core/console
.venv/bin/python -m uvicorn corvin_gateway.app:app \
  --host 127.0.0.1 --port 8765 --reload --reload-dir .
```

---

## Making Changes

### Frontend Changes (TypeScript/React)
1. Edit files in `web-next/src/`
2. Vite detects changes → **instant HMR** (no page reload needed)
3. Changes appear in browser immediately

### Backend Changes (Python)
1. Edit files in `corvin_console/routes/` or other Python files
2. Uvicorn detects changes → **auto-reloads in ~1-2 seconds**
3. Refresh browser to see API changes

### After Building Static Files
When you run `npm run build` in `web-next/`:
1. It generates `web-next/dist/`
2. Uvicorn automatically serves the new static files from `dist/`
3. No server restart needed (already watched by reload)

---

## Build for Production

```bash
cd core/console/corvin_console/web-next
npm run build
```

This creates optimized production bundles in `web-next/dist/`.

The Uvicorn server will automatically serve the latest `dist/` on every request.

---

## Troubleshooting

### "Changes don't show up"
- **Frontend**: Check Vite is running on port 5173 (browser console should show no errors)
- **Backend**: Check Uvicorn is running with `--reload` flag
- **Static files**: Run `npm run build` after major changes, files load from `dist/`

### "Port 8765 already in use"
```bash
pkill -f "uvicorn.*8765"
# or
lsof -ti:8765 | xargs kill -9
```

### "Vite not reloading"
- Ensure you're editing in `web-next/src/` (not `dist/`)
- Check `npm run dev` is running
- Clear browser cache (Cmd+Shift+R or Ctrl+Shift+R)

---

## Key Files

| Path | Purpose |
|------|---------|
| `web-next/src/pages/voice.tsx` | Voice settings page (latest changes) |
| `web-next/src/lib/api.ts` | API type definitions & fetch helpers |
| `corvin_console/routes/profile.py` | Backend profile & TTS voice endpoints |
| `corvin_console/app.py` | FastAPI router & static file mounting |

---

## Environment

- **Node.js**: v18+ (check: `node -v`)
- **Python**: 3.13+ (check: `python --version`)
- **Vite**: v5.4.9
- **React**: v18.3.1
- **FastAPI**: Latest

Run `npm install` in `web-next/` if dependencies are missing.
