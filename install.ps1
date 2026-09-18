#Requires -Version 5.1
<#
.SYNOPSIS
    CorvinOS installer for Windows.

.DESCRIPTION
    Bootstraps uv (which brings its own Python), a local Node.js runtime, and
    installs the corvinos package -- either from PyPI or, with -Editable, from a
    local clone. Mirrors install.sh (Linux/macOS) semantics.

    ASCII-ONLY BY CONTRACT. Windows PowerShell 5.1 decodes a BOM-less script as
    ANSI (cp1252), where the UTF-8 bytes of characters like U+2713 and U+2551
    decode to cp1252 smart quotes -- which PowerShell honours as string
    delimiters. That unbalances every following string and the script dies with
    a parse error before line 1 runs. Do not add non-ASCII characters here.
    Enforced by .github/workflows/install-test.yml.

.PARAMETER Editable
    Install from this local clone instead of PyPI (developer install).

.PARAMETER DryRun
    Report every action without changing anything.

.PARAMETER NoClaudeCode
    Skip the Claude Code detection step.

.PARAMETER Lan
    Print the firewall command needed to reach the console from the LAN.

.PARAMETER Provision
    Additionally run 'corvin-install --yes' (voice/STT/TTS + services). Off by
    default because it downloads models and can take several minutes.

.PARAMETER NoStart
    Install only: do not start the console and do not open a browser.

.PARAMETER NoBrowser
    Start and verify the console, but leave the browser closed.

.PARAMETER RebuildWeb
    Rebuild the console SPA even when a build already exists. Without it, an
    existing web-next/dist/index.html is reused (a full build takes minutes).

.PARAMETER Port
    Port the console listens on (default 8765). The URL is always 127.0.0.1:
    the server binds v4 loopback only, and Windows resolves "localhost" to ::1
    first, which would fail to connect.

.PARAMETER StartTimeoutSeconds
    How long to wait for the console to answer HTTP 200 with the SPA shell
    (default 180).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -Editable .

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun -Verbose

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -Editable . -Port 8790 -NoBrowser

.NOTES
    Exit codes:
      0 = Success: package installed, console answers HTTP 200 with the SPA,
          local login issues a session cookie, browser opened (unless
          -NoBrowser / -NoStart)
      1 = Installation failed (check log)
      2 = Prerequisites missing/failed
      3 = Installed, but the console is not serving. The package is fine; the
          summary names the reason and prints the server's own last lines.
      4 = Configuration error (editable path invalid)
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [Alias("e")]
    [string]$Editable = "",

    [switch]$DryRun,
    [switch]$NoClaudeCode,
    [switch]$Lan,
    [switch]$Provision,
    [switch]$NoStart,
    [switch]$NoBrowser,
    [switch]$RebuildWeb,

    [ValidateRange(1, 65535)]
    [int]$Port = 8765,

    # 30s is the measured cold boot to HTTP 200 on a developer box with the SPA
    # already built; the default leaves headroom for a slow disk, a first-run
    # plugin scan and an antivirus scanning the new tool venv.
    [ValidateRange(5, 1800)]
    [int]$StartTimeoutSeconds = 180
)

# -----------------------------------------------------------------------------
# Global configuration
# -----------------------------------------------------------------------------

$ErrorActionPreference = "Stop"
# Invoke-WebRequest is an order of magnitude slower with the progress stream on.
$ProgressPreference = "SilentlyContinue"
# PS 5.1 defaults to SSL3/TLS1.0; nodejs.org and github.com require TLS 1.2+.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol
} catch { }

$PackageName      = if ($env:CORVIN_PKG) { $env:CORVIN_PKG } else { "corvinos" }
$CorvinMinVersion = "2.0.0"
$UvVersion        = "0.12.9"
$UvInstallerUrl   = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-installer.ps1"
$UvInstallerSha256 = "69de475bf929f1ac248efb5a85189177a45517e2346cd68762bde453fec10a6b"
$DefaultNodeVersion = "24.18.0"

$InstallTimestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogDir       = Join-Path $env:TEMP "corvinos-install-$InstallTimestamp"
$MainLogFile  = Join-Path $LogDir "install.log"
$ErrorLogFile = Join-Path $LogDir "install-errors.log"
$DebugLogFile = Join-Path $LogDir "install-debug.log"
$null = New-Item -ItemType Directory -Path $LogDir -Force -ErrorAction SilentlyContinue

$script:CurrentStep        = 0
$script:TotalSteps         = 25   # keep in sync with the Write-Step call count
$script:InstallationFailed = $false
$script:Warnings           = New-Object System.Collections.ArrayList

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

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
    if ($Level -eq "Warn") {
        $null = $script:Warnings.Add($Message)
    }
    if ($Level -eq "Debug") {
        Add-Content -Path $DebugLogFile -Value $logMessage -ErrorAction SilentlyContinue
    }
}

function Write-Step {
    param([string]$Message)
    $script:CurrentStep++
    $percent = [math]::Max(0, [math]::Min(100,
        [math]::Round(($script:CurrentStep / [math]::Max(1, $script:TotalSteps)) * 100)))
    Write-Progress -Activity "CorvinOS Installation" -CurrentOperation $Message `
        -PercentComplete $percent -Status "Step $script:CurrentStep/$script:TotalSteps"
    Write-Log -Message "$Message [$script:CurrentStep/$script:TotalSteps]" -Level "Info"
}

# Readiness is probed over a raw TCP connect, not HTTP: a corporate proxy
# configured machine-wide can intercept an Invoke-WebRequest to localhost and
# turn "not up yet" into a proxy error that looks like a real failure.
function Test-TcpPort {
    param([string]$TargetHost = "127.0.0.1", [int]$TargetPort, [int]$TimeoutMs = 1000)

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

function Write-Header {
    param([string]$Title)
    Write-Host ""
    Write-Host ("-" * 62) -ForegroundColor Cyan
    Write-Host $Title -ForegroundColor Cyan
    Write-Host ("-" * 62) -ForegroundColor Cyan
}

function Write-Summary {
    param([string]$Status, [int]$ExitCode = 0)
    Write-Host ""
    Write-Host ("=" * 62) -ForegroundColor Cyan
    Write-Host " Installation Summary" -ForegroundColor Cyan
    Write-Host ("-" * 62) -ForegroundColor Cyan
    Write-Host " Status:    $Status" -ForegroundColor $(if ($ExitCode -eq 0) { "Green" } else { "Red" })
    Write-Host " Timestamp: $InstallTimestamp" -ForegroundColor White
    Write-Host " Main log:  $MainLogFile" -ForegroundColor White
    if ($ExitCode -ne 0) {
        Write-Host " Error log: $ErrorLogFile" -ForegroundColor Red
    }
    if ($script:Warnings.Count -gt 0) {
        Write-Host " Warnings:  $($script:Warnings.Count)" -ForegroundColor Yellow
        foreach ($w in $script:Warnings) {
            Write-Host "   - $w" -ForegroundColor Yellow
        }
    }
    Write-Host ("=" * 62) -ForegroundColor Cyan
}

function Stop-WithError {
    param([string]$Message, [int]$ExitCode = 1, [string]$Status = "FAILED")
    $script:InstallationFailed = $true
    Write-Log -Message $Message -Level "Error"
    Write-Summary -Status $Status -ExitCode $ExitCode
    exit $ExitCode
}

# Runs a native command, streams its output into the log, and fails on a
# non-zero exit code. Native tools do not throw, so $LASTEXITCODE is the only
# signal -- a try/catch around `& uv ...` alone catches nothing.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$IgnoreExitCode
    )

    Write-Log -Message "exec: $FilePath $($Arguments -join ' ')" -Level "Debug"

    # A native tool writing to stderr raises a TERMINATING error while
    # $ErrorActionPreference is 'Stop' and its streams are merged with 2>&1.
    # uv reports ordinary progress ("Resolved 90 packages in 593ms") on stderr,
    # so the first progress line aborted the install and was then reported AS
    # the failure. Exit code is the only reliable success signal here.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $FilePath @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }

    foreach ($line in $output) {
        Write-Log -Message ([string]$line) -Level "Debug"
    }
    if (-not $IgnoreExitCode -and $code -ne 0) {
        # Surface the tail inline: a bare "see the log" forces a second run to
        # learn anything, and the log lives in a temp directory.
        $tail = @($output | Select-Object -Last 15 | ForEach-Object { "    $_" }) -join "`n"
        throw "$FilePath exited with code $code`n$tail`n  (full output: $DebugLogFile)"
    }
    return $code
}

# Like Invoke-Native, but bounded in time and non-throwing: returns the exit
# code, the captured output and whether it was killed on timeout. npm is the
# reason this exists -- behind a corporate proxy `npm install` can sit on a
# socket indefinitely, and Invoke-Native has no way to give up, so the whole
# installer hung with no output.
function Invoke-NativeTimed {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [string]$WorkingDirectory = ""
    )

    $tag    = [guid]::NewGuid().ToString("N").Substring(0, 8)
    $outLog = Join-Path $LogDir "cmd-$tag.out.log"
    $errLog = Join-Path $LogDir "cmd-$tag.err.log"

    Write-Log -Message "exec(<=${TimeoutSeconds}s): $FilePath $($Arguments -join ' ')" -Level "Debug"

    $startArgs = @{
        FilePath               = $FilePath
        PassThru               = $true
        NoNewWindow            = $true
        RedirectStandardOutput = $outLog
        RedirectStandardError  = $errLog
    }
    if ($Arguments.Count -gt 0)     { $startArgs.ArgumentList     = $Arguments }
    if ($WorkingDirectory)          { $startArgs.WorkingDirectory = $WorkingDirectory }

    $proc = Start-Process @startArgs

    # Reading .Handle CACHES the process handle in the .NET object, which is the
    # only thing that keeps .ExitCode readable after the process is gone: with
    # -PassThru and no -Wait, PowerShell 5.1 otherwise hands back an object whose
    # ExitCode is EMPTY -- not 0, empty -- so `-ne 0` was false and every failure
    # read as success. Verified 2026-09-18: npm.cmd exiting 1 reported no exit
    # code at all without this line, and 1 with it.
    $handleCached = $true
    try { $null = $proc.Handle } catch { $handleCached = $false }

    $timedOut = -not $proc.WaitForExit($TimeoutSeconds * 1000)
    if ($timedOut) {
        Write-Log -Message "timeout after ${TimeoutSeconds}s -- killing PID $($proc.Id)" -Level "Warn"
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
        # Give the redirect handles a moment to flush before the tail is read.
        Start-Sleep -Milliseconds 500
    }

    $output = @()
    foreach ($logFile in @($outLog, $errLog)) {
        if (Test-Path -LiteralPath $logFile) {
            $output += @(Get-Content -LiteralPath $logFile -ErrorAction SilentlyContinue)
        }
    }
    foreach ($line in $output) { Write-Log -Message ([string]$line) -Level "Debug" }

    # $null means "could not be determined" and is NOT the same as 0 -- callers
    # must fall back to checking the artifact the command was supposed to produce.
    $exit = $null
    if ($timedOut) {
        $exit = -1
    } elseif ($handleCached) {
        try { $proc.Refresh(); $exit = [int]$proc.ExitCode } catch { $exit = $null }
    }
    if ($null -eq $exit -and -not $timedOut) {
        Write-Log -Message "exit code of '$FilePath' could not be read -- judging by its output artifacts instead" -Level "Warn"
    }

    return [pscustomobject]@{
        ExitCode = $exit
        TimedOut = $timedOut
        Output   = $output
        Tail     = (@($output | Where-Object { $_ } | Select-Object -Last 20 |
                      ForEach-Object { "    $_" }) -join "`n")
    }
}

# A download that fails once is not a download that fails. On corporate networks
# (TLS-inspecting proxies) and on CDNs the FIRST handshake gets reset often
# enough to matter -- ".NET: the underlying connection was closed: an unexpected
# error occurred on a send" -- while the identical request seconds later
# succeeds. Both bootstrap downloads (uv, Node.js) therefore retry with a
# bounded linear backoff.
#
# EVERY attempt tries curl.exe AND, if that fails, Invoke-WebRequest -- because
# the two do not trust the same certificates, and which one works depends on the
# box rather than on the URL:
#   * Windows' own curl.exe (System32, 10 1803+) uses Schannel and the machine
#     certificate store, so an enterprise TLS-interception root installed by
#     policy is trusted.
#   * a curl.exe from Git for Windows -- which comes FIRST on PATH on any
#     developer box and is what Get-Command returns there -- is an OpenSSL build
#     with its own CA bundle, and it does NOT see that store. Under Zscaler/
#     equivalent it fails the handshake where .NET succeeds.
#   * conversely .NET sometimes resets where curl gets through, which is the
#     original reason curl is tried first.
# Picking one mechanism per box would trade one class of failure for another;
# the goal here is that a fresh box installs, so both are tried on every pass.
#
# curl is invoked as a native command with an argument array -- NOT by composing
# a command string for cmd.exe. That distinction is not stylistic: a built
# "<exe> ... > out 2> err" command line is a dropper shape, and on a managed
# image an AMSI verdict does not fail the download, it blocks and DELETES this
# whole script (measured 2026-09-18).
function Invoke-DownloadWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [int]$MaxAttempts = 4,
        [int]$TimeoutSeconds = 120
    )

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue

    $fetchers = New-Object System.Collections.ArrayList
    if ($curl) {
        $null = $fetchers.Add(@{
            Name   = "curl.exe ($($curl.Source))"
            Action = {
                $curlArgs = @("-fsSL", "--max-time", "$TimeoutSeconds", "-o", $OutFile, $Uri)
                $r = Invoke-NativeTimed -FilePath $curl.Source -Arguments $curlArgs `
                        -TimeoutSeconds ($TimeoutSeconds + 30)
                if ($r.TimedOut) { throw "timed out after ${TimeoutSeconds}s" }
                if ($r.ExitCode -ne 0) { throw "exited $($r.ExitCode)" }
            }
        })
    }
    $null = $fetchers.Add(@{
        Name   = "Invoke-WebRequest"
        Action = {
            Invoke-WebRequest -Uri $Uri -OutFile $OutFile `
                -UseBasicParsing -TimeoutSec $TimeoutSeconds -ErrorAction Stop
        }
    })

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $errors = @()
        foreach ($fetcher in $fetchers) {
            try {
                & $fetcher.Action
                # An empty file is a failed download that reported success.
                if (-not (Test-Path -LiteralPath $OutFile)) { throw "no file was written" }
                if ((Get-Item -LiteralPath $OutFile).Length -le 0) { throw "downloaded 0 bytes" }
                Write-Log -Message "Downloaded $Uri via $($fetcher.Name)" -Level "Debug"
                return
            } catch {
                Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
                $errors += "$($fetcher.Name): $($_.Exception.Message)"
            }
        }
        if ($attempt -ge $MaxAttempts) {
            throw "download failed after $MaxAttempts attempt(s) -- $($errors -join '; ')"
        }
        $backoff = [math]::Min(2 * $attempt, 10)
        Write-Log -Message "Download attempt $attempt/$MaxAttempts failed ($($errors -join '; ')); retrying in ${backoff}s" -Level "Warn"
        Start-Sleep -Seconds $backoff
    }
}

# -----------------------------------------------------------------------------
# Console readiness, port ownership, browser launch
# -----------------------------------------------------------------------------

# Deliberately HttpWebRequest and not Invoke-WebRequest:
#   * $req.Proxy = $null bypasses any machine-wide proxy. A corporate PAC file
#     that does not exclude 127.0.0.1 turns "the console is up" into a 407 from
#     the proxy -- which is why readiness used to be probed with a bare TCP
#     connect, and a bare TCP connect cannot tell a serving console apart from
#     one answering 503.
#   * a 4xx/5xx answer is DATA here, not an exception: the whole point is to
#     distinguish 200-with-SPA from 503-without-SPA from connection-refused.
function Invoke-LoopbackHttp {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 10,
        [switch]$NoRedirect
    )

    $result = @{
        Reached    = $false
        StatusCode = 0
        Body       = ""
        Location   = ""
        SetCookie  = ""
        Error      = ""
    }

    $resp = $null
    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Proxy            = $null
        $req.Timeout          = $TimeoutSeconds * 1000
        $req.ReadWriteTimeout = $TimeoutSeconds * 1000
        $req.AllowAutoRedirect = (-not $NoRedirect)
        $req.UserAgent        = "CorvinOS-Installer"
        $req.Method           = "GET"
        $resp = $req.GetResponse()
    } catch [System.Net.WebException] {
        # A WebException still carries the response for any HTTP status; it is
        # only absent when the connection itself never happened.
        $resp = $_.Exception.Response
        if (-not $resp) {
            $result.Error = $_.Exception.Message
            return $result
        }
    } catch {
        $result.Error = $_.Exception.Message
        return $result
    }

    try {
        $result.Reached    = $true
        $result.StatusCode = [int]$resp.StatusCode
        $result.Location   = [string]$resp.Headers["Location"]
        $result.SetCookie  = [string]$resp.Headers["Set-Cookie"]

        # Bounded read: the SPA shell is a few KB, but a proxy notice or a
        # streaming endpoint could be unbounded and this runs inside a poll loop.
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
        try {
            $buffer = New-Object char[] 8192
            $read = $reader.Read($buffer, 0, $buffer.Length)
            if ($read -gt 0) { $result.Body = (-join $buffer[0..($read - 1)]) }
        } finally {
            $reader.Close()
        }
    } catch {
        $result.Error = $_.Exception.Message
    } finally {
        try { $resp.Close() } catch { }
    }

    return $result
}

# Answers the only question that matters: does http://127.0.0.1:<port>/console/
# hand back the console SPA? Three outcomes are distinguished on purpose --
# nothing listening, listening but not serving the app, and serving.
function Get-ConsoleHealth {
    param([int]$TargetPort, [int]$TimeoutSeconds = 10)

    $health = @{
        Listening  = $false
        Serving    = $false
        SpaMissing = $false
        StatusCode = 0
        Detail     = ""
    }

    if (-not (Test-TcpPort -TargetPort $TargetPort -TimeoutMs 1000)) { return $health }
    $health.Listening = $true

    $response = Invoke-LoopbackHttp -Url "http://127.0.0.1:$TargetPort/console/" -TimeoutSeconds $TimeoutSeconds
    if (-not $response.Reached) {
        $health.Detail = "no HTTP answer: $($response.Error)"
        return $health
    }

    $health.StatusCode = $response.StatusCode

    if ($response.StatusCode -eq 503) {
        # mount_static() decides ONCE at boot: booting with no built SPA
        # registers the 503 fallback route, and a later rebuild does NOT
        # recover it -- only a restart does. So this is a distinct state, not
        # a transient one to keep polling.
        $health.SpaMissing = $true
        $health.Detail = "HTTP 503 -- the console booted without a built frontend"
        return $health
    }

    if ($response.StatusCode -ne 200) {
        $health.Detail = "HTTP $($response.StatusCode)"
        return $health
    }

    # A 200 is not proof. The port could be owned by an unrelated service, or
    # the answer could be a proxy notice. The SPA shell is identified by its
    # mount point, which is the one string every build of it contains.
    if ($response.Body -match '<div id="root"') {
        $health.Serving = $true
    } else {
        $health.Detail = "HTTP 200 but the body is not the console SPA shell"
    }

    return $health
}

# Get-NetTCPConnection is absent on some editions and throws when the port is
# free, so netstat is kept as the fallback that is always present.
function Get-PortOwnerProcess {
    param([int]$TargetPort)

    $ownerPids = New-Object System.Collections.ArrayList
    try {
        foreach ($conn in @(Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction Stop)) {
            if ($conn.OwningProcess) { $null = $ownerPids.Add([int]$conn.OwningProcess) }
        }
    } catch {
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            foreach ($line in @(& netstat.exe -ano 2>$null)) {
                if ([string]$line -match "^\s+TCP\s+\S+:$TargetPort\s+\S+\s+LISTENING\s+(\d+)\s*$") {
                    $null = $ownerPids.Add([int]$Matches[1])
                }
            }
        } catch {
        } finally {
            $ErrorActionPreference = $previousPreference
        }
    }

    $owners = New-Object System.Collections.ArrayList
    foreach ($ownerPid in @($ownerPids | Select-Object -Unique)) {
        $owner = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        if ($owner) { $null = $owners.Add($owner) }
    }
    return $owners
}

# The process holding the port is usually NOT named corvin-anything. serve_entry
# blocks on a uvicorn CHILD (`python -m uvicorn corvin_console.standalone:create_app
# --factory`), and that child -- a python.exe under the uv-managed toolchain --
# is the listener. Identify it by its command line, or the installer either
# refuses to touch its own stale console or, worse, kills a stranger's service.
function Test-IsCorvinConsoleProcess {
    param($Process)

    if (-not $Process) { return $false }
    if ($Process.ProcessName -like 'corvin*') { return $true }

    $commandLine = ""
    try {
        $commandLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId = $($Process.Id)" -ErrorAction Stop).CommandLine
    } catch {
        $commandLine = ""
    }
    if ($commandLine -match 'corvin') { return $true }

    try {
        if ($Process.Path -and $Process.Path.StartsWith($CorvinHome, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    } catch { }

    return $false
}

# Stops a stale CorvinOS console holding $TargetPort, and NOTHING else. Returns
# the number of processes stopped.
function Stop-ConsoleOnPort {
    param([int]$TargetPort)

    $stopped = 0
    foreach ($owner in (Get-PortOwnerProcess -TargetPort $TargetPort)) {
        if (-not (Test-IsCorvinConsoleProcess -Process $owner)) {
            Write-Log -Message "Port $TargetPort is held by $($owner.ProcessName) (PID $($owner.Id)), which is not CorvinOS -- leaving it alone" -Level "Warn"
            continue
        }
        Write-Log -Message "Stopping stale console $($owner.ProcessName) (PID $($owner.Id))" -Level "Info"

        # Kill the TREE. serve_entry blocks on a uvicorn child, so stopping only
        # the parent leaves the child holding the port forever -- observed
        # 2026-09-18, a python.exe kept :8801 open after its launcher was gone,
        # which then read to the next run as "already serving".
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { $null = & taskkill.exe /PID $owner.Id /T /F 2>&1 } catch { } finally { $ErrorActionPreference = $previousPreference }
        try { Stop-Process -Id $owner.Id -Force -ErrorAction SilentlyContinue } catch { }
        $stopped++
    }

    if ($stopped -gt 0) {
        # Process teardown is not instant; the next bind fails if we race it.
        for ($i = 0; $i -lt 20; $i++) {
            if (-not (Test-TcpPort -TargetPort $TargetPort -TimeoutMs 500)) { break }
            Start-Sleep -Milliseconds 500
        }
    }
    return $stopped
}

# Stops every process whose EXECUTABLE lives in the corvinos tool venv, which is
# the precise set that makes `uv tool install --force` fail on Windows: uv
# replaces that directory, and a running image inside it cannot be deleted.
# CIM rather than Get-Process because .Path is unreadable for some processes
# while Win32_Process.ExecutablePath still resolves.
function Stop-ToolVenvProcess {
    $stopped = 0
    try {
        foreach ($entry in @(Get-CimInstance Win32_Process -ErrorAction Stop)) {
            $exe = [string]$entry.ExecutablePath
            if (-not $exe) { continue }
            if (-not $exe.StartsWith($UvToolDir, [StringComparison]::OrdinalIgnoreCase)) { continue }
            if ([int]$entry.ProcessId -eq $PID) { continue }

            Write-Log -Message "Stopping $($entry.Name) (PID $($entry.ProcessId)) running from the tool venv" -Level "Info"
            $previousPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try { $null = & taskkill.exe /PID $entry.ProcessId /T /F 2>&1 } catch { } finally { $ErrorActionPreference = $previousPreference }
            Stop-Process -Id ([int]$entry.ProcessId) -Force -ErrorAction SilentlyContinue
            $stopped++
        }
    } catch {
        Write-Log -Message "Could not enumerate processes: $($_.Exception.Message)" -Level "Warn"
    }
    return $stopped
}

# Turns captured server output into one actionable sentence. Every branch here
# is a failure that has actually happened on a Windows box.
function Get-ConsoleFailureHint {
    param([string[]]$Lines)

    $blob = (@($Lines) -join "`n")
    if ($blob -match 'tripwire|audit_chain_intact|chain_replaced|tail_truncated') {
        return @(
            "The boot tripwire refused to start: the audit chain under CORVIN_HOME does",
            "not match its out-of-tree anchor in $env:USERPROFILE\.config\corvin-voice\.",
            "This is fail-closed by design and has no override flag or env var.",
            "It cannot happen on a fresh machine -- it means this CORVIN_HOME was copied,",
            "restored from a backup, or edited in place.",
            "To install alongside it, point CORVIN_HOME at a new directory and re-run:",
            "    `$env:CORVIN_HOME = `"`$env:USERPROFILE\.corvin-new`"",
            "KEEP the existing chain either way: it is the append-only audit record and",
            "must not be deleted or rewritten to make the check pass."
        )
    }
    if ($blob -match 'Address already in use|WinError 10048|only one usage of each socket') {
        return @("Port $Port was taken between the check and the start. Re-run with -Port <other port>.")
    }
    if ($blob -match 'ModuleNotFoundError|ImportError|cannot import name') {
        return @(
            "A Python import failed before the server came up, so the install is incomplete.",
            "The failing module is named in the lines above. Re-run the installer; if it",
            "repeats, that module is the bug."
        )
    }
    if ($blob -match 'npm ERR|vite|error TS[0-9]') {
        return @(
            "The console tried to build its frontend while starting, and the build failed.",
            "Re-run the installer with -RebuildWeb to see the build output in full."
        )
    }
    return @()
}

# Three mechanisms, because each one fails on a real machine:
#   1. Start-Process uses the http:// association, which is absent on a stripped
#      image and dangling when it points at a removed browser.
#   2. explorer.exe reaches the same association through the shell and survives
#      some broken ProgId registrations.
#   3. cmd /c start "" <url> -- the empty title argument is MANDATORY, or cmd
#      treats the URL as the window title and opens nothing at all.
# Last resort: a known browser binary invoked directly, which needs no
# association whatsoever.
#
# NOT used: rundll32.exe url.dll,FileProtocolHandler. It is the Win32 primitive
# the three above wrap, so it would be the natural fallback -- but a script
# invoking rundll32 with a URL is a textbook LOLBin pattern, and on this class
# of managed Windows image an AMSI verdict blocks the WHOLE script (measured
# 2026-09-18 on a different construct in this same file). The explicit browser
# paths below already cover the case where every association is broken.
function Open-InBrowser {
    param([Parameter(Mandatory = $true)][string]$Url)

    $attempts = @(
        @{ Name = "default browser"; Action = { Start-Process -FilePath $Url -ErrorAction Stop } }
        @{ Name = "explorer.exe";    Action = { Start-Process -FilePath "explorer.exe" -ArgumentList $Url -ErrorAction Stop } }
        @{ Name = "cmd start";       Action = { Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "start", '""', $Url) -WindowStyle Hidden -ErrorAction Stop } }
    )

    foreach ($attempt in $attempts) {
        try {
            & $attempt.Action
            Write-Log -Message "Opened $Url via $($attempt.Name)" -Level "Success"
            return $true
        } catch {
            Write-Log -Message "Browser open via $($attempt.Name) failed: $($_.Exception.Message)" -Level "Debug"
        }
    }

    # ${env:ProgramFiles(x86)} is a real variable name; "$env:ProgramFiles (x86)"
    # only looks like one and resolves by accident.
    $browsers = @(
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
        "$env:ProgramFiles\Mozilla Firefox\firefox.exe"
        "${env:ProgramFiles(x86)}\Mozilla Firefox\firefox.exe"
    )
    foreach ($browser in $browsers) {
        if (-not (Test-Path -LiteralPath $browser)) { continue }
        try {
            Start-Process -FilePath $browser -ArgumentList $Url -ErrorAction Stop
            Write-Log -Message "Opened $Url with $(Split-Path -Leaf $browser)" -Level "Success"
            return $true
        } catch {
            Write-Log -Message "Browser open via $browser failed: $($_.Exception.Message)" -Level "Debug"
        }
    }

    return $false
}

trap {
    $script:InstallationFailed = $true
    Write-Log -Message "FATAL: $_" -Level "Error"
    Write-Log -Message "At: $($_.InvocationInfo.PositionMessage)" -Level "Error"
    Write-Log -Message "Stack trace: $($_.ScriptStackTrace)" -Level "Debug"
    Write-Summary -Status "FAILED (see error log)" -ExitCode 1
    exit 1
}

# -----------------------------------------------------------------------------
# Phase 1: Pre-checks
# -----------------------------------------------------------------------------

Write-Header "Phase 1: Pre-Checks and Prerequisites"

if ($DryRun) {
    Write-Log -Message "DRY RUN -- no changes will be made" -Level "Info"
}

Write-Step "Checking PowerShell version"
$psVersion = $PSVersionTable.PSVersion
if ($psVersion -lt [version]"5.1") {
    Stop-WithError "PowerShell $psVersion is too old (need 5.1+)" 2
}
Write-Log -Message "PowerShell version: $psVersion" -Level "Success"

Write-Step "Checking Windows version"
$osInfo = Get-CimInstance -ClassName Win32_OperatingSystem
$osVersion = [version]$osInfo.Version
if ($osVersion.Major -lt 10) {
    Stop-WithError "Windows $osVersion is too old (need Windows 10+)" 2
}
Write-Log -Message "Windows version: $osVersion ($($osInfo.Caption))" -Level "Success"

Write-Step "Checking prerequisites"
$missingPrereqs = @()
foreach ($cmd in @("curl", "git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        $missingPrereqs += $cmd
    }
}
if ($missingPrereqs.Count -gt 0) {
    Stop-WithError "Missing prerequisites: $($missingPrereqs -join ', ')" 2
}
Write-Log -Message "All prerequisites found" -Level "Success"

# -----------------------------------------------------------------------------
# Phase 2: Path validation
# -----------------------------------------------------------------------------

Write-Header "Phase 2: Path Validation"

Write-Step "Resolving repository path"
# An empty -Editable means "install from PyPI", but the repo path is still
# needed for .nvmrc and the component bootstrap scripts.
$EditableMode = -not [string]::IsNullOrWhiteSpace($Editable)
$RepoCandidate = if ($EditableMode) { $Editable } else { $PSScriptRoot }

if (-not (Test-Path -LiteralPath $RepoCandidate -PathType Container)) {
    Stop-WithError "Repository path does not exist: $RepoCandidate" 4 "FAILED (bad -Editable path)"
}

# Trailing separators must go: uv is handed "<path>[browser]" and
# "C:\repo\[browser]" is not a valid requirement specifier.
$RepoPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepoCandidate).Path)
if ($RepoPath.Length -gt 3) { $RepoPath = $RepoPath.TrimEnd('\', '/') }
Write-Log -Message "Repository path: $RepoPath" -Level "Success"
if ($EditableMode) {
    Write-Log -Message "Mode: EDITABLE (local clone)" -Level "Info"
} else {
    Write-Log -Message "Mode: PyPI ($PackageName >= $CorvinMinVersion)" -Level "Info"
}

Write-Step "Verifying CorvinOS repository structure"
$requiredFiles = @("install.ps1", "install.sh", "pyproject.toml", "package.json")
$missingFiles = @($requiredFiles | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $RepoPath $_))
})
if ($missingFiles.Count -gt 0) {
    if ($EditableMode) {
        Stop-WithError "Not a CorvinOS clone -- missing: $($missingFiles -join ', ')" 4 "FAILED (bad -Editable path)"
    }
    Write-Log -Message "Repository files missing ($($missingFiles -join ', ')); component bootstrap will be skipped" -Level "Warn"
} else {
    Write-Log -Message "Repository structure verified" -Level "Success"
}

# -----------------------------------------------------------------------------
# Phase 3: System requirements
# -----------------------------------------------------------------------------

Write-Header "Phase 3: System Requirements"

Write-Step "Checking available disk space"
# DriveInfo handles UNC and mapped paths; Get-Volume -DriveLetter does not.
try {
    $driveInfo = New-Object System.IO.DriveInfo([System.IO.Path]::GetPathRoot($RepoPath))
    if ($driveInfo.IsReady) {
        $freeGB = [math]::Round($driveInfo.AvailableFreeSpace / 1GB, 2)
        if ($freeGB -lt 0.5) {
            Stop-WithError "Insufficient disk space: ${freeGB}GB available (need 500MB+)" 2
        }
        Write-Log -Message "Available disk space: ${freeGB}GB" -Level "Success"
    }
} catch {
    Write-Log -Message "Could not determine free disk space: $_" -Level "Warn"
}

Write-Step "Checking long path support"
$regPath = "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem"
$longPathEnabled = $false
try {
    $regValue = Get-ItemProperty -Path $regPath -Name LongPathsEnabled -ErrorAction SilentlyContinue
    $longPathEnabled = ($regValue.LongPathsEnabled -eq 1)
} catch { }
if ($longPathEnabled) {
    Write-Log -Message "Long path support enabled" -Level "Success"
} else {
    Write-Log -Message "Long path support disabled (non-critical; deep node_modules paths may fail)" -Level "Warn"
    Write-Log -Message "To enable (needs admin): Set-ItemProperty -Path '$regPath' -Name LongPathsEnabled -Value 1 -Force" -Level "Info"
}

Write-Step "Checking network connectivity"
try {
    $null = Invoke-WebRequest -Uri "https://github.com" -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
    Write-Log -Message "Network connectivity verified" -Level "Success"
} catch {
    # Deliberately NOT bypassing certificate validation here. A corporate TLS
    # proxy re-signs traffic with a root that is already in the Windows store,
    # which Invoke-WebRequest uses; disabling validation would hide a real MITM
    # instead of fixing anything.
    Write-Log -Message "Network check failed: $($_.Exception.Message)" -Level "Warn"
    Write-Log -Message "Downloads (uv, Node.js, PyPI) will fail if there is no internet access" -Level "Warn"
}

# -----------------------------------------------------------------------------
# Phase 4: Environment
# -----------------------------------------------------------------------------

Write-Header "Phase 4: Environment Setup"

# Mirrors core/paths/tenant.py::corvin_home and the other paths.py copies, in
# that order: $CORVIN_HOME, else a repo-local .corvin IF IT ALREADY EXISTS (a
# source checkout's live root), else ~/.corvin. The order is load-bearing, not
# cosmetic: the installer exports CORVIN_HOME for the console it starts itself,
# so a divergence here is invisible during the install and shows up later, when
# the operator runs `corvinos-serve` from a plain shell and Python picks the
# repo-local root the installer never prepared or verified. Note the asymmetry
# with the Python side -- a repo-local root is ADOPTED, never created, so a
# fresh clone still installs into the home directory.
$RepoLocalCorvinHome = Join-Path $RepoPath ".corvin"
$CorvinHome = if ($env:CORVIN_HOME) {
    $env:CORVIN_HOME
} elseif (Test-Path -LiteralPath $RepoLocalCorvinHome -PathType Container) {
    $RepoLocalCorvinHome
} else {
    Join-Path $env:USERPROFILE ".corvin"
}
$NodeRoot = Join-Path $CorvinHome "node"
# uv-installer.ps1 installs into %USERPROFILE%\.local\bin (or UV_INSTALL_DIR).
$UvBin    = Join-Path $env:USERPROFILE ".local\bin"
$CargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
# The tool venv `uv tool install` writes to. It lives under APPDATA, NOT under
# CORVIN_HOME, so nothing that keys on CorvinHome ever sees a process running
# from it -- and on Windows a single running process there makes `--force`
# reinstall fail outright with "Access is denied" (see phase 9).
$UvToolDir = if ($env:UV_TOOL_DIR) {
    Join-Path $env:UV_TOOL_DIR $PackageName
} else {
    Join-Path $env:APPDATA "uv\tools\$PackageName"
}

Write-Step "Preparing CORVIN_HOME"
if (-not (Test-Path -LiteralPath $CorvinHome)) {
    if ($DryRun) {
        Write-Log -Message "[DRY RUN] Would create $CorvinHome" -Level "Info"
    } else {
        $null = New-Item -ItemType Directory -Path $CorvinHome -Force -ErrorAction Stop
        Write-Log -Message "Created CORVIN_HOME: $CorvinHome" -Level "Success"
    }
} else {
    Write-Log -Message "CORVIN_HOME exists: $CorvinHome" -Level "Info"
}
$env:CORVIN_HOME = $CorvinHome
$env:CORVIN_INSTALL_LOG = $MainLogFile
$env:UV_INSTALL_DIR = $UvBin

Write-Step "Extending PATH for this session"
# Node.js on Windows ships node.exe / npm.cmd at the archive ROOT -- there is no
# bin/ subdirectory as on Linux/macOS.
$pathEntries = @($UvBin, $CargoBin, $NodeRoot) + @($env:PATH -split ';')
$env:PATH = (@($pathEntries | Where-Object { $_ }) | Select-Object -Unique) -join ';'
Write-Log -Message "PATH prepended with: $UvBin; $CargoBin; $NodeRoot" -Level "Success"

# -----------------------------------------------------------------------------
# Phase 5: Stale-state detection
# -----------------------------------------------------------------------------

Write-Header "Phase 5: Stale-State Detection"

Write-Step "Checking for running CorvinOS processes"
try {
    # Scoped ON PURPOSE. An earlier revision force-killed every python.exe,
    # node.exe and uv.exe on the machine, which takes out editors, language
    # servers and unrelated work. Only processes that ARE CorvinOS (by name),
    # that run FROM CorvinHome, or that run from the corvinos TOOL VENV qualify.
    #
    # That third clause is the one phase 9 depends on. A long-lived bridge
    # (`<tool venv>\Scripts\python.exe corvin_operator\bridges\shared\adapter.py`)
    # is named "python", so the name test misses it, and it lives under APPDATA,
    # so the CorvinHome test misses it too -- while holding the very directory
    # `uv tool install --force` has to delete. Measured 2026-09-18: two such
    # processes, and the install died with
    # "failed to remove directory ...\corvinos\Scripts: Access is denied".
    $candidates = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Id -ne $PID -and (
            $_.ProcessName -like 'corvin*' -or
            ($_.Path -and $_.Path.StartsWith($CorvinHome, [StringComparison]::OrdinalIgnoreCase)) -or
            ($_.Path -and $_.Path.StartsWith($UvToolDir, [StringComparison]::OrdinalIgnoreCase))
        )
    })

    if ($candidates.Count -gt 0) {
        Write-Log -Message "Found $($candidates.Count) CorvinOS process(es) holding file locks" -Level "Warn"
        foreach ($proc in $candidates) {
            if ($DryRun) {
                Write-Log -Message "[DRY RUN] Would stop $($proc.ProcessName) (PID $($proc.Id))" -Level "Info"
            } else {
                Write-Log -Message "Stopping $($proc.ProcessName) (PID $($proc.Id))" -Level "Info"
                # /T -- the tree. corvinos-serve BLOCKS on a uvicorn child, and
                # that child is the one holding the console port; stopping the
                # parent alone orphans a listener that then reads to phase 13 as
                # "a console is already serving" while running the OLD code.
                $previousPreference = $ErrorActionPreference
                $ErrorActionPreference = "Continue"
                try { $null = & taskkill.exe /PID $proc.Id /T /F 2>&1 } catch { } finally { $ErrorActionPreference = $previousPreference }
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        }
    } else {
        Write-Log -Message "No CorvinOS processes running" -Level "Success"
    }

    # The tree kill above only reaches children of a process we could name. A
    # console orphaned by an earlier run has no CorvinOS-named ancestor left, so
    # it is found by who owns the port instead -- and only stopped if it really
    # is ours (Test-IsCorvinConsoleProcess).
    if (-not $DryRun) {
        $orphans = Stop-ConsoleOnPort -TargetPort $Port
        if ($orphans -gt 0) {
            Write-Log -Message "Stopped $orphans orphaned console process(es) on port $Port" -Level "Info"
        }
    }
} catch {
    Write-Log -Message "Process check failed: $_" -Level "Warn"
}

Write-Step "Checking for conflicting HTTP packages"
if (Get-Command pip -ErrorAction SilentlyContinue) {
    try {
        $pipList = & pip list 2>&1
        $dupes = @("httpcore2", "httpx2") | Where-Object {
            $pipList | Select-String -SimpleMatch $_ -Quiet
        }
        if ($dupes.Count -gt 0) {
            Write-Log -Message "Conflicting packages present ($($dupes -join ', ')) -- these break A2A connectivity" -Level "Warn"
            foreach ($pkg in $dupes) {
                if ($DryRun) {
                    Write-Log -Message "[DRY RUN] Would uninstall $pkg" -Level "Info"
                } else {
                    $null = Invoke-Native -FilePath "pip" -Arguments @("uninstall", $pkg, "-y") -IgnoreExitCode
                    Write-Log -Message "Removed $pkg" -Level "Success"
                }
            }
        } else {
            Write-Log -Message "No conflicting HTTP packages found" -Level "Success"
        }
    } catch {
        Write-Log -Message "Could not inspect pip packages: $_" -Level "Warn"
    }
} else {
    Write-Log -Message "pip not on PATH (expected -- uv manages its own Python); skipping" -Level "Info"
}

# -----------------------------------------------------------------------------
# Phase 6: Bootstrap uv
# -----------------------------------------------------------------------------

Write-Header "Phase 6: Bootstrap uv (Python Manager)"

Write-Step "Ensuring uv is installed"
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCmd) {
    $uvVersionText = (& $uvCmd.Source --version 2>&1) -join ' '
    Write-Log -Message "uv already installed: $uvVersionText ($($uvCmd.Source))" -Level "Success"
} elseif ($DryRun) {
    Write-Log -Message "[DRY RUN] Would download and run $UvInstallerUrl" -Level "Info"
} else {
    $uvInstallerPath = Join-Path $env:TEMP "uv-installer-$UvVersion-$InstallTimestamp.ps1"
    try {
        Write-Log -Message "Downloading uv $UvVersion installer..." -Level "Info"
        Invoke-DownloadWithRetry -Uri $UvInstallerUrl -OutFile $uvInstallerPath -TimeoutSeconds 120

        # Verify BEFORE executing: this script is about to be run as code.
        $hash = (Get-FileHash -Path $uvInstallerPath -Algorithm SHA256).Hash
        if ($hash -ne $UvInstallerSha256.ToUpper()) {
            Remove-Item -LiteralPath $uvInstallerPath -Force -ErrorAction SilentlyContinue
            Stop-WithError "uv installer SHA256 mismatch (expected $UvInstallerSha256, got $($hash.ToLower())) -- refusing to run it" 1 "FAILED (supply-chain check)"
        }
        Write-Log -Message "uv installer checksum verified" -Level "Success"

        Write-Log -Message "Running uv installer..." -Level "Info"
        $null = Invoke-Native -FilePath "powershell" `
            -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $uvInstallerPath)
    } catch {
        Stop-WithError "uv installation failed: $_" 1
    } finally {
        Remove-Item -LiteralPath $uvInstallerPath -Force -ErrorAction SilentlyContinue
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Stop-WithError "uv is not on PATH after install (looked in $UvBin). Open a new terminal and re-run." 1
    }
    Write-Log -Message "uv installed" -Level "Success"
}

# -----------------------------------------------------------------------------
# Phase 7: Bootstrap Node.js
# -----------------------------------------------------------------------------

Write-Header "Phase 7: Bootstrap Node.js Runtime"

Write-Step "Ensuring Node.js is installed"
$nvmrcPath = Join-Path $RepoPath ".nvmrc"
$nodeVersion = if (Test-Path -LiteralPath $nvmrcPath) {
    ((Get-Content -LiteralPath $nvmrcPath -Raw).Trim() -replace '^v', '')
} else {
    $DefaultNodeVersion
}
$nodeArch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
$nodeExe  = Join-Path $NodeRoot "node.exe"

$nodeInstalled = $false
if (Test-Path -LiteralPath $nodeExe) {
    $installedNodeVersion = ((& $nodeExe --version 2>&1) -join '').Trim() -replace '^v', ''
    if ($installedNodeVersion -eq $nodeVersion) {
        Write-Log -Message "Node.js v$nodeVersion already installed at $NodeRoot" -Level "Success"
        $nodeInstalled = $true
    } else {
        Write-Log -Message "Node.js v$installedNodeVersion present but v$nodeVersion requested -- replacing" -Level "Info"
    }
}

if (-not $nodeInstalled) {
    $nodeUrl = "https://nodejs.org/dist/v$nodeVersion/node-v$nodeVersion-win-$nodeArch.zip"
    if ($DryRun) {
        Write-Log -Message "[DRY RUN] Would download and extract $nodeUrl" -Level "Info"
    } else {
        # Extract into a run-unique staging directory. Extracting straight into
        # $env:TEMP and renaming to "node" collides with leftovers from a
        # previous run and fails the whole phase.
        $nodeStage = Join-Path $env:TEMP "corvinos-node-$InstallTimestamp"
        $nodeZip   = Join-Path $env:TEMP "node-v$nodeVersion-win-$nodeArch-$InstallTimestamp.zip"
        try {
            Write-Log -Message "Downloading Node.js v$nodeVersion ($nodeArch)..." -Level "Info"
            Invoke-DownloadWithRetry -Uri $nodeUrl -OutFile $nodeZip -TimeoutSeconds 300

            Write-Log -Message "Extracting Node.js..." -Level "Info"
            $null = New-Item -ItemType Directory -Path $nodeStage -Force -ErrorAction Stop
            Expand-Archive -LiteralPath $nodeZip -DestinationPath $nodeStage -Force -ErrorAction Stop

            $extracted = @(Get-ChildItem -LiteralPath $nodeStage -Directory)
            if ($extracted.Count -ne 1) {
                throw "unexpected Node.js archive layout ($($extracted.Count) top-level directories)"
            }

            if (Test-Path -LiteralPath $NodeRoot) {
                Remove-Item -LiteralPath $NodeRoot -Recurse -Force -ErrorAction Stop
            }
            Move-Item -LiteralPath $extracted[0].FullName -Destination $NodeRoot -Force -ErrorAction Stop

            if (-not (Test-Path -LiteralPath $nodeExe)) {
                throw "node.exe not found at $nodeExe after extraction"
            }
            $verifiedVersion = ((& $nodeExe --version 2>&1) -join '').Trim()
            Write-Log -Message "Node.js $verifiedVersion installed at $NodeRoot" -Level "Success"
        } catch {
            Stop-WithError "Node.js installation failed: $_" 1
        } finally {
            Remove-Item -LiteralPath $nodeZip -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $nodeStage -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# -----------------------------------------------------------------------------
# Phase 8: Claude Code detection
# -----------------------------------------------------------------------------

Write-Header "Phase 8: Claude Code Detection"

Write-Step "Detecting Claude Code"
if ($NoClaudeCode) {
    Write-Log -Message "Skipped (-NoClaudeCode)" -Level "Info"
} else {
    $claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
    if ($claudeCmd) {
        Write-Log -Message "Claude Code found at $($claudeCmd.Source)" -Level "Success"
    } else {
        # Not auto-executed: piping a remote script into the shell is exactly
        # the pattern the pinned+checksummed uv bootstrap above exists to avoid.
        Write-Log -Message "Claude Code not found (optional -- the ClaudeCodeEngine worker needs it)" -Level "Warn"
        Write-Log -Message "To install: irm https://claude.ai/install.ps1 | iex" -Level "Info"
    }
}

# -----------------------------------------------------------------------------
# Phase 9: Install the corvinos package
# -----------------------------------------------------------------------------

Write-Header "Phase 9: Install CorvinOS Package"

Write-Step "Installing corvinos via uv"
# Built as an argument ARRAY. The previous revision used
# Invoke-Expression "uv tool install --force corvinos>=2.0.0", where PowerShell
# parses '>' as a redirection operator and wrote uv's output into a file named
# '=2.0.0' -- so the version floor was silently dropped.
$uvArgs = @("tool", "install", "--force")
if ($EditableMode) {
    $uvArgs += @("--editable", ("{0}[browser]" -f $RepoPath))
} else {
    $uvArgs += @("--refresh", ("{0}[browser]>={1}" -f $PackageName, $CorvinMinVersion))
}

if ($DryRun) {
    Write-Log -Message "[DRY RUN] Would run: uv $($uvArgs -join ' ')" -Level "Info"
} else {
    Write-Log -Message "Running: uv $($uvArgs -join ' ')" -Level "Info"
    Write-Log -Message "This can take several minutes on a first install..." -Level "Info"

    $installAttempt = 0
    while ($true) {
        $installAttempt++
        try {
            $null = Invoke-Native -FilePath "uv" -Arguments $uvArgs
            Write-Log -Message "corvinos installed" -Level "Success"
            break
        } catch {
            $failure = [string]$_
            # On Windows the ENTIRE install fails if any process still runs from
            # the tool venv -- uv deletes that directory and an open image cannot
            # be removed. Phase 5 stops the ones it can see; one started since
            # then (or one whose path it could not read) lands here, where the
            # error names the cause, so it is resolved and retried once instead
            # of ending the run with a bare "Access is denied".
            $isLock = $failure -match 'Access is denied|os error 5|failed to remove directory|being used by another process'
            if ($isLock -and $installAttempt -eq 1) {
                Write-Log -Message "The tool directory is locked by a running process -- stopping it and retrying once" -Level "Warn"
                $freed = Stop-ToolVenvProcess
                Write-Log -Message "Stopped $freed process(es) holding $UvToolDir" -Level "Info"
                Start-Sleep -Seconds 2
                continue
            }
            if ($isLock) {
                Write-Log -Message "$UvToolDir is still locked. Close every CorvinOS process and any terminal running one, then re-run." -Level "Error"
            }
            Stop-WithError "corvinos installation failed: $failure" 1
        }
    }

    # Persists the uv tool bin directory on the user PATH for future terminals.
    $null = Invoke-Native -FilePath "uv" -Arguments @("tool", "update-shell") -IgnoreExitCode

    if (-not (Get-Command corvinos-serve -ErrorAction SilentlyContinue)) {
        Stop-WithError "install succeeded but 'corvinos-serve' is not on PATH (looked in $UvBin) -- open a new terminal and re-run" 1
    }
    Write-Log -Message "corvinos-serve is on PATH" -Level "Success"
}

# -----------------------------------------------------------------------------
# Phase 10: Component bootstrap
# -----------------------------------------------------------------------------

Write-Header "Phase 10: Bootstrap Components"

Write-Step "Checking component bootstrap scripts"
# DELIBERATELY NOT EXECUTED ON WINDOWS. core/{console,gateway,compliance}/bootstrap.sh
# each drive their venv through "${VENV_DIR}/bin/python", which only exists on
# POSIX -- a Windows venv puts the interpreter in Scripts/. Their _venv_usable()
# probe therefore always fails, and the recreate guard fires on the pyvenv.cfg
# that a perfectly good Windows venv has: the script would DELETE a working venv
# and then abort with "no usable pip". Running them here is destructive, not
# merely useless. The corvinos package installed in Phase 9 already carries these
# dependencies, so nothing is missing on Windows.
$componentScripts = @(
    "core/console/bootstrap.sh",
    "core/gateway/bootstrap.sh",
    "core/compliance/bootstrap.sh"
) | Where-Object { Test-Path -LiteralPath (Join-Path $RepoPath $_) }

if ($componentScripts.Count -gt 0) {
    Write-Log -Message "Skipping $($componentScripts.Count) POSIX-only bootstrap script(s) -- they assume a bin/python venv layout" -Level "Info"
    Write-Log -Message "Dependencies come from the corvinos package instead. For the POSIX venvs use install.sh under WSL." -Level "Info"
} else {
    Write-Log -Message "No component bootstrap scripts present" -Level "Info"
}

# -----------------------------------------------------------------------------
# Phase 10b: Build the console SPA
# -----------------------------------------------------------------------------
#
# THE CONSOLE UI DOES NOT EXIST UNTIL THIS RUNS. web-next/dist is gitignored and
# is not tracked, so a fresh clone installed with -Editable has no SPA at all:
# mount_static() then registers the 503 "build failed" fallback instead of the
# SPA mount and decides that ONCE, at boot. A wheel from PyPI ships a pre-built
# dist (sdist force-include), which is why only the editable path was affected.
#
# Until 2026-09-18 this phase did not exist and the only build path was the
# auto-build inside the server's own lifespan: capture_output=True, a 300s cap
# for `npm install` plus `npm run build` (the build alone measures ~105s here),
# and its failure surfaced as one log line while the installer went on to report
# SUCCESS and open a browser onto a dead console. Building here instead makes
# the failure loud, diagnosable and fixable before anything is started.

Write-Header "Phase 10b: Build the Console SPA"

Write-Step "Building the console frontend"

# The directory the RUNNING console will serve from. In editable mode that is
# the clone; a PyPI install serves the copy inside the installed package, which
# already carries dist/ -- so there is normally nothing to do there.
$WebNextDir = Join-Path $RepoPath "core\console\corvin_console\web-next"
$WebDistIndex = Join-Path $WebNextDir "dist\index.html"

if (-not $EditableMode) {
    Write-Log -Message "PyPI mode: the wheel ships a pre-built SPA -- nothing to build" -Level "Info"
} elseif (-not (Test-Path -LiteralPath (Join-Path $WebNextDir "package.json"))) {
    Write-Log -Message "No web-next/package.json under $RepoPath -- skipping the SPA build" -Level "Warn"
} elseif ($DryRun) {
    Write-Log -Message "[DRY RUN] Would run npm install + npm run build in $WebNextDir" -Level "Info"
} elseif ((Test-Path -LiteralPath $WebDistIndex) -and -not $RebuildWeb) {
    Write-Log -Message "SPA already built ($WebDistIndex) -- re-run with -RebuildWeb to force" -Level "Success"
} else {
    # npm.cmd sits at the ROOT of the Windows Node archive, next to node.exe.
    $npmCmd = Join-Path $NodeRoot "npm.cmd"
    if (-not (Test-Path -LiteralPath $npmCmd)) {
        $fallbackNpm = Get-Command npm -ErrorAction SilentlyContinue
        if ($fallbackNpm) {
            $npmCmd = $fallbackNpm.Source
            Write-Log -Message "Using npm from PATH: $npmCmd" -Level "Info"
        } else {
            Stop-WithError "npm not found (looked for $npmCmd and on PATH) -- cannot build the console SPA" 2 "FAILED (no npm)"
        }
    }

    # rollup peaks well above Node's default old-space on this bundle; an OOM
    # here looks like an unexplained non-zero exit. A cap is not a reservation,
    # so raising it is safe on a small machine.
    if (-not $env:NODE_OPTIONS) { $env:NODE_OPTIONS = "--max-old-space-size=4096" }

    $depsPresent = Test-Path -LiteralPath (Join-Path $WebNextDir "node_modules")
    $lockPresent = Test-Path -LiteralPath (Join-Path $WebNextDir "package-lock.json")

    function Install-WebDeps {
        # `npm ci` is reproducible but DELETES node_modules first; it is only
        # correct with a lockfile. Without one it errors out, so fall back.
        $verb = if ($lockPresent) { @("ci") } else { @("install") }
        Write-Log -Message "Running npm $($verb -join ' ') in $WebNextDir (this can take several minutes)..." -Level "Info"
        $r = Invoke-NativeTimed -FilePath $npmCmd -Arguments $verb `
            -WorkingDirectory $WebNextDir -TimeoutSeconds 900
        if ($r.TimedOut) {
            Write-Log -Message "npm $($verb -join ' ') timed out after 900s" -Level "Warn"
        } elseif (($null -ne $r.ExitCode) -and ($r.ExitCode -ne 0)) {
            Write-Log -Message "npm $($verb -join ' ') exited with code $($r.ExitCode)" -Level "Warn"
        } else {
            Write-Log -Message "Frontend dependencies installed" -Level "Success"
        }
        return $r
    }

    function Build-WebBundle {
        Write-Log -Message "Running npm run build in $WebNextDir..." -Level "Info"
        return Invoke-NativeTimed -FilePath $npmCmd -Arguments @("run", "build") `
            -WorkingDirectory $WebNextDir -TimeoutSeconds 1800
    }

    $installResult = $null
    if (-not $depsPresent) { $installResult = Install-WebDeps }

    $buildResult = Build-WebBundle

    # A stale or partial node_modules is the common cause of a first-build
    # failure (an interrupted earlier install, or a dependency added since).
    # Retry ONCE with a dependency install -- but only if we have not just run
    # one, so a genuinely broken build fails in one pass instead of two.
    $buildFailed = ($null -ne $buildResult.ExitCode) -and ($buildResult.ExitCode -ne 0)
    if ($buildFailed -and ($null -eq $installResult)) {
        Write-Log -Message "Build failed with node_modules already present -- reinstalling dependencies and retrying once" -Level "Warn"
        $installResult = Install-WebDeps
        $buildResult = Build-WebBundle
        $buildFailed = ($null -ne $buildResult.ExitCode) -and ($buildResult.ExitCode -ne 0)
    }

    if ($buildResult.TimedOut) {
        Stop-WithError ("The console SPA build timed out after 1800s.`n" +
            "  Build it by hand to see where it stalls:`n" +
            "    cd $WebNextDir`n    npm install`n    npm run build`n" +
            "  (full output: $DebugLogFile)") 1 "FAILED (SPA build timed out)"
    }
    if ($buildFailed) {
        Stop-WithError ("The console SPA build failed (exit $($buildResult.ExitCode)).`n" +
            "  The corvinos package IS installed -- only the browser UI is missing.`n" +
            "  Last lines:`n$($buildResult.Tail)`n" +
            "  Retry with:  cd $WebNextDir; npm install; npm run build`n" +
            "  (full output: $DebugLogFile)") 1 "FAILED (SPA build)"
    }

    # "Exit 0" is not the same as "there is a bundle". vite EMPTIES dist/ before
    # writing it, so a half-finished run leaves the directory present and empty
    # -- which every dist.exists() style probe reads as built.
    $builtAssets = @()
    if (Test-Path -LiteralPath (Join-Path $WebNextDir "dist\assets")) {
        $builtAssets = @(Get-ChildItem -LiteralPath (Join-Path $WebNextDir "dist\assets") `
            -Filter "*.js" -ErrorAction SilentlyContinue)
    }
    if ((-not (Test-Path -LiteralPath $WebDistIndex)) -or $builtAssets.Count -eq 0) {
        Stop-WithError ("npm run build reported success but produced no bundle " +
            "(index.html present: $(Test-Path -LiteralPath $WebDistIndex), " +
            "assets/*.js: $($builtAssets.Count)) -- refusing to start a console with no UI") 1 "FAILED (empty SPA build)"
    }
    Write-Log -Message "Console SPA built ($($builtAssets.Count) JS asset(s) in dist/assets)" -Level "Success"
}

# -----------------------------------------------------------------------------
# Phase 11: Optional provisioning
# -----------------------------------------------------------------------------

Write-Header "Phase 11: Voice and Service Provisioning"

Write-Step "Provisioning voice and services"
if (-not $Provision) {
    Write-Log -Message "Skipped (downloads models and takes minutes). Run later: corvin-install" -Level "Info"
} elseif ($DryRun) {
    Write-Log -Message "[DRY RUN] Would run: corvin-install --yes" -Level "Info"
} elseif (-not (Get-Command corvin-install -ErrorAction SilentlyContinue)) {
    Write-Log -Message "corvin-install not on PATH -- skipping provisioning" -Level "Warn"
} else {
    $code = Invoke-Native -FilePath "corvin-install" -Arguments @("--yes") -IgnoreExitCode
    if ($code -eq 0) {
        Write-Log -Message "Provisioning completed" -Level "Success"
    } else {
        Write-Log -Message "Provisioning did not fully complete (exit $code) -- re-run later with: corvin-install" -Level "Warn"
    }
}

# -----------------------------------------------------------------------------
# Phase 12: System integration
# -----------------------------------------------------------------------------

Write-Header "Phase 12: System Integration"

Write-Step "Registering autostart task"
$taskName = "CorvinOS-AutoRestart"
$taskPath = "\CorvinOS\"
if ($DryRun) {
    Write-Log -Message "[DRY RUN] Would register scheduled task $taskPath$taskName" -Level "Info"
} else {
    try {
        $existingTask = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
        if ($existingTask) {
            Write-Log -Message "Scheduled task $taskName already exists (state: $($existingTask.State))" -Level "Info"
        } else {
            $serveCmd = Get-Command corvinos-serve -ErrorAction SilentlyContinue
            if (-not $serveCmd) {
                throw "corvinos-serve not on PATH; cannot build a task action"
            }
            # At-logon for the CURRENT USER, default run level. -AtStartup with
            # -RunLevel Highest needs administrator rights, which a normal
            # install does not have, so it failed every time.
            # --no-browser: the task exists to keep the console SERVING across
            # logons. Without it, every single logon hijacks the default browser.
            $action = New-ScheduledTaskAction -Execute $serveCmd.Source `
                -Argument "--no-browser --port $Port"
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
            # -RunWithoutNetwork is not a New-ScheduledTaskSettingsSet
            # parameter and made this call throw unconditionally.
            $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -StartWhenAvailable `
                -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)

            $null = Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath `
                -Action $action -Trigger $trigger -Settings $settings -Force -ErrorAction Stop
            Write-Log -Message "Scheduled task registered: $taskPath$taskName" -Level "Success"
        }
    } catch {
        # Non-fatal: group policy commonly blocks task registration.
        Write-Log -Message "Could not register autostart task: $($_.Exception.Message)" -Level "Warn"
        Write-Log -Message "Start the console manually with: corvinos-serve" -Level "Info"
    }
}

Write-Step "Reporting firewall state"
if ($Lan) {
    Write-Log -Message "LAN access requires an inbound rule on TCP 8765 (needs admin):" -Level "Warn"
    Write-Log -Message "  netsh advfirewall firewall add rule name=`"CorvinOS Console`" dir=in action=allow protocol=TCP localport=8765" -Level "Info"
} else {
    Write-Log -Message "Firewall untouched (console listens on 127.0.0.1). Re-run with -Lan for the LAN instructions." -Level "Info"
}

# -----------------------------------------------------------------------------
# Phase 13: Start the console
# -----------------------------------------------------------------------------

Write-Header "Phase 13: Start the Console"

# 127.0.0.1 and not localhost: the server binds 127.0.0.1 only, while Windows
# resolves localhost to ::1 first. A client that does not fall back to IPv4
# reaches nothing at all on a URL that looks correct.
$ConsoleUrl    = "http://127.0.0.1:$Port/console/"
$ConsoleStdout = Join-Path $LogDir "console-stdout.log"
$ConsoleStderr = Join-Path $LogDir "console-stderr.log"
$script:ConsoleReady = $false
# Non-empty means: the package installed, but the browser cannot reach a chat.
# The installer exits non-zero on that, because "installed" is not the goal.
$script:ConsoleFailure = ""

function Get-ConsoleOutputTail {
    param([int]$Count = 15)
    $lines = New-Object System.Collections.ArrayList
    foreach ($logFile in @($ConsoleStderr, $ConsoleStdout)) {
        if (Test-Path -LiteralPath $logFile) {
            foreach ($line in @(Get-Content -LiteralPath $logFile -Tail $Count -ErrorAction SilentlyContinue)) {
                $null = $lines.Add([string]$line)
            }
        }
    }
    return $lines
}

Write-Step "Starting the console"
if ($NoStart) {
    Write-Log -Message "Skipped (-NoStart). Start it with: corvinos-serve" -Level "Info"
} elseif ($DryRun) {
    Write-Log -Message "[DRY RUN] Would start corvinos-serve on port $Port and open $ConsoleUrl" -Level "Info"
} else {
    # Port triage BEFORE starting anything. Three genuinely different states hide
    # behind "the port is in use", and the old bare-TCP check collapsed all of
    # them into "already serving -- reusing the running console".
    $existing = Get-ConsoleHealth -TargetPort $Port -TimeoutSeconds 10
    if ($existing.Serving) {
        Write-Log -Message "A console is already serving $ConsoleUrl -- reusing it" -Level "Success"
        $script:ConsoleReady = $true
    } elseif ($existing.Listening) {
        Write-Log -Message "Port $Port is occupied but not serving the console ($($existing.Detail))" -Level "Warn"
        $freed = Stop-ConsoleOnPort -TargetPort $Port
        if ($freed -eq 0) {
            $script:ConsoleFailure = "Port $Port is held by another program. Re-run with -Port <other port>."
            Write-Log -Message $script:ConsoleFailure -Level "Warn"
        } else {
            Write-Log -Message "Freed port $Port (stopped $freed stale console process(es))" -Level "Info"
        }
    }

    if (-not $script:ConsoleReady -and -not $script:ConsoleFailure) {
        $serveCmd = Get-Command corvinos-serve -ErrorAction SilentlyContinue
        if (-not $serveCmd) {
            $script:ConsoleFailure = "corvinos-serve is not on PATH -- open a NEW terminal and run it there."
            Write-Log -Message $script:ConsoleFailure -Level "Warn"
        } else {
            try {
                # Detached and hidden: the console is a long-running server, and
                # the scheduled task restarts it at logon. Output is redirected so
                # a startup failure is diagnosable instead of vanishing with the
                # window. --no-browser: readiness is proven HERE first, so the
                # browser is never opened onto a connection-refused page.
                #
                # KNOWN QUIRK, deliberately left alone: -RedirectStandardOutput
                # forces UseShellExecute=false + bInheritHandles=TRUE, so the
                # server also inherits the INSTALLER's stdout handle and holds it
                # for its whole life. A caller that PIPES this installer's output
                # to something waiting for EOF therefore keeps waiting after the
                # install is done (measured 2026-09-18). Interactive runs are
                # unaffected. The obvious fix -- launching through
                # `cmd /c "<exe> ... > out 2> err"` so cmd owns the redirection --
                # was implemented and reverted: constructing a cmd command line
                # that way is a dropper pattern, and AMSI blocked the ENTIRE
                # script with "This script contains malicious content", which
                # fails the install before line 1. A cosmetic pipe quirk is worth
                # less than the script being allowed to run.
                $consoleProc = Start-Process -FilePath $serveCmd.Source `
                    -ArgumentList @("--no-browser", "--port", "$Port") `
                    -WindowStyle Hidden -PassThru `
                    -RedirectStandardOutput $ConsoleStdout `
                    -RedirectStandardError $ConsoleStderr

                # Same PS 5.1 trap as in Invoke-NativeTimed: reading .Handle
                # caches the process handle, which is the only thing that keeps
                # .ExitCode readable once the process is gone. Without it the
                # startup failure printed as "exited during startup (code )".
                try { $null = $consoleProc.Handle } catch { }

                # The PID is the launcher's, not the server's: corvinos-serve runs
                # uvicorn in a child of its own, so there are three processes in
                # the chain and only the port identifies the listener.
                Write-Log -Message "Console starting (launcher PID $($consoleProc.Id)); waiting up to ${StartTimeoutSeconds}s for $ConsoleUrl ..." -Level "Info"

                # Readiness is an HTTP 200 carrying the SPA shell, NOT an open
                # port. A bare TCP connect succeeds the moment uvicorn binds --
                # seconds before the app is mounted, and it stays true forever
                # when the app mounted the 503 "build failed" route instead.
                $deadline   = (Get-Date).AddSeconds($StartTimeoutSeconds)
                $lastDetail = ""
                $spaMissing = $false
                $exitedEarly = $false
                while ((Get-Date) -lt $deadline) {
                    if ($consoleProc.HasExited) { $exitedEarly = $true; break }
                    $health = Get-ConsoleHealth -TargetPort $Port -TimeoutSeconds 5
                    if ($health.Serving) { $script:ConsoleReady = $true; break }
                    if ($health.SpaMissing) { $spaMissing = $true; break }
                    if ($health.Detail) { $lastDetail = $health.Detail }
                    Start-Sleep -Milliseconds 500
                }

                if ($script:ConsoleReady) {
                    Write-Log -Message "Console is serving the app at $ConsoleUrl" -Level "Success"
                } else {
                    if ($exitedEarly) {
                        $exitText = "unknown"
                        try {
                            $consoleProc.Refresh()
                            if ($null -ne $consoleProc.ExitCode) { $exitText = [string]$consoleProc.ExitCode }
                        } catch { }
                        $script:ConsoleFailure = "The console exited during startup (exit code $exitText)."
                    } elseif ($spaMissing) {
                        # The frontend was built in phase 10b, so reaching here
                        # means the running process booted before that -- or the
                        # build landed somewhere this install does not read.
                        $script:ConsoleFailure = "The console runs but answers 503: it booted without a built frontend. Re-run the installer with -RebuildWeb."
                    } else {
                        $script:ConsoleFailure = "The console did not serve $ConsoleUrl within ${StartTimeoutSeconds}s ($lastDetail)."
                    }
                    Write-Log -Message $script:ConsoleFailure -Level "Warn"

                    $tail = Get-ConsoleOutputTail -Count 15
                    foreach ($line in $tail) {
                        if ($line) { Write-Log -Message "  $line" -Level "Info" }
                    }
                    # Info, not Warn: every Warn is collected into the summary's
                    # warning list, and an eight-line explanation reprinted there
                    # buried the one line that says what failed.
                    foreach ($hint in (Get-ConsoleFailureHint -Lines $tail)) {
                        Write-Log -Message $hint -Level "Info"
                    }
                    Write-Log -Message "Full server output: $ConsoleStdout / $ConsoleStderr" -Level "Info"
                    Write-Log -Message "To watch it start in the foreground: corvinos-serve --port $Port" -Level "Info"
                }
            } catch {
                $script:ConsoleFailure = "Could not start the console: $($_.Exception.Message)"
                Write-Log -Message $script:ConsoleFailure -Level "Warn"
            }
        }
    }
}

Write-Step "Verifying the path to the chat"
if ($NoStart -or $DryRun -or -not $script:ConsoleReady) {
    Write-Log -Message "Skipped (no running console to verify)" -Level "Info"
} else {
    # The chat is not the SPA shell. Getting there is: /console/ -> local-login
    # -> session cookie -> /app/chat. Proving the login endpoint hands back a
    # redirect AND a session cookie is what separates "a page loaded" from
    # "the operator lands in a chat".
    $login = Invoke-LoopbackHttp -Url "http://127.0.0.1:$Port/v1/console/auth/local-login" -TimeoutSeconds 20 -NoRedirect
    if (-not $login.Reached) {
        Write-Log -Message "Local login endpoint unreachable: $($login.Error)" -Level "Warn"
    } elseif ($login.StatusCode -ge 300 -and $login.StatusCode -lt 400 -and $login.SetCookie -match 'corvin_console_sid') {
        Write-Log -Message "Local login works (HTTP $($login.StatusCode), session cookie issued) -- the chat is reachable" -Level "Success"
    } else {
        Write-Log -Message "Local login answered HTTP $($login.StatusCode) without a session cookie -- the console will show its login page" -Level "Warn"
    }
}

Write-Step "Opening the console in the browser"
if ($NoBrowser -or $NoStart) {
    Write-Log -Message "Not opening a browser. Console URL: $ConsoleUrl" -Level "Info"
} elseif ($DryRun) {
    Write-Log -Message "[DRY RUN] Would open $ConsoleUrl" -Level "Info"
} elseif (-not $script:ConsoleReady) {
    Write-Log -Message "Not opening a browser: the console is not serving yet" -Level "Warn"
} else {
    if (-not (Open-InBrowser -Url $ConsoleUrl)) {
        # Not a hard failure: the console IS serving, so the operator only has
        # to paste the URL. Say so instead of implying the install broke.
        Write-Log -Message "No browser could be launched automatically. Open this yourself: $ConsoleUrl" -Level "Warn"
    }
}

# -----------------------------------------------------------------------------
# Phase 14: Final verification
# -----------------------------------------------------------------------------

Write-Header "Phase 14: Final Verification"

Write-Step "Verifying installation artifacts"
$verificationChecks = @(
    @{ Name = "CORVIN_HOME exists";        Check = { Test-Path -LiteralPath $CorvinHome } }
    @{ Name = "uv is available";           Check = { [bool](Get-Command uv -ErrorAction SilentlyContinue) } }
    @{ Name = "Node.js is available";      Check = { Test-Path -LiteralPath $nodeExe } }
    @{ Name = "corvinos CLI available";    Check = { [bool](Get-Command corvinos -ErrorAction SilentlyContinue) } }
    @{ Name = "corvinos-serve available";  Check = { [bool](Get-Command corvinos-serve -ErrorAction SilentlyContinue) } }
)

$allChecksPassed = $true
foreach ($check in $verificationChecks) {
    $result = $false
    try { $result = [bool](& $check.Check) } catch { $result = $false }
    if ($result) {
        Write-Log -Message "OK   $($check.Name)" -Level "Success"
    } else {
        Write-Log -Message "MISS $($check.Name)" -Level $(if ($DryRun) { "Info" } else { "Warn" })
        $allChecksPassed = $false
    }
}

if ($DryRun) {
    Write-Summary -Status "DRY RUN COMPLETE (nothing changed)" -ExitCode 0
    exit 0
}

if (-not $allChecksPassed) {
    Stop-WithError "Installation finished but verification failed -- see the checks above" 1 "FAILED (verification)"
}

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

Write-Progress -Activity "CorvinOS Installation" -Completed

# The goal of this installer is an open chat, not an unpacked package. When the
# console never served, the run reports FAILED and exits non-zero -- a green
# SUCCESS above a dead console is what sent the last round of debugging into the
# backend instead of at the installer.
$expectedToServe = (-not $NoStart) -and (-not $DryRun)
if ($expectedToServe -and -not $script:ConsoleReady) {
    Write-Summary -Status "INSTALLED, BUT THE CONSOLE IS NOT SERVING" -ExitCode 3
    Write-Host ""
    Write-Host "The corvinos package IS installed. Only the running console is missing." -ForegroundColor Yellow
    if ($script:ConsoleFailure) {
        Write-Host "   Reason: $script:ConsoleFailure" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "Recover with:" -ForegroundColor Yellow
    Write-Host "   corvinos-serve --port $Port        # run it in the foreground and read the error" -ForegroundColor White
    Write-Host "   Server log: $ConsoleStdout" -ForegroundColor White
    Write-Host "   Error log:  $ConsoleStderr" -ForegroundColor White
    Write-Host ""
    exit 3
}

Write-Summary -Status "SUCCESS" -ExitCode 0

Write-Host ""
Write-Host "Installation details:" -ForegroundColor Green
Write-Host "   CORVIN_HOME: $CorvinHome" -ForegroundColor White
Write-Host "   Repository:  $RepoPath" -ForegroundColor White
Write-Host "   Mode:        $(if ($EditableMode) { 'editable (local clone)' } else { "PyPI ($PackageName)" })" -ForegroundColor White
Write-Host "   Logs:        $LogDir" -ForegroundColor White
Write-Host ""
if ($script:ConsoleReady) {
    Write-Host "Console:" -ForegroundColor Green
    Write-Host "   Running at:  $ConsoleUrl" -ForegroundColor White
    Write-Host "   Server log:  $ConsoleStdout" -ForegroundColor White
    # NOT `Get-Process corvinos-serve | Stop-Process`: that stops the launcher
    # and leaves its uvicorn child holding the port.
    Write-Host "   Stop it:     taskkill /F /T /IM corvinos-serve.exe" -ForegroundColor White
    Write-Host "   Restarts automatically at logon (task \CorvinOS\CorvinOS-AutoRestart)." -ForegroundColor White
    if ($NoBrowser) {
        Write-Host ""
        Write-Host "   Open $ConsoleUrl to reach the chat." -ForegroundColor White
    }
} else {
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "   1. Start the console:  corvinos-serve" -ForegroundColor White
    Write-Host "   2. Open the browser:   $ConsoleUrl" -ForegroundColor White
    Write-Host ""
    Write-Host "   Open a NEW terminal first if 'corvinos-serve' is not found -- the PATH" -ForegroundColor Yellow
    Write-Host "   entry added by 'uv tool update-shell' applies to new shells only." -ForegroundColor Yellow
}
Write-Host ""

exit 0
