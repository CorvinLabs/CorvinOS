# Runbook — CORVIN_HOME data loss (audit chain, learning, tasks, config)

Written after the 2026-09-24 incident (ADR-2058): an autonomous agent ran
`git reset --hard HEAD && git clean -fxd` in the CorvinOS checkout. `CORVIN_HOME`
is `<repo>/.corvin`, so `-x` deleted every gitignored runtime file. This runbook
is the ordered, verified path back. Each step says how to prove it worked.

**Order matters: stop the damage → save what is still open → restore → prove.**
The disk was 98 % full; freed blocks are reused within minutes, so the raw
carve (step 5) loses value with every write — run it early.

## 0. Stop the cause
- Find the writer: `git reflog --date=format:%T -30` (checkouts/resets/commits
  from another agent), `ps aux | grep claude`, and the agent's transcript under
  `~/.claude/projects/<session-dir>/<id>.jsonl` (+ `subagents/*.jsonl`).
- Freeze it reversibly first (`kill -STOP <pid>`), confirm `git reflog` goes
  quiet, then terminate (`kill -KILL`; TERM would need a CONT and lets it act).
- Guard: `~/.claude/hooks/block_runtime_wipe.sh` (user-level PreToolUse on Bash)
  refuses `git clean -x/-X`, `git stash --all` and recursive removal of `.corvin`
  at command position in every Claude session on the host. Install it on every
  host that keeps CORVIN_HOME inside a checkout.

## 1. Save files that are deleted but still open
Before restarting anything: `for p in $(pgrep -f "corvin|adapter.py|daemon.js"); do ls -l /proc/$p/fd | grep deleted; done`
and `cp /proc/<pid>/fd/<n> <safe place>` (e.g. `recall.db` + `-wal` + `-shm`).
A restart closes the handle and the data is gone.

## 2. Work isolated
Never repair in the main checkout while anything else may use it. Use
`git worktree add -b <branch> .claude/worktrees/<name> main`; commit WIP early.
Never use bare `git stash` (the stack is shared across sessions).

## 3. Rebuild the environment (not data)
- Console venv + web build: `bash core/console/bootstrap.sh`; then import the app
  and install what the old venv had ad hoc until `python -c "import corvin_console.app"`
  passes (2026-09-24: `duckdb`, `scikit-learn`, `jsonschema`).
- Root venv for the adapter: `uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -e ".[compute,metrics]" scikit-learn`.
- Bridges: `npm install` with the SAME node the units run (nvm v24), not the
  PATH `npm` (node 18): `PATH=$HOME/.nvm/versions/node/<v>/bin:$PATH npm install`.
- Units: `bash corvin_operator/bridges/bridge.sh install-units` (templates were
  fixed in 6a869038c; `tests/test_unit_template_paths.py` guards them). Never
  `source bridge.sh` — its default action is `up`.

## 4. Restore config/data from the newest backup
Backups: `/home/shumway/projects/Corvin-Backup/CorvinOS (Kopie 2)/` (2026-08-31).
- `rsync -a --ignore-existing --exclude='audit*.jsonl*' --exclude=instance_id "<backup>/.corvin/" .corvin/`
  (never overwrite newer files; never copy a chain file onto a live chain path).
- Put backup chains under `.corvin/tenants/_default/global/forge/recovered/` as
  evidence (they get seamed in step 6).
- `instance_id`: restore the ORIGINAL id (peers and telemetry know it; find it
  in any surviving record's `instance_id`), not the backup's or a new one.
- Bridge `settings.json` live under `.corvin/bridges/<ch>/`; the backup keeps
  them under `operator/bridges/<ch>/settings.json`. Merge, file mode 0600.
  Rotated secrets (e.g. the Discord token) cannot come from a backup.
- Check flags the backup brings back: `a2a_lan_bind` came back ON and would have
  exposed the whole console on 0.0.0.0 — reset it to the pre-incident value.
- Repo-local `.claude/` (project hooks, settings), `.ldd/`, `.idea/`,
  `.hetzner.env`: `rsync --ignore-existing` from the backup.

## 5. Carve the raw device for what no backup has (needs sudo)
`sudo bash scripts/recovery/carve_corvin_records.sh` — read-only on the device,
writes candidate JSON lines to `/dev/shm/corvin-carve/raw.jsonl` (RAM, so the
carve cannot overwrite what it reads). Patterns cover audit-chain lines and both
learning EventStore line formats; verified byte-identical on a real chain.

## 6. Rebuild authenticated history from the carve
`python3 scripts/recovery/rebuild_from_carve.py` (dry run) then `--apply`.
Admits an audit record only if its hash recomputes AND its MAC verifies under the
out-of-tree anchor key (`~/.config/corvin-voice/audit_anchor.key`) and it belongs
to the lost chain's lineage (reachable from the lost genesis named in
`~/.config/corvin-voice/chain_ids_retired/`, or a segment dominated by this
instance_id) — so deleted TEST chains and torn fragments are rejected. Learning
lines are admitted only when their `event_id` appears in an admitted record.
Writes `recovered/audit.carved-<stamp>.jsonl` + `.manifest.json` (segments and
gaps), seams it to the live chain, and merges learning events into
`<tenant>/learning/events/YYYY-MM-DD.jsonl`.

## 7. Acknowledge the chain loss (only if the tail is unrecoverable)
Stop every writer (webui, adapter, bridges, ALL corvin timers:
`systemctl --user list-units 'corvin*.timer'`), then
`PYTHONPATH=corvin_operator/forge python -m forge.chain_loss --chain <canonical> --cause "…" --backup <each surviving chain> --i-understand-the-chain-is-lost`.
Prove: `tripwire.audit_chain_intact()` → "chain verifies"; the console boots.

## 8. Tasks panel
Work items live in `<tenant>/global/task_tracking/tasks.db` (ADR-2056), seeded
from `<tenant>/global/initiatives.json` by
`python -m corvin_console.task_tracking_import --apply` (dry run without
`--apply`). On 2026-09-24 the only surviving `initiatives.json` was a copy in
another session's scratchpad (`/tmp/claude-1000/<project>/<session>/scratchpad/h1/…`) —
search `/tmp/claude-*` and worktrees for copies of lost runtime files.
Manual panel edits after the last import are lost unless `task_item.*` records
are recovered in step 6.

## 9. Console shows history
Console readers traverse seam-linked history files
(`security_events.chain_history_files` / `iter_chain_records`); verifiers and
the tripwire read only the canonical chain. Restart `corvin-webui` after
restoring files and hard-refresh the browser.

## 10. A2A
Pairing keys lived in `corvin_operator/cowork/remote_*` (gitignored → wiped).
Surviving peers still hold the old kid: delete it there and re-pair with a fresh
token (`POST /v1/console/remote-trigger/pair/friendship/create`), transported
through the private claude-playground repo. Re-set `my-url`, `relay-url`,
`a2a_relay_fallback`.

## What cannot come back
Records overwritten before the carve. The acknowledgement record and the
carve manifest document exactly which ranges are missing — never paper over a
gap, never merge fragments into the live chain.
