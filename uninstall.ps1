#Requires -Version 5.1
<#
.SYNOPSIS
    Remove CorvinOS completely from this Windows user profile.

.DESCRIPTION
    Windows twin of uninstall.sh. Order matters and is deliberate:
      1. stop       - every CorvinOS Scheduled Task of THIS user is disabled and
                      ended, then every CorvinOS process of THIS user is stopped
                      (politely, then by force). Stopping first means the backup
                      never reads an audit chain that is still being appended to.
      2. backup     - one .zip of every data/secret directory, BEFORE anything is
                      deleted. The audit chain is the GDPR Art. 30 record; it is
                      archived, never silently discarded. A failed backup aborts
                      the whole uninstall and restarts the tasks step 1 stopped.
      3. unregister - Scheduled Tasks, Startup/Desktop shortcuts, Claude Code
                      plugins and their marketplace.
      4. remove     - the uv tool, its command shims, the installer-managed source
                      tree and the data directories (locked files are retried).
      5. verify     - re-scan for every artefact above; exit 1 naming anything
                      that is left. "Uninstalled" is a measured claim.

    Self-contained: needs nothing from the repository or the installed Python
    package, so it still works when the install is broken.

    Scope. Deletion is confined to this user's profile (USERPROFILE, APPDATA,
    LOCALAPPDATA - which may be redirected to a UNC share on Citrix / roaming
    profiles), plus an explicit CORVIN_HOME. A developer checkout is NEVER
    deleted, only its generated .corvin\ state. The source tree the installer
    itself created (marker file .corvin-managed) is removed. On a multi-session
    host only processes and tasks OWNED BY THE CURRENT USER are touched - another
    user's CorvinOS keeps running.

    Never requires elevation. An elevated always-on service (Scheduled Task
    CorvinOS-AlwaysOn-*) cannot be removed by a standard user; the exact admin
    command is printed instead.

    Works in Constrained Language Mode (AppLocker / WDAC): the main path uses
    cmdlets and native tools only; .NET is used solely as a FullLanguage
    fallback for the zip step.

    ASCII-ONLY BY CONTRACT (see install.ps1: Windows PowerShell 5.1 reads a
    BOM-less script as cp1252).

.PARAMETER Yes
    Do not ask for confirmation (unattended).

.PARAMETER NoBackup
    Skip the safety backup.

.PARAMETER KeepData
    Remove the software only; keep data, secrets and the audit chain.

.PARAMETER DryRun
    Show what would be done; change nothing.

.PARAMETER VerifyOnly
    Only report leftovers (exit 1 if any).

.PARAMETER BackupDir
    Where the backup .zip is written (default: CORVIN_BACKUP_DIR, else
    USERPROFILE).

.PARAMETER Port
    Console port checked for a leftover listener (default: CORVIN_CONSOLE_PORT,
    else 8765).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Yes -NoBackup

.NOTES
    Exit codes:
      0 = removed completely (or: -VerifyOnly found nothing)
      1 = something is left (named above), or the backup failed (nothing removed)
      2 = refused: not interactive and -Yes not given
#>

[CmdletBinding()]
param(
    [Alias("y", "Force", "Purge")]
    [switch]$Yes,
    [switch]$NoBackup,
    [switch]$KeepData,
    [switch]$DryRun,
    [switch]$VerifyOnly,
    [string]$BackupDir = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

if ($DryRun) { $Yes = $true }
if (-not $BackupDir) {
    $BackupDir = if ($env:CORVIN_BACKUP_DIR) { $env:CORVIN_BACKUP_DIR } else { $env:USERPROFILE }
}
if (-not (Split-Path -IsAbsolute $BackupDir)) { $BackupDir = Join-Path (Get-Location).ProviderPath $BackupDir }
if ($Port -le 0) {
    $Port = 8765
    if ($env:CORVIN_CONSOLE_PORT -match '^\d+$') { $Port = [int]$env:CORVIN_CONSOLE_PORT }
}

# Constrained Language Mode (AppLocker / WDAC) forbids New-Object on .NET types
# and method calls on non-core types. Everything below is written for it; the
# flag only unlocks the .NET zip fallback.
$FullLanguage = ($ExecutionContext.SessionState.LanguageMode -eq "FullLanguage")

# -----------------------------------------------------------------------------
# Output
# -----------------------------------------------------------------------------

function Write-Title { param([string]$Text) Write-Host ""; Write-Host $Text -ForegroundColor Cyan }
function Write-Ok {
    param([string]$Text)
    if ($DryRun) { Write-Host "  ~ would: $Text" } else { Write-Host "  [ok] $Text" -ForegroundColor Green }
}
function Write-Warn { param([string]$Text) Write-Host "  [!!] $Text" -ForegroundColor Yellow }
function Write-Info { param([string]$Text) Write-Host "  $Text" }

$script:Left = 0
function Write-Left {
    param([string]$Text)
    Write-Host "  [xx] $Text" -ForegroundColor Red
    $script:Left++
}

# Runs a native command without letting stderr become a terminating error, and
# returns exit code + output. Native tools never throw; $LASTEXITCODE is the
# only signal.
function Invoke-Quiet {
    param([Parameter(Mandatory = $true)][string]$Exe, [string[]]$Arguments = @())
    $out = @()
    $code = 1
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = @(& $Exe @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } catch {
        # Could not even be launched (missing, or blocked by AppLocker): -1, so
        # no caller can mistake it for a tool's own "partial success" code.
        $out = @("$($_.Exception.Message)")
        $code = -1
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($null -eq $code) { $code = 0 }
    return @{ Code = $code; Out = $out }
}

# -----------------------------------------------------------------------------
# Identity (multi-session hosts: never touch another user's CorvinOS)
# -----------------------------------------------------------------------------

$MeUser   = $env:USERNAME
$MeDomain = $env:USERDOMAIN
$MeSid    = ""
$whoUser = Invoke-Quiet -Exe "whoami.exe" -Arguments @("/user", "/fo", "csv", "/nh")
if ($whoUser.Code -eq 0 -and $whoUser.Out.Count -gt 0) {
    $fields = @(([string]$whoUser.Out[-1]).Split(',') | ForEach-Object { $_.Trim('"') })
    if ($fields.Count -ge 2 -and $fields[1] -like 'S-1-*') { $MeSid = $fields[1] }
}
$IsElevated = $false
$whoGroups = Invoke-Quiet -Exe "whoami.exe" -Arguments @("/groups", "/fo", "csv", "/nh")
if ($whoGroups.Code -eq 0) {
    # High (12288) or System (16384) mandatory level = an elevated token.
    $IsElevated = [bool](@($whoGroups.Out | Where-Object { $_ -match 'S-1-16-12288|S-1-16-16384' }).Count)
}

function Test-IsMyAccount {
    param([string]$Account)
    if (-not $Account) { return $false }
    if ($MeSid -and $Account -eq $MeSid) { return $true }
    if ($Account -eq $MeUser) { return $true }
    if ($Account -eq "$MeDomain\$MeUser") { return $true }
    # "user@domain" (UPN) form
    if ($Account -like "$MeUser@*") { return $true }
    return $false
}

# Owner of a Win32_Process via GetOwner. A process we cannot query (another
# user's, without admin) is NOT ours.
function Test-ProcessIsMine {
    param($CimProcess)
    try {
        $o = Invoke-CimMethod -InputObject $CimProcess -MethodName GetOwner -ErrorAction Stop
        if ($o.ReturnValue -ne 0) { return $false }
        return (($o.User -eq $MeUser) -and ((-not $o.Domain) -or ($o.Domain -eq $MeDomain)))
    } catch {
        return $false
    }
}

# This process and everything that launched it - an uninstaller that kills its
# own shell cannot report a result.
function Get-AncestorPid {
    $ids = @($PID)
    $current = $PID
    for ($i = 0; $i -lt 16; $i++) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue
        if (-not $p -or -not $p.ParentProcessId) { break }
        $current = [int]$p.ParentProcessId
        if ($current -le 0 -or $ids -contains $current) { break }
        $ids += $current
    }
    return $ids
}
$SelfPids = Get-AncestorPid

# -----------------------------------------------------------------------------
# Paths (no drive letters assumed; UNC-redirected profiles work with
# -LiteralPath everywhere)
# -----------------------------------------------------------------------------

function Get-NormPath {
    param([string]$Path)
    if (-not $Path) { return "" }
    $p = $Path
    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        $p = $item.FullName
    } catch { }
    if ($p.Length -gt 3) { $p = $p.TrimEnd('\', '/') }
    return $p
}

function Test-PathUnder {
    param([string]$Path, [string]$Root)
    if (-not $Path -or -not $Root) { return $false }
    # "/" is a valid Windows separator too (and lets this run under pwsh on
    # Linux for testing).
    $p = (Get-NormPath $Path).ToLowerInvariant().Replace('/', '\')
    $r = (Get-NormPath $Root).ToLowerInvariant().Replace('/', '\')
    return $p.StartsWith($r + '\')
}

$ProfileRoots = @()
foreach ($r in @($env:USERPROFILE, $env:APPDATA, $env:LOCALAPPDATA)) {
    if ($r -and (Test-Path -LiteralPath $r)) { $ProfileRoots += (Get-NormPath $r) }
}

function Test-InProfile {
    param([string]$Path)
    foreach ($r in $ProfileRoots) { if (Test-PathUnder $Path $r) { return $true } }
    return $false
}

# Refuses a profile root, anything that CONTAINS a profile root, and a
# drive/share root - a last fence against a mis-set CORVIN_HOME.
function Test-SafeToDelete {
    param([string]$Path)
    $p = Get-NormPath $Path
    if (-not $p) { return $false }
    foreach ($r in $ProfileRoots) {
        if ($p -eq $r -or (Test-PathUnder $r $p)) { return $false }
    }
    $segments = @($p -split '[\\/]' | Where-Object { $_ })
    if ($p.StartsWith('\\')) { return ($segments.Count -ge 3) }
    return ($segments.Count -ge 2)
}

# uv: on PATH, or where its installer puts it.
$UvExe = ""
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCmd) { $UvExe = $uvCmd.Source }
foreach ($c in @((Join-Path $env:USERPROFILE ".local\bin\uv.exe"), (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"))) {
    if (-not $UvExe -and (Test-Path -LiteralPath $c)) { $UvExe = $c }
}

# The corvinos tool venv. `uv tool dir` is authoritative; the rest are the
# defaults of the uv versions in the wild (APPDATA is ROAMING, and on a Citrix
# profile it may be a UNC path).
$ToolDirCandidates = @()
if ($env:UV_TOOL_DIR) { $ToolDirCandidates += $env:UV_TOOL_DIR }
if ($UvExe) {
    $r = Invoke-Quiet -Exe $UvExe -Arguments @("tool", "dir")
    if ($r.Code -eq 0 -and $r.Out.Count -gt 0) { $ToolDirCandidates += ([string]$r.Out[-1]).Trim() }
}
if ($env:APPDATA)      { $ToolDirCandidates += (Join-Path $env:APPDATA "uv\tools"); $ToolDirCandidates += (Join-Path $env:APPDATA "uv\data\tools") }
if ($env:LOCALAPPDATA) { $ToolDirCandidates += (Join-Path $env:LOCALAPPDATA "uv\tools") }
$ToolEnvs = @()
foreach ($d in $ToolDirCandidates) {
    if (-not $d) { continue }
    $envDir = Join-Path $d "corvinos"
    if ((Test-Path -LiteralPath $envDir) -and ($ToolEnvs -notcontains (Get-NormPath $envDir))) {
        $ToolEnvs += (Get-NormPath $envDir)
    }
}
# Lower-cased once: the process scan below compares every running executable
# against these, repeatedly, and must stay cheap.
$ToolEnvsLower = @($ToolEnvs | ForEach-Object { $_.ToLowerInvariant() + '\' })

# TOML basic strings escape backslashes: "C:\\Users\\me" -> C:\Users\me
function Get-ReceiptValues {
    param([string]$Receipt, [string]$Key)
    $values = @()
    if (-not (Test-Path -LiteralPath $Receipt)) { return $values }
    $text = Get-Content -LiteralPath $Receipt -Raw -ErrorAction SilentlyContinue
    if (-not $text) { return $values }
    foreach ($m in [regex]::Matches($text, [regex]::Escape($Key) + '\s*=\s*"([^"]*)"')) {
        $values += $m.Groups[1].Value.Replace('\\', '\')
    }
    return $values
}

$ReceiptShims = @()
$SrcDirs = @()
foreach ($te in $ToolEnvs) {
    $receipt = Join-Path $te "uv-receipt.toml"
    foreach ($e in (Get-ReceiptValues $receipt "editable")) {
        if ($e -and (Test-Path -LiteralPath (Join-Path $e "pyproject.toml"))) {
            $n = Get-NormPath $e
            if ($SrcDirs -notcontains $n) { $SrcDirs += $n }
        }
    }
    foreach ($s in (Get-ReceiptValues $receipt "install-path")) { if ($s) { $ReceiptShims += $s } }
}

$ManagedSrc = if ($env:CORVIN_SRC_DIR) { $env:CORVIN_SRC_DIR } else { Join-Path $env:LOCALAPPDATA "corvinos\src" }
$ManagedSrc = Get-NormPath $ManagedSrc
$ManagedIsOurs = Test-Path -LiteralPath (Join-Path $ManagedSrc ".corvin-managed")
if ((Test-Path -LiteralPath (Join-Path $ManagedSrc "pyproject.toml")) -and ($SrcDirs -notcontains $ManagedSrc)) {
    $SrcDirs += $ManagedSrc
}

$XdgCfg = if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { Join-Path $env:USERPROFILE ".config" }
$VoiceDir = if ($env:VOICE_CONFIG_DIR) { $env:VOICE_CONFIG_DIR } else { Join-Path $XdgCfg "corvin-voice" }

$DataDirs = @()
function Add-DataDir {
    param([string]$Path, [switch]$Explicit)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return }
    $n = Get-NormPath $Path
    if ($script:DataDirs -contains $n) { return }
    if (-not (Test-SafeToDelete $n)) {
        Write-Warn "skipping $n -- refusing to delete a profile or drive root"
        return
    }
    if (-not $Explicit -and -not (Test-InProfile $n)) {
        Write-Info "(skipping $n -- outside the user profile; set CORVIN_HOME to include it)"
        return
    }
    $script:DataDirs += $n
}
Add-DataDir $env:CORVIN_HOME -Explicit
Add-DataDir (Join-Path $env:USERPROFILE ".corvin")
foreach ($s in $SrcDirs) { Add-DataDir (Join-Path $s ".corvin") }
Add-DataDir $VoiceDir
Add-DataDir (Join-Path $XdgCfg "claude-cowork")
$CorvinHomeReal = if ($env:CORVIN_HOME) { Get-NormPath $env:CORVIN_HOME } else { Get-NormPath (Join-Path $env:USERPROFILE ".corvin") }

$BinDirs = @()
foreach ($b in @($env:UV_TOOL_BIN_DIR, $env:XDG_BIN_HOME, (Join-Path $env:USERPROFILE ".local\bin"))) {
    if ($b -and (Test-Path -LiteralPath $b) -and ($BinDirs -notcontains (Get-NormPath $b))) { $BinDirs += (Get-NormPath $b) }
}

# Shortcut folders from the shell's own registry, because Desktop and Startup
# are routinely redirected (OneDrive "Known Folder Move", Citrix folder
# redirection to \\server\share). Get-ItemProperty expands REG_EXPAND_SZ.
$ShortcutDirs = @()
try {
    $usf = Get-ItemProperty -LiteralPath "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" -ErrorAction Stop
    foreach ($name in @("Desktop", "Startup", "Programs")) {
        $v = $usf.$name
        if ($v) { $ShortcutDirs += [string]$v }
    }
} catch { }
if ($env:APPDATA) {
    $ShortcutDirs += (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup")
    $ShortcutDirs += (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs")
}
$ShortcutDirs += (Join-Path $env:USERPROFILE "Desktop")
foreach ($od in @($env:OneDrive, $env:OneDriveCommercial, $env:OneDriveConsumer)) {
    if ($od) { $ShortcutDirs += (Join-Path $od "Desktop") }
}

function Get-Shortcuts {
    $found = @()
    foreach ($d in $ShortcutDirs) {
        if (-not $d) { continue }
        $lnk = Join-Path $d "CorvinOS.lnk"
        if ((Test-Path -LiteralPath $lnk) -and ($found -notcontains (Get-NormPath $lnk))) { $found += (Get-NormPath $lnk) }
    }
    return $found
}

# -----------------------------------------------------------------------------
# Scheduled Tasks
# -----------------------------------------------------------------------------
#
# Every name any CorvinOS installer has registered:
#   install.ps1          \CorvinOS\CorvinOS-AutoRestart (or -AutoRestart-<user>)
#   bridge.ps1           CorvinOS-Console, CorvinOS-Bridge-<name>
#   service_manager.py   \CorvinOS\<name>
#   system_service_mgr   CorvinOS-AlwaysOn-<name>   (elevated, S4U principal)
# The task namespace is MACHINE-wide, so on a multi-session host a match is
# only ours when its principal (or, failing that, its action) is.

$ScheduledTasksAvailable = [bool](Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)

function Test-TaskNameIsCorvin {
    param([string]$Name, [string]$Path)
    if ($Path -like '\CorvinOS\*') { return $true }
    foreach ($pat in @('CorvinOS-Console', 'CorvinOS-AutoRestart*', 'CorvinOS-Bridge-*', 'CorvinOS-AlwaysOn-*')) {
        if ($Name -like $pat) { return $true }
    }
    return $false
}

function Test-TaskIsMine {
    param($Task)
    $uid = ""
    try { $uid = [string]$Task.Principal.UserId } catch { }
    if ($uid) { return (Test-IsMyAccount $uid) }
    # No principal user recorded: decide by what the task would run.
    foreach ($a in @($Task.Actions)) {
        $blob = ("{0} {1}" -f $a.Execute, $a.Arguments).ToLowerInvariant()
        foreach ($r in $ProfileRoots) {
            if ($blob.Contains($r.ToLowerInvariant())) { return $true }
        }
    }
    return $false
}

$script:ForeignTasks = @()
function Get-CorvinTasks {
    $mine = @()
    $script:ForeignTasks = @()
    if ($ScheduledTasksAvailable) {
        foreach ($t in @(Get-ScheduledTask -ErrorAction SilentlyContinue)) {
            if (-not (Test-TaskNameIsCorvin $t.TaskName $t.TaskPath)) { continue }
            $entry = @{ Name = $t.TaskName; Path = $t.TaskPath; State = [string]$t.State; Task = $t }
            if (Test-TaskIsMine $t) { $mine += $entry } else { $script:ForeignTasks += $entry }
        }
        return $mine
    }
    # No ScheduledTasks module (stripped image): schtasks.exe lists only what
    # this account may read, which on a standard account is its own tasks.
    $r = Invoke-Quiet -Exe "schtasks.exe" -Arguments @("/query", "/fo", "csv", "/nh")
    foreach ($line in $r.Out) {
        $f = @(([string]$line).Split(',') | ForEach-Object { $_.Trim('"') })
        if ($f.Count -lt 1 -or -not $f[0].StartsWith('\')) { continue }
        $full = $f[0]
        $idx = $full.LastIndexOf('\')
        $name = $full.Substring($idx + 1)
        $path = $full.Substring(0, $idx + 1)
        if (-not (Test-TaskNameIsCorvin $name $path)) { continue }
        $state = if ($f.Count -ge 3) { $f[2] } else { "" }
        if (@($mine | Where-Object { $_.Path + $_.Name -eq $full }).Count -eq 0) {
            $mine += @{ Name = $name; Path = $path; State = $state; Task = $null }
        }
    }
    return $mine
}

function Get-AdminTaskCommand {
    param($Entry)
    return "Unregister-ScheduledTask -TaskName '$($Entry.Name)' -TaskPath '$($Entry.Path)' -Confirm:`$false   (admin PowerShell; or: corvin-service uninstall)"
}

# -----------------------------------------------------------------------------
# Processes
# -----------------------------------------------------------------------------
#
# Confined to interpreter/launcher executables: matching command lines across
# ALL processes is not safe - an editor with adapter.py open matches too, and a
# looser *corvin* pattern was measured matching EXCEL.EXE (update.ps1).
$HostNames = @("python.exe", "pythonw.exe", "node.exe", "powershell.exe", "pwsh.exe",
               "wscript.exe", "cscript.exe", "cmd.exe", "uv.exe")
$ProcPattern = 'corvin_gateway\.app|corvin_console\.standalone|corvinos-serve|corvin-serve|corvin-service|bridges[\\/]shared[\\/]adapter\.py|corvin_operator[\\/]bridges[\\/][a-z]+[\\/]daemon\.js|corvin-supervisor\.ps1|corvin-watchdog|uv[\\/]tools[\\/]corvinos[\\/]|tool\s+(install|upgrade)\s.*corvinos'

function Get-CorvinProcesses {
    $hits = @()
    foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        $procId = [int]$p.ProcessId
        if ($procId -le 4 -or $SelfPids -contains $procId) { continue }
        $name = [string]$p.Name
        $exe  = [string]$p.ExecutablePath
        $cmd  = [string]$p.CommandLine
        if ($cmd -match 'uninstall\.ps1') { continue }
        $match = $false
        if ($exe) {
            $exeLower = $exe.ToLowerInvariant()
            foreach ($te in $ToolEnvsLower) { if ($exeLower.StartsWith($te)) { $match = $true } }
        }
        if (-not $match -and $name -like 'corvin*') { $match = $true }
        if (-not $match -and ($HostNames -contains $name.ToLowerInvariant()) -and ($cmd -match $ProcPattern)) { $match = $true }
        if (-not $match) { continue }
        if (-not (Test-ProcessIsMine $p)) { continue }
        $short = if ($cmd.Length -gt 110) { $cmd.Substring(0, 110) + "..." } else { $cmd }
        $hits += @{ Id = $procId; Name = $name; Cmd = $short }
    }
    return $hits
}

function Stop-CorvinProcesses {
    $procs = @(Get-CorvinProcesses)
    if ($procs.Count -eq 0) { return }
    foreach ($p in $procs) {
        if ($DryRun) { Write-Info "[dry-run] would stop PID $($p.Id) $($p.Name): $($p.Cmd)"; continue }
        # Polite first (WM_CLOSE to windowed processes; the tree with it)...
        $null = Invoke-Quiet -Exe "taskkill.exe" -Arguments @("/PID", "$($p.Id)", "/T")
    }
    if ($DryRun) { return }
    for ($i = 0; $i -lt 10; $i++) {
        if (@(Get-CorvinProcesses).Count -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    # ...then by force. A console process has no window, so this is the normal path.
    foreach ($p in @(Get-CorvinProcesses)) {
        $null = Invoke-Quiet -Exe "taskkill.exe" -Arguments @("/PID", "$($p.Id)", "/T", "/F")
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    }
    for ($i = 0; $i -lt 10; $i++) {
        if (@(Get-CorvinProcesses).Count -eq 0) { return }
        Start-Sleep -Seconds 1
    }
}

# Listener PIDs on the console port. Get-NetTCPConnection is missing on some
# editions and throws when the port is free, so netstat is the fallback.
function Get-PortListenerPids {
    $pids = @()
    $viaCmdlet = $false
    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        $viaCmdlet = $true
        foreach ($c in @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
            if ($c.OwningProcess -and ($pids -notcontains [int]$c.OwningProcess)) { $pids += [int]$c.OwningProcess }
        }
    }
    if (-not $viaCmdlet) {
        $r = Invoke-Quiet -Exe "netstat.exe" -Arguments @("-ano", "-p", "TCP")
        foreach ($line in $r.Out) {
            if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
                if ($pids -notcontains [int]$Matches[1]) { $pids += [int]$Matches[1] }
            }
        }
    }
    return $pids
}

# -----------------------------------------------------------------------------
# Shims
# -----------------------------------------------------------------------------

function Get-Shims {
    $found = @()
    foreach ($s in $ReceiptShims) {
        if ((Test-Path -LiteralPath $s) -and ($found -notcontains $s)) { $found += $s }
    }
    foreach ($b in $BinDirs) {
        foreach ($f in @(Get-ChildItem -LiteralPath $b -File -Force -ErrorAction SilentlyContinue)) {
            if ($f.Name -notlike 'corvin*') { continue }
            if ($found -contains $f.FullName) { continue }
            # Only shims that belong to the corvinos tool: uv's trampoline .exe
            # embeds the path of the venv script it launches. A user's own
            # corvin-foo.exe does not.
            if (Select-String -LiteralPath $f.FullName -Pattern 'tools[\\/]corvinos[\\/]' -Quiet -ErrorAction SilentlyContinue) {
                $found += $f.FullName
            }
        }
    }
    return $found
}

# -----------------------------------------------------------------------------
# Deletion with retries (AV, Windows Search indexer and OneDrive hold handles
# for seconds after a process exits) and a long-path fallback
# -----------------------------------------------------------------------------

function Get-ExtendedPath {
    param([string]$Path)
    if ($Path.StartsWith('\\?\')) { return $Path }
    if ($Path.StartsWith('\\')) { return '\\?\UNC\' + $Path.Substring(2) }
    return '\\?\' + $Path
}

$script:EmptyDir = ""
function Remove-Tree {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $Path)) { return $true }
        Start-Sleep -Seconds $attempt
    }
    # MAX_PATH: a \\?\ path bypasses the 260-char limit where the provider
    # supports it...
    Remove-Item -LiteralPath (Get-ExtendedPath $Path) -Recurse -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    # ...and robocopy /MIR from an empty directory empties any tree, long paths
    # included (it uses the wide API natively).
    if (-not $script:EmptyDir) {
        $script:EmptyDir = Join-Path $env:TEMP ("corvin-empty-" + [guid]::NewGuid().ToString("N"))
        $null = New-Item -ItemType Directory -Path $script:EmptyDir -Force -ErrorAction SilentlyContinue
    }
    $null = Invoke-Quiet -Exe "robocopy.exe" -Arguments @($script:EmptyDir, $Path, "/MIR", "/R:2", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    # Name what is still held so the operator can close it.
    $stuck = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue | Select-Object -First 10)
    foreach ($s in $stuck) { Write-Warn "   locked: $($s.FullName)" }
    if ($stuck.Count -gt 0) {
        Write-Warn "   (held open by a program -- antivirus, Search indexer, OneDrive or an open Explorer/editor window)"
    }
    return $false
}

# -----------------------------------------------------------------------------
# Verify (also the -VerifyOnly report)
# -----------------------------------------------------------------------------

function Invoke-Verify {
    $script:Left = 0
    foreach ($t in @(Get-CorvinTasks)) { Write-Left "scheduled task: $($t.Path)$($t.Name)" }
    foreach ($t in $script:ForeignTasks) {
        Write-Info "(not ours, left alone: scheduled task $($t.Path)$($t.Name) of another user)"
    }
    foreach ($p in @(Get-CorvinProcesses)) { Write-Left "process $($p.Id) $($p.Name): $($p.Cmd)" }
    foreach ($lp in @(Get-PortListenerPids)) {
        $cp = Get-CimInstance Win32_Process -Filter "ProcessId=$lp" -ErrorAction SilentlyContinue
        if ($cp -and (Test-ProcessIsMine $cp)) {
            Write-Left "TCP $Port is still listened on by PID $lp ($($cp.Name))"
        } else {
            Write-Info "(TCP $Port is held by PID $lp of another user -- not ours)"
        }
    }
    foreach ($te in $ToolEnvs) { if (Test-Path -LiteralPath $te) { Write-Left "uv tool env: $te" } }
    foreach ($s in @(Get-Shims)) { Write-Left "command shim: $s" }
    foreach ($l in @(Get-Shortcuts)) { Write-Left "shortcut: $l" }
    if (-not $KeepData) {
        foreach ($d in $DataDirs) { if (Test-Path -LiteralPath $d) { Write-Left "data dir: $d" } }
    }
    if (Test-Path -LiteralPath (Join-Path $ManagedSrc ".corvin-managed")) { Write-Left "managed source tree: $ManagedSrc" }
}

if ($VerifyOnly) {
    Write-Title "CorvinOS leftover scan"
    Invoke-Verify
    if ($script:Left -eq 0) { Write-Host "  [ok] nothing left -- CorvinOS is not installed for $MeUser" -ForegroundColor Green; exit 0 }
    Write-Host "  $($script:Left) artefact(s) remain"
    exit 1
}

# -----------------------------------------------------------------------------
# Confirm
# -----------------------------------------------------------------------------

Write-Title "CorvinOS uninstaller"
Write-Info "User          : $MeDomain\$MeUser$(if ($IsElevated) { ' (elevated)' })"
Write-Info ("Data          : " + $(if ($DataDirs.Count) { $DataDirs -join '; ' } else { '(none found)' }))
Write-Info ("uv tool env   : " + $(if ($ToolEnvs.Count) { $ToolEnvs -join '; ' } else { '(not installed)' }))
Write-Info ("Source trees  : " + $(if ($SrcDirs.Count) { $SrcDirs -join '; ' } else { '(none)' }))
if ($KeepData) { Write-Info "Mode          : keep data (software only)" }
if (-not $FullLanguage) { Write-Info "Language mode : $($ExecutionContext.SessionState.LanguageMode) (policy-restricted; cmdlet-only paths used)" }
Write-Host ""

if (-not $Yes) {
    $answer = ""
    try {
        $suffix = if (-not $NoBackup -and -not $KeepData) { " (a backup is taken first)" } else { "" }
        $answer = Read-Host "  Remove CorvinOS completely${suffix}? [y/N]"
    } catch {
        Write-Host "  Not interactive and -Yes not given -- refusing to uninstall unattended." -ForegroundColor Yellow
        Write-Host "  Re-run with: powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Yes"
        exit 2
    }
    if ($answer -notmatch '^(y|yes|j|ja)$') {
        Write-Host "  Aborted -- nothing changed."
        exit 0
    }
}

# -----------------------------------------------------------------------------
# 1. Stop
# -----------------------------------------------------------------------------

Write-Title "[1/5] Stopping scheduled tasks and processes"
$script:StoppedTasks = @()
$script:AdminCommands = @()
foreach ($t in @(Get-CorvinTasks)) {
    $label = "$($t.Path)$($t.Name)"
    if ($DryRun) { Write-Info "[dry-run] would disable + end task $label"; continue }
    $wasRunning = ($t.State -eq "Running")
    $wasEnabled = ($t.State -ne "Disabled")
    if ($ScheduledTasksAvailable) {
        # Disable BEFORE ending: a task with a restart policy (always-on) or a
        # supervisor loop would otherwise bring the console straight back.
        $disabled = $true
        try { $null = Disable-ScheduledTask -TaskName $t.Name -TaskPath $t.Path -ErrorAction Stop } catch { $disabled = $false }
        try { $null = Stop-ScheduledTask -TaskName $t.Name -TaskPath $t.Path -ErrorAction Stop } catch { }
        if (-not $disabled -and $t.Name -like 'CorvinOS-AlwaysOn-*' -and -not $IsElevated) {
            Write-Warn "$label is an elevated always-on service -- a standard user cannot stop or remove it"
            $script:AdminCommands += (Get-AdminTaskCommand $t)
        }
    } else {
        $null = Invoke-Quiet -Exe "schtasks.exe" -Arguments @("/change", "/tn", $label, "/disable")
        $null = Invoke-Quiet -Exe "schtasks.exe" -Arguments @("/end", "/tn", $label)
    }
    $script:StoppedTasks += @{ Name = $t.Name; Path = $t.Path; WasRunning = $wasRunning; WasEnabled = $wasEnabled }
    Write-Ok "stopped task $label"
}
Stop-CorvinProcesses
if (-not $DryRun) {
    $survivors = @(Get-CorvinProcesses)
    if ($survivors.Count -eq 0) { Write-Ok "no CorvinOS process of $MeUser running" }
    else { foreach ($s in $survivors) { Write-Warn "survived: PID $($s.Id) $($s.Name)" } }
}

# Undo step 1 -- used only when the backup fails, so an aborted uninstall
# leaves the operator with a RUNNING CorvinOS, not a stopped one.
function Restore-StoppedTasks {
    $any = $false
    foreach ($t in $script:StoppedTasks) {
        $label = "$($t.Path)$($t.Name)"
        if ($ScheduledTasksAvailable) {
            if ($t.WasEnabled) { try { $null = Enable-ScheduledTask -TaskName $t.Name -TaskPath $t.Path -ErrorAction Stop } catch { } }
            if ($t.WasRunning) { try { $null = Start-ScheduledTask -TaskName $t.Name -TaskPath $t.Path -ErrorAction Stop; $any = $true } catch { } }
        } else {
            if ($t.WasEnabled) { $null = Invoke-Quiet -Exe "schtasks.exe" -Arguments @("/change", "/tn", $label, "/enable") }
            if ($t.WasRunning) { $null = Invoke-Quiet -Exe "schtasks.exe" -Arguments @("/run", "/tn", $label); $any = $true }
        }
    }
    if ($any) { Write-Host "  Scheduled tasks restarted -- CorvinOS is running as before." -ForegroundColor Yellow }
    elseif ($script:StoppedTasks.Count -gt 0) { Write-Host "  Scheduled tasks re-enabled." -ForegroundColor Yellow }
}

# -----------------------------------------------------------------------------
# 2. Backup
# -----------------------------------------------------------------------------

# Label under which a directory is stored in the zip: relative to the profile
# folder it lives in, so the archive reads the same on any machine.
function Get-BackupLabel {
    param([string]$Path)
    $n = Get-NormPath $Path
    foreach ($pair in @(@("LOCALAPPDATA", $env:LOCALAPPDATA), @("APPDATA", $env:APPDATA), @("USERPROFILE", $env:USERPROFILE))) {
        $root = Get-NormPath $pair[1]
        if ($root -and (Test-PathUnder $n $root)) { return (Join-Path $pair[0] $n.Substring($root.Length + 1)) }
    }
    return (Join-Path "OTHER" (($n -replace '^\\\\', 'UNC_') -replace '[:\\/]+', '_'))
}

function Get-ZipFileCount {
    param([string]$Zip)
    if ($FullLanguage) {
        try {
            Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
            $za = [System.IO.Compression.ZipFile]::OpenRead($Zip)
            try { return @($za.Entries | Where-Object { $_.Name }).Count } finally { $za.Dispose() }
        } catch { }
    }
    $tar = Join-Path $env:SystemRoot "System32\tar.exe"
    if (Test-Path -LiteralPath $tar) {
        $r = Invoke-Quiet -Exe $tar -Arguments @("-tf", $Zip)
        if ($r.Code -eq 0) { return @($r.Out | Where-Object { $_ -and -not $_.EndsWith('/') }).Count }
    }
    return -1
}

function Invoke-Backup {
    param([string]$ZipPath)
    $staging = "$ZipPath.staging"
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    $null = New-Item -ItemType Directory -Path $staging -Force -ErrorAction Stop
    $restore = @("CorvinOS backup taken $(Get-Date -Format s) by $MeDomain\$MeUser on $env:COMPUTERNAME.",
                 "Each folder below maps back to the original location:", "")
    try {
        foreach ($d in $DataDirs) {
            $label = Get-BackupLabel $d
            $dest = Join-Path $staging $label
            $restore += "  $label  ->  $d"
            # robocopy: long paths natively, retries on a transiently locked file,
            # exclusion by directory NAME at any depth. Re-downloadable runtimes
            # are excluded; everything a user cannot get back (secrets, pairing,
            # audit chain, sessions, models) is kept.
            $rcArgs = @($d, $dest, "/E", "/COPY:DAT", "/R:3", "/W:2", "/XJ", "/NP", "/NFL", "/NDL", "/NJH", "/NJS",
                        "/XD", "node_modules", "venv", ".venv", "__pycache__", (Join-Path $CorvinHomeReal "node"),
                        "/XF", "*.sock")
            $r = Invoke-Quiet -Exe "robocopy.exe" -Arguments $rcArgs
            # robocopy: 0-7 = success variants, >= 8 = at least one copy failed.
            if ($r.Code -lt 0 -or $r.Code -ge 8) {
                $errs = @($r.Out | Where-Object { $_ -match 'ERROR' } | Select-Object -First 8)
                throw ("could not copy $d (robocopy exit $($r.Code))`n    " + ($errs -join "`n    "))
            }
        }
        if ($ScheduledTasksAvailable) {
            $taskDir = Join-Path $staging "scheduled-tasks"
            foreach ($t in $script:StoppedTasks) {
                try {
                    $xml = Export-ScheduledTask -TaskName $t.Name -TaskPath $t.Path -ErrorAction Stop
                    $null = New-Item -ItemType Directory -Path $taskDir -Force
                    Set-Content -LiteralPath (Join-Path $taskDir "$($t.Name).xml") -Value $xml -Encoding Unicode
                    $restore += "  scheduled-tasks\$($t.Name).xml  ->  Register-ScheduledTask -TaskPath '$($t.Path)' -TaskName '$($t.Name)' -Xml (Get-Content -Raw <file>)"
                } catch { }
            }
        }
        Set-Content -LiteralPath (Join-Path $staging "RESTORE.txt") -Value $restore -Encoding ASCII

        # Compress-Archive skips Hidden/System files; clear those bits on the
        # COPY so nothing is silently left out.
        $null = Invoke-Quiet -Exe "attrib.exe" -Arguments @("-H", "-S", (Join-Path $staging "*"), "/S", "/D")
        $expected = @(Get-ChildItem -LiteralPath $staging -Recurse -Force -File -ErrorAction SilentlyContinue).Count

        # Three zip writers, because each one fails somewhere: Compress-Archive
        # (5.1) chokes on paths > 260 chars; ZipFile needs FullLanguage; tar.exe
        # (bsdtar, Windows 10 1803+) is the one that needs neither. Each result
        # is verified by its file count, not trusted.
        $methods = @("Compress-Archive")
        if ($FullLanguage) { $methods += "ZipFile" }
        $methods += "tar"
        $lastError = ""
        foreach ($m in $methods) {
            Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
            try {
                switch ($m) {
                    "Compress-Archive" {
                        # -LiteralPath with the explicit children: a "*" wildcard
                        # skips hidden items and breaks on [ ] in a user name.
                        $top = @(Get-ChildItem -LiteralPath $staging -Force | ForEach-Object { $_.FullName })
                        Compress-Archive -LiteralPath $top -DestinationPath $ZipPath -CompressionLevel Optimal -ErrorAction Stop
                    }
                    "ZipFile" {
                        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
                        [System.IO.Compression.ZipFile]::CreateFromDirectory($staging, $ZipPath)
                    }
                    "tar" {
                        $tar = Join-Path $env:SystemRoot "System32\tar.exe"
                        if (-not (Test-Path -LiteralPath $tar)) { throw "tar.exe not present" }
                        $r = Invoke-Quiet -Exe $tar -Arguments @("-a", "-c", "-f", $ZipPath, "-C", $staging, ".")
                        if ($r.Code -ne 0) { throw ("tar exit $($r.Code): " + (($r.Out | Select-Object -Last 3) -join ' ')) }
                    }
                }
                if (-not (Test-Path -LiteralPath $ZipPath)) { throw "no archive written" }
                $got = Get-ZipFileCount $ZipPath
                if ($got -ge 0 -and $got -lt $expected) { throw "archive holds $got of $expected files" }
                return $m
            } catch {
                $lastError = "$m`: $($_.Exception.Message)"
                Write-Info "   ($lastError -- trying the next method)"
            }
        }
        throw "every zip method failed; last: $lastError"
    } finally {
        $null = Remove-Tree $staging
    }
}

$BackupFile = ""
if (-not $NoBackup -and -not $KeepData -and $DataDirs.Count -gt 0) {
    Write-Title "[2/5] Backup"
    $BackupFile = Join-Path $BackupDir ("corvin-backup-{0}.zip" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    if ($DryRun) {
        Write-Info "[dry-run] would write $BackupFile"
    } else {
        try {
            foreach ($d in $DataDirs) {
                if ((Get-NormPath $BackupDir) -eq $d -or (Test-PathUnder $BackupDir $d)) {
                    throw "-BackupDir $BackupDir lies inside $d, which is about to be deleted"
                }
            }
            if (-not (Test-Path -LiteralPath $BackupDir)) { $null = New-Item -ItemType Directory -Path $BackupDir -Force -ErrorAction Stop }
            $how = Invoke-Backup -ZipPath $BackupFile
            # The archive holds secrets and pairing keys: owner-only, like the
            # chmod 600 of uninstall.sh. Best effort -- a share may refuse ACLs.
            $null = Invoke-Quiet -Exe "icacls.exe" -Arguments @($BackupFile, "/inheritance:r", "/grant:r", "${MeDomain}\${MeUser}:(F)")
            $sizeMb = "{0:N1}" -f ((Get-Item -LiteralPath $BackupFile).Length / 1MB)
            Write-Ok "backup: $BackupFile ($sizeMb MB, via $how)"
            Write-Info "   restore later: Expand-Archive '$BackupFile' <folder>, then see RESTORE.txt inside"
        } catch {
            Remove-Item -LiteralPath $BackupFile -Force -ErrorAction SilentlyContinue
            Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
            Write-Host "  Backup failed -- nothing was removed. Free space in $BackupDir, pass -BackupDir, or -NoBackup." -ForegroundColor Red
            Restore-StoppedTasks
            exit 1
        }
    }
} else {
    Write-Title "[2/5] Backup -- skipped"
}

# -----------------------------------------------------------------------------
# 3. Unregister
# -----------------------------------------------------------------------------

Write-Title "[3/5] Removing autostart entries and integrations"
foreach ($t in @(Get-CorvinTasks)) {
    $label = "$($t.Path)$($t.Name)"
    if ($DryRun) { Write-Info "[dry-run] would unregister task $label"; continue }
    $removed = $false
    if ($ScheduledTasksAvailable) {
        try { Unregister-ScheduledTask -TaskName $t.Name -TaskPath $t.Path -Confirm:$false -ErrorAction Stop; $removed = $true } catch { }
    }
    if (-not $removed) {
        $r = Invoke-Quiet -Exe "schtasks.exe" -Arguments @("/delete", "/tn", $label, "/f")
        $removed = ($r.Code -eq 0)
    }
    if ($removed) { Write-Ok "removed task $label" }
    else {
        Write-Warn "could not remove task $label (access denied -- registered elevated)"
        $cmd = Get-AdminTaskCommand $t
        if ($script:AdminCommands -notcontains $cmd) { $script:AdminCommands += $cmd }
    }
}
# The now-empty \CorvinOS\ folder: only the Task Scheduler COM API deletes
# folders, and COM needs FullLanguage. An empty folder is harmless.
if (-not $DryRun -and $FullLanguage) {
    try {
        $svc = New-Object -ComObject "Schedule.Service"
        $svc.Connect()
        $folder = $svc.GetFolder("\CorvinOS")
        if ($folder.GetTasks(1).Count -eq 0 -and $folder.GetFolders(0).Count -eq 0) {
            $svc.GetFolder("\").DeleteFolder("CorvinOS", 0)
        }
    } catch { }
}
foreach ($l in @(Get-Shortcuts)) {
    if ($DryRun) { Write-Info "[dry-run] would remove $l"; continue }
    Remove-Item -LiteralPath $l -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $l) { Write-Warn "could not remove shortcut $l" } else { Write-Ok "removed shortcut $l" }
}

# Claude Code integration (voice + cowork plugins and their marketplace).
$ClaudeExe = ""
$claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
if ($claudeCmd) { $ClaudeExe = $claudeCmd.Source }
foreach ($c in @((Join-Path $env:USERPROFILE ".local\bin\claude.exe"), (Join-Path $env:USERPROFILE ".claude\local\claude.exe"))) {
    if (-not $ClaudeExe -and (Test-Path -LiteralPath $c)) { $ClaudeExe = $c }
}
if ($ClaudeExe) {
    foreach ($pl in @("voice@corvin-voice-local", "cowork@corvin-voice-local")) {
        if ($DryRun) { Write-Info "[dry-run] would run: claude plugin uninstall $pl"; continue }
        $r = Invoke-Quiet -Exe $ClaudeExe -Arguments @("plugin", "uninstall", $pl)
        if ($r.Code -eq 0) { Write-Ok "Claude Code plugin removed: $pl" }
    }
    if ($DryRun) { Write-Info "[dry-run] would run: claude plugin marketplace remove corvin-voice-local" }
    else {
        $r = Invoke-Quiet -Exe $ClaudeExe -Arguments @("plugin", "marketplace", "remove", "corvin-voice-local")
        if ($r.Code -eq 0) { Write-Ok "Claude Code marketplace removed: corvin-voice-local" }
    }
}
foreach ($sub in @("plugins\cache\corvin-voice-local", "plugins\marketplaces\corvin-voice-local")) {
    $p = Join-Path (Join-Path $env:USERPROFILE ".claude") $sub
    if (Test-Path -LiteralPath $p) {
        if ($DryRun) { Write-Info "[dry-run] would remove $p" } else { $null = Remove-Tree $p }
    }
}

# -----------------------------------------------------------------------------
# 4. Remove
# -----------------------------------------------------------------------------

Write-Title "[4/5] Removing software and data"
$shimsBefore = @(Get-Shims)
if ($ToolEnvs.Count -gt 0) {
    if ($DryRun) {
        Write-Info "[dry-run] would run: uv tool uninstall corvinos"
    } else {
        # A process that respawned since step 1 would make uv fail on a locked
        # Scripts\python.exe.
        Stop-CorvinProcesses
        if ($UvExe) {
            $r = Invoke-Quiet -Exe $UvExe -Arguments @("tool", "uninstall", "corvinos")
            if ($r.Code -eq 0) { Write-Ok "uv tool corvinos uninstalled" }
        }
    }
}
# A broken receipt makes `uv tool uninstall` fail -- remove the env directly.
foreach ($te in $ToolEnvs) {
    if (-not (Test-Path -LiteralPath $te)) { continue }
    if ($DryRun) { Write-Info "[dry-run] would remove $te"; continue }
    if (Remove-Tree $te) { Write-Ok "removed $te" } else { Write-Warn "could not fully remove $te" }
}
foreach ($s in $shimsBefore) {
    if (-not (Test-Path -LiteralPath $s)) { continue }
    if ($DryRun) { Write-Info "[dry-run] would remove shim $s"; continue }
    Remove-Item -LiteralPath $s -Force -ErrorAction SilentlyContinue
}
if (-not $DryRun -and @(Get-Shims).Count -eq 0) { Write-Ok "command shims removed" }

if (-not $KeepData) {
    foreach ($d in $DataDirs) {
        if ($DryRun) { Write-Info "[dry-run] would remove $d"; continue }
        if (-not (Remove-Tree $d)) {
            # Busy files (a process that respawned) -- one more stop + retry.
            Stop-CorvinProcesses
            if (-not (Remove-Tree $d)) { Write-Warn "could not fully remove $d"; continue }
        }
        Write-Ok "removed $d"
    }
}

if ($ManagedIsOurs) {
    if ($DryRun) {
        Write-Info "[dry-run] would remove installer-managed source $ManagedSrc"
    } elseif ($KeepData -and (Test-Path -LiteralPath (Join-Path $ManagedSrc ".corvin"))) {
        # -KeepData with the data root INSIDE the managed tree: remove the tree
        # around it, never the .corvin\ it holds.
        foreach ($child in @(Get-ChildItem -LiteralPath $ManagedSrc -Force -ErrorAction SilentlyContinue)) {
            if ($child.Name -eq ".corvin") { continue }
            if ($child.PSIsContainer) { $null = Remove-Tree $child.FullName } else { Remove-Item -LiteralPath $child.FullName -Force -ErrorAction SilentlyContinue }
        }
        Write-Ok "removed installer-managed source $ManagedSrc (kept its .corvin\)"
    } else {
        if (Remove-Tree $ManagedSrc) { Write-Ok "removed installer-managed source $ManagedSrc" }
        else { Write-Warn "could not fully remove $ManagedSrc" }
    }
    if (-not $DryRun) {
        # update.ps1's swap leftovers next to the tree.
        foreach ($sfx in @(".prev", ".new", ".failed", ".tmp")) {
            $sib = "$ManagedSrc$sfx"
            if (Test-Path -LiteralPath $sib) { $null = Remove-Tree $sib }
        }
        $parent = Split-Path -Parent $ManagedSrc
        if ($parent -and (Test-Path -LiteralPath $parent) -and @(Get-ChildItem -LiteralPath $parent -Force -ErrorAction SilentlyContinue).Count -eq 0) {
            Remove-Item -LiteralPath $parent -Force -ErrorAction SilentlyContinue
        }
    }
}
foreach ($s in $SrcDirs) {
    if ($s -eq $ManagedSrc -and $ManagedIsOurs) { continue }
    Write-Info "developer checkout kept: $s (only its .corvin\ state was removed)"
}

if (-not $DryRun -and $env:TEMP) {
    foreach ($logDir in @(Get-ChildItem -LiteralPath $env:TEMP -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'corvinos-install-*' -or $_.Name -like 'corvinos-update-*' })) {
        Remove-Item -LiteralPath $logDir.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}
if ($script:EmptyDir) { Remove-Item -LiteralPath $script:EmptyDir -Recurse -Force -ErrorAction SilentlyContinue }

# -----------------------------------------------------------------------------
# 5. Verify
# -----------------------------------------------------------------------------

Write-Title "[5/5] Verifying"
if ($DryRun) {
    Write-Info "[dry-run] nothing was changed"
    exit 0
}
Invoke-Verify
Write-Host ""
if ($script:AdminCommands.Count -gt 0) {
    Write-Host "  Needs an administrator (this account is not elevated):" -ForegroundColor Yellow
    foreach ($c in $script:AdminCommands) { Write-Host "    $c" -ForegroundColor White }
    Write-Host ""
}
if ($script:Left -eq 0) {
    Write-Host "CorvinOS was removed completely." -ForegroundColor Green
    if ($BackupFile) { Write-Host "  Backup: $BackupFile" }
    Write-Host "  Not removed (shared with other software): uv, Claude Code, the Playwright browser cache,"
    Write-Host "  and the %USERPROFILE%\.local\bin entry on your PATH."
    exit 0
}
Write-Host "Incomplete: $($script:Left) artefact(s) could not be removed -- see the list above." -ForegroundColor Red
if ($BackupFile) { Write-Host "  Backup: $BackupFile" }
exit 1
