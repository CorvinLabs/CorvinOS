# Phase 1: CorvinOS Operator Setup Guide

**Version:** 1.0  
**Date:** 2026-09-22  
**Audience:** New CorvinOS operators  
**Time to Complete:** < 2 hours

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Initial Configuration](#initial-configuration)
4. [First Skill Setup](#first-skill-setup)
5. [Verify Installation](#verify-installation)
6. [Troubleshooting](#troubleshooting)
7. [Next Steps](#next-steps)

---

## Prerequisites

Before starting, ensure you have:

- **System Requirements:**
  - Linux (Ubuntu 20.04+, RHEL 8+) or macOS (12+) or Windows with WSL2
  - Python 3.10+ installed
  - 4GB RAM minimum (8GB recommended)
  - 2GB free disk space
  - Internet connection (for initial setup)

- **Software Dependencies:**
  - `git` (for version control)
  - `pip` (Python package manager)
  - `curl` (for API testing)
  - `jq` (optional, for JSON parsing)

- **Credentials:**
  - Anthropic API key (get one at https://console.anthropic.com)
  - GitHub account (for marketplace plugins, optional)

**Verification (run in terminal):**

```bash
python3 --version      # Should be 3.10+
pip --version          # Should be pip 21+
git --version          # Should be git 2.30+
curl --version         # Should be curl 7.68+
```

---

## Installation

### Step 1: Clone the Repository

```bash
cd ~
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
git submodule update --init --recursive
```

**Expected output:**
```
Cloning into 'CorvinOS'...
Submodule 'corvin_decisions' registered for path 'corvin_decisions'
...
```

### Step 2: Create Virtual Environment

```bash
python3 -m venv ~/.corvin-env
source ~/.corvin-env/bin/activate    # On Windows: ~/.corvin-env\Scripts\activate
```

**Verify activation:**
```bash
which python   # Should show ~/.corvin-env/bin/python
python --version
```

### Step 3: Install CorvinOS

```bash
cd ~/CorvinOS
pip install -e .
```

**Expected output:**
```
Successfully installed corvinOS (1.0.0)
```

### Step 4: Set Environment Variables

```bash
# Create/edit ~/.bashrc or ~/.zshrc (depending on your shell)
export CORVIN_HOME=~/.corvin
export ANTHROPIC_API_KEY="your-api-key-here"
export CORVIN_TENANT_ID="_default"
```

**Reload shell:**
```bash
source ~/.bashrc    # or source ~/.zshrc
```

**Verify:**
```bash
echo $CORVIN_HOME
echo $ANTHROPIC_API_KEY
```

### Step 5: Initialize CorvinOS

```bash
corvin bootstrap --tenant=_default
```

**Expected output:**
```
✅ CorvinOS initialized
✅ Audit chain created
✅ Plugin registry initialized
✅ Ready for operation
```

**What was created:**
- `~/.corvin/` — Main CorvinOS home directory
- `~/.corvin/tenants/_default/global/` — Tenant configuration
- `~/.corvin/audit.jsonl` — Audit trail (hash-chained)
- `~/.corvin/plugins/` — Plugin directory

---

## Initial Configuration

### Step 1: Configure Tenant Settings

```bash
corvin config set --tenant=_default \
  --engine=anthropic \
  --model=claude-opus-5 \
  --max-tokens=8000
```

**Verify:**
```bash
corvin config get --tenant=_default
```

### Step 2: Set Up Telemetry (Optional)

```bash
# Enable anonymous usage telemetry
corvin telemetry enable --level=basic

# Or opt-out completely
corvin telemetry disable
```

### Step 3: Enable Learning Loop

```bash
corvin learning enable
```

**Verify:**
```bash
corvin learning status
# Expected: Learning loop ACTIVE, feedback processor running
```

### Step 4: Configure Cost Tracking (Optional)

```bash
corvin cost tracking enable --threshold=100 --currency=USD
```

---

## First Skill Setup

### Step 1: List Available Skills

```bash
corvin skill list --source=marketplace
```

**Expected output:**
```
┌─────────────────────────────────────────────────────────┐
│ Available Skills                                          │
├─────────────────────────────────────────────────────────┤
│ 1. assistant.quick_fix — Find and fix common errors     │
│ 2. assistant.cost_optimizer — Optimize model selection  │
│ 3. assistant.workflow_generator — Generate workflows    │
│ ... (20+ more)
```

### Step 2: Install Your First Skill

```bash
# Install the Quick Fix skill (easiest to start with)
corvin skill install assistant.quick_fix

# Verify installation
corvin skill status assistant.quick_fix
```

**Expected output:**
```
┌─────────────────────────────────────────────────────────┐
│ Skill: assistant.quick_fix                              │
├─────────────────────────────────────────────────────────┤
│ Status: ✅ ACTIVE
│ Version: 1.2.0
│ Learning: ENABLED
│ Confidence: 0.87 (23 runs, 18 successful)
│ Last Updated: 2026-09-22 16:30:00 UTC
└─────────────────────────────────────────────────────────┘
```

### Step 3: Test the Skill

```bash
# Create a test file with an error
echo 'def hello_world(' > test.py

# Run the skill
corvin quick_fix --file=test.py

# Expected: Skill identifies syntax error and suggests fix
```

### Step 4: Check Learning Feedback

```bash
# View feedback loop
corvin learning view-feedback --skill=assistant.quick_fix

# Expected: Shows feedback collected from your usage
```

---

## Verify Installation

Run the verification suite:

```bash
corvin verify --all
```

**Expected output:**
```
┌─────────────────────────────────────────────────────────┐
│ ✅ Verification Report                                   │
├─────────────────────────────────────────────────────────┤
│ ✅ Python environment:              OK
│ ✅ CorvinOS installation:           OK
│ ✅ Audit chain:                     OK (234 events)
│ ✅ Plugin registry:                 OK (6 plugins loaded)
│ ✅ API connectivity:                OK (anthropic.com reachable)
│ ✅ Learning loop:                   OK (processor running)
│ ✅ Tenant isolation:                OK (1 active tenant)
│ ✅ Console web UI:                  OK (http://localhost:8765)
└─────────────────────────────────────────────────────────┘
```

### Test Each Component

```bash
# Test API connectivity
curl -s http://localhost:8765/v1/console/capabilities/manifest | jq .

# Test audit chain
corvin audit verify-chain --tenant=_default

# Test skill execution
corvin skill execute assistant.quick_fix \
  --input="Analyze this error: NameError: name 'x' is not defined"

# Test learning feedback
corvin learning emit-feedback --skill=assistant.quick_fix \
  --feedback-type=outcome \
  --signal=correct
```

---

## Troubleshooting

### Issue: `ANTHROPIC_API_KEY not set`

**Solution:**
```bash
export ANTHROPIC_API_KEY="your-api-key-here"
source ~/.bashrc
echo $ANTHROPIC_API_KEY  # Verify it's set
```

### Issue: `Permission denied` when running `corvin` commands

**Solution:**
```bash
# Add CorvinOS bin to PATH
export PATH="~/.corvin-env/bin:$PATH"
echo 'export PATH="~/.corvin-env/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Or run with full path
~/.corvin-env/bin/corvin bootstrap
```

### Issue: `Audit chain verification failed`

**Solution:**
```bash
# Repair audit chain
corvin audit repair --tenant=_default

# If that fails, reinitialize (WARNING: clears history)
rm -rf ~/.corvin/
corvin bootstrap --tenant=_default
```

### Issue: `Plugin X failed to load`

**Solution:**
```bash
# Check plugin status
corvin plugin status plugin-name

# View detailed error log
corvin plugin logs plugin-name --tail=50

# Reinstall plugin
corvin plugin uninstall plugin-name
corvin plugin install plugin-name
```

### Issue: `Learning loop not starting`

**Solution:**
```bash
# Check systemd service (if installed)
systemctl --user status corvin-learning-processor

# Start manually
corvin learning enable --force

# View logs
corvin learning logs --tail=100
```

---

## Next Steps

### 1. Explore the Console UI

```bash
# Open the console in your browser
corvin console open
# Or navigate to: http://localhost:8765/console
```

### 2. Install Additional Skills

```bash
corvin skill list --category=optimization
corvin skill install assistant.cost_optimizer
corvin skill install assistant.workflow_generator
```

### 3. Configure Your Team

```bash
# Add team members (if multi-user)
corvin user add --email=team@example.com --role=operator

# Configure team policies
corvin policy set --policy=cost_limit --value=500 --currency=USD
```

### 4. Read the Full Documentation

- **API Reference:** `PHASE1_API_REFERENCE.md`
- **Troubleshooting:** `PHASE1_TROUBLESHOOTING.md`
- **Incident Response:** `INCIDENT_RESPONSE_RUNBOOK.md`
- **On-Call Procedures:** `ON_CALL_PROCEDURES.md`

### 5. Enable Monitoring

```bash
# Start telemetry/monitoring
corvin telemetry enable --level=basic
corvin health monitor --interval=300

# View metrics
corvin metrics view --timerange=24h
```

---

## Support & Getting Help

- **Documentation:** https://corvinlabs.github.io/docs
- **API Reference:** See `PHASE1_API_REFERENCE.md`
- **Troubleshooting:** See `PHASE1_TROUBLESHOOTING.md`
- **GitHub Issues:** https://github.com/CorvinLabs/CorvinOS/issues
- **Community Slack:** https://corvinlabs.slack.com

---

**Congratulations!** You've successfully set up CorvinOS. Your operator onboarding is complete. 🎉

Move to the next step: [Learning the API](#next-steps) or [Setting Up Your First Workflow](#first-skill-setup).
