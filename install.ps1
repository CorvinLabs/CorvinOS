#Requires -Version 5.1
# CorvinOS Robust Installation Script for Windows
# Production-ready with comprehensive error handling, dependency detection, cleanup on failure
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File install-robust.ps1 -Editable .\
#   powershell -ExecutionPolicy Bypass -File install-robust.ps1 -Editable . -Verbose
#   powershell -ExecutionPolicy Bypass -File install-robust.ps1 -Editable . -DryRun
#
# Requirements:
#   - PowerShell 5.1+
#   - Windows 10+ or Server 2016+
#   - 500MB+ free disk space
#   - Internet connection (for initial bootstrap)
#
# Exit Codes:
#   0 = Success
#   1 = Installation failed (check log)
#   2 = Prerequisites missing/failed
#   3 = Cleanup failed (manual intervention required)
#   4 = Configuration error (editable path invalid)

param(
    [Parameter(Mandatory=$false)]
    [Alias("e")]
    [string]$Editable = ".",

    [switch]$DryRun = $false,
    [switch]$NoClaudeCode = $false,
    [switch]$Lan = $false
)

# ─────────────────────────────────────────────────────────────────────────────
# Global Configuration & Error Handling
# ─────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

# Force TLS 1.2 (Windows PowerShell 5.1 / .NET Framework defaults can omit it,
# which breaks HTTPS downloads to modern servers with "underlying connection was closed")
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# Timestamp for all logs
$InstallTimestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogDir = Join-Path $env:TEMP "corvinos-install-$InstallTimestamp"
$MainLogFile = Join-Path $LogDir "install.log"
$ErrorLogFile = Join-Path $LogDir "install-errors.log"
$DebugLogFile = Join-Path $LogDir "install-debug.log"

# Create log directory
$null = New-Item -ItemType Directory -Path $LogDir -Force -ErrorAction SilentlyContinue

# Progress tracking
$InstallSteps = @(
    "Pre-checks (prerequisites, paths)",
    "Resolve editable path",
    "Verify disk space",
    "Check long path support",
    "Bootstrap uv (Python manager)",
    "Bootstrap Node.js",
    "Install corvinos package",
    "Bootstrap components",
    "Configure system services",
    "Final verification",
    "Start console & open browser"
)
$CurrentStep = 0
$TotalSteps = $InstallSteps.Count

# ─────────────────────────────────────────────────────────────────────────────
# Logging Functions
# ─────────────────────────────────────────────────────────────────────────────

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet("Info", "Warn", "Error", "Success", "Debug")]
        [string]$Level = "Info",
        [switch]$NoNewline = $false
    )

    $timestamp = Get-Date -Format "HH:mm:ss.fff"
    $logMessage = "[$timestamp] [$Level] $Message"

    # Console output with colors
    $color = @{
        "Info"    = "White"
        "Warn"    = "Yellow"
        "Error"   = "Red"
        "Success" = "Green"
        "Debug"   = "DarkGray"
    }[$Level]

    Write-Host $logMessage -ForegroundColor $color -NoNewline:$NoNewline

    # File logging
    Add-Content -Path $MainLogFile -Value $logMessage -ErrorAction SilentlyContinue
    if ($Level -eq "Error") {
        Add-Content -Path $ErrorLogFile -Value $logMessage -ErrorAction SilentlyContinue
    }
    if ($Level -eq "Debug") {
        Add-Content -Path $DebugLogFile -Value $logMessage -ErrorAction SilentlyContinue
    }
}

function Write-Progress-Step {
    param([string]$Message)
    $CurrentStep++
    $percent = [math]::Round(($CurrentStep / $TotalSteps) * 100)
    Write-Host "`n" -NoNewline
    Write-Progress -Activity "CorvinOS Installation" -CurrentOperation $Message `
        -PercentComplete $percent -Status "Step $CurrentStep/$TotalSteps"
    Write-Log -Message "$Message [$CurrentStep/$TotalSteps]" -Level "Info"
}

function Write-Header {
    param([string]$Title)
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host $Title -ForegroundColor Cyan
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
}

function Invoke-DownloadWithRetry {
    # Corporate/CDN networks intermittently reset the TLS handshake on the
    # first attempt ("underlying connection was closed: unexpected error on
    # send") even though the URL is reachable and TLS 1.2 is negotiated
    # correctly — retrying the same request succeeds every time observed.
    # curl.exe (Schannel) is tried first per attempt since it has proven more
    # resilient than .NET's Invoke-WebRequest against this failure mode; it
    # falls back to Invoke-WebRequest if curl is unavailable.
    param(
        [string]$Uri,
        [string]$OutFile,
        [int]$MaxAttempts = 5,
        [int]$TimeoutSec = 30
    )
    $useCurl = [bool](Get-Command curl.exe -ErrorAction SilentlyContinue)
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            if ($useCurl) {
                & curl.exe -fsSL --max-time $TimeoutSec -o $OutFile $Uri
                if ($LASTEXITCODE -ne 0) {
                    throw "curl.exe exited with code $LASTEXITCODE"
                }
            } else {
                Invoke-WebRequest -Uri $Uri -OutFile $OutFile -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
            }
            return
        } catch {
            if ($attempt -ge $MaxAttempts) { throw }
            $backoffSec = [math]::Min(2 * $attempt, 10)
            Write-Log -Message "Download attempt $attempt/$MaxAttempts failed ($_); retrying in ${backoffSec}s..." -Level "Warn"
            Start-Sleep -Seconds $backoffSec
        }
    }
}

function Write-Summary {
    param([string]$Status, [int]$ExitCode = 0)
    Write-Host ""
    Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║ Installation Summary" -ForegroundColor Cyan
    Write-Host "╠════════════════════════════════════════════════════════════════╣" -ForegroundColor Cyan
    Write-Host "║ Status: $Status" -ForegroundColor $(if ($ExitCode -eq 0) {"Green"} else {"Red"})
    Write-Host "║ Timestamp: $InstallTimestamp" -ForegroundColor White
    Write-Host "║ Log Directory: $LogDir" -ForegroundColor White
    Write-Host "║ Main Log: $MainLogFile" -ForegroundColor White
    if ($ExitCode -ne 0) {
        Write-Host "║ Error Log: $ErrorLogFile" -ForegroundColor Red
    }
    Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
}

# ─────────────────────────────────────────────────────────────────────────────
# Error Handling & Cleanup
# ─────────────────────────────────────────────────────────────────────────────

$script:CleanupOnExit = $false

function Register-Cleanup {
    $script:CleanupOnExit = $true
    $null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
        if ($script:CleanupOnExit) {
            Invoke-Cleanup
        }
    }
}

function Invoke-Cleanup {
    Write-Log -Message "Cleaning up on exit..." -Level "Warn"

    # Kill any orphaned processes
    $orphanedProcesses = @("uv.exe", "node.exe", "python.exe", "pip.exe", "npm.exe")
    foreach ($proc in $orphanedProcesses) {
        Get-Process -Name $proc -ErrorAction SilentlyContinue |
            Stop-Process -Force -ErrorAction SilentlyContinue
    }

    # Clean temp files if installation failed
    if ($script:InstallationFailed -and (Test-Path $LogDir)) {
        Write-Log -Message "Installation failed. Logs preserved in: $LogDir" -Level "Warn"
    }
}

trap {
    Write-Log -Message "FATAL ERROR: $_" -Level "Error"
    Write-Log -Message "Stack trace: $($_.ScriptStackTrace)" -Level "Debug"
    Write-Summary -Status "FAILED (see error log)" -ExitCode 1
    Invoke-Cleanup
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Pre-Checks
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 1: Pre-Checks & Prerequisites"
Register-Cleanup

Write-Progress-Step "Checking PowerShell version"
$psVersion = $PSVersionTable.PSVersion
if ($psVersion.Major -lt 5 -or ($psVersion.Major -eq 5 -and $psVersion.Minor -lt 1)) {
    Write-Log -Message "PowerShell $psVersion is too old (need 5.1+)" -Level "Error"
    Write-Summary -Status "FAILED" -ExitCode 2
    exit 2
}
Write-Log -Message "PowerShell version: $psVersion" -Level "Success"

Write-Progress-Step "Checking Windows version"
$osInfo = Get-CimInstance -ClassName Win32_OperatingSystem
$osVersion = [version]$osInfo.Version
if ($osVersion.Major -lt 10) {
    Write-Log -Message "Windows $osVersion is too old (need Windows 10+)" -Level "Error"
    Write-Summary -Status "FAILED" -ExitCode 2
    exit 2
}
Write-Log -Message "Windows version: $osVersion ($($osInfo.Caption))" -Level "Success"

Write-Progress-Step "Checking prerequisites (curl, git)"
$missingPrereqs = @()
foreach ($cmd in @("curl", "git", "powershell")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        $missingPrereqs += $cmd
    }
}
if ($missingPrereqs.Count -gt 0) {
    Write-Log -Message "Missing prerequisites: $($missingPrereqs -join ', ')" -Level "Error"
    Write-Summary -Status "FAILED" -ExitCode 2
    exit 2
}
Write-Log -Message "All prerequisites found" -Level "Success"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Path Validation & Resolution
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 2: Path Validation"

Write-Progress-Step "Resolving editable path"
if (-not (Test-Path -Path $Editable -PathType Container)) {
    Write-Log -Message "Editable path does not exist: $Editable" -Level "Error"
    Write-Summary -Status "FAILED" -ExitCode 4
    exit 4
}

$EditablePath = (Resolve-Path -Path $Editable).Path
Write-Log -Message "Resolved editable path: $EditablePath" -Level "Success"

# Verify CorvinOS repo structure
Write-Progress-Step "Verifying CorvinOS repository structure"
$requiredFiles = @("install.ps1", "install.sh", "pyproject.toml", "package.json")
$missingFiles = $requiredFiles | Where-Object {
    -not (Test-Path (Join-Path $EditablePath $_))
}
if ($missingFiles.Count -gt 0) {
    Write-Log -Message "Missing repository files: $($missingFiles -join ', ')" -Level "Error"
    Write-Summary -Status "FAILED" -ExitCode 4
    exit 4
}
Write-Log -Message "Repository structure verified" -Level "Success"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Disk Space & System Checks
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 3: System Requirements"

Write-Progress-Step "Checking available disk space"
$drive = (Get-Item $EditablePath).PSDrive
$driveInfo = Get-Volume -DriveLetter $drive.Name -ErrorAction SilentlyContinue
if ($driveInfo) {
    $freeGB = [math]::Round($driveInfo.SizeRemaining / 1GB, 2)
    if ($freeGB -lt 0.5) {
        Write-Log -Message "Insufficient disk space: ${freeGB}GB available (need 500MB+)" -Level "Error"
        Write-Summary -Status "FAILED" -ExitCode 2
        exit 2
    }
    Write-Log -Message "Available disk space: ${freeGB}GB" -Level "Success"
}

Write-Progress-Step "Checking long path support (Windows limit workaround)"
# Check registry for LongPathsEnabled
$regPath = "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem"
$longPathEnabled = $false
try {
    $regValue = Get-ItemProperty -Path $regPath -Name LongPathsEnabled -ErrorAction SilentlyContinue
    $longPathEnabled = $regValue.LongPathsEnabled -eq 1
} catch { }

if (-not $longPathEnabled) {
    Write-Log -Message "Long path support is disabled (non-critical, may affect very deep paths)" -Level "Warn"
    Write-Log -Message "To enable: Set-ItemProperty -Path '$regPath' -Name LongPathsEnabled -Value 1 -Force" -Level "Info"
}

Write-Progress-Step "Checking network connectivity"
try {
    $testUrl = "https://github.com"
    $response = Invoke-WebRequest -Uri $testUrl -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
    Write-Log -Message "Network connectivity verified" -Level "Success"
} catch {
    Write-Log -Message "Network connectivity check failed: $_" -Level "Warn"
    Write-Log -Message "Installation may fail if internet is required" -Level "Warn"
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3.5: Windows-Specific Issues (Known Blockers)
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 3.5: Windows Issue Detection & Resolution"

Write-Progress-Step "Checking for duplicate HTTP packages"
try {
    $pipList = & pip list 2>&1
    $httpcore2 = $pipList | Select-String "httpcore2"
    $httpx2 = $pipList | Select-String "httpx2"

    if ($httpcore2 -or $httpx2) {
        Write-Log -Message "Duplicate HTTP packages detected (httpcore2/httpx2)" -Level "Warn"
        Write-Log -Message "This breaks A2A connectivity. Removing..." -Level "Info"

        if (-not $DryRun) {
            if ($httpcore2) { & pip uninstall httpcore2 -y 2>&1 | ForEach-Object { Write-Log -Message $_ -Level "Debug" } }
            if ($httpx2) { & pip uninstall httpx2 -y 2>&1 | ForEach-Object { Write-Log -Message $_ -Level "Debug" } }
            Write-Log -Message "Duplicate packages removed" -Level "Success"
        }
    } else {
        Write-Log -Message "No duplicate HTTP packages found" -Level "Success"
    }
} catch {
    Write-Log -Message "Could not check for duplicate packages: $_" -Level "Warn"
}

Write-Progress-Step "Checking for file locks (corvinos processes)"
try {
    $orphanedProcesses = @("corvinos-serve", "corvin-serve", "python", "uv.exe", "node.exe")
    $foundProcesses = @()

    foreach ($procName in $orphanedProcesses) {
        $procs = Get-Process -Name ($procName -replace "\.exe", "") -ErrorAction SilentlyContinue
        if ($procs) {
            $foundProcesses += $procs
        }
    }

    if ($foundProcesses.Count -gt 0) {
        Write-Log -Message "Found $($foundProcesses.Count) running processes that may hold file locks" -Level "Warn"
        if (-not $DryRun) {
            Write-Log -Message "Stopping processes before installation..." -Level "Info"
            $foundProcesses | ForEach-Object {
                Write-Log -Message "Stopping $($_.ProcessName) (PID $($_.Id))" -Level "Info"
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
            Write-Log -Message "Processes stopped" -Level "Success"
        }
    } else {
        Write-Log -Message "No interfering processes found" -Level "Success"
    }
} catch {
    Write-Log -Message "Process cleanup check failed: $_" -Level "Warn"
}

Write-Progress-Step "Checking ScheduledTask state"
try {
    $existingTask = Get-ScheduledTask -TaskName "CorvinOS-Console" `
        -ErrorAction SilentlyContinue

    if ($existingTask) {
        $taskStatus = $existingTask.State
        Write-Log -Message "ScheduledTask exists (Status: $taskStatus)" -Level "Info"

        if ($taskStatus -ne "Ready") {
            Write-Log -Message "Task is not ready; will re-register during Phase 9" -Level "Warn"
        }
    } else {
        Write-Log -Message "ScheduledTask does not exist (will create during Phase 9)" -Level "Info"
    }
} catch {
    Write-Log -Message "Could not check ScheduledTask state: $_" -Level "Warn"
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Bootstrap Environment
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 4: Bootstrap Environment Setup"

# Single source of truth (mirrors core/paths/tenant.py::corvin_home and every
# other paths.py copy across the codebase, and install.sh's Python-side
# resolution): $CORVIN_HOME env var, else a repo-local .corvin next to the
# source checkout, else the platform home directory's .corvin.
$RepoLocalCorvinHome = Join-Path $EditablePath ".corvin"
$CorvinHome = if ($env:CORVIN_HOME) {
    $env:CORVIN_HOME
} elseif (Test-Path -Path $RepoLocalCorvinHome -PathType Container) {
    $RepoLocalCorvinHome
} else {
    Join-Path $env:USERPROFILE ".corvin"
}

Write-Progress-Step "Creating CORVIN_HOME directory"
if (-not (Test-Path $CorvinHome)) {
    $null = New-Item -ItemType Directory -Path $CorvinHome -Force -ErrorAction Stop
    Write-Log -Message "Created CORVIN_HOME: $CorvinHome" -Level "Success"
} else {
    Write-Log -Message "CORVIN_HOME exists: $CorvinHome" -Level "Info"
}

# Set environment variables for this session
$env:CORVIN_HOME = $CorvinHome
$env:CORVIN_INSTALL_LOG = $MainLogFile

Write-Progress-Step "Setting up PATH for uv and Node.js"
$NodeRoot = Join-Path $CorvinHome "node"
$UvBin = Join-Path $CorvinHome "bin"
$paths = @($UvBin, "$NodeRoot\bin", $env:PATH) | Where-Object { $_ }
$env:PATH = $paths -join ";"
Write-Log -Message "Updated PATH for bootstrap tools" -Level "Success"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 5: Bootstrap uv (Python Manager)
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 5: Bootstrap uv (Python Manager)"

Write-Progress-Step "Downloading and verifying uv binary"
$UvVersion = "0.12.9"
$UvInstallerUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-installer.ps1"
$UvInstallerSha256 = "69de475bf929f1ac248efb5a85189177a45517e2346cd68762bde453fec10a6b"
$UvInstallerPath = Join-Path $env:TEMP "uv-installer-$UvVersion.ps1"

if ($DryRun) {
    Write-Log -Message "[DRY RUN] Would download: $UvInstallerUrl" -Level "Info"
} else {
    try {
        Write-Log -Message "Downloading uv installer (version $UvVersion)..." -Level "Info"
        Invoke-DownloadWithRetry -Uri $UvInstallerUrl -OutFile $UvInstallerPath -TimeoutSec 30

        # Verify SHA256
        $hash = (Get-FileHash -Path $UvInstallerPath -Algorithm SHA256).Hash
        if ($hash -ne $UvInstallerSha256) {
            Write-Log -Message "SHA256 mismatch! Expected: $UvInstallerSha256, Got: $hash" -Level "Error"
            Write-Summary -Status "FAILED (security validation)" -ExitCode 1
            exit 1
        }
        Write-Log -Message "uv installer verified (SHA256 match)" -Level "Success"
    } catch {
        Write-Log -Message "Failed to download uv installer: $_" -Level "Error"
        Write-Summary -Status "FAILED" -ExitCode 1
        exit 1
    }

    # Run uv installer
    Write-Progress-Step "Installing uv"
    try {
        Write-Log -Message "Running uv installer..." -Level "Info"
        & $UvInstallerPath 2>&1 | ForEach-Object {
            Write-Log -Message $_ -Level "Debug"
        }
        Write-Log -Message "uv installation completed" -Level "Success"
    } catch {
        Write-Log -Message "uv installation failed: $_" -Level "Error"
        Write-Summary -Status "FAILED" -ExitCode 1
        exit 1
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 6: Bootstrap Node.js
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 6: Bootstrap Node.js Runtime"

Write-Progress-Step "Downloading and installing Node.js"
# Read .nvmrc or default to v24.18.0
$NvmrcPath = Join-Path $EditablePath ".nvmrc"
$NodeVersion = if (Test-Path $NvmrcPath) {
    (Get-Content $NvmrcPath -Raw).Trim() -replace '^v', ''
} else {
    "24.18.0"
}

$NodeArchitecture = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
$NodeUrl = "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-$NodeArchitecture.zip"
$NodeZipPath = Join-Path $env:TEMP "node-v$NodeVersion-win-$NodeArchitecture.zip"
$NodeExtractPath = Join-Path $env:TEMP "node-v$NodeVersion-win-$NodeArchitecture"

if ($DryRun) {
    Write-Log -Message "[DRY RUN] Would download: $NodeUrl" -Level "Info"
} else {
    try {
        Write-Log -Message "Downloading Node.js v$NodeVersion..." -Level "Info"
        Invoke-DownloadWithRetry -Uri $NodeUrl -OutFile $NodeZipPath -TimeoutSec 60

        Write-Log -Message "Extracting Node.js..." -Level "Info"
        Expand-Archive -Path $NodeZipPath -DestinationPath $env:TEMP -Force -ErrorAction Stop

        # Move to CORVIN_HOME
        if (Test-Path $NodeRoot) {
            Remove-Item -Path $NodeRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        Rename-Item -Path $NodeExtractPath -NewName "node" -Force
        Move-Item -Path (Join-Path $env:TEMP "node") -Destination $CorvinHome -Force

        Write-Log -Message "Node.js v$NodeVersion installed" -Level "Success"
        Remove-Item -Path $NodeZipPath -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Log -Message "Node.js installation failed: $_" -Level "Error"
        Write-Summary -Status "FAILED" -ExitCode 1
        exit 1
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 7: Install corvinos Package
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 7: Install CorvinOS Package"

Write-Progress-Step "Installing corvinos via uv"
# Mirrors install.sh Phase 2: an editable path installs the LOCAL checkout
# (with the [browser] extra), never the published PyPI package. install.ps1
# used to ignore -Editable here entirely and always pull "corvinos>=2.0.0"
# from PyPI, which is why an editable/dev install failed dependency
# resolution against a package that was never meant to be fetched remotely.
$uvArgs = @("tool", "install", "--force", "--editable", "$EditablePath[browser]")
if ($DryRun) {
    Write-Log -Message "[DRY RUN] Would run: uv $($uvArgs -join ' ')" -Level "Info"
} else {
    try {
        Write-Log -Message "Installing corvinos package (editable from $EditablePath)..." -Level "Info"
        # Local override: with the script-wide "Stop" preference, each stderr
        # line uv writes gets promoted to a terminating error mid-pipeline,
        # so only the FIRST diagnostic line ever reached the log (that is why
        # a genuine "no solution found" resolver error looked like a
        # one-line, unactionable message). Capture everything, then decide
        # success/failure from the real exit code.
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & uv @uvArgs 2>&1 | Tee-Object -FilePath $MainLogFile -Append | ForEach-Object {
                Write-Log -Message $_ -Level "Debug"
            }
        } finally {
            $ErrorActionPreference = $prevEap
        }
        if ($LASTEXITCODE -ne 0) {
            throw "uv exited with code $LASTEXITCODE"
        }
        Write-Log -Message "corvinos installation completed" -Level "Success"
    } catch {
        Write-Log -Message "corvinos installation failed: $_" -Level "Error"
        Write-Summary -Status "FAILED" -ExitCode 1
        exit 1
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 8: Bootstrap Components
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 8: Bootstrap Components"

$components = @(
    @{ Name = "Console"; Script = "core/console/bootstrap.sh" },
    @{ Name = "Gateway"; Script = "core/gateway/bootstrap.sh" },
    @{ Name = "Compliance"; Script = "core/compliance/bootstrap.sh" }
)

foreach ($component in $components) {
    Write-Progress-Step "Bootstrapping $($component.Name)"
    $scriptPath = Join-Path $EditablePath $component.Script

    if (-not (Test-Path $scriptPath)) {
        Write-Log -Message "$($component.Name) bootstrap script not found (skipping)" -Level "Warn"
        continue
    }

    if ($DryRun) {
        Write-Log -Message "[DRY RUN] Would run: bash $($component.Script)" -Level "Info"
    } else {
        try {
            Write-Log -Message "Running $($component.Name) bootstrap..." -Level "Info"
            bash $scriptPath 2>&1 | Tee-Object -FilePath $MainLogFile -Append | ForEach-Object {
                Write-Log -Message $_ -Level "Debug"
            }
            Write-Log -Message "$($component.Name) bootstrap completed" -Level "Success"
        } catch {
            Write-Log -Message "$($component.Name) bootstrap failed: $_" -Level "Warn"
            # Non-fatal: continue with other components
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 9: System Integration
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 9: System Integration"

Write-Progress-Step "Registering ScheduledTask for auto-restart"
if ($DryRun) {
    Write-Log -Message "[DRY RUN] Would register ScheduledTask for corvinos-serve" -Level "Info"
} else {
    try {
        $taskName = "CorvinOS-AutoRestart"
        $taskPath = '\CorvinOS\'

        # Check if task already exists
        $existingTask = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath `
            -ErrorAction SilentlyContinue

        if ($existingTask) {
            Write-Log -Message "ScheduledTask $taskName already exists" -Level "Info"
        } else {
            Write-Log -Message "Creating ScheduledTask $taskName..." -Level "Info"
            $action = New-ScheduledTaskAction -Execute "corvinos" -Argument "serve"
            $trigger = New-ScheduledTaskTrigger -AtStartup
            $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -RunWithoutNetwork -MultipleInstances Parallel

            Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath `
                -Action $action -Trigger $trigger -Settings $settings `
                -RunLevel Highest -Force -ErrorAction Stop | Out-Null

            Write-Log -Message "ScheduledTask registered successfully" -Level "Success"
        }
    } catch {
        Write-Log -Message "Failed to register ScheduledTask: $_" -Level "Warn"
        # Non-fatal: continue
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 10: Final Verification
# ─────────────────────────────────────────────────────────────────────────────

Write-Header "Phase 10: Final Verification"

Write-Progress-Step "Verifying installation artifacts"
$verificationChecks = @(
    @{ Name = "CORVIN_HOME exists"; Check = { Test-Path $CorvinHome } },
    @{ Name = "uv is available"; Check = {
        if (Get-Command uv -ErrorAction SilentlyContinue) { $true }
        else { Test-Path (Join-Path $UvBin "uv.exe") }
    }},
    @{ Name = "Node.js is available"; Check = {
        if (Get-Command node -ErrorAction SilentlyContinue) { $true }
        else { Test-Path (Join-Path $CorvinHome "node\bin\node.exe") }
    }},
    @{ Name = "corvinos CLI is available"; Check = {
        Get-Command corvinos -ErrorAction SilentlyContinue
    }}
)

$allChecksPassed = $true
foreach ($check in $verificationChecks) {
    $result = & $check.Check
    if ($result) {
        Write-Log -Message "✓ $($check.Name)" -Level "Success"
    } else {
        Write-Log -Message "✗ $($check.Name)" -Level "Warn"
        $allChecksPassed = $false
    }
}

if (-not $allChecksPassed) {
    Write-Log -Message "Some verification checks failed (non-critical)" -Level "Warn"
}

Write-Progress-Step "Installation complete"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 11: Start Console Server & Launch Browser
# ─────────────────────────────────────────────────────────────────────────────
# Mirrors install.sh Phase 4: on Unix the installer starts corvinos-serve,
# health-checks it, and opens the console in the default browser. install.ps1
# used to stop short and just print "run corvinos serve yourself" — a real
# Windows/Unix behavior gap, not just a cosmetic one.

Write-Header "Phase 11: Start Console Server"

$ConsoleUrl = "http://127.0.0.1:8765/console/"
$HealthzUrl = "http://127.0.0.1:8765/v1/console/healthz"
$ServerReady = $false

if ($DryRun) {
    Write-Progress-Step "Starting console server"
    Write-Log -Message "[DRY RUN] Would start corvinos-serve and open $ConsoleUrl" -Level "Info"
} else {
    Write-Progress-Step "Starting console server"
    try {
        Invoke-WebRequest -Uri $HealthzUrl -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop | Out-Null
        Write-Log -Message "Console server already running" -Level "Info"
        $ServerReady = $true
    } catch {
        try {
            Start-Process -FilePath "corvinos-serve" -WindowStyle Hidden -ErrorAction Stop
            Write-Log -Message "Launched corvinos-serve" -Level "Info"
        } catch {
            Write-Log -Message "Failed to launch corvinos-serve: $_" -Level "Warn"
        }
    }

    if (-not $ServerReady) {
        Write-Log -Message "Waiting for console server to become healthy..." -Level "Info"
        $BackoffSec = 1
        for ($i = 0; $i -lt 30; $i++) {
            try {
                Invoke-WebRequest -Uri $HealthzUrl -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop | Out-Null
                $ServerReady = $true
                break
            } catch {
                Start-Sleep -Seconds $BackoffSec
                if ($BackoffSec -lt 8) { $BackoffSec *= 2 }
            }
        }
    }

    if ($ServerReady) {
        Write-Log -Message "Console server is healthy" -Level "Success"
        try {
            Start-Process $ConsoleUrl -ErrorAction Stop
            Write-Log -Message "Opened $ConsoleUrl in default browser" -Level "Success"
        } catch {
            Write-Log -Message "Could not auto-open browser: $_ (open $ConsoleUrl manually)" -Level "Warn"
        }
    } else {
        Write-Log -Message "Console server did not become healthy in time — start manually: corvinos serve" -Level "Warn"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Summary & Exit
# ─────────────────────────────────────────────────────────────────────────────

Write-Summary -Status "SUCCESS" -ExitCode 0

Write-Host ""
Write-Host "📍 Installation Summary:" -ForegroundColor Green
Write-Host "   CORVIN_HOME: $CorvinHome" -ForegroundColor White
Write-Host "   Repository: $EditablePath" -ForegroundColor White
Write-Host "   Logs: $LogDir" -ForegroundColor White
Write-Host ""
if ($ServerReady) {
    Write-Host "✅ Console is running: $ConsoleUrl" -ForegroundColor Green
} else {
    Write-Host "🚀 Next Steps:" -ForegroundColor Green
    Write-Host "   1. Test console: corvinos serve" -ForegroundColor White
    Write-Host "   2. Open browser: $ConsoleUrl" -ForegroundColor White
    Write-Host "   3. Ask a question and verify voice summary" -ForegroundColor White
}
Write-Host ""
Write-Host "ℹ️  For troubleshooting:" -ForegroundColor Green
Write-Host "   See logs in: $LogDir" -ForegroundColor White
Write-Host "   Main log: $MainLogFile" -ForegroundColor White
Write-Host "   Error log: $ErrorLogFile" -ForegroundColor White
Write-Host ""

$script:CleanupOnExit = $false
exit 0
