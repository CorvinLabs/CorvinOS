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
   uv tool list | grep corvinos                          # one-liner / uv installs
   pip show corvinos                                     # plain pip installs
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

Pick the row that matches how CorvinOS was installed.

| Installed via | Upgrade command |
|---|---|
| one-liner (`install.sh` / `install.ps1`) or `uv tool install corvinos` | `uv tool upgrade corvinos --reinstall-package corvinos` |
| `pip install corvinos` | `pip install -U corvinos` |
| editable checkout (`pip install -e .` / `install.sh --editable`) | `git pull` in the checkout, then `pip install -e .` (or re-run the installer with `--editable`) |

Notes:

- The `--reinstall-package corvinos` on the `uv` row is deliberate: without it
  `uv tool upgrade` can resolve against a cached index and report "Nothing to
  upgrade" while PyPI already has a newer release.
- The one-liners install `corvinos[browser]` with a version **floor**, never an
  exact pin, precisely so that `uv tool upgrade` keeps working (script header,
  INST-1). If you pinned manually (`uv tool install corvinos==1.0.0`), `uv tool
  upgrade` will honour that pin forever — re-run `uv tool install --force
  corvinos[browser]` to unpin.
- Auto-update: `corvinos-serve` checks PyPI at start-up and, when
  `auto_update` is enabled in `~/.config/corvin-launcher/config.json`, runs the
  same upgrade command itself. The Windows supervisor installed by
  `install.ps1` does the same once per logon.
- Re-running the one-liner is also a valid upgrade path; it is idempotent.

Then start again:

```bash
corvinos serve               # or: corvinos-serve   (console + browser)
corvinos run                 # headless: OS + API + bridges, no console
```

---

## After the upgrade: verify

```bash
curl -s http://localhost:8765/v1/console/healthz         # "version" is the new release
corvinos status                                          # gateway + Ollama state
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

Restoring a tenant bundle on another machine:

```bash
corvinos tenant import ./tenant-_default.tar.gz --tenant-id _default   # --force-overwrite to replace
```

---

## Rollback

Rolling back the **package** is a normal downgrade of the Python package; the
**data directory** is untouched by that, so restore it from your backup only
if the newer release changed something you need reverted.

```bash
# uv-managed install
uv tool install --force "corvinos[browser]==1.0.0"     # exact version you want back
# plain pip
pip install "corvinos==1.0.0"
# editable checkout
git checkout <tag-or-commit> && pip install -e .
```

Remember to unpin afterwards (`uv tool install --force corvinos[browser]`), or
`uv tool upgrade` will stay frozen on the pinned version.

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

**`uv tool upgrade corvinos` says "Nothing to upgrade" although PyPI is newer**
— add `--reinstall-package corvinos`, or your receipt carries an exact pin
(see [Upgrade](#upgrade)).

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

**Hermes / Ollama model missing after upgrade** — models are stored by Ollama,
not by CorvinOS, and survive package upgrades. `ollama list`; if empty,
`ollama pull qwen3:4b` (or the model the installer chose for your RAM), or use
Settings → Engine → Bootstrap Hermes in the console.

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
