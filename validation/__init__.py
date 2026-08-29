"""
Phase 3 Validation Framework
=============================

Core validators for Week 6 Go/No-Go gate:
- E2E Wiring Proof: Proves entry points are reachable + functional
- Reproducibility Checker: Detects flakes (3 runs, <2% target)
- Coverage Impact: Measures before/after coverage delta
- Measurement Report: Exports JSON results for analysis

Phase 3.A (Weeks 1-2): Core validators + simple JSON output, manual gate decision
Phase 3.B (Weeks 3-4): Grafana dashboard + automated gate logic (if 3.A stable)
"""

__version__ = "0.1.0"
__status__ = "PHASE_3A_ACTIVE"

from .e2e_wiring_proof import E2EWiringProofValidator
from .reproducibility_checker import ReproducibilityChecker
from .coverage_impact import CoverageImpactMeasurement
from .measurement_report import MeasurementReport

__all__ = [
    "E2EWiringProofValidator",
    "ReproducibilityChecker",
    "CoverageImpactMeasurement",
    "MeasurementReport",
]
