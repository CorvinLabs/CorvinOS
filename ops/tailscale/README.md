# ops/tailscale/ — private deployment via Tailscale

The sister of `ops/docker-compose.yml`. Use this when the only people
who need the console are you (and your phone). No domain, no
certificate, no public port. See **ADR-0038** for the architecture
rationale.

## What you end up with

```
  ┌─ your laptop / phone ─┐                     ┌─ your server ─┐
  │                       │                     │               │
  │   tailscale client    │ ◀─ WireGuard mesh ─▶│  tailscale    │
  │                       │   (encrypted P2P)   │  sidecar      │
  │   browser ──┐         │                     │     │         │
  │             ▼         │                     │     ▼         │
  │   http://corvin:8000 │                     │  corvin      │
  │     /console/         │                     │  container    │
  └───────────────────────┘                     └───────────────┘
```

The corvin container has no IP of its own; it shares the tailscale
sidecar's network namespace. The tailnet sees a host called
`corvin` (configurable) and you reach the console at
`http://corvin:8000/console/`.

## One-time setup

### 1. Tailscale account + auth-key

1. Sign up at <https://tailscale.com> (free for up to 100 devices).
2. Install Tailscale on your **laptop** and your **phone** —
   <https://tailscale.com/download> — and log in to your tailnet.
3. Generate a **server auth-key** at
   <https://login.tailscale.com/admin/settings/keys>:
   - **Reusable**: NO
   - **Ephemeral**: NO
   - **Tagged**: YES — e.g. `tag:corvin-server` (define the tag
     once under *Access controls → Tag owners*)
4. Copy the `tskey-auth-…` value — you only see it once.

### 2. Configure + start

On the server:

```bash
# 1. Get the repo (or just this directory's files)
git clone https://github.com/veegee82/Corvin /opt/corvin-repo
cd /opt/corvin-repo

# 2. Seed the env file from the template
cp ops/tailscale/.env.template ops/tailscale/.env
chmod 600 ops/tailscale/.env
$EDITOR ops/tailscale/.env
#   set TS_AUTHKEY
#   set ANTHROPIC_API_KEY
#   set OPENAI_API_KEY (for voice; optional)
#   optionally rename CORVIN_TS_HOSTNAME

# 3. Pull + start
docker compose -f ops/tailscale/docker-compose.yml \
               --env-file ops/tailscale/.env \
               pull
docker compose -f ops/tailscale/docker-compose.yml \
               --env-file ops/tailscale/.env \
               up -d
```

### 3. Verify

```bash
# Sidecar joined the tailnet?
docker exec corvin-tailscale tailscale status

# Corvin responding inside the tailnet namespace?
docker exec corvin-tailscale wget -qO- http://localhost:8000/healthz
# → {"ok": true, ...}
```

### 4. Mint your first owner-token

The owner-token is the credential you'll type into the console's
login screen. Mint it on the server:

```bash
docker exec corvin /opt/corvin-venv/bin/python -c '
import sys
sys.path.insert(0, "/opt/corvin-repo/core/gateway")
sys.path.insert(0, "/opt/corvin-repo/operator/forge")
from corvin_gateway.auth import issue_token
print(issue_token("_default", label="laptop"))
'
```

Copy the `atlr_…` string into a password manager. It is shown
exactly once.

### 5. Open the console on your laptop

```
http://corvin:8000/console/
```

(MagicDNS resolves `corvin` to the tailnet IP. If your laptop
doesn't, use the full `corvin.tail-XXXX.ts.net` FQDN —
`tailscale status` on the server prints it.)

Paste the owner-token, sign in. You're done.

## Operations

| What | How |
|---|---|
| Status | `docker compose -f ops/tailscale/docker-compose.yml ps` |
| Logs (corvin) | `docker compose -f ops/tailscale/docker-compose.yml logs -f corvin` |
| Logs (tailscale) | `docker compose -f ops/tailscale/docker-compose.yml logs -f tailscale` |
| Restart | `docker compose -f ops/tailscale/docker-compose.yml restart` |
| Stop | `docker compose -f ops/tailscale/docker-compose.yml down` |
| Update image | `docker compose ... pull && docker compose ... up -d` |
| Revoke a token | `docker exec corvin ... corvin_gateway.auth.revoke_token(...)` |
| Backup state | `tar -czf corvin-backup-$(date +%F).tgz data/` |

## Caveats

- **Restarting the tailscale sidecar drops all corvin TCP connections**
  (including any open chat WebSocket) because they live in its
  namespace. Restart corvin itself when you can avoid touching
  tailscale; restart both only when changing TS_AUTHKEY or
  TS_EXTRA_ARGS.
- **Auth-key rotation**. Tailscale auth-keys default to 90-day expiry.
  Rotate by issuing a new key in the admin console, updating
  `TS_AUTHKEY` in `.env`, and `docker compose ... up -d --force-recreate
  tailscale`. The device record persists; the key just re-authenticates.
- **Hostname uniqueness.** If your tailnet already has a device called
  `corvin`, set `CORVIN_TS_HOSTNAME=corvin-prod` (or whatever) in
  `.env`. MagicDNS rejects duplicates silently otherwise.
- **No public access by design.** If you later want the console
  reachable from outside the tailnet, switch to the Caddy variant
  (`ops/docker-compose.yml`) — the bind-mounted `data/home/` survives
  the switch.

## Switching between this and the public Caddy deployment

The two compose files use different project names (`corvin-tailscale`
vs `corvin`) and identical volume layouts. To switch:

```bash
# tailscale → public
docker compose -f ops/tailscale/docker-compose.yml down
docker compose -f ops/docker-compose.yml --env-file /opt/corvin/.env up -d

# public → tailscale
docker compose -f ops/docker-compose.yml --env-file /opt/corvin/.env down
docker compose -f ops/tailscale/docker-compose.yml --env-file ops/tailscale/.env up -d
```

State (`data/home/` ↔ `/opt/corvin/home`) carries across because both
mount it as `/home/corvin` inside the container.
