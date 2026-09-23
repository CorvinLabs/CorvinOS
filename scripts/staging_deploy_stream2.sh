#!/bin/bash
# Stream 2 Staging Deployment Script
# Deploys Security Orchestrator to staging for Week 2–3 soak test
# Phase 10, Stream 2: Security Orchestrator Skill (ADR-2047)
# Timeline: 2026-09-27 (Day 1, after Phase 10 kickoff)

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_REGISTRY="${DOCKER_REGISTRY:-corvin}"
NAMESPACE="${NAMESPACE:-staging}"
TIMEOUT="${TIMEOUT:-10m}"
DEPLOYMENT_DATE=$(date +"%Y-%m-%d %H:%M:%S UTC")

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Stream 2 Staging Deployment${NC}"
echo "========================================"
echo "Project: CorvinOS Phase 10"
echo "Stream: 2 - Security Orchestrator"
echo "Timeline: Week 2–3 Staging Soak Test"
echo "Deployment Date: $DEPLOYMENT_DATE"
echo "Repository: $REPO_ROOT"
echo "Namespace: $NAMESPACE"
echo "Registry: $DOCKER_REGISTRY"
echo ""

# Step 1: Pre-deployment checks
echo -e "${BLUE}[1/8] Running pre-deployment checks...${NC}"
cd "$REPO_ROOT"

# Verify Stream 2 exists and is production-ready
if [[ ! -d "core/skills/os_skills/security_orchestrator" ]]; then
    echo -e "${RED}❌ Stream 2 (Security Orchestrator) not found${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Stream 2 found: core/skills/os_skills/security_orchestrator${NC}"

# Verify tests exist
if [[ ! -d "core/skills/os_skills/security_orchestrator/tests" ]]; then
    echo -e "${RED}❌ Stream 2 tests directory not found${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Stream 2 tests directory verified${NC}"

# Verify routes exist
if [[ ! -d "core/skills/os_skills/security_orchestrator/routes" ]]; then
    echo -e "${RED}❌ Stream 2 routes directory not found${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Stream 2 routes directory verified${NC}"

# Check Python environment
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 not found${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Python 3 available: $(python3 --version)${NC}"

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}⚠️  Docker not found (continuing with local deployment)${NC}"
else
    echo -e "${GREEN}✅ Docker available: $(docker --version)${NC}"
fi

echo ""

# Step 2: Run Stream 2 tests locally (validation)
echo -e "${BLUE}[2/8] Running Stream 2 test suite for validation...${NC}"
python3 -m pytest core/skills/os_skills/security_orchestrator/tests/ -v --tb=short 2>&1 | tee /tmp/stream2_test_run.log || {
    echo -e "${RED}❌ Stream 2 tests failed${NC}"
    echo "See /tmp/stream2_test_run.log for details"
    exit 1
}
TEST_COUNT=$(grep -c "PASSED" /tmp/stream2_test_run.log || echo "0")
echo -e "${GREEN}✅ Stream 2 test suite passed (${TEST_COUNT} tests)${NC}"
echo ""

# Step 3: Build Docker image
echo -e "${BLUE}[3/8] Building Docker image...${NC}"
DOCKER_TAG="stream2-staging-$(date +%s)"
if command -v docker &> /dev/null; then
    docker build \
        --tag "$DOCKER_REGISTRY/stream2-security-orchestrator:$DOCKER_TAG" \
        --tag "$DOCKER_REGISTRY/stream2-security-orchestrator:staging-latest" \
        --build-arg BUILD_DATE="$DEPLOYMENT_DATE" \
        --build-arg VCS_REF="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')" \
        --file Dockerfile \
        . || {
        echo -e "${RED}❌ Docker build failed${NC}"
        exit 1
    }
    echo -e "${GREEN}✅ Docker image built: $DOCKER_REGISTRY/stream2-security-orchestrator:$DOCKER_TAG${NC}"
else
    echo -e "${YELLOW}⚠️  Skipping Docker build (Docker not available)${NC}"
    DOCKER_TAG="local"
fi
echo ""

# Step 4: Set up monitoring infrastructure
echo -e "${BLUE}[4/8] Setting up monitoring infrastructure...${NC}"

# Create monitoring directory
MONITORING_DIR="/tmp/stream2_monitoring_$(date +%s)"
mkdir -p "$MONITORING_DIR"

# Generate Prometheus configuration
cat > "$MONITORING_DIR/prometheus.yml" << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    stream: 'stream2-security-orchestrator'
    environment: 'staging'

scrape_configs:
  - job_name: 'stream2-security-orchestrator'
    static_configs:
      - targets: ['localhost:8765']
        labels:
          instance: 'stream2-staging'

  - job_name: 'stream2-threats'
    static_configs:
      - targets: ['localhost:8766']
        labels:
          instance: 'stream2-threats'

  - job_name: 'stream2-audit'
    static_configs:
      - targets: ['localhost:8767']
        labels:
          instance: 'stream2-audit'
EOF
echo -e "${GREEN}✅ Prometheus configuration generated${NC}"

# Create Slack webhook configuration (if available)
if [[ -n "$SLACK_WEBHOOK_URL" ]]; then
    cat > "$MONITORING_DIR/slack_config.json" << EOF
{
  "webhook_url": "$SLACK_WEBHOOK_URL",
  "channel": "#stream2-alerts",
  "username": "Stream 2 Security Orchestrator",
  "alert_rules": {
    "threat_detected": "🚨 Threat detected: {threat_type}",
    "false_positive": "⚠️ False positive: {reason}",
    "latency_high": "🐌 High latency: {latency_ms}ms",
    "error_occurred": "❌ Error: {error_message}"
  }
}
EOF
    echo -e "${GREEN}✅ Slack configuration generated${NC}"
else
    echo -e "${YELLOW}⚠️  Slack webhook not configured (alerts disabled)${NC}"
fi

echo -e "${GREEN}✅ Monitoring infrastructure ready: $MONITORING_DIR${NC}"
echo ""

# Step 5: Create staging deployment manifest
echo -e "${BLUE}[5/8] Creating staging deployment manifest...${NC}"

DEPLOYMENT_MANIFEST="$MONITORING_DIR/stream2_staging_deployment.yaml"
cat > "$DEPLOYMENT_MANIFEST" << EOF
# Stream 2 Staging Deployment Manifest
# Generated: $DEPLOYMENT_DATE
# ADR-2047: Security Orchestrator Skill

apiVersion: v1
kind: Namespace
metadata:
  name: staging
  labels:
    stream: stream2
    environment: staging

---
apiVersion: v1
kind: ConfigMap
metadata:
  name: stream2-config
  namespace: staging
data:
  environment: staging
  log_level: INFO
  threat_detection_enabled: "true"
  audit_trail_enabled: "true"
  max_threats_in_memory: "10000"

---
apiVersion: v1
kind: Service
metadata:
  name: stream2-security-orchestrator
  namespace: staging
  labels:
    app: stream2-security-orchestrator
    stream: stream2
spec:
  type: ClusterIP
  ports:
    - name: http
      port: 8765
      targetPort: 8765
      protocol: TCP
    - name: threats
      port: 8766
      targetPort: 8766
      protocol: TCP
    - name: audit
      port: 8767
      targetPort: 8767
      protocol: TCP
  selector:
    app: stream2-security-orchestrator

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: stream2-security-orchestrator
  namespace: staging
  labels:
    app: stream2-security-orchestrator
    stream: stream2
spec:
  replicas: 1
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels:
      app: stream2-security-orchestrator
  template:
    metadata:
      labels:
        app: stream2-security-orchestrator
        stream: stream2
      annotations:
        deployment_date: "$DEPLOYMENT_DATE"
    spec:
      containers:
      - name: stream2-security-orchestrator
        image: $DOCKER_REGISTRY/stream2-security-orchestrator:$DOCKER_TAG
        imagePullPolicy: IfNotPresent
        ports:
        - name: http
          containerPort: 8765
          protocol: TCP
        - name: threats
          containerPort: 8766
          protocol: TCP
        - name: audit
          containerPort: 8767
          protocol: TCP
        env:
        - name: ENVIRONMENT
          value: staging
        - name: LOG_LEVEL
          value: INFO
        - name: CORVIN_TENANT_ID
          value: _default
        resources:
          requests:
            cpu: 500m
            memory: 512Mi
          limits:
            cpu: 2000m
            memory: 2Gi
        livenessProbe:
          httpGet:
            path: /health
            port: 8765
          initialDelaySeconds: 10
          periodSeconds: 30
          timeoutSeconds: 5
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /health
            port: 8765
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 2
        volumeMounts:
        - name: config
          mountPath: /etc/stream2
        - name: audit-trail
          mountPath: /var/log/stream2/audit
      volumes:
      - name: config
        configMap:
          name: stream2-config
      - name: audit-trail
        emptyDir: {}
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
EOF

echo -e "${GREEN}✅ Deployment manifest created: $DEPLOYMENT_MANIFEST${NC}"
echo ""

# Step 6: Prepare staging environment
echo -e "${BLUE}[6/8] Preparing staging environment...${NC}"

# Create staging data directory
STAGING_DATA_DIR="$REPO_ROOT/.staging/stream2"
mkdir -p "$STAGING_DATA_DIR/audit"
mkdir -p "$STAGING_DATA_DIR/threats"
mkdir -p "$STAGING_DATA_DIR/policies"
mkdir -p "$STAGING_DATA_DIR/metrics"

echo -e "${GREEN}✅ Staging directories created:${NC}"
echo "   - Audit trail: $STAGING_DATA_DIR/audit"
echo "   - Threats: $STAGING_DATA_DIR/threats"
echo "   - Policies: $STAGING_DATA_DIR/policies"
echo "   - Metrics: $STAGING_DATA_DIR/metrics"
echo ""

# Step 7: Create smoke test configuration
echo -e "${BLUE}[7/8] Creating smoke test configuration...${NC}"

SMOKE_TEST_CONFIG="$MONITORING_DIR/smoke_test_config.json"
cat > "$SMOKE_TEST_CONFIG" << 'EOF'
{
  "test_suite": "stream2_staging_smoke_tests",
  "timeout_seconds": 60,
  "retry_count": 3,
  "tests": [
    {
      "name": "health_check",
      "endpoint": "GET /security/health",
      "expected_status": 200,
      "timeout_seconds": 5
    },
    {
      "name": "threats_endpoint",
      "endpoint": "GET /v1/console/security/threats",
      "expected_status": 200,
      "timeout_seconds": 10
    },
    {
      "name": "policy_endpoint",
      "endpoint": "GET /v1/console/security/policy",
      "expected_status": 200,
      "timeout_seconds": 10
    },
    {
      "name": "audit_endpoint",
      "endpoint": "GET /v1/console/security/audit",
      "expected_status": 200,
      "timeout_seconds": 10
    },
    {
      "name": "metrics_endpoint",
      "endpoint": "GET /v1/console/security/metrics",
      "expected_status": 200,
      "timeout_seconds": 10
    },
    {
      "name": "websocket_stream",
      "endpoint": "WS /v1/console/security/stream",
      "expected_status": 101,
      "timeout_seconds": 5
    }
  ]
}
EOF

echo -e "${GREEN}✅ Smoke test configuration created${NC}"
echo ""

# Step 8: Summary and next steps
echo -e "${BLUE}[8/8] Deployment preparation complete${NC}"
echo ""
echo -e "${GREEN}✅ ALL STEPS COMPLETED${NC}"
echo ""
echo "📋 Deployment Summary:"
echo "====================="
echo "Docker Tag: $DOCKER_TAG"
echo "Namespace: $NAMESPACE"
echo "Monitoring Dir: $MONITORING_DIR"
echo "Staging Data: $STAGING_DATA_DIR"
echo ""
echo "📊 Metrics & Monitoring:"
echo "========================"
echo "Prometheus Config: $MONITORING_DIR/prometheus.yml"
echo "Slack Config: $MONITORING_DIR/slack_config.json"
echo "Grafana Dashboard: dashboards/grafana_stream2_soak_test.json"
echo ""
echo "🧪 Smoke Tests:"
echo "==============="
echo "Config: $SMOKE_TEST_CONFIG"
echo "Run: pytest tests/ops/test_staging_soak_smoke.py -v"
echo ""
echo "📝 Next Steps (2026-09-27):"
echo "==========================="
echo "1. kubectl apply -f $DEPLOYMENT_MANIFEST"
echo "2. Wait for rollout: kubectl rollout status -n $NAMESPACE deployment/stream2-security-orchestrator"
echo "3. Run smoke tests: bash scripts/staging_smoke_tests.sh"
echo "4. Start load generator: python3 scripts/staging_load_generator.py --duration=7d --target=staging"
echo "5. Monitor dashboard: http://localhost:3000/d/stream2-soak-test"
echo "6. Check audit trail: tail -f .staging/stream2/audit/audit.jsonl"
echo ""
echo "🚀 Ready for Week 2–3 Soak Test Execution!"
echo ""
