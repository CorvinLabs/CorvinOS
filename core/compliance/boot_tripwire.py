"""Boot tripwire facade — ADR-0232 "Phase 1A" skeleton, DEFUSED.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

This module used to be a SECOND boot tripwire that checked a SECOND chain
(``<corvin_home>/audit.jsonl`` in its own format) — a file no writer uses, so
it could pass while the real chain was broken, or fail on a healthy install.
There is one tripwire: ``corvin_compliance_reports.tripwire`` (run by
``bootstrap.boot_platform()`` in both shipped hosts, no override). This facade
only delegates to it and refuses to vouch for any root other than the one the
canonical tripwire actually checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from .exceptions import TripwireError


def _canonical():
    from corvin_compliance_reports import tripwire  # type: ignore[import-not-found]

    return tripwire


def _same_root(corvin_home: Path) -> bool:
    from forge import paths as forge_paths  # type: ignore[import-not-found]

    return Path(corvin_home).expanduser().resolve() == forge_paths.corvin_home().resolve()


class BootTripwire:
    """Delegates to ``corvin_compliance_reports.tripwire``. Fail-closed."""

    def __init__(self, corvin_home: Path):
        self.corvin_home = Path(corvin_home)
        self.results: List[Tuple[str, bool, str]] = []

    def run(self) -> bool:
        """Run the canonical tripwires. False if ANY fails, or if ``corvin_home``
        is not the root they check (this facade never vouches for another)."""
        self.results = []
        try:
            if not _same_root(self.corvin_home):
                self.results.append((
                    "corvin_home", False,
                    "not the active CORVIN_HOME — the canonical tripwire checks only that root",
                ))
                return False
            for r in _canonical().check_all():
                self.results.append((r.name, bool(r.ok), r.detail))
        except Exception as exc:  # noqa: BLE001 - an unrunnable tripwire fails
            self.results.append(("tripwire", False, f"canonical tripwire unavailable: {type(exc).__name__}"))
            return False
        return bool(self.results) and all(ok for _, ok, _ in self.results)

    def assert_all(self) -> None:
        """Raise :class:`TripwireError` unless the canonical tripwire passes."""
        if not _same_root(self.corvin_home):
            raise TripwireError(
                f"boot tripwire refused: {self.corvin_home} is not the active CORVIN_HOME"
            )
        try:
            _canonical().assert_all()
        except TripwireError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TripwireError(f"boot tripwire failed: {exc}") from exc

    def status(self) -> dict:
        return {
            "all_pass": bool(self.results) and all(p for _, p, _ in self.results),
            "checks": [
                {"component": name, "passed": passed, "message": msg}
                for name, passed, msg in self.results
            ],
        }


def assert_boot_compliance(corvin_home: Path) -> None:
    """Assert the canonical boot tripwire for ``corvin_home``. Fail-closed."""
    BootTripwire(corvin_home).assert_all()
