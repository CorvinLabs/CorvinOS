#Requires -Version 5.1
<#
.SYNOPSIS
    Restart-forever supervisor for a CorvinOS background process on Windows.

.DESCRIPTION
    Windows has no built-in equivalent of systemd's `Restart=always` /
    `RestartSec=5` (used by core/gateway/systemd/corvin-webui.service on
    Linux/macOS) -- a process that crashes, is killed, or the machine reboots
    just stays down until a human notices and restarts it manually. For a
    process that carries the ADR-0180 presence heartbeat (the console) or a
    messenger bridge, "stays down silently" means the instance quietly drops
    off the corvin-labs.com "online now" count with no error anywhere.

    Registered once as a Scheduled Task (trigger: at user logon) by
    `bridge.ps1 install-autostart`, this script loops FOREVER in its own
    process: launch the target, block until it exits (crash, kill, graceful
    exit -- doesn't matter which), wait RestartSec, launch it again. The loop
    lives HERE rather than in Task Scheduler's own retry counters because
    those are capped (finite attempts over a rolling window); a
    `while ($true)` loop is not.

    -Target selects WHAT to supervise. Deliberately just two fixed, known-safe
    targets (not an arbitrary passed-through command line) -- the whole reason
    this script exists is a prior bug where a hand-built command string
    silently pointed at a file that doesn't exist
    (see bridge.ps1's "console" case history). Building the actual argument
    arrays natively in PowerShell HERE, instead of round-tripping them through
    a flat Task Scheduler action string, avoids that entire class of quoting
    mistake -- this also means paths containing spaces (a real thing on
    Windows: "C:\Users\Jane Doe\...") are handled correctly without any
    string-splitting on our part.

.NOTES
    Not meant to be run by hand -- see `bridge.ps1 install-autostart`.
    Writes a small append-only log to <CORVIN_HOME>\logs\<Target>-supervisor.log
    so a silent crash loop is still observable after the fact.
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet("console", "bridge")][string]$Target,
    [Parameter(Mandatory=$false)][string]$Bridge = "discord",
    [int]$RestartSec = 5,
    # ADR-0184 Stufe-1: bounded restart. Mirrors the systemd
    # StartLimitBurst=5 / StartLimitIntervalSec=300 pair used for the Linux
    # user unit (corvinOS/installer/service_manager.py) so all three
    # platforms apply the same crash-loop cutoff -- 5 restarts in 5 minutes,
    # then stop instead of spinning forever.
    [int]$MaxRestarts = 5,
    [int]$RestartWindowSec = 300
)

$ErrorActionPreference = "Continue"   # a single failed iteration must never kill the loop
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path   # …\operator\bridges\shared
$BridgeRoot = Split-Path -Parent $ScriptDir                    # …\operator\bridges

if (-not $env:CORVIN_HOME) {
    $env:CORVIN_HOME = Join-Path $env:USERPROFILE ".corvin"
}
$LogDir = Join-Path $env:CORVIN_HOME "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogName = if ($Target -eq "bridge") { "bridge-$Bridge" } else { "console" }
$LogFile = Join-Path $LogDir "$LogName-supervisor.log"

function Write-SupervisorLog([string]$Message) {
    $ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
    try { Add-Content -Path $LogFile -Value "$ts [$LogName] $Message" -ErrorAction SilentlyContinue } catch {}
}

function Find-Python {
    $venvPy = Join-Path $env:USERPROFILE ".corvinos\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    foreach ($cmd in @("python", "python3", "py")) {
        try {
            $ver = & $cmd -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>$null
            if ($ver -match "^3\.(\d+)" -and [int]$Matches[1] -ge 11) { return $cmd }
        } catch {}
    }
    return "python"   # last resort -- Start-Process will error clearly if this isn't on PATH either
}

# Build the (FilePath, ArgumentList[], WorkingDirectory) for the chosen target
# as NATIVE PowerShell values -- never as a string that has to be re-parsed.
if ($Target -eq "console") {
    $FilePath = Find-Python
    $ArgList = @("-m", "corvinOS", "serve", "--no-browser")
    $WorkDir = $BridgeRoot
} else {
    # Recurse into bridge.ps1's own "up" case -- reuses its existing TCP-loopback
    # port allocation + node-daemon launch logic exactly, no duplication.
    $FilePath = "powershell.exe"
    $ArgList = @("-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", (Join-Path $BridgeRoot "bridge.ps1"), "up", $Bridge)
    $WorkDir = $BridgeRoot
}

# WA-2/WA-3: tell the child it runs under supervision so `corvinOS serve`
# suppresses its in-process self-update handoff. Without this, an available
# release makes serve exit for the handoff, the supervisor relaunches it in
# 5s, and the detached updater hits a re-locked venv -- upgrade fails and a
# restart-budget slot burns on every 6h update-marker check. The install.ps1-
# generated supervisor sets the same flag; parity is load-bearing.
$env:CORVIN_SUPERVISED = "1"

Write-SupervisorLog "supervisor starting -- target=$Target child: $FilePath $($ArgList -join ' ')"

# Rolling window of recent restart timestamps -- bounded crash-loop guard
# (ADR-0184 Stufe-1). Keep this logic IDENTICAL to the one generated by
# install.ps1's Install-CorvinAutostart; test_windows_supervisor_parity.py
# checks both files for the same shape.
$RestartTimestamps = @()

while ($true) {
    $Now = Get-Date
    $RestartTimestamps = @($RestartTimestamps | Where-Object { ($Now - $_).TotalSeconds -le $RestartWindowSec })
    if ($RestartTimestamps.Count -ge $MaxRestarts) {
        Write-SupervisorLog "CRITICAL: $MaxRestarts restarts within ${RestartWindowSec}s -- stopping supervisor to avoid a crash loop. Check $LogFile, fix the underlying issue, then restart with: Start-ScheduledTask <task-name>"
        break
    }
    $RestartTimestamps += $Now
    try {
        Write-SupervisorLog "launching child process"
        $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgList `
            -WorkingDirectory $WorkDir -NoNewWindow -PassThru -Wait
        Write-SupervisorLog "child exited with code $($proc.ExitCode) -- restarting in ${RestartSec}s"
    } catch {
        Write-SupervisorLog "supervisor error launching child: $_ -- retrying in ${RestartSec}s"
    }
    Start-Sleep -Seconds $RestartSec
}
