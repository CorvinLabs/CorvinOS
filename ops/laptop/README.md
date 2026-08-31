# ops/laptop/ — operator-laptop integration

Companion artefacts that live on the **operator's laptop** (not on the
server, not in the container). Together they make the remote console
behave like a local-only install.

## What's here

| File | Purpose |
|---|---|
| [`corvin-tunnel.service`](corvin-tunnel.service) | systemd-user unit that keeps an SSH-tunnel `localhost:9000 → server:8000` alive across reboots, network blips and ssh disconnects. |
| [`corvin-claude-creds-sync.service`](corvin-claude-creds-sync.service) | one-shot unit that copies the laptop's `~/.claude/.credentials.json` to the remote container's bind-mount. |
| [`corvin-claude-creds-sync.timer`](corvin-claude-creds-sync.timer) | fires the above every 30 min so the OAuth token in the container is always fresh (it has ~8 h life). |
| [`corvin-sync-claude-creds.sh`](corvin-sync-claude-creds.sh) | the actual sync script the service runs. |

## One-time install

```bash
# 1. Drop the script in
mkdir -p ~/.local/bin
cp ops/laptop/corvin-sync-claude-creds.sh ~/.local/bin/
chmod +x ~/.local/bin/corvin-sync-claude-creds.sh
# Edit the DST_HOST line if your server IP changes.

# 2. Drop the systemd-user units in
mkdir -p ~/.config/systemd/user
cp ops/laptop/corvin-*.service ~/.config/systemd/user/
cp ops/laptop/corvin-*.timer   ~/.config/systemd/user/

# 3. Enable + start
systemctl --user daemon-reload
systemctl --user enable --now corvin-tunnel.service
systemctl --user enable --now corvin-claude-creds-sync.timer

# 4. (One-time, if not already on) so they survive logout
loginctl enable-linger "$USER"
```

## Verifying everything is healthy

```bash
# tunnel
systemctl --user status corvin-tunnel.service --no-pager
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:9000/healthz

# creds sync
systemctl --user list-timers corvin-claude-creds-sync.timer --no-pager
journalctl --user -u corvin-claude-creds-sync.service -n 20 --no-pager
```

## What happens when …

| Event | Result |
|---|---|
| Laptop sleeps + wakes | tunnel reconnects ≤ 10 s, next sync within 30 min |
| Laptop reboots | both units start automatically (linger keeps them outside login) |
| SSH key rotated on server | sync script logs "scp-via-stdin failed"; you re-run `ssh-add` |
| Local claude token expired | sync script logs "local credentials are expired — skipping" |
| Remote container restarted | nothing on the laptop side breaks; tunnel reconnects, sync re-fires next interval |

## Why a sync timer instead of refreshing in the container

The `claude` CLI in the container would normally refresh the OAuth token
itself when it spawns. In the headless container context that refresh
sometimes fails silently — the visible symptom is `Failed to authenticate.
API Error: 401 Invalid authentication credentials` in the chat UI 8 hours
after deploy. The laptop's own `claude` CLI refreshes its credentials
transparently when you use it locally; mirroring that fresh file to the
container's bind-mount is the cheapest fix that also survives container
restarts.
