#!/bin/bash
# SPIKE 1 ACTIVATION SCRIPT
# Run this when blocker #2 answer is received (Sept 3 06:00 UTC)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║           SPIKE 1 ACTIVATION — Feature Flags → Skills API             ║"
echo "║              (Run when blocker #2 answer received)                    ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""

# Check if blocker answers are present
if [ ! -f "$REPO_ROOT/docs/SPIKE_1_BLOCKER_ANSWERS.md" ]; then
    echo "❌ BLOCKER ANSWERS NOT FOUND"
    echo "   Expected: docs/SPIKE_1_BLOCKER_ANSWERS.md"
    echo "   This file should contain ADR-0544 Amendment with answers to all 4 blockers"
    echo ""
    echo "   Waiting for Architecture Lead response to:"
    echo "   - SPIKE_1_ADR_0544_AMENDMENT_REQUEST.md"
    echo ""
    exit 1
fi

echo "✅ Found blocker answers"
echo ""

# Parse blocker #2 answer
BLOCKER_2=$(grep -i "^architecture_choice:" "$REPO_ROOT/docs/SPIKE_1_BLOCKER_ANSWERS.md" | cut -d'"' -f2)

if [ "$BLOCKER_2" == "big_bang" ]; then
    echo "🎯 ACTIVATION: BIG BANG (Option 2a)"
    echo "   All 88 call-sites will be refactored immediately"
    echo ""
    SCENARIO="big_bang"

elif [ "$BLOCKER_2" == "wrapper_phased" ]; then
    echo "🎯 ACTIVATION: WRAPPER+PHASED (Option 2b)"
    echo "   Wrapper delegates to Skill; call-sites refactored gradually in Phase 2"
    echo ""
    SCENARIO="wrapper_phased"

else
    echo "❌ BLOCKER #2 ANSWER UNCLEAR: $BLOCKER_2"
    echo "   Expected: 'big_bang' or 'wrapper_phased'"
    exit 1
fi

# Create branch for code execution
BRANCH_NAME="spike1/phase2-execution-$(date +%s)"
echo "📝 Creating execution branch: $BRANCH_NAME"
git checkout -b "$BRANCH_NAME"

echo "✅ Execution branch created"
echo ""

# Activate test templates
echo "🧪 Activating test templates..."
sed -i 's/@pytest.mark.skip(reason="Awaiting blocker/#ACTIVATED: pytest.mark.skip(reason="Awaiting blocker/g' \
    "$REPO_ROOT/tests/integration/test_feature_flags_equivalence_template.py"

# Add scenario-specific imports
if [ "$SCENARIO" == "big_bang" ]; then
    echo "  → Big Bang: Direct Skills API (no wrapper)"
    sed -i "s/# FeatureFlagsSkill = None/from core.skills.feature_flags_skill import FeatureFlagsSkill/g" \
        "$REPO_ROOT/tests/integration/test_feature_flags_equivalence_template.py"

elif [ "$SCENARIO" == "wrapper_phased" ]; then
    echo "  → Wrapper+Phased: Legacy adapter"
    sed -i "s/# FeatureFlagsSkill = None/from core.console.corvin_core.feature_flags_legacy_adapter import is_enabled, set_enabled/g" \
        "$REPO_ROOT/tests/integration/test_feature_flags_equivalence_template.py"
fi

echo "✅ Test templates activated"
echo ""

# Update velocity tracking
echo "📊 Updating velocity tracking..."
cat >> "$REPO_ROOT/docs/SPIKE_1_VELOCITY_TRACKING.md" << EOF

## BLOCKER ANSWERS RECEIVED
**Time:** $(date -u '+%Y-%m-%d %H:%M UTC')

### Architecture Choice (Blocker #2)
**Answer:** $BLOCKER_2

### Remaining Blocker Answers
EOF

# Parse all 4 blocker answers
grep -E "^(flag_to_skill_mapping|worker_engine_mode_handling|tier_management):" \
    "$REPO_ROOT/docs/SPIKE_1_BLOCKER_ANSWERS.md" >> "$REPO_ROOT/docs/SPIKE_1_VELOCITY_TRACKING.md" || true

echo "✅ Velocity tracking updated"
echo ""

# Create checkpoint commit
echo "💾 Creating checkpoint commit..."
git add -A
git commit -m "spike1: ACTIVATION (blocker answers received, scenario=$SCENARIO)

Blocker #2 answer: architecture_choice=$BLOCKER_2

Activations:
  - Test templates un-skipped and configured for $SCENARIO
  - Velocity tracking updated with blocker answers
  - Execution branch created: $BRANCH_NAME
  - Ready for Phase 2 code execution (Sept 3 10:00 UTC)

Next: Begin Spike 1 rewrite (7 tasks, ≤10h, real-time tracking)

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"

echo "✅ Checkpoint commit created"
echo ""

# Display next actions
echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║                        ACTIVATION COMPLETE                             ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. VERIFY blocker answers in docs/SPIKE_1_BLOCKER_ANSWERS.md"
echo ""
echo "2. START Phase 2 execution (Sept 3 10:00 UTC):"
echo "   → Task 1: Skill manifest creation"
echo "   → Task 2: Skills Registry $SCENARIO path"
echo "   → Task 3: JSON storage layer"
echo "   → Task 4: Audit event injection"
echo "   → Task 5: Tenant isolation validation"
echo "   → Task 6: Testing suite"
echo "   → Task 7: Documentation & rollout plan"
echo ""
echo "3. TRACK progress in docs/SPIKE_1_VELOCITY_TRACKING.md"
echo "   → Update every 30–60 minutes"
echo "   → Log actual hours + task completion"
echo ""
echo "4. VERIFY tests pass:"
echo "   pytest tests/integration/test_feature_flags_equivalence_template.py -v"
echo ""
echo "5. SUBMIT Final Report by Sept 4 EOD:"
echo "   docs/SPIKE_1_FINAL_REPORT_SEPT_4.md"
echo ""
echo "Execution branch: $BRANCH_NAME"
echo "Scenario: $SCENARIO"
echo ""
echo "Good luck! 🚀"
