# CorvinOS Installation Guide

## Quick Start

### System Requirements

| | |
|---|---|
| **Python** | not required up front — the installer bootstraps its own via `uv` (3.10+ if you install manually) |
| **OS** | Linux (Ubuntu 22.04+ recommended), macOS 12+ (Monterey), Windows 10 build 19041+ or Windows 11 |
| **Disk** | 2–7 GB (the local Hermes model is 1.4–5.2 GB; plus the Whisper STT + Piper TTS voice models) |
| **RAM** | 4 GB minimum. The installer picks the local model by available RAM — under ~6 GB it installs the lighter `qwen3:1.7b`, 6–12 GB gets the mid-size `qwen3:4b`, and ≥12 GB gets `qwen3:8b`; the running engine automatically uses whichever model is actually installed. |

> **Bridges only** (Discord, WhatsApp, Telegram, Slack, Email) additionally require Node.js 20+
> and systemd (Linux) or launchd (macOS). On Windows, bridges require WSL2.

### Install

**Linux / macOS — one-liner (recommended):**
```bash
curl -fsSL https://corvin-labs.com/install.sh | bash
```

**Windows — PowerShell one-liner:**
```powershell
irm https://corvin-labs.com/install.ps1 | iex
```

Both one-liners bootstrap the `uv` runtime (which brings its own Python — no system Python, pip, or
package manager needed), then `uv tool install corvinos` into an isolated tool environment and add
it to your PATH. They also provision the local Hermes model and the voice (STT + TTS) models so the
install is voice-ready out of the box. Equivalent to doing it manually if you already have `uv`:

```bash
uv tool install corvinos
corvinos-serve          # opens http://localhost:8765
```

(With a system Python + pip you can also `pip install corvinos`, but the `uv` path above is what the
one-liners use and needs no pre-installed Python.)

**Hermes (local AI, no cloud required)** is automatically detected. If Ollama is not yet installed,
the console's Settings → Engine page has a one-click bootstrap button.

---

## Installation Methods

### Method 1: From PyPI (Recommended)
```bash
pip install corvinos
corvinos-serve          # web console at http://localhost:8765
```

> **Note:** `corvinos-serve` (web console + Hermes auto-detect) works fully from the pip wheel.
> The full `corvin-install` flow — which registers messaging-bridge daemons (Discord/WhatsApp/…)
> and their system services — requires a git checkout (see Method 3).

### Method 2: With Hermes (fully local, no API key required)
```bash
# Install Ollama first
curl -fsSL https://ollama.com/install.sh | sh   # Linux
brew install ollama                              # macOS
# Windows: winget install Ollama.Ollama

# Then install CorvinOS and start
pip install corvinos
corvinos-serve
# The console auto-detects Ollama and selects the right model for your RAM.
# Or use the one-click bootstrap: Settings → Engine → Bootstrap Hermes
```

### Method 3: From Source (required for bridges)
```bash
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
pip install -e .
corvin-install
```

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
- No sudo required

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

**What works natively (pip install, no WSL2):**

| Feature | Status |
|---|---|
| `pip install corvinos` | ✅ Supported |
| `corvinos-serve` (web console) | ✅ Opens browser at http://localhost:8765 |
| Hermes / Ollama | ✅ `winget install Ollama.Ollama` |
| Bridges (Discord / WhatsApp / Telegram / …) | ⚠️ Requires WSL2 — see below |

**Quick start (native):**
```powershell
pip install corvinos
corvinos-serve
# Console opens at http://localhost:8765
```

> **PATH note:** With a system-wide Python install, pip places `corvin*` scripts in the
> user Scripts folder (`%APPDATA%\Python\Python3xx\Scripts`), which is not on PATH by default.
> Either add it to PATH, or use the fallback:
> ```powershell
> py -m ops.launcher.corvin.serve_entry --no-browser
> ```
> The one-liner installer (`irm https://corvin-labs.com/install.ps1 | iex`) handles PATH
> setup automatically.

**Hermes (local AI, no API key required):**
```powershell
winget install Ollama.Ollama
pip install corvinos
corvinos-serve
# Or: Settings → Engine → Bootstrap Hermes in the browser
```

**Bridges on Windows → WSL2:**

Bridges require bash and systemd, which are not available on native Windows. Install them via
WSL2 + Ubuntu:

```powershell
# One-time setup (Admin PowerShell):
wsl --install
# Then inside Ubuntu:
pip install corvinos
corvin-install
```

**Health check:**
```powershell
curl http://localhost:8765/healthz   # Is the console running?
ollama list                          # Is Ollama running?
```

---

## Directory Structure

After installation:

```
~/.corvin/                                 # Corvin home
├── bridges/
│   ├── discord/
│   │   ├── venv/                          # Isolated Python env
│   │   ├── settings.json                  # Discord bot token
│   │   └── ...
│   └── whatsapp/
│       └── ...
├── tenants/_default/
│   ├── global/
│   ├── sessions/
│   └── voice/
├── logs/
└── audit.jsonl                            # Hash-chained audit log

~/.config/corvin-voice/
├── installer.json                         # Installation config
├── config.json                            # User preferences
└── secrets.json                           # Encrypted credentials

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

```bash
python --version    # must be 3.10+
python3 --version
```

If missing: download from https://www.python.org/downloads/ (check "Add to PATH" on Windows).

### pip install fails

```bash
pip install --upgrade pip
pip install corvinos --force-reinstall
```

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
voice-audit verify
```

This is a CRITICAL security event. Consult `docs/audit-and-compliance.md`.

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

To reinstall later with existing data:
```bash
pip install corvinos
corvin-install     # detects existing data automatically
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
4. **Backup** → back up `~/.corvin/` periodically
5. **Updates** → `pip install --upgrade corvinOS`

---

## Support & Issues

- **GitHub Issues**: https://github.com/CorvinLabs/CorvinOS/issues
- **Discussions**: https://github.com/CorvinLabs/CorvinOS/discussions
- **Documentation**: [docs/](docs/)

---

## Related Documentation

- **[INSTALL-UNIVERSAL.md](docs/INSTALL-UNIVERSAL.md)** — Detailed platform guide
- **[OLLAMA-RELEASE.md](docs/OLLAMA-RELEASE.md)** — Release & publishing
- **[audit-and-compliance.md](docs/audit-and-compliance.md)** — GDPR & compliance
