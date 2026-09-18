# CorvinOS Windows Installation Guide

**Version:** 2.0.0+  
**Updated:** 2026-09-18  
**Status:** Production Ready (ADR-0010)

## Overview

CorvinOS is now fully supported on Windows 10/11 with native PowerShell installer, cross-platform path handling, and comprehensive E2E testing.

## Quick Start

### Native Windows (PowerShell)

```powershell
irm https://corvin-labs.com/install.ps1 | iex
```

Or download and run locally:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

### Development Install (Editable)

```powershell
.\install.ps1 -Editable C:\path\to\CorvinOS
```

### Options

```powershell
# Skip Claude Code installation
.\install.ps1 -NoClaudeCode

# Allow LAN A2A pairing (firewall rule)
.\install.ps1 -Lan

# Custom CorvinOS home
$env:CORVIN_HOME = "C:\Custom\CorvinOS"; .\install.ps1
```

## Path Handling

### Environment Variables

| Variable | Windows | Unix |
|----------|---------|------|
| `CORVIN_HOME` | `C:\Users\user\.corvin` | `~/.corvin` |
| `USERPROFILE` | ✓ (home dir) | N/A |
| `HOME` | N/A | ✓ (home dir) |
| `TEMP`/`TMP` | ✓ | N/A |
| `TMPDIR` | N/A | ✓ |

### Path Normalization

The installer automatically handles:
- **Mixed separators:** `C:/Users\test/.corvin` → normalized
- **Environment vars:** `%USERPROFILE%\.corvin` → `C:\Users\user\.corvin`
- **Tilde expansion:** `~/.corvin` → `C:\Users\user\.corvin`
- **Relative paths:** Resolved to absolute paths

### Python Code

All Python code uses `pathlib.Path` for cross-platform paths:

```python
from pathlib import Path
from core.paths.windows import PlatformPath

# Cross-platform path builder
pp = PlatformPath()
corvin_home = pp.corvin_home  # Automatically Windows or Unix
tenant_path = pp.path("tenants", "_default")
```

### Symlinks & Junctions

Windows does not support Unix symlinks without admin privileges. The installer handles this automatically:

```python
from core.paths.windows import create_symlink

# Creates junction on Windows (no admin needed)
# Creates symlink on Unix
create_symlink(source, link_path, is_dir=True)
```

## Installation Components

### 1. PowerShell Installer (`install.ps1`)

- **File:** `install.ps1`
- **Size:** ~50 KB
- **Runtime:** 2-5 minutes (includes Python bootstrap)
- **Privileges:** No admin rights needed (except optional firewall rule)

**Features:**
- Bootstraps `uv` package manager (brings own Python)
- Detects & installs Claude Code (optional)
- Creates autostart task (Scheduled Task or Startup folder)
- Health checks console readiness
- Opens browser to console

**Failure Recovery:**
- Automatically retries on connection timeout
- Logs to `$CORVIN_HOME\logs\console-supervisor.log`
- Safe to re-run multiple times (idempotent)

### 2. Cross-Platform Path Utilities

- **File:** `core/paths/windows.py`
- **API:** `normalize_path()`, `get_home_dir()`, `get_temp_dir()`, `create_symlink()`
- **Tests:** 33 unit tests covering all scenarios

**Key Functions:**

```python
# Normalize any path to platform-safe version
path = normalize_path("C:/Users/test/.corvin")  # → C:\Users\test\.corvin

# Get platform-specific home directory
home = get_home_dir()  # Windows: C:\Users\user, Unix: /home/user

# Get temp directory
temp = get_temp_dir()  # Windows: %TEMP%, Unix: /tmp

# Create symlink/junction
create_symlink(source, link_path, is_dir=True)  # Junction on Windows, symlink on Unix
```

### 3. Environment Variable Integration

The installer respects all platform-specific environment variables:

| Variable | Purpose | Windows Default |
|----------|---------|-----------------|
| `CORVIN_HOME` | Installation root | `%USERPROFILE%\.corvin` |
| `CORVIN_TENANT_ID` | Tenant scope | `_default` |
| `CORVIN_SUPERVISED` | Autostart detection | (internal) |
| `PATH` | Separator: `;` on Windows, `:` on Unix | Auto-updated |

**Example:**

```powershell
# Install to custom location
$env:CORVIN_HOME = "D:\CorvinOS"
.\install.ps1

# Later shells will use this location automatically
corvinos-serve  # Reads CORVIN_HOME automatically
```

## Autostart (Always-On Mode)

### Option 1: Scheduled Task (Recommended)

Automatically created by installer:
- **Name:** `CorvinOS-Console`
- **Trigger:** At user login
- **Restart:** On crash (5-per-5-minute limit)
- **Window:** Hidden
- **Logs:** `$CORVIN_HOME\logs\console-supervisor.log`

**Manage:**

```powershell
# Check status
Get-ScheduledTask -TaskName "CorvinOS-Console"

# Manually start
Start-ScheduledTask -TaskName "CorvinOS-Console"

# Disable
Disable-ScheduledTask -TaskName "CorvinOS-Console"

# Remove
Unregister-ScheduledTask -TaskName "CorvinOS-Console" -Confirm:$false
```

### Option 2: Startup Folder Shortcut (Fallback)

If Task Scheduler access is denied (managed/restricted accounts):
- Shortcut: `%USERPROFILE%\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\CorvinOS.lnk`
- No admin rights required
- No restart safety (app restarts only via its own loop)

## Uninstallation

```powershell
# Option 1: Remove via uv (recommended)
uv tool uninstall corvinos

# Option 2: Disable autostart
Unregister-ScheduledTask -TaskName "CorvinOS-Console" -Confirm:$false

# Option 3: Manual removal
Remove-Item -Recurse $env:USERPROFILE\.corvin
Remove-Item -Recurse $env:USERPROFILE\.local\bin\corvinos* -ErrorAction SilentlyContinue
Remove-Item -Recurse $env:APPDATA\uv\tools\corvinos -ErrorAction SilentlyContinue
```

## Troubleshooting

### Console Won't Start

**Check logs:**

```powershell
Get-Content "$env:USERPROFILE\.corvin\logs\console-supervisor.log" -Tail 50
```

**Restart manually:**

```powershell
corvinos-serve
```

**Re-run installer:**

```powershell
irm https://corvin-labs.com/install.ps1 | iex
```

### "PATH not found" or Commands Not Working

Open a **NEW PowerShell window** — PATH updates need a fresh session:

```powershell
# Close current window, open new one
corvinos-serve
```

### Firewall Issues (A2A Pairing)

For LAN-based A2A pairing, allow inbound port 8765:

```powershell
# Option 1: Re-run installer with -Lan
.\install.ps1 -Lan

# Option 2: Manual firewall rule
netsh advfirewall firewall add rule name="CorvinOS Console" dir=in action=allow protocol=tcp localport=8765
```

### Custom CORVIN_HOME

If you installed to a custom location, ensure the variable persists:

**Permanent (in user environment):**

```powershell
# PowerShell (current user only)
[Environment]::SetEnvironmentVariable("CORVIN_HOME", "D:\CorvinOS", "User")

# Then restart PowerShell and verify
Write-Host $env:CORVIN_HOME
```

**Temporary (current session only):**

```powershell
$env:CORVIN_HOME = "D:\CorvinOS"
corvinos-serve
```

## Testing

### Run E2E Tests

```powershell
# From CorvinOS repo root
python -m unittest tests.test_windows_paths_e2e -v
```

**Coverage:** 33 tests across:
- Path normalization (mixed separators, env vars, tilde expansion)
- Platform detection (Windows vs Unix)
- Symlink/junction creation
- CLI argument handling
- Installer path layout
- Module consistency

### Verify Installation

```powershell
# Check paths resolve correctly
corvin-install --check-paths

# Test audit chain creation
corvin-service audit verify

# Check tenant isolation
corvin-install --test-tenants
```

## Known Limitations

| Limitation | Status | Workaround |
|-----------|--------|-----------|
| WSL1 compatibility | ⚠️ Limited | Use WSL2 or native Windows |
| UNC paths (network shares) | ⚠️ Partial | Use mapped drives instead |
| Case-insensitive filesystem | ✓ Handled | NTFS auto-lowercases; pathlib aware |
| Long paths (>260 chars) | ✓ Fixed | Python 3.6+ + `pathlib` handle them |
| Concurrent writes in %TEMP% | ✓ Mitigated | uv uses unique temp file names |

## Development

### Install from Local Repo

```powershell
# Clone repo
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS

# Dev install (editable)
.\install.ps1 -Editable $PWD

# Or manually
uv tool install --editable ".[browser]"
```

### Run Tests

```powershell
# Unit tests
python -m unittest discover tests -v

# Windows-specific tests
python -m unittest tests.test_windows_paths_e2e -v

# Full test suite
python -m pytest tests/ -v --tb=short
```

### Building Documentation

```powershell
# Generate this guide
python scripts/generate-docs.py windows-installation
```

## References

- **ADR-0010:** Windows path delimiter issue (architecture decision)
- **Core paths module:** `core/paths/`
- **Installer source:** `install.ps1`
- **E2E tests:** `tests/test_windows_paths_e2e.py`
- **Platform utilities:** `core/paths/windows.py`

## Support

For issues specific to Windows installation:

1. **Check logs:** `$env:CORVIN_HOME\logs\console-supervisor.log`
2. **Re-run installer:** `irm https://corvin-labs.com/install.ps1 | iex`
3. **Report issue:** Include OS version, Python version, and logs
4. **Manual start:** `corvinos-serve --no-browser` to debug in terminal
