#Requires -Version 5.1
# validate-install.ps1 — CorvinOS Post-Installation Verification
# Run AFTER install.ps1 completes to prove the system is fully operational
# Usage:
#   .\validate-install.ps1
#   .\validate-install.ps1 -Verbose
#   .\validate-install.ps1 -SkipBrowserLaunch

param(
    [switch]$Verbose,
    [switch]$SkipBrowserLaunch,
    [switch]$Quick  # Skip frontend rebuild if it's already built
)

$ErrorActionPreference = "Stop"

function Write-Pass { param($m) Write-Host "  ✓ $m" -ForegroundColor Green }
function Write-Fail { param($m) Write-Host "  ✗ $m" -ForegroundColor Red; exit 1 }
function Write-Warn { param($m) Write-Host "  ⚠ $m" -ForegroundColor Yellow }
function Write-Head { param($m) Write-Host "`n$m" -ForegroundColor Cyan -BackgroundColor Black }

Write-Host ""
Write-Host "CorvinOS Post-Installation Verification" -ForegroundColor White

# ============================================================================
# Phase 1: Python Environment
# ============================================================================
Write-Head "Phase 1: Python Environment"

# Check corvinos-serve exists and runs
try {
    $corvinos = Get-Command corvinos-serve -ErrorAction Stop
    Write-Pass "corvinos-serve found on PATH"
} catch {
    Write-Fail "corvinos-serve not on PATH — run 'uv tool update-shell' and restart terminal"
}

# Test Python import
try {
    $output = python -c "import corvinos; print('OK')" 2>&1
    if ($output -match "OK") {
        Write-Pass "Python environment healthy (corvinos importable)"
    } else {
        Write-Fail "Python import failed: $output"
    }
} catch {
    Write-Fail "Python test failed: $_"
}

# ============================================================================
# Phase 2: Console Frontend
# ============================================================================
Write-Head "Phase 2: Console Frontend"

$webNextDir = "core\console\corvin_console\web-next"
if (-not (Test-Path $webNextDir)) {
    Write-Fail "web-next directory not found at: $webNextDir"
}
Push-Location $webNextDir

# Check if dist exists (already built)
if ((Test-Path "dist") -and -not $Quick) {
    Write-Warn "dist/ exists but running full rebuild (use -Quick to skip)"
} elseif ((Test-Path "dist") -and $Quick) {
    Write-Pass "dist/ exists and -Quick flag set (skipping rebuild)"
} else {
    Write-Pass "dist/ missing — building frontend…"

    try {
        # Build frontend
        $buildOutput = npm run build 2>&1

        # Check for success
        if ($LASTEXITCODE -eq 0 -and $buildOutput -match "built in.*?s") {
            Write-Pass "Frontend build succeeded"
        } else {
            Write-Fail "Frontend build failed`n$buildOutput"
        }
    } catch {
        Write-Fail "Build command crashed: $_"
    }
}

Pop-Location

# ============================================================================
# Phase 3: Console Server
# ============================================================================
Write-Head "Phase 3: Console Server Startup"

# Kill any existing corvinos-serve processes
$existing = Get-Process -Name "python" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "corvinos-serve" }
if ($existing) {
    Write-Warn "Killing existing corvinos-serve processes…"
    $existing | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

# Start server
Write-Host "  Starting corvinos-serve…" -NoNewline
$process = Start-Process -FilePath "corvinos-serve" -PassThru -NoNewWindow
Start-Sleep -Seconds 3

# Verify server is running
$serverReady = $false
$maxRetries = 30
for ($i = 0; $i -lt $maxRetries; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8765/v1/console/healthz" `
            -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($response -and $response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
            Write-Host " OK ($($i+1)s)" -ForegroundColor Green
            Write-Pass "Console server is healthy"
            $serverReady = $true
            break
        }
    } catch { }

    if ($i -eq 0) { Write-Host "" -NoNewline }
    Write-Host "." -NoNewline -ForegroundColor DarkGray
    Start-Sleep -Seconds 1
}

if (-not $serverReady) {
    Write-Fail "Server did not respond after 30s — check: corvinos-serve running?"
}

# ============================================================================
# Phase 4: Console Web UI
# ============================================================================
Write-Head "Phase 4: Console Web UI"

$consoleUrl = "http://localhost:8765/console/"

try {
    $response = Invoke-WebRequest -Uri $consoleUrl -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Pass "Console UI responds (HTTP 200)"
    } else {
        Write-Fail "Console returned HTTP $($response.StatusCode)"
    }
} catch {
    Write-Fail "Console UI unreachable: $_"
}

# ============================================================================
# Phase 5: Browser Launch
# ============================================================================
Write-Head "Phase 5: Browser Launch"

if ($SkipBrowserLaunch) {
    Write-Warn "Skipping browser launch (manual open: $consoleUrl)"
} else {
    try {
        Write-Host "  Opening $consoleUrl…"
        Start-Process $consoleUrl
        Write-Pass "Browser launched"
    } catch {
        Write-Warn "Could not launch browser: $_"
        Write-Host "  Open manually: $consoleUrl" -ForegroundColor Yellow
    }
}

# ============================================================================
# Summary
# ============================================================================
Write-Head "✓ Installation Complete"
Write-Host @"
CorvinOS is ready:
  • Console:   $consoleUrl
  • Server:    http://localhost:8765 (PID: $($process.Id))
  • Python:    $((python --version) -split " " | select -Last 1)
  • Node:      $((node --version).substring(1))

Next steps:
  • Reload console browser tab if it doesn't load
  • Type commands in the console to interact with Claude
  • To stop: Stop-Process $($process.Id)

"@ -ForegroundColor White

Write-Host "Installation verified ✓" -ForegroundColor Green
