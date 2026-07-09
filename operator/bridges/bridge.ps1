#Requires -Version 5.1
# bridge.ps1 -- CorvinOS bridge launcher for Windows (ADR-0159 M4).
#
# Equivalent to bridge.sh on Linux/macOS. Activates the venv and dispatches
# subcommands: up, down, doctor, restart, status, console,
# install-autostart, uninstall-autostart, autostart-status.
#
# Usage:
#   .\bridge.ps1 up             # Start Discord (default) bridge
#   .\bridge.ps1 up whatsapp    # Start WhatsApp bridge
#   .\bridge.ps1 doctor         # Run self-test
#   .\bridge.ps1 status         # Show running bridge processes
#   .\bridge.ps1 down           # Stop bridge(s)
#   .\bridge.ps1 restart        # Restart bridge(s)
#   .\bridge.ps1 console        # Start web console (background, once, this login only)
#
#   .\bridge.ps1 install-autostart      # Console + Discord bridge survive crashes
#                                       # AND reboots (Task Scheduler, no systemd
#                                       # equivalent on Windows otherwise -- this is
#                                       # what keeps the ADR-0180 presence heartbeat
#                                       # reporting "online" continuously)
#   .\bridge.ps1 install-autostart telegram   # same, for a non-default bridge
#   .\bridge.ps1 autostart-status      # Show registered autostart tasks + state
#   .\bridge.ps1 uninstall-autostart   # Remove the autostart tasks
#
# Prerequisites (set up by install.ps1):
#   - Python 3.11+ in PATH or %USERPROFILE%\.corvinos\Scripts\python.exe
#   - CorvinOS installed via: pip install -e .
#   - Ollama running (for hermes engine): ollama serve
#   - (Optional) claude CLI for claude_code engine
#
# Environment:
#   CORVIN_HOME         Override corvin home dir (default: %USERPROFILE%\.corvin)
#   CORVIN_TENANT_ID    Override tenant (default: _default)
#   CORVIN_OS_ENGINE    Override engine detection (hermes|claude_code|opencode)
#   CORVIN_SANDBOX      Override sandbox tier (bwrap|docker|none)
#   CORVIN_BRIDGE_PORT  Set when TCP loopback transport is active (auto-set)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── Python / venv resolution ──────────────────────────────────────────────────

function Find-Python {
    $venvPy = Join-Path $env:USERPROFILE ".corvinos\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    foreach ($cmd in @("python", "python3", "py")) {
        try {
            $ver = & $cmd -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>$null
            if ($ver -match "^3\.(\d+)" -and [int]$Matches[1] -ge 11) {
                return $cmd
            }
        } catch {}
    }
    Write-Error "Python 3.11+ not found. Run install.ps1 first."
    exit 1
}

$Python = Find-Python

# ── Corvin home ───────────────────────────────────────────────────────────────

if (-not $env:CORVIN_HOME) {
    $env:CORVIN_HOME = Join-Path $env:USERPROFILE ".corvin"
}

# ── Subcommand dispatch ───────────────────────────────────────────────────────

$Subcommand = if ($args.Count -gt 0) { $args[0] } else { "up" }
$Bridge     = if ($args.Count -gt 1) { $args[1] } else { "discord" }

switch ($Subcommand) {

    "up" {
        Write-Host "Starting $Bridge bridge (Windows / TCP-loopback transport)..." -ForegroundColor Cyan

        # Allocate a free TCP port for the bridge sidecar transport (ADR-0159 M4).
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
        $listener.Start()
        $BridgePort = $listener.LocalEndpoint.Port
        $listener.Stop()
        $env:CORVIN_BRIDGE_PORT = "$BridgePort"
        Write-Host "  Bridge TCP loopback port: $BridgePort" -ForegroundColor DarkGray

        # Write port to corvin home for discovery by other processes.
        $PortFile = Join-Path $env:CORVIN_HOME "global\bridge.port"
        $null = New-Item -ItemType Directory -Force -Path (Split-Path $PortFile)
        Set-Content -Path $PortFile -Value "$BridgePort" -Encoding ASCII -NoNewline
        icacls $PortFile /inheritance:r /grant:r "${env:USERNAME}:(R,W)" | Out-Null

        # Launch the bridge adapter
        $BridgeScript = Join-Path $ScriptDir "$Bridge\daemon.js"
        if (Test-Path $BridgeScript) {
            Write-Host "  Launching Node.js daemon: $BridgeScript" -ForegroundColor DarkGray
            & node $BridgeScript
        } else {
            Write-Error "Bridge script not found: $BridgeScript"
            exit 1
        }
    }

    "doctor" {
        Write-Host "Running CorvinOS self-test..." -ForegroundColor Cyan
        & $Python -c "
import sys
sys.path.insert(0, r'$ScriptDir\shared')
from self_test import run_self_test, CRITICAL, WARNING
result = run_self_test()
for c in result.checks:
    icon = 'OK' if c.ok else c.severity
    print(f'  [{icon}] {c.name}: {c.detail}')
failed_crit = [c for c in result.checks if not c.ok and c.severity == CRITICAL]
if failed_crit:
    print(f'\nFAILED: {len(failed_crit)} CRITICAL check(s)')
    sys.exit(1)
else:
    print('\nAll CRITICAL checks passed.')
"
    }

    "status" {
        Write-Host "CorvinOS bridge processes:" -ForegroundColor Cyan
        Get-Process | Where-Object {
            $_.ProcessName -match "node" -or $_.ProcessName -match "python"
        } | Select-Object ProcessName, Id, CPU, WorkingSet | Format-Table -AutoSize
    }

    "down" {
        Write-Host "Stopping CorvinOS bridge processes..." -ForegroundColor Cyan
        Get-Process | Where-Object { $_.ProcessName -match "node" } | ForEach-Object {
            try { Stop-Process -Id $_.Id -Force; Write-Host "  Stopped: $($_.Id)" } catch {}
        }
        # Remove bridge port file
        $PortFile = Join-Path $env:CORVIN_HOME "global\bridge.port"
        if (Test-Path $PortFile) { Remove-Item $PortFile -Force }
    }

    "restart" {
        & $MyInvocation.MyCommand.Path down
        Start-Sleep -Milliseconds 500
        & $MyInvocation.MyCommand.Path up $Bridge
    }

    "console" {
        Write-Host "Starting CorvinOS web console..." -ForegroundColor Cyan
        # NOTE: this used to point at "shared\corvin_gateway.py", a script that
        # does not exist anywhere in the repo -- the command silently failed
        # (Start-Process -WindowStyle Hidden swallows the "file not found"
        # error from the hidden Python process), so the console -- and with it
        # the ADR-0180 presence heartbeat thread -- never actually started on
        # Windows via this path. `python -m corvinOS serve` is the real,
        # PATH-independent entry point (see corvinOS/__main__.py) and is what
        # install.ps1 already tells users to run day-to-day; it wires up the
        # console AND the heartbeat thread (ops/launcher/corvin/serve_backend.py).
        Start-Process -FilePath $Python -ArgumentList "-m", "corvinOS", "serve", "--no-browser" -WindowStyle Hidden
        Write-Host "  Console started in background. Check http://localhost:8765" -ForegroundColor Green
    }

    # ── autostart (Windows equivalent of `bridge.sh up`'s systemd Restart=always) ──
    # Linux's corvin-webui.service (+ the bridge units enabled by `bridge.sh up`)
    # survive crashes and reboots unattended. Windows has no default persistent
    # background-service story, so a Windows instance silently goes dark (and
    # drops off the ADR-0180 "online now" count) the moment its terminal window
    # is closed or the machine restarts -- until a human notices and re-runs
    # `bridge.ps1 console` / `bridge.ps1 up` by hand. These two Scheduled Tasks
    # close that gap: registered once, they relaunch the console and the bridge
    # at every logon AND keep relaunching them forever via
    # shared\corvin-supervisor.ps1 if either one ever exits.
    "install-autostart" {
        Write-Host "Installing CorvinOS autostart (Windows Task Scheduler)..." -ForegroundColor Cyan
        $Supervisor = Join-Path $ScriptDir "shared\corvin-supervisor.ps1"
        if (-not (Test-Path $Supervisor)) {
            Write-Error "Supervisor script not found: $Supervisor"
            exit 1
        }

        function Install-AutostartTask {
            param([string]$TaskName, [string]$TargetArg, [string]$BridgeArg)

            $ArgString = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Supervisor`" -Target $TargetArg"
            if ($BridgeArg) { $ArgString += " -Bridge $BridgeArg" }

            $Action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $ArgString
            $Trigger  = New-ScheduledTaskTrigger -AtLogOn
            # -Hidden mirrors install.ps1's Install-CorvinAutostart (keep both
            # supervisor registrations IDENTICAL in this regard -- a visible/
            # closable window on one path while the other is hidden is exactly
            # the drift that let a real "close the window, the app dies" bug
            # reach users).
            $Settings = New-ScheduledTaskSettingsSet `
                -Hidden `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit ([TimeSpan]::Zero) `
                -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -MultipleInstances IgnoreNew

            # Idempotent: replace a stale registration instead of erroring on re-run.
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
            Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
                -Settings $Settings -RunLevel Limited `
                -Description "CorvinOS $TargetArg -- auto-restarts on crash/reboot (ADR-0180 presence heartbeat)" `
                | Out-Null
            # Start it now too -- don't make the user log off/on to see it take effect.
            Start-ScheduledTask -TaskName $TaskName
            Write-Host "  Registered + started: $TaskName" -ForegroundColor Green
        }

        Install-AutostartTask -TaskName "CorvinOS-Console" -TargetArg "console"
        Install-AutostartTask -TaskName "CorvinOS-Bridge-$Bridge" -TargetArg "bridge" -BridgeArg $Bridge

        Write-Host ""
        Write-Host "Autostart installed. The console + $Bridge bridge now restart automatically" -ForegroundColor Green
        Write-Host "on crash and at every logon. Logs: %USERPROFILE%\.corvin\logs\*-supervisor.log" -ForegroundColor DarkGray
        Write-Host "Undo with: .\bridge.ps1 uninstall-autostart" -ForegroundColor DarkGray
    }

    "uninstall-autostart" {
        Write-Host "Removing CorvinOS autostart tasks..." -ForegroundColor Cyan
        foreach ($name in @("CorvinOS-Console", "CorvinOS-Bridge-$Bridge")) {
            if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
                Unregister-ScheduledTask -TaskName $name -Confirm:$false
                Write-Host "  Removed: $name" -ForegroundColor Green
            } else {
                Write-Host "  Not installed: $name" -ForegroundColor DarkGray
            }
        }
        Write-Host "  Note: this stops FUTURE auto-restarts; any already-running" -ForegroundColor DarkGray
        Write-Host "  console/bridge process keeps running until you 'bridge.ps1 down' it." -ForegroundColor DarkGray
    }

    "autostart-status" {
        Write-Host "CorvinOS autostart tasks:" -ForegroundColor Cyan
        Get-ScheduledTask | Where-Object { $_.TaskName -like "CorvinOS-*" } |
            Select-Object TaskName, State |
            Format-Table -AutoSize
    }

    default {
        Write-Host "Usage: .\bridge.ps1 <up|down|restart|doctor|status|console> [bridge]" -ForegroundColor Yellow
        Write-Host "       .\bridge.ps1 <install-autostart|uninstall-autostart|autostart-status> [bridge]" -ForegroundColor Yellow
        exit 1
    }
}
