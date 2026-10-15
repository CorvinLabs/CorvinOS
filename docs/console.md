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
bash corvin_operator/bridges/bridge.sh up
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
bash corvin_operator/bridges/bridge.sh up
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
`CodexCliEngine`, `OpenCodeEngine`, or `CopilotCliEngine`), the adaptive model selector's
current tier (Haiku / Sonnet), and any pending engine restarts. You can view
engine-level audit events and the last stderr tail from each engine subprocess.

**Engine selector (M2.4):** REST endpoints at
`GET/PUT /v1/console/settings/engine` read and write the tenant-level default
engine and per-engine model pins without editing JSON files. `PUT` accepts only
`claude_code` as `default_engine` (anything else → 422). A stored legacy value
(`hermes`, `ollama`, `claude_code_local`, … — engines removed by ADR-2091) is
read back as `claude_code`. The setting writes to
`tenant.corvin.yaml::spec.default_engine` and takes effect on the next turn — no
adapter restart needed. `GET /v1/console/settings/engine/health` reports the
Claude Code engine as ready (it no longer probes a local Ollama server).

### Compute

Compute worker status (L25): enabled/disabled state per tenant, active jobs (strategy
optimization runs), job history with outcome summaries, and the worker socket path.
A "Run Grid Search" form lets you start a compute job from the console without using
the chat interface.

### Settings

Account settings: your active tokens (IDs and expiry dates), consent history, and
role assignments. Operator settings: **auto-update toggle** (default on, stored in
`~/.config/corvin-launcher/config.json`; no file or no `auto_update` key means enabled,
only an explicit `false` turns it off). CorvinOS installs only from a local checkout
(ADR-2215), so the toggle means: **after every start, if `origin/main` has commits this
checkout lacks, run the checkout's own `update.sh` / `update.ps1`**. The trigger is
commit-based, not version-based - a commit without a version bump is an update too.
It is scheduled 60 s after startup by both hosts (`corvin_console.app` and
`corvin_gateway.app`), tries each remote commit **once** (a rolled-back update leaves
HEAD behind and would otherwise re-run on every restart), and never blocks startup.
The launcher's PyPI/`uv tool upgrade` path only applies to non-checkout installs and is
a no-op for a checkout. The **Update** card also offers a manual "Check & Install" button and a **trace** of the last run:
the phase (triggered, delivery, UI rebuild, update script, verification, completed or failed),
five proofs (delivered to this install, UI rebuilt, script finished, verified over the wire,
installed commit current) and **Last fully verified update** (`last_success_at`, with its
trigger). The updater reports each stage as a JSON line in `update_progress.jsonl`
(`CORVIN_UPDATE_PROGRESS`, set by the console; a manual `update.sh` run is unaffected). Its claims
are cross-checked against the tree (HEAD vs. the reported revision, the commit that was
`origin/main` at start, the entry bundle in `dist/index.html`). A run with exit code 0 but an
unproven link ends as `unverified` and never moves `last_success_at`. It is a thin client for the install's own `update.sh` (Linux/macOS) /
`update.ps1` (Windows) at the root of the source tree (ADR-2054, ADR-2215): the tree is
found via `CORVIN_SRC_DIR`, the checkout the console runs from, or the installer-managed
tree. On Linux with a systemd user session it runs as its own transient unit
(`corvin-update-<ts>`) and records its exit code itself, because the updater restarts
every `corvin-*.service` and would otherwise be killed with the unit it was started from.
Nothing is copied to `~/.corvin/scripts`, and there is no second updater.
`GET /settings/update/check` returns `has_update`, `behind`, `local_sha`, `remote_sha`, `running` and `last_update`
(`at`, `status` = running|success|failed|interrupted|unverified, `phase`, `steps`, `checks`, `trigger`, `last_success_at`, `version_before`, `version_after`); `POST /settings/update/install`
starts the updater detached and returns at once, because the updater restarts the console.
One update runs per tenant at a time (409 otherwise); start and end are audited and the
last run is kept in `update_state.json` under the tenant's global dir. Also here: tenant config
(`tenant.corvin.yaml` viewer), and the `bridge.sh doctor` output on demand.
Token revocation is available here.

**Autostart tier toggle (ADR-0184):** every install already starts CorvinOS
automatically at login (Stufe 1, no admin/root needed). The Settings page adds a
switch to also register CorvinOS as an **always-on system service** (Stufe 2 —
systemd system unit / macOS LaunchDaemon / Windows boot Scheduled Task), which
starts at boot even before anyone logs in. This is the same opt-in path as running
`corvin-service install`/`uninstall` from a terminal — the console never
self-elevates and never silently defaults to Stufe 2. Without admin/root the
toggle does not error; it reports back the exact manual command to run once
(`sudo corvin-service install` on Linux/macOS, an elevated PowerShell on
Windows), then the switch reflects the real state on the next check
(`GET /settings/service-tier`).

### Chat

A lightweight web-based chat interface that connects to the adapter as if it were a
bridge. Useful for testing persona behavior, forge tools, and skill injection without
opening a messenger app. Sessions initiated from the console are labeled `[console]`
in the audit log.
