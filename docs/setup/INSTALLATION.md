# CorvinOS Installation Guide

## Quick Start

### System Requirements

| | |
|---|---|
| **Python** | not required up front — the installer bootstraps its own via `uv` (3.10+ if you install manually) |
| **OS** | Linux (Ubuntu 22.04+ recommended), macOS 12+ (Monterey), Windows 10 build 19041+ or Windows 11 |
| **Disk** | ~1–2 GB (CorvinOS plus the Whisper STT + Piper TTS voice models) |
| **RAM** | 4 GB minimum. CorvinOS runs no local LLM inference (ADR-2091); the AI engine is Claude Code, which runs against the Anthropic API. |

> **Bridges only** (Discord, WhatsApp, Telegram, Slack, Email) additionally require Node.js 20+
> and systemd (Linux) or launchd (macOS). On Windows, bridges require WSL2.

### Install

CorvinOS is installed **only from a local clone of the repository**. There is no `curl … | sh` /
`irm … | iex` one-liner, no download mode and no PyPI install (`pip install corvinos` /
`uv tool install corvinos` from the index are no longer supported). The installer refuses to run
unless it sits in (or is pointed at) a CorvinOS checkout (`.corvin_repo` + `pyproject.toml`), and it
installs that checkout in editable mode.

**1. Clone the repository** (requires `git`):
```bash
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
```

**2. Run the installer from the checkout.**

Linux / macOS / WSL:
```bash
./install.sh
```

(`install.sh` is POSIX `sh`; `sh install.sh` works the same way. It is plain text in your checkout —
review it before running if you like.)

Windows (PowerShell, from the CorvinOS directory):
```powershell
.\install.ps1
```

To install a checkout that lives somewhere else, pass it explicitly:
`sh install.sh --editable /path/to/CorvinOS` or `install.ps1 -Editable C:\path\to\CorvinOS`.
A path that is not a CorvinOS checkout is refused before anything is downloaded.

The installer bootstraps the `uv` runtime (which brings its own Python — no system Python, pip, or
package manager needed) and a local Node.js runtime, then installs your checkout with
`uv tool install --editable` into an isolated tool environment and adds it to your PATH. It also
installs the Claude Code CLI (skip with `--no-claude-code` / `-NoClaudeCode`) and provisions the
voice (STT + TTS) models so the install is voice-ready out of the box. No local LLM model is
downloaded — local Ollama inference was removed in ADR-2091.

Because the install is editable, the code that runs **is** your checkout: `git pull` +
`sh update.sh` (or `update.ps1`) is all an update takes — see [UPGRADE_GUIDE.md](UPGRADE_GUIDE.md).

What the installer downloads, and how it is verified:

| Download | Pinned? | Verification |
|---|---|---|
| `uv` installer | yes — exact version, immutable GitHub release asset | SHA-256 of the installer script is checked before it runs; the script then verifies the `uv` binary against its embedded checksums |
| CorvinOS itself | not downloaded — it is your checkout | whatever revision you cloned / pulled |

`sudo` is used in exactly two places on Linux: to `apt-get`/`yum install curl` when neither curl nor
wget exists, and for `--always-on` (system-level service, ADR-0184 Stufe 2). Nothing else elevates.
The firewall is never touched unless you pass `--lan` (Linux `ufw`) / `-Lan` (Windows Defender) —
the console listens on `127.0.0.1` by default, so no inbound rule is needed until you enable A2A
LAN pairing.

**AI engine:** the default engine is Claude Code. Log in once with `claude` (or configure an API key /
cloud platform in the console's Settings → AI Engines). If Claude Code is missing or not logged in,
a chat turn answers with an error that points at Setup — there is no automatic fallback engine.

---

## Installation Methods

### Method 1: Installer from the checkout (recommended)
```bash
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
./install.sh            # Windows: .\install.ps1
```

`corvinos-serve` (web console) and `corvin-install` (voice model provisioning, API keys, login
autostart, messaging-bridge daemons + their system services) are both installed by this step; the
installer runs `corvin-install --yes` for you. The bridge daemons need Node.js 20+ at runtime.

### Method 2: Manual editable install (development)
```bash
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
pip install -e .
corvin-install
```

This is the same checkout-based install without the installer's bootstrap (you supply Python 3.10+
and Node.js yourself). Prefer `./install.sh` unless you are developing CorvinOS.

**Developer note — wheel vs. checkout.** The wheel remaps several `core/<area>/<pkg>` packages to
top-level names (`corvin_console`, `corvin_core`, `corvin_gateway`, `corvin_license`,
`corvin_plugins`, `corvin_compliance_reports`, `corvin_workflows`, …). Always import them by the
top-level name — it works in both layouts; `core.console.corvin_core…` works only in a checkout and
raises `ModuleNotFoundError` on every pip install. `tests/test_wheel_content_guard.py` fails on such
imports, on repo junk at the wheel root, and on developer paths inside the wheel. A second
checkout-vs-wheel asymmetry to know: on a wheel install the ADR-0232 boot tripwire
(`corvin_plugins.bootstrap.boot_platform()`) finds its audit/consent/house-rules modules only after
`import corvin_console` has run the vendored-operator bootstrap — both shipped hosts
(`corvinos-serve`, `corvin-service`) do that first; a third host must too (tracked as a documented
xfail in the guard test).

---

## Installation Modes

### Interactive Installation
```bash
corvin-install
```

**Step-by-step flow:**
1. Platform detection (auto)
2. **Bridge selection:**
   - Choose to set up a bridge now or skip for later
   - If now: pick from a numbered list (1–5) or select all
   - If skip: configure bridges anytime via Settings → Bridges in the web console
3. Enter credentials for selected bridges (bot tokens, API keys)
4. Confirm and register services

**Bridge selection options:**
- **Skip** (answer `n`): configure bridges later via web UI
- **Select one** (answer `y`, then `1–5`): Discord, WhatsApp, Telegram, Slack, or Email
- **Select all** (answer `y`, then `a`): all five bridges at once

### Non-Interactive Installation
```bash
corvin-install --yes
```

Installs all bridges without prompts. Requires pre-configured credentials in
`~/.config/corvin-voice/`.

### Restore

```bash
corvin-restore
```

Force-rebuilds the web console from scratch (`npm install && npm run build`) and restarts every
service. Use after pulling UI changes or when the console shows a 503.

### Uninstall
```bash
corvin-uninstall
```

Prompts whether to keep data files (`~/.corvin/`).

---

## Platform-Specific Details

### Linux

**Tested on:** Ubuntu 22.04 LTS and 24.04 LTS. Expected to work on Debian 11+, Fedora 38+, and
other systemd-based distributions. Non-systemd systems (Alpine, NixOS, etc.) are not currently
supported by the service manager.

**Package manager support:** apt (Ubuntu/Debian), dnf (Fedora/RHEL), pacman (Arch). If none is
detected, the installer prints a manual install hint.

**Requirements:**
- systemd user session (`systemctl --user`)
- No sudo required (except `--always-on`, and the curl bootstrap when neither curl nor wget is installed)

**What gets installed:**
```
~/.config/systemd/user/
├── corvin-adapter.service
├── corvin-bridge-discord.service
├── corvin-bridge-whatsapp.service
└── ...
```

**Check installation:**
```bash
systemctl --user status corvin-*
journalctl --user -u corvin-adapter -f
```

**Restart services:**
```bash
systemctl --user restart corvin-adapter
```

### macOS

**Tested on:** macOS 13 (Ventura) and 14 (Sonoma). Minimum supported version: **macOS 12
(Monterey)**, which is the floor for current Homebrew and Python 3.10 wheel builds on both Intel
and Apple Silicon.

**Requirements:**
- Homebrew (`brew`) for dependency installation
- No elevation required

**What gets installed:**
```
~/Library/LaunchAgents/
├── com.corvin.adapter.plist
├── com.corvin.bridge-discord.plist
└── ...
```

**Check installation:**
```bash
launchctl list | grep corvin
log stream --predicate 'process == "python"'
```

**Restart services:**
```bash
launchctl stop com.corvin.adapter
launchctl start com.corvin.adapter
```

### Windows

**Supported:** Windows 10 build 19041 (May 2020 Update) and Windows 11.

**What works natively (no WSL2):**

| Feature | Status |
|---|---|
| `install.ps1` from a checkout | ✅ Supported |
| `pip install corvinos` from PyPI | ❌ No longer supported — install from a clone |
| `corvinos-serve` (web console) | ✅ Opens browser at http://localhost:8765 |
| Bridges (Discord / WhatsApp / Telegram / …) | ⚠️ Requires WSL2 — see below |

**Quick start (native):**
```powershell
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
.\install.ps1
# Console opens at http://localhost:8765
```

> **PATH note:** `install.ps1` puts the `corvin*` commands on your user PATH; open a new terminal
> after the install so it picks them up. If a command is still not found, use the fallback:
> ```powershell
> py -m ops.launcher.corvin.serve_entry --no-browser
> ```

**Bridges on Windows → WSL2:**

Bridges require bash and systemd, which are not available on native Windows. Install them via
WSL2 + Ubuntu:

```powershell
# One-time setup (Admin PowerShell):
wsl --install
# Then inside Ubuntu:
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
./install.sh
```

**Health check:**
```powershell
curl http://localhost:8765/v1/console/healthz   # Is the console running? (bare /healthz is 404)
claude --version                                # Is the Claude Code CLI installed?
```

---

## Directory Structure

After installation:

```
~/.corvin/                                 # Corvin home (CORVIN_HOME)
├── bridges/
│   ├── discord/
│   │   ├── venv/                          # Isolated Python env
│   │   ├── settings.json                  # Discord bot token
│   │   └── ...
│   └── whatsapp/
│       └── ...
├── global/
│   └── forge/audit.jsonl                  # Hash-chained audit log (instance-level chain)
├── tenants/_default/
│   ├── global/
│   │   └── forge/audit.jsonl              # Hash-chained audit log (per-tenant chain)
│   ├── sessions/
│   └── voice/
├── logs/                                  # console.log etc.
└── run/                                   # pid / session state

~/.config/corvin-voice/
├── installer.json                         # Installation config
├── config.json                            # User preferences
└── service.env                            # API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, …)
```

`service.env` is a plain `KEY=value` file, **not encrypted**: it is created with mode `0600`
(owner read/write only) and never made group/world-readable, which is the protection it relies on.
Anyone with your user account or root can read it. An encrypted per-tenant store also exists
(`corvinos secrets set KEY VALUE`, see `corvinos secrets --help`).

Audit chains: `corvinos audit verify` checks the canonical chain (exit 1 if broken);
`corvinos audit verify --path <file>` checks any other `audit.jsonl`, e.g. a tenant's
`~/.corvin/tenants/<tenant>/global/forge/audit.jsonl`.

```

~/.config/systemd/user/                    # Linux only
└── corvin-*.service

~/Library/LaunchAgents/                    # macOS only
└── com.corvin.*.plist
```

---

## Configuration

### Bridge Credentials

**Option 1: Web Console (recommended)**
1. Open `http://localhost:8765`
2. Go to **Settings → Bridges**
3. Select a bridge, enter credentials, save and test

**Option 2: Manual**
```bash
vim ~/.corvin/bridges/discord/settings.json
```

Example `settings.json`:
```json
{
  "bot_token": "YOUR_DISCORD_BOT_TOKEN",
  "guild_id": "YOUR_GUILD_ID",
  "channel_id": "YOUR_CHANNEL_ID"
}
```

After editing, restart the bridge:
```bash
systemctl --user restart corvin-bridge-discord   # Linux
launchctl stop com.corvin.bridge-discord         # macOS
schtasks /run /tn "CorvinOS\bridge-discord"      # Windows (WSL2)
```

### Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `CORVIN_HOME` | Override Corvin home path | `~/.corvin/` |
| `CORVIN_TENANT_ID` | Select tenant | `_default` |

---

## Verification

### Check Services

**Linux:**
```bash
systemctl --user status corvin-adapter
```

**macOS:**
```bash
launchctl list | grep corvin
```

**Windows:**
```powershell
schtasks /query /tn "CorvinOS\adapter" /v
```

### Check Logs

```bash
journalctl --user -u corvin-adapter -n 50 -f   # Linux
log stream --level debug                        # macOS
eventvwr.msc                                    # Windows → Application log
```

---

## Troubleshooting

### Python not found or wrong version

The installer does not need a system Python (`uv` brings its own). This only matters for the
manual `pip install -e .` path (Method 2):

```bash
python --version    # must be 3.10+
python3 --version
```

If missing: download from https://www.python.org/downloads/ (check "Add to PATH" on Windows).

### Install fails

Re-run the installer from your checkout — it is idempotent and reinstalls the editable package:

```bash
cd CorvinOS
git pull
./install.sh            # Windows: .\install.ps1
```

If it says it is not inside a CorvinOS checkout, you are running a stray copy of the script: clone
the repository (see [Install](#install)) and run it from there.

### Services not starting

**Linux:**
```bash
systemctl --user status corvin-adapter
journalctl --user -u corvin-adapter -n 20
```

**macOS:**
```bash
plutil -lint ~/Library/LaunchAgents/com.corvin.adapter.plist
log stream --predicate 'process == "python"'
```

**Windows:**
```powershell
schtasks /query /tn "CorvinOS\adapter" /v
eventvwr   # Application log
```

### Node.js not found

Node.js 20+ is only required for bridges (Discord, WhatsApp, etc.), not for `corvinos-serve`.

```bash
brew install node          # macOS
sudo apt install nodejs    # Ubuntu/Debian (then verify version ≥ 20)
winget install OpenJS.NodeJS.LTS   # Windows
```

Or use nvm (Linux/macOS): `curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash && nvm install --lts`

### Audit chain verification failed

```bash
corvinos audit verify                                                    # canonical chain
corvinos audit verify --path ~/.corvin/tenants/_default/global/forge/audit.jsonl   # a tenant chain
corvinos audit health                                                    # chain ok + record count
```

This is a CRITICAL security event. Consult `docs/audit-and-compliance.md`. (`voice-audit` is a
script vendored inside the package for the bridge services, not an installed command — use
`corvinos audit`.)

---

## Restore

Force-rebuild the web console and restart all services:

```bash
corvin-restore
```

Useful after pulling source changes that include frontend updates, or when the console returns 503.

---

## Uninstalling

```bash
corvin-uninstall   # removes services; prompts whether to keep ~/.corvin/ data
```

To reinstall later with existing data, run the installer from your checkout again:
```bash
cd CorvinOS
./install.sh       # corvin-install detects existing data automatically
```

---

## Multi-Tenant Setup

```bash
# Default tenant (created automatically)
corvin-install

# Additional tenant
export CORVIN_TENANT_ID=production
corvin-install
# Creates: ~/.corvin/tenants/production/
```

---

## Next Steps

1. **Configure bridges** → Settings → Bridges in the web console, or edit `~/.corvin/bridges/<bridge>/settings.json`
2. **Test connections** → send a test message to each bridge
3. **Check logs** → `journalctl --user -u corvin-adapter -f` (Linux) or the console Logs page
4. **Backup** → back up `~/.corvin/` and `~/.config/corvin-voice/` periodically
5. **Updates** → `sh update.sh` (Windows: `update.ps1`) from your checkout; see [UPGRADE_GUIDE.md](UPGRADE_GUIDE.md)

---

## Support & Issues

- **GitHub Issues**: https://github.com/CorvinLabs/CorvinOS/issues
- **Discussions**: https://github.com/CorvinLabs/CorvinOS/discussions
- **Documentation**: [docs/](docs/)

---

## Related Documentation

- **[INSTALL-UNIVERSAL.md](docs/INSTALL-UNIVERSAL.md)** — Detailed platform guide
- **[audit-and-compliance.md](docs/audit-and-compliance.md)** — GDPR & compliance
