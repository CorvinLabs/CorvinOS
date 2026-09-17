# Docker Uninstall Coverage Specification (ADR-0868)

**Date:** 2026-09-17  
**Status:** PROPOSED  

## Overview

Docker Uninstall Coverage ensures complete cleanup of CorvinOS Docker deployments (ADR-0868), satisfying GDPR Art. 17 (right to erasure).

## Key Gaps Fixed

1. **Label-based container detection** — detects custom deployment names
2. **Network cleanup** — removes orphaned networks
3. **Dangling resource pruning** — cleans build cache
4. **Cleanup verification** — confirms all resources removed
5. **Audit trail export** — preserves compliance record

## Components

### 1. Enhanced Deployment Detection
```bash
detect_deployment_mode() {
    # 1. Check docker command exists
    # 2. Check docker daemon running
    # 3. Check containers with label "app=corvinOS"
    # 4. Fallback to legacy name pattern
}
```

### 2. Audit Trail Export (Before Deletion)
- Exports to: `~/.corvin-exports/audit-trail-export-YYYYMMDD-HHMMSS.jsonl`
- Non-blocking: continues if export fails
- Preserves compliance record (GDPR Art. 30)

### 3. Container Cleanup
- List by label: `docker ps --all --filter "label=app=corvinOS"`
- Stop + remove containers
- Legacy name fallback: `corvinOS-console`, `corvinOS-gateway`, etc.

### 4. Image Cleanup
- Remove by tag: `docker rmi corvinOS:latest`
- Remove by pattern: `docker rmi corvinOS:*`
- Prune dangling: `docker image prune -af --filter "label=app=corvinOS"`

### 5. Volume Cleanup (GDPR Critical)
- List volumes by label
- Interactive confirmation: "Remove CorvinOS volumes? (y/n):"
- Non-interactive: removes silently (no TTY)

### 6. Network Cleanup
- Remove networks with label: `docker network ls --filter "label=app=corvinOS"`

### 7. Post-Cleanup Verification
- Verify no containers remain
- Verify no images remain
- Verify no volumes remain

## Manual Testing

```bash
# Deploy CorvinOS in Docker
docker-compose up -d

# Verify resources exist
docker ps && docker volume ls

# Run uninstall
./corvin-uninstall

# Verify resources removed
docker ps  # Should be empty
docker volume ls  # Should be empty
```

## GDPR Compliance

- ✅ All containers removed (PII storage)
- ✅ All volumes removed (credential cache)
- ✅ All networks removed (deployment metadata)
- ✅ Audit trail exported (compliance record preserved)
- ✅ Unconditional deletion (no "keep backup" option)

---

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
