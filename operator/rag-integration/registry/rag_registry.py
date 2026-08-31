"""RAG Provider Registry Manager.

Manages registration, listing, and lifecycle of RAG providers.
Stores manifests in ~/.corvin/tenants/<tid>/global/rag/

Registry structure:
  ~/.corvin/tenants/<tid>/global/rag/
  ├── registry.json           # Index of all providers
  ├── manifests/
  │   ├── elasticsearch-docs.yaml
  │   ├── vector-db-semantic.yaml
  │   └── custom-api.yaml
  └── audit.jsonl             # Registry events (separate from main audit)
"""
from __future__ import annotations

import os
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────

@dataclass
class ProviderEntry:
    """Registry entry for a RAG provider."""
    id: str
    name: str
    version: str
    status: str  # active, degraded, unavailable
    registered_at: str  # ISO 8601 timestamp
    last_health_check: Optional[str] = None
    health_status: str = "unknown"  # healthy, degraded, unavailable
    query_stats: dict = field(default_factory=lambda: {
        "total": 0,
        "today": 0,
        "avg_latency_ms": 0,
    })
    manifest_sha256: Optional[str] = None


@dataclass
class RegistryIndex:
    """Complete registry index."""
    version: int = 1
    providers: list[ProviderEntry] = field(default_factory=list)
    last_updated: Optional[str] = None


# ── Registry Manager ────────────────────────────────────────

class RAGRegistry:
    """Manages RAG provider registry."""

    def __init__(self, registry_dir: Path | str):
        """Initialize registry at given path."""
        self.registry_dir = Path(registry_dir)
        self.manifests_dir = self.registry_dir / "manifests"
        self.registry_file = self.registry_dir / "registry.json"

        # Create directories
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)

        # Load existing registry
        self.index = self._load_index()

    def _load_index(self) -> RegistryIndex:
        """Load registry index from file."""
        if not self.registry_file.exists():
            return RegistryIndex()

        try:
            with open(self.registry_file) as f:
                data = json.load(f)
            return RegistryIndex(
                version=data.get("version", 1),
                providers=[
                    ProviderEntry(**p) for p in data.get("providers", [])
                ],
                last_updated=data.get("last_updated"),
            )
        except Exception as e:
            logger.error(f"Failed to load registry: {e}")
            return RegistryIndex()

    def _save_index(self) -> None:
        """Save registry index to file."""
        self.index.last_updated = _iso8601_now()
        data = {
            "version": self.index.version,
            "providers": [asdict(p) for p in self.index.providers],
            "last_updated": self.index.last_updated,
        }
        with open(self.registry_file, "w") as f:
            json.dump(data, f, indent=2)

    def register(
        self,
        manifest_path: Path | str,
        tenant_id: str = "_default",
    ) -> tuple[bool, str]:
        """
        Register a new RAG provider.

        Returns:
            (success: bool, message: str)
        """
        manifest_path = Path(manifest_path)

        # Load and validate manifest
        try:
            from ..validator.manifest_validator import ManifestValidator
            validator = ManifestValidator()

            if not manifest_path.exists():
                return False, f"Manifest file not found: {manifest_path}"

            result = validator.validate_file(manifest_path)
            if not result.valid:
                errors = "\n  ".join(result.errors or [])
                return False, f"Manifest validation failed:\n  {errors}"

            provider_id = result.provider_id
        except Exception as e:
            return False, f"Failed to validate manifest: {e}"

        # Load manifest
        try:
            with open(manifest_path) as f:
                if manifest_path.suffix in [".yaml", ".yml"]:
                    manifest = yaml.safe_load(f)
                else:
                    manifest = json.load(f)
        except Exception as e:
            return False, f"Failed to load manifest: {e}"

        # Health check
        try:
            success, msg = self._health_check(manifest)
            if not success:
                return False, f"Health check failed: {msg}"
        except Exception as e:
            logger.warning(f"Health check error: {e}")
            # Don't fail registration on health check (provider might be temporarily down)

        # Copy manifest to registry
        try:
            dest_path = self.manifests_dir / f"{provider_id}.yaml"
            with open(manifest_path) as src:
                with open(dest_path, "w") as dst:
                    dst.write(src.read())
        except Exception as e:
            return False, f"Failed to save manifest: {e}"

        # Add to registry
        provider_name = manifest.get("metadata", {}).get("name", provider_id)
        version = manifest.get("metadata", {}).get("version", "1.0.0")

        entry = ProviderEntry(
            id=provider_id,
            name=provider_name,
            version=version,
            status="active",
            registered_at=_iso8601_now(),
            health_status="healthy",
        )

        # Update index (replace if exists)
        self.index.providers = [
            p for p in self.index.providers if p.id != provider_id
        ]
        self.index.providers.append(entry)
        self._save_index()

        logger.info(f"Registered RAG provider: {provider_id}")
        return True, f"✅ Registered: {provider_id}"

    def unregister(self, provider_id: str) -> tuple[bool, str]:
        """Unregister a RAG provider."""
        # Remove manifest
        manifest_path = self.manifests_dir / f"{provider_id}.yaml"
        if manifest_path.exists():
            manifest_path.unlink()

        # Remove from index
        self.index.providers = [
            p for p in self.index.providers if p.id != provider_id
        ]
        self._save_index()

        logger.info(f"Unregistered RAG provider: {provider_id}")
        return True, f"✅ Unregistered: {provider_id}"

    def list_providers(self, status: Optional[str] = None) -> list[ProviderEntry]:
        """List registered providers."""
        if status:
            return [p for p in self.index.providers if p.status == status]
        return self.index.providers

    def get_provider(self, provider_id: str) -> Optional[ProviderEntry]:
        """Get provider by ID."""
        for p in self.index.providers:
            if p.id == provider_id:
                return p
        return None

    def get_manifest(self, provider_id: str) -> Optional[dict]:
        """Load provider manifest."""
        manifest_path = self.manifests_dir / f"{provider_id}.yaml"
        if not manifest_path.exists():
            return None

        try:
            with open(manifest_path) as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load manifest {provider_id}: {e}")
            return None

    def update_health_status(
        self,
        provider_id: str,
        status: str,  # healthy, degraded, unavailable
        latency_ms: int = 0,
    ) -> bool:
        """Update provider health status."""
        entry = self.get_provider(provider_id)
        if not entry:
            return False

        entry.health_status = status
        entry.last_health_check = _iso8601_now()

        if latency_ms > 0:
            # Update average latency
            total = entry.query_stats.get("total", 0)
            old_avg = entry.query_stats.get("avg_latency_ms", 0)
            new_avg = (old_avg * total + latency_ms) / (total + 1)
            entry.query_stats["avg_latency_ms"] = int(new_avg)

        self._save_index()
        return True

    def update_query_stats(self, provider_id: str, latency_ms: int) -> bool:
        """Update query statistics for a provider."""
        entry = self.get_provider(provider_id)
        if not entry:
            return False

        entry.query_stats["total"] += 1

        # Increment today count (naive: assumes no date boundary handling needed for Phase 2)
        entry.query_stats["today"] += 1

        # Update average latency
        total = entry.query_stats["total"]
        old_avg = entry.query_stats.get("avg_latency_ms", 0)
        new_avg = (old_avg * (total - 1) + latency_ms) / total
        entry.query_stats["avg_latency_ms"] = int(new_avg)

        self._save_index()
        return True

    @staticmethod
    def _health_check(manifest: dict) -> tuple[bool, str]:
        """Check if provider endpoint is reachable."""
        try:
            import httpx
        except ImportError:
            logger.warning("httpx not installed, skipping health check")
            return True, "OK (health check skipped)"

        retrieval = manifest.get("spec", {}).get("retrieval", {})
        endpoint = retrieval.get("endpoint")
        timeout_ms = retrieval.get("timeout_ms", 5000) / 1000

        if not endpoint:
            return False, "No endpoint defined"

        try:
            # Try HEAD request first, fall back to GET
            response = httpx.head(
                endpoint,
                timeout=timeout_ms,
                follow_redirects=True,
            )
            response.raise_for_status()
            return True, f"OK ({response.status_code})"
        except httpx.HTTPError as e:
            return False, f"HTTP error: {e}"
        except Exception as e:
            return False, f"Connection error: {e}"


# ── Utility Functions ────────────────────────────────────────

def _iso8601_now() -> str:
    """Return current time in ISO 8601 format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def get_default_registry_dir(tenant_id: str = "_default") -> Path:
    """Get default registry directory for tenant.

    Resolves through the SHARED resolver (CORVIN_HOME env → repo marker →
    ~/.corvin) so this CLI writer agrees with the console reader, which uses
    forge.paths.tenant_global_dir. A bare Path.home()/.corvin here made the
    RAG CLI write under ~/.corvin while the console read the pinned
    <repo>/.corvin → "RAG console dead" (path-audit 2026-06-25 #HIGH1).
    """
    try:
        from forge import paths as _fp  # type: ignore
        return _fp.tenant_global_dir(tenant_id) / "rag"
    except Exception:  # noqa: BLE001 — forge not importable → match the resolver's order inline
        env = os.environ.get("CORVIN_HOME")
        if env:
            base = Path(os.path.expanduser(os.path.expandvars(env)))
        else:
            base = Path.home() / ".corvin"
        return base / "tenants" / tenant_id / "global" / "rag"
