"""L5 routing monitoring (ADR-2092).

- ``routing_ledger`` — the one persisted record of decisions and outcomes
- ``correctness_tracker`` — observable-outcome success comparison
- ``rollback_detector`` — persisted, tenant-scoped trip
- ``readiness`` — evidence gate before Phase 2 may route
- ``dual_write`` — the phase gate and the ADR-0251 D2 clamp
"""
