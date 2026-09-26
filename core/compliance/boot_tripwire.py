"""Boot Tripwire — Fail-closed compliance check at startup."""

import os
from pathlib import Path
from typing import List, Tuple
from .exceptions import TripwireError
from .audit_trail import AuditTrail


class BootTripwire:
    """Verify all compliance mechanisms exist before boot. Fail-closed."""
    
    # Mechanisms required for Phase 1A (minimal)
    REQUIRED_COMPONENTS = [
        "audit_trail",  # L16
        # Phase 1B will add: consent_gate, flow_guard, house_rules, erasure
    ]
    
    def __init__(self, corvin_home: Path):
        self.corvin_home = Path(corvin_home)
        self.audit_trail_path = self.corvin_home / "audit.jsonl"
        self.results: List[Tuple[str, bool, str]] = []
    
    def check_audit_trail_present(self) -> bool:
        """Check audit trail exists, is writable, and has valid chain."""
        try:
            # Audit trail must be explicitly created before this check
            if not self.audit_trail_path.exists():
                self.results.append(("audit_trail", False, "Audit trail file not found"))
                return False
            
            # If file exists but is empty, fail (not initialized)
            if self.audit_trail_path.stat().st_size == 0:
                self.results.append(("audit_trail", False, "Audit trail is empty (not initialized)"))
                return False
            
            trail = AuditTrail(self.audit_trail_path)
            # Verify chain integrity
            trail.verify_chain()
            self.results.append(("audit_trail", True, "Present, initialized, and verified"))
            return True
        except Exception as e:
            self.results.append(("audit_trail", False, str(e)))
            return False
    
    def run(self) -> bool:
        """Run all checks. Return False if ANY fail (fail-closed)."""
        self.results = []
        
        # Phase 1A checks
        checks = [
            ("audit_trail", self.check_audit_trail_present),
        ]
        
        all_pass = True
        for name, check_fn in checks:
            try:
                result = check_fn()
                if not result:
                    all_pass = False
            except Exception as e:
                self.results.append((name, False, f"Exception: {e}"))
                all_pass = False
        
        return all_pass
    
    def assert_all(self) -> None:
        """Assert all checks pass. Raise TripwireError if ANY fail."""
        if not self.run():
            # Fail-closed: refuse to boot
            failures = [
                f"  ❌ {name}: {msg}"
                for name, passed, msg in self.results
                if not passed
            ]
            raise TripwireError(
                f"Boot tripwire failed — platform will not start:\n" +
                "\n".join(failures) +
                "\n\nPhase 1B will add remaining compliance mechanisms."
            )
    
    def status(self) -> dict:
        """Return check results."""
        return {
            "all_pass": all(passed for _, passed, _ in self.results),
            "checks": [
                {"component": name, "passed": passed, "message": msg}
                for name, passed, msg in self.results
            ],
        }


def assert_boot_compliance(corvin_home: Path) -> None:
    """Assert boot tripwire. Fail-closed."""
    tripwire = BootTripwire(corvin_home)
    tripwire.assert_all()
