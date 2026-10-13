# Update.ps1 — CorvinOS Core Update Script (Windows PowerShell)
#
# Usage:
#   .\Update.ps1              # install latest update
#   .\Update.ps1 -CheckOnly   # check for updates without installing
#
# Output:
#   On -CheckOnly: JSON on stdout with version info
#   On success: exit code 0
#   On failure: exit code 1 with error on stderr
#
# Environment:
#   CORVIN_HOME — installation root (default: $HOME\.corvin)
#   CORVIN_UPDATE_REPO — git remote to pull from (default: origin)

param(
    [switch]$CheckOnly = $false
)

$ErrorActionPreference = "Stop"

# Paths
$CORVIN_HOME = if ($env:CORVIN_HOME) { $env:CORVIN_HOME } else { "$HOME\.corvin" }
$CORVIN_REPO = if (Test-Path "$CORVIN_HOME\.git") { $CORVIN_HOME } else { (git -C $CORVIN_HOME rev-parse --show-toplevel 2>$null) -or $CORVIN_HOME }
$CORVIN_UPDATE_REPO = if ($env:CORVIN_UPDATE_REPO) { $env:CORVIN_UPDATE_REPO } else { "origin" }

# Color codes
$RED = "`e[91m"
$GREEN = "`e[92m"
$YELLOW = "`e[93m"
$NC = "`e[0m"

# Logging functions
function Write-Log {
    param([string]$Message)
    Write-Host "${GREEN}[UPDATE]${NC} $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "${YELLOW}[WARN]${NC} $Message" -ForegroundColor Yellow
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "${RED}[ERROR]${NC} $Message" -ForegroundColor Red
    exit 1
}

# Get current version
function Get-CurrentVersion {
    try {
        $version = python3 -c "import importlib.metadata; print(importlib.metadata.version('corvinos'))" 2>$null
        if ($version) { return $version }
    } catch {}

    try {
        $version = Get-Content "$CORVIN_REPO/../pyproject.toml" |
            Select-String -Pattern '^\s*version\s*=' |
            Select-Object -First 1 |
            ForEach-Object { $_ -replace '^.*version\s*=\s*["\']([^"\']+)["\'].*$', '$1' }
        if ($version) { return $version }
    } catch {}

    return "unknown"
}

# Get latest available version
function Get-LatestVersion {
    try {
        # Fetch latest tags from remote
        git -C $CORVIN_REPO fetch --quiet $CORVIN_UPDATE_REPO 2>$null
        $version = git -C $CORVIN_REPO tag -l "v*" --sort=-v:refname 2>$null |
            Select-Object -First 1 |
            ForEach-Object { $_ -replace '^v', '' }
        if ($version) { return $version }
    } catch {}

    return "unknown"
}

# Check for updates
function Check-Update {
    $current = Get-CurrentVersion
    $latest = Get-LatestVersion

    $hasUpdate = ($current -ne "unknown" -and $latest -ne "unknown" -and $current -ne $latest)

    $json = @{
        has_update = $hasUpdate
        current_version = $current
        available_version = $latest
    } | ConvertTo-Json -Compress

    Write-Output $json
}

# Perform the update
function Invoke-Update {
    Write-Log "Starting update of CorvinOS Core..."

    # Verify repo exists
    if (-not (Test-Path "$CORVIN_REPO\.git")) {
        Write-Error-Custom "Git repository not found at $CORVIN_REPO"
    }

    # Verify git is available
    $gitPath = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitPath) {
        Write-Error-Custom "git not found. Please install git and try again."
    }

    # Fetch latest
    Write-Log "Fetching latest code from $CORVIN_UPDATE_REPO..."
    try {
        git -C $CORVIN_REPO fetch $CORVIN_UPDATE_REPO main 2>&1 | Out-Null
    } catch {
        Write-Error-Custom "Failed to fetch from $CORVIN_UPDATE_REPO : $_"
    }

    # Merge/rebase
    Write-Log "Merging updates..."
    try {
        git -C $CORVIN_REPO merge "$CORVIN_UPDATE_REPO/main" --no-edit 2>&1 | Out-Null
    } catch {
        Write-Warn "Merge conflict. Attempting to rebase..."
        try {
            git -C $CORVIN_REPO merge --abort 2>$null
            git -C $CORVIN_REPO rebase "$CORVIN_UPDATE_REPO/main" 2>&1 | Out-Null
        } catch {
            Write-Error-Custom "Failed to merge updates: $_"
        }
    }

    # Reinstall if venv present
    $pipPath = $null
    if (Test-Path "$CORVIN_HOME\.venv\Scripts\pip.exe") {
        $pipPath = "$CORVIN_HOME\.venv\Scripts\pip.exe"
    } elseif (Test-Path "$CORVIN_HOME\.venv\Scripts\pip") {
        $pipPath = "$CORVIN_HOME\.venv\Scripts\pip"
    }

    if ($pipPath) {
        Write-Log "Reinstalling Python packages..."
        try {
            & $pipPath install -e $CORVIN_REPO --quiet 2>&1 | Out-Null
        } catch {
            Write-Warn "Failed to reinstall packages: $_"
        }
    }

    # Verify update
    $newVersion = Get-CurrentVersion
    Write-Log "Update complete. Version: $newVersion"

    # Return summary
    $summary = @{
        success = $true
        message = "Update completed. Restart Console to apply changes."
        new_version = $newVersion
    } | ConvertTo-Json -Compress

    Write-Output $summary
}

# Main
try {
    if ($CheckOnly) {
        Check-Update
    } else {
        Invoke-Update
    }
} catch {
    Write-Error-Custom "Update failed: $_"
}
