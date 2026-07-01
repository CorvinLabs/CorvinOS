#Requires -Version 5.1
param(
    [Alias("e")]
    [string]$Editable = ""
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
$Package = if ($env:CORVIN_PKG) { $env:CORVIN_PKG } else { "corvinos" }

function Write-Step { param($m) Write-Host "  $m" }
function Write-Ok   { param($m) Write-Host "  $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "  $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "`n  Error: $m" -ForegroundColor Red; exit 1 }
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
    irm https://astral.sh/uv/install.ps1 | iex
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
    Write-Step "Installing $Package (first run can take a minute) ..."
    uv tool install --force --upgrade $Package
}
if ($LASTEXITCODE -ne 0) { Write-Fail "install failed — see the error above" }
uv tool update-shell 2>$null | Out-Null   # persist the tool bin on the user PATH

if (-not (Get-Command corvinos-serve -ErrorAction SilentlyContinue)) {
    # PATH was updated persistently but may not be live in this session yet.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host ""
Write-Ok "Package installed."

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
