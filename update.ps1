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

# The banners below use box-drawing characters. PowerShell 5.1 encodes its output
# in the console's legacy OEM codepage by default, which has no glyph for them,
# so they arrive as "?" - both in a terminal and in anything capturing the
# output. Emit UTF-8 explicitly. (The file itself also needs a UTF-8 BOM, or 5.1
# decodes the SOURCE as the ANSI codepage and mangles them before this runs.)
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

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

# Combined output of the most recent Invoke-Native call, so a failing command can
# be made to report its own reason (see Show-LastNativeOutput).
$script:LastNativeOutput = @()

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
    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

function Invoke-Native {
    <#
        Runs a native executable, streams its combined output into the log, and
        returns its exit code.

        '2>&1' on a native command is NOT safe under $ErrorActionPreference =
        "Stop": PowerShell 5.1 wraps each stderr line in an ErrorRecord, and the
        Stop preference turns the first one into a terminating NativeCommandError.
        Git writes ordinary progress ("From github.com:...", "Already up to
        date.") to stderr, so the plain form aborts the updater on a *successful*
        fetch. Scope the preference down for the duration of the call instead.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @(),
        [string]$Level = "Debug"
    )

    $script:LastNativeOutput = @()
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object {
            $line = "$_".TrimEnd()
            if ($line) {
                $script:LastNativeOutput += $line
                Write-Log -Message $line -Level $Level
            }
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Show-LastNativeOutput {
    <#
        Re-emits the last subprocess's output at a VISIBLE level.

        Invoke-Native logs at "Debug", which Write-Log routes to Write-Verbose -
        suppressed unless the caller passed -Verbose. That is right for a
        successful 60-second build, and wrong for a failed one: a real exit-127
        run reported only "Console build failed (exit 127)" on screen while the
        cause ("/bin/bash: ...: No such file or directory") sat in the log file
        where nobody was looking. A failure has to state its own reason.
    #>
    param([int]$Tail = 20)

    $lines = @($script:LastNativeOutput)
    if ($lines.Count -eq 0) {
        Write-Log -Message "(the failing command produced no output)" -Level "Warn"
        return
    }
    if ($lines.Count -gt $Tail) {
        $lines = $lines[-$Tail..-1]
        Write-Log -Message "Last $Tail lines of output from the failing command:" -Level "Warn"
    } else {
        Write-Log -Message "Output from the failing command:" -Level "Warn"
    }
    foreach ($line in $lines) {
        Write-Host "    $line" -ForegroundColor DarkYellow
        Add-Content -Path $ErrorLogFile -Value "    $line" -ErrorAction SilentlyContinue
    }
}

function Get-BashExe {
    <#
        Finds a bash that can actually run a Windows-path shell script.

        'bash' on PATH is NOT it. On a default Windows 11 install the first
        bash.exe on PATH is %LOCALAPPDATA%\Microsoft\WindowsApps\bash.exe - the
        WSL launcher. It boots a Linux VM (~12s), cannot resolve a C:/ path
        (that is /mnt/c there) and exits 127 "No such file or directory", which
        reads exactly like a missing build tool. Git installs only Git\cmd on
        PATH, so its bash is never the one PATH resolves - it has to be located.

        So: probe candidates in order and pick the first that PROVES it can see
        the script and reach node. The probe is what excludes WSL; matching on
        the path would miss the next launcher that behaves this way.
    #>
    param([Parameter(Mandatory = $true)][string]$ProbeFile)

    $candidates = @()

    # Derive the Git root from git.exe itself (...\Git\cmd\git.exe -> ...\Git).
    # This finds a Git installed anywhere, including per-user.
    $gitCmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($gitCmd) {
        $gitRoot = Split-Path (Split-Path $gitCmd.Source -Parent) -Parent
        # bin\bash.exe is the wrapper that sets up the MSYS environment;
        # usr\bin\bash.exe is bare MSYS bash. Prefer the wrapper.
        $candidates += (Join-Path (Join-Path $gitRoot "bin") "bash.exe")
        $candidates += (Join-Path (Join-Path $gitRoot "usr") "bin\bash.exe")
    }

    # String concatenation, not Join-Path: an unset variable (ProgramFiles(x86)
    # on a machine that has none) makes Join-Path throw on a null -Path, and
    # under $ErrorActionPreference = "Stop" that aborts the whole updater.
    $candidates += @(
        "$env:ProgramFiles\Git\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe",
        "$env:ProgramFiles\Git\usr\bin\bash.exe"
    )

    # Whatever PATH calls "bash" comes LAST: it is the least trustworthy entry,
    # but if it is the only working one it should still be used.
    foreach ($c in (Get-Command bash -All -ErrorAction SilentlyContinue)) {
        if ($c.Source) { $candidates += $c.Source }
    }

    $seen = @()
    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        if ($seen -contains $candidate) { continue }
        $seen += $candidate
        if (-not (Test-Path $candidate)) { continue }

        $probe = "test -f '$ProbeFile' && command -v node >/dev/null 2>&1"
        $probeExit = 1
        try {
            $probeExit = Invoke-Native -Exe $candidate -Arguments @("-c", $probe)
        } catch {
            Write-Log -Message "bash candidate '$candidate' could not be launched: $_" -Level "Debug"
            continue
        }

        if ($probeExit -eq 0) {
            Write-Log -Message "Using bash: $candidate" -Level "Debug"
            return $candidate
        }
        Write-Log -Message "Skipping '$candidate': it cannot see the deploy script or node (exit $probeExit)." -Level "Warn"
    }

    return $null
}

function Get-AncestorPid {
    # An updater that kills its own shell cannot report a result. Walk the parent
    # chain once so the kill list can exclude this process and everything that
    # launched it.
    $ids = @($PID)
    $current = $PID
    for ($i = 0; $i -lt 16; $i++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue
        if (-not $proc -or -not $proc.ParentProcessId) { break }
        $current = [int]$proc.ParentProcessId
        if ($current -le 0 -or $ids -contains $current) { break }
        $ids += $current
    }
    return $ids
}

function Get-ConsoleProcess {
    param([int]$TargetPort)

    $self = Get-AncestorPid
    $hits = @()

    # Primary signal: whoever actually holds the port. The HTTP listener is a
    # uvicorn CHILD of corvinos-serve.exe ("python -m uvicorn
    # corvin_console.standalone:create_app"), so neither the launcher's process
    # name nor its command line identifies the thing bound to the port.
    try {
        foreach ($c in (Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue)) {
            $hits += [pscustomobject]@{ Id = [int]$c.OwningProcess; Launcher = $false }
        }
    } catch { }

    # Secondary: the launcher, plus any python host running the console/gateway.
    #
    # The command-line match is deliberately confined to python/corvinos
    # executables. Matching command lines across ALL processes is not safe: any
    # shell that merely MENTIONS "corvinos-serve" matches itself, and a looser
    # "*corvin*" pattern was measured matching EXCEL.EXE on this machine.
    $hostNames = @("corvinos-serve.exe", "python.exe", "pythonw.exe")
    foreach ($p in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($hostNames -notcontains $p.Name) { continue }
        if ($p.Name -eq "corvinos-serve.exe") {
            $hits += [pscustomobject]@{ Id = [int]$p.ProcessId; Launcher = $true }
        } elseif ($p.CommandLine -match 'corvin_console|corvin_gateway|corvinos-serve') {
            $hits += [pscustomobject]@{ Id = [int]$p.ProcessId; Launcher = $false }
        }
    }

    # Launchers first, so a supervisor cannot respawn the child we just killed.
    return @($hits |
        Where-Object { $_.Id -gt 0 -and $self -notcontains $_.Id } |
        Group-Object Id |
        ForEach-Object { [pscustomobject]@{ Id = [int]$_.Name; Launcher = [bool]($_.Group.Launcher -contains $true) } } |
        Sort-Object -Property @{ Expression = "Launcher"; Descending = $true })
}

function Stop-Console {
    param([int]$TargetPort = 8765)

    Write-Log -Message "Stopping console server..." -Level "Info"

    $targets = Get-ConsoleProcess -TargetPort $TargetPort
    if ($targets.Count -eq 0) {
        Write-Log -Message "No running console found." -Level "Success"
        return
    }

    Write-Log -Message "Found $($targets.Count) console process(es). Stopping..." -Level "Warn"
    foreach ($t in $targets) {
        $name = (Get-Process -Id $t.Id -ErrorAction SilentlyContinue).Name
        if (-not $name) { continue }  # already gone (tree kill of its parent)
        Write-Log -Message "Stopping PID $($t.Id) ($name)..." -Level "Info"
        # /T takes the children with it. Killing only the launcher leaves the
        # uvicorn child holding the port, and the restart then fails to bind.
        $null = Invoke-Native -Exe "taskkill.exe" -Arguments @("/PID", "$($t.Id)", "/T", "/F")
    }

    # "Process stopped" is not "port released", and the restart has to bind that
    # port. Wait for the listener to actually disappear instead of sleeping a
    # fixed second and hoping.
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Test-TcpPort -TargetPort $TargetPort -TimeoutMs 500)) {
            Write-Log -Message "Port $TargetPort released." -Level "Success"
            return
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Log -Message "Port $TargetPort still has a listener after 10s." -Level "Warn"
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

# Join-Path takes exactly -Path and -ChildPath on PowerShell 5.1; the
# multi-segment form is 6+ only, and on 5.1 the third segment binds to nothing
# ("A positional parameter cannot be found that accepts argument ...").
$scriptsDir = Join-Path $CorvinRepo "scripts"
$consoleDeployScript = Join-Path $scriptsDir "console-deploy.ps1"
if (-not (Test-Path $consoleDeployScript)) {
    $consoleDeployScript = Join-Path $scriptsDir "console-deploy.sh"
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
        if ((Invoke-Native -Exe "git" -Arguments @("fetch", "origin", "main")) -ne 0) {
            Write-Log -Message "Git fetch failed. Check network and retry." -Level "Error"
            Show-LastNativeOutput -Tail 15
            exit 1
        }

        $currentHash = (& git rev-parse HEAD 2>$null | Select-Object -First 1)
        $latestHash = (& git rev-parse origin/main 2>$null | Select-Object -First 1)
        $shortHash = if ($currentHash -and $currentHash.Length -ge 8) {
            $currentHash.Substring(0, 8)
        } else {
            "$currentHash"
        }

        if ($currentHash -and $currentHash -eq $latestHash) {
            Write-Log -Message "Already up-to-date (commit $shortHash)" -Level "Success"
        } else {
            Write-Log -Message "New commits available. Pulling..." -Level "Warn"
            if ((Invoke-Native -Exe "git" -Arguments @("pull", "origin", "main", "--ff-only")) -ne 0) {
                Write-Log -Message "Git pull failed. Check for local changes." -Level "Error"
                Show-LastNativeOutput -Tail 15
                exit 1
            }
            Write-Log -Message "Successfully pulled latest main" -Level "Success"
        }
    } finally {
        Pop-Location
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Rebuild console UI
#
# This runs BEFORE the server is stopped, deliberately. console-deploy.sh's
# contract is not "a build succeeded" but "the LIVE host hands out the bundle I
# just built": it re-reads index.html over HTTP from the console and exits 2 if
# it cannot. Stopping the console first made that verification structurally
# impossible, so every update landed in the unverifiable branch. The script
# builds into a staging directory and swaps, so rebuilding does not take the
# running console down.
# ─────────────────────────────────────────────────────────────────────────────
$consoleWasUp = Test-TcpPort -TargetPort $Port -TimeoutMs 2000
if ($consoleWasUp) {
    Write-Log -Message "Console is live on port $Port - the build will be verified over HTTP." -Level "Info"
} else {
    Write-Log -Message "Nothing listening on port $Port - building without live verification." -Level "Warn"
}

Write-Log -Message "Rebuilding console UI..." -Level "Info"

# console-deploy.sh accepts ONLY --fast and --marker and rejects anything else
# with exit 1, so it must never be handed "--force". -Force here means "full
# rebuild", which is that script's default path (it clears node_modules/.vite
# first); --fast is the incremental one.
$buildArgs = @()
if (-not $Force) {
    $buildArgs += "--fast"
}

$buildExit = 1
try {
    if ($consoleDeployScript -like "*.ps1") {
        $buildExit = Invoke-Native -Exe $consoleDeployScript -Arguments $buildArgs
    } else {
        # Absolute, forward-slash path: MSYS bash does not reliably resolve a
        # relative Windows path with backslashes, and the script derives its
        # REPO_ROOT from BASH_SOURCE, so it must be given a real path. Resolve it
        # BEFORE picking bash - the candidate probe needs it.
        $deployForBash = (Resolve-Path $consoleDeployScript).Path.Replace('\', '/')

        $bashExe = Get-BashExe -ProbeFile $deployForBash
        if (-not $bashExe) {
            Write-Log -Message "No bash able to run the console build was found. Install Git for Windows (the WSL 'bash' on PATH cannot run it)." -Level "Error"
            exit 2
        }

        # Splat the argument list - passing $buildArgs unsplatted hands bash one
        # array-shaped argument instead of the individual flags.
        $buildExit = Invoke-Native -Exe $bashExe -Arguments (@($deployForBash) + $buildArgs)
    }
} catch {
    Write-Log -Message "Exception during console build: $_" -Level "Error"
    exit 1
}

# console-deploy.sh exit codes: 0 = live · 1 = build failed · 2 = served != built
switch ($buildExit) {
    0 {
        Write-Log -Message "Console build successful and verified live." -Level "Success"
    }
    2 {
        if ($consoleWasUp) {
            Write-Log -Message "Built bundle is not what the console serves (stale)." -Level "Error"
            Show-LastNativeOutput -Tail 20
            exit 1
        }
        Write-Log -Message "Console build successful (live check skipped - nothing was serving port $Port)." -Level "Success"
    }
    default {
        Write-Log -Message "Console build failed (exit $buildExit)." -Level "Error"
        Show-LastNativeOutput -Tail 30
        exit 1
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Restart console (unless -NoRestart)
# ─────────────────────────────────────────────────────────────────────────────
if (-not $NoRestart) {
    Stop-Console -TargetPort $Port

    Write-Log -Message "Starting CorvinOS console..." -Level "Info"

    if (-not (Test-Command "corvinos-serve")) {
        Write-Log -Message "corvinos-serve not found on PATH. Run: uv tool upgrade corvinos" -Level "Error"
        exit 2
    }

    try {
        # Pass the port through. Without it the new instance binds the default
        # 8765 while the health check below and the URL printed at the end both
        # use $Port - so a non-default -Port reported a failure on a working
        # console. --no-browser keeps an unattended update from opening a window.
        Start-Process "corvinos-serve" `
            -ArgumentList @("--no-browser", "--port", "$Port", "--host", "127.0.0.1") `
            -WindowStyle Hidden -ErrorAction Stop
        Write-Log -Message "Console started in background" -Level "Success"
    } catch {
        Write-Log -Message "Failed to start console: $_" -Level "Error"
        exit 3
    }

    # ─────────────────────────────────────────────────────────────────────
    # Phase 3a: Health check with exponential backoff (ADR-0867)
    #
    # Bounded by WALL CLOCK, not by a retry count. With the backoff capped at 8s,
    # 60 retries is ~7.7 minutes, while both messages claimed to be reporting
    # seconds - so the numbers printed were never the time that had passed.
    # ─────────────────────────────────────────────────────────────────────
    Write-Log -Message "Waiting for console to respond..." -Level "Info"

    $timeoutSeconds = 90
    $backoff = 1
    $serverReady = $false
    $watch = [System.Diagnostics.Stopwatch]::StartNew()

    while ($watch.Elapsed.TotalSeconds -lt $timeoutSeconds) {
        if (Test-TcpPort -TargetHost "127.0.0.1" -TargetPort $Port -TimeoutMs 2000) {
            $serverReady = $true
            break
        }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds $backoff
        if ($backoff -lt 8) {
            $backoff = $backoff * 2
        }
    }

    $watch.Stop()
    $elapsed = [int]$watch.Elapsed.TotalSeconds
    Write-Host ""
    if ($serverReady) {
        Write-Log -Message "Console is ready after ${elapsed}s" -Level "Success"
    } else {
        Write-Log -Message "Console did not respond within ${timeoutSeconds}s. It may still be starting..." -Level "Warn"
        Write-Host "  Open the console: http://127.0.0.1:$Port/console/" -ForegroundColor Yellow
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor White
if ($NoRestart -or $serverReady) {
    Write-Host "  CorvinOS update complete!" -ForegroundColor Green
} else {
    # Don't claim success for a console that never came back up.
    Write-Host "  CorvinOS update finished - console NOT confirmed up" -ForegroundColor Yellow
}
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor White
Write-Host ""

if ($NoRestart) {
    Write-Host "  Console build complete, server left as it was."
    Write-Host "  Start it with: corvinos-serve --port $Port" -ForegroundColor Cyan
} elseif ($serverReady) {
    Write-Host "  Console is running at:" -ForegroundColor White
    Write-Host "  http://127.0.0.1:$Port/console/" -ForegroundColor Cyan
} else {
    Write-Host "  The rebuild succeeded, but nothing is answering on port $Port." -ForegroundColor Yellow
    Write-Host "  Check the log, then start it by hand: corvinos-serve --port $Port" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "  Logs: $MainLogFile" -ForegroundColor DarkGray
Write-Host ""

if (-not $NoRestart -and -not $serverReady) { exit 3 }
exit 0
