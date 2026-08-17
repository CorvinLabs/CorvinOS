# TreeOfThoughts Operator Guide

**For:** CorvinOS Console Operators  
**Purpose:** Grade patterns, view confidence scores, manage learning system

---

## Quick Start

### 1. Open Dashboard

In CorvinOS Console:
```
Settings → Learning → TreeOfThoughts Dashboard
```

Or direct URL:
```
http://localhost:3000/learning
```

### 2. See Your Patterns

The left sidebar shows all patterns organized by level:

```
📌 Framework: Voice Synthesis (0.85 confidence)
  ├─ 📘 Method: OpenAI TTS (0.82)
  │  ├─ ✓ Exponential Retry (0.88)
  │  └─ ✓ Fallback to Edge (0.79)
  └─ 📘 Method: Edge TTS (0.76)
     └─ ✓ Detect Rate Limit (0.71)
```

- **Confidence gauge** (blue bar) shows how much the system trusts this pattern
- **Color coding:** 🟢 High (>0.7) | 🟡 Medium (0.3-0.7) | 🔴 Low (<0.3)

### 3. Grade Patterns

Click any pattern to see details:

```
Pattern: Exponential Backoff Retry
Confidence: ████████░░ 88%
Production calls: 247
Last used: 2 minutes ago
Success rate: 98.2%

[👎 Failed] [😐 Neutral] [👍 Good]
```

Click a button to give feedback:
- **👍 Good (+0.3)** — works well, trust it more
- **😐 Neutral (0)** — works but has issues
- **👎 Failed (-0.5)** — doesn't work, needs improvement

Your feedback updates the confidence immediately.

### 4. Add Operator Notes

For each pattern, add context (append-only):

```
Exponential Retry
└─ Operator Notes
   [Add a note explaining when/why this is used]
   
   Example: "This pattern handles OpenAI 429 rate limits.
   Use when API quota exhausted or transient 503 errors.
   Avoid in auth flow (antipattern)."
```

Notes are timestamped and locked (no editing/deletion). They're part of the audit trail.

---

## Understanding Confidence Scores

### How Confidence Updates

Every pattern runs in production and gets scored:

| Event | Confidence Change |
|-------|-------------------|
| **Succeeded** (+0.05) | Used successfully |
| **Failed** (-0.15) | Error occurred |
| **Graded Good** (+0.3) | You gave 👍 |
| **Graded Neutral** (0) | You gave 😐 |
| **Graded Failed** (-0.5) | You gave 👎 |
| **Antipattern** (-0.3) | Used in wrong context |
| **Decay** (-0.1/week) | Unused for 7+ days |

### Bayesian Blending

When a pattern executes, confidence updates:

```
new_confidence = 0.7 × old_confidence + 0.3 × (old_confidence + event_delta)
```

**Example:**
```
Old confidence: 0.60
Event: Success (+0.05)
New confidence: 0.7 × 0.60 + 0.3 × (0.60 + 0.05) = 0.42 + 0.195 = 0.615
```

Result: Confidence grows slowly but steadily from real usage.

### Convergence Over Time

A pattern used 100 times successfully converges toward ~0.85 confidence.  
A pattern with 5% failure rate converges toward ~0.70 confidence.

**This is automatic — no tuning needed.**

---

## Production Scenarios

### Scenario 1: High-Confidence Pattern

```
Pattern: Retry Exponential (0.88 confidence)

Action:
→ System prefers this pattern
→ Uses it automatically in error recovery
→ You rarely need to intervene

What to do:
→ Keep it graded 👍 to maintain trust
→ Add notes if context changes
→ Monitor for regressions (confidence drops)
```

### Scenario 2: Low-Confidence Pattern

```
Pattern: Retry Linear (0.35 confidence)

Action:
→ System avoids this pattern
→ Suggests higher-confidence alternatives
→ Only used if explicitly forced

What to do:
→ Investigate why it's failing
→ Give 👎 if it's broken (mark as deprecated)
→ Or give 👍 if you know it works in certain contexts
→ Add operator notes explaining the context
```

### Scenario 3: New Pattern

```
Pattern: Circuit Breaker (0.50 confidence)

Action:
→ No production usage yet
→ System is neutral about it

What to do:
→ Wait for production data (needs 10+ calls)
→ Grade as you see results
→ Confidence will converge from actual usage
→ Add notes on expected behavior
```

---

## Antipatterns & Context

Some patterns are **dangerous in certain contexts:**

```
Pattern: Retry Backoff
├─ When to use: ✓ API rate-limits, ✓ transient network errors
└─ Antipattern when: ✗ auth failures, ✗ invalid credentials
```

**How the system detects antipatterns:**

If `Retry Backoff` is used during auth (wrong context):
- Confidence drops by -0.30
- Dashboard shows warning: "⚠️ Antipattern detected: auth_failure context"
- You can mark it 👎 to confirm

**Add operator notes** to explain why it's an antipattern:

```
"Retrying auth fails silently — server never returns success
if credentials are invalid. Wastes budget and breaks fast-fail."
```

---

## Integration with Console

TreeOfThoughts integrates with:

- **Settings → Features:** Toggle learning system on/off
- **Audit Panel:** View all confidence-change events
- **Chat Panel:** See which patterns were used in this turn
- **Error Panel:** Link to failed patterns (why did this break?)

When you grade a pattern in the Learning dashboard:
→ Immediately affects the Chat's pattern suggestions

When a chat turn fails:
→ Dashboard suggests investigating the related pattern

---

## Advanced: Reachability

Every pattern should have proof it works:

| Proof Level | Status | Example |
|---|---|---|
| **E2E Test** | ✅ Required | `tests/test_voice_tts_retry.py` |
| **Production Usage** | ✅ Required | 247 calls in production |
| **Operator Grade** | ✅ Bonus | You marked it 👍 |

If a pattern lacks either E2E or production usage:
- Dashboard shows ⚠️ warning
- System treats it as unproven (confidence clamped at 0.5)

**To prove a pattern:**
1. Add E2E test (link via Dashboard)
2. Use it in production (natural usage or guided rollout)
3. Grade it 👍 as you see it work

---

## FAQ

**Q: Can I delete/edit operator notes?**  
A: No — they're append-only for audit compliance. Wrong note? Add a new one explaining the correction.

**Q: What happens if I grade contradictory patterns?**  
A: The system blends all signals. If you grade "Retry" as 👍 but "No Retry" as 👍, the system prefers whichever has more production usage.

**Q: How long until confidence stabilizes?**  
A: ~100 production uses. Rare patterns may take weeks.

**Q: Can patterns be "permanent" or "deprecated"?**  
A: Mark as deprecated by grading 👎 repeatedly. Confidence frozen at 0.0. You can revive it later.

**Q: Why is my pattern confidence stuck?**  
A: It's either unused (decay) or has mixed results (50/50 success rate converges toward 0.5). Add operator notes explaining why.

---

## Support

- **Dashboard not updating?** Check `/learning` route is registered (Phase 7b)
- **Confidence stuck at 0.5?** Pattern needs more production data
- **Can't add notes?** Check you have write permissions (console auth)
- **Missing a pattern?** Run Phase 7d migration again: `python core/learning/migration_runner.py`

---

**Last Updated:** 2026-08-17  
**Next:** Phase 7c live wiring (autoupdate confidence from real chat turns)
