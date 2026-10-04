---
description: Install CorvinOS end-to-end and open the Web Console in the browser
argument-hint: ""
---

Runs the full CorvinOS setup to a verified, stable end-state — not just until
the installer's own internal checks pass.

CorvinOS installs only from a local clone of the repository. Run this from
Claude Code started inside your CorvinOS clone.

What this does, in order:

1. If a previous install is already complete AND the console answers real
   HTTP 200 (not just "port is open"), skip straight to step 4 — no needless
   reinstall. If the flag says done but the console is unhealthy, try
   `corvin-restore` first (cheap) before a full reinstall.
2. Otherwise find the CorvinOS checkout (this plugin's own location, else the
   current directory) and run ITS installer — `install.sh` on Linux/macOS,
   `install.ps1` on Windows. Nothing is downloaded from a website; with no
   checkout the command stops and prints the `git clone` steps.
3. Poll the Web Console with real HTTP requests, with backoff, for up to
   5 minutes. A permanent 503 "build failed" fallback (the console booted
   before `dist/` existed) is detected immediately and reported with its real
   remedy (`systemctl --user restart corvin-webui` — a rebuild alone does not
   fix it, the route is decided once at boot).
4. The browser: after a fresh install the installer has already opened
   `http://127.0.0.1:8765/console/` (when a display is available); on an
   existing install this command opens it. Without a reachable display (e.g. a
   headless bridge session) it says so and prints the URL.

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/corvin_install_command.py"
```

Report the final state in the user's language, truthfully:
- exit 0 "already set up" → confirm the console is live and whether the
  browser could be opened automatically.
- exit 0 "fresh install" → same, plus a one-line summary of what was
  installed.
- exit 1 → the installer failed, or no CorvinOS checkout was found (then
  relay the printed `git clone` steps).
- exit 2 → the console never became healthy after a successful install run;
  quote the exact detail message the script printed (it already names the
  precise cause: unreachable vs. permanent build-failed fallback) and the
  remedy command. Never report success when the exit code is non-zero.
