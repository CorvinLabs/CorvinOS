---
description: Update CorvinOS from its repository and verify the console still serves
argument-hint: "[--check] [--force]"
---

Updates the CorvinOS checkout this plugin belongs to (or the one Claude Code
was started in) and proves the result.

1. **Version check** — fetches `origin/main` and reports the checkout's commit
   and version against it. With `--check` it stops here and changes nothing.
2. **Update** — when the checkout is behind (or with `--force`), runs the
   checkout's own `update.sh` (Windows: `update.ps1`): pull, reinstall,
   rebuild the console, restart, verify, and roll back automatically if the
   new build does not come up.
3. **Verify** — polls the console with real HTTP requests until it serves the
   app.

Data and configuration (`.corvin/` inside the checkout — git-ignored — and
`~/.config/corvin-voice`) are never touched by the update and are kept as is.

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/corvin_update_command.py" $ARGUMENTS
```

Report the result in the user's language, truthfully, by exit code:
- 0 → updated (old → new commit and version) or already up to date; remind
  them to reload open console tabs (Ctrl+Shift+R) and to refresh the plugin
  with the `/plugin marketplace update …` line the script printed.
- 4 (`--check` only) → an update is available; say how many commits behind.
- 1 → the update failed and was rolled back (the previous version runs), or
  no checkout / no network — quote the printed reason.
- 2 → the rollback failed too, or the console is not healthy — quote the
  printed detail and the repair hint.
- 3 → another install/update is running; try again later.

Never report success for a non-zero exit code.
