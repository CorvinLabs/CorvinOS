"""DataHub Creator (TRACK I) — 12-phase project workspace with skill metrics + learning visualization.

Integrates with Learning Loop (Track B ✅) to provide users a project-centric view of their
skill generation journey, from creation through convergence to recommendations.

Architecture:
- ProjectModel: Persistence layer (project metadata, phase state, metrics snapshots)
- MetricsAggregator: Collects skill execution + feedback data from learning store
- ProjectSkill: Orchestrates 12-phase flow (SkillBase subclass)
- ProjectRoutes: 6 console API endpoints (create, list, detail, update, export, collaborate)
- React UI: 12-phase wizard + learning dashboard (convergence visualization)

5 LDD gates:
1. ✅ Dialectical reasoning (12-phase flow design)
2. E2E wiring proof (routes exist, metrics work)
3. RED→GREEN (full implementation)
4. Adversarial testing (injection, concurrency, edge cases)
5. Documentation + ADR

Status: In Progress (2026-09-18)
"""

__version__ = "1.0.0"
