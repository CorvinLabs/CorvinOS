# core/console/systemd/

Systemd-unit templates + xdg-autostart entry for the **Corvin
Operator UI** (ADR-0037 § "Always-on + auto-open").

## Files

| File | Purpose |
|---|---|
| `corvin-operator-ui.service.in` | system-wide unit, `WantedBy=multi-user.target` |
| `corvin-operator-ui-watchdog.service.in` | one-shot curl probe to `/healthz` |
| `corvin-operator-ui-watchdog.timer` | fires the watchdog every 60s |
| `corvin-operator-ui.desktop.in` | xdg-autostart entry — opens the console in default browser on desktop login |

Templates carry `__REPO_ROOT__`, `__SERVICE_USER__`, `__SERVICE_GROUP__`
placeholders that the installer renders at install time.

## Install

```bash
sudo bash core/console/install-systemd.sh
```

What this does:
1. Creates system user `corvin` (idempotent).
2. Creates `/opt/corvin/`, `/var/log/corvin/`, `/etc/corvin/` (idempotent).
3. Renders the three unit files into `/etc/systemd/system/`.
4. Drops the desktop autostart file into `/etc/xdg/autostart/`.
5. `systemctl daemon-reload && systemctl enable --now corvin-operator-ui.service`.

## Alternative installs

```bash
# Per-user (no sudo, lives under ~/.config/systemd/user/):
bash core/console/install-systemd.sh --user-mode
# (You probably also want: loginctl enable-linger $USER )

# System-wide service but NO browser auto-open:
sudo bash core/console/install-systemd.sh --no-autostart

# Use a different service user:
sudo bash core/console/install-systemd.sh --service-user myuser

# Clean revert:
sudo bash core/console/install-systemd.sh --uninstall
```

## Suppress the browser auto-open per session

Set `CORVIN_NO_AUTOSTART=1` in your desktop environment — the `.desktop`
file checks the env at activation time.

## Hardening

The service unit ships with a defense-in-depth profile:
`NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome`, `PrivateTmp`,
`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`,
`MemoryDenyWriteExecute`, `CapabilityBoundingSet=` (drop all),
`SystemCallArchitectures=native`. `ReadWritePaths` is the only place
the service may write outside `/tmp` and the repo's `core/` tree.

If you add a new write-path (e.g. for SQLite under `<corvin_home>`),
extend `ReadWritePaths=` in the template, not in a sidecar.
