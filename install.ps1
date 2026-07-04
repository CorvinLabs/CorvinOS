#Requires -Version 5.1
param(
    [Alias("e")]
    [string]$Editable = "",
    [switch]$NoHermes
)
# install.ps1 — CorvinOS installer for Windows (PowerShell 5.1+).
# Usage:
#   irm https://corvin-labs.com/install.ps1 | iex
#   .\install.ps1 -Editable C:\path\to\CorvinOS   # dev install from a local clone
#
# ZERO prerequisites: it bootstraps `uv` (a single binary that also manages its
# own Python), so you need NO Python, NO pip, NO package manager pre-installed.
# `irm | iex` uses no shell operators, so it works in PowerShell 5.1 AND 7 alike.

$ErrorActionPreference = "Stop"

# Keep the window open on success AND on error.
# cmd /c pause is used instead of Read-Host because Read-Host can silently
# return in non-interactive PS contexts (e.g. -Command from Run dialog).
function Pause-AndExit {
    param([int]$Code = 0)
    Write-Host ""
    if ($Code -ne 0) {
        Write-Host "  Installation failed. See the error above." -ForegroundColor Red
    }
    try { cmd /c pause } catch { Start-Sleep 10 }
    exit $Code
}

# Catch any unhandled exception so the window never closes silently.
trap {
    Write-Host "`n  Unexpected error: $_" -ForegroundColor Red
    Pause-AndExit 1
}
$Package = if ($env:CORVIN_PKG) { $env:CORVIN_PKG } else { "corvinos" }

function Write-Step { param($m) Write-Host "  $m" }
function Write-Ok   { param($m) Write-Host "  $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "  $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "`n  Error: $m" -ForegroundColor Red; Pause-AndExit 1 }
function Write-Head { param($m) Write-Host $m -ForegroundColor Cyan }
function Write-Cmd  { param($m) Write-Host "    $m" -ForegroundColor White }
function Write-Hint { param($m) Write-Host "    $m" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "CorvinOS installer — self-hosted, local-first AI voice agent" -ForegroundColor White

# ── editable path validation ──────────────────────────────────────────────────
$EditablePath = ""
if ($Editable -ne "") {
    if (-not (Test-Path $Editable -PathType Container)) {
        Write-Fail "Editable path does not exist: $Editable"
    }
    $EditablePath = (Resolve-Path $Editable).Path
}

# ── 1. ensure uv (brings its own Python → zero prerequisites) ─────────────────
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Step "Bootstrapping the uv runtime (brings its own Python) ..."
    # Run the uv installer in a child powershell.exe process.
    # Any `exit` call inside the uv installer terminates the CHILD process,
    # not our session.  [scriptblock]::Create and iex both propagate `exit`
    # up to the parent session in PS 5.1 — only a real child process is safe.
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    # uv installs to %USERPROFILE%\.local\bin — make it usable in THIS session.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Fail "uv is not on PATH after install. Open a new terminal and re-run."
}
Write-Ok ("uv " + (((uv --version) 2>$null) -split " ")[1] + " — OK")

# ── 2. install CorvinOS as an isolated tool (uv fetches Python if needed) ─────
if ($EditablePath -ne "") {
    Write-Step "Installing CorvinOS (editable) from $EditablePath ..."
    uv tool install --force --editable $EditablePath
} else {
    # Fetch the latest published version from PyPI explicitly so the installer
    # always picks up a fresh release — uv's local resolver cache can lag
    # behind a newly pushed package by several minutes.
    $PinnedVersion = ""
    try {
        $pypiInfo = Invoke-RestMethod -Uri "https://pypi.org/pypi/$Package/json" -TimeoutSec 10
        $PinnedVersion = $pypiInfo.info.version
    } catch {
        Write-Warn "Could not reach PyPI — will let uv resolve the latest version."
    }

    if ($PinnedVersion -ne "") {
        Write-Step "Installing $Package==$PinnedVersion ..."
        uv tool install --force "$Package==$PinnedVersion"
        if ($LASTEXITCODE -ne 0) {
            # PyPI JSON API reported the version but the simple index hasn't
            # propagated yet (CDN lag, typically < 60 s). Fall back to letting
            # uv resolve whatever is currently available on the index.
            Write-Warn "$Package==$PinnedVersion not yet on index — installing latest available instead ..."
            uv tool install --force --upgrade --refresh-package $Package $Package
        }
    } else {
        Write-Step "Installing $Package (latest available) ..."
        uv tool install --force --upgrade --refresh-package $Package $Package
    }
}
if ($LASTEXITCODE -ne 0) { Write-Fail "install failed — see the error above" }
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
uv tool update-shell 2>$null | Out-Null   # persist the tool bin on the user PATH
$ErrorActionPreference = $prevErrorAction

if (-not (Get-Command corvinos-serve -ErrorAction SilentlyContinue)) {
    # PATH was updated persistently but may not be live in this session yet.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host ""
Write-Ok "Package installed."

# ── 2b. Hermes (local offline engine): Ollama + model, working out of the box ──
$SkipHermes = $NoHermes -or ($env:CORVIN_SKIP_HERMES -eq "1")
if (-not $SkipHermes) {
    Write-Host ""
    Write-Step "Setting up Hermes (local offline engine) ..."
    # pick a model by RAM
    $ramMB = 8000
    try { $ramMB = [int]((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1MB) } catch {}
    $HModel = if ($ramMB -lt 6000) { "qwen3:1.7b" } else { "qwen3:8b" }
    Write-Step "RAM ~$ramMB MB -> model $HModel"

    # ensure Ollama is installed (winget)
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Step "Installing Ollama ..."
            winget install --silent --accept-package-agreements --accept-source-agreements Ollama.Ollama
            $env:Path = "$env:LOCALAPPDATA\Programs\Ollama;$env:Path"
        } else {
            Write-Warn "winget not found — install Ollama from https://ollama.com/download/windows"
        }
    }

    # ensure the Ollama server is reachable (start it if needed)
    function Test-Ollama { try { Invoke-RestMethod -TimeoutSec 2 http://localhost:11434/api/tags | Out-Null; $true } catch { $false } }
    if (-not (Test-Ollama)) {
        if (Get-Command ollama -ErrorAction SilentlyContinue) {
            Start-Process -WindowStyle Hidden ollama -ArgumentList "serve" -ErrorAction SilentlyContinue
        }
        for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep 1 }
    }

    # pull the model so Hermes is immediately usable offline
    if ((Get-Command ollama -ErrorAction SilentlyContinue) -and (Test-Ollama)) {
        $have = $false
        try { $have = ((Invoke-RestMethod http://localhost:11434/api/tags).models.name -join ",") -match [regex]::Escape($HModel) } catch {}
        if ($have) {
            Write-Ok "Hermes model $HModel already present"
        } else {
            Write-Step "Pulling $HModel (one-time, a few GB) ..."
            ollama pull $HModel
            if ($LASTEXITCODE -eq 0) { Write-Ok "Hermes ready — $HModel installed" }
            else { Write-Warn "model pull failed — finish later with: ollama pull $HModel" }
        }
    } else {
        Write-Warn "Ollama not reachable — Hermes self-heals on first run (or see https://ollama.com/download)"
    }
}

# ── 3. setup wizard ───────────────────────────────────────────────────────────
if (Get-Command corvin-install -ErrorAction SilentlyContinue) {
    Write-Host ""
    Write-Step "Launching setup wizard ..."
    Write-Host ""
    corvin-install
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Setup wizard exited early. Re-run later with: corvin-install"
    }
}

# ── 4. start server + wait for readiness + auto-launch console ──────────────────
Write-Host ""
Write-Step "Starting CorvinOS console server ..."

$ConsoleURL = "http://localhost:8765/console/"
$MaxRetries = 30
$RetryCount = 0

# Start server as a detached process so it keeps running after this installer
# window is closed.  Start-Job would be killed when the PS session ends.
$serverProc = $null
try {
    $serverProc = Start-Process -FilePath "corvinos-serve" -PassThru -WindowStyle Minimized -ErrorAction Stop
} catch {
    Write-Warn "Could not start server automatically. Run manually: corvinos-serve"
}

# Wait for server to be ready
while ($RetryCount -lt $MaxRetries) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8765/api/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Ok "Server is ready!"
            break
        }
    } catch {
        # Server not ready yet
    }
    $RetryCount++
    Start-Sleep -Seconds 1
}

if ($RetryCount -ge $MaxRetries) {
    Write-Warn "Server startup timeout. You can open manually: $ConsoleURL"
} else {
    Write-Ok "Server is ready — browser opened automatically."
}

# ── done / cheat sheet ────────────────────────────────────────────────────────
Write-Host ""
Write-Head "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host " CorvinOS is ready!" -ForegroundColor Green
Write-Head "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""
Write-Host " Open a NEW terminal (so PATH is updated), then start the console:" -ForegroundColor White
Write-Host ""
Write-Cmd  "corvinos-serve"
Write-Hint "# then open  http://localhost:8765/console/"
Write-Host ""
Write-Head "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host " Commands" -ForegroundColor White
Write-Head "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""
Write-Host "   corvinos-serve     " -NoNewline -ForegroundColor White; Write-Host "Start the web console"
Write-Host "   corvin-install     " -NoNewline -ForegroundColor White; Write-Host "Setup wizard (bridges, tokens, voice)"
Write-Host "   corvin-uninstall   " -NoNewline -ForegroundColor White; Write-Host "Remove CorvinOS"
Write-Host "   corvin-a2a         " -NoNewline -ForegroundColor White; Write-Host "Agent-to-agent pairing and messaging"
Write-Host ""
Write-Cmd  "ollama pull qwen3:8b   # optional local model (offline /engine hermes)"
Write-Host ""
Pause-AndExit 0
