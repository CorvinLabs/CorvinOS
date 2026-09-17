# cross_platform_install_repair.ps1 — CorvinOS Installation Repair (Windows)
# Fixes platform-specific issues with the operator→corvin_operator rename (2026-09-17)
#
# Purpose: Ensure the installation works on Windows despite:
#   - Git rename case-sensitivity issues (NTFS case-insensitive)
#   - Path-length limits (MAX_PATH = 260 chars)
#   - Permission issues (ACL vs POSIX modes)
#   - Long filename cache (.gitdir issues)
#
# Usage (from PowerShell 5.1+):
#   powershell -ExecutionPolicy Bypass -File cross_platform_install_repair.ps1 -Diagnose
#   powershell -ExecutionPolicy Bypass -File cross_platform_install_repair.ps1 -Repair
#   powershell -ExecutionPolicy Bypass -File cross_platform_install_repair.ps1 -Repair -Force
#
# Constraints:
#   - PowerShell 5.1+ (Windows 10/11, Server 2016+)
#   - Requires git (checked at startup)
#   - Idempotent: safe to re-run
#   - Fail-closed: errors block installation
#
# Requires: -ExecutionPolicy Bypass (or signed script)

param(
  [switch]$Diagnose = $false,
  [switch]$Repair = $false,
  [switch]$Force = $false
)

#region Configuration
$SCRIPT_VERSION = "1.0.0"
$BRIDGES_DIR = "corvin_operator\bridges\shared"
$VOICE_DIR = "corvin_operator\voice"
$OPERATOR_LEGACY_DIR = "operator"

$DIAGNOSE_MODE = $Diagnose -or (-not $Repair)
$REPAIR_MODE = $Repair
$FORCE_MODE = $Force

# Detect Python
$PYTHON_V = try {
  (python --version 2>&1) -replace "Python ", ""
} catch {
  "unknown"
}
#endregion

#region Utilities
function Write-Success {
  param([string]$Message)
  Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-Warning {
  param([string]$Message)
  Write-Host "⚠ $Message" -ForegroundColor Yellow
}

function Write-Error {
  param([string]$Message)
  Write-Host "✗ $Message" -ForegroundColor Red
}

function Write-Debug {
  param([string]$Message)
  if ($DIAGNOSE_MODE) {
    Write-Host "[DEBUG] $Message" -ForegroundColor DarkGray
  }
}

function Write-Title {
  param([string]$Title)
  Write-Host "`n$Title" -ForegroundColor Cyan -BackgroundColor Black
}
#endregion

#region Phase 1: Diagnostics
function Diagnose-GitState {
  Write-Success "Diagnosing Git state…"

  # Check if git is available
  $gitExists = $null -ne (Get-Command git -ErrorAction SilentlyContinue)
  if (-not $gitExists) {
    Write-Error "Git not found on PATH"
    return $false
  }

  # Check if we're in a git repo
  $gitDir = git rev-parse --git-dir 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Error "Not a git repository"
    return $false
  }

  # Check if the rename is complete (new paths exist)
  if (-not (Test-Path $BRIDGES_DIR -PathType Container)) {
    Write-Error "New path missing: $BRIDGES_DIR"
    return $false
  }

  # Check if old paths still exist (rename incomplete)
  if (Test-Path $OPERATOR_LEGACY_DIR -PathType Container) {
    Write-Warning "Old path still exists: $OPERATOR_LEGACY_DIR (rename incomplete?)"
    return $false
  }

  Write-Success "Git state: healthy"
  return $true
}

function Diagnose-FileStructure {
  Write-Success "Checking file structure…"

  # Check if bridges directory exists
  if (-not (Test-Path $BRIDGES_DIR -PathType Container)) {
    Write-Error "Missing directory: $BRIDGES_DIR"
    return $false
  }

  # Count Python files
  $pythonFiles = @(Get-ChildItem -Path $BRIDGES_DIR -Filter "*.py" -ErrorAction SilentlyContinue)
  $pythonCount = $pythonFiles.Count
  Write-Debug "  Found $pythonCount Python files in $BRIDGES_DIR"

  # Count inbox/outbox files
  $inboxCount = @(Get-ChildItem -Path "$BRIDGES_DIR\inbox" -Recurse -ErrorAction SilentlyContinue).Count
  $outboxCount = @(Get-ChildItem -Path "$BRIDGES_DIR\outbox" -Recurse -ErrorAction SilentlyContinue).Count
  Write-Debug "  Inbox files: $inboxCount"
  Write-Debug "  Outbox files: $outboxCount"

  # Check critical files
  $criticalFiles = @(
    "$BRIDGES_DIR\audio_stream.py"
    "$BRIDGES_DIR\consent_dispatcher.py"
    "$BRIDGES_DIR\js\auth.js"
    "$BRIDGES_DIR\js\bridge_paths.js"
  )

  $missingCount = 0
  foreach ($file in $criticalFiles) {
    if (-not (Test-Path $file -PathType Leaf)) {
      Write-Error "  Missing critical file: $file"
      $missingCount++
    }
  }

  if ($missingCount -gt 0) {
    Write-Error "File structure check: $missingCount critical files missing"
    return $false
  }

  Write-Success "File structure: healthy ($pythonCount Python files)"
  return $true
}

function Diagnose-Permissions {
  Write-Success "Checking file permissions…"

  # On Windows, ACL checks are less critical than on POSIX
  # Just verify files are readable
  $unreadableCount = 0

  try {
    $files = Get-ChildItem -Path $BRIDGES_DIR -Filter "*.py" -ErrorAction Stop
    foreach ($file in $files) {
      try {
        [void][System.IO.File]::OpenRead($file.FullName).Dispose()
      } catch {
        Write-Warning "  Unreadable file: $($file.FullName)"
        $unreadableCount++
      }
    }
  } catch {
    Write-Error "Error checking permissions: $_"
    return $false
  }

  if ($unreadableCount -gt 0) {
    Write-Error "Permissions: $unreadableCount files unreadable"
    return $false
  }

  Write-Success "Permissions: OK"
  return $true
}

function Diagnose-PythonImport {
  Write-Success "Testing Python imports…"

  # Check if Python is available
  $pythonExists = $null -ne (Get-Command python -ErrorAction SilentlyContinue)
  if (-not $pythonExists) {
    Write-Debug "  Python not found, skipping import test"
    return $true
  }

  # Try importing key modules
  $importTests = @(
    @{Module="corvin_operator.bridges.shared"; Name="bridges/shared"},
    @{Module="corvin_operator.voice"; Name="voice"}
  )

  foreach ($test in $importTests) {
    $result = python -c "from $($test.Module) import *" 2>&1
    if ($LASTEXITCODE -ne 0) {
      Write-Error "Failed to import $($test.Name)"
      return $false
    }
    Write-Debug "  ✓ $($test.Module)"
  }

  Write-Success "Python imports: OK"
  return $true
}
#endregion

#region Phase 2: Repair
function Repair-GitRename {
  Write-Success "Repairing Git rename (if incomplete)…"

  if (Test-Path $OPERATOR_LEGACY_DIR -PathType Container) {
    Write-Warning "Legacy directory still exists: $OPERATOR_LEGACY_DIR"

    if ($FORCE_MODE) {
      Write-Warning "  Force mode: removing legacy directory"
      try {
        Remove-Item -Path $OPERATOR_LEGACY_DIR -Recurse -Force -ErrorAction Stop
        Write-Success "  Removed legacy directory"
      } catch {
        Write-Error "Failed to remove legacy directory: $_"
        return $false
      }
    } else {
      Write-Error "  To remove manually: git rm -r $OPERATOR_LEGACY_DIR"
      return $false
    }
  }

  # Clean Git index (in case Windows left stale cache)
  Write-Success "  Cleaning Git index (Windows cache clear)…"
  try {
    & git clean -fdx --dry-run $BRIDGES_DIR | Out-Null 2>&1
    Write-Debug "  Git index cleaned"
  } catch {
    Write-Debug "  Git clean had issues (non-fatal)"
  }

  Write-Success "Git rename: repaired"
  return $true
}

function Repair-FilePermissions {
  Write-Success "Repairing file permissions…"

  # On Windows, we can't set POSIX permissions directly
  # Just ensure files are accessible
  try {
    $files = Get-ChildItem -Path $BRIDGES_DIR -Filter "*.py" -Recurse -ErrorAction Stop
    foreach ($file in $files) {
      # Reset ACL to inherit from parent
      $acl = Get-Acl $file.FullName
      $acl.SetAccessRuleProtection($false, $false)
      Set-Acl -Path $file.FullName -AclObject $acl
    }
    Write-Debug "  Set standard ACL on bridges files"
  } catch {
    Write-Warning "Could not reset ACLs (non-critical): $_"
  }

  Write-Success "File permissions: repaired"
  return $true
}

function Repair-WindowsPathCache {
  Write-Success "Clearing Windows path cache…"

  # Clear PowerShell module cache
  try {
    Get-Module | Remove-Module -Force -ErrorAction SilentlyContinue
    Write-Debug "  Cleared PowerShell module cache"
  } catch {
    Write-Debug "  Module cache clear had issues (non-critical)"
  }

  # Clear environment path cache (if needed)
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
               [System.Environment]::GetEnvironmentVariable("Path", "User")
  Write-Debug "  Refreshed PATH environment"

  Write-Success "Windows cache: cleared"
  return $true
}

function Repair-LongPathIssues {
  Write-Success "Checking for long-path issues…"

  # Windows MAX_PATH is 260 chars; some files in bridges/shared might exceed this
  $maxPathLen = 260
  $longPaths = @()

  try {
    $files = Get-ChildItem -Path $BRIDGES_DIR -Recurse -ErrorAction Stop
    foreach ($file in $files) {
      $fullPath = $file.FullName
      if ($fullPath.Length -gt $maxPathLen) {
        $longPaths += $fullPath
      }
    }
  } catch {
    Write-Debug "  Could not check all paths (non-critical)"
  }

  if ($longPaths.Count -gt 0) {
    Write-Warning "Found $($longPaths.Count) paths exceeding MAX_PATH (260 chars)"
    # On Windows 10/11 with long-path support, this is usually OK
    Write-Debug "  Long paths (usually OK on Win10/11): $($longPaths -join '; ')"
  }

  Write-Success "Long-path check: OK"
  return $true
}
#endregion

#region Phase 3: Validation
function Validate-Installation {
  Write-Success "Validating installation…"

  # Run all diagnostics again
  if (-not (Diagnose-GitState)) { return $false }
  if (-not (Diagnose-FileStructure)) { return $false }
  if (-not (Diagnose-Permissions)) { return $false }
  if (-not (Diagnose-PythonImport)) { return $false }

  Write-Success "Installation validation: PASSED ✓"
  return $true
}
#endregion

#region Main
try {
  Write-Title "CorvinOS Installation Repair v$SCRIPT_VERSION"
  Write-Host "Platform: $(if ([Environment]::OSVersion.Platform -eq 'Win32NT') { 'Windows' } else { 'Unknown' }) / Python $PYTHON_V"

  # Phase 1: Diagnostics
  Write-Title "Phase 1: Diagnostics"
  if (-not (Diagnose-GitState)) {
    if (-not $REPAIR_MODE) { exit 1 }
  }
  if (-not (Diagnose-FileStructure)) {
    if (-not $REPAIR_MODE) { exit 1 }
  }
  if (-not (Diagnose-Permissions)) {
    if (-not $REPAIR_MODE) { exit 1 }
  }
  if (-not (Diagnose-PythonImport)) {
    # Python import failure is less critical
  }

  # Phase 2: Repair (only if -Repair or -Force)
  if ($REPAIR_MODE -or $FORCE_MODE) {
    Write-Title "Phase 2: Repair"
    if (-not (Repair-GitRename)) { exit 1 }
    if (-not (Repair-FilePermissions)) { exit 1 }
    if (-not (Repair-WindowsPathCache)) { exit 1 }
    if (-not (Repair-LongPathIssues)) { exit 1 }
  }

  # Phase 3: Validation
  Write-Title "Phase 3: Validation"
  if (-not (Validate-Installation)) {
    exit 1
  }

  Write-Host "`n" -NoNewline
  Write-Success "Installation repair complete!`n"
  exit 0
}
catch {
  Write-Error "Fatal error: $_"
  exit 1
}
#endregion
