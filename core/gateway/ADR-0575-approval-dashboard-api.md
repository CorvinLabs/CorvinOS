---
id: ADR-0575
title: L5 k=2 Approval Dashboard API (REST endpoints for operator control)
status: ACCEPTED
depends_on:
  - ADR-0572
relates_to:
  - ADR-0232
  - ADR-0314
paths:
  - core/gateway/routes/approval_routes.py
  - core/gateway/corvin_gateway/app.py
  - core/learning/optimizer_integration.py
docs:
  - docs/claude-ref/l5_k2_operator_approval_gate.md
commits:
  - (Week 1 commit)
---

# ADR-0575 Reference: L5 k=2 Approval Dashboard API

**Canonical Location:** `/home/shumway/projects/Corvin-ADR/decisions/ADR-0575-approval-dashboard-api.md`

This file is a local reference copy. The authoritative ADR lives in the Corvin-ADR repo.

See `/home/shumway/projects/Corvin-ADR/decisions/ADR-0575-approval-dashboard-api.md` for full documentation.

## Summary

REST API surface for operator control of L5 k=2 OperatorApprovalGate:

**Endpoints:**
- `GET /v1/approvals/{skill_id}` — List pending approvals
- `GET /v1/approvals/{skill_id}/{approval_id}/status` — Get status
- `POST /v1/approvals/{skill_id}/{approval_id}/approve` — Approve
- `POST /v1/approvals/{skill_id}/{approval_id}/reject` — Reject
- `POST /v1/approvals/{skill_id}/{approval_id}/revoke` — Revoke

**Week 1 Deliverables:**
- 5 REST endpoints + validation + audit logging
- OperatorApprovalGate initialization in gateway app
- 12 E2E tests (Learning Loop → Gate → API)
- Tenant isolation + scrubbed alerts + audit trail

Tests: 30 passing (12 new E2E + 18 existing unit)
