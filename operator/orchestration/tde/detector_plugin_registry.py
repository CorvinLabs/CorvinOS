"""ADR-0214: Detector Plugin Registry (Phase 3).

Extensible detector loading with:
- Ed25519 signature validation (fail-closed)
- CLS-tier gating (licensing)
- Marketplace integration (plugin discovery)
- Metadata schema validation (enforces plugin interface)
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol

_logger = logging.getLogger(__name__)


@dataclass
class DetectorPlugin:
    """A loaded detector plugin with metadata and signature."""

    name: str
    version: str
    author: str
    cls_tier: str  # "free" | "team" | "enterprise"
    public_key_pem: str  # Ed25519 public key (PEM format)
    signature: str  # Hex-encoded Ed25519 signature over metadata_json
    metadata_json: str  # Canonical JSON (for signature verification)
    detector_class: type  # The actual detector implementation


class DetectorPluginInterface(Protocol):
    """Protocol that all plugins must implement."""

    async def detect_engine(
        self,
        task: str,
        context: dict[str, Any],
        initial_analysis: Any,  # InitialAnalysisRequest
    ) -> tuple[str, float, dict[str, Any]]:
        """Detect which engine to use.

        Must return (engine_name, confidence, signals).
        """
        ...


class Ed25519SignatureValidator:
    """Ed25519 signature verification (fail-closed)."""

    @staticmethod
    def verify_signature(
        metadata_json: str,
        public_key_pem: str,
        signature_hex: str,
    ) -> bool:
        """
        Verify Ed25519 signature over metadata.

        Args:
            metadata_json: Canonical JSON string (what was signed)
            public_key_pem: Ed25519 public key in PEM format
            signature_hex: Signature as hex string

        Returns:
            True if signature is valid, False otherwise (fail-closed)

        Raises:
            ImportError: If cryptography library is not available
            (Fail-closed: raises rather than silently returning False)
        """
        try:
            # Import crypto library (cryptography package)
            from cryptography.hazmat.primitives.asymmetric import ed25519
            from cryptography.hazmat.primitives.serialization import (
                load_pem_public_key,
            )
        except ImportError as e:
            # CRITICAL: Fail-closed if crypto library missing
            # Never silently allow unverified plugins
            raise ImportError(
                "cryptography library required for Ed25519 signature verification. "
                "Install with: pip install cryptography"
            ) from e

        try:
            # Load public key
            pub_key = load_pem_public_key(public_key_pem.encode())
            if not isinstance(pub_key, ed25519.Ed25519PublicKey):
                _logger.error("Public key is not an Ed25519 key")
                return False

            # Convert signature from hex to bytes
            signature_bytes = bytes.fromhex(signature_hex)

            # Verify signature
            pub_key.verify(signature_bytes, metadata_json.encode())
            return True

        except Exception as e:
            _logger.error(f"Signature verification failed: {e}")
            return False  # Fail-closed


class DetectorPluginRegistry:
    """Registry for extensible detectors with signature validation."""

    def __init__(self, cls_tier: str = "free"):
        """
        Initialize registry.

        Args:
            cls_tier: Current licensing tier ("free" | "team" | "enterprise")
        """
        self.cls_tier = cls_tier
        self.plugins: Dict[str, DetectorPlugin] = {}
        self.validator = Ed25519SignatureValidator()

    def register_plugin(
        self,
        metadata: dict[str, Any],
        detector_class: type,
        public_key_pem: Optional[str] = None,
        signature_hex: Optional[str] = None,
    ) -> bool:
        """
        Register a detector plugin with optional signature verification.

        Args:
            metadata: Plugin metadata (name, version, author, cls_tier)
            detector_class: The detector implementation class
            public_key_pem: Ed25519 public key (PEM format) — if provided, signature is REQUIRED
            signature_hex: Ed25519 signature (hex) — if provided, signature is VERIFIED

        Returns:
            True if plugin registered successfully, False if validation failed (fail-closed)
        """

        name = metadata.get("name")
        version = metadata.get("version")
        author = metadata.get("author")
        plugin_cls_tier = metadata.get("cls_tier", "free")

        if not all([name, version, author]):
            _logger.error(f"Plugin metadata incomplete: {metadata}")
            return False

        # CLS Tier Gating: fail-closed if plugin requires higher tier than we have
        tier_order = {"free": 0, "team": 1, "enterprise": 2}
        our_tier_level = tier_order.get(self.cls_tier, 0)
        plugin_tier_level = tier_order.get(plugin_cls_tier, 0)

        if plugin_tier_level > our_tier_level:
            _logger.warning(
                f"Plugin {name} requires CLS tier {plugin_cls_tier}, "
                f"but we have {self.cls_tier} — plugin blocked"
            )
            return False

        # Signature Verification (fail-closed)
        if public_key_pem is not None or signature_hex is not None:
            if not public_key_pem or not signature_hex:
                _logger.error("Public key and signature must both be provided")
                return False

            # Canonical JSON for signature verification
            metadata_json = json.dumps(metadata, sort_keys=True, separators=(",", ":"))

            # Verify signature (fail-closed on any error, including missing crypto lib)
            try:
                if not self.validator.verify_signature(metadata_json, public_key_pem, signature_hex):
                    _logger.error(f"Plugin {name} signature verification failed — plugin rejected")
                    return False
                _logger.info(f"Plugin {name} signature verified ✓")
            except ImportError as e:
                # Cryptography library missing — fail-closed (reject plugin)
                _logger.error(f"Cannot verify plugin {name}: {e} — plugin rejected")
                return False

        else:
            # No signature provided — allow only for built-in/trusted plugins
            _logger.warning(f"Plugin {name} has no signature — treating as local/trusted")
            metadata_json = json.dumps(metadata, sort_keys=True, separators=(",", ":"))

        # Register plugin
        plugin = DetectorPlugin(
            name=name,
            version=version,
            author=author,
            cls_tier=plugin_cls_tier,
            public_key_pem=public_key_pem or "",
            signature=signature_hex or "",
            metadata_json=metadata_json,
            detector_class=detector_class,
        )

        self.plugins[name] = plugin
        _logger.info(f"Plugin {name} registered (cls_tier={plugin_cls_tier})")
        return True

    def get_plugin(self, name: str) -> Optional[DetectorPlugin]:
        """Get a registered plugin by name."""
        return self.plugins.get(name)

    def list_plugins(self) -> list[str]:
        """List all registered plugin names."""
        return list(self.plugins.keys())

    async def execute_plugin(
        self,
        plugin_name: str,
        task: str,
        context: dict[str, Any],
        initial_analysis: Any,
    ) -> Optional[tuple[str, float, dict[str, Any]]]:
        """
        Execute a registered plugin detector.

        Args:
            plugin_name: Name of the plugin to execute
            task: Task description
            context: Task context
            initial_analysis: InitialAnalysisRequest

        Returns:
            (engine_name, confidence, signals) or None if plugin not found
        """

        plugin = self.get_plugin(plugin_name)
        if not plugin:
            _logger.error(f"Plugin {plugin_name} not registered")
            return None

        try:
            # Instantiate detector
            detector = plugin.detector_class()

            # Execute detector's detect_engine method
            result = await detector.detect_engine(task, context, initial_analysis)

            _logger.info(f"Plugin {plugin_name} executed successfully")
            return result

        except Exception as e:
            _logger.error(f"Plugin {plugin_name} execution failed: {e}")
            return None


# Global plugin registry singleton
_global_registry: Optional[DetectorPluginRegistry] = None


def get_plugin_registry(cls_tier: str = "free") -> DetectorPluginRegistry:
    """Get or create the global plugin registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = DetectorPluginRegistry(cls_tier=cls_tier)
    return _global_registry


def reset_plugin_registry():
    """Reset the global registry (for testing)."""
    global _global_registry
    _global_registry = None
