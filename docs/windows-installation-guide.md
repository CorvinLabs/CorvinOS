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

A successful run ends with the console serving and your browser open on
`http://127.0.0.1:8765/console/`. If it does not, the run exits 3 and says why
(see [Exit codes](#exit-codes)) instead of reporting success.

### Options

```powershell
# Skip Claude Code installation
.\install.ps1 -NoClaudeCode

# Allow LAN A2A pairing (firewall rule)
.\install.ps1 -Lan

# Custom CorvinOS home
$env:CORVIN_HOME = "C:\Custom\CorvinOS"; .\install.ps1

# Different port (the URL is always 127.0.0.1, never localhost)
.\install.ps1 -Port 8790

# Install without starting anything / start but do not open a browser
.\install.ps1 -NoStart
.\install.ps1 -NoBrowser

# Force a console SPA rebuild (otherwise an existing build is reused)
.\install.ps1 -RebuildWeb

# Give a slow machine more time to reach the first HTTP 200 (default 180s)
.\install.ps1 -StartTimeoutSeconds 300

# Report every action, change nothing
.\install.ps1 -DryRun -Verbose
```

### What the installer verifies before claiming success

Each of these is a real check, not an assumption:

| Check | Why it is not optional |
|---|---|
| Console answers **HTTP 200 carrying the SPA shell** | A TCP connect succeeds the moment uvicorn binds -- seconds before the app is mounted, and forever when the app mounted the 503 "build failed" route instead. An open port proves nothing. |
| The SPA is **built before the console starts** | `mount_static()` decides once at boot. A console that boots without `web-next/dist/index.html` serves 503 for its whole life; a later rebuild does not fix it, only a restart does. |
| `POST /v1/console/auth/local-login` returns **3xx + a session cookie** | This is the step between "a page loads" and "you can chat". |
| The browser actually opened | Tried through the `http://` association, `explorer.exe`, `cmd /c start ""`, then known Edge/Chrome/Firefox binaries. A failure to open is reported, not swallowed. |
| Port ownership | A console already serving on the port is reused; a stale CorvinOS process is tree-killed (the launcher spawns uvicorn as a child, so killing only the parent orphans the listener); a *foreign* owner is reported and left alone. |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Installed, console serving, login verified, browser opened |
| 1 | Installation failed (see the error log named in the summary) |
| 2 | Prerequisites missing |
| 3 | **Installed, but the console is not serving.** The package is fine. The summary prints the server's own last log lines plus a diagnosis -- audit-chain tripwire, port already in use, a Python import error, or a failed web build -- and the command to reproduce it in the foreground. |
| 4 | `-Editable` path is not a CorvinOS clone |

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
- **Size:** ~75 KB
- **Runtime:** 2-5 minutes (includes Python bootstrap; add several minutes when
  the console SPA has to be built)
- **Privileges:** No admin rights needed (except optional firewall rule)
- **Encoding:** ASCII-only **by contract**. Windows PowerShell 5.1 decodes a
  BOM-less script as cp1252, so UTF-8 box-drawing or check-mark characters
  arrive as smart quotes that PowerShell honours as string delimiters -- a
  parse error before line 1 runs. CI enforces this with a byte scan and an AST
  parse of the file.

**Features:**
- Bootstraps `uv` package manager (brings own Python)
- Bootstraps a local Node.js runtime (version from `.nvmrc`)
- Builds the console SPA when no build is present (`-RebuildWeb` forces it)
- Detects & installs Claude Code (optional)
- Creates autostart task (Scheduled Task or Startup folder)
- Starts the console and waits for a real HTTP 200 carrying the SPA shell
- Verifies local login issues a session cookie, then opens the browser

**Failure Recovery:**
- Every external command runs under a timeout and is logged separately per
  step in the log directory named in the summary
- `uv tool install --force` is retried once after stopping processes that run
  out of the tool venv (they are named `python.exe` and live under `%APPDATA%`,
  which is why a name-based sweep misses them and the install failed with
  "Access is denied (os error 5)")
- A console that fails to start produces exit 3, its own last log lines and a
  diagnosis -- never a success message
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
