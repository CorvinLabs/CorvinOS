#Requires -Version 5.1
<#
.SYNOPSIS
    CorvinOS updater for Windows.

.DESCRIPTION
    Windows twin of update.sh: update CorvinOS to the latest main, reinstall the
    package, rebuild the console, restart it, prove over the wire that the new
    build is what the browser gets, and roll back automatically if it is not.

    Which source gets updated (found through the uv tool receipt):
      * installer-managed tree (%LOCALAPPDATA%\corvinos\src, has .corvin-managed):
        reset to origin/main with git, or replaced from the codeload zip when git
        is missing or blocked -- it is ours, local edits are not preserved.
      * a developer checkout on main: fast-forward; if main was rewritten, the
        old HEAD is kept as branch corvin-update-backup-<ts> and local changes
        as a git stash before resetting -- nothing is lost.
      * a developer checkout on another branch: code left alone (warned), but
        dependencies, frontend and the console are still refreshed.
      * a PyPI install: PyPI lags main, so it is converted to a managed tree.
      * nothing installed: install.ps1 is run instead.

    Order differs from update.sh in one place, on purpose: Windows cannot
    replace a file a running process holds, so the console is stopped BEFORE
    `uv tool install --force` (and before a zip swap), and the frontend is
    built into dist.next while it still runs, to keep the downtime short.

    Only THIS user's CorvinOS processes and Scheduled Tasks are touched, and a
    console of ANOTHER user on the port is never treated as ours (multi-session
    Citrix / RDS hosts: local-login trusts any loopback caller).

    Uses cmdlets and native tools only (no .NET types), so it also runs in
    Constrained Language Mode.

    ASCII-ONLY BY CONTRACT (see install.ps1: Windows PowerShell 5.1 reads a
    BOM-less script as cp1252).

.PARAMETER ConsoleOnly
    No download: reinstall + rebuild + restart the current code (alias
    -RebuildOnly, like update.sh --rebuild-only).

.PARAMETER NoRestart
    Stage everything, leave the running console alone. (The package reinstall
    still needs the tool venv free, so a running console is stopped for it and
    started again unless this switch is given.)

.PARAMETER NoRollback
    Keep the new code even if verification fails.

.PARAMETER Force
    Accepted for compatibility; every run rebuilds.

.PARAMETER Port
    Console port. Default: the --port of this user's CorvinOS Scheduled Task,
    else CORVIN_CONSOLE_PORT, else 8765.

.PARAMETER CorvinRepo
    Update this source tree instead of the one the uv receipt names.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File update.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File update.ps1 -ConsoleOnly

.NOTES
    Exit codes (same as update.sh):
      0 = updated, verified, running
      1 = failed and rolled back (the previous version is running)
      2 = failed, and the rollback failed too or was declined (details printed)
      3 = another install/update holds the lock
#>

[CmdletBinding()]
param(
    [Alias("RebuildOnly")]
    [switch]$ConsoleOnly,
    [switch]$NoRestart,
    [switch]$NoRollback,
    [switch]$Force,

    [ValidateRange(0, 65535)]
    [int]$Port = 0,

    [string]$CorvinRepo = ""
)

# Native tools report failure through $LASTEXITCODE only; every call below
# checks it. "Stop" would turn git's progress on stderr into a fatal error.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol
} catch { }

$Branch       = if ($env:CORVIN_BRANCH)   { $env:CORVIN_BRANCH }   else { "main" }
$RepoUrl      = if ($env:CORVIN_REPO_URL) { $env:CORVIN_REPO_URL.TrimEnd('/') } else { "https://github.com/CorvinLabs/CorvinOS" }
$ManagedSrc   = if ($env:CORVIN_SRC_DIR)  { $env:CORVIN_SRC_DIR }  else { Join-Path $env:LOCALAPPDATA "corvinos\src" }
$CorvinHome   = if ($env:CORVIN_HOME)     { $env:CORVIN_HOME }     else { Join-Path $env:USERPROFILE ".corvin" }
$UvVersion    = "0.12.9"
$UvInstallerUrl    = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-installer.ps1"
$UvInstallerSha256 = "69de475bf929f1ac248efb5a85189177a45517e2346cd68762bde453fec10a6b"
$DefaultNodeVersion = "24.18.0"

$Stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$LogDir  = Join-Path $env:TEMP "corvinos-update-$Stamp"
$null = New-Item -ItemType Directory -Path $LogDir -Force -ErrorAction SilentlyContinue
$LogFile = Join-Path $LogDir "update.log"

# Corporate TLS-inspecting proxies: see install.ps1. Process scope only.
if (-not $env:UV_NATIVE_TLS)      { $env:UV_NATIVE_TLS = "1" }
if (-not $env:NODE_USE_SYSTEM_CA) { $env:NODE_USE_SYSTEM_CA = "1" }

# -----------------------------------------------------------------------------
# Output and native-command helpers
# -----------------------------------------------------------------------------

function Write-LogFile { param([string]$Text) Add-Content -LiteralPath $LogFile -Value $Text -ErrorAction SilentlyContinue }
function Write-Step { param([string]$Text) Write-Host ""; Write-Host $Text -ForegroundColor Cyan; Write-LogFile "=== $(Get-Date -Format 'HH:mm:ss') $Text ===" }
function Write-Ok   { param([string]$Text) Write-Host "  [ok] $Text" -ForegroundColor Green; Write-LogFile "ok: $Text" }
function Write-Warn { param([string]$Text) Write-Host "  [!!] $Text" -ForegroundColor Yellow; Write-LogFile "WARN: $Text" }

function Stop-Update {
    param([string]$Text, [int]$Code = 1)
    Write-Host "  [xx] $Text" -ForegroundColor Red
    Write-LogFile "FAIL: $Text"
    Exit-SetupLock
    exit $Code
}

# Runs a native command with its output going to the log only. Returns the exit
# code (-1 when it could not be launched at all: missing, or AppLocker).
$script:LastOutput = @()
function Invoke-Native {
    param([Parameter(Mandatory = $true)][string]$Exe, [string[]]$Arguments = @())
    Write-LogFile "exec: $Exe $($Arguments -join ' ')"
    $script:LastOutput = @()
    $code = -1
    try {
        $script:LastOutput = @(& $Exe @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
    } catch {
        $script:LastOutput = @("$($_.Exception.Message)")
    }
    foreach ($l in $script:LastOutput) { Write-LogFile "  $l" }
    return $code
}

function Show-LastOutput {
    param([int]$Tail = 15)
    foreach ($l in @($script:LastOutput | Select-Object -Last $Tail)) { Write-Host "      $l" -ForegroundColor DarkYellow }
}

# _retry N { ... } -- the block returns $true on success. Backoff 2, 4, 8 s.
function Invoke-Retry {
    param([int]$Times, [scriptblock]$Block)
    $wait = 2
    for ($i = 1; $i -le $Times; $i++) {
        if (& $Block) { return $true }
        if ($i -lt $Times) { Write-LogFile "(attempt $i/$Times failed -- retrying in ${wait}s)"; Start-Sleep -Seconds $wait; $wait = $wait * 2 }
    }
    return $false
}

# curl.exe (Schannel: trusts the Windows store, i.e. a corporate TLS root)
# first, Invoke-WebRequest second -- see install.ps1 for why both.
function Invoke-Download {
    param([string]$Uri, [string]$OutFile)
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $code = Invoke-Native -Exe $curl.Source -Arguments @("-fsSL", "--connect-timeout", "20", "--max-time", "900", "-o", $OutFile, $Uri)
        if ($code -eq 0 -and (Test-Path -LiteralPath $OutFile) -and (Get-Item -LiteralPath $OutFile).Length -gt 0) { return $true }
    }
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -TimeoutSec 900 -ErrorAction Stop
        return ((Test-Path -LiteralPath $OutFile) -and (Get-Item -LiteralPath $OutFile).Length -gt 0)
    } catch {
        Write-LogFile "download $Uri failed: $($_.Exception.Message)"
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
        return $false
    }
}

# Remove-Item with retries (antivirus / Search indexer / OneDrive hold handles
# for seconds) and robocopy /MIR from an empty directory for paths beyond
# MAX_PATH -- node_modules routinely has them.
function Remove-Tree {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    for ($i = 1; $i -le 3; $i++) {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $Path)) { return $true }
        Start-Sleep -Seconds $i
    }
    $empty = Join-Path $env:TEMP ("corvin-empty-" + [guid]::NewGuid().ToString("N"))
    $null = New-Item -ItemType Directory -Path $empty -Force -ErrorAction SilentlyContinue
    $null = Invoke-Native -Exe "robocopy.exe" -Arguments @($empty, $Path, "/MIR", "/R:2", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    Remove-Item -LiteralPath $empty -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
    return (-not (Test-Path -LiteralPath $Path))
}

# -----------------------------------------------------------------------------
# Lock (shared with install.ps1; same name as the POSIX lock directory)
# -----------------------------------------------------------------------------

$SetupLockDir = Join-Path $env:TEMP "corvinos-setup.lock"
$script:LockHeld = $false
function Enter-SetupLock {
    for ($i = 0; $i -lt 2; $i++) {
        try {
            $null = New-Item -ItemType Directory -Path $SetupLockDir -ErrorAction Stop
            Set-Content -LiteralPath (Join-Path $SetupLockDir "pid") -Value $PID -ErrorAction SilentlyContinue
            $script:LockHeld = $true
            return $true
        } catch {
            $ownerPid = 0
            try { $ownerPid = [int](Get-Content -LiteralPath (Join-Path $SetupLockDir "pid") -ErrorAction Stop | Select-Object -First 1) } catch { }
            $item = Get-Item -LiteralPath $SetupLockDir -ErrorAction SilentlyContinue
            $old = $item -and ($item.CreationTime -lt (Get-Date).AddHours(-2))
            $dead = ($ownerPid -gt 0) -and -not (Get-Process -Id $ownerPid -ErrorAction SilentlyContinue)
            if (-not ($old -or $dead)) { return $false }
            Remove-Item -LiteralPath $SetupLockDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    return $false
}
function Exit-SetupLock {
    if ($script:LockHeld) {
        Remove-Item -LiteralPath $SetupLockDir -Recurse -Force -ErrorAction SilentlyContinue
        $script:LockHeld = $false
    }
}

# -----------------------------------------------------------------------------
# This user's CorvinOS processes and tasks (multi-session safe)
# -----------------------------------------------------------------------------

function Get-AncestorPid {
    # An updater that kills its own shell cannot report a result.
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
$SelfPids = Get-AncestorPid

function Test-ProcessIsMine {
    param($CimProcess)
    try {
        $o = Invoke-CimMethod -InputObject $CimProcess -MethodName GetOwner -ErrorAction Stop
        if ($o.ReturnValue -ne 0) { return $false }
        return (($o.User -eq $env:USERNAME) -and ((-not $o.Domain) -or ($o.Domain -eq $env:USERDOMAIN)))
    } catch { return $false }
}

# Interpreter/launcher executables only: matching command lines across ALL
# processes also hits editors (and a looser pattern once matched EXCEL.EXE).
$HostNames = @("python.exe", "pythonw.exe", "node.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "cmd.exe", "uv.exe")
$ProcPattern = 'corvin_gateway\.app|corvin_console\.standalone|corvinos-serve|corvin-serve|corvin-service|bridges[\\/]shared[\\/]adapter\.py|corvin_operator[\\/]bridges[\\/][a-z]+[\\/]daemon\.js|corvin-supervisor\.ps1|corvin-watchdog'

function Get-CorvinProcesses {
    $hits = @()
    $venv = if ($script:ToolEnv) { $script:ToolEnv.ToLowerInvariant() + '\' } else { "" }
    foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        $procId = [int]$p.ProcessId
        if ($procId -le 4 -or $SelfPids -contains $procId) { continue }
        $name = ([string]$p.Name).ToLowerInvariant()
        $exe  = ([string]$p.ExecutablePath).ToLowerInvariant()
        $cmd  = [string]$p.CommandLine
        if ($cmd -match 'update\.ps1|install\.ps1') { continue }
        $match = ($venv -and $exe.StartsWith($venv)) -or ($name -like 'corvin*') -or
                 (($HostNames -contains $name) -and ($cmd -match $ProcPattern))
        if ($match -and (Test-ProcessIsMine $p)) { $hits += $procId }
    }
    return $hits
}

function Get-PortListenerPids {
    param([int]$TargetPort)
    $pids = @()
    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        foreach ($c in @(Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue)) {
            if ($c.OwningProcess -and ($pids -notcontains [int]$c.OwningProcess)) { $pids += [int]$c.OwningProcess }
        }
        return $pids
    }
    $null = Invoke-Native -Exe "netstat.exe" -Arguments @("-ano", "-p", "TCP")
    foreach ($line in $script:LastOutput) {
        if ($line -match "^\s*TCP\s+\S+:$TargetPort\s+\S+\s+LISTENING\s+(\d+)\s*$") { $pids += [int]$Matches[1] }
    }
    return $pids
}

# "mine" / "foreign" / "" (nothing listening).
function Get-PortState {
    param([int]$TargetPort)
    $state = ""
    foreach ($lp in @(Get-PortListenerPids -TargetPort $TargetPort)) {
        $cp = Get-CimInstance Win32_Process -Filter "ProcessId=$lp" -ErrorAction SilentlyContinue
        if ($cp -and (Test-ProcessIsMine $cp)) { $state = "mine" } else { return "foreign" }
    }
    return $state
}

$HaveTaskCmdlets = [bool](Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)
function Get-MyTasks {
    if (-not $HaveTaskCmdlets) { return @() }
    $mine = @()
    foreach ($t in @(Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        $isCorvin = ($t.TaskPath -like '\CorvinOS\*') -or ($t.TaskName -like 'CorvinOS-Console') -or
                    ($t.TaskName -like 'CorvinOS-AutoRestart*') -or ($t.TaskName -like 'CorvinOS-Bridge-*')
        if (-not $isCorvin) { continue }
        $uid = [string]$t.Principal.UserId
        if ($uid -and $uid -ne $env:USERNAME -and $uid -ne "$env:USERDOMAIN\$env:USERNAME") { continue }
        $mine += $t
    }
    return $mine
}

# The task that serves the console (install.ps1's AutoRestart, or bridge.ps1's
# supervisor task), or $null.
function Get-ConsoleTask {
    foreach ($t in @(Get-MyTasks)) {
        if ($t.TaskName -like 'CorvinOS-AutoRestart*' -or $t.TaskName -eq 'CorvinOS-Console') { return $t }
    }
    return $null
}

$script:RestartTasks = @()
$script:Stopped = $false
function Stop-Console {
    if ($script:Stopped) { return }
    # Tasks first: a supervisor (corvin-supervisor.ps1) would respawn a console
    # killed underneath it.
    foreach ($t in @(Get-MyTasks)) {
        if ([string]$t.State -eq "Running") {
            $script:RestartTasks += $t
            try { $null = Stop-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -ErrorAction Stop } catch { }
        }
    }
    $pids = @(Get-CorvinProcesses)
    foreach ($id in $pids) { $null = Invoke-Native -Exe "taskkill.exe" -Arguments @("/PID", "$id", "/T") }
    for ($i = 0; $i -lt 8 -and @(Get-CorvinProcesses).Count -gt 0; $i++) { Start-Sleep -Seconds 1 }
    foreach ($id in @(Get-CorvinProcesses)) {
        # /T: corvinos-serve BLOCKS on a uvicorn child that holds the port.
        $null = Invoke-Native -Exe "taskkill.exe" -Arguments @("/PID", "$id", "/T", "/F")
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }
    for ($i = 0; $i -lt 20 -and (Get-PortState -TargetPort $Port) -eq "mine"; $i++) { Start-Sleep -Milliseconds 500 }
    $script:Stopped = $true
    Write-Ok "console stopped ($($pids.Count) process(es))"
}

# -----------------------------------------------------------------------------
# Start
# -----------------------------------------------------------------------------

Write-Host ""
Write-Host "CorvinOS updater" -ForegroundColor White
Write-Host "  log: $LogFile"

if (-not (Enter-SetupLock)) {
    Write-Host "  [xx] another CorvinOS install/update is running (lock: $SetupLockDir)" -ForegroundColor Red
    exit 3
}

$UvBin = Join-Path $env:USERPROFILE ".local\bin"
$env:PATH = (@($UvBin, (Join-Path $env:USERPROFILE ".cargo\bin"), (Join-Path $CorvinHome "node")) + @($env:PATH -split ';') |
             Where-Object { $_ } | Select-Object -Unique) -join ';'

# -----------------------------------------------------------------------------
# 1. Tools: uv (pinned + checksummed, like install.ps1)
# -----------------------------------------------------------------------------

Write-Step "[1/6] Tools"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $uvInstaller = Join-Path $LogDir "uv-installer.ps1"
    if (-not (Invoke-Retry 3 { Invoke-Download -Uri $UvInstallerUrl -OutFile $uvInstaller })) {
        Stop-Update "could not download uv (network/proxy?) -- see $LogFile"
    }
    if ((Get-FileHash -LiteralPath $uvInstaller -Algorithm SHA256).Hash -ne $UvInstallerSha256.ToUpperInvariant()) {
        Remove-Item -LiteralPath $uvInstaller -Force -ErrorAction SilentlyContinue
        Stop-Update "uv installer checksum mismatch -- refusing to run it"
    }
    $env:UV_INSTALL_DIR = $UvBin
    if ((Invoke-Native -Exe "powershell.exe" -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $uvInstaller)) -ne 0 -or
        -not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Show-LastOutput
        Stop-Update "uv install failed -- see $LogFile"
    }
}
$UvExe = (Get-Command uv).Source
$null = Invoke-Native -Exe $UvExe -Arguments @("--version")
Write-Ok ("uv " + (($script:LastOutput | Select-Object -First 1) -replace '^uv\s+', ''))

$null = Invoke-Native -Exe $UvExe -Arguments @("tool", "dir")
$ToolDir = ([string]($script:LastOutput | Select-Object -Last 1)).Trim()
if (-not $ToolDir) { $ToolDir = Join-Path $env:APPDATA "uv\tools" }
$script:ToolEnv = Join-Path $ToolDir "corvinos"
$Receipt = Join-Path $script:ToolEnv "uv-receipt.toml"
$ToolPy  = Join-Path $script:ToolEnv "Scripts\python.exe"

# -----------------------------------------------------------------------------
# 2. Locate the install and update its source
# -----------------------------------------------------------------------------

$Src = ""
$Kind = ""
if ($CorvinRepo) {
    $Src = (Resolve-Path -LiteralPath $CorvinRepo -ErrorAction SilentlyContinue).ProviderPath
    if (-not $Src -or -not (Test-Path -LiteralPath (Join-Path $Src "pyproject.toml"))) { Stop-Update "-CorvinRepo $CorvinRepo is not a CorvinOS source tree" }
} elseif (Test-Path -LiteralPath $Receipt) {
    $text = Get-Content -LiteralPath $Receipt -Raw -ErrorAction SilentlyContinue
    if ($text -match 'editable\s*=\s*"([^"]*)"') { $Src = $Matches[1].Replace('\\', '\') }
}
if ($Src -and (Test-Path -LiteralPath (Join-Path $Src "pyproject.toml"))) {
    $Kind = if (Test-Path -LiteralPath (Join-Path $Src ".corvin-managed")) { "managed" } else { "checkout" }
} elseif (Test-Path -LiteralPath (Join-Path $ManagedSrc "pyproject.toml")) {
    $Src = $ManagedSrc; $Kind = "managed"
} elseif (Test-Path -LiteralPath $Receipt) {
    $Src = $ManagedSrc; $Kind = "pypi"
} else {
    # Nothing installed (or the venv is gone, e.g. a non-persistent VDI profile
    # reset): an update cannot restore what is not there -- install instead.
    Write-Warn "no CorvinOS install found -- running the installer instead"
    $installer = Join-Path $LogDir "install.ps1"
    $rawUrl = ($RepoUrl -replace '^https://github\.com/', 'https://raw.githubusercontent.com/') + "/$Branch/install.ps1"
    if (-not (Invoke-Retry 3 { Invoke-Download -Uri $rawUrl -OutFile $installer })) { Stop-Update "could not download install.ps1" }
    Exit-SetupLock
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    exit $LASTEXITCODE
}
# Port: explicit, else the console task's own --port, else the env, else 8765.
$ConsoleTask = Get-ConsoleTask
if ($Port -le 0 -and $ConsoleTask) {
    foreach ($a in @($ConsoleTask.Actions)) { if ([string]$a.Arguments -match '--port\s+(\d+)') { $Port = [int]$Matches[1] } }
}
if ($Port -le 0 -and $env:CORVIN_CONSOLE_PORT -match '^\d+$') { $Port = [int]$env:CORVIN_CONSOLE_PORT }
if ($Port -le 0) { $Port = 8765 }
$BaseUrl = "http://127.0.0.1:$Port"
$Web = Join-Path $Src "core\console\corvin_console\web-next"
$Dist = Join-Path $Web "dist"
Write-Step "[2/6] Source ($Kind): $Src"

$HaveGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
function Invoke-Git { param([string[]]$GitArgs) return (Invoke-Native -Exe "git" -Arguments (@("-C", $Src) + $GitArgs)) }
function Get-Rev {
    if (-not $HaveGit -or -not (Test-Path -LiteralPath (Join-Path $Src ".git"))) { return "" }
    if ((Invoke-Git @("rev-parse", "HEAD")) -ne 0) { return "" }
    return ([string]($script:LastOutput | Select-Object -First 1)).Trim()
}
$PrevRev = Get-Rev
$script:BackupRef = ""
$script:Stashed = $false
$script:ZipSwapped = $false

# Managed tree -> exactly origin/$Branch. git when it works; the codeload zip
# otherwise (no git on the desktop, or git's OpenSSL rejecting a corporate
# TLS-inspection root that curl.exe/Schannel accepts).
function Update-Managed {
    if ($HaveGit -and (Test-Path -LiteralPath (Join-Path $Src ".git"))) {
        $ok = Invoke-Retry 3 { (Invoke-Git @("fetch", "--depth", "1", "origin", $Branch)) -eq 0 }
        if ($ok -and (Invoke-Git @("reset", "--hard", "-q", "FETCH_HEAD")) -eq 0) { return $true }
        Write-Warn "git could not update the tree -- falling back to the zip download"
    } elseif ($HaveGit -and -not (Test-Path -LiteralPath $Src)) {
        $parent = Split-Path -Parent $Src
        $null = New-Item -ItemType Directory -Path $parent -Force -ErrorAction SilentlyContinue
        $ok = Invoke-Retry 3 {
            $null = Remove-Tree "$Src.tmp"
            (Invoke-Native -Exe "git" -Arguments @("clone", "-q", "--depth", "1", "--branch", $Branch, "$RepoUrl.git", "$Src.tmp")) -eq 0
        }
        if ($ok) {
            Move-Item -LiteralPath "$Src.tmp" -Destination $Src -ErrorAction SilentlyContinue
            if (Test-Path -LiteralPath (Join-Path $Src "pyproject.toml")) { return $true }
        }
        $null = Remove-Tree "$Src.tmp"
    }
    # Zip: build the new tree beside the old one, carry generated state across,
    # swap, and KEEP the old tree as $Src.prev for the rollback. The swap moves
    # directories, which Windows refuses while a process runs inside -- so the
    # console is stopped first.
    $zip = Join-Path $LogDir "src.zip"
    $zipUrl = ($RepoUrl -replace '^https://github\.com/', 'https://codeload.github.com/') + "/zip/refs/heads/$Branch"
    if (-not (Invoke-Retry 3 { Invoke-Download -Uri $zipUrl -OutFile $zip })) { return $false }
    $stage = "$Src.new"
    $null = Remove-Tree $stage
    $null = New-Item -ItemType Directory -Path $stage -Force
    try { Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force -ErrorAction Stop }
    catch {
        $tar = Join-Path $env:SystemRoot "System32\tar.exe"
        if (-not (Test-Path -LiteralPath $tar) -or (Invoke-Native -Exe $tar -Arguments @("-xf", $zip, "-C", $stage)) -ne 0) {
            $null = Remove-Tree $stage; return $false
        }
    }
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    $top = @(Get-ChildItem -LiteralPath $stage -Directory -Force)
    if ($top.Count -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $top[0].FullName "pyproject.toml"))) { $null = Remove-Tree $stage; return $false }
    $new = $top[0].FullName
    Stop-Console
    if (Test-Path -LiteralPath $Src) {
        foreach ($keep in @(".corvin", "core\console\corvin_console\web-next\node_modules", "core\console\corvin_console\web-next\dist")) {
            $from = Join-Path $Src $keep
            if (-not (Test-Path -LiteralPath $from)) { continue }
            $to = Join-Path $new $keep
            $null = New-Item -ItemType Directory -Path (Split-Path -Parent $to) -Force
            Move-Item -LiteralPath $from -Destination $to -ErrorAction SilentlyContinue
        }
        $null = Remove-Tree "$Src.prev"
        Move-Item -LiteralPath $Src -Destination "$Src.prev" -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $Src) {
            # Could not move the old tree (a file held open): put the carried
            # state back so nothing is half-moved.
            foreach ($keep in @(".corvin", "core\console\corvin_console\web-next\node_modules", "core\console\corvin_console\web-next\dist")) {
                $from = Join-Path $new $keep
                if (Test-Path -LiteralPath $from) { Move-Item -LiteralPath $from -Destination (Join-Path $Src $keep) -ErrorAction SilentlyContinue }
            }
            $null = Remove-Tree $stage
            Write-Warn "the old tree at $Src is held open by a program (Explorer window, editor, antivirus?)"
            return $false
        }
    } else {
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $Src) -Force -ErrorAction SilentlyContinue
    }
    Move-Item -LiteralPath $new -Destination $Src -ErrorAction SilentlyContinue
    $null = Remove-Tree $stage
    if (-not (Test-Path -LiteralPath (Join-Path $Src "pyproject.toml"))) { return $false }
    $script:ZipSwapped = $true
    return $true
}

# Developer checkout -- never lose work.
function Update-Checkout {
    if (-not $HaveGit) { Write-Warn "git not found -- code not updated"; return $true }
    $null = Invoke-Git @("symbolic-ref", "--short", "-q", "HEAD")
    $cur = ([string]($script:LastOutput | Select-Object -First 1)).Trim()
    if (-not $cur) { $cur = "DETACHED" }
    if (-not (Invoke-Retry 3 { (Invoke-Git @("fetch", "origin", $Branch)) -eq 0 })) { return $false }
    if ($cur -ne $Branch) {
        Write-Warn "checkout is on '$cur', not '$Branch' -- code left as is (switch branches yourself to update)"
        return $true
    }
    $null = Invoke-Git @("status", "--porcelain", "--untracked-files=no")
    if (@($script:LastOutput | Where-Object { $_ }).Count -gt 0) {
        if ((Invoke-Git @("stash", "push", "-q", "-m", "corvin-update autostash $Stamp")) -ne 0) { return $false }
        $script:Stashed = $true
    }
    if ((Invoke-Git @("merge-base", "--is-ancestor", "HEAD", "origin/$Branch")) -eq 0) {
        if ((Invoke-Git @("merge", "-q", "--ff-only", "origin/$Branch")) -ne 0) { return $false }
    } elseif ((Invoke-Git @("merge-base", "--is-ancestor", "origin/$Branch", "HEAD")) -eq 0) {
        Write-Warn "checkout is ahead of origin/$Branch (unpushed commits) -- kept as is"
    } else {
        # Diverged: origin was rewritten, or local commits exist. Keep the old
        # HEAD on a branch, then take origin as the truth.
        $script:BackupRef = "corvin-update-backup-$Stamp"
        if ((Invoke-Git @("branch", $script:BackupRef, "HEAD")) -ne 0) { return $false }
        if ((Invoke-Git @("reset", "-q", "--hard", "origin/$Branch")) -ne 0) { return $false }
        Write-Warn "origin/$Branch was rewritten -- your previous HEAD is saved as branch $($script:BackupRef)"
    }
    # Put local changes back (pull --autostash semantics). A conflict leaves the
    # tree clean and the stash in place -- never conflict markers in files the
    # build below would choke on.
    if ($script:Stashed) {
        if ((Invoke-Git @("stash", "apply", "-q")) -eq 0) {
            $null = Invoke-Git @("stash", "drop", "-q")
            $script:Stashed = $false
            Write-Ok "local changes re-applied on top of the update"
        } else {
            $null = Invoke-Git @("reset", "-q", "--hard", "HEAD")
            Write-Warn "local changes conflict with the update -- kept in the stash (git stash list)"
        }
    }
    return $true
}

if ($ConsoleOnly) {
    Write-Ok "code unchanged (-ConsoleOnly)"
} else {
    Write-Host "  ... fetching $Branch"
    $fetched = if ($Kind -eq "checkout") { Update-Checkout } else { Update-Managed }
    if (-not $fetched) {
        if ($script:Stopped) { foreach ($t in $script:RestartTasks) { try { Start-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -ErrorAction Stop } catch { } } }
        Stop-Update "could not download the update -- nothing changed (see $LogFile)"
    }
    if ($Kind -ne "checkout") { $null = New-Item -ItemType File -Path (Join-Path $Src ".corvin-managed") -Force }
    $NewRev = Get-Rev
    $shortPrev = if ($PrevRev) { $PrevRev.Substring(0, 8) } else { "none" }
    $shortNew = if ($NewRev) { $NewRev.Substring(0, 8) } else { "zip" }
    if ($PrevRev -and $PrevRev -eq $NewRev) { Write-Ok "already at the latest $Branch ($shortNew) -- refreshing anyway" }
    else {
        Write-Ok "$shortPrev -> $shortNew"
        if ($PrevRev -and $NewRev -and (Invoke-Git @("log", "--oneline", "$PrevRev..HEAD")) -eq 0) {
            foreach ($l in @($script:LastOutput | Select-Object -First 15)) { Write-Host "      $l" }
        }
    }
}

# -----------------------------------------------------------------------------
# 3. Console frontend: clean build into dist.next (the console keeps serving)
# -----------------------------------------------------------------------------

Write-Step "[3/6] Console frontend"
$NodeExe = Join-Path (Join-Path $CorvinHome "node") "node.exe"
$nvmrc = Join-Path $Src ".nvmrc"
$NodeVersion = if (Test-Path -LiteralPath $nvmrc) { ((Get-Content -LiteralPath $nvmrc -Raw).Trim() -replace '^v', '') } else { $DefaultNodeVersion }
if (-not (Test-Path -LiteralPath $NodeExe) -and (Test-Path -LiteralPath (Join-Path $Web "package.json"))) {
    # Local runtime missing (profile reset on a non-persistent VDI): fetch it
    # exactly as install.ps1 does.
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } elseif ($env:PROCESSOR_ARCHITECTURE -eq "x86" -and -not $env:PROCESSOR_ARCHITEW6432) { "x86" } else { "x64" }
    $nodeZip = Join-Path $LogDir "node.zip"
    $nodeStage = Join-Path $LogDir "node-stage"
    if (Invoke-Retry 3 { Invoke-Download -Uri "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-$arch.zip" -OutFile $nodeZip }) {
        try {
            Expand-Archive -LiteralPath $nodeZip -DestinationPath $nodeStage -Force -ErrorAction Stop
            $inner = @(Get-ChildItem -LiteralPath $nodeStage -Directory)
            if ($inner.Count -eq 1) {
                $null = Remove-Tree (Join-Path $CorvinHome "node")
                $null = New-Item -ItemType Directory -Path $CorvinHome -Force -ErrorAction SilentlyContinue
                Move-Item -LiteralPath $inner[0].FullName -Destination (Join-Path $CorvinHome "node") -ErrorAction Stop
            }
        } catch { Write-LogFile "node extract failed: $($_.Exception.Message)" }
        $null = Remove-Tree $nodeStage
        Remove-Item -LiteralPath $nodeZip -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $NodeExe) { Write-Ok "Node.js v$NodeVersion installed" } else { Write-Warn "local Node.js bootstrap failed -- trying the system Node.js" }
}
if (-not (Test-Path -LiteralPath $NodeExe)) {
    $sysNode = Get-Command node -ErrorAction SilentlyContinue
    $NodeExe = if ($sysNode) { $sysNode.Source } else { "" }
}
$NpmCmd = if ($NodeExe) { Join-Path (Split-Path -Parent $NodeExe) "npm.cmd" } else { "" }
if ($NpmCmd -and -not (Test-Path -LiteralPath $NpmCmd)) {
    $sysNpm = Get-Command npm -ErrorAction SilentlyContinue
    $NpmCmd = if ($sysNpm) { $sysNpm.Source } else { "" }
}
if (-not $env:NODE_OPTIONS) { $env:NODE_OPTIONS = "--max-old-space-size=4096" }

function Install-WebDeps {
    if (Test-Path -LiteralPath (Join-Path $Web "package-lock.json")) {
        if ((Invoke-Native -Exe $NpmCmd -Arguments @("ci", "--no-audit", "--no-fund")) -eq 0) { return $true }
        # A half-extracted node_modules (killed run, AV lock) breaks ci -- start clean.
        $null = Remove-Tree (Join-Path $Web "node_modules")
        return ((Invoke-Native -Exe $NpmCmd -Arguments @("ci", "--no-audit", "--no-fund")) -eq 0)
    }
    return ((Invoke-Native -Exe $NpmCmd -Arguments @("install", "--no-audit", "--no-fund")) -eq 0)
}

$script:TscFailed = $false
function Build-Web {
    $null = Remove-Tree (Join-Path $Web "node_modules\.vite")
    $null = Remove-Tree (Join-Path $Web "dist.next")
    # node + the JS entry points directly: no .cmd shim, no cmd.exe quoting.
    if ((Invoke-Native -Exe $NodeExe -Arguments @("node_modules\typescript\bin\tsc", "-b")) -ne 0) {
        # A type error in some panel must not keep every user on the old
        # version: vite (esbuild) still emits a working bundle. Logged loudly.
        Write-LogFile "WARNING: tsc -b reported type errors (above) -- building without the type gate"
        $script:TscFailed = $true
    }
    if ((Invoke-Native -Exe $NodeExe -Arguments @("node_modules\vite\bin\vite.js", "build", "--outDir", "dist.next")) -ne 0) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Web "dist.next\index.html"))
}

$BuildOk = $false
if (-not (Test-Path -LiteralPath (Join-Path $Web "package.json"))) {
    Write-Warn "no console source in $Src -- skipped"
} elseif (-not $NodeExe -or -not $NpmCmd) {
    Write-Warn "Node.js/npm unavailable -- console frontend NOT rebuilt"
} else {
    Push-Location -LiteralPath $Web
    try {
        Write-Host "  ... installing frontend dependencies"
        if (Invoke-Retry 2 { Install-WebDeps }) {
            Write-Host "  ... building the console"
            $BuildOk = Build-Web
        }
    } finally { Pop-Location }
    if ($BuildOk) {
        Write-Ok "console built into dist.next"
        if ($script:TscFailed) { Write-Warn "type check reported errors -- built anyway (see $LogFile)" }
    } else {
        Show-LastOutput
        Write-Warn "frontend build failed -- the previous build stays live (see $LogFile)"
        $null = Remove-Tree (Join-Path $Web "dist.next")
    }
}

# -----------------------------------------------------------------------------
# 4. Python package (deps can change with the code)
# -----------------------------------------------------------------------------

Write-Step "[4/6] Python package"
# Windows: a running image inside the tool venv makes `uv tool install --force`
# fail with "Access is denied" -- the console must be down from here on.
Stop-Console

function Install-Package {
    return ((Invoke-Native -Exe $UvExe -Arguments @("tool", "install", "--force", "--editable", "$Src[browser]")) -eq 0)
}
function Install-PackageHealing {
    if (Install-Package) { return $true }
    Write-LogFile "retry: stopping stragglers and removing the tool venv"
    $script:Stopped = $false; Stop-Console
    if ((Invoke-Native -Exe $UvExe -Arguments @("tool", "uninstall", "corvinos")) -ne 0) { $null = Remove-Tree $script:ToolEnv }
    if (Install-Package) { return $true }
    Write-LogFile "retry: clean uv cache"
    $null = Invoke-Native -Exe $UvExe -Arguments @("cache", "clean", "corvinos")
    return (Install-Package)
}
Write-Host "  ... installing corvinos from $Src"
$PkgOk = Install-PackageHealing
if ($PkgOk) { Write-Ok "corvinos installed (editable)" }
else {
    Show-LastOutput
    if (@($script:LastOutput) -match 'blocked by group policy|1260') {
        Write-Warn "Group Policy (AppLocker/WDAC) blocks programs under $ToolDir -- ask IT for an allow rule"
    }
    Write-Warn "package install failed (see $LogFile)"
}
$null = Invoke-Native -Exe $UvExe -Arguments @("tool", "dir", "--bin")
$binDir = ([string]($script:LastOutput | Select-Object -Last 1)).Trim()
if ($binDir -and (Test-Path -LiteralPath $binDir)) { $env:PATH = "$binDir;$env:PATH" }

# The new bundle goes live only now, with the console down.
if ($BuildOk) {
    $null = Remove-Tree (Join-Path $Web "dist.prev")
    if (Test-Path -LiteralPath $Dist) { Rename-Item -LiteralPath $Dist -NewName "dist.prev" -ErrorAction SilentlyContinue }
    Rename-Item -LiteralPath (Join-Path $Web "dist.next") -NewName "dist" -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath (Join-Path $Dist "index.html")) {
        $entry = [regex]::Match((Get-Content -LiteralPath (Join-Path $Dist "index.html") -Raw), 'assets/index-[^"]*\.js').Value
        Write-Ok "new build in place ($entry)"
    } else {
        if (Test-Path -LiteralPath (Join-Path $Web "dist.prev")) { Rename-Item -LiteralPath (Join-Path $Web "dist.prev") -NewName "dist" -ErrorAction SilentlyContinue }
        $BuildOk = $false
        Write-Warn "swap failed -- previous build restored"
    }
}

# -----------------------------------------------------------------------------
# 5. Voice model + restart
# -----------------------------------------------------------------------------

Write-Step "[5/6] Services"
# The offline voice model for the configured language is re-checked (and
# re-fetched if an earlier download was interrupted) -- never blocks the update.
if (Test-Path -LiteralPath $ToolPy) {
    $voiceDir = if ($env:VOICE_CONFIG_DIR) { $env:VOICE_CONFIG_DIR } else {
        Join-Path $(if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { Join-Path $env:USERPROFILE ".config" }) "corvin-voice" }
    $lang = ""
    try {
        $profileJson = Get-Content -LiteralPath (Join-Path $voiceDir "profile.json") -Raw -ErrorAction Stop | ConvertFrom-Json
        $lang = [string]$profileJson.display_language
        if (-not $lang -and $profileJson.identity) { $lang = [string]$profileJson.identity.display_language }
    } catch { }
    $langs = @()
    if ($lang -and $lang -ne "en") { $langs += $lang }
    $langs += "en"
    if ((Invoke-Native -Exe $ToolPy -Arguments (@("-m", "corvinOS.shared.voice_models") + $langs)) -eq 0) { Write-Ok "offline voice model ($($langs -join ' + '))" }
    else { Write-Warn "offline voice model not ready -- the console retries it; online voices still work" }
}

function Start-ConsoleAgain {
    $started = $false
    foreach ($t in $script:RestartTasks) {
        try { Start-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -ErrorAction Stop; $started = $started -or ($t.TaskName -like 'CorvinOS-AutoRestart*' -or $t.TaskName -eq 'CorvinOS-Console') } catch { }
    }
    if (-not $started -and $ConsoleTask) {
        try { Start-ScheduledTask -TaskName $ConsoleTask.TaskName -TaskPath $ConsoleTask.TaskPath -ErrorAction Stop; $started = $true } catch { }
    }
    if (-not $started) {
        $serve = Get-Command corvinos-serve -ErrorAction SilentlyContinue
        if (-not $serve) { Write-Warn "corvinos-serve not found -- cannot start the console"; return }
        Start-Process -FilePath $serve.Source -ArgumentList @("--no-browser", "--port", "$Port") -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $LogDir "console-stdout.log") -RedirectStandardError (Join-Path $LogDir "console-stderr.log")
    }
    $script:Stopped = $false
}

# The entry bundle /console/ hands out. curl.exe --noproxy: a machine-wide
# proxy (PAC) otherwise intercepts 127.0.0.1 and answers with its own page.
function Get-ServedEntry {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    $body = ""
    if ($curl) {
        $null = Invoke-Native -Exe $curl.Source -Arguments @("-s", "--noproxy", "*", "-m", "5", "$BaseUrl/console/")
        $body = $script:LastOutput -join "`n"
    } else {
        try { $body = (Invoke-WebRequest -Uri "$BaseUrl/console/" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop).Content } catch { }
    }
    return [regex]::Match([string]$body, 'assets/index-[^"]*\.js').Value
}

# Live = THIS user's console serves dist's entry bundle. Never a foreign one.
function Wait-Live {
    param([int]$Seconds)
    $want = ""
    if (Test-Path -LiteralPath (Join-Path $Dist "index.html")) {
        $want = [regex]::Match((Get-Content -LiteralPath (Join-Path $Dist "index.html") -Raw), 'assets/index-[^"]*\.js').Value
    }
    $deadline = (Get-Date).AddSeconds($Seconds)
    $got = ""
    while ((Get-Date) -lt $deadline) {
        $state = Get-PortState -TargetPort $Port
        if ($state -eq "foreign") { $script:ForeignPort = $true; return $false }
        if ($state -eq "mine") {
            $got = Get-ServedEntry
            if ($got -and (-not $want -or $got -eq $want)) { return $true }
        }
        Start-Sleep -Seconds 2
    }
    Write-LogFile "served '$got', expected '$want'"
    return $false
}

if ($NoRestart) {
    Write-Ok "restart skipped (-NoRestart) -- restart the console to load the update"
    Exit-SetupLock
    exit 0
}

$script:ForeignPort = $false
if ((Get-PortState -TargetPort $Port) -eq "foreign") {
    Write-Host ""
    Write-Warn "port $Port is held by ANOTHER user's CorvinOS on this machine."
    Write-Warn "The update itself is installed; start yours on a free port: corvinos-serve --port <n>"
    Exit-SetupLock
    exit 2
}
Start-ConsoleAgain
Write-Host "  ... waiting for the console"
$Live = Wait-Live 180
if ($Live) { Write-Ok "console is serving the new build" } else { Write-Warn "console did not come up with the new build" }

# -----------------------------------------------------------------------------
# 6. Verify (over the wire, like a browser)
# -----------------------------------------------------------------------------

Write-Step "[6/6] Verify"
$VerifyOk = $false
if ($Live -and $PkgOk) {
    $verifyScript = Join-Path $Src "scripts\verify_install.py"
    # Piped stdout makes Python fall back to the ANSI code page, where the
    # checker's own output characters raise UnicodeEncodeError -- which would
    # read as a failed verification and roll back a good update.
    $env:PYTHONIOENCODING = "utf-8"
    $py = if (Test-Path -LiteralPath $ToolPy) { $ToolPy } else { "" }
    if ($py -and (Test-Path -LiteralPath $verifyScript)) {
        $code = Invoke-Native -Exe $py -Arguments @($verifyScript, "--url", $BaseUrl, "--dist", $Dist, "--wait", "60")
        foreach ($l in $script:LastOutput) { Write-Host "  $l" }
        $VerifyOk = ($code -eq 0)
    } else {
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curl) { $VerifyOk = ((Invoke-Native -Exe $curl.Source -Arguments @("-fs", "--noproxy", "*", "-m", "5", "$BaseUrl/v1/console/healthz")) -eq 0) }
    }
}

if ($VerifyOk) {
    Write-Host ""
    Write-Host "CorvinOS is up to date and running." -ForegroundColor Green
    Write-Host "  $BaseUrl/console/   (reload the tab: Ctrl+Shift+R)"
    if ($script:BackupRef) { Write-Host "  previous code kept on branch: $($script:BackupRef)" }
    if ($script:Stashed) { Write-Host "  your local changes: git stash list  (re-apply: git stash pop)" }
    $null = Remove-Tree "$Src.prev"
    Exit-SetupLock
    exit 0
}

# -----------------------------------------------------------------------------
# Rollback
# -----------------------------------------------------------------------------

Write-Host ""
if ($script:ForeignPort) {
    Write-Warn "port $Port was taken by ANOTHER user's CorvinOS -- not verifying against it, not rolling back."
    Write-Warn "Start yours on a free port: corvinos-serve --port <n>"
    Exit-SetupLock
    exit 2
}
Write-Host "  The updated console did not verify." -ForegroundColor Red
if ($NoRollback -or $ConsoleOnly) {
    Write-Host "  Kept as is (-NoRollback / -ConsoleOnly). Log: $LogFile"
    Exit-SetupLock
    exit 2
}
Write-Step "Rolling back"
$rb = $true
Stop-Console
if ($PrevRev -and (Test-Path -LiteralPath (Join-Path $Src ".git"))) {
    if ((Invoke-Git @("reset", "-q", "--hard", $PrevRev)) -ne 0) { $rb = $false }
} elseif ($script:ZipSwapped -and (Test-Path -LiteralPath "$Src.prev")) {
    foreach ($keep in @(".corvin", "core\console\corvin_console\web-next\node_modules")) {
        $from = Join-Path $Src $keep
        if (Test-Path -LiteralPath $from) {
            $to = Join-Path "$Src.prev" $keep
            $null = Remove-Tree $to
            $null = New-Item -ItemType Directory -Path (Split-Path -Parent $to) -Force
            Move-Item -LiteralPath $from -Destination $to -ErrorAction SilentlyContinue
        }
    }
    $null = Remove-Tree "$Src.failed"
    Move-Item -LiteralPath $Src -Destination "$Src.failed" -ErrorAction SilentlyContinue
    Move-Item -LiteralPath "$Src.prev" -Destination $Src -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath (Join-Path $Src "pyproject.toml"))) { $rb = $false }
}
if (Test-Path -LiteralPath (Join-Path $Web "dist.prev")) {
    $null = Remove-Tree $Dist
    Rename-Item -LiteralPath (Join-Path $Web "dist.prev") -NewName "dist" -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $Dist)) { $rb = $false }
}
Write-Host "  ... reinstalling the previous version"
if (-not (Install-PackageHealing)) { $rb = $false }
Start-ConsoleAgain
if ($rb -and (Wait-Live 180)) {
    Write-Host "  Rolled back -- the previous version is running. Log: $LogFile" -ForegroundColor Yellow
    Exit-SetupLock
    exit 1
}
Write-Host "  Rollback failed too. Repair: powershell -ExecutionPolicy Bypass -File install.ps1   Log: $LogFile" -ForegroundColor Red
Exit-SetupLock
exit 2
