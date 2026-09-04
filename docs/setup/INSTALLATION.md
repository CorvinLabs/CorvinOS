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
curl -fsSL https://corvin-labs.com/install.sh | sh
```

(`install.sh` is POSIX `sh`; piping it into `bash` works too, but `sh` is what the script is
written and tested for. To review before running: `curl -fsSL https://corvin-labs.com/install.sh
-o install.sh && less install.sh && sh install.sh`.)

**Windows — PowerShell one-liner:**
```powershell
irm https://corvin-labs.com/install.ps1 | iex
```

> **DNS error?** If PowerShell reports `irm : The remote name could not be
> resolved: 'corvin-labs.com'`, the domain itself is up — this is a local DNS
> resolution issue on your machine (a stale negative-cache entry, VPN/corporate
> DNS, or a flaky resolver). Try, in order:
> 1. Flush the local DNS cache and retry: `ipconfig /flushdns`
> 2. If it still fails, use the GitHub-hosted copy of the same script instead
>    (identical content, different domain):
>    ```powershell
>    irm https://raw.githubusercontent.com/CorvinLabs/CorvinOS/main/install.ps1 | iex
>    ```
> 3. Still failing? Your resolver may be blocking/mis-resolving both domains —
>    temporarily switch your network adapter's DNS to `1.1.1.1` (Cloudflare)
>    or `8.8.8.8` (Google) and retry.

Both one-liners bootstrap the `uv` runtime (which brings its own Python — no system Python, pip, or
package manager needed), then `uv tool install corvinos` into an isolated tool environment and add
it to your PATH. They also provision the local Hermes model and the voice (STT + TTS) models so the
install is voice-ready out of the box.

What the one-liners download, and how it is verified:

| Download | Pinned? | Verification |
|---|---|---|
| `uv` installer | yes — exact version, immutable GitHub release asset | SHA-256 of the installer script is checked before it runs; the script then verifies the `uv` binary against its embedded checksums |
| `corvinos` (PyPI) | version **floor** (`corvinos>=<this release>`), deliberately not an exact pin | an exact pin would land in the uv receipt and freeze `uv tool upgrade` (the console's auto-update) — see the script header (INST-1) |
| Ollama (Linux) | no — `https://ollama.com/install.sh` is unversioned | runs only when `ollama` is absent; opt out with `--no-hermes`; its full output is kept in `$TMPDIR/corvinos-install.log` (override: `CORVIN_INSTALL_LOG`). macOS uses Homebrew, Windows uses winget (signed package) |

`sudo` is used in exactly two places on Linux: to `apt-get`/`yum install curl` when neither curl nor
wget exists, and for `--always-on` (system-level service, ADR-0184 Stufe 2). Nothing else elevates.
The firewall is never touched unless you pass `--lan` (Linux `ufw`) / `-Lan` (Windows Defender) —
the console listens on `127.0.0.1` by default, so no inbound rule is needed until you enable A2A
LAN pairing.

Equivalent to doing it manually if you already have `uv`:

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

> **Note:** `corvinos-serve` (web console + Hermes auto-detect) and `corvin-install` (voice model
> provisioning, API keys, login autostart, messaging-bridge daemons + their system services) both
> work from the pip wheel — the one-liners run `corvin-install` from the `uv tool install`, no
> checkout involved. The bridge daemons are vendored inside the wheel
> (`corvin_core/_vendor/operator/bridges/`) and need Node.js 20+ at runtime. A git checkout
> (Method 3) is only needed to develop CorvinOS or rebuild the console frontend.

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

### Method 3: From Source (development)
```bash
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
pip install -e .
corvin-install
```

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
curl http://localhost:8765/v1/console/healthz   # Is the console running? (bare /healthz is 404)
ollama list                                     # Is Ollama running?
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
4. **Backup** → back up `~/.corvin/` and `~/.config/corvin-voice/` periodically
5. **Updates** → `uv tool upgrade corvinos` (one-liner installs) or `pip install -U corvinos`; see [UPGRADE_GUIDE.md](UPGRADE_GUIDE.md)

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
