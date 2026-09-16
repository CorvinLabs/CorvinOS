# Reference: ADR-0769 — Licensing Phase 2 - A2A RSA Gate

**Canonical location**: `/home/shumway/projects/Corvin-ADR/decisions/ADR-0769-licensing-phase2-a2a-rsa-gate.md`

**Status**: ACCEPTED

**Summary**: Licensing Phase 2 A2A RSA Gate enables app-to-app delegation with member credentials. Implements RSA 2048-bit keypairs, signed task verification (fail-closed), revocation list (CRL) management, and audit-first design.

**Code paths**:
- core/licensing/member_credential.py
- core/licensing/a2a_verifier.py
- core/licensing/authority_server.py
- core/console/corvin_console/routes/a2a_licensing_gate_routes.py

**Commits**:
- feat(licensing): Phase 2 — A2A RSA Gate (credential + verifier + authority)

This is a reference file. The authoritative ADR is maintained in the Corvin-ADR repository.
