# CorvinOS Installation Guide

**Status:** ✅ Production Ready | **Version:** 2.0.0+ | **Date:** 2026-09-18

---

## Quick Start — Windows, Linux & macOS

### 🚀 Windows (PowerShell)

```powershell
# Öffne PowerShell (Admin nicht erforderlich)
powershell -ExecutionPolicy Bypass -File install.ps1 -Editable .\
```

**Was wird installiert:**
- Python (via uv)
- Node.js runtime
- corvinos CLI
- Console (FastAPI, localhost:8765)
- Plugins & Compliance

**Dauer:** ~10-15 Minuten  
**Größe:** ~600 MB

---

### 🚀 Linux & macOS (Bash)

```bash
# Clone repository
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS

# Install with editable mode
bash install.sh --editable .

# Or with preset
bash install.sh --editable . --preset minimal
```

**Dauer:** ~5-10 Minuten  
**Größe:** ~500 MB

---

## Installation Verification

After installation completes:

```bash
# Test console startup
corvinos serve

# Open in browser
# Windows: start http://127.0.0.1:8765/console
# macOS: open http://127.0.0.1:8765/console
# Linux: xdg-open http://127.0.0.1:8765/console

# Ask a question in chat
# → "What is your name?"

# Verify voice summary
# → Play button should appear and work
```

---

## Installation Options

### Windows (PowerShell)

```powershell
# Standard installation
powershell -ExecutionPolicy Bypass -File install.ps1 -Editable .\

# Verbose output for debugging
powershell -ExecutionPolicy Bypass -File install.ps1 -Editable .\ -Verbose

# Dry-run (see what would happen)
powershell -ExecutionPolicy Bypass -File install.ps1 -Editable .\ -DryRun

# Skip Claude Code installation
powershell -ExecutionPolicy Bypass -File install.ps1 -Editable .\ -NoClaudeCode

# Enable LAN firewall rules
powershell -ExecutionPolicy Bypass -File install.ps1 -Editable .\ -Lan
```

### Linux & macOS (Bash)

```bash
# Standard installation
bash install.sh --editable .

# Skip Claude Code
bash install.sh --editable . --no-claude-code

# Enable LAN access
bash install.sh --editable . --lan

# Always-on mode (survives reboot)
bash install.sh --editable . --always-on

# Custom preset
bash install.sh --editable . --preset minimal  # minimal|standard|advanced
```

---

## Troubleshooting

### Windows PowerShell Errors

If you see execution policy errors:
```powershell
# Use bypass flag (as shown above):
powershell -ExecutionPolicy Bypass -File install.ps1 -Editable .\

# Or temporarily change policy:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
powershell -File install.ps1 -Editable .\
```

If installation gets stuck:
```powershell
# Check logs
type %TEMP%\corvinos-install-*\install.log

# Or in PowerShell:
Get-ChildItem $env:TEMP -Filter "corvinos-install-*" | 
  Get-ChildItem -Filter "install.log" | 
  Get-Content -Tail 50
```

### All Platforms

**Console won't start:**
```bash
# Check if port 8765 is in use
# Windows: netstat -ano | findstr :8765
# macOS/Linux: lsof -i :8765

# Try a different port
corvinos serve --port 9000
# Then open: http://127.0.0.1:9000/console
```

**Voice summary not working:**
```bash
# Verify TTS service is initialized
# Check console logs for errors

# Manually test voice synthesis
python -c "import piper; piper.synthesize('Hello')"
```

---

## System Requirements

### Minimum

- **Disk:** 500 MB free space
- **RAM:** 2 GB
- **Internet:** Required for initial download only

### Recommended

- **Disk:** 1+ GB free space
- **RAM:** 4+ GB
- **Network:** LAN access for A2A features (optional)

### Platform-Specific

**Windows:**
- Windows 10+ or Server 2016+
- PowerShell 5.1+
- No admin privileges required (optional for scheduled tasks)

**macOS:**
- macOS 10.14+
- Xcode Command Line Tools (optional)
- Both Intel and Apple Silicon (M1/M2/M3) supported

**Linux:**
- Ubuntu 18.04+, Debian 10+, RHEL 8+, or equivalent
- `curl`, `git`, `bash` installed
- No sudo required (installation in `~/.corvin/`)

---

## Automated Installation (All Platforms)

For CI/CD or scripted deployment:

### Windows

```powershell
# Fully automated with no interaction
powershell -NoProfile -ExecutionPolicy Bypass `
  -Command "& { IEX (Get-Content install.ps1 -Raw) -Editable '.' }"
```

### Linux/macOS

```bash
# Fully automated
bash install.sh --editable . --no-claude-code
```

---

## Post-Installation Setup

### Optional: Claude Code Integration

```bash
# If you skipped Claude Code during install, add it now
curl -fsSL https://claude.ai/install | sh

# Or on Windows:
# Download from https://claude.ai/download
```

### Optional: Always-On Mode

**Windows (via scheduled task):**
- Automatically registered during install
- Console starts on login

**Linux/macOS (via systemd):**
```bash
# Install systemd service
bash core/console/install-systemd.sh --user-mode

# Enable auto-start
systemctl --user enable corvin-console
```

---

## What Gets Installed

```
~/.corvin/
├── bin/                    # uv, CLI tools
├── node/                   # Node.js runtime
├── .venv/                  # Python venv with corvinos
├── global/
│   ├── forge/             # Skills, plugins
│   ├── sessions/          # Conversation history
│   └── voice/             # Voice configuration
├── tenants/
│   └── _default/          # Default tenant data
└── audit.jsonl            # Audit trail (immutable)
```

---

## Next Steps

1. **Configure:** See [CONFIGURATION.md](./docs/CONFIGURATION.md)
2. **Extend:** See [PLUGINS.md](./docs/PLUGINS.md)
3. **Troubleshoot:** See [TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md)
4. **API Reference:** See [API.md](./docs/API.md)

---

## Installation Logs

After installation, logs are saved for troubleshooting:

**Windows:**
```
%TEMP%\corvinos-install-YYYY-MM-DD_HH-mm-ss\
├── install.log           # Main log (all output)
├── install-errors.log    # Errors only
└── install-debug.log     # Debug details
```

**Linux/macOS:**
```
/tmp/corvinos-install-$$.log
```

---

## Advanced Options

### Development Install (Editable Mode)

```bash
# Edit source code and changes reflect immediately
bash install.sh --editable /path/to/your/clone
```

### Minimal Install

```bash
# Skip optional components (smaller, faster)
bash install.sh --editable . --preset minimal
```

### Custom Installation

Set environment variables to customize:

```bash
# Use specific Python version
PYTHON_VERSION=3.11 bash install.sh --editable .

# Custom installation directory
CORVIN_HOME=/opt/corvinos bash install.sh --editable .

# Skip Claude Code
SKIP_CLAUDE_CODE=1 bash install.sh --editable .
```

---

## Support

For issues or questions:

1. **Check logs** (see above)
2. **Read [TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md)**
3. **Search issues:** https://github.com/CorvinLabs/CorvinOS/issues
4. **Ask on Discord:** https://discord.gg/corvin
5. **File a bug:** https://github.com/CorvinLabs/CorvinOS/issues/new

---

## Uninstall

### Windows

```powershell
# Remove ScheduledTask
Unregister-ScheduledTask -TaskName "CorvinOS-Console" -Confirm:$false

# Remove installation
Remove-Item -Path $env:USERPROFILE\.corvin -Recurse -Force
```

### Linux/macOS

```bash
# Stop service (if running)
systemctl --user stop corvin-console

# Remove installation
rm -rf ~/.corvin/

# Remove systemd service (if installed)
systemctl --user disable corvin-console
rm ~/.config/systemd/user/corvin-console.service
```

---

**Happy hacking! 🚀**

For the latest updates, visit: https://github.com/CorvinLabs/CorvinOS
