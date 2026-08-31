# ops/ — production deployment

Generic, kunden-agnostisch. Bring your own keys, your own tenant ID.

## Pick your deployment shape

| You want | Use |
|---|---|
| **Console reachable only from my own devices** (laptop, phone), no domain, no public ports | [`ops/tailscale/`](tailscale/README.md) (Tailscale sidecar, ADR-0038) |
| **Console reachable from the public internet** at a domain like `corvin.example.com`, TLS via Let's Encrypt | this directory (Caddy sidecar, see below) |
| **Both at once** | run the Caddy variant for public; keep Tailscale on the host for admin access |

If unsure — Tailscale is the lower-friction starting point and you can
always switch later (state survives, layouts are identical).

---

## One-shot install on a fresh Debian/Ubuntu host

```bash
curl -fsSL https://raw.githubusercontent.com/veegee82/Corvin/main/ops/bootstrap/install.sh | sudo bash
```

What this does:

1. Installs Docker CE, UFW, fail2ban, 2 GB swap.
2. Clones the repo to `/opt/corvin-repo`, pins to the latest `v*` tag.
3. Writes `/opt/corvin/{docker-compose.yml, Caddyfile, .env}` (env seeded from template).
4. Installs `corvin-compose.service` and a nightly audit-verify timer.
5. Starts the stack and waits for the healthcheck.

The installer is idempotent — re-run any time to pull the latest tag.

## Configuration

Edit `/opt/corvin/.env`:

| Variable | Required for | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude engine | Or mount `~/.claude/.credentials.json` |
| `OPENAI_API_KEY` | Voice STT/TTS | Whisper API + TTS |
| `CORVIN_DOMAIN` | TLS | Must resolve to this host before first start |
| `CORVIN_ACME_EMAIL` | Let's Encrypt | Used for cert renewal notices |
| `CORVIN_BRIDGE_TELEGRAM=true` | Telegram | Plus a populated `settings.json` under the bind-mounted home |
| `CORVIN_BRIDGE_DISCORD=true` | Discord | Same |
| `CORVIN_BRIDGE_WHATSAPP=true` | WhatsApp | QR-code pairing happens on first start; check `docker logs corvin` |

After editing `.env`:

```bash
sudo systemctl restart corvin-compose
```

## Cloudflare-proxied DNS

If `CORVIN_DOMAIN` sits behind Cloudflare in proxy mode (orange cloud),
Let's Encrypt's HTTP-01 challenge fails because Cloudflare terminates TLS
in front of us. Two options:

1. **DNS only**: switch the record to gray-cloud, let ACME succeed,
   re-enable proxy later with Caddy presenting an Origin Cert.
2. **DNS-01 challenge**: install the Cloudflare DNS plugin in Caddy and
   provide a scoped API token — see `ops/Caddyfile.template`.

## File layout on the host

```
/opt/corvin/
├── docker-compose.yml         (rendered from ops/docker-compose.yml)
├── Caddyfile                  (rendered from ops/Caddyfile.template)
├── .env                       (operator-edited, mode 0600)
├── home/                      (bind-mounted → /home/corvin inside container)
├── data/                      (optional EBS mount-point)
├── secrets/                   (operator's encrypted bundles, never read by container)
└── backups/                   (timestamped tarballs of home/)
```

## Operations

| Task | Command |
|---|---|
| Status | `docker compose -f /opt/corvin/docker-compose.yml ps` |
| Logs (all) | `docker compose -f /opt/corvin/docker-compose.yml logs -f` |
| Restart | `sudo systemctl restart corvin-compose` |
| Audit-verify (manual) | `docker exec corvin /opt/corvin-repo/operator/voice/scripts/voice-audit.py verify` |
| Backup home | `tar -czf /opt/corvin/backups/home-$(date +%F).tgz -C /opt/corvin home` |
| Update | `cd /opt/corvin-repo && sudo git fetch --tags && sudo git checkout $(git tag -l 'v*' --sort=-v:refname \| head -n1) && sudo systemctl restart corvin-compose` |

## Audit-verify failure alerting (required post-install operator action)

The nightly `corvin-audit-verify.timer` runs `voice-audit verify --all` and
exits non-zero on any broken hash-chain (GDPR Art. 30/32 integrity event).
The companion unit `corvin-audit-verify-failure@.service` is triggered via
`OnFailure=` and writes a CRIT-level syslog entry.

**Default behaviour**: the CRIT entry goes to the system journal and syslog.
To receive active notifications you must wire the failure unit to your alerting
channel.  The installer does **not** do this automatically.

**Recommended: email via systemd**

```bash
# Install a mail transfer agent (e.g. msmtp or postfix).
# Then override the failure unit:
sudo mkdir -p /etc/systemd/system/corvin-audit-verify-failure@.service.d
sudo tee /etc/systemd/system/corvin-audit-verify-failure@.service.d/email.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/bin/sh -c 'echo "ALERT: corvin-audit-verify FAILED on $(hostname) at $(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ). Check: journalctl -u corvin-audit-verify" | /usr/bin/mail -s "Corvin audit-chain FAILURE" root'
EOF
sudo systemctl daemon-reload
```

**Verify the wiring**

```bash
# Simulate a failure (safe — does not alter audit chain):
sudo systemctl start corvin-audit-verify-failure@corvin-audit-verify.service
journalctl -u corvin-audit-verify-failure@corvin-audit-verify.service --no-pager
```

You should see the CRIT-level alert in the journal (and your configured
alert channel).  If no alert arrives, check that your MTA or notification
hook is functional before relying on automated alerting.

## Reverting

```bash
sudo systemctl stop corvin-compose
sudo systemctl disable corvin-compose corvin-audit-verify.timer
sudo docker compose -f /opt/corvin/docker-compose.yml down -v
sudo rm -rf /opt/corvin /opt/corvin-repo /etc/systemd/system/corvin-*
sudo systemctl daemon-reload
```
