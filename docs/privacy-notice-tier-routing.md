# Privacy Notice — License Tier Routing Disclosure

**Effective Date:** 2026-09-11  
**Compliance:** GDPR Art. 13/14 (Transparency), EU AI Act Art. 50 (Bot Disclosure)

---

## What is Tier-Based Routing?

CorvinOS uses artificial intelligence to route requests to different language models based on your license tier. This is a normal business practice: different service levels receive different infrastructure and optimization strategies.

### How It Works

When you make a request (ask a question, run a skill, execute a workflow), CorvinOS's routing AI (`os.delegation_router`) automatically decides which language model engine to use:

- **Free Tier:** Your requests are routed to Claude Haiku with conservative decision-making (confidence threshold ≥ 0.80). This ensures stable, predictable performance and cost-effective operation.

- **Paid Tier:** Your requests are routed using optimized decision-making (confidence threshold ≥ 0.65). This results in approximately **12% lower token consumption** compared to Free-Tier routing, while maintaining equivalent quality.

- **Enterprise Tier:** Your requests receive custom routing strategies negotiated with the Corvin team, optimized for your specific use cases.

### What Data Is Used?

No personal information, browsing history, or previous requests are used in routing optimization. The routing decision is based **ONLY on:**

- The current request's complexity (word count, token budget, estimated output length)
- Your license tier (free, paid, or enterprise)
- Confidence in the routing decision (internal model uncertainty)

**We never use:**
- Your name, email, or user ID
- Previous requests or conversation history
- Behavioral patterns or preferences
- Demographic information
- Device information

### Tier-Based Optimization Example

| Aspect | Free Tier | Paid Tier |
|--------|-----------|-----------|
| Router Confidence Threshold | ≥ 0.80 | ≥ 0.65 |
| Route Decision | Conservative (Haiku) | Optimized (Sonnet/Opus) |
| Token Consumption | ~1,200 tokens/request | ~1,056 tokens/request (12% savings) |
| Quality | High (Haiku) | High (Sonnet/Opus) |
| Latency | ~2–3 seconds | ~2–4 seconds |
| Disclosure | Yes (audit trail) | Yes (audit trail) |

### Is This Disclosed?

**Yes, completely.** Every routing decision is logged to an immutable audit trail at `/v1/console/audit/routing-decisions`. You can inspect:

- Which engine routed your request (Haiku/Sonnet/Opus)
- The routing confidence score (0.0–1.0)
- The routing reason (cost_optimized, quality_priority, latency_optimized)
- Your license tier at the time of request
- The timestamp of the decision

**Real audit entry example:**
```json
{
  "timestamp": "2026-09-11T10:30:45.123Z",
  "event_type": "routing_decision",
  "skill_id": "os.delegation_router",
  "user_tier": "paid",
  "routing_decision": {
    "engine": "sonnet",
    "confidence": 0.78,
    "reason": "cost_optimized",
    "token_savings_percent": 12.3
  },
  "audit_hash": "sha256:abc123...",
  "prev_hash": "sha256:xyz789..."
}
```

### Can I Opt Out?

**No, tier-based routing cannot be disabled.** Tier-based routing is a fundamental part of your service contract. If you select a specific license tier (Free, Paid, or Enterprise), you agree to the routing strategy that tier includes.

**However, you have these options:**

1. **Upgrade/Downgrade Tiers:** Switch to a different license tier (changes your routing strategy immediately)
2. **Request Custom Routing:** Enterprise customers can negotiate custom routing strategies
3. **Cancel Subscription:** If you don't accept tier-based routing, you can cancel at any time

There is no "routing optimization off" flag. Routing is tier-specific and transparent.

### Is This Fair?

**Yes. Different service tiers have always provided different service levels**, including:
- Latency (Free: ~3s, Paid: ~2.5s)
- Token allocation (Free: 10K/day, Paid: 100K/day)
- Model selection (Free: Haiku, Paid: Sonnet/Opus)
- Routing optimization (Free: conservative, Paid: optimized)

This is standard in the software industry. Companies like AWS, Google Cloud, and Anthropic all provide tier-specific service levels.

### Compliance & Auditing

**GDPR Art. 13/14 (Transparency at Collection):**
- Routing decisions are disclosed in this notice
- Audit trail is user-accessible
- No PII is used in routing decisions

**EU AI Act Art. 50 (Bot Disclosure):**
- Every routing decision is attributed to the AI system (`os.delegation_router`)
- The decision is logged with timestamp and confidence score
- You can inspect the complete decision process

**GDPR Art. 32 (Data Integrity):**
- All routing events are signed and hash-chained
- Tampering is detectable (hash verification fails)
- Audit trail is immutable (append-only)

---

## Rights Under GDPR & EU AI Act

### Your Rights

You have the following rights under GDPR and EU AI Act:

1. **Right to Access (Art. 15):** Inspect all routing decisions made about your account via `/v1/console/audit/routing-decisions`

2. **Right to Explanation (Art. 50):** Understand why you were routed to a specific engine (logged with confidence and reason)

3. **Right to Object (Art. 21):** Appeal a routing decision or upgrade tier if you disagree

4. **Right to Erasure (Art. 17):** Request deletion of your audit trail (except records required by law)

5. **Right to Data Portability (Art. 20):** Export your audit trail in machine-readable format

### How to Exercise Your Rights

- **Access routing audit trail:** `/v1/console/audit/routing-decisions?since=2026-09-01`
- **Export as JSON:** `/v1/console/audit/routing-decisions/export?format=json`
- **Appeal a decision:** Contact support@corvin.ai with task ID and routing decision
- **Upgrade/downgrade tier:** Account settings → License Tier
- **Request erasure:** Data Subject Request form (see contact below)

### Questions?

For questions about tier-based routing and privacy:

- **Email:** privacy@corvin.ai
- **Console:** Help → Privacy & Routing
- **Chat:** Ask `/privacy-tier-routing`

---

## Change Log

| Date | Change | Justification |
|------|--------|---------------|
| 2026-09-11 | Initial disclosure | GDPR Art. 13/14 + EU AI Act Art. 50 compliance |

