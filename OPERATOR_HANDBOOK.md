# CorvinOS Operator Handbook

**Version:** v1.0 (2027-01-05)  
**For CorvinOS v0.6–v1.0**  
**Audience:** All CorvinOS operators

---

## Table of Contents

1. [Installation](#installation)
2. [Getting Started](#getting-started)
3. [Features Overview](#features-overview)
4. [Operations](#operations)
5. [Troubleshooting](#troubleshooting)
6. [FAQ](#faq)
7. [Support](#support)

---

## Installation

### System Requirements

- **Operating System:** Windows 10+, macOS 10.15+, Linux (Ubuntu 18.04+)
- **RAM:** Minimum 4GB, recommended 8GB+
- **Disk Space:** 2GB (3GB with local LLM in v0.8+)
- **Network:** Internet required for v0.6–v0.7; optional in v0.8+

### Quick Start (< 5 minutes)

#### Windows

1. Download `corvinOS-installer.exe` from [downloads.corvinOS.io](https://downloads.corvinOS.io)
2. Run installer
3. Follow setup wizard (selects default options)
4. Launch CorvinOS from Start Menu
5. Create account or log in

#### macOS

```bash
brew install corvinOS
corvinOS-setup
corvinOS start
```

#### Linux

```bash
wget https://releases.corvinOS.io/corvinOS-latest.tar.gz
tar -xzf corvinOS-latest.tar.gz
cd corvinOS-latest
./install.sh
corvinOS start
```

### First-Time Setup

On first launch:

1. **Operator Profile:** Set your name, timezone, language (English / Deutsch)
2. **Preferences:** Initial settings (default is safe, non-intrusive)
3. **API Keys:** Optionally connect to Claude API (required for suggestions in v0.6+)
4. **Privacy Consent:** Review and accept data practices (GDPR compliant)

**Time to complete:** ~2 minutes

---

## Getting Started

### Core Concepts

#### The Brain

CorvinOS's "Brain" is an AI engine that:
- Understands your goals and task context
- Breaks problems into stages (8 stages per task)
- Routes work to the right tool/model
- Learns from your feedback
- Suggests improvements

**Key interface:** Chat window (type your task, Brain responds with guidance)

#### Operator Modeling (v0.6+)

The Brain learns **your style**:
- How much risk you tolerate (cautious vs. bold)
- Your speed preference (thorough vs. quick)
- Your communication style (formal vs. casual)
- Your task strengths (good at auth? memory management?)

**Result:** Personalized guidance tailored to you.

#### Plugins (v0.7+)

Extend CorvinOS with community plugins:
- Database helpers, security tools, performance optimizers
- Install from Settings → Marketplace
- Sandboxed (safe, no system access)

#### Offline Mode (v0.8+)

Work without internet:
- Local LLM fallback (Llama 2 7B on-device)
- Queue your tasks, sync later
- Full feature parity (slightly lower quality)

---

## Features Overview

### Chat Interface

**What it is:** Main interface for working with CorvinOS.

```
┌─────────────────────────────────────────┐
│ CorvinOS Console                        │
├─────────────────────────────────────────┤
│                                         │
│ You: Fix the slow database query        │
│                                         │
│ Brain: I'll help you optimize that.     │
│ Let's break it down...                  │
│  • Identify bottlenecks                 │
│  • Analyze execution plan               │
│  • Propose index strategy               │
│  • Verify performance gain              │
│                                         │
│ [Ask follow-up?]  [Try different approach]│
│                                         │
└─────────────────────────────────────────┘
```

**How to use:**
1. Type your task or question
2. Brain responds with analysis + next steps
3. Provide feedback (good/needs work)
4. Brain learns from your feedback

### Vibe Engineering (v0.6+)

**What it is:** See how the Brain thinks through problems.

**Navigate to:** Console → Vibe Engineering

**What you see:**
- 8-stage breakdown of your task
- Brain's reasoning at each stage
- Confidence scores (how sure is Brain?)
- Glass-box prompt reveal (what instructions did Brain get?)

**Use case:** Understand why Brain suggested something, debug if something feels off.

### Your Talent Dashboard (v0.6+)

**What it is:** Your operator model + task strengths.

**Navigate to:** Console → Learning → Your Talent

**What you see:**
- **Risk Tolerance:** How bold you typically are (0-100)
- **Speed Preference:** How fast you decide (0-100)
- **Task Strengths:** Which task types you're good at
  - ✅ Strong (≥75% success)
  - ◐ Neutral (45-75% success)
  - ✗ Weak (<45% success)

**Use case:** See what the Brain learned about you. Understand your own patterns.

### Plugin Marketplace (v0.7+)

**What it is:** App store for CorvinOS plugins.

**Navigate to:** Settings → Plugins → Marketplace

**What you can do:**
- Browse 50+ community plugins
- Read reviews and ratings
- Install with one click
- Use plugins in tasks (Brain suggests them)

**Safety:** All plugins are sandboxed (can't access your files/network).

### Real-Time Dashboard (v0.9+)

**What it is:** Live view of what the Brain is doing right now.

**Navigate to:** Console → Dashboard

**What you see:**
- **Brain Health:** 13 subsystems and their status
- **Decision Stream:** Every decision the Brain makes (real-time)
- **Cost Tracker:** How much you've spent (API calls, offline computation)
- **Queue:** Pending tasks (in offline mode)

**Use case:** Monitor performance, understand costs, pause/resume tasks.

### Interrupt & Redirect (v0.9+)

**What it is:** Pause the Brain mid-task, redirect to different engine.

**How to use:**
1. In Dashboard, find the running turn
2. Click **Pause** to stop
3. Click **Resume** to continue, or **Redirect** to try a different approach
4. Select new engine (Native, ACS, TDE)

**Use case:** Task is too slow? Switch to faster (but lower-quality) engine.

---

## Operations

### Starting & Stopping

**Start CorvinOS:**
```bash
corvinOS start
```

**Stop CorvinOS:**
```bash
corvinOS stop
```

**Restart (after updates):**
```bash
corvinOS restart
```

**Check status:**
```bash
corvinOS status
```

### Configuration

**Location:** `~/.corvin/tenants/_default/config.yaml`

**Key settings:**
```yaml
spec:
  # Features (default: safe, non-intrusive)
  features:
    operator_modeling_fingerprinting: true   # v0.6+
    operator_modeling_suggestions: false     # v0.6+, opt-in
    operator_modeling_replay: false          # v0.6+, opt-in
    plugins_enabled: true                    # v0.7+
    offline_mode: false                      # v0.8+
    dashboard_enabled: true                  # v0.9+
  
  # Privacy
  learning:
    fingerprint_expiration_days: 365
    snapshot_retention_days: 30
    enable_export: true
    enable_delete: true
  
  # Performance
  performance:
    worker_engine: native  # native, acs, tde
    max_concurrent_tasks: 4
    cache_size_mb: 512
```

**To change:** Edit file, then `corvinOS restart`

### Updates

**Check for updates:**
```bash
corvinOS update check
```

**Install updates:**
```bash
corvinOS update install
```

**View update history:**
```bash
corvinOS update history
```

**Rollback to previous version:**
```bash
corvinOS downgrade v0.5
```

### Logging

**View real-time logs:**
```bash
corvinOS logs --tail=50
```

**Export logs for debugging:**
```bash
corvinOS logs --export /tmp/corvinOS-logs.tar.gz
```

**Log location:** `~/.corvin/logs/`

---

## Troubleshooting

### Brain is slow

**Symptom:** Responses take >5s

**Diagnosis:**
1. Check network: `corvinOS status network`
2. Check API quota: Settings → API → Usage
3. Try offline mode (v0.8+): Settings → Features → Offline Mode

**Fix:**
- If API quota exhausted: Wait for reset, or reduce worker_engine tier
- If network slow: Use offline mode (Llama 2 7B is faster)
- If many concurrent tasks: Reduce `max_concurrent_tasks`

### Suggestions are irrelevant

**Symptom:** Task suggestions don't match what you're working on

**Diagnosis:**
1. Check fingerprint: Console → Learning → Your Talent
2. Verify sample count >50 (fingerprint needs data)
3. Check if feature disabled: Settings → Features → Suggestions

**Fix:**
- **If <50 decisions:** Suggestions will improve as you use CorvinOS
- **If fingerprint wrong:** Go to Settings → Learning → Delete fingerprint (will reset)
- **If feature off:** Enable in Settings → Features

### Offline mode isn't working

**Symptom:** Offline tasks fail or get queued

**Diagnosis:**
1. Check feature enabled: Settings → Features → Offline Mode
2. Check local LLM installed: `corvinOS offline status`
3. Check queue: Dashboard → Queue (pending tasks)

**Fix:**
- **If feature disabled:** Enable in Settings
- **If LLM missing:** Run `corvinOS offline install-llm` (downloads ~4GB)
- **If queue stuck:** Reconnect to internet, then `corvinOS offline sync`

### Data/fingerprint seems wrong

**Symptom:** Suggestions based on wrong assumptions about you

**Diagnosis:**
1. Review fingerprint: Console → Learning → Your Talent
2. Check decision history: Console → Learning → Decisions

**Fix:**
- **To reset fingerprint:** Settings → Learning → Delete fingerprint (immediate)
- **To view your data:** Settings → Privacy → Export (downloads all data)
- **To delete all data:** Settings → Privacy → Delete everything (irreversible)

### Plugin crashes Brain

**Symptom:** Brain stops responding after installing plugin

**Diagnosis:**
1. Check plugin health: Settings → Plugins → Health
2. Check logs: `corvinOS logs | grep plugin-error`

**Fix:**
- **Quick fix:** Settings → Plugins → Disable [plugin name], then restart
- **Permanent fix:** Settings → Plugins → Uninstall [plugin name]
- **Report bug:** Click [Report] in plugin health panel

---

## FAQ

### Q: Is my data private?

**A:** Yes. CorvinOS processes data locally by default:
- Operator fingerprint stored on your device (encrypted)
- Decision history never leaves your device
- Only API calls go to Claude (your choice, can disable)
- GDPR compliant (right to deletion, data export)

**To verify:** Settings → Privacy → Show data location

### Q: How much does CorvinOS cost?

**A:** CorvinOS is **free to use locally**.

**Optional costs:**
- Claude API calls (if you enable suggestions/Brain): $0.003–0.05 per turn
- Premium plugins (v0.7+): ~$5–50/month (optional)
- Offline mode: Free (uses local LLM)

**No subscription, no hidden fees.**

### Q: Can I use CorvinOS offline?

**A:** Yes, starting in v0.8.

**How:**
1. Install offline mode: Settings → Features → Install Offline
2. Queues your tasks when offline
3. Uses Llama 2 7B locally (90%+ of Claude quality)
4. Syncs when back online

**Quality note:** Offline responses are good but slightly lower quality than Claude API.

### Q: How do I export my data?

**A:** Settings → Privacy → Export

**Includes:**
- Decision history
- Operator fingerprint
- Plugin settings
- All audit trail (encrypted)

**Format:** JSON files + audit log

### Q: Can I delete everything?

**A:** Yes.

**To delete:**
1. Settings → Privacy → Delete everything
2. Confirm (irreversible)
3. CorvinOS resets to blank slate

**Note:** This is permanent and cannot be undone. Audit trail (for compliance) is preserved but anonymized.

### Q: How do I report a bug?

**A:** Three ways:

1. **In-app:** Console → Help → Report bug (captures logs automatically)
2. **Discord:** [corvinOS Discord server](https://discord.gg/corvinOS)
3. **Email:** support@corvinOS.io

**Include:** Your OS, CorvinOS version, steps to reproduce.

### Q: What's the difference between v0.6, v0.7, v0.8?

**A:** Major feature releases:
- **v0.6:** Operator modeling (fingerprinting, suggestions)
- **v0.7:** Plugins (install community plugins)
- **v0.8:** Offline mode (work without internet)
- **v0.9:** Dashboard (monitor Brain real-time)
- **v1.0:** Stable release (polished, hardened)

**Auto-update:** Enabled by default (can disable in Settings)

### Q: Is CorvinOS GDPR compliant?

**A:** Yes, fully GDPR compliant (EU).

**Guarantees:**
- No personal data collection without consent
- Right to data export (anytime)
- Right to deletion (immediate effect)
- All data encrypted at rest
- Audit trail immutable (for compliance)
- No third-party enrichment

**Privacy policy:** [corvinOS.io/privacy](https://corvinOS.io/privacy)

---

## Support

### Getting Help

**In-app support:**
1. Console → Help → Handbook (this doc)
2. Console → Help → Report bug
3. Console → Help → Contact support

**Online resources:**
- Handbook: [corvinOS.io/handbook](https://corvinOS.io/handbook)
- FAQ: [corvinOS.io/faq](https://corvinOS.io/faq)
- Tutorials: [youtube.com/@corvinOS](https://youtube.com/@corvinOS)
- Discord: [discord.gg/corvinOS](https://discord.gg/corvinOS)

**Email support:**
- support@corvinOS.io (response <24h)
- For urgent issues: support-urgent@corvinOS.io

### Known Issues & Workarounds

| Issue | Workaround | Status |
|---|---|---|
| Fingerprint takes >1 week to compute | Collect >50 decisions (normal) | Expected (design) |
| Offline LLM slower than Claude API | Use native mode for speed-critical tasks | Expected (tradeoff) |
| Plugin marketplace slow during peak hours | Try again in off-peak (2am–8am UTC) | Known (scaling in progress) |
| Some old plugins incompatible with v0.9 | Update plugin or uninstall | Temporary (v0.10 fixes) |

### SLA (Service Level Agreement)

**Uptime target:** 99.9% (5 minutes downtime/month)

**Response time:**
- Bug report: <24 hours
- Feature request: <1 week
- Security issue: <1 hour

**Support hours:** 24/7 (community volunteers + team)

---

## Glossary

| Term | Definition |
|---|---|
| **Brain** | CorvinOS's AI engine (orchestrates 13 subsystems) |
| **Fingerprint** | Operator's learned style model (risk, speed, communication, task strengths) |
| **Vibe Engineering** | Tool to see Brain's step-by-step reasoning |
| **Operator Modeling** | v0.6 feature: learn & personalize for each operator |
| **Plugin** | Community extension (sandboxed, safe) |
| **Offline Mode** | v0.8 feature: work without internet (Llama 2 7B fallback) |
| **Dashboard** | v0.9 feature: real-time Brain monitoring |
| **What-If Replay** | v0.6 feature: explore counterfactual decisions |
| **GDPR** | European data protection regulation (privacy) |
| **ACS** | Advanced Compute System (higher-quality tool routing) |
| **TDE** | Tiered Delegation Engine (performance optimization) |

---

**Last Updated:** 2027-01-05  
**For CorvinOS v1.0+**  
**Questions?** Email support@corvinOS.io

