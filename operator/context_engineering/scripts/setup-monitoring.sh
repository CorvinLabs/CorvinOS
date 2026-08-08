#!/bin/bash
# Week 6 Measurement Monitoring Setup
# Creates Prometheus + Grafana configuration for ADR-0270–0273 tracking
#
# Usage: bash setup-monitoring.sh
# Creates: prometheus.yml, alerts.yml, grafana dashboard configs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONITORING_DIR="${SCRIPT_DIR}/monitoring"

echo "Creating monitoring configuration..."
mkdir -p "$MONITORING_DIR"

# ============================================================================
# 1. Prometheus Configuration
# ============================================================================

cat > "$MONITORING_DIR/prometheus.yml" << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    environment: 'production'
    project: 'adr-0274-measurement'

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['localhost:9093']

rule_files:
  - 'alerts.yml'

scrape_configs:
  - job_name: 'corvin-measurement'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 10s

  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']
    scrape_interval: 30s
EOF

echo "✓ prometheus.yml created"

# ============================================================================
# 2. Alerting Rules
# ============================================================================

cat > "$MONITORING_DIR/alerts.yml" << 'EOF'
groups:
  - name: adr-0274-measurement
    interval: 30s
    rules:
      # ADR-0270: Confidence Calibration
      - alert: HighPredictionError
        expr: |
          abs(confidence_pred - outcome_actual) > 0.10
        for: 5m
        labels:
          severity: warning
          track: uncertainty
        annotations:
          summary: "High prediction error detected"
          description: "Confidence predictions off by >10%"

      # ADR-0271: Learning Rate
      - alert: UnusualLearningDelta
        expr: |
          abs(score_after - score_before) > 0.05
        for: 5m
        labels:
          severity: warning
          track: feedback
        annotations:
          summary: "Unusual score delta"
          description: "Score update >±0.05 (expected ±0.03)"

      # ADR-0272: User Profile
      - alert: ProfileDivergence
        expr: |
          abs(profile_confidence_change) > 0.15 and profile_age_days > 14
        for: 10m
        labels:
          severity: medium
          track: preferences
        annotations:
          summary: "User profile divergence"
          description: "Profile confidence changed >15% in established profile"

      # ADR-0273: Budget Mismatch
      - alert: BudgetComplexityMismatch
        expr: |
          budget_match_score < 0.60
        for: 5m
        labels:
          severity: info
          track: budget
        annotations:
          summary: "Budget allocation mismatch"
          description: "Budget allocation doesn't match task complexity"

      # Infrastructure
      - alert: ChecksumValidationFailure
        expr: |
          rate(checksum_failures_total[1m]) > 0.001
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Queue checksum failures detected"
          description: "Rate > 0.1% indicates data corruption"

      - alert: LockContention
        expr: |
          aggregation_wait_seconds > 300
        for: 5m
        labels:
          severity: high
        annotations:
          summary: "High lock contention"
          description: "Aggregation blocked >5 minutes"

      - alert: ProfileSymlinkFailed
        expr: |
          symlink_update_failures_total > 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Profile symlink update failed"
          description: "Could not atomically update profile symlink"
EOF

echo "✓ alerts.yml created"

# ============================================================================
# 3. Grafana Dashboard Config (JSON)
# ============================================================================

cat > "$MONITORING_DIR/grafana-dashboard.json" << 'EOF'
{
  "dashboard": {
    "title": "ADR-0274 Week 6 Measurement",
    "tags": ["adr-0274", "measurement", "cel-phase4"],
    "timezone": "UTC",
    "panels": [
      {
        "id": 1,
        "title": "ADR-0270: Confidence Calibration",
        "type": "graph",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, confidence_pred)",
            "legendFormat": "95th percentile",
            "refId": "A"
          }
        ],
        "yaxes": [
          {"format": "percentunit", "min": 0, "max": 1}
        ]
      },
      {
        "id": 2,
        "title": "ADR-0271: Learning Rate Validation",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(score_deltas_total[1m])",
            "legendFormat": "Score updates/min",
            "refId": "A"
          }
        ]
      },
      {
        "id": 3,
        "title": "ADR-0272: User Profile Accuracy",
        "type": "stat",
        "targets": [
          {
            "expr": "profile_recall_score",
            "legendFormat": "Recall",
            "refId": "A"
          }
        ],
        "fieldConfig": {
          "defaults": {"unit": "percentunit"}
        }
      },
      {
        "id": 4,
        "title": "ADR-0273: Budget/Complexity Match",
        "type": "gauge",
        "targets": [
          {
            "expr": "avg(budget_match_score)",
            "legendFormat": "Average match",
            "refId": "A"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "percentunit",
            "min": 0,
            "max": 1,
            "thresholds": {
              "mode": "absolute",
              "steps": [
                {"color": "red", "value": 0},
                {"color": "yellow", "value": 0.6},
                {"color": "green", "value": 0.8}
              ]
            }
          }
        }
      },
      {
        "id": 5,
        "title": "Checksum Validation Health",
        "type": "stat",
        "targets": [
          {
            "expr": "rate(checksum_failures_total[5m])",
            "legendFormat": "Failure rate",
            "refId": "A"
          }
        ],
        "thresholds": {
          "mode": "absolute",
          "steps": [
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 0.001},
            {"color": "red", "value": 0.01}
          ]
        }
      },
      {
        "id": 6,
        "title": "Lock Contention (seconds)",
        "type": "graph",
        "targets": [
          {
            "expr": "histogram_quantile(0.99, aggregation_wait_seconds)",
            "legendFormat": "p99 wait",
            "refId": "A"
          }
        ],
        "yaxes": [
          {"format": "s", "label": "Wait time"}
        ]
      }
    ]
  }
}
EOF

echo "✓ grafana-dashboard.json created"

# ============================================================================
# 4. Data Collection Config
# ============================================================================

cat > "$MONITORING_DIR/data-collection.yaml" << 'EOF'
# Week 6 Measurement Data Collection Config
# Specifies which metrics to collect from measurement_hooks.py

tracks:
  uncertainty_quantification:
    enabled: true
    description: "ADR-0270: Confidence-score calibration"
    metrics:
      - prediction_accuracy
      - rare_context_uncertainty
      - negative_feedback_loops
    sample_size: "≥1000"
    target_accuracy: "±5%"

  outcome_feedback_loop:
    enabled: true
    description: "ADR-0271: Bayesian learning validation"
    metrics:
      - score_delta_per_feedback
      - decay_weighting_90d
      - score_oscillation_events
    sample_size: "≥500"
    target_validation: "±0.03 delta"

  user_preferences:
    enabled: true
    description: "ADR-0272: User profile accuracy"
    metrics:
      - profile_recall
      - profile_precision
      - user_clustering
    sample_size: "≥100 users"
    target_recall: "≥0.80"
    target_precision: "≥0.75"

  attention_budget:
    enabled: true
    description: "ADR-0273: Budget allocation vs. complexity"
    metrics:
      - budget_complexity_correlation
      - budget_overrun_events
      - nice_to_have_deferral_rate
    sample_size: "≥500 tasks"
    target_match: "≥0.80"

data_collection:
  schedule: "every 5 minutes"
  retention: "7 days"
  format: "JSONL"
  location: "~/.corvin/measurement/"
  backup_location: "~/.corvin/measurement/backups/"

alerts:
  on_metric_below_target: "immediate"
  on_track_divergence: "1 hour"
  on_data_corruption: "immediate"

dashboards:
  - name: "uncertainty-calibration"
    interval: "5m"
    panels: ["prediction accuracy", "rare contexts", "oscillation"]
  - name: "feedback-learning"
    interval: "5m"
    panels: ["score deltas", "decay weighting", "convergence"]
  - name: "user-profiles"
    interval: "10m"
    panels: ["recall/precision", "profile clusters", "drift"]
  - name: "budget-allocation"
    interval: "5m"
    panels: ["complexity match", "overruns", "deferral rate"]
EOF

echo "✓ data-collection.yaml created"

# ============================================================================
# 5. Daily Measurement Checklist
# ============================================================================

cat > "$MONITORING_DIR/daily-checklist.md" << 'EOF'
# Week 6 Daily Measurement Checklist

## Morning Stand-up (9am UTC)
- [ ] Aggregation completed overnight (check logs)
- [ ] All dashboard metrics updated
- [ ] No critical alerts firing
- [ ] Queue files present and healthy
- [ ] Checksum validation OK (no failures)

## Midday Spot-Check (12pm UTC)
- [ ] Sample 10 predictions and verify accuracy
- [ ] Review user profile classifications
- [ ] Check budget/complexity correlation
- [ ] Log observations to measurement journal

## EOD Review (5pm UTC)
- [ ] Aggregate metrics for the day
- [ ] Update day-specific dashboard
- [ ] Note any anomalies
- [ ] Prepare next-day focus areas

## Weekly Review (Friday EOD)
- [ ] Calculate all four track metrics
- [ ] Verify > 0.80 accuracy targets
- [ ] Identify any refinement areas
- [ ] Prepare Week 6 go/no-go summary

## Data Integrity Checks
- [ ] JSONL files not corrupted
- [ ] Checksums validating correctly
- [ ] No duplicate records
- [ ] Timestamps sequential
- [ ] User IDs consistent

## Troubleshooting
- If metric < target:
  1. Check sample size (need N > min)
  2. Verify data collection is enabled
  3. Review logs for errors
  4. Escalate if pattern persists

- If alerts firing:
  1. Don't ignore - investigate immediately
  2. Verify it's not a measurement artifact
  3. Escalate critical alerts (checksum, locks)
EOF

echo "✓ daily-checklist.md created"

# ============================================================================
# Summary
# ============================================================================

echo ""
echo "========== MONITORING SETUP COMPLETE =========="
echo "Created in: $MONITORING_DIR"
echo ""
echo "Files:"
echo "  ✓ prometheus.yml              - Prometheus configuration"
echo "  ✓ alerts.yml                  - Alert rules for all tracks"
echo "  ✓ grafana-dashboard.json      - Grafana dashboard config"
echo "  ✓ data-collection.yaml        - What to collect + targets"
echo "  ✓ daily-checklist.md          - Daily measurement tasks"
echo ""
echo "Next steps:"
echo "  1. Copy prometheus.yml to Prometheus config dir"
echo "  2. Import grafana-dashboard.json into Grafana"
echo "  3. Configure AlertManager (if using)"
echo "  4. Start daily stand-ups with daily-checklist.md"
echo ""
echo "Documentation:"
echo "  See: docs/implementation/WEEK6-MEASUREMENT-PHASE-PLAN.md"
echo ""
