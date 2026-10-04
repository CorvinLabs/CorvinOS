# CorvinOS Upgrade Guide

How to move an existing installation to a newer `corvinos` release, verify it,
and roll back if needed. Everything below uses commands that exist in the
shipped CLI (`corvinos --help`); nothing here is aspirational.

**Applies to:** `corvinos` 1.0.0 and later (see `version` in `pyproject.toml`).

---

## Table of Contents

1. [Before you upgrade](#before-you-upgrade)
2. [Upgrade](#upgrade)
3. [After the upgrade: verify](#after-the-upgrade-verify)
4. [Data layout and migration](#data-layout-and-migration)
5. [Rollback](#rollback)
6. [Troubleshooting](#troubleshooting)
7. [FAQ](#faq)

---

## Before you upgrade

1. **Know which version you run.**
   ```bash
   curl -s http://localhost:8765/v1/console/healthz     # {"ok":…,"version":"1.0.0",…}
   git -C /path/to/CorvinOS log -1 --oneline             # revision of your checkout
   ```
   (`corvinos` has no `--version` flag; the console health endpoint is the
   authoritative answer for a running instance.)

2. **Back up your data.** All state lives in two directories — copy them
   before any upgrade:
   ```bash
   tar czf corvin-backup-$(date +%F).tgz "${CORVIN_HOME:-$HOME/.corvin}" ~/.config/corvin-voice
   ```
   `~/.config/corvin-voice/service.env` holds API keys in plain text (mode
   0600) — store the archive accordingly.

   A per-tenant, portable bundle (settings, sessions, audit chain) can also be
   produced with the CLI:
   ```bash
   corvinos tenant export --tenant-id _default --output ./tenant-_default.tar.gz
   # add --with-secrets to include the encrypted secrets store
   ```

3. **Verify the audit chain is intact before you touch anything** — an
   already-broken chain must not be blamed on the upgrade:
   ```bash
   corvinos audit verify        # exit 0 = intact, exit 1 = broken (CRITICAL, see docs/audit-and-compliance.md)
   corvinos audit health        # chain ok + record count
   ```

4. **Stop the running instance.**
   ```bash
   corvinos stop                # console + bridges
   ```

---

## Upgrade

CorvinOS is installed only from a local clone of the repository, and is
upgraded from that clone.

| Installed via | Upgrade command |
|---|---|
| `./install.sh` / `install.ps1` from a checkout (or `--editable` / `-Editable`) | `sh update.sh` (Windows: `update.ps1`) in the checkout |
| manual editable checkout (`pip install -e .`) | `git pull` in the checkout, then `pip install -e .` (or switch to `sh update.sh`) |
| legacy PyPI install (`pip install corvinos` / `uv tool install corvinos`) | no longer supported — `update.sh` / `update.ps1` refuse it; clone the repository and run `./install.sh` |

Notes:

- `update.sh` fast-forwards a checkout on `main`, reinstalls it, rebuilds the
  console, restarts and verifies the new build is served — and rolls back if
  it is not. On a checkout on another branch it leaves the code alone and
  only refreshes dependencies, frontend and services.
- If you already ran `git pull` yourself, `sh update.sh --rebuild-only`
  reinstalls, rebuilds and restarts the current code without fetching.
- Legacy installer-managed trees (from the former download mode) are still
  updated by `update.sh` / `update.ps1`.
- With nothing installed, `update.sh` / `update.ps1` do not install anything;
  they print the clone + `./install.sh` steps.
- Re-running `./install.sh` from the checkout is also a valid upgrade path; it
  is idempotent.

Then start again:

```bash
corvinos serve               # or: corvinos-serve   (console + browser)
corvinos run                 # headless: OS + API + bridges, no console
```

---

## After the upgrade: verify

```bash
curl -s http://localhost:8765/v1/console/healthz         # "version" is the new release
corvinos status                                          # gateway + console state
corvinos audit verify                                    # chain still intact
corvinos diagnose                                        # installation / runtime self-check
```

Bridges (Discord, WhatsApp, …) are restarted by `corvinos serve` / `corvinos
run`; on Linux with services registered by `corvin-install`, check
`systemctl --user status 'corvin-*'`. On a console that shows a stale page,
hard-refresh the browser tab (Ctrl+Shift+R) — the SPA bundle is content-hashed
and the old tab may still hold the previous one.

---

## Data layout and migration

Data is never touched by `pip`/`uv` — the package and the data directory are
separate. The only on-disk migration CorvinOS performs is the move from the
pre-tenant layout to the tenant-native one (ADR-0007):

```
old:  ~/.corvin/global/…                    new:  ~/.corvin/tenants/_default/global/…
```

Installations created after that change already use the new layout; older
ones are migrated explicitly, with a dry-run first:

```bash
corvinos migrate to-tenant-native --dry-run          # preview, no FS changes
corvinos migrate to-tenant-native                    # perform (idempotent, marker file)
corvinos migrate verify-isolation [--tenant-id ID]   # integrity + isolation checks
corvinos migrate tenant-data-report                  # where the data lives now
```

The legacy paths stay addressable through symlinks; `--cleanup-ttl DAYS`
controls when the legacy directory is removed (default 30).

Skills use a separate, tenant-native migration: `corvinos skill migrate` (see
`corvinos skill --help`). Secrets stored in the old plaintext location can be
moved into the encrypted store with `corvinos secrets migrate`.

What is preserved across an upgrade (nothing here is rewritten by the package
upgrade itself):

- `~/.corvin/` — tenants, sessions, plugins, skills, run state
- `~/.corvin/global/forge/audit.jsonl` and
  `~/.corvin/tenants/<tenant>/global/forge/audit.jsonl` — the hash-chained
  audit logs (append-only; an upgrade adds events, it never rewrites them)
- `~/.config/corvin-voice/` — installer config, preferences, `service.env`
- `~/.config/corvin-launcher/config.json` — launcher settings (auto-update flag)
- bridge settings under `~/.corvin/bridges/<bridge>/settings.json`

### Upgrading past the Hermes / local-Ollama removal (ADR-2091)

This release removes the Hermes engine and every path that ran inference on a
local Ollama server. Nothing needs to be migrated by hand, but note:

- **Stored engine choices are mapped, never rejected.** A `default_engine`,
  `worker_engine`, per-chat engine pin or `/engine` argument equal to `hermes`,
  `hermes-*`, `local`, `ollama`, `opencode_ollama` or `claude_code_local` is
  read as `claude_code` (one WARNING `engine.legacy_mapped` per value in the
  log). `spec.hermes_model` and `spec.engine_models.hermes` are ignored. Pick
  another engine in Settings if Claude Code is not what you want.
- **Remove the old health timer.** `corvin-hermes-health.{service,timer}` are
  no longer installed. On Linux hosts that have them, run `bridge.sh down`
  followed by `bridge.sh up` — both remove the legacy units (stop, disable,
  delete; silent if absent).
- **Removed environment variables** (now ignored): `CORVIN_HERMES_URL`,
  `CORVIN_HERMES_BASE_URL`, `CORVIN_HERMES_MODEL`, `CORVIN_OLLAMA_BASE_URL`,
  `CORVIN_HOUSE_RULES_HERMES_TIMEOUT_S`, `CORVIN_HOUSE_RULES_DISABLE_HERMES`,
  `CORVIN_HOUSE_RULES_KEEP_ALIVE`, `CORVIN_HOUSE_RULES_MODEL`,
  `CORVIN_DELEGATE_HERMES_ZONE`, `CORVIN_VOICE_PREWARM`.
- **Launcher.** `corvin setup` no longer has an Ollama step, and its
  `--ollama-url` / `--model` options are gone; `ollama_url` / `model` keys in
  `~/.config/corvin-launcher/config.json` are dropped on read. `install.sh`
  no longer accepts `--no-hermes` (it now exits with "Unknown argument").
- **Egress-restricted tenants.** A tenant whose egress policy does not admit
  `api.anthropic.com` has no bundled engine it may use; the L44 house-rules
  gate runs `floor_only` for it (deny patterns deny, every other task
  escalates). Such a tenant needs a user-defined engine on an admitted
  endpoint. CONFIDENTIAL data is admissible only on `opencode_http` or a
  tenant-declared engine; SECRET data has no bundled admissible engine.
- **Ollama itself** is a separate program; CorvinOS does not uninstall it or
  its models. Remove it with your package manager if nothing else uses it.
  The hosted `ollama_cloud` provider is unaffected.

Restoring a tenant bundle on another machine:

```bash
corvinos tenant import ./tenant-_default.tar.gz --tenant-id _default   # --force-overwrite to replace
```

---

## Rollback

Rolling back the **code** means checking out an older revision of your
checkout and rebuilding; the **data directory** is untouched by that, so
restore it from your backup only if the newer release changed something you
need reverted. (`update.sh` / `update.ps1` already roll back automatically
when the new build fails verification.)

```bash
cd /path/to/CorvinOS
git checkout <tag-or-commit>
sh update.sh --rebuild-only        # Windows: .\update.ps1 -RebuildOnly
```

Return to `main` (`git checkout main`) before the next `sh update.sh`; on
another branch the updater leaves the code alone.

To restore data:

```bash
corvinos stop
tar xzf corvin-backup-<date>.tgz -C /          # restores ~/.corvin and ~/.config/corvin-voice
corvinos audit verify                          # the restored chain must verify
corvinos serve
```

Do **not** edit `audit.jsonl` files by hand (no `sed -i`): every record is
hash-chained and a manual edit breaks verification permanently.

---

## Troubleshooting

**`update.sh` says this CorvinOS was installed from PyPI** — PyPI installs
are no longer supported. Clone the repository and run `./install.sh` from the
checkout (your data in `~/.corvin/` is kept).

**`corvinos-serve` / `corvinos` not found after upgrading** — the tool
environment was rebuilt; open a new terminal so PATH is re-read, or run `uv
tool update-shell`.

**Console shows the old UI after upgrading** — hard-refresh the tab
(Ctrl+Shift+R). If `/v1/console/healthz` still reports the old version, the
old server is still running: `corvinos stop`, then `corvinos serve`.

**Port 8765 already in use** — `corvinos stop`; if that does not free it,
`corvinos serve --port 9000`.

**`corvinos audit verify` exits 1 after the upgrade** — compare with the
pre-upgrade check. If the chain verified before and not after, stop the
instance and consult `docs/audit-and-compliance.md` before anything else; a
broken chain is a CRITICAL security event, not a cosmetic one.

**Chat reports that Claude Code is not installed or not logged in after
upgrading from a Hermes install** — the Hermes engine was removed (ADR-2091) and a stored
`hermes` engine is now read as `claude_code`. There is no automatic fallback
engine: install the Claude Code CLI and log in (`claude`), or pick another
engine in Settings.

**Something else** — `corvinos diagnose` (Windows: `corvinos diagnose windows`)
prints a self-check; `~/.corvin/logs/console.log` has the server log.

---

## FAQ

**Do I have to upgrade?** No. Releases are additive; security fixes are called
out in the GitHub release notes.

**Will my data be lost?** No — package and data are separate. Take the backup
anyway; it costs seconds.

**Can I skip versions?** Yes. There is no release-by-release migration chain;
the tenant-native migration above is the only layout change and it is
idempotent.

**How long does it take?** The package upgrade is a download of a few MB plus
its dependencies; typically under a minute. Downtime is the `corvinos stop` /
`corvinos serve` gap.

---

## Support

- Issues: https://github.com/CorvinLabs/CorvinOS/issues
- Discussions: https://github.com/CorvinLabs/CorvinOS/discussions
- Installation: [INSTALLATION.md](INSTALLATION.md) · Compliance: `docs/audit-and-compliance.md`
