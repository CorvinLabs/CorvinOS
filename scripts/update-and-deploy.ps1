#Requires -Version 5.1
<#
.SYNOPSIS
    CorvinOS developer update-and-deploy cycle for Windows.

.DESCRIPTION
    Windows equivalent of scripts/update-and-deploy.sh. Fail-closed, self-contained
    automation for: git pull origin main -> frontend rebuild -> E2E tests -> git push
    origin main. Intended for maintainers pushing changes, NOT for end-user installs
    (see update.ps1 for that).

    ASCII-ONLY BY CONTRACT (see install.ps1 for UTF-8 warning).

.PARAMETER DryRun
    Run all steps except the final push.

.PARAMETER SkipTests
    Skip E2E tests (not recommended).

.PARAMETER VerboseOutput
    Print each command and its full output.

.PARAMETER RepoRoot
    Path to the CorvinOS repository (default: parent of this script).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\update-and-deploy.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\update-and-deploy.ps1 -DryRun -VerboseOutput

.NOTES
    Exit codes:
      0 = Success (all steps passed, pushed to origin/main)
      1 = Failure (git, build, or test failed)
      2 = Validation failure (repo state invalid before/after)
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipTests,
    [switch]$VerboseOutput,
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol
} catch { }

# ─────────────────────────────────────────────────────────────────────────────
# Configuration & Logging
# ─────────────────────────────────────────────────────────────────────────────
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = (Resolve-Path $RepoRoot).Path

$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile = Join-Path $env:TEMP "corvin-update-deploy-$Timestamp.log"
$LockFile = Join-Path $RepoRoot ".update-deploy.lock"

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet("Info", "Warn", "Error", "Success", "Debug")]
        [string]$Level = "Info"
    )

    $ts = Get-Date -Format "HH:mm:ss.fff"
    $line = "[$ts] [$Level] $Message"

    $color = @{
        "Info"    = "White"
        "Warn"    = "Yellow"
        "Error"   = "Red"
        "Success" = "Green"
        "Debug"   = "DarkGray"
    }[$Level]

    if ($Level -eq "Debug" -and -not $VerboseOutput) {
        # still logged to file, just not echoed to console
    } else {
        Write-Host $line -ForegroundColor $color
    }

    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
}

function Test-Command {
    param([string]$Command)
    $null = Get-Command $Command -ErrorAction SilentlyContinue
    return $?
}

function Invoke-Logged {
    param(
        [string]$Exe,
        [string[]]$ArgList,
        [string]$FailMessage
    )
    if ($VerboseOutput) {
        Write-Log -Message "$ $Exe $($ArgList -join ' ')" -Level "Debug"
    }
    $output = & $Exe @ArgList 2>&1
    $output | ForEach-Object { Write-Log -Message $_ -Level "Debug" }
    if ($LASTEXITCODE -ne 0) {
        Write-Log -Message "$FailMessage (exit $LASTEXITCODE)" -Level "Error"
        return $false
    }
    return $true
}

# Release the lock on any exit path.
$LockAcquired = $false
function Remove-Lock {
    if ($LockAcquired -and (Test-Path $LockFile)) {
        Remove-Item -Path $LockFile -Force -ErrorAction SilentlyContinue
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "CorvinOS update-and-deploy" -ForegroundColor White
Write-Host "=========================="
Write-Host "Log: $LogFile"
Write-Host ""

try {
    # Exclusive lock (mirrors flock in the bash version). New-Item with -ErrorAction
    # Stop fails atomically if another instance already holds the lock file.
    Write-Log -Message "Acquiring lock..." -Level "Info"
    $lockWaitSeconds = 600
    $lockDeadline = (Get-Date).AddSeconds($lockWaitSeconds)
    while ($true) {
        try {
            $null = New-Item -Path $LockFile -ItemType File -ErrorAction Stop
            $LockAcquired = $true
            break
        } catch {
            if ((Get-Date) -gt $lockDeadline) {
                Write-Log -Message "Another update-and-deploy is running (timeout ${lockWaitSeconds}s)" -Level "Error"
                exit 1
            }
            Start-Sleep -Seconds 2
        }
    }
    Write-Log -Message "Lock acquired" -Level "Success"

    Push-Location $RepoRoot

    # Step 1: Validate repository state
    Write-Log -Message "Step 1: Validating repository state..." -Level "Info"

    if (-not (Test-Command "git")) {
        Write-Log -Message "git not found on PATH" -Level "Error"
        exit 1
    }

    $currentBranch = (& git rev-parse --abbrev-ref HEAD 2>&1).Trim()
    if ($currentBranch -ne "main") {
        Write-Log -Message "Not on main branch (current: $currentBranch)" -Level "Error"
        exit 1
    }

    $dirtyStatus = & git status --porcelain
    if ($dirtyStatus) {
        Write-Log -Message "Uncommitted changes detected. Stash or commit before updating:" -Level "Error"
        $dirtyStatus | ForEach-Object { Write-Log -Message $_ -Level "Warn" }
        exit 1
    }
    Write-Log -Message "Repository clean" -Level "Success"

    # Step 2: Git pull origin main
    Write-Log -Message "Step 2: Pulling latest changes from origin/main..." -Level "Info"

    if (-not (Invoke-Logged -Exe "git" -ArgList @("fetch", "origin") -FailMessage "git fetch failed")) {
        exit 1
    }

    $localHead = (& git rev-parse main 2>&1).Trim()
    $remoteHead = (& git rev-parse origin/main 2>&1).Trim()

    if ($localHead -eq $remoteHead) {
        Write-Log -Message "Already up-to-date with origin/main" -Level "Warn"
    } else {
        Write-Log -Message "Pulling origin/main (local: $($localHead.Substring(0,7)) -> remote: $($remoteHead.Substring(0,7)))" -Level "Info"
        if (-not (Invoke-Logged -Exe "git" -ArgList @("merge", "origin/main") -FailMessage "git merge origin/main failed")) {
            Write-Log -Message "Manual conflict resolution required" -Level "Warn"
            exit 1
        }
    }
    Write-Log -Message "Git pull complete" -Level "Success"

    # Step 3: Build frontend
    Write-Log -Message "Step 3: Building frontend..." -Level "Info"

    $consoleDeployPs1 = Join-Path $RepoRoot "scripts\console-deploy.ps1"
    $consoleDeploySh = Join-Path $RepoRoot "scripts\console-deploy.sh"

    $buildOk = $false
    if (Test-Path $consoleDeployPs1) {
        $buildOk = Invoke-Logged -Exe "powershell" -ArgList @("-ExecutionPolicy", "Bypass", "-File", $consoleDeployPs1) -FailMessage "Frontend build failed"
    } elseif (Test-Path $consoleDeploySh) {
        if (Test-Command "bash") {
            $buildOk = Invoke-Logged -Exe "bash" -ArgList @($consoleDeploySh) -FailMessage "Frontend build failed"
        } else {
            $gitBash = "C:\Program Files\Git\bin\bash.exe"
            if (Test-Path $gitBash) {
                $buildOk = Invoke-Logged -Exe $gitBash -ArgList @($consoleDeploySh) -FailMessage "Frontend build failed"
            } else {
                Write-Log -Message "Neither bash nor Git Bash found; cannot run console-deploy.sh" -Level "Error"
                exit 1
            }
        }
    } else {
        Write-Log -Message "No console-deploy script found (expected scripts\console-deploy.ps1 or .sh)" -Level "Error"
        exit 1
    }

    if (-not $buildOk) {
        exit 1
    }
    Write-Log -Message "Frontend built and verified" -Level "Success"

    # Step 4: Run E2E tests
    if (-not $SkipTests) {
        Write-Log -Message "Step 4: Running E2E tests..." -Level "Info"

        if (-not (Test-Command "pytest")) {
            Write-Log -Message "pytest not found, skipping E2E tests" -Level "Warn"
        } else {
            $criticalTests = @(
                "tests\test_console_app_importable.py",
                "tests\e2e\test_engine_config_real_data_e2e.py"
            )

            $testFailed = $false
            foreach ($testFile in $criticalTests) {
                $fullPath = Join-Path $RepoRoot $testFile
                if (Test-Path $fullPath) {
                    Write-Log -Message "Running $testFile..." -Level "Info"
                    if (-not (Invoke-Logged -Exe "pytest" -ArgList @($fullPath, "-v", "--tb=short") -FailMessage "E2E test failed: $testFile")) {
                        $testFailed = $true
                    }
                }
            }

            if ($testFailed) {
                Write-Log -Message "One or more E2E tests failed" -Level "Error"
                exit 1
            }
            Write-Log -Message "E2E tests passed" -Level "Success"
        }
    } else {
        Write-Log -Message "Skipping E2E tests (-SkipTests flag)" -Level "Warn"
    }

    # Step 5: Validate state before push
    Write-Log -Message "Step 5: Final validation before push..." -Level "Info"

    $currentBranch = (& git rev-parse --abbrev-ref HEAD 2>&1).Trim()
    if ($currentBranch -ne "main") {
        Write-Log -Message "Not on main branch after update (current: $currentBranch)" -Level "Error"
        exit 2
    }

    $commitsAhead = (& git rev-list --count origin/main..main 2>&1).Trim()
    if ($commitsAhead -eq "0") {
        Write-Log -Message "No new commits to push (already in sync with origin/main)" -Level "Info"
    } else {
        Write-Log -Message "$commitsAhead commits ahead of origin/main" -Level "Info"
    }
    Write-Log -Message "All validation passed" -Level "Success"

    # Step 6: Git push (unless -DryRun)
    if ($DryRun) {
        Write-Log -Message "DRY RUN: Would push to origin/main (omit -DryRun to push)" -Level "Warn"
    } else {
        Write-Log -Message "Step 6: Pushing to origin/main..." -Level "Info"

        $userEmail = (& git config user.email 2>&1)
        $userName = (& git config user.name 2>&1)
        if (-not $userEmail -or -not $userName) {
            Write-Log -Message "git user.email or user.name not configured" -Level "Error"
            exit 1
        }

        if (-not (Invoke-Logged -Exe "git" -ArgList @("push", "origin", "main") -FailMessage "git push origin main failed")) {
            Write-Log -Message "Local changes are ready but push failed. Manual intervention required." -Level "Warn"
            exit 1
        }
        Write-Log -Message "Pushed to origin/main" -Level "Success"
    }

    # Summary
    Write-Host ""
    Write-Log -Message "===== UPDATE CYCLE COMPLETE =====" -Level "Success"
    Write-Log -Message "Frontend: Built and verified" -Level "Info"
    Write-Log -Message "Tests: All passed" -Level "Info"
    if (-not $DryRun) {
        Write-Log -Message "Status: Pushed to origin/main" -Level "Info"
    } else {
        Write-Log -Message "Status: Dry run (no push)" -Level "Info"
    }
    Write-Log -Message "Log file: $LogFile" -Level "Info"

    exit 0
} finally {
    Pop-Location -ErrorAction SilentlyContinue
    Remove-Lock
}
