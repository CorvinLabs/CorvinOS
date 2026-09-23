#Requires -Version 5.1
<#
.SYNOPSIS
    CorvinOS updater for Windows.

.DESCRIPTION
    Updates CorvinOS to the latest main branch and rebuilds the console UI.
    Stops the running console gracefully, pulls latest changes, rebuilds the SPA,
    and optionally restarts the server.

    ASCII-ONLY BY CONTRACT (see install.ps1 for UTF-8 warning).

.PARAMETER NoRestart
    Rebuild the console but do not restart the server.

.PARAMETER ConsoleOnly
    Rebuild the console UI only (skip 'git pull').

.PARAMETER Force
    Force a full rebuild even if no changes detected.

.PARAMETER Port
    Console port (default 8765). Used for health checks.

.PARAMETER CorvinRepo
    Path to the CorvinOS repository (default: current directory).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File update.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File update.ps1 -ConsoleOnly -Force

.NOTES
    Exit codes:
      0 = Success
      1 = Update failed (check log)
      2 = Prerequisites missing (git, PowerShell 5.1+)
      3 = Console failed to start after update
#>

[CmdletBinding()]
param(
    [switch]$NoRestart,
    [switch]$ConsoleOnly,
    [switch]$Force,

    [ValidateRange(1, 65535)]
    [int]$Port = 8765,

    [string]$CorvinRepo = "."
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Enforce TLS 1.2 for HTTPS
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol
} catch { }

# ─────────────────────────────────────────────────────────────────────────────
# Configuration & Logging
# ─────────────────────────────────────────────────────────────────────────────
$UpdateTimestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogDir = Join-Path $env:TEMP "corvinos-update-$UpdateTimestamp"
$MainLogFile = Join-Path $LogDir "update.log"
$ErrorLogFile = Join-Path $LogDir "update-errors.log"

$null = New-Item -ItemType Directory -Path $LogDir -Force -ErrorAction SilentlyContinue

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet("Info", "Warn", "Error", "Success", "Debug")]
        [string]$Level = "Info"
    )

    $timestamp = Get-Date -Format "HH:mm:ss.fff"
    $logMessage = "[$timestamp] [$Level] $Message"

    $color = @{
        "Info"    = "White"
        "Warn"    = "Yellow"
        "Error"   = "Red"
        "Success" = "Green"
        "Debug"   = "DarkGray"
    }[$Level]

    if ($Level -eq "Debug") {
        Write-Verbose $logMessage
    } else {
        Write-Host $logMessage -ForegroundColor $color
    }

    Add-Content -Path $MainLogFile -Value $logMessage -ErrorAction SilentlyContinue
    if ($Level -eq "Error") {
        Add-Content -Path $ErrorLogFile -Value $logMessage -ErrorAction SilentlyContinue
    }
}

function Test-Command {
    param([string]$Command)
    $null = Get-Command $Command -ErrorAction SilentlyContinue
    return $?
}

function Stop-Console {
    Write-Log -Message "Stopping console server..." -Level "Info"

    $processes = @()
    $processes += Get-Process -Name "corvinos-serve" -ErrorAction SilentlyContinue
    $processes += Get-Process -Name "python" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*corvinos-serve*" -or $_.CommandLine -like "*corvin_gateway*" }

    if ($processes.Count -gt 0) {
        Write-Log -Message "Found $($processes.Count) running process(es). Stopping gracefully..." -Level "Warn"

        foreach ($proc in $processes) {
            try {
                Write-Log -Message "Stopping PID $($proc.Id)..." -Level "Info"
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 500
                Write-Log -Message "Process $($proc.Id) stopped." -Level "Success"
            } catch {
                Write-Log -Message "Warning stopping process $($proc.Id): $_" -Level "Warn"
            }
        }
    } else {
        Write-Log -Message "No running console found." -Level "Success"
    }

    Start-Sleep -Seconds 1  # Ensure ports are released
}

function Test-TcpPort {
    param(
        [string]$TargetHost = "127.0.0.1",
        [int]$TargetPort,
        [int]$TimeoutMs = 1000
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($TargetHost, $TargetPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "$([char]27)[1mCorvinOS updater$([char]27)[0m" -ForegroundColor White
Write-Host ""

# Pre-flight checks
if (-not (Test-Path $CorvinRepo)) {
    Write-Log -Message "Repository path does not exist: $CorvinRepo" -Level "Error"
    exit 1
}

$gitRoot = Join-Path $CorvinRepo ".git"
if (-not (Test-Path $gitRoot) -and -not $ConsoleOnly) {
    Write-Log -Message "$CorvinRepo is not a git repository. Use -ConsoleOnly to rebuild just the console UI." -Level "Error"
    exit 1
}

$consoleDeployScript = Join-Path $CorvinRepo "scripts" "console-deploy.ps1"
if (-not (Test-Path $consoleDeployScript)) {
    $consoleDeployScript = Join-Path $CorvinRepo "scripts" "console-deploy.sh"
    if (-not (Test-Path $consoleDeployScript)) {
        Write-Log -Message "console-deploy.ps1 or console-deploy.sh not found in $CorvinRepo\scripts" -Level "Error"
        exit 1
    }
}

Write-Log -Message "Update started (Log: $MainLogFile)" -Level "Info"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Git update (unless -ConsoleOnly)
# ─────────────────────────────────────────────────────────────────────────────
if (-not $ConsoleOnly) {
    if (-not (Test-Command "git")) {
        Write-Log -Message "Git not found on PATH. Install Git and retry." -Level "Error"
        exit 2
    }

    Write-Log -Message "Fetching latest changes from origin/main..." -Level "Info"

    Push-Location $CorvinRepo -ErrorAction Stop

    try {
        & git fetch origin main 2>&1 | ForEach-Object { Write-Log -Message $_ -Level "Debug" }
        if ($LASTEXITCODE -ne 0) {
            Write-Log -Message "Git fetch failed. Check network and retry." -Level "Error"
            exit 1
        }

        $currentHash = & git rev-parse HEAD 2>&1 | Select-Object -First 1
        $latestHash = & git rev-parse origin/main 2>&1 | Select-Object -First 1

        if ($currentHash -eq $latestHash) {
            Write-Log -Message "Already up-to-date (commit $($currentHash.Substring(0,8)))" -Level "Success"
        } else {
            Write-Log -Message "New commits available. Pulling..." -Level "Warn"
            & git pull origin main --ff-only 2>&1 | ForEach-Object { Write-Log -Message $_ -Level "Debug" }
            if ($LASTEXITCODE -ne 0) {
                Write-Log -Message "Git pull failed. Check for local changes." -Level "Error"
                exit 1
            }
            Write-Log -Message "Successfully pulled latest main" -Level "Success"
        }
    } finally {
        Pop-Location
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Stop console server
# ─────────────────────────────────────────────────────────────────────────────
Write-Log -Message "Stopping console server..." -Level "Info"
Stop-Console

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Rebuild console UI
# ─────────────────────────────────────────────────────────────────────────────
Write-Log -Message "Rebuilding console UI..." -Level "Info"

$buildArgs = @()
if ($Force) {
    $buildArgs += "--force"
}

try {
    if ($consoleDeployScript -like "*.ps1") {
        # PowerShell script
        & $consoleDeployScript @buildArgs 2>&1 | ForEach-Object { Write-Log -Message $_ -Level "Debug" }
    } else {
        # Bash script (needs bash or git bash)
        if (Test-Command "bash") {
            & bash $consoleDeployScript $buildArgs 2>&1 | ForEach-Object { Write-Log -Message $_ -Level "Debug" }
        } else {
            Write-Log -Message "bash not found. Trying Git Bash..." -Level "Warn"
            $gitBash = "C:\Program Files\Git\bin\bash.exe"
            if (Test-Path $gitBash) {
                & $gitBash $consoleDeployScript $buildArgs 2>&1 | ForEach-Object { Write-Log -Message $_ -Level "Debug" }
            } else {
                Write-Log -Message "Git Bash not found. Cannot run console build." -Level "Error"
                exit 1
            }
        }
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Log -Message "Console build failed." -Level "Error"
        exit 1
    }

    Write-Log -Message "Console build successful." -Level "Success"
} catch {
    Write-Log -Message "Exception during console build: $_" -Level "Error"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Restart console (unless -NoRestart)
# ─────────────────────────────────────────────────────────────────────────────
if (-not $NoRestart) {
    Write-Log -Message "Starting CorvinOS console..." -Level "Info"

    if (-not (Test-Command "corvinos-serve")) {
        Write-Log -Message "corvinos-serve not found on PATH. Run: uv tool upgrade corvinos" -Level "Error"
        exit 2
    }

    try {
        Start-Process "corvinos-serve" -WindowStyle Hidden -ErrorAction Stop
        Write-Log -Message "Console started in background" -Level "Success"
    } catch {
        Write-Log -Message "Failed to start console: $_" -Level "Error"
        exit 3
    }

    # ─────────────────────────────────────────────────────────────────────
    # Phase 4a: Health check with exponential backoff (ADR-0867)
    # ─────────────────────────────────────────────────────────────────────
    Write-Log -Message "Waiting for console to respond..." -Level "Info"

    $maxRetries = 60
    $retryCount = 0
    $backoff = 1
    $serverReady = $false

    while ($retryCount -lt $maxRetries) {
        if (Test-TcpPort -TargetHost "127.0.0.1" -TargetPort $Port -TimeoutMs 2000) {
            Write-Log -Message "Console is ready after $retryCount seconds" -Level "Success"
            $serverReady = $true
            break
        }
        $retryCount++
        Write-Host "." -NoNewline
        Start-Sleep -Seconds $backoff
        if ($backoff -lt 8) {
            $backoff = $backoff * 2
        }
    }

    Write-Host ""
    if (-not $serverReady) {
        Write-Log -Message "Console did not respond after ${maxRetries}s. It may still be starting..." -Level "Warn"
        Write-Host "  Open the console: http://127.0.0.1:$Port/console/" -ForegroundColor Yellow
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "$(([char]27)[1]$(([char]27)[0])" -NoNewline
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor White
Write-Host "  CorvinOS update complete!" -ForegroundColor Green
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor White
Write-Host ""

if ($NoRestart) {
    Write-Host "  Console build complete but not restarted."
    Write-Host "  Start it with: corvinos-serve" -ForegroundColor Cyan
} else {
    Write-Host "  Console is running at:" -ForegroundColor White
    Write-Host "  http://127.0.0.1:$Port/console/" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "  Logs: $MainLogFile" -ForegroundColor DarkGray
Write-Host ""

exit 0
