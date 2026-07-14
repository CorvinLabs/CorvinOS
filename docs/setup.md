# Corvin — Setup Guide

This guide walks you through installing Corvin from scratch using the Python installer.

---

## 1. Prerequisites

### Operating system

| Platform | Support level |
|---|---|
| Linux (Ubuntu 22.04 LTS or later, Debian 12+, Fedora 38+, Arch) | Full |
| macOS 13 Ventura or later | Full (voice output from Claude not supported; messenger voice notes still work) |
| WSL2 on Windows 11 | Full (systemd optional; `fg` mode always works) |
| Windows 11 native | Full via `pip install corvinOS` — PATH is auto-configured; voice via edge-tts (no OpenAI key required) |

### Claude Code CLI

Corvin is built *on top* of Claude Code. The installer handles this automatically,
but you can pre-install manually:

```bash
# Install Claude Code (official one-liner):
curl -fsSL https://claude.ai/install.sh | bash

# Log in via browser (opens OAuth flow):
claude auth login
```

Verify: `claude --version` must print a version string and exit 0. If you see
`command not found`, see [troubleshooting.md](troubleshooting.md).

### API keys

| Key | Where | Required for |
|---|---|---|
| **OpenAI API key** | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | TTS voice synthesis + STT fallback. STT uses free local pywhispercpp (whisper.cpp) by default on every platform, incl. Windows; OpenAI key is a fallback. **Optional but recommended for voice output.** |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) | Optional summarizer, dialectic judge. Claude Code's browser login is sufficient for the main agent — this key is only needed for background helper calls. |

Store both in `~/.config/corvin-voice/service.env` (the installer creates this file):

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...   # optional
```

This file must have mode `0600`. The installer enforces this automatically.

### System dependencies

The installer handles these automatically, but it is useful to know what is needed:

| Package | Purpose |
|---|---|
| `node` ≥ 20, `npm` | Bridge daemons (Telegram, Discord, WhatsApp, etc.) |
| `python3` ≥ 3.11, `pip3` | Adapter, forge MCP server |
| `ffmpeg` | Audio conversion for voice notes (optional — a bundled `imageio-ffmpeg` binary is used automatically if no system ffmpeg is found, so this is not a hard requirement on any platform, including Windows) |
| `git` | Repository cloning and updates |
| `jq` | JSON processing in shell scripts |
| `systemd` (Linux) | Service management (optional on macOS/WSL2) |

---

## 2. Running the installer

```bash
git clone https://github.com/CorvinLabs/CorvinOS.git ~/projects/CorvinOS
cd ~/projects/CorvinOS
pip install -e ".[all]"
corvin-install
```

The installer runs interactively. Here is what each stage does.

### Stage 1 — OS detection

The installer detects your platform automatically. No input needed. It prints a summary
of what it found (distro, Node.js version, Python version, systemd availability).
If it reports an unsupported OS, you can continue anyway — most things still work.

### Stage 2 — Claude Code check

Setup runs `claude --version`. If the command is not found, it offers to add
`~/.local/bin` to your `PATH` via `~/.bashrc` and re-checks. If Claude Code is
missing entirely, setup prints install instructions and exits.

If prompted **"Add Claude Code to PATH?"** — answer `y`.

### Stage 3 — System dependencies

Setup runs your package manager (`apt`, `brew`, `dnf`, or `pacman`) to install
missing system packages. You will be asked for your `sudo` password.

If you prefer to install manually, answer `n` when asked **"Install system
dependencies automatically?"** — then install the packages listed above yourself
before continuing.

### Stage 4 — API key prompts

Setup asks for your OpenAI API key and (optionally) your Anthropic API key.
Keys are written to `~/.config/corvin-voice/service.env` with mode `0600`.

You can skip the Anthropic key (`Enter` on an empty line) if you use Claude Code's
browser login.

### Stage 5 — Bridge selection menu

A numbered menu lists all available bridges:

```
  1) Telegram
  2) Discord
  3) WhatsApp
  4) Slack
  5) Email
  6) Microsoft Teams
  7) Signal
  8) Done
```

Enter one or more numbers separated by spaces (e.g. `1 2`) or select `8` to skip
bridge setup now and configure bridges later via the Web Console (Settings → Bridges).

### Stage 6 — Per-bridge credential prompts

For each selected bridge, setup asks for the credentials specific to that bridge.
The per-bridge credential guide in the next section tells you exactly what to
obtain and where to find it.

### Stage 7 — Python dependencies

Setup creates a virtual environment at `~/.venv/corvin` (or uses an existing one)
and runs `pip install -e ".[all]"`. If you see a PEP 668 error, see
[troubleshooting.md](troubleshooting.md).

### Stage 8 — npm install

Setup runs `npm install` inside each selected bridge's directory. This downloads
the Node.js runtime dependencies (typically takes 30–60 seconds per bridge on a
fresh install).

### Stage 9 — Plugin registration

Setup runs:

```bash
claude plugin install voice@corvin-voice-local
```

This registers the forge MCP server, the skill-forge MCP server, and the path-gate
hook with your Claude Code installation. Required for forge tools and runtime skills
to appear inside Claude.

### Stage 10 — Service startup

On Linux with systemd:

```bash
systemctl --user enable --now corvin-voice-bridge-adapter.service
systemctl --user enable --now corvin-voice-bridge-telegram.service   # per-bridge
```

On macOS or WSL2 without systemd, setup starts the bridges in the background and
prints the `bridge.sh fg` command you can use to attach.

### Stage 11 — Validation

Setup runs `bridge.sh status` and `voice-audit verify`. A healthy output looks like:

```
adapter       ✓ running   (pid 12345)
telegram      ✓ running   (pid 12346)
audit chain   ✓ verified  (42 events, chain intact)
```

If any component shows a red `✗`, check the logs:

```bash
bash operator/bridges/bridge.sh tail
```

To specifically verify voice (STT+TTS) is working — a real, non-mocked round-trip,
not just "is the process up" — run:

```bash
corvin-voice doctor
```

This actually transcribes a bundled sample audio file and actually synthesizes a
test voice note, printing a clear per-check PASS/FAIL and exiting non-zero if
either fails (ADR-0185 M5).

---

## 3. Per-bridge credential guide

### Telegram

1. Open Telegram and start a conversation with `@BotFather`.
2. Send `/newbot` and follow the prompts (choose a display name and a unique username
   ending in `bot`, e.g. `MyCorvinBot`).
3. BotFather replies with a **token** that looks like `7123456789:AAFsomething...`. Copy it.
4. To find your Telegram **user ID** (needed for the whitelist), start a conversation
   with `@userinfobot` — it replies with your numeric user ID (e.g. `123456789`).
5. Paste the token into `operator/bridges/telegram/settings.json` under `telegram_token`.
6. Add your user ID to the `whitelist` array.

```json
{
  "telegram_token": "7123456789:AAFsomething...",
  "whitelist": [123456789]
}
```

### Discord

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
   and click **New Application**. Give it a name.
2. In the left sidebar, click **Bot**.
3. Click **Reset Token** (confirm the warning), then copy the token.
4. On the same page, scroll down to **Privileged Gateway Intents** and enable
   **MESSAGE CONTENT INTENT**. Save changes. (Without this, the bot cannot read
   message text.)
5. In the left sidebar, click **OAuth2 → URL Generator**.
6. Under **Scopes**, check `bot`.
7. Under **Bot Permissions**, check: `Send Messages`, `Read Message History`,
   `Attach Files`.
8. Copy the generated URL, open it in your browser, and invite the bot to your server.
9. Paste the token into `operator/bridges/discord/settings.json`:

```json
{
  "discord_token": "MTIz...",
  "whitelist": ["your-discord-username"]
}
```

To find your Discord user ID: in Discord, go to Settings → Advanced → enable
Developer Mode. Then right-click your username and select **Copy User ID**.

### WhatsApp

WhatsApp uses a QR code pairing flow (via whatsapp-web.js). No token is needed — your
phone number is the credential.

1. Ensure your phone has WhatsApp installed and is connected to the internet.
2. Run:

```bash
bash operator/voice/scripts/whatsapp_cli.sh pair
```

3. A QR code is printed in the terminal. Open WhatsApp on your phone →
   Linked Devices → Link a Device → scan the QR code.
4. Pairing takes 5–10 seconds. The script prints `Session saved` when done.
5. Your user ID for the whitelist is your phone number in international format
   (e.g. `4915123456789@c.us` for a German number). The daemon logs your ID
   on first message if you are unsure.

If the QR code expires before you scan it, re-run the command — sessions are
single-use for the pairing step.

### Slack

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App →
   From Scratch**. Name it and pick your workspace.
2. In the left sidebar, click **OAuth & Permissions**.
3. Under **Bot Token Scopes**, add:
   - `channels:read`
   - `chat:write`
   - `files:write`
   - `groups:read`
   - `im:read`
   - `im:write`
   - `mpim:read`
   - `users:read`
4. Click **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-...`).
5. In the left sidebar, click **Socket Mode** and enable it. You will be prompted to
   create an **App-Level Token** — name it anything, give it the `connections:write`
   scope, and copy the token (`xapp-...`).
6. In the left sidebar, click **Event Subscriptions**, enable events, and subscribe to:
   - `message.channels`
   - `message.groups`
   - `message.im`
   - `message.mpim`
7. Save changes. Reinstall the app to your workspace if prompted.
8. Paste both tokens into `operator/bridges/slack/settings.json`:

```json
{
  "slack_bot_token": "xoxb-...",
  "slack_app_token": "xapp-...",
  "whitelist": ["U0123ABCDEF"]
}
```

Your Slack user ID (e.g. `U0123ABCDEF`) appears in your Slack profile under
the three-dot menu → Copy member ID.

### Email

Email bridges use IMAP (receive) and SMTP (send). Most major providers support
app-specific passwords — do **not** use your regular account password.

**Gmail:**
1. Enable 2-factor authentication on your Google account.
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Choose "Mail" and your device name, then copy the 16-character app password.

**iCloud:**
1. Go to [appleid.apple.com](https://appleid.apple.com) → Sign-In and Security →
   App-Specific Passwords → Generate Password.

**Outlook / Microsoft 365:**
1. Go to [account.microsoft.com/security](https://account.microsoft.com/security) →
   Advanced Security Options → App Passwords.

Setup.sh auto-detects IMAP/SMTP servers for Gmail (`imap.gmail.com` / `smtp.gmail.com`),
iCloud (`imap.mail.me.com` / `smtp.mail.me.com`), and Outlook
(`outlook.office365.com`). For other providers, enter the server addresses manually.

```json
{
  "email": "you@example.com",
  "password": "app-specific-password",
  "imap_host": "imap.example.com",
  "smtp_host": "smtp.example.com",
  "whitelist": ["trusted@example.com"]
}
```

### Microsoft Teams

Teams requires an Azure App Registration and a bot channel registration.

1. Go to [portal.azure.com](https://portal.azure.com) → Azure Active Directory →
   App Registrations → New Registration. Name it; keep the default redirect URI.
2. Note the **Application (client) ID** and **Directory (tenant) ID**.
3. Go to Certificates & Secrets → New Client Secret. Copy the secret value.
4. Go to [dev.botframework.com](https://dev.botframework.com) → My Bots → Create
   a Bot. Choose **Microsoft App ID** and enter the client ID from step 2.
5. Under **Channels**, add **Microsoft Teams**.
6. For the messaging endpoint, you need a public HTTPS URL. For local development,
   use [ngrok](https://ngrok.com): `ngrok http 3979`. For production, use a proper
   HTTPS domain.
7. Paste credentials into `operator/bridges/teams/settings.json`.

### Signal

Signal uses `signal-cli` (a JVM-based CLI client). A JVM (Java 17+) is required.

1. Download `signal-cli` from [github.com/AsamK/signal-cli/releases](https://github.com/AsamK/signal-cli/releases).
2. Make it executable and add it to your PATH:

```bash
chmod +x signal-cli
sudo mv signal-cli /usr/local/bin/
```

3. Register your phone number (you will receive an SMS verification code):

```bash
signal-cli -u +491234567890 register
signal-cli -u +491234567890 verify 123456
```

4. See `operator/bridges/signal/README.md` for further configuration options
   including linking as a secondary device instead of registering fresh.

---

## 4. Starting and stopping

The `bridge.sh` script is the central control interface for all running components.
Run it from the repository root:

```bash
bash operator/bridges/bridge.sh <command>
```

| Command | What it does |
|---|---|
| `up` | Start all configured bridges and the adapter. Runs `npm install` if needed. Generates/refreshes systemd units on Linux. |
| `down` | Stop all bridges and the adapter gracefully. |
| `restart` | Stop then start. Use after token or structural config changes. |
| `status` | Show running state of each component with PIDs and uptime. |
| `tail` | Stream live logs from all components in one terminal (color-coded by bridge). Press Ctrl-C to stop tailing; processes keep running. |
| `fg` | Start everything in the foreground (one combined log stream). Useful on macOS, WSL2 without systemd, or remote headless servers without systemd. Ctrl-C stops everything. |
| `console` | Start **only the web console** on `http://127.0.0.1:8765`. Does not require Claude Code or any bridge token. Use this for first-time setup or when you want to configure engines in-browser. |
| `doctor` | Run the self-test suite: checks config, Claude CLI, MCP plugins, audit chain, and vault mode. |

Examples:

```bash
# First-time start (no Claude Code required — configure in-browser):
bash operator/bridges/bridge.sh console   # opens http://127.0.0.1:8765

# First-time start with full bridges (Linux/WSL2 with systemd):
bash operator/bridges/bridge.sh up

# Watch logs live:
bash operator/bridges/bridge.sh tail

# Restart after editing a token:
bash operator/bridges/bridge.sh restart

# Run without systemd (macOS / WSL2 headless):
bash operator/bridges/bridge.sh fg
```

---

## 5. Platform notes

| Platform | Notes |
|---|---|
| **Linux** | Full support. Systemd user services are created automatically by `corvin-install` (and by `bridge.sh up` for dev checkouts), which also runs `loginctl enable-linger $USER` for you so the service survives a reboot even without logging back in — no manual step needed. |
| **macOS** | Full support except voice output (TTS playback) from within Claude — messenger voice notes are still generated and sent. `corvin-install` automatically creates and loads a launchd `LaunchAgent` (`~/Library/LaunchAgents/com.corvin.*.plist`), so services start at login and restart on crash with no manual setup; use `bridge.sh fg` only if you want to run in the foreground instead. Homebrew must be installed for system dependencies. |
| **WSL2** | Systemd is optional (requires `systemd=true` in `/etc/wsl.conf`). Without systemd, use `bridge.sh fg`. Run `wsl --update` if you encounter WSL2 kernel version warnings. |
| **Windows native** | `pip install corvinOS` → open a new PowerShell → `corvin serve`. The installer auto-adds the Scripts directory to PATH. Voice works via edge-tts (no API key needed). The `install.ps1` one-liner registers a per-user Scheduled Task so the console starts automatically at login and restarts itself on crash/reboot; on accounts where the Task Scheduler store denies that (some managed/family/education Windows images) it automatically falls back to a Startup-folder shortcut instead — still no admin rights needed either way. A `CorvinOS.lnk` Desktop shortcut is also created so you can start the console by hand. |

### Autostart: "start at login" vs. "always-on, no login ever" (ADR-0184)

Every install (Linux/macOS/Windows) already starts CorvinOS automatically the
moment you log in, and restarts it on crash or reboot — no manual step
needed. This covers the normal desktop/laptop case.

For a headless box you never log into (a home server, a mini-PC), that's
not enough: a user-level autostart mechanism only fires once someone logs
in. For that case, opt into the **always-on** mode, which registers a real
system-level service (still runs as your own user account, never as
root/SYSTEM):

```bash
sudo corvin-service install     # Linux / macOS — needs sudo
corvin-service install          # Windows — run from an elevated PowerShell
corvin-service status           # check whether it's active
corvin-service uninstall        # remove it again
```

Installing always-on mode also **disables the start-at-login autostart**
(both would fight over port 8765 and the loser crash-loops at every
login). `corvin-service uninstall` prints the one command to re-enable
start-at-login afterwards. On Windows the always-on service now starts
immediately after registration — no reboot needed.

The piped installers also accept `--autostart` (registers the normal
start-at-login setup even when piped, e.g. `curl ... | sh -s -- --autostart`)
and `--always-on` (also runs `corvin-service install` for you, Linux/macOS
only for now — pass it in the same one-liner, e.g.
`curl ... | sh -s -- --always-on`). Neither is on by default: a piped
install intentionally stays lightweight unless you ask for more.

---

## 6. Accessing the web console

Corvin includes a browser-based console for viewing bridge status, managing
personas, reviewing the audit log, and more.

### Open the console (local)

Open `http://127.0.0.1:8765/console/` in your browser — no token needed.
The loopback binding is the security boundary; the console auto-logs you in from localhost.

### Remote server with Caddy

If Corvin is running on a remote server, use the included Caddy config:

```bash
# On the server — set your domain and email in .env first:
echo "CORVIN_DOMAIN=corvin.example.com" >> .env
echo "CORVIN_ACME_EMAIL=you@example.com" >> .env

# Start Caddy (obtains TLS certificate automatically via Let's Encrypt):
caddy run --config ops/Caddyfile.template
```

Console is then at `https://corvin.example.com/console/`.

For details on all three deployment scenarios (local, remote Caddy, Docker), see
[console.md](console.md).
