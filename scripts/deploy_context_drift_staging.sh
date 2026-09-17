#!/bin/bash
# Context-Drift Staging Deployment Script
# Validates, builds, and deploys Context-Drift to staging environment
# ADR-0407: Session Context Drift Prevention
# ADR-0362: Production Deployment Framework

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_REGISTRY="${DOCKER_REGISTRY:-corvin}"
NAMESPACE="${NAMESPACE:-staging}"
TIMEOUT="${TIMEOUT:-5m}"

echo "🚀 Context-Drift Staging Deployment"
echo "=================================="
echo "Repository: $REPO_ROOT"
echo "Namespace: $NAMESPACE"
echo "Registry: $DOCKER_REGISTRY"
echo ""

# Step 1: Validation
echo "[1/6] Running deployment validation..."
cd "$REPO_ROOT"
python3 -m core.deployment.context_drift_deployment_validation || {
    echo "❌ Deployment validation failed"
    exit 1
}
echo "✅ Deployment validation passed"
echo ""

# Step 2: Build Docker image
echo "[2/6] Building Docker image..."
DOCKER_TAG="stage-$(date +%s)"
docker build \
    --tag "$DOCKER_REGISTRY/context-drift:$DOCKER_TAG" \
    --tag "$DOCKER_REGISTRY/context-drift:stage-latest" \
    --file Dockerfile \
    . || {
    echo "❌ Docker build failed"
    exit 1
}
echo "✅ Docker image built: $DOCKER_REGISTRY/context-drift:$DOCKER_TAG"
echo ""

# Step 3: Push to registry (if registry URL provided)
if [[ -n "$DOCKER_REGISTRY" && "$DOCKER_REGISTRY" != "corvin" ]]; then
    echo "[3/6] Pushing Docker image..."
    docker push "$DOCKER_REGISTRY/context-drift:stage-latest" || {
        echo "⚠️  Docker push failed (continuing with local image)"
    }
    echo "✅ Docker image pushed"
else
    echo "[3/6] Skipping push (using local registry)"
fi
echo ""

# Step 4: Create namespace if needed
echo "[4/6] Preparing Kubernetes namespace..."
kubectl create namespace "$NAMESPACE" 2>/dev/null || true
echo "✅ Namespace $NAMESPACE ready"
echo ""

# Step 5: Deploy via Helm (or kubectl if Helm chart not available)
echo "[5/6] Deploying Context-Drift..."
HELM_CHART="$REPO_ROOT/helm/context-drift"

if [[ -f "$HELM_CHART/Chart.yaml" ]]; then
    helm upgrade --install context-drift-stage "$HELM_CHART" \
        --namespace="$NAMESPACE" \
        --values "$HELM_CHART/values-staging.yaml" \
        --set image.tag="$DOCKER_TAG" \
        --wait || {
        echo "❌ Helm deployment failed"
        exit 1
    }
    echo "✅ Helm deployment successful"
else
    echo "⚠️  Helm chart not found, using kubectl apply"
    kubectl apply -f "$REPO_ROOT/k8s/context-drift-staging.yaml" \
        --namespace="$NAMESPACE" || {
        echo "❌ Kubernetes deployment failed"
        exit 1
    }
    echo "✅ Kubernetes deployment successful"
fi
echo ""

# Step 6: Wait for rollout
echo "[6/6] Waiting for rollout..."
kubectl rollout status deployment/context-drift-stage \
    --namespace="$NAMESPACE" \
    --timeout="$TIMEOUT" || {
    echo "❌ Rollout failed or timed out"
    exit 1
}
echo "✅ Rollout complete"
echo ""

# Post-deployment verification
echo "🔍 Post-Deployment Verification"
echo "================================"

# Check pod status
PODS=$(kubectl get pods -n "$NAMESPACE" -l app=context-drift-stage -o jsonpath='{.items[*].metadata.name}')
if [[ -z "$PODS" ]]; then
    echo "⚠️  No pods found"
else
    echo "✅ Pods running: $PODS"
fi

# Check service
SERVICE_IP=$(kubectl get svc -n "$NAMESPACE" -l app=context-drift-stage -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "pending")
if [[ "$SERVICE_IP" != "pending" ]]; then
    echo "✅ Service IP: $SERVICE_IP"
else
    echo "⏳ Service IP pending (this is normal for staging)"
fi

# Check logs
echo ""
echo "📋 Recent logs (last 10 lines):"
for POD in $PODS; do
    echo "  Pod: $POD"
    kubectl logs -n "$NAMESPACE" "$POD" --tail=5 2>/dev/null | sed 's/^/    /'
done

echo ""
echo "✅ Staging deployment complete"
echo ""
echo "Next steps:"
echo "1. Run smoke tests: pytest tests/e2e/test_staging_integration_full.py"
echo "2. Check logs: kubectl logs -f -l app=context-drift-stage -n $NAMESPACE"
echo "3. Verify API: curl http://<service-ip>/v1/context-drift/health"
