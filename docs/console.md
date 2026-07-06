# Corvin — Web Console

The Corvin web console is a browser-based management interface that gives you visibility
into every running component: bridge status, active personas, the audit trail, forge
tools, skills, and more. It is served by the adapter's built-in uvicorn HTTP server.

This guide covers three deployment scenarios: local desktop, remote server with Caddy,
and Docker.

---

## Section 1: Local desktop

### Start the adapter

```bash
bash operator/bridges/bridge.sh up
```

This starts the adapter (which includes uvicorn on `127.0.0.1:8765`) plus all
configured bridge daemons. The adapter must be running for the console to be
accessible.

### Open the console

Open `http://localhost:8765/console/` in your browser — no token required for local access.
The loopback binding (`127.0.0.1`) is the security boundary. The console auto-logs you in
when you connect from localhost.

---

## Section 2: Remote server (self-hosted with Caddy)

When Corvin runs on a remote server (VPS, cloud VM, home server with port
forwarding), use Caddy as a TLS-terminating reverse proxy. The `ops/Caddyfile.template`
in the repository is pre-configured for this.

### Requirements

- A domain name with DNS A record pointing to your server's IP.
- Ports 80 and 443 open in your firewall (Caddy needs both for ACME challenges).
- Caddy installed: [caddyserver.com/docs/install](https://caddyserver.com/docs/install).

### Configuration

Set your domain and ACME email. The simplest approach is a `.env` file in the
repository root:

```bash
echo "CORVIN_DOMAIN=corvin.example.com" >> .env
echo "CORVIN_ACME_EMAIL=you@example.com" >> .env
```

Or export them directly in your shell:

```bash
export CORVIN_DOMAIN=corvin.example.com
export CORVIN_ACME_EMAIL=you@example.com
```

### Start the adapter

```bash
bash operator/bridges/bridge.sh up
```

This starts uvicorn on `127.0.0.1:8765`. Caddy proxies public HTTPS traffic to it.

### Start Caddy

```bash
caddy run --config ops/Caddyfile.template
```

On first run, Caddy automatically obtains a TLS certificate from Let's Encrypt.
This takes 5–30 seconds and requires the domain's DNS to already resolve to your
server.

To run Caddy as a systemd service:

```bash
sudo cp ops/systemd/caddy.service /etc/systemd/system/
sudo systemctl enable --now caddy
```

### Access the console

Open `https://corvin.example.com/console/` in your browser. Log in with an owner
token generated on the server:

```bash
python -m corvin_gateway.cli token issue _default --label owner-console
```

### Security notes for remote deployments

- The `ops/Caddyfile.template` restricts `/console/` to HTTPS and redirects HTTP.
- Do not expose port 8765 directly on a public interface — let Caddy proxy it.
- Consider adding IP allowlisting in the Caddyfile if the console should only be
  accessible from specific IP ranges.

---

## Section 3: Docker (ops/ stack)

The `ops/docker-compose.yml` includes both the Corvin adapter and a Caddy container.
No separate Caddy installation is needed.

### Configuration

Create or edit `/opt/corvin/.env`:

```bash
CORVIN_DOMAIN=corvin.example.com
CORVIN_ACME_EMAIL=you@example.com
# Add bridge tokens here as well, or mount them via volumes:
TELEGRAM_TOKEN=7123456789:AAF...
```

### Start the stack

```bash
docker compose -f ops/docker-compose.yml up -d
```

This starts:
- `adapter` — the Python adapter + uvicorn on the internal network
- `caddy` — TLS termination + reverse proxy, listening on 0.0.0.0:443

Check that everything is running:

```bash
docker compose -f ops/docker-compose.yml ps
```

### Access the console

Open `https://<domain>/console/` once DNS resolves and Caddy has obtained the certificate.

Generate an owner token inside the container:

```bash
docker compose -f ops/docker-compose.yml exec adapter \
  python -m corvin_gateway.cli token issue _default --label owner-console
```

### Updating

```bash
git pull
docker compose -f ops/docker-compose.yml build
docker compose -f ops/docker-compose.yml up -d
```

The adapter restarts automatically when the container is replaced. Open sessions
on messenger bridges may briefly drop — they reconnect within a few seconds.

---

## Console sections

Once logged in, the console is divided into sections accessible via the left sidebar.

### Dashboard

An at-a-glance overview: bridge status indicators (green/red), message count for the
last 24 hours, active sessions, forge tool count, skill count, and recent audit events.
A timeline chart shows message volume by bridge.

### Bridges

Per-bridge status panel: running state, PID, uptime, last message timestamp, whitelist
summary, and a live log tail (last 50 lines). You can stop or restart individual bridges
from this page without touching others.

### Personas

Lists all active personas (bundle + user overrides). For each persona: name, description,
`permission_mode`, active `ldd_preset`, assigned forge tools, and which chats currently
have this persona pinned. You can create a new persona, edit an existing one, or delete
a user override. Bundle personas can be viewed but not edited here — edit them in the
repository.

### Voice

Voice pipeline controls: TTS provider status (OpenAI or local), STT provider, current
`voice_summary_mode`, and the listener profile viewer. You can preview a TTS sample with
a custom text string without sending anything to a chat.

### Forge

The forge tool registry: all tools in all scopes (task / session / project / user),
their schema, last-used time, and sandbox config. You can inspect a tool's full JSON
schema, promote it to a higher scope, or delete it. Policy violations that occurred
in the last 24 hours are highlighted.

### Skills

The SkillForge skill registry: all skills in all scopes with their grade history, auto-grade
scores, and promotion status. You can read the full skill markdown, manually trigger a
grade, promote a skill, or purge it. Skills blocked by the linter show the violation
reason.

### Cowork

Multi-persona routing view: a table of all active chats with their current persona
assignment (pinned, auto-routed, or fallback), the router's confidence score for
auto-routed chats, and the last routing decision timestamp. You can pin a persona to
a chat directly from this page.

### LDD

LDD layer status: which of the 12 LDD layers are enabled globally and per-persona.
Toggle layers on or off, apply presets (off / light / full), and view the last LDD
trace entries per chat.

### Compliance

Compliance reports for EU AI Act Art. 50 and GDPR Art. 30. Lists disclosure events
(when the bot identified itself to a new user), consent events (opt-in / opt-out
timestamps), and a summary of audit events by category. A "Generate Report" button
produces a downloadable JSON report for the current tenant.

Also: the data classification matrix (L34) and egress gate status (L35) for each
configured engine, and the current erasure request queue (L36).

### Engines

Engine configuration viewer: which WorkerEngine is active (`ClaudeCodeEngine`,
`CodexCliEngine`, `OpenCodeEngine`, or `HermesEngine`), the adaptive model selector's
current tier (Haiku / Sonnet), and any pending engine restarts. You can view
engine-level audit events and the last stderr tail from each engine subprocess.

**Engine selector (M2.4):** REST endpoints at
`GET/PUT /v1/console/settings/engine` let the operator switch the tenant-level
default engine between Claude Code and Hermes without editing JSON files.
`GET /v1/console/settings/engine/health` probes Ollama availability. The setting
writes to `tenant.corvin.yaml::spec.default_engine` and takes effect on the next
turn — no adapter restart needed. Hermes engine requires Ollama running locally;
the health endpoint reports reachability and pulled-model count.

### Compute

Compute worker status (L25): enabled/disabled state per tenant, active jobs (strategy
optimization runs), job history with outcome summaries, and the worker socket path.
A "Run Grid Search" form lets you start a compute job from the console without using
the chat interface.

### Settings

Account settings: your active tokens (IDs and expiry dates), consent history, and
role assignments. Operator settings: **auto-update toggle** (enable/disable the
automatic upgrade to the latest PyPI release at every startup — default on, stored
in `~/.config/corvin-launcher/config.json`). The updater is install-flavour aware:
a `uv tool install` (the Windows one-line installer default) upgrades via
`uv tool upgrade corvinos`, a `pip install` upgrades via `pip install`. On Windows
the autostart supervisor (Scheduled Task `CorvinOS-Console`) also runs this upgrade
once per logon/boot **before** launching the console, so an always-on machine still
lands on the newest version; it is best-effort and never blocks startup (offline /
timeout / missing `uv` just logs and continues). Also here: tenant config
(`tenant.corvin.yaml` viewer), and the `bridge.sh doctor` output on demand.
Token revocation is available here.

### Chat

A lightweight web-based chat interface that connects to the adapter as if it were a
bridge. Useful for testing persona behavior, forge tools, and skill injection without
opening a messenger app. Sessions initiated from the console are labeled `[console]`
in the audit log.
