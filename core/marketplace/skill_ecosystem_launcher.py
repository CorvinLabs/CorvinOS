"""Skill Ecosystem Launcher & Orchestration (ADR-0511 + ADR-0532).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) —
``grep -rn skill_ecosystem_launcher`` outside tests finds no route, CLI, daemon
or plugin that constructs a ``SkillEcosystemLauncher``. Until 2026-09-27 the
module did not even import (it imported ``core.infinite_session.event_persistence``
and ``core.infinite_session.security_events``, neither of which exists), and
``_load_skill_manifests`` assigned to a frozen dataclass, so every manifest
failed to load. Installations, ratings and install counts are IN-MEMORY only.

Phase 3 Week 3+ parallel track: Enable skill distribution + community plugin framework.

Responsibilities:
  1. Skill discovery from marketplace (buildin + contributor tiers)
  2. Installation orchestration (manifest validation, versioning, registry binding)
  3. Community plugin framework (rating, feedback, auto-updates)
  4. Tenant-specific skill versions (per-tenant learning models, ADR-0007)

Architecture:
  - Buildin skills: Apache 2.0 + CLA, SLA guaranteed (48h bugfix, 24h security)
  - Contributor skills: MIT, community-driven, opt-in learning
  - Skill manifests: ADR-0264 frontmatter + skill.json schema
  - Marketplace index: plugins.json (categories, ratings, installs)
  - Tenant isolation: All queries filtered by tenant_id (GDPR Art. 5, 6, 32)

Compliance:
  - Audit-first, fail-closed: index load, install, rating and uninstall are
    recorded on the tenant chain through ``core/deployment/audit_sink.py``
    (``forge.security_events.write_event`` on ``tenant_audit_chain``) BEFORE
    the in-memory state changes; ``AuditWriteFailed`` propagates.
  - Tenant-scoped: No cross-tenant leakage (ADR-0007)
  - Learning loop: NOT integrated (no ADR-0314 events are emitted here).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4

from core.deployment import audit_sink
from core.paths.tenant import corvin_home
from core.tenants.validation import validate_tenant_id

logger = logging.getLogger(__name__)

# Content-free field sets: ids, versions, counts, enum codes — never a path,
# an exception text or a review comment.
_AUDIT_EVENTS: dict[str, frozenset[str]] = {
    "marketplace.index_loaded": frozenset({"total_skills", "buildin_count", "contributor_count", "invalid_count"}),
    "marketplace.manifest_invalid": frozenset({"tier", "reason"}),
    "marketplace.skill_installation_started": frozenset({"skill_id", "version"}),
    "marketplace.skill_installation_completed": frozenset({"skill_id", "version", "installation_id"}),
    "marketplace.skill_installation_failed": frozenset({"skill_id", "reason"}),
    "marketplace.skill_rated": frozenset({"skill_id", "rating"}),
    "marketplace.skill_uninstalled": frozenset({"skill_id"}),
}
audit_sink.register_events(_AUDIT_EVENTS)


def _process_tenant() -> str:
    return os.environ.get("CORVIN_TENANT_ID", "").strip() or "_default"

__all__ = [
    "SkillEcosystemLauncher",
    "SkillDistribution",
    "SkillManifest",
    "SkillInstallation",
    "SkillDiscoveryIndex",
    "CommunitySkillFramework",
    "TenantSkillRegistry",
]


class SkillTier(Enum):
    """Skill classification tier (ADR-0511)."""

    BUILDIN = "buildin"  # Apache 2.0 + CLA, SLA guaranteed
    CONTRIBUTOR = "contributor"  # MIT, community-driven
    INTERNAL = "internal"  # Internal tooling (not marketplace)


class SkillCategory(Enum):
    """Skill categories derived from CorvinOS layer analysis."""

    MEMORY = "memory"  # L28, ADR-0314–0321
    SECURITY_COMPLIANCE = "security_compliance"  # L16, L10, L34
    INTEGRATION = "integration"  # L4, L38, bridges, MCP
    DATA_PROCESSING = "data_processing"  # L25, L34, L36
    OBSERVABILITY = "observability"  # L36, ACO L5, diagnostics
    WORKFLOW = "workflow"  # L22, orchestration
    CUSTOM = "custom"  # User-defined


@dataclass(frozen=True)
class SkillManifest:
    """Immutable skill manifest (ADR-0264 + skill.json schema).

    Every skill carries standardized metadata for discovery, validation,
    and installation orchestration.
    """

    id: str  # skill:buildin-os_routing or skill:contributor-user-custom
    name: str
    version: str  # Semantic version (e.g., "1.0.0")
    author: str
    license: str  # "Apache-2.0" (buildin) or "MIT" (contributor)
    tier: SkillTier
    category: SkillCategory
    description: str
    description_long: Optional[str] = None

    # Distribution
    supports_source: bool = True
    supports_wheel: bool = True
    wheel_url: Optional[str] = None
    wheel_checksum: Optional[str] = None  # sha256

    # Skill specifics
    dependencies: list[str] = field(default_factory=list)  # Other skill IDs
    boot_layer: str = "bundled"  # ADR-0243: compliance, core, bundled, installed
    audit_events: list[str] = field(default_factory=list)  # Events this skill emits

    # Metadata
    learning_loop_enabled: bool = True  # ADR-0314 feedback integration
    sla_level: str = "buildin"  # "buildin" or "community"
    security_audit_date: Optional[str] = None
    security_findings: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "license": self.license,
            "tier": self.tier.value,
            "category": self.category.value,
            "description": self.description,
            "description_long": self.description_long,
            "supports_source": self.supports_source,
            "supports_wheel": self.supports_wheel,
            "wheel_url": self.wheel_url,
            "wheel_checksum": self.wheel_checksum,
            "dependencies": self.dependencies,
            "boot_layer": self.boot_layer,
            "audit_events": self.audit_events,
            "learning_loop_enabled": self.learning_loop_enabled,
            "sla_level": self.sla_level,
            "security_audit_date": self.security_audit_date,
            "security_findings": self.security_findings,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillManifest:
        """Deserialize from JSON dict."""
        tier = SkillTier(data.get("tier", "contributor"))
        category = SkillCategory(data.get("category", "custom"))

        return cls(
            id=data["id"],
            name=data["name"],
            version=data["version"],
            author=data["author"],
            license=data["license"],
            tier=tier,
            category=category,
            description=data["description"],
            description_long=data.get("description_long"),
            supports_source=data.get("supports_source", True),
            supports_wheel=data.get("supports_wheel", True),
            wheel_url=data.get("wheel_url"),
            wheel_checksum=data.get("wheel_checksum"),
            dependencies=data.get("dependencies", []),
            boot_layer=data.get("boot_layer", "bundled"),
            audit_events=data.get("audit_events", []),
            learning_loop_enabled=data.get("learning_loop_enabled", True),
            sla_level=data.get("sla_level", "community"),
            security_audit_date=data.get("security_audit_date"),
            security_findings=data.get("security_findings", 0),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now(timezone.utc).isoformat())),
            updated_at=datetime.fromisoformat(data.get("updated_at", datetime.now(timezone.utc).isoformat())),
        )


@dataclass(frozen=True)
class SkillDistribution:
    """Immutable skill distribution record (source + wheel options)."""

    skill_id: str
    version: str
    distribution_id: str = field(default_factory=lambda: str(uuid4()))

    # Distribution options
    source_url: Optional[str] = None  # Git repo or download
    source_hash: Optional[str] = None  # Commit hash or tarball hash

    wheel_url: Optional[str] = None
    wheel_hash: str = ""  # sha256
    wheel_signature: Optional[str] = None  # RFC 3161 or Ed25519

    available_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None  # Optional TTL (None = permanent)

    def is_available(self) -> bool:
        """Check if distribution is still available."""
        now = datetime.now(timezone.utc)
        if now < self.available_at:
            return False
        if self.expires_at and now > self.expires_at:
            return False
        return True


@dataclass
class SkillInstallation:
    """Mutable skill installation record (per tenant, per skill)."""

    tenant_id: str
    skill_id: str
    installation_id: str = field(default_factory=lambda: str(uuid4()))

    # ``field(default_factory=None)`` made the dataclass call ``None()`` for a
    # record built without a manifest (``from_dict`` always did) → TypeError.
    manifest: Optional[SkillManifest] = None
    installed_version: str = ""

    # Installation state
    installed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_executed_at: Optional[datetime] = None

    # Health tracking
    execution_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    average_latency_ms: float = 0.0
    # Not measured: nothing feeds a learning-loop confidence into this record.
    # It used to default to 1.0 (a perfect score nobody computed).
    confidence_score: Optional[float] = None

    # Config & learning
    learning_enabled: bool = True
    config_version: int = 0
    custom_config: dict[str, Any] = field(default_factory=dict)

    # Status
    enabled: bool = True
    auto_update: bool = True
    # No rollback is implemented for an installation — never claim one.
    rollback_available: bool = False

    @property
    def tier(self) -> Optional[SkillTier]:
        """Tier of the installed manifest (None when unknown)."""
        return self.manifest.tier if self.manifest is not None else None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON."""
        return {
            "tenant_id": self.tenant_id,
            "skill_id": self.skill_id,
            "installation_id": self.installation_id,
            "installed_version": self.installed_version,
            "installed_at": self.installed_at.isoformat(),
            "last_updated_at": self.last_updated_at.isoformat(),
            "last_executed_at": self.last_executed_at.isoformat() if self.last_executed_at else None,
            "execution_count": self.execution_count,
            "error_count": self.error_count,
            "average_latency_ms": self.average_latency_ms,
            "confidence_score": self.confidence_score,
            "enabled": self.enabled,
            "auto_update": self.auto_update,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillInstallation:
        """Deserialize from JSON."""
        inst = cls(
            tenant_id=data["tenant_id"],
            skill_id=data["skill_id"],
            installation_id=data.get("installation_id", str(uuid4())),
        )
        inst.installed_version = data.get("installed_version", "")
        inst.installed_at = datetime.fromisoformat(data.get("installed_at", datetime.now(timezone.utc).isoformat()))
        inst.last_updated_at = datetime.fromisoformat(data.get("last_updated_at", datetime.now(timezone.utc).isoformat()))
        inst.execution_count = data.get("execution_count", 0)
        inst.error_count = data.get("error_count", 0)
        inst.average_latency_ms = data.get("average_latency_ms", 0.0)
        inst.confidence_score = data.get("confidence_score")
        inst.enabled = data.get("enabled", True)
        inst.auto_update = data.get("auto_update", True)
        return inst


@dataclass
class SkillDiscoveryIndex:
    """Marketplace index for skill discovery (buildin + contributor tiers)."""

    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    skills: list[SkillManifest] = field(default_factory=list)
    by_category: dict[str, list[SkillManifest]] = field(default_factory=dict)
    by_tier: dict[str, list[SkillManifest]] = field(default_factory=dict)

    # Stats
    total_skills: int = 0
    total_installs: int = 0  # Aggregate across all tenants
    trending: list[str] = field(default_factory=list)  # Top 10 skills by recent activity

    def index_skill(self, manifest: SkillManifest) -> None:
        """Add skill to index (discovery)."""
        self.skills.append(manifest)

        # Index by category
        cat = manifest.category.value
        if cat not in self.by_category:
            self.by_category[cat] = []
        self.by_category[cat].append(manifest)

        # Index by tier
        tier = manifest.tier.value
        if tier not in self.by_tier:
            self.by_tier[tier] = []
        self.by_tier[tier].append(manifest)

        self.total_skills = len(self.skills)

    def discover_by_category(self, category: SkillCategory) -> list[SkillManifest]:
        """Discover skills by category."""
        return self.by_category.get(category.value, [])

    def discover_by_tier(self, tier: SkillTier) -> list[SkillManifest]:
        """Discover skills by tier (buildin / contributor)."""
        return self.by_tier.get(tier.value, [])

    def to_dict(self) -> dict[str, Any]:
        """Serialize index."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "total_skills": self.total_skills,
            "total_installs": self.total_installs,
            "trending": self.trending,
            "by_category": {
                cat: [s.to_dict() for s in skills]
                for cat, skills in self.by_category.items()
            },
            "by_tier": {
                tier: [s.to_dict() for s in skills]
                for tier, skills in self.by_tier.items()
            },
        }


@dataclass
class CommunitySkillFramework:
    """Community plugin framework (rating, feedback, auto-updates)."""

    community_registry_url: str
    feedback_collection_enabled: bool = True
    rating_system_enabled: bool = True
    auto_update_enabled: bool = True
    auto_update_check_interval_hours: int = 24

    # Rating aggregates
    skill_ratings: dict[str, float] = field(default_factory=dict)  # skill_id -> avg rating (1-5)
    skill_rating_counts: dict[str, int] = field(default_factory=dict)  # skill_id -> number of ratings
    skill_install_counts: dict[str, int] = field(default_factory=dict)  # skill_id -> installs

    # Feedback collection (append-only, tenant-scoped)
    feedback_log: list[dict[str, Any]] = field(default_factory=list)

    def rate_skill(
        self,
        skill_id: str,
        user_id: Optional[str],
        rating: float,
        comment: Optional[str] = None,
        tenant_id: str = "_default",
    ) -> None:
        """Record a skill rating (1-5 stars)."""
        validate_tenant_id(tenant_id)

        if not 1.0 <= rating <= 5.0:
            raise ValueError(f"Rating must be 1-5, got {rating}")

        # Append-only feedback log (never modify)
        self.feedback_log.append({
            "skill_id": skill_id,
            "user_id": user_id,
            "rating": rating,
            "comment": comment,
            "tenant_id": tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Running arithmetic mean. It used to be ``(old + new) / 2``, which
        # weights the latest rating as much as all previous ones together
        # (5, 3, 1 → 2.5 instead of 3.0).
        n = self.skill_rating_counts.get(skill_id, 0) + 1
        old = self.skill_ratings.get(skill_id, 0.0)
        self.skill_ratings[skill_id] = old + (rating - old) / n
        self.skill_rating_counts[skill_id] = n

    def get_skill_rating(self, skill_id: str) -> Optional[float]:
        """Get current rating for a skill."""
        return self.skill_ratings.get(skill_id)

    def increment_install_count(self, skill_id: str) -> None:
        """Track skill installation."""
        if skill_id not in self.skill_install_counts:
            self.skill_install_counts[skill_id] = 0
        self.skill_install_counts[skill_id] += 1

    def get_install_count(self, skill_id: str) -> int:
        """Get install count for a skill."""
        return self.skill_install_counts.get(skill_id, 0)


@dataclass
class TenantSkillRegistry:
    """Per-tenant skill registry (ADR-0007 tenant isolation)."""

    tenant_id: str
    installations: dict[str, SkillInstallation] = field(default_factory=dict)
    learning_models: dict[str, dict[str, Any]] = field(default_factory=dict)  # Per-skill learning

    def install_skill(
        self,
        manifest: SkillManifest,
        version: str = "",
        auto_update: bool = True,
    ) -> SkillInstallation:
        """Install a skill for this tenant."""
        validate_tenant_id(self.tenant_id)

        installation = SkillInstallation(
            tenant_id=self.tenant_id,
            skill_id=manifest.id,
            manifest=manifest,
            installed_version=version or manifest.version,
            auto_update=auto_update,
        )

        self.installations[manifest.id] = installation
        return installation

    def get_installed_skill(self, skill_id: str) -> Optional[SkillInstallation]:
        """Get installed skill for this tenant."""
        return self.installations.get(skill_id)

    def list_installed_skills(self) -> list[SkillInstallation]:
        """List all installed skills for this tenant."""
        return list(self.installations.values())

    def update_skill_config(
        self,
        skill_id: str,
        config_delta: dict[str, Any],
    ) -> bool:
        """Update skill config (for learning loop integration)."""
        if skill_id not in self.installations:
            return False

        installation = self.installations[skill_id]
        installation.custom_config.update(config_delta)
        installation.config_version += 1
        installation.last_updated_at = datetime.now(timezone.utc)

        return True

    def record_skill_execution(
        self,
        skill_id: str,
        latency_ms: float,
        error: Optional[str] = None,
    ) -> None:
        """Record skill execution for health tracking."""
        if skill_id not in self.installations:
            return

        installation = self.installations[skill_id]
        installation.execution_count += 1
        installation.last_executed_at = datetime.now(timezone.utc)

        if error:
            installation.error_count += 1
            installation.last_error = error

        # Update rolling average latency
        n = installation.execution_count
        old_avg = installation.average_latency_ms
        installation.average_latency_ms = (old_avg * (n - 1) + latency_ms) / n


class SkillEcosystemLauncher:
    """Skill ecosystem orchestrator (ADR-0511 + ADR-0532) — NOT WIRED, in-memory.

    Responsibilities:
      1. Load marketplace index (buildin + contributor skills)
      2. Validate skill manifests against schema
      3. Install skills per tenant with isolation (in-memory registries)
      4. Audit-first, fail-closed: every state change is recorded on the
         tenant chain before it is applied (``AuditWriteFailed`` propagates)
    """

    def __init__(self, marketplace_root: Optional[Path] = None):
        """Initialize ecosystem launcher.

        Args:
            marketplace_root: Marketplace directory (buildin + contributor
                plugins). Defaults to ``<corvin_home>/marketplace`` (honours
                ``CORVIN_HOME``; it used to hard-wire ``~/.corvin``).

        There is deliberately no audit-callback seam: an injected callback
        was the only audit this class ever had, i.e. an in-memory list.
        """
        self.marketplace_root = marketplace_root or corvin_home() / "marketplace"

        # Skill registry (per tenant)
        self.tenant_registries: dict[str, TenantSkillRegistry] = {}

        # Marketplace index
        self.discovery_index = SkillDiscoveryIndex()

        # Community framework (in-memory aggregates)
        self.community_framework = CommunitySkillFramework(
            community_registry_url="https://marketplace.corvinOS.io/skills"
        )
        self._invalid_manifest_count = 0

        logger.info(f"SkillEcosystemLauncher initialized (marketplace={self.marketplace_root})")

    def load_marketplace_index(self, tenant_id: Optional[str] = None) -> SkillDiscoveryIndex:
        """Load marketplace index from disk.

        Reads buildin + contributor skills from the marketplace directory and
        populates the discovery index. Each unreadable/invalid manifest is
        audited as ``marketplace.manifest_invalid``; the load itself as
        ``marketplace.index_loaded`` (on ``tenant_id``, default: process tenant).
        """
        tenant = tenant_id or _process_tenant()
        validate_tenant_id(tenant)
        index = SkillDiscoveryIndex()
        self._invalid_manifest_count = 0

        counts = {SkillTier.BUILDIN: 0, SkillTier.CONTRIBUTOR: 0}
        for sub, tier in (("buildin", SkillTier.BUILDIN), ("contributor", SkillTier.CONTRIBUTOR)):
            tier_path = self.marketplace_root / "plugins" / sub
            if tier_path.exists():
                for manifest in self._load_skill_manifests(tier_path, tier, tenant):
                    index.index_skill(manifest)
                    counts[tier] += 1

        audit_sink.emit(
            "marketplace.index_loaded",
            {
                "total_skills": index.total_skills,
                "buildin_count": counts[SkillTier.BUILDIN],
                "contributor_count": counts[SkillTier.CONTRIBUTOR],
                "invalid_count": self._invalid_manifest_count,
                "lom": "SkillEcosystemLauncher.load_marketplace_index",
            },
            tenant_id=tenant,
        )
        self.discovery_index = index
        return self.discovery_index

    def _load_skill_manifests(
        self,
        tier_path: Path,
        tier: SkillTier,
        tenant_id: str,
    ) -> Iterator[SkillManifest]:
        """Load skill manifests from tier directory.

        Directory structure:
            tier_path/[category]/[skill_id]/skill.json

        The directory decides the tier (a contributor manifest cannot claim
        ``buildin``). ``SkillManifest`` is frozen: this used to assign
        ``manifest.tier = tier``, which raised ``FrozenInstanceError`` for
        EVERY manifest, so the index was always empty.
        """
        if not tier_path.exists():
            return

        for category_dir in sorted(tier_path.iterdir()):
            if not category_dir.is_dir():
                continue

            for skill_dir in sorted(category_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue

                manifest_file = skill_dir / "skill.json"
                if not manifest_file.exists():
                    logger.warning(f"No skill.json found in {skill_dir}")
                    continue

                try:
                    with open(manifest_file, "r") as f:
                        manifest_data = json.load(f)
                    manifest = dataclasses.replace(SkillManifest.from_dict(manifest_data), tier=tier)
                except Exception as e:  # noqa: BLE001 — any bad manifest is skipped, audited
                    logger.error(f"Failed to load manifest {manifest_file}: {e}")
                    self._invalid_manifest_count += 1
                    audit_sink.emit(
                        "marketplace.manifest_invalid",
                        {
                            "tier": tier.value,
                            "reason": type(e).__name__,
                            "lom": "SkillEcosystemLauncher._load_skill_manifests",
                        },
                        tenant_id=tenant_id,
                        severity="WARNING",
                    )
                    continue
                yield manifest

    def get_tenant_registry(self, tenant_id: str) -> TenantSkillRegistry:
        """Get or create tenant-specific skill registry (ADR-0007)."""
        validate_tenant_id(tenant_id)

        if tenant_id not in self.tenant_registries:
            self.tenant_registries[tenant_id] = TenantSkillRegistry(tenant_id=tenant_id)

        return self.tenant_registries[tenant_id]

    def install_skill(
        self,
        tenant_id: str,
        skill_id: str,
        auto_update: bool = True,
    ) -> Optional[SkillInstallation]:
        """Install a skill for a tenant (in-memory registry).

        Returns the installation, or None when the skill is not in the index.
        Raises ``AuditWriteFailed`` when an audit record does not commit — the
        installation is then NOT applied.
        """
        validate_tenant_id(tenant_id)

        skill_manifest = self.get_skill_details(skill_id)
        if not skill_manifest:
            logger.error(f"Skill {skill_id} not found in marketplace")
            audit_sink.emit(
                "marketplace.skill_installation_failed",
                {"skill_id": skill_id, "reason": "skill_not_found",
                 "lom": "SkillEcosystemLauncher.install_skill"},
                tenant_id=tenant_id,
                severity="WARNING",
            )
            return None

        audit_sink.emit(
            "marketplace.skill_installation_started",
            {"skill_id": skill_id, "version": skill_manifest.version,
             "lom": "SkillEcosystemLauncher.install_skill"},
            tenant_id=tenant_id,
        )

        installation = SkillInstallation(
            tenant_id=tenant_id,
            skill_id=skill_manifest.id,
            manifest=skill_manifest,
            installed_version=skill_manifest.version,
            auto_update=auto_update,
        )
        audit_sink.emit(
            "marketplace.skill_installation_completed",
            {"skill_id": skill_id, "version": installation.installed_version,
             "installation_id": installation.installation_id,
             "lom": "SkillEcosystemLauncher.install_skill"},
            tenant_id=tenant_id,
        )
        # Applied only after the completion record committed.
        registry = self.get_tenant_registry(tenant_id)
        registry.installations[skill_manifest.id] = installation
        self.community_framework.increment_install_count(skill_id)

        logger.info(f"Skill {skill_id} installed for tenant {tenant_id}")
        return installation

    def discover_skills(
        self,
        category: Optional[SkillCategory] = None,
        tier: Optional[SkillTier] = None,
        search_text: Optional[str] = None,
    ) -> list[SkillManifest]:
        """Discover skills from the loaded index (category / tier / text filter)."""
        results = self.discovery_index.skills

        if category:
            results = self.discovery_index.discover_by_category(category)

        if tier:
            results = [s for s in results if s.tier == tier]

        if search_text:
            text_lower = search_text.lower()
            results = [
                s for s in results
                if text_lower in s.name.lower()
                or text_lower in s.description.lower()
            ]

        return results

    def get_skill_details(self, skill_id: str) -> Optional[SkillManifest]:
        """Get detailed information about a skill."""
        for skill in self.discovery_index.skills:
            if skill.id == skill_id:
                return skill
        return None

    def rate_skill(
        self,
        tenant_id: str,
        skill_id: str,
        rating: float,
        comment: Optional[str] = None,
    ) -> bool:
        """Rate a skill (1-5). Returns False for an out-of-range rating.

        The rating is audited (``marketplace.skill_rated``, never the comment)
        before it is applied; ``AuditWriteFailed`` propagates.
        """
        validate_tenant_id(tenant_id)
        if not 1.0 <= rating <= 5.0:
            logger.error(f"Rating must be 1-5, got {rating}")
            return False

        audit_sink.emit(
            "marketplace.skill_rated",
            {"skill_id": skill_id, "rating": rating,
             "lom": "SkillEcosystemLauncher.rate_skill"},
            tenant_id=tenant_id,
        )
        self.community_framework.rate_skill(
            skill_id,
            user_id=None,  # Pseudonymous rating
            rating=rating,
            comment=comment,
            tenant_id=tenant_id,
        )
        return True

    def list_tenant_skills(self, tenant_id: str) -> list[SkillInstallation]:
        """List skills installed in a tenant."""
        validate_tenant_id(tenant_id)
        registry = self.get_tenant_registry(tenant_id)
        return registry.list_installed_skills()

    def uninstall_skill(self, tenant_id: str, skill_id: str) -> bool:
        """Uninstall a skill from a tenant (audited before removal)."""
        validate_tenant_id(tenant_id)

        registry = self.get_tenant_registry(tenant_id)
        if skill_id not in registry.installations:
            return False
        audit_sink.emit(
            "marketplace.skill_uninstalled",
            {"skill_id": skill_id, "lom": "SkillEcosystemLauncher.uninstall_skill"},
            tenant_id=tenant_id,
        )
        del registry.installations[skill_id]
        return True
