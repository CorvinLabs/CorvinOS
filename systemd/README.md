# CorvinOS Systemd Services & Timers

Automated services for compliance and operational tasks.

## Services

### corvin-registry-snapshot.service / .timer

**Purpose:** Commit Task Registry snapshots to git (GDPR Art. 30, 32 audit trail)

**How it works:**
1. `corvin-task-registry-sync.timer` fires daily (03:00 UTC)
2. Registry synced to `~/.corvin/task_registry.json`
3. `corvin-registry-snapshot.service` runs (via timer dependency)
4. Snapshot copied to `docs/reference/task_registry_snapshots/<YYYYMMDD-HHMMSS>.json`
5. Committed to git with metadata (task count, timestamp, audit trail note)

**Installation:**

```bash
# Copy service + timer to systemd user directory
mkdir -p ~/.config/systemd/user/
cp systemd/corvin-registry-snapshot.service ~/.config/systemd/user/
cp systemd/corvin-registry-snapshot.timer ~/.config/systemd/user/

# Enable and start
systemctl --user daemon-reload
systemctl --user enable corvin-registry-snapshot.timer
systemctl --user start corvin-registry-snapshot.timer

# Verify
systemctl --user status corvin-registry-snapshot.timer
journalctl --user -u corvin-registry-snapshot.service -n 10
```

**Compliance:**
- **GDPR Art. 30:** Audit trail of task registry state (who/what/when)
- **GDPR Art. 32:** Git history provides immutable, timestamped snapshots
- **ADR-0864:** Snapshots are git-tracked for compliance reporting

**Testing:**

```bash
# Manual trigger (one-off)
systemctl --user start corvin-registry-snapshot.service

# Check latest snapshot
ls -la docs/reference/task_registry_snapshots/ | tail -5

# Verify latest is valid JSON + has tasks
jq '.tasks | length' docs/reference/task_registry_snapshots/$(ls docs/reference/task_registry_snapshots | tail -1)

# Check git history
git log --oneline --grep='Task Registry Snapshot' | head -5
```

## References

- **ADR-0864:** Git-Tracked Registry Snapshots
- **MEMORY.md:** Task Registry snapshot automation
- **compliance-baseline.md:** Audit trail requirements

---

## User-manager units: never `User=` (fixed 2026-09-20)

All Corvin units are installed into `~/.config/systemd/user/` and run under
`systemctl --user`. In the user manager, systemd 255 refuses a `User=` or
`Group=` directive with `status=216/GROUP` — "Failed to determine
supplementary groups: Operation not permitted" — **before** `ExecStart`
runs. Three units carried `User=%u` / `User=shumway` and had therefore
failed on every firing since they were installed:

| Unit | Timer | Effect while broken |
|---|---|---|
| `corvin-task-registry-sync.service` | daily 03:00 | `~/.corvin/task_registry.json` never refreshed — the context pipeline re-suggested completed ADRs |
| `corvin-stats-sync.service` | every 10 min | nothing (see below) |
| `corvin-session-cleanup.service` | daily 02:00 | sessions never cleaned |

The templates in this directory are the fixed versions; install with the
same `cp` + `daemon-reload` sequence as above. `corvin-task-registry-sync`
now runs the script through the repo's venv interpreter explicitly (the file
has no executable bit in git, so a bare `ExecStart=` on it is `203/EXEC`).

`corvin-stats-sync` had two more defects in `scripts/github_pages_stats_sync.sh`:
its default output dir was the literal `.../docs/stats` (a directory named
`...` under `$HOME`), and it fetched `/v1/stats/live` from :8765, which does
not exist — the console router lives under `/v1/console`, and
`routes/stats_live.py` is mounted by **no host on this build**. The script
now probes the API first and, when it is unavailable, prints `SKIPPED` and
exits 0 without writing anything (a 404 body is not a stats file). It also
pushes only from a dedicated pages checkout (`$GITHUB_PAGES_DIR/.git`);
the old "any enclosing git dir" clause would have committed to the CorvinOS
worktree's `main` every ten minutes.

Also observed but NOT changed: `corvin-audit-verify.service` fails with
`status=1` because the daily chain verification itself reports a problem
(a real finding, not a unit defect — see its journal); `claudeos-audit-verify`
points at the legacy AtelierOS checkout and should be removed.

