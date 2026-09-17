#!/bin/bash
# scripts/commit_registry_snapshot.sh
# Commits Task Registry snapshots to git (run by systemd after registry sync)
#
# ADR-0864: Git-Tracked Registry Snapshots
# Compliance: GDPR Art. 30, 32 (audit trail of task registry state over time)

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAPSHOT_DIR="$REPO_ROOT/docs/reference/task_registry_snapshots"
REGISTRY_FILE="${CORVIN_HOME:~/.corvin}/task_registry.json"

# Ensure directories exist
mkdir -p "$SNAPSHOT_DIR"

if [[ ! -f "$REGISTRY_FILE" ]]; then
    echo "❌ Registry file not found: $REGISTRY_FILE"
    exit 1
fi

# Create timestamped snapshot
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SNAPSHOT_FILE="$SNAPSHOT_DIR/$TIMESTAMP.json"

# Copy registry to snapshot
cp "$REGISTRY_FILE" "$SNAPSHOT_FILE"

# Verify snapshot is valid JSON
if ! jq empty "$SNAPSHOT_FILE" 2>/dev/null; then
    echo "❌ Snapshot is not valid JSON: $SNAPSHOT_FILE"
    rm "$SNAPSHOT_FILE"
    exit 1
fi

# Count tasks in snapshot
TASK_COUNT=$(jq '.tasks | length' "$SNAPSHOT_FILE" 2>/dev/null || echo "unknown")

# Change to repo root for git commit
cd "$REPO_ROOT"

# Stage snapshot
git add "$SNAPSHOT_FILE"

# Only commit if there are changes
if ! git diff --cached --quiet; then
    git commit -m "automation: Task Registry Snapshot $TIMESTAMP

Task count: $TASK_COUNT
Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Automated by: corvin-registry-snapshot.service

GDPR Art. 30, 32: Audit trail of task registry state.
ADR-0864: Git-tracked registry snapshots for compliance." || true
    echo "✅ Snapshot committed: $SNAPSHOT_FILE (tasks: $TASK_COUNT)"
else
    echo "✅ No changes to commit (snapshot identical to previous)"
fi
