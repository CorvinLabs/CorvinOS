"""
Skill Version Manager (ADR-0533)

Implements semantic versioning and in-flight-freeze semantics.
Ensures immutability and tenant version-pinning.
"""

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# Simple semver parsing (no external dependency required)
class SemanticVersion:
    """Simple semantic version parser."""

    def __init__(self, version_str: str):
        """Parse a semantic version string."""
        match = re.match(r'^(\d+)\.(\d+)\.(\d+)(?:-([a-z0-9]+))?$', version_str)
        if not match:
            raise ValueError(f"Invalid semver: {version_str}")

        self.major = int(match.group(1))
        self.minor = int(match.group(2))
        self.patch = int(match.group(3))
        self.prerelease = match.group(4)

    def __str__(self):
        s = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            s += f"-{self.prerelease}"
        return s

    def __lt__(self, other):
        if not isinstance(other, SemanticVersion):
            other = SemanticVersion(str(other))
        if self.major != other.major:
            return self.major < other.major
        if self.minor != other.minor:
            return self.minor < other.minor
        if self.patch != other.patch:
            return self.patch < other.patch
        # Prerelease versions are less than release versions
        if self.prerelease and not other.prerelease:
            return True
        if not self.prerelease and other.prerelease:
            return False
        if self.prerelease and other.prerelease:
            return self.prerelease < other.prerelease
        return False

    def __le__(self, other):
        return self < other or self == other

    def __gt__(self, other):
        if not isinstance(other, SemanticVersion):
            other = SemanticVersion(str(other))
        return other < self

    def __ge__(self, other):
        return self > other or self == other

    def __eq__(self, other):
        if not isinstance(other, SemanticVersion):
            other = SemanticVersion(str(other))
        return (self.major == other.major and
                self.minor == other.minor and
                self.patch == other.patch and
                self.prerelease == other.prerelease)


@dataclass
class SkillRun:
    """Metadata for an in-flight skill execution."""
    run_id: str
    skill_id: str
    skill_version_at_start: str  # Immutable: version when run started
    tenant_id: str
    started_at: str  # ISO 8601
    phase_completed: int  # Which phase has completed (0 = not started)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class SkillVersionConstraint:
    """Represents a version constraint (e.g., '>=1.0.0', '~1.2.3')."""
    operator: str  # >=, >, <=, <, =, ~, ^
    version: str
    
    @classmethod
    def from_string(cls, constraint_str: str) -> "SkillVersionConstraint":
        """Parse a version constraint string."""
        match = re.match(r"^(>=?|<=?|~|\^|=)?(.+)$", constraint_str.strip())
        if not match:
            raise ValueError(f"Invalid version constraint: {constraint_str}")
        
        operator = match.group(1) or "="
        version = match.group(2).strip()
        
        # Validate version
        try:
            SemanticVersion(version)
        except ValueError:
            raise ValueError(f"Invalid semver in constraint: {version}")
        
        return cls(operator, version)
    
    def matches(self, version_str: str) -> bool:
        """
        Check if a version matches this constraint.
        
        Args:
            version_str: Version to check (e.g., '1.2.3')
            
        Returns:
            True if version matches constraint
        """
        try:
            v = SemanticVersion(version_str)
            constraint_v = SemanticVersion(self.version)
        except ValueError:
            return False
        
        if self.operator == "=":
            return v == constraint_v
        elif self.operator == ">":
            return v > constraint_v
        elif self.operator == ">=":
            return v >= constraint_v
        elif self.operator == "<":
            return v < constraint_v
        elif self.operator == "<=":
            return v <= constraint_v
        elif self.operator == "~":
            # Tilde: compatible with this version (~1.2.3 = >=1.2.3, <1.3.0)
            return (v >= constraint_v and 
                    v.major == constraint_v.major and
                    v.minor == constraint_v.minor)
        elif self.operator == "^":
            # Caret: compatible with this version (^1.2.3 = >=1.2.3, <2.0.0)
            return (v >= constraint_v and v.major == constraint_v.major)
        
        return False


class SkillVersionManager:
    """Manages skill versioning with in-flight-freeze semantics."""
    
    def __init__(self, state_dir: Optional[Path] = None):
        """
        Initialize version manager.
        
        Args:
            state_dir: Directory for storing in-flight run metadata
        """
        if state_dir is None:
            state_dir = Path.home() / ".corvin" / "tenants" / "_default" / "global" / "skills"
        
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        # In-flight runs registry (in-memory cache + disk)
        self.runs_file = self.state_dir / "in_flight_runs.json"
        self._runs: Dict[str, SkillRun] = self._load_runs()
    
    def start_run(
        self,
        run_id: str,
        skill_id: str,
        skill_version: str,
        tenant_id: str = "_default",
    ) -> SkillRun:
        """
        Register a new skill run (start of execution).
        
        Args:
            run_id: Unique run identifier
            skill_id: Skill identifier
            skill_version: Version being run
            tenant_id: Tenant ID
            
        Returns:
            SkillRun metadata
        """
        run = SkillRun(
            run_id=run_id,
            skill_id=skill_id,
            skill_version_at_start=skill_version,
            tenant_id=tenant_id,
            started_at=datetime.utcnow().isoformat() + "Z",
            phase_completed=0,
        )
        
        self._runs[run_id] = run
        self._save_runs()
        
        return run
    
    def get_run_version(self, run_id: str) -> Optional[str]:
        """
        Get the skill version for an in-flight run.
        
        In-flight-freeze: Returns the version when the run started,
        not the current installed version.
        
        Args:
            run_id: Run identifier
            
        Returns:
            Version string or None if run not found
        """
        if run_id in self._runs:
            return self._runs[run_id].skill_version_at_start
        return None
    
    def update_phase(self, run_id: str, phase_completed: int) -> bool:
        """
        Update the phase completion counter for a run.
        
        Args:
            run_id: Run identifier
            phase_completed: Phase number completed
            
        Returns:
            True if updated, False if run not found
        """
        if run_id not in self._runs:
            return False
        
        self._runs[run_id].phase_completed = max(
            self._runs[run_id].phase_completed,
            phase_completed
        )
        self._save_runs()
        return True
    
    def finish_run(self, run_id: str) -> bool:
        """
        Remove a run from the in-flight registry (execution complete).
        
        Args:
            run_id: Run identifier
            
        Returns:
            True if removed, False if not found
        """
        if run_id in self._runs:
            del self._runs[run_id]
            self._save_runs()
            return True
        return False
    
    def get_all_runs(self) -> List[SkillRun]:
        """Get all in-flight runs."""
        return list(self._runs.values())
    
    def cleanup_old_runs(self, max_age_days: int = 7) -> int:
        """
        Clean up runs older than max_age_days.
        
        Args:
            max_age_days: Maximum age in days
            
        Returns:
            Number of runs removed
        """
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        
        old_runs = []
        for run_id, run in self._runs.items():
            try:
                started = datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
                if started < cutoff:
                    old_runs.append(run_id)
            except (ValueError, AttributeError):
                pass
        
        for run_id in old_runs:
            del self._runs[run_id]
        
        if old_runs:
            self._save_runs()
        
        return len(old_runs)
    
    def _load_runs(self) -> Dict[str, SkillRun]:
        """Load in-flight runs from disk."""
        if not self.runs_file.exists():
            return {}
        
        try:
            with open(self.runs_file) as f:
                data = json.load(f)
            
            runs = {}
            for run_id, run_dict in data.items():
                runs[run_id] = SkillRun(**run_dict)
            
            return runs
        except (json.JSONDecodeError, TypeError):
            return {}
    
    def _save_runs(self) -> None:
        """Save in-flight runs to disk."""
        data = {
            run_id: run.to_dict()
            for run_id, run in self._runs.items()
        }
        
        with open(self.runs_file, "w") as f:
            json.dump(data, f, indent=2)


@dataclass
class CanaryConfig:
    """Configuration for canary deployment."""
    enabled: bool = False
    traffic_percent: int = 10  # % of traffic routed to new version
    duration_days: int = 7
    score_improvement_percent: float = 2.0  # Must beat baseline by 2%
    error_rate_max_percent: float = 1.0
    auto_rollback_on_failure: bool = True


class TenantVersionPin:
    """
    Manages per-tenant version pinning (from tenant.corvin.yaml).
    
    Allows operators to pin specific versions per tenant and control canary mode.
    """
    
    def __init__(self, config: Dict):
        """
        Initialize from tenant config.
        
        Args:
            config: Tenant skills configuration section
        """
        self.config = config
    
    def get_skill_version(self, skill_id: str) -> Optional[str]:
        """
        Get pinned version for a skill in this tenant.
        
        Args:
            skill_id: Skill identifier
            
        Returns:
            Pinned version string or None if not pinned
        """
        if skill_id in self.config:
            skill_cfg = self.config[skill_id]
            if isinstance(skill_cfg, dict) and "version" in skill_cfg:
                return skill_cfg["version"]
        return None
    
    def is_skill_enabled(self, skill_id: str) -> bool:
        """Check if a skill is enabled in this tenant."""
        if skill_id in self.config:
            skill_cfg = self.config[skill_id]
            if isinstance(skill_cfg, dict):
                return skill_cfg.get("enabled", True)
        return True
    
    def get_canary_config(self, skill_id: str) -> CanaryConfig:
        """Get canary config for a skill."""
        if skill_id in self.config:
            skill_cfg = self.config[skill_id]
            if isinstance(skill_cfg, dict) and skill_cfg.get("canary"):
                canary_cfg = skill_cfg["canary"]
                return CanaryConfig(
                    enabled=canary_cfg.get("enabled", False),
                    traffic_percent=canary_cfg.get("traffic_percent", 10),
                    duration_days=canary_cfg.get("duration_days", 7),
                    score_improvement_percent=canary_cfg.get("score_improvement_percent", 2.0),
                    error_rate_max_percent=canary_cfg.get("error_rate_max_percent", 1.0),
                    auto_rollback_on_failure=canary_cfg.get("auto_rollback_on_failure", True),
                )
        
        return CanaryConfig()


class VersionResolver:
    """Resolves which version of a skill to use (pin > canary > installed)."""
    
    def __init__(self):
        """Initialize version resolver."""
        self.version_manager = SkillVersionManager()
    
    def resolve_version(
        self,
        skill_id: str,
        available_versions: List[str],
        tenant_pin: Optional[TenantVersionPin] = None,
    ) -> str:
        """
        Resolve which version to use for a skill.
        
        Priority:
        1. Explicit tenant pin (if set)
        2. Canary version (if canary enabled and within traffic %)
        3. Latest stable version
        
        Args:
            skill_id: Skill identifier
            available_versions: List of available versions
            tenant_pin: Tenant version configuration
            
        Returns:
            Selected version string
        """
        # Check for explicit tenant pin
        if tenant_pin:
            pinned_version = tenant_pin.get_skill_version(skill_id)
            if pinned_version and pinned_version in available_versions:
                return pinned_version
        
        # For now, return latest version
        # TODO: Implement canary logic
        if not available_versions:
            raise ValueError(f"No versions available for skill: {skill_id}")
        
        # Sort by semver and return latest
        try:
            sorted_versions = sorted(
                available_versions,
                key=lambda v: SemanticVersion(v),
                reverse=True
            )
            return sorted_versions[0]
        except ValueError:
            # Fallback to alphabetical sort
            return sorted(available_versions, reverse=True)[0]
    
    def resolve_dependency_version(
        self,
        dependency_name: str,
        constraint_str: str,
        available_versions: List[str],
    ) -> str:
        """
        Resolve a dependency version given a constraint.
        
        Args:
            dependency_name: Dependency name
            constraint_str: Version constraint (e.g., '>=1.0.0')
            available_versions: Available versions
            
        Returns:
            Resolved version
            
        Raises:
            ValueError: If no version matches constraint
        """
        constraint = SkillVersionConstraint.from_string(constraint_str)
        
        matching = [
            v for v in available_versions
            if constraint.matches(v)
        ]
        
        if not matching:
            raise ValueError(
                f"No version of {dependency_name} matches constraint {constraint_str}. "
                f"Available: {', '.join(available_versions)}"
            )
        
        # Return latest matching version
        try:
            sorted_versions = sorted(
                matching,
                key=lambda v: SemanticVersion(v),
                reverse=True
            )
            return sorted_versions[0]
        except ValueError:
            return sorted(matching, reverse=True)[0]


# Singleton instance
_version_manager: Optional[SkillVersionManager] = None


def get_version_manager() -> SkillVersionManager:
    """Get or create the singleton version manager."""
    global _version_manager
    if _version_manager is None:
        _version_manager = SkillVersionManager()
    return _version_manager


def get_version_resolver() -> VersionResolver:
    """Get a version resolver instance."""
    return VersionResolver()
