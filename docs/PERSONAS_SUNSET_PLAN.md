# Old Personas Sunset Plan (Weeks 9–16)

## 3-Phase Deprecation

### **Phase A: Monitoring (Weeks 9–10)**
- Track fallback metrics: How many requests still use old personas?
- Target: <5% fallback (95%+ on new Skills)
- If fallback >20%: investigate routing issues, don't proceed

### **Phase B: Deprecation Notice (Weeks 11–13)**
- Mark old persona code `@deprecated` in source
- Update docs: "Old personas will be sunset 2026-09-30"
- Monitor compliance: are new deployments still using old?

### **Phase C: Deletion (Weeks 14–16)**
- Remove old persona code from `core/personas/`
- Delete from registry
- Archive code (git tag `personas_v1_final`)
- Final audit: zero references remaining

## Success Criteria
- Fallback rate stays <1% during Phase C
- No customer-facing outages
- All audit trail preserved (immutable)
- GDPR deletion path fully tested

---

## Operati Runbook: Context Filtering + Learning + Deletion

### **Troubleshooting: High Error Rate**
1. Check intent classifier accuracy (`/v1/console/metrics/intent_accuracy`)
2. If <80%: increase confidence_threshold (fallback to full context)
3. If >0.1%: page oncall, evaluate rollback

### **Troubleshooting: Learning Divergence**
1. Check convergence iterations (`/v1/console/learning/profile/<user>`)
2. If >1000: freeze profile (`curl POST /v1/admin/profile/freeze/<user>`)
3. Reset profile weights to zero, restart learning

### **Troubleshooting: Deletion Timeout**
1. Check `deletion_orchestrator` logs
2. If user data > 10 GB: increase timeout (5s → 10s)
3. Run `corvin audit verify-deletion <user_id>`

### **Emergency Rollback**
```bash
# Rollback to 100% old personas
curl -X POST /v1/config/reload \
  -d '{"traffic_split": {"old_personas": 100, "new_skills": 0}}'

# Verify
curl /v1/console/metrics/summary
# Expected: 0% traffic on new Skills
```

---

**Status:** Ready for production  
**Next:** Execute staging canary (Week 5)
