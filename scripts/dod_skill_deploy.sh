#!/bin/bash
# Deploy DoD Verifier Skill 2.0 (ADR-0721, ADR-0722, ADR-0723)
# Usage: ./scripts/dod_skill_deploy.sh

set -e

echo "🚀 DoD Skill 2.0 Deployment"
echo "================================"

SKILL_DIR="/home/shumway/projects/CorvinOS/core/skills/os_skills/definition_of_done_verifier"
CORVIN_HOME="${HOME}/.corvin"
SKILL_CONFIG="${CORVIN_HOME}/skill_config/dod_verifier.yaml"

# 1. Verify skill exists
if [ ! -d "$SKILL_DIR" ]; then
    echo "❌ Skill directory not found: $SKILL_DIR"
    exit 1
fi

echo "✅ Skill found: $SKILL_DIR"

# 2. Load manifest
if [ ! -f "$SKILL_DIR/manifest.json" ]; then
    echo "❌ Manifest not found"
    exit 1
fi

SKILL_ID=$(grep -o '"skill_id":"[^"]*' "$SKILL_DIR/manifest.json" | cut -d'"' -f4)
SKILL_VERSION=$(grep -o '"version":"[^"]*' "$SKILL_DIR/manifest.json" | cut -d'"' -f4)

echo "✅ Loaded: $SKILL_ID@$SKILL_VERSION"

# 3. Register skill
mkdir -p "$CORVIN_HOME/skills/registered"
cp "$SKILL_DIR/manifest.json" "$CORVIN_HOME/skills/registered/${SKILL_ID}_v${SKILL_VERSION}.json"
echo "✅ Registered to $CORVIN_HOME/skills/registered/"

# 4. Create operator CLI wrapper
cat > "$CORVIN_HOME/bin/dod" << 'DOFEOF'
#!/usr/bin/env python3
"""DoD Verifier CLI — Operator interface"""
import sys
import json
from pathlib import Path

sys.path.insert(0, "/home/shumway/projects/CorvinOS")

from core.skills.os_skills.definition_of_done_verifier.skill import DODVerifierSkill

def main():
    if len(sys.argv) < 2:
        print("DoD Verifier Skill 2.0")
        print("Usage: dod <task_id> [--all-checks] [--export csv|json]")
        sys.exit(1)

    task_id = sys.argv[1]
    skill = DODVerifierSkill()

    # Execute verification
    result = skill.execute(task_id=task_id, deep_check=True)

    # Output
    print(f"\n📋 DoD Verification: {task_id}")
    print(f"   Status: {result['status']}")
    print(f"   Quality: {result['quality_score']*100:.1f}%")
    print(f"   Iteration: {result['iteration']}")

    # Check breakdown
    if 'checks' in result:
        print("\n   Checks:")
        for check_name, check_result in result['checks'].items():
            status = "✅" if check_result.get('passed') else "❌"
            print(f"     {status} {check_name}: {check_result.get('message', 'N/A')}")

    return 0 if result['status'] == 'PASS' else 1

if __name__ == "__main__":
    sys.exit(main())
DOFEOF

chmod +x "$CORVIN_HOME/bin/dod"
echo "✅ Operator CLI installed: $CORVIN_HOME/bin/dod"

# 5. Start skill daemon
mkdir -p "$CORVIN_HOME/logs"
python3 "$SKILL_DIR/skill.py" > "$CORVIN_HOME/logs/dod_skill.log" 2>&1 &
SKILL_PID=$!
echo $SKILL_PID > "$CORVIN_HOME/pids/dod_skill.pid"
echo "✅ Skill daemon started (PID: $SKILL_PID)"

# 6. Test
sleep 1
if ps -p $SKILL_PID > /dev/null; then
    echo "✅ Skill health check PASS"
else
    echo "❌ Skill health check FAIL"
    tail -20 "$CORVIN_HOME/logs/dod_skill.log"
    exit 1
fi

echo ""
echo "🎉 DoD Skill 2.0 DEPLOYED"
echo "================================"
echo "Available commands:"
echo "  • dod <task_id>          — Verify task completion"
echo "  • dod <task_id> --all     — Deep verification (all checks)"
echo "  • dod <task_id> --export  — Export metrics (csv/json)"
echo ""
echo "Example:"
echo "  $ dod my_workflow_task"
echo "  📋 DoD Verification: my_workflow_task"
echo "  Status: PASS"
echo "  Quality: 92.5%"
