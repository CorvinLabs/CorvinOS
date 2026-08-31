"""Entry point registry (Finding #12: enforce wiring)."""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


class EntryPointCategory(Enum):
    FLASK_ROUTE = "flask_route"
    CLI_COMMAND = "cli_command"
    WEBSOCKET_HANDLER = "websocket_handler"
    BRIDGE_HANDLER = "bridge_handler"
    PLUGIN_ENTRY = "plugin_entry"
    FORGE_TOOL = "forge_tool"


class WiringStatus(Enum):
    NOT_WIRED = "not_wired"
    WIRED = "wired"
    TESTED = "tested"
    PRODUCTION = "production"


@dataclass
class EntryPoint:
    """Single entry point metadata."""
    name: str
    category: EntryPointCategory
    capability_required: str
    module_path: str
    function_name: str
    is_critical: bool = True
    status: WiringStatus = WiringStatus.NOT_WIRED
    wired_commit: Optional[str] = None
    test_file: Optional[str] = None


class CallSiteRegistry:
    """Central registry tracking all entry points (50+ Phase 1)."""

    def __init__(self):
        self.entries: dict[str, EntryPoint] = {}

    def register(self, ep: EntryPoint) -> None:
        """Register an entry point."""
        if ep.name in self.entries:
            raise ValueError(f"Entry point {ep.name} already registered")
        self.entries[ep.name] = ep
        logger.debug(f"[Registry] Registered: {ep.name}")

    def get(self, name: str) -> Optional[EntryPoint]:
        """Get entry point by name."""
        return self.entries.get(name)

    def by_category(self, category: EntryPointCategory) -> List[EntryPoint]:
        """Get all entry points in a category."""
        return [ep for ep in self.entries.values() if ep.category == category]

    def not_wired(self) -> List[EntryPoint]:
        """Get all not-yet-wired entry points."""
        return [
            ep for ep in self.entries.values()
            if ep.status == WiringStatus.NOT_WIRED
        ]

    def mark_wired(self, name: str, commit: str) -> None:
        """Mark entry point as wired."""
        ep = self.get(name)
        if ep:
            ep.status = WiringStatus.WIRED
            ep.wired_commit = commit
            logger.debug(f"[Registry] Marked wired: {name}")

    def mark_tested(self, name: str, test_file: str) -> None:
        """Mark entry point as tested."""
        ep = self.get(name)
        if ep:
            ep.status = WiringStatus.TESTED
            ep.test_file = test_file
            logger.debug(f"[Registry] Marked tested: {name}")

    def enforce_wiring(self, severity: str = "critical") -> None:
        """Enforce wiring compliance (Finding #12)."""
        unwired = self.not_wired()
        critical_unwired = [ep for ep in unwired if ep.is_critical]

        if critical_unwired and severity == "critical":
            raise RuntimeError(
                f"Critical entry points unwired: "
                f"{[ep.name for ep in critical_unwired]}"
            )

        if unwired and severity == "warning":
            logger.warning(f"Unwired entry points: {[ep.name for ep in unwired]}")

    def summary(self) -> dict:
        """Return wiring summary."""
        total = len(self.entries)
        wired = len([ep for ep in self.entries.values() if ep.status != WiringStatus.NOT_WIRED])
        tested = len([ep for ep in self.entries.values() if ep.status == WiringStatus.TESTED])
        return {
            "total": total,
            "wired": wired,
            "tested": tested,
            "not_wired": total - wired,
        }
