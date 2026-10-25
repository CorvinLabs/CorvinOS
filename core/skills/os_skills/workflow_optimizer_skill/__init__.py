"""Workflow Optimizer Skill (Phase 10, Stream 1) — package husk.

NOT WIRED. Its last module, ``confidence_calculator.py``, was retired by ADR-2282
(2026-10-25): its consumer was deleted in T-0140 and L5 routing never read these
weights. The trust-weight invariant it carried lives in
``core/learning/operator_feedback.py`` and ``core/learning/adversarial_detector.py``.
The package stays only because the ADR-2281 learning register scans this directory.
"""

__version__ = "1.0.0"
__phase__ = "Phase 10 Stream 1"
__status__ = "retired (ADR-2282)"
