"""Skill Manifest v2 — License binding schema (Phase 2, ADR-0667).

Implements:
1. Updated SkillManifest v2 with license binding
2. LicenseBindingMetadata dataclass
3. v1→v2 migration function
4. Backward compatibility for v1 manifests
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
import json

from core.skills.manifest_validator import (
    SkillManifest as SkillManifestV1,
    ManifestParameter,
    ManifestDependency,
)


@dataclass(frozen=True)
class LicenseBindingMetadata:
    """License binding metadata for Skill manifest.

    Binds a Skill to a specific tier and ties it to the operator's RSA key.
    Immutable and cryptographically signed.
    """

    required_tier: str  # "free" | "paid"
    binding_hash: str  # SHA256(skill_id + version + tier + operator_key_fingerprint)
    operator_signature: str  # Base64 RSA signature of binding_hash
    timestamp: str  # ISO 8601 UTC timestamp


@dataclass(frozen=True)
class SkillManifestV2:
    """Skill Manifest v2 — with license binding.

    Extends v1 manifest with license binding metadata.
    Maintains backward compatibility with v1 (v1 manifests load with default tier="free").
    """

    # Original v1 fields (immutable)
    skill_id: str
    version: str  # Semver: 1.0.0
    boot_layer: str  # bundled, installed, core, compliance
    parameters: List[ManifestParameter] = field(default_factory=list)
    dependencies: List[ManifestDependency] = field(default_factory=list)
    entry_point: Optional[str] = None  # module:class.method
    audit_events: List[str] = field(default_factory=list)  # Events this Skill emits

    # New v2 fields
    license_binding: Optional[LicenseBindingMetadata] = None  # None = free tier (default)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "boot_layer": self.boot_layer,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.param_type,
                    "default": p.default,
                    "bounds": p.bounds,
                }
                for p in self.parameters
            ],
            "dependencies": [
                {"skill_id": d.skill_id, "version": d.version_constraint}
                for d in self.dependencies
            ],
            "entry_point": self.entry_point,
            "audit_events": self.audit_events,
            "license_binding": (
                {
                    "required_tier": self.license_binding.required_tier,
                    "binding_hash": self.license_binding.binding_hash,
                    "operator_signature": self.license_binding.operator_signature,
                    "timestamp": self.license_binding.timestamp,
                }
                if self.license_binding
                else None
            ),
        }

    def required_tier(self) -> str:
        """Get required license tier for this Skill.

        Returns:
            "free" | "paid"
        """
        return self.license_binding.required_tier if self.license_binding else "free"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SkillManifestV2:
        """Deserialize from JSON dict."""
        # Parse license binding if present
        license_binding = None
        if data.get("license_binding"):
            lb = data["license_binding"]
            license_binding = LicenseBindingMetadata(
                required_tier=lb["required_tier"],
                binding_hash=lb["binding_hash"],
                operator_signature=lb["operator_signature"],
                timestamp=lb["timestamp"],
            )

        # Parse parameters
        parameters = []
        for p in data.get("parameters", []):
            parameters.append(
                ManifestParameter(
                    name=p["name"],
                    param_type=p["type"],
                    default=p["default"],
                    bounds=tuple(p.get("bounds")) if p.get("bounds") else None,
                )
            )

        # Parse dependencies
        dependencies = []
        for d in data.get("dependencies", []):
            dependencies.append(
                ManifestDependency(
                    skill_id=d["skill_id"],
                    version_constraint=d["version"],
                )
            )

        return cls(
            skill_id=data["skill_id"],
            version=data["version"],
            boot_layer=data["boot_layer"],
            parameters=parameters,
            dependencies=dependencies,
            entry_point=data.get("entry_point"),
            audit_events=data.get("audit_events", []),
            license_binding=license_binding,
        )


def migrate_manifest_v1_to_v2(
    v1_manifest: SkillManifestV1,
    default_tier: str = "free",
) -> SkillManifestV2:
    """Migrate manifest from v1 to v2.

    Args:
        v1_manifest: SkillManifestV1 to migrate
        default_tier: Default tier for new manifests ("free" or "paid")

    Returns:
        SkillManifestV2 with same data + empty license_binding (will be signed later)
    """
    return SkillManifestV2(
        skill_id=v1_manifest.skill_id,
        version=v1_manifest.version,
        boot_layer=v1_manifest.boot_layer,
        parameters=v1_manifest.parameters or [],
        dependencies=v1_manifest.dependencies or [],
        entry_point=v1_manifest.entry_point,
        audit_events=v1_manifest.audit_events or [],
        license_binding=None,  # Will be added during signing
    )


def load_manifest_v2_from_file(manifest_path: str) -> SkillManifestV2:
    """Load Skill manifest v2 from JSON file.

    Args:
        manifest_path: Path to manifest.json file

    Returns:
        SkillManifestV2 object

    Raises:
        FileNotFoundError: if file not found
        ValueError: if manifest is invalid
    """
    import pathlib

    path = pathlib.Path(manifest_path)

    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    try:
        with open(path, "r") as f:
            data = json.load(f)
        return SkillManifestV2.from_dict(data)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid manifest JSON: {e}")
    except Exception as e:
        raise ValueError(f"Failed to load manifest: {e}")


def save_manifest_v2_to_file(manifest: SkillManifestV2, manifest_path: str) -> None:
    """Save Skill manifest v2 to JSON file.

    Args:
        manifest: SkillManifestV2 to save
        manifest_path: Path to save manifest

    Raises:
        IOError: if save fails
    """
    import pathlib

    path = pathlib.Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(path, "w") as f:
            json.dump(manifest.to_dict(), f, indent=2)
    except Exception as e:
        raise IOError(f"Failed to save manifest: {e}")
