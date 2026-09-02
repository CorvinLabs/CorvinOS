# A2A WINDOWS/LINUX PAIRING VERIFICATION — HARDWARE SETUP (OPTION A)
**Priority:** CRITICAL | **Deadline:** Sept 2 16:00 UTC (provisioning START) | **Owner:** DevOps

---

## OPTION A: REAL HARDWARE SETUP (Windows VM + Ubuntu LAN)

**Time Estimate:** 4 hours (Sept 2 16:00 → Sept 3 ~20:00 UTC)  
**Target Completion:** Ubuntu + Windows both booted, connected to same LAN, corvinos running with `a2a_lan_bind=true`

---

## PHASE 0: INFRASTRUCTURE DECISIONS (NEXT 2 HOURS)

### **Hosting Environment (Choose ONE):**

| Option | Setup Time | Network Isolation | Real Test? | Cost |
|--------|-----------|-------------------|-----------|------|
| **0a: KVM/Proxmox** (recommended) | 1–2h | None (real LAN) | ✅ YES | $0–50 |
| **0b: VirtualBox/Hyper-V** (local) | 1–2h | Bridge mode = real LAN | ✅ YES | $0 |
| **0c: AWS/Azure/GCP** | 1–2h | VPC = isolated, may fail | ⚠️ NO | $20–50 |
| **0d: Docker Compose** (last resort) | 0.5h | Localhost bridge | ❌ NO | $0 |

**RECOMMENDATION:** 0a or 0b (real LAN required for Phase 1b direct send test).

**DECISION GATE (NOW):**
- [ ] **Host Decision:** KVM, VirtualBox, or Hyper-V?
- [ ] **Networking:** Will VMs be on same subnet as host? (yes = real LAN, no = isolated VPC)
- [ ] **Person Responsible:** (DevOps lead name)

---

## PHASE 1: UBUNTU SETUP (1.5 HOURS)

### **1a: VM Creation**
```bash
# Hypervisor-specific (choose based on infrastructure decision)
# KVM example:
virt-install \
  --name corvinOS-ubuntu \
  --memory 4096 --vcpus 2 \
  --disk size=50 \
  --os-type linux --os-variant ubuntu22.04 \
  --network bridge=br0 \  # CRITICAL: bridge to host LAN
  --location https://archive.ubuntu.com/ubuntu/dists/jammy/main/installer-amd64/
```

### **1b: OS Install** (20 min)
- Ubuntu 22.04 LTS (standard installer, no custom options)
- Hostname: `corvinOS-ubuntu-verify` (for distinction in logs)
- Network: DHCP (auto-connect to LAN bridge)
- SSH enabled: YES
- User: `corvin` (for corvinos installation)

### **1c: Post-Install Prep** (20 min)
```bash
# SSH into Ubuntu VM
ssh corvin@<ubuntu-ip>

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11+
sudo apt install -y python3.11 python3.11-venv python3-pip

# Install corvinos latest
curl -fsSL https://install.corvinlabs.ai/corvinos-install.sh | bash
# (or: build from git repo if HEAD needed)

# Enable A2A LAN bind (CRITICAL for test)
mkdir -p ~/.corvin/tenants/_default/global
cat > ~/.corvin/tenants/_default/global/tenant.corvin.yaml << 'EOF'
spec:
  features:
    a2a_lan_bind: true  # ← CRITICAL
    bridge_mid_turn_task_notify: true  # ADR-0551 (optional for Spike)
EOF

# Start corvinos
corvin serve --host 0.0.0.0 &
# Verify: curl http://localhost:8765/health or curl http://<ubuntu-ip>:8765/health
```

### **1d: Verification**
```bash
# Confirm A2A endpoint is listening on LAN
sudo netstat -tlnp | grep 6789
# Expected: 0.0.0.0:6789 (not 127.0.0.1:6789)

# Confirm HTTP API reachable from external IP
curl -s http://<ubuntu-ip>:8765/health | jq .
# Expected: {"status": "healthy", ...}

# Extract Ubuntu kid (for later pairing confirmation)
cat ~/.corvin/global/remote_trigger/local_kid.txt
# Save this for Step 3 verification
```

**Checkpoint:** Ubuntu ready. Note: IP address, kid, running process PID.

---

## PHASE 2: WINDOWS SETUP (1.5 HOURS)

### **2a: VM Creation**
```bash
# Hypervisor-specific (e.g., KVM with virtio drivers)
virt-install \
  --name corvinOS-windows \
  --memory 4096 --vcpus 2 \
  --disk size=100 \
  --os-type windows --os-variant win10 \
  --network bridge=br0 \  # CRITICAL: same LAN as Ubuntu
  --cdrom /path/to/Win10_install.iso
```

### **2b: Windows OS Install** (30 min)
- Windows 10 / 11 (latest build)
- Hostname: `corvinOS-windows-verify`
- Network: DHCP (auto-connect to LAN bridge)
- Windows Defender: ON (real-world firewall conditions)

### **2c: Provisioning**
```powershell
# Open PowerShell as Administrator

# Install WSL2 (optional, for easier shell scripting)
wsl --install -d Ubuntu-22.04

# OR: Install Python directly
# Download from python.org (3.11+) and add to PATH

# Install corvinos (Windows build)
# Either: native Windows build, or use WSL2 to install
# https://docs.corvinlabs.ai/installation/windows.md

# Create tenant config
mkdir -p $env:USERPROFILE\.corvin\tenants\_default\global
# Edit tenant.corvin.yaml:
@"
spec:
  features:
    a2a_lan_bind: true
    bridge_mid_turn_task_notify: true
"@ | Out-File $env:USERPROFILE\.corvin\tenants\_default\global\tenant.corvin.yaml

# Start corvinos service
# Option 1: Manual start
corvin serve --host 0.0.0.0

# Option 2: Install as Windows Service (for persistent test)
# sc create corvin-service binPath= "C:\path\to\corvin.exe serve --host 0.0.0.0"
# net start corvin-service
```

### **2d: Verification**
```powershell
# Confirm A2A endpoint listening on LAN interface (not 127.0.0.1)
netstat -ano | findstr 6789
# Expected: 0.0.0.0:6789 LISTENING

# Verify HTTP API reachable from Ubuntu VM
ssh corvin@<ubuntu-ip> "curl -s http://<windows-ip>:8765/health | jq ."
# Expected: {"status": "healthy", ...}

# Extract Windows kid
type $env:USERPROFILE\.corvin\global\remote_trigger\local_kid.txt
# Save for Step 3 verification
```

**Checkpoint:** Windows ready. Note: IP address, kid, running process.

---

## PHASE 3: NETWORK VERIFICATION (30 MIN)

### **3a: Ping/Connectivity**
```bash
# From Ubuntu → Windows
ping <windows-ip>
# Expected: replies, no packet loss

# From Windows → Ubuntu
ping <ubuntu-ip>
# Expected: replies, no packet loss

# Firewall check (Linux)
sudo ufw status
# If enabled: allow inbound 6789 from Windows subnet
# sudo ufw allow from <windows-subnet> to any port 6789

# Firewall check (Windows)
# Settings → Firewall → Advanced → Inbound Rules
# Ensure port 6789 is NOT blocked (Defender may block by default)
# If blocked: Add exception for corvinos.exe or port 6789
```

### **3b: DNS/Service Discovery (Optional)**
```bash
# If using mDNS for discovery:
# Ubuntu: install avahi-daemon
sudo apt install -y avahi-daemon

# Test mDNS resolution
avahi-resolve-host-name corvinOS-windows-verify.local
# Expected: IPv4 address of Windows

# Windows: should support mDNS natively (Windows 10+)
# Test: nslookup corvinOS-ubuntu-verify.local
```

**Checkpoint:** Both VMs can ping each other. No firewall blocks port 6789.

---

## PHASE 4: A2A CONFIGURATION (1 HOUR)

### **4a: Disable Relay Fallback (Phase 1b test is direct-send only)**
```bash
# On both Ubuntu and Windows:
# Edit tenant.corvin.yaml

spec:
  features:
    a2a_relay_fallback: false  # Phase 1b is direct-only (Phase 2 will test relay)
    a2a_lan_bind: true        # CRITICAL
```

### **4b: Extract Kids for Pairing**
```bash
# Ubuntu:
UBUNTU_KID=$(cat ~/.corvin/global/remote_trigger/local_kid.txt)
UBUNTU_IP=<from-phase-1>

# Windows:
# PowerShell:
$WINDOWS_KID = Get-Content $env:USERPROFILE\.corvin\global\remote_trigger\local_kid.txt
$WINDOWS_IP = <from-phase-2>

echo "Ubuntu kid: $UBUNTU_KID @ $UBUNTU_IP"
echo "Windows kid: $WINDOWS_KID @ $WINDOWS_IP"
# Save these for test script (Step 5)
```

### **4c: Verify API Endpoints Accessible**
```bash
# Ubuntu calls Windows A2A API
curl -X POST http://<windows-ip>:6789/v1/a2a/ping \
  -H "Content-Type: application/json" \
  -d '{"kid":"test"}'
# Expected: {"status": "pong", ...} or 400 (bad kid is OK for connectivity test)

# Windows calls Ubuntu A2A API
curl -X POST http://<ubuntu-ip>:6789/v1/a2a/ping \
  -H "Content-Type: application/json" \
  -d '{"kid":"test"}'
# Expected: {"status": "pong", ...}
```

**Checkpoint:** Both instances respond to A2A pings from opposite LAN segment.

---

## PHASE 5: A2A PAIRING TEST SCRIPT (READY FOR SEPT 3 MORNING)

### **5a: Create Test Script** (save as `test_a2a_pairing.sh`)

```bash
#!/bin/bash
set -e

UBUNTU_IP="192.168.1.100"   # UPDATE FROM PHASE 1
UBUNTU_KID="e6c0fba2-..."   # UPDATE FROM PHASE 4
WINDOWS_IP="192.168.1.101"  # UPDATE FROM PHASE 2
WINDOWS_KID="a7d1ccb3-..."  # UPDATE FROM PHASE 4

echo "=== PHASE 1a: Kid Generation Verification ==="
echo "Ubuntu kid: $UBUNTU_KID"
echo "Windows kid: $WINDOWS_KID"
[[ -n "$UBUNTU_KID" && -n "$WINDOWS_KID" ]] && echo "✅ PASS: Both kids generated" || echo "❌ FAIL: Missing kid"

echo ""
echo "=== PHASE 1b: Bidirectional Send Test ==="

# Ubuntu → Windows
echo "Sending from Ubuntu to Windows..."
curl -X POST http://$WINDOWS_IP:6789/v1/a2a/receive \
  -H "Content-Type: application/json" \
  -d "{\"kid\":\"$UBUNTU_KID\",\"payload\":\"test from ubuntu\"}" \
  -w "\nHTTP Status: %{http_code}\n"

# Windows → Ubuntu
echo "Sending from Windows to Ubuntu..."
curl -X POST http://$UBUNTU_IP:6789/v1/a2a/receive \
  -H "Content-Type: application/json" \
  -d "{\"kid\":\"$WINDOWS_KID\",\"payload\":\"test from windows\"}" \
  -w "\nHTTP Status: %{http_code}\n"

echo ""
echo "=== PHASE 1b Result ==="
echo "Check audit logs for a2a_task_executed events:"
echo "Ubuntu: tail ~/.corvin/audit.jsonl | grep a2a_task_executed"
echo "Windows: type %USERPROFILE%\.corvin\audit.jsonl | findstr a2a_task_executed"
```

### **5b: Audit Trail Verification**
```bash
# After running test script, check audit events:

# Ubuntu
tail -20 ~/.corvin/audit.jsonl | grep -A2 "a2a_task_executed"
# Expected: event with source_kid=$WINDOWS_KID, route="direct", status="success"

# Windows
# PowerShell:
Get-Content $env:USERPROFILE\.corvin\audit.jsonl -Tail 20 | Select-String "a2a_task_executed"
# Expected: same format
```

**Checkpoint:** Test script ready. Kids extracted. Audit events logged.

---

## TRACKING & ESCALATION

### **Checkpoints (Must Complete by Dates Below)**

| Checkpoint | Task | Target Date | Owner | Escalate If |
|---|---|---|---|---|
| **0** | Infrastructure decision (KVM/VirtualBox/Hyper-V) | Sept 2 16:00 UTC | DevOps | Delayed >30 min |
| **1** | Ubuntu VM booted, corvinos running, kid generated | Sept 3 02:00 UTC | DevOps | Fails or delayed |
| **2** | Windows VM booted, corvinos running, kid generated | Sept 3 04:00 UTC | DevOps | Fails or delayed |
| **3** | Networking verified (ping both directions, A2A API responds) | Sept 3 06:00 UTC | QA/DevOps | Any failure |
| **4** | Test script created and dry-run on localhost (optional) | Sept 3 08:00 UTC | QA | Delayed >1h |
| **5** | Phase 1b live test: bidirectional A2A send | Sept 3 12:00 UTC | QA | **🔴 ESCALATE if FAILS** |
| **6** | Audit trail verified (both directions logged) | Sept 3 14:00 UTC | QA | Any gaps |
| **7** | Report: Phase 1b PASS/FAIL + root cause | Sept 3 16:00 UTC | QA Lead | Missing or late |

### **Escalation Contacts**
- **DevOps Issue (setup fails):** @DevOps-Lead
- **Network Issue (connectivity fails):** @Network-Eng
- **A2A Test Fail (send fails):** @Bridge-Eng + @Steering
- **Audit Issue (events missing):** @Audit-Compliance + @Bridge-Eng

---

## CONTINGENCY (If Option A Exceeds 4h)

| If Delay | Fallback | Impact |
|---|---|---|
| **Provisioning >2h** | Switch to Option B (GitHub Actions CI runners) | May lose real LAN test, but proceed with CI-based verification |
| **Network config >1h** | Use mDNS discovery instead of manual IPs | Slightly slower, but reduces human error |
| **Firewall issues** | Default to relay-only pairing (Phase 2) | Shifts test focus to relay verification |

---

## DELIVERABLES FOR SEPT 3 16:00 UTC

**Owner:** QA Lead + DevOps

**Inputs:**
- [ ] Ubuntu IP, kid, running corvinos process
- [ ] Windows IP, kid, running corvinos process
- [ ] Network connectivity confirmed (ping, curl both directions)
- [ ] Test script output (bidirectional A2A sends)
- [ ] Audit trail exports (both machines)

**Output:**
- [ ] `A2A_VERIFICATION_REPORT_SEPT_3.md` with Phase 1a + 1b results
- [ ] If PASS: "✅ GO — Windows/Linux direct pairing verified"
- [ ] If FAIL: "❌ NO-GO — [specific failure] — root cause: [details]"

**Escalation:** If Phase 1b fails by Sept 3 12:00 UTC → **STOP** and escalate to steering immediately.

---

**STATUS:** Hardware provisioning begins IMMEDIATELY upon approval of this plan.  
**Contact:** For questions/blockers, reach out to DevOps lead.
