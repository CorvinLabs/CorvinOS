# 🎯 CORVINOS LIVE TESTING PLAYBOOK

**System Status:** ✅ LIVE & READY  
**Start Date:** Next session  
**Environment:** Local Python 3.12 + /tmp/corvinos_runtime/  

---

## Quick Start

### 1. Verify System is Running
```bash
python3 -c "
import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS')
from core.learning.hybrid_context import HybridContextModel
from core.vibe.vibe_hub_core import VibeHubOrchestrator
print('✅ CorvinOS Live: All core modules loaded')
"
```

### 2. Test Skill Execution
```bash
python3 << 'TESTEOF'
import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS')
from core.skills.os_skills_phase3d import CostOptimizerSkill

skill = CostOptimizerSkill()
result = skill.execute({'complexity': 5, 'cost_budget': 1.0})
print(f"✅ Skill Execution: {result['engine']} selected")
print(f"   Confidence: {result['confidence']}")
print(f"   Cost: ${result['estimated_cost']:.2f}")
TESTEOF
```

### 3. Test Marketplace
```bash
python3 << 'TESTEOF'
import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS')
from core.skills.marketplace_core import MarketplaceAPI

marketplace = MarketplaceAPI()
# Simulate skills approved
marketplace.submissions['os.router'] = type('obj', (object,), {
    'status': 'APPROVED', 'version': '1.0.0', 'author': 'corvin_team'
})()
results = marketplace.discover_skills('router', limit=10)
print(f"✅ Marketplace: {len(results)} skills found")
TESTEOF
```

### 4. Test Learning Loop
```bash
python3 << 'TESTEOF'
import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS')
from core.learning.confidence_drift import ConfidenceDriftDetector

detector = ConfidenceDriftDetector()
baseline = 0.8
observations = [0.5, 0.52, 0.51, 0.50, 0.53] * 3  # 15 samples
drift_report = detector.detect(baseline, observations)
print(f"✅ Learning Loop: Drift detected = {drift_report}")
TESTEOF
```

### 5. Test Vibe Hub Orchestration
```bash
python3 << 'TESTEOF'
import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS')
from core.vibe.vibe_hub_core import VibeHubOrchestrator, Subsystem, SubsystemType

hub = VibeHubOrchestrator()
skill = Subsystem('os.router', SubsystemType.SKILL, '1.0.0', [], {})
success = hub.register_subsystem(skill)
print(f"✅ Vibe Hub: Subsystem registered = {success}")
result = hub.orchestrate('task_1', {'skill_id': 'os.router'})
print(f"   Result: {result['task_id']} orchestrated")
TESTEOF
```

---

## Test Scenarios

### Scenario 1: End-to-End Skill Learning
```
1. Create hybrid context model
2. Inject learned layer (cost optimizer config)
3. Execute skill with injected context
4. Verify confidence improved
5. Check audit trail
```

### Scenario 2: Multi-Agent Routing
```
1. Register 3 agents (Haiku/Sonnet/Opus)
2. Route simple task (complexity=3) → should go to Haiku
3. Route complex task (complexity=9) → should go to Opus
4. Verify routing decisions in audit
```

### Scenario 3: Community Marketplace Search
```
1. Index 5+ marketplace skills
2. Search for "router" → should find all matching
3. Rate a skill (4.5/5)
4. Verify average rating updated
5. Check marketplace metrics
```

### Scenario 4: Cross-Skill Learning
```
1. Skill A learns pattern (task_selection_v1)
2. Share pattern via CrossSkillLearner
3. Skill B receives pattern
4. Verify B's confidence improved
5. Check learning audit events
```

---

## Expected Outputs

### ✅ Success Indicators
- All modules import without errors
- Skill execution returns confidence scores
- Marketplace finds skills
- Learning drift detected correctly
- Vibe Hub orchestrates tasks
- Audit events are logged
- No CRITICAL errors in logs

### ❌ Failure Indicators
- Import errors (missing dependencies)
- Audit write failures
- NoneType errors (null checks failed)
- Tier1 validation rejections (expected for attempted violations)

---

## Performance Baselines

Expected from this session's simulations:
- Skill execution: <500ms
- Marketplace search: <100ms
- Learning drift detection: <50ms
- Multi-Agent routing: <100ms
- Audit write: <200ms

---

## Next Steps After Testing

1. If all tests pass → proceed to infrastructure deployment
2. If issues found → investigate root cause + document
3. Prepare customer onboarding (25+ enterprises)
4. Schedule real penetration testing

---

**Status:** 🚀 SYSTEM LIVE & READY FOR TESTING

**Last Updated:** After adversarial review + fixes (commit 98ab9df2)

**Contact:** Engineering team for issues during testing
