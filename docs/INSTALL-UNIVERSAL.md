# Universal CorvinOS Installer

The CorvinOS universal installer is a **cross-platform, self-contained** Python package that works on **Linux, macOS, and Windows** without requiring Docker, SystemD, or any external dependencies.

## Installation

### From PyPI

```bash
pip install corvinOS
corvin-install
```

### From Source

```bash
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
pip install -e .
corvin-install
```

## Architecture

### Cross-Platform Path Resolution

| Platform | Corvin Home | Voice Config |
|----------|-------------|--------------|
| **Linux** | `~/.corvin/` | `~/.config/corvin-voice/` |
| **macOS** | `~/.corvin/` | `~/.config/corvin-voice/` |
| **Windows** | `~/.corvin/` | `%USERPROFILE%\.config\corvin-voice\` |

### Service Management

| Platform | Service Manager | Location | Elevation |
|----------|-----------------|----------|-----------|
| **Linux** | systemd (user) | `~/.config/systemd/user/` | None (user-space) |
| **macOS** | launchd | `~/Library/LaunchAgents/` | None (user-space) |
| **Windows** | Task Scheduler | `Control Panel → Task Scheduler` | None (user-space) |

### Bridge Isolation

Each bridge (Discord, WhatsApp, Telegram, Slack, Email) gets its own:
- **Isolated Python venv:** `~/.corvin/bridges/<name>/venv/`
- **Dependencies:** Python deps via pip, Node deps via npm
- **Systemd/launchd/Task service:** Auto-start on system reboot

## Usage

### Interactive Installation

```bash
corvin-install
```

The installer will:
1. Detect your platform
2. Create necessary directories
3. Ask which bridges you want to enable
4. Set up virtual environments
5. Register services with your OS
6. Start the services
7. Verify everything is working

### Non-Interactive Installation

```bash
corvin-install --yes
```

This uses defaults and doesn't prompt for input.

### Restore

```bash
corvin-restore
```

Force-rebuilds the web console frontend and restarts all services. Use this after
pulling updates that include UI changes, or to recover a 503 console page.

### Uninstall

```bash
corvin-uninstall
```

This will:
- Stop all services
- Unregister services from your OS
- Reset onboarding and engine selection (always — so a subsequent
  `corvin-install` goes through first-boot onboarding again, even if you
  decline the prompts below)
- Optionally remove Corvin data files, API keys/secrets, and audit logs
  (asks separately for each — nothing sensitive is deleted without confirming)

## Service Management

After installation, services are registered with your OS:

### Linux (systemd)

```bash
# Check status
systemctl --user status corvin-adapter
systemctl --user status corvin-bridge-discord

# Start/stop
systemctl --user start corvin-adapter
systemctl --user stop corvin-adapter

# Logs
journalctl --user -u corvin-adapter -f
```

### macOS (launchd)

```bash
# Check status
launchctl list | grep corvin

# Start/stop
launchctl start com.corvin.adapter
launchctl stop com.corvin.adapter

# Logs
log stream --predicate 'process == "python"'
```

### Windows (Task Scheduler)

```powershell
# View tasks
schtasks /query | findstr CorvinOS

# Run manually
schtasks /run /tn "CorvinOS\adapter"

# View results
Get-WinEvent -LogName Application | Where-Object { $_.ProviderName -like "*Corvin*" }
```

> **Note:** the standalone one-liner installer (`install.ps1`, `irm
> https://corvin-labs.com/install.ps1 | iex`) registers a *different*,
> flat-named autostart task, `CorvinOS-Console` (not the `CorvinOS\*` folder
> scheme above, which belongs to the `corvin-install` Python installer flow).
> `corvin-uninstall` removes both: the `CorvinOS\*` folder-scoped tasks via
> the service manager, and `CorvinOS-Console` directly. To remove it manually
> without uninstalling: `Unregister-ScheduledTask CorvinOS-Console`.

## Configuration

Configuration is stored in:

```
~/.config/corvin-voice/installer.json
```

Example:

```json
{
  "installed_bridges": ["discord", "whatsapp"],
  "corvin_home": "/home/user/.corvin",
  "voice_config": "/home/user/.config/corvin-voice",
  "version": "0.1.0"
}
```

## Multi-Tenant Support

CorvinOS supports multiple tenants via `CORVIN_TENANT_ID`:

```bash
export CORVIN_TENANT_ID=production
corvin-install  # Installs to ~/.corvin/tenants/production/
```

Default tenant: `_default`

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `CORVIN_HOME` | Override Corvin home | `/custom/path` |
| `CORVIN_TENANT_ID` | Select tenant | `production` |
| `CORVIN_INSTALLED_VIA_OLLAMA` | Ollama detection | `1` |

## Troubleshooting

### Services not starting

Check logs:
- **Linux:** `journalctl --user -f`
- **macOS:** `log stream --predicate 'process == "python"'`
- **Windows:** Event Viewer → Application

### venv creation fails

Ensure you have:
- Python 3.9+
- Disk space for venv (~50 MB per bridge)
- Write permissions to `~/.corvin/`

### npm install fails

Install Node.js globally:

```bash
# macOS
brew install node

# Linux
sudo apt-get install nodejs

# Windows
choco install nodejs
```

## Architecture Overview

```
Installation Flow:
  1. Detect platform (Linux/macOS/Windows)
  2. Create ~/.corvin/ and ~/.config/corvin-voice/
  3. Ask user which bridges to enable
  4. Create venv for each bridge
  5. Install Python/Node dependencies
  6. Register services with OS (systemd/launchd/schtasks)
  7. Start services
  8. Verify installation
  9. Save config to installer.json

Service Structure:
  ~/.corvin/
  ├── bridges/
  │   ├── discord/
  │   │   └── venv/          (isolated Python environment)
  │   ├── whatsapp/
  │   │   └── venv/
  │   └── ...
  ├── tenants/
  │   └── _default/
  │       ├── global/
  │       ├── sessions/
  │       ├── forge/
  │       └── voice/
  ├── logs/
  └── sessions/

Service Registration:
  Linux:   ~/.config/systemd/user/corvin-*.service
  macOS:   ~/Library/LaunchAgents/com.corvin.*.plist
  Windows: Task Scheduler → CorvinOS folder
```

## Contributing

To contribute improvements:

1. Fork and clone the repo
2. Make changes to `operator/installer/`
3. Run tests: `pytest tests/test_installer_*.py`
4. Submit PR

## License

Apache License 2.0 — See LICENSE file
