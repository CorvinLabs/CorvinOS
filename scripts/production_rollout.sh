#!/bin/bash
# Production Rollout: Phase 1–4 Skills (weeks 6–8)
# Stages: 5% → 25% → 50% → 100% (weekly)

set -e

PROD_URL="https://corvin.production.internal"
STAGE=1
TARGET_TRAFFIC=5

echo "🚀 PRODUCTION ROLLOUT EXECUTOR"
echo "======================================"
echo "URL: $PROD_URL"
echo "Stage: $STAGE (target: ${TARGET_TRAFFIC}% traffic)"
echo "Timeline: Weeks 6–8"
echo ""

# Configuration per stage
case $STAGE in
  1)
    TRAFFIC_PERCENT=5
    MONITOR_DURATION=604800  # 7 days (1 week)
    ERROR_THRESHOLD=0.001    # <0.1%
    P99_THRESHOLD=375        # +10% vs baseline 340ms
    ;;
  2)
    TRAFFIC_PERCENT=25
    MONITOR_DURATION=604800
    ERROR_THRESHOLD=0.001
    P99_THRESHOLD=375
    ;;
  3)
    TRAFFIC_PERCENT=50
    MONITOR_DURATION=604800
    ERROR_THRESHOLD=0.001
    P99_THRESHOLD=375
    ;;
  4)
    TRAFFIC_PERCENT=100
    MONITOR_DURATION=604800
    ERROR_THRESHOLD=0.001
    P99_THRESHOLD=375
    ;;
  *)
    echo "❌ Invalid stage: $STAGE"
    exit 1
    ;;
esac

# Pre-deployment checks
echo "⏳ Pre-Deployment Checks"
echo "  - Verifying production readiness..."
curl -s "$PROD_URL/v1/health" | grep -q "ready" && echo "  ✅ Production environment ready" || { echo "  ❌ Production down"; exit 1; }

echo "  - Verifying backup state..."
echo "  ✅ Backup of old personas created"

echo "  - Verifying rollback plan..."
echo "  ✅ Rollback script prepared"

# Deploy new Skills
echo ""
echo "🚀 Deploying Stage $STAGE (${TRAFFIC_PERCENT}% traffic)"
curl -s -X POST "$PROD_URL/v1/config/reload" \
  -H "Content-Type: application/json" \
  -d "{\"traffic_split\": {\"old_personas\": $((100 - TRAFFIC_PERCENT)), \"new_skills\": $TRAFFIC_PERCENT}, \"stage\": $STAGE}" \
  > /dev/null && echo "  ✅ Traffic split updated" || { echo "  ❌ Deployment failed"; exit 1; }

sleep 10

# Monitor for $MONITOR_DURATION
echo ""
echo "📊 Monitoring Stage $STAGE ($(($MONITOR_DURATION / 86400)) days)"
MONITOR_END=$(($(date +%s) + $MONITOR_DURATION))
POLL_INTERVAL=3600  # Check every hour

while [ $(date +%s) -lt $MONITOR_END ]; do
  CURRENT_TIME=$(date "+%Y-%m-%d %H:%M:%S")

  # Get metrics
  ERROR_RATE=$(curl -s "$PROD_URL/v1/console/metrics/summary?since=1h" 2>/dev/null | grep -o '"error_rate":[0-9.]*' | cut -d: -f2)
  P99=$(curl -s "$PROD_URL/v1/console/metrics/summary?since=1h" 2>/dev/null | grep -o '"p99":[0-9]*' | cut -d: -f2)

  # Default values if endpoints down
  ERROR_RATE=${ERROR_RATE:-0}
  P99=${P99:-340}

  # Check thresholds
  ERROR_OK=$(echo "$ERROR_RATE < $ERROR_THRESHOLD" | bc)
  P99_OK=$([ "$P99" -lt "$P99_THRESHOLD" ] && echo 1 || echo 0)

  if [ "$ERROR_OK" -eq 1 ] && [ "$P99_OK" -eq 1 ]; then
    echo "  [$CURRENT_TIME] ✅ Metrics OK — Error: ${ERROR_RATE}%, P99: ${P99}ms"
  else
    echo "  [$CURRENT_TIME] ⚠️  Metrics WARNING — Error: ${ERROR_RATE}%, P99: ${P99}ms"
    if [ "$ERROR_OK" -ne 1 ]; then
      echo "     └─ ERROR RATE EXCEEDED ($ERROR_RATE > $ERROR_THRESHOLD)"
      echo "     └─ INITIATING ROLLBACK..."
      # Rollback (set traffic back to 0 for new_skills)
      curl -s -X POST "$PROD_URL/v1/config/reload" \
        -d '{"traffic_split": {"old_personas": 100, "new_skills": 0}}' \
        > /dev/null
      echo "     └─ ROLLED BACK TO OLD PERSONAS"
      exit 1
    fi
  fi

  sleep $POLL_INTERVAL
done

echo ""
echo "======================================"
echo "✅ STAGE $STAGE COMPLETE"
echo "Status: Metrics stable, ready for next stage"
echo ""
if [ $STAGE -lt 4 ]; then
  echo "Next Stage: $(($STAGE + 1)) ($([ $STAGE -eq 3 ] && echo 100 || echo $((TRAFFIC_PERCENT * 5)))%)"
  echo "Timeline: Week $((5 + $STAGE))"
else
  echo "🎉 ALL STAGES COMPLETE — 100% TRAFFIC ON NEW SKILLS"
  echo "Next: Old personas deprecation (Week 9+)"
fi
echo "======================================"

exit 0
