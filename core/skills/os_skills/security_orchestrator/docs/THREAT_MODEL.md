# THREAT MODEL — Security Orchestrator Skill (ADR-2031)

**Deliverable:** Week 3 (Oct 10–16)  
**Scope:** 20+ attack scenarios covering threat detection + policy response

---

## Attack Scenario Matrix (24 Total)

| # | Attack Name | Threat Type | Detection Rule | Policy Response | Expected Outcome | Status |
|---|---|---|---|---|---|---|
| **1** | Brute Force: 5 Failed Auth Attempts | Brute Force | >5 attempts in 5min | Reduce auth_max_failures: 3→1 | Attack blocked after 1 attempt | Week 2 |
| **2** | Brute Force: 15 Failed Attempts (Escalation) | Brute Force | >10 attempts in 5min | auth_max_failures: 3→1 | Confidence: 0.92 (critical) | Week 2 |
| **3** | Brute Force: Rapid-Fire (100/sec) | Brute Force | >5 in 30sec | Rate limit reduced + auth gate tightened | Both gates tightened | Week 3 |
| **4** | Brute Force: Distributed IPs (Botnets) | Distributed Attack | >20 unique IPs targeting auth endpoint | Rate limit per IP reduces | Bot IPs blocked after limit reached | Week 3 |
| **5** | Privilege Escalation: Single User Spam | Priv Escalation | >3 unauthorized overrides in 5min | Overrides disabled for user | User cannot override until TTL | Week 2 |
| **6** | Privilege Escalation: Admin Account Compromise | Priv Escalation | >10 unauthorized overrides, admin account | Overrides: 10→0 + audit escalation | Incident response triggered; ops notified | Week 3 |
| **7** | Data Exfiltration: PII to External Engine | Data Exfiltration | >1 high-risk PII flow to non-approved destination | data_high_risk_flow_limit: 100→50 | Subsequent PII flows limited/blocked | Week 2 |
| **8** | Data Exfiltration: Bulk Export Attempt | Data Exfiltration | >50 high-risk flows in 10min | Flow limit: 100→10 | Export job killed; operator alerted | Week 3 |
| **9** | Data Exfiltration: Slow Leak (1 flow/min) | Anomalous Behavior | Baseline deviation: 10x usual flow volume | Context-dependent tightening | Audit logged; operator decides | Week 4 |
| **10** | Distributed Attack: HTTP Flood | Distributed Attack | >1000 req/sec from >50 unique IPs | rate_limit_requests_per_minute: 1000→300 | Traffic shaped; legitimate users throttled | Week 3 |
| **11** | Distributed Attack: Targeted Endpoint (DDoS) | Distributed Attack | >100 req/sec from >10 IPs to /login | Rate limit by endpoint | /login responds 429, other endpoints normal | Week 4 |
| **12** | Insider Threat: Data Access Pattern Change | Anomalous Behavior | User access >100x historical average | Audit logged + alert | Manual operator review required | Week 4 |
| **13** | Session Hijacking: Unusual Login Location | Anomalous Behavior | User login from new country (3x/day) | Consent gate re-check | MFA challenge triggered | Week 4 |
| **14** | Privilege Escalation: Token Forging Attempt | Priv Escalation | >5 override attempts with malformed tokens | Overrides disabled + audit escalation | Token validation enforced; attempts logged | Week 4 |
| **15** | API Key Compromise: Excessive API Calls | Distributed Attack | API key rate limit exceeded 5x in 10min | Rate limit: 1000→100 req/min for key | Requests 429; operator investigates key | Week 3 |
| **16** | Cache Poisoning: Malicious Data Injection | Data Exfiltration | Attempt to write >N items to cache from untrusted source | Flow policy tightened; cache access locked | Injection blocked; cache integrity verified | Week 5 |
| **17** | CSRF Attack: Unauthorized State Change | Priv Escalation | >10 state-change attempts without CSRF token | Override gate tightened | Requests rejected + logged | Week 4 |
| **18** | Timing Attack: Brute Force Phone Verification | Brute Force | >5 failed phone code verification in 5min | Override disabled for user; MFA reset required | User locked until manual operator review | Week 4 |
| **19** | Replay Attack: Old Auth Token Reused | Brute Force | Same token used 3x from different IP within 1min | Auth gate tightened + token revoked | Token invalidated; session terminated | Week 4 |
| **20** | Denial of Service: Policy Tightening Spiral | (Meta) | Policy tightening creates legitimate user lockout | TTL prevents permanent lockdown | After 1h, policy reverts to baseline | Week 3 |
| **21** | False Positive: Legitimate Batch Job | (Tuning) | Batch job with >100 requests/sec (normal) | Should NOT trigger distributed attack | No policy tightening; confidence <0.75 | Week 5 |
| **22** | False Positive: CDN Edge Surge | (Tuning) | CDN health checks + cache refresh (>1K req/sec) | Should NOT trigger rate limit | Monitoring tools whitelisted; no response | Week 5 |
| **23** | False Positive: Maintenance Window | (Tuning) | Scheduled maintenance creates high audit volume | Should NOT trigger anomaly detection | Maintenance window flag prevents false pos | Week 5 |
| **24** | Cross-Tenant Attack: Tenant A Exploits Tenant B | (Compliance) | Tenant A's threat should NOT affect Tenant B's policy | Policy tightening isolated to Tenant A | Tenant B unaffected; audit shows isolation | Week 3 |

---

## Attack Signatures (Implementation Guide)

### Brute Force (Scenarios 1–4)

**Signature:** >N failed authentication attempts in T minutes from same user.

```python
# ThreatDetector.analyze_auth_events()
failed_by_user = {}
for event in auth_events:
    if not event.get("success"):
        user = event["user_id"]
        failed_by_user[user] = failed_by_user.get(user, 0) + 1

if max(failed_by_user.values()) >= threshold:
    confidence = min(1.0, max_fails / (2 * threshold))
    return ThreatSignal(
        pattern=ThreatPattern.BRUTE_FORCE,
        confidence=confidence,
        affected_users=[user for user, count in failed_by_user.items() if count > threshold]
    )
```

**Policy Response:** Reduce `auth_max_failures` from default (3) → 1  
**TTL:** 3,600 seconds (1 hour)  
**Revert:** When threat clears OR TTL expires

**Test Cases:**
1. Threshold tuning: N=5, test with 4 failures (no trigger), 5 failures (trigger), 10 failures (high confidence)
2. Window sliding: Test with failures spread across 10 minutes (outside window = old), 5 minutes (inside)
3. Multi-user: 5 failures user A, 3 failures user B → only A tightened
4. Distributed: Same failure count, 10 different IPs → distributed attack pattern (separate)

### Privilege Escalation (Scenarios 5–6, 14, 17–19)

**Signature:** >N unauthorized override attempts in T minutes from same user.

```python
# ThreatDetector.analyze_privilege_escalation_events()
unauthorized_by_user = {}
for event in override_events:
    if not event.get("authorized"):  # Unauthorized attempt
        user = event["user_id"]
        unauthorized_by_user[user] = unauthorized_by_user.get(user, 0) + 1

if max(unauthorized_by_user.values()) >= threshold:
    confidence = min(1.0, max_attempts / (2 * threshold))
    return ThreatSignal(
        pattern=ThreatPattern.PRIVILEGE_ESCALATION,
        confidence=confidence,
        affected_users=[user for user, count in unauthorized_by_user.items() if count > threshold]
    )
```

**Policy Response:** Set `override_allowed_per_user` → 0 (disable overrides)  
**TTL:** 3,600 seconds  
**Revert:** Only by TTL; manual override requires operator approval + audit

**Test Cases:**
1. Threshold: N=3, test 2 (no), 3 (trigger), 10 (critical)
2. Token validation: Forged token rejection logged (counts toward threshold)
3. CSRF protection: CSRF token missing = unauthorized (counts)
4. MFA bypass: Attempt to bypass MFA during override (counts)
5. Cross-user isolation: Admin A's attempts don't affect User B

### Data Exfiltration (Scenarios 7–9, 16)

**Signature:** >N high-risk data flows to external/untrusted destination in T minutes.

```python
# ThreatDetector.analyze_data_exfiltration_events()
high_risk_flows = 0
pii_flows = 0
for event in data_flow_events:
    if event.get("risk_level") == "high":
        high_risk_flows += 1
    if event.get("data_class") == "PII":
        pii_flows += 1

# Any PII flow to external = immediate threat
if pii_flows > 0 and "external" in event.get("destination", ""):
    return ThreatSignal(
        pattern=ThreatPattern.DATA_EXFILTRATION,
        confidence=0.95,  # High confidence
        severity="critical"
    )

# Or high volume of high-risk flows
if high_risk_flows >= threshold:
    confidence = min(1.0, high_risk_flows / (2 * threshold))
    return ThreatSignal(
        pattern=ThreatPattern.DATA_EXFILTRATION,
        confidence=confidence
    )
```

**Policy Response:** Reduce `data_high_risk_flow_limit` from default (100) → 50 (further restricts flows)  
**TTL:** 3,600 seconds  
**Revert:** When threat clears OR TTL expires

**Test Cases:**
1. PII detection: Any PII to external endpoint = high confidence + critical severity
2. Volume threshold: N=10, test with 9 (no), 10 (trigger), 50 (critical)
3. Endpoint filtering: Flows to approved endpoints (company S3, databases) not flagged
4. Timing window: Flows spread over 1 hour (outside window = old), 5 minutes (inside)
5. Data classification: High-risk vs. low-risk flow segregation

### Distributed Attack (Scenarios 3–4, 10–11, 15, 20, 24)

**Signature:** >N unique source IPs targeting same resource in T minutes.

```python
# ThreatDetector.analyze_distributed_attack()
ips_by_target = {}
for event in request_events:
    target = event["target"]  # /login, /api/data, etc.
    ip = event["ip"]
    if target not in ips_by_target:
        ips_by_target[target] = set()
    ips_by_target[target].add(ip)

max_ips = max(len(ips) for ips in ips_by_target.values())
if max_ips >= threshold:
    confidence = min(1.0, max_ips / (2 * threshold))
    return ThreatSignal(
        pattern=ThreatPattern.DISTRIBUTED_ATTACK,
        confidence=confidence,
        affected_ips=sorted(ips_by_target[worst_target])
    )
```

**Policy Response:** Reduce `rate_limit_requests_per_minute` from default (1000) → 300  
**Per-IP Rate Limit:** Further restrict offending IPs  
**TTL:** 3,600 seconds  
**Revert:** When threat clears OR TTL expires

**Test Cases:**
1. IP diversity: 5 IPs (low confidence), 20 IPs (high confidence), 100 IPs (critical)
2. Endpoint specificity: Attack on /login, but /health checks untouched
3. Whitelist bypass: Known bot IPs on whitelist should not trigger
4. Legitimate traffic: CDN with 50 IPs for cache refresh should NOT trigger (false positive tuning)
5. Rate limiting: Requests from attacking IPs rejected with 429; others go through

### Anomalous Behavior (Scenarios 12–13, 9, 20–23)

**Signature:** User/system behavior deviation >X standard deviations from baseline.

```python
# ThreatDetector.analyze_anomalous_behavior()
# Requires historical baseline per user (mean + stddev of various metrics)
# Metrics: request_rate, data_flow_volume, endpoint_access_pattern, time_of_day_deviation

baseline = get_user_baseline(user_id)  # mean, stddev per metric
current_metrics = compute_current_metrics(user_id, events)

z_score = (current_metrics - baseline.mean) / baseline.stddev
if any(z_score > 3.0):  # >3 sigma deviation
    return ThreatSignal(
        pattern=ThreatPattern.ANOMALOUS_BEHAVIOR,
        confidence=1.0 - (1.0 / (1.0 + abs(max(z_score)))),  # Sigmoid confidence
        severity="medium"  # Requires human review
    )
```

**Policy Response:** Audit logged + alert (not auto-tighten; operator decides)  
**TTL:** N/A (anomaly requires manual review)  
**False Positive Risk:** High (e.g., user on vacation, new timezone, legitimate behavior change)

**Test Cases:**
1. User traveling: New country, new time zone, new IP → should NOT trigger (false positive)
2. Legitimate behavior change: User switches teams, new access pattern → should NOT trigger
3. Insider threat: Sudden 10x data access + export → should trigger + alert operator
4. Session hijacking: Login from 3 countries in 1 hour → should trigger (MFA challenge)
5. Baseline computation: Ensure 30-day history before anomaly detection active

---

## False Positive Tuning Strategy (Week 5)

### Target: <5% False Positive Rate

**Baseline Workload Test:**
- Run 24 hours of production-like traffic (via load generator)
- Capture all audit events
- Count true positives (actual attacks) vs. false positives (legitimate activity flagged)
- Tune thresholds iteratively

**Whitelisting Strategy:**
- CDN IP ranges: Always exempt from distributed attack detection
- Monitoring tools: Exclude from rate limit detection
- Batch jobs: Tag with "scheduled" flag; exclude from anomaly detection
- Maintenance windows: Tag with "maintenance" flag; suppress alerts

**Confidence-Gating:**
- Only auto-tighten if confidence >= 0.75
- Confidence 0.50–0.75: Audit logged + alert (no auto-action)
- Confidence <0.50: Silently logged (no alert)

---

## Compliance Checklist (Hardening Phase, Weeks 4–6)

- [ ] No PII in audit events (names, emails, API keys always scrubbed)
- [ ] Tenant isolation verified (Tenant A's threat doesn't affect Tenant B)
- [ ] TTL-based revert verified (no permanent lockout >1 hour)
- [ ] House-rules (L44) never bypassed (consent gates, audit chain intact)
- [ ] Cross-threat isolation (brute force detection doesn't affect data exfil policy)
- [ ] Operator override audited (manual tightening/loosening logged + attributed)

---

## Testing Execution Schedule

| Week | Scenarios | Test Type | Verification |
|---|---|---|---|
| Week 2 | 1–5 | Unit + E2E | Threat detection accuracy + policy response |
| Week 3 | 6–11, 20, 24 | E2E + multi-tenant | Integration + isolation |
| Week 4 | 12–19 | E2E + adversarial | Edge cases + security constraints |
| Week 5 | 21–23 | False positive tuning | Baseline workload + whitelisting |

---

**Status:** 🟡 **PENDING** (Created Week 1, detailed in Weeks 2–5)  
**Owner:** Stream 2 Lead  
**Last Updated:** 2026-09-22

