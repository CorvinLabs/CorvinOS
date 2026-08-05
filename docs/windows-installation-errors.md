# Windows 11 Installation Error Diagnosis & Recovery Guide

This document provides systematic diagnosis and recovery for common Windows 11 CorvinOS installation errors, with PowerShell scripts for each category.

**Latest audit:** 2026-08-05 (v0.10.116)

---

## 🔴 CRITICAL — Duplicate/Conflicting HTTP Packages

### Symptom
- HTTP requests fail with import errors or module conflicts
- A2A connectivity breaks
- Bridge messages silently drop
- Cloud features timeout

### Root Cause
`httpcore==1.0.9` + `httpcore2==2.9.1` and `httpx==0.28.1` + `httpx2==2.9.1` installed simultaneously.
The dependency resolver allowed both old and new HTTP library versions to coexist in the same Python environment.

### Impact
**High.** Callers may import the wrong version, causing runtime failures in:
- A2A (instance-to-instance messaging)
- Bridges (Discord, Telegram, WhatsApp, Slack, Email)
- Cloud sync features
- Remote trigger receivers

### Diagnosis

```powershell
# Check which versions are installed
pip list | Select-String -Pattern "httpcore|httpx"

# Expected output (healthy):
# httpcore                          1.0.9
# httpx                             0.28.1
#
# Problem output (unhealthy):
# httpcore                          1.0.9
# httpcore2                         2.9.1
# httpx                             0.28.1
# httpx2                            2.9.1
```

### Recovery

```powershell
# Step 1: Remove the conflicting packages
pip uninstall httpcore2 httpx2 -y

# Step 2: Upgrade to latest stable
pip install --upgrade httpcore httpx

# Step 3: Verify
python -c "import httpcore; import httpx; print(f'httpcore: {httpcore.__version__}'); print(f'httpx: {httpx.__version__}')"

# Expected output:
# httpcore: 1.0.9
# httpx: 0.28.1
```

### Prevention
- Run `pip check` after every `pip install` to detect conflicts
- Use `uv lock` (CorvinOS default) to resolve dependencies deterministically

---

## 🟠 HIGH — Ollama Not in Windows Autostart

### Symptom
- First run after installation: Hermes works ("self-heals")
- Restart machine: `corvin serve` hangs at "Starting Ollama..." or fails
- `curl http://localhost:11434/api/tags` times out or refuses connection

### Root Cause
Windows does not automatically start the Ollama service at boot.
The assumption "self-heals on first run" is often false when Ollama is never launched.

### Impact
**High.** Hermes (qwen3:8b) unavailable on restart; TTS synthesis fails; `corvin serve` hangs.

### Diagnosis

```powershell
# Check if Ollama is running
curl -s http://localhost:11434/api/tags

# If it times out or refuses, Ollama is not running
# Check Scheduled Tasks
Get-ScheduledTask -TaskName "*Ollama*" -ErrorAction SilentlyContinue

# If nothing returned, Ollama is not in autostart
```

### Recovery

```powershell
# Step 1: Start Ollama manually (as a test)
Start-Process "C:\Users\$env:USERNAME\AppData\Local\Programs\Ollama\ollama.exe"
Start-Sleep -Seconds 5

# Step 2: Verify it responds
curl -s http://localhost:11434/api/tags | jq '.models[] | .name'
# Expected: qwen3:8b

# Step 3: Register as Scheduled Task (autostart at login)
$taskName = "Ollama-Autostart"
$taskPath = "\CorvinOS\"
$ollamaPath = "C:\Users\$env:USERNAME\AppData\Local\Programs\Ollama\ollama.exe"

$action = New-ScheduledTaskAction -Execute $ollamaPath
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -TaskPath $taskPath -Force

# Step 4: Verify registration
Get-ScheduledTask -TaskName "Ollama-Autostart" | Select-Object State, TaskPath, LastTaskResult
# Expected: State = Ready, LastTaskResult = 0 or empty
```

### Prevention
- After installation, restart Windows once to test autostart
- Verify `corvin serve` completes startup without "Starting Ollama..." hanging

---

## 🟠 HIGH — CorvinOS Scheduled Task Missing After Install

### Symptom
- Installation completes but CorvinOS console doesn't start at login
- Manual `corvin serve` works fine
- Scheduled Task "CorvinOS-Console" doesn't exist
- Installation log ends at Step 8 (speech model setup)

### Root Cause
Installation Step 9–10 (Scheduled Task registration) never ran because Step 8 ended abruptly.
Possible triggers:
1. Network error during Piper model download
2. PowerShell window closed mid-install
3. User logged out during setup
4. Disk space exhausted at Step 8

### Impact
**High.** CorvinOS console does not start automatically at login; operator must manually run `corvin serve` every restart.

### Diagnosis

```powershell
# Check if the task exists
Get-ScheduledTask -TaskName "CorvinOS-Console" -ErrorAction SilentlyContinue

# If nothing returned, the task is missing
# Check recent runs
Get-ScheduledTaskInfo -TaskName "CorvinOS-Console" -ErrorAction SilentlyContinue
```

### Recovery

```powershell
# Step 1: Find the corvin-serve executable path
$corvinServe = "C:\Users\$env:USERNAME\AppData\Roaming\uv\tools\corvinos\Scripts\corvin-serve.exe"

# Verify it exists
if (-not (Test-Path $corvinServe)) {
    Write-Error "corvin-serve.exe not found at $corvinServe"
    exit 1
}

# Step 2: Create the Scheduled Task
$taskName = "CorvinOS-Console"
$taskPath = "\CorvinOS\"

$action = New-ScheduledTaskAction -Execute $corvinServe -Argument "--no-browser"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -RunLevel HighestAvailable -LogonType ServiceAccount

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -TaskPath $taskPath -Force

# Step 3: Verify registration
Get-ScheduledTask -TaskName "CorvinOS-Console" | Select-Object State, TaskPath, LastTaskResult
# Expected: State = Ready
```

### Prevention
- Monitor installation progress; if Step 8 hangs > 2 minutes, check disk space / network
- Ensure terminal remains open and focused during entire installation
- Run full installation in a dedicated terminal window, not inside a background tab

---

## 🟡 MEDIUM — Installation Incomplete (Step 8 Truncation)

### Symptom
- Installation log ends mid–Step 8 (speech model selection)
- No completion message or exit code logged
- Voice synthesis may fail at runtime with "Piper model not found"
- TTS falls back to edge-tts or fails silently

### Root Cause
Installation process exits or crashes during Piper model download (`--lang de --speaker kerstin`).
Possible triggers:
1. Network timeout during 200+ MB model download
2. Insufficient disk space in `~/.config/corvin-voice/`
3. Permission denied writing to config directory
4. User canceled the installer

### Impact
**Medium.** Voice synthesis degrades to OpenAI TTS (if key available) or edge-tts (internet required).
Full offline mode (Piper) unavailable; operator hears degraded quality on restart.

### Diagnosis

```powershell
# Check if Piper models exist
ls "$env:USERPROFILE\.config\corvin-voice\piper\"

# If directory doesn't exist or is empty, models weren't downloaded
# Check CorvinOS directory structure
ls "$env:USERPROFILE\.corvin\"
# Should have: global/, tenants/, sessions/, voice/

# Check if installation was interrupted
ls "$env:USERPROFILE\.config\corvin-voice\config.json"
```

### Recovery

```powershell
# Step 1: Ensure config directory exists
if (-not (Test-Path "$env:USERPROFILE\.config\corvin-voice")) {
    mkdir "$env:USERPROFILE\.config\corvin-voice" -Force
}

# Step 2: Download Piper model manually
corvin-voice --lang de --speaker kerstin

# Or using piper CLI directly:
piper --download-dir "$env:USERPROFILE\.config\corvin-voice\piper" --download de_DE-kerstin-high

# Step 3: Verify download
ls "$env:USERPROFILE\.config\corvin-voice\piper\*.onnx"
# Expected: de_DE-kerstin-high.onnx (~400 MB)

# Step 4: Test TTS
corvin-voice --list
# Expected: Kerstin (de) in output
```

### Prevention
- Monitor Step 8 progress; if download hangs > 5 minutes, abort and retry
- Ensure ≥1 GB free disk space before installation
- Use wired network connection for model download (faster, more stable than WiFi)
- Re-run `corvin-install` to backfill skipped steps

---

## 🟡 MEDIUM — pywin32 Version Compatibility

### Symptom
- Rare: "COM object not registered" errors
- Windows-specific plugins fail to initialize
- Occasional "Access denied" on Windows API calls

### Root Cause
`pywin32==312` may have COM registration drift on Windows 11 22H2+ (recent updates).
The wheel includes pre-built binaries, but they may not be re-registered after upgrade.

### Impact
**Medium.** Rare edge-case; most installations unaffected. Only relevant if using Windows-specific features (Scheduled Task queries, system audio, clipboard access).

### Diagnosis

```powershell
# Check pywin32 version
pip show pywin32 | Select-String "Version"

# Verify COM registration
python -c "import win32com.client; print('✓ COM OK')" 2>$null || Write-Warning "COM registration missing"
```

### Recovery (optional, one-time)

```powershell
# Upgrade pywin32
pip install --upgrade pywin32

# Re-register COM objects (one-time admin task)
python -m pywin32_postinstall -install

# Verify
python -c "import win32com.client; print('✓ COM registered')"
```

---

## Quick Checklist After Installation

```powershell
# 1. Verify all packages
pip list | Select-String -Pattern "httpcore|httpx|pywin32"

# 2. Start Ollama
Start-Process "C:\Users\$env:USERNAME\AppData\Local\Programs\Ollama\ollama.exe"
Start-Sleep -Seconds 3
curl -s http://localhost:11434/api/tags | jq '.models[] | .name'

# 3. Check Scheduled Tasks
Get-ScheduledTask -TaskName "CorvinOS-Console"
Get-ScheduledTask -TaskName "Ollama-Autostart"

# 4. Test Piper model
ls "$env:USERPROFILE\.config\corvin-voice\piper\*.onnx"

# 5. Boot CorvinOS
corvin serve
# Should reach http://localhost:8765/console/ without errors
```

---

## Future Work: `corvin diagnose windows` (M2)

**Planned:** Add CLI command to auto-detect all five error categories and suggest fixes programmatically.

```bash
# Future (not yet implemented):
corvin diagnose windows
# Output:
# ✓ HTTP packages: OK (httpcore 1.0.9, httpx 0.28.1)
# ✗ Ollama autostart: MISSING — Run: Register-ScheduledTask -TaskName Ollama-Autostart ...
# ✗ CorvinOS task: MISSING — Run: Register-ScheduledTask -TaskName CorvinOS-Console ...
# ✓ Piper models: OK (de_DE-kerstin-high)
# ✓ pywin32: OK (registered)
```

---

## Support

If errors persist after following these steps:

1. **Collect logs:**
   ```powershell
   # CorvinOS audit log
   cat "$env:USERPROFILE\.corvin\tenants\_default\global\audit.jsonl" | tail -50
   
   # Bridge logs
   bridge.sh logs
   ```

2. **Report on GitHub:** <https://github.com/CorvinLabs/CorvinOS/issues>
   - Include: Windows version, Python version, installation log excerpt, error category number

3. **Fresh install as last resort:**
   ```powershell
   corvin-uninstall
   # Remove lingering state (optional):
   rm -r "$env:USERPROFILE\.corvin"
   rm -r "$env:USERPROFILE\.config\corvin-voice"
   
   # Reinstall
   irm https://corvin-labs.com/install.ps1 | iex
   ```
