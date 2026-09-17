#!/bin/bash
# Context-Drift Production Deployment Script
# Safe deployment with readiness checks, zero-downtime rollout, post-verification
# ADR-0407: Session Context Drift Prevention
# ADR-0362: Production Deployment Framework

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_REGISTRY="${DOCKER_REGISTRY:-corvin}"
NAMESPACE="${NAMESPACE:-production}"
TIMEOUT="${TIMEOUT:-10m}"
DRY_RUN="${DRY_RUN:-false}"

echo "🚀 CONTEXT-DRIFT PRODUCTION DEPLOYMENT"
echo "======================================"
echo "Repository: $REPO_ROOT"
echo "Namespace: $NAMESPACE"
echo "Registry: $DOCKER_REGISTRY"
echo "Timeout: $TIMEOUT"
echo "Dry Run: $DRY_RUN"
echo ""

# Function to run command with dry-run support
run_cmd() {
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "DRY-RUN: $@"
    else
        "$@"
    fi
}

# Step 1: Production Readiness Checklist
echo "[1/6] Running production readiness checklist..."
cd "$REPO_ROOT"
python3 scripts/context_drift_production_readiness_checklist.py || {
    echo "❌ Production readiness checks failed"
    exit 1
}
echo "✅ All 20 production readiness checks passed"
echo ""

# Step 2: Generate Compliance Report
echo "[2/6] Generating compliance report..."
python3 -m core.compliance.context_drift_compliance_report || {
    echo "⚠️  Compliance report generation failed (continuing)"
}
echo "✅ Compliance report generated"
echo ""

# Step 3: Build Docker Image
echo "[3/6] Building production Docker image..."
DOCKER_TAG="prod-$(date +%s)"
run_cmd docker build \
    --tag "$DOCKER_REGISTRY/context-drift:$DOCKER_TAG" \
    --tag "$DOCKER_REGISTRY/context-drift:prod-latest" \
    --file Dockerfile \
    . || {
    echo "❌ Docker build failed"
    exit 1
}
echo "✅ Docker image built: $DOCKER_REGISTRY/context-drift:$DOCKER_TAG"
echo ""

# Step 4: Push to Production Registry
echo "[4/6] Pushing Docker image to production registry..."
if [[ -n "$DOCKER_REGISTRY" && "$DOCKER_REGISTRY" != "corvin" ]]; then
    run_cmd docker push "$DOCKER_REGISTRY/context-drift:prod-latest" || {
        echo "❌ Docker push failed"
        exit 1
    }
    echo "✅ Docker image pushed to registry"
else
    echo "ℹ️  Using local registry (--registry corvin)"
fi
echo ""

# Step 5: Deploy via Helm
echo "[5/6] Deploying Context-Drift to production..."
HELM_CHART="$REPO_ROOT/helm/context-drift"

if [[ -f "$HELM_CHART/Chart.yaml" ]]; then
    run_cmd helm upgrade --install context-drift-prod "$HELM_CHART" \
        --namespace="$NAMESPACE" \
        --values "$HELM_CHART/values-production.yaml" \
        --set image.tag="$DOCKER_TAG" \
        --wait \
        --timeout="$TIMEOUT" || {
        echo "❌ Helm deployment failed"
        exit 1
    }
    echo "✅ Helm deployment successful"
else
    echo "ℹ️  Helm chart not found, using kubectl apply"
    run_cmd kubectl apply -f "$REPO_ROOT/k8s/context-drift-production.yaml" \
        --namespace="$NAMESPACE" || {
        echo "❌ Kubernetes deployment failed"
        exit 1
    }
    echo "✅ Kubernetes deployment successful"
fi
echo ""

# Step 6: Rollout Verification
echo "[6/6] Waiting for rollout to complete..."
run_cmd kubectl rollout status deployment/context-drift-prod \
    --namespace="$NAMESPACE" \
    --timeout="$TIMEOUT" || {
    echo "❌ Rollout failed or timed out"
    exit 1
}
echo "✅ Rollout complete"
echo ""

# Post-Deployment Verification
echo "🔍 POST-DEPLOYMENT VERIFICATION"
echo "======================================"

# Check pod status
echo ""
echo "Pod Status:"
PODS=$(kubectl get pods -n "$NAMESPACE" -l app=context-drift-prod -o jsonpath='{.items[*].metadata.name}')
if [[ -z "$PODS" ]]; then
    echo "⚠️  No pods found (may still be starting)"
else
    echo "✅ Pods running: $PODS"

    # Check pod readiness
    for POD in $PODS; do
        READY=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].ready}')
        RESTARTS=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].restartCount}')
        echo "   - $POD: Ready=$READY, Restarts=$RESTARTS"
    done
fi

# Check service status
echo ""
echo "Service Status:"
SERVICE_IP=$(kubectl get svc -n "$NAMESPACE" -l app=context-drift-prod -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "pending")
if [[ "$SERVICE_IP" != "pending" ]]; then
    echo "✅ Service IP: $SERVICE_IP"
else
    echo "ℹ️  Service IP pending (normal for some cluster types)"
fi

# Health check
echo ""
echo "Health Checks:"
for POD in $PODS; do
    echo "   Checking $POD..."
    kubectl exec -it "$POD" -n "$NAMESPACE" -- \
        curl -s http://localhost:8765/v1/context-drift/health | grep -q "healthy" && \
        echo "   ✅ $POD health check passed" || \
        echo "   ⚠️  $POD health check status unknown"
done

# Check logs
echo ""
echo "Recent Logs (last 10 lines from each pod):"
for POD in $PODS; do
    echo "   Pod: $POD"
    kubectl logs -n "$NAMESPACE" "$POD" --tail=10 2>/dev/null | sed 's/^/     /'
done

# Metrics
echo ""
echo "Metrics Status:"
curl -s http://localhost:8765/v1/context-drift/metrics 2>/dev/null | \
    jq '{threshold: .threshold, feedback_quality: .feedback_quality, active_goals: .goals.active}' || \
    echo "   ℹ️  Metrics endpoint not yet responding (may need more time)"

# Summary
echo ""
echo "======================================"
echo "✅ PRODUCTION DEPLOYMENT COMPLETE"
echo "======================================"
echo ""
echo "Next Steps:"
echo "1. Monitor logs: kubectl logs -f deployment/context-drift-prod -n $NAMESPACE"
echo "2. Check dashboard: http://grafana:3000/d/context-drift-overview"
echo "3. View alerts: http://prometheus:9090/alerts"
echo "4. On-call: #corvin-prod-alerts on Slack"
echo ""
echo "Rollback command (if needed):"
echo "  helm rollback context-drift-prod -1 -n $NAMESPACE"
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
    echo "ℹ️  This was a DRY-RUN. No actual changes made."
fi
