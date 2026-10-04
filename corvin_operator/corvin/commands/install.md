---
description: Install CorvinOS end-to-end and open the Web Console in the browser
argument-hint: ""
---

Runs the full CorvinOS setup to a verified, stable end-state — not just until
the installer's own internal checks pass.

What this does, in order:

1. If a previous install is already complete AND the console answers real
   HTTP 200 (not just "port is open"), skip straight to step 4 — no needless
   reinstall. If the flag says done but the console is unhealthy, try
   `corvin-installer restore` first (cheap) before a full reinstall.
2. Otherwise run the existing `corvin-install` CLI non-interactively
   (`--yes`), or `install.sh --yes` from a local checkout if `corvin-install`
   isn't on PATH yet. This never reimplements the installer — it drives the
   one that already exists.
3. Poll the Web Console with real HTTP requests, with backoff, for up to
   5 minutes — long enough for a cold `npm install && npm run build`. A
   permanent 503 "build failed" fallback (the console booted before `dist/`
   existed) is detected immediately and reported with its real remedy
   (`systemctl --user restart corvin-webui` — a rebuild alone does not fix
   this, because the route is decided once at boot) instead of burning the
   whole budget on a state that can't self-heal.
4. Open `http://127.0.0.1:8765/console/` in the browser. If no GUI browser
   is reachable from this environment (e.g. a headless bridge session),
   say so explicitly and print the URL instead of silently doing nothing.

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/corvin_install_command.py"
```

Report the final state in the user's language, truthfully:
- exit 0 "already set up" → confirm the console is live and whether the
  browser could be opened automatically.
- exit 0 "fresh install" → same, plus a one-line summary of what was
  installed.
- exit 1 → the installer step that failed, with its own printed remedy.
- exit 2 → the console never became healthy after a successful install run;
  quote the exact detail message the script printed (it already names the
  precise cause: unreachable vs. permanent build-failed fallback) and the
  remedy command. Never report success when the exit code is non-zero.
