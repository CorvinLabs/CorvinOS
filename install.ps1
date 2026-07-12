#Requires -Version 5.1
param(
    [Alias("e")]
    [string]$Editable = "",
    [switch]$NoHermes
)
# install.ps1 -- CorvinOS installer for Windows (PowerShell 5.1+).
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
Write-Host "CorvinOS installer -- self-hosted, local-first AI voice agent" -ForegroundColor White

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
    # up to the parent session in PS 5.1 -- only a real child process is safe.
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    # uv installs to %USERPROFILE%\.local\bin -- make it usable in THIS session.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Fail "uv is not on PATH after install. Open a new terminal and re-run."
}
# PS 5.1: stderr redirection of a native command under EAP=Stop turns any
# stray uv stderr line into a terminating error that kills the whole install
# at its very first step -- wrap in EAP=Continue (same guard as the
# `uv tool update-shell` call below).
$prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$uvVersion = try { (((uv --version) 2>$null) -split " ")[1] } catch { "?" }
$ErrorActionPreference = $prevEAP
Write-Ok ("uv " + $uvVersion + " -- OK")

# ── 2. install CorvinOS as an isolated tool (uv fetches Python if needed) ─────
# INST-2: on a re-run/update, a previously-installed CorvinOS-Console task is
# still running corvinos-serve out of the uv-tool venv -- holding locks on the
# very files `uv tool install --force` must replace, which makes the install
# fail on Windows. Stop the task and kill any lingering serve/venv python FIRST
# so the install hits no locked files.
try {
    Stop-ScheduledTask -TaskName "CorvinOS-Console" -ErrorAction SilentlyContinue
} catch {}
try {
    # Disable (not just stop) the task while installing: the registration
    # carries restart-on-failure, so a merely-stopped instance can relaunch
    # mid-install and re-lock the venv (INST-2 class). Step 3b re-registers.
    Disable-ScheduledTask -TaskName "CorvinOS-Console" -ErrorAction SilentlyContinue | Out-Null
} catch {}
try {
    # Also match corvin_gateway/uvicorn: the wizard and the always-on (Stufe-2)
    # service run `python -m uvicorn corvin_gateway.app:app`, which the old
    # pattern missed -- leaving the venv locked and the install failing.
    # Guard: only kill python-ish processes so an editor/terminal that merely
    # has a corvin path in its argv is never collateral.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match "corvinos-serve|corvin-serve|corvin_console|corvin_gateway" -and
            $_.Name -match "^python|^corvin"
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch {}

if ($EditablePath -ne "") {
    Write-Step "Installing CorvinOS (editable) from $EditablePath ..."
    uv tool install --force --editable $EditablePath
} else {
    # INST-1: install UNPINNED. `uv tool install corvinos==<ver>` writes that
    # exact pin into the uv receipt, after which `uv tool upgrade corvinos`
    # (the supervisor's per-logon auto-update below, and serve_backend.py)
    # honours the pin forever and exits 0 "Nothing to upgrade" -- permanently
    # freezing auto-update, and on Windows feeding the exit-before-uvicorn
    # relaunch loop. The PyPI JSON query is now used ONLY for a log line.
    $LatestVersion = ""
    try {
        $pypiInfo = Invoke-RestMethod -Uri "https://pypi.org/pypi/$Package/json" -TimeoutSec 10
        $LatestVersion = $pypiInfo.info.version
    } catch {
        Write-Warn "Could not reach PyPI -- installing whatever uv resolves as latest."
    }

    if ($LatestVersion -ne "") {
        Write-Step "Installing $Package (latest on PyPI: $LatestVersion) ..."
    } else {
        Write-Step "Installing $Package (latest available) ..."
    }
    # --refresh bypasses uv's local index cache (which can lag a fresh release)
    # WITHOUT pinning the version into the receipt, so upgrades keep working.
    uv tool install --force --refresh $Package
}
if ($LASTEXITCODE -ne 0) {
    # Re-enable the autostart task we disabled above before bailing out —
    # otherwise a failed (e.g. offline) re-install leaves a previously
    # working autostart permanently disabled, worse than before the install.
    try { Enable-ScheduledTask -TaskName "CorvinOS-Console" -ErrorAction SilentlyContinue | Out-Null } catch {}
    Write-Fail "install failed -- see the error above"
}
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
    # Three-tier ladder so the pulled model actually RUNS alongside Windows +
    # console. qwen3:8b (~5.2 GB) OOMs/swaps on a 6-8 GB box, so it is reserved
    # for >=12 GB; 6-12 GB gets qwen3:4b (~2.6 GB); < 6 GB gets the 1.7b. The
    # running Hermes engine auto-selects whatever tag is present, so a later
    # manual pull upgrades it.
    $HModel = if ($ramMB -lt 6000) { "qwen3:1.7b" } elseif ($ramMB -lt 12000) { "qwen3:4b" } else { "qwen3:8b" }
    Write-Step "RAM ~$ramMB MB -> model $HModel"

    # ensure Ollama is installed (winget)
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            # --silent suppresses winget's own GUI/progress window, so without
            # this message the download (~100+ MB) gave no indication it was
            # still working -- looked like a hung installer on a slower
            # connection. Kept synchronous (no background job) here, unlike
            # install.sh's dot-heartbeat -- winget's interaction with a
            # background PowerShell job is untested and a broken install step
            # would be worse than a plain "please wait".
            Write-Step "Downloading Ollama (~100 MB, one-time) -- this can take a minute, please wait ..."
            winget install --silent --accept-package-agreements --accept-source-agreements Ollama.Ollama
            if ($LASTEXITCODE -ne 0) { Write-Warn "Ollama install failed -- install manually: https://ollama.com/download/windows" }
            $env:Path = "$env:LOCALAPPDATA\Programs\Ollama;$env:Path"
        } else {
            Write-Warn "winget not found -- install Ollama from https://ollama.com/download/windows"
        }
    }

    # ensure the Ollama server is reachable (start it if needed)
    function Test-Ollama { try { Invoke-RestMethod -TimeoutSec 2 http://localhost:11434/api/tags | Out-Null; $true } catch { $false } }
    if (-not (Test-Ollama)) {
        Write-Host -NoNewline "  Starting Ollama service "
        if (Get-Command ollama -ErrorAction SilentlyContinue) {
            Start-Process -WindowStyle Hidden ollama -ArgumentList "serve" -ErrorAction SilentlyContinue
        }
        for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Write-Host -NoNewline "."; Start-Sleep 1 }
        if (Test-Ollama) { Write-Host " ready" } else { Write-Host " not ready yet" }
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
            if ($LASTEXITCODE -eq 0) { Write-Ok "Hermes ready -- $HModel installed" }
            else { Write-Warn "model pull failed -- finish later with: ollama pull $HModel" }
        }
        # Pre-warm the L44 safety classifier (it uses the SAME model) so the very
        # first message isn't a ~22 s cold model load and hits a real semantic
        # check instead of the deterministic Tier-0 floor. keep_alive 30m keeps it
        # resident. (We deliberately don't pin a tiny model: qwen3:1.7b is fast but
        # fails the classifier JSON schema, so it'd be worse than the warm model.)
        $have2 = $false
        try { $have2 = ((Invoke-RestMethod http://localhost:11434/api/tags).models.name -join ",") -match [regex]::Escape($HModel) } catch {}
        if ($have2) {
            Write-Host -NoNewline "  Warming up the safety classifier ($HModel) "
            $body = @{ model = $HModel; prompt = "ok"; stream = $false; keep_alive = "30m" } | ConvertTo-Json -Compress
            $job = Start-Job -ScriptBlock {
                param($b)
                try { Invoke-RestMethod -Method Post -TimeoutSec 180 -Uri "http://localhost:11434/api/generate" -Body $b -ContentType "application/json" | Out-Null } catch {}
            } -ArgumentList $body
            while ($job.State -eq 'Running') { Write-Host -NoNewline "."; Start-Sleep -Seconds 1 }
            Receive-Job $job | Out-Null; Remove-Job $job -Force
            Write-Host " done"
        }
    } else {
        Write-Warn "Ollama not reachable -- Hermes self-heals on first run (or see https://ollama.com/download)"
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

# ── 3b. autostart: survive terminal close, logoff, and reboot ─────────────────
# Windows has no equivalent of systemd's Restart=always (what keeps the
# Linux/macOS install always-on) -- a bare `Start-Process corvinos-serve` only
# lives as long as this installer window's process tree does, and once the
# machine reboots or the user logs off, the console (and with it the
# ADR-0180 presence heartbeat) just stays down until someone notices and
# manually restarts it. A per-user Scheduled Task (RunLevel Limited, no admin
# NEEDED in principle) that supervises the process forever -- restart on ANY
# exit, 5 s cooldown -- is the closest practical match, and this makes it the
# DEFAULT so it works out of the box instead of being an opt-in step the user
# has to discover later.
#
# WA-9: "no admin needed in principle" isn't "always allowed in practice" --
# some standard (non-admin) accounts get "Access is denied" from
# Register-ScheduledTask itself (managed/family/education Windows images,
# some OEM images restrict the Task Scheduler store via policy). Those
# accounts still have full write access to their OWN per-user Startup folder
# with zero elevation, ever, so that's the fallback below.
#
# Self-contained on purpose: this installer runs via `irm | iex` before any
# repo checkout necessarily exists on disk, so the supervisor script is
# generated here rather than referencing operator/bridges/shared/ (which the
# dev-checkout equivalent, bridge.ps1 install-autostart, does instead).

function New-CorvinShortcut {
    # Standard WScript.Shell COM pattern -- creates a .lnk shortcut. Needs no
    # elevation: any account can always write its own Desktop/Startup folder.
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$TargetPath,
        [string]$Arguments = "",
        [string]$Description = "CorvinOS",
        [switch]$Hidden
    )
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($Path)
    $Shortcut.TargetPath = $TargetPath
    if ($Arguments) { $Shortcut.Arguments = $Arguments }
    $Shortcut.Description = $Description
    if ($Hidden) { $Shortcut.WindowStyle = 7 }  # 7 = minimized; .lnk has no true "hidden"
    $Shortcut.Save()
}

function Install-CorvinAutostart {
    $CorvinHome = if ($env:CORVIN_HOME) { $env:CORVIN_HOME } else { Join-Path $env:USERPROFILE ".corvin" }
    # CORVIN_HOME is a documented user-overridable env var, not a validated
    # path -- its value is interpolated into the generated supervisor script
    # below as literal text. Escape backtick/`$`/`"` (in that order) before
    # any such interpolation so a crafted CORVIN_HOME value can't break out
    # of the double-quoted string it lands in (same injection class already
    # fixed in serve_backend.py::_ps_quote this session -- adversarial review
    # finding).
    $CorvinHomeEscaped = $CorvinHome.Replace('`', '``').Replace('$', '`$').Replace('"', '`"')
    $BinDir = Join-Path $CorvinHome "bin"
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $Supervisor = Join-Path $BinDir "corvin-supervisor.ps1"

    $ServeCmd = (Get-Command corvinos-serve -ErrorAction SilentlyContinue).Source
    if (-not $ServeCmd) { $ServeCmd = (Get-Command corvin-serve -ErrorAction SilentlyContinue).Source }
    if (-not $ServeCmd) { throw "corvinos-serve not found on PATH" }

    # INST-11: $ServeCmd/$Supervisor are filesystem paths interpolated as
    # literal text into the generated supervisor's double-quoted strings -- a
    # path containing a `$`, backtick or `"` would break out of them (same
    # injection class already handled for $CorvinHomeEscaped). Escape
    # backtick/`$`/`"` in that order before any such interpolation.
    $ServeCmdEscaped   = $ServeCmd.Replace('`', '``').Replace('$', '`$').Replace('"', '`"')
    $SupervisorEscaped = $Supervisor.Replace('`', '``').Replace('$', '`$').Replace('"', '`"')

    @"
# Auto-generated by install.ps1 -- restart-forever supervisor for corvinos-serve.
# Not meant to be run by hand. Re-run install.ps1 (or bridge.ps1 install-autostart
# from a repo checkout) to regenerate. Logs: `$CorvinHome\logs\console-supervisor.log
`$ErrorActionPreference = "Continue"
`$LogDir = Join-Path "$CorvinHomeEscaped" "logs"
New-Item -ItemType Directory -Force -Path `$LogDir | Out-Null
`$LogFile = Join-Path `$LogDir "console-supervisor.log"
function Write-Log(`$m) {
    `$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
    try { Add-Content -Path `$LogFile -Value "`$ts [console] `$m" -ErrorAction SilentlyContinue } catch {}
}
Write-Log "supervisor starting: $ServeCmdEscaped --no-browser"

# INST-2 / WA-2 / WA-3: mark every serve process THIS supervisor launches as
# supervised. serve_backend.py sees CORVIN_SUPERVISED=1 and skips its own
# in-process self-update handoff, so it never fights the one-time
# `uv tool upgrade` this supervisor already ran above (which would otherwise
# burn the 5-per-300s restart budget on a handoff the locked venv can't finish
# in 5s). Set on the supervisor process → inherited by every child.
`$env:CORVIN_SUPERVISED = "1"

# ── One-time auto-update per logon/boot ─────────────────────────────────────
# The Windows install is `uv tool install`d, so upgrade with `uv tool upgrade`
# (that venv has no pip). Runs ONCE here -- before the restart loop -- so a crash
# loop never hammers PyPI. Honours the console's auto_update toggle and never
# blocks startup: any failure/timeout/offline just logs and continues.
function Get-CorvinAutoUpdate {
    `$cfg = Join-Path `$env:USERPROFILE ".config\corvin-launcher\config.json"
    try {
        if (Test-Path `$cfg) {
            `$j = Get-Content -Raw -Path `$cfg | ConvertFrom-Json
            if (`$null -ne `$j.auto_update) { return [bool]`$j.auto_update }
        }
    } catch {}
    return `$true
}
if (Get-CorvinAutoUpdate) {
    `$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not `$uv) {
        `$cand = Join-Path `$env:USERPROFILE ".local\bin\uv.exe"
        if (Test-Path `$cand) { `$uv = `$cand }
    }
    if (`$uv) {
        Write-Log "auto-update: uv tool upgrade corvinos"
        try {
            `$job = Start-Job -ScriptBlock { param(`$u) & `$u tool upgrade corvinos 2>&1 } -ArgumentList `$uv
            if (Wait-Job `$job -Timeout 120) {
                Write-Log ("auto-update result: " + ((Receive-Job `$job) -join ' '))
            } else {
                Write-Log "auto-update timed out (120s) -- continuing"
                Stop-Job `$job -ErrorAction SilentlyContinue
            }
            Remove-Job `$job -Force -ErrorAction SilentlyContinue
        } catch { Write-Log "auto-update failed: `$_ -- continuing" }
    } else {
        Write-Log "auto-update skipped: uv not found on PATH"
    }
}

# Rolling window of recent restart timestamps -- bounded crash-loop guard
# (ADR-0184 Stufe-1): 5 restarts per 5-minute window, then stop instead of
# spinning forever. Mirrors the systemd StartLimitBurst=5/
# StartLimitIntervalSec=300 pair used for the Linux user unit
# (corvinOS/installer/service_manager.py) and the dev-checkout supervisor
# (operator/bridges/shared/corvin-supervisor.ps1) -- keep this logic
# IDENTICAL across all three; test_windows_supervisor_parity.py checks it.
`$MaxRestarts = 5
`$RestartWindowSec = 300
`$RestartTimestamps = @()

while (`$true) {
    # Port-collision standby (adversarial finding, 2026-07-12): the install
    # wizard leaves a transient gateway process serving the port until the
    # installer window closes. Launching corvinos-serve over it makes every
    # attempt exit immediately, burns the 5-restart budget in seconds, and
    # the supervisor then stops -- leaving NO console once the wizard process
    # dies. If anything already answers HTTP on the port, stand by and
    # re-check instead of launching a doomed process (mirrors install.sh's
    # pre-start healthz guard). Standby cycles do not consume restart budget.
    `$portBusy = `$false
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8765/healthz" -TimeoutSec 3 -ErrorAction Stop | Out-Null
        `$portBusy = `$true
    } catch {
        if (`$_.Exception.Response) { `$portBusy = `$true }
    }
    if (`$portBusy) {
        Write-Log "port 8765 already serving (install wizard or another instance) -- standing by, re-check in 30s"
        Start-Sleep -Seconds 30
        continue
    }
    `$Now = Get-Date
    `$RestartTimestamps = @(`$RestartTimestamps | Where-Object { (`$Now - `$_).TotalSeconds -le `$RestartWindowSec })
    if (`$RestartTimestamps.Count -ge `$MaxRestarts) {
        Write-Log "CRITICAL: `$MaxRestarts restarts within `${RestartWindowSec}s -- stopping supervisor to avoid a crash loop. Check the log above, fix the underlying issue, then restart with: Start-ScheduledTask CorvinOS-Console"
        break
    }
    `$RestartTimestamps += `$Now
    try {
        Write-Log "launching corvinos-serve"
        `$proc = Start-Process -FilePath "$ServeCmdEscaped" -ArgumentList "--no-browser" -NoNewWindow -PassThru -Wait
        Write-Log "corvinos-serve exited with code `$(`$proc.ExitCode) -- restarting in 5s"
    } catch {
        Write-Log "supervisor error: `$_ -- retrying in 5s"
    }
    Start-Sleep -Seconds 5
}
"@ | Set-Content -Path $Supervisor -Encoding UTF8

    $SupervisorArgs = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$SupervisorEscaped`""
    $Action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $SupervisorArgs
    $Trigger  = New-ScheduledTaskTrigger -AtLogOn
    # -Hidden: belt-and-suspenders on top of the Action's own -WindowStyle
    # Hidden -- marks the TASK ITSELF as hidden in Task Scheduler's UI/API, so
    # nothing about this background process is surfaced for a user to
    # discover and terminate by hand.
    $Settings = New-ScheduledTaskSettingsSet `
        -Hidden `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew

    # Idempotent -- a re-run of install.ps1 (e.g. an update) replaces the
    # existing registration instead of erroring on it.
    Unregister-ScheduledTask -TaskName "CorvinOS-Console" -Confirm:$false -ErrorAction SilentlyContinue

    # WA-9: fall back to a Startup-folder shortcut when the Task Scheduler
    # store denies this account write access. The shortcut loses the OS-level
    # "restart the task if powershell.exe itself dies" safety net, but the
    # supervisor's own restart-forever loop (above) already covers the actual
    # common case (corvinos-serve crashing) -- and it needs zero privilege,
    # ever, on any Windows account.
    try {
        Register-ScheduledTask -TaskName "CorvinOS-Console" -Action $Action -Trigger $Trigger `
            -Settings $Settings -RunLevel Limited `
            -Description "CorvinOS console -- auto-restarts on crash/reboot (ADR-0180 presence heartbeat)" `
            -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskName "CorvinOS-Console"
        return "task"
    } catch {
        Write-Warn "Scheduled Task registration denied ($_) -- falling back to a Startup-folder shortcut (no admin rights needed)."
        $StartupDir = [Environment]::GetFolderPath("Startup")
        New-CorvinShortcut -Path (Join-Path $StartupDir "CorvinOS.lnk") `
            -TargetPath "powershell.exe" -Arguments $SupervisorArgs `
            -Description "Starts the CorvinOS console at login" -Hidden
        # Start it once right now too -- this install shouldn't need a logoff/logon first.
        Start-Process -FilePath "powershell.exe" -ArgumentList $SupervisorArgs -WindowStyle Hidden
        return "startup-shortcut"
    }
}

# ── 4. start server + wait for readiness + auto-launch console ──────────────────
Write-Host ""
Write-Step "Starting CorvinOS console server ..."

$ConsoleURL = "http://localhost:8765/console/"
# Slow Windows boots (cold Python import + Defender scan) can take well over
# 30s to answer healthz. The top-level goal is "the console opens in the
# browser no matter what", so give the server generous headroom AND still open
# the browser even if the probe times out (the server is durable via autostart).
$MaxRetries = 60
$RetryCount = 0
$ServerReady = $false

# Launched via the always-on Scheduled Task (or its Startup-folder fallback)
# above so it's durable from the first boot -- not just a one-off process
# tied to this installer window.
try {
    $AutostartMode = Install-CorvinAutostart
    if ($AutostartMode -eq "task") {
        Write-Ok "Console will auto-start on login and auto-restart on crash/reboot (Scheduled Task)."
    } else {
        Write-Ok "Console will auto-start on login via a Startup-folder shortcut (this account can't register Scheduled Tasks)."
    }
} catch {
    Write-Warn "Could not set up any autostart ($_) -- starting once instead (won't survive logoff/reboot). Re-run install.ps1 later to retry."
    # -Hidden, not -Minimized: a minimized window still has a taskbar entry
    # the user can click and close, killing this process exactly like closing
    # a visible console would -- Hidden has no window at all to close.
    try { Start-Process -FilePath "corvinos-serve" -ArgumentList "--no-browser" -WindowStyle Hidden -ErrorAction Stop } catch {}
}

# ── 3c. Desktop shortcut ──────────────────────────────────────────────────
# Independent of autostart: a visible, double-clickable way to (re)start the
# console by hand. Always attempted, never fatal if it fails.
try {
    $DesktopServeCmd = (Get-Command corvinos-serve -ErrorAction SilentlyContinue).Source
    if (-not $DesktopServeCmd) { $DesktopServeCmd = (Get-Command corvin-serve -ErrorAction SilentlyContinue).Source }
    if ($DesktopServeCmd) {
        $DesktopDir = [Environment]::GetFolderPath("Desktop")
        New-CorvinShortcut -Path (Join-Path $DesktopDir "CorvinOS.lnk") `
            -TargetPath $DesktopServeCmd -Description "Start the CorvinOS console"
        Write-Ok "Desktop shortcut created: CorvinOS.lnk"
    } else {
        Write-Warn "Could not create Desktop shortcut: corvinos-serve not found on PATH."
    }
} catch {
    Write-Warn "Could not create a Desktop shortcut ($_)."
}

# Wait for server to be ready. Live "still working" feedback on one
# self-overwriting line -- a cold Python import + Windows Defender scanning
# a freshly spawned python.exe can push this well past 30s with zero output
# otherwise, which read as a hang to a user watching the terminal (confirmed
# via a screenshot showing this exact step frozen with no further line
# printed).
while ($RetryCount -lt $MaxRetries) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8765/v1/console/healthz" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($response -and $response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
            $ServerReady = $true
            break
        }
    } catch {
        # Server not ready yet
    }
    $RetryCount++
    Write-Host "`r  waiting for server to come up... ($RetryCount/${MaxRetries}s)" -NoNewline -ForegroundColor DarkGray
    Start-Sleep -Seconds 1
}
Write-Host ("`r" + (" " * 60) + "`r") -NoNewline
if ($ServerReady) {
    Write-Ok "Server is ready! (${RetryCount}s)"
}

# Open the console no matter what. If the probe timed out the server is still
# coming up (autostart keeps it durable), so the browser tab will connect on
# reload a few seconds later -- far better than never opening it at all.
if (-not $ServerReady) {
    Write-Warn "Server is taking longer than expected to answer -- opening the console anyway; reload the tab if it doesn't connect immediately: $ConsoleURL"
}
try { Start-Process $ConsoleURL -ErrorAction Stop; if ($ServerReady) { Write-Ok "Server is ready -- opening the console in your browser ..." } }
catch { Write-Ok "Open the console in your browser: $ConsoleURL" }

# ── done / cheat sheet ────────────────────────────────────────────────────────
Write-Host ""
Write-Head "========================================================"
Write-Host " CorvinOS is ready!" -ForegroundColor Green
Write-Head "========================================================"
Write-Host ""
Write-Host " The console now starts automatically at login and restarts itself" -ForegroundColor White
Write-Host " if it ever crashes or the machine reboots -- nothing more to run:" -ForegroundColor White
Write-Host ""
Write-Cmd  "$ConsoleURL"
Write-Hint "# check status:  Get-ScheduledTask CorvinOS-Console"
Write-Hint "# turn off:      Unregister-ScheduledTask CorvinOS-Console"
Write-Host ""
Write-Head "========================================================"
Write-Host " Commands" -ForegroundColor White
Write-Head "========================================================"
Write-Host ""
Write-Host "   corvinos-serve     " -NoNewline -ForegroundColor White; Write-Host "Start the web console manually (already auto-started, see above)"
Write-Host "   corvin-install     " -NoNewline -ForegroundColor White; Write-Host "Setup wizard (bridges, tokens, voice)"
Write-Host "   corvin-uninstall   " -NoNewline -ForegroundColor White; Write-Host "Remove CorvinOS"
Write-Host "   corvin-a2a         " -NoNewline -ForegroundColor White; Write-Host "Agent-to-agent pairing and messaging"
Write-Host ""
Write-Cmd  "ollama pull qwen3:8b   # optional local model (offline /engine hermes)"
Write-Host ""
Pause-AndExit 0
