"""Workflow Optimizer Skill (Phase 10, Stream 1).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). Every
importer of this package is a test; L5 routing does not read these weights.

Phase 2 (Week 2-3): Learning loop integration via ADR-0314.

Modules:
- feedback_handler: Receives operator feedback, emits LearningEvents
- confidence_calculator: Computes Bayesian confidence scores, updates routing weights
"""

__version__ = "1.0.0"
__phase__ = "Phase 10 Stream 1"
__status__ = "Phase 1: Week 2 (Feedback + Confidence Calculator)"
