# A2A WINDOWS/LINUX PAIRING VERIFICATION LOG — SEPT 2–5
**Project:** Phase 1 Big Bang Big Bang Feature Flags  
**Objective:** Verify Windows ↔ Linux A2A direct pairing works end-to-end  
**Deadline:** Sept 5 10:00 UTC (go/no-go decision)

---

## HARDWARE PROVISIONING STATUS

**Decision Gate:** Sept 2 16:00 UTC (IMMEDIATE)

| Item | Status | Details | Owner |
|------|--------|---------|-------|
| Hosting Platform | ⬜ PENDING | KVM / VirtualBox / Hyper-V? | DevOps Lead |
| LAN Network Config | ⬜ PENDING | Bridge mode (real LAN)? | DevOps Lead |
| Ubuntu VM (Phase 1) | ⬜ PENDING | Target: Sept 3 02:00 UTC | DevOps |
| Windows VM (Phase 2) | ⬜ PENDING | Target: Sept 3 04:00 UTC | DevOps |
| Network Connectivity | ⬜ PENDING | Ping both directions, A2A API responds | QA/DevOps |
| Test Script Ready | ⬜ PENDING | Bidirectional A2A send test | QA |

**Escalation Trigger:** If infrastructure decision not made by Sept 2 16:30 UTC → escalate to DevOps Lead.

---

## PHASE 0: INFRASTRUCTURE DECISION (SEPT 2 16:00–16:30 UTC)

**Required Decision (choose ONE):**

| Option | Decision? | Platform | LAN? | Real Test? | Comment |
|--------|-----------|----------|------|-----------|---------|
| **A1: KVM** | ⬜ | Linux host | ✅ bridge=br0 | ✅ YES | Recommended |
| **A2: VirtualBox** | ⬜ | Any | ✅ bridge mode | ✅ YES | Simpler setup |
| **A3: Hyper-V** | ⬜ | Windows | ✅ external switch | ✅ YES | Win-only |
| **B: AWS/Azure/GCP** | ⬜ | Cloud | ❌ VPC | ⚠️ NO | Network isolation risk |
| **C: Docker** | ⬜ | Container | ❌ localhost | ❌ NO | Last resort only |

**Decision Made (fill in):**
```
INFRASTRUCTURE CHOICE: [ ] A1-KVM [ ] A2-VirtualBox [ ] A3-Hyper-V [ ] B-Cloud [ ] C-Docker

Host Specifications:
  - CPU cores available: ___
  - RAM available: ___
  - Storage available: ___
  - Host network interface (for bridge): ___
  - LAN subnet (for VMs): ___
  - Owner/Operator: ___________
```

**Approval:** [Signature/Timestamp]

---

## PHASE 1: UBUNTU VM SETUP (TARGET: SEPT 3 02:00 UTC)

### **1a: VM Creation**
```
VM Name: corvinOS-ubuntu-verify
Hypervisor: [from infrastructure choice above]
Memory: 4 GB
vCPUs: 2
Disk: 50 GB
Network: Bridge mode (real LAN)
Status: ⬜ PENDING
```

| Task | Target Time | Actual Time | Status | Notes |
|------|-------------|-------------|--------|-------|
| VM creation | Sept 3 00:00 | — | ⬜ | [Estimate 30 min] |
| OS install (Ubuntu 22.04 LTS) | Sept 3 00:30 | — | ⬜ | [20 min installer] |
| Post-install (packages, corvinos) | Sept 3 01:30 | — | ⬜ | [30 min setup] |
| **CHECKPOINT 1a: Ubuntu booted, corvinos running** | Sept 3 02:00 | — | ⬜ | **CRITICAL** |

### **1b: Ubuntu Verification**

**After Ubuntu VM is running:**

```bash
# SSH into Ubuntu
ssh corvin@<ubuntu-ip>

# Verify corvinos is running
ps aux | grep 'corvin serve'
# Expected: corvin serve --host 0.0.0.0 running

# Verify A2A endpoint listening on LAN
sudo netstat -tlnp | grep 6789
# Expected: 0.0.0.0:6789 LISTEN (NOT 127.0.0.1:6789)

# Extract Ubuntu kid (save for later)
UBUNTU_KID=$(cat ~/.corvin/global/remote_trigger/local_kid.txt)
UBUNTU_IP=$(hostname -I | awk '{print $1}')
echo "Ubuntu kid: $UBUNTU_KID"
echo "Ubuntu IP: $UBUNTU_IP"

# Test HTTP API
curl -s http://localhost:8765/health | jq .
# Expected: {"status": "healthy", ...}
```

**Verification Checklist:**
- [ ] SSH connection works
- [ ] `corvin serve` process running (check: `ps aux | grep corvin`)
- [ ] Port 6789 listening on 0.0.0.0 (not 127.0.0.1)
- [ ] Kid file exists and contains UUID
- [ ] HTTP health endpoint responds
- [ ] `/health` returns {"status": "healthy"} or similar

**Result:**
```
Ubuntu IP: ___________
Ubuntu Kid: ___________
Status: ✅ PASS / ⚠️ PARTIAL / ❌ FAIL
Issue (if fail): ___________
```

---

## PHASE 2: WINDOWS VM SETUP (TARGET: SEPT 3 04:00 UTC)

### **2a: VM Creation**
```
VM Name: corvinOS-windows-verify
Hypervisor: [from infrastructure choice]
Memory: 4 GB
vCPUs: 2
Disk: 100 GB
Network: Bridge mode (same LAN as Ubuntu)
Status: ⬜ PENDING
```

| Task | Target Time | Actual Time | Status | Notes |
|------|-------------|-------------|--------|-------|
| VM creation | Sept 3 02:30 | — | ⬜ | [30 min] |
| Windows OS install | Sept 3 03:00 | — | ⬜ | [30 min + UAC setup] |
| Post-install (Python, corvinos) | Sept 3 03:45 | — | ⬜ | [15 min, Windows slower] |
| **CHECKPOINT 2a: Windows booted, corvinos running** | Sept 3 04:00 | — | ⬜ | **CRITICAL** |

### **2b: Windows Verification**

**After Windows VM is running:**

```powershell
# Open PowerShell as Administrator

# Verify corvinos service running
Get-Process | Where-Object {$_.Name -like "*corvin*"}
# Expected: corvin.exe (or similar process name)

# Verify port 6789 listening on all interfaces
netstat -ano | findstr 6789
# Expected: 0.0.0.0:6789 LISTENING (NOT 127.0.0.1:6789)

# Extract Windows kid
$WINDOWS_KID = Get-Content $env:USERPROFILE\.corvin\global\remote_trigger\local_kid.txt
$WINDOWS_IP = (Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred).IPAddress
Write-Host "Windows kid: $WINDOWS_KID"
Write-Host "Windows IP: $WINDOWS_IP"

# Test HTTP API
curl -s http://localhost:8765/health | ConvertFrom-Json
# Expected: status = "healthy"
```

**Verification Checklist:**
- [ ] RDP connection works (or local console)
- [ ] `corvin` process running
- [ ] Port 6789 listening on 0.0.0.0 (not 127.0.0.1)
- [ ] Kid file exists and contains UUID
- [ ] HTTP health endpoint responds
- [ ] Windows Defender firewall: port 6789 accessible (may need exception)

**Result:**
```
Windows IP: ___________
Windows Kid: ___________
Status: ✅ PASS / ⚠️ PARTIAL / ❌ FAIL
Issue (if fail): ___________
Firewall Action Taken: [ ] Added exception [ ] Disabled temporarily [ ] None
```

---

## PHASE 3: NETWORK CONNECTIVITY TEST (TARGET: SEPT 3 06:00 UTC)

### **3a: Ping Test**

```bash
# From Ubuntu to Windows
ping <windows-ip>
# Expected: replies, no packet loss

# From Windows to Ubuntu (PowerShell)
ping <ubuntu-ip>
# Expected: replies, no packet loss
```

**Result:**
```
Ubuntu → Windows ping: ✅ PASS / ❌ FAIL (latency: ___ ms)
Windows → Ubuntu ping: ✅ PASS / ❌ FAIL (latency: ___ ms)
```

### **3b: Firewall Configuration (if needed)**

**Linux (Ubuntu):**
```bash
# Check firewall status
sudo ufw status
# If enabled, allow port 6789 from Windows subnet:
sudo ufw allow from <windows-ip>/32 to any port 6789
```

**Windows:**
```powershell
# Check Defender Firewall
Get-NetFirewallProfile | Select-Object Name, Enabled

# If port 6789 blocked:
# Settings → Firewall & network protection → Advanced settings → Inbound Rules
# OR (PowerShell):
New-NetFirewallRule -DisplayName "Corvinos A2A" -Direction Inbound -Protocol TCP -LocalPort 6789 -Action Allow
```

**Result:**
```
Linux firewall: ✅ Allows 6789 / ⚠️ Manual check needed / ❌ BLOCKED
Windows firewall: ✅ Allows 6789 / ⚠️ Exception added / ❌ BLOCKED
```

### **3c: A2A API Connectivity Test**

```bash
# From Ubuntu to Windows A2A endpoint
curl -v -X POST http://<windows-ip>:6789/v1/a2a/ping \
  -H "Content-Type: application/json" \
  -d '{"kid":"test"}'
# Expected: HTTP 200 or 400 (means endpoint is reachable; 400 is OK for bad kid)

# From Windows to Ubuntu A2A endpoint (PowerShell)
curl -X POST http://<ubuntu-ip>:6789/v1/a2a/ping `
  -H "Content-Type: application/json" `
  -d '{"kid":"test"}'
# Expected: HTTP 200 or 400
```

**Result:**
```
Ubuntu → Windows A2A API: ✅ RESPONSIVE (HTTP ___) / ❌ TIMEOUT / ❌ REFUSED
Windows → Ubuntu A2A API: ✅ RESPONSIVE (HTTP ___) / ❌ TIMEOUT / ❌ REFUSED
Network isolation issue?: ✅ NO / ⚠️ POSSIBLE / ❌ YES
```

**CHECKPOINT 3: Network connectivity verified**
- [ ] Ping both directions ✅
- [ ] A2A endpoints respond ✅
- [ ] No firewall blocks ✅

---

## PHASE 4: BIDIRECTIONAL A2A SEND TEST (TARGET: SEPT 3 12:00 UTC) — 🔴 CRITICAL PATH

**This is the most important test. If it fails by Sept 3 12:00 UTC, ESCALATE IMMEDIATELY.**

### **4a: Test Script Execution**

**Location:** `/tmp/test_a2a_pairing.sh` (created during Phase 5 of hardware setup doc)

**Before running, populate:**
```bash
UBUNTU_IP="<from-phase-1b>"          # e.g., 192.168.1.100
UBUNTU_KID="<from-phase-1b>"         # e.g., e6c0fba2-...
WINDOWS_IP="<from-phase-2b>"         # e.g., 192.168.1.101
WINDOWS_KID="<from-phase-2b>"        # e.g., a7d1ccb3-...
```

**Execute from Ubuntu:**
```bash
bash /tmp/test_a2a_pairing.sh
```

### **4b: Test Results**

**Test 1: Kid Verification**
```
UBUNTU_KID generated: ✅ YES / ❌ NO
WINDOWS_KID generated: ✅ YES / ❌ NO
Kids are different (expected): ✅ YES / ⚠️ SAME / ❌ MISSING
```

**Test 2a: Ubuntu → Windows Send**
```bash
curl -X POST http://<windows-ip>:6789/v1/a2a/receive \
  -H "Content-Type: application/json" \
  -d "{\"kid\":\"<ubuntu-kid>\",\"payload\":\"test from ubuntu\"}" \
  -v
```

**Expected Response:**
```json
{
  "status": "success",
  "route": "direct",
  "latency_ms": 42,
  "audit_logged": true
}
```

**Result:**
```
HTTP Status: ___ (expected 200)
Response: [paste JSON]
Status: ✅ SUCCESS / ⚠️ PARTIAL / ❌ FAIL
Error (if fail): ___________
```

**Test 2b: Windows → Ubuntu Send**
```powershell
curl -X POST http://<ubuntu-ip>:6789/v1/a2a/receive `
  -H "Content-Type: application/json" `
  -d "{`"kid`":`"<windows-kid>`",`"payload`":`"test from windows`"}" `
  -v
```

**Expected Response:**
```json
{
  "status": "success",
  "route": "direct",
  "latency_ms": 38,
  "audit_logged": true
}
```

**Result:**
```
HTTP Status: ___ (expected 200)
Response: [paste JSON]
Status: ✅ SUCCESS / ⚠️ PARTIAL / ❌ FAIL
Error (if fail): ___________
```

### **4c: Audit Trail Verification**

**From Ubuntu:**
```bash
tail -20 ~/.corvin/audit.jsonl | grep -A3 "a2a_task_executed"
```

**Expected:**
```json
{
  "event_type": "a2a_task_executed",
  "source_kid": "<windows-kid>",
  "destination_kid": "<ubuntu-kid>",
  "route": "direct",
  "status": "success",
  "latency_ms": 38
}
```

**From Windows:**
```powershell
Get-Content $env:USERPROFILE\.corvin\audit.jsonl -Tail 20 | Select-String "a2a_task_executed" -A3
```

**Expected:** Same format as above

**Result:**
```
Ubuntu audit events: ✅ PRESENT (count: ___) / ❌ MISSING
Windows audit events: ✅ PRESENT (count: ___) / ❌ MISSING
Event fields correct: ✅ YES / ⚠️ PARTIAL / ❌ NO
```

### **PHASE 4 GO/NO-GO GATE**

**All must PASS for GO:**

| Criterion | Result | Pass? |
|-----------|--------|-------|
| Ubuntu → Windows send succeeds | ✅ / ❌ | [ ] |
| Windows → Ubuntu send succeeds | ✅ / ❌ | [ ] |
| Both audit events logged correctly | ✅ / ❌ | [ ] |
| Zero crashes/exceptions | ✅ / ❌ | [ ] |

**Phase 4 Decision:**
```
✅ GO: All 4 criteria pass → Pairing works end-to-end
❌ NO-GO: Any criterion fails → Escalate immediately with root cause
```

**If NO-GO, escalation details:**
```
Failed Test: ___ (which of the 4 above)
Error Code: ___
Error Message: ___
Root Cause Analysis: 
  - Network issue? [ ] Ping fails [ ] API unreachable [ ] Firewall blocks
  - Protocol issue? [ ] Wrong endpoint [ ] Malformed request [ ] Bad kid format
  - Service issue? [ ] Process dead [ ] Port not listening [ ] Config error
  - Audit issue? [ ] No events [ ] Wrong format [ ] Missing fields
Proposed Fix: ___________
Time to Fix Estimate: ___
```

**Escalation Contacts (if NO-GO):**
- Primary: @Bridge-Eng (A2A protocol issues)
- Secondary: @Network-Eng (connectivity issues)
- Tertiary: @DevOps-Lead (VM/service issues)

---

## PHASE 5: PHASE 2 (RELAY FALLBACK) — OPTIONAL, DEFERRED IF NEEDED (TARGET: SEPT 4)

*Only if Phase 4 passes. If Phase 4 fails and fixes take >4 hours, defer Phase 2 to post-launch.*

### **5a: Simulate CGNAT (block direct route)**

```bash
# On Windows, simulate CGNAT by blocking Ubuntu IP on port 6789:
netsh advfirewall firewall add rule name="Block Ubuntu A2A" dir=out action=block remoteip=<ubuntu-ip> remoteport=6789 protocol=tcp
```

### **5b: Test Relay Fallback**

Repeat Phase 4 tests with relay enabled. Expected: messages reach destination via relay.

```bash
# Check audit events for relay_used=true
tail ~/.corvin/audit.jsonl | grep relay_used
```

**Result:**
```
Relay engaged: ✅ YES / ❌ NO
Messages delivered via relay: ✅ YES / ❌ NO
Phase 2 Status: ✅ PASS / ⚠️ DEFERRED / ❌ FAIL
```

---

## PHASE 6: FINAL REPORT (TARGET: SEPT 5 10:00 UTC)

**Document:** `A2A_VERIFICATION_REPORT_SEPT_5.md`

```markdown
# A2A WINDOWS/LINUX PAIRING VERIFICATION — FINAL REPORT

## EXECUTIVE DECISION

**GO / NO-GO:** [ ] GO (Phase 1 ✅ PASS) [ ] NO-GO (Phase 1 ❌ FAIL)

## PHASE 1 RESULTS (CRITICAL PATH)

### Phase 1a: Kid Generation
- Ubuntu kid generated: ✅ / ❌
- Windows kid generated: ✅ / ❌

### Phase 1b: Bidirectional Sends
- Ubuntu → Windows: ✅ SUCCESS / ❌ FAIL
- Windows → Ubuntu: ✅ SUCCESS / ❌ FAIL
- Audit events logged: ✅ / ❌

## ROOT CAUSE (IF NO-GO)
[Detailed root cause analysis]

## RECOMMENDATIONS FOR PHASE 1b
- [ ] Proceed with big-bang (A2A pairing works)
- [ ] Defer Windows support to Phase 2 (Linux works, Windows needs fixes)
- [ ] Block big-bang (critical A2A bug found)

## APPENDIX: Full Test Logs
[Paste all curl responses, audit events, error messages]
```

---

## ESCALATION MATRIX (DO NOT WAIT — ESCALATE IMMEDIATELY)

| Event | Escalate To | When | Action |
|-------|-------------|------|--------|
| **Infrastructure decision not made** | DevOps Lead + Steering | Sept 2 16:30 UTC | Delay provisioning, choose contingency |
| **Ubuntu setup fails** | DevOps | Sept 3 02:30 UTC | Retry or switch hypervisor |
| **Windows setup fails** | DevOps + Steering | Sept 3 04:30 UTC | Retry; if repeated, use CI fallback |
| **Network connectivity fails** | Network Eng + DevOps | Sept 3 06:30 UTC | Check LAN config, firewall rules |
| **Phase 1b direct send fails** | Bridge Eng + Steering | **Sept 3 12:00 UTC** | 🔴 **CRITICAL** — must escalate same day |
| **Audit events missing** | Audit + Bridge Eng | Sept 3 14:00 UTC | Verify audit backend running |
| **Report missing/late** | Steering | Sept 5 10:30 UTC | Rerun Phase 1 on emergency schedule |

---

## CONTACTS & ROLES

| Role | Name | Slack | Responsibility |
|------|------|-------|---|
| **DevOps Lead** | [TBD] | @devops | Provisioning, VM setup |
| **QA Lead** | [TBD] | @qa | Test execution, results |
| **Bridge Engineer** | [TBD] | @bridge-eng | Protocol issues, root cause |
| **Network Engineer** | [TBD] | @network | Connectivity, firewall |
| **Steering/Coordinator** | [TBD] | @coordinator | Escalations, go/no-go decisions |

---

## NOTES

- **No "nice to haves" here.** Phase 1 (direct send) is the critical path. Phase 2 (relay) is secondary.
- **Escalate on time, not late.** Sept 3 12:00 UTC is the Phase 1b hard deadline.
- **Document everything.** Logs, error messages, steps taken — all needed for root cause analysis.
- **No surprises.** Daily async updates if possible, but escalate blockers immediately (don't wait 24h).

---

**Status:** Awaiting infrastructure decision (Sept 2 EOD)  
**Next Update:** Sept 3 02:00 UTC (Ubuntu checkpoint)  
**Last Updated:** Sept 2, 2026 | 16:30 UTC
