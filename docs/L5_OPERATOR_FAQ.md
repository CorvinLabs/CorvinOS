# L5 Operator FAQ — 50+ Common Questions

**Last Updated:** 2026-09-04  
**Audience:** L5 Operators, Skill Owners, Ops Teams

---

## QUICK START (5 Questions)

**Q1: What is L5 in one sentence?**  
A: L5 is an automated approval system that learns which config changes are safe, auto-approves high-confidence ones, and routes uncertain changes to you for manual review.

**Q2: How much time will L5 add to my job?**  
A: ~10-20 minutes per day. You review ~15-30 approval requests (takes ~20-60 seconds each), and the system handles 60%+ automatically.

**Q3: What happens if I mess up and approve something bad?**  
A: The system has a 24-hour "hold period" to catch issues. If problems appear, you can revoke the change. The system also learns from reversions to avoid similar decisions.

**Q4: Can I trust the auto-approvals?**  
A: Yes, with caveats. Auto-approvals only happen with >95% confidence on changes the system has seen before. But nothing is 100% safe—monitor dashboards during hold periods.

**Q5: Who do I contact if something breaks?**  
A: Your org's L5 escalation team (usually in Slack #l5-oncall). For training, check the L5 dashboard or email l5-support@corvin-labs.io.

---

## THE 5 GATES (12 Questions)

**Q6: Why are there 5 gates instead of 1?**  
A: Each gate checks a different property: confidence (k=1), human judgment (k=2), correctness (k=3), cross-skill coordination (k=4), runtime safety (k=5). Redundancy catches errors gates before them miss.

**Q7: Can a change skip gates?**  
A: No. Every change goes through all 5 gates in order (k=1→k=2→k=3→k=4→k=5), though k=1 can auto-complete, and you usually only touch k=2.

**Q8: What does "confidence" really mean?**  
A: It's the system's estimated probability that the proposed change will not cause a revocation within 7 days. High confidence = skill has seen similar changes succeed before.

**Q9: If k=1 auto-approves, does it skip k=2?**  
A: Yes. Auto-approved changes go k=1→k=3→k=4→k=5 (skipping k=2 operator review). This is intentional—k=1 uses high thresholds (>95%) to ensure safety.

**Q10: What if k=3 rejects my approval?**  
A: The change is blocked and logged. The skill receives feedback ("you proposed an invalid value"). It will propose a corrected value that passes k=3, and you can review it again if it's >70% confidence.

**Q11: How does k=4 detect conflicts?**  
A: k=4 checks if multiple pending changes would interfere (e.g., Skill A increases timeout, Skill B decreases connections). If it finds incompatibilities, it rejects the lower-priority one.

**Q12: Can two operators approve different conflicting changes?**  
A: k=4 runs AFTER approval, so conflicts are caught before deployment. But if an operator approves something questionable, k=3 validation or k=5 hold can catch it.

**Q13: What's the point of k=5 (Hold)?**  
A: k=5 is your safety net. Changes deploy but stay "on probation" for 24h. If monitoring detects issues, you can revoke (rollback) without permanent damage.

**Q14: Can I shorten the 24-hour hold?**  
A: Your org controls hold duration (defaults to 24h, configurable 1-72h). Shorter holds = faster deployment, longer holds = more safety margin. Ops decides the tradeoff.

**Q15: What if I revoke a change during hold?**  
A: The change rolls back to the previous value instantly. The skill learns "that change caused problems" and adjusts (lower confidence on similar future changes).

**Q16: Can a change pass all 5 gates but still fail?**  
A: Yes, in theory. All gates guard against known failure modes, but new issues can emerge. That's why monitoring and operator attention during hold are critical.

**Q17: How often do changes actually get revoked?**  
A: Target: < 3% revocation rate. If you see > 5%, that's a signal something's wrong (bad skill, broken validation, or unusual load).

---

## OPERATOR APPROVAL (k=2) (13 Questions)

**Q18: How do I decide APPROVE vs. REJECT?**  
A: Use the decision framework in the Operator Guide (Table 3). Roughly: confidence > 85% + good skill history = APPROVE. New skill or low confidence = REJECT (wait for more data).

**Q19: What if I don't know anything about the metric?**  
A: The approval request includes "reason" from the skill. Read it carefully. If you're still unsure, REJECT—better safe than sorry. Skills expect rejections and learn from them.

**Q20: Should I approve quickly or think hard?**  
A: SLA is 5 minutes, but take the time you need (up to ~10 min is fine). Batching 10-15 approvals in one session is more efficient than context-switching.

**Q21: What's the worst thing that can happen if I approve badly?**  
A: The change could cause a revocation (24h hold catches it). The skill learns you're unreliable and deprioritizes your approvals (asks for other operators). Repeat rejections → escalation to your manager.

**Q22: Can I discuss approval decisions with colleagues?**  
A: Yes, good idea. Talk to skill owners, ops engineers, whoever knows the system. Discussion is logged (reason field), and others can review your call later.

**Q23: Should I batch approvals or do them one-by-one?**  
A: Batching is better for efficiency. Approve 10-15 at a time if possible. But don't batch things you don't understand—better to skip one and move on.

**Q24: What if the queue has 100 items and I can only do 20?**  
A: Prioritize: CRITICAL > NORMAL > LOW. Do all CRITICAL + HIGH, then as many NORMAL as time allows. Low-priority items stay pending (safe—no time pressure).

**Q25: Can I undo an approval?**  
A: No, approval is permanent. But if the change causes issues during hold (k=5), you can REVOKE it. Revocation is different from undoing—it rolls back and teaches the skill a lesson.

**Q26: Should I worry about being wrong?**  
A: No. Wrong decisions are part of learning. Skill owners and ops teams monitor outcomes. Your job is to use judgment with available info, not be perfect.

**Q27: How do I know if I'm doing well?**  
A: Your approval accuracy is tracked (% of approvals that don't later revoke). Target: > 97% accuracy. Dashboard shows your personal stats vs. team average.

**Q28: Can I set up a deputy to approve on my behalf?**  
A: Ask your org's ops lead. Some teams allow "approval delegates" if you're unavailable. Delegate must sign a handoff agreement.

**Q29: What if I approve something and then find an issue myself?**  
A: During hold (k=5), you can revoke it. After hold expires, the change is locked—escalate to ops if critical. Future: always flag concerns immediately.

**Q30: How much training do I need before approving?**  
A: Complete this FAQ + the interactive tutorial. After that, start with low-stakes changes (< 1% delta, high confidence). Ramp up gradually.

---

## METRICS & MONITORING (11 Questions)

**Q31: What's the most important metric to watch?**  
A: Revocation rate (% of changes that fail after approval). Target: < 3%. If it climbs above 5%, something's broken—investigate immediately.

**Q32: How often should I check the dashboard?**  
A: Daily during ramp-up. Once stable, weekly spot-checks. During any incident or threshold change, monitor more closely.

**Q33: What if operator latency is high?**  
A: Could mean: (a) too many approvals queued, (b) approval UI is slow, or (c) ops team is understaffed. Work with your lead to diagnose.

**Q34: What does "auto-approval rate" mean?**  
A: % of changes that passed k=1 (auto-approved without operator touch). Target: 55-65%. Too low (<45%) = too conservative. Too high (>75%) = maybe unsafe.

**Q35: Should I worry about gate latency?**  
A: Gate latency is usually <1s (k=1), <100ms (k=3), <2min (k=4). If k=4 (conflict) is slow, that's a problem—report it. Otherwise, usually background noise.

**Q36: What's a "SLA breach"?**  
A: Approval took > 5 min (k=2) or k=4 took > 2 min. Rare, usually means system overload. Not your fault, but report to ops if you see patterns.

**Q37: How do I read the "confidence trajectory"?**  
A: Dashboard shows confidence on similar past changes. Trending UP = skill improving. Trending DOWN = skill struggling (maybe new environment, need retuning).

**Q38: What does "skill health" mean?**  
A: Composite of: approval acceptance rate (% of k=2 approvals), revocation rate (% that failed), hold-period incidents. Health score: 0-100. Target: > 85.

**Q39: Can I see metrics for other operators?**  
A: Yes, team dashboard shows aggregate + individual stats (blinded by default). You can see your own stats + team average. Ops lead can show you peer comparison.

**Q40: Should I compete with other operators?**  
A: No. System incentivizes accuracy (% of approvals that don't revoke), not speed. Better to take 10 min on a hard decision than 1 min and get it wrong.

**Q41: What if my skill has 100% acceptance rate?**  
A: Great! But could also mean: (a) you're too generous, or (b) the skills proposing changes are just really good. Check your rejection rate—target: 10-20%.

---

## TROUBLESHOOTING (9 Questions)

**Q42: I approved something, and it's causing issues. What do I do?**  
A: If still in hold period (first 24h): REVOKE immediately. If hold expired: escalate to ops. Either way, the system logs the issue for future learning.

**Q43: A skill keeps proposing changes I keep rejecting. What happens?**  
A: Skill receives your rejections as feedback. If >3 rejections in a row on similar changes, skill adjusts (lowers confidence, tries different approach). If it keeps failing, escalate to skill owner.

**Q44: The queue is stuck (no movement for hours). What's wrong?**  
A: Possible causes: (a) k=3 validation broken (every change fails), (b) k=4 conflict loop (changes keep blocking each other), (c) system crashed. Check dashboard alerts. Escalate to ops.

**Q45: I can't log in to the approval panel. What do I do?**  
A: Check: (1) VPN connected? (2) Session expired? (3) Permission revoked? Contact ops + IT. Should recover in < 5 min.

**Q46: I see a change I want to approve, but the "approve" button is greyed out. Why?**  
A: Could mean: (a) already approved by someone else, (b) already rejected (in retry loop), (c) on hold from previous attempt, or (d) permission issue. Hover for tooltip.

**Q47: A skill is asking me to approve something that looks crazy. Should I?**  
A: NO. Read the skill's reasoning. If it doesn't make sense, REJECT. Leave a note in the reason field. Ops will investigate. Crazy proposals = broken skill.

**Q48: Can approvals time out?**  
A: Yes. If you don't decide within 10 min, the approval gets escalated (bumped to higher priority queue). You get a reminder. Decide ASAP.

**Q49: I revoked a change, and now the skill is proposing it again. What gives?**  
A: The skill is supposed to learn from revocations. If it's immediately re-proposing after revocation, either: (a) it didn't learn, or (b) it's trying a slightly different approach. Either way, this is a problem—escalate.

**Q50: What's the "hold period" exactly?**  
A: After you approve and change deploys, it stays "on trial" for 24h. Monitoring watches for issues. At 24h, if no issues, change is locked (permanent). If issues found, revoke (rollback).

---

## ADVANCED TOPICS (8 Questions)

**Q51: How does the system learn from my decisions?**  
A: Every approval/rejection teaches the skill. Approved changes that don't revoke = skill's confidence was justified. Rejected changes that later pass = skill was right, operator was too cautious. Skills adjust.

**Q52: Can I see the audit trail for a change?**  
A: Yes, click the change in the approval panel → "audit trail" tab. Shows every decision, every gate result, every operator who touched it. Fully transparent.

**Q53: What if two skills disagree on the same metric?**  
A: k=4 (conflict gate) detects this. It ranks by importance and approves the higher-ranked one, rejects the other. The loser re-proposes with adjustment. You only see this if it's a deadlock (rare).

**Q54: Can I adjust the confidence threshold myself?**  
A: No, ops team controls it. But you can suggest changes. If you see systematic issues (too many auto-approvals = too many revokes, or too few auto-approvals = too much queue), tell your lead.

**Q55: What happens to my approvals if the system reboots?**  
A: Persisted to disk. After reboot, you'll see the same queue. In-flight approvals (while rebooting) might be lost—escalate to ops if this happens.

**Q56: Can I appeal an auto-rejection (k=1)?**  
A: No, k=1 is automatic. But if you think a change should have passed k=1, notify the skill owner. They can adjust the skill's strategy (propose with higher confidence, or in smaller increments).

**Q57: What if I disagree with k=3 (Quality) validation?**  
A: File an issue. Quality validation is conservative (err on safe side). If you think a valid change was wrongly rejected, escalate. Quality team will review.

**Q58: How do I know if a skill owner is cheating (gaming the system)?**  
A: Watch for patterns: always high confidence (suspicious), always rejected (maybe unreliable), or changes that always revoke (skill is broken). Report to ops. System tracks all of this.

---

## ORGANIZATIONAL & TRAINING (4 Questions)

**Q59: Is there a certification program?**  
A: Some orgs run L5 Operator Certification (levels 1-3). Level 1: basic approval. Level 2: advanced tuning + mentoring. Level 3: skill owner liaison. Ask your ops lead.

**Q60: Can I train others on L5?**  
A: Yes, if you're Level 2+ certified. Share this FAQ + the interactive tutorial. Have new operators start with low-risk changes under supervision.

**Q61: What if my org doesn't have many L5 users yet?**  
A: You might be on a pilot team. That's fine—more learning opportunity. Pilot feedback helps shape the system. Take notes on what's hard / confusing / surprising.

**Q62: How is L5 different from a traditional change management system?**  
A: Traditional CM is approval-first (approve before deploying). L5 is confidence-first (auto-approve high-confidence, operator review mid-confidence, reject low-confidence). Faster, learns over time.

---

## EMERGENCY SITUATIONS (2 Questions)

**Q63: The system is down. What do we do?**  
A: Manual mode: all changes require operator approval (via Slack/email, not the dashboard). Page the L5 oncall. Incident commander will coordinate recovery. ETA: 1-2 hours.

**Q64: I accidentally approved something that shouldn't have been approved, and it's already deployed past hold. What now?**  
A: Escalate immediately to ops. Document what happened. Ops will investigate root cause (why didn't guards catch it?), possibly roll back, and improve safeguards.

---

**Last Updated:** 2026-09-04  
**Questions or feedback?** Email l5-support@corvin-labs.io or post in #l5-oncall Slack.
