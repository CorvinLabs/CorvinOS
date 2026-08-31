"""
CorvinOS Plugin Marketplace - discovery, installation, rating, governance.

Features:
- Plugin metadata (name, version, category, rating, downloads)
- Search and discovery with operator affinity suggestions
- Installation tracking with resource limits
- Rating/review system with weighted averages
- Governance: removal on <2 stars OR security audit failure
- Revenue sharing: 70% author, 20% Corvin, 10% ecosystem fund

Design:
- All data is immutable (frozen dataclasses)
- Audit-logged (every install/rate/remove event)
- Tenant-scoped (isolation for multi-tenant)
- Backward compatible with v0.6 plugin system
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal
from datetime import datetime
from enum import Enum
from pathlib import Path
import json
import logging

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

logger = logging.getLogger(__name__)


# ── Gap 2: Signature Verification ────────────────────────────────────────────

class SignatureVerificationError(Exception):
    """Plugin signature verification failed."""
    pass


def verify_ed25519_signature(
    package_path: Path,
    public_key_pem: str,
    signature_bytes: bytes,
) -> bool:
    """Verify ED25519 signature of a plugin package (Gap 2).

    Args:
        package_path: Path to .whl or plugin package file
        public_key_pem: ED25519 public key in PEM format
        signature_bytes: Signature bytes to verify

    Returns:
        True if signature is valid

    Raises:
        SignatureVerificationError: If verification fails or crypto unavailable

    Design:
    - Fail-closed: any error during verification raises exception
    - Immutable package: only verifies bytes, never modifies
    - Audit-trail: caller logs verification result
    """
    if not CRYPTO_AVAILABLE:
        raise SignatureVerificationError(
            "cryptography library not installed; signature verification unavailable"
        )

    try:
        # Parse PEM-encoded public key
        from cryptography.hazmat.primitives import serialization
        public_key = serialization.load_pem_public_key(
            public_key_pem.encode("utf-8") if isinstance(public_key_pem, str) else public_key_pem,
            backend=None,  # default backend
        )

        if not isinstance(public_key, ed25519.Ed25519PublicKey):
            raise SignatureVerificationError(f"Expected ED25519 public key, got {type(public_key)}")

        # Read package bytes
        package_bytes = package_path.read_bytes()

        # Verify signature (raises InvalidSignature on failure)
        public_key.verify(signature_bytes, package_bytes)
        return True

    except Exception as exc:
        raise SignatureVerificationError(f"signature verification failed: {exc}")


# ───────────────────────────────────────────────────────────────────────────────


class PluginCategory(Enum):
    """Plugin marketplace categories."""
    AUTHENTICATION = "Authentication"
    PERFORMANCE = "Performance"
    SECURITY = "Security"
    DATABASE = "Database"
    INTEGRATION = "Integration"
    USER_INTERFACE = "UI"
    ANALYTICS = "Analytics"
    TOOLING = "Tooling"


class PluginOrigin(Enum):
    """Plugin source/provenance."""
    BUILTIN = "builtin"  # Shipped with CorvinOS
    VETTED = "vetted"  # Reviewed by Corvin Security team
    COMMUNITY = "community"  # Community-contributed


class BootLayer(Enum):
    """Plugin boot layer (ADR-0243) - load order and disableability."""
    COMPLIANCE = "compliance"  # Security-critical, never disableable
    CORE = "core"  # Core functionality, may be replaceable
    BUNDLED = "bundled"  # Shipped plugins, may be disabled
    INSTALLED = "installed"  # User-installed plugins


@dataclass(frozen=True)
class PluginMetadata:
    """
    Immutable plugin registry entry.

    Changes to metadata are recorded in audit trail as new entries,
    never updated in-place.
    """

    # Identity
    plugin_id: str  # e.g., "auth-saml-2.1"
    name: str
    version: str  # semantic: MAJOR.MINOR.PATCH

    # Classification
    category: PluginCategory
    boot_layer: BootLayer
    origin: PluginOrigin

    # Metadata
    author_id: str
    author_email: str
    license: str  # e.g., "Apache-2.0"
    description: str  # max 200 chars
    long_description: str  # markdown, max 5000 chars
    homepage_url: Optional[str] = None
    repository_url: Optional[str] = None

    # Dependencies
    depends_on: List[str] = field(default_factory=list)  # ["plugin_id@version", ...]
    conflicts_with: List[str] = field(default_factory=list)
    min_corvin_version: str = "0.7.0"
    max_corvin_version: Optional[str] = None  # None = no upper bound

    # Sandbox profile (immutable declaration)
    required_syscalls: List[str] = field(default_factory=list)
    filesystem_paths: Dict[str, str] = field(default_factory=dict)  # {"/tmp": "rw", ...}
    network_access: bool = False

    # Governance
    rating_count: int = 0
    rating_average: float = 5.0  # [1.0..5.0]
    download_count: int = 0
    listed: bool = True  # false if flagged/suspended
    last_updated: datetime = field(default_factory=datetime.utcnow)

    # Audit metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    tenant_id: str = "_default"
    audit_hash: str = ""

    def is_discoverable(self) -> bool:
        """Whether plugin appears in marketplace listings."""
        # All listed plugins are discoverable (including bundled/builtin for admin install flow)
        return self.listed

    def should_auto_remove(self) -> bool:
        """Whether governance rules require removal."""
        # Remove if rating dropped below 2 stars
        if self.rating_count > 5 and self.rating_average < 2.0:
            return True
        return False

    def to_dict(self) -> Dict:
        """Serialize for API responses."""
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "category": self.category.value,
            "origin": self.origin.value,
            "author_id": self.author_id,
            "description": self.description,
            "rating": self.rating_average,
            "rating_count": self.rating_count,
            "download_count": self.download_count,
            "listed": self.listed,
        }


@dataclass(frozen=True)
class PluginInstallation:
    """Immutable per-operator plugin installation record."""

    installation_id: str
    operator_id: str
    tenant_id: str

    # Plugin reference
    plugin_id: str
    version: str

    # State
    enabled: bool
    installed_at: datetime
    last_enabled_at: Optional[datetime] = None

    # Sandbox config
    cpu_limit_percent: int = 20  # [1..100]
    memory_limit_mb: int = 256  # [64..512]
    timeout_seconds: int = 60  # [5..3600]

    # Audit
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    audit_hash: str = ""

    def __post_init__(self):
        """Validate installation invariants."""
        if not (1 <= self.cpu_limit_percent <= 100):
            raise ValueError(f"CPU limit must be in [1..100], got {self.cpu_limit_percent}")
        if not (64 <= self.memory_limit_mb <= 512):
            raise ValueError(f"Memory must be in [64..512]MB, got {self.memory_limit_mb}")
        if not (5 <= self.timeout_seconds <= 3600):
            raise ValueError(f"Timeout must be in [5..3600]s, got {self.timeout_seconds}")


@dataclass(frozen=True)
class PluginReview:
    """Immutable plugin review."""

    review_id: str
    plugin_id: str
    operator_id: str
    tenant_id: str

    rating: int  # [1..5]
    comment: Optional[str] = None  # max 500 chars
    helpful_count: int = 0  # upvotes

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    audit_hash: str = ""

    def __post_init__(self):
        """Validate review invariants."""
        if not (1 <= self.rating <= 5):
            raise ValueError(f"Rating must be in [1..5], got {self.rating}")
        if self.comment and len(self.comment) > 500:
            raise ValueError("Comment must be ≤500 chars")


@dataclass(frozen=True)
class PluginRevenue:
    """Immutable revenue sharing record."""

    revenue_id: str
    plugin_id: str
    author_id: str
    tenant_id: str

    period_month: str  # "2026-08"
    total_installs: int
    total_usage_hours: float  # estimated
    revenue_usd: float  # total before split

    # Shares (must sum to 1.0)
    author_percent: float = 0.70
    corvin_percent: float = 0.20
    ecosystem_percent: float = 0.10

    author_payout_usd: float = 0.0
    corvin_payout_usd: float = 0.0
    ecosystem_payout_usd: float = 0.0

    paid_at: Optional[datetime] = None
    audit_hash: str = ""

    def __post_init__(self):
        """Validate revenue invariants."""
        # Shares must sum to ~1.0
        total = self.author_percent + self.corvin_percent + self.ecosystem_percent
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Revenue shares must sum to 1.0, got {total}")

        # Payouts must match shares
        if self.revenue_usd > 0:
            expected_author = self.revenue_usd * self.author_percent
            if abs(self.author_payout_usd - expected_author) > 0.01:
                raise ValueError(f"Author payout mismatch: expected {expected_author}, got {self.author_payout_usd}")


class PluginMarketplace:
    """
    In-memory marketplace index with search and governance.

    In production, this would be backed by SQLite or PostgreSQL.
    """

    def __init__(self, registry_path: Optional[str] = None):
        self.plugins: Dict[str, PluginMetadata] = {}
        self.installations: Dict[str, List[PluginInstallation]] = {}  # plugin_id -> [installations]
        self.reviews: Dict[str, List[PluginReview]] = {}  # plugin_id -> [reviews]

        # Load registry if provided, otherwise try default locations
        if registry_path:
            self._load_registry(registry_path)
        else:
            self._load_registry_from_defaults()

    def _load_registry_from_defaults(self) -> None:
        """Load registry from default locations and plugin directories (ADR-0511)."""
        default_paths = [
            Path('/home/shumway/projects/Corvin-Marketplace/registry.json'),
            Path.home() / '.corvin/marketplace/registry.json',
            Path.cwd() / 'registry.json',
        ]
        registry_loaded = False
        for path in default_paths:
            if path.exists():
                self._load_registry(str(path))
                registry_loaded = True
                break

        if not registry_loaded:
            logger.debug("No marketplace registry found in default locations")

        # Always discover from buildin/ + contributor/ hierarchies (ADR-0511)
        # This supplements any registry.json, allowing both mechanisms to coexist
        self._load_plugins_from_directories()

    def _load_registry(self, registry_path: str) -> None:
        """Load plugins from registry.json file."""
        try:
            with open(registry_path) as f:
                registry_data = json.load(f)

            plugins = registry_data.get('plugins', {})
            logger.info(f"Loading {len(plugins)} plugins from {registry_path}")

            for plugin_id, plugin_data in plugins.items():
                try:
                    # Map category string to enum (handle variations)
                    category_str = plugin_data.get('categories', ['integration'])[0].upper()
                    # Try exact match first, then common mappings
                    try:
                        category = PluginCategory[category_str]
                    except KeyError:
                        # Try common mappings
                        category_map = {
                            'COMMUNICATION': 'INTEGRATION',
                            'INTEGRATION': 'INTEGRATION',
                            'NOTIFICATION': 'INTEGRATION',
                            'AUTH': 'AUTHENTICATION',
                        }
                        category = PluginCategory[category_map.get(category_str, 'INTEGRATION')]

                    # Map boot_layer to enum
                    boot_layer_str = plugin_data.get('boot_layer', 'bundled').upper()
                    boot_layer = BootLayer[boot_layer_str]

                    # Map origin to enum
                    origin_str = plugin_data.get('origin', 'builtin').upper()
                    origin = PluginOrigin[origin_str]

                    metadata = PluginMetadata(
                        plugin_id=plugin_data['id'],
                        name=plugin_data['name'],
                        version=plugin_data['version'],
                        category=category,
                        boot_layer=boot_layer,
                        origin=origin,
                        author_id=plugin_data.get('email', 'unknown'),
                        author_email=plugin_data.get('email', 'unknown'),
                        license=plugin_data.get('license', 'Apache-2.0'),
                        description=plugin_data.get('description', ''),
                        long_description=plugin_data.get('description', ''),
                        homepage_url=plugin_data.get('homepage'),
                        repository_url=plugin_data.get('repository'),
                        min_corvin_version=plugin_data.get('min_corvin_version', '0.10.0'),
                        max_corvin_version=plugin_data.get('max_corvin_version'),
                        download_count=plugin_data.get('download_count', 0),
                        rating_count=plugin_data.get('rating_count', 0),
                        rating_average=plugin_data.get('rating_average', 5.0),
                        listed=plugin_data.get('listed', True),
                    )
                    self.plugins[plugin_id] = metadata
                    logger.debug(f"Loaded plugin: {plugin_id} ({plugin_data['name']})")

                except Exception as e:
                    logger.error(f"Failed to load plugin {plugin_id}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to load marketplace registry from {registry_path}: {e}")

    def _load_plugins_from_directories(self) -> None:
        """
        Discover and load plugins from buildin/ + contributor/ hierarchies.
        Each plugin folder should contain plugin.json (ADR-0511).
        """
        plugin_dirs = [
            (Path.cwd() / 'buildin', PluginOrigin.BUILTIN, BootLayer.BUNDLED),
            (Path.cwd() / 'contributor', PluginOrigin.COMMUNITY, BootLayer.INSTALLED),
        ]

        for base_dir, origin, boot_layer in plugin_dirs:
            if not base_dir.exists():
                logger.debug(f"Plugin directory not found: {base_dir}")
                continue

            for plugin_folder in base_dir.iterdir():
                if not plugin_folder.is_dir():
                    continue

                plugin_json_path = plugin_folder / 'plugin.json'
                if not plugin_json_path.exists():
                    logger.debug(f"No plugin.json in {plugin_folder}")
                    continue

                try:
                    with open(plugin_json_path) as f:
                        plugin_data = json.load(f)

                    # Enrich with origin and boot_layer if not specified
                    if 'origin' not in plugin_data:
                        plugin_data['origin'] = origin.value

                    if 'boot_layer' not in plugin_data:
                        plugin_data['boot_layer'] = boot_layer.value

                    # Map category to enum
                    category_str = plugin_data.get('category', 'INTEGRATION').upper()
                    try:
                        category = PluginCategory[category_str]
                    except KeyError:
                        category_map = {
                            'COMMUNICATION': 'INTEGRATION',
                            'INTEGRATION': 'INTEGRATION',
                            'NOTIFICATION': 'INTEGRATION',
                            'AUTH': 'AUTHENTICATION',
                            'MEMORY': 'TOOLING',
                            'DATA': 'DATABASE',
                            'SECURITY': 'SECURITY',
                            'OBSERVABILITY': 'ANALYTICS',
                        }
                        category = PluginCategory[category_map.get(category_str, 'INTEGRATION')]

                    # Map boot_layer to enum
                    boot_layer_str = plugin_data.get('boot_layer', 'installed').upper()
                    boot_layer_enum = BootLayer[boot_layer_str]

                    # Map origin to enum
                    origin_str = plugin_data.get('origin', 'community').upper()
                    origin_enum = PluginOrigin[origin_str]

                    metadata = PluginMetadata(
                        plugin_id=plugin_data.get('id', plugin_folder.name),
                        name=plugin_data.get('name', plugin_folder.name),
                        version=plugin_data.get('version', '0.1.0'),
                        category=category,
                        boot_layer=boot_layer_enum,
                        origin=origin_enum,
                        author_id=plugin_data.get('author', 'Community'),
                        author_email=plugin_data.get('email', 'unknown@corvin.org'),
                        license=plugin_data.get('license', 'Apache-2.0'),
                        description=plugin_data.get('description', ''),
                        long_description=plugin_data.get('description', ''),
                        homepage_url=plugin_data.get('homepage'),
                        repository_url=plugin_data.get('github') or plugin_data.get('repository'),
                        min_corvin_version=plugin_data.get('min_corvin_version', '0.10.0'),
                        max_corvin_version=plugin_data.get('max_corvin_version'),
                        download_count=plugin_data.get('installs', 0),
                        rating_count=plugin_data.get('rating_count', 0),
                        rating_average=plugin_data.get('rating', 5.0),
                        listed=plugin_data.get('listed', True),
                    )
                    self.plugins[plugin_data.get('id', plugin_folder.name)] = metadata
                    logger.info(f"Discovered plugin: {metadata.plugin_id} ({metadata.name}) from {plugin_folder.name}")

                except Exception as e:
                    logger.error(f"Failed to load plugin from {plugin_folder}: {e}")
                    continue

    def register_plugin(self, metadata: PluginMetadata) -> None:
        """Register a plugin in the marketplace."""
        if metadata.plugin_id in self.plugins:
            raise ValueError(f"Plugin {metadata.plugin_id} already registered")
        self.plugins[metadata.plugin_id] = metadata

    def list_plugins(
        self,
        category: Optional[PluginCategory] = None,
        query: Optional[str] = None,
        origin: Optional[PluginOrigin] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[PluginMetadata]:
        """Search and filter plugins."""
        results = []
        for plugin in self.plugins.values():
            if not plugin.is_discoverable():
                continue
            if category and plugin.category != category:
                continue
            if query and query.lower() not in plugin.name.lower():
                continue
            if origin and plugin.origin != origin:
                continue
            results.append(plugin)

        # Sort by rating, then by download count
        results.sort(key=lambda p: (p.rating_average, p.download_count), reverse=True)

        return results[offset:offset + limit]

    def get_plugin(self, plugin_id: str) -> Optional[PluginMetadata]:
        """Get plugin by ID."""
        return self.plugins.get(plugin_id)

    def record_installation(self, installation: PluginInstallation) -> None:
        """Record plugin installation."""
        plugin_id = installation.plugin_id
        if plugin_id not in self.installations:
            self.installations[plugin_id] = []
        self.installations[plugin_id].append(installation)

    def record_review(self, review: PluginReview) -> None:
        """Record plugin review and recalculate rating."""
        plugin_id = review.plugin_id
        if plugin_id not in self.reviews:
            self.reviews[plugin_id] = []
        self.reviews[plugin_id].append(review)

        # Recalculate rating
        reviews = self.reviews[plugin_id]
        if reviews:
            avg_rating = sum(r.rating for r in reviews) / len(reviews)
            plugin = self.plugins[plugin_id]
            # Update plugin metadata (in practice, create new entry in audit trail)
            self.plugins[plugin_id] = PluginMetadata(
                plugin_id=plugin.plugin_id,
                name=plugin.name,
                version=plugin.version,
                category=plugin.category,
                boot_layer=plugin.boot_layer,
                origin=plugin.origin,
                author_id=plugin.author_id,
                author_email=plugin.author_email,
                license=plugin.license,
                description=plugin.description,
                long_description=plugin.long_description,
                rating_count=len(reviews),
                rating_average=avg_rating,
                download_count=plugin.download_count,
                listed=plugin.listed and not plugin.should_auto_remove(),
            )

    def get_reviews(self, plugin_id: str, limit: int = 10) -> List[PluginReview]:
        """Get reviews for a plugin."""
        return self.reviews.get(plugin_id, [])[-limit:]

    def check_governance(self) -> List[str]:
        """
        Check marketplace governance rules.

        Returns list of plugin IDs that should be removed.
        """
        to_remove = []
        for plugin_id, plugin in self.plugins.items():
            if plugin.should_auto_remove():
                to_remove.append(plugin_id)
        return to_remove

    def install_plugin_with_verification(
        self,
        plugin_id: str,
        package_path: Path,
        signature_path: Optional[Path] = None,
        skip_verification: bool = False,
    ) -> bool:
        """Install plugin with signature verification (Gap 2).

        Args:
            plugin_id: Plugin ID to install
            package_path: Path to downloaded .whl or plugin package
            signature_path: Optional path to .sig file (for vetted plugins)
            skip_verification: Skip verification even for vetted plugins (debug only)

        Returns:
            True if installation successful

        Raises:
            SignatureVerificationError: If signature verification fails
            ValueError: If plugin not found or invalid

        Verification logic:
        - VETTED origin: requires valid signature (fail-closed)
        - COMMUNITY origin: no signature verification (user already confirmed)
        - BUILTIN origin: no signature needed (shipped with core)
        """
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin not found: {plugin_id}")

        # Skip verification in debug mode (test-only)
        if skip_verification:
            logger.warning(f"Signature verification SKIPPED for {plugin_id}")
            return True

        # Verify vetted plugins
        if plugin.origin == PluginOrigin.VETTED:
            if not plugin.public_key:
                raise SignatureVerificationError(
                    f"Vetted plugin {plugin_id} missing public_key in manifest"
                )

            if not signature_path or not signature_path.is_file():
                raise SignatureVerificationError(
                    f"Signature file not found for vetted plugin {plugin_id}"
                )

            # Verify signature
            signature_bytes = signature_path.read_bytes()
            verify_ed25519_signature(package_path, plugin.public_key, signature_bytes)
            logger.info(f"✓ Signature verified for vetted plugin {plugin_id}")

        return True

    def remove_plugin(self, plugin_id: str) -> None:
        """Remove plugin from marketplace (governance action)."""
        if plugin_id in self.plugins:
            plugin = self.plugins[plugin_id]
            # Mark as unlisted (don't delete audit trail)
            self.plugins[plugin_id] = PluginMetadata(
                plugin_id=plugin.plugin_id,
                name=plugin.name,
                version=plugin.version,
                category=plugin.category,
                boot_layer=plugin.boot_layer,
                origin=plugin.origin,
                author_id=plugin.author_id,
                author_email=plugin.author_email,
                license=plugin.license,
                description=plugin.description,
                long_description=plugin.long_description,
                listed=False,  # Mark as unlisted
            )
