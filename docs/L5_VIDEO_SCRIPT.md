# L5 Operator Training Video Script — 10 Minutes

**Format:** Screen recording + voiceover  
**Target Audience:** New L5 operators  
**Production Notes:** Use CorvinOS UI screenshots + animated diagrams  

---

## VIDEO 1: INTRODUCTION (1:00)

**Visual:** Title card "L5: Automated Decision Approval for CorvinOS"

**Voiceover:**
"Welcome to L5, CorvinOS's automated decision approval system. In this 10-minute video, you'll learn how L5 works, your role as an operator, and how to make approval decisions.

L5 automates the boring decisions and gives you the interesting ones. Think of it as a smart assistant that learns from your feedback, gets better over time, and handles 60% of changes automatically.

What is L5? It's a five-gate approval system:
- Gate 1 (Smooth): Auto-approves high-confidence changes
- Gate 2 (Operator): Your decision point—approve or reject
- Gate 3 (Quality): Validates the change is correct
- Gate 4 (Conflict): Detects conflicts between skills
- Gate 5 (Hold): 24-hour safety monitoring

Your job: Make 10-30 approval decisions per day with about 80% accuracy. The system learns from your decisions and improves."

**Visual:** Animated flowchart showing the 5 gates

**Duration:** 1:00

---

## VIDEO 2: THE APPROVAL QUEUE (1:30)

**Visual:** Screenshot of the Approval Control Panel (ApprovalControlPanel.tsx)

**Voiceover:**
"Let's look at the approval queue. You'll see pending changes waiting for your decision. Each request shows:

- **Skill ID**: Who's proposing this change
- **Metric name**: What's being changed (e.g., 'timeout_ms')
- **Confidence score**: How sure the skill is (0-100%)
- **Proposed value**: The new value being suggested
- **Current value**: What it's set to now
- **Delta**: Percentage change from current

Look at this example: Skill 'os.routing' wants to increase 'request_timeout_ms' from 5000 to 5100. That's only a 2% change, and confidence is 91%. This looks safe.

But this one's concerning: Skill 'os.cache' wants to disable caching entirely. That's a 100% change, and confidence is only 72%. You'd probably want to reject this—too much risk, not enough confidence.

The best practice: Batch 10-15 approvals per session for efficiency. Don't jump between individual items."

**Visual:** 
- Screenshot of queue with highlighted examples
- Annotations showing what each field means
- Animation: scrolling through 10-15 items

**Duration:** 1:30

---

## VIDEO 3: DECISION FRAMEWORK (1:45)

**Visual:** Decision tree table from Operator Guide

**Voiceover:**
"How do you decide? Use this simple framework:

**If confidence is 85% or higher**, skill has a good history, and the change is small (<10%), **APPROVE**.

**If confidence is 75-85%**, it depends: Is the skill reliable? Has it made similar changes successfully? If yes, APPROVE. If no, REJECT.

**If confidence is below 75%**, the skill is too uncertain. **REJECT**. Wait for more data. You're not being mean—rejections are how skills learn.

**Red flags** you should always reject:
- Timeout changes > 50% (could break everything)
- Connection limits changing 10x (massive instability risk)
- Values outside historical range (weird outliers)
- Cascading changes on the same metric (skill is confused)
- A skill that keeps proposing changes you keep rejecting (skill is broken)

**Green lights** you can usually approve:
- Less than 10% change from current value
- Confidence > 85%
- Skill with good track record (< 5% revoke rate)
- Similar decision approved successfully before
- No conflicting changes in queue

And remember: You're not expected to know everything. If a request looks reasonable, ask a colleague. Discussion is logged and helps everyone learn."

**Visual:**
- Decision framework table (animated, with examples highlighting safe vs. risky zones)
- Red/green highlighting of example requests
- Icons for "approved" vs. "rejected" decisions

**Duration:** 1:45

---

## VIDEO 4: WHAT HAPPENS AFTER YOU APPROVE (1:15)

**Visual:** Animated 5-gate flow with timeline

**Voiceover:**
"Once you approve, the change goes through three more gates:

**Gate 3 (Quality)**: Validates the change is syntactically correct and within allowed ranges. If it passes, great. If it fails, the system blocks it and the skill re-proposes with a fixed value.

**Gate 4 (Conflict)**: Checks if other pending changes would conflict. For example, if another skill is trying to *reduce* connections while this skill *increases* timeout, Gate 4 detects that and resolves the conflict (usually by rejecting the lower-priority change).

**Gate 5 (Hold)**: The change deploys but stays on a 24-hour probation period. During hold, the system monitors:
- Latency (should be normal)
- Error rates (should not spike)
- Logs (should be clean)
- Related changes (other skills making compatible decisions?)

If metrics look clean after 24 hours, the change is locked permanent. If something looks wrong, you can **revoke** the change (rollback)—it's safe because hold protects you."

**Visual:**
- 5-gate diagram with progress bar showing k=2 (your decision) position
- Timeline animation: k=2 → k=3 → k=4 → k=5 (deployment) → 24h hold → locked
- Monitoring dashboard showing metrics during hold period
- "Revoke" button lighting up if metrics look bad

**Duration:** 1:15

---

## VIDEO 5: MONITORING YOUR DECISIONS (1:00)

**Visual:** L5 Metrics Monitor dashboard (L5MetricsMonitor.tsx)

**Voiceover:**
"Your decisions have measurable impact. The dashboard shows:

**Operator latency**: How long it takes you to approve. Target: under 5 minutes. SLA: 10 minutes max.

**Accuracy**: % of your approvals that don't later get revoked. Target: > 97%. You're not expected to be perfect, but track your trend.

**Rejection rate**: % you reject. Target: 10-20%. If you're rejecting everything (> 50%), maybe you're too cautious. If you're rejecting nothing (< 5%), maybe you're being too permissive.

**Auto-approval rate**: System-wide, % of changes that passed Gate 1. Target: 55-65%. Not your control, but it tells you if the system is working right.

**Revoke rate**: % of changes that failed during hold. Target: < 3%. If this climbs, something's wrong—either bad approvals or bad skills.

Check this dashboard weekly when you're ramping up. Once stable, monthly spot-checks are fine."

**Visual:**
- Live dashboard with real metrics
- Highlighting your personal stats vs. team average
- Sparklines showing trends over time
- Color coding: green (healthy), yellow (caution), red (alert)

**Duration:** 1:00

---

## VIDEO 6: REAL-WORLD EXAMPLE (2:00)

**Visual:** Walkthrough of actual approval request

**Voiceover:**
"Let's walk through a real approval request step-by-step.

Scenario: It's Tuesday morning. You're looking at the approval queue. You see a request from 'os.delegation_router' (a Skill that optimizes request routing). It wants to change 'router_cache_ttl_seconds' from 300 to 285. That's a 5% decrease. Confidence: 87%.

Step 1: Read the skill's reasoning. It says: 'Cache TTL optimal at 285s based on recent request patterns. Reduces memory footprint without affecting hit rate.' Sounds reasonable.

Step 2: Check the skill's history. You click the audit trail. Last 10 similar decisions: 9 approved, 1 revoked (revocation rate: 10%, slightly high but not terrible). The one revocation was 2 weeks ago, and the skill has improved since.

Step 3: Check for conflicts. You scan the queue for other routing changes. You see one from 'os.cache' that increases cache size. These changes are compatible (less TTL + more cache space actually work well together). No conflict.

Step 4: Make your decision. Confidence is 87% (above 85%). Change is small (5%). Skill history is decent. No conflicts. You APPROVE.

The request goes to Gate 3 (Quality), which validates 285 is within the allowed range [60, 3600]. Passes. Then Gate 4 checks for conflicts (finds none). Then Gate 5 deploys with a 24-hour hold. You monitor over the next 24 hours. All metrics stay green. At 24 hours, the hold expires and the change is locked.

Later, you check your accuracy dashboard. That approval didn't revoke, so your accuracy went from 96.5% to 96.6%. Good.

That's L5 in a nutshell."

**Visual:**
- Screen recording walking through actual ApprovalControlPanel
- Clicking into audit trail
- Viewing skill history graph
- Seeing conflict detection
- Approving the request
- Real-time monitoring during hold period
- Final accuracy update on dashboard

**Duration:** 2:00

---

## VIDEO 7: TROUBLESHOOTING & WHEN TO ASK FOR HELP (1:30)

**Visual:** Troubleshooting flowchart + contact info

**Voiceover:**
"Sometimes things go wrong. Here's how to handle it:

**Scenario 1: I approved something, and it's causing issues during hold.**  
Action: Click the revoke button. The system rolls back immediately. Document what went wrong in the revocation reason—skills learn from this.

**Scenario 2: A skill keeps proposing changes I keep rejecting.**  
Action: Escalate to the Skill owner. This usually means the skill's strategy needs adjustment. Or maybe the skill is broken—ops team investigates.

**Scenario 3: The queue is stuck (no movement for hours).**  
Action: Check dashboard alerts. If something's broken, page the L5 oncall team (link in Slack). Don't try to fix it yourself.

**Scenario 4: I don't understand an approval request.**  
Action: Ask the skill owner or a colleague. It's logged as a discussion. Or skip it and come back later—no time pressure.

**When to escalate:**
- Anything that seems broken (queue stuck, dashboard down, can't log in)
- Decisions you're unsure about (ask a colleague first, then escalate)
- High revoke rates (> 5% over a day) → ops team investigates

**Support contacts:**
- Slack: #l5-oncall for urgent issues
- Email: l5-support@corvin-labs.io for questions
- Training: Complete this video series + the interactive tutorial + the FAQ"

**Visual:**
- Flowchart: "Something broken?" → "Check dashboard alerts" → "Escalate to #l5-oncall"
- Screenshot of Slack channel + support email
- List of quick links (FAQ, tutorial, skill owner contact list)

**Duration:** 1:30

---

## VIDEO 8: CLOSING (0:40)

**Visual:** Montage of successful operators using the system

**Voiceover:**
"You now know how L5 works. Your job is straightforward:

1. Review 10-30 approval requests per day
2. Use judgment based on confidence, skill history, and change magnitude
3. Monitor outcomes on the dashboard
4. Escalate when something looks broken

L5 is powerful because it learns from you. Every approval, every rejection teaches the system. Over time, with your help, the system gets smarter and routes fewer uncertain decisions your way.

You're not expected to be perfect. You're expected to be thoughtful and willing to learn. The system is built to catch your mistakes (during hold period), and your mistakes teach it.

Next steps: Complete the interactive tutorial for hands-on practice. Read the FAQ for detailed answers. And reach out to the L5 team if you have questions.

Thank you for helping CorvinOS make better decisions."

**Visual:**
- Montage: different operators approving/rejecting decisions
- Dashboard metrics climbing (accuracy up, revoke rate down)
- Happy users benefiting from fast deployments
- Logos: CorvinOS + L5 system

**Duration:** 0:40

---

## PRODUCTION NOTES

**Recording setup:**
- Screen resolution: 1920x1080
- Browser zoom: 100%
- Record CorvinOS running locally or in staging
- Use real approval requests if possible (redacted user names)

**Voiceover:**
- Read at normal pace (~140 WPM)
- Pause for 1-2 seconds after each major point
- Use friendly, conversational tone (not robotic)
- Emphasize key decisions: confidence, skill history, magnitude

**Graphics:**
- Create animated flowcharts for 5-gate process
- Show red/green highlighting for safe vs. risky decisions
- Use sparklines for metric trends
- Include decision framework table with colors

**Final video specs:**
- Total length: ~10 minutes
- Format: MP4 (h.264, AAC)
- Subtitles: English (SRT file)
- Resolution: 1920x1080 @ 30fps
- File size: ~100-150 MB

**Distribution:**
- Host on CorvinOS internal wiki / L5 dashboard
- Embed in interactive training tutorial
- Link from FAQ + operator guide
- Make available offline (download link)

**Updates:**
- Review every 3 months for accuracy
- Update for major L5 system changes (new gates, new thresholds)
- Version control in git (docs/videos/)

---

**Last Updated:** 2026-09-04  
**Ready for:** Production video recording
