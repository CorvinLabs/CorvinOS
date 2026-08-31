"""RAG Provider Import/Export — Sharing mechanism.

Allows operators to:
1. Export a provider manifest (YAML) from the Hub
2. Import a provider manifest locally
3. Validate manifest before importing
"""
from __future__ import annotations

import hashlib
import logging
import yaml
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class RAGProviderImportExport:
    """Import/export RAG provider manifests."""

    @staticmethod
    def export_manifest(manifest_path: Path) -> str:
        """Export a provider manifest as YAML string."""
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        with open(manifest_path, "r") as f:
            content = f.read()

        return content

    @staticmethod
    def compute_manifest_hash(manifest_yaml: str) -> str:
        """Compute SHA256 hash of manifest (for Hub deduplication)."""
        return hashlib.sha256(manifest_yaml.encode()).hexdigest()

    @staticmethod
    def validate_manifest(manifest_yaml: str) -> tuple[bool, Optional[str]]:
        """Validate manifest structure before importing."""
        try:
            data = yaml.safe_load(manifest_yaml)

            # Check required fields
            required = ["apiVersion", "kind", "metadata", "spec"]
            for field in required:
                if field not in data:
                    return False, f"Missing required field: {field}"

            # Validate metadata
            metadata = data.get("metadata", {})
            if not metadata.get("name"):
                return False, "Missing metadata.name"

            # Validate spec
            spec = data.get("spec", {})
            if not spec.get("retrieval"):
                return False, "Missing spec.retrieval"

            # Validate no credentials in manifest (security check)
            manifest_str = str(data)
            suspicious_keys = ["api_key", "apikey", "token", "secret", "password"]
            for key in suspicious_keys:
                if key.lower() in manifest_str.lower():
                    logger.warning(f"Manifest contains suspicious key: {key}")
                    # Don't fail, but warn

            return True, None

        except yaml.YAMLError as e:
            return False, f"Invalid YAML: {str(e)}"
        except Exception as e:
            return False, f"Validation error: {str(e)}"

    @staticmethod
    def import_manifest(
        manifest_yaml: str,
        destination_dir: Path,
        provider_id: Optional[str] = None,
    ) -> tuple[bool, str, Optional[str]]:
        """Import a manifest locally.

        Args:
            manifest_yaml: YAML string of the manifest
            destination_dir: Where to save the manifest locally
            provider_id: Optional override for provider ID

        Returns:
            (success, provider_id, error_message)
        """
        # Validate first
        valid, error = RAGProviderImportExport.validate_manifest(manifest_yaml)
        if not valid:
            return False, "", error

        try:
            # Parse manifest
            data = yaml.safe_load(manifest_yaml)
            metadata = data.get("metadata", {})
            name = metadata.get("name", "unknown")

            # Use provided ID or extract from manifest
            if provider_id is None:
                provider_id = name

            # ADR-0144 CON-01 (path-traversal chokepoint): provider_id becomes a
            # filename. When it is derived from manifest metadata.name it is fully
            # attacker-controlled, so constrain it here too — this is the last line
            # of defence even if a future caller forgets the route-level sanitizer.
            import re as _re
            if not _re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", str(provider_id)):
                return False, "", (
                    "invalid provider_id: must match ^[a-z0-9][a-z0-9._-]{0,63}$ "
                    "(no path separators or '..')"
                )

            # Create destination if needed
            destination_dir.mkdir(parents=True, exist_ok=True)

            # Write to file
            manifest_file = destination_dir / f"{provider_id}.yaml"
            with open(manifest_file, "w") as f:
                f.write(manifest_yaml)

            logger.info(f"Imported provider: {provider_id} → {manifest_file}")
            return True, provider_id, None

        except Exception as e:
            return False, "", f"Import failed: {str(e)}"

    @staticmethod
    def generate_share_link(manifest_hash: str, hub_url: str = "https://hub.corvin.local") -> str:
        """Generate a shareable link to download a manifest from the Hub."""
        return f"{hub_url}/manifest/{manifest_hash}"

    @staticmethod
    def manifest_to_dict(manifest_yaml: str) -> Optional[dict]:
        """Convert YAML manifest to dict."""
        try:
            return yaml.safe_load(manifest_yaml)
        except Exception:
            return None
